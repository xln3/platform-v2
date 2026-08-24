"""Strict execution-governance contracts for collection surfaces.

The models in this module deliberately contain no persistence or transport code.
They make the authorization facts that must exist before an external side effect
explicit while keeping credentials, browser/device endpoints, and hardware
identifiers structurally unrepresentable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from ipaddress import AddressValueError, IPv6Address
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.collection.surface import (
    QUOTA_SCOPE_KIND_LOCK_ORDER,
    QUOTA_SCOPE_LOCK_ORDER_VERSION,
    CollectionSurface,
    CollectionTarget,
    QuotaScopeKind,
    SendState,
)

BINDING_SCHEMA_VERSION: Literal["collection-binding-v1"] = "collection-binding-v1"
EXECUTION_GRANT_SCHEMA_VERSION: Literal["collection-execution-grant-v1"] = (
    "collection-execution-grant-v1"
)
OWNER_AUTHORIZATION_SCHEMA_VERSION: Literal["collection-owner-authorization-v2"] = (
    "collection-owner-authorization-v2"
)
QUOTA_RESERVATION_SET_VERSION: Literal["collection-quota-reservation-set-v1"] = (
    "collection-quota-reservation-set-v1"
)

_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_DOMAIN_TOKEN_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_AUDIT_REASON_PATTERN = r"^[a-z0-9][a-z0-9._:-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

_RAW_CONNECTION_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
_IP_LITERAL_RE = re.compile(
    r"(?i)(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])|"
    r"\[(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}\]"
)
_IPV6_CANDIDATE_RE = re.compile(
    r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])"
)
_HOST_PORT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9._-])"
    r"(?:localhost|[A-Za-z_](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9_-])?)"
    r"\s*:\s*\d{1,5}(?:\b|/)"
)
_AUTH_OR_COOKIE_RE = re.compile(
    r"(?i)(?:authorization\s*[:=]|(?:set-)?cookie\s*[:=]|"
    r"bearer\s+[A-Za-z0-9._~+/=-]+|(?:sessionid|csrftoken|auth_token)\s*=)"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_KNOWN_SECRET_RE = re.compile(r"(?i)\b(?:sk|xox[abpr]|gh[oprsu])[-_][A-Za-z0-9_-]{8,}\b")
_MAC_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
_IMEI_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")

_ALLOWED_SECRET_METADATA_KEYS = frozenset(
    {
        "secret_references",
        "secret_ref_pub_id",
        "secret_version",
        "secret_fingerprint_sha256",
    }
)
_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "secret",
        "secret_value",
        "raw_secret",
        "credential",
        "credentials",
        "credential_value",
        "api_key",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "authorization",
        "cookie",
        "cookies",
        "password",
        "passphrase",
        "cdp_url",
        "webdriver_url",
        "webdriver_endpoint",
        "adb_url",
        "adb_endpoint",
        "device_serial",
        "serial_number",
        "imei",
        "meid",
        "udid",
        "hardware_id",
        "android_id",
        "mac_address",
    }
)
_FORBIDDEN_KEY_SUFFIXES = (
    "_secret_value",
    "_raw_secret",
    "_api_key",
    "_access_token",
    "_refresh_token",
    "_password",
    "_cookie",
    "_cdp_url",
    "_webdriver_url",
    "_adb_endpoint",
    "_device_serial",
    "_hardware_id",
)

_QUOTA_LOCK_INDEX = {
    scope_kind: index for index, scope_kind in enumerate(QUOTA_SCOPE_KIND_LOCK_ORDER)
}


class ExecutionGovernanceError(ValueError):
    """Fail-closed error with a stable code and secret-free context."""

    code: str
    context: Mapping[str, str | int | bool | None]

    def __init__(self, code: str, **context: str | int | bool | None) -> None:
        self.code = code
        self.context = dict(sorted(context.items()))
        suffix = ":".join(f"{key}={value}" for key, value in self.context.items())
        super().__init__(f"{code}:{suffix}" if suffix else code)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "context": dict(self.context)}


def assert_governance_payload_safe(value: object, *, path: str = "$") -> None:
    """Reject dangerous execution material instead of redacting it.

    Strict models already reject unknown fields.  This recursive guard is a
    defence-in-depth boundary for model dumps, workflow payloads, audit context,
    and caller-provided mappings.
    """

    if isinstance(value, BaseModel):
        assert_governance_payload_safe(value.model_dump(mode="json"), path=path)
        return
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.strip().lower().replace("-", "_")
            if normalized not in _ALLOWED_SECRET_METADATA_KEYS and (
                normalized in _FORBIDDEN_EXACT_KEYS or normalized.endswith(_FORBIDDEN_KEY_SUFFIXES)
            ):
                raise ExecutionGovernanceError(
                    "governance_payload_forbidden_field", path=f"{path}.{key}"
                )
            assert_governance_payload_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, child in enumerate(value):
            assert_governance_payload_safe(child, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    if _RAW_CONNECTION_RE.search(value):
        raise ExecutionGovernanceError("governance_payload_bare_connection", path=path)
    contains_ipv6 = False
    for candidate in _IPV6_CANDIDATE_RE.findall(value):
        try:
            IPv6Address(candidate)
        except AddressValueError:
            continue
        contains_ipv6 = True
        break
    if _IP_LITERAL_RE.search(value) or contains_ipv6 or _HOST_PORT_RE.search(value):
        raise ExecutionGovernanceError("governance_payload_bare_endpoint", path=path)
    if _AUTH_OR_COOKIE_RE.search(value) or _JWT_RE.search(value) or _KNOWN_SECRET_RE.search(value):
        raise ExecutionGovernanceError("governance_payload_secret", path=path)
    if _MAC_RE.search(value) or _IMEI_RE.search(value):
        raise ExecutionGovernanceError("governance_payload_hardware_id", path=path)


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return value


class FrozenGovernanceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @model_validator(mode="after")
    def payload_is_safe(self) -> Self:
        assert_governance_payload_safe(self.model_dump(mode="json"))
        return self


class BindingLifecycleState(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class ReadinessState(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    CHALLENGE_REQUIRED = "challenge_required"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class ExecutionGrantState(StrEnum):
    ISSUED = "issued"
    REVOKED = "revoked"


class ResourceKind(StrEnum):
    PROVIDER_TENANT = "provider_tenant"
    CREDENTIAL_SLOT = "credential_slot"
    GOVERNED_ACCOUNT = "governed_account"
    BROWSER_OWNER = "browser_owner"
    BROWSER_PROFILE = "browser_profile"
    WEB_SESSION = "web_session"
    DEVICE_OWNER = "device_owner"
    APP_INSTALL = "app_install"
    APP_SESSION = "app_session"
    RELAY_CAPACITY = "relay_capacity"


class ResourceLeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    PREEMPTED = "preempted"
    QUARANTINED = "quarantined"


class ResourceLeaseHeartbeatDisposition(StrEnum):
    """Result of an owner-side heartbeat compare-and-set attempt."""

    APPLIED = "applied"
    STALE_LEASE = "stale_lease"
    STALE_GENERATION = "stale_generation"
    LEASE_NOT_ACTIVE = "lease_not_active"
    LEASE_EXPIRED = "lease_expired"
    NON_EXTENDING_EXPIRY = "non_extending_expiry"


class QuotaReservationState(StrEnum):
    PREPARING = "preparing"
    RESERVED = "reserved"
    RECONCILING = "reconciling"
    SETTLED_CONSUMED = "settled_consumed"
    SETTLED_UNKNOWN = "settled_unknown"
    RELEASED = "released"


class QuotaReservationEffectState(StrEnum):
    RESERVED = "reserved"
    SETTLED_CONSUMED = "settled_consumed"
    SETTLED_UNKNOWN = "settled_unknown"
    RELEASED = "released"


class ResourceOwnerState(StrEnum):
    READY = "ready"
    LOST = "lost"
    QUARANTINED = "quarantined"
    STOPPED = "stopped"


class GatewayKind(StrEnum):
    PROVIDER_REQUEST = "provider_request"
    RESIDENT_BROWSER = "resident_browser"
    MANAGED_APP_SESSION = "managed_app_session"


class ExecutionAction(StrEnum):
    SUBMIT_QUERY = "submit_query"


class SecretReferenceMetadata(FrozenGovernanceModel):
    """Non-usable metadata; the gateway resolves the value from its secret store."""

    secret_ref_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    secret_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    secret_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    rotated_at: datetime

    @field_validator("rotated_at")
    @classmethod
    def rotated_at_is_aware(cls, value: datetime) -> datetime:
        return _aware(value, field="rotated_at")


class QuotaSubjectRef(FrozenGovernanceModel):
    scope_kind: QuotaScopeKind
    subject_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @property
    def order_key(self) -> tuple[int, str]:
        return (_QUOTA_LOCK_INDEX[self.scope_kind], self.subject_pub_id)


class BindingQuotaScopeRef(FrozenGovernanceModel):
    """One exact quota bucket that every operation must reserve.

    Subject identity alone is intentionally insufficient: one subject can have
    simultaneous day/week/year, surface, and mode buckets.
    """

    quota_scope_policy_id: UUID
    scope_kind: QuotaScopeKind
    scope_key: str = Field(min_length=1, max_length=1000)
    subject_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_units: int = Field(default=1, strict=True, ge=1)

    @property
    def identity(self) -> tuple[UUID, QuotaScopeKind, str, str, str]:
        return (
            self.quota_scope_policy_id,
            self.scope_kind,
            self.scope_key,
            self.subject_pub_id,
            self.policy_revision,
        )

    @property
    def order_key(self) -> tuple[int, str, str]:
        return (
            _QUOTA_LOCK_INDEX[self.scope_kind],
            self.scope_key,
            self.policy_revision,
        )


class BindingReadiness(FrozenGovernanceModel):
    assessment_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    assessed_at: datetime
    resources: ReadinessState
    quota: ReadinessState
    route: ReadinessState

    @field_validator("assessed_at")
    @classmethod
    def assessed_at_is_aware(cls, value: datetime) -> datetime:
        return _aware(value, field="assessed_at")

    @property
    def production_ready(self) -> bool:
        return all(
            state is ReadinessState.READY for state in (self.resources, self.quota, self.route)
        )


class BindingTargetRef(FrozenGovernanceModel):
    target_key: str = Field(min_length=1, max_length=500)
    platform: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    capability_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @model_validator(mode="after")
    def target_key_matches_dimensions(self) -> Self:
        expected = CollectionTarget(
            platform=self.platform,
            collection_surface=self.collection_surface,
            product_variant=self.product_variant,
            interaction_modes=(self.interaction_mode,),
        ).target_key
        if self.target_key != expected:
            raise ValueError("binding_target_key_mismatch")
        return self


class BindingApproval(FrozenGovernanceModel):
    owner_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approved_by_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approval_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    reason: str = Field(pattern=_AUDIT_REASON_PATTERN)
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def approved_at_is_aware(cls, value: datetime) -> datetime:
        return _aware(value, field="approved_at")


class BindingResourceRef(FrozenGovernanceModel):
    """Exact versioned binding-to-resource mapping from persistence."""

    resource_registration_id: UUID
    resource_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    resource_kind: ResourceKind
    resource_role: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    ordinal: int = Field(strict=True, ge=0)
    required: bool = True
    adoption_required: bool = False
    mapping_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @property
    def identity(self) -> tuple[UUID, ResourceKind, str, str, int, str]:
        return (
            self.resource_registration_id,
            self.resource_kind,
            self.resource_pub_id,
            self.resource_role,
            self.ordinal,
            self.mapping_revision,
        )

    @property
    def order_key(self) -> tuple[str, int, str]:
        return (self.resource_role, self.ordinal, self.resource_pub_id)


class ProviderApiBinding(FrozenGovernanceModel):
    binding_type: Literal["provider_api"] = "provider_api"
    provider_gateway_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_tenant_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_account_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_contract_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    credential_slot_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    endpoint_catalog_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    endpoint_catalog_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    api_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    entitlement_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    credential_rotation_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    egress_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    credential_state: ReadinessState
    entitlement_state: ReadinessState
    provider_account_state: ReadinessState
    provider_request_capacity: int = Field(default=1, strict=True, ge=1)
    credential_capacity: int = Field(default=1, strict=True, ge=1)
    relay_required: bool = False

    @property
    def production_ready(self) -> bool:
        return all(
            state is ReadinessState.READY
            for state in (
                self.credential_state,
                self.entitlement_state,
                self.provider_account_state,
            )
        )


class ConsumerWebBinding(FrozenGovernanceModel):
    binding_type: Literal["consumer_web"] = "consumer_web"
    governed_account_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    browser_owner_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    browser_profile_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    browser_profile_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    web_session_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    web_session_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approved_host_catalog_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    approved_host_catalog_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    relay_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    constraints_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    login_state: ReadinessState
    captcha_state: ReadinessState
    risk_state: ReadinessState
    human_assist_state: ReadinessState
    account_capacity: int = Field(default=1, strict=True, ge=1)
    browser_capacity: int = Field(default=1, strict=True, ge=1)
    session_capacity: int = Field(default=1, strict=True, ge=1)
    relay_required: bool = True

    @property
    def production_ready(self) -> bool:
        return all(
            state is ReadinessState.READY
            for state in (
                self.login_state,
                self.captcha_state,
                self.risk_state,
                self.human_assist_state,
            )
        )


class ConsumerAppBinding(FrozenGovernanceModel):
    binding_type: Literal["consumer_app"] = "consumer_app"
    governed_account_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    device_owner_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    managed_device_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_package_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    app_build_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    distribution_channel: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    app_install_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_profile_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_session_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_session_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    automation_agent_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    attestation_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    relay_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    session_state: ReadinessState
    attestation_state: ReadinessState
    device_health_state: ReadinessState
    human_assist_state: ReadinessState
    account_capacity: int = Field(default=1, strict=True, ge=1)
    device_capacity: int = Field(default=1, strict=True, ge=1)
    session_capacity: int = Field(default=1, strict=True, ge=1)
    relay_required: bool = True

    @field_validator("app_package_id")
    @classmethod
    def crowd_assistant_is_not_a_collector(cls, value: str) -> str:
        if value == "com.geosys.crowdassistant":
            raise ValueError("crowd_assistant_apk_is_not_app_collector")
        return value

    @property
    def production_ready(self) -> bool:
        return all(
            state is ReadinessState.READY
            for state in (
                self.session_state,
                self.attestation_state,
                self.device_health_state,
                self.human_assist_state,
            )
        )


type BindingPayload = Annotated[
    ProviderApiBinding | ConsumerWebBinding | ConsumerAppBinding,
    Field(discriminator="binding_type"),
]

_BASE_REQUIRED_RESOURCES: dict[CollectionSurface, frozenset[ResourceKind]] = {
    CollectionSurface.PROVIDER_API: frozenset(
        {ResourceKind.PROVIDER_TENANT, ResourceKind.CREDENTIAL_SLOT}
    ),
    CollectionSurface.CONSUMER_WEB: frozenset(
        {
            ResourceKind.GOVERNED_ACCOUNT,
            ResourceKind.BROWSER_OWNER,
            ResourceKind.BROWSER_PROFILE,
            ResourceKind.WEB_SESSION,
        }
    ),
    CollectionSurface.CONSUMER_APP: frozenset(
        {
            ResourceKind.GOVERNED_ACCOUNT,
            ResourceKind.DEVICE_OWNER,
            ResourceKind.APP_INSTALL,
            ResourceKind.APP_SESSION,
        }
    ),
}


class BindingRevision(FrozenGovernanceModel):
    schema_version: Literal["collection-binding-v1"] = BINDING_SCHEMA_VERSION
    binding_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    revision: int = Field(strict=True, ge=1)
    tenant_id: UUID
    project_id: UUID
    lifecycle_state: BindingLifecycleState
    effective_from: datetime
    expires_at: datetime
    activated_at: datetime | None = None
    suspended_at: datetime | None = None
    revoked_at: datetime | None = None
    superseded_at: datetime | None = None
    lifecycle_reason: str = Field(pattern=_AUDIT_REASON_PATTERN)
    binding_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    target: BindingTargetRef
    quota_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_registry_id: UUID
    quota_scope_registry_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_scopes: tuple[BindingQuotaScopeRef, ...] = Field(min_length=1)
    required_resource_kinds: tuple[ResourceKind, ...] = Field(min_length=1)
    resource_mappings: tuple[BindingResourceRef, ...] = Field(min_length=1)
    region_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    route_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approval: BindingApproval
    readiness: BindingReadiness
    secret_references: tuple[SecretReferenceMetadata, ...] = ()
    payload: BindingPayload

    @field_validator(
        "effective_from",
        "expires_at",
        "activated_at",
        "suspended_at",
        "revoked_at",
        "superseded_at",
    )
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _aware(value, field=info.field_name)

    @field_validator("quota_scopes")
    @classmethod
    def canonicalize_quota_scopes(
        cls, values: tuple[BindingQuotaScopeRef, ...]
    ) -> tuple[BindingQuotaScopeRef, ...]:
        keys = {value.scope_key for value in values}
        if len(keys) != len(values):
            raise ValueError("duplicate_binding_quota_scope")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @field_validator("required_resource_kinds")
    @classmethod
    def canonicalize_resource_kinds(
        cls, values: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate_binding_resource_kind")
        return tuple(sorted(values, key=lambda value: value.value))

    @field_validator("resource_mappings")
    @classmethod
    def canonicalize_resource_mappings(
        cls, values: tuple[BindingResourceRef, ...]
    ) -> tuple[BindingResourceRef, ...]:
        if len(values) != len({value.identity for value in values}):
            raise ValueError("duplicate_binding_resource_mapping")
        role_ordinals = {(value.resource_role, value.ordinal) for value in values}
        if len(role_ordinals) != len(values):
            raise ValueError("duplicate_binding_resource_role_ordinal")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @field_validator("secret_references")
    @classmethod
    def canonicalize_secret_references(
        cls, values: tuple[SecretReferenceMetadata, ...]
    ) -> tuple[SecretReferenceMetadata, ...]:
        keys = {(value.secret_ref_pub_id, value.secret_version) for value in values}
        if len(keys) != len(values):
            raise ValueError("duplicate_binding_secret_reference")
        return tuple(
            sorted(values, key=lambda value: (value.secret_ref_pub_id, value.secret_version))
        )

    @model_validator(mode="after")
    def validate_binding_contract(self) -> Self:
        if self.expires_at <= self.effective_from:
            raise ValueError("binding_expiry_must_follow_effective_from")
        if self.approval.approved_at > self.effective_from:
            raise ValueError("binding_effective_time_precedes_approval")
        if self.readiness.assessed_at < self.approval.approved_at:
            raise ValueError("binding_readiness_precedes_approval")
        if self.readiness.assessed_at >= self.expires_at:
            raise ValueError("binding_readiness_not_within_validity_window")
        if self.payload.binding_type != self.target.collection_surface.value:
            raise ValueError("binding_surface_subtype_mismatch")
        required = set(_BASE_REQUIRED_RESOURCES[self.target.collection_surface])
        if self.payload.relay_required:
            required.add(ResourceKind.RELAY_CAPACITY)
        missing = required.difference(self.required_resource_kinds)
        if missing:
            names = ",".join(sorted(value.value for value in missing))
            raise ValueError(f"binding_required_resources_missing:{names}")
        mapped_required_kinds = {
            value.resource_kind for value in self.resource_mappings if value.required
        }
        if mapped_required_kinds != set(self.required_resource_kinds):
            raise ValueError("binding_required_resource_kind_mapping_mismatch")
        if isinstance(self.payload, ProviderApiBinding) and not self.secret_references:
            raise ValueError("provider_api_binding_requires_secret_reference")
        if self.activated_at is not None:
            if not (self.effective_from <= self.activated_at < self.expires_at):
                raise ValueError("binding_activation_not_within_validity_window")
            if self.activated_at < self.readiness.assessed_at:
                raise ValueError("binding_activation_precedes_readiness")
        if self.lifecycle_state is BindingLifecycleState.ACTIVE:
            if self.activated_at is None:
                raise ValueError("active_binding_requires_activated_at")
            if not self.production_ready:
                raise ValueError("active_binding_requires_readiness")
        if self.lifecycle_state is BindingLifecycleState.SUSPENDED:
            if self.activated_at is None or self.suspended_at is None:
                raise ValueError("suspended_binding_requires_lifecycle_times")
        if self.lifecycle_state is BindingLifecycleState.REVOKED and self.revoked_at is None:
            raise ValueError("revoked_binding_requires_revoked_at")
        if self.lifecycle_state is BindingLifecycleState.SUPERSEDED and self.superseded_at is None:
            raise ValueError("superseded_binding_requires_superseded_at")
        for field, value in (
            ("suspended_at", self.suspended_at),
            ("revoked_at", self.revoked_at),
            ("superseded_at", self.superseded_at),
        ):
            if value is not None and (self.activated_at is None or value < self.activated_at):
                raise ValueError(f"binding_{field}_precedes_activation")
        if (
            self.lifecycle_state is not BindingLifecycleState.SUSPENDED
            and self.suspended_at is not None
        ):
            raise ValueError("binding_state_forbids_suspended_at")
        if (
            self.lifecycle_state is not BindingLifecycleState.REVOKED
            and self.revoked_at is not None
        ):
            raise ValueError("binding_state_forbids_revoked_at")
        if (
            self.lifecycle_state is not BindingLifecycleState.SUPERSEDED
            and self.superseded_at is not None
        ):
            raise ValueError("binding_state_forbids_superseded_at")
        return self

    @property
    def production_ready(self) -> bool:
        return self.readiness.production_ready and self.payload.production_ready

    @property
    def quota_subjects(self) -> tuple[QuotaSubjectRef, ...]:
        """Canonical subject projection; never use it to prove bucket completeness."""

        by_identity = {
            (scope.scope_kind, scope.subject_pub_id): QuotaSubjectRef(
                scope_kind=scope.scope_kind,
                subject_pub_id=scope.subject_pub_id,
            )
            for scope in self.quota_scopes
        }
        return tuple(sorted(by_identity.values(), key=lambda value: value.order_key))

    @property
    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "binding_pub_id": self.binding_pub_id,
            "revision": self.revision,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "effective_from": self.effective_from.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "binding_policy_revision": self.binding_policy_revision,
            "target": self.target.model_dump(mode="json"),
            "quota_policy_revision": self.quota_policy_revision,
            "quota_registry_id": str(self.quota_registry_id),
            "quota_scope_registry_revision": self.quota_scope_registry_revision,
            "quota_scopes": [value.model_dump(mode="json") for value in self.quota_scopes],
            "required_resource_kinds": [value.value for value in self.required_resource_kinds],
            "resource_mappings": [
                value.model_dump(mode="json") for value in self.resource_mappings
            ],
            "region_policy_revision": self.region_policy_revision,
            "route_policy_revision": self.route_policy_revision,
            "approval": self.approval.model_dump(mode="json"),
            "secret_references": [
                value.model_dump(mode="json") for value in self.secret_references
            ],
            "payload": self.payload.model_dump(mode="json"),
        }

    @property
    def binding_hash(self) -> str:
        encoded = json.dumps(
            self.semantic_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


_BINDING_TRANSITIONS: dict[BindingLifecycleState, frozenset[BindingLifecycleState]] = {
    BindingLifecycleState.DRAFT: frozenset({BindingLifecycleState.CANDIDATE}),
    BindingLifecycleState.CANDIDATE: frozenset(
        {BindingLifecycleState.DRAFT, BindingLifecycleState.ACTIVE}
    ),
    BindingLifecycleState.ACTIVE: frozenset(
        {
            BindingLifecycleState.SUSPENDED,
            BindingLifecycleState.REVOKED,
            BindingLifecycleState.SUPERSEDED,
        }
    ),
    BindingLifecycleState.SUSPENDED: frozenset(
        {
            BindingLifecycleState.ACTIVE,
            BindingLifecycleState.REVOKED,
            BindingLifecycleState.SUPERSEDED,
        }
    ),
    BindingLifecycleState.REVOKED: frozenset(),
    BindingLifecycleState.SUPERSEDED: frozenset(),
}


def assert_binding_usable(binding: BindingRevision, *, at: datetime) -> None:
    at = _aware(at, field="at")
    if binding.lifecycle_state is not BindingLifecycleState.ACTIVE:
        raise ExecutionGovernanceError(
            "binding_not_active", lifecycle_state=binding.lifecycle_state.value
        )
    if at < binding.effective_from:
        raise ExecutionGovernanceError("binding_not_yet_effective")
    if binding.activated_at is None or at < binding.activated_at:
        raise ExecutionGovernanceError("binding_not_activated")
    if at >= binding.expires_at:
        raise ExecutionGovernanceError("binding_expired")
    if not binding.production_ready:
        raise ExecutionGovernanceError("binding_not_ready")


def transition_binding_lifecycle(
    binding: BindingRevision,
    *,
    target: BindingLifecycleState,
    at: datetime,
    reason: str,
) -> BindingRevision:
    """Return a validated lifecycle snapshot without mutating binding content."""

    at = _aware(at, field="at")
    if not re.fullmatch(_AUDIT_REASON_PATTERN, reason):
        raise ExecutionGovernanceError("binding_lifecycle_reason_invalid")
    if target not in _BINDING_TRANSITIONS[binding.lifecycle_state]:
        raise ExecutionGovernanceError(
            "binding_lifecycle_transition_invalid",
            current=binding.lifecycle_state.value,
            target=target.value,
        )
    if target is BindingLifecycleState.ACTIVE:
        if at < binding.effective_from:
            raise ExecutionGovernanceError("binding_not_yet_effective")
        if at >= binding.expires_at:
            raise ExecutionGovernanceError("binding_expired")
        if not binding.production_ready:
            raise ExecutionGovernanceError("binding_not_ready")
    payload = binding.model_dump(mode="python")
    payload.update(
        {
            "lifecycle_state": target,
            "lifecycle_reason": reason,
            "suspended_at": at if target is BindingLifecycleState.SUSPENDED else None,
            "revoked_at": at if target is BindingLifecycleState.REVOKED else None,
            "superseded_at": at if target is BindingLifecycleState.SUPERSEDED else None,
        }
    )
    if target is BindingLifecycleState.ACTIVE:
        payload["activated_at"] = binding.activated_at or at
    return BindingRevision.model_validate(payload)


class GrantDimensions(FrozenGovernanceModel):
    platform: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    province_code: str = Field(pattern=r"^\d{6}$")


class ConfigTargetExecutionRef(FrozenGovernanceModel):
    config_revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    config_revision_hash: str = Field(pattern=_SHA256_PATTERN)
    config_target_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    target_key: str = Field(min_length=1, max_length=500)
    capability_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)


class CampaignSlotExecutionRef(FrozenGovernanceModel):
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    campaign_membership_hash: str = Field(pattern=_SHA256_PATTERN)
    campaign_target_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    sampling_leg_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    slot_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    slot_key: str = Field(min_length=1, max_length=1500)
    question_slot_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    question_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)


class SubmissionOperationRef(FrozenGovernanceModel):
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    logical_item_key: str = Field(min_length=1, max_length=1500)
    generation: int = Field(strict=True, ge=1)


class BindingExecutionRef(FrozenGovernanceModel):
    binding_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_revision: int = Field(strict=True, ge=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)
    binding_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_registry_id: UUID
    region_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    route_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    surface_route_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    required_quota_scopes: tuple[BindingQuotaScopeRef, ...] = Field(min_length=1)
    required_resource_kinds: tuple[ResourceKind, ...] = Field(min_length=1)
    resource_mappings: tuple[BindingResourceRef, ...] = Field(min_length=1)

    @field_validator("required_quota_scopes")
    @classmethod
    def canonicalize_quota_scopes(
        cls, values: tuple[BindingQuotaScopeRef, ...]
    ) -> tuple[BindingQuotaScopeRef, ...]:
        if len(values) != len({value.scope_key for value in values}):
            raise ValueError("duplicate_grant_binding_quota_scope")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @field_validator("required_resource_kinds")
    @classmethod
    def canonicalize_resources(cls, values: tuple[ResourceKind, ...]) -> tuple[ResourceKind, ...]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate_grant_binding_resource_kind")
        return tuple(sorted(values, key=lambda value: value.value))

    @field_validator("resource_mappings")
    @classmethod
    def canonicalize_resource_mappings(
        cls, values: tuple[BindingResourceRef, ...]
    ) -> tuple[BindingResourceRef, ...]:
        if len(values) != len({value.identity for value in values}):
            raise ValueError("duplicate_grant_binding_resource_mapping")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @model_validator(mode="after")
    def required_resource_mappings_are_complete(self) -> Self:
        mapped_required_kinds = {
            value.resource_kind for value in self.resource_mappings if value.required
        }
        if mapped_required_kinds != set(self.required_resource_kinds):
            raise ValueError("grant_binding_required_resource_mapping_mismatch")
        return self


class QuotaReservationEffectRef(FrozenGovernanceModel):
    effect_id: UUID
    quota_bucket_id: UUID
    quota_scope_policy_id: UUID
    scope_key: str = Field(min_length=1, max_length=1000)
    bucket_hash: str = Field(pattern=_SHA256_PATTERN)
    bucket_key: str = Field(min_length=1, max_length=2000)
    scope_kind: QuotaScopeKind
    units: int = Field(strict=True, ge=1)

    @property
    def order_key(self) -> tuple[int, str]:
        # quota_v2 materializes buckets from declarations in this exact order.
        # ``bucket_key`` happens to embed the selector today, but is persistence
        # identity, not the authoritative policy ordering contract.
        return (_QUOTA_LOCK_INDEX[self.scope_kind], self.scope_key)


def quota_reservation_effect_set_hash(
    effects: Sequence[QuotaReservationEffectRef],
    *,
    requested_units: int,
) -> str:
    """Reproduce quota_v2's persisted reservation effect-set digest."""

    if isinstance(requested_units, bool) or requested_units < 1 or not effects:
        raise ValueError("quota_effect_set_hash_input_invalid")
    if len(effects) != len({(effect.scope_kind, effect.scope_key) for effect in effects}):
        raise ValueError("quota_effect_set_scope_duplicate")
    ordered = tuple(sorted(effects, key=lambda effect: effect.order_key))
    material = "\n".join(
        (
            QUOTA_RESERVATION_SET_VERSION,
            QUOTA_SCOPE_LOCK_ORDER_VERSION,
            f"requested_units={requested_units}",
            *(
                f"{effect.bucket_hash}|scope_policy_id="
                f"{effect.quota_scope_policy_id}|units={effect.units}"
                for effect in ordered
            ),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


class QuotaReservationRef(FrozenGovernanceModel):
    """One persisted reservation envelope with its exact frozen effects."""

    reservation_id: UUID
    requested_units: int = Field(strict=True, ge=1)
    expected_effect_count: int = Field(strict=True, ge=1)
    effect_set_hash: str = Field(pattern=_SHA256_PATTERN)
    effects: tuple[QuotaReservationEffectRef, ...] = Field(min_length=1)

    @field_validator("effects")
    @classmethod
    def canonicalize_effects(
        cls, values: tuple[QuotaReservationEffectRef, ...]
    ) -> tuple[QuotaReservationEffectRef, ...]:
        if len(values) != len({value.effect_id for value in values}):
            raise ValueError("duplicate_quota_effect_id")
        if len(values) != len({value.quota_bucket_id for value in values}):
            raise ValueError("duplicate_quota_effect_bucket")
        if len(values) != len({value.quota_scope_policy_id for value in values}):
            raise ValueError("duplicate_quota_effect_scope_policy")
        if len(values) != len({(value.scope_kind, value.scope_key) for value in values}):
            raise ValueError("duplicate_quota_effect_scope_key")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @model_validator(mode="after")
    def effect_set_is_complete(self) -> Self:
        if self.expected_effect_count != len(self.effects):
            raise ValueError("quota_reservation_effect_count_mismatch")
        if self.effect_set_hash != quota_reservation_effect_set_hash(
            self.effects,
            requested_units=self.requested_units,
        ):
            raise ValueError("quota_reservation_effect_set_hash_mismatch")
        return self


class ResourceFenceRef(FrozenGovernanceModel):
    resource_registration_id: UUID
    capacity_unit_id: UUID
    resource_kind: ResourceKind
    resource_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    resource_role: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    resource_ordinal: int = Field(strict=True, ge=0)
    binding_resource_mapping_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    lease_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    owner_gateway_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    fence_generation: int = Field(strict=True, ge=1)
    capacity_unit: int = Field(default=1, strict=True, ge=1)
    acquired_at: datetime
    expires_at: datetime

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, field=info.field_name)

    @model_validator(mode="after")
    def expiry_follows_acquisition(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("resource_fence_expiry_must_follow_acquisition")
        return self

    @property
    def order_key(self) -> tuple[str, int, str]:
        return (self.resource_role, self.resource_ordinal, self.lease_pub_id)

    @property
    def binding_mapping_identity(self) -> tuple[UUID, ResourceKind, str, str, int, str]:
        return (
            self.resource_registration_id,
            self.resource_kind,
            self.resource_pub_id,
            self.resource_role,
            self.resource_ordinal,
            self.binding_resource_mapping_revision,
        )


class ExecutionCompatibility(FrozenGovernanceModel):
    workflow_contract_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    adapter_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    gateway_protocol_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    worker_build_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    agent_revision: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)


class ApiExecutionGrant(FrozenGovernanceModel):
    grant_type: Literal["provider_api"] = "provider_api"
    provider_gateway_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    credential_slot_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_endpoint_catalog_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    provider_api_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_tenant_context_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    provider_quota_subject_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)


class WebExecutionGrant(FrozenGovernanceModel):
    grant_type: Literal["consumer_web"] = "consumer_web"
    browser_owner_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    governed_account_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    browser_profile_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    browser_profile_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    web_session_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    web_session_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approved_host_catalog_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)


class AppExecutionGrant(FrozenGovernanceModel):
    grant_type: Literal["consumer_app"] = "consumer_app"
    device_owner_handle: str = Field(pattern=_OPAQUE_ID_PATTERN)
    governed_account_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    managed_device_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_package_id: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    app_build_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    distribution_channel: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    app_install_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_session_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    app_session_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    automation_agent_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @field_validator("app_package_id")
    @classmethod
    def crowd_assistant_is_not_a_collector(cls, value: str) -> str:
        if value == "com.geosys.crowdassistant":
            raise ValueError("crowd_assistant_apk_is_not_app_collector")
        return value


type ExecutionGrantPayload = Annotated[
    ApiExecutionGrant | WebExecutionGrant | AppExecutionGrant,
    Field(discriminator="grant_type"),
]


class ExecutionGrantEnvelope(FrozenGovernanceModel):
    schema_version: Literal["collection-execution-grant-v1"] = EXECUTION_GRANT_SCHEMA_VERSION
    grant_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    grant_revision: int = Field(strict=True, ge=1)
    grant_state: ExecutionGrantState = ExecutionGrantState.ISSUED
    tenant_id: UUID
    project_id: UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = Field(default=None, pattern=_AUDIT_REASON_PATTERN)
    config_target: ConfigTargetExecutionRef
    campaign_slot: CampaignSlotExecutionRef
    operation: SubmissionOperationRef
    dimensions: GrantDimensions
    binding: BindingExecutionRef
    quota_registry_id: UUID
    quota_scope_registry_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    quota_reservation: QuotaReservationRef
    resource_fences: tuple[ResourceFenceRef, ...] = Field(min_length=1)
    compatibility: ExecutionCompatibility
    allowed_actions: tuple[ExecutionAction, ...] = Field(min_length=1)
    issued_by_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    issuance_reason: str = Field(pattern=_AUDIT_REASON_PATTERN)
    payload: ExecutionGrantPayload

    @field_validator("issued_at", "expires_at", "revoked_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _aware(value, field=info.field_name)

    @field_validator("resource_fences")
    @classmethod
    def canonicalize_resource_fences(
        cls, values: tuple[ResourceFenceRef, ...]
    ) -> tuple[ResourceFenceRef, ...]:
        if len(values) != len({value.lease_pub_id for value in values}):
            raise ValueError("duplicate_grant_resource_lease")
        if len(values) != len({value.binding_mapping_identity for value in values}):
            raise ValueError("duplicate_grant_resource_mapping")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @field_validator("allowed_actions")
    @classmethod
    def canonicalize_allowed_actions(
        cls, values: tuple[ExecutionAction, ...]
    ) -> tuple[ExecutionAction, ...]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate_grant_action")
        return tuple(sorted(values, key=lambda value: value.value))

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("grant_expiry_must_follow_issuance")
        if self.grant_state is ExecutionGrantState.REVOKED:
            if self.revoked_at is None or self.revocation_reason is None:
                raise ValueError("revoked_grant_requires_audit_fields")
        elif self.revoked_at is not None or self.revocation_reason is not None:
            raise ValueError("issued_grant_forbids_revocation_fields")
        if self.payload.grant_type != self.dimensions.collection_surface.value:
            raise ValueError("grant_surface_subtype_mismatch")
        expected_target_key = CollectionTarget(
            platform=self.dimensions.platform,
            collection_surface=self.dimensions.collection_surface,
            product_variant=self.dimensions.product_variant,
            interaction_modes=(self.dimensions.interaction_mode,),
        ).target_key
        if self.config_target.target_key != expected_target_key:
            raise ValueError("grant_target_dimensions_mismatch")
        if self.operation.logical_item_key != self.campaign_slot.slot_key:
            raise ValueError("grant_operation_logical_item_mismatch")
        if self.quota_registry_id != self.binding.quota_registry_id:
            raise ValueError("grant_quota_registry_identity_mismatch")
        bound_scopes = {
            scope.quota_scope_policy_id: scope for scope in self.binding.required_quota_scopes
        }
        effects = {
            effect.quota_scope_policy_id: effect for effect in self.quota_reservation.effects
        }
        if set(effects) != set(bound_scopes):
            raise ValueError("grant_required_quota_scope_set_mismatch")
        for policy_id, scope in bound_scopes.items():
            effect = effects[policy_id]
            if effect.scope_kind is not scope.scope_kind:
                raise ValueError("grant_quota_effect_scope_kind_mismatch")
            if effect.scope_key != scope.scope_key:
                raise ValueError("grant_quota_effect_scope_key_mismatch")
            if effect.units != scope.quota_units * self.quota_reservation.requested_units:
                raise ValueError("grant_quota_effect_units_mismatch")
        allowed_mapping_ids = {value.identity for value in self.binding.resource_mappings}
        required_mapping_ids = {
            value.identity for value in self.binding.resource_mappings if value.required
        }
        actual_mapping_ids = {value.binding_mapping_identity for value in self.resource_fences}
        if not required_mapping_ids.issubset(actual_mapping_ids):
            raise ValueError("grant_required_resource_mapping_missing")
        if not actual_mapping_ids.issubset(allowed_mapping_ids):
            raise ValueError("grant_resource_mapping_not_bound")
        if any(value.acquired_at > self.issued_at for value in self.resource_fences):
            raise ValueError("grant_resource_lease_acquired_in_future")
        if any(value.expires_at < self.expires_at for value in self.resource_fences):
            raise ValueError("grant_outlives_resource_lease")
        return self


def assert_grant_usable(grant: ExecutionGrantEnvelope, *, at: datetime) -> None:
    at = _aware(at, field="at")
    if grant.grant_state is not ExecutionGrantState.ISSUED:
        raise ExecutionGovernanceError("grant_revoked")
    if at < grant.issued_at:
        raise ExecutionGovernanceError("grant_not_yet_issued")
    if at >= grant.expires_at:
        raise ExecutionGovernanceError("grant_expired")


def revoke_execution_grant(
    grant: ExecutionGrantEnvelope, *, revoked_at: datetime, reason: str
) -> ExecutionGrantEnvelope:
    revoked_at = _aware(revoked_at, field="revoked_at")
    if grant.grant_state is not ExecutionGrantState.ISSUED:
        raise ExecutionGovernanceError("grant_already_revoked")
    if revoked_at < grant.issued_at:
        raise ExecutionGovernanceError("grant_revocation_precedes_issuance")
    payload = grant.model_dump(mode="python")
    payload.update(
        {
            "grant_state": ExecutionGrantState.REVOKED,
            "revoked_at": revoked_at,
            "revocation_reason": reason,
        }
    )
    return ExecutionGrantEnvelope.model_validate(payload)


class SubmissionOperationSnapshot(FrozenGovernanceModel):
    tenant_id: UUID
    project_id: UUID
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    slot_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    logical_item_key: str = Field(min_length=1, max_length=1500)
    generation: int = Field(strict=True, ge=1)
    current_generation: int = Field(strict=True, ge=1)
    send_state: SendState
    send_state_version: int = Field(strict=True, ge=1)


class QuotaReservationEffectSnapshot(FrozenGovernanceModel):
    effect_id: UUID
    quota_bucket_id: UUID
    quota_scope_policy_id: UUID
    scope_key: str = Field(min_length=1, max_length=1000)
    bucket_hash: str = Field(pattern=_SHA256_PATTERN)
    bucket_key: str = Field(min_length=1, max_length=2000)
    scope_kind: QuotaScopeKind
    units: int = Field(strict=True, ge=1)
    state: QuotaReservationEffectState

    @property
    def order_key(self) -> tuple[int, str]:
        return (_QUOTA_LOCK_INDEX[self.scope_kind], self.scope_key)

    @property
    def grant_ref(self) -> QuotaReservationEffectRef:
        return QuotaReservationEffectRef(
            effect_id=self.effect_id,
            quota_bucket_id=self.quota_bucket_id,
            quota_scope_policy_id=self.quota_scope_policy_id,
            scope_key=self.scope_key,
            bucket_hash=self.bucket_hash,
            bucket_key=self.bucket_key,
            scope_kind=self.scope_kind,
            units=self.units,
        )


class QuotaReservationSnapshot(FrozenGovernanceModel):
    """Current persistence view of one operation-level reservation envelope."""

    tenant_id: UUID
    project_id: UUID
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_revision: int = Field(strict=True, ge=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)
    reservation_id: UUID
    quota_registry_id: UUID
    scope_registry_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    requested_units: int = Field(strict=True, ge=1)
    expected_effect_count: int = Field(strict=True, ge=1)
    effect_set_hash: str = Field(pattern=_SHA256_PATTERN)
    state: QuotaReservationState
    effects: tuple[QuotaReservationEffectSnapshot, ...] = Field(min_length=1)

    @field_validator("effects")
    @classmethod
    def canonicalize_effects(
        cls, values: tuple[QuotaReservationEffectSnapshot, ...]
    ) -> tuple[QuotaReservationEffectSnapshot, ...]:
        if len(values) != len({value.effect_id for value in values}):
            raise ValueError("duplicate_quota_effect_snapshot_id")
        if len(values) != len({value.quota_bucket_id for value in values}):
            raise ValueError("duplicate_quota_effect_snapshot_bucket")
        if len(values) != len({value.quota_scope_policy_id for value in values}):
            raise ValueError("duplicate_quota_effect_snapshot_scope_policy")
        if len(values) != len({(value.scope_kind, value.scope_key) for value in values}):
            raise ValueError("duplicate_quota_effect_snapshot_scope_key")
        return tuple(sorted(values, key=lambda value: value.order_key))

    @model_validator(mode="after")
    def effect_set_matches_envelope(self) -> Self:
        if self.expected_effect_count != len(self.effects):
            raise ValueError("quota_reservation_effect_count_mismatch")
        if self.effect_set_hash != quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in self.effects),
            requested_units=self.requested_units,
        ):
            raise ValueError("quota_reservation_effect_set_hash_mismatch")
        return self

    @property
    def grant_ref(self) -> QuotaReservationRef:
        return QuotaReservationRef(
            reservation_id=self.reservation_id,
            requested_units=self.requested_units,
            expected_effect_count=self.expected_effect_count,
            effect_set_hash=self.effect_set_hash,
            effects=tuple(effect.grant_ref for effect in self.effects),
        )


class ResourceLeaseSnapshot(FrozenGovernanceModel):
    tenant_id: UUID
    project_id: UUID
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_revision: int = Field(strict=True, ge=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)
    resource_registration_id: UUID
    capacity_unit_id: UUID
    resource_kind: ResourceKind
    resource_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    resource_role: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    resource_ordinal: int = Field(strict=True, ge=0)
    binding_resource_mapping_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    lease_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    owner_gateway_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    fence_generation: int = Field(strict=True, ge=1)
    current_fence_generation: int = Field(strict=True, ge=1)
    capacity_unit: int = Field(default=1, strict=True, ge=1)
    state: ResourceLeaseState
    acquired_at: datetime
    expires_at: datetime

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, field=info.field_name)

    @model_validator(mode="after")
    def expiry_follows_acquisition(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("resource_lease_expiry_must_follow_acquisition")
        return self

    @property
    def binding_mapping_identity(self) -> tuple[UUID, ResourceKind, str, str, int, str]:
        return (
            self.resource_registration_id,
            self.resource_kind,
            self.resource_pub_id,
            self.resource_role,
            self.resource_ordinal,
            self.binding_resource_mapping_revision,
        )

    @property
    def grant_ref(self) -> ResourceFenceRef:
        return ResourceFenceRef(
            resource_registration_id=self.resource_registration_id,
            capacity_unit_id=self.capacity_unit_id,
            resource_kind=self.resource_kind,
            resource_pub_id=self.resource_pub_id,
            resource_role=self.resource_role,
            resource_ordinal=self.resource_ordinal,
            binding_resource_mapping_revision=self.binding_resource_mapping_revision,
            lease_pub_id=self.lease_pub_id,
            owner_gateway_pub_id=self.owner_gateway_pub_id,
            fence_generation=self.fence_generation,
            capacity_unit=self.capacity_unit,
            acquired_at=self.acquired_at,
            expires_at=self.expires_at,
        )


class ResourceCapacityUnitSnapshot(FrozenGovernanceModel):
    """Current owner view of one independently fenced capacity unit.

    A resource with capacity ``N`` is represented by ``N`` distinct units.
    Generations are monotonic per unit, which avoids turning an entire region or
    an unrelated resource into a global mutex.
    """

    capacity_unit_id: UUID
    capacity_unit: int = Field(strict=True, ge=1)
    current_fence_generation: int = Field(strict=True, ge=0)
    quarantined: bool = False
    lease: ResourceLeaseSnapshot | None = None

    @model_validator(mode="after")
    def lease_matches_unit(self) -> Self:
        if self.quarantined and self.lease is not None:
            raise ValueError("quarantined_capacity_unit_forbids_lease")
        if self.lease is None:
            return self
        if self.lease.capacity_unit_id != self.capacity_unit_id:
            raise ValueError("capacity_unit_lease_identity_mismatch")
        if self.lease.capacity_unit != self.capacity_unit:
            raise ValueError("capacity_unit_lease_ordinal_mismatch")
        if self.lease.current_fence_generation != self.current_fence_generation:
            raise ValueError("capacity_unit_current_fence_mismatch")
        if self.lease.fence_generation != self.current_fence_generation:
            raise ValueError("capacity_unit_active_lease_fence_stale")
        return self


class ResourceCapacityPoolSnapshot(FrozenGovernanceModel):
    """One physical resource and its explicit, independently leasable units."""

    tenant_id: UUID
    project_id: UUID
    resource_registration_id: UUID
    resource_kind: ResourceKind
    resource_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    owner_gateway_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    units: tuple[ResourceCapacityUnitSnapshot, ...] = Field(min_length=1)

    @field_validator("units")
    @classmethod
    def canonicalize_units(
        cls, values: tuple[ResourceCapacityUnitSnapshot, ...]
    ) -> tuple[ResourceCapacityUnitSnapshot, ...]:
        if len(values) != len({value.capacity_unit for value in values}):
            raise ValueError("duplicate_resource_capacity_unit")
        if len(values) != len({value.capacity_unit_id for value in values}):
            raise ValueError("duplicate_resource_capacity_unit_identity")
        return tuple(sorted(values, key=lambda value: value.capacity_unit))

    @model_validator(mode="after")
    def leases_match_pool(self) -> Self:
        for unit in self.units:
            lease = unit.lease
            if lease is None:
                continue
            if lease.tenant_id != self.tenant_id or lease.project_id != self.project_id:
                raise ValueError("capacity_pool_lease_scope_mismatch")
            if (
                lease.resource_registration_id != self.resource_registration_id
                or lease.resource_kind is not self.resource_kind
                or lease.resource_pub_id != self.resource_pub_id
                or lease.owner_gateway_pub_id != self.owner_gateway_pub_id
            ):
                raise ValueError("capacity_pool_lease_resource_mismatch")
        return self

    @property
    def capacity(self) -> int:
        return len(self.units)


class ResourceLeaseAcquireRequest(FrozenGovernanceModel):
    tenant_id: UUID
    project_id: UUID
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    binding_revision: int = Field(strict=True, ge=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)
    binding_resource: BindingResourceRef
    lease_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    acquired_at: datetime
    expires_at: datetime

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, field=info.field_name)

    @model_validator(mode="after")
    def expiry_follows_acquisition(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("resource_lease_expiry_must_follow_acquisition")
        return self


class ResourceLeaseAcquireResult(FrozenGovernanceModel):
    pool: ResourceCapacityPoolSnapshot
    lease: ResourceLeaseSnapshot
    replayed: bool = False


class ResourceLeaseHeartbeatRequest(FrozenGovernanceModel):
    lease_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    fence_generation: int = Field(strict=True, ge=1)
    heartbeat_at: datetime
    extend_expires_at: datetime

    @field_validator("heartbeat_at", "extend_expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, field=info.field_name)

    @model_validator(mode="after")
    def extension_is_in_the_future(self) -> Self:
        if self.extend_expires_at <= self.heartbeat_at:
            raise ValueError("resource_heartbeat_expiry_must_follow_heartbeat")
        return self


class ResourceLeaseHeartbeatResult(FrozenGovernanceModel):
    pool: ResourceCapacityPoolSnapshot
    disposition: ResourceLeaseHeartbeatDisposition
    lease: ResourceLeaseSnapshot | None = None

    @property
    def applied(self) -> bool:
        return self.disposition is ResourceLeaseHeartbeatDisposition.APPLIED


class ResourceOwnerSnapshot(FrozenGovernanceModel):
    owner_gateway_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    gateway_kind: GatewayKind
    collection_surface: CollectionSurface
    state: ResourceOwnerState
    protocol_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @model_validator(mode="after")
    def gateway_surface_matches(self) -> Self:
        expected = {
            GatewayKind.PROVIDER_REQUEST: CollectionSurface.PROVIDER_API,
            GatewayKind.RESIDENT_BROWSER: CollectionSurface.CONSUMER_WEB,
            GatewayKind.MANAGED_APP_SESSION: CollectionSurface.CONSUMER_APP,
        }[self.gateway_kind]
        if self.collection_surface is not expected:
            raise ValueError("gateway_surface_kind_mismatch")
        return self


class SideEffectAuthorization(FrozenGovernanceModel):
    schema_version: Literal["collection-owner-authorization-v2"] = (
        OWNER_AUTHORIZATION_SCHEMA_VERSION
    )
    grant_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    operation_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    operation_generation: int = Field(strict=True, ge=1)
    owner_gateway_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    action: ExecutionAction
    checked_at: datetime
    expected_send_state: Literal[SendState.NOT_SENT] = SendState.NOT_SENT
    expected_send_state_version: int = Field(strict=True, ge=1)
    required_next_send_state: Literal[SendState.SENDING] = SendState.SENDING
    quota_reservation_id: UUID
    quota_effect_ids: tuple[UUID, ...] = Field(min_length=1)
    fence_assertions: tuple[ResourceFenceRef, ...] = Field(min_length=1)

    @field_validator("checked_at")
    @classmethod
    def checked_at_is_aware(cls, value: datetime) -> datetime:
        return _aware(value, field="checked_at")
