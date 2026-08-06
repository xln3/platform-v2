"""Rebuild V2 derived analytics from migrated legacy raw answers."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import psycopg
from geo_platform.analytics.service import AnalyticsService
from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.analyzer import CitationInput
from tools.migration.migrate_legacy_core import CoreMigrator, _dsn, parse_time


def rebuild(source_path: Path, *, dsn: str) -> dict[str, object]:
    migrator = CoreMigrator(source_path, dsn=dsn)
    service = AnalyticsService(dsn=dsn)
    counts = {"seen": 0, "rebuilt": 0, "failed": 0}
    with sqlite3.connect(f"file:{source_path.resolve(strict=True)}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        rows = source.execute(
            """
            SELECT a.*,w.monitoring_config_id,w.query_item_id,mc.brand_id,b.name AS brand_name,
                   b.own_domains_json
            FROM answer a
            JOIN work_item w ON w.id=a.work_item_id
            JOIN monitoring_config mc ON mc.id=w.monitoring_config_id
            JOIN brand b ON b.id=mc.brand_id
            ORDER BY a.id
            """
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            counts["seen"] += 1
            references = json.loads(row["references_json"] or "[]")
            citations = tuple(
                CitationInput(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    cited_text=item.get("text"),
                )
                for item in references
                if isinstance(item, dict) and item.get("url")
            )
            competitors = tuple(
                item["name"]
                for item in source.execute(
                    "SELECT name FROM competitor WHERE brand_id=? ORDER BY id",
                    (row["brand_id"],),
                ).fetchall()
            )
            capture_time = parse_time(row["tick_time"])
            service.analyze_and_persist(
                tenant_pub_id=migrator._pub("tnt", "tenant", row["tenant_id"]),
                project_pub_id=migrator._pub("prj", "project", row["monitoring_config_id"]),
                answer_pub_id=migrator._pub("ans", "answer", row["id"]),
                answer_text=row["response_text"] or "",
                brand=row["brand_name"],
                competitors=competitors,
                citations=citations,
                dimensions={
                    "question_pub_id": migrator._pub("qry", "query_item", row["query_item_id"]),
                    "query_text": row["query_text"],
                    "model": row["model_id"] or row["engine"],
                    "region": row["region"],
                    "mode": row["mode"],
                    "eligible": str(bool(row["eligible"])).lower(),
                    "degraded": str(bool(row["degraded_flag"])).lower(),
                },
                own_domains=tuple(
                    domain.lower()
                    for domain in json.loads(row["own_domains_json"] or "[]")
                    if isinstance(domain, str)
                ),
                provenance=RedactedProvenance(
                    platform_account_pub_id=None,
                    browser_profile_version_pub_id=None,
                    session_event_pub_id=None,
                    channel=CaptureChannel.API,
                    authorization_scope=("historical:migrated-read",),
                    adapter_version="legacy-migration-v1",
                    capture_time=capture_time,
                    access_class=AccessClass.CUSTOMER_PRIVATE,
                ),
                scorer_version="geo-scoring-v2-migration",
                metric_version="metrics-v2-migration",
                model_version="deterministic-v2",
            )
            counts["rebuilt"] += 1
    with psycopg.connect(dsn, row_factory=dict_row) as target:
        completion_reconciliation = migrator.reconcile_migrated_completion_events(target)
    return {
        "schema_version": "1.0",
        "source_snapshot_sha256": migrator.snapshot_hash,
        "derived_values_copied_from_legacy": False,
        "counts": counts,
        "completion_reconciliation": completion_reconciliation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = rebuild(args.source, dsn=_dsn())
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
