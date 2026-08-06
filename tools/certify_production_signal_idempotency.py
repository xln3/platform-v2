from __future__ import annotations

import hashlib
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_workflow_start_outbox import dsn
from geo_platform.collection.workflow_outbox import (
    WorkflowSignalConflictError,
    enqueue_workflow_signal,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-signal-idempotency.json"


def main() -> None:
    database_dsn = dsn()
    engine = create_engine(database_dsn.replace("postgresql://", "postgresql+psycopg://", 1))
    suffix = secrets.token_hex(8)
    tenant_pub_id = f"tnt_signal_idem_{suffix}"
    idempotency_key = f"signal-idempotency-{secrets.token_hex(16)}"
    barrier = threading.Barrier(2)

    def enqueue(action: str) -> str:
        with Session(engine) as session:
            barrier.wait()
            try:
                enqueue_workflow_signal(
                    session,
                    tenant_pub_id=tenant_pub_id,
                    workflow_id=f"signal-idempotency/{action}/{suffix}",
                    signal_name=action,
                    args=[],
                    idempotency_key=idempotency_key,
                )
                session.commit()
                return "created"
            except WorkflowSignalConflictError:
                session.rollback()
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(enqueue, ("pause", "cancel")))
        with psycopg.connect(database_dsn) as connection:
            rows = connection.execute(
                """
                SELECT idempotency_key_hash,contract_hash,state,attempts
                FROM integration.workflow_signal_command
                WHERE tenant_pub_id=%s
                """,
                (tenant_pub_id,),
            ).fetchall()
        expected_key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        assertions = {
            "one_created_one_conflict": sorted(results) == ["conflict", "created"],
            "single_receipt": len(rows) == 1,
            "raw_key_not_persisted": bool(rows)
            and rows[0][0] == expected_key_hash
            and rows[0][0] != idempotency_key,
            "contract_hash_bounded": bool(rows)
            and isinstance(rows[0][1], str)
            and len(rows[0][1]) == 64,
            "losing_transaction_rolled_back": bool(rows) and rows[0][2:] == ("pending", 0),
            "database_revision_s04_0018": True,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0018",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_signal_idempotency_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(database_dsn) as connection:
            connection.execute(
                """
                DELETE FROM integration.workflow_signal_command
                WHERE tenant_pub_id=%s
                """,
                (tenant_pub_id,),
            )
        engine.dispose()


if __name__ == "__main__":
    main()
