"""常驻浏览器契约层测试（attach-or-launch + 平台互斥锁）。"""

from __future__ import annotations

import threading
import time

import pytest

from workflows.activities import resident_browser
from workflows.activities.resident_browser import (
    BrowserBusyError,
    platform_browser,
    resident_cdp_url,
)


class _FakePage:
    pass


class _FakeContext:
    def __init__(self, page: object) -> None:
        self.pages = [page]
        self.closed = False

    def new_page(self) -> object:
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.connect_calls: list[str] = []

    def connect_over_cdp(self, url: str) -> _FakeBrowser:
        self.connect_calls.append(url)
        return self._browser


class _FakePw:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)


def test_cdp_url_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    assert resident_cdp_url("doubao") is None
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "  ")
    assert resident_cdp_url("doubao") is None
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    assert resident_cdp_url("doubao") == "http://127.0.0.1:19222"
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "ftp://bad")
    with pytest.raises(ValueError, match="CDP_URL"):
        resident_cdp_url("doubao")


def test_resident_attach_disconnects_without_closing_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    context = _FakeContext(_FakePage())
    browser = _FakeBrowser(context)
    pw = _FakePw(browser)
    launched = False

    def _launch() -> tuple[object, object]:
        nonlocal launched
        launched = True
        raise AssertionError("resident 模式不得回退 launch")

    with platform_browser(pw, platform="doubao", launch=_launch) as (ctx, page, resident):
        assert ctx is context and resident is True
        assert page is context.pages[0]
    assert pw.chromium.connect_calls == ["http://127.0.0.1:19222"]
    assert browser.closed is True  # 断开 CDP
    assert context.closed is False  # 常驻浏览器不被动关闭


def test_launch_fallback_closes_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    context = _FakeContext(_FakePage())
    pw = _FakePw(_FakeBrowser(context))

    def _launch() -> tuple[object, object]:
        return context, context.pages[0]

    with platform_browser(pw, platform="doubao", launch=_launch) as (ctx, _page, resident):
        assert ctx is context and resident is False
    assert context.closed is True
    assert pw.chromium.connect_calls == []


def test_platform_lock_serializes_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    pw = _FakePw(_FakeBrowser(_FakeContext(_FakePage())))
    order: list[str] = []

    def _session(name: str) -> None:
        def _launch() -> tuple[object, object]:
            ctx = _FakeContext(_FakePage())
            return ctx, ctx.pages[0]

        with platform_browser(pw, platform="doubao", launch=_launch):
            order.append(f"{name}-enter")
            time.sleep(0.05)
            order.append(f"{name}-exit")

    first = threading.Thread(target=_session, args=("a",))
    second = threading.Thread(target=_session, args=("b",))
    first.start()
    time.sleep(0.01)
    second.start()
    first.join()
    second.join()
    # 互斥：一个会话完整退出后另一个才能进入
    assert order[:2] == ["a-enter", "a-exit"]
    assert order[2:] == ["b-enter", "b-exit"]


def test_lock_timeout_raises_browser_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resident_browser, "_LOCK_TIMEOUT_S", 0.05)
    lock = resident_browser._lock_for("doubao")
    assert lock.acquire()
    try:
        with pytest.raises(BrowserBusyError):
            with platform_browser(_FakePw(_FakeBrowser(_FakeContext(_FakePage()))),
                                  platform="doubao", launch=lambda: (None, None)):
                pass
    finally:
        lock.release()
