from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg
from certify_production_workflow_start_outbox import (
    cleanup,
    dsn,
    insert_fixture,
)
from geo_platform.config import get_settings
from temporalio.client import Client

from workflows.activities.collection import CollectionTaskInput
from workflows.definitions.collection import GeoCollectionInput, GeoCollectionWorkflow

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-workflow-already-started.json"


async def main() -> None:
    database_dsn = dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, workflow_id, payload = insert_fixture(database_dsn, suffix, enqueue=False)
    # Hold the first execution open so the outbox must take the concurrent
    # AlreadyStarted recovery path instead of workflow ID reuse.
    payload["requires_intervention"] = True
    settings = get_settings()
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = None
    try:
        tasks = cast(list[dict[str, Any]], payload["tasks"])
        handle = await temporal.start_workflow(
            GeoCollectionWorkflow.run,
            GeoCollectionInput(
                tenant_pub_id=str(payload["tenant_pub_id"]),
                project_pub_id=str(payload["project_pub_id"]),
                run_pub_id=str(payload["run_pub_id"]),
                config_version_pub_id=str(payload["config_version_pub_id"]),
                tasks=[CollectionTaskInput(**item) for item in tasks],
                requires_intervention=bool(payload["requires_intervention"]),
                account_pub_id=None,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        accepted_run_id = handle.result_run_id
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

        command = None
        run = None
        for _ in range(120):
            with psycopg.connect(database_dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,attempts,last_error_code,temporal_run_id
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
                    SELECT state,temporal_run_id FROM platform.collection_run
                    WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            if (
                command == ("started", 1, None, accepted_run_id)
                and run is not None
                and run[1] == accepted_run_id
            ):
                break
            await asyncio.sleep(0.25)

        assertions = {
            "temporal_preaccepted_workflow": bool(accepted_run_id),
            "outbox_converged_started": command is not None and command[0] == "started",
            "single_recovery_attempt": command is not None and command[1] == 1,
            "no_dispatch_error": command is not None and command[2] is None,
            "already_started_run_id_persisted": command is not None
            and command[3] == accepted_run_id,
            "collection_run_id_matches": run is not None and run[1] == accepted_run_id,
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
            raise RuntimeError("production_workflow_already_started_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        try:
            if handle is not None:
                await handle.signal(GeoCollectionWorkflow.cancel)
                await handle.result()
        except Exception:
            pass
        cleanup(database_dsn, tenant_pub_id, workflow_id)


if __name__ == "__main__":
    asyncio.run(main())
