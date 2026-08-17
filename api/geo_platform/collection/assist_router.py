# ruff: noqa: B008
"""撞验证码 → 手机人工接管 公网端点（ticket 即凭据，无登录）。

worker 侧 captcha_assist 撞码时在 ``runtime/captcha-assist/`` 落注册表
（文件名 = sha256(ticket).json，契约见 workflows/activities/captcha_assist.py），
本模块只消费：手机页 + 帧/状态/输入代理 + 人工完成回执。
先例：旧系统 ``/api/ops/remote/view/<token>`` 同样只做 ticket 鉴权。

安全口径：
- ticket 绝不落盘/记日志原文，只以 sha256 比对（``secrets.compare_digest``）；
- 文件缺失/hash 不符/过期/已关闭 一律 403 同文案，不泄露 ticket 存在性；
- 代理目标硬编码 127.0.0.1，端口必须落在 1024-65535（注册表被篡改也打不到内网）。
"""

from __future__ import annotations

import collections
import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ..notifications.config import FeishuBotConfig, read_secret_file
from ..notifications.security import CallbackSecurityError, verify_assist_capability
from ..tenancy.database import get_db
from .assist_completion import (
    AssistCompletionError,
    WorkflowSignalConflictError,
    prepare_assist_completion,
)
from .assist_registry import (
    DEFAULT_ASSIST_DIR,
    AssistRegistryError,
    load_registry_by_digest,
    mark_registry_solved,
    registry_path,
    session_kind,
    write_registry_atomic,
)

router = APIRouter(prefix="/api/v2", tags=["assist"])

# 注册表目录 = platform-v2/runtime/captcha-assist/。契约文档写的 parents[2] 是
# 从 worker 侧 workflows/activities/ 算的；本文件在 api/geo_platform/collection/
# 下，深一级，故 parents[3]。目录由 worker 创建，这里只读（/done 只原地替换文件）。
ASSIST_DIR = DEFAULT_ASSIST_DIR

_TICKET_MAX_LEN = 256
_INPUT_MAX_BYTES = 8192
_BRIDGE_TIMEOUT = 2.0

# 简易频控：每 ticket 滑窗（进程内状态即可，单 API 进程部署）。
_RATE_LIMITS: dict[str, tuple[int, float]] = {"frame": (3, 1.0), "input": (20, 1.0)}
_rate_buckets: dict[str, collections.deque[float]] = {}
_rate_lock = threading.Lock()
# /done 注册表 read-modify-write 的进程内锁（跨进程与 worker 的并发靠原子替换兜底）。
_registry_lock = threading.Lock()


def _denied() -> HTTPException:
    # 所有 ticket 校验失败共用同一文案，不区分原因、不泄露存在性。
    return HTTPException(status_code=403, detail={"code": "assist_ticket_invalid"})


def _registry_path(ticket: str) -> Path:
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    return registry_path(ASSIST_DIR, digest)


def _load_registry(ticket: str) -> dict[str, Any]:
    """按 ticket 读注册表并做全部有效性校验；任何失败都是同一个 403。"""
    if not ticket or len(ticket) > _TICKET_MAX_LEN:
        raise _denied()
    try:
        return load_registry_by_digest(
            ASSIST_DIR,
            hashlib.sha256(ticket.encode("utf-8")).hexdigest(),
        )
    except AssistRegistryError:
        raise _denied() from None


def _load_notification_registry(notification_id: str, capability: str) -> dict[str, Any]:
    if not notification_id.startswith("ntf_") or len(notification_id) > 80:
        raise _denied()
    try:
        config = FeishuBotConfig.from_env()
        key = read_secret_file(
            config.link_signing_key_file,
            label="feishu_link_signing_key",
            min_length=32,
        )
        ticket_sha256, _expires_at = verify_assist_capability(
            notification_id=notification_id,
            capability=capability,
            key=key,
        )
        return load_registry_by_digest(ASSIST_DIR, ticket_sha256)
    except (AssistRegistryError, CallbackSecurityError, RuntimeError, ValueError):
        raise _denied() from None


def _bridge_port(registry: dict[str, Any]) -> int:
    port = registry.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise _denied()
    return port


def _write_registry_atomic(path: Path, data: dict[str, Any]) -> None:
    """同目录临时文件 + os.replace，worker 侧读到的永远是完整 JSON。"""
    write_registry_atomic(path, data)


def _rate_limited(kind: str, ticket: str) -> bool:
    limit, window = _RATE_LIMITS[kind]
    now = time.monotonic()
    # 桶键用 hash 不用原文，内存里也不留 ticket。
    key = f"{kind}:{hashlib.sha256(ticket.encode('utf-8')).hexdigest()}"
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, collections.deque())
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        if len(_rate_buckets) > 4096:  # 防膨胀：顺手清掉已流空的桶
            for stale in [name for name, items in _rate_buckets.items() if not items]:
                del _rate_buckets[stale]
    return False


class _BridgeDown(Exception):
    pass


def _bridge(port: int, method: str, path: str, *, content: bytes | None = None) -> httpx.Response:
    # trust_env=False：宿主机可能有系统代理 env，127.0.0.1 绝不能走代理。
    try:
        return httpx.request(
            method,
            f"http://127.0.0.1:{port}{path}",
            content=content,
            headers={"Content-Type": "application/json"} if content is not None else None,
            timeout=_BRIDGE_TIMEOUT,
            trust_env=False,
        )
    except httpx.HTTPError as error:
        raise _BridgeDown from error


def _bridge_unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "bridge_unavailable"})


@router.get("/assist/{ticket}", response_class=HTMLResponse)
def assist_page(ticket: str) -> HTMLResponse:
    """手机接管页：内联全部 JS/CSS，零外部依赖（公网可能无 CDN）。"""
    _load_registry(ticket)
    return HTMLResponse(_PAGE_HTML)


@router.get(
    "/assist/notification/{notification_id}/{capability}",
    response_class=HTMLResponse,
)
def notification_assist_page(notification_id: str, capability: str) -> HTMLResponse:
    """Card link backed by a short-lived HMAC capability, never a stored raw ticket."""
    _load_notification_registry(notification_id, capability)
    return HTMLResponse(_PAGE_HTML)


def _frame_response(registry: dict[str, Any], *, rate_key: str) -> Response:
    if _rate_limited("frame", rate_key):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    port = _bridge_port(registry)
    try:
        upstream = _bridge(port, "GET", "/frame")
    except _BridgeDown:
        return _bridge_unavailable()
    if upstream.status_code != 200:
        return JSONResponse(status_code=upstream.status_code, content={"error": "bridge_error"})
    return Response(content=upstream.content, media_type="image/jpeg")


@router.get("/assist/{ticket}/frame")
def assist_frame(ticket: str) -> Response:
    registry = _load_registry(ticket)
    return _frame_response(registry, rate_key=ticket)


@router.get("/assist/notification/{notification_id}/{capability}/frame")
def notification_assist_frame(notification_id: str, capability: str) -> Response:
    registry = _load_notification_registry(notification_id, capability)
    return _frame_response(registry, rate_key=capability)


def _status_response(registry: dict[str, Any]) -> JSONResponse:
    port = _bridge_port(registry)
    try:
        upstream = _bridge(port, "GET", "/status")
    except _BridgeDown:
        return _bridge_unavailable()
    if upstream.status_code != 200:
        return JSONResponse(status_code=upstream.status_code, content={"error": "bridge_error"})
    try:
        body: Any = upstream.json()
    except ValueError:
        body = {}
    merged = body if isinstance(body, dict) else {"bridge_status": body}
    merged["state"] = registry.get("state")
    merged["expires_at"] = registry.get("expires_at")
    merged["solved_at"] = registry.get("solved_at")
    merged["platform"] = registry.get("platform")
    merged["business_key"] = registry.get("business_key")
    merged["session_kind"] = session_kind(registry)
    return JSONResponse(status_code=200, content=merged)


@router.get("/assist/{ticket}/status")
def assist_status(ticket: str) -> JSONResponse:
    registry = _load_registry(ticket)
    return _status_response(registry)


@router.get("/assist/notification/{notification_id}/{capability}/status")
def notification_assist_status(notification_id: str, capability: str) -> JSONResponse:
    return _status_response(_load_notification_registry(notification_id, capability))


async def _input_response(
    registry: dict[str, Any], *, rate_key: str, request: Request
) -> JSONResponse:
    if _rate_limited("input", rate_key):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    port = _bridge_port(registry)
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            declared = _INPUT_MAX_BYTES + 1
        if declared > _INPUT_MAX_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    body = await request.body()
    if len(body) > _INPUT_MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    try:
        upstream = _bridge(port, "POST", "/input", content=body)
    except _BridgeDown:
        return _bridge_unavailable()
    try:
        payload: Any = upstream.json()
    except ValueError:
        payload = {"ok": upstream.is_success}
    return JSONResponse(status_code=upstream.status_code, content=payload)


@router.post("/assist/{ticket}/input")
async def assist_input(ticket: str, request: Request) -> JSONResponse:
    registry = _load_registry(ticket)
    return await _input_response(registry, rate_key=ticket, request=request)


@router.post("/assist/notification/{notification_id}/{capability}/input")
async def notification_assist_input(
    notification_id: str, capability: str, request: Request
) -> JSONResponse:
    registry = _load_notification_registry(notification_id, capability)
    return await _input_response(registry, rate_key=capability, request=request)


def _complete_registry(
    registry: dict[str, Any], *, ticket_sha256: str, session: Session
) -> dict[str, bool]:
    try:
        prepare_assist_completion(
            session,
            registry=registry,
            ticket_sha256=ticket_sha256,
        )
        session.commit()
    except WorkflowSignalConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from error
    except AssistCompletionError as error:
        session.rollback()
        code = str(error)
        if code == "assist_run_not_found":
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from error
        raise _denied() from error
    with _registry_lock:
        registry_finalized = mark_registry_solved(ASSIST_DIR, ticket_sha256)
    if not registry_finalized:
        # The database effect is already durable and idempotent. A 503 asks the
        # caller to retry only the file-finalization side of this crash window.
        raise HTTPException(status_code=503, detail={"code": "assist_finalize_pending"})
    return {"ok": True}


@router.post("/assist/{ticket}/done")
def assist_done(ticket: str, session: Session = Depends(get_db)) -> dict[str, bool]:
    """人工确认完成：按 session_kind 落库，再原子更新注册表。

    workflow captcha 走幂等 signal outbox；OTP CLI 不查询 run、不发 signal。
    两类都保持 DB 先于文件，重复请求可补写崩溃窗口里的注册表。
    """
    registry = _load_registry(ticket)
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    return _complete_registry(registry, ticket_sha256=digest, session=session)


@router.post("/assist/notification/{notification_id}/{capability}/done")
def notification_assist_done(
    notification_id: str,
    capability: str,
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    registry = _load_notification_registry(notification_id, capability)
    digest = str(registry["ticket_hash"])
    return _complete_registry(registry, ticket_sha256=digest, session=session)


_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>GEO 采集人工协助</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; background: #0b1020; color: #e5e9f2;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    overscroll-behavior: none; }
  body { display: flex; flex-direction: column; touch-action: none; }
  header { padding: 10px 14px; background: #131a30; border-bottom: 1px solid #232c4d;
    font-size: 14px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .lamp { width: 10px; height: 10px; border-radius: 50%; background: #f5b942;
    display: inline-block; flex: none; }
  .lamp.ok { background: #3dd68c; }
  .lamp.dead { background: #6b7280; }
  #biz { color: #9aa7cc; word-break: break-all; margin-top: 6px; }
  #countdown { margin-left: auto; font-variant-numeric: tabular-nums; color: #f5b942; }
  main { flex: 1; position: relative; overflow: hidden; }
  #stage { position: absolute; inset: 0; }
  #frame { width: 100%; height: 100%; object-fit: contain; touch-action: none;
    user-select: none; -webkit-user-select: none; }
  #trail { position: absolute; inset: 0; pointer-events: none; }
  #banner { display: none; height: 100%; align-items: center; justify-content: center;
    text-align: center; font-size: 26px; line-height: 1.6; color: #3dd68c; padding: 24px; }
  #fatal { display: none; height: 100%; align-items: center; justify-content: center;
    text-align: center; font-size: 20px; color: #f67373; padding: 24px; }
  #doneBtn { margin: 12px 16px calc(16px + env(safe-area-inset-bottom)); padding: 16px;
    font-size: 18px; border: none; border-radius: 12px; background: #2f6fed; color: #fff;
    touch-action: manipulation; }
  #doneBtn:disabled { background: #2a3350; color: #7c86a5; }
</style>
</head>
<body>
<header>
  <div class="row"><span id="lamp" class="lamp"></span><b id="platform">人工协助</b>
    <span id="countdown">--:--</span></div>
  <div id="biz"></div>
</header>
<main>
  <div id="stage">
    <img id="frame" alt="">
    <canvas id="trail"></canvas>
  </div>
  <div id="banner">✅ 已解决，采集已自动恢复</div>
  <div id="fatal">会话已结束或已过期</div>
</main>
<button id="doneBtn">我已完成，继续采集</button>
<script>
(function () {
  'use strict';
  // ticket 不硬编码：从当前 URL path 取，fetch 全部相对当前页地址。
  var base = location.pathname.replace(/\\/+$/, '');
  var img = document.getElementById('frame');
  var stage = document.getElementById('stage');
  var trail = document.getElementById('trail');
  var ctx = trail.getContext('2d');
  var banner = document.getElementById('banner');
  var fatal = document.getElementById('fatal');
  var doneBtn = document.getElementById('doneBtn');
  var lamp = document.getElementById('lamp');
  var countdownEl = document.getElementById('countdown');
  var bizEl = document.getElementById('biz');
  var platformEl = document.getElementById('platform');
  var finished = false;
  var frameURL = null;
  var frameFails = 0;
  var expiresAt = 0;
  var autoDoneSent = false;

  function setText(el, value) { el.textContent = value == null ? '' : String(value); }

  function showSolved() {
    if (finished) return;
    finished = true;
    stage.style.display = 'none';
    fatal.style.display = 'none';
    banner.style.display = 'flex';
    doneBtn.disabled = true;
    lamp.className = 'lamp ok';
    setText(platformEl, '已解决');
  }

  function showFatal() {
    if (finished) return;
    finished = true;
    stage.style.display = 'none';
    fatal.style.display = 'flex';
    doneBtn.disabled = true;
    lamp.className = 'lamp dead';
    setText(platformEl, '会话结束');
  }

  function postDone() {
    return fetch(base + '/done', { method: 'POST' }).then(function (r) {
      if (r.ok) showSolved();
      return r.ok;
    }).catch(function () { return false; });
  }

  function refreshFrame() {
    if (finished) return;
    fetch(base + '/frame', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('frame:' + r.status);
      return r.blob();
    }).then(function (blob) {
      frameFails = 0;
      var url = URL.createObjectURL(blob);
      img.src = url;
      if (frameURL) URL.revokeObjectURL(frameURL);
      frameURL = url;
    }).catch(function () {
      frameFails += 1;
      if (frameFails >= 3) showFatal();
    });
  }

  function pollStatus() {
    if (finished) return;
    fetch(base + '/status', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('status:' + r.status);
      return r.json();
    }).then(function (s) {
      if (s.platform) setText(platformEl, s.platform + ' 人工协助');
      if (s.session_kind === 'otp_cli') {
        doneBtn.textContent = '我已完成登录';
        if (s.business_key) setText(bizEl, '登录事项：' + s.business_key);
      } else if (s.business_key) {
        setText(bizEl, '撞码题：' + s.business_key);
      }
      if (typeof s.expires_at === 'number') expiresAt = s.expires_at;
      if (s.state === 'solved') { showSolved(); return; }
      if (s.state === 'closed') { showFatal(); return; }
      if (s.cleared === true && !autoDoneSent) {
        autoDoneSent = true;  // cleared 检测兜底自动确认，防重复提交
        postDone();
      }
    }).catch(function () { /* 单轮失败忽略，下一轮再试 */ });
  }

  // 倒计时走 expires_at 本地推算，状态轮询只负责校准。
  setInterval(function () {
    if (!expiresAt) { setText(countdownEl, '--:--'); return; }
    var left = Math.max(0, Math.round(expiresAt - Date.now() / 1000));
    var m = Math.floor(left / 60);
    var s = left % 60;
    setText(countdownEl, (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s);
    if (left <= 0 && !finished) showFatal();
  }, 1000);

  // ── 触摸交互：位移>10px 判拖拽，≤10px 且 <400ms 判点击 ──
  var tracking = false, sx = 0, sy = 0, lx = 0, ly = 0, st = 0;

  function resizeTrail() {
    trail.width = stage.clientWidth;
    trail.height = stage.clientHeight;
  }

  function drawTrail(x0, y0, x1, y1) {
    var r = stage.getBoundingClientRect();
    ctx.clearRect(0, 0, trail.width, trail.height);
    ctx.strokeStyle = '#f5b942';
    ctx.lineWidth = 4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x0 - r.left, y0 - r.top);
    ctx.lineTo(x1 - r.left, y1 - r.top);
    ctx.stroke();
    ctx.fillStyle = '#f5b942';
    ctx.beginPath();
    ctx.arc(x1 - r.left, y1 - r.top, 8, 0, Math.PI * 2);
    ctx.fill();
  }

  function clearTrail() { ctx.clearRect(0, 0, trail.width, trail.height); }

  // 显示像素 → 帧页面坐标。img 是 object-fit:contain：元素盒内有上下/左右
  // 留白，必须先求出图像在元素内的实际渲染矩形（否则手机竖屏下 y 全偏，
  // 拖拽全落在错误位置——20260807 真机演练实证）。
  function toFrame(cx, cy) {
    var r = img.getBoundingClientRect();
    var nw = img.naturalWidth, nh = img.naturalHeight;
    if (!r.width || !r.height || !nw || !nh) return [0, 0];
    var scale = Math.min(r.width / nw, r.height / nh);
    var offX = r.left + (r.width - nw * scale) / 2;
    var offY = r.top + (r.height - nh * scale) / 2;
    return [
      Math.max(0, Math.round((cx - offX) / scale)),
      Math.max(0, Math.round((cy - offY) / scale))
    ];
  }

  function sendInput(payload) {
    fetch(base + '/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch(function () { /* 下一帧会反映真实状态 */ });
  }

  stage.addEventListener('touchstart', function (e) {
    e.preventDefault();
    if (finished) return;
    var t = e.changedTouches[0];
    tracking = true;
    sx = lx = t.clientX;
    sy = ly = t.clientY;
    st = Date.now();
    resizeTrail();
    clearTrail();
  }, { passive: false });
  stage.addEventListener('touchmove', function (e) {
    e.preventDefault();
    if (!tracking) return;
    var t = e.changedTouches[0];
    lx = t.clientX;
    ly = t.clientY;
    drawTrail(sx, sy, lx, ly);  // 拖拽轨迹可视化，用户能看见自己在画什么
  }, { passive: false });
  stage.addEventListener('touchend', function (e) {
    e.preventDefault();
    if (!tracking) return;
    tracking = false;
    clearTrail();
    var dx = lx - sx;
    var dy = ly - sy;
    var dist = Math.sqrt(dx * dx + dy * dy);
    var dur = Date.now() - st;
    if (dist > 10) {
      sendInput({ type: 'drag', start: toFrame(sx, sy), end: toFrame(lx, ly) });
    } else if (dur < 400) {
      sendInput({ type: 'click', at: toFrame(lx, ly) });
    }
  }, { passive: false });
  stage.addEventListener('touchcancel', function () {
    tracking = false;
    clearTrail();
  });

  // 微信 X5 webview 在 body{touch-action:none} 下按钮 click 可能不合成——
  // click/touchend 双绑 + 去抖，任一通道触发即算数。
  var doneFiredAt = 0;
  function doneHandler(e) {
    var now = Date.now();
    if (now - doneFiredAt < 800) return;
    doneFiredAt = now;
    if (e && e.type === 'touchend') e.preventDefault();
    postDone();
  }
  doneBtn.addEventListener('click', doneHandler);
  doneBtn.addEventListener('touchend', doneHandler);

  setInterval(refreshFrame, 700);
  setInterval(pollStatus, 2000);
  refreshFrame();
  pollStatus();
})();
</script>
</body>
</html>
"""
