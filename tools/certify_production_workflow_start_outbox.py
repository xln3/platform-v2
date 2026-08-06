from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from geo_platform.config import get_settings
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-workflow-start-outbox.json"


def dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "").replace("postgresql+psycopg://", "postgresql://")
    if not value:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    return value


def insert_fixture(
    database_dsn: str, suffix: str, *, enqueue: bool = True
) -> tuple[str, str, dict[str, object]]:
    tenant_id, customer_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    config_id, version_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenant_pub_id = f"tnt_outbox_{suffix}"
    run_pub_id = f"run_outbox_{suffix}"
    workflow_id = f"geo-collection/{tenant_pub_id}/prj_outbox_{suffix}/{run_pub_id}"
    now = datetime.now(UTC)
    payload = {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": f"prj_outbox_{suffix}",
        "run_pub_id": run_pub_id,
        "config_version_pub_id": f"cfv_outbox_{suffix}",
        "tasks": [
            {
                "business_key": hashlib.sha256(f"outbox-{suffix}".encode()).hexdigest(),
                "query": "synthetic outbox certification",
                "model": "fixed",
                "region": "isolated",
                "mode": "fast",
                "adapter": "fixed",
                "fail_until_attempt": 0,
            }
        ],
        "requires_intervention": False,
        "account_pub_id": None,
    }
    with psycopg.connect(database_dsn) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,'workflow-outbox-certification','active',%s,%s)
            """,
            (tenant_id, tenant_pub_id, now, now),
        )
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO platform.customer
              (id,pub_id,tenant_id,name,version,created_at,updated_at)
            VALUES (%s,%s,%s,'Outbox certification',1,%s,%s)
            """,
            (customer_id, f"cus_outbox_{suffix}", tenant_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO platform.project
              (id,pub_id,tenant_id,customer_id,name,state,version,created_at,updated_at)
            VALUES (%s,%s,%s,%s,'Outbox certification','active',1,%s,%s)
            """,
            (
                project_id,
                f"prj_outbox_{suffix}",
                tenant_id,
                customer_id,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.monitoring_config
              (id,pub_id,tenant_id,project_id,state,current_version,
               version,created_at,updated_at)
            VALUES (%s,%s,%s,%s,'frozen',1,1,%s,%s)
            """,
            (
                config_id,
                f"cfg_outbox_{suffix}",
                tenant_id,
                project_id,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.monitoring_config_version (
              id,pub_id,tenant_id,config_id,revision,effective_at,frozen_at,
              snapshot_json,snapshot_hash,version,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,1,%s,%s,'{}',%s,1,%s,%s)
            """,
            (
                version_id,
                f"cfv_outbox_{suffix}",
                tenant_id,
                config_id,
                now,
                now,
                hashlib.sha256(b"{}").hexdigest(),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.collection_run (
              id,pub_id,tenant_id,project_id,config_version_id,idempotency_key,
              workflow_id,state,total_tasks,completed_tasks,failed_tasks,paused,
              version,created_at,updated_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,'starting',1,0,0,false,1,%s,%s
            )
            """,
            (
                run_id,
                run_pub_id,
                tenant_id,
                project_id,
                version_id,
                f"outbox-{suffix}",
                workflow_id,
                now,
                now,
            ),
        )
        if enqueue:
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
                    get_settings().temporal_task_queue,
                    json.dumps(payload),
                ),
            )
    return tenant_pub_id, workflow_id, payload


def cleanup(database_dsn: str, tenant_pub_id: str, workflow_id: str) -> None:
    with psycopg.connect(database_dsn) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            return
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant[0]),))
        for table in (
            "collection_task",
            "collection_run",
            "monitoring_config_version",
            "monitoring_config",
            "project",
            "customer",
        ):
            connection.execute(
                f"DELETE FROM platform.{table} WHERE tenant_id=%s",  # noqa: S608
                (tenant[0],),
            )
        connection.execute(
            "DELETE FROM integration.workflow_signal_command WHERE workflow_id=%s",
            (workflow_id,),
        )
        connection.execute(
            "DELETE FROM integration.workflow_start_command WHERE workflow_id=%s",
            (workflow_id,),
        )
        connection.execute("DELETE FROM platform.tenant WHERE id=%s", (tenant[0],))


async def main() -> None:
    database_dsn = dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, workflow_id, _payload = insert_fixture(database_dsn, suffix)
    state = None
    try:
        for _ in range(120):
            with psycopg.connect(database_dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,attempts,last_error_code,temporal_run_id
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
                connection.execute(
                    "SELECT set_config('app.tenant_pub_id', %s, true)",
                    (tenant_pub_id,),
                )
                tenant = connection.execute(
                    "SELECT id::text FROM platform.tenant WHERE pub_id=%s",
                    (tenant_pub_id,),
                ).fetchone()
                assert tenant is not None
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant[0],))
                state = connection.execute(
                    """
                    SELECT state,completed_tasks,total_tasks,temporal_run_id
                    FROM platform.collection_run WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            if command is not None and command[0] == "started" and state is not None:
                if state[0] in {"completed", "failed", "cancelled"}:
                    break
            await asyncio.sleep(0.25)
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        workflow_failure_type = None
        try:
            await temporal.get_workflow_handle(workflow_id).result()
        except WorkflowFailureError as error:
            cause: BaseException | None = error
            while cause is not None:
                if isinstance(cause, ApplicationError):
                    workflow_failure_type = cause.type
                    break
                cause = cause.__cause__
        assertions = {
            "command_started": command is not None and command[0] == "started",
            "single_dispatch_attempt": command is not None and command[1] == 1,
            "no_dispatch_error": command is not None and command[2] is None,
            "temporal_run_id_persisted": command is not None and bool(command[3]),
            "collection_terminal_failed": state is not None and state[0] == "failed",
            "no_task_falsely_completed": state is not None and state[1] == 0 and state[2] == 1,
            "run_id_matches_command": (
                state is not None and command is not None and state[3] == command[3]
            ),
            "production_adapter_admission_failed_closed": (
                workflow_failure_type == "adapter_not_configured"
            ),
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0013",
            "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n")
        if evidence["result"] != "passed":
            raise RuntimeError("production_workflow_start_outbox_certification_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        cleanup(database_dsn, tenant_pub_id, workflow_id)


if __name__ == "__main__":
    asyncio.run(main())
