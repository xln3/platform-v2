"""常驻浏览器（跨 run CDP 复用）契约层（2026-08-06 起；当日升级为 DB fencing）。

背景：采集拟人化（W6）+ run 级会话复用（W7）后，豆包风控残留的最后一层
机器指纹是"每个 run 冷启一个全新 Chromium 进程"。真人浏览器是长期开着的——
本模块让适配器 attach 到常驻 Chromium（``--remote-debugging-port``，由
supervisor 管理生命周期），跨 run 复用同一会话。

契约：
- ``GEO_<PLATFORM>_CDP_URL``（如 ``http://127.0.0.1:19222``）非空 →
  ``connect_over_cdp`` attach；未配置 → 回退调用方自带的 launch（旧行为，
  开发/测试与未接常驻的平台不受影响）。2026-08-09 起 ``platform`` 实参可以是
  **实例键**（``doubao_sh`` 等，浏览器矩阵化，见 browser_router.py）：实例键
  优先读 ``GEO_BROWSER_<KEY>_CDP_URL``，未设置才回退 ``GEO_<PLATFORM>_CDP_URL``；
  互斥锁/fence 同样以该 opaque 串为键（``platform.browser_fence`` 键列
  String(80)，实例键直装无需迁移）。
- attach 模式下浏览器**不归适配器关闭**：退出只断开 CDP 连接，不杀进程；
  profile/登录态归 supervisor 所有。
- 平台互斥 = 进程内 ``threading.Lock`` 快速路径 + PG lease fencing
  （``platform.browser_fence``，2026-08-06 起，多 worker 安全）：
  platform 单行唯一，``fencing_token`` 单调递增（含抢占）；持有期间后台
  心跳线程续期（缺省 30s 一拍、TTL 缺省 2h——assist 持锁 ~70min 给足）；
  holder 进程崩溃靠 ``expires_at`` 兜底回收，过期租约可被其他 worker
  抢占（leases 层如实记 ``browser_fence_preempted``）。
- ``GEO_BROWSER_FENCING`` = ``db``（缺省，跨 worker fencing）| ``local``
  （纯进程内锁，零 DB 调用，单 worker 开发/测试用）。db 模式下 DB 不可达
  = **fail-closed** 抛 ``BrowserBusyError``，绝不静默降级为进程内锁。
- holder 标识缺省 ``hostname:pid``，``GEO_BROWSER_FENCE_HOLDER`` 可覆盖；
  TTL/心跳间隔分别由 ``GEO_BROWSER_FENCE_TTL_S`` /
  ``GEO_BROWSER_FENCE_HEARTBEAT_S`` 覆盖。释放校验 holder+fencing_token，
  stale token（租约已被抢占重发）释放如实 warning 不炸、不误删他人租约。
- 断连/崩溃 → 调用方按 browser_launch_failed 诚实重试；supervisor 重启后自愈。
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import structlog

log = structlog.get_logger()

# (context, page, is_resident)
BrowserLease = tuple[Any, Any, bool]

_LOCK_TIMEOUT_S = 30 * 60  # 单 worker 内批排队上限；超时说明上游调度出错
_FENCE_TTL_S = 2 * 3600.0  # assist 会话持锁 ~70min，TTL 给足；崩溃兜底回收窗
_FENCE_HEARTBEAT_S = 30.0  # 持有期间心跳续期间隔
_FENCE_DB_RETRY_S = 1.0  # lease 被其他 worker 持有时的 DB 重试间隔


class BrowserBusyError(RuntimeError):
    """平台浏览器互斥排队超时 / fencing 存储不可用（fail-closed）。

    调度层面本不该发生，如实上报不重试扩散。"""


def resident_cdp_url(platform: str) -> str | None:
    """CDP URL 解析（空串/未设置 → None，回退 launch）。

    浏览器矩阵化（2026-08-09 起）：``platform`` 可以是实例键
    （``doubao_sh`` 等，见 browser_router）——优先读
    ``GEO_BROWSER_<KEY>_CDP_URL``；未设置时回退 ``GEO_<PLATFORM>_CDP_URL``
    （旧平台 slug 直配，per-task 老路径/工具/历史部署行为逐字节不变）。
    错误消息指明实际生效的变量名。
    """
    upper = platform.strip().upper()
    name = f"GEO_BROWSER_{upper}_CDP_URL"
    raw = os.environ.get(name, "").strip()
    if not raw:
        name = f"GEO_{upper}_CDP_URL"
        raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")) or len(raw) > 200:
        raise ValueError(f"{name} is not a valid http(s) URL")
    return raw


def _fencing_mode() -> str:
    """GEO_BROWSER_FENCING：db（缺省）| local。"""
    return os.environ.get("GEO_BROWSER_FENCING", "db").strip().lower() or "db"


def _fence_holder() -> str:
    """GEO_BROWSER_FENCE_HOLDER 覆盖；缺省 hostname:pid（跨 worker 唯一即可）。"""
    return (
        os.environ.get("GEO_BROWSER_FENCE_HOLDER", "").strip()
        or f"{socket.gethostname()}:{os.getpid()}"
    )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("browser_fence_env_invalid", name=name, value=raw, default=default)
        return default
    return value if value > 0 else default


# ── DB fencing seam（测试 monkeypatch 这三个函数，绝不起真 PG） ──────────────────


def _db_fence_acquire(
    platform: str, holder: str, ttl_s: float, timeout_s: float | None
) -> int | None:
    """获取 DB lease（含 busy 重试）：成功 → fencing_token；
    ``timeout_s`` 内一直被他人持有 → None；DB 不可达等故障 → BrowserBusyError。"""
    from geo_platform.collection.leases import LeaseBusyError, acquire_browser_fence
    from geo_platform.tenancy.database import WorkerSessionLocal

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        try:
            with WorkerSessionLocal() as session:
                lease = acquire_browser_fence(
                    session, platform=platform, holder=holder, ttl=timedelta(seconds=ttl_s)
                )
                session.commit()
                return int(lease.fencing_token)
        except LeaseBusyError:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(_FENCE_DB_RETRY_S)
        except Exception as exc:
            raise BrowserBusyError(
                f"browser fence store unavailable for {platform}: {exc}"
            ) from exc


def _db_fence_release(platform: str, holder: str, fencing_token: int) -> bool:
    """归还 DB lease；holder/token 失配（stale）→ False。"""
    from geo_platform.collection.leases import release_browser_fence
    from geo_platform.tenancy.database import WorkerSessionLocal

    with WorkerSessionLocal() as session:
        released = release_browser_fence(
            session, platform=platform, holder=holder, fencing_token=fencing_token
        )
        session.commit()
        return released


def _db_fence_heartbeat(platform: str, holder: str, fencing_token: int, ttl_s: float) -> bool:
    """续期 DB lease；fencing 已丢失（失配/已释放/已过期）→ False。"""
    from geo_platform.collection.leases import heartbeat_browser_fence
    from geo_platform.tenancy.database import WorkerSessionLocal

    with WorkerSessionLocal() as session:
        renewed = heartbeat_browser_fence(
            session,
            platform=platform,
            holder=holder,
            fencing_token=fencing_token,
            ttl=timedelta(seconds=ttl_s),
        )
        session.commit()
        return renewed


class _BrowserFenceLock:
    """平台浏览器复合锁：进程内 ``threading.Lock`` 快速路径 + PG lease fencing。

    调用面兼容 ``threading.Lock`` 的 ``acquire(timeout=...)`` / ``release()``
    （captcha_assist 会话全程持锁依赖此语义）。db 模式下 acquire 的
    timeout 预算是本地锁等待 + DB lease 等待之和；lease 忙 → False，
    DB 故障 → BrowserBusyError（fail-closed）。
    """

    def __init__(self, platform: str) -> None:
        self._platform = platform
        self._local = threading.Lock()
        self._holder: str | None = None
        self._fencing_token: int | None = None
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if _fencing_mode() == "local":
            return self._local.acquire(blocking, timeout)
        budget: float | None = 0.0 if not blocking else (None if timeout < 0 else timeout)
        deadline = None if budget is None else time.monotonic() + budget
        local_timeout = -1 if deadline is None else max(deadline - time.monotonic(), 0.0)
        if not self._local.acquire(True, local_timeout):
            return False
        holder = _fence_holder()
        remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        try:
            token = _db_fence_acquire(
                self._platform,
                holder,
                _env_float("GEO_BROWSER_FENCE_TTL_S", _FENCE_TTL_S),
                remaining,
            )
        except Exception:
            self._local.release()
            raise
        if token is None:
            self._local.release()
            return False
        self._holder = holder
        self._fencing_token = token
        self._start_heartbeat()
        return True

    def release(self) -> None:
        if _fencing_mode() == "local":
            self._local.release()
            return
        holder, token = self._holder, self._fencing_token
        self._holder = None
        self._fencing_token = None
        self._stop_heartbeat()
        try:
            if holder is not None and token is not None:
                if not _db_fence_release(self._platform, holder, token):
                    log.warning(
                        "browser_fence_release_stale",
                        platform=self._platform,
                        holder=holder,
                        fencing_token=token,
                    )
        except Exception as exc:  # 释放失败不炸：本地锁必须解开，DB 租约靠 TTL 回收
            log.warning("browser_fence_release_failed", platform=self._platform, error=str(exc))
        finally:
            self._local.release()

    # ── 心跳（持有期间续期，崩溃后 expires_at 兜底回收） ──────────────────────

    def _start_heartbeat(self) -> None:
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(
                _env_float("GEO_BROWSER_FENCE_HEARTBEAT_S", _FENCE_HEARTBEAT_S),
                _env_float("GEO_BROWSER_FENCE_TTL_S", _FENCE_TTL_S),
            ),
            daemon=True,
            name=f"browser-fence-hb-{self._platform}",
        )
        self._hb_thread.start()

    def _stop_heartbeat(self) -> None:
        self._hb_stop.set()
        thread = self._hb_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._hb_thread = None

    def _heartbeat_loop(self, interval_s: float, ttl_s: float) -> None:
        holder, token = self._holder, self._fencing_token
        while not self._hb_stop.wait(interval_s):
            if holder is None or token is None:
                return
            try:
                if not _db_fence_heartbeat(self._platform, holder, token, ttl_s):
                    log.error(
                        "browser_fence_lost",
                        platform=self._platform,
                        holder=holder,
                        fencing_token=token,
                    )
                    return
            except Exception as exc:  # 瞬时 DB 故障：如实记 log，下一拍再试（TTL 兜底）
                log.warning(
                    "browser_fence_heartbeat_failed", platform=self._platform, error=str(exc)
                )


_LOCKS: dict[str, _BrowserFenceLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(platform: str) -> _BrowserFenceLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(platform, _BrowserFenceLock(platform))


def browser_lock(platform: str) -> _BrowserFenceLock:
    """平台互斥锁的公共句柄：captcha assist 会话在人工接管期间持锁，
    防止另一个 worker/run 的 batch attach 同一常驻浏览器抢走页面。
    调用面兼容 threading.Lock（``acquire(timeout=...)`` / ``release()``）。"""
    return _lock_for(platform)


@contextmanager
def platform_browser(
    pw: Any,
    *,
    platform: str,
    launch: Callable[[], tuple[Any, Any]],
) -> Iterator[BrowserLease]:
    """attach-or-launch + 平台互斥锁（进程内 + DB fencing）。

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
