"""Read-only v1 collection configuration overlay.

The legacy configuration bytes and hash remain authoritative historical records.
This reader adds only the user-confirmed ``consumer_web`` interpretation; it does
not manufacture v2 targets, province codes, campaigns, slots, or denominators.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .surface import CollectionSurface

LEGACY_CONFIG_READER_SCHEMA_VERSION: Literal["collection-config-v1-web-view"] = (
    "collection-config-v1-web-view"
)
LEGACY_CONTRACT_VERSION: Literal["collection-v1-consumer-web-overlay-20260824"] = (
    "collection-v1-consumer-web-overlay-20260824"
)
HISTORICAL_SURFACE_ASSIGNMENT_BASIS: Literal[
    "authoritative_historical_collection_policy_20260824"
] = "authoritative_historical_collection_policy_20260824"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_CONFIG_KEYS = frozenset(
    {"effective_at", "frequency", "models", "modes", "query_groups", "regions"}
)
_LEGACY_MIGRATION_CONFIG_KEYS = frozenset(
    {
        "cadence",
        "legacy_enabled",
        "migration_activation",
        "models",
        "modes",
        "platforms",
        "query_groups",
        "regions",
    }
)


class LegacyConfigReadError(ValueError):
    """A legacy snapshot cannot be interpreted without guessing."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class LegacyWebModelView(_FrozenModel):
    """A legacy configured model seen on Web, not a canonical v2 target."""

    legacy_model: str = Field(min_length=1, max_length=128)
    collection_surface: Literal[CollectionSurface.CONSUMER_WEB] = CollectionSurface.CONSUMER_WEB
    product_variant: None = None
    capability_revision: None = None


class LegacyCollectionConfigWebView(_FrozenModel):
    """Surface-only overlay for one byte-preserved v1 config revision."""

    schema_version: Literal["collection-config-v1-web-view"] = LEGACY_CONFIG_READER_SCHEMA_VERSION
    legacy_contract_version: Literal["collection-v1-consumer-web-overlay-20260824"] = (
        LEGACY_CONTRACT_VERSION
    )
    surface_assignment_basis: Literal["authoritative_historical_collection_policy_20260824"] = (
        HISTORICAL_SURFACE_ASSIGNMENT_BASIS
    )
    source_snapshot_hash: str = Field(pattern=_SHA256_RE.pattern)
    source_contract_shape: Literal["monitoring-config-v1", "migration-activation-v1"]
    collection_surface: Literal[CollectionSurface.CONSUMER_WEB] = CollectionSurface.CONSUMER_WEB
    models: tuple[LegacyWebModelView, ...]
    interaction_modes: tuple[str, ...]
    legacy_regions: tuple[str, ...]
    campaign_identity_state: Literal["not_available"] = "not_available"
    primary_slot_identity_state: Literal["not_available"] = "not_available"
    configured_denominator_state: Literal["not_available"] = "not_available"

    @field_validator("models")
    @classmethod
    def models_must_be_unique(
        cls, values: tuple[LegacyWebModelView, ...]
    ) -> tuple[LegacyWebModelView, ...]:
        if not values or len(values) != len({item.legacy_model for item in values}):
            raise ValueError("legacy_models_must_be_nonempty_and_unique")
        return values


def read_legacy_config_web_view(
    *,
    snapshot_json: str,
    snapshot_hash: str,
) -> LegacyCollectionConfigWebView:
    """Verify and interpret an exact v1 snapshot without mutating its bytes/hash."""

    if not _SHA256_RE.fullmatch(snapshot_hash):
        raise LegacyConfigReadError("legacy_snapshot_hash_invalid")
    if hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest() != snapshot_hash:
        raise LegacyConfigReadError("legacy_snapshot_hash_mismatch")
    try:
        snapshot = json.loads(
            snapshot_json,
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LegacyConfigReadError("legacy_snapshot_json_invalid") from exc
    if not isinstance(snapshot, dict):
        raise LegacyConfigReadError("legacy_snapshot_schema_unknown")

    keys = frozenset(snapshot)
    if keys == _LEGACY_CONFIG_KEYS:
        source_contract_shape: Literal["monitoring-config-v1", "migration-activation-v1"] = (
            "monitoring-config-v1"
        )
        if not isinstance(snapshot.get("frequency"), str) or not snapshot["frequency"].strip():
            raise LegacyConfigReadError("legacy_frequency_invalid")
        if (
            not isinstance(snapshot.get("effective_at"), str)
            or not snapshot["effective_at"].strip()
        ):
            raise LegacyConfigReadError("legacy_effective_at_invalid")
    elif keys == _LEGACY_MIGRATION_CONFIG_KEYS:
        source_contract_shape = "migration-activation-v1"
        if not isinstance(snapshot.get("cadence"), str) or not snapshot["cadence"].strip():
            raise LegacyConfigReadError("legacy_cadence_invalid")
        if not isinstance(snapshot.get("legacy_enabled"), bool):
            raise LegacyConfigReadError("legacy_enabled_invalid")
        if (
            not isinstance(snapshot.get("migration_activation"), str)
            or not snapshot["migration_activation"].strip()
        ):
            raise LegacyConfigReadError("legacy_migration_activation_invalid")
    else:
        raise LegacyConfigReadError("legacy_snapshot_schema_unknown")

    models = _string_tuple(snapshot.get("models"), code="legacy_models_invalid")
    modes = _string_tuple(snapshot.get("modes"), code="legacy_modes_invalid")
    regions = _string_tuple(snapshot.get("regions"), code="legacy_regions_invalid")
    query_groups = snapshot.get("query_groups")
    if not isinstance(query_groups, list) or not query_groups:
        raise LegacyConfigReadError("legacy_query_groups_invalid")
    if source_contract_shape == "migration-activation-v1":
        platforms = _string_tuple(snapshot.get("platforms"), code="legacy_platforms_invalid")
        if platforms != models:
            raise LegacyConfigReadError("legacy_models_platforms_mismatch")

    return LegacyCollectionConfigWebView(
        source_snapshot_hash=snapshot_hash,
        source_contract_shape=source_contract_shape,
        models=tuple(LegacyWebModelView(legacy_model=model) for model in models),
        interaction_modes=modes,
        legacy_regions=regions,
    )


def _string_tuple(value: Any, *, code: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise LegacyConfigReadError(code)
    normalized = tuple(item.strip() for item in value)
    if len(normalized) != len(set(normalized)):
        raise LegacyConfigReadError(code)
    return normalized


def _reject_nonstandard_number(value: str) -> None:
    raise LegacyConfigReadError(f"legacy_snapshot_nonstandard_number:{value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyConfigReadError(f"legacy_snapshot_duplicate_key:{key}")
        result[key] = value
    return result


__all__ = [
    "HISTORICAL_SURFACE_ASSIGNMENT_BASIS",
    "LEGACY_CONFIG_READER_SCHEMA_VERSION",
    "LEGACY_CONTRACT_VERSION",
    "LegacyCollectionConfigWebView",
    "LegacyConfigReadError",
    "LegacyWebModelView",
    "read_legacy_config_web_view",
]
