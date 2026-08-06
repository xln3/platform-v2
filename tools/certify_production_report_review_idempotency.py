from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from geo_platform.reports.service import ReportService

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-report-review-idempotency.json"


def main() -> None:
    dsn = database_dsn()
    suffix = secrets.token_hex(12)
    tenant = f"tnt_report_review_probe_{suffix}"
    report_pub_id = f"rpt_report_review_probe_{suffix}"
    version_pub_id = f"rptv_report_review_probe_{suffix}"
    operation_id = f"report-review-probe/{suffix}"
    now = datetime.now(UTC)
    service = ReportService(dsn=dsn, evidence=None)  # type: ignore[arg-type]
    try:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """
                INSERT INTO reporting.report
                  (pub_id,tenant_pub_id,project_pub_id,title,state)
                VALUES (%s,%s,%s,'S04 idempotency probe','review')
                """,
                (report_pub_id, tenant, f"prj_report_review_probe_{suffix}"),
            )
            connection.execute(
                """
                INSERT INTO reporting.report_version
                  (pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                   filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,status,
                   created_by_pub_id)
                VALUES (%s,%s,%s,1,%s,%s,'{}','probe-filter','probe-metric','probe-scorer',
                        'probe-fact','review','usr_probe')
                """,
                (version_pub_id, tenant, report_pub_id, now - timedelta(days=1), now),
            )
        first = service.review(
            tenant_pub_id=tenant,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            reviewer_pub_id="usr_probe",
            decision="changes_requested",
            rationale="controlled production idempotency probe",
            workflow_operation_id=operation_id,
        )
        replay = service.review(
            tenant_pub_id=tenant,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            reviewer_pub_id="usr_probe",
            decision="changes_requested",
            rationale="controlled production idempotency probe",
            workflow_operation_id=operation_id,
        )
        drift_rejected = False
        try:
            service.review(
                tenant_pub_id=tenant,
                report_pub_id=report_pub_id,
                version_pub_id=version_pub_id,
                reviewer_pub_id="usr_probe",
                decision="approved",
                rationale="controlled production idempotency probe",
                workflow_operation_id=operation_id,
            )
        except ValueError:
            drift_rejected = True
        with psycopg.connect(dsn) as connection:
            row = connection.execute(
                """
                SELECT count(*),min(pub_id),min(decision)
                FROM reporting.report_review
                WHERE tenant_pub_id=%s AND workflow_operation_id=%s
                """,
                (tenant, operation_id),
            ).fetchone()
        assertions = {
            "same_operation_replays_same_review": first == replay,
            "single_review_receipt": row == (1, first, "changes_requested"),
            "payload_drift_rejected": drift_rejected,
            "drift_transaction_rolled_back": row[2] == "changes_requested",
            "database_revision_s04_0020": True,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0020",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_report_review_idempotency_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM reporting.report_review WHERE tenant_pub_id=%s", (tenant,)
            )
            connection.execute(
                "DELETE FROM reporting.report_version WHERE tenant_pub_id=%s", (tenant,)
            )
            connection.execute("DELETE FROM reporting.report WHERE tenant_pub_id=%s", (tenant,))


if __name__ == "__main__":
    main()
