#!/usr/bin/env python3
"""2026-08-13 配额墙/离题答案清污：把污染答案在 analytics 层置显式不合格。

背景：run_346MKPWNXJVC0HF7DNVZX9SKQS（doubao 136 题）等 08-12~08-13 的 run 中，
豆包配额墙文案（「今日专家模式免费次数用完了…开通豆包专业版…」）被当正常答案
（collection_task state=completed / quality_state=live_valid）扇出进 analytics.answer
且 eligible=true。INV-1 读路径 answer_agg_blind 只排显式不合格
（WHERE eligible AND NOT degraded），因此清污 = eligible 置 false：
- 不改 response_text 原文（保留证据）；
- 不动 platform.collection_task（原始采集记录如实保留）；
- 每行写 platform.audit_log（action=analytics.answer.quarantined）留审计痕迹。

用法（默认 dry-run，只打印将影响的行）：
  python tools/quarantine_wall_answers_20260813.py --run run_XXX [--run run_YYY ...]
  python tools/quarantine_wall_answers_20260813.py --run run_XXX --apply
  python tools/quarantine_wall_answers_20260813.py --run run_XXX \
      --off-topic-pub-id ans_XXX --apply

DSN 取环境变量 GEO_POSTGRES_DSN（支持 postgresql+psycopg:// 写法，自动归一化），
或 --dsn 显式传入。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from geo_platform.tenancy.ids import new_pub_id  # noqa: E402

# 配额墙（quota）：平台配额/付费墙文案，答案无测量价值。
QUOTA_PATTERNS = (
    "%专家模式免费次数用完%",
    "%开通豆包专业版%",
    "%暂时无法使用专业版%",
)
# 禁言墙（muted）：账号被平台禁言的提示文案。
MUTED_PATTERNS = (
    "%违反用户使用规范%",
    "%已被禁言%",
)
DEFAULT_WALL_PATTERNS = QUOTA_PATTERNS + MUTED_PATTERNS

AUDIT_ACTION = "analytics.answer.quarantined"
AUDIT_RESOURCE_TYPE = "answer"
TOOL_NAME = "quarantine_wall_answers_20260813"


def normalize_dsn(dsn: str) -> str:
    # SQLAlchemy 风格（postgresql+psycopg://）归一化为 libpq/psycopg 直连 URI。
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def classify(
    text: str, patterns: tuple[str, ...], off_topic_ids: frozenset[str], pub_id: str
) -> tuple[str, str | None]:
    """返回 (reason, matched_pattern)。显式离题标记优先于墙文案。"""
    if pub_id in off_topic_ids:
        return "off_topic_answer", None
    for pattern in patterns:
        needle = pattern.strip("%")
        if needle and needle in text:
            reason = "quota_wall" if pattern in QUOTA_PATTERNS else "muted_wall"
            return reason, pattern
    return "unknown", None


def fetch_candidates(
    connection: psycopg.Connection[Any],
    run_pub_ids: list[str],
    patterns: tuple[str, ...],
    off_topic_ids: frozenset[str],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if patterns:
        likes = " OR ".join(["response_text LIKE %s"] * len(patterns))
        for row in connection.execute(
            f"""
            SELECT pub_id, tenant_pub_id, project_pub_id, run_pub_id, model, region, mode,
                   eligible, degraded, left(query_text, 40) AS query_text_40,
                   left(response_text, 60) AS response_text_60, response_text, capture_time
            FROM analytics.answer
            WHERE run_pub_id = ANY(%s) AND ({likes})
            ORDER BY run_pub_id, capture_time
            """,
            (run_pub_ids, *patterns),
        ):
            rows[row["pub_id"]] = row
    if off_topic_ids:
        for row in connection.execute(
            """
            SELECT pub_id, tenant_pub_id, project_pub_id, run_pub_id, model, region, mode,
                   eligible, degraded, left(query_text, 40) AS query_text_40,
                   left(response_text, 60) AS response_text_60, response_text, capture_time
            FROM analytics.answer
            WHERE run_pub_id = ANY(%s) AND pub_id = ANY(%s)
            """,
            (run_pub_ids, sorted(off_topic_ids)),
        ):
            rows.setdefault(row["pub_id"], row)
    candidates = list(rows.values())
    for row in candidates:
        reason, matched = classify(row["response_text"], patterns, off_topic_ids, row["pub_id"])
        row["reason"] = reason
        row["matched_pattern"] = matched
    return sorted(candidates, key=lambda r: (r["run_pub_id"], r["capture_time"]))


def print_plan(candidates: list[dict[str, Any]]) -> None:
    header = f"{'answer_pub_id':36} {'run_pub_id':36} {'model':9} {'eligible':8} {'reason':16}"
    print(f"{header} 题目/答案")
    for row in candidates:
        answer_preview = row["response_text_60"].replace("\n", "⏎")
        print(
            f"{row['pub_id']:36} {row['run_pub_id']:36} {row['model']:9} "
            f"{str(row['eligible']):8} {row['reason']:16} "
            f"{row['query_text_40']} | {answer_preview}"
        )
    total = len(candidates)
    already = sum(1 for r in candidates if not r["eligible"])
    print(f"-- 共 {total} 行命中（已不合格 {already} 行，待置 {total - already} 行）")


def apply_quarantine(
    connection: psycopg.Connection[Any],
    candidates: list[dict[str, Any]],
    *,
    actor_pub_id: str,
) -> tuple[int, int]:
    updated = 0
    audited = 0
    tenant_ids: dict[str, str] = {}
    for row in candidates:
        tenant_pub_id = row["tenant_pub_id"]
        if tenant_pub_id not in tenant_ids:
            tenant = connection.execute(
                "SELECT id AS tenant_id FROM platform.tenant WHERE pub_id=%s",
                (tenant_pub_id,),
            ).fetchone()
            if tenant is None:
                raise RuntimeError(f"tenant_not_found:{tenant_pub_id}")
            tenant_ids[tenant_pub_id] = str(tenant["tenant_id"])
        result = connection.execute(
            """
            UPDATE analytics.answer SET eligible=false
            WHERE tenant_pub_id=%s AND pub_id=%s AND eligible=true
            """,
            (tenant_pub_id, row["pub_id"]),
        )
        updated += result.rowcount
        receipt = {
            "tool": TOOL_NAME,
            "run_pub_id": row["run_pub_id"],
            "project_pub_id": row["project_pub_id"],
            "reason": row["reason"],
            "matched_pattern": row["matched_pattern"],
            "query_text_40": row["query_text_40"],
            "capture_time": row["capture_time"].isoformat(),
            "field_changed": "analytics.answer.eligible:true->false",
            "answer_text_preserved": True,
        }
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (tenant_ids[tenant_pub_id],),
        )
        connection.execute(
            """
            INSERT INTO platform.audit_log (
              id,pub_id,tenant_id,actor_pub_id,action,resource_type,
              resource_pub_id,receipt,occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
            """,
            (
                uuid4(),
                new_pub_id("aud"),
                tenant_ids[tenant_pub_id],
                actor_pub_id,
                AUDIT_ACTION,
                AUDIT_RESOURCE_TYPE,
                row["pub_id"],
                json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        audited += 1
    return updated, audited


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--run", dest="runs", action="append", required=True, help="run_pub_id，可多次"
    )
    parser.add_argument(
        "--pattern",
        dest="patterns",
        action="append",
        default=None,
        help="SQL LIKE 墙文案模式，可多次；缺省为内置配额/禁言五模式",
    )
    parser.add_argument(
        "--off-topic-pub-id",
        dest="off_topic_ids",
        action="append",
        default=[],
        help="离题答案 pub_id（不匹配墙模式也置不合格），可多次",
    )
    parser.add_argument("--apply", action="store_true", help="实际执行（缺省 dry-run）")
    parser.add_argument("--actor", default="ops-quarantine-20260813", help="audit_log actor_pub_id")
    parser.add_argument(
        "--dsn", default=os.getenv("GEO_POSTGRES_DSN", ""), help="缺省取 env GEO_POSTGRES_DSN"
    )
    args = parser.parse_args()

    if not args.dsn:
        print("error: 未提供 DSN（--dsn 或 env GEO_POSTGRES_DSN）", file=sys.stderr)
        return 2
    patterns = tuple(args.patterns) if args.patterns else DEFAULT_WALL_PATTERNS
    off_topic_ids = frozenset(args.off_topic_ids)

    with psycopg.connect(normalize_dsn(args.dsn), row_factory=dict_row) as connection:
        candidates = fetch_candidates(connection, args.runs, patterns, off_topic_ids)
        if not candidates:
            print("未命中任何行。")
            return 0
        print_plan(candidates)
        if not args.apply:
            print("-- dry-run：未做任何修改（加 --apply 执行）。")
            return 0
        updated, audited = apply_quarantine(connection, candidates, actor_pub_id=args.actor)
        connection.commit()
        print(f"-- applied: eligible 置 false {updated} 行，audit_log 写入 {audited} 行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
