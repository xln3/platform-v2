from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import errors

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-revoked-account-terminal-state.json"


def main() -> None:
    dsn = os.environ.get("GEO_POSTGRES_DSN", "").replace("postgresql+psycopg://", "postgresql://")
    if not dsn:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(UTC)
    blocked = False
    sqlstate = None
    constraint_name = None
    terminal_state = None
    trigger_enabled = False
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            adapter_id, tenant_id, account_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(
                """
                INSERT INTO platform.platform_adapter (
                  id, pub_id, slug, display_name, admission_level,
                  capabilities_json, adapter_version
                ) VALUES (%s, %s, %s, 'terminal-state-certification',
                          'synthetic', '[]', 'certification-only')
                """,
                (adapter_id, f"pad_terminal_{suffix}", f"terminal-{suffix}"),
            )
            connection.execute(
                """
                INSERT INTO platform.tenant (id, pub_id, name, state, created_at, updated_at)
                VALUES (%s, %s, 'terminal-state-certification', 'active', %s, %s)
                """,
                (tenant_id, f"tnt_terminal_{suffix}", now, now),
            )
            connection.execute(
                """
                INSERT INTO platform.platform_account (
                  id, pub_id, tenant_id, adapter_id, owner_pub_id, account_mask,
                  purpose, responsible_pub_id, custody_mode, region, state,
                  admission_level, version, created_at, updated_at
                ) VALUES (
                  %s, %s, %s, %s, %s, 'synthetic-terminal', 'certification',
                  %s, 'server', 'isolated', 'revoked', 'synthetic', 1, %s, %s
                )
                """,
                (
                    account_id,
                    f"pac_terminal_{suffix}",
                    tenant_id,
                    adapter_id,
                    f"usr_terminal_{suffix}",
                    f"usr_terminal_{suffix}",
                    now,
                    now,
                ),
            )
            connection.execute("SAVEPOINT attempted_reactivation")
            try:
                connection.execute(
                    "UPDATE platform.platform_account SET state = 'active' WHERE id = %s",
                    (account_id,),
                )
            except errors.CheckViolation as error:
                blocked = True
                sqlstate = error.sqlstate
                constraint_name = error.diag.constraint_name
                connection.execute("ROLLBACK TO SAVEPOINT attempted_reactivation")
            state_row = connection.execute(
                "SELECT state FROM platform.platform_account WHERE id = %s", (account_id,)
            ).fetchone()
            if state_row is None:
                raise RuntimeError("synthetic_terminal_account_missing")
            terminal_state = state_row[0]
            trigger = connection.execute(
                """
                SELECT tgenabled
                FROM pg_trigger
                WHERE tgname = 'trg_platform_account_revoked_terminal'
                  AND tgrelid = 'platform.platform_account'::regclass
                  AND NOT tgisinternal
                """
            ).fetchone()
            trigger_enabled = trigger is not None and trigger[0] == "O"
            connection.execute("DELETE FROM platform.platform_account WHERE id = %s", (account_id,))
            connection.execute("DELETE FROM platform.tenant WHERE id = %s", (tenant_id,))
            connection.execute("DELETE FROM platform.platform_adapter WHERE id = %s", (adapter_id,))
    with psycopg.connect(dsn) as connection:
        fixture_row = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM platform.tenant WHERE pub_id LIKE 'tnt_terminal_%')
              + (SELECT count(*) FROM platform.platform_adapter
                 WHERE pub_id LIKE 'pad_terminal_%')
            """
        ).fetchone()
        if fixture_row is None:
            raise RuntimeError("synthetic_fixture_cleanup_count_unavailable")
        fixture_count = fixture_row[0]
    assertions = {
        "reactivation_blocked": blocked,
        "check_violation_sqlstate": sqlstate == "23514",
        "stable_constraint_name": constraint_name == "ck_platform_account_revoked_terminal",
        "state_remains_revoked": terminal_state == "revoked",
        "trigger_enabled": trigger_enabled,
        "synthetic_fixture_removed": fixture_count == 0,
    }
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "database_revision": "s04_0012",
        "assertions": assertions,
        "sensitive_values_recorded": False,
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    if result["result"] != "passed":
        raise RuntimeError("revoked_account_terminal_state_certification_failed")
    print(json.dumps({"result": "passed", "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
