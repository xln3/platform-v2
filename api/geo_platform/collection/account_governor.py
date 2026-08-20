"""采集账号治理状态机唯一入口（migration s06_0022，设计文档 caiji-0813 §6.1）。

检测层（验收门/banner 解析/失败收敛）、管理页、API 都经本类改写实体状态；
调度（browser_router，下一 worker）经 ``resolve_collectable`` 消费实体状态。

- DB 访问模式照 run_service.py：``conn`` 是 SQLAlchemy ``Session``（worker 侧用
  ``WorkerSessionLocal``）。调用方拥有事务——本类只 flush，不 commit。
- 并发：常驻浏览器 fence 已按实例串行化采集执行，账号行与实例 1:1 绑定
  （一实例一平台一号）→ 账号行实际单写者，本类不另加 advisory lock。
- 时间一律 UTC；平台额度日重置点缺省 = 北京时间次日 00:00 + 0~30min 抖动
  （错峰，平台实际重置口径待 P1 逐平台 QuotaProbe 实证校准）。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from ..tenancy.models import now_utc
from .account_models import (
    CollectionAccountEvent,
    CollectionBrowser,
    CollectionPlatformAccount,
    CollectionRegion,
)

log = structlog.get_logger()

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_QUOTA_RESET_JITTER_S = 1800  # 重置点抖动上限（秒）

# 同类失败连续熔断阈值 / 实例级熔断时长（设计文档 §5.3：256 次硬撞 → 压到 3 次）
_ERROR_STREAK_BREAKER = 3
_INSTANCE_BREAKER_TTL = timedelta(hours=2)

# 墙命中证据截断（命中原文进审计，防整段答案灌库）
_EVIDENCE_MAX = 500

# record_task_outcome 去重扫描窗（同 task 重试重放在时间上相邻，窗口足够）
_OUTCOME_DEDUP_WINDOW = 50

# runtime_state 合法迁移表（程序层校验，列不加 CHECK——词表演进不该要 migration）。
# 同态迁移（X→X）恒合法 = 幂等重申（更新 reason/until 字段并照记事件）。
_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle": frozenset({"running", "quota_exhausted", "muted", "captcha", "error"}),
    "running": frozenset({"idle", "quota_exhausted", "muted", "captcha", "error"}),
    "quota_exhausted": frozenset({"idle", "error"}),
    "muted": frozenset({"idle", "error"}),
    "captcha": frozenset({"idle", "error"}),
    "error": frozenset({"idle"}),
}
_ALL_STATES = frozenset(_TRANSITIONS)

_WALL_TYPES = frozenset({"wall_quota", "wall_muted", "wall_captcha", "wall_refusal"})


def _next_quota_reset(now: datetime) -> datetime:
    """平台额度日重置点缺省：北京时间次日 00:00 + 0~30min 抖动（UTC 表达）。"""
    shanghai = now.astimezone(_SHANGHAI)
    next_day = shanghai.date() + timedelta(days=1)
    midnight = datetime(next_day.year, next_day.month, next_day.day, tzinfo=_SHANGHAI)
    jitter = secrets.randbelow(_QUOTA_RESET_JITTER_S + 1)
    return (midnight + timedelta(seconds=jitter)).astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class AccountGovernor:
    """采集账号/浏览器/地域实体的状态机与额度台账唯一入口。

    ``conn`` = SQLAlchemy ``Session``（照 run_service.py 的 DB 访问模式）。
    全部方法只 flush；事务边界（commit/rollback）由调用方持有。
    """

    def __init__(self, conn: Session) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # 检测层入口：墙命中
    # ------------------------------------------------------------------

    def report_wall(
        self,
        *,
        platform: str,
        wall_type: str,
        evidence: str,
        browser_instance_key: str | None = None,
        run_pub_id: str | None = None,
        mode: str | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """墙命中统一出口（验收门词表/banner 解析撞墙后调用）。

        - wall_quota → 账号 quota_exhausted + quota_resume_at（until 缺省 =
          北京时间次日 00:00+0~30min 抖动）；wall_muted → muted + muted_until=until；
          wall_captcha → captcha；wall_refusal 只记事件不改状态。
        - 优先落 platform_account（经 browser_instance_key 匹配）；无账号行则落
          collection_browser 实例级兜底（wall_quota→breaker_until、
          wall_muted→muted_until、wall_captcha→activity='captcha'）。
        - 每次写 wall_hit 事件（evidence 截断 500 字）；账号路径的状态迁移
          若被迁移表拒绝（如实记 warning）不阻塞事件落库。
        - 返回 dict：target（platform_account/browser/none）、两 pub_id、
          runtime_state、until（生效的封禁/恢复点）、event_pub_id。
        """
        if wall_type not in _WALL_TYPES:
            raise ValueError(f"unknown_wall_type:{wall_type}")
        now = now_utc()
        evidence_cut = evidence[:_EVIDENCE_MAX]
        wall_payload: dict[str, Any] = {
            "wall_type": wall_type,
            "platform": platform,
            "mode": mode,
            "browser_instance_key": browser_instance_key,
            "until": _iso(until),
        }
        account = self._find_account(platform=platform, browser_instance_key=browser_instance_key)
        if account is not None:
            new_state: str | None = None
            effective_until: datetime | None = None
            if wall_type == "wall_quota":
                effective_until = until if until is not None else _next_quota_reset(now)
                new_state = "quota_exhausted"
            elif wall_type == "wall_muted":
                effective_until = until
                new_state = "muted"
            elif wall_type == "wall_captcha":
                new_state = "captcha"
            # wall_refusal 只记事件，不改状态
            if new_state is not None:
                try:
                    self.set_runtime_state(
                        platform_account_id=account.id,
                        new_state=new_state,
                        reason=evidence_cut,
                        until=effective_until,
                        actor="wall_detector",
                        run_pub_id=run_pub_id,
                    )
                except ValueError:
                    # 迁移表拒绝（如 error 态又撞墙）：状态不动，事件照落
                    log.warning(
                        "account_wall_state_transition_rejected",
                        platform=platform,
                        wall_type=wall_type,
                        runtime_state=account.runtime_state,
                        new_state=new_state,
                    )
                    new_state = None
                    effective_until = None
            event = self._emit(
                "wall_hit",
                phone_account_id=account.phone_account_id,
                platform_account_id=account.id,
                new_value=wall_payload | {"runtime_state": new_state},
                evidence=evidence_cut,
                actor="wall_detector",
                run_pub_id=run_pub_id,
            )
            return {
                "target": "platform_account",
                "platform_account_pub_id": account.pub_id,
                "browser_pub_id": None,
                "wall_type": wall_type,
                "runtime_state": new_state,
                "until": effective_until,
                "event_pub_id": event.pub_id,
            }

        # 无账号行 → 实例级兜底（breaker_until / muted_until / activity）
        browser = self._find_browser(browser_instance_key) if browser_instance_key else None
        if browser is not None:
            effective_until = None
            if wall_type == "wall_quota":
                browser.breaker_until = until if until is not None else _next_quota_reset(now)
                effective_until = browser.breaker_until
            elif wall_type == "wall_muted":
                browser.muted_until = until
                effective_until = until
            elif wall_type == "wall_captcha":
                browser.activity = "captcha"
            browser.updated_at = now
            self._conn.flush()
            event = self._emit(
                "wall_hit",
                browser_id=browser.id,
                new_value=wall_payload | {"fallback": "browser"},
                evidence=evidence_cut,
                actor="wall_detector",
                run_pub_id=run_pub_id,
            )
            return {
                "target": "browser",
                "platform_account_pub_id": None,
                "browser_pub_id": browser.pub_id,
                "wall_type": wall_type,
                "runtime_state": None,
                "until": effective_until,
                "event_pub_id": event.pub_id,
            }

        # 账号行与实例行都没有：事件照落（无 FK），如实返回 target=none
        log.warning(
            "account_wall_orphan",
            platform=platform,
            wall_type=wall_type,
            browser_instance_key=browser_instance_key,
        )
        event = self._emit(
            "wall_hit",
            new_value=wall_payload,
            evidence=evidence_cut,
            actor="wall_detector",
            run_pub_id=run_pub_id,
        )
        return {
            "target": "none",
            "platform_account_pub_id": None,
            "browser_pub_id": None,
            "wall_type": wall_type,
            "runtime_state": None,
            "until": None,
            "event_pub_id": event.pub_id,
        }

    # ------------------------------------------------------------------
    # 检测层入口：任务终态（用量台账 + 失败收敛）
    # ------------------------------------------------------------------

    def record_task_outcome(
        self,
        *,
        platform: str,
        browser_instance_key: str | None,
        outcome: str,
        error_type: str | None = None,
        run_pub_id: str | None = None,
        mode: str | None = None,
        task_pub_id: str | None = None,
    ) -> None:
        """采集任务终态上报：成功记用量台账，同类失败连续 ≥3 熔断。

        - 成功 → used_today/week/year +1（账号路径），并把已由真实成功证伪的浏览器
          captcha/activity/breaker 陈旧态恢复为 idle（两条路径均清 error_streak）。
        - 失败（outcome != "success"）→ 账号路径：同类失败连续 ≥3 →
          runtime_state='error' + breaker 事件；实例路径（无账号行）：
          error_streak+1，≥3 → breaker_until = now+2h + breaker 事件。
        - lazy reset：读写用量前若过 quota_reset_at 则 used_today 清零并重排
          下一重置点；跨 ISO 周/年一并清 used_week/used_year（见 _lazy_quota_reset）。
        - 幂等：同 task 重复上报（Temporal activity 重试/重放）不重复计数。
          去重键 = (账号或实例, run_pub_id, mode, task_pub_id, outcome, error_type)。
          ``task_pub_id``（2026-08-14 起）= collection_task.pub_id 题级标识：
          逐题上报必须带——同 (run,platform,mode) 不同题的终态各占一次计数；
          缺省 None 保持旧 batch 级语义（同 (run,platform,mode) 同元组的多次
          上报被去重合并）。事件 payload 记录该键，旧 payload 无此键按 None 匹配。
          去重扫描窗 = 最近 50 条 task_outcome 事件（重试重放时间相邻，窗口足够）。
        """
        now = now_utc()
        account = self._find_account(platform=platform, browser_instance_key=browser_instance_key)
        if account is not None:
            if self._outcome_already_recorded(
                platform_account_id=account.id,
                run_pub_id=run_pub_id,
                mode=mode,
                task_pub_id=task_pub_id,
                outcome=outcome,
                error_type=error_type,
            ):
                return
            self._lazy_quota_reset(account, now)
            if outcome == "success":
                account.used_today += 1
                account.used_week += 1
                account.used_year += 1
            account.updated_at = now
            self._conn.flush()
            browser = self._find_browser(account.browser_instance_key)
            if outcome == "success" and browser is not None:
                # 一次真实成功比陈旧的验证码/熔断记录更新；人工过码后首个受控任务
                # 用此路径自动解锁调度。禁言时间是独立硬状态，不在这里清除。
                browser.activity = "idle"
                browser.error_streak = 0
                browser.breaker_until = None
                browser.updated_at = now
                self._conn.flush()
            region = (
                self._conn.scalar(
                    select(CollectionRegion).where(CollectionRegion.region_gb == account.region_gb)
                )
                if account.region_gb
                else None
            )
            self._emit(
                "task_outcome",
                phone_account_id=account.phone_account_id,
                platform_account_id=account.id,
                new_value={
                    "platform": platform,
                    "mode": mode,
                    "task_pub_id": task_pub_id,
                    "outcome": outcome,
                    "error_type": error_type,
                    "used_today": account.used_today,
                    # Immutable, privacy-preserving sampling provenance.  Reports use
                    # this event payload, never the account/browser's later state.
                    "account_id_masked": sha256(account.pub_id.encode()).hexdigest()[:12],
                    "browser_instance": account.browser_instance_key,
                    "egress_region_gb": account.region_gb,
                    "egress_ip_sha256": (
                        sha256(browser.exit_ip.encode()).hexdigest()
                        if browser is not None and browser.exit_ip
                        else None
                    ),
                    "egress_probe_at": _iso(region.last_probe_at) if region is not None else None,
                    "egress_probe_state": region.state if region is not None else None,
                },
                actor="worker",
                run_pub_id=run_pub_id,
            )
            if outcome != "success":
                streak = self._failure_streak(account.id, error_type)
                if streak >= _ERROR_STREAK_BREAKER and account.runtime_state != "error":
                    self.set_runtime_state(
                        platform_account_id=account.id,
                        new_state="error",
                        reason=f"consecutive_failures:{error_type}",
                        actor="worker",
                        run_pub_id=run_pub_id,
                    )
                    self._emit(
                        "breaker",
                        phone_account_id=account.phone_account_id,
                        platform_account_id=account.id,
                        new_value={"streak": streak, "error_type": error_type},
                        actor="worker",
                        run_pub_id=run_pub_id,
                    )
            return

        # 无账号行 → 实例级兜底
        browser = self._find_browser(browser_instance_key) if browser_instance_key else None
        if browser is None:
            log.warning(
                "task_outcome_orphan",
                platform=platform,
                browser_instance_key=browser_instance_key,
                outcome=outcome,
            )
            return
        if self._outcome_already_recorded(
            browser_id=browser.id,
            run_pub_id=run_pub_id,
            mode=mode,
            task_pub_id=task_pub_id,
            outcome=outcome,
            error_type=error_type,
        ):
            return
        if outcome == "success":
            browser.activity = "idle"
            browser.error_streak = 0
            browser.breaker_until = None
        else:
            browser.error_streak += 1
        browser.updated_at = now
        self._conn.flush()
        self._emit(
            "task_outcome",
            browser_id=browser.id,
            new_value={
                "platform": platform,
                "mode": mode,
                "task_pub_id": task_pub_id,
                "outcome": outcome,
                "error_type": error_type,
                "error_streak": browser.error_streak,
                "account_id_masked": None,
                "browser_instance": browser.instance_key,
                "egress_region_gb": browser.region_gb,
                "egress_ip_sha256": (
                    sha256(browser.exit_ip.encode()).hexdigest() if browser.exit_ip else None
                ),
                "egress_probe_at": None,
                "egress_probe_state": None,
            },
            actor="worker",
            run_pub_id=run_pub_id,
        )
        if outcome != "success" and browser.error_streak >= _ERROR_STREAK_BREAKER:
            browser.breaker_until = now + _INSTANCE_BREAKER_TTL
            browser.updated_at = now
            self._conn.flush()
            self._emit(
                "breaker",
                browser_id=browser.id,
                new_value={
                    "streak": browser.error_streak,
                    "error_type": error_type,
                    "breaker_until": _iso(browser.breaker_until),
                },
                actor="worker",
                run_pub_id=run_pub_id,
            )

    # ------------------------------------------------------------------
    # 调度消费入口
    # ------------------------------------------------------------------

    def resolve_collectable(self, *, platform: str, region_gb: str) -> dict[str, Any] | None:
        """派题解析：platform ∧ region_gb ∧ idle ∧ 未超 quota ∧ 无熔断。

        命中 → dict {platform_account_pub_id, browser_instance_key, region_gb,
        remaining_today}（remaining_today=None 表示日预算不限）。
        不可派 → None，原因只进 structlog（读路径不落事件，避免每题一条审计
        噪音）：region_down（region 行存在且 state!='ok'；region 无行 = 未纳管，
        不拦截）/ no_account_registered（该平台该地域一行账号都没有）/
        region_ip_mismatch（账号绑定的浏览器行 region 与派题地域不一致 =
        配置错误 fail-closed，设计文档 §1.2 硬约束）/ no_collectable_account
        （有账号但全部不可用：非 idle、超预算、实例熔断/禁言中）。

        注意：本方法会写库——对过期 muted/quota_exhausted 做 lazy resume、对过
        quota_reset_at 的账号做 lazy 用量清零（「重置点后 lazy 清零」的读侧落点）。
        """
        now = now_utc()
        region = self._conn.scalar(
            select(CollectionRegion).where(CollectionRegion.region_gb == region_gb)
        )
        if region is not None and region.state != "ok":
            log.warning(
                "account_resolve_failed",
                reason="region_down",
                platform=platform,
                region_gb=region_gb,
                region_state=region.state,
            )
            return None
        candidates = list(
            self._conn.scalars(
                select(CollectionPlatformAccount).where(
                    CollectionPlatformAccount.platform == platform,
                    CollectionPlatformAccount.region_gb == region_gb,
                )
            )
        )
        if not candidates:
            log.warning(
                "account_resolve_failed",
                reason="no_account_registered",
                platform=platform,
                region_gb=region_gb,
            )
            return None
        saw_region_mismatch = False
        for account in candidates:
            self._lazy_resume(account, now)
            self._lazy_quota_reset(account, now)
            if account.runtime_state != "idle":
                continue
            if not self._quota_available(account):
                continue
            browser = (
                self._find_browser(account.browser_instance_key)
                if account.browser_instance_key
                else None
            )
            if browser is not None:
                if browser.region_gb and browser.region_gb != region_gb:
                    # 实例出口地域 ≠ 账号绑定地域 = 配置错误，fail-closed
                    saw_region_mismatch = True
                    log.warning(
                        "account_resolve_region_ip_mismatch",
                        platform=platform,
                        region_gb=region_gb,
                        platform_account_pub_id=account.pub_id,
                        browser_instance_key=browser.instance_key,
                        browser_region_gb=browser.region_gb,
                    )
                    continue
                if (browser.breaker_until is not None and browser.breaker_until > now) or (
                    browser.muted_until is not None and browser.muted_until > now
                ):
                    continue
            remaining_today = (
                None
                if account.quota_day is None
                else max(account.quota_day - account.used_today, 0)
            )
            return {
                "platform_account_pub_id": account.pub_id,
                "browser_instance_key": account.browser_instance_key,
                "region_gb": region_gb,
                "remaining_today": remaining_today,
            }
        reason = "region_ip_mismatch" if saw_region_mismatch else "no_collectable_account"
        log.warning(
            "account_resolve_failed",
            reason=reason,
            platform=platform,
            region_gb=region_gb,
        )
        return None

    # ------------------------------------------------------------------
    # 管理页 / API 入口
    # ------------------------------------------------------------------

    def set_runtime_state(
        self,
        *,
        platform_account_id: int,
        new_state: str,
        reason: str | None = None,
        until: datetime | None = None,
        actor: str = "system",
        run_pub_id: str | None = None,
    ) -> None:
        """runtime_state 迁移唯一入口：合法迁移校验 + state_transition 事件。

        非法迁移 / 未知状态 → ValueError（fail-loud，不静默纠正）。until 语义：
        muted→muted_until、quota_exhausted→quota_resume_at（缺省 = 下一日重置点）；
        running→current_run_pub_id=run_pub_id；idle→清空 current_run/两个 until。
        """
        account = self._conn.get(CollectionPlatformAccount, platform_account_id)
        if account is None:
            raise LookupError("platform_account_not_found")
        if new_state not in _ALL_STATES:
            raise ValueError(f"unknown_runtime_state:{new_state}")
        old_state = account.runtime_state
        if new_state != old_state and new_state not in _TRANSITIONS.get(old_state, frozenset()):
            raise ValueError(f"illegal_state_transition:{old_state}->{new_state}")
        now = now_utc()
        old_snapshot = self._state_snapshot(account)
        account.runtime_state = new_state
        account.state_reason = reason
        account.state_updated_at = now
        if new_state == "running":
            account.current_run_pub_id = run_pub_id
        elif new_state == "idle":
            account.current_run_pub_id = None
            account.muted_until = None
            account.quota_resume_at = None
        if new_state == "muted":
            account.muted_until = until
        elif new_state == "quota_exhausted":
            account.quota_resume_at = until if until is not None else _next_quota_reset(now)
        account.updated_at = now
        self._conn.flush()
        self._emit(
            "state_transition",
            phone_account_id=account.phone_account_id,
            platform_account_id=account.id,
            old_value=old_snapshot,
            new_value=self._state_snapshot(account) | {"reason": reason},
            actor=actor,
            run_pub_id=run_pub_id,
        )

    def record_region_probe(
        self,
        *,
        region_gb: str,
        ok: bool,
        exit_ip: str | None = None,
        note: str | None = None,
    ) -> None:
        """relay 巡检结果落 collection_region；状态翻转写 relay_probe 事件。

        region 行不存在则建档（来源缺省 wukong）。state 只在 ok/down 间自动
        翻转；人工标注的 arrears（欠费）不被探测覆盖（须人工恢复）。note 只进
        事件 evidence，不覆盖 region.note（人工备注）。
        """
        now = now_utc()
        region = self._conn.scalar(
            select(CollectionRegion).where(CollectionRegion.region_gb == region_gb)
        )
        if region is None:
            region = CollectionRegion(
                pub_id=new_pub_id("rgn"),
                region_gb=region_gb,
                state="ok",
                created_at=now,
                updated_at=now,
            )
            self._conn.add(region)
            self._conn.flush()
        old_state = region.state
        if exit_ip is not None:
            region.exit_ip_last = exit_ip
        region.last_probe_at = now
        if region.state != "arrears":
            region.state = "ok" if ok else "down"
        region.updated_at = now
        self._conn.flush()
        if region.state != old_state:
            self._emit(
                "relay_probe",
                region_id=region.id,
                old_value={"state": old_state},
                new_value={"state": region.state, "exit_ip": exit_ip},
                evidence=note,
                actor="relay_probe",
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _find_account(
        self, *, platform: str, browser_instance_key: str | None
    ) -> CollectionPlatformAccount | None:
        if browser_instance_key is None:
            return None
        return self._conn.scalar(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.platform == platform,
                CollectionPlatformAccount.browser_instance_key == browser_instance_key,
            )
        )

    def _find_browser(self, instance_key: str | None) -> CollectionBrowser | None:
        if instance_key is None:
            return None
        return self._conn.scalar(
            select(CollectionBrowser).where(CollectionBrowser.instance_key == instance_key)
        )

    @staticmethod
    def _state_snapshot(account: CollectionPlatformAccount) -> dict[str, Any]:
        return {
            "runtime_state": account.runtime_state,
            "muted_until": _iso(account.muted_until),
            "quota_resume_at": _iso(account.quota_resume_at),
            "current_run_pub_id": account.current_run_pub_id,
        }

    @staticmethod
    def _quota_available(account: CollectionPlatformAccount) -> bool:
        return not (
            (account.quota_day is not None and account.used_today >= account.quota_day)
            or (account.quota_week is not None and account.used_week >= account.quota_week)
            or (account.quota_year is not None and account.used_year >= account.quota_year)
        )

    def _lazy_quota_reset(self, account: CollectionPlatformAccount, now: datetime) -> None:
        """日重置点 lazy 清零：过 quota_reset_at → used_today=0 并重排下一重置点。

        周/年口径：以旧重置点（≈上次活动时间锚）与当前时刻的 ISO 周/年差分——
        跨周清 used_week、跨年清 used_year（无独立周/年重置点列，这是确定性
        最近似）。quota_reset_at 为 NULL（新行）只建档重排不清零。
        """
        reset_at = account.quota_reset_at
        if reset_at is None:
            account.quota_reset_at = _next_quota_reset(now)
            account.updated_at = now
            self._conn.flush()
            return
        if now < reset_at:
            return
        previous = reset_at.astimezone(_SHANGHAI)
        current = now.astimezone(_SHANGHAI)
        account.used_today = 0
        if previous.isocalendar()[:2] != current.isocalendar()[:2]:
            account.used_week = 0
        if previous.year != current.year:
            account.used_year = 0
        account.quota_reset_at = _next_quota_reset(now)
        account.updated_at = now
        self._conn.flush()

    def _lazy_resume(self, account: CollectionPlatformAccount, now: datetime) -> None:
        """过期的 muted / quota_exhausted 自动回 idle（写 state_auto_resume 事件）。

        muted_until / quota_resume_at 为 NULL 的不自动恢复（人工封禁走
        set_runtime_state 人工解除）。quota 恢复同时清 used_today 并重排日重置点
        （配额已刷新）。
        """
        resumed_from: str | None = None
        if (
            account.runtime_state == "muted"
            and account.muted_until is not None
            and account.muted_until <= now
        ):
            resumed_from = "muted"
        elif (
            account.runtime_state == "quota_exhausted"
            and account.quota_resume_at is not None
            and account.quota_resume_at <= now
        ):
            resumed_from = "quota_exhausted"
        if resumed_from is None:
            return
        old_snapshot = self._state_snapshot(account)
        account.runtime_state = "idle"
        account.muted_until = None
        account.quota_resume_at = None
        if resumed_from == "quota_exhausted":
            account.used_today = 0
            account.quota_reset_at = _next_quota_reset(now)
        account.state_reason = f"auto_resume_from_{resumed_from}"
        account.state_updated_at = now
        account.updated_at = now
        self._conn.flush()
        self._emit(
            "state_auto_resume",
            phone_account_id=account.phone_account_id,
            platform_account_id=account.id,
            old_value=old_snapshot,
            new_value=self._state_snapshot(account),
            actor="system",
        )

    def _recent_outcome_events(
        self,
        *,
        platform_account_id: int | None = None,
        browser_id: int | None = None,
        window: int = _OUTCOME_DEDUP_WINDOW,
    ) -> list[CollectionAccountEvent]:
        stmt = (
            select(CollectionAccountEvent)
            .where(CollectionAccountEvent.event_type == "task_outcome")
            .order_by(CollectionAccountEvent.id.desc())
            .limit(window)
        )
        if platform_account_id is not None:
            stmt = stmt.where(CollectionAccountEvent.platform_account_id == platform_account_id)
        if browser_id is not None:
            stmt = stmt.where(CollectionAccountEvent.browser_id == browser_id)
        return list(self._conn.scalars(stmt))

    def _outcome_already_recorded(
        self,
        *,
        platform_account_id: int | None = None,
        browser_id: int | None = None,
        run_pub_id: str | None,
        mode: str | None,
        task_pub_id: str | None,
        outcome: str,
        error_type: str | None,
    ) -> bool:
        for event in self._recent_outcome_events(
            platform_account_id=platform_account_id, browser_id=browser_id
        ):
            payload = event.new_value or {}
            if (
                event.run_pub_id == run_pub_id
                and payload.get("mode") == mode
                and payload.get("task_pub_id") == task_pub_id
                and payload.get("outcome") == outcome
                and payload.get("error_type") == error_type
            ):
                return True
        return False

    def _failure_streak(self, account_id: int, error_type: str | None) -> int:
        """最近同类失败连续次数（含刚落的本次；success 或异类 error_type 断链）。"""
        streak = 0
        for event in self._recent_outcome_events(platform_account_id=account_id, window=10):
            payload = event.new_value or {}
            if payload.get("outcome") == "success":
                break
            if payload.get("error_type") != error_type:
                break
            streak += 1
        return streak

    def _emit(
        self,
        event_type: str,
        *,
        phone_account_id: int | None = None,
        platform_account_id: int | None = None,
        browser_id: int | None = None,
        region_id: int | None = None,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        evidence: str | None = None,
        actor: str = "system",
        run_pub_id: str | None = None,
    ) -> CollectionAccountEvent:
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
            run_pub_id=run_pub_id,
            created_at=now_utc(),
        )
        self._conn.add(event)
        self._conn.flush()
        return event
