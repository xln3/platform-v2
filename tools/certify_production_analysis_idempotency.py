from __future__ import annotations

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from geo_platform.analytics.service import AnalyticsService

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.analyzer import CitationInput

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-analysis-idempotency.json"


def main() -> None:
    dsn = database_dsn()
    suffix = secrets.token_hex(12)
    tenant = f"tnt_analysis_probe_{suffix}"
    answer = f"ans_analysis_probe_{suffix}"
    request = {
        "tenant_pub_id": tenant,
        "project_pub_id": f"prj_analysis_probe_{suffix}",
        "answer_pub_id": answer,
        "answer_text": "Acme is cited by an independent source.",
        "brand": "Acme",
        "competitors": (),
        "citations": (CitationInput("https://example.com/independent-source"),),
        "dimensions": {"model": "probe", "region": "cn", "mode": "normal"},
        "own_domains": (),
        "provenance": RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("read",),
            adapter_version="s04-production-probe",
            capture_time=datetime.now(UTC),
            access_class=AccessClass.PUBLIC,
        ),
        "scorer_version": "s04-probe-scorer",
        "metric_version": "s04-probe-metrics",
        "model_version": "s04-probe-rules",
    }
    service = AnalyticsService(dsn=dsn)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: service.analyze_and_persist(**request), range(2)))
        drift_rejected = False
        try:
            service.analyze_and_persist(
                **(request | {"answer_text": "changed input under the same answer ID"})
            )
        except ValueError:
            drift_rejected = True
        with psycopg.connect(dsn) as connection:
            counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM analytics.answer_analysis
                   WHERE tenant_pub_id=%s AND answer_pub_id=%s),
                  (SELECT count(*) FROM analytics.analysis_run WHERE tenant_pub_id=%s),
                  (SELECT count(*) FROM integration.outbox_event
                   WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
                     AND event_type='analytics.answer.analyzed')
                """,
                (tenant, answer, tenant, tenant, answer),
            ).fetchone()
        assertions = {
            "concurrent_replay_returns_one_analysis": len(
                {result["analysis_pub_id"] for result in results}
            )
            == 1,
            "concurrent_replay_returns_one_analysis_run": len(
                {result["analysis_run_pub_id"] for result in results}
            )
            == 1,
            "concurrent_replay_returns_one_outbox_event": len(
                {result["outbox_event_id"] for result in results}
            )
            == 1,
            "database_has_single_analysis_run_and_event": counts == (1, 1, 1),
            "answer_payload_drift_rejected": drift_rejected,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0020",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_analysis_idempotency_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(dsn) as connection:
            for table in (
                "integration.outbox_event",
                "analytics.metric_daily",
                "analytics.metric_trace",
                "analytics.citation_fact",
                "analytics.answer_analysis",
                "analytics.analysis_run",
                "analytics.answer",
            ):
                connection.execute(f"DELETE FROM {table} WHERE tenant_pub_id=%s", (tenant,))


if __name__ == "__main__":
    main()
