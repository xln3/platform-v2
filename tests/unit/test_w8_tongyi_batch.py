"""tongyi 采集适配器 batch 化（2026-08-06，与 doubao 同构）单元测试。

浏览器层全部 fake（记录全事件序列 / 注入 fake session），绝不启动真浏览器。
覆盖：run 级会话复用（launch==1 / 题序 / fresh_chat 逐题 / 阅读停顿逐题 /
证据逐题 / CDP 逐题 detach）、wall→后续 aborted 零交互、activity 层
（outcome 映射 / session 级墙 / session 级 incomplete / mode 门 / 配置门 /
空 batch / 契约违背 / 默认 session 必须 to_thread）、fresh-chat 纪律四态、
CDP 常驻 attach 路径（不 close context、不动 profile）、per-task 拟人化全链路、
容器级抽取（卡片段拼接/噪声过滤）与 DOM 稳定门（2026-08-07 截断案回归）。
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

from workflows.activities import tongyi_adapter
from workflows.activities.collection import CollectionTaskInput
from workflows.activities.human_like import human_pause
from workflows.activities.tongyi_adapter import (
    CollectedAnswer,
    TongyiAdapterConfig,
    _ensure_fresh_chat,
    _IncompleteCapture,
    _PlaywrightTongyiSession,
    _WallError,
)

_PLACEHOLDER = "\ufeff向千问提问"


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-2",
        query="你好，请用一句话介绍你自己",
        model="tongyi",
        region="CN-BJ",
        mode=mode,
        adapter="tongyi",
    )


def _batch(items: list[CollectionTaskInput]) -> tongyi_adapter.CollectionBatchInput:
    return tongyi_adapter.CollectionBatchInput(
        tenant_pub_id="tnt_test", run_pub_id="run_test", items=items
    )


# ---------------------------------------------------------------------------
# fake browser（全事件序列记录；route_click 让落在特定区域的鼠标点击产生真实
# 副作用：发送受理 / 新对话切换 / 风控吞发送）
# ---------------------------------------------------------------------------

_COMPOSER_BB = {"x": 80.0, "y": 600.0, "width": 600.0, "height": 48.0}
_SEND_BB = {"x": 640.0, "y": 610.0, "width": 32.0, "height": 32.0}
_NEW_CHAT_BB = {"x": 40.0, "y": 120.0, "width": 96.0, "height": 32.0}
_OVERLAY_BB = {"x": 300.0, "y": 200.0, "width": 90.0, "height": 32.0}


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
        return {}

    def on(self, name: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self.handlers[name] = fn

    def detach(self) -> None:
        self.detached += 1

    def emit_stream(self) -> None:
        rid = f"req-{self._page.send_clicks}"
        self.handlers["Network.responseReceived"](
            {"requestId": rid, "response": {"mimeType": "text/event-stream"}}
        )
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
        # 逐字输入进 composer：首个字符替换占位符（发送受理/吞没由 route_click 决定后续）
        if self._page.composer_value.replace("\ufeff", "") == "向千问提问":
            self._page.composer_value = ""
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
        if self._selector == ".qk-markdown" and self._page.answer_visible:
            return [self]
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
        if self._selector in tongyi_adapter._INPUT_SELECTORS:
            if "isConnected" in script:
                return True
            return self._page.composer_value
        return None

    def inner_text(self, timeout: int | None = None) -> str:
        if self._selector == ".qk-markdown":
            return self._page.answer_text
        return self._page.body_text


class _FakePage:
    """记录全事件序列的 page 替身。messages>0 模拟旧会话残留；answer_visible
    模拟页面已渲染助手气泡（_extract_response 的 DOM ground truth）。"""

    def __init__(
        self,
        *,
        messages: int = 0,
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
        self.url = tongyi_adapter._CHAT_URL
        self.messages = messages
        self.composer_value = _PLACEHOLDER
        self.new_chat_button = new_chat_button
        self.goto_clears = goto_clears
        self.visible_overlays = visible_overlays or frozenset()
        self.body_text = ""
        self.answer_visible = False
        self.answer_text = "这是答案"
        # 容器级抽取探针（_ANSWER_EXTRACT_JS）注入面：非 None 时原样返回
        # （测卡片段/噪声过滤）；answer_grows_forever 模拟「流已完但 DOM 仍增长」
        # （2026-08-07 截断案回归）——每次抽取文本追加递增后缀，DOM 永不静默。
        self.extract_payload: dict[str, Any] | None = None
        self.answer_grows_forever = False
        self._extract_calls = 0
        # 发送吞没模拟（风控静默吞发送）：第 N 次（1-based）起 send 区点击不再
        # 清空 composer、不再触发 event-stream——驱动 wall_send 路径。
        self.swallow_sends_from = swallow_sends_from
        self.send_clicks = 0
        # 受理但静默（内容过滤/服务端丢包）：composer 清空但无流、无答案渲染——
        # 驱动 no-stream-and-dom-not-quiet 的 incomplete 路径。
        self.accept_but_silent = False

    def classify(self, selector: str) -> tuple[str, bool, dict[str, float] | None]:
        if selector == "body":
            return ("body", True, None)
        if selector in tongyi_adapter._INPUT_SELECTORS:
            return ("composer", True, _COMPOSER_BB)
        if selector == '[data-proxyllm-send="true"]':
            return ("send", True, _SEND_BB)
        if self.new_chat_button and selector in tongyi_adapter._NEW_CHAT_SELECTORS:
            return ("new_chat", True, _NEW_CHAT_BB)
        if selector in self.visible_overlays:
            return ("overlay", True, _OVERLAY_BB)
        return ("none", False, None)

    def route_click(self, x: float, y: float) -> None:
        if _in_bb(_SEND_BB, x, y):
            self.send_clicks += 1
            if self.swallow_sends_from is not None and self.send_clicks >= (
                self.swallow_sends_from
            ):
                return  # 风控吞发送：composer 不清空、无 event-stream
            self.composer_value = _PLACEHOLDER  # 发送被受理：占位符恢复（live 实测形态）
            if self.accept_but_silent:
                return  # 受理但无流、无答案渲染
            self.messages = 2  # 一问一答出现在页面（下一题需点「新对话」）
            self.answer_visible = True
            self.cdp.emit_stream()
        elif _in_bb(_NEW_CHAT_BB, x, y):
            self.messages = 0  # 「新对话」切到全新会话（composer 一并重渲染为空）
            self.answer_visible = False
            self.composer_value = _PLACEHOLDER

    def locator(self, selector: str) -> _FakeLocator:
        self.events.append(("locator", selector))
        return _FakeLocator(self, selector)

    def evaluate(self, script: str, *_args: Any) -> Any:
        self.events.append(("evaluate", script))
        if script == tongyi_adapter._TAG_JS:
            return True
        if script == tongyi_adapter._CHAT_MESSAGE_COUNT_JS:
            return self.messages
        if script == tongyi_adapter._FLATTEN_FOR_SCREENSHOT_JS:
            return {}
        if script == tongyi_adapter._ANSWER_EXTRACT_JS:
            if self.extract_payload is not None:
                return self.extract_payload
            if not self.answer_visible:
                return {"segments": [], "refs": []}
            text = self.answer_text
            if self.answer_grows_forever:
                self._extract_calls += 1
                text = f"{self.answer_text}{self._extract_calls}"
            return {
                "segments": [
                    {"kind": "markdown", "cls": "qk-markdown", "text": text}
                ],
                "refs": [],
            }
        return None

    def goto(self, url: str, **_kw: Any) -> None:
        self.events.append(("goto", url))
        if self.goto_clears:
            self.messages = 0  # 导航兜底成功：全新聊天页
            self.answer_visible = False

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
    """把浏览器驱动/时钟/崩溃清理替换为 fake（launch 路径）。"""
    monkeypatch.delenv("GEO_TONGYI_CDP_URL", raising=False)
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
        tongyi_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        tongyi_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )
    real_clean = tongyi_adapter._clean_profile_crash_state

    def _clean_spy(profile_dir: Path) -> bool:
        page.events.append(("clean",))
        return real_clean(profile_dir)

    monkeypatch.setattr(tongyi_adapter, "_clean_profile_crash_state", _clean_spy)


def _make_pace(page: _FakePage, rng: random.Random) -> Callable[[float, float], float]:
    def pace(lo: float, hi: float) -> float:
        return human_pause(rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000)))

    return pace


def _recording_shot(calls: list[str]) -> Callable[[str], None]:
    def shot(suffix: str) -> None:
        calls.append(suffix)

    return shot


def _batch_specs(count: int) -> list[tongyi_adapter.TongyiBatchItemSpec]:
    return [
        tongyi_adapter.TongyiBatchItemSpec(
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
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_TONGYI_HEADLESS", "1")
    _install_fake_browser(monkeypatch, page)
    config = TongyiAdapterConfig.from_env()
    return tongyi_adapter._PlaywrightTongyiSession(config, evidence, "batch-stem")


# ---------------------------------------------------------------------------
# collect_batch：run 级会话复用
# ---------------------------------------------------------------------------


def test_collect_batch_shares_one_browser_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 题共享一次 launch：fresh_chat 探针逐题、阅读停顿逐题、证据逐题落盘、
    context.close 恰好一次（优雅关闭 + 崩溃清理首尾各一次）、CDP 逐题 detach。"""
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
    count_probes = [
        e for e in events if e == ("evaluate", tongyi_adapter._CHAT_MESSAGE_COUNT_JS)
    ]
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

    # 5) 证据逐题落盘：整页截图每题一份（per-item stem 区分）
    evidence = tmp_path / "evidence"
    for spec in specs:
        assert (evidence / f"{spec.file_stem}.png").is_file()

    # 6) 每题 CDP capture 题末 detach（3 题 = 3 次）
    assert page.cdp.detached == 3

    # 7) 全程无裸 locator.click（发送/弹层/新对话全走鼠标事件链）
    assert not [e for e in events if e[0] == "locator_click"]


def test_collect_batch_wall_aborts_remaining_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 题发送被风控吞没（wall_send）：results=[ok, wall, aborted]，无 raise；
    aborted 题零浏览器交互；失败题有 per-item 存证截图；优雅关闭仍发生。"""
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


def test_collect_batch_incomplete_aborts_remaining_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 1 题 capture 级 incomplete（发送受理但无流且 DOM 不静默）→
    [incomplete, aborted]：诚实记可重试失败，后续题零交互。"""
    page = _FakePage(messages=0)
    page.accept_but_silent = True  # composer 清空但无 event-stream、无答案渲染
    session = _make_session(tmp_path, monkeypatch, page)
    specs = _batch_specs(2)

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["incomplete", "aborted"]
    assert outcomes[0].error_type == "answer_capture_incomplete"
    assert outcomes[1].error_type == "aborted_after_failure"
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(specs[0].query)  # 第 2 题零输入


# ---------------------------------------------------------------------------
# run_tongyi_batch（activity 层，fake session 注入）
# ---------------------------------------------------------------------------


async def test_run_tongyi_batch_maps_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activity 层：fake session 注入（不启动浏览器），outcome→per-item 结果映射。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    shot = evidence / "run-7-task-3-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    wall_shot = evidence / "run-7-task-4-a1-send_wall.png"
    wall_shot.write_bytes(b"\x89PNG-fake")

    class _BatchFakeSession:
        def collect_batch(
            self,
            items: list[tongyi_adapter.TongyiBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[tongyi_adapter.TongyiBatchItemOutcome]:
            on_stage(f"item:{items[0].business_key}")
            return [
                tongyi_adapter.TongyiBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="ok",
                    answer=CollectedAnswer(
                        answer_text="真实回答", references=[], screenshot_path=shot
                    ),
                ),
                tongyi_adapter.TongyiBatchItemOutcome(
                    business_key=items[1].business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send",
                    evidence_path=wall_shot,
                ),
                tongyi_adapter.TongyiBatchItemOutcome(
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
                model="tongyi",
                region="CN-BJ",
                mode="normal",
                adapter="tongyi",
            )
            for index in (3, 4, 5)
        ]
    )
    result = await tongyi_adapter.run_tongyi_batch(
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


async def test_run_tongyi_batch_session_wall_marks_all_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级墙（导航后登录墙，一题未发）→ 全题 wall 结果，不 raise。"""
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _WallSession:
        def collect_batch(
            self,
            items: list[tongyi_adapter.TongyiBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[tongyi_adapter.TongyiBatchItemOutcome]:
            raise _WallError("wall_login_required", "tongyi login wall detected", None)

    result = await tongyi_adapter.run_tongyi_batch(
        _batch([_item(), _item()]),
        session_factory=lambda config, evidence_dir, stem: _WallSession(),
        heartbeat=lambda p: None,
    )
    assert [r.status for r in result.results] == ["wall", "wall"]
    assert all(r.error_type == "wall_login_required" for r in result.results)


async def test_run_tongyi_batch_session_incomplete_raises_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session 级临时故障（浏览器启动失败，一题未发）→ raise 可重试错误。"""
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _IncompleteSession:
        def collect_batch(
            self,
            items: list[tongyi_adapter.TongyiBatchItemSpec],
            on_stage: Callable[[str], None],
        ) -> list[tongyi_adapter.TongyiBatchItemOutcome]:
            raise _IncompleteCapture("browser-launch-failed(patchright): boom")

    with pytest.raises(ApplicationError) as exc_info:
        await tongyi_adapter.run_tongyi_batch(
            _batch([_item()]),
            session_factory=lambda config, evidence_dir, stem: _IncompleteSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "answer_capture_incomplete"
    assert exc_info.value.non_retryable is False


async def test_run_tongyi_batch_config_and_mode_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置类错误照常 raise：mode 门（仅 normal；deep_think 拒绝）在浏览器启动
    之前；profile 缺失 fail-closed；CDP URL 非法同属配置类。"""
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _NeverCalled:
        def collect_batch(self, items: Any, on_stage: Any) -> Any:
            raise AssertionError("session must not be started")

    def factory(config: Any, evidence_dir: Any, stem: Any) -> Any:
        return _NeverCalled()

    with pytest.raises(ApplicationError) as exc_info:
        await tongyi_adapter.run_tongyi_batch(
            _batch([_item(mode="deep_think")]), session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True

    monkeypatch.delenv("GEO_TONGYI_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await tongyi_adapter.run_tongyi_batch(
            _batch([_item()]), session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"

    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_TONGYI_CDP_URL", "not-a-url")
    with pytest.raises(ApplicationError) as exc_info_cdp:
        await tongyi_adapter.run_tongyi_batch(
            _batch([_item()]), session_factory=factory, heartbeat=lambda p: None
        )
    assert exc_info_cdp.value.type == "adapter_not_configured"
    assert exc_info_cdp.value.non_retryable is True


async def test_run_tongyi_batch_empty_items_and_outcome_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 batch → 空结果（零浏览器交互）；outcome 数量不符 → fail-closed raise。"""
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))

    class _EmptySession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            assert items == []
            return []

    empty = await tongyi_adapter.run_tongyi_batch(
        _batch([]),
        session_factory=lambda config, evidence_dir, stem: _EmptySession(),
        heartbeat=lambda p: None,
    )
    assert empty.results == []

    class _ShortSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            return []  # 契约违背：3 题 0 结果

    with pytest.raises(ApplicationError) as exc_info:
        await tongyi_adapter.run_tongyi_batch(
            _batch([_item(), _item(), _item()]),
            session_factory=lambda config, evidence_dir, stem: _ShortSession(),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "batch_outcome_contract_violation"


async def test_run_tongyi_batch_default_session_runs_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产约定（不传 session_factory）必须走 to_thread——sync 浏览器不进事件循环。

    回归（doubao 2026-08-06 batch 首航生产事故同款）：activity 实现显式传真实
    session 类会被误判为注入 fake，在事件循环里直跑 sync patchright。
    """
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    seen: dict[str, bool] = {}

    class _ThreadProbeSession:
        def collect_batch(self, items: Any, on_stage: Any) -> list[Any]:
            seen["on_main_thread"] = threading.current_thread() is threading.main_thread()
            return [
                tongyi_adapter.TongyiBatchItemOutcome(
                    business_key=items[0].business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="probe only",
                )
            ]

    monkeypatch.setattr(
        tongyi_adapter,
        "_PlaywrightTongyiSession",
        lambda config, evidence_dir, stem: _ThreadProbeSession(),
    )
    result = await tongyi_adapter.run_tongyi_batch(
        _batch([_item()]),
        heartbeat=lambda p: None,
    )
    assert seen["on_main_thread"] is False
    assert result.results[0].error_type == "aborted_after_failure"


# ---------------------------------------------------------------------------
# fresh-chat 纪律四态（_ensure_fresh_chat 单测）
# ---------------------------------------------------------------------------


def test_fresh_chat_fast_path_when_already_fresh() -> None:
    page = _FakePage(messages=0)
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(tongyi_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    # 已是新会话：不点「新对话」、不导航，但验证探针确实跑过
    assert not [e for e in page.events if e[0] == "mouse_click"]
    assert not [e for e in page.events if e[0] == "goto"]
    assert ("evaluate", tongyi_adapter._CHAT_MESSAGE_COUNT_JS) in page.events


def test_fresh_chat_clicks_new_conversation_button() -> None:
    page = _FakePage(messages=2)  # 旧会话残留
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(tongyi_adapter._INPUT_SELECTORS[0]),
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
        page.locator(tongyi_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    assert page.messages == 0
    assert ("goto", tongyi_adapter._CHAT_URL) in page.events


def test_fresh_chat_honest_failure_when_stuck_in_old_conversation() -> None:
    page = _FakePage(messages=1, new_chat_button=False, goto_clears=False)
    rng = random.Random(6)
    shots: list[str] = []
    with pytest.raises(_IncompleteCapture, match="could-not-establish-fresh-chat"):
        _ensure_fresh_chat(
            page,
            page.locator(tongyi_adapter._INPUT_SELECTORS[0]),
            rng,
            pace=_make_pace(page, rng),
            shot=_recording_shot(shots),
        )
    assert shots == ["fresh_chat"]  # 失败有存证截图，绝不静默沿用旧会话


def test_fresh_chat_composer_not_empty_is_not_fresh() -> None:
    """composer 有正文（非占位符）即「不新」——占位符识别绝不能把真内容当空。"""
    page = _FakePage(messages=0, goto_clears=True)
    page.composer_value = "半句未发送的草稿"
    rng = random.Random(6)
    _ensure_fresh_chat(
        page,
        page.locator(tongyi_adapter._INPUT_SELECTORS[0]),
        rng,
        pace=_make_pace(page, rng),
        shot=_recording_shot([]),
    )
    # 点了「新对话」（草稿视为旧会话残留），未走导航兜底
    clicks = [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(_NEW_CHAT_BB, e[1], e[2])
    ]
    assert len(clicks) == 1
    assert not [e for e in page.events if e[0] == "goto"]


# ---------------------------------------------------------------------------
# CDP 常驻 attach 路径（GEO_TONGYI_CDP_URL 非空）
# ---------------------------------------------------------------------------


def test_browser_session_resident_attach_skips_close_and_profile_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """attach 路径：connect_over_cdp 复用常驻浏览器——退出只断开（browser.close），
    绝不 launch、绝不 context.close、绝不做 profile 崩溃清理（归 supervisor）。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_TONGYI_HEADLESS", "1")
    monkeypatch.setenv("GEO_TONGYI_CDP_URL", "http://127.0.0.1:19222")

    page = _FakePage(messages=0)
    context = _FakeContext(page)
    page.context = context

    class _FakeResidentBrowser:
        def __init__(self) -> None:
            self.contexts = [context]

        def close(self) -> None:
            page.events.append(("browser_disconnect",))

    def _connect_over_cdp(url: str) -> _FakeResidentBrowser:
        page.events.append(("connect_over_cdp", url))
        return _FakeResidentBrowser()

    def _launch_forbidden(**kw: Any) -> Any:
        raise AssertionError("resident mode must not launch a new browser")

    chromium = SimpleNamespace(
        connect_over_cdp=_connect_over_cdp,
        launch_persistent_context=_launch_forbidden,
    )
    pw = SimpleNamespace(chromium=chromium)

    def _sync_playwright() -> _FakePWContextManager:
        return _FakePWContextManager(pw)

    monkeypatch.setattr(
        tongyi_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", _sync_playwright, TimeoutError),
    )
    monkeypatch.setattr(
        tongyi_adapter, "time", SimpleNamespace(monotonic=page.clock.monotonic)
    )

    def _clean_forbidden(profile_dir: Path) -> bool:
        raise AssertionError("resident mode must not touch the profile")

    monkeypatch.setattr(tongyi_adapter, "_clean_profile_crash_state", _clean_forbidden)

    config = TongyiAdapterConfig.from_env()
    session = _PlaywrightTongyiSession(config, evidence, "resident-stem")
    answer = session.collect("你好", on_stage=lambda s: None)

    assert answer.answer_text == "这是答案"
    events = page.events
    assert ("connect_over_cdp", "http://127.0.0.1:19222") in events
    assert events[-1] == ("browser_disconnect",)  # 只断开，不 close context
    assert not [e for e in events if e[0] == "context_close"]
    assert not [e for e in events if e[0] == "launch"]


# ---------------------------------------------------------------------------
# per-task 拟人化全链路（run_tongyi_collection + _PlaywrightTongyiSession）
# ---------------------------------------------------------------------------


async def test_session_collect_full_humanized_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """per-task 全链路：逐字输入、发送前停顿、新会话验证、占位符 composer
    识别、优雅关闭+崩溃清理（Preferences 写回 Normal）。"""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_TONGYI_HEADLESS", "1")
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
    result = await tongyi_adapter.run_tongyi_collection(
        item, session_factory=_PlaywrightTongyiSession, heartbeat=lambda p: None
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

    # 4) 新会话验证被调用（消息节点计数探针）；占位符 composer 被正确识别为空
    assert ("evaluate", tongyi_adapter._CHAT_MESSAGE_COUNT_JS) in events
    assert page.composer_value == _PLACEHOLDER  # 发送受理后占位符恢复（live 实测形态）

    # 5) 全程无裸 locator.click（输入框/发送/弹层全走鼠标事件链）
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


def test_overlay_cleanup_clicks_only_visible_overlay() -> None:
    """弹层清理拟人化：候选先 count/visible 粗筛，真实存在的遮罩才 human_click。"""
    page = _FakePage(visible_overlays={'button:has-text("知道了")'})
    tongyi_adapter._try_close_overlays(page, random.Random(2))
    assert ("press", "Escape") in page.events
    overlay_clicks = [
        e for e in page.events if e[0] == "mouse_click" and _in_bb(_OVERLAY_BB, e[1], e[2])
    ]
    assert len(overlay_clicks) == 1  # 可见遮罩被拟人化点击关闭
    assert not [e for e in page.events if e[0] == "locator_click"]


# ---------------------------------------------------------------------------
# 容器级抽取（卡片段拼接/噪声过滤）+ DOM 稳定门（2026-08-07，215 字截断案）
# ---------------------------------------------------------------------------


def test_compose_answer_segments_concatenates_markdown_and_card() -> None:
    """markdown 多段 + 富文本卡片段按文档序拼接（供应商卡片不再损失）。"""
    segments = [
        {"kind": "markdown", "cls": "qk-markdown", "text": "正文开头"},
        {"kind": "widget", "cls": "supplier-rich-card", "text": "盛邦安全 DayDayMap 卡片"},
        {"kind": "markdown", "cls": "qk-markdown", "text": "正文结尾"},
    ]
    text = tongyi_adapter._compose_answer_segments(segments)
    assert text == "正文开头\n盛邦安全 DayDayMap 卡片\n正文结尾"


def test_compose_answer_segments_filters_toolbar_noise() -> None:
    """工具栏/按钮类卡片段丢弃：操作条类名、纯按钮短文本、过短段、非 dict 段。"""
    segments = [
        {"kind": "markdown", "cls": "qk-markdown", "text": "正文"},
        {"kind": "widget", "cls": "answer-action-bar", "text": "复制 点赞 重新生成"},
        {"kind": "widget", "cls": "some-card", "text": "分享"},
        {"kind": "widget", "cls": "x", "text": "短"},
        {"kind": "widget", "cls": "", "text": ""},
        "garbage",
        {"kind": "widget", "cls": "vendor-card", "text": "真正的供应商卡片内容"},
    ]
    text = tongyi_adapter._compose_answer_segments(segments)
    assert text == "正文\n真正的供应商卡片内容"


def test_extract_response_container_path_includes_card_segments() -> None:
    """_extract_response 走 _ANSWER_EXTRACT_JS 容器路径：卡片段并入正文、
    引用透传、尾部 UI 噪声仍裁剪。"""
    page = _FakePage(messages=0)
    page.answer_visible = True
    page.extract_payload = {
        "segments": [
            {"kind": "markdown", "cls": "qk-markdown", "text": "正文一"},
            {"kind": "widget", "cls": "supplier-card", "text": "供应商卡片：甲公司"},
            {"kind": "markdown", "cls": "qk-markdown", "text": "正文二"},
        ],
        "refs": [{"url": "https://example.com/a", "title": "例", "sitename": None}],
    }
    text, refs = tongyi_adapter._extract_response(page)
    assert text == "正文一\n供应商卡片：甲公司\n正文二"
    assert refs == [{"url": "https://example.com/a", "title": "例", "sitename": None}]


def test_extract_response_falls_back_to_selector_chain_without_js_payload() -> None:
    """evaluate 产出非 dict（JS 异常/结构剧变）→ 回退旧猜测选择器链（.qk-markdown
    已由 JS 路径覆盖，fake 走剩余候选时返回空）。"""
    page = _FakePage(messages=0)
    page.answer_visible = True
    page.extract_payload = "js-broken"  # 非 dict：JS 路径跳过
    # fake 的 locator 只对 .qk-markdown 出元素（JS 路径已覆盖，不在回退链内），
    # 其余猜测候选 inner_text 为 body_text（空）→ 整体落空返回空串
    text, refs = tongyi_adapter._extract_response(page)
    assert text == "" and refs == []


def test_collect_batch_dom_still_growing_after_stream_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-08-07 截断案回归：CDP 判流完成但 DOM 仍持续增长（多阶段流生成段
    走 WebSocket 的场景）→ DOM 稳定门不过 → 该题诚实 incomplete、后续题
    aborted，绝不把截断答案当 live_valid。"""
    page = _FakePage(messages=0)
    page.answer_grows_forever = True  # 每次抽取文本追加后缀：DOM 永不静默
    session = _make_session(tmp_path, monkeypatch, page)
    specs = _batch_specs(2)

    outcomes = session.collect_batch(specs, on_stage=lambda s: None)

    assert [o.status for o in outcomes] == ["incomplete", "aborted"]
    assert outcomes[0].error_type == "answer_capture_incomplete"
    assert outcomes[0].error_message and "dom-still-growing" in outcomes[0].error_message
    assert outcomes[0].answer is None
    assert outcomes[1].error_type == "aborted_after_failure"
    # 存证截图逐题区分（dom_unstable 后缀）
    evidence = tmp_path / "evidence"
    assert (evidence / f"{specs[0].file_stem}-dom_unstable.png").is_file()


def test_collect_batch_stable_dom_after_stream_passes_settle_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """稳定门正常通过路径：流完成 + 文本静默 → 照常成功（回归防过度拦截）。"""
    page = _FakePage(messages=0)
    session = _make_session(tmp_path, monkeypatch, page)
    outcomes = session.collect_batch(_batch_specs(1), on_stage=lambda s: None)
    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].answer is not None
    assert outcomes[0].answer.answer_text == "这是答案"


# ---------------------------------------------------------------------------
# 结构化 trace 证据（20260810，kind="sse"/transport="dom"；思考链/检索词平台
# 未暴露诚实留空——引用卡片折叠为唯一内容，无引用不出空证据）
# ---------------------------------------------------------------------------


def test_collect_batch_persists_trace_when_references_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有引用：{file_stem}-sse-trace.json 落盘（engine=tongyi/transport=dom/
    thinking_chain 诚实留空），trace_path 随 CollectedAnswer 返回。"""
    page = _FakePage(messages=0)
    page.extract_payload = {
        "segments": [{"kind": "markdown", "cls": "qk-markdown", "text": "这是答案"}],
        "refs": [{"url": "https://example.com/a", "title": "例", "sitename": None}],
    }
    session = _make_session(tmp_path, monkeypatch, page)
    spec = _batch_specs(1)[0]

    outcomes = session.collect_batch([spec], on_stage=lambda s: None)

    assert outcomes[0].status == "ok"
    assert outcomes[0].answer is not None
    trace_file = tmp_path / "evidence" / f"{spec.file_stem}-sse-trace.json"
    assert trace_file.is_file()
    assert outcomes[0].answer.trace_path == trace_file
    record = json.loads(trace_file.read_text(encoding="utf-8"))
    assert record["engine"] == "tongyi"
    assert record["transport"] == "dom"
    assert record["deep_think_active"] is False
    assert record["thinking_chain"] == []
    assert record["queries"] == []
    assert [r["url"] for r in record["search_blocks"][0]["results"]] == [
        "https://example.com/a"
    ]


def test_collect_batch_without_references_writes_no_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无引用：trace 不落盘、trace_path=None（无内容不出空证据）。"""
    page = _FakePage(messages=0)  # 默认 extract 路径 refs=[]
    session = _make_session(tmp_path, monkeypatch, page)
    spec = _batch_specs(1)[0]

    outcomes = session.collect_batch([spec], on_stage=lambda s: None)

    assert outcomes[0].status == "ok"
    assert outcomes[0].answer is not None
    assert outcomes[0].answer.trace_path is None
    assert not (tmp_path / "evidence" / f"{spec.file_stem}-sse-trace.json").exists()
