from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
EVIDENCE_PATH = Path("tests/s04-evidence/external-gates-audit.json")
TOPOLOGY_PATH = Path("tests/s04-evidence/production-topology-predeploy.json")
IDENTITY_PATH = Path("tests/s04-evidence/production-identity-certification.json")
VAULT_PATH = Path("tests/s04-evidence/production-vault-transit.json")


def environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://")


def scalar(connection: psycopg.Connection[tuple[object, ...]], statement: str) -> int:
    row = connection.execute(statement).fetchone()
    if row is None or not isinstance(row[0], int):
        raise RuntimeError("external_gate_count_unavailable")
    return row[0]


def main() -> None:
    values = environment(ENV_PATH)
    with psycopg.connect(psycopg_dsn(values["GEO_POSTGRES_DSN"])) as connection:
        state = {
            "platform_accounts": scalar(
                connection, "SELECT count(*) FROM platform.platform_account"
            ),
            "browser_profiles": scalar(connection, "SELECT count(*) FROM platform.browser_profile"),
            "active_profile_deks": scalar(
                connection,
                """
                SELECT count(*) FROM platform.browser_profile
                WHERE wrapped_dek IS NOT NULL AND purged_at IS NULL
                """,
            ),
            "platform_adapters": scalar(
                connection, "SELECT count(*) FROM platform.platform_adapter"
            ),
            "currently_authorized_accounts": scalar(
                connection,
                """
                SELECT count(DISTINCT account_id)
                FROM platform.account_authorization
                WHERE revoked_at IS NULL
                  AND valid_from <= now()
                  AND valid_until > now()
                """,
            ),
            "customer_device_bindings": scalar(
                connection, "SELECT count(*) FROM platform.device_binding"
            ),
            "customer_terminal_tasks": scalar(
                connection, "SELECT count(*) FROM platform.terminal_task"
            ),
        }

    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    vault = json.loads(VAULT_PATH.read_text(encoding="utf-8")) if VAULT_PATH.is_file() else {}
    verified_roles = identity.get("final_identity_gates", {}).get("human_roles_verified", [])
    provider = values.get("GEO_KMS_PROVIDER", "unavailable")
    environment_kms_configured = (
        provider == "vault_transit"
        and bool(values.get("GEO_VAULT_TRANSIT_ADDRESS"))
        and bool(values.get("GEO_VAULT_TRANSIT_TOKEN_FILE"))
        and bool(values.get("GEO_VAULT_TRANSIT_KEY_NAME"))
    )
    deployed_vault_certified = (
        vault.get("result") == "passed"
        and vault.get("vault", {}).get("health_status") == 200
        and vault.get("vault", {}).get("sealed") is False
    )
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "external_authorization_required",
        "production_account_state": state,
        "legacy_sensitive_candidates": topology["backup"]["sensitive_profile_manifest_entries"],
        "production_identity_state": {
            "verified_real_roles": verified_roles,
            "required_roles": ["customer", "operator", "analyst", "reviewer", "admin"],
        },
        "external_kms_state": {
            "source_adapter": "vault_transit",
            "configured": environment_kms_configured or deployed_vault_certified,
            "live_mechanics_certified": deployed_vault_certified,
            "independent_organizational_custody_certified": False,
            "evidence": "production-vault-transit.json",
        },
        "missing_authoritative_inputs": [
            "profile owner mapping",
            "platform and account scope mapping",
            "customer/legal authorization",
            "authorized customer terminal",
            "live external-platform credentials",
            "approved capability admission matrix",
            "independently operated KMS/HSM and deletion policy",
        ],
        "prohibited_inferences": [
            "do not infer owner from filesystem paths",
            "do not import or encrypt a profile under a guessed account",
            "do not destroy plaintext before per-account cutover evidence",
            "do not label adapter readiness as live login/read/draft/publish verification",
            "do not migrate passkey, face or device-binding state away from the customer terminal",
            "do not label same-host Vault mechanics as independent organizational custody",
        ],
        "safe_progress_state": {
            "metadata_only_inventory": True,
            "plaintext_imported": False,
            "plaintext_destroyed": False,
            "device_verification_state_migrated": False,
            "live_capability_claimed": False,
            "external_kms_adapter_implemented": True,
            "production_vault_transit_configured": deployed_vault_certified,
            "independent_kms_custody_claimed": False,
            "signed_terminal_protocol_deployed": True,
            "native_customer_terminal_canary_verified": False,
        },
        "secret_material_in_evidence": False,
        "goal_status": "active",
    }
    EVIDENCE_PATH.write_text(f"{json.dumps(evidence, indent=2)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
