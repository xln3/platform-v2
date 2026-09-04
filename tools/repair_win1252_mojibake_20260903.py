#!/usr/bin/env python3
"""存量证据资产 windows-1252 mojibake 一次性修复工具（2026-09-03）。

根因：Chromium Network.getResponseBody 对非 base64 文本响应按响应头 charset
解码，缺省 charset 时按 WHATWG windows-1252 误读 UTF-8（元宝 /api/chat/* 与
豆包 /chat/completion 的 text/event-stream 不带 charset）。增量修复已落在
workflows/activities/raw_capture.py:_restore_chromium_decoded_body（2026-09-03
随 release fll-yiyan-url-cap-20260902T1653JST 上线）。本工具只处理存量。

纪律（与任务书 clients/client-fll/sse-mojibake-repair-prompt-20260903.md 一致）：

- CAS 不可变：旧对象永不删除/覆盖；修复 = 新存修正版对象（新 sha256）
  + 原地更新 evidence.evidence_asset 行的 sha256/object_key/byte_size/
  dlp_findings（pub_id 不变，全部 FK 引用——evidence_relation /
  evidence_anchor / reporting.report_evidence_reference 等 12 处——自动保持
  有效）+ evidence.evidence_access_audit 审计行 + 本地 JSONL 审计文件。
- fail-closed：逆映射 KeyError / 严格 UTF-8 解码失败 / 修复后仍有乱码特征 /
  HAR 出现 content.text 之外的签名残留，一律跳过并列入 undecided，绝不动。
- INV-32：本变换是可证明的 1:1 字节级逆还原，不是内容生成；每个资产输出
  修复前后 sha256、字节长度、严格解码标记的验证证据。

用法（先 source 生产 env：GEO_POSTGRES_DSN / GEO_MINIO_* / GEO_ADAPTER_EVIDENCE_DIR）：

    python tools/repair_win1252_mojibake_20260903.py --dry-run   # 缺省
    python tools/repair_win1252_mojibake_20260903.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

# ---------------------------------------------------------------------------
# 逆还原算法——与 workflows/activities/raw_capture.py:150-169 的
# _WIN1252_INVERSE 逐字节同源（该模块 import 链太重，一次性工具复制 6 行，
# 修改任一处必须同步另一处）。与线上版差异：严格解码（fail-closed），
# 线上版 decode(...,"replace") 是增量路径的宽容取舍。
# ---------------------------------------------------------------------------
_WHATWG_WIN1252_C1_BYTES = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})
_WIN1252_INVERSE: dict[str, int] = {}
for _byte in range(256):
    _WIN1252_INVERSE[
        chr(_byte) if _byte in _WHATWG_WIN1252_C1_BYTES else bytes([_byte]).decode("cp1252")
    ] = _byte

_ALGO_VERSION = "win1252-inverse-utf8-strict-v1"
_ROOT_CAUSE = (
    "chromium getResponseBody default WHATWG windows-1252 decode for "
    "text/event-stream without charset (doubao /chat/completion, "
    "yuanbao /api/chat/*); ingress fixed in raw_capture.py 2026-09-03"
)

_C1_RE = re.compile(r"[\u0080-\u009f]")
# cp1252 高位拉丁（ä å æ ç è é œ 等）后紧跟续字节区字符 = UTF-8 三字节序列
# 被误读的前两节的典型形状。
_TYPICAL_RE = re.compile(
    r"[\u00c0-\u00ff\u0152\u0153\u0160\u0161\u017d\u017e\u0178]"
    r"[\u0080-\u00bf]"
)


def _has_signature(text: str) -> bool:
    return bool(_C1_RE.search(text) or _TYPICAL_RE.search(text))


def _repair_text(text: str) -> str | None:
    """严格逆还原；任何一步不符都返回 None（调用方列入 undecided，绝不动）。"""
    try:
        raw = bytes(_WIN1252_INVERSE[char] for char in text)
    except KeyError:
        return None
    try:
        repaired = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # 安全闸（任务书第 4 条）：修复结果再按 UTF-8 编码必须恒等于逆变换字节。
    assert repaired.encode("utf-8") == raw
    if "\ufffd" in repaired:
        return None
    if _has_signature(repaired):
        return None
    return repaired


# ---------------------------------------------------------------------------
# HAR 修复：只修 response.content.text（body_url_hints 命中的 completion 条目
# 才有 body——见 raw_capture._har_entry），content.size 同步为修复后 UTF-8 字节长；
# 其余一切字段原样。postData 或任何其他字段带签名 → 整个资产 undecided。
# ---------------------------------------------------------------------------

_HAR_DIFF_ALLOWED = re.compile(r"^log\.entries\[\d+\]\.response\.content\.(text|size)$")


def _diff_paths(old: Any, new: Any, path: str, out: set[str]) -> None:
    if type(old) is not type(new):
        out.add(path)
        return
    if isinstance(old, dict):
        if set(old) != set(new):
            out.add(path)
            return
        for key in old:
            _diff_paths(old[key], new[key], f"{path}.{key}", out)
    elif isinstance(old, list):
        if len(old) != len(new):
            out.add(path)
            return
        for index, (o_item, n_item) in enumerate(zip(old, new, strict=True)):
            _diff_paths(o_item, n_item, f"{path}[{index}]", out)
    elif old != new:
        out.add(path)


def _encode_har(har: dict[str, Any]) -> bytes:
    # 与 raw_capture._encode_har 完全一致的序列化，保证修复前后仅目标字段差异。
    return json.dumps(har, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def process_asset(
    kind: str, payload: bytes
) -> tuple[str, bytes | None, dict[str, Any]]:
    """返回 (status, new_payload, detail)。status ∈ clean/repairable/undecided。"""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return "undecided", None, {"reason": "payload_not_utf8"}

    if kind == "sse_raw":
        if not _has_signature(text):
            return "clean", None, {}
        repaired = _repair_text(text)
        if repaired is None:
            return "undecided", None, {"reason": "strict_inverse_failed"}
        return "repairable", repaired.encode("utf-8"), {"chars": len(text)}

    if kind == "har":
        if not _has_signature(text):
            return "clean", None, {}
        try:
            har = json.loads(text)
        except ValueError:
            return "undecided", None, {"reason": "har_not_json"}
        entries = har.get("log", {}).get("entries", [])
        repaired_entries: list[dict[str, str]] = []
        for index, entry in enumerate(entries):
            content = (entry.get("response") or {}).get("content") or {}
            body = content.get("text")
            if isinstance(body, str) and _has_signature(body):
                fixed = _repair_text(body)
                if fixed is None:
                    return "undecided", None, {
                        "reason": "entry_strict_inverse_failed",
                        "entry_index": index,
                        "entry_url": (entry.get("request") or {}).get("url", ""),
                    }
                content["text"] = fixed
                content["size"] = len(fixed.encode("utf-8"))
                repaired_entries.append(
                    {"index": str(index), "url": (entry.get("request") or {}).get("url", "")}
                )
        if not repaired_entries:
            # 签名在 content.text 之外（如 postData）——任务书范围外，不动。
            return "undecided", None, {"reason": "signature_outside_content_text"}
        new_payload = _encode_har(har)
        # 逐字段 diff 守卫：除 content.text/size 外任何差异都拒收。
        diffs: set[str] = set()
        _diff_paths(json.loads(payload), json.loads(new_payload), "", diffs)
        bad = {d for d in diffs if not _HAR_DIFF_ALLOWED.match(d.lstrip("."))}
        if bad:
            return (
                "undecided",
                None,
                {"reason": "har_diff_outside_content", "paths": sorted(bad)[:5]},
            )
        return "repairable", new_payload, {
            "entries_repaired": len(repaired_entries),
            "entries": repaired_entries[:10],
        }

    return "undecided", None, {"reason": f"unexpected_kind:{kind}"}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

_AUDIT_DIR = _ROOT.parent / "developlog" / "implementation" / "assets-mojibake-repair-20260903"


def _dsn() -> str:
    dsn = os.environ["GEO_POSTGRES_DSN"]
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _store() -> ContentAddressedObjectStore:
    return ContentAddressedObjectStore(
        endpoint=os.environ["GEO_MINIO_ENDPOINT"],
        access_key=os.environ["GEO_MINIO_ACCESS_KEY"],
        secret_key=os.environ["GEO_MINIO_SECRET_KEY"],
    )


def _host_of(source_url: str | None) -> str:
    if not source_url:
        return "(none)"
    match = re.match(r"https?://([^/]+)", source_url)
    return match.group(1) if match else "(none)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实跑（缺省 dry-run）")
    args = parser.parse_args()
    batch_id = f"mojibake-repair-20260903-{uuid.uuid4().hex[:8]}"
    started = datetime.now(UTC)

    store = _store()
    conn = psycopg.connect(_dsn(), autocommit=True)
    rows = conn.execute(
        """
        SELECT pub_id, tenant_pub_id, project_pub_id, kind, sha256, object_key,
               byte_size, mime_type, source_url, capture_time
        FROM evidence.evidence_asset
        WHERE kind IN ('sse_raw','har') AND deleted_at IS NULL
        ORDER BY capture_time
        """
    ).fetchall()
    print(f"candidate rows: {len(rows)}", file=sys.stderr)

    # 同一 CAS 对象可能被多行引用（内容级去重）——按对象分组，修复一次更新全部。
    by_object: dict[str, list[Any]] = {}
    for row in rows:
        by_object.setdefault(row[5], []).append(row)
    print(f"unique objects: {len(by_object)}", file=sys.stderr)

    report: dict[str, Any] = {
        "batch_id": batch_id,
        "mode": "apply" if args.apply else "dry-run",
        "started_at": started.isoformat(),
        "algorithm": _ALGO_VERSION,
        "root_cause": _ROOT_CAUSE,
        "stats": {},
        "repairable": [],
        "undecided": [],
        "applied": [],
        "errors": [],
    }
    stats: dict[str, dict[str, int]] = {}

    def _bump(group: str, status: str) -> None:
        bucket = stats.setdefault(group, {})
        bucket[status] = bucket.get(status, 0) + 1

    # 本地暂存副本：apply 时收集 old_sha256 -> new_bytes，最后扫盘重写。
    local_repair_map: dict[str, bytes] = {}

    for object_key, asset_rows in by_object.items():
        old_sha256 = asset_rows[0][4]
        kind = asset_rows[0][3]
        host = _host_of(asset_rows[0][8])
        group = f"{host}|{kind}"
        try:
            payload = store.get_verified(object_key, old_sha256)
        except Exception as exc:
            _bump(group, "undecided")
            report["undecided"].append(
                {
                    "pub_ids": [r[0] for r in asset_rows],
                    "kind": kind,
                    "sha256": old_sha256,
                    "reason": f"object_fetch_failed:{type(exc).__name__}",
                }
            )
            continue
        status, new_payload, detail = process_asset(kind, payload)
        _bump(group, status)
        date = str(asset_rows[0][9].date()) if asset_rows[0][9] else "?"
        if status == "repairable":
            assert new_payload is not None
            entry = {
                "pub_ids": [r[0] for r in asset_rows],
                "tenant_pub_id": asset_rows[0][1],
                "kind": kind,
                "host": host,
                "date": date,
                "old_sha256": old_sha256,
                "old_byte_size": len(payload),
                "new_byte_size": len(new_payload),
                "strict_decode_ok": True,
                "fffd_count": 0,
                **detail,
            }
            if not args.apply:
                report["repairable"].append(entry)
                continue
            try:
                stored = store.put_redacted(new_payload, mime_type=asset_rows[0][7])
                # 二次核验：取回最终落盘字节，必须判 clean（残留签名/不可解都拒收）。
                final = store.get_verified(stored.key, stored.sha256)
                final_status, _, _ = process_asset(kind, final)
                if final_status != "clean":
                    raise RuntimeError(f"stored object not clean after repair: {final_status}")
                with conn.transaction():
                    # 乐观闸：按 (object_key, sha256) 命中全部引用行（含可能的
                    # 软删行），保证修复后无任何行再指向乱码对象。
                    referencing = conn.execute(
                        "SELECT count(*) FROM evidence.evidence_asset "
                        "WHERE object_key=%s AND sha256=%s",
                        (object_key, old_sha256),
                    ).fetchone()[0]
                    updated = conn.execute(
                        """
                        UPDATE evidence.evidence_asset
                        SET sha256=%s, object_key=%s, byte_size=%s, dlp_findings=%s
                        WHERE object_key=%s AND sha256=%s
                        """,
                        (
                            stored.sha256,
                            stored.key,
                            stored.byte_size,
                            list(stored.dlp_findings),
                            object_key,
                            old_sha256,
                        ),
                    ).rowcount
                    if updated != referencing:
                        raise RuntimeError(f"rowcount {updated} != referencing {referencing}")
                    for row in asset_rows:
                        conn.execute(
                            """
                            INSERT INTO evidence.evidence_access_audit
                              (tenant_pub_id, resource_pub_id, actor_pub_id, action,
                               outcome, request_id, data)
                            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                row[1],
                                row[0],
                                "tool:repair_win1252_mojibake_20260903",
                                "repair_win1252_mojibake",
                                "repaired",
                                batch_id,
                                json.dumps(
                                    {
                                        "old_sha256": old_sha256,
                                        "new_sha256": stored.sha256,
                                        "old_object_key": object_key,
                                        "new_object_key": stored.key,
                                        "old_byte_size": len(payload),
                                        "new_byte_size": stored.byte_size,
                                        "rows_updated": updated,
                                        "algorithm": _ALGO_VERSION,
                                        "root_cause": _ROOT_CAUSE,
                                        "detail": detail,
                                    },
                                    ensure_ascii=False,
                                ),
                            ),
                        )
                local_repair_map[old_sha256] = final
                entry["new_sha256"] = stored.sha256
                entry["new_byte_size"] = stored.byte_size
                entry["dlp_findings"] = list(stored.dlp_findings)
                entry["rows_updated"] = updated
                entry["applied"] = True
                report["applied"].append(entry)
            except Exception as exc:
                report["errors"].append(
                    {
                        "pub_ids": [r[0] for r in asset_rows],
                        "sha256": old_sha256,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        elif status == "undecided":
            report["undecided"].append(
                {
                    "pub_ids": [r[0] for r in asset_rows],
                    "kind": kind,
                    "host": host,
                    "date": date,
                    "sha256": old_sha256,
                    **detail,
                }
            )

    # 本地暂存副本（worker 上传前的落盘文件；非 CAS，可原地重写，留审计）。
    if args.apply and local_repair_map:
        staging = Path(os.environ.get("GEO_ADAPTER_EVIDENCE_DIR", ""))
        local_report = []
        if staging.is_dir():
            for path in sorted(staging.iterdir()):
                if not (path.name.endswith("-sse-raw.txt") or path.name.endswith("-har.json")):
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                replacement = local_repair_map.get(digest)
                if replacement is None:
                    continue
                tmp = path.with_suffix(path.suffix + ".repair-tmp")
                tmp.write_bytes(replacement)
                os.replace(tmp, path)
                local_report.append(
                    {
                        "file": str(path),
                        "old_sha256": digest,
                        "new_sha256": hashlib.sha256(replacement).hexdigest(),
                    }
                )
        report["local_staging_repaired"] = local_report

    report["stats"] = stats
    report["finished_at"] = datetime.now(UTC).isoformat()
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "apply" if args.apply else "dry-run"
    out = _AUDIT_DIR / f"{batch_id}-{suffix}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(stats, indent=2, ensure_ascii=False))
    print(
        f"repairable={len(report['repairable'])} undecided={len(report['undecided'])} "
        f"errors={len(report['errors'])} report={out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
