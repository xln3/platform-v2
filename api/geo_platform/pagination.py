"""Opaque, scope-bound keyset cursors for operational collection lists.

The cursor is a signed transport token.  Callers must treat it as opaque; the
server binds it to one tenant, endpoint kind, and canonical filter set before
using its immutable ``created_at, pub_id`` anchor.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from fastapi import HTTPException, Response

from .config import get_settings

_CURSOR_VERSION: Final[int] = 1
_CURSOR_TTL: Final[timedelta] = timedelta(hours=24)
_CLOCK_SKEW: Final[timedelta] = timedelta(minutes=5)
_SIGNING_CONTEXT: Final[bytes] = b"geo-operational-keyset-cursor-v1\0"


@dataclass(frozen=True, slots=True)
class KeysetCursor:
    created_at: datetime
    pub_id: str


@dataclass(frozen=True, slots=True)
class NumberedPage:
    page: int
    page_size: int
    total_count: int
    total_pages: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def numbered_page(*, requested_page: int, page_size: int, total_count: int) -> NumberedPage:
    """Normalize a requested page against a current, possibly shrinking result set."""

    total_pages = (total_count + page_size - 1) // page_size
    page = min(requested_page, total_pages) if total_pages else 1
    return NumberedPage(
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
    )


def set_numbered_page_headers(response: Response, page: NumberedPage) -> None:
    response.headers["X-Page"] = str(page.page)
    response.headers["X-Page-Size"] = str(page.page_size)
    response.headers["X-Total-Count"] = str(page.total_count)
    response.headers["X-Page-Count"] = str(page.total_pages)
    response.headers["X-Has-More"] = "true" if page.page < page.total_pages else "false"


def encode_keyset_cursor(
    *,
    kind: str,
    tenant_pub_id: str,
    filters: Mapping[str, str | None],
    created_at: datetime,
    pub_id: str,
    now: datetime | None = None,
) -> str:
    issued_at = _aware_utc(now or datetime.now(UTC))
    anchor_time = _aware_utc(created_at)
    payload = {
        "v": _CURSOR_VERSION,
        "k": kind,
        "s": _scope_digest(tenant_pub_id),
        "f": _filter_digest(filters),
        "c": anchor_time.isoformat(timespec="microseconds"),
        "p": pub_id,
        "i": int(issued_at.timestamp()),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_signing_key(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url(payload_bytes)}.{_b64url(signature)}"


def decode_keyset_cursor(
    token: str,
    *,
    kind: str,
    tenant_pub_id: str,
    filters: Mapping[str, str | None],
    now: datetime | None = None,
) -> KeysetCursor:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload_bytes = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
        expected = hmac.new(_signing_key(), payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise ValueError("shape")
        if payload.get("v") != _CURSOR_VERSION or payload.get("k") != kind:
            raise ValueError("version_or_kind")
        if not hmac.compare_digest(str(payload.get("s", "")), _scope_digest(tenant_pub_id)):
            raise ValueError("scope")
        if not hmac.compare_digest(str(payload.get("f", "")), _filter_digest(filters)):
            raise ValueError("filter")
        issued_at = datetime.fromtimestamp(int(payload["i"]), tz=UTC)
        checked_at = _aware_utc(now or datetime.now(UTC))
        if issued_at > checked_at + _CLOCK_SKEW or checked_at - issued_at > _CURSOR_TTL:
            raise CursorExpiredError
        created_at = _aware_utc(datetime.fromisoformat(str(payload["c"])))
        pub_id = str(payload["p"])
        if not pub_id or len(pub_id) > 128 or not pub_id.isascii():
            raise ValueError("pub_id")
    except CursorExpiredError as exc:
        raise HTTPException(status_code=422, detail={"code": "cursor_expired"}) from exc
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from exc
    return KeysetCursor(created_at=created_at, pub_id=pub_id)


def set_cursor_headers(
    response: Response,
    *,
    next_cursor: str | None,
    has_more: bool,
    total_count: int | None = None,
    extra_counts: Mapping[str, int] | None = None,
) -> None:
    response.headers["X-Has-More"] = "true" if has_more else "false"
    if next_cursor is not None:
        response.headers["X-Next-Cursor"] = next_cursor
    if total_count is not None:
        response.headers["X-Total-Count"] = str(total_count)
    for header, value in (extra_counts or {}).items():
        response.headers[header] = str(value)


class CursorExpiredError(ValueError):
    pass


def _signing_key() -> bytes:
    pepper = get_settings().native_auth_pepper.encode()
    return hmac.new(pepper, _SIGNING_CONTEXT, hashlib.sha256).digest()


def _scope_digest(tenant_pub_id: str) -> str:
    return hmac.new(_signing_key(), tenant_pub_id.encode(), hashlib.sha256).hexdigest()[:32]


def _filter_digest(filters: Mapping[str, str | None]) -> str:
    canonical = json.dumps(dict(filters), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:32]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("cursor_timestamp_must_be_aware")
    return value.astimezone(UTC)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = [
    "KeysetCursor",
    "NumberedPage",
    "decode_keyset_cursor",
    "encode_keyset_cursor",
    "numbered_page",
    "set_cursor_headers",
    "set_numbered_page_headers",
]
