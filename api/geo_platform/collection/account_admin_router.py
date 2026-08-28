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

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from workflows.activities.assist_notify import push_captcha_assist

from ..identity.policy import Principal, get_principal
from ..otp.extract import PHONE_RE, mask_phone
from ..pagination import decode_keyset_cursor, encode_keyset_cursor, set_cursor_headers
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
from .otp_bridge import upsert_phone_account
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
    """账号管理页行。

    ``phone`` 只对具备 ``account:operate`` 的管理员/操作员返回；只读审核角色
    继续只拿 ``phone_masked``，避免扩大完整号码的可见范围。
    """

    phone_account_pub_id: str
    phone: str | None
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
    owner_note: str | None = Field(default=None, max_length=200)


class PhoneAccountPatch(StrictModel):
    owner_note: str | None = Field(..., max_length=200)


class PlatformAccountCreate(StrictModel):
    """Create the missing phone x platform dispatch row.

    Binding is deliberately explicit: a phone registration or quota observation must
    never silently become a dispatchable collection account.
    """

    platform: Literal["doubao", "yiyan", "deepseek", "yuanbao", "tongyi"]
    region_gb: str
    browser_instance_key: str
    quota_day: int | None = Field(default=None, ge=0)
    quota_week: int | None = Field(default=None, ge=0)
    quota_year: int | None = Field(default=None, ge=0)
    confirm: bool = False


class OtpRegistrySyncResult(StrictModel):
    scanned: int
    created: int
    updated: int
    unchanged: int


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


class AccountQuotaObservationView(StrictModel):
    """账号管理页的平台额度安全投影。

    真源是 ``collection_account_event(event_type='quota_observation')``；响应只暴露
    白名单字段，绝不透传平台原始响应、完整手机号、平台用户标识或探测证据。
    ``observed_window_count`` 可以是日志下限/估算值，精度由 ``count_kind`` 明示，
    不能冒充官方固定额度。
    """

    observation_pub_id: str
    phone_account_pub_id: str
    phone_masked: str
    platform: str
    observed_browser_instance_key: str
    observed_region_gb: str | None
    mode: Literal["normal", "deep_think", "unknown"]
    account_tier: Literal["free", "subscriber", "unknown"]
    quota_state: Literal["available", "exhausted", "unknown"]
    window_type: Literal["rolling", "calendar", "unknown"]
    window_days: int | None
    observed_window_count: int | None
    daily_equivalent: float | None
    count_kind: Literal["lower_bound", "estimate", "platform_exact", "unknown"]
    reset_at: datetime | None
    observed_at: datetime
    source: Literal["platform", "platform_and_logs", "manual", "unknown"]


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


def _quota_enum(value: object, allowed: set[str], default: str = "unknown") -> str:
    return value if isinstance(value, str) and value in allowed else default


def _quota_bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    # bool 是 int 子类，但额度协议不接受 true/false 冒充数字。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _quota_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _quota_observation_view(
    event: CollectionAccountEvent,
    phone: CollectionPhoneAccount,
    browser: CollectionBrowser,
) -> AccountQuotaObservationView | None:
    payload = event.new_value
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    mode = _quota_enum(payload.get("mode"), {"normal", "deep_think"})
    tier = _quota_enum(payload.get("account_tier"), {"free", "subscriber"})
    state = _quota_enum(payload.get("quota_state"), {"available", "exhausted"})
    window_type = _quota_enum(payload.get("window_type"), {"rolling", "calendar"})
    count_kind = _quota_enum(
        payload.get("count_kind"), {"lower_bound", "estimate", "platform_exact"}
    )
    source = _quota_enum(payload.get("source"), {"platform", "platform_and_logs", "manual"})
    window_days = _quota_bounded_int(payload.get("window_days"), minimum=1, maximum=366)
    observed_count = _quota_bounded_int(
        payload.get("observed_window_count"), minimum=0, maximum=1_000_000
    )
    daily_equivalent = (
        round(observed_count / window_days, 1)
        if observed_count is not None and window_days is not None
        else None
    )
    return AccountQuotaObservationView(
        observation_pub_id=event.pub_id,
        phone_account_pub_id=phone.pub_id,
        phone_masked=mask_phone(phone.phone),
        platform=browser.platform,
        observed_browser_instance_key=browser.instance_key,
        observed_region_gb=browser.region_gb,
        mode=mode,  # type: ignore[arg-type]
        account_tier=tier,  # type: ignore[arg-type]
        quota_state=state,  # type: ignore[arg-type]
        window_type=window_type,  # type: ignore[arg-type]
        window_days=window_days,
        observed_window_count=observed_count,
        daily_equivalent=daily_equivalent,
        count_kind=count_kind,  # type: ignore[arg-type]
        reset_at=_quota_datetime(payload.get("reset_at")),
        observed_at=event.created_at,
        source=source,  # type: ignore[arg-type]
    )


def _phone_row(
    phone: CollectionPhoneAccount,
    platform_rows: list[CollectionPlatformAccount],
    *,
    reveal_phone: bool,
) -> PhoneAccountRow:
    by_platform = {row.platform: row for row in platform_rows}
    return PhoneAccountRow(
        phone_account_pub_id=phone.pub_id,
        phone=phone.phone if reveal_phone else None,
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


def _binding_conflict_code(exc: IntegrityError) -> str | None:
    """Map the two dispatch-binding uniqueness guards to stable API errors."""

    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name == "uq_collection_platform_account_phone_platform":
        return "platform_account_already_exists"
    if constraint_name == "uq_collection_platform_account_browser_instance_key":
        return "browser_already_bound"
    return None


def _raise_binding_integrity_error(session: Session, exc: IntegrityError) -> None:
    session.rollback()
    code = _binding_conflict_code(exc)
    if code is not None:
        raise HTTPException(status_code=409, detail={"code": code}) from exc
    raise exc


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


@router.get(
    "/collection-accounts",
    response_model=list[PhoneAccountRow],
    responses={
        200: {
            "headers": {
                "X-Next-Cursor": {"schema": {"type": "string"}},
                "X-Has-More": {"schema": {"type": "boolean"}},
                "X-Total-Count": {"schema": {"type": "integer"}},
            }
        }
    },
)
def list_collection_accounts(
    response: Response,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[PhoneAccountRow]:
    principal.require("account:read")
    filters: dict[str, str | None] = {}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="collection-phone-accounts",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    statement = select(CollectionPhoneAccount)
    if anchor is not None:
        statement = statement.where(
            or_(
                CollectionPhoneAccount.created_at < anchor.created_at,
                and_(
                    CollectionPhoneAccount.created_at == anchor.created_at,
                    CollectionPhoneAccount.pub_id < anchor.pub_id,
                ),
            )
        )
    phones = list(
        session.scalars(
            statement.order_by(
                CollectionPhoneAccount.created_at.desc(), CollectionPhoneAccount.pub_id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(phones) > limit
    visible = phones[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="collection-phone-accounts",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last.created_at,
            pub_id=last.pub_id,
        )
    total_count = session.scalar(select(func.count()).select_from(CollectionPhoneAccount)) or 0
    set_cursor_headers(
        response,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=int(total_count),
    )
    rows: list[PhoneAccountRow] = []
    for phone in visible:
        platform_rows = list(
            session.scalars(
                select(CollectionPlatformAccount).where(
                    CollectionPlatformAccount.phone_account_id == phone.id
                )
            )
        )
        rows.append(
            _phone_row(
                phone,
                platform_rows,
                reveal_phone=principal.allows("account:operate"),
            )
        )
    return rows


@router.get(
    "/collection-account-quota-observations",
    response_model=list[AccountQuotaObservationView],
)
def list_collection_account_quota_observations(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[AccountQuotaObservationView]:
    """返回每个手机号 × 平台 × mode 最新一条额度观测。

    额度属于登录账号而不是出口地域，因此事件必须显式关联 ``phone_account_id``；
    browser/region 仅作为最近观测来源。接口最多扫描最近 200 条审计事件，按
    ``(phone, platform, mode)`` 去重；不返回 ``new_value`` 原文，也不再把没有
    手机号归属的历史 browser-only 事件展示成账号额度。
    """

    principal.require("account:read")
    events = list(
        session.scalars(
            select(CollectionAccountEvent)
            .where(CollectionAccountEvent.event_type == "quota_observation")
            .order_by(CollectionAccountEvent.created_at.desc())
            .limit(200)
        )
    )
    phone_cache: dict[int, CollectionPhoneAccount | None] = {}
    browser_cache: dict[int, CollectionBrowser | None] = {}
    views: list[AccountQuotaObservationView] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        if event.phone_account_id is None or event.browser_id is None:
            continue
        if event.phone_account_id not in phone_cache:
            phone_cache[event.phone_account_id] = session.get(
                CollectionPhoneAccount, event.phone_account_id
            )
        if event.browser_id not in browser_cache:
            browser_cache[event.browser_id] = session.get(CollectionBrowser, event.browser_id)
        phone = phone_cache[event.phone_account_id]
        browser = browser_cache[event.browser_id]
        if phone is None or browser is None:
            continue
        view = _quota_observation_view(event, phone, browser)
        if view is None:
            continue
        key = (view.phone_account_pub_id, view.platform, view.mode)
        if key in seen:
            continue
        seen.add(key)
        views.append(view)
    return sorted(
        views,
        key=lambda view: (
            view.phone_masked,
            view.platform,
            view.mode,
        ),
    )


@router.post("/collection-accounts", response_model=PhoneAccountRow, status_code=201)
def create_collection_account(
    body: PhoneAccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PhoneAccountRow:
    principal.require("account:operate")
    phone = body.phone.strip()
    owner_note = body.owner_note.strip() if body.owner_note else None
    owner_note = owner_note or None
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
        owner_note=owner_note,
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
        new_value={"phone_masked": mask_phone(phone), "owner_note": owner_note},
    )
    session.commit()
    return _phone_row(row, [], reveal_phone=True)


@router.patch("/collection-accounts/{pub_id}", response_model=PhoneAccountRow)
def patch_collection_account(
    pub_id: str,
    body: PhoneAccountPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PhoneAccountRow:
    """修改手机号备注；空字符串与 ``null`` 都表示清空备注。"""

    principal.require("account:operate")
    phone = _find_phone(session, pub_id)
    owner_note = body.owner_note.strip() if body.owner_note else None
    owner_note = owner_note or None
    platform_rows = list(
        session.scalars(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.phone_account_id == phone.id
            )
        )
    )
    if owner_note == phone.owner_note:
        return _phone_row(phone, platform_rows, reveal_phone=True)

    old_note = phone.owner_note
    phone.owner_note = owner_note
    phone.updated_at = now_utc()
    session.flush()
    _emit_event(
        session,
        "phone_account_note_changed",
        actor=_actor(principal),
        phone_account_id=phone.id,
        old_value={"owner_note": old_note},
        new_value={"owner_note": owner_note},
    )
    session.commit()
    return _phone_row(phone, platform_rows, reveal_phone=True)


@router.post(
    "/collection-accounts/{pub_id}/platform-accounts",
    response_model=PlatformAccountView,
    status_code=201,
)
def create_platform_account(
    pub_id: str,
    body: PlatformAccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PlatformAccountView:
    """Create an audited phone x platform dispatch binding.

    The browser, platform and egress region must agree in the same request.  This is
    intentionally separate from phone registration and quota observations so neither
    operation can unexpectedly change collection routing.
    """

    principal.require("account:operate")
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail={"code": "platform_account_binding_requires_confirmation"},
        )
    phone = _find_phone(session, pub_id)
    if phone.state != "active":
        raise HTTPException(status_code=409, detail={"code": "phone_account_not_active"})
    existing = session.scalar(
        select(CollectionPlatformAccount).where(
            CollectionPlatformAccount.phone_account_id == phone.id,
            CollectionPlatformAccount.platform == body.platform,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "platform_account_already_exists"})

    region = session.scalar(
        select(CollectionRegion).where(CollectionRegion.region_gb == body.region_gb)
    )
    if region is None or region.state != "ok":
        raise HTTPException(status_code=400, detail={"code": "region_not_available"})
    browser = session.scalar(
        select(CollectionBrowser).where(CollectionBrowser.instance_key == body.browser_instance_key)
    )
    if browser is None:
        raise HTTPException(status_code=404, detail={"code": "browser_not_found"})
    if browser.platform != body.platform:
        raise HTTPException(status_code=409, detail={"code": "browser_platform_mismatch"})
    if browser.region_gb != body.region_gb:
        raise HTTPException(status_code=409, detail={"code": "region_ip_mismatch"})
    bound = session.scalar(
        select(CollectionPlatformAccount).where(
            CollectionPlatformAccount.browser_instance_key == body.browser_instance_key
        )
    )
    if bound is not None:
        raise HTTPException(status_code=409, detail={"code": "browser_already_bound"})

    now = now_utc()
    account = CollectionPlatformAccount(
        pub_id=new_pub_id("pac"),
        phone_account_id=phone.id,
        platform=body.platform,
        region_gb=body.region_gb,
        quota_day=body.quota_day,
        quota_week=body.quota_week,
        quota_year=body.quota_year,
        used_today=0,
        used_week=0,
        used_year=0,
        runtime_state="idle",
        state_reason="created_by_operator",
        state_updated_at=now,
        browser_instance_key=body.browser_instance_key,
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(account)
        session.flush()
        _emit_event(
            session,
            "platform_account_created",
            actor=_actor(principal),
            phone_account_id=phone.id,
            platform_account_id=account.id,
            browser_id=browser.id,
            region_id=region.id,
            new_value={
                "platform": body.platform,
                "region_gb": body.region_gb,
                "browser_instance_key": body.browser_instance_key,
                "quota_day": body.quota_day,
                "quota_week": body.quota_week,
                "quota_year": body.quota_year,
            },
        )
        session.commit()
    except IntegrityError as exc:
        _raise_binding_integrity_error(session, exc)
    return PlatformAccountView(
        **_platform_cell(account).model_dump(), phone_account_pub_id=phone.pub_id
    )


def _otp_registry_entries() -> list[dict[str, Any]]:
    """Read the persistent OTP registry for an explicit admin reconciliation.

    The setup page is deliberately unable to enumerate this file. Only the
    authenticated ``account:operate`` endpoint below may consume it, and its
    response contains counts rather than phone numbers.
    """

    configured = os.environ.get("GEO_OTP_REGISTRY_PATH", "").strip()
    path = Path(configured or "runtime/otp_registered_numbers.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning(
            "otp_registry_admin_sync_unreadable",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail={"code": "otp_registry_unreadable"}) from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=503, detail={"code": "otp_registry_unreadable"})
    return [entry for entry in payload if isinstance(entry, dict)]


@router.post(
    "/collection-accounts/sync-otp-registry",
    response_model=OtpRegistrySyncResult,
)
def sync_otp_registry_accounts(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> OtpRegistrySyncResult:
    """Reconcile config-page registrations into the account-governance table.

    Normal registrations already upsert this table. This idempotent repair path
    covers legacy registrations and temporary DB failures, then the UI reloads
    ``GET /collection-accounts`` to show the result immediately.
    """

    principal.require("account:operate")
    entries = _otp_registry_entries()
    created = 0
    updated = 0
    unchanged = 0
    scanned = 0
    for entry in entries:
        phone = str(entry.get("phone") or "").strip()
        if not PHONE_RE.fullmatch(phone):
            continue
        scanned += 1
        slot = re.sub(r"\s+", " ", str(entry.get("slot") or "")).strip()[:16]
        carrier = re.sub(r"\s+", " ", str(entry.get("carrier") or "")).strip()[:24]
        owner_note = " ".join(part for part in (slot, carrier) if part) or "OTP 配置页注册"
        existing = session.scalar(
            select(CollectionPhoneAccount).where(CollectionPhoneAccount.phone == phone)
        )
        old_note = existing.owner_note if existing is not None else None
        row = upsert_phone_account(session, phone=phone, owner_note=owner_note)
        if existing is None:
            created += 1
            change = "created"
        elif old_note != row.owner_note:
            updated += 1
            change = "updated"
        else:
            unchanged += 1
            continue
        _emit_event(
            session,
            "otp_registry_sync",
            actor=_actor(principal),
            phone_account_id=row.id,
            new_value={
                "source": "otp_registry",
                "change": change,
                "phone_masked": mask_phone(phone),
            },
        )
    session.commit()
    return OtpRegistrySyncResult(
        scanned=scanned,
        created=created,
        updated=updated,
        unchanged=unchanged,
    )


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
    browser_changed = (
        "browser_instance_key" in provided
        and body.browser_instance_key != account.browser_instance_key
    )
    if browser_changed:
        old_value["browser_instance_key"] = account.browser_instance_key
        new_value["browser_instance_key"] = body.browser_instance_key
    region_changed = "region_gb" in new_value
    effective_browser_key = (
        body.browser_instance_key
        if "browser_instance_key" in provided
        else account.browser_instance_key
    )
    if (region_changed or browser_changed) and effective_browser_key is not None:
        browser = session.scalar(
            select(CollectionBrowser).where(CollectionBrowser.instance_key == effective_browser_key)
        )
        if browser is None:
            raise HTTPException(status_code=404, detail={"code": "browser_not_found"})
        if browser.platform != account.platform:
            raise HTTPException(status_code=409, detail={"code": "browser_platform_mismatch"})
        if browser.region_gb != effective_region:
            raise HTTPException(status_code=409, detail={"code": "region_ip_mismatch"})
        bound = session.scalar(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.browser_instance_key == effective_browser_key
            )
        )
        if bound is not None and bound.id != account.id:
            raise HTTPException(status_code=409, detail={"code": "browser_already_bound"})
    for field_name in ("quota_day", "quota_week", "quota_year"):
        if field_name in provided and getattr(body, field_name) != getattr(account, field_name):
            old_value[field_name] = getattr(account, field_name)
            new_value[field_name] = getattr(body, field_name)
    try:
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
    except IntegrityError as exc:
        _raise_binding_integrity_error(session, exc)
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


@router.get(
    "/collection-accounts/{pub_id}/events",
    response_model=list[AccountEventView],
    responses={
        200: {
            "headers": {
                "X-Next-Cursor": {"schema": {"type": "string"}},
                "X-Has-More": {"schema": {"type": "boolean"}},
                "X-Total-Count": {"schema": {"type": "integer"}},
            }
        }
    },
)
def list_account_events(
    pub_id: str,
    response: Response,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[AccountEventView]:
    """该手机号（含其平台行）的完整审计事件游标页。"""
    principal.require("account:read")
    phone = _find_phone(session, pub_id)
    platform_rows = list(
        session.scalars(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.phone_account_id == phone.id
            )
        )
    )
    platform_ids = [row.id for row in platform_rows]
    scope = CollectionAccountEvent.phone_account_id == phone.id
    if platform_ids:
        scope = or_(scope, CollectionAccountEvent.platform_account_id.in_(platform_ids))
    filters = {"phone_account_pub_id": pub_id}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="collection-account-events",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    statement = select(CollectionAccountEvent).where(scope)
    if anchor is not None:
        statement = statement.where(
            or_(
                CollectionAccountEvent.created_at < anchor.created_at,
                and_(
                    CollectionAccountEvent.created_at == anchor.created_at,
                    CollectionAccountEvent.pub_id < anchor.pub_id,
                ),
            )
        )
    events = list(
        session.scalars(
            statement.order_by(
                CollectionAccountEvent.created_at.desc(), CollectionAccountEvent.pub_id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(events) > limit
    visible = events[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="collection-account-events",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last.created_at,
            pub_id=last.pub_id,
        )
    total_count = session.scalar(
        select(func.count()).select_from(CollectionAccountEvent).where(scope)
    )
    set_cursor_headers(
        response,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=int(total_count or 0),
    )
    browser_pub_cache: dict[int, str] = {}
    region_pub_cache: dict[int, str] = {}
    views: list[AccountEventView] = []
    for event in visible:
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


@router.get(
    "/collection-browsers",
    response_model=list[CollectionBrowserView],
    responses={
        200: {
            "headers": {
                "X-Next-Cursor": {"schema": {"type": "string"}},
                "X-Has-More": {"schema": {"type": "boolean"}},
                "X-Total-Count": {"schema": {"type": "integer"}},
            }
        }
    },
)
def list_collection_browsers(
    response: Response,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CollectionBrowserView]:
    principal.require("account:read")
    filters: dict[str, str | None] = {}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="collection-browsers",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    statement = select(CollectionBrowser)
    if anchor is not None:
        statement = statement.where(
            or_(
                CollectionBrowser.created_at < anchor.created_at,
                and_(
                    CollectionBrowser.created_at == anchor.created_at,
                    CollectionBrowser.pub_id < anchor.pub_id,
                ),
            )
        )
    browsers = list(
        session.scalars(
            statement.order_by(
                CollectionBrowser.created_at.desc(), CollectionBrowser.pub_id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(browsers) > limit
    visible = browsers[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="collection-browsers",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last.created_at,
            pub_id=last.pub_id,
        )
    total_count = session.scalar(select(func.count()).select_from(CollectionBrowser)) or 0
    set_cursor_headers(
        response,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=int(total_count),
    )
    views: list[CollectionBrowserView] = []
    for browser in visible:
        runtime = probe_browser_runtime(browser.systemd_unit, browser.instance_key)
        bound_accounts = list(
            session.scalars(
                select(CollectionPlatformAccount).where(
                    CollectionPlatformAccount.browser_instance_key == browser.instance_key
                )
            )
        )
        bindings = {account.platform: _phone_pub_id(session, account) for account in bound_accounts}
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
    browser = session.scalar(select(CollectionBrowser).where(CollectionBrowser.instance_key == key))
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
    browser = session.scalar(select(CollectionBrowser).where(CollectionBrowser.instance_key == key))
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
    regions = list(session.scalars(select(CollectionRegion).order_by(CollectionRegion.created_at)))
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
