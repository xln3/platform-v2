"""doubao 采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 / deep_think mode 透传与 toggle
失败（W1 起解锁）/ profile 未配置 / screenshot_ref 过 DLP。
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities import doubao_adapter
from workflows.activities.collection import CollectionTaskInput
from workflows.activities.doubao_adapter import (
    CollectedAnswer,
    DoubaoAdapterConfig,
    _capture_source_screenshots,
    _clean_profile_crash_state,
    _DeepThinkToggleFailed,
    _ensure_fresh_chat,
    _HumanizedPageFacade,
    _IncompleteCapture,
    _PlaywrightDoubaoSession,
    _try_close_overlays,
    _try_enable_deep_think,
    _WallError,
    mask_proxy_url,
    run_doubao_collection,
)
from workflows.activities.human_like import human_pause


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-7-task-3",
        query="中意人寿的重疾险有哪些",
        model="doubao",
        region="Beijing",
        mode=mode,
        adapter="doubao",
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
    def _make(config: DoubaoAdapterConfig, evidence_dir: Path, file_stem: str) -> _FakeSession:
        return session

    return _make


@pytest.fixture
def adapter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    # W8：防宿主环境残留 GEO_DOUBAO_CDP_URL 把测试带进 attach 路径
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    return evidence


async def test_success_maps_result_fields(adapter_env: Path) -> None:
    shot = adapter_env / "run-7-task-3-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="中意人寿在售的重疾险包括……",
            references=[
                {
                    "url": "https://example.com/article/1",
                    "title": "产品页",
                    "sitename": " example.com ",
                    "summary": None,
                    "index": 0,
                }
            ],
            screenshot_path=shot,
        )
    )
    beats: list[dict[str, Any]] = []
    result = await run_doubao_collection(
        _item(),
        session_factory=_factory(session),
        heartbeat=lambda payload: beats.append(payload),
    )
    assert result.business_key == "run-7-task-3"
    assert "中意人寿在售的重疾险" in result.answer_text
    assert "参考来源：" in result.answer_text
    assert "https://example.com/article/1" in result.answer_text
    assert result.screenshot_ref == f"file://{shot}"
    assert result.screenshot_ref.startswith("file://")
    assert result.quality_state == "live_valid"
    assert result.citations == [
        {
            "url": "https://example.com/article/1",
            "title": "产品页",
            "cited_text": None,
        }
    ]
    assert [(item.kind, item.relation_type) for item in result.evidence] == [
        ("answer_screenshot", "answer_page")
    ]
    assert beats and beats[0]["business_key"] == "run-7-task-3"


async def test_login_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-7-task-3-a1-login.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_login_required", "doubao login wall detected", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_login_required"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_deep_think_mode_passes_through_to_session(adapter_env: Path) -> None:
    """W1 起 deep_think 解锁：mode 原样透传到浏览器层，由 session 负责 UI toggle。"""
    shot = adapter_env / "run-7-task-3-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="深度思考后的回答",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_doubao_collection(
        _item(mode="deep_think"), session_factory=_factory(session), heartbeat=lambda p: None
    )
    assert session.modes == ["deep_think"]
    assert result.quality_state == "live_valid"
    assert "深度思考后的回答" in result.answer_text


async def test_deep_think_toggle_failure_is_non_retryable(adapter_env: Path) -> None:
    """toggle 无法确认启用 → deep_think_toggle_failed，绝不静默回退 normal。"""
    evidence = adapter_env / "run-7-task-3-a1-deep_think.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_DeepThinkToggleFailed("picker still reads 快速", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(mode="deep_think"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "deep_think_toggle_failed"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_unknown_mode_rejected_as_unsupported(adapter_env: Path) -> None:
    """normal/deep_think 之外的 mode 仍诚实拒绝（mode 门在浏览器启动之前）。"""
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(mode="vision"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []
    assert session.modes == []


async def test_search_queries_pass_through_to_result(adapter_env: Path) -> None:
    """W1：session 抽到的平台真实检索词透传进 CollectionTaskResult。"""
    shot = adapter_env / "run-7-task-3-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="回答",
            references=[],
            screenshot_path=shot,
            search_queries=[
                {"query": "中意人寿 重疾险", "ordinal": 1},
                {"query": "中意人寿 产品", "ordinal": 2},
            ],
        )
    )
    result = await run_doubao_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    assert result.search_queries == [
        {"query": "中意人寿 重疾险", "ordinal": 1},
        {"query": "中意人寿 产品", "ordinal": 2},
    ]


async def test_missing_profile_dir_is_adapter_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_doubao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "adapter_not_configured"
    assert exc_info.value.non_retryable is True
    assert session.stages == []

    monkeypatch.delenv("GEO_DOUBAO_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_doubao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"
    assert exc_info_unset.value.non_retryable is True


async def test_screenshot_ref_and_answer_pass_dlp(adapter_env: Path) -> None:
    shot = adapter_env / "run-7-task-3-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="真实回答正文",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_doubao_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    # 真调用 DLP：两个字段都必须干净（persist 层同语义）
    assert_secret_free(result.screenshot_ref)
    assert_secret_free(result.answer_text)


def test_proxy_url_masking_never_leaks_credentials() -> None:
    assert mask_proxy_url("http://user:pass@proxy.example.com:8080") == (
        "http://proxy.example.com:8080"
    )
    assert mask_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert mask_proxy_url(None) is None
    assert mask_proxy_url("not-a-url") == "<invalid-proxy-url>"


def test_overlay_cleanup_handles_late_download_modal() -> None:
    """新行为（拟人化口径）：候选先 count/visible 粗筛，真实存在的遮罩才经
    human_click（贝塞尔移动+悬停+鼠标点击），不再对每个候选发裸 locator.click。"""
    page = _FakePage(visible_overlays={'button:has-text("我知道了")'})
    _try_close_overlays(page, random.Random(2))

    assert ("press", "Escape") in page.events
    overlay_clicks = [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(_OVERLAY_BB, e[1], e[2])
    ]
    assert len(overlay_clicks) == 1  # 可见遮罩被拟人化点击关闭
    # 不可见候选只探测不点击；全程无裸 locator.click
    assert not [e for e in page.events if e[0] == "locator_click"]
    probes = [e for e in page.events if e[0] == "locator"]
    assert ('button:has-text("下次提醒")',) in [(e[1],) for e in probes]


def test_source_screenshot_prefers_citation_summary_over_cookie_banner(
    adapter_env: Path,
) -> None:
    overlays: list[dict[str, Any]] = []

    class FakeSourcePage:
        def goto(self, *args: Any, **kwargs: Any) -> None:
            return None

        def wait_for_timeout(self, timeout: int) -> None:
            assert timeout == 1_500

        def evaluate(self, script: str, argument: Any) -> dict[str, str] | None:
            if isinstance(argument, str):
                return {"title": "友邦产品查询", "mentioned": "本网站使用 Cookie 改善体验"}
            overlays.append(argument)
            return None

        def screenshot(self, *, path: str, full_page: bool, timeout: int) -> None:
            assert full_page is False
            assert timeout == 15_000
            Path(path).write_bytes(b"\x89PNG-source")

        def close(self) -> None:
            return None

    class FakeContext:
        def new_page(self) -> FakeSourcePage:
            return FakeSourcePage()

    summary = "友邦友如意顺心佳是一款成人重疾险产品。"
    evidence, audit = _capture_source_screenshots(
        FakeContext(),
        [{"url": "https://www.aia.com.cn/product", "title": "产品页", "summary": summary}],
        evidence_dir=adapter_env,
        file_stem="source-proof",
        timeout_error=TimeoutError,
    )

    assert audit == {"requested": 1, "captured": 1, "failures": []}
    assert evidence[0].cited_text == summary
    assert overlays[0]["mention"] == summary
# ---------------------------------------------------------------------------
# fake browser 全事件序列测试（_PlaywrightDoubaoSession.collect 全程 mock 驱动，
# 记录 page 事件序列，验证拟人化接线 / 新会话纪律 / 优雅关闭）
# ---------------------------------------------------------------------------

_COMPOSER_BB = {"x": 80.0, "y": 600.0, "width": 600.0, "height": 48.0}
_SEND_BB = {"x": 640.0, "y": 610.0, "width": 32.0, "height": 32.0}
_NEW_CHAT_BB = {"x": 40.0, "y": 120.0, "width": 96.0, "height": 32.0}
_PICKER_BB = {"x": 100.0, "y": 560.0, "width": 60.0, "height": 28.0}
_OPTION_BB = {"x": 200.0, "y": 400.0, "width": 120.0, "height": 36.0}
_OVERLAY_BB = {"x": 300.0, "y": 200.0, "width": 90.0, "height": 32.0}
_DOWNLOAD_IMG_BB = {"x": 400.0, "y": 300.0, "width": 80.0, "height": 32.0}

_SSE_BODY = (
    "event: FULL_MSG_NOTIFY\n"
    'data: {"message": {"message_id": "m1", "conversation_id": "c1", '
    '"section_id": "s1", "user_type": 2}, "content": {"content_block": '
    '[{"block_id": "b1", "block_type": 10000, "parent_id": "", "content": '
    '{"text_block": {"text": "这是答案"}}}]}}\n'
    "\n"
    "data: [DONE]\n"
)


def _in_bb(bb: dict[str, float], x: float, y: float) -> bool:
    return bb["x"] <= x <= bb["x"] + bb["width"] and bb["y"] <= y <= bb["y"] + bb["height"]


class _FakeClock:
    """确定性假时钟：只随 page.wait_for_timeout 前进（测试即时完成）。"""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


class _FakeCDP:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.detached = 0

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "Network.getResponseBody":
            return {"body": _SSE_BODY, "base64Encoded": False}
        return {}

    def on(self, name: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self.handlers[name] = fn

    def detach(self) -> None:
        self.detached += 1

    def emit_completion(self) -> None:
        rid = "req-1"
        self.handlers["Network.requestWillBeSent"](
            {"requestId": rid, "request": {"url": "https://www.doubao.com/chat/completion"}}
        )
        self.handlers["Network.responseReceived"](
            {"requestId": rid, "response": {"mimeType": "text/event-stream"}}
        )
        self.handlers["Network.dataReceived"](
            {"requestId": rid, "dataLength": len(_SSE_BODY)}
        )
        self.handlers["Network.loadingFinished"]({"requestId": rid, "encodedDataLength": 1})


class _FakeMouse:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def move(self, x: float, y: float, **_kw: Any) -> None:
        self._page.events.append(("mouse_move", float(x), float(y)))

    def click(self, x: float, y: float, **_kw: Any) -> None:
        self._page.events.append(("mouse_click", float(x), float(y)))
        self._page.route_click(float(x), float(y))

    def wheel(self, dx: float, dy: float, **_kw: Any) -> None:
        self._page.events.append(("wheel", float(dx), float(dy)))

    def down(self, **_kw: Any) -> None:
        pass

    def up(self, **_kw: Any) -> None:
        pass


class _FakeKeyboard:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def type(self, text: str, **_kw: Any) -> None:
        self._page.events.append(("key", text))
        # 逐字输入进 composer（发送受理/吞没由 route_click 决定后续清空与否）
        self._page.composer_value += text

    def press(self, key: str, **_kw: Any) -> None:
        self._page.events.append(("press", key))


class _FakeLocator:
    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    @property
    def last(self) -> _FakeLocator:
        return self

    @property
    def page(self) -> _FakePage:
        return self._page

    def nth(self, _index: int) -> _FakeLocator:
        return self

    def filter(self, **_kw: Any) -> _FakeLocator:
        return self

    def all(self) -> list[_FakeLocator]:
        return []

    def _present(self) -> bool:
        return self._page.classify(self._selector)[1]

    def count(self) -> int:
        return 1 if self._present() else 0

    def is_visible(self, timeout: int | None = None) -> bool:
        return self._present()

    def wait_for(self, state: str | None = None, timeout: int | None = None) -> None:
        if not self._present():
            raise TimeoutError(f"not visible: {self._selector}")

    def bounding_box(self) -> dict[str, float] | None:
        return self._page.classify(self._selector)[2]

    def scroll_into_view_if_needed(self, timeout: int | None = None) -> None:
        if not self._present():
            raise TimeoutError(f"not visible: {self._selector}")
        self._page.events.append(("scroll", self._selector))

    def click(self, **kw: Any) -> None:
        self._page.events.append(("locator_click", self._selector, kw))

    def focus(self) -> None:
        self._page.events.append(("focus", self._selector))

    def evaluate(self, script: str, *_args: Any) -> Any:
        if self._selector in doubao_adapter._INPUT_SELECTORS:
            return self._page.composer_value
        return None

    def inner_text(self, timeout: int | None = None) -> str:
        return self._page.body_text


class _FakePage:
    """记录全事件序列的 page 替身。messages>0 模拟旧会话残留；route_click 让
    落在特定区域的鼠标点击产生真实副作用（发送受理 / 新对话切换 / 模式选中）。"""

    def __init__(
        self,
        *,
        messages: int = 0,
        composer_value: str = "",
        new_chat_button: bool = True,
        goto_clears: bool = False,
        deep_think: bool = False,
        visible_overlays: frozenset[str] | None = None,
        swallow_sends_from: int | None = None,
    ) -> None:
        self.clock = _FakeClock()
        self.events: list[tuple] = []
        self.mouse = _FakeMouse(self)
        self.keyboard = _FakeKeyboard(self)
        self.viewport_size = {"width": 1280, "height": 720}
        self.cdp = _FakeCDP(self)
        self.context: _FakeContext | None = None
        self.url = doubao_adapter._CHAT_URL
        self.messages = messages
        self.composer_value = composer_value
        self.new_chat_button = new_chat_button
        self.goto_clears = goto_clears
        self.deep_think = deep_think
        self.deep_think_engaged = False
        self.visible_overlays = visible_overlays or frozenset()
        self.body_text = ""
        # 发送吞没模拟（风控静默吞发送）：第 N 次（1-based）起 send 区点击不再
        # 清空 composer、不再触发 /chat/completion——驱动 wall_send 路径。
        self.swallow_sends_from = swallow_sends_from
        self.send_clicks = 0

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector == "body":
            return ("body", True, None)
        if selector in doubao_adapter._INPUT_SELECTORS:
            return ("composer", True, _COMPOSER_BB)
        if selector == '[data-proxyllm-send="true"]':
            return ("send", True, _SEND_BB)
        if self.new_chat_button and selector in doubao_adapter._NEW_CHAT_SELECTORS:
            return ("new_chat", True, _NEW_CHAT_BB)
        if self.deep_think and selector == 'button:has-text("快速")':
            return ("picker", True, _PICKER_BB)
        if self.deep_think and selector == "__option_expert__":
            return ("option", True, _OPTION_BB)
        if selector == 'button:has-text("下载图片")':
            return ("download", True, _DOWNLOAD_IMG_BB)
        if selector in self.visible_overlays:
            return ("overlay", True, _OVERLAY_BB)
        return ("none", False, None)

    def route_click(self, x: float, y: float) -> None:
        if _in_bb(_SEND_BB, x, y):
            self.send_clicks += 1
            if self.swallow_sends_from is not None and self.send_clicks >= (
                self.swallow_sends_from
            ):
                return  # 风控吞发送：composer 不清空、无 /chat/completion
            self.composer_value = ""  # 发送被受理：composer 清空
            self.messages = 2  # 一问一答出现在页面（下一题需点「新对话」）
            self.cdp.emit_completion()
        elif _in_bb(_NEW_CHAT_BB, x, y):
            self.messages = 0  # 「新对话」切到全新会话
        elif _in_bb(_OPTION_BB, x, y):
            self.deep_think_engaged = True

    def locator(self, selector: str) -> _FakeLocator:
        self.events.append(("locator", selector))
        return _FakeLocator(self, selector)

    def get_by_text(self, text: str, exact: bool = False) -> _FakeLocator:
        if self.deep_think and text == "专家" and exact:
            return _FakeLocator(self, "__option_expert__")
        return _FakeLocator(self, "__absent_text__")

    def get_by_role(self, role: str, **_kw: Any) -> _FakeLocator:
        return _FakeLocator(self, "__absent_role__")

    def evaluate(self, script: str, *_args: Any) -> Any:
        self.events.append(("evaluate", script))
        if script == doubao_adapter._TAG_JS:
            return True
        if script == doubao_adapter._CHAT_MESSAGE_COUNT_JS:
            return self.messages
        if script == doubao_adapter._PICKER_STATE_JS:
            return ["专家"] if self.deep_think_engaged else ["快速"]
        if script == doubao_adapter._FLATTEN_FOR_SCREENSHOT_JS:
            return {}
        return None

    def goto(self, url: str, **_kw: Any) -> None:
        self.events.append(("goto", url))
        if self.goto_clears:
            self.messages = 0  # 导航兜底成功：全新聊天页

    def wait_for_timeout(self, timeout: float) -> None:
        self.events.append(("wait", timeout))
        self.clock.advance_ms(timeout)

    def screenshot(self, *, path: str, **_kw: Any) -> None:
        Path(path).write_bytes(b"\x89PNG-fake")


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self._closed = False

    @property
    def pages(self) -> list[_FakePage]:
        return [self._page]

    def new_page(self) -> _FakePage:
        return self._page

    def new_cdp_session(self, page: _FakePage) -> _FakeCDP:
        return page.cdp

    def set_default_timeout(self, _ms: int) -> None:
        pass

    def close(self) -> None:
        # 与真实 patchright 一致：close 幂等（_closing_or_closed 守卫）——W8 起
        # 契约层 platform_browser 在适配器 close 后还有一次兜底 close，必须是 no-op。
        if self._closed:
            return
        self._closed = True
        assert self._page.events is not None
        self._page.events.append(("context_close",))


class _FakePWContextManager:
    def __init__(self, pw: Any) -> None:
        self._pw = pw

    def __enter__(self) -> Any:
        return self._pw

    def __exit__(self, *_exc: Any) -> bool:
        return False


def _install_fake_browser(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> None:
    """把 collect() 的浏览器驱动/时钟/崩溃清理/分享导出全部替换为 fake。"""
    # W8：防宿主环境残留 GEO_DOUBAO_CDP_URL 把 launch 路径测试带进 attach
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    context = _FakeContext(page)
    page.context = context
    chromium = SimpleNamespace(
        launch_persistent_context=lambda **kw: (
            page.events.append(("launch", str(kw.get("user_data_dir")))) or context
        )
    )
    pw = SimpleNamespace(chromium=chromium)

    def _sync_playwright() -> _FakePWContextManager:
        return _FakePWContextManager(pw)

    monkeypatch.setattr(
        doubao_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        doubao_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )
    real_clean = doubao_adapter._clean_profile_crash_state

    def _clean_spy(profile_dir: Path) -> bool:
        page.events.append(("clean",))
        return real_clean(profile_dir)

    monkeypatch.setattr(doubao_adapter, "_clean_profile_crash_state", _clean_spy)

    def _fake_share_image(pg: Any, _out_path: Path) -> dict[str, Any]:
        pg.mouse.click(300.0, 300.0)  # 经 facade：应变成贝塞尔移动+悬停+点击
        pg.locator('button:has-text("下载图片")').first.click(timeout=4_000)
        return {"ok": False, "error": "fake-skip"}

    def _fake_share_link(pg: Any) -> dict[str, Any]:
        pg.mouse.click(500.0, 500.0)  # 无业务区坐标（避开「新对话」等业务按钮）
        return {"ok": False, "error": "fake-skip"}

    monkeypatch.setattr(doubao_adapter, "capture_share_image", _fake_share_image)
    monkeypatch.setattr(doubao_adapter, "capture_share_link", _fake_share_link)


def _make_pace(page: _FakePage, rng: random.Random) -> Callable[[float, float], float]:
    def pace(lo: float, hi: float) -> float:
        return human_pause(rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000)))

    return pace


def _recording_shot(calls: list[str]) -> Callable[[str], None]:
    def shot(suffix: str) -> None:
        calls.append(suffix)

    return shot


async def test_session_collect_full_humanized_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全链路（normal mode）：逐字输入、发送前停顿、新会话验证、优雅关闭+崩溃清理。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    prefs_dir = tmp_path / "Default"
    prefs_dir.mkdir()
    (prefs_dir / "Preferences").write_text(
        json.dumps(
            {"profile": {"exit_type": "Crashed", "exited_cleanly": False}, "other_key": 1}
        ),
        encoding="utf-8",
    )
    page = _FakePage(messages=0)
    _install_fake_browser(monkeypatch, page)

    item = _item()
    result = await run_doubao_collection(
        item, session_factory=_PlaywrightDoubaoSession, heartbeat=lambda p: None
    )

    assert result.answer_text == "这是答案"
    assert result.quality_state == "live_valid"
    events = page.events

    # 1) 逐字输入：key 事件数 == 字符数，内容零污染
    keys = [e[1] for e in events if e[0] == "key"]
    assert keys == list(item.query)

    # 2) 顺序：点输入框（composer 区、发送区外）→ 逐字输入 → 发送（send 区鼠标点击）
    composer_clicks = [
        i
        for i, e in enumerate(events)
        if e[0] == "mouse_click"
        and _in_bb(_COMPOSER_BB, e[1], e[2])
        and not _in_bb(_SEND_BB, e[1], e[2])
    ]
    send_clicks = [
        i for i, e in enumerate(events) if e[0] == "mouse_click" and _in_bb(_SEND_BB, e[1], e[2])
    ]
    first_key = next(i for i, e in enumerate(events) if e[0] == "key")
    last_key = max(i for i, e in enumerate(events) if e[0] == "key")
    assert composer_clicks and send_clicks
    assert composer_clicks[0] < first_key < last_key < send_clicks[0]

    # 3) 发送前有 0.5-1.5s 通读停顿；页面就绪后有 0.6-1.8s 端详停顿
    pre_send_waits = [e[1] for e in events[last_key : send_clicks[0]] if e[0] == "wait"]
    assert any(500.0 <= w <= 1_500.0 for w in pre_send_waits)
    ready_waits = [e[1] for e in events[: composer_clicks[0]] if e[0] == "wait"]
    assert any(600.0 <= w <= 1_800.0 for w in ready_waits)

    # 4) 新会话验证被调用（composer 空探针 + 消息节点计数探针）
    assert ("evaluate", doubao_adapter._CHAT_MESSAGE_COUNT_JS) in events

    # 5) 全程无裸 locator.click（发送/弹层/分享全走鼠标事件链）
    assert not [e for e in events if e[0] == "locator_click"]

    # 6) 优雅关闭：启动前清理 → launch → context.close → close 后再清理
    assert events[0] == ("clean",)
    assert events[-1] == ("clean",)
    close_idx = events.index(("context_close",))
    launch_idx = events.index(("launch", str(tmp_path)))
    assert 0 < launch_idx < close_idx < len(events) - 1

    # 7) 崩溃标记被写回 Normal（其余键保留）
    prefs = json.loads((prefs_dir / "Preferences").read_text(encoding="utf-8"))
    assert prefs["profile"]["exit_type"] == "Normal"
    assert prefs["profile"]["exited_cleanly"] is True
    assert prefs["other_key"] == 1

    # 8) 分享导出走 facade（裸 mouse.click(300,300) 被贝塞尔化：移动样本 ≥5）
    assert len([e for e in events if e[0] == "mouse_move"]) >= 5


def test_deep_think_toggle_uses_human_pacing() -> None:
    """picker 悬停 300-900ms → 点击 → 候选 wait_for 水合等待 → 读菜单 400-1000ms → 点选项。"""
    page = _FakePage(deep_think=True)
    ok = _try_enable_deep_think(page, random.Random(3))

    assert ok is True
    assert page.deep_think_engaged is True
    events = page.events
    picker_clicks = [
        i for i, e in enumerate(events) if e[0] == "mouse_click" and _in_bb(_PICKER_BB, e[1], e[2])
    ]
    option_clicks = [
        i for i, e in enumerate(events) if e[0] == "mouse_click" and _in_bb(_OPTION_BB, e[1], e[2])
    ]
    assert len(picker_clicks) == 1
    assert len(option_clicks) == 1
    pc, oc = picker_clicks[0], option_clicks[0]
    # picker 点击前悬停 300-900ms，且有贝塞尔移动前奏
    hover_waits = [e[1] for e in events[:pc] if e[0] == "wait"]
    assert 300.0 <= hover_waits[-1] <= 900.0
    assert len([e for e in events[:pc] if e[0] == "mouse_move"]) >= 5
    # 弹层水合等待（候选 wait_for，不假时钟）之后、选项点击之前存在读菜单停顿 400-1000ms
    mid_waits = [(i, e[1]) for i, e in enumerate(events) if e[0] == "wait" and pc < i < oc]
    assert any(400.0 <= w <= 1_000.0 for _, w in mid_waits)


def test_fresh_chat_fast_path_when_already_fresh() -> None:
    page = _FakePage(messages=0)
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(doubao_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    # 已是新会话：不点「新对话」、不导航，但验证探针确实跑过
    assert not [e for e in page.events if e[0] == "mouse_click"]
    assert not [e for e in page.events if e[0] == "goto"]
    assert ("evaluate", doubao_adapter._CHAT_MESSAGE_COUNT_JS) in page.events


def test_fresh_chat_clicks_new_conversation_button() -> None:
    page = _FakePage(messages=2)  # 旧会话残留
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(doubao_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    assert page.messages == 0  # 点了「新对话」
    clicks = [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(_NEW_CHAT_BB, e[1], e[2])
    ]
    assert len(clicks) == 1
    assert not [e for e in page.events if e[0] == "goto"]  # 按钮优先，不动导航兜底


def test_fresh_chat_navigation_fallback_when_button_missing() -> None:
    page = _FakePage(messages=1, new_chat_button=False, goto_clears=True)
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(doubao_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    assert page.messages == 0
    assert ("goto", doubao_adapter._CHAT_URL) in page.events


def test_fresh_chat_honest_failure_when_stuck_in_old_conversation() -> None:
    page = _FakePage(messages=1, new_chat_button=False, goto_clears=False)
    rng = random.Random(6)
    shots: list[str] = []
    with pytest.raises(_IncompleteCapture, match="could-not-establish-fresh-chat"):
        _ensure_fresh_chat(
            page,
            page.locator(doubao_adapter._INPUT_SELECTORS[0]),
            rng,
            pace=_make_pace(page, rng),
            shot=_recording_shot(shots),
        )
    assert shots == ["fresh_chat"]  # 失败有存证截图，绝不静默沿用旧会话


def test_clean_profile_crash_state_rewrites_crash_markers(tmp_path: Path) -> None:
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text(
        json.dumps(
            {
                "profile": {"exit_type": "Session", "exited_cleanly": False, "other": 7},
                "unrelated": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    assert _clean_profile_crash_state(tmp_path) is True
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["profile"]["exit_type"] == "Normal"
    assert data["profile"]["exited_cleanly"] is True
    assert data["profile"]["other"] == 7  # 其余键原样保留
    assert data["unrelated"] == {"keep": True}
    assert _clean_profile_crash_state(tmp_path) is False  # 幂等：二次调用不改写


def test_clean_profile_crash_state_root_preferences_fallback(tmp_path: Path) -> None:
    prefs = tmp_path / "Preferences"  # 无 Default 子目录的布局
    prefs.write_text(json.dumps({"profile": {"exit_type": "Crashed"}}), encoding="utf-8")
    assert _clean_profile_crash_state(tmp_path) is True
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["profile"]["exit_type"] == "Normal"
    assert data["profile"]["exited_cleanly"] is True


def test_clean_profile_crash_state_missing_or_corrupt_is_noop(tmp_path: Path) -> None:
    assert _clean_profile_crash_state(tmp_path) is False  # 文件不存在
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text("{ not-json", encoding="utf-8")
    assert _clean_profile_crash_state(tmp_path) is False
    assert prefs.read_text(encoding="utf-8") == "{ not-json"  # 损坏文件绝不动


def test_share_facade_humanizes_mouse_and_locator_clicks() -> None:
    page = _FakePage()
    facade = _HumanizedPageFacade(page, random.Random(4), start=(10.0, 10.0))

    facade.mouse.click(300, 300)
    moves = [e for e in page.events if e[0] == "mouse_move"]
    assert len(moves) >= 5  # 瞬移被贝塞尔化
    assert moves[0][1:] == (10.0, 10.0)  # 轨迹从已知光标位置起
    assert moves[-1][1:] == (300.0, 300.0)
    clicks = [e for e in page.events if e[0] == "mouse_click"]
    assert clicks == [("mouse_click", 300.0, 300.0)]
    hover = [e[1] for e in page.events if e[0] == "wait"]
    assert 80.0 <= hover[-1] <= 300.0  # 点击前悬停
    assert facade.mouse.pos == (300.0, 300.0)  # 光标连续性

    page.events.clear()
    facade.locator('button:has-text("下载图片")').first.click(timeout=4_000)
    clicks = [e for e in page.events if e[0] == "mouse_click"]
    assert len(clicks) == 1 and _in_bb(_DOWNLOAD_IMG_BB, clicks[0][1], clicks[0][2])
    assert not [e for e in page.events if e[0] == "locator_click"]

    page.events.clear()
    facade.locator("unknown-selector").first.click(timeout=5)
    # 无布局元素回退原生 click，kwargs 原样透传（保持 legacy 调用语义）
    assert ("locator_click", "unknown-selector", {"timeout": 5}) in page.events

    # 透传面不受影响（evaluate / keyboard / viewport 等）
    assert facade.evaluate(doubao_adapter._TAG_JS) is True


# ---------------------------------------------------------------------------
# collect_batch：run 级会话复用（fake 浏览器全程记录；真实驱动绝不启动）
# ---------------------------------------------------------------------------


def _batch_specs(count: int) -> list[doubao_adapter.DoubaoBatchItemSpec]:
    return [
        doubao_adapter.DoubaoBatchItemSpec(
            business_key=f"run-1-task-{index}",
            query=f"第{index}题的重疾险有哪些",
            mode="normal",
            file_stem=f"run-1-task-{index}-a1",
        )
        for index in range(1, count + 1)
    ]


def _make_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> Any:
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    _install_fake_browser(monkeypatch, page)
    config = DoubaoAdapterConfig.from_env()
    return doubao_adapter._PlaywrightDoubaoSession(config, evidence, "batch-stem")


def test_collect_batch_shares_one_browser_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 题共享一次 launch：fresh_chat 探针逐题、阅读停顿逐题、证据逐题落盘、
    context.close 恰好一次（优雅关闭 + 崩溃清理首尾各一次）。"""
    page = _FakePage(messages=0)
    session = _make_session(tmp_path, monkeypatch, page)
    specs = _batch_specs(3)

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok", "ok", "ok"]
    assert [o.business_key for o in outcomes] == [s.business_key for s in specs]
    assert all(o.answer is not None and o.answer.answer_text == "这是答案" for o in outcomes)
    events = page.events

    # 1) 一次 launch、一次 context.close（同一个常驻会话完成整个 batch）
    assert len([e for e in events if e[0] == "launch"]) == 1
    assert len([e for e in events if e[0] == "context_close"]) == 1
    assert events[0] == ("clean",) and events[-1] == ("clean",)

    # 2) 题序保持：每题的逐字输入按顺序出现
    keys = [e[1] for e in events if e[0] == "key"]
    expected: list[str] = []
    for spec in specs:
        expected.extend(list(spec.query))
    assert keys == expected

    # 3) fresh_chat 消息计数探针每题都跑（>=3 次；第 2/3 题答案残留需点「新对话」）
    count_probes = [e for e in events if e == ("evaluate", doubao_adapter._CHAT_MESSAGE_COUNT_JS)]
    assert len(count_probes) >= 3
    new_chat_clicks = [
        e for e in events if e[0] == "mouse_click" and _in_bb(_NEW_CHAT_BB, e[1], e[2])
    ]
    assert len(new_chat_clicks) == 2  # 第 2、3 题各点一次「新对话」（第 1 题本就新会话）

    # 4) 阅读停顿逐题：wheel 滚动 2-5 次/题（共 6-15 次，delta 240-720 向下），
    #    每题一次 8-25s 停留
    wheels = [e for e in events if e[0] == "wheel"]
    assert 3 * 2 <= len(wheels) <= 3 * 5
    assert all(e[1] == 0.0 and 240.0 <= e[2] <= 720.0 for e in wheels)
    long_waits = [e[1] for e in events if e[0] == "wait" and 8_000.0 <= e[1] <= 25_000.0]
    assert len(long_waits) == 3

    # 5) 证据逐题落盘：整页截图 + SSE trace 每题各一份（per-item stem 区分）
    evidence = tmp_path / "evidence"
    for spec in specs:
        assert (evidence / f"{spec.file_stem}.png").is_file()
        assert (evidence / f"{spec.file_stem}-sse-trace.json").is_file()

    # 6) 每题 CDP capture 题末 detach（3 题 = 3 次）
    assert page.cdp.detached == 3


def test_collect_batch_wall_aborts_remaining_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 题发送被风控吞没（wall_send）：results=[ok, wall, aborted]，无 raise；
    aborted 题零浏览器交互；失败题有 per-item 存证截图。"""
    page = _FakePage(messages=0, swallow_sends_from=2)  # 第 2 次发送点击起吞没
    session = _make_session(tmp_path, monkeypatch, page)
    specs = _batch_specs(3)

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok", "wall", "aborted"]
    assert outcomes[0].answer is not None
    assert outcomes[1].error_type == "wall_send"
    assert outcomes[1].error_message and "send-not-accepted" in outcomes[1].error_message
    assert outcomes[1].evidence_path is not None
    assert outcomes[2].error_type == "aborted_after_failure"
    assert outcomes[2].error_message and specs[1].business_key in outcomes[2].error_message
    assert outcomes[2].answer is None and outcomes[2].evidence_path is None

    events = page.events
    # aborted 题零浏览器交互：键盘事件恰好只有第 1、2 题的字符（第 3 题未输入）
    keys = [e[1] for e in events if e[0] == "key"]
    assert keys == list(specs[0].query) + list(specs[1].query)
    # 发送点击：题1×1（受理）+ 题2 attempts=2 各点一次（均吞没）；第 3 题零点击
    assert page.send_clicks == 3
    # 失败存证用 per-item stem；第 3 题无任何证据文件
    evidence = tmp_path / "evidence"
    assert (evidence / f"{specs[1].file_stem}-send_wall.png").is_file()
    assert not list(evidence.glob(f"{specs[2].file_stem}*"))
    # 优雅关闭仍发生（撞墙后 finally close + 崩溃清理）
    assert len([e for e in events if e[0] == "context_close"]) == 1
    assert events[-1] == ("clean",)


async def test_run_doubao_batch_maps_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activity 层：fake session 注入（不启动浏览器），outcome→per-item 结果映射。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    shot = evidence / "run-1-task-1-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    wall_shot = evidence / "run-1-task-2-a1-send_wall.png"
    wall_shot.write_bytes(b"\x89PNG-fake")

    class _BatchFakeSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            on_stage(f"item:{items[0].business_key}")
            return [
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="真实回答", references=[], screenshot_path=shot
                    ),
                ),
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[1].business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send",
                    evidence_path=wall_shot,
                ),
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[2].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped",
                ),
            ]

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[
            CollectionTaskInput(
                business_key=f"run-7-task-{index}",
                query=f"查询{index}",
                model="doubao",
                region="Beijing",
                mode="normal",
                adapter="doubao",
            )
            for index in (3, 4, 5)
        ],
    )
    result = await doubao_adapter.run_doubao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _BatchFakeSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["ok", "wall", "aborted"]
    ok = result.results[0]
    assert ok.answer_text == "真实回答"
    assert ok.quality_state == "live_valid"
    assert ok.screenshot_ref == f"file://{shot}"
    wall = result.results[1]
    assert wall.error_type == "wall_captcha"
    assert wall.screenshot_ref == f"file://{wall_shot}"
    assert wall.answer_text is None
    aborted = result.results[2]
    assert aborted.error_type == "aborted_after_failure"
    assert aborted.screenshot_ref is None


async def test_run_doubao_batch_session_wall_marks_all_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级墙（导航后登录墙，一题未发）→ 全题 wall 结果，不 raise。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _WallSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            raise _WallError("wall_login_required", "doubao login wall detected", None)

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[_item(), _item()],
    )
    result = await doubao_adapter.run_doubao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _WallSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert all(r.error_type == "wall_login_required" for r in result.results)


async def test_run_doubao_batch_session_incomplete_raises_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级临时故障（浏览器启动失败，一题未发）→ raise 可重试错误。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _IncompleteSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            raise _IncompleteCapture("browser-launch-failed(patchright): boom")

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item()]
    )
    with pytest.raises(ApplicationError) as exc_info:
        await doubao_adapter.run_doubao_batch(
            batch,
            session_factory=lambda config, evidence_dir, stem: _IncompleteSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "answer_capture_incomplete"
    assert exc_info.value.non_retryable is False


async def test_run_doubao_batch_config_and_mode_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置类错误照常 raise：mode 门在浏览器启动之前；profile 缺失 fail-closed。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _NeverCalled:
        def collect_batch(self, items: Any, on_stage: Any) -> Any:
            raise AssertionError("session must not be started")

    factory = lambda config, evidence_dir, stem: _NeverCalled()  # noqa: E731
    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[_item(mode="vision")],
    )
    with pytest.raises(ApplicationError) as exc_info:
        await doubao_adapter.run_doubao_batch(
            batch, session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True

    monkeypatch.delenv("GEO_DOUBAO_PROFILE_DIR")
    ok_batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item()]
    )
    with pytest.raises(ApplicationError) as exc_info_unset:
        await doubao_adapter.run_doubao_batch(
            ok_batch, session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"


async def test_run_doubao_batch_empty_items_and_outcome_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 batch → 空结果（零浏览器交互）；outcome 数量不符 → fail-closed raise。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _EmptySession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            assert items == []
            return []

    empty = await doubao_adapter.run_doubao_batch(
        doubao_adapter.CollectionBatchInput(
            tenant_pub_id="tnt_test", run_pub_id="run_test", items=[]
        ),
        session_factory=lambda config, evidence_dir, stem: _EmptySession(),
        heartbeat=lambda p: None,
    )
    assert empty.results == []

    class _ShortSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            return []  # 契约违背：3 题 0 结果

    with pytest.raises(ApplicationError) as exc_info:
        await doubao_adapter.run_doubao_batch(
            doubao_adapter.CollectionBatchInput(
                tenant_pub_id="tnt_test",
                run_pub_id="run_test",
                items=[_item(), _item(), _item()],
            ),
            session_factory=lambda config, evidence_dir, stem: _ShortSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "batch_outcome_contract_violation"


async def test_run_doubao_batch_default_session_runs_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产约定（不传 session_factory）必须走 to_thread——sync 浏览器不进事件循环。

    回归（2026-08-06 batch 首航生产事故）：collect_doubao_batch 曾显式传
    _PlaywrightDoubaoSession，被误判为注入 fake，在事件循环里直跑 sync
    patchright（"Playwright Sync API inside the asyncio loop"）。
    """
    import threading

    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))
    seen: dict[str, bool] = {}

    class _ThreadProbeSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            seen["on_main_thread"] = threading.current_thread() is threading.main_thread()
            return [
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="probe only",
                )
            ]

    monkeypatch.setattr(
        doubao_adapter,
        "_PlaywrightDoubaoSession",
        lambda config, evidence_dir, stem: _ThreadProbeSession(),
    )
    result = await doubao_adapter.run_doubao_batch(
        doubao_adapter.CollectionBatchInput(
            tenant_pub_id="tnt_test",
            run_pub_id="run_test",
            items=[_item()],
        ),
        heartbeat=lambda p: None,
    )
    assert seen["on_main_thread"] is False
    assert result.results[0].error_type == "aborted_after_failure"


async def test_run_doubao_batch_marks_captcha_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """captcha-assist-v1：撞码题（error_type=wall_captcha）→ 结果仍等长全占位，
    并标注 captcha_pause(resume_index=撞码题下标) 供 workflow 挂起续跑。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(evidence))
    wall_shot = evidence / "wall.png"
    wall_shot.write_bytes(b"\x89PNG-fake")

    class _CaptchaSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            return [
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="真实回答", references=[], screenshot_path=wall_shot
                    ),
                ),
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[1].business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send",
                    evidence_path=wall_shot,
                ),
                doubao_adapter.DoubaoBatchItemOutcome(
                    business_key=items[2].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped",
                ),
            ]

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[_item(), _item(), _item()],
    )
    result = await doubao_adapter.run_doubao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _CaptchaSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["ok", "wall", "aborted"]
    assert result.captcha_pause is not None
    assert result.captcha_pause.resume_index == 1
    assert result.captcha_pause.business_key == batch.items[1].business_key
    assert result.captcha_pause.wall_type == "wall_captcha"
    assert result.captcha_pause.evidence_ref == f"file://{wall_shot}"


async def test_run_doubao_batch_session_level_captcha_marks_pause_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级撞码（batch 开场即撞，一题未发）→ pause resume_index=0。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _SessionCaptchaSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            raise _WallError("wall_captcha", "captcha widget visible before input", None)

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item(), _item()]
    )
    result = await doubao_adapter.run_doubao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _SessionCaptchaSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert result.captcha_pause is not None
    assert result.captcha_pause.resume_index == 0


async def test_run_doubao_batch_non_captcha_wall_has_no_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录墙等非撞码失败维持现行语义：不标注 pause（人工接管只管验证码）。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _LoginWallSession:
        def collect_batch(
            self,
            items: list[doubao_adapter.DoubaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[doubao_adapter.DoubaoBatchItemOutcome]:
            raise _WallError("wall_login_required", "doubao login wall detected", None)

    batch = doubao_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item()]
    )
    result = await doubao_adapter.run_doubao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _LoginWallSession(),
        heartbeat=lambda p: None,
    )
    assert result.results[0].error_type == "wall_login_required"
    assert result.captcha_pause is None
