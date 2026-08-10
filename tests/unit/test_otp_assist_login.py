"""otp_assist_login CLI 单元测试：浏览器层全部 fake（注入 fake driver），
绝不起真浏览器、绝不发真网络推送（push monkeypatch 记录；bridge 只打
127.0.0.1 ephemeral 端口的本机回环，与 test_captcha_assist 同口径）。

覆盖：配置门（缺 CDP_URL / 推送未配齐且无 --no-notify）、--no-notify 放行、
注册表 schema 与 workflow 撞码路径产物 live 对比 + assist_router 识别、
done 双通道（注册表 solved / stdin Enter）→ exit 0 且锁释放、TTL → exit 2
且清理、attach 失败 → exit 1、推送失败不废会话、绝不杀浏览器（只断 CDP）、
--goto 导航、best-effort 登录态验证报告。

锁说明：resident_browser 2026-08-06 起升级为 进程内锁 + PG fencing 复合锁；
测试一律 ``GEO_BROWSER_FENCING=local``（该模块文档的单 worker 测试口径），
绝不碰真 DB。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time

import pytest

from tools import otp_assist_login
from workflows.activities import captcha_assist
from workflows.activities.captcha_assist import (
    CaptchaAssistInput,
    CaptchaAssistStopInput,
    captcha_assist_start,
    captcha_assist_stop,
)
from workflows.activities.resident_browser import browser_lock

_FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


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
    def press(self, key: str) -> None:
        pass

    def type(self, text: str, delay: float = 0) -> None:
        pass


class _FakePage:
    """登录页 fake：url/title/goto + 可见选择器集合；close 置标记（绝不应被调）。"""

    def __init__(self, *, url: str = "about:blank", title: str = "fake",
                 visible_selectors: set[str] | None = None) -> None:
        self._url = url
        self._title = title
        self._visible_selectors = set(visible_selectors or set())
        self.goto_calls: list[str] = []
        self.keyboard = _FakeKeyboard()
        self.context: _FakeContext | None = None
        self.cdp_events: list = []
        self.screenshot_calls = 0
        self.closed = False

    @property
    def url(self) -> str:
        return self._url

    def title(self) -> str:
        return self._title

    def goto(self, url: str, **kw: object) -> None:
        self.goto_calls.append(url)
        self._url = url

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(sel in self._visible_selectors)

    def screenshot(self, **kw: object) -> bytes:
        self.screenshot_calls += 1
        return _FAKE_JPEG

    def evaluate(self, expr: str, arg: object = None) -> dict:
        return {"x": 0.0, "y": 0.0}

    def wait_for_timeout(self, ms: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True      # attach 语义下绝不应被调（profile 归 supervisor）


class _FakeContext:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = list(pages)
        for p in self.pages:
            p.context = self
        self.closed = False

    def close(self) -> None:
        self.closed = True      # attach 语义下绝不应被调


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    def close(self) -> None:
        self.closed = True     # 只断 CDP——fake 无进程可杀


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
    monkeypatch: pytest.MonkeyPatch, tmp_path, pages: list[_FakePage],
    *, cdp_url: str | None = "http://127.0.0.1:19226", notify: bool = True,
) -> tuple[_FakeBrowser, _FakePwHandle, list]:
    """注入 fake driver/CDP/注册表目录/推送记录器/local fencing，返回可断言句柄。"""
    browser = _FakeBrowser(_FakeContext(pages))
    handle = _FakePwHandle(browser)
    monkeypatch.setattr(
        captcha_assist, "load_sync_browser_driver",
        lambda: ("patchright", lambda: _FakePwStarter(handle), Exception),
    )
    monkeypatch.setattr(captcha_assist, "resident_cdp_url", lambda platform: cdp_url)
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)
    monkeypatch.setenv("GEO_BROWSER_FENCING", "local")   # 纯进程内锁，绝不碰 DB
    monkeypatch.setattr(otp_assist_login, "_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(otp_assist_login, "_scrub_proxy_env", lambda: None)  # 不动测试进程 env
    pushes: list[dict] = []
    monkeypatch.setattr(
        otp_assist_login, "push_captcha_assist",
        lambda **kw: pushes.append(kw) is None or True,
    )
    if notify:
        monkeypatch.setenv("GEO_ASSIST_PUBLIC_BASE", "https://assist.example/")
        monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://notify.example/hook")
    else:
        monkeypatch.delenv("GEO_ASSIST_PUBLIC_BASE", raising=False)
        monkeypatch.delenv("GEO_ASSIST_NOTIFY_URL", raising=False)
    monkeypatch.delenv("GEO_ASSIST_NOTIFY_FLAVOR", raising=False)
    return browser, handle, pushes


def _solve_when_active(tmp_path) -> threading.Thread:
    """后台线程：等注册表出现 active 记录后标 solved（模拟外部人工确认）。"""
    def _worker() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for path in tmp_path.glob("*.json"):
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if rec.get("state") == "active":
                    captcha_assist._patch_registry(
                        path.stem, state="solved", solved_at=int(time.time()))
                    return
            time.sleep(0.05)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def _only_record(tmp_path) -> dict:
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _assert_lock_free(platform: str) -> None:
    lock = browser_lock(platform)
    assert lock.acquire(blocking=False), "browser_lock 未释放"
    lock.release()


# ── 配置门 ────────────────────────────────────────────────────────────────────


def test_missing_cdp_url_exits_3(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()], cdp_url=None)
    rc = otp_assist_login.main(["--platform", "yiyan"])
    assert rc == 3
    assert "GEO_YIYAN_CDP_URL" in capsys.readouterr().err
    assert list(tmp_path.glob("*.json")) == []          # 不落注册表


def test_notify_unconfigured_without_no_notify_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()], notify=False)
    rc = otp_assist_login.main(["--platform", "yiyan"])
    assert rc == 3
    assert "--no-notify" in capsys.readouterr().err
    assert list(tmp_path.glob("*.json")) == []


def test_invalid_cdp_url_exits_3(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 用真 resident_cdp_url（本测试不走 _wire）：非法 URL → ValueError → exit 3
    monkeypatch.setenv("GEO_YIYAN_CDP_URL", "not-a-url")
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(otp_assist_login, "_scrub_proxy_env", lambda: None)
    rc = otp_assist_login.main(["--platform", "yiyan"])
    assert rc == 3


# ── 注册表 schema / assist_router 识别 ─────────────────────────────────────────


async def test_registry_schema_matches_workflow_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """CLI 记录字段集与 workflow 撞码路径（captcha_assist_start）产物 live 对比。"""
    _wire(monkeypatch, tmp_path, [_FakePage()])
    monkeypatch.setattr(captcha_assist, "push_captcha_assist", lambda **kw: True)
    started = await captcha_assist_start(CaptchaAssistInput(
        tenant_pub_id="tenant_1", run_pub_id="run_schema", platform="doubao",
        business_key="bk"))
    try:
        workflow_rec = next(
            json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("run_pub_id") == "run_schema")
        cli_rec = otp_assist_login._build_registry_record(
            platform="yiyan", instance_key="yiyan_sh", run_pub_id="otp-assist-yiyan-1",
            session_id="s" * 24,
            ticket_hash="h" * 64, port=19226, note="155开户", ttl_s=600)
        assert set(cli_rec) == set(workflow_rec)           # 字段集一字不差
        assert cli_rec["version"] == workflow_rec["version"] == 1
        assert cli_rec["state"] == workflow_rec["state"] == "active"
    finally:
        await captcha_assist_stop(CaptchaAssistStopInput(
            run_pub_id="run_schema", session_id=started.session_id))


def test_registry_record_recognized_by_assist_router(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """assist_router._load_registry 全套校验（version/hash/过期/state）通过 = 能服务。"""
    from api.geo_platform.collection import assist_router

    monkeypatch.setattr(assist_router, "ASSIST_DIR", tmp_path)
    monkeypatch.setattr(captcha_assist, "_REGISTRY_DIR", tmp_path)   # _write_registry 也进 tmp
    ticket = secrets.token_urlsafe(32)
    th = captcha_assist._ticket_hash(ticket)
    rec = otp_assist_login._build_registry_record(
        platform="yiyan", instance_key="yiyan_sh", run_pub_id="otp-assist-yiyan-1",
        session_id="s" * 24,
        ticket_hash=th, port=19226, note="155开户", ttl_s=600)
    captcha_assist._write_registry(rec)
    loaded = assist_router._load_registry(ticket)          # 任何校验失败都会 403
    assert loaded["platform"] == "yiyan" and loaded["state"] == "active"
    assert loaded["business_key"] == "155开户"
    assert assist_router._bridge_port(loaded) == 19226     # 端口校验也通过
    raw = (tmp_path / f"{th}.json").read_text(encoding="utf-8")
    assert ticket not in raw                               # ticket 只存 sha256
    assert th == hashlib.sha256(ticket.encode()).hexdigest()


# ── 主流程：done → exit 0 ──────────────────────────────────────────────────────


def test_done_via_registry_solved_exit_0_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    page = _FakePage(url="https://yiyan.baidu.com/", title="文心一言")
    browser, handle, pushes = _wire(monkeypatch, tmp_path, [page])
    _solve_when_active(tmp_path)
    rc = otp_assist_login.main(
        ["--platform", "yiyan", "--note", "155开户", "--ttl-min", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    # 推送：文案标明登录/OTP 接管 + note
    assert len(pushes) == 1
    assert "登录/OTP" in pushes[0]["title"]
    assert "155开户" in pushes[0]["body"]
    # stdout：接管链接含 ticket 明文；链接指向 assist_router 手机页
    ticket = pushes[0]["body"].rsplit("/api/v2/assist/", 1)[-1].strip()
    assert f"接管链接: https://assist.example/api/v2/assist/{ticket}" in out
    assert "人工已确认完成" in out
    # 注册表终态：会话 stop → closed（captcha_assist 词表），solved_at/push_sent 留痕
    rec = _only_record(tmp_path)
    assert rec["state"] == "closed"
    assert rec["solved_at"] is not None
    assert rec["push_sent"] is True
    assert rec["platform"] == "yiyan" and rec["business_key"] == "155开户"
    assert rec["expires_at"] - rec["created_at"] == 300    # --ttl-min 5
    assert ticket not in json.dumps(rec)                   # 明文只进 stdout/推送
    # 清理：只断 CDP，playwright 停，锁释放；浏览器 context/page 绝不被 close
    assert browser.closed is True
    assert handle.stopped is True
    assert browser.contexts[0].closed is False
    assert page.closed is False
    assert page.goto_calls == []                           # 未给 --goto，不动页面
    _assert_lock_free("yiyan")


def test_done_via_stdin_enter_exit_0(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    page = _FakePage(url="https://yiyan.baidu.com/", title="文心一言")
    _wire(monkeypatch, tmp_path, [page])

    def _fake_watcher(done_evt: threading.Event) -> threading.Thread:
        thread = threading.Thread(
            target=lambda: (time.sleep(0.3), done_evt.set()), daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(otp_assist_login, "_start_done_watcher", _fake_watcher)
    rc = otp_assist_login.main(["--platform", "yiyan", "--ttl-min", "5"])
    assert rc == 0
    assert "人工已确认完成" in capsys.readouterr().out
    rec = _only_record(tmp_path)
    assert rec["state"] == "closed"
    assert rec["solved_at"] is not None                    # CLI 镜像 router 的 done 写
    _assert_lock_free("yiyan")


def test_no_notify_runs_and_prints_ticket(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()], notify=False)
    _solve_when_active(tmp_path)
    rc = otp_assist_login.main(["--platform", "yiyan", "--no-notify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GEO_ASSIST_PUBLIC_BASE 未配置" in out          # 如实报缺配置
    assert "ticket: " in out                               # 明文照打，运维自行拼接
    rec = _only_record(tmp_path)
    assert rec["push_sent"] is False                       # 未推送
    _assert_lock_free("yiyan")


# ── TTL / 异常路径 ─────────────────────────────────────────────────────────────


def test_ttl_timeout_exit_2_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    page = _FakePage()
    browser, handle, _pushes = _wire(monkeypatch, tmp_path, [page])
    rc = otp_assist_login.main(["--platform", "yiyan", "--ttl-min", "0.03"])  # ≈1s
    assert rc == 2
    assert "TTL" in capsys.readouterr().err
    # 清理发生：bridge 断（stop 即 shutdown）、CDP 断、锁释放、注册表标 closed
    assert browser.closed is True
    assert handle.stopped is True
    rec = _only_record(tmp_path)
    assert rec["state"] == "closed"                        # captcha_assist 词表（无 expired 态）
    assert rec["solved_at"] is None
    _assert_lock_free("yiyan")


def test_attach_failure_no_page_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    browser, _handle, _pushes = _wire(monkeypatch, tmp_path, [])   # 常驻浏览器无页
    rc = otp_assist_login.main(["--platform", "yiyan"])
    assert rc == 1
    assert "接管失败" in capsys.readouterr().err
    assert list(tmp_path.glob("*.json")) == []             # 未起成，不落注册表
    assert browser.closed is True                          # CDP 仍被干净断开
    _assert_lock_free("yiyan")


def test_push_failure_keeps_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    _wire(monkeypatch, tmp_path, [_FakePage()])
    monkeypatch.setattr(otp_assist_login, "push_captcha_assist", lambda **kw: False)
    _solve_when_active(tmp_path)
    rc = otp_assist_login.main(["--platform", "yiyan"])
    out = capsys.readouterr().out
    assert rc == 0                                         # 推送失败不废会话
    assert "推送失败" in out
    assert "接管链接: https://assist.example/api/v2/assist/" in out
    assert _only_record(tmp_path)["push_sent"] is False
    _assert_lock_free("yiyan")


# ── --goto 与 best-effort 验证 ──────────────────────────────────────────────────


def test_goto_navigates_and_verification_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    page = _FakePage(url="about:blank", title="文心一言",
                     visible_selectors={".user-avatar"})
    _wire(monkeypatch, tmp_path, [page])
    _solve_when_active(tmp_path)
    rc = otp_assist_login.main([
        "--platform", "yiyan", "--goto", "https://yiyan.baidu.com/",
        "--expect-url-regex", r"yiyan\.baidu\.com", "--expect-selector", ".user-avatar",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert page.goto_calls == ["https://yiyan.baidu.com/"]
    assert "已导航到" in out
    assert "登录态验证[url" in out and "PASS" in out
    assert "登录态验证[selector" in out


def test_verification_fail_still_exit_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    page = _FakePage(url="https://yiyan.baidu.com/login", title="登录")
    _wire(monkeypatch, tmp_path, [page])
    _solve_when_active(tmp_path)
    rc = otp_assist_login.main([
        "--platform", "yiyan", "--expect-url-regex", r"example\.com"])
    out = capsys.readouterr().out
    assert rc == 0                                         # best-effort：只报告不改退出码
    assert "FAIL" in out


# ── proxy env scrub ────────────────────────────────────────────────────────────


def test_scrub_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    otp_assist_login._scrub_proxy_env()
    assert os.environ.get("http_proxy") is None
    assert os.environ.get("HTTPS_PROXY") is None
