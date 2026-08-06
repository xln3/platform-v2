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
from temporalio.client import Client

from workflows.definitions.session import (
    PlatformSessionLifecycleWorkflow,
    SessionLifecycleInput,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-session-lifecycle.json"


def _postgres_dsn() -> str:
    dsn = os.environ.get("GEO_POSTGRES_DSN", "").replace("postgresql+psycopg://", "postgresql://")
    if not dsn:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    return dsn


def _eligible_account(dsn: str) -> tuple[str, str, str] | None:
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            """
            SELECT t.pub_id, a.pub_id, z.scopes_json
            FROM platform.platform_account a
            JOIN platform.tenant t ON t.id = a.tenant_id
            JOIN LATERAL (
              SELECT auth.scopes_json
              FROM platform.account_authorization auth
              WHERE auth.account_id = a.id
                AND auth.revoked_at IS NULL
                AND auth.valid_from <= now()
                AND auth.valid_until > now()
              ORDER BY auth.created_at DESC
              LIMIT 1
            ) z ON true
            WHERE a.state IN ('active', 'challenge_required')
              AND EXISTS (
                SELECT 1
                FROM platform.browser_profile profile
                WHERE profile.account_id = a.id AND profile.state = 'ACTIVE'
              )
            ORDER BY a.created_at
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    scopes = json.loads(row[2])
    if not isinstance(scopes, list) or not scopes or not isinstance(scopes[0], str):
        raise RuntimeError("authorized_scope_unavailable")
    return row[0], row[1], scopes[0]


def _create_synthetic_account(dsn: str) -> tuple[str, str, str, str | None]:
    suffix = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    with psycopg.connect(dsn) as connection:
        adapter = connection.execute(
            "SELECT id FROM platform.platform_adapter ORDER BY slug LIMIT 1"
        ).fetchone()
        synthetic_adapter_pub_id = None
        if adapter is None:
            adapter_id = uuid.uuid4()
            synthetic_adapter_pub_id = f"pad_cert_{suffix}"
            connection.execute(
                """
                INSERT INTO platform.platform_adapter (
                  id, pub_id, slug, display_name, admission_level,
                  capabilities_json, adapter_version
                )
                VALUES (%s, %s, %s, 'S04 lifecycle certification',
                        'synthetic', '["query"]', 'certification-only')
                """,
                (adapter_id, synthetic_adapter_pub_id, f"cert-{suffix}"),
            )
            adapter = (adapter_id,)
        connection.execute(
            """
            INSERT INTO platform.tenant (id, pub_id, name, state, created_at, updated_at)
            VALUES (%s, %s, %s, 'active', %s, %s)
            """,
            (tenant_id, f"tnt_cert_{suffix}", "S04 lifecycle certification", now, now),
        )
        connection.execute(
            """
            INSERT INTO platform.platform_account (
              id, pub_id, tenant_id, adapter_id, owner_pub_id, account_mask,
              purpose, responsible_pub_id, custody_mode, region, state,
              admission_level, version, created_at, updated_at
            )
            VALUES (
              %s, %s, %s, %s, %s, 'synthetic-certification', 'certification',
              %s, 'server_vault', 'isolated', 'active', 'synthetic',
              1, %s, %s
            )
            """,
            (
                account_id,
                f"pac_cert_{suffix}",
                tenant_id,
                adapter[0],
                f"usr_cert_{suffix}",
                f"usr_cert_{suffix}",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.account_authorization (
              id, pub_id, tenant_id, account_id, scopes_json,
              forbidden_actions_json, regions_json, valid_from, valid_until,
              version, created_at, updated_at
            )
            VALUES (
              %s, %s, %s, %s, '["query"]', '[]', '["isolated"]',
              %s, %s + interval '1 hour', 1, %s, %s
            )
            """,
            (
                uuid.uuid4(),
                f"aat_cert_{suffix}",
                tenant_id,
                account_id,
                now,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.browser_profile (
              id, pub_id, tenant_id, account_id, profile_version, custody_mode,
              state, constraints_json, version, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, 1, 'server_vault', 'ACTIVE', '[]', 1, %s, %s)
            """,
            (
                uuid.uuid4(),
                f"bpf_cert_{suffix}",
                tenant_id,
                account_id,
                now,
                now,
            ),
        )
    return f"tnt_cert_{suffix}", f"pac_cert_{suffix}", "query", synthetic_adapter_pub_id


def _delete_synthetic_tenant(dsn: str, tenant_pub_id: str, adapter_pub_id: str | None) -> None:
    with psycopg.connect(dsn) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id = %s FOR UPDATE",
            (tenant_pub_id,),
        ).fetchone()
        if tenant is None:
            return
        for table in (
            "session_lease",
            "browser_profile",
            "account_authorization",
            "platform_account",
        ):
            connection.execute(
                f"DELETE FROM platform.{table} WHERE tenant_id = %s",  # noqa: S608
                (tenant[0],),
            )
        connection.execute("DELETE FROM platform.tenant WHERE id = %s", (tenant[0],))
        if adapter_pub_id:
            connection.execute(
                "DELETE FROM platform.platform_adapter WHERE pub_id = %s",
                (adapter_pub_id,),
            )


async def main() -> None:
    dsn = _postgres_dsn()
    eligible = _eligible_account(dsn)
    synthetic_fixture = eligible is None
    if eligible:
        tenant_pub_id, account_pub_id, scope = eligible
        synthetic_adapter_pub_id = None
    else:
        (
            tenant_pub_id,
            account_pub_id,
            scope,
            synthetic_adapter_pub_id,
        ) = _create_synthetic_account(dsn)
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    workflow_id = f"platform-session/certification-{uuid.uuid4().hex}"
    try:
        handle = await client.start_workflow(
            PlatformSessionLifecycleWorkflow.run,
            SessionLifecycleInput(
                tenant_pub_id=tenant_pub_id,
                account_pub_id=account_pub_id,
                scope=scope,
                holder=workflow_id,
                challenge_required=True,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        nonce = uuid.uuid4().hex
        await handle.signal(PlatformSessionLifecycleWorkflow.intervention_completed, nonce)
        # A duplicate delivery must not alter the accepted verification.
        await handle.signal(PlatformSessionLifecycleWorkflow.intervention_completed, nonce)
        result = await handle.result()

        with psycopg.connect(dsn) as connection:
            lease = connection.execute(
                """
                SELECT released_at IS NOT NULL, fencing_token, profile_id
                FROM platform.session_lease
                WHERE pub_id = %s
                """,
                (result.lease_pub_id,),
            ).fetchone()
    finally:
        if synthetic_fixture:
            _delete_synthetic_tenant(dsn, tenant_pub_id, synthetic_adapter_pub_id)
    assertions = {
        "workflow_completed": result.state == "completed",
        "challenge_verified": result.intervention_verified is True,
        "result_declares_release": result.lease_released is True,
        "lease_exists": lease is not None,
        "lease_released_in_postgres": lease is not None and lease[0] is True,
        "fencing_token_matches": lease is not None and lease[1] == result.fencing_token,
        "profile_version_positive": result.profile_version > 0,
    }
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
        "account_pub_id_sha256": hashlib.sha256(account_pub_id.encode()).hexdigest(),
        "scope": scope,
        "synthetic_fixture": synthetic_fixture,
        "workflow_state": result.state,
        "profile_version": result.profile_version,
        "assertions": assertions,
        "sensitive_values_recorded": False,
        "qualification": (
            "This certifies the production Temporal, authorization Activity and fenced lease path. "
            "A synthetic isolated account was used when no authorized customer profile existed; "
            "live customer account custody remains an external admission gate."
        ),
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n")
    if evidence["result"] != "passed":
        raise RuntimeError("production_session_lifecycle_certification_failed")
    print(json.dumps({"result": "passed", "assertions": len(assertions)}))


if __name__ == "__main__":
    asyncio.run(main())
