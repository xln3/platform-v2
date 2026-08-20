"""原始流量捕获（2026-08-10 起，用户拍板「需要有、默认开」）单元测试。

覆盖：RawTrafficCapture 六事件序列 → HAR 形状/截断阶梯/组盘剥除；sse_raw 命中与
诚实缺省；env 开关；DLP round-trip（组盘已剥 + dlp.py 双保险）；collection 词表；
失败题 `_persist_collection_failure` 证据持久化与重试幂等。fake CDP/page 注入，
绝不起真浏览器/真 DB/真 MinIO。
"""

from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from geo_platform.collection.models import CollectionRun, CollectionTask
from geo_platform.projects.models import Project
from structlog.testing import capture_logs

from domain.evidence.dlp import redact_bytes
from workflows.activities import collection
from workflows.activities.collection import (
    CollectionBatchItemResult,
    CollectionEvidenceRef,
    _normalize_evidence_refs,
)
from workflows.activities.raw_capture import (
    RawTrafficCapture,
    dump_raw_evidence_refs,
    har_body_max_bytes,
    har_max_bytes,
    maybe_raw_capture,
    raw_capture_enabled,
)

# ---------------------------------------------------------------------------
# fake CDP（单总线广播；getResponseBody 按 requestId 出摊）
# ---------------------------------------------------------------------------

_SSE_BODY = 'data: {"a":1}\n\ndata: [DONE]\n'


class _FakeCDP:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        # requestId → (body, base64Encoded)
        self.bodies: dict[str, tuple[str, bool]] = {}
        self.sent: list[str] = []
        self.detached = 0

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append(method)
        if method == "Network.getResponseBody":
            rid = (params or {}).get("requestId", "")
            body, encoded = self.bodies.get(rid, ("", False))
            return {"body": body, "base64Encoded": encoded}
        return {}

    def on(self, name: str, fn: Any) -> None:
        self.handlers.setdefault(name, []).append(fn)

    def detach(self) -> None:
        self.detached += 1

    def emit(self, name: str, payload: dict[str, Any]) -> None:
        for fn in self.handlers.get(name, []):
            fn(payload)


class _FakeContext:
    def __init__(self, cdp: _FakeCDP) -> None:
        self._cdp = cdp

    def new_cdp_session(self, _page: Any) -> _FakeCDP:
        return self._cdp


def _make_capture(
    cdp: _FakeCDP,
    *,
    hints: tuple[str, ...] = ("/chat/completion",),
    har_max: int = 8 * 1024 * 1024,
    body_max: int = 4 * 1024 * 1024,
) -> RawTrafficCapture:
    return RawTrafficCapture(
        _FakeContext(cdp),
        object(),
        body_url_hints=hints,
        har_max_bytes=har_max,
        body_max_bytes=body_max,
        creator="geo-test-adapter",
    )


def _emit_request(
    cdp: _FakeCDP,
    rid: str,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    post_data: str | None = None,
    wall: float = 1_754_800_000.0,
    ts: float = 100.0,
) -> None:
    request: dict[str, Any] = {"url": url, "method": method, "headers": headers or {}}
    if post_data is not None:
        request["postData"] = post_data
    cdp.emit(
        "Network.requestWillBeSent",
        {"requestId": rid, "request": request, "timestamp": ts, "wallTime": wall},
    )


def _emit_response(
    cdp: _FakeCDP,
    rid: str,
    url: str,
    *,
    status: int = 200,
    mime: str = "text/event-stream",
    headers: dict[str, str] | None = None,
    ts: float = 100.5,
) -> None:
    cdp.emit(
        "Network.responseReceived",
        {
            "requestId": rid,
            "timestamp": ts,
            "response": {
                "url": url,
                "status": status,
                "statusText": "OK",
                "headers": headers or {},
                "mimeType": mime,
                "protocol": "h2",
            },
        },
    )


def _emit_finish(cdp: _FakeCDP, rid: str, *, ts: float = 101.0, size: int = 512) -> None:
    cdp.emit("Network.dataReceived", {"requestId": rid, "dataLength": size})
    cdp.emit(
        "Network.loadingFinished",
        {"requestId": rid, "timestamp": ts, "encodedDataLength": size},
    )


def _emit_full_completion(
    cdp: _FakeCDP,
    rid: str = "req-1",
    url: str = "https://www.doubao.com/chat/completion?aid=1&token=secret-token",
    *,
    body: str = _SSE_BODY,
) -> None:
    cdp.bodies[rid] = (body, False)
    _emit_request(
        cdp,
        rid,
        url,
        method="POST",
        headers={"Cookie": "sessionid=abc123", "Authorization": "Bearer tok", "Accept": "*/*"},
        post_data='{"prompt":"你好"}',
    )
    _emit_response(
        cdp,
        rid,
        url,
        headers={"Content-Type": "text/event-stream", "Set-Cookie": "t=1"},
    )
    _emit_finish(cdp, rid)


# ---------------------------------------------------------------------------
# RawTrafficCapture：事件序列 → 落盘形状
# ---------------------------------------------------------------------------


def test_full_sequence_har_shape_and_sse_raw(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    # 静态资源（只留 metadata）+ completion 命中（收 body）
    _emit_request(cdp, "img-1", "https://www.doubao.com/static/logo.png", ts=99.0)
    _emit_response(
        cdp, "img-1", "https://www.doubao.com/static/logo.png", mime="image/png", ts=99.2
    )
    _emit_finish(cdp, "img-1", ts=99.4)
    _emit_full_completion(cdp)

    raw_path = capture.dump_sse_raw(tmp_path, "k-a1")
    assert raw_path == tmp_path / "k-a1-sse-raw.txt"
    assert raw_path.read_text(encoding="utf-8") == _SSE_BODY  # 原文零加工

    har_path = capture.dump_har(tmp_path, "k-a1")
    har = json.loads(har_path.read_text(encoding="utf-8"))
    log = har["log"]
    assert log["version"] == "1.2"
    assert log["creator"] == {"name": "geo-test-adapter", "version": "1"}
    entries = log["entries"]
    assert [e["_requestId"] for e in entries] == ["img-1", "req-1"]  # first-seen 时序

    static, completion = entries
    # 静态资源：只留 metadata，绝不收 body
    assert static["response"]["content"] == {"mimeType": "image/png", "size": 512}
    assert static["response"]["status"] == 200
    assert static["startedDateTime"].endswith("Z")
    assert static["timings"] == {"receive": 200.0, "send": 0, "wait": 200.0}
    assert static["time"] == 400.0

    # completion 命中：content 收 {text,size}；postData 保留
    assert completion["response"]["content"]["text"] == _SSE_BODY
    assert completion["response"]["content"]["mimeType"] == "text/event-stream"
    assert completion["request"]["postData"]["text"] == '{"prompt":"你好"}'
    assert completion["request"]["method"] == "POST"


def test_redaction_at_assembly(tmp_path: Path) -> None:
    """组盘即剥：cookie/authorization/set-cookie → [REDACTED] 保形状；URL 查询串
    token 形参数值打码。"""
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_full_completion(cdp)

    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    entry = har["log"]["entries"][0]
    req_headers = {h["name"]: h["value"] for h in entry["request"]["headers"]}
    assert req_headers["Cookie"] == "[REDACTED]"
    assert req_headers["Authorization"] == "[REDACTED]"
    assert req_headers["Accept"] == "*/*"
    resp_headers = {h["name"]: h["value"] for h in entry["response"]["headers"]}
    assert resp_headers["Set-Cookie"] == "[REDACTED]"
    assert resp_headers["Content-Type"] == "text/event-stream"
    assert "token=[REDACTED]" in entry["request"]["url"]
    assert "aid=1" in entry["request"]["url"]
    # 秘密绝不出现在序列化产物里
    payload = capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8")
    assert "secret-token" not in payload
    assert "abc123" not in payload


def test_post_data_only_kept_for_completion_hit(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_request(
        cdp, "api-1", "https://www.doubao.com/api/history", method="POST", post_data='{"x":1}'
    )
    _emit_response(cdp, "api-1", "https://www.doubao.com/api/history", mime="application/json")
    _emit_finish(cdp, "api-1")
    _emit_full_completion(cdp)

    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    other, completion = har["log"]["entries"]
    assert "postData" not in other["request"]  # 非命中端点：bodySize 留形、text 不落
    assert other["request"]["bodySize"] == 7
    assert completion["request"]["postData"]["text"] == '{"prompt":"你好"}'


def test_body_max_bytes_truncates_har_content_but_not_sse_raw(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp, body_max=10)
    big_body = "data: " + "x" * 100 + "\n\n"
    _emit_full_completion(cdp, body=big_body)

    raw_path = capture.dump_sse_raw(tmp_path, "k-a1")
    assert raw_path.read_text(encoding="utf-8") == big_body  # sse_raw 原文不受此限

    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    content = har["log"]["entries"][0]["response"]["content"]
    assert content["_truncated"] is True
    assert len(content["text"].encode("utf-8")) == 10
    assert content["size"] == len(big_body.encode("utf-8"))  # size 是全量


def test_har_max_bytes_ladder_never_drops_entries(tmp_path: Path) -> None:
    """截断阶梯：丢 postData.text → body 前缀截断；entries 永不丢（极端超顶如实
    写盘+warning）。"""
    cdp = _FakeCDP()
    capture = _make_capture(cdp, har_max=200)  # 故意极小逼出整条阶梯
    _emit_full_completion(cdp, body="data: " + "x" * 2000 + "\n\n")
    _emit_request(cdp, "img-1", "https://www.doubao.com/a.png")
    _emit_response(cdp, "img-1", "https://www.doubao.com/a.png", mime="image/png")
    _emit_finish(cdp, "img-1")

    with capture_logs() as logs:
        har_path = capture.dump_har(tmp_path, "k-a1")
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = har["log"]["entries"]
    assert [e["_requestId"] for e in entries] == ["req-1", "img-1"]  # entries 不丢
    assert "postData" not in entries[0]["request"]  # 阶梯 1：postData.text 已丢
    assert entries[0]["response"]["content"]["_truncated"] is True  # 阶梯 2：前缀截断
    assert len(entries[0]["response"]["content"]["text"].encode("utf-8")) <= 1024
    assert any(entry["event"] == "raw_capture_har_over_budget" for entry in logs)


def test_no_completion_hit_sse_raw_honest_none(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_request(cdp, "img-1", "https://www.doubao.com/a.png")
    _emit_response(cdp, "img-1", "https://www.doubao.com/a.png", mime="image/png")
    _emit_finish(cdp, "img-1")

    assert capture.dump_sse_raw(tmp_path, "k-a1") is None  # 无命中：诚实缺省
    assert not (tmp_path / "k-a1-sse-raw.txt").exists()
    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    assert len(har["log"]["entries"]) == 1  # HAR 有什么算什么


def test_event_stream_without_url_hint_is_not_a_body_hit(tmp_path: Path) -> None:
    """event-stream mime 但 URL 不命中 hints（如埋点流）→ 不抓 body。"""
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    cdp.bodies["t-1"] = ("data: track\n\n", False)
    _emit_request(cdp, "t-1", "https://www.doubao.com/telemetry/stream")
    _emit_response(cdp, "t-1", "https://www.doubao.com/telemetry/stream")
    _emit_finish(cdp, "t-1")

    assert capture.dump_sse_raw(tmp_path, "k-a1") is None
    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    assert "text" not in har["log"]["entries"][0]["response"]["content"]


def test_base64_body_decoded(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    encoded = base64.b64encode(_SSE_BODY.encode()).decode()
    cdp.bodies["req-1"] = (encoded, True)
    _emit_request(cdp, "req-1", "https://www.doubao.com/chat/completion", method="POST")
    _emit_response(cdp, "req-1", "https://www.doubao.com/chat/completion")
    _emit_finish(cdp, "req-1")

    assert capture.dump_sse_raw(tmp_path, "k-a1").read_text(encoding="utf-8") == _SSE_BODY


def test_loading_failed_marks_entry(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_request(cdp, "req-1", "https://www.doubao.com/chat/completion", method="POST")
    _emit_response(cdp, "req-1", "https://www.doubao.com/chat/completion")
    cdp.emit(
        "Network.loadingFailed",
        {"requestId": "req-1", "timestamp": 101.0, "errorText": "net::ERR_ABORTED"},
    )

    # loadingFailed 不抓 body（无 loadingFinished）→ sse_raw 诚实缺省
    assert capture.dump_sse_raw(tmp_path, "k-a1") is None
    har = json.loads(capture.dump_har(tmp_path, "k-a1").read_text(encoding="utf-8"))
    entry = har["log"]["entries"][0]
    assert entry["_failed"] is True
    assert entry["_errorText"] == "net::ERR_ABORTED"


def test_zero_requests_warns_once(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    with capture_logs() as logs:
        assert capture.dump_sse_raw(tmp_path, "k-a1") is None
        capture.dump_har(tmp_path, "k-a1")
    warnings = [entry for entry in logs if entry["event"] == "raw_capture_zero_requests"]
    assert len(warnings) == 1  # 每实例最多一次
    har = json.loads((tmp_path / "k-a1-har.json").read_text(encoding="utf-8"))
    assert har["log"]["entries"] == []


def test_dump_idempotent_and_detach_best_effort(tmp_path: Path) -> None:
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_full_completion(cdp)
    first = capture.dump_har(tmp_path, "k-a1")
    assert capture.dump_har(tmp_path, "k-a1") is first  # 缓存：不重复写盘
    assert capture.dump_sse_raw(tmp_path, "k-a1") == tmp_path / "k-a1-sse-raw.txt"
    assert cdp.sent.count("Network.getResponseBody") == 1  # body 只抓一次
    capture.detach()
    capture.detach()  # best-effort 幂等
    assert cdp.detached == 2


def test_dump_raw_evidence_refs_vocab_and_write_failure(tmp_path: Path) -> None:
    """refs 形状（kind/relation/mime 词表）+ 写盘失败诚实缺省（warning 不拖垮）。"""
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_full_completion(cdp)
    refs = dump_raw_evidence_refs(
        capture, tmp_path, "k-a1", source_url="https://www.doubao.com/chat", warn_tag="test"
    )
    assert [(ref.kind, ref.relation_type, ref.mime_type) for ref in refs] == [
        ("sse_raw", "answer_sse_raw", "text/event-stream"),
        ("har", "answer_har", "application/har+json"),
    ]
    # 目录不存在 → 写盘 OSError → 诚实缺省（不出证据不 raise）
    missing = tmp_path / "no-such-dir"
    with capture_logs() as logs:
        refs = dump_raw_evidence_refs(capture, missing, "k-a2", source_url=None, warn_tag="test")
    assert refs == []
    assert any(entry["event"] == "test_sse_raw_write_failed" for entry in logs)
    assert any(entry["event"] == "test_har_write_failed" for entry in logs)


# ---------------------------------------------------------------------------
# env 开关
# ---------------------------------------------------------------------------


def test_raw_capture_env_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_RAW_CAPTURE", raising=False)
    assert raw_capture_enabled() is True  # 缺省开（用户拍板默认开）
    monkeypatch.setenv("GEO_RAW_CAPTURE", "0")
    assert raw_capture_enabled() is False
    assert maybe_raw_capture(object(), object(), body_url_hints=(), creator="t") is None
    monkeypatch.setenv("GEO_RAW_CAPTURE", "1")
    capture = maybe_raw_capture(_FakeContext(_FakeCDP()), object(), body_url_hints=(), creator="t")
    assert isinstance(capture, RawTrafficCapture)


def test_raw_capture_init_failure_degrades_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """CDP session 建不起来 → warning + None（诚实降级，绝不拖垮采集）。"""
    monkeypatch.delenv("GEO_RAW_CAPTURE", raising=False)

    class _BrokenContext:
        def new_cdp_session(self, _page: Any) -> Any:
            raise RuntimeError("cdp unavailable")

    with capture_logs() as logs:
        assert maybe_raw_capture(_BrokenContext(), object(), body_url_hints=(), creator="t") is None
    assert any(entry["event"] == "raw_capture_init_failed" for entry in logs)


def test_har_byte_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_HAR_MAX_BYTES", raising=False)
    monkeypatch.delenv("GEO_HAR_BODY_MAX_BYTES", raising=False)
    assert har_max_bytes() == 8 * 1024 * 1024
    assert har_body_max_bytes() == 4 * 1024 * 1024
    monkeypatch.setenv("GEO_HAR_MAX_BYTES", "1024")
    monkeypatch.setenv("GEO_HAR_BODY_MAX_BYTES", "512")
    assert har_max_bytes() == 1024
    assert har_body_max_bytes() == 512
    monkeypatch.setenv("GEO_HAR_MAX_BYTES", "junk")  # 畸形 → 缺省
    assert har_max_bytes() == 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# DLP round-trip（组盘主动剥 + dlp.py 双保险）
# ---------------------------------------------------------------------------


def test_dlp_roundtrip_assembled_har_has_no_secret_bytes(tmp_path: Path) -> None:
    """我方组装的 HAR（组盘已剥成 [REDACTED] 占位）过 redact_bytes：秘密字节
    绝不出现。注意 dlp.py 的 JSON-aware 词表会把 {name,value} 形占位再标一次
    （值重写为 [REDACTED:cookie] 并记 findings）——双保险重复标记无害。"""
    cdp = _FakeCDP()
    capture = _make_capture(cdp)
    _emit_full_completion(cdp)
    payload = capture.dump_har(tmp_path, "k-a1").read_bytes()

    result = redact_bytes(payload, mime_type="application/har+json")
    assert b"abc123" not in result.redacted
    assert b"secret-token" not in result.redacted
    assert b"[REDACTED" in result.redacted  # 占位仍在（保形状）


def test_dlp_double_insurance_kills_unredacted_har_headers() -> None:
    """万一组装漏剥，dlp.py 的 JSON-aware 词表能再杀 {name,value} 形秘密。"""
    payload = json.dumps(
        {
            "log": {
                "entries": [
                    {
                        "request": {
                            "headers": [
                                {"name": "cookie", "value": "sid=topsecret"},
                                {"name": "authorization", "value": "Bearer topsecret"},
                            ]
                        },
                        "response": {"headers": [{"name": "set-cookie", "value": "t=topsecret"}]},
                    }
                ]
            }
        }
    ).encode()
    result = redact_bytes(payload, mime_type="application/har+json")
    assert b"topsecret" not in result.redacted
    assert "cookie" in result.findings
    assert "authorization" in result.findings


def test_dlp_sse_raw_regex_only() -> None:
    """text/event-stream 只过正则：Bearer/Basic 形授权串被杀，业务原文不动。"""
    payload = b"data: Authorization: Bearer topsecret123\n\ndata: hello\n\n"
    result = redact_bytes(payload, mime_type="text/event-stream")
    assert b"topsecret123" not in result.redacted
    assert b"data: hello" in result.redacted
    assert "authorization" in result.findings


# ---------------------------------------------------------------------------
# collection 词表
# ---------------------------------------------------------------------------


def test_evidence_vocab_accepts_raw_kinds(tmp_path: Path) -> None:
    har = tmp_path / "k-a1-har.json"
    har.write_text("{}", encoding="utf-8")
    raw = tmp_path / "k-a1-sse-raw.txt"
    raw.write_text("data: x\n\n", encoding="utf-8")
    result = CollectionBatchItemResult(
        business_key="k",
        evidence=[
            CollectionEvidenceRef(
                kind="har",
                path=str(har),
                relation_type="answer_har",
                mime_type="application/har+json",
            ),
            CollectionEvidenceRef(
                kind="sse_raw",
                path=str(raw),
                relation_type="answer_sse_raw",
                mime_type="text/event-stream",
            ),
        ],
    )
    refs = _normalize_evidence_refs(result)
    assert [(ref.kind, ref.relation_type) for ref in refs] == [
        ("har", "answer_har"),
        ("sse_raw", "answer_sse_raw"),
    ]


def test_evidence_vocab_rejects_unknown_kind(tmp_path: Path) -> None:
    bad = tmp_path / "k-a1-x.txt"
    bad.write_text("x", encoding="utf-8")
    result = CollectionBatchItemResult(
        business_key="k",
        evidence=[
            CollectionEvidenceRef(
                kind="raw_blob",  # 词表外 → 400 语义（ValueError → collection_result_invalid）
                path=str(bad),
                relation_type="answer_har",
                mime_type="text/plain",
            )
        ],
    )
    with pytest.raises(ValueError, match="kind is invalid"):
        _normalize_evidence_refs(result)


def test_evidence_mime_inference_for_raw_suffixes(tmp_path: Path) -> None:
    """adapter 未显式给 mime 时按自有后缀推断权威 mime（mimetypes 猜不出来）。"""
    har = tmp_path / "k-a1-har.json"
    har.write_text("{}", encoding="utf-8")
    raw = tmp_path / "k-a1-sse-raw.txt"
    raw.write_text("x", encoding="utf-8")
    result = CollectionBatchItemResult(
        business_key="k",
        evidence=[
            CollectionEvidenceRef(
                kind="har", path=str(har), relation_type="answer_har", mime_type=""
            ),
            CollectionEvidenceRef(
                kind="sse_raw", path=str(raw), relation_type="answer_sse_raw", mime_type=""
            ),
        ],
    )
    refs = _normalize_evidence_refs(result)
    assert refs[0].mime_type == "application/har+json"
    assert refs[1].mime_type == "text/event-stream"


# ---------------------------------------------------------------------------
# 失败题持久化（fake DB/CAS seam；绝不起真 PG/MinIO）
# ---------------------------------------------------------------------------


class _FakeObjectStore:
    def __init__(self) -> None:
        self.puts: list[tuple[bytes, str]] = []

    def ensure_bucket(self) -> None:
        pass

    def put_redacted(self, payload: bytes, *, mime_type: str) -> Any:
        self.puts.append((payload, mime_type))
        digest = sha256(payload).hexdigest()
        return SimpleNamespace(
            sha256=digest,
            key=f"cas/{digest}",
            mime_type=mime_type,
            byte_size=len(payload),
            dlp_findings=(),
        )


class _FakeMappings:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _FakeMappings:
        return self

    def one(self) -> dict[str, Any]:
        return self._row


class _FakeDb:
    def __init__(self) -> None:
        self.run = SimpleNamespace(
            pub_id="run_x",
            id=1,
            tenant_id=1,
            project_id=2,
            completed_tasks=0,
            failed_tasks=0,
            total_tasks=1,
            state="running",
        )
        self.project = SimpleNamespace(pub_id="prj_x")
        self.task: Any = None
        self.asset_inserts: list[dict[str, Any]] = []
        self.relation_inserts: list[dict[str, Any]] = []
        self.commits = 0


class _FakeSession:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def scalar(self, stmt: Any) -> Any:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is CollectionRun:
            return self._db.run
        if entity is CollectionTask:
            return self._db.task
        raise AssertionError(f"unexpected scalar: {entity}")

    def get(self, model: Any, _key: Any) -> Any:
        assert model is Project
        return self._db.project

    def add(self, obj: Any) -> None:
        assert isinstance(obj, CollectionTask)
        self._db.task = obj

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if "INSERT INTO evidence.evidence_asset" in sql:
            self._db.asset_inserts.append(dict(params or {}))
            return None
        if "FROM evidence.evidence_asset" in sql:
            inserted = self._db.asset_inserts[-1]
            keys = (
                "tenant_pub_id",
                "project_pub_id",
                "kind",
                "sha256",
                "object_key",
                "mime_type",
                "byte_size",
                "source_url",
                "adapter_version",
                "image_width",
                "image_height",
                "customer_visible",
            )
            return _FakeMappings({key: inserted[key] for key in keys})
        if "INSERT INTO evidence.evidence_relation" in sql:
            self._db.relation_inserts.append(dict(params or {}))
            return None
        raise AssertionError(f"unexpected execute: {sql[:80]}")

    def commit(self) -> None:
        self._db.commits += 1


def _wire_failure_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_FakeDb, _FakeObjectStore]:
    db = _FakeDb()
    store = _FakeObjectStore()
    monkeypatch.setattr(collection, "WorkerSessionLocal", lambda: _FakeSession(db))
    monkeypatch.setattr(
        collection, "TenantRepository", lambda session, tenant_pub_id: SimpleNamespace()
    )
    monkeypatch.setattr(collection, "ContentAddressedObjectStore", lambda **kw: store)
    return db, store


def _failure_result(tmp_path: Path) -> CollectionBatchItemResult:
    raw = tmp_path / "k-a1-sse-raw.txt"
    raw.write_text(_SSE_BODY, encoding="utf-8")
    har = tmp_path / "k-a1-har.json"
    har.write_text('{"log":{"entries":[]}}', encoding="utf-8")
    return CollectionBatchItemResult(
        business_key="bk-1",
        status="wall",
        error_type="wall_send",
        error_message="send-not-accepted",
        evidence=[
            CollectionEvidenceRef(
                kind="sse_raw",
                path=str(raw),
                relation_type="answer_sse_raw",
                mime_type="text/event-stream",
            ),
            CollectionEvidenceRef(
                kind="har",
                path=str(har),
                relation_type="answer_har",
                mime_type="application/har+json",
            ),
        ],
    )


def test_persist_collection_failure_persists_raw_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败题：result.evidence 的 raw/HAR ref 进 CAS + 登记 evidence_asset/
    relation；evidence_json 列保持 failure_record 原样（无漂移）。"""
    db, store = _wire_failure_seam(monkeypatch)
    result = _failure_result(tmp_path)

    collection._persist_collection_failure("tnt_x", "run_x", result, None, "wall")

    task = db.task
    assert task is not None and task.state == "failed"
    assert task.quality_state == "wall_send"
    assert json.loads(task.evidence_json)[0]["kind"] == "failure_record"
    assert db.run.failed_tasks == 1
    assert db.run.state == "completed_with_failures"

    assert [(item["kind"], item["mime_type"]) for item in db.asset_inserts] == [
        ("sse_raw", "text/event-stream"),
        ("har", "application/har+json"),
    ]
    assert all(item["project_pub_id"] == "prj_x" for item in db.asset_inserts)
    assert all(item["customer_visible"] is False for item in db.asset_inserts)
    assert [(item["relation_type"]) for item in db.relation_inserts] == [
        "answer_sse_raw",
        "answer_har",
    ]
    assert all(item["from_pub_id"] == task.pub_id for item in db.relation_inserts)
    # CAS 收到原文字节（sse_raw 原文零加工）
    assert store.puts[0][0] == _SSE_BODY.encode()
    assert store.puts[0][1] == "text/event-stream"
    assert db.commits == 1


def test_persist_collection_failure_retry_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试幂等：同 payload 重放无 drift、failed_tasks 不重复增量、证据 pub_id
    派生稳定（真实 PG 上 ON CONFLICT DO NOTHING 不产生重复行）。"""
    db, _store = _wire_failure_seam(monkeypatch)
    result = _failure_result(tmp_path)

    collection._persist_collection_failure("tnt_x", "run_x", result, None, "wall")
    collection._persist_collection_failure("tnt_x", "run_x", result, None, "wall")

    assert db.run.failed_tasks == 1  # 第二次走 prior 分支，不重复计数
    first_call_pub_ids = {item["pub_id"] for item in db.asset_inserts[:2]}
    second_call_pub_ids = {item["pub_id"] for item in db.asset_inserts[2:]}
    assert first_call_pub_ids == second_call_pub_ids  # 派生稳定 → ON CONFLICT 幂等
    assert len(db.relation_inserts) == 4
    assert db.commits == 2


def test_persist_collection_failure_without_evidence_keeps_prior_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无证据失败题（现状主流）：零 asset/relation 写入，行为与改动前一致。"""
    db, store = _wire_failure_seam(monkeypatch)
    result = CollectionBatchItemResult(
        business_key="bk-1",
        status="incomplete",
        error_type="answer_capture_incomplete",
        error_message="stream-open-at-timeout",
    )

    collection._persist_collection_failure("tnt_x", "run_x", result, None, "incomplete")

    assert db.task is not None and db.task.state == "failed"
    assert db.asset_inserts == []
    assert db.relation_inserts == []
    assert store.puts == []
    assert db.commits == 1
