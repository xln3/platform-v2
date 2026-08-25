"""撞验证码 → 手机人工接管 → 断点续跑的 assist 会话层（2026-08-06 起）。

workflow 撞码时调 ``captcha_assist_start``：attach 常驻 headed Chromium（supervisor
管理生命周期，CDP 由 ``GEO_<PLATFORM>_CDP_URL`` 提供），把验证码页面的实时画面
（``GET /frame`` JPEG）与触摸输入（``POST /input`` CDP drag/click）中继给管理员
手机浏览器；人工作答后 workflow 调 ``captcha_assist_stop``，原 run 断点续跑。

移植自旧系统生产验证实现 ``server/proxyllm/intervention_bridge.py``（693 行）：
- ``InterventionBridge`` 原样移植（含 verifycenter iframe 偏移修正），唯一改动：
  ``_write_status`` 改 no-op——bridge_status.json 会被同目录多会话互相覆盖，
  会话状态一律以注册表文件（``runtime/captcha-assist/<ticket_sha256>.json``）为权威。
- ``_MarshalledPage`` 全家门面原样移植：patchright/playwright sync API 基于
  greenlet + 私有 asyncio loop，绑定创建线程，跨线程调用即 greenlet.error
  （旧链生产实证）；一切 playwright 调用必须 marshal 回持有浏览器的专属线程。
- ``AssistSession`` 照搬 ``RelaySession`` 线程拓扑（专属线程 + ``_call_q`` 队列泵 +
  ``max_lifetime_s`` 兜底自杀），但删掉 profile 锁 / ``launch_persistent_context`` /
  storage_state 水合——assist 是 attach（``connect_over_cdp``）不是 launch。
  2026-08-07 起也被 ``tools/otp_assist_login.py``（登录/OTP 人工接管 CLI）复用：
  经 ``page_picker`` / ``cleared_check`` 两个扩展点注入登录语义，workflow 撞码
  路径两参数全缺省，行为不变。

纪律：
- 绝不杀常驻浏览器：退出只 ``browser.close()`` 断开 CDP 连接，绝不 close
  context/page（profile/登录态归 supervisor）。
- ticket 明文绝不出现在注册表文件/日志/Temporal payload——只出现在推送 URL。
- assist 会话整个生命周期持有 ``browser_lock(lock_key)``，防同 worker 另一 run
  的 batch 抢占同一常驻浏览器。2026-08-09 起（浏览器矩阵化）``lock_key`` =
  常驻实例键（``doubao_sh`` 等，CaptchaAssistInput.instance_key），与 batch 侧
  的锁/CDP/fence 同键互斥；撞码特征表/选页/已清判定仍按平台 slug。
  旧调用 instance_key=None → lock_key 回退平台 slug（启用前行为不变）。
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import queue
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.security.redaction import safe_exception_summary
from workflows.activities.assist_notify import push_captcha_assist
from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.deepseek_adapter import _captcha_hit as _deepseek_captcha_hit
from workflows.activities.doubao_adapter import _captcha_hit as _doubao_captcha_hit

# resident_cdp_url 显式再导出（同名 as）：tools/otp_assist_login.py 经本模块消费，
# 与会话内部共用同一 monkeypatch 点（strict mypy 的 no_implicit_reexport 要求）。
from workflows.activities.resident_browser import browser_lock
from workflows.activities.resident_browser import resident_cdp_url as resident_cdp_url
from workflows.activities.tongyi_adapter import _captcha_hit as _tongyi_captcha_hit
from workflows.activities.yiyan_adapter import _captcha_hit as _yiyan_captcha_hit
from workflows.activities.yuanbao_adapter import _captcha_hit as _yuanbao_captcha_hit

log = structlog.get_logger()

# A configured absolute path is stable across immutable release snapshots. The
# source-relative fallback preserves development compatibility; tests
# monkeypatch this variable to tmp_path.
_configured_registry = os.environ.get("GEO_ASSIST_REGISTRY_DIR", "").strip()
if _configured_registry and not Path(_configured_registry).is_absolute():
    raise RuntimeError("assist_registry_dir_must_be_absolute")
_REGISTRY_DIR = (
    Path(_configured_registry)
    if _configured_registry
    else Path(__file__).resolve().parents[2] / "runtime" / "captcha-assist"
)
_LOCK_TIMEOUT_S = 60.0  # assist 在 workflow 保证的串行点启动，等不到锁 = 调度出错
_READY_WAIT_S = 30.0  # start() 等会话就绪上限（CDP attach 本地理应秒级）
_DEFAULT_TTL_S = 4200  # 70min 兜底自杀：管理员忘接管也不留孤儿会话/不放干锁

_PLATFORM_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "tongyi": "通义千问",
    "yiyan": "文心一言",
    "yuanbao": "腾讯元宝",
}

# 「wall 仍存在」特征判定表（2026-08-07 起五平台）：直接复用各 live adapter
# 的 ``_captcha_hit``——选择器词表单一真源在各 adapter（其 _CAPTCHA_SELECTORS
# 是各自平台风控组件的实测/移植词表），本模块绝不复制。cleared = 接管页面上
# 该平台撞码特征全部消失。表外平台 → fail-closed（不判 cleared，靠超时回退）。
_CAPTCHA_HIT_BY_PLATFORM: dict[str, Callable[[Any], str | None]] = {
    "doubao": _doubao_captcha_hit,
    "deepseek": _deepseek_captcha_hit,
    "tongyi": _tongyi_captcha_hit,
    "yiyan": _yiyan_captcha_hit,
    "yuanbao": _yuanbao_captcha_hit,
}


def _captcha_cleared_check_for(platform: str) -> Callable[[Any], bool] | None:
    """按平台取「已清」判定：撞码选择器全部消失 = 已清（workflow 撞码接管语义）。

    未知/无特征表平台返回 None——fail-closed：bridge /status 的 cleared 恒
    False，绝不误判已清，等不到人工确认就靠 60min 超时回退旧语义。

    登录/OTP 接管（tools/otp_assist_login.py）显式传 None——没有"已清"概念，
    bridge /status 的 cleared 恒 False，完成信号只认人工确认。
    """
    hit = _CAPTCHA_HIT_BY_PLATFORM.get(platform)
    if hit is None:
        return None

    def _cleared(page: Any) -> bool:
        return hit(page) is None

    return _cleared


class _PlatformDefaultCheck:
    """sentinel：AssistSession.cleared_check 缺省 = 按会话平台查特征表推导。"""


_PLATFORM_DEFAULT_CHECK = _PlatformDefaultCheck()


# ---------------------------------------------------------------------------
# 注册表文件（与 API 侧的接口契约，一字不能改）
# ---------------------------------------------------------------------------


def _ticket_hash(ticket: str) -> str:
    """ticket 只存 sha256——明文绝不出现在文件/日志/payload。"""
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def _registry_path(ticket_hash: str) -> Path:
    return _REGISTRY_DIR / f"{ticket_hash}.json"


def _write_registry(record: dict[str, Any]) -> None:
    """0600 + tmp/os.replace 原子写：API 侧永远读到完整 JSON。"""
    _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _registry_path(record["ticket_hash"])
    tmp = _REGISTRY_DIR / f".{record['ticket_hash']}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False)
    os.replace(tmp, path)
    os.chmod(path, 0o600)  # replace 保留 tmp 权限，这里再保险一次


def _read_registry(ticket_hash: str) -> dict[str, Any] | None:
    try:
        record: dict[str, Any] = json.loads(_registry_path(ticket_hash).read_text(encoding="utf-8"))
        return record
    except (OSError, json.JSONDecodeError):
        return None


def _patch_registry(ticket_hash: str, **fields: Any) -> None:
    """best-effort 读改写（state/push_sent 等单字段推进）；文件不在则跳过。"""
    rec = _read_registry(ticket_hash)
    if rec is None:
        return
    rec.update(fields)
    try:
        _write_registry(rec)
    except OSError:
        pass


def registry_expired(record: dict[str, Any], *, now: float | None = None) -> bool:
    """过期判定（API 侧 done 端点与本模块共用同一口径：now >= expires_at）。"""
    return (now if now is not None else time.time()) >= float(record.get("expires_at", 0))


def _ttl_s() -> int:
    raw = os.environ.get("GEO_ASSIST_TTL_S", "").strip()
    try:
        return max(1, int(raw)) if raw else _DEFAULT_TTL_S
    except ValueError:
        return _DEFAULT_TTL_S


def _enqueue_feishu_app_assist(
    *,
    tenant_pub_id: str | None,
    session_kind: str,
    run_pub_id: str,
    session_id: str,
    ticket_hash: str,
    platform: str,
    instance_key: str,
    business_key: str,
    created_at: int,
    expires_at: int,
    chat_id: str,
) -> str | None:
    """Best-effort local outbox insert; deliberately performs no Feishu I/O."""
    try:
        from geo_platform.notifications.service import NotificationService
        from geo_platform.tenancy.database import WorkerSessionLocal

        with WorkerSessionLocal() as session:
            notification_id = NotificationService(session).enqueue_assist(
                tenant_pub_id=tenant_pub_id,
                session_kind=session_kind,
                run_pub_id=run_pub_id,
                session_id=session_id,
                ticket_sha256=ticket_hash,
                platform=platform,
                instance_key=instance_key,
                business_key=business_key,
                created_at_epoch=created_at,
                expires_at_epoch=expires_at,
                target_chat_id=chat_id,
            )
            session.commit()
        return notification_id
    except Exception as error:  # noqa: BLE001 - notification failure cannot block collection
        log.warning(
            "captcha_assist.feishu_outbox_failed",
            run_pub_id=run_pub_id,
            marker=type(error).__name__,
        )
        return None


def _mark_feishu_app_assist_state(ticket_hash: str, state: str) -> bool:
    """Best-effort state outbox update for non-HTTP completion paths."""
    if os.environ.get("GEO_ASSIST_NOTIFY_FLAVOR", "").strip().lower() != "feishu_app":
        return False
    try:
        from geo_platform.notifications.service import NotificationService
        from geo_platform.tenancy.database import WorkerSessionLocal

        with WorkerSessionLocal() as session:
            notice = NotificationService(session).mark_assist_state_by_ticket(
                ticket_sha256=ticket_hash,
                state=state,
            )
            session.commit()
        return notice is not None
    except Exception as error:  # noqa: BLE001 - notification is never completion authority
        log.warning(
            "captcha_assist.feishu_state_failed",
            state=state,
            marker=type(error).__name__,
        )
        return False


# ---------------------------------------------------------------------------
# InterventionBridge（旧链 intervention_bridge.py 原样移植；_write_status → no-op）
# ---------------------------------------------------------------------------


class InterventionBridge:
    """HTTP bridge that lets an operator interact with a captcha page remotely.

    Parameters
    ----------
    page:
        A playwright sync Page object — or a marshalling facade with the same
        interface subset (see ``_MarshalledPage``).  A raw page is only safe
        when every call stays on the thread that owns playwright.
    cleared_check:
        Optional callable(page) -> bool.  When provided, /status includes
        {"cleared": cleared_check(page)}.  If None, cleared is always False.
    """

    def __init__(self, page: Any, *, cleared_check: Callable[[Any], bool] | None = None) -> None:
        self._page = page
        self._cleared_check = cleared_check
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    # ── public lifecycle ──────────────────────────────────────────────────────

    def start(self) -> int:
        """Start the bridge server in a daemon thread. Returns the ephemeral port."""
        # ThreadingHTTPServer：每个连接一条 handler 线程；/input 不再排在 /frame 后面。
        # 线程安全由调用方保证——page 必须是 marshal 门面（AssistSession 即如此）。
        server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        server.daemon_threads = True  # 关停不等在飞的 handler 线程
        server.allow_reuse_address = True
        self._server = server
        self.port = server.server_address[1]
        self._write_status(active=True, port=self.port)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self._thread = t
        return self.port

    def stop(self) -> None:
        """Stop the bridge server."""
        self._write_status(active=False, port=None)
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self.port = None

    # context manager support
    def __enter__(self) -> InterventionBridge:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _write_status(self, *, active: bool, port: int | None) -> None:
        """no-op（移植改动点）：旧链写 bridge_status.json，但 runtime/captcha-assist/
        下多会话会互相覆盖该文件；会话状态以注册表文件为唯一权威，此处不再落盘。"""
        return

    def _iframe_offset(self, page: Any) -> dict[str, float]:
        """Return {x, y} pixel offset of the verifycenter iframe on the page.

        Mirrors the same logic used by CdpInputActuator in captcha_actuator.
        Returns {x:0, y:0} if the iframe is not found or evaluation fails.
        """
        try:
            result = page.evaluate("""() => {
                const iframe = document.querySelector(
                    'iframe[src*="verifycenter"], iframe[id*="verify"], iframe[class*="verify"]'
                );
                if (!iframe) return {x: 0, y: 0};
                const r = iframe.getBoundingClientRect();
                return {x: r.left, y: r.top};
            }""")
            if isinstance(result, dict):
                return {"x": float(result.get("x", 0)), "y": float(result.get("y", 0))}
        except Exception:
            pass
        return {"x": 0.0, "y": 0.0}

    def _dispatch_input(self, body: dict[str, Any]) -> dict[str, Any]:
        """Actuate a drag/click via CDP (iframe offset corrected), or keyboard via page.keyboard.

        A new CDP session is created per call and detached after (mouse path).
        Keyboard path (``press``/``type``) goes through playwright's keyboard API so
        text lands wherever the page focus currently is (login / OTP / 2FA forms).
        """
        itype = body.get("type", "")

        # ── 键盘代理（手机端打字/回车过表单）──
        if itype == "press":
            key = str(body.get("key") or "")[:40]
            if not key:
                return {"ok": False, "error": "press needs key"}
            self._page.keyboard.press(key)
            return {"ok": True, "type": "press", "key": key}
        if itype == "type":
            text = str(body.get("text") or "")[:2000]
            if not text:
                return {"ok": False, "error": "type needs text"}
            self._page.keyboard.type(text, delay=30)
            return {"ok": True, "type": "type", "len": len(text)}

        offset = self._iframe_offset(self._page)
        ox, oy = offset["x"], offset["y"]

        cdp = self._page.context.new_cdp_session(self._page)
        try:
            if itype == "click":
                at = body.get("at", [0, 0])
                x, y = float(at[0]) + ox, float(at[1]) + oy
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 1,
                    },
                )
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 1,
                    },
                )
                return {"ok": True, "type": "click", "at": [x, y]}

            elif itype == "drag":
                start = body.get("start", [0, 0])
                end = body.get("end", [0, 0])
                sx, sy = float(start[0]) + ox, float(start[1]) + oy
                ex, ey = float(end[0]) + ox, float(end[1]) + oy
                steps = 25
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mousePressed",
                        "x": sx,
                        "y": sy,
                        "button": "left",
                        "clickCount": 1,
                    },
                )
                for i in range(1, steps + 1):
                    t = i / steps
                    cdp.send(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseMoved",
                            "x": sx + (ex - sx) * t,
                            "y": sy + (ey - sy) * t,
                            "button": "left",
                        },
                    )
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": ex,
                        "y": ey,
                        "button": "left",
                        "clickCount": 1,
                    },
                )
                return {"ok": True, "type": "drag", "start": [sx, sy], "end": [ex, ey]}

            else:
                return {"ok": False, "error": f"unknown type: {itype!r}"}
        finally:
            try:
                cdp.detach()
            except Exception:
                pass

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self  # captured in closure

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # silence access log
                pass

            def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/status":
                    cleared = False
                    if bridge._cleared_check is not None:
                        try:
                            cleared = bool(bridge._cleared_check(bridge._page))
                        except Exception:
                            cleared = False
                    self._send_json(
                        {
                            "active": True,
                            "cleared": cleared,
                            "port": bridge.port,
                        }
                    )
                elif path == "/frame":
                    try:
                        data = bridge._page.screenshot(type="jpeg", quality=50)
                        self._send_bytes(data, "image/jpeg")
                    except Exception as exc:
                        self._send_json({"error": safe_exception_summary(exc)}, 503)
                else:
                    self._send_json({"error": "not found"}, 404)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/input":
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length)
                    try:
                        body = json.loads(raw)
                    except Exception:
                        self._send_json({"error": "bad json"}, 400)
                        return
                    try:
                        result = bridge._dispatch_input(body)
                        self._send_json(result)
                    except Exception as exc:
                        self._send_json({"error": safe_exception_summary(exc)}, 503)
                else:
                    self._send_json({"error": "not found"}, 404)

        return _Handler


# ══ marshal 门面：HTTP handler 线程绝不直接碰 playwright 对象 ═══════════════════
# patchright/playwright sync API 基于 greenlet + 私有 asyncio loop，绑定创建线程；
# 跨线程调用即 greenlet.error（旧链生产实证：RelaySession 每个 /frame 503）。
# 门面把 InterventionBridge 用到的 page 接口子集全部经 ``call(fn)`` 编组回
# 持有浏览器的专属线程串行执行（AssistSession._pump_calls 泵），结果/异常回抛。
# call(fn) -> fn(page) 在 owner 线程执行（marshal 门面通用编组函数类型）
_MarshalCall = Callable[[Callable[[Any], Any]], Any]


class _MarshalledPage:
    """InterventionBridge 所需的 page 接口子集，全部 marshal 回 owner 线程。"""

    def __init__(self, call: _MarshalCall) -> None:
        self._call = call  # call(fn) -> fn(page) 在 owner 线程执行
        self._stash: dict[str, Any] = {}  # cdp session 等真身，键控存取（仅 owner 线程读写）

    def screenshot(self, **kw: Any) -> Any:
        return self._call(lambda page: page.screenshot(**kw))

    def evaluate(self, expr: str, arg: Any = None) -> Any:
        if arg is None:
            return self._call(lambda page: page.evaluate(expr))
        return self._call(lambda page: page.evaluate(expr, arg))

    def wait_for_timeout(self, ms: float) -> Any:
        return self._call(lambda page: page.wait_for_timeout(ms))

    def set_viewport_size(self, size: dict[str, int]) -> Any:
        return self._call(lambda page: page.set_viewport_size(size))

    @property
    def viewport_size(self) -> Any:
        return self._call(lambda page: page.viewport_size)

    def locator(self, selector: str) -> _MarshalledLocator:
        return _MarshalledLocator(self._call, selector)

    @property
    def keyboard(self) -> _MarshalledKeyboard:
        return _MarshalledKeyboard(self._call)

    @property
    def mouse(self) -> _MarshalledMouse:
        return _MarshalledMouse(self._call)

    @property
    def context(self) -> _MarshalledContext:
        return _MarshalledContext(self._call, self._stash)


class _MarshalledKeyboard:
    def __init__(self, call: _MarshalCall) -> None:
        self._call = call

    def press(self, key: str) -> Any:
        return self._call(lambda page: page.keyboard.press(key))

    def type(self, text: str, delay: float = 0) -> Any:
        return self._call(lambda page: page.keyboard.type(text, delay=delay))


class _MarshalledMouse:
    """mouse 子集（move/down/up/click/wheel）。"""

    def __init__(self, call: _MarshalCall) -> None:
        self._call = call

    def move(self, x: float, y: float) -> Any:
        return self._call(lambda page: page.mouse.move(x, y))

    def down(self, **kw: Any) -> Any:
        return self._call(lambda page: page.mouse.down(**kw))

    def up(self, **kw: Any) -> Any:
        return self._call(lambda page: page.mouse.up(**kw))

    def click(self, x: float, y: float, **kw: Any) -> Any:
        return self._call(lambda page: page.mouse.click(x, y, **kw))

    def wheel(self, dx: float, dy: float) -> Any:
        return self._call(lambda page: page.mouse.wheel(dx, dy))


class _MarshalledLocator:
    """locator 惰性查询门面（_captcha_hit 的探测链）。

    每次操作在 owner 线程重新解析 locator——与 playwright locator 的惰性查询语义一致，
    故无需 stash 真身。仅覆盖探测用子集：``.first`` / ``is_visible`` / ``inner_text``。
    """

    def __init__(self, call: _MarshalCall, selector: str, *, first: bool = False) -> None:
        self._call = call
        self._selector = selector
        self._first = first

    @property
    def first(self) -> _MarshalledLocator:
        return _MarshalledLocator(self._call, self._selector, first=True)

    def _resolve(self, page: Any) -> Any:
        loc = page.locator(self._selector)
        return loc.first if self._first else loc

    def is_visible(self, timeout: float | None = None) -> Any:
        return self._call(lambda page: self._resolve(page).is_visible(timeout=timeout))

    def inner_text(self, timeout: float | None = None) -> Any:
        return self._call(lambda page: self._resolve(page).inner_text(timeout=timeout))


class _MarshalledContext:
    def __init__(self, call: _MarshalCall, stash: dict[str, Any]) -> None:
        self._call = call
        self._stash = stash

    def new_cdp_session(self, _page: Any) -> _MarshalledCdp:
        return _MarshalledCdp(self._call, self._stash)


class _MarshalledCdp:
    """CDP session 真身留在 owner 线程的 stash 里；send/detach 按键 marshal 回去。"""

    _ids = itertools.count(1)

    def __init__(self, call: _MarshalCall, stash: dict[str, Any]) -> None:
        self._call = call
        self._stash = stash
        self._key = f"cdp_{next(type(self)._ids)}"
        self._call(lambda page: stash.__setitem__(self._key, page.context.new_cdp_session(page)))

    def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self._call(lambda page: self._stash[self._key].send(method, params or {}))

    def detach(self) -> Any:
        return self._call(lambda page: self._stash.pop(self._key).detach())


# ══ AssistSession：attach 常驻浏览器 + InterventionBridge，专属线程拥有全生命周期 ══
class AssistSession:
    """一次撞码接管会话 = [CDP attach 常驻浏览器 + InterventionBridge]，专属线程拥有全生命周期。

    与旧链 RelaySession 的差别：不 launch、不碰 profile——attach supervisor 管理的
    常驻 Chromium，退出只断 CDP 连接（``browser.close()``），浏览器进程/context/page
    全部归 supervisor。整个生命周期持有平台互斥锁。

    幂等/安全纪律：
      * ``start()`` 幂等——已 alive 直接返回现有 port，绝不双开 attach。
      * 异常路径（启动失败/超时/stop/TTL 自杀）一律
        [bridge.stop → browser.close(仅断 CDP) → pw.stop → 放锁 → 注册表 closed]。
      * ``max_lifetime_s`` 兜底自杀：管理员忘接管也不留孤儿会话、不放干平台锁。

    复用扩展点（workflow 撞码路径两个参数都传缺省，语义逐字节不变）：
      * ``page_picker``：选页策略，缺省 ``_pick_page``（撞码页优先，否则 pages[0]）；
        登录/OTP 接管传" pages[0]"策略——登录页没有撞码概念。
      * ``cleared_check``：缺省 ``_PLATFORM_DEFAULT_CHECK`` sentinel = 按会话
        平台查 ``_CAPTCHA_HIT_BY_PLATFORM`` 推导（表外平台 fail-closed 恒不清）；
        显式传 None 则 bridge /status 的 cleared 恒 False（登录/OTP 无自动
        完成检测）。
    """

    def __init__(
        self,
        *,
        platform: str,
        run_pub_id: str,
        session_id: str,
        ticket_hash: str,
        max_lifetime_s: int = _DEFAULT_TTL_S,
        instance_key: str | None = None,
        page_picker: Callable[[Any], Any] | None = None,
        cleared_check: Callable[[Any], bool] | None | _PlatformDefaultCheck = (
            _PLATFORM_DEFAULT_CHECK
        ),
    ):
        self._platform = platform
        # 浏览器矩阵化（2026-08-09 起）：锁/CDP/fence 用实例键（attach 撞码 batch
        # 的同一台常驻浏览器），撞码特征表/选页/已清判定仍按平台 slug。
        # instance_key=None（旧调用）→ 回退 platform（启用前行为逐字节不变）。
        self._lock_key = (instance_key or "").strip().lower() or platform
        self._instance_key = self._lock_key
        self._run_pub_id = run_pub_id
        self._session_id = session_id
        self._ticket_hash = ticket_hash
        self._max_lifetime_s = max(1, int(max_lifetime_s))
        self._page_picker = page_picker
        self._cleared_check = cleared_check
        self._stop_evt = threading.Event()
        self._ready_evt = threading.Event()
        self._call_q: queue.Queue[
            tuple[Callable[[Any], Any] | None, queue.Queue[tuple[bool, Any]]]
        ] = queue.Queue()  # handler 线程 → assist 线程的 marshal 队列
        self._thread: threading.Thread | None = None
        self.port: int | None = None
        self.error: BaseException | None = None
        self.alive = False
        # start 成功后由 activity 回填（幂等命中时原样返回）
        self.assist_url = ""
        self.pushed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def ticket_hash(self) -> str:
        return self._ticket_hash

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self, *, wait_ready_s: float = _READY_WAIT_S) -> int:
        """启动会话（幂等：已 alive → 直接返回现有 port）。失败回抛线程内捕获的异常。"""
        if self.alive and self.port:
            return self.port  # 幂等：同会话不双开 attach
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"captcha-assist-{self._session_id[:8]}"
        )
        self._thread.start()
        if not self._ready_evt.wait(wait_ready_s):
            self.stop()
            raise RuntimeError("assist_start_timeout")
        if self.error is not None:
            raise self.error
        assert self.port is not None  # ready 且 error 为空 ⇒ bridge.start() 已回填端口
        return int(self.port)

    def stop(self) -> None:
        """关桥+断 CDP+放锁（幂等；可在任意线程调用）。"""
        self._stop_evt.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=15)

    # ── thread body（playwright 全生命周期都在这根专属线程里） ──────────────────
    def _run(self) -> None:
        pw = browser = None
        bridge: InterventionBridge | None = None
        lock = browser_lock(self._lock_key)
        locked = False
        try:
            # workflow 保证 assist 在串行点启动，本不该等锁；60s 拿不到 = 调度出错，如实上报
            if not lock.acquire(timeout=_LOCK_TIMEOUT_S):
                raise ApplicationError(
                    f"platform browser lock busy for {self._lock_key} (>{_LOCK_TIMEOUT_S:.0f}s)",
                    type="assist_browser_busy",
                )
            locked = True
            _driver_name, sync_playwright, _pw_timeout = load_sync_browser_driver()
            pw = sync_playwright().start()
            cdp_url = resident_cdp_url(self._lock_key)
            if not cdp_url:
                raise ApplicationError(
                    f"resident browser CDP URL not configured for {self._lock_key}",
                    type="assist_no_resident_browser",
                    non_retryable=True,  # workflow 走超时回退，重试无意义
                )
            browser = pw.chromium.connect_over_cdp(cdp_url)
            picker = self._page_picker if self._page_picker is not None else self._pick_page
            page = picker(browser)
            check = self._cleared_check
            if isinstance(check, _PlatformDefaultCheck):
                # 缺省 = 按平台特征表推导；表外平台 fail-closed（cleared 恒 False）。
                check = _captcha_cleared_check_for(self._platform)
            bridge = InterventionBridge(
                _MarshalledPage(self._marshal),
                cleared_check=check,
            )
            self.port = bridge.start()
            self.alive = True
            self._ready_evt.set()
            # handler 线程的一切 playwright 调用 marshal 回本线程（_pump_calls）执行
            self._pump_calls(page)  # 串行执行 marshal 来的调用；stop/寿命尽则返
        except Exception as exc:  # noqa: BLE001 — 记录后经 start() 回抛，finally 全清
            self.error = exc
            self._ready_evt.set()  # 唤醒 start() 的等待者去读 error
        finally:
            self.alive = False
            try:
                if bridge is not None:
                    bridge.stop()
            except Exception:
                pass
            self._drain_calls()  # 拒答关停后到达的 marshal 请求，防 handler 吊死
            try:
                if browser is not None:
                    # connect_over_cdp 的 close 只断 CDP 连接——绝不杀常驻浏览器进程、
                    # 绝不 close context/page（profile/登录态归 supervisor）。
                    browser.close()
            except Exception:
                pass
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
            if locked:
                try:
                    lock.release()
                except Exception:
                    pass
            _patch_registry(self._ticket_hash, state="closed")
            self.port = None

    def _pick_page(self, browser: Any) -> Any:
        """选页：撞码页优先（逐 candidate 跑本平台的 _captcha_hit），都不命中取
        pages[0]。表外平台无特征可判，直接 pages[0]。"""
        contexts = list(getattr(browser, "contexts", None) or [])
        pages = list(getattr(contexts[0], "pages", None) or []) if contexts else []
        hit = _CAPTCHA_HIT_BY_PLATFORM.get(self._platform)
        if hit is not None:
            for cand in pages:
                # 选页本就在 owner 线程：用直调 shim 套 marshal 门面跑 _captcha_hit
                # （同一接口子集，但探测发生在 _pump_calls 启动前，不能走队列泵）。
                def _direct(fn: Callable[[Any], Any], p: Any = cand) -> Any:
                    return fn(p)

                facade = _MarshalledPage(_direct)
                try:
                    if hit(facade):
                        return cand
                except Exception:
                    continue
        if pages:
            return pages[0]
        # 不该替用户开页（contexts[0].new_page() 否决）——如实上报，可重试
        raise ApplicationError(
            f"resident browser has no page to attach for {self._platform}",
            type="assist_no_page",
        )

    # ── marshal 泵：一切 playwright 调用回本线程串行执行（greenlet 非线程安全） ──
    def run_on_page(self, fn: Callable[[Any], Any]) -> Any:
        """``_marshal`` 的公开门面：在 owner 线程对当前页执行 ``fn(page)``。

        供 CLI（tools/otp_assist_login.py）等外部调用方做会话期页面操作
        （goto / 读 url、title / 登录态探测）。会话已停 → RuntimeError("assist_closed")。
        """
        return self._marshal(fn)

    def _marshal(self, fn: Callable[[Any], Any]) -> Any:
        """把 ``fn(page)`` 编组进 assist 专属线程执行；结果/异常回抛给调用线程。"""
        if self._stop_evt.is_set():
            raise RuntimeError("assist_closed")
        resq: queue.Queue[tuple[bool, Any]] = queue.Queue()
        self._call_q.put((fn, resq))
        ok, val = resq.get()
        if not ok:
            raise val
        return val

    def _pump_calls(self, page: Any) -> None:
        """owner 线程泵：取队列里的 fn(page) 串行执行，直到 stop 或 max_lifetime 兜底自杀。"""
        deadline = time.monotonic() + self._max_lifetime_s
        while not self._stop_evt.is_set():
            try:
                fn, resq = self._call_q.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    return  # 兜底自杀：忘接管也不留孤儿会话/不放干锁
                continue
            if fn is None:
                return
            try:
                resq.put((True, fn(page)))
            except Exception as exc:  # noqa: BLE001 — 异常回抛调用线程
                resq.put((False, exc))

    def _drain_calls(self) -> None:
        """泵已退：给队列里剩余的 marshal 请求回错误，防 handler 线程永远吊在 resq 上。"""
        while True:
            try:
                fn, resq = self._call_q.get_nowait()
            except queue.Empty:
                return
            if fn is not None:
                resq.put((False, RuntimeError("assist_closed")))


# ── 会话注册表：key=run_pub_id → AssistSession（幂等门面；模块级锁） ─────────────
_SESSIONS: dict[str, AssistSession] = {}
_SESSIONS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Temporal activity 契约（workflow 侧按此调用，签名一字不能改）
# ---------------------------------------------------------------------------


@dataclass
class CaptchaAssistInput:
    tenant_pub_id: str
    run_pub_id: str
    platform: str  # 五平台 slug（doubao/deepseek/tongyi/yiyan/yuanbao）
    business_key: str  # 撞码题
    evidence_ref: str | None = None
    # 浏览器矩阵化（2026-08-09 起）：撞码 batch 实际使用的常驻实例键
    # （doubao_sh 等）——锁/CDP/fence 都按实例键，assist 接管 attach 同一台
    # 常驻浏览器；特征表/页面逻辑仍按 platform slug。None（旧调用/旧历史
    # payload）→ 回退用 platform 取锁/CDP（启用矩阵化前的行为）。
    instance_key: str | None = None


@dataclass
class CaptchaAssistStarted:
    session_id: str
    assist_url: str
    pushed: bool


@dataclass
class CaptchaAssistStopInput:
    run_pub_id: str
    session_id: str


@activity.defn(name="captcha_assist_start")
async def captcha_assist_start(input: CaptchaAssistInput) -> CaptchaAssistStarted:
    """启动/复用撞码接管会话（幂等：同 run_pub_id 不双开 attach、不重推送）。

    async activity 跑在 worker 事件循环上，而会话启动全程同步阻塞（sess.start
    就绪等最长 30s + 推送 urllib + 全程持 _SESSIONS_LOCK）——2026-08-07 起整个
    阻塞体经 ``asyncio.to_thread`` 挪出事件循环（与 batch activity 的
    ``_blocking``/to_thread 同款，豆包 2026-08-06 循环直跑 sync API 事故教训），
    避免撞码接管期间吊死同 loop 上 batch activity 的 heartbeat。锁在 to_thread
    工作线程内持有，并发 start 仍串行，语义与持锁直跑完全一致；直接 await 本
    函数的调用方（drill 脚本/单测）零感知。
    """
    return await asyncio.to_thread(_captcha_assist_start_blocking, input)


def _captcha_assist_start_blocking(input: CaptchaAssistInput) -> CaptchaAssistStarted:
    """captcha_assist_start 的同步阻塞体（在 to_thread 工作线程里跑）。"""
    ttl_s = _ttl_s()
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(input.run_pub_id)
        if sess is not None and sess.alive:
            return CaptchaAssistStarted(
                session_id=sess.session_id, assist_url=sess.assist_url, pushed=sess.pushed
            )
        if sess is not None:  # 旧会话已死 → 清掉重开
            try:
                sess.stop()
            except Exception:
                pass

        base = os.environ.get("GEO_ASSIST_PUBLIC_BASE", "").strip()
        notify_url = os.environ.get("GEO_ASSIST_NOTIFY_URL", "").strip()
        flavor = os.environ.get("GEO_ASSIST_NOTIFY_FLAVOR", "raw").strip().lower() or "raw"
        chat_id = os.environ.get("GEO_FEISHU_CHAT_ID", "").strip()
        notification_configured = bool(chat_id) if flavor == "feishu_app" else bool(notify_url)
        if not base or not notification_configured:
            # 未配置公网基址/推送通道 = 功能未启用：fail fast 让 workflow 立即回
            # 退现行 wall+abort 语义。绝不能起了会话空等 60min——收不到通知的
            # "等人工"等于白挂起（未配置的生产行为必须与启用前逐字节一致）。
            raise ApplicationError(
                "captcha assist notification configuration is incomplete",
                type="assist_not_configured",
                non_retryable=True,
            )

        # 浏览器矩阵化：实例键第一段恒为平台 slug（browser_router 契约）——
        # 失配说明 workflow/activity 路由出错，fail-closed 绝不 attach 错浏览器。
        if input.instance_key:
            key_platform = input.instance_key.strip().lower().split("_", 1)[0]
            if key_platform != input.platform:
                raise ApplicationError(
                    f"instance_key {input.instance_key!r} does not belong to platform "
                    f"{input.platform!r}",
                    type="assist_instance_platform_mismatch",
                    non_retryable=True,
                )

        ticket = secrets.token_urlsafe(32)
        session_id = secrets.token_urlsafe(24)
        th = _ticket_hash(ticket)
        now = int(time.time())
        sess = AssistSession(
            platform=input.platform,
            run_pub_id=input.run_pub_id,
            session_id=session_id,
            ticket_hash=th,
            max_lifetime_s=ttl_s,
            instance_key=input.instance_key,
        )
        # 持锁启动（管理员低频动作）：同 run 并发 start 只赢出一个会话。
        # 启动失败（无 CDP/无页/锁忙）异常原样上抛，finally 已全清，不进注册表。
        sess.start()

        base = os.environ.get("GEO_ASSIST_PUBLIC_BASE", "").strip()
        assist_url = f"{base.rstrip('/')}/api/v2/assist/{ticket}"
        record = {
            "version": 1,
            "session_kind": "workflow_captcha",
            "tenant_pub_id": input.tenant_pub_id,
            "run_pub_id": input.run_pub_id,
            "session_id": session_id,
            "ticket_hash": th,
            "port": sess.port,
            "platform": input.platform,
            # 浏览器矩阵化：实际 attach 的常驻实例键（ops 台账用；旧调用=None→
            # 回退平台 slug，与锁/CDP 口径一致）。
            "instance_key": sess._instance_key,
            "state": "active",
            "business_key": input.business_key,
            "evidence_ref": input.evidence_ref,
            "created_at": now,
            "expires_at": now + ttl_s,
            "push_sent": False,
            "solved_at": None,
        }
        _write_registry(record)

        pushed = False
        label = _PLATFORM_LABELS.get(input.platform, input.platform)
        title = f"[GEO] {label}采集撞验证码，点此接管"
        body = (
            f"平台: {label}\n"
            f"撞码 query: {input.business_key}\n"
            f"有效期: {round(ttl_s / 60)} 分钟\n"
            f"接管链接: {assist_url}"
        )
        if flavor == "feishu_app":
            notification_id = _enqueue_feishu_app_assist(
                tenant_pub_id=input.tenant_pub_id,
                session_kind="workflow_captcha",
                run_pub_id=input.run_pub_id,
                session_id=session_id,
                ticket_hash=th,
                platform=input.platform,
                instance_key=sess._instance_key,
                business_key=input.business_key,
                created_at=now,
                expires_at=now + ttl_s,
                chat_id=chat_id,
            )
            pushed = notification_id is not None
            if notification_id is not None:
                _patch_registry(
                    th,
                    push_sent=True,
                    delivery_enqueued=True,
                    notification_id=notification_id,
                )
        else:
            pushed = push_captcha_assist(
                flavor=flavor,
                url=notify_url,
                title=title,
                body=body,
            )
            if pushed:
                _patch_registry(th, push_sent=True)
        if not pushed:
            # 推送失败仍保留会话：operator 可能恰好在本机（/ops 台账可查），
            # 且 60min 等待窗内随时可人工补推——不因此废掉这次接管机会。
            log.warning("captcha_assist.push_failed", run_pub_id=input.run_pub_id, flavor=flavor)

        # The workflow never consumes assist_url.  With the app channel keep the
        # raw ticket out of Temporal history; the card sender derives a separate
        # signed capability from the registry digest.
        activity_assist_url = "" if flavor == "feishu_app" else assist_url
        sess.assist_url = activity_assist_url
        sess.pushed = pushed
        _SESSIONS[input.run_pub_id] = sess
        log.info(
            "captcha_assist.session_started",
            run_pub_id=input.run_pub_id,
            session_ref=hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12],
            platform=input.platform,
            pushed=pushed,
        )  # ticket 明文绝不进日志
        return CaptchaAssistStarted(
            session_id=session_id,
            assist_url=activity_assist_url,
            pushed=pushed,
        )


@activity.defn(name="captcha_assist_stop")
async def captcha_assist_stop(input: CaptchaAssistStopInput) -> None:
    """幂等关会话（best-effort：一切异常记日志不抛出，绝不阻断 workflow）。

    sess.stop() 的 t.join(timeout=15) 是同步阻塞——与 start 同理经
    ``asyncio.to_thread`` 挪出事件循环。
    """
    await asyncio.to_thread(_captcha_assist_stop_blocking, input)


def _captcha_assist_stop_blocking(input: CaptchaAssistStopInput) -> None:
    """captcha_assist_stop 的同步阻塞体（在 to_thread 工作线程里跑）。"""
    try:
        with _SESSIONS_LOCK:
            sess = _SESSIONS.pop(input.run_pub_id, None)
        if sess is None:
            return  # 无会话 → no-op
        if sess.session_id != input.session_id:
            log.warning(
                "captcha_assist.stop_session_mismatch",
                run_pub_id=input.run_pub_id,
                session_ref=hashlib.sha256(input.session_id.encode("utf-8")).hexdigest()[:12],
            )
        try:
            # 线程 finally：bridge.stop → browser.close(仅断 CDP) → pw.stop → 放锁 → 注册表 closed
            sess.stop()
        except Exception:
            log.warning("captcha_assist.stop_failed", run_pub_id=input.run_pub_id, exc_info=True)
        _patch_registry(sess.ticket_hash, state="closed")
        _mark_feishu_app_assist_state(sess.ticket_hash, "closed")
    except Exception:
        log.warning("captcha_assist.stop_unexpected", run_pub_id=input.run_pub_id, exc_info=True)
