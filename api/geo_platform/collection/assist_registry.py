"""Shared access to captcha/OTP assist registry records.

The raw bearer ticket is deliberately absent from this module.  Callers either
hash a legacy ticket before entering here or present a signed notification
capability that already resolves to the stored SHA-256 digest.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any


def _configured_registry_dir(default: Path) -> Path:
    configured = os.getenv("GEO_ASSIST_REGISTRY_DIR", "").strip()
    if not configured:
        return default
    path = Path(configured)
    if not path.is_absolute():
        raise RuntimeError("assist_registry_dir_must_be_absolute")
    return path


DEFAULT_ASSIST_DIR = _configured_registry_dir(
    Path(__file__).resolve().parents[3] / "runtime" / "captcha-assist"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_STATES = frozenset({"active", "solved", "closed"})


class AssistRegistryError(ValueError):
    pass


def registry_path(directory: Path, ticket_sha256: str) -> Path:
    if not _DIGEST_RE.fullmatch(ticket_sha256):
        raise AssistRegistryError("assist_registry_invalid")
    return directory / f"{ticket_sha256}.json"


def load_registry_by_digest(
    directory: Path,
    ticket_sha256: str,
    *,
    require_usable: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    try:
        path = registry_path(directory, ticket_sha256)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AssistRegistryError("assist_registry_invalid") from None
    if not isinstance(value, dict) or value.get("version") != 1:
        raise AssistRegistryError("assist_registry_invalid")
    stored_hash = value.get("ticket_hash")
    if not isinstance(stored_hash, str) or not secrets.compare_digest(stored_hash, ticket_sha256):
        raise AssistRegistryError("assist_registry_invalid")
    expires_at = value.get("expires_at")
    if (
        not isinstance(expires_at, int | float)
        or isinstance(expires_at, bool)
        or not math.isfinite(expires_at)
        or expires_at <= 0
    ):
        raise AssistRegistryError("assist_registry_invalid")
    state = value.get("state")
    if state not in _KNOWN_STATES:
        raise AssistRegistryError("assist_registry_invalid")
    explicit_kind = value.get("session_kind")
    if explicit_kind is not None and explicit_kind not in {"workflow_captcha", "otp_cli"}:
        raise AssistRegistryError("assist_registry_invalid")
    if require_usable and (
        (time.time() if now is None else now) >= expires_at or state not in {"active", "solved"}
    ):
        raise AssistRegistryError("assist_registry_invalid")
    return value


def session_kind(record: dict[str, Any]) -> str:
    explicit = record.get("session_kind")
    if explicit in {"workflow_captcha", "otp_cli"}:
        return str(explicit)
    if explicit is not None:
        raise AssistRegistryError("assist_registry_invalid")
    # Compatibility for records written before session_kind existed.
    run_pub_id = record.get("run_pub_id")
    return "otp_cli" if str(run_pub_id or "").startswith("otp-assist-") else "workflow_captcha"


def write_registry_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def mark_registry_solved(directory: Path, ticket_sha256: str, *, now: int | None = None) -> bool:
    path = registry_path(directory, ticket_sha256)
    try:
        latest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(latest, dict):
        return False
    stored = latest.get("ticket_hash")
    if not isinstance(stored, str) or not secrets.compare_digest(stored, ticket_sha256):
        return False
    latest["state"] = "solved"
    latest["solved_at"] = int(time.time()) if now is None else now
    write_registry_atomic(path, latest)
    return True
