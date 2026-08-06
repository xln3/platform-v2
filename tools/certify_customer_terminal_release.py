from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import stat
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).parents[1]
SOURCE_MANIFEST = ROOT / "packages/customer-terminal-extension/manifest.json"
EVIDENCE = ROOT / "tests/s04-evidence/customer-terminal-extension-release.json"
PRODUCTION_BASE = os.environ.get("GEO_PRODUCTION_BASE_URL", "https://127.0.0.1:8443").rstrip("/")
SIGNING_KEY = Path(
    os.environ.get(
        "GEO_EXTENSION_SIGNING_KEY",
        "/etc/geo-platform-v2/extension-signing/customer-terminal.pem",
    )
)
SIGNING_BACKUP = Path(
    os.environ.get(
        "GEO_EXTENSION_SIGNING_BACKUP",
        str(
            ROOT
            / ".production-backups/20260725T020500Z"
            / "customer-terminal-extension-signing.tar"
        ),
    )
)


def get(path: str) -> bytes:
    context = ssl.create_default_context()
    if os.environ.get("GEO_PRODUCTION_TLS_VERIFY", "0") == "0":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        f"{PRODUCTION_BASE}/platform/customer-terminal-extension/{path}",
        headers={"User-Agent": "geo-s04-release-certifier/1"},
    )
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"production_artifact_http_{response.status}")
        return response.read()


def extension_id(public_key: str) -> str:
    prefix = hashlib.sha256(base64.b64decode(public_key, validate=True)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(character, 16)) for character in prefix)


def protobuf_fields(payload: bytes) -> list[tuple[int, int, bytes | int]]:
    fields: list[tuple[int, int, bytes | int]] = []
    offset = 0

    def varint() -> int:
        nonlocal offset
        value = 0
        shift = 0
        while offset < len(payload):
            byte = payload[offset]
            offset += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 63:
                break
        raise ValueError("invalid_crx_protobuf_varint")

    while offset < len(payload):
        tag = varint()
        number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 0:
            fields.append((number, wire_type, varint()))
        elif wire_type == 2:
            length = varint()
            end = offset + length
            if end > len(payload):
                raise ValueError("invalid_crx_protobuf_length")
            fields.append((number, wire_type, payload[offset:end]))
            offset = end
        else:
            raise ValueError(f"unsupported_crx_protobuf_wire_type_{wire_type}")
    return fields


def crx3_signature_valid(crx: bytes, expected_public_key: bytes) -> bool:
    if crx[:4] != b"Cr24" or int.from_bytes(crx[4:8], "little") != 3:
        return False
    header_length = int.from_bytes(crx[8:12], "little")
    header_end = 12 + header_length
    if header_end > len(crx):
        return False
    header_fields = protobuf_fields(crx[12:header_end])
    proof_payload = next(
        value
        for number, wire_type, value in header_fields
        if number == 2 and wire_type == 2 and isinstance(value, bytes)
    )
    signed_header = next(
        value
        for number, wire_type, value in header_fields
        if number == 10000 and wire_type == 2 and isinstance(value, bytes)
    )
    proof_fields = protobuf_fields(proof_payload)
    proof_public_key = next(
        value
        for number, wire_type, value in proof_fields
        if number == 1 and wire_type == 2 and isinstance(value, bytes)
    )
    signature = next(
        value
        for number, wire_type, value in proof_fields
        if number == 2 and wire_type == 2 and isinstance(value, bytes)
    )
    if proof_public_key != expected_public_key:
        return False
    public_key = serialization.load_der_public_key(proof_public_key)
    if not isinstance(public_key, rsa.RSAPublicKey):
        return False
    signed_payload = (
        b"CRX3 SignedData\x00"
        + len(signed_header).to_bytes(4, "little")
        + signed_header
        + crx[header_end:]
    )
    public_key.verify(signature, signed_payload, padding.PKCS1v15(), hashes.SHA256())
    return True


def main() -> None:
    source_manifest: dict[str, Any] = json.loads(SOURCE_MANIFEST.read_text())
    manifest: dict[str, Any] = json.loads(get("manifest.json"))
    remote_release: dict[str, Any] = json.loads(get("release.json"))
    remote_crx = get("customer-terminal-extension.crx")
    key_stat = SIGNING_KEY.stat()
    backup_stat = SIGNING_BACKUP.stat()
    private_key = serialization.load_pem_private_key(SIGNING_KEY.read_bytes(), password=None)
    signing_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    manifest_public_key = base64.b64decode(manifest["key"], validate=True)
    private_key_safe = (
        key_stat.st_uid == 0 and key_stat.st_gid == 0 and stat.S_IMODE(key_stat.st_mode) == 0o600
    )
    crx_hash = hashlib.sha256(remote_crx).hexdigest()
    expected_id = extension_id(manifest["key"])
    assertions = {
        "manifest_v3": manifest["manifest_version"] == 3,
        "source_manifest_matches_production": manifest == source_manifest,
        "stable_extension_id": expected_id == remote_release["extension_id"],
        "production_release_version_matches_manifest": (
            remote_release["version"] == manifest["version"]
        ),
        "production_crx3_header": remote_crx[:4] == b"Cr24"
        and int.from_bytes(remote_crx[4:8], "little") == 3,
        "production_crx_hash_matches": crx_hash == remote_release["crx_sha256"],
        "production_crx_size_matches": len(remote_crx) == remote_release["crx_bytes"],
        "manifest_public_key_matches_private_key": manifest_public_key == signing_public_key,
        "production_crx_signature_valid": crx3_signature_valid(remote_crx, manifest_public_key),
        "signing_private_key_root_only": private_key_safe,
        "signing_backup_root_only": backup_stat.st_uid == 0
        and backup_stat.st_gid == 0
        and stat.S_IMODE(backup_stat.st_mode) == 0o600,
        "signing_private_key_not_in_crx": b"PRIVATE KEY" not in remote_crx,
    }
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "scope": (
            "production managed CRX artifact mechanics; not an authorized customer "
            "installation or native-platform challenge canary"
        ),
        "release": remote_release,
        "signing_backup": {
            "path": str(SIGNING_BACKUP.relative_to(ROOT)),
            "bytes": backup_stat.st_size,
            "sha256": hashlib.sha256(SIGNING_BACKUP.read_bytes()).hexdigest(),
        },
        "assertions": assertions,
        "sensitive_values_recorded": False,
    }
    EVIDENCE.write_text(json.dumps(result, indent=2) + "\n")
    if result["result"] != "passed":
        raise RuntimeError("customer_terminal_release_certification_failed")
    print(json.dumps({"result": result["result"], "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
