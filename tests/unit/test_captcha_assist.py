"""captcha assist 会话层单元测试：浏览器层全部 fake（注入 fake driver），
绝不起真浏览器、绝不发真 HTTP（推送函数 monkeypatch 记录；bridge 只打
127.0.0.1  ephemeral 端口的本机回环）。

覆盖：注册表文件契约（写入/读取/0600/原子写/过期/ticket 只存 hash）、
start 幂等（同 run 不双开）、stop 幂等（无会话 no-op）、cleared_check 语义、
CDP 未配置 non_retryable、无页可重试、浏览器锁忙、TTL 兜底自杀、
bridge 移植行为（/frame JPEG、/input drag CDP 序列、iframe 偏移修正）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import stat
import time
import urllib.request

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities import (
    captcha_assist,
    deepseek_adapter,
    tongyi_adapter,
    yiyan_adapter,
    yuanbao_adapter,
)
from workflows.activities.captcha_assist import (
    CaptchaAssistInput,
    CaptchaAssistStopInput,
    InterventionBridge,
    captcha_assist_start,
    captcha_assist_stop,
)
from workflows.activities.doubao_adapter import _CAPTCHA_SELECTORS, _captcha_hit
from workflows.activities.resident_browser import browser_lock

_CAPTCHA_SEL = _CAPTCHA_SELECTORS[0]
_FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


@pytest.fixture(autouse=True)
def _local_fencing_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文件用例全走 local fencing（纯进程内锁，零 DB 调用）；跨 worker
    DB fencing 由 test_resident_browser.py 覆盖。生产缺省 GEO_BROWSER_FENCING=db。"""
    monkeypatch.setenv("GEO_BROWSER_FENCING", "local")


# ── fake 浏览器层（绝不启动真浏览器） ──────────────────────────────────────────


class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> _FakeLocator:
        return self

    def is_visible(self, timeout: float | None = None) -> bool:
        return self._visible


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)

    def type(self, text: str, delay: float = 0) -> None:
        pass


class _FakeCdpSession:
    """记录 CDP send 序列；detach 置标记。"""

    def __init__(self, sink: list) -> None:
        self._sink = sink
        self.detached = False

    def send(self, method: str, params: dict | None = None) -> None:
        self._sink.append((method, dict(params or {})))

    def detach(self) -> None:
        self.detached = True


class _FakeContext:
    def __init__(self, pages: list) -> None:
        self.pages = list(pages)
        for p in self.pages:
            p.context = self
        self.cdp_sessions: list[_FakeCdpSession] = []

    def new_cdp_session(self, _page: object) -> _FakeCdpSession:
        sess = _FakeCdpSession(self._page_cdp_sink(_page))
        self.cdp_sessions.append(sess)
        return sess

    @staticmethod
    def _page_cdp_sink(page: object) -> list:
        return page.cdp_events  # type: ignore[attr-defined]


class _FakePage:
    """按可见选择器集合应答 _captcha_hit 探测链；screenshot 返回固定 JPEG。"""

    def __init__(
        self,
        *,
        captcha_visible: bool = False,
        jpeg: bytes = _FAKE_JPEG,
        iframe_offset: dict | None = None,
    ) -> None:
        self._visible_sels: set[str] = {_CAPTCHA_SEL} if captcha_visible else set()
        self._jpeg = jpeg
        self._iframe_offset = iframe_offset or {"x": 0.0, "y": 0.0}
        self.keyboard = _FakeKeyboard()
        self.context: _FakeContext | None = None
        self.cdp_events: list = []
        self.screenshot_calls = 0

    def set_captcha_visible(self, visible: bool) -> None:
        self._visible_sels = {_CAPTCHA_SEL} if visible else set()

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(sel in self._visible_sels)

    def screenshot(self, **kw: object) -> bytes:
        self.screenshot_calls += 1
        return self._jpeg

    def evaluate(self, expr: str, arg: object = None) -> dict:
        return dict(self._iframe_offset)

    def wait_for_timeout(self, ms: float) -> None:
        pass


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    def close(self) -> None:
        self.closed = True  # 只断 CDP——fake 无进程可杀，由测试断言没碰 context/page


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.connect_calls: list[str] = []

    def connect_over_cdp(self, url: str) -> _FakeBrowser:
        self.connect_calls.append(url)
        return self._browser


class _FakePwHandle:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakePwStarter:
    def __init__(self, handle: _FakePwHandle) -> None:
        self._handle = handle

    def start(self) -> _FakePwHandle:
        return self._handle


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    pages: list[_FakePage],
    *,
    cdp_url: str | None = "http://127.0.0.1:19222",
) -> tuple[_FakeBrowser, _FakePwHandle, list]:
    """注入 fake driver/CDP/注册表目录/推送记录器，返回可断言句柄。"""
    browser = _FakeBrowser(_FakeContext(pages))
    handle = _FakePwHandle(browser)
    monkeypatch.setattr(
        captcha_assist,
        "load_sync_browser_driver",
        lambda: ("patchright", lambda: _FakePwStarter(handle), Exception),
    )
    monkeypatch.setattr(captcha_assist, "resident_cdp_url", lambda platform: cdp_url)
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)
    pushes: list[dict] = []
    monkeypatch.setattr(
        captcha_assist,
        "push_captcha_assist",
        lambda **kw: pushes.append(kw) is None or True,
    )
    monkeypatch.setenv("GEO_ASSIST_PUBLIC_BASE", "https://assist.example/")
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://notify.example/hook")
    monkeypatch.delenv("GEO_ASSIST_TTL_S", raising=False)
    return browser, handle, pushes


@pytest.fixture(autouse=True)
def _clean_sessions():
    yield
    for _key, sess in list(captcha_assist._SESSIONS.items()):
        try:
            sess.stop()
        except Exception:
            pass
    captcha_assist._SESSIONS.clear()


def _input(run_pub_id: str = "run_1", platform: str = "doubao") -> CaptchaAssistInput:
    return CaptchaAssistInput(
        tenant_pub_id="tenant_1",
        run_pub_id=run_pub_id,
        platform=platform,
        business_key="run_1-task-7",
        evidence_ref="file:///tmp/ev.png",
    )


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def _http_get(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read(), resp.headers["Content-Type"]


# ── 注册表文件契约 ─────────────────────────────────────────────────────────────


def test_registry_file_contract(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)
    ticket = secrets.token_urlsafe(32)
    th = captcha_assist._ticket_hash(ticket)
    assert th == hashlib.sha256(ticket.encode()).hexdigest()
    record = {
        "version": 1,
        "run_pub_id": "run_1",
        "session_id": "s" * 24,
        "ticket_hash": th,
        "port": 12345,
        "platform": "doubao",
        "state": "active",
        "business_key": "bk",
        "evidence_ref": None,
        "created_at": 1786000000,
        "expires_at": 1786004200,
        "push_sent": True,
        "solved_at": None,
    }
    captcha_assist._write_registry(record)
    path = tmp_path / f"{th}.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # 0600 权限
    raw = path.read_text(encoding="utf-8")
    assert json.loads(raw) == record  # 写入/读取 roundtrip
    assert ticket not in raw  # ticket 只存 hash
    assert not list(tmp_path.glob("*.tmp"))  # 原子写不留 tmp 残件
    # 过期判定：now >= expires_at
    assert not captcha_assist.registry_expired(record, now=record["expires_at"] - 1)
    assert captcha_assist.registry_expired(record, now=record["expires_at"])
    # _patch_registry 单字段推进
    captcha_assist._patch_registry(th, state="closed")
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "closed"


# ── start/stop 行为 ────────────────────────────────────────────────────────────


async def test_start_idempotent_and_stop_closes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    page = _FakePage(captcha_visible=True)
    browser, handle, pushes = _wire(monkeypatch, tmp_path, [page])

    r1 = await captcha_assist_start(_input())
    assert r1.assist_url.startswith("https://assist.example/api/v2/assist/")
    assert r1.pushed is True
    r2 = await captcha_assist_start(_input())  # 幂等：同 run 不双开
    assert (r2.session_id, r2.assist_url, r2.pushed) == (r1.session_id, r1.assist_url, r1.pushed)
    assert handle.chromium.connect_calls == ["http://127.0.0.1:19222"]  # 只 attach 一次
    assert len(pushes) == 1  # 不重推送

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["run_pub_id"] == "run_1" and rec["state"] == "active"
    assert rec["session_id"] == r1.session_id and rec["push_sent"] is True
    assert rec["ticket_hash"] == files[0].stem
    assert rec["expires_at"] - rec["created_at"] == 4200  # 默认 TTL 70min
    ticket = r1.assist_url.rsplit("/", 1)[-1]
    assert ticket not in files[0].read_text(encoding="utf-8")  # 明文只出现在推送 URL

    await captcha_assist_stop(CaptchaAssistStopInput(run_pub_id="run_1", session_id=r1.session_id))
    assert browser.closed is True  # 只断 CDP
    assert handle.stopped is True
    assert json.loads(files[0].read_text(encoding="utf-8"))["state"] == "closed"
    # stop 幂等：无会话 no-op，不抛
    await captcha_assist_stop(CaptchaAssistStopInput(run_pub_id="run_1", session_id=r1.session_id))
    await captcha_assist_stop(
        CaptchaAssistStopInput(run_pub_id="run_ghost", session_id="nonexistent")
    )


async def test_registry_write_failure_releases_started_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    browser, handle, _pushes = _wire(monkeypatch, tmp_path, [_FakePage(captcha_visible=True)])

    def fail_registry(_record: dict) -> None:
        raise OSError("registry directory is not writable")

    monkeypatch.setattr(captcha_assist, "_write_registry", fail_registry)
    with pytest.raises(OSError, match="not writable"):
        await captcha_assist_start(_input("run_registry_failure"))

    assert browser.closed is True
    assert handle.stopped is True
    assert captcha_assist._SESSIONS == {}
    lock = browser_lock("doubao")
    assert lock.acquire(timeout=0.1) is True
    lock.release()


async def test_feishu_app_start_only_enqueues_local_outbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _browser, _handle, legacy_pushes = _wire(monkeypatch, tmp_path, [_FakePage()])
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_FLAVOR", "feishu_app")
    monkeypatch.setenv("GEO_FEISHU_CHAT_ID", "oc_test")
    monkeypatch.delenv("GEO_ASSIST_NOTIFY_URL", raising=False)
    enqueued: list[dict] = []

    def enqueue(**kwargs):  # type: ignore[no-untyped-def]
        enqueued.append(kwargs)
        return "ntf_test_assist"

    state_changes: list[tuple[str, str]] = []
    monkeypatch.setattr(captcha_assist, "_enqueue_feishu_app_assist", enqueue)
    monkeypatch.setattr(
        captcha_assist,
        "_mark_feishu_app_assist_state",
        lambda ticket_hash, state: state_changes.append((ticket_hash, state)) or True,
    )
    started = await captcha_assist_start(_input("run_feishu_app"))
    assert started.pushed is True
    assert started.assist_url == ""  # raw ticket never enters Temporal activity result
    assert legacy_pushes == []  # no webhook/OpenAPI network call in activity
    assert enqueued[0]["session_kind"] == "workflow_captcha"
    record = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert record["session_kind"] == "workflow_captcha"
    assert record["notification_id"] == "ntf_test_assist"
    assert record["delivery_enqueued"] is True
    await captcha_assist_stop(
        CaptchaAssistStopInput(run_pub_id="run_feishu_app", session_id=started.session_id)
    )
    assert state_changes == [(record["ticket_hash"], "closed")]


async def test_cdp_url_missing_non_retryable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()], cdp_url=None)
    with pytest.raises(ApplicationError) as exc_info:
        await captcha_assist_start(_input())
    assert exc_info.value.type == "assist_no_resident_browser"
    assert exc_info.value.non_retryable is True


async def test_no_page_retryable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path, [])  # 常驻浏览器一个 page 都没有
    with pytest.raises(ApplicationError) as exc_info:
        await captcha_assist_start(_input())
    assert exc_info.value.type == "assist_no_page"
    assert exc_info.value.non_retryable is False


async def test_browser_lock_busy(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()])
    monkeypatch.setattr(captcha_assist, "_LOCK_TIMEOUT_S", 0.1)
    lock = browser_lock("doubao")
    assert lock.acquire()
    try:
        with pytest.raises(ApplicationError) as exc_info:
            await captcha_assist_start(_input())
        assert exc_info.value.type == "assist_browser_busy"
        assert exc_info.value.non_retryable is False
    finally:
        lock.release()


async def test_ttl_suicide_closes_session(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    page = _FakePage(captcha_visible=True)
    browser, _handle, _pushes = _wire(monkeypatch, tmp_path, [page])
    monkeypatch.setenv("GEO_ASSIST_TTL_S", "1")
    r1 = await captcha_assist_start(_input("run_ttl"))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    deadline = time.monotonic() + 5
    state = "active"
    while time.monotonic() < deadline:
        state = json.loads(files[0].read_text(encoding="utf-8"))["state"]
        if state == "closed":
            break
        await asyncio.sleep(0.1)
    assert state == "closed"  # 兜底自杀已推进注册表
    assert browser.closed is True
    assert captcha_assist._SESSIONS["run_ttl"].alive is False
    # 自杀后再 start = 全新会话（旧会话已死 → 清掉重开）
    r2 = await captcha_assist_start(_input("run_ttl"))
    assert r2.session_id != r1.session_id


# ── cleared_check 语义 + 选页（走真 bridge HTTP + marshal 泵） ───────────────────


async def test_cleared_check_semantics_via_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    page = _FakePage(captcha_visible=True)
    _wire(monkeypatch, tmp_path, [page])
    await captcha_assist_start(_input("run_status"))
    rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    url = f"http://127.0.0.1:{rec['port']}"
    # 同步阻塞 HTTP 放 to_thread：不吊死 event loop（activity 线程泵不依赖它，仅测试卫生）
    assert (await asyncio.to_thread(_http_json, f"{url}/status"))["cleared"] is False
    page.set_captcha_visible(False)
    # _captcha_hit None → 已清
    assert (await asyncio.to_thread(_http_json, f"{url}/status"))["cleared"] is True


async def test_page_selection_prefers_captcha_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    clean = _FakePage(jpeg=b"\xff\xd8clean-page")
    captcha = _FakePage(captcha_visible=True, jpeg=b"\xff\xd8captcha-page")
    _wire(monkeypatch, tmp_path, [clean, captcha])
    await captcha_assist_start(_input("run_pick"))
    rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    body, content_type = await asyncio.to_thread(_http_get, f"http://127.0.0.1:{rec['port']}/frame")
    assert body == b"\xff\xd8captcha-page"  # 命中者优先
    assert content_type == "image/jpeg"


# ── bridge 移植行为（直接用 fake page，不过 session 线程） ───────────────────────


@pytest.fixture
def bridge():
    page = _FakePage(iframe_offset={"x": 10.0, "y": 20.0})
    _FakeContext([page])
    br = InterventionBridge(page, cleared_check=lambda p: _captcha_hit(p) is None)
    br.start()
    yield page, br
    br.stop()


def test_frame_returns_jpeg(bridge) -> None:
    page, br = bridge
    with urllib.request.urlopen(f"http://127.0.0.1:{br.port}/frame", timeout=5) as resp:
        assert resp.headers["Content-Type"] == "image/jpeg"
        assert resp.read() == _FAKE_JPEG
    assert page.screenshot_calls == 1


def test_input_drag_cdp_sequence_with_iframe_offset(bridge) -> None:
    page, br = bridge
    req = urllib.request.Request(
        f"http://127.0.0.1:{br.port}/input",
        data=json.dumps({"type": "drag", "start": [100, 100], "end": [200, 150]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read())
    assert result["ok"] is True and result["type"] == "drag"
    events = page.cdp_events
    # pressed → moved×25 → released，共 27 帧，且全部带 iframe 偏移 (+10, +20)
    assert len(events) == 27
    kinds = [p["type"] for _m, p in events]
    assert kinds[0] == "mousePressed" and kinds[-1] == "mouseReleased"
    assert kinds[1:-1] == ["mouseMoved"] * 25
    assert (events[0][1]["x"], events[0][1]["y"]) == (110.0, 120.0)
    assert (events[-1][1]["x"], events[-1][1]["y"]) == (210.0, 170.0)
    mid = events[13][1]  # i=13 → t=13/25 的中间帧
    assert mid["x"] == pytest.approx(110.0 + 100.0 * 13 / 25)
    assert mid["y"] == pytest.approx(120.0 + 50.0 * 13 / 25)
    assert page.context.cdp_sessions[0].detached is True  # CDP session 用完即 detach


def test_input_click_with_iframe_offset(bridge) -> None:
    page, br = bridge
    req = urllib.request.Request(
        f"http://127.0.0.1:{br.port}/input",
        data=json.dumps({"type": "click", "at": [5, 5]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read())
    assert result == {"ok": True, "type": "click", "at": [15.0, 25.0]}
    assert [p["type"] for _m, p in page.cdp_events] == ["mousePressed", "mouseReleased"]


async def test_start_fails_fast_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """未配置 GEO_ASSIST_PUBLIC_BASE/NOTIFY_URL = 功能未启用：fail fast 让
    workflow 立即回退现行语义——绝不空挂 60min 等一个收不到通知的人工。"""
    monkeypatch.delenv("GEO_ASSIST_PUBLIC_BASE", raising=False)
    monkeypatch.delenv("GEO_ASSIST_NOTIFY_URL", raising=False)
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)
    with pytest.raises(ApplicationError) as excinfo:
        await captcha_assist_start(_input("run_unconfigured"))
    assert excinfo.value.type == "assist_not_configured"
    assert excinfo.value.non_retryable
    assert captcha_assist._SESSIONS == {}  # 不起会话、不占浏览器锁
    assert list(tmp_path.glob("*.json")) == []  # 不落注册表

    # 只配其一同样 fail fast（两个都齐才是"已启用"）
    monkeypatch.setenv("GEO_ASSIST_PUBLIC_BASE", "https://assist.example/")
    with pytest.raises(ApplicationError) as excinfo2:
        await captcha_assist_start(_input("run_unconfigured"))
    assert excinfo2.value.type == "assist_not_configured"


# ── 平台化 cleared 判定（captcha-assist-v1 门放开，2026-08-07） ───────────────────

# 每平台取其 adapter _CAPTCHA_SELECTORS 词表的最末项做探针——词表单一真源在
# 各 adapter，本测试只验证「assist 用的是该平台那张表」，不复制词表内容。
_PLATFORM_ADAPTERS = {
    "doubao": None,  # doubao 用文件头部已导入的 _CAPTCHA_SELECTORS
    "deepseek": deepseek_adapter,
    "tongyi": tongyi_adapter,
    "yiyan": yiyan_adapter,
    "yuanbao": yuanbao_adapter,
}


@pytest.mark.parametrize("platform", ["doubao", "deepseek", "tongyi", "yiyan", "yuanbao"])
def test_platform_cleared_check_uses_platform_feature_table(platform: str) -> None:
    mod = _PLATFORM_ADAPTERS[platform]
    sels = mod._CAPTCHA_SELECTORS if mod is not None else _CAPTCHA_SELECTORS
    check = captcha_assist._captcha_cleared_check_for(platform)
    assert check is not None
    page = _FakePage()
    page._visible_sels = {sels[-1]}  # wall 仍在 → 未清
    assert check(page) is False
    page._visible_sels = set()  # wall 全消失 → 已清
    assert check(page) is True


def test_unknown_platform_cleared_check_fail_closed() -> None:
    """表外平台 fail-closed：无 cleared 判定（bridge 恒未清），靠超时回退。"""
    assert captcha_assist._captcha_cleared_check_for("obscure-platform") is None


async def test_session_cleared_check_follows_input_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """会话默认 cleared_check 按 input.platform 查表解析：tongyi 会话用通义
    特征判清（sentinel 在 _run 建桥时解析，doubao 行为与旧版逐字节一致）。"""
    sel = tongyi_adapter._CAPTCHA_SELECTORS[-1]
    page = _FakePage()
    page._visible_sels = {sel}
    _wire(monkeypatch, tmp_path, [page])
    started = await captcha_assist_start(_input("run_ty", platform="tongyi"))
    assert started.pushed is True
    rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert rec["platform"] == "tongyi"
    url = f"http://127.0.0.1:{rec['port']}"
    assert (await asyncio.to_thread(_http_json, f"{url}/status"))["cleared"] is False
    page._visible_sels = set()
    assert (await asyncio.to_thread(_http_json, f"{url}/status"))["cleared"] is True


async def test_session_unknown_platform_cleared_stays_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """表外平台会话：无任何 wall 可见也绝不报已清（fail-closed）。"""
    _wire(monkeypatch, tmp_path, [_FakePage()])
    await captcha_assist_start(_input("run_unk", platform="obscure-platform"))
    rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    url = f"http://127.0.0.1:{rec['port']}"
    assert (await asyncio.to_thread(_http_json, f"{url}/status"))["cleared"] is False


# ── 事件循环不阻塞（async activity 的同步阻塞段走 asyncio.to_thread） ─────────────


def test_activities_stay_async_for_direct_await_callers() -> None:
    """activity 签名必须保持 async def：scripts/drill_captcha_assist.py 等
    直接 await 本 activity（to_thread 化在内部，对外零感知）。"""
    assert asyncio.iscoroutinefunction(captcha_assist_start)
    assert asyncio.iscoroutinefunction(captcha_assist_stop)


async def test_start_stop_do_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """sess.start 就绪等（最长 30s）与 stop 的 join（最长 15s）若跑在事件循环
    上会吊死同 loop 其它 coroutine（batch heartbeat 30s 饥饿竞争位）。探针
    tick 在 start/stop 全程应持续推进——tick 数远大于阻塞时长对应的上限。"""
    page = _FakePage(captcha_visible=True)
    _wire(monkeypatch, tmp_path, [page])
    real_start = captcha_assist.AssistSession.start
    real_stop = captcha_assist.AssistSession.stop

    def _slow_start(self, **kw):  # 同步阻塞模拟 CDP attach 慢
        time.sleep(0.4)
        return real_start(self, **kw)

    def _slow_stop(self):  # 同步阻塞模拟会话线程 join 慢
        time.sleep(0.4)
        return real_stop(self)

    monkeypatch.setattr(captcha_assist.AssistSession, "start", _slow_start)
    monkeypatch.setattr(captcha_assist.AssistSession, "stop", _slow_stop)

    ticks = 0
    halt = asyncio.Event()

    async def _ticker() -> None:
        nonlocal ticks
        while not halt.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    ticker = asyncio.create_task(_ticker())
    try:
        started = await captcha_assist_start(_input("run_noblock"))
        await captcha_assist_stop(
            CaptchaAssistStopInput(run_pub_id="run_noblock", session_id=started.session_id)
        )
    finally:
        halt.set()
        await ticker
    # 0.8s 同步阻塞若压在循环上 tick ≈ 0；to_thread 化后应持续走（0.8s/0.02s ≈ 40）
    assert ticks >= 10


# ── 浏览器矩阵化：实例键（锁/CDP/fence）与平台 slug（特征表）拆分 ──────────────


class _RecordingLock:
    """browser_lock fake：记录 acquire/release，绝不起真锁。"""

    def __init__(self) -> None:
        self.acquired = False

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        del blocking, timeout
        self.acquired = True
        return True

    def release(self) -> None:
        self.acquired = False


async def test_instance_key_routes_lock_and_cdp_but_features_stay_slug(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """CaptchaAssistInput.instance_key=doubao_sh：锁与 CDP 按实例键（attach 撞码
    batch 的同一台常驻浏览器），撞码特征表/选页仍按平台 slug doubao。"""
    page = _FakePage(captcha_visible=True)  # doubao 特征命中 → 选页选中它
    _browser, _handle, _pushes = _wire(monkeypatch, tmp_path, [page])
    lock_calls: list[str] = []
    cdp_key_calls: list[str] = []

    def _lock_recorder(key: str) -> _RecordingLock:
        lock_calls.append(key)
        return _RecordingLock()

    monkeypatch.setattr(captcha_assist, "browser_lock", _lock_recorder)
    monkeypatch.setattr(
        captcha_assist,
        "resident_cdp_url",
        lambda key: cdp_key_calls.append(key) or "http://127.0.0.1:19222",
    )
    started = await captcha_assist_start(
        CaptchaAssistInput(
            tenant_pub_id="tenant_1",
            run_pub_id="run_inst",
            platform="doubao",
            business_key="bk",
            instance_key="doubao_sh",
        )
    )
    try:
        assert lock_calls == ["doubao_sh"]  # 锁/fence 键 = 实例键
        assert cdp_key_calls == ["doubao_sh"]  # CDP 解析键 = 实例键
        rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
        assert rec["platform"] == "doubao"  # 特征语义仍是平台 slug
        assert rec["instance_key"] == "doubao_sh"
    finally:
        await captcha_assist_stop(
            CaptchaAssistStopInput(run_pub_id="run_inst", session_id=started.session_id)
        )


async def test_instance_key_none_falls_back_to_platform_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """旧调用（instance_key=None）：锁/CDP 回退平台 slug——启用矩阵化前的行为。"""
    _wire(monkeypatch, tmp_path, [_FakePage()])
    lock_calls: list[str] = []

    def _lock_recorder(key: str) -> _RecordingLock:
        lock_calls.append(key)
        return _RecordingLock()

    monkeypatch.setattr(captcha_assist, "browser_lock", _lock_recorder)
    started = await captcha_assist_start(_input("run_legacy"))
    try:
        assert lock_calls == ["doubao"]
        rec = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
        assert rec["instance_key"] == "doubao"  # 注册表记实际生效键（回退 slug）
    finally:
        await captcha_assist_stop(
            CaptchaAssistStopInput(run_pub_id="run_legacy", session_id=started.session_id)
        )


async def test_instance_key_platform_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """实例键第一段 ≠ 平台 slug = 路由出错：fail-closed，绝不起会话/占锁/落注册表。"""
    _wire(monkeypatch, tmp_path, [_FakePage()])
    with pytest.raises(ApplicationError) as exc_info:
        await captcha_assist_start(
            CaptchaAssistInput(
                tenant_pub_id="tenant_1",
                run_pub_id="run_bad",
                platform="doubao",
                business_key="bk",
                instance_key="tongyi_bj",
            )
        )
    assert exc_info.value.type == "assist_instance_platform_mismatch"
    assert exc_info.value.non_retryable is True
    assert captcha_assist._SESSIONS == {}
    assert list(tmp_path.glob("*.json")) == []
