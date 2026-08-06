from __future__ import annotations

import json
import os
import ssl
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidTag
from geo_platform.collection.vault import (
    KmsUnavailableError,
    ProfileVault,
    VaultTransitKms,
    profile_aad,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "tests/fixtures/vault-integration-config.hcl"
EVIDENCE = ROOT / "tests/s04-evidence/vault-transit-real-integration.json"
IMAGE = "hashicorp/vault:2.0.3"
IMAGE_DIGEST = "sha256:a296a888b118615dc01d5f1a6846e6d4a7277946caaed5b447008fff5fe06b54"
CONTAINER = "geo-vault-s04-integration"
ADDRESS = "https://127.0.0.1:48200"


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def api(
    path: str,
    ca_file: Path,
    *,
    method: str = "GET",
    token: str = "",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    request = Request(
        f"{ADDRESS}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )
    context = ssl.create_default_context(cafile=str(ca_file))
    try:
        with urlopen(request, timeout=2, context=context) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}


def wait_for_vault(ca_file: Path) -> None:
    for _attempt in range(60):
        running = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
            check=False,
            text=True,
            capture_output=True,
        ).stdout.strip()
        if running == "false":
            logs = subprocess.run(
                ["docker", "logs", "--tail", "20", CONTAINER],
                check=False,
                text=True,
                capture_output=True,
            )
            diagnostic = (logs.stdout + logs.stderr)[-2000:].strip()
            raise RuntimeError(f"vault_integration_container_exited: {diagnostic}")
        try:
            status, _body = api("/v1/sys/health", ca_file)
            if status in {200, 429, 472, 473, 501, 503}:
                return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError("vault_integration_start_timeout")


def create_account_key(
    ca_file: Path, token: str, kms: VaultTransitKms, tenant: str, account: str
) -> str:
    key_name = kms.account_key_name(tenant, account)
    status, _ = api(
        f"/v1/transit/keys/{key_name}",
        ca_file,
        method="POST",
        token=token,
        payload={"type": "aes256-gcm96", "exportable": False, "allow_plaintext_backup": False},
    )
    if status not in {200, 204}:
        raise RuntimeError("vault_account_key_create_failed")
    status, _ = api(
        f"/v1/transit/keys/{key_name}/config",
        ca_file,
        method="POST",
        token=token,
        payload={"deletion_allowed": True},
    )
    if status not in {200, 204}:
        raise RuntimeError("vault_account_key_config_failed")
    return key_name


def main() -> None:
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with tempfile.TemporaryDirectory(prefix="geo-vault-integration-") as temporary:
        tls_dir = Path(temporary)
        ca_key = tls_dir / "ca.key"
        ca_file = tls_dir / "ca.crt"
        server_key = tls_dir / "server.key"
        server_request = tls_dir / "server.csr"
        server_certificate = tls_dir / "server.crt"
        token_file = tls_dir / "vault-token"
        run("openssl", "genrsa", "-out", str(ca_key), "2048")
        run(
            "openssl",
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "1",
            "-subj",
            "/CN=GEO Vault Integration CA",
            "-out",
            str(ca_file),
        )
        run("openssl", "genrsa", "-out", str(server_key), "2048")
        run(
            "openssl",
            "req",
            "-new",
            "-key",
            str(server_key),
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-out",
            str(server_request),
        )
        run(
            "openssl",
            "x509",
            "-req",
            "-in",
            str(server_request),
            "-CA",
            str(ca_file),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-days",
            "1",
            "-sha256",
            "-copy_extensions",
            "copy",
            "-out",
            str(server_certificate),
        )
        tls_dir.chmod(0o700)
        for public_file in (ca_file, server_certificate):
            public_file.chmod(0o644)
        server_key.chmod(0o600)
        # Grant only the container's fixed Vault uid access to the ephemeral
        # TLS directory. No group/other read bit is added to private keys.
        run("setfacl", "-m", "u:100:rx", str(tls_dir))
        run("setfacl", "-m", "u:100:r", str(server_key))
        run("setfacl", "-m", "u:100:r", str(server_certificate))
        try:
            run(
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER,
                "--entrypoint",
                "vault",
                "-p",
                "127.0.0.1:48200:8200",
                "-v",
                f"{CONFIG}:/vault/config/integration.hcl:ro",
                "-v",
                f"{tls_dir}:/vault/tls:ro",
                IMAGE,
                "server",
                "-config=/vault/config/integration.hcl",
            )
            wait_for_vault(ca_file)
            status, initialized = api(
                "/v1/sys/init",
                ca_file,
                method="POST",
                payload={"secret_shares": 1, "secret_threshold": 1},
            )
            if status != 200:
                raise RuntimeError("vault_init_failed")
            root_token = initialized["root_token"]
            unseal_key = initialized["keys_base64"][0]
            status, _ = api(
                "/v1/sys/unseal",
                ca_file,
                method="POST",
                payload={"key": unseal_key},
            )
            if status != 200:
                raise RuntimeError("vault_unseal_failed")
            status, _ = api(
                "/v1/sys/mounts/transit",
                ca_file,
                method="POST",
                token=root_token,
                payload={"type": "transit"},
            )
            if status not in {200, 204}:
                raise RuntimeError("vault_transit_enable_failed")
            token_file.write_text(root_token, encoding="utf-8")
            token_file.chmod(0o600)
            os.environ["SSL_CERT_FILE"] = str(ca_file)
            kms = VaultTransitKms(ADDRESS, str(token_file), "geo-profile")
            first_key = create_account_key(ca_file, root_token, kms, "tnt_a", "pac_a")
            second_key = create_account_key(ca_file, root_token, kms, "tnt_a", "pac_b")
            vault = ProfileVault(kms)
            first_aad = profile_aad("tnt_a", "usr_a", "fixed", "pac_a", 1)
            second_aad = profile_aad("tnt_a", "usr_a", "fixed", "pac_b", 1)
            first = vault.seal(b'{"synthetic":"account-a"}', first_aad)
            second = vault.seal(b'{"synthetic":"account-b"}', second_aad)
            assert vault.open(first, first_aad) == b'{"synthetic":"account-a"}'
            assert vault.open(second, second_aad) == b'{"synthetic":"account-b"}'
            retained_backup = first
            kms.destroy_account_key("tnt_a", "pac_a")
            create_account_key(ca_file, root_token, kms, "tnt_a", "pac_a")
            backup_reactivation_blocked = False
            try:
                vault.open(retained_backup, first_aad)
            except (InvalidTag, KmsUnavailableError):
                backup_reactivation_blocked = True
            assert backup_reactivation_blocked
            assert vault.open(second, second_aad) == b'{"synthetic":"account-b"}'
            image_id = subprocess.run(
                ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            evidence = {
                "generated_at": datetime.now(UTC).isoformat(),
                "result": "passed",
                "dependency": {
                    "product": "HashiCorp Vault",
                    "version": "2.0.3",
                    "image": IMAGE,
                    "image_manifest_digest": IMAGE_DIGEST,
                    "local_image_id": image_id,
                    "tls": True,
                    "storage": "isolated in-memory integration instance",
                },
                "verification": {
                    "transit_enabled": True,
                    "per_account_keys_distinct": first_key != second_key,
                    "profile_seal_open": True,
                    "account_key_deleted": True,
                    "same_name_recreated_with_new_material": True,
                    "retained_backup_reactivation_blocked": backup_reactivation_blocked,
                    "unrelated_account_still_decrypts": True,
                },
                "production_data_touched": False,
                "secret_material_in_evidence": False,
            }
            EVIDENCE.write_text(f"{json.dumps(evidence, indent=2)}\n", encoding="utf-8")
        finally:
            run("docker", "rm", "-f", CONTAINER)
            for secret_file in (token_file, server_key, ca_key):
                if secret_file.exists():
                    secret_file.write_bytes(b"")


if __name__ == "__main__":
    main()
