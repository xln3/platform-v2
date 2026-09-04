"""采集账号治理的 collection 撞墙接线 × workflow 占位纯函数单测（2026-08-14 起）。

覆盖（设计文档 caiji-0813 §5.4 统一出口 / §6.2 调度消费）：

- ``_report_outcome_to_governor``：逐题 record_task_outcome（带 task_pub_id）
  + 墙 outcome 的 report_wall 参数（quota→mode+until=None；muted→从
  error_message 解析解封点；refusal/captcha 透传；mode_unconfirmed 不报墙）；
  治理层异常吞 warning（旁路不阻断主链）；闸门 off / task_input None 跳过。
- ``_parse_wall_until`` / ``_governor_wall_type`` 词表边界。
- captcha_pause 判定保持 wall_captcha 专属：新墙类型/account_unavailable 占位
  不产生 pause（挂起泛化是 P1，不做）。
- definitions 纯函数：account_unavailable_reason 的因子甄别 +
  account_unavailable_placeholders 的等长占位形状（走失败落库路径 =
  state=failed/answer None，永不进 fanout、永不盖 dimensions 章——与
  captcha_pause 占位同管道）。

governor 用记录式 fake（采集调用参数），session 用最小上下文管理器假面——
unit 层不起真 PG。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from temporalio.exceptions import ActivityError, ApplicationError

from workflows.activities import collection
from workflows.activities.collection import (
    COLLECTION_BATCH_ITEM_STATUSES,
    CollectionBatchItemResult,
    CollectionTaskInput,
    batch_result_with_captcha_pause,
)
from workflows.definitions.collection import (
    account_contention_timeout_reason,
    account_unavailable_placeholders,
    account_unavailable_reason,
)


def _task_input(adapter: str = "doubao", mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="bk-1",
        query="q",
        model="m",
        region="CN-SH",
        mode=mode,
        adapter=adapter,
    )


def _item_result(
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> CollectionBatchItemResult:
    return CollectionBatchItemResult(
        business_key="bk-1",
        status=status,
        error_type=error_type,
        error_message=error_message,
        answer_text="答案" if status == "ok" else None,
        browser_instance="doubao_sh",
    )


class _RecordingGovernor:
    """AccountGovernor 的记录式 fake：只采集调用参数，不做状态机。"""

    def __init__(self) -> None:
        self.task_outcomes: list[dict[str, Any]] = []
        self.walls: list[dict[str, Any]] = []

    def record_task_outcome(self, **kwargs: Any) -> None:
        self.task_outcomes.append(kwargs)

    def report_wall(self, **kwargs: Any) -> dict[str, Any]:
        self.walls.append(kwargs)
        return {"target": "platform_account"}


class _FakeSessionCtx:
    """WorkerSessionLocal 的最小假面（上下文管理器 + commit/rollback 记录）。"""

    def __init__(self) -> None:
        self.commits = 0

    def __enter__(self) -> _FakeSessionCtx:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


@pytest.fixture()
def governor_seam(monkeypatch: pytest.MonkeyPatch) -> tuple[_RecordingGovernor, _FakeSessionCtx]:
    """把 collection 模块内的 WorkerSessionLocal / AccountGovernor 换成 fake，
    并开启治理闸门（conftest 全局缺省 off）。"""
    governor = _RecordingGovernor()
    session = _FakeSessionCtx()
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")
    monkeypatch.setattr(collection, "WorkerSessionLocal", lambda: session)
    monkeypatch.setattr(collection, "AccountGovernor", lambda conn: governor)
    return governor, session


def _report(
    result: CollectionBatchItemResult,
    *,
    task_input: CollectionTaskInput | None = None,
    status: str | None = None,
) -> None:
    collection._report_outcome_to_governor(
        run_pub_id="run_G",
        result=result,
        task_input=_task_input() if task_input is None else task_input,
        task_pub_id="ans_1",
        status=status if status is not None else result.status,
    )


# ── 逐题 task_outcome 上报 ───────────────────────────────────────────────────


def test_ok_outcome_reports_success_with_task_pub_id(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, session = governor_seam
    _report(_item_result("ok"))
    assert len(governor.task_outcomes) == 1
    call = governor.task_outcomes[0]
    assert call == {
        "platform": "doubao",
        "browser_instance_key": "doubao_sh",
        "outcome": "success",
        "error_type": None,
        "run_pub_id": "run_G",
        "mode": "normal",
        "task_pub_id": "ans_1",
    }
    assert governor.walls == []
    assert session.commits == 1


def test_wall_quota_reports_wall_with_mode_and_no_until(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, _session = governor_seam
    message = "answer-text wall hit [wall_quota] phrase='免费次数用完' fragment='…'"
    _report(_item_result("wall", "wall_quota", message))
    assert len(governor.task_outcomes) == 1
    assert governor.task_outcomes[0]["outcome"] == "wall"
    assert governor.task_outcomes[0]["error_type"] == "wall_quota"
    assert governor.task_outcomes[0]["task_pub_id"] == "ans_1"
    assert len(governor.walls) == 1
    wall = governor.walls[0]
    assert wall["wall_type"] == "wall_quota"
    assert wall["platform"] == "doubao"
    assert wall["until"] is None  # governor 自算日重置点
    assert wall["mode"] == "normal"
    assert wall["evidence"] == message
    assert wall["browser_instance_key"] == "doubao_sh"
    assert wall["run_pub_id"] == "run_G"


def test_wall_muted_parses_until_from_error_message(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, _session = governor_seam
    # adapter 落库形状（wall_lexicon WallVerdict.until=naive 北京时间）
    message = (
        "answer-text wall hit [wall_muted] phrase='已被禁言至 2026 年 8 月 14 日 "
        "13:02' until=2026-08-14T13:02:00 fragment='…'"
    )
    _report(_item_result("wall", "wall_muted", message))
    assert governor.walls[0]["wall_type"] == "wall_muted"
    # 2026-08-14 13:02 北京 = 05:02 UTC
    assert governor.walls[0]["until"] == datetime(2026, 8, 14, 5, 2, tzinfo=UTC)


def test_wall_muted_without_until_passes_none(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, _session = governor_seam
    message = "muted banner on page ('你的账号已被封禁')"
    _report(_item_result("wall", "wall_muted", message))
    assert governor.walls[0]["wall_type"] == "wall_muted"
    assert governor.walls[0]["until"] is None  # 人工封禁语义（不自动恢复）


def test_wall_captcha_and_refusal_pass_through(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, _session = governor_seam
    _report(_item_result("wall", "wall_captcha", "滑块验证"))
    _report(_item_result("wall", "wall_refusal", "我们换个话题"))
    assert [w["wall_type"] for w in governor.walls] == ["wall_captcha", "wall_refusal"]
    assert all(w["until"] is None for w in governor.walls)
    # refusal 不改状态是 governor 内语义——接线只负责透传
    assert len(governor.task_outcomes) == 2


def test_mode_unconfirmed_records_outcome_but_no_wall(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    """mode_unconfirmed 不在治理墙词表：只记 task_outcome（参与同类失败熔断）。"""
    governor, _session = governor_seam
    _report(_item_result("wall", "mode_unconfirmed", "no think evidence"))
    assert len(governor.task_outcomes) == 1
    assert governor.task_outcomes[0]["outcome"] == "wall"
    assert governor.task_outcomes[0]["error_type"] == "mode_unconfirmed"
    assert governor.walls == []


def test_incomplete_and_aborted_record_outcome_but_no_wall(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    governor, _session = governor_seam
    _report(_item_result("incomplete", "answer_capture_incomplete", "截图为空"))
    _report(_item_result("aborted", "aborted_after_failure", "not executed"))
    assert [c["outcome"] for c in governor.task_outcomes] == ["incomplete", "aborted"]
    assert governor.walls == []


def test_account_unavailable_placeholder_records_outcome_no_wall(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
) -> None:
    """治理不可用占位题同样逐题上报 outcome（熔断事实源），但不报墙——
    不可用判定本就来自治理层，无需回写。"""
    governor, _session = governor_seam
    placeholder = account_unavailable_placeholders([_task_input()], "reason=x")[0]
    _report(placeholder)
    assert len(governor.task_outcomes) == 1
    assert governor.task_outcomes[0]["error_type"] == "account_unavailable"
    assert governor.walls == []


def test_governor_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """治理旁路任何异常只记 warning——绝不阻断/毒化采集落库主链。"""
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")

    class _Boom:
        def __enter__(self) -> Any:
            raise RuntimeError("pg down")

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(collection, "WorkerSessionLocal", lambda: _Boom())
    # 不 raise 即通过
    _report(_item_result("ok"))


def test_report_skipped_when_gate_off_or_no_task_input(
    governor_seam: tuple[_RecordingGovernor, _FakeSessionCtx],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor, session = governor_seam
    # task_input=None（老历史形状）→ 跳过
    collection._report_outcome_to_governor(
        run_pub_id="run_G",
        result=_item_result("ok"),
        task_input=None,
        task_pub_id="ans_1",
        status="ok",
    )
    # 闸门 off → 跳过
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "off")
    _report(_item_result("ok"))
    assert governor.task_outcomes == []
    assert session.commits == 0


# ── 词表 / 解析纯函数 ────────────────────────────────────────────────────────


def test_governor_wall_type_lexicon_boundary() -> None:
    assert collection._governor_wall_type("wall", "wall_quota") == "wall_quota"
    assert collection._governor_wall_type("wall", "wall_muted") == "wall_muted"
    assert collection._governor_wall_type("wall", "wall_captcha") == "wall_captcha"
    assert collection._governor_wall_type("wall", "wall_refusal") == "wall_refusal"
    # mode_unconfirmed / 其余 error_type 不在治理墙词表
    assert collection._governor_wall_type("wall", "mode_unconfirmed") is None
    assert collection._governor_wall_type("wall", "wall_login") is None
    assert collection._governor_wall_type("wall", None) is None
    # 非 wall 状态一律不报墙
    assert collection._governor_wall_type("ok", "wall_quota") is None
    assert collection._governor_wall_type("incomplete", "wall_quota") is None


def test_parse_wall_until_variants() -> None:
    assert collection._parse_wall_until(None) is None
    assert collection._parse_wall_until("no until here") is None
    assert collection._parse_wall_until("until=not-a-date") is None
    # banner 形状（until 后接 evidence 尾注）
    parsed = collection._parse_wall_until(
        "muted banner on page ('已被禁言至…') until=2026-08-14T13:02:00; evidence=file://x"
    )
    assert parsed == datetime(2026, 8, 14, 5, 2, tzinfo=UTC)


# ── captcha_pause 判定保持 wall_captcha 专属 ─────────────────────────────────


def test_captcha_pause_stays_wall_captcha_exclusive() -> None:
    """新墙类型/account_unavailable 占位不产生 pause（挂起泛化是 P1，不做）。"""
    for error_type in ("wall_quota", "wall_muted", "wall_refusal", "account_unavailable"):
        results = [_item_result("wall", error_type, "x")]
        out = batch_result_with_captcha_pause(results, instance_key="doubao_sh")
        assert out.captcha_pause is None
        assert len(out.results) == 1  # 等长占位不变
    # wall_captcha 照旧标 pause（既有语义锚定）
    captcha = batch_result_with_captcha_pause(
        [_item_result("wall", "wall_captcha", "滑块")], instance_key="doubao_sh"
    )
    assert captcha.captcha_pause is not None
    assert captcha.captcha_pause.resume_index == 0


# ── definitions 纯函数：account_unavailable 占位 ─────────────────────────────


def _activity_error(error_type: str, message: str) -> ActivityError:
    cause = ApplicationError(message, type=error_type, non_retryable=True)
    try:
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test",
            activity_type="collect_doubao_batch",
            activity_id="3",
            retry_state=None,
        ) from cause
    except ActivityError as exc:
        return exc
    raise AssertionError("unreachable")


def test_account_unavailable_reason_extracts_governance_signal() -> None:
    exc = _activity_error("account_unavailable", "no collectable governed account …")
    assert account_unavailable_reason(exc) == "no collectable governed account …"
    # 其余失败类型不冒充治理信号（region_exit_mismatch 等维持整批 fail-loud）
    assert account_unavailable_reason(_activity_error("region_exit_mismatch", "x")) is None
    assert account_unavailable_reason(RuntimeError("plain")) is None
    try:
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test",
            activity_type="collect_doubao_batch",
            activity_id="3",
            retry_state=None,
        ) from RuntimeError("not an application error")
    except ActivityError as non_app:
        assert account_unavailable_reason(non_app) is None


def test_account_unavailable_placeholders_equal_length_failure_shape() -> None:
    """等长占位（与输入同序同数）；形状照 captcha_pause 占位先例——status=wall
    走失败落库路径（state=failed/quality_state=error_type/answer None），永不进
    fanout、永不盖 dimensions 的 not_challenged/degraded=0 章（结构性保证：
    盖章只发生在 publish_downstream_event 的 completed 答案上）。"""
    items = [_task_input(), _task_input()]
    items[1] = CollectionTaskInput(
        business_key="bk-2",
        query="q2",
        model="m",
        region="CN-SH",
        mode="deep_think",
        adapter="doubao",
    )
    placeholders = account_unavailable_placeholders(items, "no collectable (reason=x)")
    assert len(placeholders) == len(items)
    assert [p.business_key for p in placeholders] == ["bk-1", "bk-2"]
    for placeholder in placeholders:
        assert placeholder.status == "wall"
        assert placeholder.status in COLLECTION_BATCH_ITEM_STATUSES  # persist 接受
        assert placeholder.error_type == "account_unavailable"
        assert placeholder.error_message == "no collectable (reason=x)"
        assert placeholder.answer_text is None  # 零合成，绝不进答案/分析链路


def test_account_contention_timeout_reason_extracts_signal() -> None:
    """竞争超时与账号不存在/不可自愈是两种可分辨的占位原因（占用模型 2026-09-01）。"""
    exc = _activity_error("account_contention_timeout", "governed account contention …")
    assert account_contention_timeout_reason(exc) == "governed account contention …"
    assert account_contention_timeout_reason(_activity_error("account_unavailable", "x")) is None
    assert account_contention_timeout_reason(RuntimeError("plain")) is None


def test_placeholders_passthrough_contention_error_type() -> None:
    """error_type 原样透传进占位：竞争超时占位在数据层与 account_unavailable 可分辨。"""
    placeholders = account_unavailable_placeholders(
        [_task_input()],
        "contention (reason=no_collectable_account)",
        error_type="account_contention_timeout",
    )
    assert len(placeholders) == 1
    assert placeholders[0].status == "wall"
    assert placeholders[0].error_type == "account_contention_timeout"
    assert placeholders[0].answer_text is None
