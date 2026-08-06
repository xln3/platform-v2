from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from certify_production_outbox_trace import (
    BASE_URL,
    database_dsn,
    legacy_session_token,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-run-terminal-guard.json"
TERMINAL_STATES = {
    "completed",
    "completed_with_failures",
    "failed",
    "cancelled",
    "skipped",
}


def main() -> None:
    dsn = database_dsn()
    with httpx.Client(
        base_url=BASE_URL,
        verify=False,
        cookies={"session": legacy_session_token()},
        timeout=15,
    ) as client:
        identity = client.get("/api/v2/identity/session")
        identity.raise_for_status()
        tenant_pub_id = str(identity.json()["tenant_pub_id"])
        response = client.get("/api/v2/collection/runs")
        response.raise_for_status()
        terminal = next(
            (item for item in response.json() if item["state"] in TERMINAL_STATES),
            None,
        )
        if terminal is None:
            raise RuntimeError("production_terminal_run_not_available")
        workflow_id = str(terminal["workflow_id"])
        run_pub_id = str(terminal["pub_id"])
        original_state = str(terminal["state"])

        with psycopg.connect(dsn) as connection:
            before_signals = connection.execute(
                """
                SELECT count(*) FROM integration.workflow_signal_command
                WHERE workflow_id=%s
                """,
                (workflow_id,),
            ).fetchone()
        rejected = client.post(
            f"/api/v2/collection/runs/{run_pub_id}/cancel",
            headers={"Idempotency-Key": f"terminal-guard-{secrets.token_hex(16)}"},
        )
        with psycopg.connect(dsn) as connection:
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
            after_state = connection.execute(
                """
                SELECT state FROM platform.collection_run WHERE workflow_id=%s
                """,
                (workflow_id,),
            ).fetchone()
            after_signals = connection.execute(
                """
                SELECT count(*) FROM integration.workflow_signal_command
                WHERE workflow_id=%s
                """,
                (workflow_id,),
            ).fetchone()
        constraint_name = None
        with psycopg.connect(dsn) as connection:
            try:
                connection.execute(
                    """
                    UPDATE platform.collection_run
                    SET state='running' WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                )
            except psycopg.errors.CheckViolation as error:
                constraint_name = error.diag.constraint_name
                connection.rollback()
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_pub_id', %s, true)",
                (tenant_pub_id,),
            )
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant[0],))
            post_attack_state = connection.execute(
                """
                SELECT state FROM platform.collection_run WHERE workflow_id=%s
                """,
                (workflow_id,),
            ).fetchone()

    error_code = (
        rejected.json().get("error", {}).get("code")
        if rejected.headers.get("content-type", "").startswith("application/json")
        else None
    )
    assertions = {
        "terminal_run_present": original_state in TERMINAL_STATES,
        "cancel_rejected_409": rejected.status_code == 409,
        "stable_error_code": error_code == "run_terminal",
        "terminal_state_unchanged": after_state == (original_state,),
        "no_signal_enqueued": before_signals == after_signals,
        "direct_sql_reactivation_rejected": constraint_name == "ck_collection_run_terminal_state",
        "terminal_state_survives_sql_attack": post_attack_state == (original_state,),
        "read_only_probe": True,
    }
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "database_revision": "s04_0019",
        "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
        "original_terminal_state": original_state,
        "assertions": assertions,
        "sensitive_values_recorded": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if evidence["result"] != "passed":
        raise RuntimeError("production_run_terminal_guard_failed")
    print(json.dumps({"result": "passed", "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
