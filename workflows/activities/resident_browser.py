"""常驻浏览器（跨 run CDP 复用）契约层（2026-08-06 起）。

背景：采集拟人化（W6）+ run 级会话复用（W7）后，豆包风控残留的最后一层
机器指纹是"每个 run 冷启一个全新 Chromium 进程"。真人浏览器是长期开着的——
本模块让适配器 attach 到常驻 Chromium（``--remote-debugging-port``，由
supervisor 管理生命周期），跨 run 复用同一会话。

契约：
- ``GEO_<PLATFORM>_CDP_URL``（如 ``http://127.0.0.1:19222``）非空 →
  ``connect_over_cdp`` attach；未配置 → 回退调用方自带的 launch（旧行为，
  开发/测试与未接常驻的平台不受影响）。
- attach 模式下浏览器**不归适配器关闭**：退出只断开 CDP 连接，不杀进程；
  profile/登录态归 supervisor 所有。
- 每平台一把进程级互斥锁：单 worker 内并发 batch 不得同时操作同一浏览器
  （多 worker 部署时需升级为 DB fencing，届时改这里一处即可）。
- 断连/崩溃 → 调用方按 browser_launch_failed 诚实重试；supervisor 重启后自愈。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

# (context, page, is_resident)
BrowserLease = tuple[Any, Any, bool]

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCK_TIMEOUT_S = 30 * 60  # 单 worker 内批排队上限；超时说明上游调度出错


def resident_cdp_url(platform: str) -> str | None:
    """GEO_<PLATFORM>_CDP_URL（空串/未设置 → None，回退 launch）。"""
    raw = os.environ.get(f"GEO_{platform.upper()}_CDP_URL", "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")) or len(raw) > 200:
        raise ValueError(f"GEO_{platform.upper()}_CDP_URL is not a valid http(s) URL")
    return raw


def _lock_for(platform: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(platform, threading.Lock())


class BrowserBusyError(RuntimeError):
    """平台浏览器互斥锁排队超时（调度层面本不该发生，如实上报不重试扩散）。"""


@contextmanager
def platform_browser(
    pw: Any,
    *,
    platform: str,
    launch: Callable[[], tuple[Any, Any]],
) -> Iterator[BrowserLease]:
    """attach-or-launch + 平台互斥锁。

    ``launch`` 仅在未配置 CDP URL 时调用，返回 (context, page)；其打开与
    profile 崩溃清理仍归调用方（launch 路径的全部语义不变）。
    用法::

        with platform_browser(pw, platform="doubao", launch=_launch) as (ctx, page, resident):
            ...  # 导航/采集；resident=True 时浏览器在退出后存活
    """
    lock = _lock_for(platform)
    if not lock.acquire(timeout=_LOCK_TIMEOUT_S):
        raise BrowserBusyError(f"platform browser busy: {platform}")
    try:
        cdp_url = resident_cdp_url(platform)
        if cdp_url:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            try:
                if not browser.contexts:
                    raise RuntimeError(
                        f"resident browser has no default context: {platform} ({cdp_url})"
                    )
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                yield context, page, True
            finally:
                # 只断开 CDP 连接——常驻浏览器进程归 supervisor，绝不在此关闭。
                browser.close()
        else:
            context, page = launch()
            try:
                yield context, page, False
            finally:
                context.close()
    finally:
        lock.release()
