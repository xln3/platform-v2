"""常驻浏览器 launcher（W8：跨 run CDP 复用的 supervisor 侧，2026-08-06 起）。

真人浏览器长期开着，采集 attach 而不是冷启动——本进程就是那个长期开着的
浏览器：patchright ``launch_persistent_context`` 带 ``--remote-debugging-port``
启动 headed Chromium，打 CDP URL 与健康日志后阻塞保活（定期 ``page.title``
探活），SIGTERM/SIGINT 时 ``context.close()`` 优雅退出（写回 exit_type=Normal）。

- 由 systemd 模板单元 ``geo-platform-v2-browser@.service`` 拉起（2026-08-09 起
  浏览器矩阵化：一实例一进程，实例 = 平台 × 地域 × 账号；env 文件
  ``/etc/geo-platform-v2/browser-<实例键>.env``，示例在同目录
  ``browser-doubao_sh.env.example``；RESIDENT_PLATFORM 填实例键）。
- 采集侧（worker）batch 经 ``browser_router`` 按 (adapter, region) 路由到实例，
  配置 ``GEO_BROWSER_<KEY>_CDP_URL=http://127.0.0.1:<port>`` 后 attach
  （契约层 ``workflows/activities/resident_browser.py``）；本进程独占
  profile 目录与 CDP 端口，worker 侧绝不再 launch 同一 profile。
- 失败即死（profile 锁占用/端口占用/浏览器崩溃/探活失败）→ 非零退出，
  systemd ``Restart=always`` 兜底重启。重启期间采集 attach 断连，按
  browser-launch-failed 诚实重试后自愈。

env（``RESIDENT_*``）见 ``ResidentBrowserConfig.from_env`` 的校验逻辑。
"""

from __future__ import annotations

import os
import re
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from domain.security.redaction import safe_exception_summary
from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.doubao_adapter import (
    _USER_AGENT,
    _clean_profile_crash_state,
    _parse_proxy,
    mask_proxy_url,
)

log = structlog.get_logger()

ENV_PLATFORM = "RESIDENT_PLATFORM"
ENV_PROFILE_DIR = "RESIDENT_PROFILE_DIR"
ENV_PROXY_URL = "RESIDENT_PROXY_URL"
ENV_CDP_PORT = "RESIDENT_CDP_PORT"
ENV_DISPLAY = "RESIDENT_DISPLAY"

_DEFAULT_DISPLAY = ":1"
_HEALTH_INTERVAL_S = 60.0
_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class ResidentBrowserConfig:
    """常驻实例配置。proxy_url 原文只在启动浏览器时使用，日志只落打码值。"""

    platform: str
    profile_dir: Path
    proxy_url: str | None
    cdp_port: int
    display: str = _DEFAULT_DISPLAY
    health_interval_s: float = _HEALTH_INTERVAL_S

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> ResidentBrowserConfig:
        """解析并校验 RESIDENT_* env；任何缺失/非法 → ValueError（调用方转退出码 2）。

        profile 目录必须已存在（fail-closed：路径打错时绝不静默新建空 profile
        丢掉登录态——新建平台 profile 请先人工 mkdir）。
        """
        env = os.environ if environ is None else environ
        platform = env.get(ENV_PLATFORM, "").strip()
        if not platform:
            raise ValueError(f"{ENV_PLATFORM} is not set (platform slug, e.g. doubao)")
        if not _PLATFORM_RE.match(platform):
            raise ValueError(f"{ENV_PLATFORM} is not a valid platform slug: {platform!r}")
        raw_profile = env.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ValueError(f"{ENV_PROFILE_DIR} is not set (persistent browser profile dir)")
        profile_dir = Path(raw_profile)
        if not profile_dir.is_dir():
            raise ValueError(
                f"{ENV_PROFILE_DIR} is not an existing directory: {profile_dir} "
                "(create it by hand first — never auto-create a fresh profile)"
            )
        proxy_url = env.get(ENV_PROXY_URL, "").strip() or None
        if proxy_url is not None and _parse_proxy(proxy_url) is None:
            raise ValueError(
                f"{ENV_PROXY_URL} is not a valid proxy URL "
                "(expected scheme://[user:pass@]host:port)"
            )
        raw_port = env.get(ENV_CDP_PORT, "").strip()
        if not raw_port:
            raise ValueError(f"{ENV_CDP_PORT} is not set (e.g. 19222)")
        try:
            cdp_port = int(raw_port)
        except ValueError:
            raise ValueError(f"{ENV_CDP_PORT} is not an integer: {raw_port!r}") from None
        if not 1024 <= cdp_port <= 65535:
            raise ValueError(f"{ENV_CDP_PORT} must be within [1024, 65535]: {cdp_port}")
        display = env.get(ENV_DISPLAY, "").strip() or _DEFAULT_DISPLAY
        return cls(
            platform=platform,
            profile_dir=profile_dir,
            proxy_url=proxy_url,
            cdp_port=cdp_port,
            display=display,
        )

    @property
    def cdp_url(self) -> str:
        """采集侧 attach 用的 CDP URL（仅监听本机回环——CDP 无鉴权，绝不绑外网）。"""
        return f"http://127.0.0.1:{self.cdp_port}"


def _close_gracefully(context: Any, *, platform: str) -> None:
    """优雅关闭：异常如实记日志（吞掉=浏览器被强杀=profile 留崩溃标记）。"""
    try:
        context.close()
    except Exception as exc:
        log.warning(
            "resident_browser_close_failed",
            platform=platform,
            error=safe_exception_summary(exc),
        )


def run_resident_browser(
    config: ResidentBrowserConfig,
    *,
    driver_loader: Callable[[], tuple[str, Any, Any]] = load_sync_browser_driver,
    stop_event: threading.Event | None = None,
) -> int:
    """启动常驻浏览器并阻塞保活。返回进程退出码（0=优雅退出，1=故障）。

    ``stop_event`` 缺省时自行创建并挂 SIGTERM/SIGINT（优雅退出语义）；
    测试注入自己的 event 以驱动退出路径。
    """
    driver, sync_playwright, _pw_timeout = driver_loader()
    bound = log.bind(platform=config.platform, cdp_url=config.cdp_url, driver=driver)
    # headed Chromium 需要 DISPLAY（env 文件权威；缺省 :1，本机 GNOME 桌面）。
    os.environ["DISPLAY"] = config.display

    own_stop = stop_event is None
    stop = stop_event if stop_event is not None else threading.Event()
    old_handlers: dict[int, Any] = {}
    if own_stop:

        def _handle_signal(signum: int, _frame: Any) -> None:
            bound.info("resident_browser_signal", signum=signum)
            stop.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                old_handlers[signum] = signal.signal(signum, _handle_signal)
            except ValueError:
                pass  # 非主线程（防御；生产/测试都在主线程）

    try:
        # 启动前愈合前任进程的崩溃标记（SIGKILL/断电会绕过优雅 close）。
        try:
            healed = _clean_profile_crash_state(config.profile_dir)
        except Exception as exc:
            healed = False
            bound.warning("resident_browser_crash_clean_failed", error=safe_exception_summary(exc))
        if healed:
            bound.info("resident_browser_crash_state_healed", profile_dir=str(config.profile_dir))

        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(config.profile_dir),
                    headless=False,  # 常驻=真人浏览器必须有头；headless 违背反风控初衷
                    proxy=_parse_proxy(config.proxy_url) if config.proxy_url else None,
                    args=[f"--remote-debugging-port={config.cdp_port}", "--lang=zh-CN"],
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                    user_agent=_USER_AGENT,
                )
            except Exception as exc:
                # profile 锁占用（另一实例在跑）/端口占用/驱动缺失——失败即死，
                # systemd Restart=always 兜底；日志如实记录原因。
                bound.error(
                    "resident_browser_launch_failed",
                    profile_dir=str(config.profile_dir),
                    proxy=mask_proxy_url(config.proxy_url),
                    error=safe_exception_summary(exc),
                )
                return 1
            bound.info(
                "resident_browser_up",
                profile_dir=str(config.profile_dir),
                proxy=mask_proxy_url(config.proxy_url),
                display=config.display,
            )
            page = context.pages[0] if context.pages else context.new_page()
            while not stop.wait(config.health_interval_s):
                try:
                    title = page.title()
                except Exception as exc:
                    # 浏览器崩溃/卡死——探活失败即死，让 systemd 重启出新浏览器。
                    bound.error(
                        "resident_browser_unhealthy",
                        error=safe_exception_summary(exc),
                    )
                    _close_gracefully(context, platform=config.platform)
                    return 1
                bound.info("resident_browser_health", title=title[:120] or None)
            _close_gracefully(context, platform=config.platform)
            bound.info("resident_browser_stopped")
            return 0
    finally:
        for signum, handler in old_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass


def main() -> int:
    try:
        config = ResidentBrowserConfig.from_env()
    except ValueError as exc:
        log.error("resident_browser_config_invalid", error_type=type(exc).__name__)
        return 2
    return run_resident_browser(config)


if __name__ == "__main__":
    sys.exit(main())
