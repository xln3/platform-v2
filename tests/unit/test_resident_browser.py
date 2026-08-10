"""常驻浏览器契约层测试（attach-or-launch + 平台互斥锁 + DB fencing）。

锁行为用例缺省跑 ``GEO_BROWSER_FENCING=local``（纯进程内锁，零 DB 调用）；
跨 worker fencing 用例 monkeypatch ``resident_browser._db_fence_*`` 三个
seam（绝不起真 PG），leases 层 SQL 逻辑用最小 fake session 直接断言。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from geo_platform.collection.leases import (
    LeaseBusyError,
    acquire_browser_fence,
    heartbeat_browser_fence,
    release_browser_fence,
)
from geo_platform.collection.models import BrowserFence
from geo_platform.tenancy import database as tenancy_db

from workflows.activities import resident_browser
from workflows.activities.resident_browser import (
    BrowserBusyError,
    browser_lock,
    platform_browser,
    resident_cdp_url,
)


@pytest.fixture(autouse=True)
def _local_fencing_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 local（生产缺省是 db）：纯锁行为用例不碰 DB；
    fencing 用例在本 fixture 之后再 setenv("GEO_BROWSER_FENCING", "db") 覆盖。"""
    monkeypatch.setenv("GEO_BROWSER_FENCING", "local")


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
            with platform_browser(
                _FakePw(_FakeBrowser(_FakeContext(_FakePage()))),
                platform="doubao",
                launch=lambda: (None, None),
            ):
                pass
    finally:
        lock.release()


def test_local_mode_zero_db_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """GEO_BROWSER_FENCING=local：attach/launch/公共锁全程零 DB seam 调用。"""

    def _forbid(*args: object) -> object:
        raise AssertionError("local 模式不得触碰 DB fencing seam")

    monkeypatch.setattr(resident_browser, "_db_fence_acquire", _forbid)
    monkeypatch.setattr(resident_browser, "_db_fence_release", _forbid)
    monkeypatch.setattr(resident_browser, "_db_fence_heartbeat", _forbid)
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    context = _FakeContext(_FakePage())
    with platform_browser(
        _FakePw(_FakeBrowser(context)), platform="doubao", launch=lambda: (None, None)
    ):
        pass
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL")
    ctx2 = _FakeContext(_FakePage())
    with platform_browser(
        _FakePw(_FakeBrowser(ctx2)), platform="doubao", launch=lambda: (ctx2, ctx2.pages[0])
    ):
        pass
    lock = browser_lock("doubao")
    assert lock.acquire(timeout=0.1)
    lock.release()


# ── DB fencing：契约层（seam 全部 fake，绝不起真 PG） ────────────────────────────


class _FenceSeams:
    """``_db_fence_*`` 三 seam 的记录型 fake：按调用发放递增 fencing_token。"""

    def __init__(self) -> None:
        self.acquire_calls: list[tuple] = []
        self.release_calls: list[tuple] = []
        self.heartbeat_calls: list[tuple] = []
        self.token = 0

    def acquire(
        self, platform: str, holder: str, ttl_s: float, timeout_s: float | None
    ) -> int | None:
        self.acquire_calls.append((platform, holder, ttl_s, timeout_s))
        self.token += 1
        return self.token

    def release(self, platform: str, holder: str, fencing_token: int) -> bool:
        self.release_calls.append((platform, holder, fencing_token))
        return True

    def heartbeat(self, platform: str, holder: str, fencing_token: int, ttl_s: float) -> bool:
        self.heartbeat_calls.append((platform, holder, fencing_token, ttl_s))
        return True


def _wire_db_fencing(monkeypatch: pytest.MonkeyPatch, seams: _FenceSeams) -> None:
    monkeypatch.setenv("GEO_BROWSER_FENCING", "db")
    monkeypatch.setattr(resident_browser, "_db_fence_acquire", seams.acquire)
    monkeypatch.setattr(resident_browser, "_db_fence_release", seams.release)
    monkeypatch.setattr(resident_browser, "_db_fence_heartbeat", seams.heartbeat)


def test_db_mode_attach_acquires_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.setenv("GEO_BROWSER_FENCE_HOLDER", "worker-test:1")
    context = _FakeContext(_FakePage())
    browser = _FakeBrowser(context)

    with platform_browser(_FakePw(browser), platform="doubao", launch=lambda: (None, None)) as (
        _ctx,
        _page,
        resident,
    ):
        assert resident is True
        assert len(seams.acquire_calls) == 1
        assert seams.release_calls == []  # 持有期间不释放

    platform, holder, _ttl, _timeout = seams.acquire_calls[0]
    assert platform == "doubao" and holder == "worker-test:1"
    assert seams.release_calls == [("doubao", "worker-test:1", 1)]  # token 原样回传
    assert browser.closed is True and context.closed is False  # attach 语义不变


def test_db_mode_launch_acquires_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    context = _FakeContext(_FakePage())

    with platform_browser(
        _FakePw(_FakeBrowser(context)),
        platform="doubao",
        launch=lambda: (context, context.pages[0]),
    ):
        assert len(seams.acquire_calls) == 1
    assert len(seams.release_calls) == 1
    assert context.closed is True


def test_db_mode_lease_busy_raises_browser_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 里已有他人未过期 lease（seam 返回 None=timeout 内拿不到）→ BrowserBusyError。"""
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    monkeypatch.setattr(
        resident_browser, "_db_fence_acquire", lambda *args: None
    )  # 他人持租，重试到超时仍 None
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)

    with pytest.raises(BrowserBusyError, match="busy"):
        with platform_browser(
            _FakePw(_FakeBrowser(_FakeContext(_FakePage()))),
            platform="doubao",
            launch=lambda: (None, None),
        ):
            pass
    # 本地锁已解开：换乘功 seam 后能立刻拿到
    monkeypatch.setattr(resident_browser, "_db_fence_acquire", seams.acquire)
    lock = browser_lock("doubao")
    assert lock.acquire(timeout=0.1)
    lock.release()
    assert seams.release_calls[-1][2] == 1


def test_db_mode_db_failure_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 不可达 → 真 seam fail-closed 抛 BrowserBusyError，本地锁不残留。"""
    monkeypatch.setenv("GEO_BROWSER_FENCING", "db")
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)

    def _unreachable() -> object:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tenancy_db, "WorkerSessionLocal", _unreachable)
    with pytest.raises(BrowserBusyError, match="unavailable"):
        with platform_browser(
            _FakePw(_FakeBrowser(_FakeContext(_FakePage()))),
            platform="doubao",
            launch=lambda: (None, None),
        ):
            pass
    # 本地锁已解开：换成好 seam 后能立刻拿到
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    lock = browser_lock("doubao")
    assert lock.acquire(timeout=0.1)
    lock.release()


def test_db_mode_heartbeat_renews_while_held_and_stops_on_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持有期间后台心跳按间隔续期（holder/token 原样透传），释放后不再心跳。"""
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    monkeypatch.setenv("GEO_BROWSER_FENCE_HOLDER", "worker-test:2")
    monkeypatch.setenv("GEO_BROWSER_FENCE_HEARTBEAT_S", "0.05")

    lock = browser_lock("doubao")
    assert lock.acquire(timeout=1)
    time.sleep(0.18)
    assert len(seams.heartbeat_calls) >= 2
    assert {call[:3] for call in seams.heartbeat_calls} == {("doubao", "worker-test:2", 1)}
    lock.release()
    assert seams.release_calls == [("doubao", "worker-test:2", 1)]
    time.sleep(0.12)
    after = len(seams.heartbeat_calls)
    time.sleep(0.12)
    assert len(seams.heartbeat_calls) == after  # 释放后心跳线程已停


def test_db_mode_serializes_sessions_same_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """db 模式下锁串行顺序不变（进程内快速路径 + lease 各获取/释放一次）。"""
    seams = _FenceSeams()
    _wire_db_fencing(monkeypatch, seams)
    monkeypatch.delenv("GEO_DOUBAO_CDP_URL", raising=False)
    pw = _FakePw(_FakeBrowser(_FakeContext(_FakePage())))
    order: list[str] = []

    def _session(name: str) -> None:
        def _launch() -> tuple[object, object]:
            ctx = _FakeContext(_FakePage())
            return ctx, ctx.pages[0]

        with platform_browser(pw, platform="doubao", launch=_launch):
            order.append(f"{name}-enter")
            time.sleep(0.03)
            order.append(f"{name}-exit")

    first = threading.Thread(target=_session, args=("a",))
    second = threading.Thread(target=_session, args=("b",))
    first.start()
    time.sleep(0.01)
    second.start()
    first.join()
    second.join()
    assert order[:2] == ["a-enter", "a-exit"]
    assert order[2:] == ["b-enter", "b-exit"]
    # 第二根线程被进程内锁挡住，lease 获取/释放严格交替
    assert len(seams.acquire_calls) == 2
    assert len(seams.release_calls) == 2
    assert [call[2] for call in seams.release_calls] == [1, 2]


# ── DB fencing：leases 层（最小 fake session，绝不起真 PG） ──────────────────────


class _FakeDbSession:
    """leases 函数需要的最小 session 假面：execute/scalar/add/flush 全记录。"""

    def __init__(self, row: BrowserFence | None) -> None:
        self.row = row
        self.executed: list[object] = []
        self.flushed = False

    def execute(self, stmt: object) -> None:
        self.executed.append(stmt)

    def scalar(self, _stmt: object) -> BrowserFence | None:
        return self.row

    def add(self, obj: object) -> None:
        assert isinstance(obj, BrowserFence)
        self.row = obj

    def flush(self) -> None:
        self.flushed = True


def _fence_row(
    *,
    holder: str = "worker-old:1",
    token: int = 5,
    expired: bool = False,
    released: bool = False,
) -> BrowserFence:
    now = datetime.now(UTC)
    return BrowserFence(
        platform="doubao",
        holder=holder,
        fencing_token=token,
        acquired_at=now - timedelta(minutes=10),
        heartbeat_at=now - timedelta(minutes=1),
        expires_at=(now - timedelta(seconds=1)) if expired else (now + timedelta(hours=2)),
        released_at=now if released else None,
    )


def test_lease_acquire_busy_on_live_foreign_row() -> None:
    session = _FakeDbSession(_fence_row())
    with pytest.raises(LeaseBusyError):
        acquire_browser_fence(
            session,
            platform="doubao",
            holder="worker-new:2",  # type: ignore[arg-type]
            ttl=timedelta(hours=2),
        )
    assert session.executed  # advisory lock 串行化已发起


def test_lease_acquire_preempts_expired_row_and_token_increments() -> None:
    row = _fence_row(expired=True)
    session = _FakeDbSession(row)
    lease = acquire_browser_fence(
        session,
        platform="doubao",
        holder="worker-new:2",  # type: ignore[arg-type]
        ttl=timedelta(hours=2),
    )
    assert lease is row  # 单例行原地接管
    assert lease.fencing_token == 6  # 抢占也单调递增
    assert lease.holder == "worker-new:2"
    assert lease.released_at is None
    assert lease.expires_at > datetime.now(UTC)
    assert session.flushed


def test_lease_acquire_creates_row_with_token_1() -> None:
    session = _FakeDbSession(None)
    lease = acquire_browser_fence(
        session,
        platform="doubao",
        holder="worker-a:1",  # type: ignore[arg-type]
        ttl=timedelta(hours=2),
    )
    assert session.row is lease
    assert lease.fencing_token == 1
    assert lease.platform == "doubao" and lease.holder == "worker-a:1"


def test_lease_release_stale_token_keeps_row() -> None:
    row = _fence_row()
    session = _FakeDbSession(row)
    ok = release_browser_fence(
        session,
        platform="doubao",
        holder="worker-old:1",  # type: ignore[arg-type]
        fencing_token=99,
    )
    assert ok is False
    assert row.released_at is None  # stale token 不误释放他人租约


def test_lease_release_marks_released() -> None:
    row = _fence_row()
    session = _FakeDbSession(row)
    ok = release_browser_fence(
        session,
        platform="doubao",
        holder="worker-old:1",  # type: ignore[arg-type]
        fencing_token=5,
    )
    assert ok is True
    assert row.released_at is not None
    # 已释放行再次释放 → False（幂等，不炸）
    assert (
        release_browser_fence(
            session,
            platform="doubao",
            holder="worker-old:1",  # type: ignore[arg-type]
            fencing_token=5,
        )
        is False
    )


def test_lease_heartbeat_renews_and_rejects_stale() -> None:
    row = _fence_row()
    session = _FakeDbSession(row)
    before = row.expires_at
    ok = heartbeat_browser_fence(
        session,
        platform="doubao",
        holder="worker-old:1",  # type: ignore[arg-type]
        fencing_token=5,
        ttl=timedelta(hours=3),
    )
    assert ok is True
    assert row.expires_at > before
    assert (
        heartbeat_browser_fence(
            session,
            platform="doubao",
            holder="worker-old:1",  # type: ignore[arg-type]
            fencing_token=4,
            ttl=timedelta(hours=3),
        )
        is False
    )
    expired = _FakeDbSession(_fence_row(expired=True))
    assert (
        heartbeat_browser_fence(
            expired,
            platform="doubao",
            holder="worker-old:1",  # type: ignore[arg-type]
            fencing_token=5,
            ttl=timedelta(hours=3),
        )
        is False
    )
