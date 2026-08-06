import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..config import Settings

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str, *, length: int) -> bytes:
    if not value or "=" in value or any(character.isspace() for character in value):
        raise ValueError("invalid_base64url")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_base64url") from exc
    if len(decoded) != length or b64url_encode(decoded) != value:
        raise ValueError("invalid_base64url")
    return decoded


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def public_key_from_text(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(b64url_decode(value, length=32))


def verify_signature(public_key: Ed25519PublicKey, signature: str, payload: bytes) -> None:
    try:
        public_key.verify(b64url_decode(signature, length=64), payload)
    except InvalidSignature as exc:
        raise ValueError("signature_invalid") from exc


def public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes_raw()


def fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


def normalize_allowed_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if not _HOSTNAME.fullmatch(domain):
        raise ValueError("allowed_domain must be an ASCII hostname")
    return domain


def _read_private_key(path_text: str) -> bytes:
    path = Path(path_text)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("terminal_signing_key_invalid")
    if info.st_mode & 0o077:
        raise ValueError("terminal_signing_key_permissions")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(descriptor, 256)
        if os.read(descriptor, 1):
            raise ValueError("terminal_signing_key_invalid")
    finally:
        os.close(descriptor)
    return b64url_decode(raw.decode("ascii").strip(), length=32)


def task_signing_key(settings: Settings) -> Ed25519PrivateKey:
    if settings.terminal_task_signing_key_file:
        return Ed25519PrivateKey.from_private_bytes(
            _read_private_key(settings.terminal_task_signing_key_file)
        )
    if settings.env.lower() in {"production", "prod"}:
        raise ValueError("terminal_signing_key_unavailable")
    seed = hashlib.sha256(
        b"geo-development-terminal-task-key\x00" + settings.kms_master_key.encode()
    ).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)
