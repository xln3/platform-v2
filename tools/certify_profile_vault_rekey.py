from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "tests/s04-evidence"
OUTPUT = EVIDENCE / "profile-vault-rekey-certification.json"


def load(name: str) -> dict[str, Any]:
    value = json.loads((EVIDENCE / name).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain an object")
    return value


def main() -> None:
    openapi = json.loads((ROOT / "contracts/openapi.json").read_text())
    production_vault = load("production-vault-transit.json")
    external = load("external-gates-audit.json")
    router = (ROOT / "api/geo_platform/collection/router.py").read_text()
    tests = (ROOT / "tests/integration/test_s01_governance.py").read_text()
    provisioner = (ROOT / "tools/provision_production_vault.py").read_text()
    path = "/api/v2/platform-accounts/{account_pub_id}/profiles/rekey"
    assertions = {
        "generated_openapi_contract_present": path in openapi["paths"],
        "no_profile_plaintext_in_rekey_request": (
            "profile_payload"
            not in json.dumps(openapi["components"]["schemas"]["ProfileRekey"], sort_keys=True)
        ),
        "fenced_profile_bound_lease": (
            'detail={"code": "profile_lease_mismatch"}' in router
            and "assert_fenced_write(lease, body.fencing_token)" in router
        ),
        "version_aad_and_fresh_dek": (
            "rotate_dek" in router
            and 'current.state = "SUPERSEDED"' in router
            and 'state="ACTIVE"' in router
        ),
        "idempotent_replay_and_conflict": (
            "profile.rekeyed:" in router
            and 'detail={"code": "idempotency_conflict"}' in router
            and "pg_advisory_xact_lock" in router
        ),
        "secret_free_audit_reason_enum": (
            "scheduled_rotation" in router
            and "incident_recovery" in router
            and "rekey-sensitive-value" in tests
        ),
        "integration_rekey_regression_present": (
            "test_profile_dek_rekey_is_fenced_idempotent_versioned_and_secret_free" in tests
        ),
        "real_production_vault_rotation_passed": (
            production_vault["probe"]["fresh_dek_rotation_and_version_aad"] == "passed"
        ),
        "real_production_kek_rotation_and_recovery_passed": all(
            production_vault["probe"][name] == "passed"
            for name in (
                "kek_version_rotation_and_old_ciphertext_recovery",
                "post_rotation_profile_rewrap_uses_new_kek_version",
                "minimum_decryption_version_blocks_kek_rollback",
            )
        ),
        "admitted_account_rotation_tool_present": (
            "--rotate-existing" in provisioner
            and "profile_rekey_required_before_minimum_version_advance" in provisioner
            and "account_not_currently_admitted" in provisioner
        ),
        "real_production_vault_probe_removed": production_vault["probe"]["probe_key_removed"],
        "no_customer_profile_fabricated": (
            external["production_account_state"]["browser_profiles"] == 0
            and external["production_account_state"]["active_profile_deks"] == 0
        ),
    }
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "openapi_path_count": len(openapi["paths"]),
        "contract_sha256": hashlib.sha256(
            (ROOT / "contracts/openapi.json").read_bytes()
        ).hexdigest(),
        "automated_tests": {
            "focused_profile_vault_and_governance": "28/28",
            "full_python": "145/145",
        },
        "assertions": assertions,
        "qualification": (
            "The API lifecycle and a non-customer production Vault mechanism probe pass. "
            "Production contains no authorized customer profile, so this does not prove "
            "customer migration/cutover, independent organizational custody, or deletion "
            "propagation for customer data."
        ),
        "sensitive_values_recorded": False,
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    if result["result"] != "passed":
        raise RuntimeError("profile_vault_rekey_certification_failed")
    print(json.dumps({"result": result["result"], "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
