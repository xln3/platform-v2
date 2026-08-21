"""doubao 适配器「配额/拒答/封禁」检测层单测（2026-08-14，墙词表接入）。

覆盖五点接线（fake 浏览器基础设施复用 test_doubao_adapter /
test_doubao_mode_evidence，单一出处绝不复制漂移；真实浏览器绝不启动）：

1. 答案验收门：平台提示文案（配额/禁言/拒答）被当作答案采回 → _WallError
   走既有墙管道（batch 与 per-task 两路径）；
2. 软墙/实名扫描无条件执行（`if not answer_text:` 门已删），且已出答案时
   答案正文从扫描文本剔除（旧零误伤不变量保持）；
3. batch 连坐四语义：wall_muted 全连坐 / wall_quota 只连坐同 mode /
   模式控件失败只熔断同 mode / wall_refusal 不连坐；
4. composer 不可得路径的禁言 banner → wall_muted（带 until）；
5. deep_think 无 SSE 思考证据 → mode_unconfirmed（non_retryable，不落
   completed；trace 先落盘取证）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_doubao_adapter as tda
from temporalio.exceptions import ApplicationError
from test_doubao_adapter import _FakePage, _install_fake_browser, _item
from test_doubao_mode_evidence import _SSE_BODY_DEEP

from workflows.activities import doubao_adapter
from workflows.activities.doubao_adapter import (
    DoubaoAdapterConfig,
    DoubaoBatchItemSpec,
    run_doubao_collection,
)

# 2026-08-13 事故原文（live 实证，逐字）。
_QUOTA_TEXT = (
    "今日专家模式免费次数用完了，暂时无法使用专业版功能，先使用快速模式和我聊聊"
    "别的吧。开通豆包专业版，免等待，继续为你服务。"
)
_MUTED_TEXT = (
    "由于违反用户使用规范，你的账号已被禁言至 2026 年 8 月 14 日 13:02，如有疑问请联系我们。"
)
_REFUSAL_TEXT = "这个问题我们换个话题聊聊吧。"


def _sse_body_with_text(text: str) -> str:
    """与 tda._SSE_BODY 同构、替换答案正文的 SSE 流（JSON 安全的纯中文标点文本）。"""
    return tda._SSE_BODY.replace("这是答案", text)


class _ScriptedCDP(tda._FakeCDP):
    """按发送次序轮换 SSE body 的 CDP fake（同一题内 completion capture 与
    raw capture 两次 getResponseBody 读同一份；下一题切下一份）。"""

    def __init__(self, page: _FakePage, bodies: list[str]) -> None:
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


class _NoInputPage(_FakePage):
    """composer 永不出现的页面（驱动 could-not-find-chat-input 路径）。"""

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector in doubao_adapter._INPUT_SELECTORS:
            return ("none", False, None)
        return super().classify(selector)


def _make_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: _FakePage,
    *,
    bodies: list[str] | None = None,
) -> tuple[doubao_adapter._PlaywrightDoubaoSession, Path]:
    """fake 浏览器全链路 session；bodies 非空时换装 _ScriptedCDP 逐题轮换。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    _install_fake_browser(monkeypatch, page)
    if bodies is not None:
        page.cdp = _ScriptedCDP(page, bodies)
    config = DoubaoAdapterConfig.from_env()
    return doubao_adapter._PlaywrightDoubaoSession(config, evidence, "batch-stem"), evidence


def _spec(index: int, mode: str = "normal") -> DoubaoBatchItemSpec:
    return DoubaoBatchItemSpec(
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
    """配额文案当答案采回 → wall_quota（batch 路径）；证据截图带 per-item stem。"""
    page = _FakePage(messages=0)
    session, evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(_QUOTA_TEXT)]
    )

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[0].error_message and "免费次数用完" in outcomes[0].error_message
    assert "wall_quota" in outcomes[0].error_message
    assert (evidence / "run-1-task-1-a1-answer_wall.png").is_file()


async def test_answer_gate_quota_text_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径同一道门：_WallError → ApplicationError(type=wall_quota,
    non_retryable)——事故原文绝不再能以 live_valid 落库。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    page = _FakePage(messages=0)
    _install_fake_browser(monkeypatch, page)
    page.cdp = _ScriptedCDP(page, [_sse_body_with_text(_QUOTA_TEXT)])

    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(),
            session_factory=doubao_adapter._PlaywrightDoubaoSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "wall_quota"
    assert exc_info.value.non_retryable is True


def test_answer_gate_muted_text_carries_until(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁言文案当答案采回 → wall_muted，error_message 带解析出的解封时间。"""
    page = _FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(_MUTED_TEXT)]
    )

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
    page = _FakePage(messages=0)
    page.body_text = "系统通知：今日请求过频，请稍后再试"
    session, _evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "wall_send"
    assert outcomes[0].error_message and "今日请求过频" in outcomes[0].error_message


def test_softban_scan_excludes_answer_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """零误伤不变量：答案正文本身提及「过频」不翻标记（扫描前剔除答案正文）。"""
    answer = "这是答案：今日请求过频时请降低发送频率并重试。"
    page = _FakePage(messages=0)
    page.body_text = answer  # 页面文本=答案气泡内容（无额外系统通知）
    session, _evidence = _make_session(
        tmp_path, monkeypatch, page, bodies=[_sse_body_with_text(answer)]
    )

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].answer is not None
    assert "今日请求过频" in outcomes[0].answer.answer_text


# ---------------------------------------------------------------------------
# 3) batch 连坐三语义
# ---------------------------------------------------------------------------


def test_batch_muted_cascades_all_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wall_muted=账号级封禁：余题（含不同 mode）全 aborted，零浏览器交互。"""
    page = _FakePage(messages=0, deep_think=True)
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
    page = _FakePage(messages=0, deep_think=True)
    session, _evidence = _make_session(
        tmp_path,
        monkeypatch,
        page,
        bodies=[_sse_body_with_text(_QUOTA_TEXT), _SSE_BODY_DEEP],
    )
    specs = [_spec(1), _spec(2, mode="deep_think"), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "ok", "aborted"]
    assert outcomes[0].error_type == "wall_quota"
    assert outcomes[1].answer is not None and outcomes[1].answer.answer_text == "这是答案"
    assert outcomes[2].error_type == "aborted_after_failure"
    assert outcomes[2].error_message and "same-mode quota wall" in outcomes[2].error_message
    # 第 3 题零交互：键盘事件 = 第 1、2 题字符
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query)


def test_batch_refusal_does_not_cascade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """wall_refusal=题级内容失败：不连坐，后续题照常跑通。"""
    page = _FakePage(messages=0)
    session, _evidence = _make_session(
        tmp_path,
        monkeypatch,
        page,
        bodies=[_sse_body_with_text(_REFUSAL_TEXT), tda._SSE_BODY, tda._SSE_BODY],
    )
    specs = [_spec(1), _spec(2), _spec(3)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "ok", "ok"]
    assert outcomes[0].error_type == "wall_refusal"
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query) + list(specs[2].query)


def test_batch_mode_toggle_failure_opens_same_mode_session_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模式 trigger 在单题内穷尽重试仍不可用时，同 session 后续同 mode 零交互。

    这避免一次 selector 漂移让四题依次产生四个同源 mode_toggle 失败；结果仍与
    输入等长，未执行题明确记 aborted_after_failure，绝不伪造回答。
    """
    page = _FakePage(messages=0, deep_think=False)
    session, _evidence = _make_session(tmp_path, monkeypatch, page)
    specs = [_spec(i, mode="deep_think") for i in range(1, 5)]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [outcome.status for outcome in outcomes] == ["wall", "aborted", "aborted", "aborted"]
    assert outcomes[0].error_type == "deep_think_toggle_failed"
    assert all(outcome.error_type == "aborted_after_failure" for outcome in outcomes[1:])
    assert all(
        "same-mode mode-toggle circuit" in (outcome.error_message or "") for outcome in outcomes[1:]
    )
    # 模式确认失败发生在输入前；后续三题也没有任何键盘/发送交互。
    assert [event for event in page.events if event[0] == "key"] == []


# ---------------------------------------------------------------------------
# 4) 禁言 banner（composer 不可得路径）
# ---------------------------------------------------------------------------


def test_muted_banner_upgrades_no_input_to_wall_muted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """composer 长期不可得 + 页面禁言 banner → wall_muted（带 until），不再是
    笼统的 could-not-find-chat-input incomplete。"""
    page = _NoInputPage(messages=0)
    page.body_text = f"豆包\n{_MUTED_TEXT}\n下载电脑版"
    session, _evidence = _make_session(tmp_path, monkeypatch, page)
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
    session, _evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1)], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["incomplete"]
    assert outcomes[0].error_type == "answer_capture_incomplete"
    assert outcomes[0].error_message and "could-not-find-chat-input" in (outcomes[0].error_message)


# ---------------------------------------------------------------------------
# 5) mode_unconfirmed（deep_think 无 SSE 思考证据）
# ---------------------------------------------------------------------------


def test_mode_unconfirmed_fails_deep_think_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI toggle 确认但 SSE 无 thinking root → 失败 outcome mode_unconfirmed
    （不落 ok/completed）；trace 先落盘取证（mode 段 actual=normal 如实留痕）。"""
    page = _FakePage(messages=0, deep_think=True)  # 默认 _SSE_BODY 无 thinking root
    session, evidence = _make_session(tmp_path, monkeypatch, page)

    outcomes = session.collect_batch([_spec(1, mode="deep_think")], on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall"]
    assert outcomes[0].error_type == "mode_unconfirmed"
    assert outcomes[0].error_message and "no thinking-root evidence" in (outcomes[0].error_message)
    trace = json.loads((evidence / "run-1-task-1-a1-sse-trace.json").read_text("utf-8"))
    assert trace["mode"]["requested"] == "deep_think"
    assert trace["mode"]["actual"] == "normal"


async def test_mode_unconfirmed_per_task_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 路径：_ModeUnconfirmed → ApplicationError(type=mode_unconfirmed,
    non_retryable)。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    page = _FakePage(messages=0, deep_think=True)
    _install_fake_browser(monkeypatch, page)

    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(mode="deep_think"),
            session_factory=doubao_adapter._PlaywrightDoubaoSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "mode_unconfirmed"
    assert exc_info.value.non_retryable is True
