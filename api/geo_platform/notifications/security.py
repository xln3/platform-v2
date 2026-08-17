from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CAP_VERSION = 1


class CallbackSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedCallback:
    payload: dict[str, Any]
    timestamp: int
    nonce: str
    replay_key: str


def actor_hash(open_id: str) -> str:
    return hashlib.sha256(open_id.encode("utf-8")).hexdigest()


def mask_actor(open_id: str) -> str:
    if len(open_id) <= 8:
        return "***"
    return f"{open_id[:4]}…{open_id[-4:]}"


def make_assist_capability(
    *, notification_id: str, ticket_sha256: str, expires_at: int, key: str
) -> str:
    if not _SHA256_RE.fullmatch(ticket_sha256):
        raise ValueError("invalid_ticket_sha256")
    body = bytes([_CAP_VERSION]) + struct.pack(">Q", expires_at) + bytes.fromhex(ticket_sha256)
    signature = hmac.new(
        key.encode("utf-8"), notification_id.encode("utf-8") + b"\0" + body, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(body + signature).rstrip(b"=").decode("ascii")


def verify_assist_capability(
    *, notification_id: str, capability: str, key: str, now: int | None = None
) -> tuple[str, int]:
    if not capability or len(capability) > 160 or _CAPABILITY_RE.fullmatch(capability) is None:
        raise CallbackSecurityError("assist_capability_invalid")
    try:
        padded = capability + "=" * (-len(capability) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise CallbackSecurityError("assist_capability_invalid") from error
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(capability, canonical):
        raise CallbackSecurityError("assist_capability_invalid")
    if len(decoded) != 1 + 8 + 32 + 32 or decoded[0] != _CAP_VERSION:
        raise CallbackSecurityError("assist_capability_invalid")
    body, supplied = decoded[:-32], decoded[-32:]
    expected = hmac.new(
        key.encode("utf-8"), notification_id.encode("utf-8") + b"\0" + body, hashlib.sha256
    ).digest()
    if not secrets.compare_digest(supplied, expected):
        raise CallbackSecurityError("assist_capability_invalid")
    expires_at = struct.unpack(">Q", body[1:9])[0]
    if (int(time.time()) if now is None else now) >= expires_at:
        raise CallbackSecurityError("assist_capability_expired")
    return body[9:41].hex(), expires_at


def callback_signature(*, timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    return hashlib.sha256((timestamp + nonce + encrypt_key).encode("utf-8") + body).hexdigest()


def decrypt_callback(encrypted: str, *, encrypt_key: str) -> bytes:
    try:
        raw = base64.b64decode(encrypted, validate=True)
    except (ValueError, TypeError) as error:
        raise CallbackSecurityError("callback_ciphertext_invalid") from error
    if len(raw) < 32 or len(raw) % 16:
        raise CallbackSecurityError("callback_ciphertext_invalid")
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded = decryptor.update(raw[16:]) + decryptor.finalize()
    try:
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as error:
        raise CallbackSecurityError("callback_ciphertext_invalid") from error


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CallbackSecurityError("callback_json_invalid") from error
    if not isinstance(value, dict):
        raise CallbackSecurityError("callback_json_invalid")
    return value


def verify_callback_request(
    *,
    headers: Mapping[str, str],
    body: bytes,
    encrypt_key: str,
    verification_token: str,
    max_age_seconds: int,
    now: int | None = None,
) -> VerifiedCallback:
    envelope = _json_object(body)
    timestamp_text = headers.get("x-lark-request-timestamp", "")
    nonce = headers.get("x-lark-request-nonce", "")
    supplied_signature = headers.get("x-lark-signature", "")
    has_signature_material = any((timestamp_text, nonce, supplied_signature))
    if has_signature_material:
        if not timestamp_text.isdigit() or not nonce or len(nonce) > 256:
            raise CallbackSecurityError("callback_signature_headers_missing")
        current = int(time.time()) if now is None else now
        timestamp = int(timestamp_text)
        if abs(current - timestamp) > max_age_seconds:
            raise CallbackSecurityError("callback_timestamp_stale")
        expected = callback_signature(
            timestamp=timestamp_text, nonce=nonce, encrypt_key=encrypt_key, body=body
        )
        if not secrets.compare_digest(supplied_signature, expected):
            raise CallbackSecurityError("callback_signature_invalid")
    else:
        timestamp = int(time.time()) if now is None else now
        nonce = "challenge"

    # Authenticate signed ciphertext before attempting AES unpadding. URL
    # verification is the sole unsigned exception and remains state-free plus
    # protected by the independent verification token below.
    encrypted = envelope.get("encrypt")
    plaintext = (
        decrypt_callback(encrypted, encrypt_key=encrypt_key) if isinstance(encrypted, str) else body
    )
    payload = _json_object(plaintext)
    is_challenge = payload.get("type") == "url_verification"
    if not has_signature_material and not is_challenge:
        raise CallbackSecurityError("callback_signature_headers_missing")

    header = payload.get("header")
    token = header.get("token") if isinstance(header, dict) else payload.get("token")
    if not isinstance(token, str) or not secrets.compare_digest(token, verification_token):
        raise CallbackSecurityError("callback_verification_token_invalid")
    replay_key = hashlib.sha256(f"{timestamp}:{nonce}".encode()).hexdigest()
    return VerifiedCallback(
        payload=payload,
        timestamp=timestamp,
        nonce=nonce,
        replay_key=replay_key,
    )
