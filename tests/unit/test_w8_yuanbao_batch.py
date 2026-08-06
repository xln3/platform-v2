"""元宝采集适配器 v2 batch/拟人化单元测试（对照 test_doubao_adapter.py 同构矩阵）：

- fake browser 全事件序列：拟人化接线（逐字输入/发送前停顿/零裸 click）、
  新会话纪律、优雅关闭 + profile 崩溃清理；
- collect_batch：一次会话 N 题（launch==1）、题序保持、fresh_chat 每题、
  阅读停顿每题（含最后一题）、证据逐题落盘、CDP capture 题末 detach；
- 失败语义：题级 wall → 后续题 aborted（零浏览器交互）；session 级墙 →
  全题 wall 结果不 raise；session 级 incomplete → raise 可重试；
- activity 层：mode 门（deep_think → unsupported_mode）/空 batch/契约违背；
- 默认 session 路径必须 to_thread（thread-probe 回归）；
- 常驻浏览器 attach（GEO_YUANBAO_CDP_URL）：不 launch、不关 context、
  不清理 profile，退出只断开 CDP。

真实浏览器绝不启动。
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

from workflows.activities import yuanbao_adapter
from workflows.activities.collection import CollectionBatchInput, CollectionTaskInput
from workflows.activities.human_like import human_pause
from workflows.activities.yuanbao_adapter import (
    CollectedAnswer,
    YuanbaoAdapterConfig,
    _ensure_fresh_chat,
    _IncompleteCapture,
    _PlaywrightYuanbaoSession,
    _WallError,
    run_yuanbao_batch,
    run_yuanbao_collection,
)

# ---------------------------------------------------------------------------
# fake browser 全事件序列测试工具（_PlaywrightYuanbaoSession 全程 mock 驱动，
# 记录 page 事件序列，验证拟人化接线 / 新会话纪律 / 优雅关闭）
# ---------------------------------------------------------------------------

_COMPOSER_BB = {"x": 80.0, "y": 600.0, "width": 600.0, "height": 48.0}
_SEND_BB = {"x": 640.0, "y": 610.0, "width": 32.0, "height": 32.0}
_NEW_CHAT_BB = {"x": 40.0, "y": 120.0, "width": 96.0, "height": 32.0}
_OVERLAY_BB = {"x": 300.0, "y": 200.0, "width": 90.0, "height": 32.0}

_ANSWER_TEXT = "这是元宝的真实回答。"


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
        self._emitted = 0

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {}

    def on(self, name: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self.handlers[name] = fn

    def detach(self) -> None:
        self.detached += 1

    def emit_chat_stream(self) -> None:
        self._emitted += 1
        rid = f"req-{self._emitted}"
        self.handlers["Network.requestWillBeSent"](
            {
                "requestId": rid,
                "request": {"url": "https://yuanbao.tencent.com/api/chat/conv-1", "method": "POST"},
            }
        )
        self.handlers["Network.responseReceived"]({"requestId": rid, "response": {}})
        self.handlers["Network.dataReceived"]({"requestId": rid, "dataLength": 128})
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


class _FakeElement:
    """all() 命中的助手气泡元素（inner_text 即答案正文）。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self, timeout: int | None = None) -> str:
        return self._text

    def get_attribute(self, name: str) -> None:
        return None


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

    def all(self) -> list[_FakeElement]:
        return self._page.elements(self._selector)

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
        if self._selector in yuanbao_adapter._INPUT_SELECTORS:
            return self._page.composer_value
        return None

    def inner_text(self, timeout: int | None = None) -> str:
        return self._page.body_text


class _FakePage:
    """记录全事件序列的 page 替身。messages>0 模拟旧会话残留；route_click 让
    落在特定区域的鼠标点击产生真实副作用（发送受理+流发出 / 新对话切换）。"""

    def __init__(
        self,
        *,
        messages: int = 0,
        composer_value: str = "",
        new_chat_button: bool = True,
        goto_clears: bool = False,
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
        self.url = yuanbao_adapter._CHAT_URL
        self.messages = messages
        self.composer_value = composer_value
        self.new_chat_button = new_chat_button
        self.goto_clears = goto_clears
        self.visible_overlays = visible_overlays or frozenset()
        self.body_text = ""
        self.answer_text = ""
        # 发送吞没模拟（风控静默吞发送）：第 N 次（1-based）起 send 区点击不再
        # 清空 composer、不再触发 /api/chat/ 流——驱动 wall_send 路径。
        self.swallow_sends_from = swallow_sends_from
        self.send_clicks = 0

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector == "body":
            return ("body", True, None)
        if selector in yuanbao_adapter._INPUT_SELECTORS:
            return ("composer", True, _COMPOSER_BB)
        if selector in yuanbao_adapter._SEND_SELECTORS:
            return ("send", True, _SEND_BB)
        if self.new_chat_button and selector in yuanbao_adapter._NEW_CHAT_SELECTORS:
            return ("new_chat", True, _NEW_CHAT_BB)
        if selector in self.visible_overlays:
            return ("overlay", True, _OVERLAY_BB)
        return ("none", False, None)

    def elements(self, selector: str) -> list[_FakeElement]:
        # 助手气泡：发送受理后出现（答案正文 DOM 抽取的权威来源）
        if (
            selector in yuanbao_adapter._ASSISTANT_SELECTORS
            and self.messages > 0
            and self.answer_text
        ):
            return [_FakeElement(self.answer_text)]
        return []

    def route_click(self, x: float, y: float) -> None:
        if _in_bb(_SEND_BB, x, y):
            self.send_clicks += 1
            if self.swallow_sends_from is not None and self.send_clicks >= (
                self.swallow_sends_from
            ):
                return  # 风控吞发送：composer 不清空、无 /api/chat/ 流
            self.composer_value = ""  # 发送被受理：composer 清空
            self.messages = 2  # 一问一答出现在页面（下一题需点「新对话」）
            self.answer_text = _ANSWER_TEXT
            self.cdp.emit_chat_stream()
        elif _in_bb(_NEW_CHAT_BB, x, y):
            self.messages = 0  # 「新对话」切到全新会话
            self.composer_value = ""
            self.answer_text = ""

    def locator(self, selector: str) -> _FakeLocator:
        self.events.append(("locator", selector))
        return _FakeLocator(self, selector)

    def evaluate(self, script: str, *_args: Any) -> Any:
        self.events.append(("evaluate", script))
        if script == yuanbao_adapter._CHAT_MESSAGE_COUNT_JS:
            return self.messages
        if script == yuanbao_adapter._FLATTEN_FOR_SCREENSHOT_JS:
            return {}
        return None

    def goto(self, url: str, **_kw: Any) -> None:
        self.events.append(("goto", url))
        if self.goto_clears:
            self.messages = 0  # 导航兜底成功：全新聊天页
            self.answer_text = ""

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
        self._page.events.append(("context_close",))


class _FakePWContextManager:
    def __init__(self, pw: Any) -> None:
        self._pw = pw

    def __enter__(self) -> Any:
        return self._pw

    def __exit__(self, *_exc: Any) -> bool:
        return False


def _install_fake_browser(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> None:
    """把浏览器驱动/时钟/崩溃清理全部替换为 fake（launch 路径）。"""
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
        yuanbao_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        yuanbao_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )
    real_clean = yuanbao_adapter._clean_profile_crash_state

    def _clean_spy(profile_dir: Path) -> bool:
        page.events.append(("clean",))
        return real_clean(profile_dir)

    monkeypatch.setattr(yuanbao_adapter, "_clean_profile_crash_state", _clean_spy)


def _yuanbao_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_YUANBAO_HEADLESS", "1")
    return evidence


def _make_pace(page: _FakePage, rng: random.Random) -> Callable[[float, float], float]:
    def pace(lo: float, hi: float) -> float:
        return human_pause(rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000)))

    return pace


def _recording_shot(calls: list[str]) -> Callable[[str], None]:
    def shot(suffix: str) -> None:
        calls.append(suffix)

    return shot


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-5",
        query="你好，请用一句话介绍你自己",
        model="yuanbao",
        region="Beijing",
        mode=mode,
        adapter="yuanbao",
    )


# ---------------------------------------------------------------------------
# 拟人化单题全链路（per-task 路径，真实 session 类 + fake 驱动）
# ---------------------------------------------------------------------------


async def test_session_collect_full_humanized_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全链路（normal mode）：逐字输入、发送前停顿、新会话验证、优雅关闭+崩溃清理。"""
    evidence = _yuanbao_env(tmp_path, monkeypatch)
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
    result = await run_yuanbao_collection(
        item, session_factory=_PlaywrightYuanbaoSession, heartbeat=lambda p: None
    )

    assert result.answer_text == _ANSWER_TEXT
    assert result.quality_state == "live_valid"
    assert (evidence / "run-9-task-5-a1.png").is_file()
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
    assert ("evaluate", yuanbao_adapter._CHAT_MESSAGE_COUNT_JS) in events

    # 5) 全程无裸 locator.click（发送/弹层/输入框聚焦全走鼠标事件链）
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


# ---------------------------------------------------------------------------
# 新会话纪律（_ensure_fresh_chat 单元级）
# ---------------------------------------------------------------------------


def test_fresh_chat_fast_path_when_already_fresh() -> None:
    page = _FakePage(messages=0)
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(yuanbao_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    # 已是新会话：不点「新对话」、不导航，但验证探针确实跑过
    assert not [e for e in page.events if e[0] == "mouse_click"]
    assert not [e for e in page.events if e[0] == "goto"]
    assert ("evaluate", yuanbao_adapter._CHAT_MESSAGE_COUNT_JS) in page.events


def test_fresh_chat_clicks_new_conversation_button() -> None:
    page = _FakePage(messages=2)  # 旧会话残留
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(yuanbao_adapter._INPUT_SELECTORS[0]),
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
        page.locator(yuanbao_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    assert page.messages == 0
    assert ("goto", yuanbao_adapter._CHAT_URL) in page.events


def test_fresh_chat_honest_failure_when_stuck_in_old_conversation() -> None:
    page = _FakePage(messages=1, new_chat_button=False, goto_clears=False)
    rng = random.Random(6)
    shots: list[str] = []
    with pytest.raises(_IncompleteCapture, match="could-not-establish-fresh-chat"):
        _ensure_fresh_chat(
            page,
            page.locator(yuanbao_adapter._INPUT_SELECTORS[0]),
            rng,
            pace=_make_pace(page, rng),
            shot=_recording_shot(shots),
        )
    assert shots == ["fresh_chat"]  # 失败有存证截图，绝不静默沿用旧会话


# ---------------------------------------------------------------------------
# collect_batch：run 级会话复用（fake 浏览器全程记录；真实驱动绝不启动）
# ---------------------------------------------------------------------------


def _batch_specs(count: int) -> list[yuanbao_adapter.YuanbaoBatchItemSpec]:
    return [
        yuanbao_adapter.YuanbaoBatchItemSpec(
            business_key=f"run-1-task-{index}",
            query=f"第{index}题的重疾险有哪些",
            mode="normal",
            file_stem=f"run-1-task-{index}-a1",
        )
        for index in range(1, count + 1)
    ]


def _make_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> Any:
    evidence = _yuanbao_env(tmp_path, monkeypatch)
    _install_fake_browser(monkeypatch, page)
    config = YuanbaoAdapterConfig.from_env()
    return yuanbao_adapter._PlaywrightYuanbaoSession(config, evidence, "batch-stem")


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
    assert all(o.answer is not None and o.answer.answer_text == _ANSWER_TEXT for o in outcomes)
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
    count_probes = [
        e for e in events if e == ("evaluate", yuanbao_adapter._CHAT_MESSAGE_COUNT_JS)
    ]
    assert len(count_probes) >= 3
    new_chat_clicks = [
        e for e in events if e[0] == "mouse_click" and _in_bb(_NEW_CHAT_BB, e[1], e[2])
    ]
    assert len(new_chat_clicks) == 2  # 第 2、3 题各点一次「新对话」（第 1 题本就新会话）

    # 4) 阅读停顿逐题：wheel 滚动 2-5 次/题（共 6-15 次，delta 240-720 向下），
    #    每题一次 8-25s 停留（含最后一题）。导航后的 11s hydration settle 也落在
    #    该区间，按位置确定性排除（区别于豆包的 6s settle——它不落入判定区间）。
    nav_settle_idx = next(i for i, e in enumerate(events) if e == ("wait", 11_000))
    wheels = [e for e in events if e[0] == "wheel"]
    assert 3 * 2 <= len(wheels) <= 3 * 5
    assert all(e[1] == 0.0 and 240.0 <= e[2] <= 720.0 for e in wheels)
    long_waits = [
        e[1]
        for i, e in enumerate(events)
        if e[0] == "wait" and 8_000.0 <= e[1] <= 25_000.0 and i != nav_settle_idx
    ]
    assert len(long_waits) == 3

    # 5) 证据逐题落盘：整页截图每题一份（per-item stem 区分）
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


def test_collect_batch_resident_attach_skips_launch_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GEO_YUANBAO_CDP_URL 非空 → attach 常驻浏览器：不 launch、不关 context、
    不做 profile 崩溃清理（归 supervisor），退出只断开 CDP 连接。"""
    evidence = _yuanbao_env(tmp_path, monkeypatch)
    monkeypatch.setenv("GEO_YUANBAO_CDP_URL", "http://127.0.0.1:19222")
    page = _FakePage(messages=0)
    context = _FakeContext(page)
    page.context = context
    browser = SimpleNamespace(
        contexts=[context],
        close=lambda: page.events.append(("browser_disconnect",)),
    )

    def _no_launch(**kw: Any) -> Any:
        raise AssertionError("resident 模式绝不 launch")

    chromium = SimpleNamespace(
        launch_persistent_context=_no_launch,
        connect_over_cdp=lambda url: page.events.append(("attach", url)) or browser,
    )
    pw = SimpleNamespace(chromium=chromium)

    def _sync_playwright() -> _FakePWContextManager:
        return _FakePWContextManager(pw)

    monkeypatch.setattr(
        yuanbao_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        yuanbao_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )

    def _clean_spy(profile_dir: Path) -> bool:
        page.events.append(("clean",))
        return False

    monkeypatch.setattr(yuanbao_adapter, "_clean_profile_crash_state", _clean_spy)

    config = YuanbaoAdapterConfig.from_env()
    session = yuanbao_adapter._PlaywrightYuanbaoSession(config, evidence, "batch-stem")
    outcomes = session.collect_batch(_batch_specs(1), on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["ok"]
    events = page.events
    assert ("attach", "http://127.0.0.1:19222") in events
    assert ("browser_disconnect",) in events  # 退出只断开 CDP
    assert not [e for e in events if e[0] == "clean"]  # profile 归 supervisor
    assert not [e for e in events if e[0] == "context_close"]  # context 不归适配器关
    # attach 路径同样导航 + 采集 + 证据落盘
    assert (evidence / "run-1-task-1-a1.png").is_file()


# ---------------------------------------------------------------------------
# run_yuanbao_batch（activity 层：fake session 注入，不启动浏览器）
# ---------------------------------------------------------------------------


async def test_run_yuanbao_batch_maps_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activity 层：fake session 注入（不启动浏览器），outcome→per-item 结果映射。"""
    evidence = _yuanbao_env(tmp_path, monkeypatch)
    shot = evidence / "run-1-task-1-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    wall_shot = evidence / "run-1-task-2-a1-send_wall.png"
    wall_shot.write_bytes(b"\x89PNG-fake")

    class _BatchFakeSession:
        def collect_batch(
            self,
            items: list[yuanbao_adapter.YuanbaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[yuanbao_adapter.YuanbaoBatchItemOutcome]:
            on_stage(f"item:{items[0].business_key}")
            return [
                yuanbao_adapter.YuanbaoBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="真实回答", references=[], screenshot_path=shot
                    ),
                ),
                yuanbao_adapter.YuanbaoBatchItemOutcome(
                    business_key=items[1].business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send",
                    evidence_path=wall_shot,
                ),
                yuanbao_adapter.YuanbaoBatchItemOutcome(
                    business_key=items[2].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped",
                ),
            ]

    batch = CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[
            CollectionTaskInput(
                business_key=f"run-7-task-{index}",
                query=f"查询{index}",
                model="yuanbao",
                region="Beijing",
                mode="normal",
                adapter="yuanbao",
            )
            for index in (3, 4, 5)
        ],
    )
    result = await run_yuanbao_batch(
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


async def test_run_yuanbao_batch_session_wall_marks_all_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级墙（导航后登录墙，一题未发）→ 全题 wall 结果，不 raise。"""
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _WallSession:
        def collect_batch(
            self,
            items: list[yuanbao_adapter.YuanbaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[yuanbao_adapter.YuanbaoBatchItemOutcome]:
            raise _WallError("wall_login_required", "yuanbao login wall detected", None)

    batch = CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[_item(), _item()],
    )
    result = await run_yuanbao_batch(
        batch,
        session_factory=lambda config, evidence_dir, stem: _WallSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert all(r.error_type == "wall_login_required" for r in result.results)


async def test_run_yuanbao_batch_session_incomplete_raises_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级临时故障（浏览器启动失败，一题未发）→ raise 可重试错误。"""
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _IncompleteSession:
        def collect_batch(
            self,
            items: list[yuanbao_adapter.YuanbaoBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[yuanbao_adapter.YuanbaoBatchItemOutcome]:
            raise _IncompleteCapture("browser-launch-failed(patchright): boom")

    batch = CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item()]
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_batch(
            batch,
            session_factory=lambda config, evidence_dir, stem: _IncompleteSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "answer_capture_incomplete"
    assert exc_info.value.non_retryable is False


async def test_run_yuanbao_batch_config_and_mode_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置类错误照常 raise：mode 门（仅 normal；deep_think 拒绝）在浏览器启动
    之前；profile 缺失 fail-closed。"""
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _NeverCalled:
        def collect_batch(self, items: Any, on_stage: Any) -> Any:
            raise AssertionError("session must not be started")

    factory = lambda config, evidence_dir, stem: _NeverCalled()  # noqa: E731
    batch = CollectionBatchInput(
        tenant_pub_id="tnt_test",
        run_pub_id="run_test",
        items=[_item(mode="deep_think")],
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_batch(batch, session_factory=factory, heartbeat=lambda p: None)
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True

    monkeypatch.delenv("GEO_YUANBAO_PROFILE_DIR")
    ok_batch = CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=[_item()]
    )
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_yuanbao_batch(ok_batch, session_factory=factory, heartbeat=lambda p: None)
    assert exc_info_unset.value.type == "adapter_not_configured"


async def test_run_yuanbao_batch_empty_items_and_outcome_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 batch → 空结果（零浏览器交互）；outcome 数量不符 → fail-closed raise。"""
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _EmptySession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            assert items == []
            return []

    empty = await run_yuanbao_batch(
        CollectionBatchInput(tenant_pub_id="tnt_test", run_pub_id="run_test", items=[]),
        session_factory=lambda config, evidence_dir, stem: _EmptySession(),
        heartbeat=lambda p: None,
    )
    assert empty.results == []

    class _ShortSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            return []  # 契约违背：3 题 0 结果

    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_batch(
            CollectionBatchInput(
                tenant_pub_id="tnt_test",
                run_pub_id="run_test",
                items=[_item(), _item(), _item()],
            ),
            session_factory=lambda config, evidence_dir, stem: _ShortSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "batch_outcome_contract_violation"


async def test_run_yuanbao_batch_default_session_runs_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产约定（不传 session_factory）必须走 to_thread——sync 浏览器不进事件循环。

    回归（豆包 2026-08-06 batch 首航生产事故）：activity 显式传真实 session 类
    会被误判为注入 fake，在事件循环里直跑 sync patchright
    （"Playwright Sync API inside the asyncio loop"）。
    """
    import threading

    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    seen: dict[str, bool] = {}

    class _ThreadProbeSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            seen["on_main_thread"] = threading.current_thread() is threading.main_thread()
            return [
                yuanbao_adapter.YuanbaoBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="probe only",
                )
            ]

    monkeypatch.setattr(
        yuanbao_adapter,
        "_PlaywrightYuanbaoSession",
        lambda config, evidence_dir, stem: _ThreadProbeSession(),
    )
    result = await run_yuanbao_batch(
        CollectionBatchInput(
            tenant_pub_id="tnt_test",
            run_pub_id="run_test",
            items=[_item()],
        ),
        heartbeat=lambda p: None,
    )
    assert seen["on_main_thread"] is False
    assert result.results[0].error_type == "aborted_after_failure"
