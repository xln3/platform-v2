"""account_governor 状态机/台账/派题解析单测（fake Session，绝不起真 PG）。

fake 先例照 test_resident_browser.py 的 _FakeDbSession 与
test_post_analysis_service.py 的 _FakeConnection：unit 层不起真 PG（dev 库可能
未迁移，且 2026-08-13 起 dev docker 栈已下线）。本文件的 _FakeSession 按 select
语句结构（实体 + 等值 where + order_by + limit）路由到内存行——governor 全部
查询都是这个形状；flush 负责分配 BIGSERIAL id（事件 id desc 排序依赖它）。

时间控制：monkeypatch 模块级 ``account_governor.now_utc``（governor 唯一时钟
来源），需要固定时刻的用例用 _FIXED_NOW。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from geo_platform.collection import account_governor
from geo_platform.collection.account_governor import AccountGovernor
from geo_platform.collection.account_models import (
    CollectionAccountEvent,
    CollectionBrowser,
    CollectionPlatformAccount,
    CollectionRegion,
)
from geo_platform.tenancy.ids import new_pub_id

_FIXED_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class _FakeSession:
    """governor 用到的最小 Session 假面：get/scalar/scalars/add/flush。"""

    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}
        self.scalar_statements: list[Any] = []

    def get(self, cls: type, pk: int) -> Any | None:
        for row in self.rows.get(cls, []):
            if row.id == pk:
                return row
        return None

    def scalar(self, stmt: Any) -> Any | None:
        self.scalar_statements.append(stmt)
        rows = self._select(stmt)
        return rows[0] if rows else None

    def scalars(self, stmt: Any) -> list[Any]:
        return list(self._select(stmt))

    def _select(self, stmt: Any) -> list[Any]:
        cls = stmt.column_descriptions[0]["entity"]
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            key = criterion.left.key
            value = criterion.right.value
            rows = [row for row in rows if getattr(row, key) == value]
        for order in stmt._order_by_clauses:
            key = order.element.key
            desc = "desc" in str(order.modifier)
            rows.sort(key=lambda row: getattr(row, key), reverse=desc)
        if stmt._offset is not None:
            rows = rows[stmt._offset :]
        if stmt._limit is not None:
            rows = rows[: stmt._limit]
        return rows

    def add(self, obj: Any) -> None:
        self.rows.setdefault(type(obj), []).append(obj)

    def flush(self) -> None:
        for cls, rows in self.rows.items():
            counter = self._ids.setdefault(cls, itertools.count(1))
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = next(counter)


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def governor(session: _FakeSession) -> AccountGovernor:
    return AccountGovernor(session)  # type: ignore[arg-type]


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_governor, "now_utc", lambda: _FIXED_NOW)


def _events(session: _FakeSession, event_type: str | None = None) -> list[CollectionAccountEvent]:
    rows = session.rows.get(CollectionAccountEvent, [])
    if event_type is None:
        return list(rows)
    return [row for row in rows if row.event_type == event_type]


def _seed_account(
    session: _FakeSession, *, platform: str = "doubao", **overrides: Any
) -> CollectionPlatformAccount:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("pac"),
        "phone_account_id": 1,
        "platform": platform,
        "region_gb": "310000",
        "runtime_state": "idle",
        "used_today": 0,
        "used_week": 0,
        "used_year": 0,
        "browser_instance_key": f"{platform}_sh",
        "created_at": _FIXED_NOW,
        "updated_at": _FIXED_NOW,
    }
    fields.update(overrides)
    row = CollectionPlatformAccount(**fields)
    session.add(row)
    session.flush()
    return row


def _seed_browser(session: _FakeSession, **overrides: Any) -> CollectionBrowser:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("brw"),
        "instance_key": "doubao_sh",
        "platform": "doubao",
        "region_gb": "310000",
        "activity": "idle",
        "error_streak": 0,
        "created_at": _FIXED_NOW,
        "updated_at": _FIXED_NOW,
    }
    fields.update(overrides)
    row = CollectionBrowser(**fields)
    session.add(row)
    session.flush()
    return row


def _seed_region(session: _FakeSession, **overrides: Any) -> CollectionRegion:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("rgn"),
        "region_gb": "310000",
        "state": "ok",
        "probe_success_streak": 0,
        "probe_failure_streak": 0,
        "last_probe_ok": None,
        "last_probe_note": None,
        "created_at": _FIXED_NOW,
        "updated_at": _FIXED_NOW,
    }
    fields.update(overrides)
    row = CollectionRegion(**fields)
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# set_runtime_state：合法迁移 / 非法拒绝
# ---------------------------------------------------------------------------


def test_set_runtime_state_legal_transition_records_event(
    session: _FakeSession, governor: AccountGovernor
) -> None:
    account = _seed_account(session)
    governor.set_runtime_state(
        platform_account_id=account.id,
        new_state="running",
        reason="dispatch",
        actor="worker",
        run_pub_id="run_X",
    )
    assert account.runtime_state == "running"
    assert account.current_run_pub_id == "run_X"
    assert account.state_reason == "dispatch"
    assert account.state_updated_at is not None
    events = _events(session, "state_transition")
    assert len(events) == 1
    assert events[0].old_value["runtime_state"] == "idle"
    assert events[0].new_value["runtime_state"] == "running"
    assert events[0].actor == "worker"
    assert events[0].run_pub_id == "run_X"
    assert events[0].platform_account_id == account.id


def test_set_runtime_state_idle_clears_blockers(
    session: _FakeSession, governor: AccountGovernor
) -> None:
    account = _seed_account(
        session,
        runtime_state="muted",
        muted_until=_FIXED_NOW + timedelta(hours=1),
        current_run_pub_id="run_Y",
    )
    governor.set_runtime_state(platform_account_id=account.id, new_state="idle")
    assert account.runtime_state == "idle"
    assert account.muted_until is None
    assert account.current_run_pub_id is None


def test_set_runtime_state_rejects_illegal_and_unknown(
    session: _FakeSession, governor: AccountGovernor
) -> None:
    account = _seed_account(session, runtime_state="quota_exhausted")
    with pytest.raises(ValueError, match="illegal_state_transition"):
        governor.set_runtime_state(platform_account_id=account.id, new_state="running")
    with pytest.raises(ValueError, match="unknown_runtime_state"):
        governor.set_runtime_state(platform_account_id=account.id, new_state="bogus")
    with pytest.raises(LookupError, match="platform_account_not_found"):
        governor.set_runtime_state(platform_account_id=999, new_state="idle")
    # 被拒后状态保持原值，无事件
    assert account.runtime_state == "quota_exhausted"
    assert _events(session) == []


def test_set_runtime_state_self_transition_is_idempotent_reassert(
    session: _FakeSession, governor: AccountGovernor
) -> None:
    later = _FIXED_NOW + timedelta(hours=3)
    account = _seed_account(session, runtime_state="muted")
    governor.set_runtime_state(
        platform_account_id=account.id, new_state="muted", reason="重申", until=later
    )
    assert account.runtime_state == "muted"
    assert account.muted_until == later
    assert len(_events(session, "state_transition")) == 1


# ---------------------------------------------------------------------------
# report_wall
# ---------------------------------------------------------------------------


def test_report_wall_quota_default_resume_next_midnight_with_jitter(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    result = governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="今日专家模式免费次数已用完",
        browser_instance_key="doubao_sh",
        run_pub_id="run_Q",
        mode="deep_think",
    )
    # _FIXED_NOW = 2026-08-13 20:00 北京 → 下一重置点 ∈ [08-14 00:00, 00:30] 北京
    floor = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)  # = 08-14 00:00 北京
    assert account.runtime_state == "idle"
    assert account.quota_resume_at is None
    mode_block = account.quota_probe_json["mode_quota_blocks"]["deep_think"]
    resume_at = datetime.fromisoformat(mode_block["resume_at"])
    assert floor <= resume_at <= floor + timedelta(minutes=30)
    assert result["target"] == "platform_account"
    assert result["runtime_state"] is None
    assert result["mode_scoped"] is True
    assert result["until"] == resume_at
    events = _events(session, "wall_hit")
    assert len(events) == 1
    assert events[0].new_value["wall_type"] == "wall_quota"
    assert events[0].new_value["mode"] == "deep_think"
    assert len(_events(session, "state_transition")) == 0


def test_report_wall_quota_without_mode_keeps_legacy_account_block(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    result = governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="未标明模式的旧探测",
        browser_instance_key="doubao_sh",
    )
    assert account.runtime_state == "quota_exhausted"
    assert account.quota_resume_at is not None
    assert result["mode_scoped"] is False
    assert len(_events(session, "state_transition")) == 1


def test_report_wall_muted_uses_explicit_until(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    until = _FIXED_NOW + timedelta(days=1, hours=1)
    account = _seed_account(session)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_muted",
        evidence="已被禁言至 2026 年 8 月 14 日 13:00",
        browser_instance_key="doubao_sh",
        until=until,
    )
    assert account.runtime_state == "muted"
    assert account.muted_until == until


def test_report_wall_captcha_and_refusal(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_captcha",
        evidence="滑块验证",
        browser_instance_key="doubao_sh",
    )
    assert account.runtime_state == "captcha"
    # wall_refusal 只记事件不改状态
    governor.set_runtime_state(platform_account_id=account.id, new_state="idle")
    governor.report_wall(
        platform="doubao",
        wall_type="wall_refusal",
        evidence="该问题暂无法回答",
        browser_instance_key="doubao_sh",
    )
    assert account.runtime_state == "idle"
    refusal_events = [
        e for e in _events(session, "wall_hit") if e.new_value["wall_type"] == "wall_refusal"
    ]
    assert len(refusal_events) == 1
    assert refusal_events[0].new_value["runtime_state"] is None


def test_report_wall_truncates_evidence_to_500(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="长" * 800,
        browser_instance_key="doubao_sh",
    )
    event = _events(session, "wall_hit")[0]
    assert len(event.evidence) == 500
    assert len(account.state_reason) == 500


def test_report_wall_browser_fallback_when_no_account(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    until = _FIXED_NOW + timedelta(hours=5)
    browser = _seed_browser(session)
    result = governor.report_wall(
        platform="doubao",
        wall_type="wall_muted",
        evidence="禁言",
        browser_instance_key="doubao_sh",
        until=until,
    )
    assert result["target"] == "browser"
    assert result["browser_pub_id"] == browser.pub_id
    assert browser.muted_until == until
    events = _events(session, "wall_hit")
    assert events[0].browser_id == browser.id
    assert events[0].platform_account_id is None
    # 实例兜底不落状态迁移事件
    assert _events(session, "state_transition") == []


def test_report_wall_browser_fallback_quota_breaker_and_captcha_activity(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    browser = _seed_browser(session)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="次数用完",
        browser_instance_key="doubao_sh",
    )
    floor = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)
    assert browser.breaker_until is not None
    assert floor <= browser.breaker_until <= floor + timedelta(minutes=30)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_captcha",
        evidence="滑块",
        browser_instance_key="doubao_sh",
    )
    assert browser.activity == "captcha"


def test_report_wall_orphan_still_records_event(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    result = governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="次数用完",
        browser_instance_key="ghost_instance",
    )
    assert result["target"] == "none"
    events = _events(session, "wall_hit")
    assert len(events) == 1
    assert events[0].platform_account_id is None
    assert events[0].browser_id is None


def test_report_wall_rejected_transition_still_records_event(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    # error 态只许回 idle；此时撞 quota 墙 = 迁移表拒绝，但事件照落、状态不动
    account = _seed_account(session, runtime_state="error")
    result = governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="次数用完",
        browser_instance_key="doubao_sh",
    )
    assert account.runtime_state == "error"
    assert result["runtime_state"] is None
    assert len(_events(session, "wall_hit")) == 1
    assert _events(session, "state_transition") == []


def test_report_wall_unknown_type_rejected(governor: AccountGovernor) -> None:
    with pytest.raises(ValueError, match="unknown_wall_type"):
        governor.report_wall(platform="doubao", wall_type="wall_bogus", evidence="x")


# ---------------------------------------------------------------------------
# record_task_outcome：台账 / 幂等 / lazy reset / 熔断
# ---------------------------------------------------------------------------


def test_record_task_outcome_success_counts_usage_and_idempotent(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session, quota_reset_at=_FIXED_NOW + timedelta(hours=2))
    call = {
        "platform": "doubao",
        "browser_instance_key": "doubao_sh",
        "outcome": "success",
        "run_pub_id": "run_A",
        "mode": "normal",
    }
    governor.record_task_outcome(**call)
    assert (account.used_today, account.used_week, account.used_year) == (1, 1, 1)
    # 同 task 重复上报（Temporal 重试）不重复计数
    governor.record_task_outcome(**call)
    assert (account.used_today, account.used_week, account.used_year) == (1, 1, 1)
    assert len(_events(session, "task_outcome")) == 1
    # 另一 run 的另一次成功 = 新计数
    governor.record_task_outcome(**(call | {"run_pub_id": "run_B"}))
    assert account.used_today == 2


def test_account_success_clears_stale_browser_captcha_and_breaker(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_account(session, quota_reset_at=_FIXED_NOW + timedelta(hours=2))
    browser = _seed_browser(
        session,
        activity="captcha",
        error_streak=5,
        breaker_until=_FIXED_NOW + timedelta(hours=2),
    )

    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="success",
        run_pub_id="run_after_manual_captcha",
        mode="normal",
    )

    assert browser.activity == "idle"
    assert browser.error_streak == 0
    assert browser.breaker_until is None


def test_record_task_outcome_breaker_after_three_same_failures(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    for index in range(4):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome="failed",
            error_type="answer_capture_incomplete",
            run_pub_id=f"run_{index}",
        )
    assert account.runtime_state == "error"
    assert account.state_reason == "consecutive_failures:answer_capture_incomplete"
    # 熔断只触发一次（第 4 次失败时已是 error 态，不重复迁移/重复 breaker 事件）
    assert len(_events(session, "breaker")) == 1
    transitions = _events(session, "state_transition")
    assert len(transitions) == 1
    assert transitions[0].new_value["runtime_state"] == "error"


def test_record_task_outcome_streak_broken_by_other_error_type(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    for index, error_type in enumerate(["a", "a", "b", "a"]):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome="failed",
            error_type=error_type,
            run_pub_id=f"run_{index}",
        )
    # 最近链 = b,a（异类断链）→ 同类连续不足 3，不熔断
    assert account.runtime_state == "idle"
    assert _events(session, "breaker") == []


def test_record_task_outcome_streak_broken_by_success(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session, quota_reset_at=_FIXED_NOW + timedelta(hours=2))
    outcomes = ["failed", "failed", "success", "failed", "failed"]
    for index, outcome in enumerate(outcomes):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome=outcome,
            error_type="e" if outcome == "failed" else None,
            run_pub_id=f"run_{index}",
        )
    assert account.runtime_state == "idle"
    assert _events(session, "breaker") == []


def test_record_task_outcome_browser_fallback_breaker(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    browser = _seed_browser(session)
    for index in range(2):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome="failed",
            error_type="adapter_crash",
            run_pub_id=f"run_{index}",
        )
    assert browser.error_streak == 2
    assert browser.breaker_until is None
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="failed",
        error_type="adapter_crash",
        run_pub_id="run_2",
    )
    assert browser.error_streak == 3
    assert browser.breaker_until == _FIXED_NOW + timedelta(hours=2)
    assert len(_events(session, "breaker")) == 1
    # 人工处理后首个真实成功同时证伪陈旧 captcha/activity 与 breaker。
    browser.activity = "captcha"
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="success",
        run_pub_id="run_3",
    )
    assert browser.activity == "idle"
    assert browser.error_streak == 0
    assert browser.breaker_until is None


def test_aborted_after_failure_is_audited_but_neutral_for_account_streak(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    for index in range(3):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome="failed",
            error_type="mode_toggle_failed",
            run_pub_id=f"root_{index}",
            task_pub_id=f"root_task_{index}",
        )
        for aborted_index in range(7):
            governor.record_task_outcome(
                platform="doubao",
                browser_instance_key="doubao_sh",
                outcome="aborted",
                error_type="aborted_after_failure",
                run_pub_id=f"root_{index}",
                task_pub_id=f"aborted_task_{index}_{aborted_index}",
            )
    # root + 7 aborted 连续三批：占位既没放大根因，也没把真实根因 streak 断开。
    assert account.runtime_state == "error"
    assert len(_events(session, "breaker")) == 1
    aborted = [
        event
        for event in _events(session, "task_outcome")
        if event.new_value["error_type"] == "aborted_after_failure"
    ]
    assert len(aborted) == 21
    assert all(event.new_value["breaker_eligible"] is False for event in aborted)


def test_aborted_after_failure_does_not_increment_browser_fallback_streak(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    browser = _seed_browser(session, error_streak=2)
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="aborted",
        error_type="aborted_after_failure",
        run_pub_id="run_aborted",
        task_pub_id="task_aborted",
    )
    assert browser.error_streak == 2
    assert browser.breaker_until is None
    assert len(_events(session, "task_outcome")) == 1
    assert _events(session, "task_outcome")[0].new_value["breaker_eligible"] is False
    assert _events(session, "breaker") == []


def test_mode_quota_outcome_does_not_escalate_to_global_account_error(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    account = _seed_account(session)
    for index in range(4):
        governor.record_task_outcome(
            platform="doubao",
            browser_instance_key="doubao_sh",
            outcome="wall",
            error_type="wall_quota",
            mode="deep_think",
            run_pub_id=f"quota_{index}",
            task_pub_id=f"quota_task_{index}",
        )
    assert account.runtime_state == "idle"
    assert _events(session, "breaker") == []
    assert all(
        event.new_value["breaker_eligible"] is False for event in _events(session, "task_outcome")
    )


def test_record_task_outcome_orphan_is_logged_noop(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="ghost",
        outcome="success",
        run_pub_id="run_X",
    )
    assert _events(session) == []


def test_lazy_quota_reset_on_past_reset_point(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    # 重置点 = 昨天（与北京今天同 ISO 周）→ 日清零、周/年不清
    account = _seed_account(
        session,
        used_today=5,
        used_week=8,
        used_year=30,
        quota_reset_at=_FIXED_NOW - timedelta(days=1),
    )
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="success",
        run_pub_id="run_R",
    )
    assert (account.used_today, account.used_week, account.used_year) == (1, 9, 31)
    assert account.quota_reset_at > _FIXED_NOW


def test_lazy_quota_reset_crosses_iso_week_and_year(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 固定时钟：2026-08-17（周一，ISO 周 34）10:00 UTC = 北京 18:00
    monkeypatch.setattr(
        account_governor, "now_utc", lambda: datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    )
    account = _seed_account(
        session,
        used_today=3,
        used_week=7,
        used_year=90,
        # 重置点 = 2026-08-16（周日，ISO 周 33）10:00 UTC
        quota_reset_at=datetime(2026, 8, 16, 10, 0, tzinfo=UTC),
    )
    governor = AccountGovernor(session)  # type: ignore[arg-type]
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="success",
        run_pub_id="run_W",
    )
    # 跨 ISO 周 → 日/周清零后 +1；同年 → 年只 +1
    assert (account.used_today, account.used_week, account.used_year) == (1, 1, 91)


def test_record_task_outcome_task_pub_id_joins_dedup_key(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    """逐题上报（2026-08-14 起）：同 (run,platform,mode,outcome) 不同 task_pub_id
    各占一次计数；同 task 重复上报（重试/重放）仍幂等去重。"""
    account = _seed_account(session, quota_reset_at=_FIXED_NOW + timedelta(hours=2))
    base = {
        "platform": "doubao",
        "browser_instance_key": "doubao_sh",
        "outcome": "success",
        "run_pub_id": "run_T",
        "mode": "normal",
    }
    governor.record_task_outcome(**(base | {"task_pub_id": "ans_1"}))
    governor.record_task_outcome(**(base | {"task_pub_id": "ans_2"}))
    # 同 (run,mode) 两题逐题上报 → 两次计数（旧语义会被去重合并成 1）
    assert account.used_today == 2
    # 同 task 重复上报不重复计数
    governor.record_task_outcome(**(base | {"task_pub_id": "ans_1"}))
    assert account.used_today == 2
    events = _events(session, "task_outcome")
    assert len(events) == 2
    assert {e.new_value["task_pub_id"] for e in events} == {"ans_1", "ans_2"}


def test_record_task_outcome_none_task_pub_id_keeps_batch_level_semantics(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    """task_pub_id 缺省 None = 旧 batch 级语义：同元组去重合并；与带题级标识的
    上报互不混淆（None 只匹配 None）。"""
    account = _seed_account(session, quota_reset_at=_FIXED_NOW + timedelta(hours=2))
    base = {
        "platform": "doubao",
        "browser_instance_key": "doubao_sh",
        "outcome": "success",
        "run_pub_id": "run_N",
        "mode": "normal",
    }
    governor.record_task_outcome(**base)
    governor.record_task_outcome(**base)
    assert account.used_today == 1
    # None 与显式题级标识是不同的去重键
    governor.record_task_outcome(**(base | {"task_pub_id": "ans_9"}))
    assert account.used_today == 2
    # 失败侧同键：带 task_pub_id 的失败重复上报不叠加熔断 streak
    governor.record_task_outcome(
        **(base | {"outcome": "wall", "error_type": "wall_quota", "task_pub_id": "ans_7"})
    )
    governor.record_task_outcome(
        **(base | {"outcome": "wall", "error_type": "wall_quota", "task_pub_id": "ans_7"})
    )
    failure_events = [
        e for e in _events(session, "task_outcome") if e.new_value["outcome"] == "wall"
    ]
    assert len(failure_events) == 1
    assert account.runtime_state == "idle"


# ---------------------------------------------------------------------------
# resolve_collectable
# ---------------------------------------------------------------------------


def test_resolve_collectable_hit(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session)
    _seed_browser(session)
    account = _seed_account(session, quota_day=10, used_today=3)
    result = governor.resolve_collectable(platform="doubao", region_gb="310000")
    assert result == {
        "platform_account_pub_id": account.pub_id,
        "browser_instance_key": "doubao_sh",
        "region_gb": "310000",
        "remaining_today": 7,
    }


def test_resolve_collectable_claims_and_reuses_run_account(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session)
    _seed_browser(session)
    account = _seed_account(session)

    first = governor.resolve_collectable(
        platform="doubao", region_gb="310000", run_pub_id="run_sticky"
    )
    second = governor.resolve_collectable(
        platform="doubao", region_gb="310000", run_pub_id="run_sticky"
    )

    assert first is not None and second is not None
    assert first["platform_account_pub_id"] == account.pub_id
    assert second["platform_account_pub_id"] == account.pub_id
    assert account.runtime_state == "running"
    assert account.current_run_pub_id == "run_sticky"


def test_release_run_reservations_only_releases_running_owner(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    running = _seed_account(
        session,
        pub_id="pac_running",
        runtime_state="running",
        current_run_pub_id="run_terminal",
    )
    captcha = _seed_account(
        session,
        pub_id="pac_captcha",
        runtime_state="captcha",
        current_run_pub_id="run_terminal",
    )
    other = _seed_account(
        session,
        pub_id="pac_other",
        runtime_state="running",
        current_run_pub_id="run_other",
    )

    assert governor.release_run_reservations(run_pub_id="run_terminal") == 1
    assert running.runtime_state == "idle"
    assert running.current_run_pub_id is None
    assert captcha.runtime_state == "captcha"
    assert captcha.current_run_pub_id == "run_terminal"
    assert other.runtime_state == "running"
    assert other.current_run_pub_id == "run_other"


def test_resolve_collectable_unlimited_quota_remaining_none(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    _seed_account(session, quota_day=None)
    result = governor.resolve_collectable(platform="doubao", region_gb="310000")
    assert result is not None
    assert result["remaining_today"] is None


def test_resolve_collectable_region_down(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session, state="down")
    _seed_account(session)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_region_unregistered_is_not_blocked(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    _seed_account(session)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is not None


def test_resolve_collectable_no_account_registered(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session)
    _seed_account(session, platform="deepseek")  # 别的平台有账号不算
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_region_ip_mismatch_fail_closed(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session)
    _seed_browser(session, region_gb="120000")  # 实例在天津，账号格派上海题
    _seed_account(session)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_skips_non_idle_and_over_quota(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_region(session)
    _seed_browser(session)
    # 候选1 running；候选2 日预算打满；候选3 可用
    _seed_account(
        session, runtime_state="running", browser_instance_key="doubao_sh", pub_id="pac_busy"
    )
    _seed_account(
        session,
        quota_day=2,
        used_today=2,
        quota_reset_at=_FIXED_NOW + timedelta(hours=1),
        browser_instance_key="doubao_sh",
        pub_id="pac_full",
    )
    free = _seed_account(session, pub_id="pac_free")
    result = governor.resolve_collectable(platform="doubao", region_gb="310000")
    assert result is not None
    assert result["platform_account_pub_id"] == free.pub_id


def test_resolve_collectable_all_unavailable_returns_none(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    _seed_account(
        session,
        quota_day=1,
        used_today=1,
        quota_reset_at=_FIXED_NOW + timedelta(hours=1),
    )
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_skips_browser_breaker(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session, breaker_until=_FIXED_NOW + timedelta(minutes=30))
    _seed_account(session)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_requires_recovery_after_breaker_ttl_expires(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    browser = _seed_browser(
        session,
        error_streak=41,
        breaker_until=_FIXED_NOW - timedelta(minutes=1),
    )
    _seed_account(session)

    # Time passing is not a recovery signal: generic/manual scheduler paths must
    # still fail closed even though the breaker timestamp is already in the past.
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None

    # A verified successful outcome clears the persisted root condition and
    # re-admits the browser.  Operators may also use the explicit governance-off
    # recovery procedure before recording/resetting this state.
    governor.record_task_outcome(
        platform="doubao",
        browser_instance_key="doubao_sh",
        outcome="success",
        run_pub_id="run_recovery",
        mode="normal",
        task_pub_id="tsk_recovery",
    )
    assert browser.error_streak == 0
    assert browser.breaker_until is None
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is not None


@pytest.mark.parametrize("activity", ["busy", "captcha"])
def test_resolve_collectable_requires_idle_browser(
    activity: str,
    session: _FakeSession,
    governor: AccountGovernor,
    fixed_clock: None,
) -> None:
    _seed_browser(session, activity=activity)
    _seed_account(session)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


def test_resolve_collectable_mode_quota_block_only_blocks_that_mode(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    account = _seed_account(session)
    governor.report_wall(
        platform="doubao",
        wall_type="wall_quota",
        evidence="专家次数已用完",
        browser_instance_key="doubao_sh",
        mode="deep_think",
        until=_FIXED_NOW + timedelta(hours=1),
    )
    assert (
        governor.resolve_collectable(platform="doubao", region_gb="310000", mode="deep_think")
        is None
    )
    assert (
        governor.resolve_collectable(platform="doubao", region_gb="310000", mode="normal")
        is not None
    )
    # 无 mode 的旧调用不能猜测安全模式，仍 fail-closed。
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None
    assert account.runtime_state == "idle"


def test_resolve_collectable_prunes_expired_mode_quota_block(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    account = _seed_account(
        session,
        quota_probe_json={
            "vendor_probe": {"kept": True},
            "mode_quota_blocks": {
                "deep_think": {"resume_at": (_FIXED_NOW - timedelta(seconds=1)).isoformat()}
            },
        },
    )
    assert (
        governor.resolve_collectable(platform="doubao", region_gb="310000", mode="deep_think")
        is not None
    )
    assert account.quota_probe_json["mode_quota_blocks"] == {}
    assert account.quota_probe_json["vendor_probe"] == {"kept": True}


def test_resolve_collectable_lazy_resumes_expired_mute(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    account = _seed_account(
        session,
        runtime_state="muted",
        muted_until=_FIXED_NOW - timedelta(minutes=1),
    )
    result = governor.resolve_collectable(platform="doubao", region_gb="310000")
    assert result is not None
    assert account.runtime_state == "idle"
    assert account.muted_until is None
    events = _events(session, "state_auto_resume")
    assert len(events) == 1
    assert events[0].old_value["runtime_state"] == "muted"


def test_resolve_collectable_lazy_resumes_quota_and_resets_usage(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    _seed_browser(session)
    account = _seed_account(
        session,
        runtime_state="quota_exhausted",
        quota_resume_at=_FIXED_NOW - timedelta(minutes=1),
        used_today=9,
        quota_day=10,
    )
    result = governor.resolve_collectable(platform="doubao", region_gb="310000")
    assert result is not None
    assert account.runtime_state == "idle"
    assert account.used_today == 0
    assert account.quota_resume_at is None


def test_resolve_collectable_manual_mute_stays(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    # muted_until=NULL = 人工封禁，不自动恢复
    _seed_account(session, runtime_state="muted", muted_until=None)
    assert governor.resolve_collectable(platform="doubao", region_gb="310000") is None


# ---------------------------------------------------------------------------
# record_region_probe
# ---------------------------------------------------------------------------


def test_record_region_probe_creates_row_then_flips_with_event(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    governor.record_region_probe(region_gb="110000", ok=True, exit_ip="1.2.3.4")
    region = session.rows[CollectionRegion][0]
    assert region.state == "ok"
    assert region.exit_ip_last == "1.2.3.4"
    assert region.last_probe_at == _FIXED_NOW
    # 建档即 ok，无翻转 → 无事件
    assert _events(session, "relay_probe") == []

    governor.record_region_probe(region_gb="110000", ok=False, note="timeout")
    assert region.state == "ok"
    assert region.probe_failure_streak == 1
    assert region.last_probe_ok is False
    assert region.last_probe_note == "timeout"
    # 新 governor 模拟 exporter 重启：连续计数仍随 DB 行保存。
    AccountGovernor(session).record_region_probe(region_gb="110000", ok=False, note="timeout")
    assert region.state == "ok"
    governor.record_region_probe(region_gb="110000", ok=False, note="timeout")
    assert region.state == "down"
    events = _events(session, "relay_probe")
    assert len(events) == 1
    assert events[0].old_value == {"state": "ok"}
    assert events[0].new_value["state"] == "down"
    assert events[0].evidence == "timeout"
    assert events[0].region_id == region.id

    # 状态未翻转 → 不重复记事件；exit_ip 仍刷新
    governor.record_region_probe(region_gb="110000", ok=False, exit_ip="5.6.7.8")
    assert len(_events(session, "relay_probe")) == 1
    assert region.exit_ip_last == "5.6.7.8"

    governor.record_region_probe(region_gb="110000", ok=True)
    assert region.state == "down"
    assert len(_events(session, "relay_probe")) == 1
    governor.record_region_probe(region_gb="110000", ok=True)
    assert region.state == "ok"
    assert len(_events(session, "relay_probe")) == 2


def test_record_region_probe_does_not_override_arrears_or_note(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    region = _seed_region(session, state="arrears", note="人工标注欠费 8 月")
    governor.record_region_probe(region_gb="310000", ok=True, exit_ip="9.9.9.9", note="通")
    # arrears = 人工标注，探测成功不自动翻回 ok；人工备注不被探测 note 覆盖
    assert region.state == "arrears"
    assert region.note == "人工标注欠费 8 月"
    assert region.exit_ip_last == "9.9.9.9"
    assert _events(session, "relay_probe") == []


def test_record_region_probe_locks_existing_region_before_updating_streak(
    session: _FakeSession, governor: AccountGovernor, fixed_clock: None
) -> None:
    """Concurrent probes must serialize the persisted read/modify/write streak."""
    _seed_region(session)

    governor.record_region_probe(region_gb="310000", ok=False, note="timeout")

    region_selects = [
        stmt
        for stmt in session.scalar_statements
        if stmt.column_descriptions[0]["entity"] is CollectionRegion
    ]
    assert len(region_selects) == 1
    assert region_selects[0]._for_update_arg is not None
    assert region_selects[0].get_execution_options()["populate_existing"] is True
