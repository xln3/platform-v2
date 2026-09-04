#!/usr/bin/env python3
"""人工补测登记 CLI：从 JSON 文件把人工实测的平台回答登记为正式 analytics.answer 行。

输入 JSON 与 ``POST /api/v2/analytics/manual-answers`` 的 body 同构：

  {
    "project_pub_id": "prj_...",
    "operator": "xln",
    "reason": "平台风控人工补测",
    "items": [
      {"model": "deepseek", "query_text": "...", "response_plain_text": "...",
       "capture_time": "2026-09-01T16:05:00+08:00", "region": "北京",
       "mode": "normal", "source_url": "https://...",
       "evidence_pub_ids": ["evd_..."], "idempotency_key": "..."}
    ]
  }

用法（默认 dry-run，只打印将插入的行形状；--apply 才实写）：

  python tools/manual_ingest_answer.py payload.json
  python tools/manual_ingest_answer.py payload.json --apply

DSN 取环境变量 GEO_POSTGRES_DSN（支持 postgresql+psycopg:// 写法，自动归一
化），或 --dsn 显式传入。tenant 由 project 反查（geo 角色可枚举）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.analytics import manual_ingestion  # noqa: E402


def normalize_dsn(dsn: str) -> str:
    # SQLAlchemy 风格（postgresql+psycopg://）归一化为 libpq/psycopg 直连 URI。
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise SystemExit("payload 必须是含 items 数组的 JSON 对象")
    for key in ("project_pub_id", "operator", "reason"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise SystemExit(f"payload 缺 {key}")
    return payload


def parse_items(payload: dict[str, Any]) -> tuple[manual_ingestion.ManualAnswerItem, ...]:
    items = []
    for index, raw in enumerate(payload["items"]):
        if not isinstance(raw, dict):
            raise SystemExit(f"items[{index}] 不是对象")
        try:
            capture_time = datetime.fromisoformat(str(raw["capture_time"]))
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"items[{index}].capture_time 非法：{exc}") from exc
        items.append(
            manual_ingestion.ManualAnswerItem(
                model=str(raw["model"]),
                query_text=str(raw["query_text"]),
                response_plain_text=str(raw["response_plain_text"]),
                capture_time=capture_time,
                region=str(raw.get("region") or "unknown"),
                mode=str(raw.get("mode") or "normal"),
                source_url=(str(raw["source_url"]) if raw.get("source_url") else None),
                evidence_pub_ids=tuple(str(v) for v in raw.get("evidence_pub_ids") or ()),
                idempotency_key=(
                    str(raw["idempotency_key"]) if raw.get("idempotency_key") else None
                ),
            )
        )
    return tuple(items)


def resolve_tenant(dsn: str, project_pub_id: str) -> str:
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            """
            SELECT tenant.pub_id FROM platform.project project
            JOIN platform.tenant tenant ON tenant.id=project.tenant_id
            WHERE project.pub_id=%s
            """,
            (project_pub_id,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"project 不存在：{project_pub_id}")
    return str(row[0])


def print_plan(
    payload: dict[str, Any],
    items: tuple[manual_ingestion.ManualAnswerItem, ...],
    *,
    tenant_pub_id: str,
    existing: set[str],
) -> None:
    print(f"tenant={tenant_pub_id} project={payload['project_pub_id']}")
    print(f"operator={payload['operator']} reason={payload['reason']}")
    header = f"{'answer_pub_id':32} {'model':10} {'region':6} {'mode':8} {'new':4} 题目/答案"
    print(header)
    for item in items:
        answer_pub_id = manual_ingestion.manual_answer_pub_id(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=payload["project_pub_id"],
            item=item,
        )
        response = item.response_plain_text
        print(
            f"{answer_pub_id:32} {item.model:10} {item.region:6} {item.mode:8} "
            f"{'否' if answer_pub_id in existing else '是':4} "
            f"query={item.query_text[:30]!r} | capture_time={item.capture_time.isoformat()} "
            f"| eligible=true channel=manual adapter={manual_ingestion.MANUAL_ADAPTER_VERSION} "
            f"| resp_len={len(response)} sha256={sha256(response.encode()).hexdigest()[:12]} "
            f"| evidence={list(item.evidence_pub_ids) or '-'}"
        )


def fetch_existing(dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]) -> set[str]:
    if not answer_pub_ids:
        return set()
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            "SELECT pub_id FROM analytics.answer WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[])",
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    return {str(row[0]) for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="登记负载 JSON 文件")
    parser.add_argument("--dsn", default=os.environ.get("GEO_POSTGRES_DSN", ""))
    parser.add_argument("--apply", action="store_true", help="实写（缺省 dry-run）")
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("缺 DSN：--dsn 或 GEO_POSTGRES_DSN")
    dsn = normalize_dsn(args.dsn)

    payload = load_payload(args.payload)
    items = parse_items(payload)
    tenant_pub_id = resolve_tenant(dsn, str(payload["project_pub_id"]))
    answer_pub_ids = [
        manual_ingestion.manual_answer_pub_id(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=str(payload["project_pub_id"]),
            item=item,
        )
        for item in items
    ]
    existing = fetch_existing(dsn, tenant_pub_id, answer_pub_ids)
    print_plan(payload, items, tenant_pub_id=tenant_pub_id, existing=existing)
    if not args.apply:
        print("dry-run：未写入。加 --apply 实写。")
        return
    registrations = manual_ingestion.register_manual_answers(
        dsn,
        tenant_pub_id=tenant_pub_id,
        project_pub_id=str(payload["project_pub_id"]),
        operator=str(payload["operator"]),
        reason=str(payload["reason"]),
        items=items,
    )
    for registration in registrations:
        print(
            f"registered answer={registration.answer_pub_id} "
            f"analysis={registration.analysis_pub_id} created={registration.created} "
            f"eligible={registration.eligible} evidence_attached={registration.evidence_attached}"
        )


if __name__ == "__main__":
    main()
