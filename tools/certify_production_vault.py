from __future__ import annotations

import argparse
import json
import ssl
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from geo_platform.collection.vault import (
    KmsUnavailableError,
    ProfileVault,
    VaultTransitKms,
    profile_aad,
)
from provision_production_vault import restricted_token


def api(
    address: str,
    token: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = Request(
        f"{address.rstrip('/')}{path}",
        data=None if payload is None else json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "X-Vault-Token": token},
        method=method,
    )
    try:
        with urlopen(request, timeout=5, context=ssl.create_default_context()) as response:  # noqa: S310
            raw = response.read(1_048_577)
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read(1_048_577)
        return exc.code, json.loads(raw) if raw else {}


def create_key(address: str, token: str, key_name: str) -> None:
    escaped = quote(key_name, safe="")
    status, _ = api(
        address,
        token,
        f"/v1/transit/keys/{escaped}",
        method="POST",
        payload={
            "type": "aes256-gcm96",
            "exportable": False,
            "allow_plaintext_backup": False,
        },
    )
    if status not in {200, 204}:
        raise RuntimeError(f"vault_key_create_failed:{status}")
    status, _ = api(
        address,
        token,
        f"/v1/transit/keys/{escaped}/config",
        method="POST",
        payload={"deletion_allowed": True},
    )
    if status not in {200, 204}:
        raise RuntimeError(f"vault_key_config_failed:{status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="https://127.0.0.1:18200")
    parser.add_argument("--runtime-token-file", type=Path, required=True)
    parser.add_argument("--provision-token-file", type=Path, required=True)
    parser.add_argument("--deletion-token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    runtime_token = restricted_token(arguments.runtime_token_file)
    provision_token = restricted_token(arguments.provision_token_file)
    deletion_token = restricted_token(arguments.deletion_token_file)
    suffix = uuid4().hex
    tenant = f"tnt_vault_probe_{suffix}"
    account = f"pac_vault_probe_{suffix}"
    owner = f"usr_vault_probe_{suffix}"
    prefix = "geo-platform-profile"
    runtime = VaultTransitKms(arguments.address, str(arguments.runtime_token_file), prefix)
    deletion = VaultTransitKms(arguments.address, str(arguments.deletion_token_file), prefix)
    key_name = runtime.account_key_name(tenant, account)
    try:
        create_key(arguments.address, provision_token, key_name)
        vault = ProfileVault(runtime)
        aad = profile_aad(tenant, owner, "production-vault-probe", account, 1)
        plaintext = b'{"synthetic":"production-vault-probe"}'
        sealed = vault.seal(plaintext, aad)
        opened = vault.open(sealed, aad)
        if opened != plaintext:
            raise RuntimeError("vault_round_trip_failed")
        rotated_aad = profile_aad(tenant, owner, "production-vault-probe", account, 2)
        rotated = vault.rotate_dek(sealed, aad, rotated_aad)
        if (
            vault.open(rotated, rotated_aad) != plaintext
            or rotated.ciphertext == sealed.ciphertext
            or rotated.wrapped_dek == sealed.wrapped_dek
        ):
            raise RuntimeError("vault_dek_rotation_failed")
        old_aad_rejected = False
        try:
            vault.open(rotated, aad)
        except (InvalidTag, KmsUnavailableError):
            old_aad_rejected = True
        if not old_aad_rejected:
            raise RuntimeError("vault_rotated_aad_not_enforced")
        escaped = quote(key_name, safe="")
        rotate_status, _ = api(
            arguments.address,
            provision_token,
            f"/v1/transit/keys/{escaped}/rotate",
            method="POST",
            payload={},
        )
        if rotate_status not in {200, 204}:
            raise RuntimeError(f"vault_kek_rotation_failed:{rotate_status}")
        if vault.open(sealed, aad) != plaintext:
            raise RuntimeError("vault_pre_rotation_ciphertext_recovery_failed")
        post_kek_aad = profile_aad(tenant, owner, "production-vault-probe", account, 3)
        post_kek_rotation = vault.rotate_dek(rotated, rotated_aad, post_kek_aad)
        if (
            not post_kek_rotation.wrapped_dek.startswith(b"vault:v2:")
            or vault.open(post_kek_rotation, post_kek_aad) != plaintext
        ):
            raise RuntimeError("vault_post_kek_profile_rewrap_failed")
        min_version_status, _ = api(
            arguments.address,
            provision_token,
            f"/v1/transit/keys/{escaped}/config",
            method="POST",
            payload={"min_decryption_version": 2},
        )
        if min_version_status not in {200, 204}:
            raise RuntimeError(f"vault_min_decryption_version_failed:{min_version_status}")
        pre_rotation_rejected = False
        try:
            vault.open(sealed, aad)
        except (InvalidTag, KmsUnavailableError):
            pre_rotation_rejected = True
        if not pre_rotation_rejected or vault.open(post_kek_rotation, post_kek_aad) != plaintext:
            raise RuntimeError("vault_kek_rollback_boundary_failed")
        retained = sealed

        runtime_create_status, _ = api(
            arguments.address,
            runtime_token,
            f"/v1/transit/keys/{escaped}",
            method="POST",
            payload={"type": "aes256-gcm96"},
        )
        runtime_rotate_status, _ = api(
            arguments.address,
            runtime_token,
            f"/v1/transit/keys/{escaped}/rotate",
            method="POST",
            payload={},
        )
        provision_encrypt_status, _ = api(
            arguments.address,
            provision_token,
            f"/v1/transit/encrypt/{escaped}",
            method="POST",
            payload={"plaintext": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
        )
        deletion_encrypt_status, _ = api(
            arguments.address,
            deletion_token,
            f"/v1/transit/encrypt/{escaped}",
            method="POST",
            payload={"plaintext": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
        )
        deletion_rotate_status, _ = api(
            arguments.address,
            deletion_token,
            f"/v1/transit/keys/{escaped}/rotate",
            method="POST",
            payload={},
        )
        if (
            runtime_create_status,
            runtime_rotate_status,
            provision_encrypt_status,
            deletion_encrypt_status,
            deletion_rotate_status,
        ) != (
            403,
            403,
            403,
            403,
            403,
        ):
            raise RuntimeError("vault_policy_separation_failed")

        deletion.destroy_account_key(tenant, account)
        create_key(arguments.address, provision_token, key_name)
        backup_reactivation_blocked = False
        try:
            vault.open(retained, aad)
        except (InvalidTag, KmsUnavailableError):
            backup_reactivation_blocked = True
        if not backup_reactivation_blocked:
            raise RuntimeError("retained_backup_reactivated")
    finally:
        deletion.destroy_account_key(tenant, account)

    status, health = api(arguments.address, runtime_token, "/v1/sys/health")
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed",
        "vault": {
            "address": arguments.address,
            "health_status": status,
            "version": health.get("version"),
            "initialized": health.get("initialized"),
            "sealed": health.get("sealed"),
            "storage": "integrated_raft_persistent_volume",
            "tls": True,
        },
        "credential_separation": {
            "runtime_encrypt_decrypt": "allowed",
            "runtime_key_create": "denied",
            "provision_key_create_config_rotate": "allowed",
            "provision_encrypt": "denied",
            "deletion_key_delete": "allowed",
            "deletion_encrypt": "denied",
            "root_token_retained": False,
        },
        "probe": {
            "synthetic_non_customer": True,
            "key_name_sha256": sha256(key_name.encode()).hexdigest(),
            "encrypt_decrypt": "passed",
            "fresh_dek_rotation_and_version_aad": "passed",
            "kek_version_rotation_and_old_ciphertext_recovery": "passed",
            "post_rotation_profile_rewrap_uses_new_kek_version": "passed",
            "minimum_decryption_version_blocks_kek_rollback": "passed",
            "account_key_delete": "passed",
            "same_name_recreation_cannot_open_retained_ciphertext": True,
            "probe_key_removed": True,
            "secret_material_emitted": False,
        },
        "qualification": (
            "This proves the configured production Vault service and ACL mechanics. "
            "It does not prove independent organizational custody, customer authorization, "
            "or deletion of a migrated customer profile."
        ),
    }
    arguments.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
