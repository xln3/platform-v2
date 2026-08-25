"""V2 原生 OTP 收件端点（公网机器面，无会话，共享密钥门）。

旧系统 geosys 2026-08-07 退役，OTP 推送链整体迁入 V2。契约对齐旧
``server/geosys/otp_ingest.py``（docstring 即契约），代码 V2 原生：

  * ``POST /api/v2/otp/push``    末端 Android SmsForwarder 或 iPhone Apple
                                 快捷指令推「手机号+验证码短信」。
                                 ``X-Relay-Token`` 对 env ``GEO_OTP_RELAY_TOKEN``
                                 （``secrets.compare_digest`` 常量时间比较）；
                                 **env 未配 → fail-closed 503**（功能关闭语义，
                                 同 assist_router 的 GEO_ASSIST_* fail-fast 惯例）；
                                 token 错 → 401。无 session/租户（公网机器面，
                                 手机不可能带登录会话，同 assist ticket 免登录模式）。
                                 服务端权威抽码（正则级联，**V2 首版无 LLM**，见
                                 extract.py docstring），原子落 ``<inbox>/<phone>.json``。
  * ``GET /api/v2/otp/latest``   统一取码口：``?phone=<11位>&within=180`` → 取
                                 within 秒内到达的验证码。``X-Operator-Token`` 对
                                 env ``GEO_OTP_OPERATOR_TOKEN``（同款 fail-closed）。
                                 返回**明文 code**——验证码是敏感 operator 机密，
                                 门够硬即可，绝不进客户租户面。

记录契约（每手机号一文件，原子写，poller 绝不读到半写）::

    <inbox>/<phone>.json = {"ts": <服务端 epoch>, "phone", "code",
                            "raw", "from", "platform"[, "meta"]}

``ts`` 用**服务端自己的钟**（time.time()）——与取码/登录的窗判定同钟，免疫
转发手机钟不准。收件箱目录 env ``GEO_OTP_INBOX_DIR``，缺省
``platform-v2/runtime/otp_inbox/``（自动建目录）。另有 append-only JSONL 台账
``<inbox>/otp_events.jsonl``（best-effort，失败只 warning，绝不阻断推送）。

秘密纪律：响应与日志**绝不出现完整明文码**（push 响应只给 code_len；phone 一律
掩码中间四位）；明文码只在 latest 响应（operator 门内）与收件箱文件里。
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from ..collection.otp_bridge import record_sms_received, upsert_phone_account
from ..tenancy.database import SessionLocal
from .extract import (
    HINT_RE,
    PHONE_RE,
    extract_otp_code,
    mask_phone,
    normalize_push,
    phone_from_slot,
    platform_of,
    standalone,
)

router = APIRouter(prefix="/api/v2", tags=["otp"])

log = structlog.get_logger()

# api/geo_platform/otp/router.py → parents[3] = platform-v2/
_DEFAULT_INBOX_DIR = Path(__file__).resolve().parents[3] / "runtime" / "otp_inbox"

_BODY_MAX_BYTES = 65536  # SMS 体很小；公网面兜底防滥（同 assist_router 的 size 门惯例）

_TZ_CN = ZoneInfo("Asia/Shanghai")  # status 时间戳显示口径（运维/手机同一时区）

_DEFAULT_WITHIN_S = 180  # 默认取「3 分钟内」的验证码（旧系统用户设计）
_MAX_WITHIN_S = 900  # 上限 15 分钟（防把远古旧码当新码返回）

_NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _sensitive_json(content: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    """Return operator data with an explicit browser/proxy no-store boundary."""
    return JSONResponse(status_code=status_code, content=content, headers=_NO_STORE_HEADERS)


# 简易频控：每 phone 滑窗（进程内状态；生产 uvicorn --workers 2，故实际限额≈
# 配置值×worker 数——push/latest 的真实流量远低于此，放宽无感；同 assist_router 惯例）。
# push：真实短信推送每号每分钟寥寥几条；latest：otp_wait 2s 轮询=30 次/分/号，
# 120 给足多 poller 余量。
_RATE_LIMITS: dict[str, tuple[int, float]] = {"push": (20, 60.0), "latest": (120, 60.0)}
_rate_buckets: dict[str, collections.deque[float]] = {}
_rate_lock = threading.Lock()

# meta 键：URL query 补空缺（body 已解析出的值更权威，优先）——比 body 稳
# （SMS 正文含换行/引号会破坏 JSON body→丢字段），旧链 live 教训。
_QUERY_META_KEYS = (
    ("slot", "sim_slot"),
    ("card_slot", "sim_slot"),
    ("subid", "sub_id"),
    ("sub_id", "sub_id"),
    ("card_subid", "sub_id"),
    ("siminfo", "sim_info"),
    ("sim_info", "sim_info"),
)

# ``platform`` can be supplied by the lower-privilege relay client, then rendered in
# the operator page. Keep this an explicit, short vocabulary instead of persisting an
# arbitrary label. Both current Chinese SMS signatures and adapter slugs are accepted.
_PLATFORM_ALIASES = {
    "豆包": "豆包",
    "doubao": "doubao",
    "深度求索": "深度求索",
    "deepseek": "deepseek",
    "文心一言": "文心一言",
    "百度": "百度",
    "yiyan": "yiyan",
    "通义": "通义",
    "千问": "千问",
    "通义千问": "通义千问",
    "qwen": "qwen",
    "元宝": "元宝",
    "腾讯元宝": "腾讯元宝",
    "yuanbao": "yuanbao",
    "博客园": "博客园",
    "cnblogs": "cnblogs",
    "搜狐": "搜狐",
    "sohu": "sohu",
    "百家号": "百家号",
    "baijiahao": "baijiahao",
    "今日头条": "今日头条",
    "头条号": "头条号",
    "toutiao": "toutiao",
    "网易新闻": "网易新闻",
}
_PLATFORM_BY_CASEFOLD = {key.casefold(): value for key, value in _PLATFORM_ALIASES.items()}


def _controlled_platform(value: object) -> str:
    """Canonicalize a relay-controlled platform label without dropping the SMS.

    Empty remains valid because some SMS bodies do not carry a recognizable platform.
    Unknown, overlong, or control-bearing labels are quarantined as empty instead of
    rejecting the entire push: the label stays controlled while a new vendor spelling
    cannot make a valid OTP disappear.
    """
    candidate = str(value or "").strip()
    has_control = any(unicodedata.category(char).startswith("C") for char in candidate)
    canonical = _PLATFORM_BY_CASEFOLD.get(candidate.casefold())
    if candidate and len(candidate) <= 24 and not has_control and canonical is not None:
        return canonical
    return ""


def _inbox_dir() -> Path:
    """``<phone>.json`` 落盘目录。``GEO_OTP_INBOX_DIR`` 覆盖默认；每次调用时读，
    好让 API 与工具共享一目录、测试指向 tmp。"""
    return Path(os.environ.get("GEO_OTP_INBOX_DIR") or _DEFAULT_INBOX_DIR)


def _require_token(
    request: Request,
    *,
    env_name: str,
    header_name: str,
    disabled_code: str,
    unauthorized_code: str,
    error_headers: dict[str, str] | None = None,
) -> None:
    """共享密钥门（常量时间比较）。env 未配 → fail-closed 503（功能关闭，绝不
    开放无鉴权端点）；只认 header——不设 ?token= 兜底（会落日志/Referer）。"""
    configured = os.environ.get(env_name, "") or ""
    if not configured:
        raise HTTPException(status_code=503, detail={"code": disabled_code}, headers=error_headers)
    supplied = request.headers.get(header_name, "") or ""
    if not supplied or not secrets.compare_digest(supplied, configured):
        if env_name == "GEO_OTP_OPERATOR_TOKEN":
            log.warning(
                "otp_operator_auth_failed",
                actor="otp_operator_shared_token",
                path=request.url.path,
                remote_addr=request.client.host if request.client else "-",
                request_id=getattr(request.state, "request_id", ""),
            )
        raise HTTPException(
            status_code=401, detail={"code": unauthorized_code}, headers=error_headers
        )
    # ``otp_wait`` legitimately polls latest every two seconds. Nginx already keeps a
    # query-free access audit for that route, so duplicating every successful poll in
    # the application log adds noise without increasing accountability. Failures above
    # remain logged, as do successful human/admin actions on the other operator routes.
    if env_name == "GEO_OTP_OPERATOR_TOKEN" and request.url.path != "/api/v2/otp/latest":
        log.info(
            "otp_operator_access",
            actor="otp_operator_shared_token",
            path=request.url.path,
            remote_addr=request.client.host if request.client else "-",
            request_id=getattr(request.state, "request_id", ""),
            result="allowed",
        )


def _require_relay_token(request: Request) -> None:
    _require_token(
        request,
        env_name="GEO_OTP_RELAY_TOKEN",
        header_name="X-Relay-Token",
        disabled_code="otp_relay_disabled",
        unauthorized_code="otp_relay_unauthorized",
    )


def _require_operator_token(request: Request) -> None:
    _require_token(
        request,
        env_name="GEO_OTP_OPERATOR_TOKEN",
        header_name="X-Operator-Token",
        disabled_code="otp_operator_disabled",
        unauthorized_code="otp_operator_unauthorized",
        error_headers=_NO_STORE_HEADERS,
    )


def _rate_limited(kind: str, key: str) -> bool:
    limit, window = _RATE_LIMITS[kind]
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(f"{kind}:{key}", collections.deque())
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        if len(_rate_buckets) > 4096:  # 防膨胀：顺手清掉已流空的桶
            for stale in [name for name, items in _rate_buckets.items() if not items]:
                del _rate_buckets[stale]
    return False


def _atomic_write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    """同目录临时文件 + os.replace，读侧（latest/登录 seam）永远读到完整 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _append_event_best_effort(rec: dict[str, Any], *, code_source: str, remote_addr: str) -> None:
    """把本条推送追加进 append-only JSONL 台账（审计/对账）。best-effort：
    失败只 warning，绝不影响推送/取码。不放 raw（原文含码+PII，仅留存于
    每号收件箱文件，与旧链缺省不留原文同口径）。"""
    try:
        inbox = _inbox_dir()
        inbox.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": rec["ts"],
            "phone": rec["phone"],
            "platform": rec.get("platform") or "",
            "code": rec.get("code") or "",
            "code_source": code_source,
            "from": rec.get("from") or "",
            "remote_addr": remote_addr,
            "meta": rec.get("meta") or {},
        }
        with open(inbox / "otp_events.jsonl", "a", encoding="utf-8") as f:  # O_APPEND 并发不丢行
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — 台账失败绝不阻断推送（短信已落收件箱）
        log.warning(
            "otp_ledger_append_failed", phone=mask_phone(str(rec.get("phone") or "")), error=repr(e)
        )


def _clamp_within(raw: str | None) -> int:
    """把 ?within= 收进 [1, _MAX_WITHIN_S]，畸形/缺省 → 默认 180（3 分钟）。"""
    try:
        v = int(raw or "")
    except (TypeError, ValueError):
        return _DEFAULT_WITHIN_S
    return max(1, min(v, _MAX_WITHIN_S))


# ---------------------------------------------------------------------------
# OTP 迁库旁路（2026-08-13，采集账号治理 s06_0022）：文件链路仍是现状真源，
# collection_phone_account 行是镜像。两个钩子一律 best-effort——失败只 warning，
# 绝不阻断推送/注册现有链路（DB 未迁移/不可达时文件链路照常工作）。
# ---------------------------------------------------------------------------


def _sync_phone_account_best_effort(phone: str, owner_note: str | None) -> None:
    """注册 → upsert collection_phone_account（phone 唯一；slot/carrier→owner_note）。"""
    try:
        with SessionLocal() as session:
            upsert_phone_account(session, phone=phone, owner_note=owner_note)
            session.commit()
    except Exception as e:  # noqa: BLE001 — 迁库失败绝不阻断注册（文件已落）
        log.warning(
            "otp_phone_account_sync_failed",
            phone=mask_phone(phone),
            error_type=type(e).__name__,
        )


def _record_sms_best_effort(phone: str) -> None:
    """push 路由到号 → 回填 last_sms_at=now + sms_link_state='ok'（转码链路事实源）。"""
    try:
        with SessionLocal() as session:
            record_sms_received(session, phone=phone)
            session.commit()
    except Exception as e:  # noqa: BLE001 — 回填失败绝不阻断推送（收件箱已落）
        log.warning(
            "otp_sms_backfill_failed",
            phone=mask_phone(phone),
            error_type=type(e).__name__,
        )


def _extract_code(norm: dict[str, Any]) -> tuple[str, str, str]:
    """抽 (code, code_source, method)。V2 首版 **regex-only**：``extract_otp_code``
    正则级联为权威；转发器附带的 code hint 仅在被短信正文数字边界佐证时兜底
    （防幻觉）。旧链的 LLM 优先层不移植（见 extract.py docstring）。

    ``code_source`` 是台账闭合词汇 {extracted, hint, none}（+ T-39 'voice'，语音
    通道在 push 视图里短路，不走本函数）；``method`` {regex, hint, none} 记
    meta.extract_method 供审计，不进 code_source 词汇。"""
    raw = str(norm.get("raw") or "")
    code = extract_otp_code(raw) or ""
    method = "regex" if code else "none"
    hint = str(norm.get("code_hint") or "").strip()
    if not code and HINT_RE.match(hint) and standalone(hint, raw):
        code, method = hint, "hint"
    code_source = {"regex": "extracted", "hint": "hint", "none": "none"}[method]
    return code, code_source, method


@router.post("/otp/push")
async def otp_push(request: Request) -> JSONResponse:
    """处理一条手机推送（iPhone JSON ``{"phone","sms"}``、Android
    SmsForwarder JSON ``{"slot","sms"}`` / 表单 / 纯文本期望格式）。

    无法定位手机号时**软收下**存 ``unrouted.json``（绝不丢码；响应 ``routed=false``
    + 大声记日志），对齐旧 otp_ingest.otp_push_view 的全部容错路径。"""
    _require_relay_token(request)
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            declared = _BODY_MAX_BYTES + 1
        if declared > _BODY_MAX_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    body_text = (await request.body()).decode("utf-8", "replace")
    if len(body_text.encode("utf-8")) > _BODY_MAX_BYTES:  # Content-Length 缺失/撒谎的兜底
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})

    json_data: dict[str, Any] | None = None
    try:
        parsed: Any = json.loads(body_text)
        if isinstance(parsed, dict):
            json_data = parsed
    except ValueError:
        pass
    form: dict[str, str] = {}
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        form = dict(urllib.parse.parse_qsl(body_text, keep_blank_values=True))
    elif "multipart/form-data" in content_type:
        form = {k: str(v) for k, v in (await request.form()).items()}

    norm = normalize_push(json_data, form, body_text)
    query = request.query_params
    # 路由键兜底：body 没解析出合法手机号时取 URL ?phone=（SmsForwarder 体模板难配、
    # 或「测试」按钮发空体时手机号仍能从 URL 定路由——旧链 live 2026-07-14 逮到）。
    # phone 是路由键、非机密（不同于 token 绝不放 URL）。
    if not PHONE_RE.match(str(norm["phone"])):
        qp = (query.get("phone") or "").strip()
        if PHONE_RE.match(qp):
            norm["phone"] = qp
            if not (str(norm["raw"]) or "").strip():
                dec = urllib.parse.unquote(body_text) if "%" in body_text else body_text
                norm["raw"] = dec
                norm["platform"] = platform_of(dec)
    # 容错：JSON 模板被短信里的引号/换行破坏时（SmsForwarder 不转义 {{SMS}}）从原始
    # body 正则捞 "phone" 字段（放在 sms 之前恒完整），raw 用整个 body 交抽码器。
    if not PHONE_RE.match(str(norm["phone"])):
        m = re.search(r'"phone"\s*:\s*"?(1[0-9]{10})', body_text)
        if m:
            norm["phone"] = m.group(1)
            dec = urllib.parse.unquote(body_text) if "%" in body_text else body_text
            norm["raw"] = norm["raw"] if (str(norm["raw"]) or "").strip() else dec
            norm["platform"] = norm["platform"] or platform_of(dec)
    # 平台标签：URL ?platform= 显式覆盖（每平台单独转发规则用；比内容【品牌】自动
    # 识别更可靠，且支持无【品牌】前缀的平台如 deepseek/博客园）。
    qplat = (query.get("platform") or "").strip()
    if qplat:
        norm["platform"] = qplat
    norm["platform"] = _controlled_platform(norm.get("platform"))
    # SIM 槽/子ID 也可走 URL query；body 已解析出的值优先，URL 只补空缺。
    norm_meta = norm.get("meta")
    meta: dict[str, str] = dict(norm_meta) if isinstance(norm_meta, dict) else {}
    for qk, mk in _QUERY_META_KEYS:
        qv = (query.get(qk) or "").strip()
        if qv and not meta.get(mk):
            meta[mk] = qv
    norm["meta"] = meta
    # 卡槽备注/SIM 信息里嵌的真实手机号 → **权威覆盖** body/URL 的 phone（双卡机
    # body phone 会被 ROM 错标，卡槽备注是 SIM 硬件级真值）。
    slot_phone = phone_from_slot(str(meta.get("sim_slot") or ""), str(meta.get("sim_info") or ""))
    if slot_phone:
        norm["phone"] = slot_phone
    # 无法定位手机号 → 软收下存 unrouted.json，绝不丢码。
    routed = bool(PHONE_RE.match(str(norm["phone"])))
    phone = str(norm["phone"]) if routed else "unrouted"
    norm["phone"] = phone
    raw = str(norm["raw"] or "")

    if _rate_limited("push", phone):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})

    # T-39 语音验证码通道：显式 code_source='voice'（body 字段或 ?code_source=/?source=）
    # → 跳过 SMS 抽码（语音无短信正文可佐证 standalone），直接采纳人工听写的 code，
    # 仍按 HINT_RE（4-8 位数字）校验。
    voice = (
        str(norm.get("code_source") or "") == "voice"
        or (query.get("code_source") or query.get("source") or "").strip() == "voice"
    )
    if voice:
        hint = str(norm.get("code_hint") or "").strip()
        code = hint if HINT_RE.match(hint) else ""
        code_source, method = "voice", "voice"
        platform = str(norm["platform"] or "")
    else:
        code, code_source, method = _extract_code(norm)
        platform = str(norm["platform"] or "")
    if method != "none":  # 抽码机制入 meta 供审计（regex vs hint vs 语音），不撞 code_source 词汇
        meta = dict(meta)
        meta["extract_method"] = method
        norm["meta"] = meta

    rec: dict[str, Any] = {
        "ts": time.time(),
        "phone": phone,
        "code": code,
        "raw": raw,
        "from": str(norm["from"] or ""),
        "platform": platform,
    }
    if norm["meta"]:
        rec["meta"] = norm["meta"]
    remote_addr = request.client.host if request.client else ""
    _atomic_write_json(_inbox_dir() / f"{phone}.json", rec)
    _append_event_best_effort(rec, code_source=code_source, remote_addr=remote_addr)
    if routed:  # 迁库旁路：回填转码链路事实（unrouted 无号可回填，跳过）
        _record_sms_best_effort(phone)

    # 绝不把短信正文（含验证码明文）写日志——只记长度/平台，phone 掩码。
    if not code and raw:
        log.warning(
            "otp_push_no_code", phone=mask_phone(phone), raw_len=len(raw), platform=platform or "-"
        )
    if not routed:
        log.warning("otp_push_unrouted", have_code=bool(code), platform=platform or "-")
    log.info(
        "otp_push",
        phone=mask_phone(phone),
        platform=platform or "-",
        have_code=bool(code),
        code_len=len(code),
        routed=routed,
        slot=meta.get("sim_slot", "-"),
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "have_code": bool(code),
            "code_len": len(code),
            "phone": mask_phone(phone),
            "routed": routed,
            "platform": platform,
        },
    )


@router.get("/otp/latest")
def otp_latest(request: Request) -> JSONResponse:
    """统一取码：**输入手机号 → 取 within 秒内到达的验证码**（默认 180=3 分钟）。

    幂等只读（窗内可重复取；不消费、不删文件）——一次性防旧码是自动登录 seam
    的职责，不是本人肉/工具查码口的职责。窗外/无码 → 200 ``{found:false}``
    （便于轮询，非错误）。返回**明文 code**（operator 已鉴权，需真码去填）。"""
    _require_operator_token(request)
    phone = (request.query_params.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(
            status_code=400, detail={"code": "bad_phone"}, headers=_NO_STORE_HEADERS
        )
    if _rate_limited("latest", phone):
        return _sensitive_json({"error": "rate_limited"}, status_code=429)
    within = _clamp_within(request.query_params.get("within"))
    path = _inbox_dir() / f"{phone}.json"
    if not path.exists():
        return _sensitive_json(
            {"ok": True, "found": False, "within": within, "reason": "no_sms_for_phone"}
        )
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _sensitive_json(
            {"ok": True, "found": False, "within": within, "reason": "unreadable"}
        )
    ts = float(rec.get("ts", 0) or 0)
    age = time.time() - ts
    code = str(rec.get("code") or "").strip()
    if age > within:
        return _sensitive_json(
            {
                "ok": True,
                "found": False,
                "within": within,
                "age_s": round(age, 1),
                "reason": "stale",
            }
        )
    if not code:
        return _sensitive_json(
            {
                "ok": True,
                "found": False,
                "within": within,
                "age_s": round(age, 1),
                "reason": "no_code_extracted",
            }
        )
    return _sensitive_json(
        {
            "ok": True,
            "found": True,
            "code": code,
            "platform": str(rec.get("platform") or ""),
            "age_s": round(age, 1),
        }
    )


# ---------------------------------------------------------------------------
# 装机配置页（2026-08-07 起）：公开静态页（零秘密内嵌）+ operator 门后的
# setup-info（含 relay token）。旧 SmsForwarder 文档铁规「token 绝不写回网页」
# 不变——页面本身只是说明+表单，key 只在输管理密码后由受门端点下发。
#
# 在册号码注册（2026-08-09 起）：``POST /api/v2/otp/register``（operator 门内）
# 把测量号登记进服务端注册表（``GEO_OTP_REGISTRY_PATH``，缺省
# ``platform-v2/runtime/otp_registered_numbers.json``，原子写）。注册只需手机号，
# 卡槽/运营商是**选填自由文本**（不限 SIM1/2，留空亦可）——卡槽槽位只是给人看的
# 物理提示，服务端反解真号靠的是备注串里嵌的 11 位号码（phone_from_slot 正则）。
# setup-info 不返回注册表，装机页不能枚举其他号码；刚提交的号码只在本次注册响应中
# 返回其 Android 备注，完整清单统一在原生身份鉴权的账号管理页查看。
# 并发口径：读-改-写无跨进程锁，多 worker 下同瞬间双注册会丢一条——运维人工
# 单操作者低频动作，实际无感；真出现并发需求再补文件锁。
# ---------------------------------------------------------------------------

_APK_ENV = "GEO_OTP_APK_PATH"
_DEFAULT_APK_PATH = Path(__file__).resolve().parents[3] / "runtime" / "smsforwarder.apk"
_APK_PROJECT_URL = "https://github.com/pppscn/SmsForwarder"
_APK_UPSTREAM_LICENSE_URL = "https://github.com/pppscn/SmsForwarder/blob/main/LICENSE"
_APK_DEFAULT_VERSION = "3.5.0.260224"

# The phone-side gate must require both an approved platform signature and an OTP
# business phrase. A bare company name no longer releases every SMS from that brand.
_BODY_TEMPLATE = '{"slot":"{{CARD_SLOT}}","sms":"{{SMS}}"}'
_WHITELIST_REGEX = (
    r"(?is)^(?=.*【(?:豆包|深度求索|DeepSeek|文心一言|百度|通义千问|通义|千问|腾讯元宝"
    r"|元宝|博客园|搜狐|百家号|今日头条|头条号)】)"
    r"(?=.*(?:验证码|校验码|动态码|动态口令|登录码|安全码|verification\s*code|\botp\b)).*$"
)

_SMSFORWARDER_LICENSE = """BSD 2-Clause License

Copyright (c) 2021, pppscn
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
_REGISTRY_ENV = "GEO_OTP_REGISTRY_PATH"
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[3] / "runtime" / "otp_registered_numbers.json"
)


def _registry_path() -> Path:
    """在册注册表落盘路径。``GEO_OTP_REGISTRY_PATH`` 覆盖默认（测试指向 tmp）。"""
    return Path(os.environ.get(_REGISTRY_ENV, "") or _DEFAULT_REGISTRY_PATH)


def _read_registry() -> list[dict[str, Any]]:
    """读在册注册表（best-effort：缺失/损坏 → 空表 + warning，绝不让 setup-info 挂）。
    记录格式 ``[{"phone","carrier","slot","remark","ts"}, ...]``（按 phone 唯一）。"""
    path = _registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.warning("otp_registry_unreadable", path=str(path))
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and PHONE_RE.match(str(e.get("phone") or ""))]


def _clean_label(value: object, *, max_len: int) -> str:
    """卡槽/运营商标签清洗：去换行与多余空白、限长（备注串给人看 + 给正则反解真号，
    绝不带控制字符）。自由文本——卡槽**不限 SIM1/2**，留空合法。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_len]


def _build_remark(phone: str, carrier: str, slot: str) -> str:
    """拼卡槽备注 ``[<槽位>_][<运营商>_]+86<手机号>``（与现役 env 备注同格式；
    槽位/运营商可空——反解真号只依赖串内 11 位号码，phone_from_slot 正则兜底）。"""
    parts = [p for p in (slot, carrier) if p]
    parts.append(f"+86{phone}")
    return "_".join(parts)


def _public_origin(request: Request) -> str:
    """Return the one configured public origin, with a safe development fallback.

    Production deployments set ``GEO_PUBLIC_BASE_URL`` explicitly so reverse-proxy
    ``Host``/port rewriting cannot silently produce unusable setup URLs. The fallback
    keeps local TestClient/development usage convenient; a configured value always
    receives strict validation and is never derived from forwarded client headers.
    """
    configured = os.environ.get("GEO_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not configured:
        if os.environ.get("GEO_ENV", "").strip().lower() == "production":
            raise HTTPException(
                status_code=503,
                detail={"code": "otp_public_base_missing"},
                headers=_NO_STORE_HEADERS,
            )
        return str(request.base_url).rstrip("/")
    parsed = urllib.parse.urlsplit(configured)
    try:
        invalid_port = not (parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        invalid_port = True
    hostname = parsed.hostname or ""
    invalid_hostname = any(
        char.isspace() or unicodedata.category(char).startswith("C") for char in hostname
    )
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        (parsed.scheme != "https" and not is_local_http)
        or not hostname
        or invalid_hostname
        or invalid_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "otp_public_base_invalid"},
            headers=_NO_STORE_HEADERS,
        )
    return configured


def _apk_path() -> Path:
    return Path(os.environ.get(_APK_ENV, "") or _DEFAULT_APK_PATH)


@lru_cache(maxsize=16)
def _apk_sha256(
    path_text: str,
    device: int,
    inode: int,
    size: int,
    mtime_ns: int,
    ctime_ns: int,
) -> str:
    """Hash one immutable file identity; stat fields invalidate replacements."""
    del device, inode, size, mtime_ns, ctime_ns
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _apk_state() -> tuple[Path, dict[str, Any]]:
    """Resolve public APK readiness without exposing its server filesystem path."""
    path = _apk_path()
    version = os.environ.get("GEO_OTP_APK_VERSION", "").strip() or _APK_DEFAULT_VERSION
    signer = os.environ.get("GEO_OTP_APK_SIGNER_SHA256", "").strip().upper()
    expected = os.environ.get("GEO_OTP_APK_SHA256", "").strip().lower()
    base: dict[str, Any] = {
        "ready": False,
        "reason": "apk_missing",
        "version": version[:64],
        "filename": f"SmsForwarder-{version[:64]}.apk",
        "size_bytes": 0,
        "sha256": "",
        "signer_sha256": signer[:128],
        "updated_at": "",
        "integrity": "unavailable",
        "project_url": _APK_PROJECT_URL,
        "license_url": "/api/v2/otp/smsforwarder-license",
        "upstream_license_url": _APK_UPSTREAM_LICENSE_URL,
        "commercial_use_review_required": True,
    }
    try:
        stat = path.stat()
        if not path.is_file():
            return path, base
        digest = _apk_sha256(
            str(path.resolve()),
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
    except FileNotFoundError:
        return path, base
    except (OSError, ValueError):
        base["reason"] = "apk_unreadable"
        return path, base

    base.update(
        {
            "size_bytes": stat.st_size,
            "sha256": digest,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }
    )
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        base.update(reason="apk_manifest_invalid", integrity="failed")
        return path, base
    if expected and not secrets.compare_digest(digest, expected):
        base.update(reason="apk_integrity_failed", integrity="failed")
        return path, base
    if not expected and os.environ.get("GEO_ENV", "").strip().lower() == "production":
        base.update(reason="apk_manifest_missing", integrity="unverified")
        return path, base
    base.update(
        ready=True,
        reason="",
        integrity="verified" if expected else "development_unverified",
    )
    return path, base


_RATE_LIMITS["setup"] = (30, 60.0)


@router.post("/otp/register")
async def otp_register(request: Request) -> JSONResponse:
    """在册号码注册（operator 门内）：``{"phone","carrier"?,"slot"?}`` → 登记进
    服务端注册表（原子写）并同步账号治理表。幂等：同号再注册=更新备注。
    响应带完整 remark（operator 自填自拿，装机页一键复制进手机卡槽备注）。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return _sensitive_json({"error": "rate_limited"}, status_code=429)
    body_text = (await request.body()).decode("utf-8", "replace")
    if len(body_text.encode("utf-8")) > 4096:  # 注册体极小，公网面兜底防滥
        return _sensitive_json({"error": "payload_too_large"}, status_code=413)
    try:
        data: Any = json.loads(body_text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail={"code": "bad_body"}, headers=_NO_STORE_HEADERS)
    phone = re.sub(r"^\+?86", "", str(data.get("phone") or "").strip())
    if not PHONE_RE.match(phone):
        raise HTTPException(
            status_code=400, detail={"code": "bad_phone"}, headers=_NO_STORE_HEADERS
        )
    carrier = _clean_label(data.get("carrier"), max_len=24)
    slot = _clean_label(data.get("slot"), max_len=16)
    remark = _build_remark(phone, carrier, slot)

    existing = _read_registry()
    created = all(str(e.get("phone")) != phone for e in existing)
    entries = [e for e in existing if str(e.get("phone")) != phone]
    entries.append(
        {"phone": phone, "carrier": carrier, "slot": slot, "remark": remark, "ts": time.time()}
    )
    _atomic_write_json(_registry_path(), entries)
    # 迁库旁路：slot/carrier 拼 owner_note（皆空 → 完整 remark 兜底）
    _sync_phone_account_best_effort(phone, " ".join(p for p in (slot, carrier) if p) or remark)
    log.info("otp_number_registered", phone=mask_phone(phone), created=created, slot=slot or "-")
    return _sensitive_json(
        {"ok": True, "created": created, "phone": mask_phone(phone), "remark": remark}
    )


@router.get("/otp/setup-info")
def otp_setup_info(request: Request) -> JSONResponse:
    """装机配置（operator 门内）：两端共用推送地址/relay token，以及 Android
    Body 模板/白名单正则。注册表不在此响应中暴露。
    URL 优先来自显式 ``GEO_PUBLIC_BASE_URL``；生产缺失时 fail-closed，避免反代
    ``Host`` 丢端口后向操作员下发不可用地址。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return _sensitive_json({"error": "rate_limited"}, status_code=429)
    origin = _public_origin(request)
    _, apk = _apk_state()
    return _sensitive_json(
        {
            "ok": True,
            "push_url": f"{origin}/api/v2/otp/push",
            "relay_token": os.environ.get("GEO_OTP_RELAY_TOKEN", ""),
            "body_template": _BODY_TEMPLATE,
            "whitelist_regex": _WHITELIST_REGEX,
            "apk_url": f"{origin}/api/v2/otp/smsforwarder.apk",
            "apk": apk,
        }
    )


@router.get("/otp/status")
def otp_status(request: Request) -> JSONResponse:
    """最近推送一览（operator 门内，**掩码**——无 code 无原文）：phone 掩码 +
    code_len + 平台 + 到达秒龄。给装机页「验证」步用（手机自查转发是否到达）。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return _sensitive_json({"error": "rate_limited"}, status_code=429)
    inbox = _inbox_dir()
    rows: list[dict[str, Any]] = []
    try:
        files = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    now = time.time()
    for path in files[:10]:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        code = str(rec.get("code") or "")
        ts = float(rec.get("ts", 0) or 0)
        rows.append(
            {
                "phone": mask_phone(str(rec.get("phone") or path.stem)),
                "platform": _controlled_platform(rec.get("platform")) or "-",
                "code_len": len(code),
                "time": datetime.fromtimestamp(ts, tz=_TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
                "age_s": round(now - ts, 1),
            }
        )
    return _sensitive_json({"ok": True, "recent": rows})


@router.get("/otp/apk-info", include_in_schema=False)
def otp_apk_info() -> JSONResponse:
    """Public, non-secret artifact readiness used before enabling the download CTA."""
    _, state = _apk_state()
    return JSONResponse(content={"ok": True, "apk": state}, headers=_NO_STORE_HEADERS)


@router.get("/otp/smsforwarder-license", include_in_schema=False)
def otp_smsforwarder_license() -> PlainTextResponse:
    """License notice that accompanies the redistributed third-party binary."""
    return PlainTextResponse(
        _SMSFORWARDER_LICENSE,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/otp/smsforwarder.apk")
def otp_apk() -> Any:
    """SmsForwarder 安装包自托管下载（公开——APK 非秘密；CN 手机直连 GitHub 慢）。
    文件路径 env ``GEO_OTP_APK_PATH``，缺省 ``platform-v2/runtime/smsforwarder.apk``。"""
    path, state = _apk_state()
    if not state["ready"]:
        code = str(state["reason"] or "apk_unavailable")
        status_code = 404 if code == "apk_missing" else 503
        raise HTTPException(status_code=status_code, detail={"code": code})
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename="SmsForwarder.apk",
        headers={
            # The APK is explicitly public and contains no operator data. A short public
            # cache avoids re-downloading ~19 MiB on an accidental repeat while the
            # stable URL can still roll to a new verified release promptly.
            "Cache-Control": "public, max-age=300, must-revalidate",
            "X-Content-Type-Options": "nosniff",
            "X-APK-SHA256": str(state["sha256"]),
        },
    )


@router.get("/otp/setup")
def otp_setup_page() -> Any:
    """装机配置页（公开，纯静态说明+表单，**零秘密内嵌**）：输入管理密码后经
    setup-info 拉取含 key 的配置项，逐项一键复制。"""
    nonce = secrets.token_urlsafe(24)
    html = _SETUP_PAGE_HTML.replace("__CSP_NONCE__", nonce)
    headers = {
        **_NO_STORE_HEADERS,
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'; object-src 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
    return HTMLResponse(html, headers=headers)


_SETUP_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTP 转发手机配置 · GEO</title>
<style nonce="__CSP_NONCE__">
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", sans-serif;
         margin: 0; background: #f4f6fb; color: #1a2233; line-height: 1.65; }
  main { max-width: 720px; margin: 0 auto; padding: 20px 16px 64px; }
  h1 { font-size: 20px; margin: 8px 0 4px; }
  h2 { font-size: 16px; margin: 28px 0 8px; padding-left: 10px; border-left: 4px solid #4f46e5; }
  .sub { color: #64748b; font-size: 13px; margin-bottom: 12px; }
  .card { background: #fff; border-radius: 12px; padding: 14px 16px; margin: 10px 0;
          box-shadow: 0 1px 3px rgba(15,23,42,.08); }
  .field { margin: 12px 0; }
  .field label { display: block; font-size: 13px; color: #475569; margin-bottom: 4px; }
  .row { display: flex; gap: 8px; }
  .val { flex: 1; font-family: ui-monospace, Menlo, monospace; font-size: 13px;
         background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px;
         padding: 9px 10px; word-break: break-all; user-select: all; }
  button { font: inherit; cursor: pointer; border: 0; border-radius: 8px; }
  .cp { flex: 0 0 auto; background: #4f46e5; color: #fff; padding: 0 14px; font-size: 13px; }
  .cp.ok { background: #16a34a; }
  .cp.fail { background: #b91c1c; }
  .cp.off { background: #cbd5e1; cursor: default; }
  .opt { font-size: 14px; padding: 2px 0; }
  .val.locked { color: #94a3b8; }
  .big { display: block; width: 100%; padding: 13px; font-size: 15px; font-weight: 600;
         background: #4f46e5; color: #fff; text-align: center; text-decoration: none; }
  .ghost { background: #e2e8f0; color: #1a2233; }
  .big.off { background: #cbd5e1; color: #64748b; pointer-events: none; }
  input[type=password] { width: 100%; margin: 8px 0; }
  .text-input { font: inherit; padding: 9px; border-radius: 8px; border: 1px solid #cbd5e1; }
  .slot-input { width: 190px; }
  .grow { flex: 1; }
  ul, ol { padding-left: 20px; margin: 8px 0; }
  li { margin: 5px 0; font-size: 14px; }
  .warn { background: #fef3c7; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
  .critical { background: #fee2e2; border: 1px solid #fecaca; color: #7f1d1d;
              border-radius: 8px; padding: 10px 12px; font-size: 13px; }
  .okbox { background: #dcfce7; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
  .hidden { display: none; }
  #st { font-size: 13px; margin-top: 6px; min-height: 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td, th { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e2e8f0; }
  .mono { font-family: ui-monospace, Menlo, monospace; }
  .mt6 { margin-top: 6px; }
  .mt8 { margin-top: 8px; }
  .mt10 { margin-top: 10px; }
  .mt14 { margin-top: 14px; }
  .mb8 { margin-bottom: 8px; }
  .actions { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
  .device-switch { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .device-switch button { min-height: 48px; border: 1px solid #cbd5e1; border-radius: 9px;
                          background: #f8fafc; color: #334155; font-weight: 700; }
  .device-switch button[aria-pressed="true"] { background: #3730a3; border-color: #3730a3;
                                                color: #fff; }
  .clipboard-probe { position: fixed; left: -9999px; opacity: 0; }
  a { color: #3730a3; }
</style>
</head>
<body>
<main>
  <h1>OTP 转发手机配置</h1>
  <div class="sub">iPhone 快捷指令 / Android SmsForwarder → GEO V2 收件端点</div>
  <div class="critical">
    <b>HTTPS 校验：</b>公网入口应由系统信任的公共 CA 正常验证，无需安装自签证书或
    描述文件。浏览器若出现证书警告，立即停止，不要输入 operator token；不要绕过警告。
    页面本体不含秘密；含密配置、号码注册和状态查询仍须 operator token 鉴权。
  </div>

  <h2 id="device-choice">选择手机系统</h2>
  <div class="card">
    <div class="device-switch" role="group" aria-label="选择短信转发手机系统">
      <button type="button" id="deviceIos" aria-pressed="true" aria-controls="iosGuide">
        Apple / iPhone
      </button>
      <button type="button" id="deviceAndroid" aria-pressed="false"
              aria-controls="androidGuide">Android</button>
    </div>
    <div class="sub mt8" id="deviceHint" aria-live="polite">
      当前显示 Apple 快捷指令方案：免费、无需第三方转发 App。
    </div>
  </div>

  <h2 id="unlock">通用第 1 步 · 解锁配置项</h2>
  <div class="card">
    <div class="sub">令牌只保存在当前页面内存中，不写入 URL 或浏览器存储；闲置 20 分钟自动锁定，
      离开页面或从后退缓存恢复时立即清除。</div>
    <input type="password" class="text-input" id="pw" placeholder="管理密码（operator token）"
           autocomplete="off" autocapitalize="none" spellcheck="false">
    <div class="actions">
      <button class="big" id="btnLoad">加载配置</button>
      <button class="big ghost" id="btnLock" disabled>立即锁定</button>
    </div>
    <div id="st" aria-live="polite"></div>
  </div>

  <h2 id="number-registry">通用第 2 步 · 登记接码号码</h2>
  <div class="card">
    <div class="sub">iPhone 的快捷指令需要在 JSON 的 <span class="mono">phone</span>
      字段中固定填写本机接码号码；Android 还要把服务器生成的备注填入对应 SIM 卡槽。
      网页无法读取本机号码，请人工核对后登记。</div>
    <div class="warn">本页不显示或枚举在册号码。提交后可到
      <a href="/platform/operations/accounts" target="_blank" rel="noopener noreferrer">账号管理</a>
      点击「刷新号码」核对；同一号码再次注册会更新备注，卡槽不限 SIM1/2。</div>
    <div class="field mt14">
      <label>注册新号码（需先解锁；同号再注册=更新备注）</label>
      <div class="row">
        <input id="slotIn" class="text-input slot-input" list="slotOpts"
               placeholder="卡槽（选填，如 SIM1/eSIM）">
        <datalist id="slotOpts">
          <option value="SIM1"></option><option value="SIM2"></option><option value="eSIM"></option>
        </datalist>
        <input id="carrierIn" class="text-input grow" placeholder="运营商（选填，如 中国联通）">
      </div>
      <div class="row mt8">
        <input id="phoneIn" class="text-input grow" inputmode="numeric" maxlength="13"
               placeholder="11 位手机号（必填）">
        <button class="cp" id="btnReg">注册号码</button>
      </div>
      <div id="genOut" class="sub"></div>
    </div>
  </div>

  <section id="iosGuide" aria-labelledby="iphone">
  <h2 id="iphone">iPhone · 免费转发（Apple 快捷指令）</h2>
  <div class="card">
    <div class="okbox"><b>选择：苹果自带「快捷指令」。</b>无需下载第三方转发器、
      无订阅费，短信从本机直接 POST 到 GEO。</div>
    <div class="warn mt8"><b>终端边界：</b>只用于专用测量号码。信息自动化无法可靠告诉
      GEO 双卡中的实际收信线路，因此一台 iPhone 只配置一个接码号码；双卡设备须先用
      真机测试确认另一线路不会触发，不能把个人短信设为全部转发。</div>
    <ol>
      <li>打开「快捷指令」→「自动化」→「+」→「信息」。按已知平台发送方设置
        <b>发件人</b>，并设置「信息包含」为该平台验证码稳定关键词；不同发送方分别建自动化，
        不要选择所有信息。</li>
      <li>选择<b>立即运行</b>（旧版界面为关闭「运行前询问」），然后新建空白自动化。</li>
      <li>添加「获取 URL 内容」，请求方式选 <b>POST</b>，URL 使用下方解锁值。</li>
      <li>展开「标头」，添加 <span class="mono">X-Relay-Token</span>，值使用下方解锁值。</li>
      <li>请求正文选 <b>JSON</b>：添加文本字段 <span class="mono">phone</span>，值填刚刚
        人工核对并注册的 11 位号码；再添加文本字段 <span class="mono">sms</span>，值必须插入紫色的
        <b>「快捷指令输入」变量</b>，不能输入这六个字作为普通文本。</li>
      <li>保存后先让另一台手机发送一条真实格式的测试短信；再锁屏重测一次，并在本页底部
        「查看最近推送」确认到达。</li>
    </ol>
    <div class="field"><label>URL</label>
      <div class="row"><div class="val locked" id="iosUrl">🔒 解锁后显示</div>
        <button class="cp off" id="cpIosUrl" disabled>复制</button></div></div>
    <div class="field"><label>标头 Key</label>
      <div class="row"><div class="val">X-Relay-Token</div>
        <button class="cp" data-t="X-Relay-Token">复制</button></div></div>
    <div class="field"><label>标头 Value</label>
      <div class="row"><div class="val locked" id="iosTok">🔒 解锁后显示</div>
        <button class="cp off" id="cpIosTok" disabled>复制</button></div></div>
    <div class="field"><label>JSON 字段（在快捷指令中逐项添加，不要粘贴成一段文本）</label>
      <div class="val"><span class="mono">phone</span> = 刚刚注册的 11 位接码号码<br>
        <span class="mono">sms</span> = 快捷指令输入（紫色变量）</div></div>
    <div class="critical mt8"><b>HTTPS 必须正常：</b>当前入口使用系统信任的公共 CA 证书，
      iPhone 不需要安装自签证书或描述文件；如果「获取 URL 内容」仍报证书错误，停止配置，
      不要改用 HTTP 或关闭校验，并联系运维检查证书续期。</div>
  </div>
  </section>

  <section id="androidGuide" aria-labelledby="android" hidden>
  <h2 id="android">Android 第 0 步 · 防杀设置（必做，上次就是这么断的）</h2>
  <div class="card">
    <ul>
      <li>系统设置 → 应用管理 → SmsForwarder → <b>省电策略 = 无限制</b></li>
      <li>同页打开 <b>自启动</b>；最近任务卡片下拉 <b>锁定后台</b></li>
      <li>权限：确认 <b>短信、通知</b> 均已授予</li>
      <li>HyperOS/国产 ROM：另关「夜间休眠断网」「定时清理后台」类开关</li>
      <li>通用设置 → <b>免打扰(禁用转发)时间段 = 00:00 ~ 00:00</b>（起止相等=该功能停用；
        20260810 实测被设成 00:00 ~ 24:00 = <b>全天禁转发</b>，一条都发不出）</li>
    </ul>
  </div>

  <h2>Android 第 1 步 · 安装 SmsForwarder</h2>
  <div class="card">
    <a class="big off" id="apkDownload" download="SmsForwarder.apk"
       aria-disabled="true">正在检查安装包…</a>
    <div id="apkState" class="sub mt8" aria-live="polite">正在读取制品状态…</div>
    <div id="apkMeta"></div>
    <div class="critical mt8"><b>过渡风险：</b>当前审查的第三方 v3.5.0 客户端会绕过 HTTPS
      证书错误；文件哈希一致只证明拿到既定 APK，不代表手机传输已安全。受控 pin 客户端
      上线前，只能在管理 VPN 等隔离链路中临时使用；不得把“没有忽略证书的 UI 开关”
      误解为客户端已经验证服务器身份。</div>
    <div class="sub mt8">
      第三方项目：<a href="https://github.com/pppscn/SmsForwarder" target="_blank"
      rel="noopener noreferrer">pppscn/SmsForwarder</a>；随包
      <a href="/api/v2/otp/smsforwarder-license" target="_blank"
      rel="noopener noreferrer">BSD 2-Clause 通知</a>。上游 README 另有仅供测试学习、
      禁止商业用途的声明；商业使用边界须由权利人或法务书面确认。
    </div>
    <div class="sub mt8">已装过可跳过——上次只是被系统杀死，不是卸载。</div>
  </div>

  <h2>Android 第 2 步 · 卡槽备注（双卡防错标）</h2>
  <div class="card">
    <div class="sub">SmsForwarder → 通用设置 → 个性设置 →「SIM1主键/备注」「SIM2主键/备注」
      的备注框，填入上方注册成功后生成且含<b>该卡真实号码</b>的完整文本；
      <span class="mono">{{CARD_SLOT}}</span> 发的就是它。服务端靠备注中的号码反解归属。</div>
  </div>

  <h2>Android 第 3 步 · 发送通道（Webhook）</h2>
  <div class="card">
    <div class="sub">SmsForwarder → 发送通道 → 新增 → Webhook。下列条目按 App 表单
      自上而下逐格对应：文本框给「复制」值，单选/开关给操作指示。</div>
    <div class="field"><label>通道名称/状态（文本框；右侧开关保持开）</label>
      <div class="row"><div class="val">geosys-otp</div>
        <button class="cp" data-t="geosys-otp">复制</button></div></div>
    <div class="field"><label>请求方式（单选）</label>
      <div class="opt">选 <b>POST</b></div></div>
    <div class="field"><label>Webhook Server（文本框）</label>
      <div class="row"><div class="val locked" id="vUrl">🔒 解锁后显示</div>
        <button class="cp off" id="cpUrl" disabled>复制</button></div></div>
    <div class="field"><label>消息模板（多行文本框）</label>
      <div class="row"><div class="val locked" id="vTpl">🔒 解锁后显示</div>
        <button class="cp off" id="cpTpl" disabled>复制</button></div></div>
    <div class="warn">⚠️ 消息模板必须与复制值<b>逐字符一致</b>——20260810 实测多一个
      <span class="mono">"</span> 即破坏 JSON，服务端只能按 unrouted 软收（码不丢但不归号）。</div>
    <div class="field"><label>Secret（文本框）</label>
      <div class="opt">留空（置空则不计算 sign）</div></div>
    <div class="field"><label>成功应答关键字（文本框）</label>
      <div class="opt">留空（HTTP 200 即为成功）</div></div>
    <div class="field"><label>Headers 第 1 行 Key（点表单 + 号添加行）</label>
      <div class="row"><div class="val">X-Relay-Token</div>
        <button class="cp" data-t="X-Relay-Token">复制</button></div></div>
    <div class="field"><label>Headers 第 1 行 Value</label>
      <div class="row"><div class="val locked" id="vTok">🔒 解锁后显示</div>
        <button class="cp off" id="cpTok" disabled>复制</button></div></div>
    <div class="field"><label>Headers 第 2 行 Key / Value</label>
      <div class="row"><div class="val">Content-Type</div>
        <button class="cp" data-t="Content-Type">复制</button></div>
      <div class="row mt6"><div class="val">application/json</div>
        <button class="cp" data-t="application/json">复制</button></div></div>
    <div class="field"><label>代理设置（单选）</label>
      <div class="opt">选 <b>无代理</b></div></div>
    <div class="sub mt6">填完点【保存】，再点通道页【测试】；然后走第 5 步验证。
      v3.5.0 表单没有「忽略 SSL 证书」开关，但经审查的现有二进制仍会绕过证书错误；
      这不是安全开关已经关闭的证据，过渡期必须遵守上方网络隔离要求。</div>
  </div>

  <h2>Android 第 4 步 · 转发规则（平台白名单）</h2>
  <div class="card">
    <div class="sub">SmsForwarder → 转发规则 → 新增（短信转发规则）。逐格对应：</div>
    <div class="warn mb8">🔒 本规则采用<b>平台签名 + 验证码业务词</b>组合匹配：只配置
      测评平台（豆包/DeepSeek/文心一言/通义千问/元宝）和媒体号平台
      （博客园/搜狐/百家号/头条号），不再以“百度”“腾讯”等泛品牌词单独放行。
      模板可能随平台更新，不能据此承诺所有隐私短信绝对不会命中；请使用专用测量号码，
      切勿把规则泛化成“所有验证码”，并定期用支付、密码重置、私信等负向样本复核。</div>
    <div class="field"><label>规则别名（文本框）</label>
      <div class="row"><div class="val">geosys</div>
        <button class="cp" data-t="geosys">复制</button></div></div>
    <div class="field"><label>发送通道（下拉选择）</label>
      <div class="opt">选 <b>geosys-otp</b>（第 3 步建的通道）</div></div>
    <div class="field"><label>匹配卡槽（单选）</label>
      <div class="opt">选 <b>不限卡槽</b>（真号由卡槽备注反解，与匹配哪张卡无关）</div></div>
    <div class="field"><label>匹配字段（单选）</label>
      <div class="opt">选 <b>短信内容</b></div></div>
    <div class="field"><label>匹配模式（单选）</label>
      <div class="opt">选 <b>正则匹配</b></div></div>
    <div id="ruleLocked" class="warn">🔒 「匹配的值」白名单正则需解锁后显示。</div>
    <div id="rule"></div>
    <div class="warn mt8">⚠️ 此规则使用完整匹配和两个正向条件；请整段复制，不要只保留品牌词。</div>
    <div class="field"><label>三个开关</label>
      <div class="opt">启用自定义模版=<b>关</b>；启用正则替换内容=<b>关</b>；
        启用该条转发规则=<b>开</b></div></div>
    <div class="field"><label>免打扰(禁用转发)时间段</label>
      <div class="opt">保持 <b>00:00 ~ 00:00</b>（起止相等=该功能停用，即全天可转发）</div></div>
    <div class="sub mt6">填完点【保存】。</div>
  </div>
  </section>

  <h2>通用第 3 步 · 验证</h2>
  <div class="card">
    <ol>
      <li>用另一台手机给测量号发一条测试短信（内容如「【豆包】验证码 123456」）。</li>
      <li>解锁本页后点下方按钮，能看到刚才那条（手机号掩码 + 码长 + 平台）即全链路通。</li>
    </ol>
    <button class="big ghost" id="btnStatus">查看最近推送</button>
    <div id="statusOut" class="mt10"></div>
  </div>

</main>
<script nonce="__CSP_NONCE__">
(() => {
  "use strict";

  const IDLE_LOCK_MS = 20 * 60 * 1000;
  let operatorToken = "";
  let idleTimer = 0;
  const byId = (id) => document.getElementById(id);
  const text = (value) => typeof value === "string" ? value : "";

  function requestSuffix(response) {
    const requestId = text(response.headers.get("X-Request-Id")).slice(0, 128);
    return requestId ? "；请求 ID：" + requestId : "";
  }

  function notice(container, message, className = "warn") {
    const box = document.createElement("div");
    box.className = className;
    box.textContent = message;
    container.replaceChildren(box);
  }

  async function writeClipboard(value) {
    if (!value) return false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (error) {
        // Continue to the synchronous compatibility path while activation is live.
      }
    }
    const probe = document.createElement("textarea");
    probe.className = "clipboard-probe";
    probe.value = value;
    probe.setAttribute("readonly", "");
    document.body.appendChild(probe);
    probe.select();
    let copied = false;
    try {
      copied = document.execCommand("copy") === true;
    } catch (error) {
      copied = false;
    }
    probe.remove();
    return copied;
  }

  async function copyText(value, button) {
    const original = button.textContent;
    const copied = await writeClipboard(value);
    button.textContent = copied ? "已复制" : "复制失败，请手动选择";
    button.classList.toggle("ok", copied);
    button.classList.toggle("fail", !copied);
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("ok", "fail");
    }, 1800);
    return copied;
  }

  function field(label, value) {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    const labelNode = document.createElement("label");
    labelNode.textContent = label;
    const row = document.createElement("div");
    row.className = "row";
    const valueNode = document.createElement("div");
    valueNode.className = "val";
    valueNode.textContent = text(value);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cp";
    button.textContent = "复制";
    button.addEventListener("click", () => copyText(valueNode.textContent || "", button));
    row.append(valueNode, button);
    wrapper.append(labelNode, row);
    return wrapper;
  }

  function bindGate(valueId, buttonId, value) {
    const valueNode = byId(valueId);
    valueNode.textContent = text(value);
    valueNode.classList.remove("locked");
    const button = byId(buttonId);
    button.disabled = false;
    button.classList.remove("off");
  }

  function resetGate(valueId, buttonId) {
    const valueNode = byId(valueId);
    valueNode.textContent = "🔒 解锁后显示";
    valueNode.classList.add("locked");
    const button = byId(buttonId);
    button.disabled = true;
    button.classList.add("off");
  }

  function lock(message = "🔒 已锁定") {
    operatorToken = "";
    if (idleTimer) window.clearTimeout(idleTimer);
    idleTimer = 0;
    byId("pw").value = "";
    byId("phoneIn").value = "";
    byId("carrierIn").value = "";
    byId("slotIn").value = "";
    [["iosUrl", "cpIosUrl"], ["iosTok", "cpIosTok"],
     ["vUrl", "cpUrl"], ["vTpl", "cpTpl"], ["vTok", "cpTok"]]
      .forEach(([valueId, buttonId]) => resetGate(valueId, buttonId));
    byId("rule").replaceChildren();
    byId("statusOut").replaceChildren();
    byId("genOut").replaceChildren();
    byId("ruleLocked").classList.remove("hidden");
    byId("btnLock").disabled = true;
    byId("st").textContent = message;
  }

  function scheduleIdleLock() {
    if (!operatorToken) return;
    if (idleTimer) window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => lock("🔒 已因闲置 20 分钟自动锁定"), IDLE_LOCK_MS);
  }

  async function operatorFetch(path, options = {}) {
    if (!operatorToken) throw new Error("page_locked");
    const headers = new Headers(options.headers || {});
    headers.set("X-Operator-Token", operatorToken);
    const response = await fetch(path, {
      ...options,
      headers,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    if (response.status === 401 || response.status === 403) {
      const suffix = requestSuffix(response);
      lock("🔒 凭据已失效，请重新解锁" + suffix);
      throw new Error("operator_unauthorized");
    }
    scheduleIdleLock();
    return response;
  }

  function renderApk(apk, response) {
    const link = byId("apkDownload");
    const state = byId("apkState");
    const meta = byId("apkMeta");
    meta.replaceChildren();
    const ready = apk && apk.ready === true;
    if (!ready) {
      const reasons = {
        apk_missing: "安装包尚未部署",
        apk_unreadable: "安装包不可读",
        apk_manifest_missing: "生产完整性清单未配置",
        apk_manifest_invalid: "完整性清单格式错误",
        apk_integrity_failed: "安装包完整性校验失败",
      };
      const reason = apk && reasons[apk.reason] ? reasons[apk.reason] : "安装包状态不可用";
      link.removeAttribute("href");
      link.classList.add("off");
      link.setAttribute("aria-disabled", "true");
      link.textContent = "安装包暂不可下载";
      state.className = "critical mt8";
      state.textContent = reason + (response ? requestSuffix(response) : "") +
        "；请联系运维核对制品清单。";
      return;
    }
    link.href = "/api/v2/otp/smsforwarder.apk";
    link.classList.remove("off");
    link.setAttribute("aria-disabled", "false");
    link.textContent = "⬇ 下载安装包（" + text(apk.version) + "，服务器自托管）";
    state.className = "okbox mt8";
    state.textContent = apk.integrity === "verified"
      ? "✅ 文件存在且 SHA-256 与受控发布清单一致。"
      : "⚠️ 开发模式：文件可读，但未配置生产 SHA-256 清单。";
    meta.appendChild(field("版本", text(apk.version)));
    const sizeText = Number(apk.size_bytes || 0).toLocaleString("zh-CN") + " 字节";
    meta.appendChild(field("文件大小", sizeText));
    meta.appendChild(field("APK SHA-256", text(apk.sha256)));
    meta.appendChild(field("签名证书 SHA-256", text(apk.signer_sha256) || "发布清单未配置"));
    meta.appendChild(field("制品更新时间（UTC）", text(apk.updated_at)));
  }

  async function loadApkState() {
    try {
      const response = await fetch("/api/v2/otp/apk-info", {
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      });
      if (!response.ok) {
        renderApk(null, response);
        return;
      }
      const payload = await response.json();
      renderApk(payload && payload.apk, response);
    } catch (error) {
      renderApk(null, null);
    }
  }

  document.querySelectorAll(".cp[data-t]").forEach((button) => {
    button.addEventListener("click", () => copyText(button.getAttribute("data-t") || "", button));
  });
  [["cpIosUrl", "iosUrl"], ["cpIosTok", "iosTok"],
   ["cpUrl", "vUrl"], ["cpTpl", "vTpl"], ["cpTok", "vTok"]]
    .forEach(([buttonId, valueId]) => {
      const button = byId(buttonId);
      button.addEventListener("click", () => copyText(byId(valueId).textContent || "", button));
    });

  function selectDevice(device) {
    const iosSelected = device === "ios";
    byId("deviceIos").setAttribute("aria-pressed", iosSelected ? "true" : "false");
    byId("deviceAndroid").setAttribute("aria-pressed", iosSelected ? "false" : "true");
    byId("iosGuide").hidden = !iosSelected;
    byId("androidGuide").hidden = iosSelected;
    byId("deviceHint").textContent = iosSelected
      ? "当前显示 Apple 快捷指令方案：免费、无需第三方转发 App。"
      : "当前显示 Android SmsForwarder 方案：按步骤完成防杀、通道和规则设置。";
  }

  byId("deviceIos").addEventListener("click", () => selectDevice("ios"));
  byId("deviceAndroid").addEventListener("click", () => selectDevice("android"));

  byId("btnLoad").addEventListener("click", async () => {
    const status = byId("st");
    const candidate = byId("pw").value.trim();
    if (!candidate) {
      status.textContent = "请先输入管理密码";
      return;
    }
    lock("");
    status.textContent = "加载中…";
    try {
      const response = await fetch("/api/v2/otp/setup-info", {
        headers: { "X-Operator-Token": candidate },
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      });
      if (response.status === 401) {
        status.textContent = "密码错误（401）" + requestSuffix(response);
        return;
      }
      if (!response.ok) {
        status.textContent = "加载失败：HTTP " + response.status + requestSuffix(response);
        return;
      }
      const payload = await response.json();
      if (!payload || typeof payload.push_url !== "string" ||
          typeof payload.body_template !== "string" || typeof payload.relay_token !== "string" ||
          typeof payload.whitelist_regex !== "string") {
        status.textContent = "加载失败：响应格式不完整" + requestSuffix(response);
        return;
      }
      operatorToken = candidate;
      byId("btnLock").disabled = false;
      byId("ruleLocked").classList.add("hidden");
      bindGate("iosUrl", "cpIosUrl", payload.push_url);
      bindGate("iosTok", "cpIosTok", payload.relay_token);
      bindGate("vUrl", "cpUrl", payload.push_url);
      bindGate("vTpl", "cpTpl", payload.body_template);
      bindGate("vTok", "cpTok", payload.relay_token);
      byId("rule").replaceChildren(field("匹配的值（多行文本框）", payload.whitelist_regex));
      if (payload.apk) renderApk(payload.apk, response);
      status.textContent = "✅ 已解锁；闲置 20 分钟后自动锁定";
      scheduleIdleLock();
    } catch (error) {
      status.textContent = "网络错误，未保留凭据";
    }
  });

  byId("btnLock").addEventListener("click", () => lock("🔒 已手动锁定并清除敏感内容"));

  byId("btnReg").addEventListener("click", async () => {
    const out = byId("genOut");
    if (!operatorToken) {
      out.textContent = "先在上方输入管理密码并加载配置。";
      return;
    }
    const slot = byId("slotIn").value.trim();
    const carrier = byId("carrierIn").value.trim();
    const phone = byId("phoneIn").value.trim().replace(/^\+?86/, "");
    if (!/^1[0-9]{10}$/.test(phone)) {
      out.textContent = "号码格式不对（需 11 位）";
      return;
    }
    out.textContent = "注册中…";
    try {
      const response = await operatorFetch("/api/v2/otp/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, carrier, slot }),
      });
      if (!response.ok) {
        notice(out, "注册失败：HTTP " + response.status + requestSuffix(response));
        return;
      }
      const payload = await response.json();
      if (!payload || typeof payload.remark !== "string") {
        notice(out, "注册失败：响应格式不完整" + requestSuffix(response));
        return;
      }
      const result = document.createElement("div");
      result.className = "okbox";
      result.textContent = payload.created ? "✅ 已注册入册。请点击下方复制按钮。" :
        "✅ 已更新号码配置。请点击下方复制按钮。";
      out.replaceChildren(
        result,
        field("服务器生成的 Android 卡槽备注", payload.remark),
        field("iPhone JSON 的 phone 值", phone),
      );
      byId("phoneIn").value = "";
    } catch (error) {
      if (operatorToken) notice(out, "网络错误，号码未确认注册成功。");
    }
  });

  byId("btnStatus").addEventListener("click", async () => {
    const out = byId("statusOut");
    if (!operatorToken) {
      notice(out, "🔒 先在上方输入管理密码并加载配置。");
      return;
    }
    out.textContent = "查询中…";
    try {
      const response = await operatorFetch("/api/v2/otp/status");
      if (!response.ok) {
        notice(out, "查询失败：HTTP " + response.status + requestSuffix(response));
        return;
      }
      const payload = await response.json();
      const rows = payload && Array.isArray(payload.recent) ? payload.recent : [];
      if (!rows.length) {
        notice(out, "暂无推送记录。");
        return;
      }
      const table = document.createElement("table");
      const header = document.createElement("tr");
      ["手机号", "平台", "码长", "到达时间"].forEach((label) => {
        const cell = document.createElement("th");
        cell.textContent = label;
        header.appendChild(cell);
      });
      table.appendChild(header);
      rows.forEach((rowData) => {
        const row = document.createElement("tr");
        [text(rowData && rowData.phone), text(rowData && rowData.platform),
         String(rowData && rowData.code_len || "-"), text(rowData && rowData.time)]
          .forEach((value, index) => {
            const cell = document.createElement("td");
            if (index === 0 || index === 3) cell.className = "mono";
            cell.textContent = value;
            row.appendChild(cell);
          });
        table.appendChild(row);
      });
      out.replaceChildren(table);
    } catch (error) {
      if (operatorToken) notice(out, "网络错误，未取得推送状态。");
    }
  });

  ["pointerdown", "keydown", "touchstart"].forEach((eventName) => {
    document.addEventListener(eventName, scheduleIdleLock, { passive: true });
  });
  window.addEventListener("pagehide", () => lock(""));
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) lock("🔒 页面从后退缓存恢复，已重新锁定");
  });

  selectDevice("ios");
  loadApkState();
})();
</script>
</body>
</html>
"""
