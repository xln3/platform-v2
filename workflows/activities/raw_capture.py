"""每题级原始流量捕获（2026-08-10 起，用户拍板「需要有、默认开」）。

每个采集任务留存两类原始证据，随既有证据链（adapter 落本地文件 →
collection._persist_evidence_assets → MinIO CAS）入库：

1. completion 端点的**原始响应体**（SSE raw blob）—— ``dump_sse_raw`` 落
   ``<stem>-sse-raw.txt``（kind="sse_raw", relation="answer_sse_raw"）；
2. 本题期间页面级 **HAR 1.2** 流量记录 —— ``dump_har`` 落 ``<stem>-har.json``
   （kind="har", relation="answer_har"）。

与四个 adapter 既有 CDP capture 类同生命周期（每题一个实例、题末先 dump 后
detach），但挂在**独立 CDP session** 上各自 Network.enable（Chromium 支持同
target 多 session）——互不干扰，既有 capture 行为一行不改。订阅五个 Network
事件：requestWillBeSent（method/url/headers/postData）、responseReceived
（status/headers/mimeType）、dataReceived（计字节）、loadingFinished（对
body_url_hints 命中的 event-stream 同步 getResponseBody——Chromium 缓冲短保留，
既有 capture 同款纪律）、loadingFailed（落账）。

DLP 纪律（**组盘时主动剥，domain/evidence/dlp.py 只当双保险**）：

- request headers 的 cookie/authorization、response headers 的 set-cookie
  → ``[REDACTED]`` 占位（保 ``{name,value}`` 形状，dlp.py 的 JSON-aware
  词表能再兜底一次）；
- URL 查询串里 token/session/ticket/sig/auth 形参数值打码；
- ``postData.text`` 仅 completion 端点（body_url_hints 命中者）保留且有截断
  上限；``response.content`` 仅命中者收 ``{text,size}``，超
  ``GEO_HAR_BODY_MAX_BYTES`` 标 ``_truncated:true`` 只留前缀；静态资源只留
  metadata（size/mimeType，绝不收 body）。

HAR 序列化超 ``GEO_HAR_MAX_BYTES`` 的截断阶梯：先丢 postData.text（静态资源
content 组盘即不收，无需再丢）→ completion body 前缀截断 → entries 永不丢
（极端情形如实超顶写盘 + warning）。

env（worker 进程级；模块级函数每次调用现读，测试可 monkeypatch）：

- ``GEO_RAW_CAPTURE``（缺省 ``1``；``0`` = 全关回退现状：不建 session、不落
  文件、不出证据 ref）；
- ``GEO_HAR_MAX_BYTES``（缺省 8 MiB——远低于 evidence 单文件 30MB 硬顶）；
- ``GEO_HAR_BODY_MAX_BYTES``（缺省 4 MiB）。

已知边界：文心走 ServiceWorker 中转，page 级 session 可能看不到 completion
流量——看不到时 sse_raw 诚实缺省（None 不出证据）、HAR 有什么算什么；capture
全程零请求时 log warning（live 复核用）。
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from workflows.activities.collection import CollectionEvidenceRef

log = structlog.get_logger()

_ENV_RAW_CAPTURE = "GEO_RAW_CAPTURE"
_ENV_HAR_MAX_BYTES = "GEO_HAR_MAX_BYTES"
_ENV_HAR_BODY_MAX_BYTES = "GEO_HAR_BODY_MAX_BYTES"
_DEFAULT_HAR_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_HAR_BODY_MAX_BYTES = 4 * 1024 * 1024

# 组盘即剥（DLP 双保险之外的主动剥离面）
_REDACTED = "[REDACTED]"
_REQUEST_HEADER_BLOCKLIST = frozenset({"cookie", "authorization"})
_RESPONSE_HEADER_BLOCKLIST = frozenset({"set-cookie"})
_SENSITIVE_QUERY_KEY_RE = re.compile(r"(?i)(token|session|ticket|sig|auth)")

# completion 端点 postData.text 保留上限（问题 JSON 本体很小，64KiB 足够）
_POST_TEXT_MAX_CHARS = 64 * 1024
# HAR 截断阶梯的 body 前缀水位
_LADDER_BODY_PREFIX_BYTES = 64 * 1024
_LADDER_BODY_FLOOR_BYTES = 1024


def raw_capture_enabled() -> bool:
    """GEO_RAW_CAPTURE=0 全关（回退现状）；其余取值（含缺省）开启。"""
    return os.environ.get(_ENV_RAW_CAPTURE, "1").strip() != "0"


def har_max_bytes() -> int:
    return _env_int(_ENV_HAR_MAX_BYTES, _DEFAULT_HAR_MAX_BYTES)


def har_body_max_bytes() -> int:
    return _env_int(_ENV_HAR_BODY_MAX_BYTES, _DEFAULT_HAR_BODY_MAX_BYTES)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _mask_url(url: str) -> str:
    """URL 查询串敏感形参数值打码（token/session/ticket/sig/auth）；其余原文。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    masked = [
        (key, _REDACTED if _SENSITIVE_QUERY_KEY_RE.search(key) else value) for key, value in pairs
    ]
    if masked == pairs:
        return url
    # safe="[]"：[REDACTED] 占位保持字面可读（证据人工复核友好）。
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(masked, safe="[]"), parts.fragment)
    )


def _redacted_headers(headers: dict[str, Any], blocklist: frozenset[str]) -> list[dict[str, str]]:
    """headers dict → HAR [{name,value}]；黑名单键值 → [REDACTED] 占位保形状。"""
    return [
        {
            "name": str(name),
            "value": _REDACTED if str(name).lower() in blocklist else str(value),
        }
        for name, value in headers.items()
    ]


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节上限截断（字符边界对齐）；返回 (text, truncated)。"""
    payload = text.encode("utf-8")
    if len(payload) <= max_bytes:
        return text, False
    return payload[:max_bytes].decode("utf-8", "ignore"), True


# Chromium getResponseBody 非 base64 通道按响应 charset 解码；缺省 charset 时
# 走 WHATWG windows-1252 legacy 默认（5 个 cp1252 未定义字节 0x81/8D/8F/90/9D
# 按 WHATWG 语义映射到对应 C1 控制符）。逆映射把已被误解码的 str 还原回原始
# 网络字节；逆映射不覆盖的字符说明 Chromium 用的不是该解码（理论外情形），
# 此时保留原 str 诚实落盘，绝不硬转。
_WHATWG_WIN1252_C1_BYTES = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})
_WIN1252_INVERSE: dict[str, int] = {}
for _byte in range(256):
    _WIN1252_INVERSE[
        chr(_byte) if _byte in _WHATWG_WIN1252_C1_BYTES else bytes([_byte]).decode("cp1252")
    ] = _byte


def _restore_chromium_decoded_body(body: str, charset: str | None) -> str:
    """还原 Chromium 已按缺省 windows-1252 解码的响应体字节，再按 UTF-8 落 str。

    charset 非空时 Chromium 已按声明 charset 正确解码，原样返回。
    """
    if charset:
        return body
    try:
        raw = bytes(_WIN1252_INVERSE[char] for char in body)
    except KeyError:
        return body
    return raw.decode("utf-8", "replace")


def _iso_from_wall_time(wall_time: float | None) -> str:
    # CDP 必带 wallTime（epoch 秒）；缺失属防御分支（如实落采集当下时间）。
    stamp = wall_time if isinstance(wall_time, int | float) else None
    moment = datetime.fromtimestamp(
        stamp if stamp is not None else datetime.now(UTC).timestamp(), UTC
    )
    return moment.isoformat().replace("+00:00", "Z")


def maybe_raw_capture(
    context: Any,
    page: Any,
    *,
    body_url_hints: tuple[str, ...],
    creator: str,
) -> RawTrafficCapture | None:
    """建每题 RawTrafficCapture；GEO_RAW_CAPTURE=0 → None（全关回退现状）。

    CDP session 创建失败 → warning + None（诚实降级，绝不拖垮采集——原始证据
    是增强留痕，不是成功判据）。
    """
    if not raw_capture_enabled():
        return None
    try:
        return RawTrafficCapture(
            context,
            page,
            body_url_hints=body_url_hints,
            har_max_bytes=har_max_bytes(),
            body_max_bytes=har_body_max_bytes(),
            creator=creator,
        )
    except Exception as exc:
        log.warning(
            "raw_capture_init_failed",
            creator=creator,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def dump_raw_evidence_refs(
    capture: RawTrafficCapture | None,
    evidence_dir: Path,
    file_stem: str,
    *,
    source_url: str | None,
    warn_tag: str,
) -> list[CollectionEvidenceRef]:
    """题末导出 sse_raw + har → evidence refs（capture=None/写盘失败 → 对应缺省）。

    幂等：同 (dir, stem) 重复调用走 RawTrafficCapture 内部缓存，不重复写盘。
    写盘失败如实 warning 且不出该证据（绝不出残缺证据），不拖垮采集。
    """
    if capture is None:
        return []
    refs: list[CollectionEvidenceRef] = []
    try:
        sse_path = capture.dump_sse_raw(evidence_dir, file_stem)
    except OSError as exc:
        log.warning(
            f"{warn_tag}_sse_raw_write_failed",
            file_stem=file_stem,
            error=f"{type(exc).__name__}: {exc}",
        )
    else:
        if sse_path is not None:
            refs.append(
                CollectionEvidenceRef(
                    kind="sse_raw",
                    path=str(sse_path),
                    relation_type="answer_sse_raw",
                    mime_type="text/event-stream",
                    source_url=source_url,
                )
            )
    try:
        har_path = capture.dump_har(evidence_dir, file_stem)
    except OSError as exc:
        log.warning(
            f"{warn_tag}_har_write_failed",
            file_stem=file_stem,
            error=f"{type(exc).__name__}: {exc}",
        )
    else:
        refs.append(
            CollectionEvidenceRef(
                kind="har",
                path=str(har_path),
                relation_type="answer_har",
                mime_type="application/har+json",
                source_url=source_url,
            )
        )
    return refs


class RawTrafficCapture:
    """每题一个实例，与既有 capture 同生命周期（题末先 dump 后 detach）。

    自组 HAR 1.2 + 落 completion 端点原始响应体。与既有 capture 类在同一页面
    各有独立 CDP session（Chromium 支持同 target 多 session 各自 Network.enable）。
    """

    def __init__(
        self,
        context: Any,
        page: Any,
        *,
        body_url_hints: tuple[str, ...],
        har_max_bytes: int,
        body_max_bytes: int,
        creator: str = "geo-raw-capture",
        creator_version: str = "1",
    ) -> None:
        self._cdp = context.new_cdp_session(page)
        self._cdp.send("Network.enable")
        self._body_url_hints = tuple(body_url_hints)
        self._har_max_bytes = har_max_bytes
        self._body_max_bytes = body_max_bytes
        self._creator = creator
        self._creator_version = creator_version
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._body_hits: list[str] = []
        self._bodies: dict[str, str] = {}
        self._dump_cache: dict[tuple[str, str, str], Path | None] = {}
        self._warned_empty = False
        for name in (
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
            "Network.dataReceived",
        ):
            self._cdp.on(name, lambda payload, n=name: self._handle(n, payload))

    @property
    def request_count(self) -> int:
        return len(self._order)

    def detach(self) -> None:
        """best-effort 断开 CDP session（与既有 capture 同款纪律：题末断开避免
        旧 session 挂着监听累积）。失败静默——页面可能已随 context 关闭。"""
        try:
            self._cdp.detach()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CDP 事件
    # ------------------------------------------------------------------

    def _handle(self, name: str, payload: dict[str, Any]) -> None:
        try:
            req_id = payload.get("requestId") or ""
            if not req_id:
                return
            if name == "Network.requestWillBeSent":
                self._on_request(req_id, payload)
            elif name == "Network.responseReceived":
                self._on_response(req_id, payload)
            elif name == "Network.loadingFinished":
                record = self._record(req_id)
                ts = payload.get("timestamp")
                if isinstance(ts, int | float):
                    record["finished_ts"] = float(ts)
                if req_id in self._body_hits:
                    self._fetch_body(req_id)
            elif name == "Network.loadingFailed":
                record = self._record(req_id)
                record["failed"] = True
                record["error_text"] = str(payload.get("errorText") or "")
                ts = payload.get("timestamp")
                if isinstance(ts, int | float):
                    record["finished_ts"] = float(ts)
            elif name == "Network.dataReceived":
                record = self._record(req_id)
                record["bytes"] = int(record["bytes"]) + int(payload.get("dataLength", 0) or 0)
        except Exception:
            pass

    def _record(self, req_id: str) -> dict[str, Any]:
        record = self._records.get(req_id)
        if record is None:
            record = {
                "url": "",
                "method": "",
                "request_headers": {},
                "post_data": None,
                "post_truncated": False,
                "started_wall": None,
                "started_ts": None,
                "response_ts": None,
                "finished_ts": None,
                "status": None,
                "status_text": "",
                "response_headers": {},
                "mime_type": "",
                "protocol": "",
                "bytes": 0,
                "failed": False,
                "error_text": "",
            }
            self._records[req_id] = record
            self._order.append(req_id)
        return record

    def _on_request(self, req_id: str, payload: dict[str, Any]) -> None:
        record = self._record(req_id)
        req = payload.get("request") or {}
        # 重定向同 requestId 复发（带 redirectResponse）：请求字段以最后一跳为
        # 准、起始时间保留首段——302 链在 HAR 里坍缩成最后一跳（证据关注终点）。
        record["url"] = str(req.get("url") or record["url"])
        record["method"] = str(req.get("method") or record["method"])
        record["request_headers"] = dict(req.get("headers") or {})
        post_data = req.get("postData")
        if isinstance(post_data, str):
            if len(post_data) > _POST_TEXT_MAX_CHARS:
                record["post_data"] = post_data[:_POST_TEXT_MAX_CHARS]
                record["post_truncated"] = True
            else:
                record["post_data"] = post_data
                record["post_truncated"] = False
        wall = payload.get("wallTime")
        if isinstance(wall, int | float) and record["started_wall"] is None:
            record["started_wall"] = float(wall)
        ts = payload.get("timestamp")
        if isinstance(ts, int | float) and record["started_ts"] is None:
            record["started_ts"] = float(ts)

    def _on_response(self, req_id: str, payload: dict[str, Any]) -> None:
        record = self._record(req_id)
        resp = payload.get("response") or {}
        url = str(resp.get("url") or record["url"])
        record["url"] = url
        record["status"] = resp.get("status")
        record["status_text"] = str(resp.get("statusText") or "")
        record["response_headers"] = dict(resp.get("headers") or {})
        record["mime_type"] = str(resp.get("mimeType") or "")
        record["protocol"] = str(resp.get("protocol") or "")
        ts = payload.get("timestamp")
        if isinstance(ts, int | float):
            record["response_ts"] = float(ts)
        if "event-stream" in record["mime_type"] and any(
            hint in url for hint in self._body_url_hints
        ):
            if req_id not in self._body_hits:
                self._body_hits.append(req_id)

    def _fetch_body(self, req_id: str) -> None:
        """loadingFinished 同步拉原始响应体（Chromium 只短暂保留缓冲——既有
        capture 同款纪律）。原文存储=忠实还原网络字节：base64 通道直接解码；
        非 base64 通道 Chromium 已按响应 charset 把字节解码成 str——缺省
        charset 时按 WHATWG windows-1252（元宝/豆包的 text/event-stream 不
        带 charset，2026-09-02 实证落盘即 mojibake），此处逆向回原始字节再按
        UTF-8 还原，不改写内容本身。"""
        if req_id in self._bodies:
            return
        try:
            result = self._cdp.send("Network.getResponseBody", {"requestId": req_id})
        except Exception:
            return
        body = result.get("body", "") or ""
        if result.get("base64Encoded"):
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:
                return
        else:
            body = _restore_chromium_decoded_body(body, self._response_charset(req_id))
        self._bodies[req_id] = body

    def _response_charset(self, req_id: str) -> str | None:
        record = self._records.get(req_id) or {}
        headers = record.get("response_headers") or {}
        for name, value in headers.items():
            if name.lower() == "content-type":
                match = re.search(r"charset=([\w.-]+)", str(value), re.I)
                return match.group(1).lower() if match else None
        return None

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    def dump_sse_raw(self, directory: Path, stem: str) -> Path | None:
        """命中 completion 端点的原始 body 原文 → ``<stem>-sse-raw.txt``；
        无命中/抓取失败 → None（诚实缺省）。幂等（同 dir+stem 走缓存）。"""
        key = ("sse_raw", str(directory), stem)
        if key in self._dump_cache:
            return self._dump_cache[key]
        self._warn_if_empty(stem)
        result: Path | None = None
        for rid in reversed(self._body_hits):
            body = self._bodies.get(rid)
            if body:
                result = Path(directory) / f"{stem}-sse-raw.txt"
                result.write_text(body, encoding="utf-8")
                break
        self._dump_cache[key] = result
        return result

    def dump_har(self, directory: Path, stem: str) -> Path:
        """本题 HAR 1.2 → ``<stem>-har.json``。截断阶梯：丢 postData.text →
        body 前缀截断 → entries 永不丢（仍超顶如实写盘 + warning）。幂等。"""
        key = ("har", str(directory), stem)
        cached = self._dump_cache.get(key)
        if cached is not None:
            return cached
        self._warn_if_empty(stem)
        har = self._har_log(body_limit=None, include_post_text=True)
        payload = _encode_har(har)
        if len(payload) > self._har_max_bytes:
            har = self._har_log(body_limit=None, include_post_text=False)
            payload = _encode_har(har)
        if len(payload) > self._har_max_bytes:
            har = self._har_log(body_limit=_LADDER_BODY_PREFIX_BYTES, include_post_text=False)
            payload = _encode_har(har)
        if len(payload) > self._har_max_bytes:
            har = self._har_log(body_limit=_LADDER_BODY_FLOOR_BYTES, include_post_text=False)
            payload = _encode_har(har)
        if len(payload) > self._har_max_bytes:
            log.warning(
                "raw_capture_har_over_budget",
                stem=stem,
                bytes=len(payload),
                budget=self._har_max_bytes,
            )
        path = Path(directory) / f"{stem}-har.json"
        path.write_bytes(payload)
        self._dump_cache[key] = path
        return path

    def _warn_if_empty(self, stem: str) -> None:
        """capture 全程零请求 → warning（文心 SW 中转等 page 级不可见情形的
        live 复核信号）。每实例最多一次。"""
        if self._order or self._warned_empty:
            return
        self._warned_empty = True
        log.warning("raw_capture_zero_requests", stem=stem, creator=self._creator)

    # ------------------------------------------------------------------
    # HAR 组装（组盘即剥：cookie/authorization/set-cookie/敏感查询参数）
    # ------------------------------------------------------------------

    def _har_log(self, *, body_limit: int | None, include_post_text: bool) -> dict[str, Any]:
        entries = [
            self._har_entry(rid, body_limit=body_limit, include_post_text=include_post_text)
            for rid in self._order
        ]
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": self._creator, "version": self._creator_version},
                "entries": entries,
            }
        }

    def _har_entry(
        self, req_id: str, *, body_limit: int | None, include_post_text: bool
    ) -> dict[str, Any]:
        record = self._records[req_id]
        is_hit = req_id in self._body_hits
        request: dict[str, Any] = {
            "method": record["method"],
            "url": _mask_url(record["url"]),
            "httpVersion": record["protocol"] or "unknown",
            "headers": _redacted_headers(record["request_headers"], _REQUEST_HEADER_BLOCKLIST),
            "headersSize": -1,
            "bodySize": len(record["post_data"] or ""),
        }
        if is_hit and include_post_text and record["post_data"] is not None:
            post_text, post_cut = _truncate_text(str(record["post_data"]), _POST_TEXT_MAX_CHARS)
            post: dict[str, Any] = {
                "mimeType": str(record["request_headers"].get("content-type") or ""),
                "text": post_text,
            }
            if post_cut or record["post_truncated"]:
                post["_truncated"] = True
            request["postData"] = post
        content: dict[str, Any] = {
            "size": int(record["bytes"]),
            "mimeType": record["mime_type"],
        }
        if is_hit:
            body = self._bodies.get(req_id)
            if body is not None:
                text, cut = _truncate_text(body, body_limit or self._body_max_bytes)
                content["text"] = text
                content["size"] = len(body.encode("utf-8"))
                if cut:
                    content["_truncated"] = True
            else:
                # 命中但 getResponseBody 失败（缓冲被清/竞态）——诚实标注不编造。
                content["comment"] = "response body unavailable"
        started_ts = record["started_ts"]
        response_ts = record["response_ts"]
        finished_ts = record["finished_ts"]
        wait_ms = (
            max(0.0, (response_ts - started_ts) * 1000.0)
            if isinstance(started_ts, float) and isinstance(response_ts, float)
            else 0.0
        )
        receive_ms = (
            max(0.0, (finished_ts - response_ts) * 1000.0)
            if isinstance(response_ts, float) and isinstance(finished_ts, float)
            else 0.0
        )
        entry: dict[str, Any] = {
            "startedDateTime": _iso_from_wall_time(record["started_wall"]),
            "time": round(wait_ms + receive_ms, 3),
            "request": request,
            "response": {
                "status": int(record["status"] or 0),
                "statusText": record["status_text"],
                "httpVersion": record["protocol"] or "unknown",
                "headers": _redacted_headers(
                    record["response_headers"], _RESPONSE_HEADER_BLOCKLIST
                ),
                "content": content,
                "headersSize": -1,
                "bodySize": int(record["bytes"]),
            },
            "timings": {
                "send": 0,
                "wait": round(wait_ms, 3),
                "receive": round(receive_ms, 3),
            },
            "_requestId": req_id,
        }
        if record["failed"]:
            entry["_failed"] = True
            entry["_errorText"] = record["error_text"]
        return entry


def _encode_har(har: dict[str, Any]) -> bytes:
    return json.dumps(har, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
