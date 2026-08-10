"""deepseek 采集适配器 batch（collect_deepseek_batch / run 级会话复用）与拟人化
接线单元测试：fake 浏览器全程记录事件序列，真实驱动绝不启动。

测试矩阵与豆包 batch 测试（test_doubao_adapter.py）逐点对齐：
- 全链路拟人化（逐字输入 / 发送前停顿 / 新会话验证 / 优雅关闭+崩溃清理）；
- fresh_chat 纪律（fast path / 点「新对话」/ 导航兜底 / 诚实失败）；
- collect_batch：一次 launch 多题共享 / 题序 / fresh_chat 每题 / read_pause 每题
  （含最后一题）/ 证据逐题 / CDP capture 每题 detach；
- wall → 后续题 aborted 零浏览器交互；
- activity 层：outcome 映射 / session 级墙全题 wall / session 级 incomplete 可重试 /
  mode 门（normal/deep_think 放行透传，未知 mode→unsupported_mode）/ 配置门 /
  空 batch / 契约违背 / 默认 session 路径必须 to_thread（thread-probe 回归测试）；
- 模式确保（20260810 起，两种 mode 都显式确保）：normal=快速模式 tab+智能搜索
  开+深度思考关；deep_think=快速+搜索开+思考开（已到位零点击、点击先于打字）；
  无法确认 → mode_toggle_failed non_retryable（题级 wall + 后续题 aborted；
  session 级防御全题 wall）。

与豆包 fake 的关键差异（平台机制差异，非语义差异）：DeepSeek 发送主路径是
Enter 键盘提交（2026-07-27 live 校准），发送按钮点击是兜底——fake 的
keyboard.press("Enter") 与发送按钮鼠标点击共用同一 ``route_send`` 副作用。
"""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities import deepseek_adapter
from workflows.activities.collection import CollectionBatchInput, CollectionTaskInput
from workflows.activities.deepseek_adapter import (
    CollectedAnswer,
    DeepseekAdapterConfig,
    _ensure_fresh_chat,
    _IncompleteCapture,
    _PlaywrightDeepseekSession,
    _WallError,
    run_deepseek_batch,
    run_deepseek_collection,
)
from workflows.activities.human_like import human_pause


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-5",
        query="你好，请用一句话介绍你自己",
        model="deepseek",
        region="CN-TJ",
        mode=mode,
        adapter="deepseek",
    )


# ---------------------------------------------------------------------------
# fake browser 全事件序列 harness（_PlaywrightDeepseekSession 全程 mock 驱动）
# ---------------------------------------------------------------------------

_COMPOSER_BB = {"x": 80.0, "y": 600.0, "width": 600.0, "height": 48.0}
_SEND_BB = {"x": 640.0, "y": 610.0, "width": 32.0, "height": 32.0}
_NEW_CHAT_BB = {"x": 40.0, "y": 120.0, "width": 96.0, "height": 32.0}
_OVERLAY_BB = {"x": 300.0, "y": 200.0, "width": 90.0, "height": 32.0}
# deep_think 开关（composer 下方 chips / 上方模式 tab 条；与真实布局同相对位置）
_CHIP_BB = {
    "深度思考": {"x": 80.0, "y": 660.0, "width": 92.0, "height": 32.0},
    "智能搜索": {"x": 180.0, "y": 660.0, "width": 92.0, "height": 32.0},
}
_FAST_TAB_BB = {"x": 320.0, "y": 400.0, "width": 96.0, "height": 32.0}

# DeepSeek SSE JSON-patch 增量流（2026-07-27 live 校准 schema）：初始快照
# fragments[type=RESPONSE].content + 裸增量 {"v":"..."} + [DONE]。
_SSE_BODY = (
    'data: {"v":{"response":{"message_id":2,"parent_id":1,"role":"ASSISTANT",'
    '"thinking_enabled":false,"status":"WIP","fragments":'
    '[{"id":2,"type":"RESPONSE","content":"这是答案","references":[],"stage_id":1}]}}}\n\n'
    'data: {"v":"。"}\n\n'
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
            {
                "requestId": rid,
                "request": {"url": "https://chat.deepseek.com/api/v0/chat/completion"},
            }
        )
        self.handlers["Network.responseReceived"](
            {"requestId": rid, "response": {"mimeType": "text/event-stream"}}
        )
        self.handlers["Network.dataReceived"]({"requestId": rid, "dataLength": len(_SSE_BODY)})
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
        # 逐字输入进 composer（发送受理/吞没由 route_send 决定后续清空与否）
        self._page.composer_value += text

    def press(self, key: str, **_kw: Any) -> None:
        self._page.events.append(("press", key))
        if key == "Enter":
            # DeepSeek 回车即发送（live 校准主路径）；受理/吞没由 route_send 决定。
            self._page.route_send()


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
        if self._selector in deepseek_adapter._INPUT_SELECTORS:
            return self._page.composer_value
        return None

    def inner_text(self, timeout: int | None = None) -> str:
        return self._page.body_text

    def get_attribute(self, name: str, timeout: int | None = None) -> str | None:
        """chip 状态位探针（aria-pressed）；非 chip 选择器/未配状态 → None。"""
        if name != "aria-pressed":
            return None
        for chip, sel in (
            ("深度思考", 'div.ds-toggle-button:has-text("深度思考")'),
            ("智能搜索", 'div.ds-toggle-button:has-text("智能搜索")'),
        ):
            if self._selector == sel and chip in self._page.chips:
                return "true" if self._page.chips[chip] else "false"
        return None


class _FakePage:
    """记录全事件序列的 page 替身。messages>0 模拟旧会话残留；route_send 让
    Enter/发送按钮产生真实副作用（发送受理 / 风控吞没），route_click 让落在
    「新对话」区域的点击切到全新会话。"""

    def __init__(
        self,
        *,
        messages: int = 0,
        composer_value: str = "",
        new_chat_button: bool = True,
        goto_clears: bool = False,
        visible_overlays: frozenset[str] | None = None,
        swallow_sends_from: int | None = None,
        chips: dict[str, bool] | None = None,
        tab_selected: str = "快速模式",
        tab_found: bool = True,
    ) -> None:
        self.clock = _FakeClock()
        self.events: list[tuple] = []
        self.mouse = _FakeMouse(self)
        self.keyboard = _FakeKeyboard(self)
        self.viewport_size = {"width": 1280, "height": 720}
        self.cdp = _FakeCDP(self)
        self.context: _FakeContext | None = None
        self.url = deepseek_adapter._CHAT_URL
        self.messages = messages
        self.composer_value = composer_value
        self.new_chat_button = new_chat_button
        self.goto_clears = goto_clears
        self.visible_overlays = visible_overlays or frozenset()
        self.body_text = ""
        # 发送吞没模拟（风控静默吞发送）：第 N 次（1-based）起发送不再清空
        # composer、不再触发 completion 流——驱动 wall_send 路径。
        self.swallow_sends_from = swallow_sends_from
        self.send_attempts = 0
        # deep_think 开关态：chips = aria-pressed 语义；tab_* = 模式 tab 条探针结果。
        # 缺省全开/快速（幂等路径零点击）；测试用 False/缺 key 驱动点击与失败路径。
        self.chips: dict[str, bool] = (
            dict(chips) if chips is not None else {"深度思考": True, "智能搜索": True}
        )
        self.tab_selected = tab_selected
        self.tab_found = tab_found

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector == "body":
            return ("body", True, None)
        if selector in deepseek_adapter._INPUT_SELECTORS:
            return ("composer", True, _COMPOSER_BB)
        if selector == '[data-geo-send="true"]':
            return ("send", True, _SEND_BB)
        if self.new_chat_button and selector in deepseek_adapter._NEW_CHAT_SELECTORS:
            return ("new_chat", True, _NEW_CHAT_BB)
        if selector in self.visible_overlays:
            return ("overlay", True, _OVERLAY_BB)
        for chip, bb in _CHIP_BB.items():
            if selector == f'div.ds-toggle-button:has-text("{chip}")':
                return ("chip", chip in self.chips, bb)
        if selector == 'span:text-is("快速模式")':
            return ("fast_tab", self.tab_found, _FAST_TAB_BB)
        return ("none", False, None)

    def route_send(self) -> None:
        """发送副作用（Enter 主路径与发送按钮兜底共用）。"""
        self.send_attempts += 1
        if self.swallow_sends_from is not None and self.send_attempts >= (
            self.swallow_sends_from
        ):
            return  # 风控吞发送：composer 不清空、无 completion 流
        self.composer_value = ""  # 发送被受理：composer 清空
        self.messages = 2  # 一问一答出现在页面（下一题需点「新对话」）
        self.cdp.emit_completion()

    def route_click(self, x: float, y: float) -> None:
        if _in_bb(_SEND_BB, x, y):
            self.route_send()
        elif _in_bb(_NEW_CHAT_BB, x, y):
            self.messages = 0  # 「新对话」切到全新会话
        for chip, bb in _CHIP_BB.items():
            if chip in self.chips and _in_bb(bb, x, y):
                self.chips[chip] = not self.chips[chip]  # toggle 语义
                return
        if self.tab_found and _in_bb(_FAST_TAB_BB, x, y):
            self.tab_selected = "快速模式"

    def locator(self, selector: str) -> _FakeLocator:
        self.events.append(("locator", selector))
        return _FakeLocator(self, selector)

    def evaluate(self, script: str, *_args: Any) -> Any:
        self.events.append(("evaluate", script))
        if script == deepseek_adapter._TAG_JS:
            return True
        if script == deepseek_adapter._CHAT_MESSAGE_COUNT_JS:
            return self.messages
        if script == deepseek_adapter._FLATTEN_FOR_SCREENSHOT_JS:
            return {}
        if script == deepseek_adapter._TAB_STATE_JS:
            if not self.tab_found:
                return {"found": False}
            return {"found": True, "selected": self.tab_selected}
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
    """把 session 的浏览器驱动/时钟/崩溃清理全部替换为 fake（launch 路径：
    GEO_DEEPSEEK_CDP_URL 必须缺省，attach 分支由 test_resident_browser.py 覆盖）。"""
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
        deepseek_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        deepseek_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )
    real_clean = deepseek_adapter._clean_profile_crash_state

    def _clean_spy(profile_dir: Path) -> bool:
        page.events.append(("clean",))
        return real_clean(profile_dir)

    monkeypatch.setattr(deepseek_adapter, "_clean_profile_crash_state", _clean_spy)


def _adapter_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: _FakePage | None = None
) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DEEPSEEK_HEADLESS", "1")
    monkeypatch.delenv("GEO_DEEPSEEK_CDP_URL", raising=False)  # 强制 launch 路径
    if page is not None:
        _install_fake_browser(monkeypatch, page)
    return evidence


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
    """全链路（per-task 单题）：逐字输入、发送前停顿、新会话验证、Enter 主路径
    发送、优雅关闭+崩溃清理。"""
    evidence = _adapter_env(tmp_path, monkeypatch)
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
    result = await run_deepseek_collection(
        item, session_factory=_PlaywrightDeepseekSession, heartbeat=lambda p: None
    )

    assert result.answer_text == "这是答案。"
    assert result.quality_state == "live_valid"
    assert result.screenshot_ref.startswith("file://")
    events = page.events

    # 1) 逐字输入：key 事件数 == 字符数，内容零污染（绝不 insert_text）
    keys = [e[1] for e in events if e[0] == "key"]
    assert keys == list(item.query)

    # 2) 顺序：点输入框（composer 区、发送区外）→ 逐字输入 → Enter 发送
    composer_clicks = [
        i
        for i, e in enumerate(events)
        if e[0] == "mouse_click"
        and _in_bb(_COMPOSER_BB, e[1], e[2])
        and not _in_bb(_SEND_BB, e[1], e[2])
    ]
    enter_presses = [i for i, e in enumerate(events) if e == ("press", "Enter")]
    first_key = next(i for i, e in enumerate(events) if e[0] == "key")
    last_key = max(i for i, e in enumerate(events) if e[0] == "key")
    assert composer_clicks and enter_presses
    assert composer_clicks[0] < first_key < last_key < enter_presses[0]
    # Enter 主路径一次受理：全程不碰发送按钮（发送按钮只是兜底路径）
    assert not [
        e for e in events if e[0] == "mouse_click" and _in_bb(_SEND_BB, e[1], e[2])
    ]

    # 3) 发送前有 0.5-1.5s 通读停顿；页面就绪后有 0.6-1.8s 端详停顿
    pre_send_waits = [e[1] for e in events[last_key : enter_presses[0]] if e[0] == "wait"]
    assert any(500.0 <= w <= 1_500.0 for w in pre_send_waits)
    ready_waits = [e[1] for e in events[: composer_clicks[0]] if e[0] == "wait"]
    assert any(600.0 <= w <= 1_800.0 for w in ready_waits)

    # 4) 新会话验证被调用（composer 空探针 + 消息节点计数探针）
    assert ("evaluate", deepseek_adapter._CHAT_MESSAGE_COUNT_JS) in events

    # 5) 全程无裸 locator.click（聚焦/发送/弹层全走鼠标事件链或真实键盘）
    assert not [e for e in events if e[0] == "locator_click"]

    # 6) 优雅关闭（launch 路径）：启动前清理 → launch → context.close → close 后再清理
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
    assert (evidence / "run-9-task-5-a1.png").is_file()


def test_fresh_chat_fast_path_when_already_fresh() -> None:
    page = _FakePage(messages=0)
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(deepseek_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    # 已是新会话：不点「新对话」、不导航，但验证探针确实跑过
    assert not [e for e in page.events if e[0] == "mouse_click"]
    assert not [e for e in page.events if e[0] == "goto"]
    assert ("evaluate", deepseek_adapter._CHAT_MESSAGE_COUNT_JS) in page.events


def test_fresh_chat_clicks_new_conversation_button() -> None:
    page = _FakePage(messages=2)  # 旧会话残留
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(deepseek_adapter._INPUT_SELECTORS[0]),
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
        page.locator(deepseek_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    assert page.messages == 0
    assert ("goto", deepseek_adapter._CHAT_URL) in page.events


def test_fresh_chat_honest_failure_when_stuck_in_old_conversation() -> None:
    page = _FakePage(messages=1, new_chat_button=False, goto_clears=False)
    rng = random.Random(6)
    shots: list[str] = []
    with pytest.raises(_IncompleteCapture, match="could-not-establish-fresh-chat"):
        _ensure_fresh_chat(
            page,
            page.locator(deepseek_adapter._INPUT_SELECTORS[0]),
            rng,
            pace=_make_pace(page, rng),
            shot=_recording_shot(shots),
        )
    assert shots == ["fresh_chat"]  # 失败有存证截图，绝不静默沿用旧会话


# ---------------------------------------------------------------------------
# collect_batch：run 级会话复用（fake 浏览器全程记录；真实驱动绝不启动）
# ---------------------------------------------------------------------------


def _batch_specs(count: int) -> list[deepseek_adapter.DeepseekBatchItemSpec]:
    return [
        deepseek_adapter.DeepseekBatchItemSpec(
            business_key=f"run-1-task-{index}",
            query=f"第{index}题的重疾险有哪些",
            mode="normal",
            file_stem=f"run-1-task-{index}-a1",
        )
        for index in range(1, count + 1)
    ]


def _make_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> Any:
    evidence = _adapter_env(tmp_path, monkeypatch, page)
    config = DeepseekAdapterConfig.from_env()
    return deepseek_adapter._PlaywrightDeepseekSession(config, evidence, "batch-stem")


def test_collect_batch_shares_one_browser_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 题共享一次浏览器会话：fresh_chat 探针逐题、阅读停顿逐题（含最后一题）、
    证据逐题落盘、context.close 恰好一次（崩溃清理首尾各一次）。"""
    page = _FakePage(messages=0)
    session = _make_session(tmp_path, monkeypatch, page)
    specs = _batch_specs(3)

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok", "ok", "ok"]
    assert [o.business_key for o in outcomes] == [s.business_key for s in specs]
    assert all(o.answer is not None and o.answer.answer_text == "这是答案。" for o in outcomes)
    events = page.events

    # 1) 一次 launch、一次 context.close（同一个会话完成整个 batch，绝不每题冷启）
    assert len([e for e in events if e[0] == "launch"]) == 1
    assert len([e for e in events if e[0] == "context_close"]) == 1
    assert events[0] == ("clean",) and events[-1] == ("clean",)

    # 2) 题序保持：每题的逐字输入按顺序出现；Enter 主路径每题各一次
    keys = [e[1] for e in events if e[0] == "key"]
    expected: list[str] = []
    for spec in specs:
        expected.extend(list(spec.query))
    assert keys == expected
    assert len([e for e in events if e == ("press", "Enter")]) == 3

    # 3) fresh_chat 消息计数探针每题都跑（>=3 次；第 2/3 题答案残留需点「新对话」）
    count_probes = [
        e for e in events if e == ("evaluate", deepseek_adapter._CHAT_MESSAGE_COUNT_JS)
    ]
    assert len(count_probes) >= 3
    new_chat_clicks = [
        e for e in events if e[0] == "mouse_click" and _in_bb(_NEW_CHAT_BB, e[1], e[2])
    ]
    assert len(new_chat_clicks) == 2  # 第 2、3 题各点一次「新对话」（第 1 题本就新会话）

    # 4) 阅读停顿逐题（含最后一题）：wheel 滚动 2-5 次/题（共 6-15 次，向下
    #    240-720px），每题一次 8-25s 停留
    wheels = [e for e in events if e[0] == "wheel"]
    assert 3 * 2 <= len(wheels) <= 3 * 5
    assert all(e[1] == 0.0 and 240.0 <= e[2] <= 720.0 for e in wheels)
    long_waits = [e[1] for e in events if e[0] == "wait" and 8_000.0 <= e[1] <= 25_000.0]
    assert len(long_waits) == 3

    # 5) 证据逐题落盘（per-item stem 区分）
    evidence = tmp_path / "evidence"
    for spec in specs:
        assert (evidence / f"{spec.file_stem}.png").is_file()

    # 6) 每题 CDP capture 题末 detach（3 题 = 3 次）
    assert page.cdp.detached == 3


def test_collect_batch_wall_aborts_remaining_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 题发送被风控吞没（wall_send）：results=[ok, wall, aborted]，无 raise；
    aborted 题零浏览器交互；失败题有 per-item 存证截图。"""
    page = _FakePage(messages=0, swallow_sends_from=2)  # 第 2 次发送起吞没
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
    # 发送：题1 Enter×1（受理）+ 题2 attempts=2×(Enter+按钮兜底)（均吞没）；第 3 题零发送
    assert page.send_attempts == 5
    # 失败存证用 per-item stem；第 3 题无任何证据文件
    evidence = tmp_path / "evidence"
    assert (evidence / f"{specs[1].file_stem}-send_wall.png").is_file()
    assert not list(evidence.glob(f"{specs[2].file_stem}*"))
    # 优雅关闭仍发生（撞墙后契约层 close + 崩溃清理）
    assert len([e for e in events if e[0] == "context_close"]) == 1
    assert events[-1] == ("clean",)


# ---------------------------------------------------------------------------
# run_deepseek_batch（activity 层）：fake session 注入，不启动浏览器
# ---------------------------------------------------------------------------


def _batch(items: list[CollectionTaskInput]) -> CollectionBatchInput:
    return CollectionBatchInput(tenant_pub_id="tnt_test", run_pub_id="run_test", items=items)


async def test_run_deepseek_batch_maps_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activity 层：fake session 注入（不启动浏览器），outcome→per-item 结果映射。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    shot = evidence / "run-1-task-1-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    wall_shot = evidence / "run-1-task-2-a1-send_wall.png"
    wall_shot.write_bytes(b"\x89PNG-fake")

    class _BatchFakeSession:
        def collect_batch(
            self,
            items: list[deepseek_adapter.DeepseekBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[deepseek_adapter.DeepseekBatchItemOutcome]:
            on_stage(f"item:{items[0].business_key}")
            return [
                deepseek_adapter.DeepseekBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="真实回答", references=[], screenshot_path=shot
                    ),
                ),
                deepseek_adapter.DeepseekBatchItemOutcome(
                    business_key=items[1].business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send",
                    evidence_path=wall_shot,
                ),
                deepseek_adapter.DeepseekBatchItemOutcome(
                    business_key=items[2].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped",
                ),
            ]

    batch = _batch(
        [
            CollectionTaskInput(
                business_key=f"run-7-task-{index}",
                query=f"查询{index}",
                model="deepseek",
                region="CN-TJ",
                mode="normal",
                adapter="deepseek",
            )
            for index in (3, 4, 5)
        ]
    )
    result = await run_deepseek_batch(
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


async def test_run_deepseek_batch_session_wall_marks_all_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级墙（导航后登录墙，一题未发）→ 全题 wall 结果，不 raise。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _WallSession:
        def collect_batch(
            self,
            items: list[deepseek_adapter.DeepseekBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[deepseek_adapter.DeepseekBatchItemOutcome]:
            raise _WallError(
                "wall_login_required",
                "deepseek login wall detected right after navigation (redirect to /sign_in)",
                None,
            )

    result = await run_deepseek_batch(
        _batch([_item(), _item()]),
        session_factory=lambda config, evidence_dir, stem: _WallSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert all(r.error_type == "wall_login_required" for r in result.results)


async def test_run_deepseek_batch_session_incomplete_raises_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级临时故障（浏览器启动失败，一题未发）→ raise 可重试错误。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _IncompleteSession:
        def collect_batch(
            self,
            items: list[deepseek_adapter.DeepseekBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[deepseek_adapter.DeepseekBatchItemOutcome]:
            raise _IncompleteCapture("browser-launch-failed(patchright): boom")

    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_batch(
            _batch([_item()]),
            session_factory=lambda config, evidence_dir, stem: _IncompleteSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "answer_capture_incomplete"
    assert exc_info.value.non_retryable is False


async def test_run_deepseek_batch_config_and_mode_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置类错误照常 raise：mode 门（normal/deep_think 之外的未知 mode →
    unsupported_mode）在浏览器启动之前；profile 缺失 fail-closed。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _NeverCalled:
        def collect_batch(self, items: Any, on_stage: Any) -> Any:
            raise AssertionError("session must not be started")

    factory = lambda config, evidence_dir, stem: _NeverCalled()  # noqa: E731
    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_batch(
            _batch([_item(mode="vision")]),
            session_factory=factory,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True

    monkeypatch.delenv("GEO_DEEPSEEK_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_deepseek_batch(
            _batch([_item()]), session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"


async def test_run_deepseek_batch_empty_items_and_outcome_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 batch → 空结果（零浏览器交互，session 都不建）；outcome 数量不符 →
    fail-closed raise。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _NeverCalled:
        def collect_batch(self, items: Any, on_stage: Any) -> Any:
            raise AssertionError("empty batch must not start a browser session")

    empty = await run_deepseek_batch(
        _batch([]),
        session_factory=lambda config, evidence_dir, stem: _NeverCalled(),
        heartbeat=lambda p: None,
    )
    assert empty.results == []

    class _ShortSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            return []  # 契约违背：3 题 0 结果

    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_batch(
            _batch([_item(), _item(), _item()]),
            session_factory=lambda config, evidence_dir, stem: _ShortSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "batch_outcome_contract_violation"
    assert exc_info.value.non_retryable is True


async def test_run_deepseek_batch_default_session_runs_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产约定（不传 session_factory）必须走 to_thread——sync 浏览器不进事件循环。

    回归（豆包 2026-08-06 batch 首航生产事故同款）：activity 显式传真实
    session 类会被误判为注入 fake，在事件循环里直跑 sync patchright
    （"Playwright Sync API inside the asyncio loop"）。
    """
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    seen: dict[str, bool] = {}

    class _ThreadProbeSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            seen["on_main_thread"] = threading.current_thread() is threading.main_thread()
            return [
                deepseek_adapter.DeepseekBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="probe only",
                )
            ]

    monkeypatch.setattr(
        deepseek_adapter,
        "_PlaywrightDeepseekSession",
        lambda config, evidence_dir, stem: _ThreadProbeSession(),
    )
    result = await run_deepseek_batch(
        _batch([_item()]),
        heartbeat=lambda p: None,
    )
    assert seen["on_main_thread"] is False
    assert result.results[0].error_type == "aborted_after_failure"


# ---------------------------------------------------------------------------
# deep_think（20260810 起）：快速模式 tab + 深度思考/智能搜索 chips 确保与失败语义
# ---------------------------------------------------------------------------


async def test_collect_one_deep_think_enables_toggles_before_typing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deep_think 题（per-task 真 session + fake 浏览器）：发送前完成 快速模式
    tab + 深度思考/智能搜索 chips 全开；点击全部落在打字之前；幂等——已开的
    开关零重复点击。"""
    _adapter_env(tmp_path, monkeypatch)
    page = _FakePage(
        messages=0,
        chips={"深度思考": False, "智能搜索": True},  # 只差点深度思考
        tab_selected="专家模式",
    )
    _install_fake_browser(monkeypatch, page)

    result = await run_deepseek_collection(
        _item(mode="deep_think"),
        session_factory=_PlaywrightDeepseekSession,
        heartbeat=lambda p: None,
    )

    assert result.quality_state == "live_valid"
    assert page.chips == {"深度思考": True, "智能搜索": True}
    assert page.tab_selected == "快速模式"
    first_key = next(i for i, e in enumerate(page.events) if e[0] == "key")
    toggle_clicks = [
        i
        for i, e in enumerate(page.events)
        if e[0] == "mouse_click"
        and (
            _in_bb(_CHIP_BB["深度思考"], e[1], e[2])
            or _in_bb(_FAST_TAB_BB, e[1], e[2])
        )
    ]
    # tab + 深度思考各点一次（智能搜索已开零点击），且全部先于打字
    assert len(toggle_clicks) == 2
    assert max(toggle_clicks) < first_key
    assert not [
        e
        for e in page.events
        if e[0] == "mouse_click" and _in_bb(_CHIP_BB["智能搜索"], e[1], e[2])
    ]


async def test_collect_one_deep_think_toggle_failure_is_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chip 缺失（选择器漂移）→ mode_toggle_failed non_retryable，
    绝不带 normal 答案蒙混（一个字都不打）。"""
    _adapter_env(tmp_path, monkeypatch)
    page = _FakePage(messages=0, chips={"深度思考": False})  # 智能搜索 chip 缺失
    _install_fake_browser(monkeypatch, page)

    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            _item(mode="deep_think"),
            session_factory=_PlaywrightDeepseekSession,
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "mode_toggle_failed"
    assert exc_info.value.non_retryable is True
    assert not [e for e in page.events if e[0] == "key"]  # 零输入


async def test_batch_deep_think_toggle_failure_walls_and_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch 题级 toggle 失败 → 本题 wall(mode_toggle_failed) + 后续题
    aborted（零浏览器交互）。"""
    page = _FakePage(messages=0, chips={"深度思考": False})  # 智能搜索 chip 缺失
    session = _make_session(tmp_path, monkeypatch, page)
    specs = [
        deepseek_adapter.DeepseekBatchItemSpec(
            business_key=f"run-2-task-{index}",
            query=f"第{index}题",
            mode="deep_think",
            file_stem=f"run-2-task-{index}-a1",
        )
        for index in (1, 2)
    ]

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["wall", "aborted"]
    assert outcomes[0].error_type == "mode_toggle_failed"
    assert outcomes[1].error_type == "aborted_after_failure"
    assert "mode_toggle_failed" in (outcomes[1].error_message or "")
    assert not [e for e in page.events if e[0] == "key"]  # 两题都零输入


async def test_run_deepseek_batch_deep_think_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode 门放行 deep_think：spec.mode 原样透传到 session 层。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    seen: list[str] = []
    shot = tmp_path / "evidence" / "run-9-task-5-a1.png"
    shot.parent.mkdir(exist_ok=True)
    shot.write_bytes(b"\x89PNG-fake")

    class _RecSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            seen.extend(spec.mode for spec in items)
            return [
                deepseek_adapter.DeepseekBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="深度思考后的回答", references=[], screenshot_path=shot
                    ),
                )
            ]

    result = await run_deepseek_batch(
        _batch([_item(mode="deep_think")]),
        session_factory=lambda config, evidence_dir, stem: _RecSession(),
        heartbeat=lambda p: None,
    )
    assert seen == ["deep_think"]
    assert result.results[0].status == "ok"
    assert result.results[0].quality_state == "live_valid"


async def test_run_deepseek_batch_session_toggle_failure_marks_all_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """防御：toggle 失败逃出题内映射（session 级抛出）→ 全题 wall 诚实记录。"""
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _ToggleFailSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            raise deepseek_adapter._ModeToggleFailed("chip not found", None)

    result = await run_deepseek_batch(
        _batch([_item(mode="deep_think"), _item(mode="deep_think")]),
        session_factory=lambda config, evidence_dir, stem: _ToggleFailSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert all(r.error_type == "mode_toggle_failed" for r in result.results)


async def test_collect_one_normal_turns_deep_think_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """normal 口径（20260810 用户拍板）= 快速模式+智能搜索开+深度思考**关**：
    账号粘滞的思考开态必须被关掉（幂等——已在目标态的开关零点击）。"""
    _adapter_env(tmp_path, monkeypatch)
    page = _FakePage(
        messages=0,
        chips={"深度思考": True, "智能搜索": True},  # 思考开（深度思考跑过的粘滞态）
        tab_selected="快速模式",
    )
    _install_fake_browser(monkeypatch, page)

    result = await run_deepseek_collection(
        _item(mode="normal"),
        session_factory=_PlaywrightDeepseekSession,
        heartbeat=lambda p: None,
    )

    assert result.quality_state == "live_valid"
    assert page.chips == {"深度思考": False, "智能搜索": True}
    first_key = next(i for i, e in enumerate(page.events) if e[0] == "key")
    think_clicks = [
        i
        for i, e in enumerate(page.events)
        if e[0] == "mouse_click" and _in_bb(_CHIP_BB["深度思考"], e[1], e[2])
    ]
    assert len(think_clicks) == 1 and think_clicks[0] < first_key
    # 智能搜索已在目标态 → 零点击；tab 已是快速 → 零点击
    assert not [
        e
        for e in page.events
        if e[0] == "mouse_click" and _in_bb(_CHIP_BB["智能搜索"], e[1], e[2])
    ]
    assert not [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(_FAST_TAB_BB, e[1], e[2])
    ]


async def test_collect_one_normal_turns_search_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """normal 口径下智能搜索被关过 → 必须重新点开（搜索是 GEO 评测引用的来源）。"""
    _adapter_env(tmp_path, monkeypatch)
    page = _FakePage(
        messages=0,
        chips={"深度思考": False, "智能搜索": False},
        tab_selected="快速模式",
    )
    _install_fake_browser(monkeypatch, page)

    result = await run_deepseek_collection(
        _item(mode="normal"),
        session_factory=_PlaywrightDeepseekSession,
        heartbeat=lambda p: None,
    )

    assert result.quality_state == "live_valid"
    assert page.chips == {"深度思考": False, "智能搜索": True}
    search_clicks = [
        e
        for e in page.events
        if e[0] == "mouse_click" and _in_bb(_CHIP_BB["智能搜索"], e[1], e[2])
    ]
    assert len(search_clicks) == 1
