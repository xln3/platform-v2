#!/usr/bin/env python3
"""派生层 mojibake 修复（2026-09-03）：collection_task / analytics.answer /
answer_source_occurrence 中经 win1252 误读污染的文本字段。

来源（全量签名扫描实证，fix-20260903-1655.md §7）：
- platform.collection_task.citations_json 20 行（元宝 15=0831 backfill 批次+1 行
  0902，豆包 5=live 期引用解析经过 CDP 文本副本）；其中 2 个豆包任务连
  answer_text/response_* 都带乱码段。
- analytics.answer 同 2 个答案的 response_text/raw/markdown/ast/html/plain。
- platform.answer_source_occurrence 的 raw_url/title/summary（backfill 派生）。

修复语义（与证据层同一逆变换，字段/段粒度）：
- citations_json / occurrence 的字符串字段：整体严格逆还原（这些字符串全程经过
  win1252 通道）；严格失败走分段保底（无效字节发可逆 win1252 字符，字节级 1:1
  断言，见 repair_win1252_mojibake_partial_20260903._partial_repair）。
- answer_text 等混合文本：**段级修复**——maximal run（全部字符在逆映射表内
  且带乱码特征）才修；run 内严格解码失败的段退化为逐字节恒等保留（合法拼音
  等假阳性经此路径恒等不变，审计计数）。
- response_hash = sha256(response_markdown_normalized) 重算（口径=
  domain/collection/answer_content.project_answer_content）。
- 不重跑 project_answer_content（避免投影代码漂移改写无关节面）；逐字段段级
  修复保持各投影字段原位一致。

审计与回滚：所有改动行的改动列**改前全值**写入
developlog/implementation/assets-mojibake-repair-20260903/derived-rollback-<batch>.jsonl；
每处段替换的 old→new 摘入 derived-audit-<batch>.json（dry-run 也产出）。

用法：source 生产 env（GEO_POSTGRES_DSN）后
  python tools/repair_derived_mojibake_20260903.py          # dry-run
  python tools/repair_derived_mojibake_20260903.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
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

from tools.repair_win1252_mojibake_20260903 import (  # noqa: E402
    _WIN1252_INVERSE,
    _has_signature,
    _repair_text,
)
from tools.repair_win1252_mojibake_partial_20260903 import _partial_repair  # noqa: E402

_ALGO = "win1252-inverse-utf8-field-segment-v1"
_AUDIT_DIR = _ROOT.parent / "developlog" / "implementation" / "assets-mojibake-repair-20260903"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _repair_whole(text: str, stats: dict[str, int]) -> str:
    """整串：严格 → 段级修复（混合串中的真实 CJK 不参与逆映射）。"""
    if not _has_signature(text):
        return text
    fixed = _repair_text(text)
    if fixed is not None:
        stats["whole_strict"] = stats.get("whole_strict", 0) + 1
        return fixed
    return _repair_segments(text, stats)


def _repair_segments(text: str, stats: dict[str, int]) -> str:
    """混合文本段级修复：只动「全在逆映射表内且带签名」的 maximal run。"""
    if not _has_signature(text):
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] in _WIN1252_INVERSE:
            j = i
            while j < n and text[j] in _WIN1252_INVERSE:
                j += 1
            run = text[i:j]
            if _has_signature(run):
                fixed = _repair_text(run)
                if fixed is not None:
                    stats["segment_strict"] = stats.get("segment_strict", 0) + 1
                else:
                    fixed, _detail = _partial_repair(run)
                    stats["segment_partial"] = stats.get("segment_partial", 0) + 1
                out.append(fixed)
            else:
                out.append(run)
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _walk_strings(obj: Any, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    batch_id = f"derived-mojibake-20260903-{uuid.uuid4().hex[:8]}"
    dsn = os.environ["GEO_POSTGRES_DSN"].replace("postgresql+psycopg://", "postgresql://", 1)
    conn = psycopg.connect(dsn, autocommit=True)

    rollback: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    def bump(key: str) -> None:
        stats[key] = stats.get(key, 0) + 1

    def _ws(text: str) -> str:
        before = text
        local: dict[str, int] = {"whole_strict": 0, "whole_partial": 0}
        after = _repair_whole(text, local)
        for k, v in local.items():
            stats[k] = stats.get(k, 0) + v
        if after != before:
            audit.append({"kind": "whole", "before_len": len(before), "after_len": len(after),
                          "before_sha": _sha(before), "after_sha": _sha(after)})
        return after

    def _seg(text: str) -> str:
        before = text
        local: dict[str, int] = {}
        after = _repair_segments(text, local)
        for k, v in local.items():
            stats[k] = stats.get(k, 0) + v
        if after != before:
            audit.append({"kind": "segment", "before_len": len(before), "after_len": len(after),
                          "before_sha": _sha(before), "after_sha": _sha(after)})
        return after

    # ---------------- platform.collection_task ----------------
    task_rows = conn.execute(
        "SELECT pub_id, answer_text, citations_json, response_markdown_normalized, "
        "response_ast_json, response_html_sanitized, response_plain_text, response_hash "
        "FROM platform.collection_task"
    ).fetchall()
    for (pub_id, answer_text, citations_json, md, ast_json, html, plain, old_hash) in task_rows:
        updates: dict[str, str] = {}
        if citations_json and _has_signature(citations_json):
            repaired_citations = _dumps(_walk_strings(json.loads(citations_json), _ws))
            if repaired_citations != citations_json:
                updates["citations_json"] = repaired_citations
        text_fields = {
            "answer_text": answer_text,
            "response_markdown_normalized": md,
            "response_html_sanitized": html,
            "response_plain_text": plain,
        }
        if any(v and _has_signature(v) for v in text_fields.values()):
            for column, value in text_fields.items():
                if value and _has_signature(value):
                    updates[column] = _seg(value)
            if ast_json and _has_signature(ast_json):
                updates["response_ast_json"] = _dumps(_walk_strings(json.loads(ast_json), _seg))
            new_md = updates.get("response_markdown_normalized", md)
            if new_md is not None:
                new_hash = hashlib.sha256(new_md.encode("utf-8")).hexdigest()
                if new_hash != old_hash:
                    updates["response_hash"] = new_hash
        if not updates:
            continue
        rollback.append({
            "table": "platform.collection_task", "pub_id": pub_id,
            "before": {c: (answer_text if c == "answer_text" else
                           citations_json if c == "citations_json" else
                           md if c == "response_markdown_normalized" else
                           ast_json if c == "response_ast_json" else
                           html if c == "response_html_sanitized" else
                           plain if c == "response_plain_text" else
                           old_hash)
                       for c in updates},
        })
        if args.apply:
            with conn.transaction():
                for column, value in updates.items():
                    conn.execute(
                        f"UPDATE platform.collection_task SET {column}=%s "
                        f"WHERE pub_id=%s AND {column} IS NOT DISTINCT FROM %s",
                        (value, pub_id, rollback[-1]["before"][column]),
                    )
        bump("collection_task_rows")

    # ---------------- analytics.answer ----------------
    answer_rows = conn.execute(
        "SELECT pub_id, response_text, response_raw, response_markdown_normalized, "
        "response_ast::text, response_html_sanitized, response_plain_text, response_hash "
        "FROM analytics.answer"
    ).fetchall()
    for (pub_id, rtext, rraw, rmd, rast, rhtml, rplain, rhash) in answer_rows:
        fields = {
            "response_text": rtext,
            "response_raw": rraw,
            "response_markdown_normalized": rmd,
            "response_html_sanitized": rhtml,
            "response_plain_text": rplain,
        }
        if not any(v and _has_signature(v) for v in fields.values()):
            continue
        updates = {}
        for column, value in fields.items():
            if value and _has_signature(value):
                updates[column] = _seg(value)
        if rast and _has_signature(rast):
            updates["response_ast"] = _dumps(_walk_strings(json.loads(rast), _seg))
        new_md = updates.get("response_markdown_normalized", rmd)
        if new_md is not None:
            new_hash = hashlib.sha256(new_md.encode("utf-8")).hexdigest()
            if new_hash != rhash:
                updates["response_hash"] = new_hash
        before = {c: (rtext if c == "response_text" else
                      rraw if c == "response_raw" else
                      rmd if c == "response_markdown_normalized" else
                      json.loads(rast) if c == "response_ast" else
                      rhtml if c == "response_html_sanitized" else
                      rplain if c == "response_plain_text" else
                      rhash)
                  for c in updates}
        rollback.append({"table": "analytics.answer", "pub_id": pub_id, "before": before})
        if args.apply:
            with conn.transaction():
                for column, value in updates.items():
                    old_value = rast if column == "response_ast" else before[column]
                    conn.execute(
                        f"UPDATE analytics.answer SET {column}=%s "
                        f"WHERE pub_id=%s AND {column} IS NOT DISTINCT FROM %s",
                        (value, pub_id, old_value),
                    )
        bump("analytics_answer_rows")

    # ---------------- platform.answer_source_occurrence ----------------
    occ_rows = conn.execute(
        "SELECT pub_id, raw_url, title, summary FROM platform.answer_source_occurrence "
        "WHERE raw_url IS NOT NULL OR title IS NOT NULL OR summary IS NOT NULL"
    ).fetchall()
    for (pub_id, raw_url, title, summary) in occ_rows:
        fields = {"raw_url": raw_url, "title": title, "summary": summary}
        if not any(v and _has_signature(v) for v in fields.values()):
            continue
        updates = {c: _ws(v) for c, v in fields.items() if v and _has_signature(v)}
        updates = {c: v for c, v in updates.items() if v != fields[c]}
        if not updates:
            continue
        rollback.append({"table": "platform.answer_source_occurrence", "pub_id": pub_id,
                         "before": {c: fields[c] for c in updates}})
        if args.apply:
            with conn.transaction():
                for column, value in updates.items():
                    conn.execute(
                        f"UPDATE platform.answer_source_occurrence SET {column}=%s "
                        f"WHERE pub_id=%s AND {column} IS NOT DISTINCT FROM %s",
                        (value, pub_id, fields[column]),
                    )
        bump("answer_source_occurrence_rows")

    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    mode = "apply" if args.apply else "dry-run"
    if rollback:
        rb = _AUDIT_DIR / f"derived-rollback-{batch_id}.jsonl"
        rb.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rollback),
            encoding="utf-8",
        )
    report = {
        "batch_id": batch_id,
        "mode": mode,
        "algorithm": _ALGO,
        "finished_at": datetime.now(UTC).isoformat(),
        "stats": stats,
        "audit_entries": len(audit),
        "audit": audit,
    }
    out = _AUDIT_DIR / f"derived-audit-{batch_id}-{mode}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"rows: {json.dumps({k: v for k, v in stats.items() if k.endswith('_rows')})}")
    print(f"rollback_rows={len(rollback)} report={out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
