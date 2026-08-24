"""Canonical collection-surface, configuration, and logical identity contracts.

This module is deliberately independent of persistence and orchestration.  API,
workflow, analytics, and reporting layers can all consume the same values without
reconstructing collection identities from display labels or physical resources.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

COLLECTION_CONFIG_SCHEMA_VERSION: Literal["collection-config-v2"] = "collection-config-v2"
CAPABILITY_SCHEMA_VERSION: Literal["collection-capability-v1"] = "collection-capability-v1"
CAPABILITY_REGISTRY_SCHEMA_VERSION: Literal["collection-capability-registry-v1"] = (
    "collection-capability-registry-v1"
)
QUOTA_WINDOW_SCHEMA_VERSION: Literal["quota-window-v1"] = "quota-window-v1"
QUOTA_SCOPE_SCHEMA_VERSION: Literal["quota-scope-v1"] = "quota-scope-v1"
QUOTA_SCOPE_REGISTRY_SCHEMA_VERSION: Literal["quota-scope-registry-v1"] = "quota-scope-registry-v1"
QUOTA_SCOPE_LOCK_ORDER_VERSION: Literal["quota-scope-lock-order-v1"] = "quota-scope-lock-order-v1"
PROVINCE_CATALOG_VERSION = "mainland-province-31-v1"

TARGET_IDENTITY_GRAMMAR = (
    "collection-target-v1|platform=<platform>|collection_surface=<collection_surface>|"
    "product_variant=<product_variant>"
)
SLOT_IDENTITY_GRAMMAR = (
    "collection-slot-v1|campaign_id=<campaign_id>|question_slot_id=<question_slot_id>|"
    "platform=<platform>|collection_surface=<collection_surface>|"
    "product_variant=<product_variant>|province_code=<province_code>|"
    "interaction_mode=<interaction_mode>|sample_ordinal=<sample_ordinal>|"
    "slot_role=<slot_role>"
)

# Mainland province-level administrative divisions.  Hong Kong, Macao, Taiwan,
# and a synthetic "nationwide" value are intentionally outside this catalog.
MAINLAND_PROVINCE_CODES: frozenset[str] = frozenset(
    {
        "110000",
        "120000",
        "130000",
        "140000",
        "150000",
        "210000",
        "220000",
        "230000",
        "310000",
        "320000",
        "330000",
        "340000",
        "350000",
        "360000",
        "370000",
        "410000",
        "420000",
        "430000",
        "440000",
        "450000",
        "460000",
        "500000",
        "510000",
        "520000",
        "530000",
        "540000",
        "610000",
        "620000",
        "630000",
        "640000",
        "650000",
    }
)

_DOMAIN_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CollectionSurface(StrEnum):
    """The product surface observed by a sampling leg, never a fallback route."""

    PROVIDER_API = "provider_api"
    CONSUMER_WEB = "consumer_web"
    CONSUMER_APP = "consumer_app"


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    PILOT = "pilot"
    UNSUPPORTED = "unsupported"


class ConfigLifecycleState(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    FROZEN = "frozen"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class SlotRole(StrEnum):
    PRIMARY = "primary"
    SUPPLEMENTARY = "supplementary"
    TOPUP = "topup"


class SendState(StrEnum):
    """Durable external-submit truth; sent or unknown states are no-resend states."""

    NOT_SENT = "NOT_SENT"
    SENDING = "SENDING"
    CONFIRMED_SENT = "CONFIRMED_SENT"
    SEND_UNKNOWN = "SEND_UNKNOWN"
    CONFIRMED_NOT_SENT = "CONFIRMED_NOT_SENT"


class CaptureState(StrEnum):
    """Truth about capture of the response to an already prepared operation."""

    NOT_STARTED = "not_started"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_OBSERVABLE = "not_observable"


class AnalysisState(StrEnum):
    """Truth about replayable analysis of an immutable capture."""

    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class QuotaScopeKind(StrEnum):
    """Consumption-bucket dimensions; physical capacity remains a lease concern."""

    PROVIDER = "provider"
    ACCOUNT = "account"
    CREDENTIAL = "credential"
    PROJECT = "project"
    CONTRACT = "contract"
    PLATFORM_SURFACE = "platform_surface"
    MODE = "mode"


class QuotaWindowUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    YEAR = "year"
    PROVIDER_CUSTOM = "provider_custom"


# This order is part of the durable reservation protocol.  Callers must lock the
# complete resolved bucket set in this order instead of relying on input order.
QUOTA_SCOPE_KIND_LOCK_ORDER: tuple[QuotaScopeKind, ...] = (
    QuotaScopeKind.PROVIDER,
    QuotaScopeKind.ACCOUNT,
    QuotaScopeKind.CREDENTIAL,
    QuotaScopeKind.PROJECT,
    QuotaScopeKind.CONTRACT,
    QuotaScopeKind.PLATFORM_SURFACE,
    QuotaScopeKind.MODE,
)
_QUOTA_SCOPE_KIND_LOCK_INDEX = {
    scope_kind: index for index, scope_kind in enumerate(QUOTA_SCOPE_KIND_LOCK_ORDER)
}


SEND_STATE_TRANSITIONS: dict[SendState, frozenset[SendState]] = {
    SendState.NOT_SENT: frozenset({SendState.SENDING, SendState.CONFIRMED_NOT_SENT}),
    SendState.SENDING: frozenset(
        {
            SendState.CONFIRMED_SENT,
            SendState.SEND_UNKNOWN,
            SendState.CONFIRMED_NOT_SENT,
        }
    ),
    SendState.CONFIRMED_SENT: frozenset(),
    SendState.SEND_UNKNOWN: frozenset(),
    SendState.CONFIRMED_NOT_SENT: frozenset(),
}
NO_RESEND_SEND_STATES: frozenset[SendState] = frozenset(
    {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}
)


def transition_send_state(current: SendState, target: SendState) -> SendState:
    """Validate the normative durable-send transition graph."""

    if target not in SEND_STATE_TRANSITIONS[current]:
        raise ValueError(f"invalid_send_state_transition:{current.value}->{target.value}")
    return target


def validate_province_code(value: str) -> str:
    """Return a canonical mainland province code or fail closed."""

    if not isinstance(value, str):
        raise ValueError("province_code_must_be_string")
    normalized = value.strip()
    if normalized not in MAINLAND_PROVINCE_CODES:
        raise ValueError(f"invalid_province_code:{normalized}")
    return normalized


def expand_sample_ordinals(samples_per_cell: int) -> tuple[int, ...]:
    """Expand a frozen cell count to stable 1-based logical sample identities."""

    if isinstance(samples_per_cell, bool) or not isinstance(samples_per_cell, int):
        raise ValueError("samples_per_cell_must_be_integer")
    if samples_per_cell < 1:
        raise ValueError("samples_per_cell_must_be_positive")
    return tuple(range(1, samples_per_cell + 1))


class FrozenDomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class QuotaWindowPolicy(FrozenDomainModel):
    """Versioned calendar/provider window semantics for a consumption bucket."""

    schema_version: Literal["quota-window-v1"] = QUOTA_WINDOW_SCHEMA_VERSION
    unit: QuotaWindowUnit
    size: int = Field(default=1, strict=True, ge=1)
    timezone: str = Field(min_length=1, max_length=128)
    boundary_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    provider_window_code: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_RE.pattern)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_resolvable(cls, value: str) -> str:
        if "|" in value or "=" in value:
            raise ValueError("invalid_quota_timezone")
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(f"invalid_quota_timezone:{value}") from exc
        return value

    @model_validator(mode="after")
    def validate_provider_window(self) -> Self:
        if self.unit is QuotaWindowUnit.PROVIDER_CUSTOM and self.provider_window_code is None:
            raise ValueError("provider_custom_window_requires_code")
        if (
            self.unit is not QuotaWindowUnit.PROVIDER_CUSTOM
            and self.provider_window_code is not None
        ):
            raise ValueError("calendar_window_forbids_provider_code")
        return self

    @property
    def window_key(self) -> str:
        provider_code = self.provider_window_code or "*"
        return (
            "quota-window-v1"
            f"|unit={self.unit.value}"
            f"|size={self.size}"
            f"|timezone={self.timezone}"
            f"|boundary_revision={self.boundary_revision}"
            f"|provider_window_code={provider_code}"
        )


class QuotaScopeDeclaration(FrozenDomainModel):
    """One active quota policy bucket selector and its consumption limit."""

    schema_version: Literal["quota-scope-v1"] = QUOTA_SCOPE_SCHEMA_VERSION
    policy_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    scope_kind: QuotaScopeKind
    scope_subject_id: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    limit: int = Field(strict=True, ge=1)
    window: QuotaWindowPolicy
    platform: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_RE.pattern)
    collection_surface: CollectionSurface | None = None
    product_variant: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_RE.pattern)
    interaction_mode: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_RE.pattern)

    @model_validator(mode="after")
    def validate_scope_dimensions(self) -> Self:
        if self.collection_surface is not None and self.platform is None:
            raise ValueError("quota_surface_requires_platform")
        if self.product_variant is not None and self.platform is None:
            raise ValueError("quota_product_requires_platform")
        if self.scope_kind is QuotaScopeKind.PLATFORM_SURFACE and (
            self.platform is None or self.collection_surface is None
        ):
            raise ValueError("platform_surface_scope_requires_platform_and_surface")
        if self.scope_kind is QuotaScopeKind.MODE and self.interaction_mode is None:
            raise ValueError("mode_scope_requires_interaction_mode")
        return self

    @property
    def selector_key(self) -> str:
        """Logical selector used to reject overlapping policies in one registry."""

        return self._key_material(include_policy_revision=False)

    @property
    def scope_key(self) -> str:
        """Versioned canonical key used for reservation and ledger references."""

        return self._key_material(include_policy_revision=True)

    @property
    def canonical_key(self) -> str:
        return self.scope_key

    @property
    def lock_order_key(self) -> tuple[int, str]:
        return (_QUOTA_SCOPE_KIND_LOCK_INDEX[self.scope_kind], self.scope_key)

    def _key_material(self, *, include_policy_revision: bool) -> str:
        values = {
            "platform": self.platform,
            "collection_surface": (
                self.collection_surface.value if self.collection_surface is not None else None
            ),
            "product_variant": self.product_variant,
            "interaction_mode": self.interaction_mode,
        }
        material = (
            "quota-scope-v1"
            f"|scope_kind={self.scope_kind.value}"
            f"|scope_subject_id={self.scope_subject_id}"
            f"|platform={values['platform'] or '*'}"
            f"|collection_surface={values['collection_surface'] or '*'}"
            f"|product_variant={values['product_variant'] or '*'}"
            f"|interaction_mode={values['interaction_mode'] or '*'}"
            f"|window_unit={self.window.unit.value}"
            f"|window_size={self.window.size}"
            f"|window_timezone={self.window.timezone}"
            f"|window_boundary_revision={self.window.boundary_revision}"
            f"|provider_window_code={self.window.provider_window_code or '*'}"
        )
        if include_policy_revision:
            return f"{material}|policy_revision={self.policy_revision}"
        return material


class QuotaScopeRegistry(FrozenDomainModel):
    """Versioned quota registry with one deterministic global lock order."""

    schema_version: Literal["quota-scope-registry-v1"] = QUOTA_SCOPE_REGISTRY_SCHEMA_VERSION
    registry_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    lock_order_version: Literal["quota-scope-lock-order-v1"] = QUOTA_SCOPE_LOCK_ORDER_VERSION
    scopes: tuple[QuotaScopeDeclaration, ...] = ()

    @field_validator("scopes")
    @classmethod
    def reject_duplicates_and_apply_lock_order(
        cls, values: tuple[QuotaScopeDeclaration, ...]
    ) -> tuple[QuotaScopeDeclaration, ...]:
        seen: set[str] = set()
        for scope in values:
            if scope.selector_key in seen:
                raise ValueError(f"duplicate_quota_scope:{scope.selector_key}")
            seen.add(scope.selector_key)
        return canonical_quota_lock_order(values)

    @property
    def canonical_scope_keys(self) -> tuple[str, ...]:
        return tuple(scope.scope_key for scope in self.scopes)


def canonical_quota_lock_order(
    scopes: tuple[QuotaScopeDeclaration, ...],
) -> tuple[QuotaScopeDeclaration, ...]:
    return tuple(sorted(scopes, key=lambda scope: scope.lock_order_key))


class CollectionTarget(FrozenDomainModel):
    """One explicitly selected platform product surface in canonical config v2."""

    platform: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    interaction_modes: tuple[str, ...] = Field(min_length=1)

    @field_validator("interaction_modes")
    @classmethod
    def canonicalize_interaction_modes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not _DOMAIN_TOKEN_RE.fullmatch(value):
                raise ValueError(f"invalid_interaction_mode:{value}")
        if len(values) != len(set(values)):
            raise ValueError("duplicate_interaction_mode")
        return tuple(sorted(values))

    @property
    def identity(self) -> tuple[str, CollectionSurface, str]:
        return (self.platform, self.collection_surface, self.product_variant)

    @property
    def target_key(self) -> str:
        return (
            "collection-target-v1"
            f"|platform={self.platform}"
            f"|collection_surface={self.collection_surface.value}"
            f"|product_variant={self.product_variant}"
        )

    @property
    def business_key(self) -> str:
        return self.target_key


class CollectionConfigV2(FrozenDomainModel):
    """Canonical, server-hashed semantic content of an immutable config revision."""

    schema_version: Literal["collection-config-v2"] = COLLECTION_CONFIG_SCHEMA_VERSION
    question_set_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    collection_targets: tuple[CollectionTarget, ...] = Field(min_length=1)
    province_codes: tuple[str, ...] = Field(min_length=1)
    samples_per_cell: int = Field(strict=True, ge=1)
    schedule_policy: Mapping[str, JsonValue] = Field(default_factory=dict)
    comparison_policy_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)

    @field_validator("collection_targets")
    @classmethod
    def canonicalize_targets(
        cls, values: tuple[CollectionTarget, ...]
    ) -> tuple[CollectionTarget, ...]:
        seen: set[tuple[str, CollectionSurface, str]] = set()
        for target in values:
            if target.identity in seen:
                raise ValueError(f"duplicate_collection_target:{target.target_key}")
            seen.add(target.identity)
        return tuple(sorted(values, key=lambda target: target.target_key))

    @field_validator("province_codes")
    @classmethod
    def canonicalize_province_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_province_code(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate_province_code")
        return tuple(sorted(normalized))

    @field_validator("schedule_policy")
    @classmethod
    def validate_schedule_policy(cls, value: dict[str, JsonValue]) -> Mapping[str, JsonValue]:
        _assert_finite_json(value)
        return cast(Mapping[str, JsonValue], _freeze_json(value))

    @field_serializer("schedule_policy")
    def serialize_schedule_policy(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_json(value))

    @property
    def sample_ordinals(self) -> tuple[int, ...]:
        return expand_sample_ordinals(self.samples_per_cell)

    @property
    def canonical_json(self) -> str:
        return canonical_config_json(self)

    @property
    def revision_hash(self) -> str:
        return canonical_config_hash(self)

    @property
    def config_hash(self) -> str:
        return self.revision_hash


class CapabilityDeclaration(FrozenDomainModel):
    """A versioned static capability for one complete capability key."""

    schema_version: Literal["collection-capability-v1"] = CAPABILITY_SCHEMA_VERSION
    capability_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    platform: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    status: CapabilityStatus
    region_policy_revision: str | None = Field(default=None, pattern=_OPAQUE_ID_RE.pattern)
    required_resource_kinds: tuple[str, ...] = ()
    observable_capture_fields: tuple[str, ...] = ()
    product_version_constraints: Mapping[str, JsonValue] = Field(default_factory=dict)
    production_allowed: bool = Field(default=False, strict=True)
    unsupported_reason: str | None = Field(default=None, max_length=500)
    alternative_suggestion: str | None = Field(default=None, max_length=500)

    @field_validator("required_resource_kinds", "observable_capture_fields")
    @classmethod
    def canonicalize_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not _DOMAIN_TOKEN_RE.fullmatch(value):
                raise ValueError(f"invalid_capability_token:{value}")
        if len(values) != len(set(values)):
            raise ValueError("duplicate_capability_token")
        return tuple(sorted(values))

    @field_validator("product_version_constraints")
    @classmethod
    def validate_product_constraints(cls, value: dict[str, JsonValue]) -> Mapping[str, JsonValue]:
        _assert_finite_json(value)
        return cast(Mapping[str, JsonValue], _freeze_json(value))

    @field_serializer("product_version_constraints")
    def serialize_product_constraints(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_json(value))

    @model_validator(mode="after")
    def validate_unsupported_contract(self) -> Self:
        if self.status is CapabilityStatus.UNSUPPORTED:
            if self.production_allowed:
                raise ValueError("unsupported_capability_cannot_allow_production")
            if not self.unsupported_reason:
                raise ValueError("unsupported_capability_requires_reason")
        return self

    @property
    def key(self) -> tuple[str, CollectionSurface, str, str]:
        return (
            self.platform,
            self.collection_surface,
            self.product_variant,
            self.interaction_mode,
        )


class StaticCapabilityError(ValueError):
    """Machine-readable rejection raised at candidate/freeze validation."""

    code: str
    target_key: str
    interaction_mode: str
    capability_status: CapabilityStatus | None

    def __init__(
        self,
        *,
        code: str,
        target_key: str,
        interaction_mode: str,
        capability_status: CapabilityStatus | None,
    ) -> None:
        self.code = code
        self.target_key = target_key
        self.interaction_mode = interaction_mode
        self.capability_status = capability_status
        status = capability_status.value if capability_status is not None else "missing"
        super().__init__(
            f"{code}:target_key={target_key}:interaction_mode={interaction_mode}:status={status}"
        )


class CapabilityRegistry(FrozenDomainModel):
    """Versioned lookup and the single static-validation entry point."""

    schema_version: Literal["collection-capability-registry-v1"] = (
        CAPABILITY_REGISTRY_SCHEMA_VERSION
    )
    registry_revision: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    capabilities: tuple[CapabilityDeclaration, ...] = ()

    @field_validator("capabilities")
    @classmethod
    def canonicalize_capabilities(
        cls, values: tuple[CapabilityDeclaration, ...]
    ) -> tuple[CapabilityDeclaration, ...]:
        seen: set[tuple[str, CollectionSurface, str, str]] = set()
        for capability in values:
            if capability.key in seen:
                raise ValueError("duplicate_capability_key")
            seen.add(capability.key)
        return tuple(
            sorted(
                values,
                key=lambda capability: tuple(str(part) for part in capability.key),
            )
        )

    def validate_target(self, target: CollectionTarget) -> tuple[CapabilityDeclaration, ...]:
        by_key = {capability.key: capability for capability in self.capabilities}
        accepted: list[CapabilityDeclaration] = []
        for interaction_mode in target.interaction_modes:
            key = (
                target.platform,
                target.collection_surface,
                target.product_variant,
                interaction_mode,
            )
            capability = by_key.get(key)
            if capability is None:
                raise StaticCapabilityError(
                    code="capability_not_declared",
                    target_key=target.target_key,
                    interaction_mode=interaction_mode,
                    capability_status=None,
                )
            if capability.status is CapabilityStatus.UNSUPPORTED:
                raise StaticCapabilityError(
                    code="capability_unsupported",
                    target_key=target.target_key,
                    interaction_mode=interaction_mode,
                    capability_status=capability.status,
                )
            accepted.append(capability)
        return tuple(accepted)

    def validate_config(self, config: CollectionConfigV2) -> tuple[CapabilityDeclaration, ...]:
        return tuple(
            capability
            for target in config.collection_targets
            for capability in self.validate_target(target)
        )


class SlotIdentity(FrozenDomainModel):
    """Frozen logical slot identity; physical attempts and resources are excluded."""

    campaign_id: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    question_slot_id: str = Field(pattern=_OPAQUE_ID_RE.pattern)
    platform: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    province_code: str
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_RE.pattern)
    sample_ordinal: int = Field(strict=True, ge=1)
    slot_role: SlotRole = SlotRole.PRIMARY

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @property
    def slot_key(self) -> str:
        return (
            "collection-slot-v1"
            f"|campaign_id={self.campaign_id}"
            f"|question_slot_id={self.question_slot_id}"
            f"|platform={self.platform}"
            f"|collection_surface={self.collection_surface.value}"
            f"|product_variant={self.product_variant}"
            f"|province_code={self.province_code}"
            f"|interaction_mode={self.interaction_mode}"
            f"|sample_ordinal={self.sample_ordinal}"
            f"|slot_role={self.slot_role.value}"
        )

    @property
    def business_key(self) -> str:
        return self.slot_key


def canonical_config_payload(config: CollectionConfigV2) -> dict[str, JsonValue]:
    """Return the normalized semantic payload included in the revision hash."""

    return cast(dict[str, JsonValue], config.model_dump(mode="json"))


def canonical_config_json(config: CollectionConfigV2) -> str:
    return _canonical_json(canonical_config_payload(config))


def canonical_config_hash(config: CollectionConfigV2) -> str:
    return sha256(canonical_config_json(config).encode("utf-8")).hexdigest()


def build_target_key(target: CollectionTarget) -> str:
    return target.target_key


def build_slot_key(identity: SlotIdentity) -> str:
    return identity.slot_key


def _canonical_json(value: JsonValue | dict[str, JsonValue]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assert_finite_json(value: JsonValue, *, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non_finite_json_number:{path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite_json(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_finite_json(item, path=f"{path}.{key}")


def _freeze_json(value: JsonValue) -> object:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    return value


def _thaw_json(value: object) -> JsonValue:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    return cast(JsonValue, value)
