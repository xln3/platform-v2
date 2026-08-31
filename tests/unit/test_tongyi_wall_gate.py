"""tongyi 适配器「配额/拒答/封禁」检测层单测（2026-08-14，墙词表接入）。

对照豆包样板（test_doubao_wall_gate.py）逐点覆盖，fake 浏览器基建复用
test_w8_tongyi_batch（_FakePage/_install_fake_browser/_make_session，单一出处
绝不复制漂移；真实浏览器绝不启动）：

1. 答案验收门：平台提示文案（配额/禁言/拒答）被当作答案采回 → _WallError
   走既有墙管道（batch 与 per-task 两路径）；
2. 软墙/实名扫描无条件执行（`if not answer_text:` 门已删），且已出答案时
   答案正文从扫描文本剔除（旧零误伤不变量保持）；
3. batch 连坐三语义：wall_muted 全连坐 / wall_quota 只连坐同 mode /
   wall_refusal 不连坐；
4. composer 不可得路径的禁言 banner → wall_muted（带 until）；
5. deep_think 菜单确认但无 bar_workflow 思考流程卡证据 → mode_unconfirmed
   （non_retryable，不落 completed）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import test_w8_tongyi_batch as w8t
from temporalio.exceptions import ApplicationError

from workflows.activities import tongyi_adapter
from workflows.activities.tongyi_adapter import (
    TongyiBatchItemSpec,
    run_tongyi_collection,
)

# 词表样例（wall_lexicon tongyi 表：「体验次数已用完」须伴随「今日/开通」语境）。
_QUOTA_TEXT = "今日体验次数已用完，开通会员可继续畅聊。"
# 禁言 regex 为 common 表（平台第二人称铁证 + 具体解封时间）。
_MUTED_TEXT = (
    "由于违反用户使用规范，你的账号已被禁言至 2026 年 8 月 14 日 13:02，如有疑问请联系我们。"
)
_REFUSAL_TEXT = "很抱歉，我暂时无法回答这个问题。"
_OK_ANSWER = "这是答案"

# deep_think 思考流程卡证据（_THINKING_EXTRACT_JS 探针注入面）。
_THINKING_PAYLOAD = {
    "card_found": True,
    "steps": [
        {"kind": "reasoning", "title": "拆解问题", "text": "需要先检索资料。"},
    ],
    "queries": ["重疾险对比"],
}


class _ScriptedAnswerPage(w8t._FakePage):
    """按受理次序轮换答案正文（通义答案走 DOM：send 区点击受理后
    answer_visible=True，_ANSWER_EXTRACT_JS 探针回 answer_text）。"""

    def __init__(self, answers: list[str], **kw: Any) -> None:
        super().__init__(**kw)
        self._answers = answers
        self._accepted = 0

    def route_click(self, x: float, y: float) -> None:
        before = self.send_clicks
        super().route_click(x, y)
        if self.send_clicks > before and self.answer_visible:
            idx = min(self._accepted, len(self._answers) - 1)
            self.answer_text = self._answers[idx]
            self._accepted += 1


class _NoInputPage(w8t._FakePage):
    """composer 永不出现的页面（驱动 could-not-find-chat-input 路径）。"""

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector in tongyi_adapter._INPUT_SELECTORS:
            return ("none", False, None)
        return super().classify(selector)


def _spec(index: int, mode: str = "normal") -> TongyiBatchItemSpec:
    return TongyiBatchItemSpec(
        business_key=f"run-1-task-{index}",
        query=f"第{index}题的重疾险有哪些",
        mode=mode,
        file_stem=f"run-1-task-{index}-a1",
    )


def _per_task_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: w8t._FakePage) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_TONGYI_HEADLESS", "1")
    w8t._install_fake_browser(monkeypatch, page)


# ---------------------------------------------------------------------------
# 1) 答案验收门
# ---------------------------------------------------------------------------


def test_answer_gate_quota_text_becomes_wall_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配额文案当答案采回 → wall_quota（batch 路径）；存证截图带 per-item stem。"""
    page = _ScriptedAnswerPage([_QUOTA_TEXT], messages=0)
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[0].error_message and "体验次数已用完" in outcomes[0].error_message
    assert "wall_quota" in outcomes[0].error_message
    assert (tmp_path / "evidence" / "run-1-task-1-a1-answer_wall.png").is_file()


async def test_answer_gate_quota_text_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径同一道门：_WallError → ApplicationError(type=wall_quota,
    non_retryable)——配额文案绝不再能以 live_valid 落库。"""
    page = _ScriptedAnswerPage([_QUOTA_TEXT], messages=0)
    _per_task_env(tmp_path, monkeypatch, page)

    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            w8t._item(),
            session_factory=tongyi_adapter._PlaywrightTongyiSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "wall_quota"
    assert exc_info.value.non_retryable is True


def test_answer_gate_muted_text_carries_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁言文案当答案采回 → wall_muted，error_message 带解析出的解封时间。"""
    page = _ScriptedAnswerPage([_MUTED_TEXT], messages=0)
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_muted"
    assert outcomes[0].error_message and "until=2026-08-14T13:02:00" in (outcomes[0].error_message)


# ---------------------------------------------------------------------------
# 2) 软墙扫描无条件执行 + 答案正文剔除不变量
# ---------------------------------------------------------------------------


def test_softban_scan_runs_even_with_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`if not answer_text:` 门已删：出了正常答案，页面上的过频通知仍命中
    wall_send（旧代码此场景直接放行 = 事故根因之一）。"""
    page = _ScriptedAnswerPage([_OK_ANSWER], messages=0)
    page.body_text = "系统提示：请求频率过高，请稍后再试"
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_send"
    assert outcomes[0].error_message and "请求频率过高" in outcomes[0].error_message


def test_softban_scan_excludes_answer_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """零误伤不变量：答案正文本身提及「请求频率过高」不翻标记（扫描前剔除
    答案正文）。"""
    answer = "这是答案：当平台提示请求频率过高时，应降低发送频率并重试。"
    page = _ScriptedAnswerPage([answer], messages=0)
    page.body_text = answer  # 页面文本=答案气泡内容（无额外系统通知）
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].answer is not None
    assert "请求频率过高" in outcomes[0].answer.answer_text


def test_login_phrases_inside_normal_answer_do_not_trigger_wall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录词只出现在答案气泡时不是墙；墙判定必须有可见模态/iframe 证据。"""
    answer = (
        "请登录后使用账号管理功能；页面也可能显示“立即登录”或“登录以继续”，"
        "这些都是本文正在解释的界面文案。"
    )
    page = _ScriptedAnswerPage([answer], messages=0)
    page.body_text = answer
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].answer is not None
    assert outcomes[0].answer.answer_text == answer


# ---------------------------------------------------------------------------
# 3) batch 连坐三语义
# ---------------------------------------------------------------------------


def test_batch_muted_cascades_all_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wall_muted=账号级封禁：余题（含不同 mode）全 aborted，零浏览器交互。"""
    page = _ScriptedAnswerPage([_MUTED_TEXT], messages=0)
    session = w8t._make_session(tmp_path, monkeypatch, page)
    specs = [_spec(1), _spec(2, mode="deep_think"), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "aborted", "aborted"]
    assert outcomes[0].error_type == "wall_muted"
    assert all(o.error_type == "aborted_after_failure" for o in outcomes[1:])
    assert all("wall_muted" in (o.error_message or "") for o in outcomes[1:])
    # 余题零交互：键盘事件恰好只有第 1 题的字符
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query)


def test_batch_quota_cascades_same_mode_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wall_quota 按 (账号×mode) 计费：normal 题撞配额 → 后续 normal 题
    aborted（批次未停），夹在中间的 deep_think 题照常跑通。"""
    page = _ScriptedAnswerPage([_QUOTA_TEXT, _OK_ANSWER, _OK_ANSWER], messages=0)
    page.thinking_payload = _THINKING_PAYLOAD  # deep_think 题的思考流程卡证据
    session = w8t._make_session(tmp_path, monkeypatch, page)
    specs = [_spec(1), _spec(2, mode="deep_think"), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "ok", "aborted"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[1].answer is not None and outcomes[1].answer.answer_text == _OK_ANSWER
    assert outcomes[2].error_type == "aborted_after_failure"
    assert outcomes[2].error_message and "same-mode quota wall" in outcomes[2].error_message
    # 第 3 题零交互：键盘逐字事件 = 第 1、2 题字符
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query)


def test_batch_refusal_does_not_cascade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """wall_refusal=题级内容失败：不连坐，后续题照常跑通。"""
    page = _ScriptedAnswerPage([_REFUSAL_TEXT, _OK_ANSWER, _OK_ANSWER], messages=0)
    session = w8t._make_session(tmp_path, monkeypatch, page)
    specs = [_spec(1), _spec(2), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "ok", "ok"]
    assert outcomes[0].error_type == "wall_refusal"
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query) + list(specs[2].query)


# ---------------------------------------------------------------------------
# 4) 禁言 banner（composer 不可得路径）
# ---------------------------------------------------------------------------


def test_muted_banner_upgrades_no_input_to_wall_muted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """composer 长期不可得 + 页面禁言 banner → wall_muted（带 until），不再是
    笼统的 could-not-find-chat-input incomplete。"""
    page = _NoInputPage(messages=0)
    page.body_text = f"通义千问\n{_MUTED_TEXT}\n下载App"
    session = w8t._make_session(tmp_path, monkeypatch, page)
    specs = [_spec(1), _spec(2)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "aborted"]
    assert outcomes[0].error_type == "wall_muted"
    assert outcomes[0].error_message and "until=2026-08-14T13:02:00" in (outcomes[0].error_message)
    assert outcomes[1].error_type == "aborted_after_failure"
    # 一题未发：全程无键盘输入
    assert [e for e in page.events if e[0] == "key"] == []


def test_no_input_without_banner_stays_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照：无 banner 时原语义不变——could-not-find-chat-input incomplete。"""
    page = _NoInputPage(messages=0)
    page.body_text = "正常页面，只是没有输入框"
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["incomplete"]
    assert outcomes[0].error_type == "answer_capture_incomplete"
    assert outcomes[0].error_message and "could-not-find-chat-input" in (outcomes[0].error_message)


# ---------------------------------------------------------------------------
# 5) mode_unconfirmed（deep_think 无 bar_workflow 思考流程卡证据）
# ---------------------------------------------------------------------------


def test_mode_unconfirmed_fails_deep_think_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """菜单确认切到思考研究但无思考流程卡 DOM 证据 → 失败 outcome
    mode_unconfirmed（不落 ok/completed）；无卡无引用 → trace 不出空证据。"""
    page = _ScriptedAnswerPage([_OK_ANSWER], messages=0)
    page.thinking_payload = None  # 探针返回 None → 无卡
    session = w8t._make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1, mode="deep_think")], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "mode_unconfirmed"
    assert outcomes[0].error_message and "bar_workflow" in outcomes[0].error_message
    assert page.composer_mode == "deep_think"  # 模式确实被确保到思考研究（请求态已下达）
    assert not (tmp_path / "evidence" / "run-1-task-1-a1-sse-trace.json").exists()


async def test_mode_unconfirmed_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径：_ModeUnconfirmed → ApplicationError(type=mode_unconfirmed,
    non_retryable)。"""
    page = _ScriptedAnswerPage([_OK_ANSWER], messages=0)
    _per_task_env(tmp_path, monkeypatch, page)

    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            w8t._item(mode="deep_think"),
            session_factory=tongyi_adapter._PlaywrightTongyiSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "mode_unconfirmed"
    assert exc_info.value.non_retryable is True
