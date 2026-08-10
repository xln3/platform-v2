"""V2 原生 OTP 收件端点（公网机器面，无会话，共享密钥门）。

旧系统 geosys 2026-08-07 退役，OTP 推送链整体迁入 V2。契约对齐旧
``server/geosys/otp_ingest.py``（docstring 即契约），代码 V2 原生：

  * ``POST /api/v2/otp/push``    末端 SmsForwarder 推「手机号+验证码短信」。
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
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

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
) -> None:
    """共享密钥门（常量时间比较）。env 未配 → fail-closed 503（功能关闭，绝不
    开放无鉴权端点）；只认 header——不设 ?token= 兜底（会落日志/Referer）。"""
    configured = os.environ.get(env_name, "") or ""
    if not configured:
        raise HTTPException(status_code=503, detail={"code": disabled_code})
    supplied = request.headers.get(header_name, "") or ""
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail={"code": unauthorized_code})


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
    """处理一条 SmsForwarder 推送（JSON ``{"slot","sms"}`` / 表单 / 纯文本期望格式）。

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
        raise HTTPException(status_code=400, detail={"code": "bad_phone"})
    if _rate_limited("latest", phone):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    within = _clamp_within(request.query_params.get("within"))
    path = _inbox_dir() / f"{phone}.json"
    if not path.exists():
        return JSONResponse(
            content={"ok": True, "found": False, "within": within, "reason": "no_sms_for_phone"}
        )
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse(
            content={"ok": True, "found": False, "within": within, "reason": "unreadable"}
        )
    ts = float(rec.get("ts", 0) or 0)
    age = time.time() - ts
    code = str(rec.get("code") or "").strip()
    if age > within:
        return JSONResponse(
            content={
                "ok": True,
                "found": False,
                "within": within,
                "age_s": round(age, 1),
                "reason": "stale",
            }
        )
    if not code:
        return JSONResponse(
            content={
                "ok": True,
                "found": False,
                "within": within,
                "age_s": round(age, 1),
                "reason": "no_code_extracted",
            }
        )
    return JSONResponse(
        content={
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
# setup-info 的 slot_remarks = env 备注 ∪ 注册表（按手机号去重，同号以注册表为准）。
# 并发口径：读-改-写无跨进程锁，多 worker 下同瞬间双注册会丢一条——运维人工
# 单操作者低频动作，实际无感；真出现并发需求再补文件锁。
# ---------------------------------------------------------------------------

_APK_ENV = "GEO_OTP_APK_PATH"
_DEFAULT_APK_PATH = Path(__file__).resolve().parents[3] / "runtime" / "smsforwarder.apk"

# 与 server/docs/OTP_SMSFORWARDER.md §1b/§1c 同值（单一事实源=本常量，改时同步文档）
_BODY_TEMPLATE = '{"slot":"{{CARD_SLOT}}","sms":"{{SMS}}"}'
_WHITELIST_REGEX = (
    r"(?s).*(豆包|深度求索|DeepSeek|文心一言|百度|通义|千问|元宝|腾讯"
    r"|博客园|搜狐|百家号|今日头条|头条号).*"
)
# 现役测量号的卡槽备注（双卡防错标=SIM 硬件级真值）；只经 operator 门下发出页面。
# 真源 = env ``GEO_OTP_SLOT_REMARKS``（逗号分隔；换号/换卡运维改这一处即可）∪
# 注册表（``POST /otp/register`` 落盘，见下）——缺省回退到下列现役两号；
# 页面不内嵌，解锁后由 setup-info 实时下发。
_DEFAULT_SLOT_REMARKS = (
    "SIM1_中国联通_+8613121622231",
    "SIM2_中国移动_+8615510162660",
)

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


def _slot_remarks() -> list[str]:
    raw = os.environ.get("GEO_OTP_SLOT_REMARKS", "").strip()
    env_items = (
        [item.strip() for item in raw.split(",") if item.strip()]
        if raw
        else list(_DEFAULT_SLOT_REMARKS)
    )
    # 合并注册表：按手机号去重，同号以注册表为准（注册动作更新、更近）。
    registered = _read_registry()
    reg_phones = {str(e["phone"]) for e in registered}
    merged = [r for r in env_items if phone_from_slot(r) not in reg_phones]
    merged.extend(str(e["remark"]) for e in registered)
    return merged


_RATE_LIMITS["setup"] = (30, 60.0)


@router.post("/otp/register")
async def otp_register(request: Request) -> JSONResponse:
    """在册号码注册（operator 门内）：``{"phone","carrier"?,"slot"?}`` → 登记进
    服务端注册表（原子写），setup-info 即刻下发。幂等：同号再注册=更新备注。
    响应带完整 remark（operator 自填自拿，装机页一键复制进手机卡槽备注）。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    body_text = (await request.body()).decode("utf-8", "replace")
    if len(body_text.encode("utf-8")) > 4096:  # 注册体极小，公网面兜底防滥
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    try:
        data: Any = json.loads(body_text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail={"code": "bad_body"})
    phone = re.sub(r"^\+?86", "", str(data.get("phone") or "").strip())
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail={"code": "bad_phone"})
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
    log.info("otp_number_registered", phone=mask_phone(phone), created=created, slot=slot or "-")
    return JSONResponse(
        content={"ok": True, "created": created, "phone": mask_phone(phone), "remark": remark}
    )


@router.get("/otp/setup-info")
def otp_setup_info(request: Request) -> JSONResponse:
    """装机配置（operator 门内）：推送地址/relay token/Body 模板/白名单正则/卡槽备注。
    URL 从请求 origin 派生（供工具消费；**装机页展示不用它**——页面以浏览器
    location.origin 拼地址，反代 Host $host 丢端口时页面依然正确）。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    origin = str(request.base_url).rstrip("/")
    return JSONResponse(
        content={
            "ok": True,
            "push_url": f"{origin}/api/v2/otp/push",
            "relay_token": os.environ.get("GEO_OTP_RELAY_TOKEN", ""),
            "body_template": _BODY_TEMPLATE,
            "whitelist_regex": _WHITELIST_REGEX,
            "slot_remarks": _slot_remarks(),
            "apk_url": f"{origin}/api/v2/otp/smsforwarder.apk",
            "latest_example": f"{origin}/api/v2/otp/latest?phone=13121622231",
        }
    )


@router.get("/otp/status")
def otp_status(request: Request) -> JSONResponse:
    """最近推送一览（operator 门内，**掩码**——无 code 无原文）：phone 掩码 +
    code_len + 平台 + 到达秒龄。给装机页「验证」步用（手机自查转发是否到达）。"""
    _require_operator_token(request)
    if _rate_limited("setup", request.client.host if request.client else "?"):
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
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
                "platform": str(rec.get("platform") or "") or "-",
                "code_len": len(code),
                "time": datetime.fromtimestamp(ts, tz=_TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
                "age_s": round(now - ts, 1),
            }
        )
    return JSONResponse(content={"ok": True, "recent": rows})


@router.get("/otp/smsforwarder.apk")
def otp_apk() -> Any:
    """SmsForwarder 安装包自托管下载（公开——APK 非秘密；CN 手机直连 GitHub 慢）。
    文件路径 env ``GEO_OTP_APK_PATH``，缺省 ``platform-v2/runtime/smsforwarder.apk``。"""
    from fastapi.responses import FileResponse

    path = Path(os.environ.get(_APK_ENV, "") or _DEFAULT_APK_PATH)
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "apk_missing"})
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename="SmsForwarder.apk",
    )


@router.get("/otp/setup")
def otp_setup_page() -> Any:
    """装机配置页（公开，纯静态说明+表单，**零秘密内嵌**）：输入管理密码后经
    setup-info 拉取含 key 的配置项，逐项一键复制。"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_SETUP_PAGE_HTML)


_SETUP_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTP 转发手机配置 · GEO</title>
<style>
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
  .cp.off { background: #cbd5e1; cursor: default; }
  .opt { font-size: 14px; padding: 2px 0; }
  .val.locked { color: #94a3b8; }
  .big { display: block; width: 100%; padding: 13px; font-size: 15px; font-weight: 600;
         background: #4f46e5; color: #fff; text-align: center; text-decoration: none; }
  .ghost { background: #e2e8f0; color: #1a2233; }
  input[type=password] { width: 100%; font: inherit; padding: 11px 12px; border-radius: 8px;
         border: 1px solid #cbd5e1; margin: 8px 0; }
  ul, ol { padding-left: 20px; margin: 8px 0; }
  li { margin: 5px 0; font-size: 14px; }
  .warn { background: #fef3c7; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
  .hidden { display: none; }
  #st { font-size: 13px; margin-top: 6px; min-height: 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td, th { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e2e8f0; }
  .mono { font-family: ui-monospace, Menlo, monospace; }
</style>
</head>
<body>
<main>
  <h1>OTP 转发手机配置</h1>
  <div class="sub">SmsForwarder → GEO V2 收件端点 · 按顺序逐步执行</div>

  <h2>第 0 步 · 防杀设置（必做，上次就是这么断的）</h2>
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

  <h2>第 1 步 · 安装 SmsForwarder</h2>
  <div class="card">
    <a class="big" href="/api/v2/otp/smsforwarder.apk">⬇ 下载安装包（v3.5.0，服务器自托管）</a>
    <div class="sub" style="margin-top:8px">已装过可跳过——上次只是被系统杀死，不是卸载。</div>
  </div>

  <h2>第 2 步 · 卡槽备注（双卡防错标）</h2>
  <div class="card">
    <div class="sub">SmsForwarder → 通用设置 → 个性设置 →「SIM1主键/备注」「SIM2主键/备注」
      的备注框，填入含<b>该卡真实号码</b>的文本（<span class="mono">{{CARD_SLOT}}</span>
      发的就是它；App 提示格式：运营商_手机号）。</div>
    <div class="warn">网页读不到本机号码（浏览器安全限制）——点「刷新」拉取服务器
      <b>当前在册</b>的测量号备注；配的手机不在册时，在下方<b>注册</b>（入册后任何
      装机页刷新都能拉到，卡槽不限 SIM1/2）。</div>
    <button class="big ghost" id="btnSlots" style="margin-top:10px">🔄 刷新获取在册号码备注</button>
    <div id="slots"></div>
    <div class="field" style="margin-top:14px">
      <label>注册新号码（需先解锁；同号再注册=更新备注）</label>
      <div class="row">
        <input id="slotIn" list="slotOpts" placeholder="卡槽（选填，如 SIM1）"
               style="width:190px;font:inherit;padding:9px;border-radius:8px;
                      border:1px solid #cbd5e1">
        <datalist id="slotOpts">
          <option value="SIM1"></option><option value="SIM2"></option>
        </datalist>
        <input id="carrierIn" placeholder="运营商（选填，如 中国联通）"
               style="flex:1;font:inherit;padding:9px;border-radius:8px;border:1px solid #cbd5e1">
      </div>
      <div class="row" style="margin-top:8px">
        <input id="phoneIn" inputmode="numeric" maxlength="13" placeholder="11 位手机号（必填）"
               style="flex:1;font:inherit;padding:9px;border-radius:8px;border:1px solid #cbd5e1">
        <button class="cp" id="btnReg">注册并复制备注</button>
      </div>
      <div id="genOut" class="sub"></div>
    </div>
  </div>

  <h2>第 3 步 · 发送通道（Webhook）</h2>
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
      <div class="row" style="margin-top:6px"><div class="val">application/json</div>
        <button class="cp" data-t="application/json">复制</button></div></div>
    <div class="field"><label>代理设置（单选）</label>
      <div class="opt">选 <b>无代理</b></div></div>
    <div class="sub" style="margin-top:6px">填完点【保存】，再点通道页【测试】；然后走第 5 步验证。
      （v3.5.0 表单没有「忽略 SSL 证书」开关，无需找；旧文档那条已废止。）</div>
  </div>

  <h2>第 4 步 · 转发规则（平台白名单）</h2>
  <div class="card">
    <div class="sub">SmsForwarder → 转发规则 → 新增（短信转发规则）。逐格对应：</div>
    <div class="warn" style="margin-bottom:8px">🔒 本规则是<b>白名单制</b>：只有命中下列
      <b>测评平台（豆包/DeepSeek/文心一言/通义千问/元宝）和媒体号平台（博客园/搜狐/百家号/头条号）</b>
      的短信才会被转发。<b>银行、支付、社交等一切金融与隐私短信不匹配、不转发、不离开本机</b>——
      切勿把规则泛化成「所有验证码」。</div>
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
    <div class="warn" style="margin-top:8px">⚠️ full-match 语义：关键词两头的
      <span class="mono">.*</span> 必须保留，删掉=永不匹配。</div>
    <div class="field"><label>三个开关</label>
      <div class="opt">启用自定义模版=<b>关</b>；启用正则替换内容=<b>关</b>；
        启用该条转发规则=<b>开</b></div></div>
    <div class="field"><label>免打扰(禁用转发)时间段</label>
      <div class="opt">保持 <b>00:00 ~ 00:00</b>（起止相等=该功能停用，即全天可转发）</div></div>
    <div class="sub" style="margin-top:6px">填完点【保存】。</div>
  </div>

  <h2>第 5 步 · 验证</h2>
  <div class="card">
    <ol>
      <li>用另一台手机给测量号发一条测试短信（内容含「豆包」等白名单词）。</li>
      <li>解锁本页后点下方按钮，能看到刚才那条（手机号掩码 + 码长 + 平台）即全链路通。</li>
    </ol>
    <button class="big ghost" id="btnStatus">查看最近推送</button>
    <div id="statusOut" style="margin-top:10px"></div>
  </div>

  <h2>解锁配置项</h2>
  <div class="card">
    <input type="password" id="pw" placeholder="管理密码（operator token）" autocomplete="off">
    <button class="big" id="btnLoad">加载配置</button>
    <div id="st"></div>
  </div>
</main>
<script>
let TOKEN = "";
function copyText(txt, btn) {
  const ta = document.createElement("textarea");
  ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  if (!ok && navigator.clipboard) { navigator.clipboard.writeText(txt).catch(()=>{}); }
  if (btn) { const o = btn.textContent; btn.textContent = "已复制"; btn.classList.add("ok");
    setTimeout(() => { btn.textContent = o; btn.classList.remove("ok"); }, 1200); }
}
document.querySelectorAll(".cp[data-t]").forEach(b => {
  b.addEventListener("click", () => copyText(b.getAttribute("data-t"), b));
});
function bindGate(valId, btnId, value) {
  const v = document.getElementById(valId);
  v.textContent = value; v.classList.remove("locked");
  const b = document.getElementById(btnId);
  b.disabled = false; b.classList.remove("off");
  b.addEventListener("click", (ev) => copyText(value, ev.target));
}
function field(label, value) {
  const d = document.createElement("div"); d.className = "field";
  d.innerHTML = '<label>' + label + '</label>' +
    '<div class="row"><div class="val"></div><button class="cp">复制</button></div>';
  d.querySelector(".val").textContent = value;
  d.querySelector(".cp").addEventListener("click", (ev) => copyText(value, ev.target));
  return d;
}
document.getElementById("btnLoad").addEventListener("click", async () => {
  const st = document.getElementById("st");
  TOKEN = document.getElementById("pw").value.trim();
  if (!TOKEN) { st.textContent = "请先输入管理密码"; return; }
  st.textContent = "加载中…";
  try {
    const r = await fetch("/api/v2/otp/setup-info", { headers: { "X-Operator-Token": TOKEN } });
    if (r.status === 401) { st.textContent = "密码错误（401）"; return; }
    if (!r.ok) { st.textContent = "加载失败：" + r.status; return; }
    const d = await r.json();
    st.textContent = "✅ 已解锁";
    document.getElementById("ruleLocked").classList.add("hidden");
    fillSlots(d.slot_remarks || []);
    // 推送地址以浏览器地址栏 origin 为准：反代 Host 头是 $host（丢 8443 端口），
    // 操作者正在访问的地址才是手机该推的公网地址（20260810 漏端口教训）。
    bindGate("vUrl", "cpUrl", location.origin + "/api/v2/otp/push");
    bindGate("vTpl", "cpTpl", d.body_template);
    bindGate("vTok", "cpTok", d.relay_token);
    const rule = document.getElementById("rule"); rule.innerHTML = "";
    rule.appendChild(field("匹配的值（多行文本框）", d.whitelist_regex));
  } catch (e) { st.textContent = "网络错误：" + e; }
});
function fillSlots(list) {
  const slots = document.getElementById("slots"); slots.innerHTML = "";
  list.forEach((s, i) => slots.appendChild(field("在册备注 " + (i+1), s)));
}
document.getElementById("btnSlots").addEventListener("click", async () => {
  const out = document.getElementById("slots");
  if (!TOKEN) { out.innerHTML = '<div class="warn">🔒 先在下方输入管理密码并加载配置。</div>';
    return; }
  try {
    const r = await fetch("/api/v2/otp/setup-info", { headers: { "X-Operator-Token": TOKEN } });
    if (!r.ok) { out.innerHTML = '<div class="warn">刷新失败：' + r.status + "</div>"; return; }
    const d = await r.json();
    fillSlots(d.slot_remarks || []);
  } catch (e) { out.innerHTML = '<div class="warn">网络错误：' + e + "</div>"; }
});
document.getElementById("btnReg").addEventListener("click", async (ev) => {
  const out = document.getElementById("genOut");
  if (!TOKEN) { out.textContent = "先在下方输入管理密码并加载配置。"; return; }
  const slot = document.getElementById("slotIn").value.trim();
  const carrier = document.getElementById("carrierIn").value.trim();
  const phone = document.getElementById("phoneIn").value.trim().replace(/^\+?86/, "");
  if (!/^1[0-9]{10}$/.test(phone)) { out.textContent = "号码格式不对（需 11 位）"; return; }
  out.textContent = "注册中…";
  try {
    const r = await fetch("/api/v2/otp/register", {
      method: "POST",
      headers: { "X-Operator-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ phone: phone, carrier: carrier, slot: slot }),
    });
    if (r.status === 401) { out.textContent = "密码错误（401）"; return; }
    if (!r.ok) { out.textContent = "注册失败：" + r.status; return; }
    const d = await r.json();
    copyText(d.remark, ev.target);
    out.textContent = (d.created ? "✅ 已注册入册" : "✅ 已更新在册备注") +
      "并复制：" + d.remark + " ——粘贴到通用设置里该卡的「主键/备注」框。";
    const rr = await fetch("/api/v2/otp/setup-info",
                           { headers: { "X-Operator-Token": TOKEN } });
    if (rr.ok) { const dd = await rr.json(); fillSlots(dd.slot_remarks || []); }
  } catch (e) { out.textContent = "网络错误：" + e; }
});
document.getElementById("btnStatus").addEventListener("click", async () => {
  const out = document.getElementById("statusOut");
  if (!TOKEN) { out.innerHTML = '<div class="warn">🔒 先在下方输入管理密码并加载配置。</div>';
    return; }
  out.textContent = "查询中…";
  try {
    const r = await fetch("/api/v2/otp/status", { headers: { "X-Operator-Token": TOKEN } });
    if (!r.ok) { out.textContent = "查询失败：" + r.status; return; }
    const d = await r.json();
    if (!d.recent || !d.recent.length) {
      out.innerHTML = '<div class="warn">暂无推送记录。</div>'; return; }
    let h = "<table><tr><th>手机号</th><th>平台</th><th>码长</th><th>到达时间</th></tr>";
    d.recent.forEach(x => {
      h += "<tr><td class=mono>" + x.phone + "</td><td>" + x.platform + "</td><td>" +
           (x.code_len || "-") + "</td><td class=mono>" + x.time + "</td></tr>";
    });
    out.innerHTML = h + "</table>";
  } catch (e) { out.textContent = "网络错误：" + e; }
});
</script>
</body>
</html>
"""
