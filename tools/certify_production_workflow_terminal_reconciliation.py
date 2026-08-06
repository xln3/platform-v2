from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from geo_platform.config import get_settings
from temporalio.client import Client

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-workflow-terminal-reconciliation.json"


def dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "").replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    if not value:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    return value


async def main() -> None:
    database_dsn = dsn()
    with psycopg.connect(database_dsn) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        observations = connection.execute(
            """
            SELECT command.tenant_pub_id,command.workflow_id,
                   command.terminal_status,run.state,run.error_code
            FROM integration.workflow_start_command command
            JOIN platform.tenant tenant ON tenant.pub_id=command.tenant_pub_id
            JOIN platform.collection_run run
              ON run.tenant_id=tenant.id AND run.workflow_id=command.workflow_id
            WHERE command.workflow_type='geo_collection_observation'
            ORDER BY command.id
            """
        ).fetchall()
        nonterminal_without_command = connection.execute(
            """
            SELECT count(*)
            FROM platform.collection_run run
            LEFT JOIN integration.workflow_start_command command
              ON command.workflow_id=run.workflow_id
            WHERE run.state IN ('starting','start_failed','running')
              AND command.workflow_id IS NULL
            """
        ).fetchone()

    settings = get_settings()
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    temporal_statuses: list[str] = []
    workflow_hashes: list[str] = []
    for _tenant_pub_id, workflow_id, *_rest in observations:
        description = await temporal.get_workflow_handle(workflow_id).describe()
        temporal_statuses.append(
            description.status.name if description.status is not None else "UNKNOWN"
        )
        workflow_hashes.append(hashlib.sha256(workflow_id.encode()).hexdigest())

    assertions = {
        "database_revision_s04_0014": revision == ("s04_0014",),
        "historical_nonterminal_runs_observed": len(observations) > 0,
        "all_observations_reconciled_failed": all(
            terminal_status == "FAILED"
            and run_state == "failed"
            and error_code == "temporal_failed"
            for _, _, terminal_status, run_state, error_code in observations
        ),
        "temporal_confirms_failed": bool(temporal_statuses)
        and all(status == "FAILED" for status in temporal_statuses),
        "no_nonterminal_run_without_command": nonterminal_without_command == (0,),
    }
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "database_revision": revision[0] if revision else None,
        "historical_observation_count": len(observations),
        "temporal_status_counts": {
            status: temporal_statuses.count(status) for status in sorted(set(temporal_statuses))
        },
        "workflow_id_sha256": workflow_hashes,
        "assertions": assertions,
        "read_only_certification": True,
        "sensitive_values_recorded": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n")
    if evidence["result"] != "passed":
        raise RuntimeError("production_workflow_terminal_reconciliation_failed")
    print(json.dumps({"result": "passed", "assertions": len(assertions)}))


if __name__ == "__main__":
    asyncio.run(main())
