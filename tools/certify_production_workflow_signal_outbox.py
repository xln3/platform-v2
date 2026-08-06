from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_workflow_start_outbox import cleanup, dsn, insert_fixture
from geo_platform.collection.workflow_outbox import (
    WorkflowStartOutbox,
    workflow_signal_hashes,
)
from geo_platform.config import get_settings
from temporalio.client import Client

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-workflow-signal-outbox.json"


async def main() -> None:
    database_dsn = dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, workflow_id, payload = insert_fixture(database_dsn, suffix, enqueue=False)
    payload["requires_intervention"] = True
    settings = get_settings()
    try:
        with psycopg.connect(database_dsn) as connection:
            connection.execute(
                """
                INSERT INTO integration.workflow_start_command (
                  command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload
                ) VALUES (%s,%s,'geo_collection',%s,%s,%s)
                """,
                (
                    uuid.uuid4(),
                    tenant_pub_id,
                    workflow_id,
                    settings.temporal_task_queue,
                    json.dumps(payload),
                ),
            )
            for signal_name in ("pause", "resume", "cancel"):
                key_hash, contract_hash = workflow_signal_hashes(
                    workflow_id=workflow_id,
                    signal_name=signal_name,
                    args=[],
                    idempotency_key=f"signal-cert:{suffix}:{signal_name}",
                )
                connection.execute(
                    """
                    INSERT INTO integration.workflow_signal_command (
                      command_id,tenant_pub_id,workflow_id,signal_name,args,
                      idempotency_key_hash,contract_hash
                    ) VALUES (%s,%s,%s,%s,'[]',%s,%s)
                    """,
                    (
                        uuid.uuid4(),
                        tenant_pub_id,
                        workflow_id,
                        signal_name,
                        key_hash,
                        contract_hash,
                    ),
                )

        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        outbox = WorkflowStartOutbox(dsn=database_dsn, temporal=temporal)
        signal_blocked_until_start = not await outbox.dispatch_signal_one(workflow_id)
        start_dispatched = await outbox.dispatch_one(workflow_id)
        signal_dispatches = [await outbox.dispatch_signal_one(workflow_id) for _ in range(3)]

        commands = []
        receipts = None
        start_command = None
        run = None
        for _ in range(160):
            with psycopg.connect(database_dsn) as connection:
                start_command = connection.execute(
                    """
                    SELECT state,attempts,temporal_run_id
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
                commands = connection.execute(
                    """
                    SELECT signal_name,state,attempts,last_error_code
                    FROM integration.workflow_signal_command
                    WHERE workflow_id=%s ORDER BY id
                    """,
                    (workflow_id,),
                ).fetchall()
                receipts = connection.execute(
                    """
                    SELECT count(*),count(DISTINCT idempotency_key_hash),
                           bool_and(length(idempotency_key_hash)=64),
                           bool_and(length(contract_hash)=64)
                    FROM integration.workflow_signal_command
                    WHERE workflow_id=%s
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
            if (
                len(commands) == 3
                and all(command[1] == "delivered" for command in commands)
                and run is not None
                and run[0] == "cancelled"
            ):
                break
            await asyncio.sleep(0.25)

        result = await temporal.get_workflow_handle(workflow_id).result()
        assertions = {
            "signal_blocked_until_start": signal_blocked_until_start,
            "start_dispatched": start_dispatched,
            "workflow_started_once": start_command is not None
            and start_command[0] == "started"
            and start_command[1] == 1,
            "temporal_run_id_persisted": start_command is not None and bool(start_command[2]),
            "three_signals_recorded": len(commands) == 3,
            "signals_preserved_order": [command[0] for command in commands]
            == ["pause", "resume", "cancel"],
            "signals_delivered_once": all(
                command[1:] == ("delivered", 1, None) for command in commands
            ),
            "hashed_idempotency_receipts_unique": receipts == (3, 3, True, True),
            "three_signal_dispatches": signal_dispatches == [True, True, True],
            "workflow_cancelled": result["state"] == "cancelled",
            "database_cancelled": run is not None and run[0] == "cancelled",
            "no_terminal_error": run is not None and run[1] is None,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0018",
            "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_workflow_signal_outbox_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        cleanup(database_dsn, tenant_pub_id, workflow_id)


if __name__ == "__main__":
    asyncio.run(main())
