from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_workflow_start_outbox import cleanup, dsn, insert_fixture
from geo_platform.config import get_settings

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-workflow-missing-history.json"


async def main() -> None:
    database_dsn = dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, workflow_id, payload = insert_fixture(database_dsn, suffix, enqueue=False)
    try:
        with psycopg.connect(database_dsn) as connection:
            connection.execute(
                """
                UPDATE platform.collection_run
                SET state='running' WHERE workflow_id=%s
                """,
                (workflow_id,),
            )
            connection.execute(
                """
                INSERT INTO integration.workflow_start_command (
                  command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,
                  payload,state,attempts,started_at
                ) VALUES (
                  %s,%s,'geo_collection',%s,%s,%s,'started',1,now()
                )
                """,
                (
                    uuid.uuid4(),
                    tenant_pub_id,
                    workflow_id,
                    get_settings().temporal_task_queue,
                    json.dumps(payload),
                ),
            )

        command = None
        run = None
        for _ in range(120):
            with psycopg.connect(database_dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,attempts,terminal_status
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
                tenant = connection.execute(
                    "SELECT id::text FROM platform.tenant WHERE pub_id=%s",
                    (tenant_pub_id,),
                ).fetchone()
                assert tenant is not None
                connection.execute(
                    "SELECT set_config('app.tenant_pub_id', %s, true)",
                    (tenant_pub_id,),
                )
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant[0],))
                run = connection.execute(
                    """
                    SELECT state,error_code FROM platform.collection_run
                    WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            if command == ("started", 1, "NOT_FOUND") and run == (
                "failed",
                "temporal_history_missing",
            ):
                break
            await asyncio.sleep(0.25)

        assertions = {
            "accepted_command_retained": command is not None and command[:2] == ("started", 1),
            "missing_history_recorded": command is not None and command[2] == "NOT_FOUND",
            "run_failed_closed": run == ("failed", "temporal_history_missing"),
            "no_dispatch_replay": command is not None and command[1] == 1,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0015",
            "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_workflow_missing_history_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        cleanup(database_dsn, tenant_pub_id, workflow_id)


if __name__ == "__main__":
    asyncio.run(main())
