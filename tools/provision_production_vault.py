from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import psycopg
from geo_platform.collection.vault import VaultTransitKms


def restricted_token(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        raw = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise RuntimeError("vault_token_permissions_unsafe")
    token = raw.decode("utf-8").strip()
    if not token or len(token) > 4096 or "\n" in token or "\r" in token:
        raise RuntimeError("vault_token_invalid")
    return token


def request(
    address: str,
    token: str,
    path: str,
    *,
    method: str = "POST",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(address)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise RuntimeError("vault_address_invalid")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    call = Request(
        f"{address.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json", "X-Vault-Token": token},
        method=method,
    )
    try:
        with urlopen(call, timeout=5, context=ssl.create_default_context()) as response:  # noqa: S310
            raw = response.read(1_048_577)
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read(1_048_577)
        return exc.code, json.loads(raw) if raw else {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision one non-exportable production Transit key after account admission."
    )
    parser.add_argument("--address", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--key-prefix", default="geo-platform-profile")
    parser.add_argument("--tenant-pub-id", required=True)
    parser.add_argument("--account-pub-id", required=True)
    parser.add_argument(
        "--rotate-existing",
        action="store_true",
        help=(
            "Rotate the admitted account KEK version. Every active profile must then "
            "use the fenced rekey API before independently advancing Vault's "
            "min_decryption_version."
        ),
    )
    arguments = parser.parse_args()

    dsn = os.environ.get("GEO_POSTGRES_DSN", "")
    if not dsn:
        raise RuntimeError("GEO_POSTGRES_DSN is required for admission verification")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)",
            (arguments.tenant_pub_id,),
        )
        admitted = connection.execute(
            """
            SELECT 1
            FROM platform.platform_account account
            JOIN platform.tenant tenant ON tenant.id=account.tenant_id
            WHERE tenant.pub_id=%s
              AND account.pub_id=%s
              AND account.state NOT IN ('revoked','quarantined')
              AND EXISTS (
                SELECT 1
                FROM platform.account_authorization authz
                WHERE authz.account_id=account.id
                  AND authz.revoked_at IS NULL
                  AND authz.valid_from <= %s
                  AND authz.valid_until > %s
              )
            """,
            (
                arguments.tenant_pub_id,
                arguments.account_pub_id,
                datetime.now(UTC),
                datetime.now(UTC),
            ),
        ).fetchone()
    if admitted is None:
        raise RuntimeError("account_not_currently_admitted")

    token = restricted_token(arguments.token_file)
    kms = VaultTransitKms(arguments.address, str(arguments.token_file), arguments.key_prefix)
    key_name = kms.account_key_name(arguments.tenant_pub_id, arguments.account_pub_id)
    escaped = quote(key_name, safe="")
    if arguments.rotate_existing:
        status, _ = request(
            arguments.address,
            token,
            f"/v1/transit/keys/{escaped}/rotate",
            payload={},
        )
        if status not in {200, 204}:
            raise RuntimeError(f"vault_account_key_rotate_failed:{status}")
        print(
            json.dumps(
                {
                    "result": "rotated",
                    "key_name_sha256": hashlib.sha256(key_name.encode()).hexdigest(),
                    "profile_rekey_required_before_minimum_version_advance": True,
                }
            )
        )
        return
    status, _ = request(
        arguments.address,
        token,
        f"/v1/transit/keys/{escaped}",
        payload={
            "type": "aes256-gcm96",
            "exportable": False,
            "allow_plaintext_backup": False,
        },
    )
    if status not in {200, 204}:
        raise RuntimeError(f"vault_account_key_create_failed:{status}")
    status, _ = request(
        arguments.address,
        token,
        f"/v1/transit/keys/{escaped}/config",
        payload={"deletion_allowed": True},
    )
    if status not in {200, 204}:
        raise RuntimeError(f"vault_account_key_config_failed:{status}")
    print(
        json.dumps(
            {
                "result": "provisioned",
                "key_name_sha256": hashlib.sha256(key_name.encode()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
