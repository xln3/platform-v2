from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from geo_platform.analytics.outbox import OutboxConsumer

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-outbox-domain-routing.json"


def main() -> None:
    dsn = database_dsn()
    suffix = secrets.token_hex(12)
    tenant = f"tnt_outbox_routing_probe_{suffix}"
    analytics_event = f"evt_analytics_routing_{suffix}"
    collection_event = f"evt_collection_routing_{suffix}"
    consumer_name = f"s04-routing-probe-{suffix}"
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO integration.outbox_event
              (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,occurred_at)
            VALUES
              (%s,%s,'analytics.answer.analyzed',%s,'probe','{}',now()),
              (%s,%s,'collection.run.completed',%s,'probe','{}',now())
            """,
            (
                analytics_event,
                tenant,
                f"ans_routing_{suffix}",
                collection_event,
                tenant,
                f"run_routing_{suffix}",
            ),
        )
    delivered: list[str] = []
    consumer = OutboxConsumer(
        dsn=dsn,
        consumer_name=consumer_name,
        publish=lambda event: delivered.append(str(event["event_id"])),
        event_types=("analytics.answer.analyzed", "intelligence.feature.recorded"),
    )
    try:
        consumer.drain()
        with psycopg.connect(dsn) as connection:
            states = dict(
                connection.execute(
                    """
                    SELECT event_id,published_at IS NOT NULL
                    FROM integration.outbox_event
                    WHERE event_id=ANY(%s)
                    """,
                    ([analytics_event, collection_event],),
                ).fetchall()
            )
        assertions = {
            "analytics_event_delivered": delivered == [analytics_event],
            "analytics_event_marked_published": states.get(analytics_event) is True,
            "collection_event_not_delivered_to_analytics": collection_event not in delivered,
            "collection_event_left_for_own_consumer": states.get(collection_event) is False,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0022",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_outbox_domain_routing_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM integration.consumer_receipt WHERE consumer_name=%s",
                (consumer_name,),
            )
            connection.execute(
                "DELETE FROM integration.outbox_event WHERE tenant_pub_id=%s", (tenant,)
            )


if __name__ == "__main__":
    main()
