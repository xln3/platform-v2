"""W8 常驻浏览器（跨 run CDP 复用）测试：doubao attach/launch 双路径 + launcher。

- ``_browser_session`` attach 路径：connect_over_cdp、不 launch、不关 context、
  不做崩溃清理，退出只断开 browser（契约管）；launch 路径行为回归（关 context +
  首尾崩溃清理）。
- ``tools/resident_browser.py``：env 解析校验 / launch 参数组装 / 健康探活 /
  失败退出码（fake driver 注入，绝不启动真浏览器）。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from tools import resident_browser
from tools.resident_browser import ResidentBrowserConfig
from workflows.activities import doubao_adapter
from workflows.activities.doubao_adapter import (
    _USER_AGENT,
    DoubaoAdapterConfig,
    _IncompleteCapture,
    _PlaywrightDoubaoSession,
)


@pytest.fixture(autouse=True)
def _restore_display() -> Iterator[None]:
    """run_resident_browser 会写 os.environ["DISPLAY"]——测试间复原，不泄漏进程状态。"""
    old = os.environ.get("DISPLAY")
    yield
    if old is None:
        os.environ.pop("DISPLAY", None)
    else:
        os.environ["DISPLAY"] = old


# ---------------------------------------------------------------------------
# _browser_session 双路径 fake
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append(url)

    def wait_for_timeout(self, _ms: int) -> None:
        pass


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]
        self.close_calls = 0
        self._closed = False

    def new_page(self) -> _FakePage:
        return self.pages[0]

    def set_default_timeout(self, _ms: int) -> None:
        pass

    def close(self) -> None:
        # 与真实 patchright 一致：幂等（契约层兜底二次 close 必须是 no-op）
        if self._closed:
            return
        self._closed = True
        self.close_calls += 1


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeChromium:
    """connect_over_cdp / launch_persistent_context 双侧记录。"""

    def __init__(self, context: _FakeContext) -> None:
        self._context = context
        self.browser = _FakeBrowser(context)
        self.connect_calls: list[str] = []
        self.launch_calls: list[dict[str, Any]] = []
        self.connect_error: Exception | None = None

    def connect_over_cdp(self, url: str) -> _FakeBrowser:
        self.connect_calls.append(url)
        if self.connect_error is not None:
            raise self.connect_error
        return self.browser

    def launch_persistent_context(self, **kwargs: Any) -> _FakeContext:
        self.launch_calls.append(kwargs)
        return self._context


class _FakePw:
    def __init__(self, chromium: Any) -> None:
        self.chromium = chromium


class _FakePwCM:
    def __init__(self, pw: _FakePw) -> None:
        self._pw = pw

    def __enter__(self) -> _FakePw:
        return self._pw

    def __exit__(self, *_exc: Any) -> bool:
        return False


def _install_session_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[_FakeChromium, list[str]]:
    """注入 fake 驱动/墙检查/崩溃清理；返回 (chromium, clean_calls)。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("GEO_DOUBAO_HEADLESS", "1")
    page = _FakePage()
    context = _FakeContext(page)
    chromium = _FakeChromium(context)
    pw = _FakePw(chromium)
    monkeypatch.setattr(
        doubao_adapter,
        "load_sync_browser_driver",
        lambda: ("fake", lambda: _FakePwCM(pw), TimeoutError),
    )
    monkeypatch.setattr(doubao_adapter, "_try_close_overlays", lambda *_a: None)
    monkeypatch.setattr(doubao_adapter, "_detect_login_wall", lambda *_a: False)
    clean_calls: list[str] = []
    monkeypatch.setattr(
        doubao_adapter,
        "_clean_profile_crash_state",
        lambda profile_dir: clean_calls.append(str(profile_dir)) or True,
    )
    return chromium, clean_calls


def _make_session(tmp_path: Path) -> _PlaywrightDoubaoSession:
    config = DoubaoAdapterConfig.from_env()
    return _PlaywrightDoubaoSession(config, tmp_path / "evidence", "w8-stem")


def test_browser_session_attach_path_skips_close_and_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置 CDP URL → attach：connect_over_cdp 一次、不 launch、不关 context、
    不做崩溃清理；退出只断开 browser。导航与 launch 路径共用（goto 仍发生）。"""
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    chromium, clean_calls = _install_session_fakes(monkeypatch, tmp_path)
    session = _make_session(tmp_path)
    stages: list[str] = []

    with session._browser_session(stages.append) as (context, page, pw_timeout, driver):
        assert context is chromium.browser.contexts[0]
        assert page is context.pages[0]
        assert pw_timeout is TimeoutError and driver == "fake"
        assert stages[:2] == ["browser_launch", "navigate"]
        assert page.goto_calls == [doubao_adapter._CHAT_URL]  # 导航两路径共用

    assert chromium.connect_calls == ["http://127.0.0.1:19222"]
    assert chromium.launch_calls == []  # attach 绝不 launch
    assert chromium.browser.contexts[0].close_calls == 0  # 常驻 context 不关
    assert chromium.browser.close_calls == 1  # 退出只断开 CDP 连接
    assert clean_calls == []  # 崩溃清理归 supervisor/launcher


def test_browser_session_launch_path_closes_context_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置 CDP URL → 旧 launch 行为回归：launch 一次、关 context 一次、
    崩溃清理首尾各一次（clean → launch → close → clean），绝不 connect。"""
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    chromium, clean_calls = _install_session_fakes(monkeypatch, tmp_path)
    session = _make_session(tmp_path)
    order: list[str] = []

    def _clean_spy(profile_dir: Path) -> bool:
        clean_calls.append(str(profile_dir))
        order.append("clean")
        return True

    monkeypatch.setattr(doubao_adapter, "_clean_profile_crash_state", _clean_spy)
    ctx = chromium.browser.contexts[0]
    orig_launch = chromium.launch_persistent_context

    def _launch_spy(**kwargs: Any) -> _FakeContext:
        order.append("launch")
        return orig_launch(**kwargs)

    chromium.launch_persistent_context = _launch_spy
    orig_close = ctx.close

    def _close_spy() -> None:
        before = ctx.close_calls
        orig_close()
        if ctx.close_calls > before:  # 契约兜底二次 close 幂等（不计入）
            order.append("close")

    ctx.close = _close_spy

    with session._browser_session(lambda s: None) as (context, page, _t, _d):
        assert context is ctx
        assert page.goto_calls == [doubao_adapter._CHAT_URL]

    assert chromium.connect_calls == []  # launch 路径绝不 attach
    assert len(chromium.launch_calls) == 1
    kwargs = chromium.launch_calls[0]
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["user_agent"] == _USER_AGENT
    assert kwargs["locale"] == "zh-CN"
    assert ctx.close_calls == 1
    assert chromium.browser.close_calls == 0
    assert clean_calls == [str(tmp_path), str(tmp_path)]  # 启动前 + close 后
    assert order == ["clean", "launch", "close", "clean"]


def test_browser_session_attach_connect_failure_is_honest_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """attach 断连（常驻未起/崩溃）→ browser-launch-failed 诚实可重试
    （_IncompleteCapture），绝不裸抛驱动异常。"""
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    chromium, _clean_calls = _install_session_fakes(monkeypatch, tmp_path)
    chromium.connect_error = RuntimeError("connect ECONNREFUSED")
    session = _make_session(tmp_path)

    with pytest.raises(_IncompleteCapture, match=r"browser-launch-failed"):
        with session._browser_session(lambda s: None):
            pass


def test_doubao_config_cdp_url_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CDP URL 格式错误 = adapter_not_configured（fail-closed 不重试）；合法放行。"""
    monkeypatch.setenv("GEO_DOUBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "ftp://bad")
    with pytest.raises(ApplicationError, match="CDP_URL") as excinfo:
        DoubaoAdapterConfig.from_env()
    assert excinfo.value.type == "adapter_not_configured"
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    assert DoubaoAdapterConfig.from_env().profile_dir == tmp_path


# ---------------------------------------------------------------------------
# tools/resident_browser.py launcher
# ---------------------------------------------------------------------------


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "RESIDENT_PLATFORM": "doubao",
        "RESIDENT_PROFILE_DIR": "/tmp/x",
        "RESIDENT_CDP_PORT": "19222",
    }
    base.update(overrides)
    return base


def test_resident_config_from_env_valid_and_defaults(tmp_path: Path) -> None:
    config = ResidentBrowserConfig.from_env(_env(RESIDENT_PROFILE_DIR=str(tmp_path)))
    assert config.platform == "doubao"
    assert config.profile_dir == tmp_path
    assert config.proxy_url is None
    assert config.cdp_port == 19222
    assert config.display == ":1"  # 缺省
    assert config.health_interval_s == 15.0
    assert config.health_timeout_s == 60.0
    assert config.cdp_url == "http://127.0.0.1:19222"
    with_proxy = ResidentBrowserConfig.from_env(
        _env(
            RESIDENT_PROFILE_DIR=str(tmp_path),
            RESIDENT_PROXY_URL="http://user:pass@proxy.example:3128",
            RESIDENT_DISPLAY=":0",
            RESIDENT_HEALTH_INTERVAL_SECONDS="8.5",
            RESIDENT_HEALTH_TIMEOUT_SECONDS="3",
        )
    )
    assert with_proxy.proxy_url == "http://user:pass@proxy.example:3128"
    assert with_proxy.display == ":0"
    assert with_proxy.health_interval_s == 8.5
    assert with_proxy.health_timeout_s == 3.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"RESIDENT_PLATFORM": ""},
        {"RESIDENT_PLATFORM": "Doubao!"},
        {"RESIDENT_CDP_PORT": ""},
        {"RESIDENT_CDP_PORT": "abc"},
        {"RESIDENT_CDP_PORT": "80"},
        {"RESIDENT_PROXY_URL": "not-a-url"},
        {"RESIDENT_PROFILE_DIR": "/nonexistent/geo-w8-profile"},
        {"RESIDENT_PROFILE_DIR": ""},
        {"RESIDENT_HEALTH_INTERVAL_SECONDS": "0"},
        {"RESIDENT_HEALTH_TIMEOUT_SECONDS": "not-a-number"},
    ],
)
def test_resident_config_from_env_invalid(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ResidentBrowserConfig.from_env(_env(**overrides))


class _LauncherFakePage:
    def __init__(self, stop: threading.Event | None = None, fail: bool = False) -> None:
        self._stop = stop
        self._fail = fail
        self.title_calls = 0

    def title(self) -> str:
        self.title_calls += 1
        if self._fail:
            raise RuntimeError("Target crashed")
        if self._stop is not None and self.title_calls >= 2:
            self._stop.set()  # 探活两拍后驱动退出
        return "豆包 - 你的 AI 朋友"


class _LauncherFakeContext:
    def __init__(self, page: _LauncherFakePage) -> None:
        self.pages = [page]
        self.closed = False

    def new_page(self) -> _LauncherFakePage:
        return self.pages[0]

    def close(self) -> None:
        self.closed = True


class _LauncherFakeChromium:
    def __init__(
        self, context: _LauncherFakeContext, launch_error: Exception | None = None
    ) -> None:
        self._context = context
        self._launch_error = launch_error
        self.launch_kwargs: dict[str, Any] | None = None

    def launch_persistent_context(self, **kwargs: Any) -> _LauncherFakeContext:
        self.launch_kwargs = kwargs
        if self._launch_error is not None:
            raise self._launch_error
        return self._context


def _fake_driver_loader(chromium: _LauncherFakeChromium) -> Any:
    pw = _FakePw(chromium)
    return lambda: ("fake", lambda: _FakePwCM(pw), TimeoutError)


def _launcher_config(tmp_path: Path, **overrides: Any) -> ResidentBrowserConfig:
    kwargs: dict[str, Any] = {
        "platform": "doubao",
        "profile_dir": tmp_path,
        "proxy_url": "http://user:pass@proxy.example:3128",
        "cdp_port": 19222,
        "health_interval_s": 0.01,
        "health_timeout_s": 0.1,
    }
    kwargs.update(overrides)
    return ResidentBrowserConfig(**kwargs)


def test_resident_launcher_launch_kwargs_and_graceful_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数组装与 doubao_adapter 口径对齐（CDP port + lang + locale/tz/UA/headed），
    预设 stop → 优雅 close、退出码 0、DISPLAY 写入。"""
    monkeypatch.delenv("DISPLAY", raising=False)
    page = _LauncherFakePage()
    context = _LauncherFakeContext(page)
    chromium = _LauncherFakeChromium(context)
    stop = threading.Event()
    stop.set()  # 零探活直接优雅退出

    rc = resident_browser.run_resident_browser(
        _launcher_config(tmp_path),
        driver_loader=_fake_driver_loader(chromium),
        stop_event=stop,
    )

    assert rc == 0
    assert chromium.launch_kwargs is not None
    kwargs = chromium.launch_kwargs
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is False
    assert kwargs["args"] == ["--remote-debugging-port=19222", "--lang=zh-CN"]
    assert kwargs["locale"] == "zh-CN"
    assert kwargs["timezone_id"] == "Asia/Shanghai"
    assert kwargs["extra_http_headers"] == {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"}
    assert kwargs["user_agent"] == _USER_AGENT
    assert kwargs["proxy"] == {
        "server": "http://proxy.example:3128",
        "username": "user",
        "password": "pass",
    }
    assert context.closed is True
    assert os.environ["DISPLAY"] == ":1"


def test_resident_launcher_health_probe_runs_until_stop(tmp_path: Path) -> None:
    """健康探活：只探测浏览器级 CDP 端点，stop 后优雅退出 0。"""
    stop = threading.Event()
    page = _LauncherFakePage()
    context = _LauncherFakeContext(page)
    chromium = _LauncherFakeChromium(context)
    probe_calls: list[tuple[str, float]] = []

    def _healthy_probe(url: str, timeout_s: float) -> str:
        probe_calls.append((url, timeout_s))
        if len(probe_calls) >= 2:
            stop.set()
        return "Chrome/test"

    rc = resident_browser.run_resident_browser(
        _launcher_config(tmp_path),
        driver_loader=_fake_driver_loader(chromium),
        health_probe=_healthy_probe,
        stop_event=stop,
    )

    assert rc == 0
    assert probe_calls == [
        ("http://127.0.0.1:19222", 0.1),
        ("http://127.0.0.1:19222", 0.1),
    ]
    assert page.title_calls == 0  # 不与采集连接争用 page target
    assert context.closed is True


def test_resident_launcher_launch_failure_returns_nonzero(tmp_path: Path) -> None:
    """profile 锁占用/端口占用（launch 抛错）→ 非零退出（systemd 重启兜底）。"""
    chromium = _LauncherFakeChromium(
        _LauncherFakeContext(_LauncherFakePage()),
        launch_error=RuntimeError("ProcessSingleton: profile in use"),
    )

    rc = resident_browser.run_resident_browser(
        _launcher_config(tmp_path),
        driver_loader=_fake_driver_loader(chromium),
        stop_event=threading.Event(),
    )

    assert rc == 1


def test_resident_launcher_unhealthy_probe_returns_nonzero(tmp_path: Path) -> None:
    """探活发现浏览器崩溃 → 尝试优雅 close 后非零退出。"""
    page = _LauncherFakePage()
    context = _LauncherFakeContext(page)
    chromium = _LauncherFakeChromium(context)

    def _failed_probe(_url: str, _timeout_s: float) -> str:
        raise RuntimeError("CDP endpoint unavailable")

    rc = resident_browser.run_resident_browser(
        _launcher_config(tmp_path),
        driver_loader=_fake_driver_loader(chromium),
        health_probe=_failed_probe,
        stop_event=threading.Event(),
    )

    assert rc == 1
    assert page.title_calls == 0
    assert context.closed is True


def test_resident_launcher_hung_probe_hits_hard_deadline(tmp_path: Path) -> None:
    """CDP 探活不返回时，独立 watchdog 触发 fatal exit；迟到结果不算健康。"""
    stop = threading.Event()
    release_probe = threading.Event()
    fatal_codes: list[int] = []

    def _fake_fatal_exit(code: int) -> None:
        fatal_codes.append(code)
        release_probe.set()

    def _hung_probe(_url: str, _timeout_s: float) -> str:
        assert release_probe.wait(timeout=1.0)
        return "Chrome/late"

    page = _LauncherFakePage()
    context = _LauncherFakeContext(page)
    chromium = _LauncherFakeChromium(context)

    rc = resident_browser.run_resident_browser(
        _launcher_config(tmp_path, health_interval_s=0.001, health_timeout_s=0.02),
        driver_loader=_fake_driver_loader(chromium),
        health_probe=_hung_probe,
        stop_event=stop,
        fatal_exit=_fake_fatal_exit,
    )

    assert rc == 1
    assert fatal_codes == [1]
    assert page.title_calls == 0
    assert context.closed is True


def test_resident_launcher_main_config_invalid_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in (
        "RESIDENT_PLATFORM",
        "RESIDENT_PROFILE_DIR",
        "RESIDENT_PROXY_URL",
        "RESIDENT_CDP_PORT",
        "RESIDENT_DISPLAY",
    ):
        monkeypatch.delenv(var, raising=False)
    assert resident_browser.main() == 2
    monkeypatch.setenv("RESIDENT_PLATFORM", "doubao")
    monkeypatch.setenv("RESIDENT_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("RESIDENT_CDP_PORT", "not-a-port")
    assert resident_browser.main() == 2
