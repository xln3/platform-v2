from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from psycopg.rows import dict_row

from workflows.activities.collection import publish_downstream_event

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-collection-completion-outbox.json"


def main() -> None:
    dsn = database_dsn()
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        completed_count = connection.execute(
            """
            SELECT count(*) AS count FROM platform.collection_run
            WHERE state IN ('completed','completed_with_failures')
            """
        ).fetchone()["count"]
        event_count = connection.execute(
            """
            SELECT count(*) AS count FROM integration.outbox_event
            WHERE event_type='collection.run.completed'
            """
        ).fetchone()["count"]
        run = connection.execute(
            """
            SELECT run.pub_id,tenant.pub_id AS tenant_pub_id,run.total_tasks,
                   run.completed_tasks,run.failed_tasks
            FROM platform.collection_run run
            JOIN platform.tenant tenant ON tenant.id=run.tenant_id
            WHERE run.state IN ('completed','completed_with_failures')
            ORDER BY run.updated_at DESC LIMIT 1
            """
        ).fetchone()
    if run is None:
        raise RuntimeError("production_completed_collection_run_not_available")
    first = publish_downstream_event(run["pub_id"], run["tenant_pub_id"])
    replay = publish_downstream_event(run["pub_id"], run["tenant_pub_id"])
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT event_id,payload FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
              AND event_type='collection.run.completed'
            """,
            (run["tenant_pub_id"], run["pub_id"]),
        ).fetchall()
        duplicates = connection.execute(
            """
            SELECT count(*) AS count FROM (
              SELECT tenant_pub_id,aggregate_pub_id,count(*)
              FROM integration.outbox_event
              WHERE event_type='collection.run.completed'
              GROUP BY tenant_pub_id,aggregate_pub_id HAVING count(*) > 1
            ) duplicate_groups
            """
        ).fetchone()["count"]
    payload = rows[0]["payload"] if rows else {}
    assertions = {
        "all_historical_completed_runs_backfilled": completed_count == event_count,
        "exact_publish_replay_returns_same_event": first == replay,
        "selected_run_has_one_event": len(rows) == 1,
        "no_duplicate_completion_event_groups": duplicates == 0,
        "event_payload_matches_authoritative_counts": (
            payload.get("total_tasks"),
            payload.get("completed_tasks"),
            payload.get("failed_tasks"),
        )
        == (run["total_tasks"], run["completed_tasks"], run["failed_tasks"]),
    }
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "database_revision": "s04_0022",
        "completed_run_count": completed_count,
        "completion_event_count": event_count,
        "selected_run_sha256": hashlib.sha256(run["pub_id"].encode()).hexdigest(),
        "assertions": assertions,
        "sensitive_values_recorded": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if evidence["result"] != "passed":
        raise RuntimeError("production_collection_completion_outbox_failed")
    print(json.dumps({"result": "passed", "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
