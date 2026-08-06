"""human_like 拟人化原语单元测试：seeded 确定性、节奏边界、贝塞尔端点精确。

全部走 fake page/locator（记录事件序列），绝不启动真浏览器。
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from workflows.activities.human_like import (
    _PUNCT_PAUSE_MAX_MS,
    _READ_PAUSE_MAX_S,
    _READ_PAUSE_MIN_S,
    _READ_SCROLL_MAX,
    _READ_SCROLL_MAX_PX,
    _READ_SCROLL_MIN,
    _READ_SCROLL_MIN_PX,
    _READ_SCROLL_PAUSE_MAX_S,
    _READ_SCROLL_PAUSE_MIN_S,
    _TYPE_INTERVAL_MAX_MS,
    _TYPE_INTERVAL_MIN_MS,
    build_trajectory,
    human_click,
    human_move_to,
    human_pause,
    human_read_pause,
    human_type,
    max_deviation_from_line,
    velocity_profile,
)


class _RecMouse:
    def __init__(self, events: list[tuple]) -> None:
        self._events = events

    def move(self, x: float, y: float, **_kw: Any) -> None:
        self._events.append(("mouse_move", round(float(x), 4), round(float(y), 4)))

    def click(self, x: float, y: float, **kw: Any) -> None:
        event = ("mouse_click", round(float(x), 4), round(float(y), 4), kw.get("delay"))
        self._events.append(event)

    def wheel(self, dx: float, dy: float, **_kw: Any) -> None:
        self._events.append(("wheel", round(float(dx), 4), round(float(dy), 4)))

    def down(self, **_kw: Any) -> None:
        self._events.append(("mouse_down",))

    def up(self, **_kw: Any) -> None:
        self._events.append(("mouse_up",))


class _RecKeyboard:
    def __init__(self, events: list[tuple]) -> None:
        self._events = events

    def type(self, text: str, **_kw: Any) -> None:
        self._events.append(("key", text))

    def press(self, key: str, **_kw: Any) -> None:
        self._events.append(("press", key))


class _RecPage:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.mouse = _RecMouse(self.events)
        self.keyboard = _RecKeyboard(self.events)
        self.viewport_size = {"width": 1280, "height": 720}

    def wait_for_timeout(self, timeout: float) -> None:
        self.events.append(("wait", timeout))


class _RecLocator:
    def __init__(self, page: _RecPage, bb: dict[str, float] | None) -> None:
        self._page = page
        self._bb = bb

    @property
    def page(self) -> _RecPage:
        return self._page

    def scroll_into_view_if_needed(self, timeout: int | None = None) -> None:
        self._page.events.append(("scroll", timeout))

    def bounding_box(self) -> dict[str, float] | None:
        return self._bb

    def click(self, **kw: Any) -> None:
        self._page.events.append(("locator_click", kw))

    def focus(self) -> None:
        self._page.events.append(("focus",))


_BB = {"x": 100.0, "y": 100.0, "width": 40.0, "height": 20.0}


def test_build_trajectory_deterministic_and_endpoints_exact() -> None:
    start, end = (10.0, 20.0), (400.0, 300.0)
    traj_a = build_trajectory(start, end, rng=random.Random(7), allow_retract=False)
    traj_b = build_trajectory(start, end, rng=random.Random(7), allow_retract=False)
    assert traj_a == traj_b  # seeded 确定性
    first, last = traj_a[0], traj_a[-1]
    assert (first.x, first.y) == start  # 端点精确
    assert (last.x, last.y) == end
    assert 5 <= len(traj_a) <= 81  # sample_count+1，界内
    times = [p.t_ms for p in traj_a]
    assert times == sorted(times)  # 时间单调
    assert 400.0 <= times[-1] <= 1200.0


def test_build_trajectory_bows_off_straight_line_with_bell_velocity() -> None:
    start, end = (0.0, 0.0), (600.0, 0.0)
    traj = build_trajectory(start, end, rng=random.Random(11), allow_retract=False)
    # 控制点垂直偏移 15-40px：轨迹必须明显弯离直线（直线匀速=机器人指纹）
    assert max_deviation_from_line(traj, start, end) > 1.0
    speeds = velocity_profile(traj)
    assert speeds and min(speeds) < max(speeds)  # 速度非常数（钟形）
    # 中间样本带抖动：至少一个样本不精确落在直线上（y != 0）
    assert any(abs(p.y) > 1e-9 for p in traj[1:-1])


def test_human_move_to_samples_path_and_lands_exactly() -> None:
    page = _RecPage()
    pos = human_move_to(page, 500.0, 300.0, random.Random(3), start=(100.0, 100.0))
    assert pos == (500.0, 300.0)
    moves = [e for e in page.events if e[0] == "mouse_move"]
    assert len(moves) >= 5  # 多样本贝塞尔，绝不瞬移
    assert (moves[0][1], moves[0][2]) == (100.0, 100.0)  # 起点精确
    assert (moves[-1][1], moves[-1][2]) == (500.0, 300.0)  # 落点精确
    total_wait = sum(e[1] for e in page.events if e[0] == "wait")
    assert 400.0 <= total_wait <= 1200.0  # 轨迹时长界内
    page2 = _RecPage()
    human_move_to(page2, 500.0, 300.0, random.Random(3), start=(100.0, 100.0))
    assert page.events == page2.events  # 同种子同序列


def test_human_click_moves_hovers_then_clicks_inside_element() -> None:
    page = _RecPage()
    loc = _RecLocator(page, dict(_BB))
    clicked = human_click(loc, page, random.Random(5), start=(50.0, 50.0))
    assert clicked is not None
    tx, ty = clicked
    # 元素内 25%-75% 安全边带随机偏移
    assert 110.0 <= tx <= 130.0
    assert 105.0 <= ty <= 115.0
    events = page.events
    click_idx = next(i for i, e in enumerate(events) if e[0] == "mouse_click")
    click = events[click_idx]
    assert click[1] == pytest.approx(tx)
    assert click[2] == pytest.approx(ty)
    assert 30 <= click[3] <= 90  # 人间 hold delay
    # 点击前必有贝塞尔移动，且最后一步移动落在点击点
    moves_before = [e for e in events[:click_idx] if e[0] == "mouse_move"]
    assert len(moves_before) >= 5
    assert moves_before[-1][1] == pytest.approx(tx)
    assert moves_before[-1][2] == pytest.approx(ty)
    # 到位后悬停 80-300ms（点击前最后一个等待）
    waits_before = [e[1] for e in events[:click_idx] if e[0] == "wait"]
    assert 80.0 <= waits_before[-1] <= 300.0
    assert ("scroll", 1_500) in events  # 先 scroll_into_view
    # 同种子完全复现
    page2 = _RecPage()
    human_click(_RecLocator(page2, dict(_BB)), page2, random.Random(5), start=(50.0, 50.0))
    assert page2.events == events


def test_human_click_falls_back_to_native_click_without_layout() -> None:
    page = _RecPage()
    loc = _RecLocator(page, None)  # 无布局（detached/隐藏）
    clicked = human_click(loc, page, random.Random(5), click_kwargs={"timeout": 4_000})
    assert clicked is None
    assert ("locator_click", {"timeout": 4_000}) in page.events  # kwargs 原样透传
    assert not [e for e in page.events if e[0] == "mouse_click"]


def test_human_type_per_char_events_with_human_timing() -> None:
    text = "重疾险，包括哪些？"
    page = _RecPage()
    human_type(_RecLocator(page, dict(_BB)), text, random.Random(42))
    keys = [e[1] for e in page.events if e[0] == "key"]
    assert keys == list(text)  # 逐字事件数 == 字符数，内容零污染
    assert ("focus",) in page.events
    waits = [e[1] for e in page.events if e[0] == "wait"]
    assert len(waits) == len(text)  # 每字一个间隔
    lo = _TYPE_INTERVAL_MIN_MS
    hi = _TYPE_INTERVAL_MAX_MS + _PUNCT_PAUSE_MAX_MS
    assert all(lo <= w <= hi for w in waits)
    # 同种子完全复现
    page2 = _RecPage()
    human_type(_RecLocator(page2, dict(_BB)), text, random.Random(42))
    assert page2.events == page.events


def test_human_type_punctuation_pauses_occur_but_not_always() -> None:
    text = "你好，世界。 ok"
    runs_with_pause = 0
    for seed in range(30):
        page = _RecPage()
        human_type(_RecLocator(page, dict(_BB)), text, random.Random(seed))
        waits = [e[1] for e in page.events if e[0] == "wait"]
        if any(w > _TYPE_INTERVAL_MAX_MS for w in waits):
            runs_with_pause += 1
    # 15% 概率/标点：30 个种子里必有一些触发、一些不触发（确定性断言）
    assert 0 < runs_with_pause < 30


def test_human_pause_uses_injected_sleeper_and_returns_seconds() -> None:
    slept: list[float] = []
    seconds = human_pause(random.Random(1), 0.5, 1.5, sleep=slept.append)
    assert slept == [seconds]
    assert 0.5 <= seconds <= 1.5


def test_human_read_pause_scrolls_then_lingers() -> None:
    """阅读停顿：向下滚动 2-5 次（240-720px、间隔 0.4-1.2s）+ 停留 8-25s；
    等待走 page.wait_for_timeout（留在页面事件序列）；seeded 确定性。"""
    page = _RecPage()
    total = human_read_pause(page, random.Random(11))
    wheels = [e for e in page.events if e[0] == "wheel"]
    assert _READ_SCROLL_MIN <= len(wheels) <= _READ_SCROLL_MAX
    for _, dx, dy in wheels:
        assert dx == 0.0  # 只向下滚动
        assert _READ_SCROLL_MIN_PX <= dy <= _READ_SCROLL_MAX_PX
    waits = [e[1] for e in page.events if e[0] == "wait"]
    # 每次滚动后一个滚动间隔（秒→ms），最后一个等待是 8-25s 停留
    assert len(waits) == len(wheels) + 1
    for w in waits[:-1]:
        assert _READ_SCROLL_PAUSE_MIN_S * 1000 <= w <= _READ_SCROLL_PAUSE_MAX_S * 1000
    assert _READ_PAUSE_MIN_S * 1000 <= waits[-1] <= _READ_PAUSE_MAX_S * 1000
    # 返回总耗时（秒）：= 滚动间隔和 + 末尾停留（waits 为 int 毫秒截断，误差 <1ms/段）
    wait_seconds = sum(w / 1000 for w in waits)
    assert 0.0 <= total - wait_seconds <= len(waits) * 0.001
    # 滚动与等待交替出现、全部滚动在停留之前
    kinds = [e[0] for e in page.events]
    assert kinds[-1] == "wait"
    assert all(kinds[i] == "wheel" for i in range(0, 2 * len(wheels), 2))
    # 同种子完全复现
    page2 = _RecPage()
    assert human_read_pause(page2, random.Random(11)) == total
    assert page2.events == page.events


def test_human_read_pause_accepts_injected_sleeper() -> None:
    """sleep 可注入（测试/无 page 场景），wheel 仍落在 page 事件序列。"""
    page = _RecPage()
    slept: list[float] = []
    human_read_pause(page, random.Random(3), sleep=slept.append)
    wheels = [e for e in page.events if e[0] == "wheel"]
    assert _READ_SCROLL_MIN <= len(wheels) <= _READ_SCROLL_MAX
    assert not [e for e in page.events if e[0] == "wait"]  # 等待全走注入 sleeper
    assert len(slept) == len(wheels) + 1
    assert _READ_PAUSE_MIN_S <= slept[-1] <= _READ_PAUSE_MAX_S
