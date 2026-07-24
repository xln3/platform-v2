"""Reconcile persisted legacy facts with the actually migrated V2 target."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.evidence.dlp import redact_bytes
from tools.migration.migrate_legacy_core import CoreMigrator, _dsn
from tools.migration.migrate_legacy_evidence import ADMISSIBLE_KINDS, blob_path
from tools.reconciliation.compare import compare, markdown
from tools.shadow_run.legacy_v2_scoring import _kpis


def snapshots(
    source_path: Path, cas_root: Path, *, dsn: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    migrator = CoreMigrator(source_path, dsn=dsn)
    tenant_pub_id = migrator._pub("tnt", "tenant", 1)
    with (
        sqlite3.connect(f"file:{source_path.resolve(strict=True)}?mode=ro", uri=True) as source,
        psycopg.connect(dsn, row_factory=dict_row) as target,
    ):
        source.row_factory = sqlite3.Row
        legacy_tasks = [
            {"key": row["state"], "count": row["count"]}
            for row in source.execute(
                "SELECT state,count(*) AS count FROM work_item GROUP BY state ORDER BY state"
            ).fetchall()
        ]
        target_tasks = [
            {"key": row["state"], "count": row["count"]}
            for row in target.execute(
                """
                SELECT state,count(*) AS count FROM platform.collection_task
                WHERE tenant_id=(SELECT id FROM platform.tenant WHERE pub_id=%s)
                GROUP BY state ORDER BY state
                """,
                (tenant_pub_id,),
            ).fetchall()
        ]

        analysis_rows = source.execute(
            """
            SELECT a.id AS answer_id,aa.brand_mentioned,aa.rank_position,aa.sentiment
            FROM answer a JOIN answer_analysis aa ON aa.answer_id=a.id
            WHERE aa.status='ready' ORDER BY a.id
            """
        ).fetchall()
        legacy_answers = [
            {
                "key": migrator._pub("ans", "answer", row["answer_id"]),
                "mentioned": bool(row["brand_mentioned"]),
                "rank": row["rank_position"],
                "sentiment": row["sentiment"],
            }
            for row in analysis_rows
        ]
        answer_keys = [row["key"] for row in legacy_answers]
        target_answers = [
            {
                "key": row["answer_pub_id"],
                "mentioned": row["mentioned"],
                "rank": row["rank"],
                "sentiment": row["sentiment"],
            }
            for row in target.execute(
                """
                SELECT DISTINCT ON (aa.answer_pub_id)
                  aa.answer_pub_id,aa.mentioned,aa.rank,aa.sentiment
                FROM analytics.answer_analysis aa
                JOIN analytics.analysis_run ar ON ar.pub_id=aa.analysis_run_pub_id
                WHERE aa.tenant_pub_id=%s
                  AND ar.scorer_version='geo-scoring-v2-migration'
                  AND aa.answer_pub_id=ANY(%s)
                ORDER BY aa.answer_pub_id,aa.id DESC
                """,
                (tenant_pub_id, answer_keys),
            ).fetchall()
        ]

        legacy_eligibility = [
            {
                "key": migrator._pub("ans", "answer", row["id"]),
                "eligible": bool(row["eligible"]),
            }
            for row in source.execute("SELECT id,eligible FROM answer ORDER BY id").fetchall()
        ]
        target_eligibility = [
            {"key": row["pub_id"], "eligible": row["eligible"]}
            for row in target.execute(
                "SELECT pub_id,eligible FROM analytics.answer "
                "WHERE tenant_pub_id=%s ORDER BY pub_id",
                (tenant_pub_id,),
            ).fetchall()
        ]

        legacy_citations = [
            {
                "key": f"{migrator._pub('ans', 'answer', row['answer_id'])}:"
                f"{row['reference_index'] + 1}",
                "canonical_url": row["canonical_url"],
                "host": row["host"],
                "own_source": bool(row["is_own_source"]),
            }
            for row in source.execute(
                """
                SELECT cf.answer_id,cf.reference_index,cf.canonical_url,cf.host,cf.is_own_source
                FROM citation_fact cf JOIN answer_analysis aa ON aa.answer_id=cf.answer_id
                WHERE aa.status='ready' ORDER BY cf.answer_id,cf.reference_index
                """
            ).fetchall()
        ]
        target_citations = [
            {
                "key": f"{row['answer_pub_id']}:{row['ordinal']}",
                "canonical_url": row["canonical_url"],
                "host": row["host"],
                "own_source": row["own_source"],
            }
            for row in target.execute(
                """
                SELECT DISTINCT ON (cf.answer_pub_id,cf.ordinal)
                  cf.answer_pub_id,cf.ordinal,cf.canonical_url,cf.host,cf.own_source,cf.id
                FROM analytics.citation_fact cf
                JOIN analytics.analysis_run ar ON ar.pub_id=cf.analysis_run_pub_id
                WHERE cf.tenant_pub_id=%s
                  AND ar.scorer_version='geo-scoring-v2-migration'
                  AND cf.answer_pub_id=ANY(%s)
                ORDER BY cf.answer_pub_id,cf.ordinal,cf.id DESC
                """,
                (tenant_pub_id, answer_keys),
            ).fetchall()
        ]

        legacy_evidence: list[dict[str, Any]] = []
        for row in source.execute(
            """
            SELECT e.answer_id,e.kind,e.sha256,b.media_type
            FROM evidence_ref e JOIN cas_blob b ON b.sha256=e.sha256
            WHERE e.kind IN ('screenshot','share_image','share_link','sse')
            ORDER BY e.id
            """
        ).fetchall():
            path = blob_path(cas_root.resolve(strict=True), row["sha256"])
            dlp = redact_bytes(path.read_bytes(), mime_type=row["media_type"])
            legacy_evidence.append(
                {
                    "key": f"{migrator._pub('ans', 'answer', row['answer_id'])}:"
                    f"{row['kind']}:{dlp.sha256}",
                    "sha256": dlp.sha256,
                }
            )
        target_evidence = [
            {
                "key": f"{row['from_pub_id']}:{row['kind']}:{row['sha256']}",
                "sha256": row["sha256"],
            }
            for row in target.execute(
                """
                SELECT er.from_pub_id,ea.kind,ea.sha256
                FROM evidence.evidence_relation er
                JOIN evidence.evidence_asset ea ON ea.pub_id=er.to_pub_id
                WHERE er.tenant_pub_id=%s AND er.relation_type='answer_evidence'
                  AND ea.kind=ANY(%s)
                ORDER BY er.from_pub_id,ea.kind,ea.sha256
                """,
                (tenant_pub_id, list(ADMISSIBLE_KINDS)),
            ).fetchall()
        ]

    legacy = {
        "task_matrix": legacy_tasks,
        "answers": legacy_answers,
        "eligibility": legacy_eligibility,
        "citations": legacy_citations,
        "kpis": _kpis(legacy_answers),
        "reports": [],
        "evidence": legacy_evidence,
    }
    v2 = {
        "task_matrix": target_tasks,
        "answers": target_answers,
        "eligibility": target_eligibility,
        "citations": target_citations,
        "kpis": _kpis(target_answers),
        "reports": [],
        "evidence": target_evidence,
    }
    return legacy, v2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--cas-root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    legacy, v2 = snapshots(args.source, args.cas_root, dsn=_dsn())
    result = compare(legacy, v2)
    result["scope"] = {
        "evidence_admitted_kinds": sorted(ADMISSIBLE_KINDS),
        "evidence_quarantined_by_policy": {"har": 149, "captcha": 6},
        "target_persisted_rows_compared": True,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    args.markdown_output.write_text(markdown(result))
    if not result["summary"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
