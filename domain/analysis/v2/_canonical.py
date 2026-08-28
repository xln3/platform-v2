"""Canonical JSON and immutable-model primitives for decision contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from hashlib import sha256
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=2_000)]
OpaqueRef = Annotated[
    str,
    Field(min_length=1, max_length=500, pattern=r"^[A-Za-z0-9][A-Za-z0-9:/@._-]{0,499}$"),
]


class FrozenDomainModel(BaseModel):
    """Strict, shallowly immutable Pydantic base used at domain boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def datetimes_are_aware(cls, value: object) -> object:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime_must_be_timezone_aware")
        return value


def canonical_value(value: object) -> object:
    """Convert supported domain values into stable, JSON-compatible values."""

    if isinstance(value, BaseModel):
        return canonical_value(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime_must_be_timezone_aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical_mapping_keys_must_be_strings")
        return {key: canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [canonical_value(item) for item in value]
    if isinstance(value, set | frozenset):
        normalized = [canonical_value(item) for item in value]
        return sorted(normalized, key=_canonical_sort_key)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical_number_must_be_finite")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported_canonical_type:{type(value).__name__}")


def _canonical_sort_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json(value: object) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def hash_model_payload(value: BaseModel, *, excluded_fields: frozenset[str]) -> str:
    payload: dict[str, Any] = value.model_dump(mode="python", exclude=set(excluded_fields))
    return canonical_hash(payload)


def contains_forbidden_secret(value: object) -> bool:
    """Reject credential-shaped keys from version-controlled policy documents."""

    forbidden = {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in forbidden or normalized.endswith("_secret"):
                return True
            if contains_forbidden_secret(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(contains_forbidden_secret(item) for item in value)
    return False
