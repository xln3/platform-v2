"""文心一言采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / deep_think 拒绝 /
profile 未配置 / screenshot_ref 过 DLP / 代理打码。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import CollectionEvidenceRef, CollectionTaskInput
from workflows.activities.yiyan_adapter import (
    _ANSWER_MODE_FAST_ITEM_SELECTOR,
    _ANSWER_MODE_SELECTOR,
    _ANSWER_MODE_STATE_JS,
    _ANSWER_MODE_TASK_ITEM_SELECTOR,
    _STRIP_THINKING_JS,
    CollectedAnswer,
    YiyanAdapterConfig,
    _answer_mode_state,
    _build_yiyan_trace,
    _deep_think_chip_state,
    _ensure_deep_think,
    _extract_references,
    _ModeToggleFailed,
    _task_execution_running,
    _task_execution_state,
    _task_result_from_collected,
    _wait_dom_stream,
    _WallError,
    run_yiyan_collection,
)


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-5",
        query="友邦的重疾险有哪些",
        model="yiyan",
        region="Shanghai",
        mode=mode,
        adapter="yiyan",
    )


class _FakeSession:
    """注入的浏览器层替身：按构造参数返回结果或抛墙。"""

    def __init__(
        self,
        *,
        result: CollectedAnswer | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.stages: list[str] = []
        self.modes: list[str] = []

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer:
        on_stage("fake_stage")
        self.stages.append("fake_stage")
        self.modes.append(mode)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _factory(session: _FakeSession) -> Callable[..., _FakeSession]:
    def _make(config: YiyanAdapterConfig, evidence_dir: Path, file_stem: str) -> _FakeSession:
        return session

    return _make


@pytest.fixture
def adapter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_YIYAN_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_YIYAN_HEADLESS", "1")
    return evidence


async def test_success_maps_result_fields(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="友邦在售的重疾险包括……",
            references=[
                {
                    "url": "https://example.com/article/2",
                    "title": "产品页",
                    "sitename": " example.com ",
                }
            ],
            screenshot_path=shot,
        )
    )
    beats: list[dict[str, Any]] = []
    result = await run_yiyan_collection(
        _item(),
        session_factory=_factory(session),
        heartbeat=lambda payload: beats.append(payload),
    )
    assert result.business_key == "run-9-task-5"
    assert result.answer_text == "友邦在售的重疾险包括……"
    assert result.citations[0]["url"] == "https://example.com/article/2"
    assert result.screenshot_ref == f"file://{shot}"
    assert result.quality_state == "live_valid"
    assert beats and beats[0]["business_key"] == "run-9-task-5"


async def test_login_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-5-a1-login.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_login_required", "yiyan login wall detected", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_yiyan_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_login_required"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_unknown_mode_rejected_as_unsupported_mode(adapter_env: Path) -> None:
    """normal/deep_think 之外的 mode 仍被 mode 门拦下（浏览器不启动）。"""
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_yiyan_collection(
            _item(mode="expert"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []  # mode 门在浏览器启动之前


async def test_missing_profile_dir_is_adapter_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_YIYAN_PROFILE_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_yiyan_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "adapter_not_configured"
    assert exc_info.value.non_retryable is True
    assert session.stages == []

    monkeypatch.delenv("GEO_YIYAN_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_yiyan_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"
    assert exc_info_unset.value.non_retryable is True


async def test_screenshot_ref_and_answer_pass_dlp(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="真实回答正文",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_yiyan_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    # 真调用 DLP：两个字段都必须干净（persist 层同语义）
    assert_secret_free(result.screenshot_ref)
    assert_secret_free(result.answer_text)


async def test_success_maps_structured_citations(adapter_env: Path) -> None:
    """references → 结构化 citations（W2 source_fetch 唯一输入；cited_text=None
    诚实缺省，transcript 口径落 unverifiable）；非 http(s) URL 被滤除。"""
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="正文",
            references=[
                {"url": "https://example.com/a", "title": "标题A", "sitename": "站点A"},
                {"url": "not-a-url", "title": "假链接"},
            ],
            screenshot_path=shot,
        )
    )
    result = await run_yiyan_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    assert result.citations == [
        {"url": "https://example.com/a", "title": "标题A", "cited_text": None}
    ]


# ---------------------------------------------------------------------------
# deep_think（20260810 解锁）：chip 状态探针 / 显式确保 / trace 证据映射
# ---------------------------------------------------------------------------


class _ChipFakeLocator:
    """chip locator 替身：记录点击；human_click 拿不到布局会回退原生 click。"""

    def __init__(self, page: _ChipFakePage) -> None:
        self._page = page

    @property
    def first(self) -> _ChipFakeLocator:
        return self

    def click(self, timeout: int | None = None) -> None:
        self._page.clicks += 1
        self._page.flips -= 1  # 简化：点击即翻转到目标态（剩余翻转数-1）


class _ChipFakePage:
    """chip 探针页替身：flips=还需几次点击才到目标态（None=永不到达）。"""

    def __init__(self, state: dict | None, flips: int = 1) -> None:
        self._state = state
        self.flips = flips
        self.clicks = 0

    def evaluate(self, script: str) -> object:
        return self._state

    def locator(self, selector: str) -> _ChipFakeLocator:
        assert "ci-model-button" in selector
        return _ChipFakeLocator(self)

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def screenshot(self, path: object = None, **kw: object) -> None:
        return None

    # _ensure_deep_think 内 human_click 可能调用的其它探针面（best-effort 兜底）
    def mouse(self) -> None:  # pragma: no cover - human_click 协议兜底
        return None


def test_chip_state_parse() -> None:
    active = {"active": True, "inactive": False, "is_open": "1"}
    inactive = {"active": False, "inactive": True, "is_open": "0"}
    assert _deep_think_chip_state(_ChipFakePage(active)) is True
    assert _deep_think_chip_state(_ChipFakePage(inactive)) is False
    # class 与 is_open 不一致 / 探针缺失 → None（不猜）
    mixed = _ChipFakePage({"active": True, "inactive": False, "is_open": "0"})
    both_off = _ChipFakePage({"active": False, "inactive": False, "is_open": None})
    assert _deep_think_chip_state(mixed) is None
    assert _deep_think_chip_state(both_off) is None
    assert _deep_think_chip_state(_ChipFakePage(None)) is None


def test_ensure_deep_think_idempotent_when_already_engaged() -> None:
    page = _ChipFakePage({"active": True, "inactive": False, "is_open": "1"})
    _ensure_deep_think(page, __import__("random").Random(0), engaged=True, shot=lambda s: None)
    assert page.clicks == 0  # 已在目标态零点击


def test_ensure_deep_think_flips_and_confirms() -> None:
    page = _ChipFakePage({"active": False, "inactive": True, "is_open": "0"}, flips=1)
    # 点击后 fake 翻到目标态：模拟 evaluate 第二次返回 active
    original_evaluate = page.evaluate

    def _eval(script: str) -> object:
        if page.clicks >= 1:
            return {"active": True, "inactive": False, "is_open": "1"}
        return original_evaluate(script)

    page.evaluate = _eval  # type: ignore[method-assign]
    _ensure_deep_think(page, __import__("random").Random(0), engaged=True, shot=lambda s: None)
    assert page.clicks >= 1


def test_ensure_deep_think_unconfirmable_fails() -> None:
    page = _ChipFakePage(None)  # 永远读不出
    with pytest.raises(_ModeToggleFailed):
        _ensure_deep_think(page, __import__("random").Random(0), engaged=True, shot=lambda s: None)


class _NewModeLocator:
    def __init__(self, page: _NewModePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _NewModeLocator:
        return self

    def is_visible(self, timeout: int | None = None) -> bool:
        return (
            self._selector
            in {
                _ANSWER_MODE_FAST_ITEM_SELECTOR,
                _ANSWER_MODE_TASK_ITEM_SELECTOR,
            }
            and self._page.expanded
        )

    def scroll_into_view_if_needed(self, timeout: int | None = None) -> None:
        return None

    def bounding_box(self) -> None:
        return None

    def click(self, **kw: object) -> None:
        if self._selector == _ANSWER_MODE_SELECTOR:
            self._page.expanded = True
            self._page.selector_clicks += 1
        elif self._selector == _ANSWER_MODE_FAST_ITEM_SELECTOR and self._page.expanded:
            self._page.mode = "fast"
            self._page.expanded = False
            self._page.fast_clicks += 1
        elif self._selector == _ANSWER_MODE_TASK_ITEM_SELECTOR and self._page.expanded:
            self._page.mode = "task"
            self._page.expanded = False
            self._page.task_clicks += 1
        else:  # pragma: no cover - defensive fake contract
            raise RuntimeError(f"unexpected click: {self._selector}")


class _NewModePage:
    """20260831 新回答模式下拉 fake：normal=fast，deep_think=task。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.expanded = False
        self.selector_clicks = 0
        self.fast_clicks = 0
        self.task_clicks = 0

    def evaluate(self, script: str) -> object:
        if script == _ANSWER_MODE_STATE_JS:
            labels = {"fast": "快速", "task": "任务"}
            return {
                "surface": "answer_mode_selector",
                "mode": self.mode,
                "label": labels.get(self.mode, "未知"),
                "expanded": self.expanded,
            }
        return None  # 旧 deep-think chip 不存在

    def locator(self, selector: str) -> _NewModeLocator:
        return _NewModeLocator(self, selector)

    def wait_for_timeout(self, ms: int) -> None:
        return None


def test_new_answer_mode_state_parse() -> None:
    assert _answer_mode_state(_NewModePage("fast")) == "normal"
    assert _answer_mode_state(_NewModePage("task")) == "task"
    assert _answer_mode_state(_NewModePage("future-mode")) == "unknown"


def test_new_fast_mode_is_idempotent_for_normal() -> None:
    page = _NewModePage("fast")
    _ensure_deep_think(page, __import__("random").Random(0), engaged=False, shot=lambda s: None)
    assert page.selector_clicks == 0
    assert page.fast_clicks == 0
    assert page.task_clicks == 0


def test_new_task_mode_switches_to_fast_for_normal() -> None:
    page = _NewModePage("task")
    _ensure_deep_think(page, __import__("random").Random(0), engaged=False, shot=lambda s: None)
    assert page.mode == "fast"
    assert page.selector_clicks == 1
    assert page.fast_clicks == 1
    assert page.task_clicks == 0


def test_new_task_mode_is_idempotent_for_deep_think() -> None:
    page = _NewModePage("task")
    _ensure_deep_think(page, __import__("random").Random(0), engaged=True, shot=lambda s: None)
    assert page.selector_clicks == 0
    assert page.fast_clicks == 0
    assert page.task_clicks == 0


def test_new_fast_mode_switches_to_task_for_deep_think() -> None:
    page = _NewModePage("fast")
    _ensure_deep_think(page, __import__("random").Random(0), engaged=True, shot=lambda s: None)
    assert page.mode == "task"
    assert page.selector_clicks == 1
    assert page.fast_clicks == 0
    assert page.task_clicks == 1


class _TaskStatusTitle:
    def __init__(self, text: str, *, visible: bool = True) -> None:
        self._text = text
        self._visible = visible

    def is_visible(self, timeout: int | None = None) -> bool:
        return self._visible

    def inner_text(self, timeout: int | None = None) -> str:
        return self._text


class _TaskStatusLocator:
    def __init__(self, titles: list[_TaskStatusTitle]) -> None:
        self._titles = titles

    def all(self) -> list[_TaskStatusTitle]:
        return self._titles


class _TaskStatusPage:
    def __init__(self, titles: list[_TaskStatusTitle]) -> None:
        self._titles = titles

    def locator(self, selector: str) -> _TaskStatusLocator:
        assert selector == ".thinking-steps-title-text"
        return _TaskStatusLocator(self._titles)


def test_task_execution_running_reads_explicit_platform_status() -> None:
    assert _task_execution_running(_TaskStatusPage([_TaskStatusTitle("任务执行中")])) is True
    assert _task_execution_running(_TaskStatusPage([_TaskStatusTitle("任务已完成")])) is False
    assert _task_execution_state(_TaskStatusPage([_TaskStatusTitle("任务执行完成")])) == "completed"
    assert _task_execution_state(_TaskStatusPage([])) == "unknown"
    assert (
        _task_execution_running(_TaskStatusPage([_TaskStatusTitle("任务执行中", visible=False)]))
        is False
    )


class _TaskWaitPage:
    def __init__(self, clock: object) -> None:
        self.clock = clock
        self.polls = 0

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.polls += 1
        self.clock.now += milliseconds / 1000  # type: ignore[attr-defined]


def test_dom_stream_does_not_finish_while_task_status_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = type("Clock", (), {"now": 1_000.0})()
    page = _TaskWaitPage(clock)
    monkeypatch.setattr("workflows.activities.yiyan_adapter.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("workflows.activities.yiyan_adapter._dom_stream_started", lambda _p: True)
    monkeypatch.setattr("workflows.activities.yiyan_adapter._loading_visible", lambda _p: False)
    monkeypatch.setattr(
        "workflows.activities.yiyan_adapter._task_execution_state",
        lambda p: "running" if p.polls < 4 else ("unknown" if p.polls < 8 else "completed"),
    )
    monkeypatch.setattr(
        "workflows.activities.yiyan_adapter._last_answer_text",
        lambda p: "研究过程" if p.polls < 4 else "最终答案",
    )

    result = _wait_dom_stream(
        page,
        appearance_timeout_s=1.0,
        timeout_s=10.0,
        quiet_s=1.0,
        poll_ms=500,
        require_task_completed=True,
    )

    assert result["finished"] is True
    assert result["bytes_received"] == len("最终答案")
    assert page.polls >= 9


class _ReferenceItem:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def get_attribute(self, name: str, timeout: int | None = None) -> str:
        assert name == "data-long-press-ext-info"
        return self._payload


class _ReferenceLocator:
    def __init__(self, items: list[_ReferenceItem]) -> None:
        self._items = items

    def all(self) -> list[_ReferenceItem]:
        return self._items


class _ReferencePage:
    def __init__(self, payloads: list[str]) -> None:
        self._items = [_ReferenceItem(payload) for payload in payloads]

    def locator(self, selector: str) -> _ReferenceLocator:
        if selector == "div.ai-thinking-steps li[data-long-press-ext-info]":
            return _ReferenceLocator(self._items)
        return _ReferenceLocator([])


def test_new_reference_list_reads_platform_dom_payload() -> None:
    page = _ReferencePage(
        [
            '{"link":"https://example.com/a?id=1","linkTitle":"来源 A"}',
            '{"link":"https://example.com/a?id=1#dup","linkTitle":"重复来源"}',
            '{"link":"javascript:alert(1)","linkTitle":"无效来源"}',
            "not-json",
        ]
    )
    assert _extract_references(page) == [
        {
            "url": "https://example.com/a?id=1",
            "title": "来源 A",
            "sitename": None,
        }
    ]


def test_build_yiyan_trace_shape() -> None:
    trace = _build_yiyan_trace("想了一下", deep_think_active=True)
    assert trace["engine"] == "yiyan"
    assert trace["transport"] == "dom"
    assert trace["deep_think_active"] is True
    assert trace["thinking_chain"] == [{"kind": "reasoning", "text": "想了一下"}]
    empty = _build_yiyan_trace("", deep_think_active=False)
    assert empty["thinking_chain"] == []


async def test_deep_think_mode_passes_gate_and_reaches_session(adapter_env: Path) -> None:
    """deep_think 不再被 mode 门拦下，且 mode 透传到浏览器会话层。"""
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(answer_text="正文", references=[], screenshot_path=shot)
    )
    result = await run_yiyan_collection(
        _item(mode="deep_think"),
        session_factory=_factory(session),
        heartbeat=lambda p: None,
    )
    assert result.quality_state == "live_valid"
    assert session.modes == ["deep_think"]


async def test_bogus_mode_rejected(adapter_env: Path) -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await run_yiyan_collection(
            _item(mode="expert"),
            session_factory=_factory(_FakeSession()),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True


def test_task_result_maps_trace_evidence(tmp_path: Path) -> None:
    """trace_path → kind="sse" 证据（transport="dom" 思考链）。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    trace = tmp_path / "run-9-task-5-sse-trace.json"
    trace.write_text("{}", encoding="utf-8")
    collected = CollectedAnswer(
        answer_text="正文", references=[], screenshot_path=shot, trace_path=trace
    )
    result = _task_result_from_collected(_item(mode="deep_think"), collected)
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "sse"
    assert result.evidence[0].relation_type == "answer_sse_trace"


def test_task_result_prefers_official_share_image_as_screenshot_ref(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.png"
    runtime.write_bytes(b"runtime")
    official = tmp_path / "official-share.png"
    official.write_bytes(b"official")
    collected = CollectedAnswer(
        answer_text="正文",
        references=[],
        screenshot_path=runtime,
        evidence=[
            CollectionEvidenceRef(
                kind="share_image",
                path=str(official),
                relation_type="official_share_image",
                mime_type="image/png",
                source_url="https://mr.baidu.com/r/example",
            )
        ],
    )

    result = _task_result_from_collected(_item(), collected)

    assert result.screenshot_ref == f"file://{official}"


def test_strip_thinking_js_serializes_tables_as_markdown() -> None:
    # 20260812 锚定（W3 表格碎片证据根治）：正文抽取必须把 <table> 序列化为
    # markdown 管道行（首行表头补分隔行、<pre> 首尾补换行防表头与前序文本粘连），
    # 防回退成 innerText 直出压平表格、丢行列对应。live 实证见 yiyan_adapter 注释。
    assert "querySelectorAll('table')" in _STRIP_THINKING_JS
    assert "querySelectorAll('th,td')" in _STRIP_THINKING_JS
    assert "'---'" in _STRIP_THINKING_JS
    assert "replaceWith" in _STRIP_THINKING_JS
    assert "'\\n' + lines.join" in _STRIP_THINKING_JS
