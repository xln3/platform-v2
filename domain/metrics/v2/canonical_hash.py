"""Canonical JSON V1 used by every immutable V2 metric artifact.

The serializer deliberately accepts only data-shaped values.  In particular it
does not use ``default=str`` because that would make unsupported objects and
process-local representations part of an audit hash.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

CANONICALIZATION_VERSION = "canonical-json-v1"


class CanonicalizationError(TypeError):
    """Raised when a value cannot be represented by canonical-json-v1."""


def _decimal_string(value: Decimal) -> str:
    if not value.is_finite():
        raise CanonicalizationError("non-finite Decimal is not canonical JSON")
    rendered = format(value, "f")
    return "0" if value.is_zero() else rendered


def _datetime_string(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError("canonical datetime must be timezone-aware")
    utc_value = value.astimezone(UTC)
    rendered = utc_value.isoformat(timespec="microseconds")
    return rendered.removesuffix("+00:00") + "Z"


def canonicalize(value: Any) -> Any:
    """Return a JSON-compatible canonical projection of ``value``."""

    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("floats are forbidden; use Decimal")
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, datetime):
        return _datetime_string(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonicalize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical JSON object keys must be strings")
            projected[key] = canonicalize(item)
        return projected
    if isinstance(value, set | frozenset):
        projected_items = [canonicalize(item) for item in value]
        return sorted(projected_items, key=_json_from_projected)
    if isinstance(value, list | tuple):
        return [canonicalize(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical JSON type: {type(value).__name__}")


def _json_from_projected(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json(value: Any) -> str:
    """Serialize using UTF-8 friendly, sorted, whitespace-free canonical JSON."""

    return _json_from_projected(canonicalize(value))


def canonical_hash(value: Any) -> str:
    """Return the lowercase SHA-256 of a canonical JSON value."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_set_hash(values: Iterable[Any]) -> str:
    """Hash an unordered content set after sorting each canonical item."""

    projected = [canonicalize(value) for value in values]
    projected.sort(key=_json_from_projected)
    return canonical_hash(projected)
