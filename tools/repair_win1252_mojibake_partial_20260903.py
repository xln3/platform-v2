#!/usr/bin/env python3
"""未决 10 资产的分段级（partial）逆还原修复（2026-09-03）。

整文件严格修复（repair_win1252_mojibake_20260903.py）fail-closed 跳过的 10 个
资产，诊断分三桶（诊断过程见 fix-20260903-1655.md §6 与本报文审计）：

1. 混合编码（豆包 ×6）：引文段是上游已双重乱码的字节（源站自身乱码），严格
   UTF-8 不可能成功。策略=有效 UTF-8 段严格还原；无效字节逐字节发 win1252
   字符（INV 逆映射的可逆表示，绝不猜译——猜译=合成，违 INV-32）。
2. 网线原生 U+FFFD（豆包 ×2）：服务端截断引文标题时自己丢了字节（EF BF BD
   本就在 wire bytes 里）。严格解码成功即 1:1 成立，FFFD 计数入审计。
3. 残余签名误报（元宝 ×2）：合法拼音排版「í·」撞特征正则。85KB 全文逆映射
   +严格解码成功即 win1252 来源的铁证；签名命中上下文入审计。

字节级 1:1 证明方式（逐段构造）：输出 = 严格解码段（utf-8 编码 == 原字节）与
win1252 保留段（INV[char] == 原字节）的拼接；脚本内置逐段断言，任何一段不
满足即整体失败不落盘。

只处理 --allowlist 给定的旧 sha256（缺省读本仓库最新 apply 报告的 undecided
清单），且 DB 当前 sha256 仍等于旧值才动（幂等，重跑自动跳过已修复）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "api"))
sys.path.insert(0, str(_ROOT))

import psycopg  # noqa: E402
from geo_platform.evidence.object_store import ContentAddressedObjectStore  # noqa: E402

from tools.repair_win1252_mojibake_20260903 import (  # noqa: E402
    _C1_RE,
    _HAR_DIFF_ALLOWED,
    _TYPICAL_RE,
    _WIN1252_INVERSE,
    _diff_paths,
    _encode_har,
    _has_signature,
)

_ALGO_VERSION = "win1252-inverse-utf8-partial-v1"
_ROOT_CAUSE = (
    "chromium win1252 misdecode + upstream mixed-encoding segment / "
    "wire-native U+FFFD / legitimate typography false-positive; see "
    "fix-20260903-1655.md bucket analysis"
)
_AUDIT_DIR = _ROOT.parent / "developlog" / "implementation" / "assets-mojibake-repair-20260903"


def _partial_repair(text: str) -> tuple[str, dict[str, Any]]:
    """分段逆还原。返回 (修复文本, 审计明细)。任何一段无法 1:1 对应即抛错。"""
    raw = bytes(_WIN1252_INVERSE[c] for c in text)  # KeyError → 调用方拒收
    out: list[str] = []
    invalid_spans: list[dict[str, Any]] = []
    pos = 0
    while pos < len(raw):
        try:
            # 单字节试探不如直接逐段：先试解剩余全部，按错误位置切段。
            out.append(raw[pos:].decode("utf-8"))
            pos = len(raw)
        except UnicodeDecodeError as exc:
            fail_at = pos + exc.start
            if fail_at > pos:
                out.append(raw[pos:fail_at].decode("utf-8"))
            # 无效字节逐字节发 win1252 可逆字符（一次一个，保证可证）。
            bad = raw[fail_at]
            out.append(chr(bad) if bad in (0x81, 0x8D, 0x8F, 0x90, 0x9D)
                       else bytes([bad]).decode("cp1252"))
            invalid_spans.append({"byte_offset": fail_at, "byte": f"0x{bad:02x}"})
            pos = fail_at + 1
    repaired = "".join(out)
    # 字节级 1:1 全量验证：干净段 strict decode ⇒ encode==原段字节；无效字节段
    # 发的是 win1252 可逆字符 ⇒ 逆映射==原字节。按 invalid_spans 把 repaired
    # 逐段切回字节，拼接必须恒等于 raw，任何一段不符即断言失败（不落盘）。
    rebuilt_bytes = bytearray()
    cursor_text = 0
    cursor_raw = 0
    for off in [s["byte_offset"] for s in invalid_spans]:
        seg_text_len = len(raw[cursor_raw:off].decode("utf-8"))
        seg_text = repaired[cursor_text : cursor_text + seg_text_len]
        assert seg_text.encode("utf-8") == raw[cursor_raw:off], "clean span mismatch"
        rebuilt_bytes += seg_text.encode("utf-8")
        kept_char = repaired[cursor_text + seg_text_len]
        assert _WIN1252_INVERSE[kept_char] == raw[off], "kept byte mismatch"
        rebuilt_bytes.append(raw[off])
        cursor_text += seg_text_len + 1
        cursor_raw = off + 1
    tail = repaired[cursor_text:]
    assert tail.encode("utf-8") == raw[cursor_raw:], "tail span mismatch"
    rebuilt_bytes += tail.encode("utf-8")
    assert bytes(rebuilt_bytes) == raw, "byte-exactness proof failed"
    detail: dict[str, Any] = {
        "invalid_bytes_kept": len(invalid_spans),
        "invalid_spans": invalid_spans[:20],
        "fffd_count": repaired.count("\ufffd"),
    }
    residual = [m.start() for m in _C1_RE.finditer(repaired)] + [
        m.start() for m in _TYPICAL_RE.finditer(repaired)
    ]
    if residual:
        detail["residual_signature_contexts"] = [
            repaired[max(0, s - 40) : s + 40] for s in residual[:5]
        ]
    return repaired, detail


def _process(kind: str, payload: bytes) -> tuple[bytes | None, dict[str, Any] | None]:
    text = payload.decode("utf-8")
    if kind == "sse_raw":
        repaired, detail = _partial_repair(text)
        return repaired.encode("utf-8"), detail
    if kind == "har":
        har = json.loads(text)
        details: list[dict[str, Any]] = []
        for index, entry in enumerate(har.get("log", {}).get("entries", [])):
            content = (entry.get("response") or {}).get("content") or {}
            body = content.get("text")
            if isinstance(body, str) and _has_signature(body):
                fixed, d = _partial_repair(body)
                content["text"] = fixed
                content["size"] = len(fixed.encode("utf-8"))
                details.append({"entry_index": index, **d})
        if not details:
            return None, {"reason": "no_entry_repaired"}
        new_payload = _encode_har(har)
        diffs: set[str] = set()
        _diff_paths(json.loads(payload), json.loads(new_payload), "", diffs)
        bad = {d for d in diffs if not _HAR_DIFF_ALLOWED.match(d.lstrip("."))}
        if bad:
            return None, {"reason": "har_diff_outside_content", "paths": sorted(bad)[:5]}
        return new_payload, {"entries": details}
    return None, {"reason": f"unexpected_kind:{kind}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实跑（缺省 dry-run）")
    args = parser.parse_args()
    batch_id = f"mojibake-partial-20260903-{uuid.uuid4().hex[:8]}"

    store = ContentAddressedObjectStore(
        endpoint=os.environ["GEO_MINIO_ENDPOINT"],
        access_key=os.environ["GEO_MINIO_ACCESS_KEY"],
        secret_key=os.environ["GEO_MINIO_SECRET_KEY"],
    )
    dsn = os.environ["GEO_POSTGRES_DSN"].replace("postgresql+psycopg://", "postgresql://", 1)
    conn = psycopg.connect(dsn, autocommit=True)

    # 未决清单=最新两份 apply 报告 undecided 的并集（按旧 sha 去重）。
    reports = sorted(glob.glob(str(_AUDIT_DIR / "*-apply.json")))
    undecided: dict[str, dict[str, Any]] = {}
    for path in reports:
        for item in json.loads(Path(path).read_text(encoding="utf-8"))["undecided"]:
            if item.get("reason", "").startswith("object_fetch_failed"):
                continue  # 瞬时超时的已在第三轮修复，不是真未决
            undecided[item["sha256"]] = item
    print(f"undecided allowlist: {len(undecided)}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    for old_sha, item in undecided.items():
        pub_ids = item["pub_ids"]
        rows = conn.execute(
            "SELECT pub_id, tenant_pub_id, kind, sha256, object_key, mime_type "
            "FROM evidence.evidence_asset WHERE pub_id = ANY(%s)",
            (pub_ids,),
        ).fetchall()
        if not rows or any(r[3] != old_sha for r in rows):
            results.append(
                {
                    "pub_ids": pub_ids,
                    "old_sha256": old_sha,
                    "status": "skipped_already_repaired",
                }
            )
            continue
        kind = rows[0][2]
        object_key = rows[0][4]
        payload = store.get_verified(object_key, old_sha)
        try:
            new_payload, detail = _process(kind, payload)
        except KeyError as exc:
            results.append(
                {
                    "pub_ids": pub_ids,
                    "old_sha256": old_sha,
                    "status": "failed_keyerror",
                    "char": repr(exc),
                }
            )
            continue
        if new_payload is None:
            results.append(
                {"pub_ids": pub_ids, "old_sha256": old_sha, "status": "failed", "detail": detail}
            )
            continue
        entry: dict[str, Any] = {
            "pub_ids": pub_ids,
            "kind": kind,
            "old_sha256": old_sha,
            "old_byte_size": len(payload),
            "new_byte_size": len(new_payload),
            "detail": detail,
        }
        if not args.apply:
            entry["status"] = "would_repair"
            results.append(entry)
            continue
        stored = store.put_redacted(new_payload, mime_type=rows[0][5])
        final = store.get_verified(stored.key, stored.sha256)
        assert final == new_payload or len(final) == stored.byte_size
        with conn.transaction():
            referencing = conn.execute(
                "SELECT count(*) FROM evidence.evidence_asset WHERE object_key=%s AND sha256=%s",
                (object_key, old_sha),
            ).fetchone()[0]
            updated = conn.execute(
                "UPDATE evidence.evidence_asset SET sha256=%s, object_key=%s,"
                " byte_size=%s, dlp_findings=%s "
                "WHERE object_key=%s AND sha256=%s",
                (
                    stored.sha256,
                    stored.key,
                    stored.byte_size,
                    list(stored.dlp_findings),
                    object_key,
                    old_sha,
                ),
            ).rowcount
            if updated != referencing:
                raise RuntimeError(f"rowcount {updated} != referencing {referencing}")
            for row in rows:
                conn.execute(
                    "INSERT INTO evidence.evidence_access_audit "
                    "(tenant_pub_id, resource_pub_id, actor_pub_id,"
                    " action, outcome, request_id, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        row[1],
                        row[0],
                        "tool:repair_win1252_mojibake_20260903",
                        "repair_win1252_mojibake_partial",
                        "repaired",
                        batch_id,
                        json.dumps(
                            {
                                "old_sha256": old_sha,
                                "new_sha256": stored.sha256,
                                "old_object_key": object_key,
                                "new_object_key": stored.key,
                                "algorithm": _ALGO_VERSION,
                                "root_cause": _ROOT_CAUSE,
                                "detail": detail,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
        entry["status"] = "repaired"
        entry["new_sha256"] = stored.sha256
        results.append(entry)

    report = {
        "batch_id": batch_id,
        "mode": "apply" if args.apply else "dry-run",
        "algorithm": _ALGO_VERSION,
        "finished_at": datetime.now(UTC).isoformat(),
        "results": results,
    }
    out = _AUDIT_DIR / f"{batch_id}-{'apply' if args.apply else 'dry-run'}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print(json.dumps(r, ensure_ascii=False)[:240])
    print(f"report={out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
