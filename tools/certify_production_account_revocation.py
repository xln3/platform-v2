from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from certify_production_vault import create_key
from cryptography.exceptions import InvalidTag
from geo_platform.collection.vault import (
    KmsUnavailableError,
    ProfileVault,
    SealedProfile,
    VaultTransitKms,
    profile_aad,
)
from geo_platform.config import get_settings
from provision_production_vault import restricted_token
from temporalio.client import Client

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-account-revocation.json"
RUNTIME_TOKEN = Path("/etc/geo-platform-v2/vault-runtime-token")
PROVISION_TOKEN = Path("/etc/geo-platform-v2/vault-provision-token")
DELETION_TOKEN = Path("/etc/geo-platform-v2/vault-deletion-token")
VAULT_ADDRESS = "https://127.0.0.1:18200"
KEY_PREFIX = "geo-platform-profile"


def _dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "").replace("postgresql+psycopg://", "postgresql://")
    if not value:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    return value


def _insert_fixture(dsn: str, sealed: SealedProfile, suffix: str) -> tuple[str, str, str]:
    tenant_id, account_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenant_pub_id, account_pub_id = f"tnt_revoke_{suffix}", f"pac_revoke_{suffix}"
    adapter_pub_id = f"pad_revoke_{suffix}"
    now = datetime.now(UTC)
    with psycopg.connect(dsn) as connection:
        adapter_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO platform.platform_adapter (
              id, pub_id, slug, display_name, admission_level,
              capabilities_json, adapter_version
            ) VALUES (%s, %s, %s, 'S04 revocation certification',
                      'synthetic', '["query"]', 'certification-only')
            """,
            (adapter_id, adapter_pub_id, f"revoke-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO platform.tenant (id, pub_id, name, state, created_at, updated_at)
            VALUES (%s, %s, 'S04 revocation certification', 'active', %s, %s)
            """,
            (tenant_id, tenant_pub_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO platform.platform_account (
              id, pub_id, tenant_id, adapter_id, owner_pub_id, account_mask,
              purpose, responsible_pub_id, custody_mode, region, state,
              admission_level, version, created_at, updated_at
            ) VALUES (
              %s, %s, %s, %s, %s, 'synthetic-revocation', 'certification',
              %s, 'server', 'isolated', 'active', 'synthetic', 1, %s, %s
            )
            """,
            (
                account_id,
                account_pub_id,
                tenant_id,
                adapter_id,
                f"usr_revoke_{suffix}",
                f"usr_revoke_{suffix}",
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
            ) VALUES (
              %s, %s, %s, %s, '["query"]', '[]', '["isolated"]',
              %s, %s, 1, %s, %s
            )
            """,
            (
                uuid.uuid4(),
                f"aat_revoke_{suffix}",
                tenant_id,
                account_id,
                now,
                now + timedelta(hours=1),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.browser_profile (
              id, pub_id, tenant_id, account_id, profile_version, custody_mode,
              state, constraints_json, ciphertext, nonce, wrapped_dek,
              ciphertext_sha256, version, created_at, updated_at
            ) VALUES (
              %s, %s, %s, %s, 1, 'server', 'ACTIVE', '[]',
              %s, %s, %s, %s, 1, %s, %s
            )
            """,
            (
                profile_id,
                f"bpf_revoke_{suffix}",
                tenant_id,
                account_id,
                sealed.ciphertext,
                sealed.nonce,
                sealed.wrapped_dek,
                sealed.sha256,
                now,
                now,
            ),
        )
        workflow_id = f"account-revocation/{tenant_pub_id}/{account_pub_id}"
        connection.execute(
            """
            INSERT INTO platform.revocation_request (
              id, pub_id, tenant_id, account_id, state, reason, workflow_id,
              version, created_at, updated_at
            ) VALUES (
              %s, %s, %s, %s, 'starting', 'certification', %s, 1, %s, %s
            )
            """,
            (
                uuid.uuid4(),
                f"rev_revoke_{suffix}",
                tenant_id,
                account_id,
                workflow_id,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO integration.workflow_start_command (
              command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload
            ) VALUES (%s,%s,'account_revocation',%s,%s,%s)
            """,
            (
                uuid.uuid4(),
                tenant_pub_id,
                workflow_id,
                get_settings().temporal_task_queue,
                json.dumps(
                    {
                        "tenant_pub_id": tenant_pub_id,
                        "account_pub_id": account_pub_id,
                        "profile_versions": [1],
                    }
                ),
            ),
        )
    return tenant_pub_id, account_pub_id, adapter_pub_id


def _cleanup(dsn: str, tenant_pub_id: str, adapter_pub_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id = %s", (tenant_pub_id,)
        ).fetchone()
        if tenant:
            connection.execute(
                """
                DELETE FROM integration.workflow_start_command
                WHERE tenant_pub_id=%s
                """,
                (tenant_pub_id,),
            )
            for table in (
                "session_event",
                "revocation_request",
                "browser_profile",
                "account_authorization",
                "platform_account",
            ):
                connection.execute(
                    f"DELETE FROM platform.{table} WHERE tenant_id = %s",  # noqa: S608
                    (tenant[0],),
                )
            connection.execute("DELETE FROM platform.tenant WHERE id = %s", (tenant[0],))
        connection.execute(
            "DELETE FROM platform.platform_adapter WHERE pub_id = %s", (adapter_pub_id,)
        )


async def main() -> None:
    dsn = _dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, account_pub_id = f"tnt_revoke_{suffix}", f"pac_revoke_{suffix}"
    aad = profile_aad(
        tenant_pub_id,
        f"usr_revoke_{suffix}",
        f"revoke-{suffix}",
        account_pub_id,
        1,
    )
    runtime = VaultTransitKms(VAULT_ADDRESS, str(RUNTIME_TOKEN), KEY_PREFIX)
    deletion = VaultTransitKms(VAULT_ADDRESS, str(DELETION_TOKEN), KEY_PREFIX)
    provision_token = restricted_token(PROVISION_TOKEN)
    key_name = runtime.account_key_name(tenant_pub_id, account_pub_id)
    create_key(VAULT_ADDRESS, provision_token, key_name)
    sealed = ProfileVault(runtime).seal(b'{"synthetic":"revocation-certification"}', aad)
    created_tenant = ""
    adapter_pub_id = ""
    try:
        created_tenant, created_account, adapter_pub_id = _insert_fixture(dsn, sealed, suffix)
        settings = get_settings()
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        workflow_id = f"account-revocation/{created_tenant}/{created_account}"
        command = None
        for _ in range(120):
            with psycopg.connect(dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,attempts,last_error_code,temporal_run_id,
                           terminal_status
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            if command is not None and command[0] == "started":
                break
            await asyncio.sleep(0.25)
        result = await client.get_workflow_handle(workflow_id).result()
        for _ in range(160):
            with psycopg.connect(dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,attempts,last_error_code,temporal_run_id,
                           terminal_status
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            if command is not None and command[4] == "COMPLETED":
                break
            await asyncio.sleep(0.25)
        with psycopg.connect(dsn) as connection:
            state = connection.execute(
                """
                SELECT a.state, p.state, p.ciphertext IS NULL, p.wrapped_dek IS NULL,
                       r.state, r.deletion_verified_at IS NOT NULL
                FROM platform.platform_account a
                JOIN platform.browser_profile p ON p.account_id = a.id
                JOIN platform.revocation_request r ON r.account_id = a.id
                WHERE a.pub_id = %s
                """,
                (created_account,),
            ).fetchone()
        deleted_ciphertext_rejected = False
        try:
            ProfileVault(runtime).open(sealed, aad)
        except (InvalidTag, KmsUnavailableError):
            deleted_ciphertext_rejected = True
        create_key(VAULT_ADDRESS, provision_token, key_name)
        recreated_ciphertext_rejected = False
        try:
            ProfileVault(runtime).open(sealed, aad)
        except (InvalidTag, KmsUnavailableError):
            recreated_ciphertext_rejected = True
        assertions = {
            "workflow_completed": result["deletion_verified"] is True,
            "outbox_command_started": command is not None and command[0] == "started",
            "outbox_single_attempt": command is not None and command[1] == 1,
            "outbox_has_no_error": command is not None and command[2] is None,
            "outbox_temporal_run_id_persisted": command is not None and bool(command[3]),
            "outbox_terminal_reconciled": command is not None and command[4] == "COMPLETED",
            "database_account_revoked": state is not None and state[0] == "revoked",
            "database_profile_purged": state is not None and state[1] == "PURGED",
            "database_profile_material_erased": state is not None and state[2] and state[3],
            "request_completed": state is not None and state[4] == "completed" and state[5],
            "deleted_key_rejects_retained_ciphertext": deleted_ciphertext_rejected,
            "same_name_recreation_still_rejects_ciphertext": recreated_ciphertext_rejected,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
            "qualification": (
                "Production Temporal/PostgreSQL/Vault mechanics only. This is not an authorized "
                "customer profile, backup-propagation approval or independent custody proof."
            ),
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n")
        if evidence["result"] != "passed":
            raise RuntimeError("production_account_revocation_certification_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        try:
            deletion.destroy_account_key(tenant_pub_id, account_pub_id)
        except KmsUnavailableError:
            pass
        if created_tenant:
            _cleanup(dsn, created_tenant, adapter_pub_id)


if __name__ == "__main__":
    asyncio.run(main())
