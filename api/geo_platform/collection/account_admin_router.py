# ruff: noqa: B008
"""采集账号治理 API（migration s06_0022 实体，设计文档 caiji-0813 §1/§6.5）。

账号管理页 / 浏览器管理页的数据源与操作面。实体是机器资源（无租户、无 RLS），
身份走 operations 体系（``get_principal`` + ``account:read``/``account:operate``），
审计一律落 ``collection_account_event``（actor=操作者身份）——设计 §3.5 指定
该表为这类机器资源的审计存储，不复用租户域 platform.audit_log。

- 五平台格固定列：doubao / yiyan / deepseek / yuanbao / tongyi（无行 = null，
  前端好渲染）。
- 「地域IP不匹配」硬约束：换绑浏览器时目标实例 region_gb 必须 == 账号行
  region_gb（含 region 同帧修改后的生效值），否则 409 region_ip_mismatch。
- 实例实况（started_at/rss_bytes）查询时经 ``systemctl show -P`` 实采：
  只读属性查询走 D-Bus，非特权用户即可（2026-08-13 本机实证 rc=0），api 沙箱
  ProtectSystem=strict 只限文件写、不拦 systemctl 只读查询，故无需 /proc 回退；
  任何失败 → null 如实返回，绝不编造。
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from workflows.activities.assist_notify import push_captcha_assist

from ..identity.policy import Principal, get_principal
from ..otp.extract import PHONE_RE, mask_phone
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import now_utc
from .account_models import (
    CollectionAccountEvent,
    CollectionBrowser,
    CollectionPhoneAccount,
    CollectionPlatformAccount,
    CollectionRegion,
)
from .collection_browser_sync import sync_collection_browsers
from .models import BrowserFence
from .relay_probe import probe_collection_region

log = structlog.get_logger()

router = APIRouter(prefix="/api/v2", tags=["collection-account-governance"])

# 五平台固定列（顺序 = 管理页列序：豆包/文心一言/DeepSeek/腾讯元宝/通义千问）
_PLATFORMS = ("doubao", "yiyan", "deepseek", "yuanbao", "tongyi")

# 转码链路测试等待窗（指引发出后 180s 内有 otp push 到达即判联通）
_SMS_TEST_WINDOW_S = 180

_SMS_TEST_GUIDANCE = (
    "用任意手机向该测量号发送一条测试短信，内容需含平台白名单词（如「豆包」）；"
    "SmsForwarder 命中后推送至 /api/v2/otp/push，等待窗 180 秒内到达即判定转码"
    "链路联通（sms_link_state 变 ok）。再次点击「测试」或刷新账号列表查看状态。"
)

# systemctl ActiveEnterTimestamp 形如 "Sat 2026-08-08 01:33:53 CST"
_SYSTEMD_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------


class PlatformAccountCell(StrictModel):
    """手机号 × 平台格（五平台格同构三子列：地域/额度/状态 + 实例绑定）。"""

    platform_account_pub_id: str
    region_gb: str | None
    quota_day: int | None
    quota_week: int | None
    quota_year: int | None
    used_today: int
    used_week: int
    used_year: int
    quota_reset_at: datetime | None
    quota_resume_at: datetime | None
    runtime_state: str
    current_run_pub_id: str | None
    muted_until: datetime | None
    state_reason: str | None
    browser_instance_key: str | None


class PlatformAccountView(PlatformAccountCell):
    """PATCH 响应 = 格 + 所属手机号行。"""

    phone_account_pub_id: str


class PhoneAccountRow(StrictModel):
    """账号管理页行 = 手机号（platforms 五平台固定列，无行 = null）。"""

    phone_account_pub_id: str
    phone_masked: str
    owner_note: str | None
    state: str
    sms_link_state: str
    last_sms_at: datetime | None
    push_link_state: str
    last_push_test_at: datetime | None
    platforms: dict[str, PlatformAccountCell | None]


class PhoneAccountCreate(StrictModel):
    phone: str
    owner_note: str | None = None


class PlatformAccountPatch(StrictModel):
    region_gb: str | None = None
    quota_day: int | None = Field(default=None, ge=0)
    quota_week: int | None = Field(default=None, ge=0)
    quota_year: int | None = Field(default=None, ge=0)
    browser_instance_key: str | None = None
    confirm: bool = False


class LinkTestRequest(StrictModel):
    channel: Literal["sms", "push"]


class LinkTestResult(StrictModel):
    ok: bool
    channel: str
    sms_link_state: str | None = None
    push_link_state: str | None = None
    last_sms_at: datetime | None = None
    last_push_test_at: datetime | None = None
    wait_window_s: int | None = None
    guidance: str | None = None
    detail: str | None = None


class AccountEventView(StrictModel):
    event_pub_id: str
    event_type: str
    actor: str
    phone_account_pub_id: str | None
    platform_account_pub_id: str | None
    browser_pub_id: str | None
    region_pub_id: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    evidence: str | None
    run_pub_id: str | None
    created_at: datetime


class CollectionBrowserView(StrictModel):
    """浏览器管理页行 = 常驻实例（bindings：平台 → 手机号行 pub_id，稀疏）。"""

    browser_pub_id: str
    instance_key: str
    platform: str
    region_gb: str | None
    exit_ip: str | None
    cdp_port: int | None
    systemd_unit: str | None
    activity: str
    error_streak: int
    breaker_until: datetime | None
    muted_until: datetime | None
    started_at: datetime | None
    uptime_s: int | None
    rss_bytes: int | None
    bindings: dict[str, str]


class BrowserActionResult(StrictModel):
    ok: bool
    instance_key: str
    executed: bool
    detail: str


class ReleaseLockResult(StrictModel):
    ok: bool
    instance_key: str
    released: bool
    detail: str


class BrowserSyncResult(StrictModel):
    synced: int
    created: int
    updated: int
    errors: list[str]
    instances: list[str]


class CollectionRegionView(StrictModel):
    region_pub_id: str
    region_gb: str
    name: str | None
    source: str
    proxy_env_key: str | None
    relay_unit: str | None
    exit_ip_last: str | None
    last_probe_at: datetime | None
    state: str
    note: str | None


class RegionCreate(StrictModel):
    region_gb: str
    name: str | None = None
    proxy_env_key: str | None = None
    relay_unit: str | None = None


class RegionProbeResult(StrictModel):
    region_gb: str
    ok: bool
    exit_ip: str | None
    note: str | None
    alerted: bool


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------


def _actor(principal: Principal) -> str:
    return principal.user_pub_id or principal.subject


def _platform_cell(row: CollectionPlatformAccount) -> PlatformAccountCell:
    return PlatformAccountCell(
        platform_account_pub_id=row.pub_id,
        region_gb=row.region_gb,
        quota_day=row.quota_day,
        quota_week=row.quota_week,
        quota_year=row.quota_year,
        used_today=row.used_today,
        used_week=row.used_week,
        used_year=row.used_year,
        quota_reset_at=row.quota_reset_at,
        quota_resume_at=row.quota_resume_at,
        runtime_state=row.runtime_state,
        current_run_pub_id=row.current_run_pub_id,
        muted_until=row.muted_until,
        state_reason=row.state_reason,
        browser_instance_key=row.browser_instance_key,
    )


def _phone_row(
    phone: CollectionPhoneAccount, platform_rows: list[CollectionPlatformAccount]
) -> PhoneAccountRow:
    by_platform = {row.platform: row for row in platform_rows}
    return PhoneAccountRow(
        phone_account_pub_id=phone.pub_id,
        phone_masked=mask_phone(phone.phone),
        owner_note=phone.owner_note,
        state=phone.state,
        sms_link_state=phone.sms_link_state,
        last_sms_at=phone.last_sms_at,
        push_link_state=phone.push_link_state,
        last_push_test_at=phone.last_push_test_at,
        platforms={
            slug: (_platform_cell(by_platform[slug]) if slug in by_platform else None)
            for slug in _PLATFORMS
        },
    )


def _emit_event(
    session: Session,
    event_type: str,
    *,
    actor: str,
    phone_account_id: int | None = None,
    platform_account_id: int | None = None,
    browser_id: int | None = None,
    region_id: int | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    evidence: str | None = None,
) -> CollectionAccountEvent:
    """管理页操作审计事件（形状照 AccountGovernor._emit，本 router 不绕开它改状态机，
    只写治理动作事件）。"""
    event = CollectionAccountEvent(
        pub_id=new_pub_id("aev"),
        phone_account_id=phone_account_id,
        platform_account_id=platform_account_id,
        browser_id=browser_id,
        region_id=region_id,
        event_type=event_type,
        actor=actor,
        old_value=old_value,
        new_value=new_value,
        evidence=evidence,
        created_at=now_utc(),
    )
    session.add(event)
    session.flush()
    return event


def _systemctl_property(unit: str, prop: str) -> str | None:
    """单属性只读查询（多属性 -P 输出顺序不稳定，2026-08-13 实测逐属性调）。"""
    try:
        out = subprocess.run(
            ["systemctl", "show", "-P", prop, unit],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


def _parse_systemd_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    match = _SYSTEMD_TS_RE.search(raw)
    if not match:
        return None
    naive = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S")
    # api 与被观测实例同机同区（CST），naive 按本机时区落地
    return naive.astimezone()


def probe_browser_runtime(systemd_unit: str | None, instance_key: str) -> dict[str, Any]:
    """实例实况实采：started_at/uptime_s/rss_bytes。失败一律 null 如实（绝不编造）。

    实测结论（2026-08-13，本机 xln 非特权）：``systemctl show -P`` 只读属性查询
    不需要提权（D-Bus 属性读），api 沙箱 ProtectSystem=strict 只限文件系统写、
    不拦该查询，故选 systemctl 直采而非 /proc 进程反查（/proc 需先找 PID 且
    MemoryCurrent 口径还要再算 cgroup，复杂且口径不一致）。
    """
    unit = systemd_unit or f"geo-platform-v2-browser@{instance_key}.service"
    started_at = _parse_systemd_timestamp(_systemctl_property(unit, "ActiveEnterTimestamp"))
    rss_raw = _systemctl_property(unit, "MemoryCurrent")
    uptime_s = int((now_utc() - started_at).total_seconds()) if started_at else None
    rss_bytes = int(rss_raw) if rss_raw and rss_raw.isdigit() else None
    return {"started_at": started_at, "uptime_s": uptime_s, "rss_bytes": rss_bytes}


def _find_phone(session: Session, pub_id: str) -> CollectionPhoneAccount:
    phone = session.scalar(
        select(CollectionPhoneAccount).where(CollectionPhoneAccount.pub_id == pub_id)
    )
    if phone is None:
        raise HTTPException(status_code=404, detail={"code": "phone_account_not_found"})
    return phone


# ---------------------------------------------------------------------------
# 账号管理页
# ---------------------------------------------------------------------------


@router.get("/collection-accounts", response_model=list[PhoneAccountRow])
def list_collection_accounts(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[PhoneAccountRow]:
    principal.require("account:read")
    phones = list(
        session.scalars(
            select(CollectionPhoneAccount).order_by(CollectionPhoneAccount.created_at.desc())
        )
    )
    rows: list[PhoneAccountRow] = []
    for phone in phones:
        platform_rows = list(
            session.scalars(
                select(CollectionPlatformAccount).where(
                    CollectionPlatformAccount.phone_account_id == phone.id
                )
            )
        )
        rows.append(_phone_row(phone, platform_rows))
    return rows


@router.post("/collection-accounts", response_model=PhoneAccountRow, status_code=201)
def create_collection_account(
    body: PhoneAccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PhoneAccountRow:
    principal.require("account:operate")
    phone = body.phone.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail={"code": "bad_phone"})
    existing = session.scalar(
        select(CollectionPhoneAccount).where(CollectionPhoneAccount.phone == phone)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "phone_already_exists"})
    now = now_utc()
    row = CollectionPhoneAccount(
        pub_id=new_pub_id("pha"),
        phone=phone,
        owner_note=body.owner_note,
        state="active",
        sms_link_state="untested",
        push_link_state="untested",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    _emit_event(
        session,
        "phone_account_created",
        actor=_actor(principal),
        phone_account_id=row.id,
        new_value={"phone_masked": mask_phone(phone), "owner_note": body.owner_note},
    )
    session.commit()
    return _phone_row(row, [])


@router.patch("/collection-platform-accounts/{pub_id}", response_model=PlatformAccountView)
def patch_platform_account(
    pub_id: str,
    body: PlatformAccountPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PlatformAccountView:
    """地域/额度/实例绑定修改。region_gb 变更必须 confirm=true（前端二次确认的
    后端锚点）；浏览器换绑做「地域IP不匹配」fail-closed 复核。"""
    principal.require("account:operate")
    account = session.scalar(
        select(CollectionPlatformAccount).where(CollectionPlatformAccount.pub_id == pub_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"code": "platform_account_not_found"})
    provided = body.model_fields_set
    old_value: dict[str, Any] = {}
    new_value: dict[str, Any] = {}
    effective_region = account.region_gb
    if "region_gb" in provided and body.region_gb != account.region_gb:
        if not body.confirm:
            raise HTTPException(
                status_code=400, detail={"code": "region_change_requires_confirmation"}
            )
        if body.region_gb is not None:
            region = session.scalar(
                select(CollectionRegion).where(CollectionRegion.region_gb == body.region_gb)
            )
            if region is None or region.state != "ok":
                raise HTTPException(status_code=400, detail={"code": "region_not_available"})
        effective_region = body.region_gb
        old_value["region_gb"] = account.region_gb
        new_value["region_gb"] = body.region_gb
    if (
        "browser_instance_key" in provided
        and body.browser_instance_key != account.browser_instance_key
    ):
        if body.browser_instance_key is not None:
            browser = session.scalar(
                select(CollectionBrowser).where(
                    CollectionBrowser.instance_key == body.browser_instance_key
                )
            )
            if browser is None:
                raise HTTPException(status_code=404, detail={"code": "browser_not_found"})
            if browser.region_gb != effective_region:
                raise HTTPException(status_code=409, detail={"code": "region_ip_mismatch"})
        old_value["browser_instance_key"] = account.browser_instance_key
        new_value["browser_instance_key"] = body.browser_instance_key
    for field_name in ("quota_day", "quota_week", "quota_year"):
        if field_name in provided and getattr(body, field_name) != getattr(account, field_name):
            old_value[field_name] = getattr(account, field_name)
            new_value[field_name] = getattr(body, field_name)
    if new_value:
        for field_name, value in new_value.items():
            setattr(account, field_name, value)
        account.updated_at = now_utc()
        session.flush()
        _emit_event(
            session,
            "config_change",
            actor=_actor(principal),
            phone_account_id=account.phone_account_id,
            platform_account_id=account.id,
            old_value=old_value,
            new_value=new_value,
        )
    session.commit()
    return PlatformAccountView(
        **_platform_cell(account).model_dump(), phone_account_pub_id=_phone_pub_id(session, account)
    )


def _phone_pub_id(session: Session, account: CollectionPlatformAccount) -> str:
    phone = session.get(CollectionPhoneAccount, account.phone_account_id)
    return phone.pub_id if phone is not None else str(account.phone_account_id)


@router.post("/collection-accounts/{pub_id}/link-test", response_model=LinkTestResult)
def link_test(
    pub_id: str,
    body: LinkTestRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> LinkTestResult:
    """链路联通性测试（设计 §6.4）。push=发测试推送看回执；sms=发测试指引 +
    惰性判定（等待窗内已有 otp push 到达 → sms_link_state='ok'，不挂长任务）。"""
    principal.require("account:operate")
    phone = _find_phone(session, pub_id)
    now = now_utc()
    if body.channel == "push":
        notify_url = os.environ.get("GEO_ASSIST_NOTIFY_URL", "").strip()
        if not notify_url:
            raise HTTPException(status_code=503, detail={"code": "push_channel_not_configured"})
        flavor = os.environ.get("GEO_ASSIST_NOTIFY_FLAVOR", "").strip() or "serverchan"
        ok = push_captcha_assist(
            flavor=flavor,
            url=notify_url,
            title=f"[GEO采集] 接管通道测试 {mask_phone(phone.phone)}",
            body=(
                f"采集账号「接管」链路测试：{mask_phone(phone.phone)}"
                f"（{phone.pub_id}）。收到本推送即方糖通道联通。"
            ),
            timeout_s=5.0,
        )
        if ok:
            phone.push_link_state = "ok"
            phone.last_push_test_at = now
            phone.updated_at = now
        session.flush()
        _emit_event(
            session,
            "link_test",
            actor=_actor(principal),
            phone_account_id=phone.id,
            new_value={"channel": "push", "result": "ok" if ok else "failed"},
        )
        session.commit()
        return LinkTestResult(
            ok=ok,
            channel="push",
            push_link_state=phone.push_link_state,
            last_push_test_at=phone.last_push_test_at,
            detail=None if ok else "push_failed",
        )
    fresh = phone.last_sms_at is not None and (now - phone.last_sms_at).total_seconds() <= (
        _SMS_TEST_WINDOW_S
    )
    if fresh and phone.sms_link_state != "ok":
        phone.sms_link_state = "ok"
        phone.updated_at = now
        session.flush()
    _emit_event(
        session,
        "link_test",
        actor=_actor(principal),
        phone_account_id=phone.id,
        new_value={"channel": "sms", "fresh_sms_in_window": fresh},
    )
    session.commit()
    return LinkTestResult(
        ok=True,
        channel="sms",
        sms_link_state=phone.sms_link_state,
        last_sms_at=phone.last_sms_at,
        wait_window_s=_SMS_TEST_WINDOW_S,
        guidance=_SMS_TEST_GUIDANCE,
    )


@router.get("/collection-accounts/{pub_id}/events", response_model=list[AccountEventView])
def list_account_events(
    pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[AccountEventView]:
    """该手机号（含其平台行）的审计事件，倒序 50 条。"""
    principal.require("account:read")
    phone = _find_phone(session, pub_id)
    platform_rows = list(
        session.scalars(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.phone_account_id == phone.id
            )
        )
    )
    events = list(
        session.scalars(
            select(CollectionAccountEvent).where(
                CollectionAccountEvent.phone_account_id == phone.id
            )
        )
    )
    platform_ids = [row.id for row in platform_rows]
    if platform_ids:
        events.extend(
            session.scalars(
                select(CollectionAccountEvent).where(
                    CollectionAccountEvent.platform_account_id.in_(platform_ids)
                )
            )
        )
    events.sort(key=lambda event: (event.created_at, event.id or 0), reverse=True)
    browser_pub_cache: dict[int, str] = {}
    region_pub_cache: dict[int, str] = {}
    views: list[AccountEventView] = []
    for event in events[:50]:
        browser_pub_id: str | None = None
        if event.browser_id is not None:
            if event.browser_id not in browser_pub_cache:
                browser = session.get(CollectionBrowser, event.browser_id)
                browser_pub_cache[event.browser_id] = browser.pub_id if browser else ""
            browser_pub_id = browser_pub_cache[event.browser_id] or None
        region_pub_id: str | None = None
        if event.region_id is not None:
            if event.region_id not in region_pub_cache:
                region = session.get(CollectionRegion, event.region_id)
                region_pub_cache[event.region_id] = region.pub_id if region else ""
            region_pub_id = region_pub_cache[event.region_id] or None
        platform_pub_id: str | None = None
        if event.platform_account_id is not None:
            platform_pub_id = next(
                (row.pub_id for row in platform_rows if row.id == event.platform_account_id), None
            )
        views.append(
            AccountEventView(
                event_pub_id=event.pub_id,
                event_type=event.event_type,
                actor=event.actor,
                phone_account_pub_id=phone.pub_id if event.phone_account_id == phone.id else None,
                platform_account_pub_id=platform_pub_id,
                browser_pub_id=browser_pub_id,
                region_pub_id=region_pub_id,
                old_value=event.old_value,
                new_value=event.new_value,
                evidence=event.evidence,
                run_pub_id=event.run_pub_id,
                created_at=event.created_at,
            )
        )
    return views


# ---------------------------------------------------------------------------
# 浏览器管理页
# ---------------------------------------------------------------------------


@router.get("/collection-browsers", response_model=list[CollectionBrowserView])
def list_collection_browsers(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CollectionBrowserView]:
    principal.require("account:read")
    browsers = list(
        session.scalars(select(CollectionBrowser).order_by(CollectionBrowser.created_at))
    )
    views: list[CollectionBrowserView] = []
    for browser in browsers:
        runtime = probe_browser_runtime(browser.systemd_unit, browser.instance_key)
        bound_accounts = list(
            session.scalars(
                select(CollectionPlatformAccount).where(
                    CollectionPlatformAccount.browser_instance_key == browser.instance_key
                )
            )
        )
        bindings = {
            account.platform: _phone_pub_id(session, account) for account in bound_accounts
        }
        views.append(
            CollectionBrowserView(
                browser_pub_id=browser.pub_id,
                instance_key=browser.instance_key,
                platform=browser.platform,
                region_gb=browser.region_gb,
                exit_ip=browser.exit_ip,
                cdp_port=browser.cdp_port,
                systemd_unit=browser.systemd_unit,
                activity=browser.activity,
                error_streak=browser.error_streak,
                breaker_until=browser.breaker_until,
                muted_until=browser.muted_until,
                started_at=runtime["started_at"],
                uptime_s=runtime["uptime_s"],
                rss_bytes=runtime["rss_bytes"],
                bindings=bindings,
            )
        )
    return views


@router.post("/collection-browsers/sync", response_model=BrowserSyncResult)
def sync_browsers(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BrowserSyncResult:
    """把 GEO_BROWSER_INSTANCES 镜像进 collection_browser（幂等；手动触发，
    不在启动时自动调）。"""
    principal.require("account:operate")
    try:
        summary = sync_collection_browsers(session)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from exc
    session.commit()
    return BrowserSyncResult(**summary)


@router.post("/collection-browsers/{key}/restart", response_model=BrowserActionResult)
def restart_browser(
    key: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BrowserActionResult:
    """P0 不真执行 systemctl restart（api 沙箱也无权），如实记录 + 返回
    「需运维窗口」。
    TODO(P1): 审批流 + fence 释放 → systemctl restart → 探活闭环。
    """
    principal.require("account:operate")
    browser = session.scalar(
        select(CollectionBrowser).where(CollectionBrowser.instance_key == key)
    )
    if browser is None:
        raise HTTPException(status_code=404, detail={"code": "browser_not_found"})
    _emit_event(
        session,
        "browser_restart_requested",
        actor=_actor(principal),
        browser_id=browser.id,
        evidence="P0 不执行 systemctl restart——需运维窗口人工执行（P1 做审批流）",
    )
    session.commit()
    return BrowserActionResult(
        ok=True, instance_key=key, executed=False, detail="manual_restart_window_required"
    )


@router.post("/collection-browsers/{key}/release-lock", response_model=ReleaseLockResult)
def release_browser_lock(
    key: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ReleaseLockResult:
    """释放实例 fence 锁（platform.browser_fence.released_at=now）——现有手工
    SQL 回收的产品化；stale lease 回收语义不变（本操作即「人工回收」）。"""
    principal.require("account:operate")
    browser = session.scalar(
        select(CollectionBrowser).where(CollectionBrowser.instance_key == key)
    )
    fence = session.scalar(select(BrowserFence).where(BrowserFence.platform == key))
    if browser is None and fence is None:
        raise HTTPException(status_code=404, detail={"code": "browser_not_found"})
    released = False
    old_value: dict[str, Any] | None = None
    if fence is not None:
        old_value = {
            "holder": fence.holder,
            "fencing_token": fence.fencing_token,
            "released_at": fence.released_at.isoformat() if fence.released_at else None,
        }
        if fence.released_at is None:
            fence.released_at = now_utc()
            session.flush()
            released = True
    _emit_event(
        session,
        "browser_lock_released",
        actor=_actor(principal),
        browser_id=browser.id if browser is not None else None,
        old_value=old_value,
        new_value={"released": released},
        evidence=None if released else "无活动锁（幂等空操作）",
    )
    session.commit()
    return ReleaseLockResult(
        ok=True,
        instance_key=key,
        released=released,
        detail="lock_released" if released else "no_active_lock",
    )


# ---------------------------------------------------------------------------
# 地域字典 / relay 巡检
# ---------------------------------------------------------------------------


def _region_view(region: CollectionRegion) -> CollectionRegionView:
    return CollectionRegionView(
        region_pub_id=region.pub_id,
        region_gb=region.region_gb,
        name=region.name,
        source=region.source,
        proxy_env_key=region.proxy_env_key,
        relay_unit=region.relay_unit,
        exit_ip_last=region.exit_ip_last,
        last_probe_at=region.last_probe_at,
        state=region.state,
        note=region.note,
    )


@router.get("/collection-regions", response_model=list[CollectionRegionView])
def list_collection_regions(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CollectionRegionView]:
    principal.require("account:read")
    regions = list(
        session.scalars(select(CollectionRegion).order_by(CollectionRegion.created_at))
    )
    return [_region_view(region) for region in regions]


@router.post("/collection-regions", response_model=CollectionRegionView, status_code=201)
def create_collection_region(
    body: RegionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CollectionRegionView:
    """「添加地域」向导落点：只登记字典行（凭证明文在 env，proxy_env_key 引用之，
    不落库明文）；relay 单元/浏览器扩实例是部署动作，不在本端点做。"""
    principal.require("account:operate")
    region_gb = body.region_gb.strip()
    if not (region_gb.isdigit() and len(region_gb) == 6):
        raise HTTPException(status_code=400, detail={"code": "bad_region_gb"})
    existing = session.scalar(
        select(CollectionRegion).where(CollectionRegion.region_gb == region_gb)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "region_already_exists"})
    now = now_utc()
    region = CollectionRegion(
        pub_id=new_pub_id("rgn"),
        region_gb=region_gb,
        name=body.name,
        source="wukong",
        proxy_env_key=body.proxy_env_key,
        relay_unit=body.relay_unit,
        state="ok",
        created_at=now,
        updated_at=now,
    )
    session.add(region)
    session.flush()
    _emit_event(
        session,
        "region_created",
        actor=_actor(principal),
        region_id=region.id,
        new_value={
            "region_gb": region_gb,
            "name": body.name,
            "proxy_env_key": body.proxy_env_key,
            "relay_unit": body.relay_unit,
        },
    )
    session.commit()
    return _region_view(region)


@router.post("/collection-regions/{gb}/probe", response_model=RegionProbeResult)
def probe_region(
    gb: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> RegionProbeResult:
    """立即巡检一个地域的 relay 出口（结果经 governor.record_region_probe 落库）。"""
    principal.require("account:operate")
    try:
        result = probe_collection_region(session, gb)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "region_not_found"}) from exc
    session.commit()
    return RegionProbeResult(**result)
