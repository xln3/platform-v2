"""拟人化交互原语（平台无关，sync Playwright）。

从旧链 ``server/proxyllm/humanlike_drag.py``（滑块验证码对抗实证）移植并泛化为
采集驱动统一的页面交互层。指纹口径：

- 逐字真实键盘事件：绝不 ``insert_text``/``fill`` 注入正文。CJK 字符经 Playwright
  逐字 input 事件下发（与旧链 ``locator.type()`` 同事件形态，仅节奏从固定 delay
  改为人间分布）；不打错字——查询文本不可污染（错字会改变测量对象本身）。
- 鼠标绝不瞬移：点击前先沿三次贝塞尔曲线移动（ease-in-out 速度钟形 + 逐样本
  高斯抖动 + 10% 末尾 overshoot/retract），到位悬停后再按下；点击带 30-90ms
  人间 hold。
- 节奏全部随机化且 RNG 可注入：测试 seeded 确定性，生产真随机。
- 等待尽量走 ``page.wait_for_timeout``（可读、可 fake）；无 page 场景的
  ``human_pause`` 接受注入 sleeper（缺省 ``time.sleep``）。

公共 API：
  - ``build_trajectory``  纯函数贝塞尔轨迹采样（可离线测试）
  - ``human_move_to``     贝塞尔移动到绝对坐标，返回落点（供调用方追踪光标）
  - ``human_click``       移动 + 悬停 + 元素内随机偏移点击
  - ``human_type``        逐字键盘输入
  - ``human_pause``       统一节奏等待
  - ``human_read_pause``  阅读停顿（滚动浏览 + 停留，batch 题间用）
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# 轨迹参数（旧链滑块实证值，勿轻调：直线/匀速/零抖动会被行为风控近 100% 识别）
_DURATION_MEAN_MS = 700.0
_DURATION_STD_MS = 150.0
_DURATION_MIN_MS = 400.0
_DURATION_MAX_MS = 1200.0
_SAMPLE_MIN = 40
_SAMPLE_MAX = 80
_CTRL_OFFSET_MIN_PX = 15.0
_CTRL_OFFSET_MAX_PX = 40.0
_JITTER_STD_PX = 0.8
_RETRACT_PROBABILITY = 0.10
_RETRACT_OVERSHOOT_MIN_PX = 3.0
_RETRACT_OVERSHOOT_MAX_PX = 8.0

# 打字节奏：基线逐字间隔 40-140ms；标点/空格后 15% 概率追加 250-800ms 停顿
_TYPE_INTERVAL_MIN_MS = 40.0
_TYPE_INTERVAL_MAX_MS = 140.0
_PUNCT_PAUSE_PROBABILITY = 0.15
_PUNCT_PAUSE_MIN_MS = 250.0
_PUNCT_PAUSE_MAX_MS = 800.0
_PAUSE_AFTER_CHARS = frozenset(" \t\n，。！？；：、,.!?;:…—「」“”‘’（）()《》")

# 点击：元素内随机偏移的安全边带（避开边缘，落在 25%-75% 区域）
_CLICK_BAND_MIN = 0.25
_CLICK_BAND_MAX = 0.75
_CLICK_HOVER_MIN_S = 0.08
_CLICK_HOVER_MAX_S = 0.30
_CLICK_HOLD_MIN_MS = 30
_CLICK_HOLD_MAX_MS = 90

# 阅读停顿（batch 题间）：真人读完回答会滚动浏览并停留。滚动 2-5 次
# （每次 240-720px 向下、间隔 0.4-1.2s），再停留 8-25s 抖动——既是题间
# 天然间隔，也产出真实浏览信号（纯发送零浏览的会话结构是机器人指纹）。
_READ_SCROLL_MIN = 2
_READ_SCROLL_MAX = 5
_READ_SCROLL_MIN_PX = 240.0
_READ_SCROLL_MAX_PX = 720.0
_READ_SCROLL_PAUSE_MIN_S = 0.4
_READ_SCROLL_PAUSE_MAX_S = 1.2
_READ_PAUSE_MIN_S = 8.0
_READ_PAUSE_MAX_S = 25.0

Point = tuple[float, float]


@dataclass(frozen=True)
class TrajectoryPoint:
    t_ms: float
    x: float
    y: float


def _ease_in_out(u: float) -> float:
    # 正弦 smoothstep：f(0)=0, f(1)=1，两端导数为 0（慢起步、中段最快、慢收尾）。
    return (1 - math.cos(math.pi * u)) / 2


def _cubic_bezier(
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    t: float,
) -> Point:
    one_minus = 1.0 - t
    bx = (
        one_minus**3 * p0[0]
        + 3 * one_minus**2 * t * p1[0]
        + 3 * one_minus * t**2 * p2[0]
        + t**3 * p3[0]
    )
    by = (
        one_minus**3 * p0[1]
        + 3 * one_minus**2 * t * p1[1]
        + 3 * one_minus * t**2 * p2[1]
        + t**3 * p3[1]
    )
    return bx, by


def _control_points(start: Point, end: Point, rng: random.Random) -> tuple[Point, Point]:
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy) or 1.0
    # 单位法向量：(dx, dy) 逆时针转 90° 再归一。
    perp_x = -dy / length
    perp_y = dx / length
    sign1 = rng.choice([-1.0, 1.0])
    sign2 = rng.choice([-1.0, 1.0])
    off1 = rng.uniform(_CTRL_OFFSET_MIN_PX, _CTRL_OFFSET_MAX_PX) * sign1
    off2 = rng.uniform(_CTRL_OFFSET_MIN_PX, _CTRL_OFFSET_MAX_PX) * sign2
    c1 = (sx + dx / 3.0 + perp_x * off1, sy + dy / 3.0 + perp_y * off1)
    c2 = (sx + 2 * dx / 3.0 + perp_x * off2, sy + 2 * dy / 3.0 + perp_y * off2)
    return c1, c2


def build_trajectory(
    start: Point,
    end: Point,
    *,
    rng: random.Random | None = None,
    duration_ms: float | None = None,
    sample_count: int | None = None,
    allow_retract: bool = True,
) -> list[TrajectoryPoint]:
    """采样一条 start→end 的拟人鼠标轨迹（纯函数，可离线测试）。

    端点精确落在 start 与轨迹定义的终点（无 retract 时即 end）；中间样本带
    亚像素高斯抖动，速度呈钟形（ease-in-out）。
    """
    rng = rng or random.Random()
    if duration_ms is None:
        duration_ms = max(
            _DURATION_MIN_MS,
            min(_DURATION_MAX_MS, rng.gauss(_DURATION_MEAN_MS, _DURATION_STD_MS)),
        )
    if sample_count is None:
        sample_count = rng.randint(_SAMPLE_MIN, _SAMPLE_MAX)
    sample_count = max(sample_count, 5)

    c1, c2 = _control_points(start, end, rng)

    points: list[TrajectoryPoint] = []
    for i in range(sample_count + 1):
        u = i / sample_count
        t_eased = _ease_in_out(u)
        bx, by = _cubic_bezier(start, c1, c2, end, t_eased)
        if 0 < i < sample_count:
            bx += rng.gauss(0.0, _JITTER_STD_PX)
            by += rng.gauss(0.0, _JITTER_STD_PX)
        # 墙钟在均匀采样上线性推进；被 ease 的是贝塞尔参数，由此给出速度钟形。
        points.append(TrajectoryPoint(t_ms=duration_ms * u, x=bx, y=by))

    if allow_retract and rng.random() < _RETRACT_PROBABILITY:
        sx, sy = start
        ex, ey = end
        dx, dy = ex - sx, ey - sy
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        overshoot = rng.uniform(_RETRACT_OVERSHOOT_MIN_PX, _RETRACT_OVERSHOOT_MAX_PX)
        ox, oy = ex + ux * overshoot, ey + uy * overshoot
        last_t = points[-1].t_ms
        retract_dur = rng.uniform(60.0, 140.0)
        points.append(TrajectoryPoint(last_t + retract_dur * 0.3, ox, oy))
        # overshoot 处短暂停顿——真人修正前会顿一下。
        points.append(TrajectoryPoint(last_t + retract_dur * 0.55, ox, oy))
        mx, my = (ox + ex) / 2.0, (oy + ey) / 2.0
        points.append(TrajectoryPoint(last_t + retract_dur * 0.8, mx, my))
        points.append(TrajectoryPoint(last_t + retract_dur, ex, ey))

    return points


def max_deviation_from_line(
    points: list[TrajectoryPoint],
    start: Point,
    end: Point,
) -> float:
    """所有样本到 start→end 直线的最大垂直距离（证明轨迹确实弯离直线）。"""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy) or 1.0
    worst = 0.0
    for p in points:
        cross = (p.x - sx) * dy - (p.y - sy) * dx
        d = abs(cross) / length
        if d > worst:
            worst = d
    return worst


def velocity_profile(points: list[TrajectoryPoint]) -> list[float]:
    """相邻样本间的瞬时速度（px/ms）——钟形速度曲线的可测证据。"""
    speeds: list[float] = []
    for prev, cur in zip(points, points[1:], strict=False):
        dt = cur.t_ms - prev.t_ms
        if dt <= 0:
            continue
        dist = math.hypot(cur.x - prev.x, cur.y - prev.y)
        speeds.append(dist / dt)
    return speeds


def _synthesize_start(page: Any, target: Point, rng: random.Random) -> Point:
    """当前光标位置未知时，在目标附近合成合理起点（真人发起点击前的初始位移
    通常是短距的）；坐标夹进 viewport。"""
    vw, vh = 1280.0, 720.0
    try:
        size = page.viewport_size
        if size:
            vw = float(size.get("width") or vw)
            vh = float(size.get("height") or vh)
    except Exception:
        pass
    angle = rng.uniform(0.0, 2.0 * math.pi)
    radius = min(max(abs(rng.gauss(140.0, 70.0)), 40.0), 400.0)
    sx = min(max(target[0] + math.cos(angle) * radius, 0.0), vw - 1.0)
    sy = min(max(target[1] + math.sin(angle) * radius, 0.0), vh - 1.0)
    return sx, sy


def human_move_to(
    page: Any,
    x: float,
    y: float,
    rng: random.Random,
    *,
    start: Point | None = None,
) -> Point:
    """沿贝塞尔轨迹把鼠标移到 (x, y)，逐样本发 mousemove。返回落点坐标。

    ``start`` 是已知的当前光标位置（调用方追踪）；缺省在目标附近合成起点。
    """
    target = (float(x), float(y))
    origin = start if start is not None else _synthesize_start(page, target, rng)
    points = build_trajectory(origin, target, rng=rng)
    prev_t = 0.0
    for p in points:
        delta = max(0.0, p.t_ms - prev_t)
        prev_t = p.t_ms
        if delta > 0:
            page.wait_for_timeout(delta)
        page.mouse.move(p.x, p.y)
    return target


def human_click(
    locator: Any,
    page: Any,
    rng: random.Random,
    *,
    start: Point | None = None,
    hover_s: tuple[float, float] = (_CLICK_HOVER_MIN_S, _CLICK_HOVER_MAX_S),
    click_kwargs: dict[str, Any] | None = None,
) -> Point | None:
    """贝塞尔移动 → 悬停 → 元素内随机偏移点击。返回实际点击坐标。

    拿不到元素布局（detached/隐藏）时回退 Playwright 原生 ``locator.click()``
    （真实鼠标事件，仅缺贝塞尔前奏；``click_kwargs`` 原样透传），返回 None；
    原生点击失败原样抛出（诚实失败，不吞）。
    """
    try:
        locator.scroll_into_view_if_needed(timeout=1_500)
    except Exception:
        pass
    try:
        bb = locator.bounding_box()
    except Exception:
        bb = None
    if not bb:
        locator.click(**(click_kwargs or {}))
        return None
    tx = bb["x"] + bb["width"] * rng.uniform(_CLICK_BAND_MIN, _CLICK_BAND_MAX)
    ty = bb["y"] + bb["height"] * rng.uniform(_CLICK_BAND_MIN, _CLICK_BAND_MAX)
    human_move_to(page, tx, ty, rng, start=start)
    page.wait_for_timeout(rng.uniform(hover_s[0], hover_s[1]) * 1000)
    page.mouse.click(tx, ty, delay=rng.randint(_CLICK_HOLD_MIN_MS, _CLICK_HOLD_MAX_MS))
    return tx, ty


def human_type(locator: Any, text: str, rng: random.Random) -> None:
    """逐字真实键盘输入。

    基线逐字间隔 40-140ms 抖动；标点/空格后 15% 概率追加 250-800ms 停顿
    （真人在语义边界处会自然减速）。不打错字——查询文本不可污染。
    """
    page = locator.page
    try:
        locator.focus()
    except Exception:
        pass  # 调用方通常已 human_click 聚焦；失败由后续清空校验诚实兜底
    for ch in text:
        page.keyboard.type(ch)
        delay_ms = rng.uniform(_TYPE_INTERVAL_MIN_MS, _TYPE_INTERVAL_MAX_MS)
        if ch in _PAUSE_AFTER_CHARS and rng.random() < _PUNCT_PAUSE_PROBABILITY:
            delay_ms += rng.uniform(_PUNCT_PAUSE_MIN_MS, _PUNCT_PAUSE_MAX_MS)
        page.wait_for_timeout(delay_ms)


def human_pause(
    rng: random.Random,
    lo_s: float,
    hi_s: float,
    *,
    sleep: Callable[[float], Any] = time.sleep,
) -> float:
    """统一节奏等待：uniform(lo_s, hi_s) 秒。返回实际等待秒数。

    sleeper 可注入：测试传记录型假 sleeper，适配器传 ``page.wait_for_timeout``
    包装（让停顿留在页面事件序列里）。
    """
    seconds = rng.uniform(lo_s, hi_s)
    sleep(seconds)
    return seconds


def human_read_pause(
    page: Any,
    rng: random.Random,
    *,
    sleep: Callable[[float], Any] | None = None,
) -> float:
    """拟人阅读停顿：向下滚动 2-5 次（每次 240-720px、间隔 0.4-1.2s），
    再停留 8-25s 抖动。返回总耗时的近似秒数（滚动间隔 + 末尾停留）。

    真人读完回答会滚动浏览再停留——这是 batch 题间的天然间隔，也产出真实
    浏览信号。等待默认走 ``page.wait_for_timeout``（留在页面事件序列里、
    可 fake）；``sleep`` 可注入（测试传记录型假 sleeper）。
    """
    if sleep is None:

        def sleep(seconds: float) -> Any:
            return page.wait_for_timeout(int(seconds * 1000))

    total = 0.0
    for _ in range(rng.randint(_READ_SCROLL_MIN, _READ_SCROLL_MAX)):
        page.mouse.wheel(0.0, rng.uniform(_READ_SCROLL_MIN_PX, _READ_SCROLL_MAX_PX))
        total += human_pause(
            rng, _READ_SCROLL_PAUSE_MIN_S, _READ_SCROLL_PAUSE_MAX_S, sleep=sleep
        )
    total += human_pause(rng, _READ_PAUSE_MIN_S, _READ_PAUSE_MAX_S, sleep=sleep)
    return total
