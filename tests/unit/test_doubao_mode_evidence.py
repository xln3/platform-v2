"""doubao deep_think 请求态/实际态分离记录的单元测试（mock 页面 + 手写 SSE 流）。

纪律来源（旧链）：请求 deep_think ≠ 实际启用——实际态只能由 SSE 证据
（thinking root block_type=10040）二次确认；确认不了（证据缺失/为负）如实
标 actual=normal（旧链 portal 同款口径：「请求了深度思考，但证据中未检测到
启用」不计深度态）。fake 浏览器基础设施复用 test_doubao_adapter（单一出处，
绝不复制漂移）；真实浏览器绝不启动。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_doubao_adapter as tda
from test_doubao_adapter import _FakePage, _in_bb, _install_fake_browser

from workflows.activities import doubao_adapter
from workflows.activities.doubao_adapter import (
    DoubaoAdapterConfig,
    DoubaoBatchItemSpec,
    _mode_evidence,
)

# 含 thinking root（block_type=10040）+ 根级答案块的 deep_think SSE 流。
_SSE_BODY_DEEP = (
    "event: STREAM_MSG_NOTIFY\n"
    'data: {"message": {"message_id": "m1", "conversation_id": "c1", '
    '"section_id": "s1", "user_type": 2}, "content": {"content_block": ['
    '{"block_id": "t-root", "block_type": 10040, "parent_id": "", "content": '
    '{"thinking_block": {"finish_title": "已深度思考", "streaming_title": "思考中"}}},'
    '{"block_id": "t1", "block_type": 10000, "parent_id": "t-root", "content": '
    '{"text_block": {"text": "先拆解问题再检索。"}}},'
    '{"block_id": "b1", "block_type": 10000, "parent_id": "", "content": '
    '{"text_block": {"text": "这是答案"}}}]}}\n'
    "\n"
    "data: [DONE]\n"
)


def _make_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: _FakePage, *, stem: str
) -> tuple[doubao_adapter._PlaywrightDoubaoSession, Path]:
    """fake 浏览器全链路 session（镜像 test_doubao_adapter._make_session，stem 可指定）。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    _install_fake_browser(monkeypatch, page)
    config = DoubaoAdapterConfig.from_env()
    return doubao_adapter._PlaywrightDoubaoSession(config, evidence, stem), evidence


def _read_trace(evidence: Path, stem: str) -> dict:
    return json.loads((evidence / f"{stem}-sse-trace.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# _mode_evidence 纯函数真值表
# ---------------------------------------------------------------------------


def test_mode_evidence_deep_requested_and_sse_confirmed() -> None:
    record = _mode_evidence("deep_think", ui_engaged=True, sse_trace={"deep_think_active": True})
    assert record == {
        "requested": "deep_think",
        "ui_toggle_engaged": True,
        "sse_deep_think_active": True,
        "actual": "deep_think",
    }


def test_mode_evidence_deep_requested_but_sse_negative_is_normal() -> None:
    """请求了 deep_think 但 SSE 无 thinking root → 如实 actual=normal（旧链口径）。"""
    record = _mode_evidence("deep_think", ui_engaged=True, sse_trace={"deep_think_active": False})
    assert record["requested"] == "deep_think"
    assert record["sse_deep_think_active"] is False
    assert record["actual"] == "normal"


def test_mode_evidence_deep_requested_without_sse_is_normal() -> None:
    """无 SSE 可判（DOM 兜底/解析失败）→ 确认不了，如实 actual=normal + 证据 None。"""
    record = _mode_evidence("deep_think", ui_engaged=True, sse_trace=None)
    assert record["sse_deep_think_active"] is None
    assert record["actual"] == "normal"


def test_mode_evidence_normal_requested_but_sse_active_is_deep() -> None:
    """反向错配同样如实：请求 normal 而 SSE 见 thinking root → actual=deep_think。"""
    record = _mode_evidence("normal", ui_engaged=None, sse_trace={"deep_think_active": True})
    assert record["requested"] == "normal"
    assert record["actual"] == "deep_think"


def test_mode_evidence_normal_requested_and_sse_negative() -> None:
    record = _mode_evidence("normal", ui_engaged=None, sse_trace={"deep_think_active": False})
    assert record == {
        "requested": "normal",
        "ui_toggle_engaged": None,
        "sse_deep_think_active": False,
        "actual": "normal",
    }


# ---------------------------------------------------------------------------
# fake 浏览器全链路：toggle（UI 触发）→ SSE 证据确认 → trace mode 段落盘
# ---------------------------------------------------------------------------


def test_collect_deep_think_sse_confirmed_records_actual_deep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deep_think 全链路：picker toggle 确认 + SSE 见 thinking root → actual=deep_think。"""
    monkeypatch.setattr(tda, "_SSE_BODY", _SSE_BODY_DEEP)
    page = _FakePage(deep_think=True)
    session, evidence = _make_session(tmp_path, monkeypatch, page, stem="run-9-task-1-a1")

    answer = session.collect("深度思考的问题", on_stage=lambda s: None, mode="deep_think")

    assert answer.answer_text == "这是答案"
    assert page.deep_think_engaged is True  # UI toggle 确实点过且后置校验通过
    trace = _read_trace(evidence, "run-9-task-1-a1")
    assert trace["deep_think_active"] is True
    assert trace["mode"] == {
        "requested": "deep_think",
        "ui_toggle_engaged": True,
        "sse_deep_think_active": True,
        "actual": "deep_think",
    }
    assert answer.meta["mode"] == trace["mode"]
    assert any(ref.kind == "sse" for ref in answer.evidence)


def test_collect_deep_think_unconfirmed_raises_after_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI toggle 确认但 SSE 无 thinking root → trace 先落盘取证（actual=normal
    如实留痕），随后抛 _ModeUnconfirmed：请求态≠实际态绝不产出答案
    （2026-08-14 起废止旧「答案照出」行为——豆包配额墙假答案曾借此污染
    analytics，教训见 developlog/architecture/caiji-0813 设计计划 §5）。"""
    page = _FakePage(deep_think=True)  # 默认 _SSE_BODY 无 thinking root
    session, evidence = _make_session(tmp_path, monkeypatch, page, stem="run-9-task-2-a1")

    with pytest.raises(doubao_adapter._ModeUnconfirmed):
        session.collect("深度思考的问题", on_stage=lambda s: None, mode="deep_think")

    trace = _read_trace(evidence, "run-9-task-2-a1")  # 证据先落盘，抛错不丢痕
    assert trace["deep_think_active"] is False
    assert trace["mode"] == {
        "requested": "deep_think",
        "ui_toggle_engaged": True,
        "sse_deep_think_active": False,
        "actual": "normal",
    }


def test_collect_normal_mode_records_requested_normal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """normal 题同样留 mode 段（ui_toggle_engaged=None=无 toggle 环节），
    且全程未触碰模式 picker。"""
    page = _FakePage(deep_think=True)  # picker 存在也不许点
    session, evidence = _make_session(tmp_path, monkeypatch, page, stem="run-9-task-3-a1")

    answer = session.collect("普通问题", on_stage=lambda s: None, mode="normal")

    assert answer.answer_text == "这是答案"
    assert page.deep_think_engaged is False
    picker_clicks = [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(tda._PICKER_BB, e[1], e[2])
    ]
    assert picker_clicks == []
    trace = _read_trace(evidence, "run-9-task-3-a1")
    assert trace["mode"] == {
        "requested": "normal",
        "ui_toggle_engaged": None,
        "sse_deep_think_active": False,
        "actual": "normal",
    }


def test_collect_batch_records_mode_evidence_per_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch 路径：deep_think 题与 normal 题各留各的 mode 段（逐题 stem 区分）。
    第 2 题请求 normal 但 SSE 见 thinking root → 反向错配如实 actual=deep_think。"""
    monkeypatch.setattr(tda, "_SSE_BODY", _SSE_BODY_DEEP)
    page = _FakePage(deep_think=True)
    session, evidence = _make_session(tmp_path, monkeypatch, page, stem="batch-stem")
    specs = [
        DoubaoBatchItemSpec(
            business_key="run-9-task-1",
            query="深度思考的问题",
            mode="deep_think",
            file_stem="run-9-task-1-a1",
        ),
        DoubaoBatchItemSpec(
            business_key="run-9-task-2",
            query="普通问题",
            mode="normal",
            file_stem="run-9-task-2-a1",
        ),
    ]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok", "ok"]
    deep_trace = _read_trace(evidence, "run-9-task-1-a1")
    assert deep_trace["mode"]["requested"] == "deep_think"
    assert deep_trace["mode"]["ui_toggle_engaged"] is True
    assert deep_trace["mode"]["actual"] == "deep_think"
    normal_trace = _read_trace(evidence, "run-9-task-2-a1")
    assert normal_trace["mode"]["requested"] == "normal"
    assert normal_trace["mode"]["ui_toggle_engaged"] is None
    assert normal_trace["mode"]["sse_deep_think_active"] is True
    assert normal_trace["mode"]["actual"] == "deep_think"  # 反向错配如实记录
    assert outcomes[0].answer is not None
    assert outcomes[0].answer.meta["mode"]["actual"] == "deep_think"
