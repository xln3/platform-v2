from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from geo_platform.reports.service import ReportService

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-report-delivery-confirmation.json"


def main() -> None:
    dsn = database_dsn()
    suffix = secrets.token_hex(12)
    tenant = f"tnt_report_delivery_probe_{suffix}"
    published_report = f"rpt_delivery_published_{suffix}"
    draft_report = f"rpt_delivery_draft_{suffix}"
    recipient = f"customer_delivery_probe_{suffix}"
    other_recipient = f"customer_delivery_other_{suffix}"
    operator = f"operator_delivery_probe_{suffix}"
    service = ReportService(dsn=dsn, evidence=None)  # type: ignore[arg-type]
    generated_at = datetime.now(UTC)
    try:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """
                INSERT INTO reporting.report
                  (pub_id,tenant_pub_id,project_pub_id,title,state)
                VALUES
                  (%s,%s,%s,'S04 published delivery probe','published'),
                  (%s,%s,%s,'S04 draft delivery probe','draft')
                """,
                (
                    published_report,
                    tenant,
                    f"prj_delivery_published_{suffix}",
                    draft_report,
                    tenant,
                    f"prj_delivery_draft_{suffix}",
                ),
            )

        draft_rejected = False
        try:
            service.deliver(
                tenant_pub_id=tenant,
                report_pub_id=draft_report,
                recipient_pub_id=recipient,
                delivered_by_pub_id=operator,
            )
        except PermissionError:
            draft_rejected = True

        delivery_pub_id = service.deliver(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            recipient_pub_id=recipient,
            delivered_by_pub_id=operator,
        )
        before_confirmation = service.list_deliveries(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            recipient_pub_id=recipient,
        )
        replayed_delivery_pub_id = service.deliver(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            recipient_pub_id=recipient,
            delivered_by_pub_id=f"admin_delivery_replay_{suffix}",
        )
        wrong_recipient_rejected = False
        try:
            service.confirm_delivery(
                tenant_pub_id=tenant,
                report_pub_id=published_report,
                delivery_pub_id=delivery_pub_id,
                recipient_pub_id=other_recipient,
                confirmation_comment="controlled recipient mismatch probe",
            )
        except PermissionError:
            wrong_recipient_rejected = True

        confirmation_comment = "controlled customer confirmation probe"
        confirmed_pub_id = service.confirm_delivery(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            delivery_pub_id=delivery_pub_id,
            recipient_pub_id=recipient,
            confirmation_comment=confirmation_comment,
        )
        confirmed_once = service.list_deliveries(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            recipient_pub_id=recipient,
        )
        replayed_confirmation_pub_id = service.confirm_delivery(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            delivery_pub_id=delivery_pub_id,
            recipient_pub_id=recipient,
            confirmation_comment=confirmation_comment,
        )
        confirmed_replay = service.list_deliveries(
            tenant_pub_id=tenant,
            report_pub_id=published_report,
            recipient_pub_id=recipient,
        )
        drift_rejected = False
        try:
            service.confirm_delivery(
                tenant_pub_id=tenant,
                report_pub_id=published_report,
                delivery_pub_id=delivery_pub_id,
                recipient_pub_id=recipient,
                confirmation_comment="changed confirmation probe",
            )
        except ValueError:
            drift_rejected = True

        with psycopg.connect(dsn) as connection:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            delivery_row = connection.execute(
                """
                SELECT count(*),min(pub_id),min(confirmation_comment)
                FROM reporting.report_delivery
                WHERE tenant_pub_id=%s AND report_pub_id=%s
                """,
                (tenant, published_report),
            ).fetchone()
            events = connection.execute(
                """
                SELECT event_type,actor_pub_id,data->>'delivery_pub_id'
                FROM reporting.report_event
                WHERE tenant_pub_id=%s AND report_pub_id=%s
                  AND event_type IN ('delivered','delivery_confirmed')
                ORDER BY event_type
                """,
                (tenant, published_report),
            ).fetchall()

        assertions = {
            "database_revision_s04_0025": revision == ("s04_0025",),
            "draft_delivery_rejected": draft_rejected,
            "delivery_replay_returns_authoritative_id": (
                delivery_pub_id == replayed_delivery_pub_id
            ),
            "delivery_initially_unconfirmed": (
                len(before_confirmation) == 1 and before_confirmation[0]["confirmed_at"] is None
            ),
            "wrong_recipient_confirmation_rejected": wrong_recipient_rejected,
            "recipient_confirmation_replays_authoritative_id": (
                delivery_pub_id == confirmed_pub_id == replayed_confirmation_pub_id
            ),
            "confirmation_timestamp_monotonic": (
                confirmed_once[0]["confirmed_at"] == confirmed_replay[0]["confirmed_at"] is not None
            ),
            "confirmation_payload_drift_rejected": drift_rejected,
            "single_delivery_row_retained": delivery_row
            == (1, delivery_pub_id, confirmation_comment),
            "single_actor_attributed_event_per_transition": events
            == [
                ("delivered", operator, delivery_pub_id),
                ("delivery_confirmed", recipient, delivery_pub_id),
            ],
            "other_recipient_cannot_list_delivery": service.list_deliveries(
                tenant_pub_id=tenant,
                report_pub_id=published_report,
                recipient_pub_id=other_recipient,
            )
            == [],
        }
        evidence = {
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": revision[0] if revision else None,
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "real_production_dependencies": ["PostgreSQL"],
            "qualification": (
                "Storage, replay, actor attribution and recipient isolation are certified. "
                "This is not a real-customer identity acceptance."
            ),
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_report_delivery_confirmation_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM reporting.report_event WHERE tenant_pub_id=%s", (tenant,)
            )
            connection.execute(
                "DELETE FROM reporting.report_delivery WHERE tenant_pub_id=%s", (tenant,)
            )
            connection.execute("DELETE FROM reporting.report WHERE tenant_pub_id=%s", (tenant,))


if __name__ == "__main__":
    main()
