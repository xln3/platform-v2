from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import psycopg

ROOT = Path(__file__).parents[1]
ENV_PATH = Path("/etc/geo-platform-v2/platform.env")
OUTPUT = ROOT / "tests/s04-evidence/production-runtime-data-counts.json"

POSTGRES_COUNTS = {
    "tenants": "platform.tenant",
    "users": "platform.app_user",
    "memberships": "platform.membership",
    "projects": "platform.project",
    "monitoring_configs": "platform.monitoring_config",
    "queries": "platform.query_item",
    "runs": "platform.collection_run",
    "tasks": "platform.collection_task",
    "answers": "analytics.answer",
    "analyses": "analytics.answer_analysis",
    "citations": "analytics.citation_fact",
    "evidence_assets": "evidence.evidence_asset",
    "reports": "reporting.report",
    "report_deliveries": "reporting.report_delivery",
    "anti_geo_evaluation_datasets": "intelligence.evaluation_dataset",
    "anti_geo_evaluation_runs": "intelligence.evaluation_run",
    "anti_geo_model_admissions": "intelligence.model_admission",
    "outbox_total": "integration.outbox_event",
    "migration_runs": "integration.migration_run",
    "watermarks": "integration.migration_watermark",
}
CLICKHOUSE_TABLES = (
    "answer_fact",
    "citation_fact",
    "run_event",
    "metric_daily",
    "feature_fact",
)


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _clickhouse_counts(values: dict[str, str]) -> dict[str, int]:
    statements = [
        f"SELECT '{table}',count() FROM geo_analytics.{table}" for table in CLICKHOUSE_TABLES
    ]
    response = httpx.post(
        values["GEO_CLICKHOUSE_URL"],
        params={"query": " UNION ALL ".join(statements) + " FORMAT TabSeparated"},
        auth=(values["GEO_CLICKHOUSE_USER"], values["GEO_CLICKHOUSE_PASSWORD"]),
        timeout=15,
    )
    response.raise_for_status()
    return {
        name: int(count)
        for line in response.text.splitlines()
        for name, count in [line.split("\t", 1)]
    }


def _scalar_count(connection: psycopg.Connection[tuple[object, ...]], query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError("production count query returned no row")
    return cast(int, row[0])


def main() -> None:
    values = _environment(ENV_PATH)
    with psycopg.connect(_psycopg_dsn(values["GEO_POSTGRES_DSN"])) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision is None:
            raise RuntimeError("production schema revision is absent")
        counts = {
            name: _scalar_count(connection, f"SELECT count(*) FROM {table}")
            for name, table in POSTGRES_COUNTS.items()
        }
        unpublished = _scalar_count(
            connection,
            "SELECT count(*) FROM integration.outbox_event WHERE published_at IS NULL",
        )
        unpublished_by_type = {
            str(row[0]): cast(int, row[1])
            for row in connection.execute(
                """
                SELECT event_type,count(*)
                FROM integration.outbox_event
                WHERE published_at IS NULL
                GROUP BY event_type ORDER BY event_type
                """
            ).fetchall()
        }
        migrated_answers_without_lineage = _scalar_count(
            connection,
            """
            SELECT count(*)
            FROM analytics.answer
            WHERE adapter_version='legacy-migration-v1'
              AND (run_pub_id IS NULL OR config_version_pub_id IS NULL)
            """,
        )
        migrated_answers_without_analysis = _scalar_count(
            connection,
            """
            SELECT count(*)
            FROM analytics.answer answer
            WHERE answer.adapter_version='legacy-migration-v1'
              AND NOT EXISTS (
                SELECT 1
                FROM analytics.answer_analysis analysis
                WHERE analysis.tenant_pub_id=answer.tenant_pub_id
                  AND analysis.answer_pub_id=answer.pub_id
              )
            """,
        )
        reconciled_completion_events = _scalar_count(
            connection,
            """
            SELECT count(*)
            FROM integration.outbox_event
            WHERE event_type='collection.run.completed'
              AND published_at IS NOT NULL
              AND payload->>'analysis_admission'='migrated_v2_rebuild'
            """,
        )
        reconciled_completion_answers = _scalar_count(
            connection,
            """
            SELECT COALESCE(sum((payload->>'analysis_rebuilt')::integer),0)::bigint
            FROM integration.outbox_event
            WHERE event_type='collection.run.completed'
              AND published_at IS NOT NULL
              AND payload->>'analysis_admission'='migrated_v2_rebuild'
            """,
        )
        unsupported_roles = _scalar_count(
            connection,
            """
            SELECT count(*) FROM platform.membership
            WHERE role NOT IN ('customer','operator','analyst','reviewer','admin')
            """,
        )
        report_delivery_events = _scalar_count(
            connection,
            """
            SELECT count(*) FROM reporting.report_event
            WHERE event_type IN ('delivered','delivery_confirmed')
            """,
        )
    clickhouse = _clickhouse_counts(values)
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": "production",
        "result": (
            "passed"
            if revision[0] == "s04_0029"
            and unpublished == 0
            and unsupported_roles == 0
            and migrated_answers_without_lineage == 0
            and migrated_answers_without_analysis == 0
            else "failed"
        ),
        "schema_version": revision[0],
        "counts": counts
        | {
            "outbox_unpublished": unpublished,
            "report_delivery_events": report_delivery_events,
            "migrated_answers_without_lineage": migrated_answers_without_lineage,
            "migrated_answers_without_analysis": migrated_answers_without_analysis,
            "reconciled_completion_events": reconciled_completion_events,
            "reconciled_completion_answers": reconciled_completion_answers,
        },
        "clickhouse_projection": clickhouse,
        "unpublished_outbox_by_type": unpublished_by_type,
        "historical_completion_reconciliation": (
            "legacy events converge only after every task answer has run/config lineage and a "
            "V2 rebuilt analysis"
        ),
        "supported_membership_roles_only": unsupported_roles == 0,
        "synthetic_report_delivery_rows_retained": (
            counts["report_deliveries"] != 0 or report_delivery_events != 0
        ),
        "secrets_or_tenant_identifiers_recorded": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "schema_version": evidence["schema_version"],
                "postgres_count_families": len(counts),
                "clickhouse_tables": len(clickhouse),
            }
        )
    )
    if evidence["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"result": "failed", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
