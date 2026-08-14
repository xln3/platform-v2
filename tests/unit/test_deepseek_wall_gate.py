"""deepseek 适配器「配额/拒答/封禁」检测层单测（2026-08-14，墙词表接入）。

对照豆包样板（test_doubao_wall_gate.py）逐点覆盖，fake 浏览器基建复用
test_w8_deepseek_batch（_FakePage/_FakeCDP/_install_fake_browser，单一出处
绝不复制漂移；真实浏览器绝不启动）：

1. 答案验收门：平台提示文案（配额/禁言/拒答）被当作答案采回 → _WallError
   走既有墙管道（batch 与 per-task 两路径）；
2. 软墙/实名扫描无条件执行（`if not answer_text:` 门已删），且已出答案时
   答案正文从扫描文本剔除（旧零误伤不变量保持）；
3. batch 连坐三语义：wall_muted 全连坐 / wall_quota 只连坐同 mode /
   wall_refusal 不连坐；
4. composer 不可得路径的禁言 banner → wall_muted（带 until）；
5. deep_think 无 SSE 思考证据 → mode_unconfirmed（non_retryable，不落
   completed；trace 已先落盘取证）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_w8_deepseek_batch as w8
from temporalio.exceptions import ApplicationError

from workflows.activities import deepseek_adapter
from workflows.activities.deepseek_adapter import (
    DeepseekAdapterConfig,
    DeepseekBatchItemSpec,
    run_deepseek_collection,
)

# 词表样例（wall_lexicon deepseek 表：「免费额度已用完」须伴随「今日/本月」语境）。
_QUOTA_TEXT = "今日免费额度已用完，请明天再试或下月再来。"
# 禁言 regex 为 common 表（平台第二人称铁证 + 具体解封时间）。
_MUTED_TEXT = (
    "由于违反用户使用规范，你的账号已被禁言至 2026 年 8 月 14 日 13:02，"
    "如有疑问请联系我们。"
)
_REFUSAL_TEXT = "服务器繁忙，请稍后再试。"

# 最小 deep_think SSE 流：thinking_enabled=true 快照 + THINK 碎片 + RESPONSE
# 正文（无 TOOL_OPEN——避免 opened-source 预览抓取触发真实 DNS 解析）。
_SSE_BODY_DEEP_THINK = (
    'data: {"v":{"response":{"message_id":2,"role":"ASSISTANT","thinking_enabled":true,'
    '"search_enabled":true,"fragments":[{"id":2,"type":"THINK","content":"先想一下",'
    '"references":[],"stage_id":1}]}}}\n\n'
    'data: {"p":"response/fragments","o":"APPEND","v":'
    '[{"id":3,"type":"RESPONSE","content":"深度答案","references":[],"stage_id":2}]}\n\n'
    'data: {"v":"。"}\n\n'
    'data: {"p":"response/status","o":"SET","v":"FINISHED"}\n\n'
    "data: [DONE]\n"
)


def _sse_body_with_text(text: str) -> str:
    """与 w8._SSE_BODY 同构、替换答案正文的 SSE 流（组装后正文 = text + 「。」）。"""
    return w8._SSE_BODY.replace("这是答案", text)


class _ScriptedCDP(w8._FakeCDP):
    """按发送次序轮换 SSE body 的 CDP fake（同一题内 completion capture 与
    raw capture 两次 getResponseBody 读同一份；下一题切下一份）。"""

    def __init__(self, page: w8._FakePage, bodies: list[str]) -> None:
        super().__init__(page)
        self._bodies = bodies
        self._completions = 0

    def send(self, method: str, params: dict | None = None) -> dict:
        if method == "Network.getResponseBody":
            idx = min(max(self._completions - 1, 0), len(self._bodies) - 1)
            return {"body": self._bodies[idx], "base64Encoded": False}
        return {}

    def emit_completion(self) -> None:
        # 先自增再广播：loadingFinished handler 在广播期间同步 getResponseBody
        # 抓 body，计数必须先指向本题。
        self._completions += 1
        super().emit_completion()


class _NoInputPage(w8._FakePage):
    """composer 永不出现的页面（驱动 could-not-find-chat-input 路径）。"""

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector in deepseek_adapter._INPUT_SELECTORS:
            return ("none", False, None)
        return super().classify(selector)


def _make_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: w8._FakePage,
    *,
    bodies: list[str] | None = None,
) -> tuple[deepseek_adapter._PlaywrightDeepseekSession, Path]:
    """fake 浏览器全链路 session；bodies 非空时换装 _ScriptedCDP 逐题轮换。"""
    evidence = w8._adapter_env(tmp_path, monkeypatch, page)
    if bodies is not None:
        page.cdp = _ScriptedCDP(page, bodies)
    config = DeepseekAdapterConfig.from_env()
    return deepseek_adapter._PlaywrightDeepseekSession(config, evidence, "batch-stem"), evidence


def _spec(index: int, mode: str = "normal") -> DeepseekBatchItemSpec:
    return DeepseekBatchItemSpec(
        business_key=f"run-1-task-{index}",
        query=f"第{index}题的重疾险有哪些",
        mode=mode,
        file_stem=f"run-1-task-{index}-a1",
    )


# ---------------------------------------------------------------------------
# 1) 答案验收门
# ---------------------------------------------------------------------------


def test_answer_gate_quota_text_becomes_wall_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配额文案当答案采回 → wall_quota（batch 路径）；存证截图带 per-item stem。"""
    page = w8._FakePage(messages=0)
    session, evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(_QUOTA_TEXT)]
    )

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[0].error_message and "免费额度已用完" in outcomes[0].error_message
    assert "wall_quota" in outcomes[0].error_message
    assert (evidence / "run-1-task-1-a1-answer_wall.png").is_file()


async def test_answer_gate_quota_text_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径同一道门：_WallError → ApplicationError(type=wall_quota,
    non_retryable)——配额文案绝不再能以 live_valid 落库。"""
    page = w8._FakePage(messages=0)
    w8._adapter_env(tmp_path, monkeypatch, page)
    page.cdp = _ScriptedCDP(page, [_sse_body_with_text(_QUOTA_TEXT)])

    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            w8._item(),
            session_factory=deepseek_adapter._PlaywrightDeepseekSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "wall_quota"
    assert exc_info.value.non_retryable is True


def test_answer_gate_muted_text_carries_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁言文案当答案采回 → wall_muted，error_message 带解析出的解封时间。"""
    page = w8._FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(_MUTED_TEXT)]
    )

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_muted"
    assert outcomes[0].error_message and "until=2026-08-14T13:02:00" in (
        outcomes[0].error_message
    )


# ---------------------------------------------------------------------------
# 2) 软墙扫描无条件执行 + 答案正文剔除不变量
# ---------------------------------------------------------------------------


def test_softban_scan_runs_even_with_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`if not answer_text:` 门已删：出了正常答案，页面上的过频通知仍命中
    wall_send（旧代码此场景直接放行 = 事故根因之一）。"""
    page = w8._FakePage(messages=0)
    page.body_text = "系统通知：今日请求过频，请稍后再试"
    session, _evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_send"
    assert outcomes[0].error_message and "今日请求过频" in outcomes[0].error_message


def test_softban_scan_excludes_answer_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零误伤不变量：答案正文本身提及「请求频率过高」不翻标记（扫描前剔除
    答案正文）。"""
    answer = "这是答案：当平台提示请求频率过高时，应降低发送频率并重试。"
    page = w8._FakePage(messages=0)
    page.body_text = answer  # 页面文本=答案气泡内容（无额外系统通知）
    session, _evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(answer[:-1])]
    )

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].answer is not None
    assert "请求频率过高" in outcomes[0].answer.answer_text


# ---------------------------------------------------------------------------
# 3) batch 连坐三语义
# ---------------------------------------------------------------------------


def test_batch_muted_cascades_all_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wall_muted=账号级封禁：余题（含不同 mode）全 aborted，零浏览器交互。"""
    page = w8._FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(_MUTED_TEXT)]
    )
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
    page = w8._FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path,
        monkeypatch,
        page,
        bodies=[_sse_body_with_text(_QUOTA_TEXT), _SSE_BODY_DEEP_THINK],
    )
    specs = [_spec(1), _spec(2, mode="deep_think"), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "ok", "aborted"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[1].answer is not None and outcomes[1].answer.answer_text == "深度答案。"
    assert outcomes[2].error_type == "aborted_after_failure"
    assert outcomes[2].error_message and "same-mode quota wall" in outcomes[2].error_message
    # 第 3 题零交互：键盘事件 = 第 1、2 题字符
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query)


def test_batch_refusal_does_not_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wall_refusal=题级内容失败：不连坐，后续题照常跑通。"""
    page = w8._FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path,
        monkeypatch,
        page,
        bodies=[_sse_body_with_text(_REFUSAL_TEXT), w8._SSE_BODY, w8._SSE_BODY],
    )
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
    page.body_text = f"DeepSeek\n{_MUTED_TEXT}\n下载电脑版"
    session, _evidence = _make_session(tmp_path, monkeypatch, page)
    specs = [_spec(1), _spec(2)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "aborted"]
    assert outcomes[0].error_type == "wall_muted"
    assert outcomes[0].error_message and "until=2026-08-14T13:02:00" in (
        outcomes[0].error_message
    )
    assert outcomes[1].error_type == "aborted_after_failure"
    # 一题未发：全程无键盘输入
    assert [e for e in page.events if e[0] == "key"] == []


def test_no_input_without_banner_stays_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照：无 banner 时原语义不变——could-not-find-chat-input incomplete。"""
    page = _NoInputPage(messages=0)
    page.body_text = "正常页面，只是没有输入框"
    session, _evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["incomplete"]
    assert outcomes[0].error_type == "answer_capture_incomplete"
    assert outcomes[0].error_message and "could-not-find-chat-input" in (
        outcomes[0].error_message
    )


# ---------------------------------------------------------------------------
# 5) mode_unconfirmed（deep_think 无 SSE 思考证据）
# ---------------------------------------------------------------------------


def test_mode_unconfirmed_fails_deep_think_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chip 确认但 SSE 无 thinking 碎片证据 → 失败 outcome mode_unconfirmed
    （不落 ok/completed）；trace 已先落盘取证（deep_think_active 如实 false）。"""
    page = w8._FakePage(messages=0)  # 默认 w8._SSE_BODY thinking_enabled=false
    session, evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1, mode="deep_think")], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "mode_unconfirmed"
    assert outcomes[0].error_message and "no thinking-fragment evidence" in (
        outcomes[0].error_message
    )
    trace = json.loads((evidence / "run-1-task-1-a1-sse-trace.json").read_text("utf-8"))
    assert trace["deep_think_active"] is False


async def test_mode_unconfirmed_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径：_ModeUnconfirmed → ApplicationError(type=mode_unconfirmed,
    non_retryable)。"""
    page = w8._FakePage(messages=0)
    w8._adapter_env(tmp_path, monkeypatch, page)

    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            w8._item(mode="deep_think"),
            session_factory=deepseek_adapter._PlaywrightDeepseekSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "mode_unconfirmed"
    assert exc_info.value.non_retryable is True
