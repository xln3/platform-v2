"""Pure collection-v2 configuration and campaign identity construction.

This module validates and freezes semantic inputs but never reads, commits, or
flushes a database session.  Every physical route/resource choice is deliberately
absent: those values belong to later grants and attempt provenance, not logical
membership.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.collection.surface import (
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
    ConfigLifecycleState,
    SlotIdentity,
    SlotRole,
    StaticCapabilityError,
)

IDENTITY_V2_SCHEMA_VERSION: Literal["collection-identity-v2"] = "collection-identity-v2"
CAMPAIGN_MEMBERSHIP_SCHEMA_VERSION: Literal["collection-campaign-membership-v1"] = (
    "collection-campaign-membership-v1"
)
CAMPAIGN_SLOT_GENERATOR_VERSION: Literal["collection-slot-generator-v1"] = (
    "collection-slot-generator-v1"
)
CAMPAIGN_MEMBERSHIP_DIGEST_VERSION: Literal["collection-membership-chain-v1"] = (
    "collection-membership-chain-v1"
)

_MAX_SLOT_COUNT = (1 << 63) - 1
MAX_CAMPAIGN_SLOT_CHUNK_SIZE = 4096
MAX_CAMPAIGN_EXECUTION_PAGE_SIZE = 2048

_IDENTITY_NAMESPACE = UUID("50f5410c-aa5f-4d99-9b9a-55ec47fe86fc")
_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_DOMAIN_TOKEN_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_AUDIT_REASON_PATTERN = r"^[a-z0-9][a-z0-9._:-]{0,127}$"
_TIME_WINDOW_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/+|=-]{0,254}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class IdentityV2Error(ValueError):
    """Fail-closed exception with a stable code and JSON-safe context."""

    code: str
    context: Mapping[str, str | int | bool | None]

    def __init__(self, code: str, **context: str | int | bool | None) -> None:
        self.code = code
        self.context = dict(sorted(context.items()))
        suffix = ":" + ":".join(f"{key}={value}" for key, value in self.context.items())
        super().__init__(f"{code}{suffix}" if self.context else code)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "context": dict(self.context)}


class FrozenIdentityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class CapabilityRevisionBinding(FrozenIdentityModel):
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    capability_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    status: CapabilityStatus
    production_allowed: bool
    region_policy_revision: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)


class FrozenConfigTarget(FrozenIdentityModel):
    id: UUID
    pub_id: str = Field(min_length=1, max_length=30)
    target: CollectionTarget
    capability_bindings: tuple[CapabilityRevisionBinding, ...]

    @field_validator("capability_bindings")
    @classmethod
    def canonicalize_capability_bindings(
        cls, values: tuple[CapabilityRevisionBinding, ...]
    ) -> tuple[CapabilityRevisionBinding, ...]:
        if len(values) != len({value.interaction_mode for value in values}):
            raise ValueError("duplicate_capability_binding_mode")
        return tuple(sorted(values, key=lambda value: value.interaction_mode))

    @property
    def target_key(self) -> str:
        return self.target.target_key

    @property
    def capability_revision_mapping(self) -> Mapping[str, str]:
        return {
            binding.interaction_mode: binding.capability_revision
            for binding in self.capability_bindings
        }


class ConfigFreezeRequest(FrozenIdentityModel):
    revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    revision: int = Field(strict=True, ge=1)
    parent_revision_id: UUID | None = None
    config: CollectionConfigV2
    capability_registry: CapabilityRegistry
    current_state: ConfigLifecycleState = ConfigLifecycleState.CANDIDATE
    change_reason: str = Field(pattern=_AUDIT_REASON_PATTERN)
    change_request_pub_id: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)
    approved_by_pub_id: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def frozen_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field="frozen_at")


class FrozenConfigRevision(FrozenIdentityModel):
    schema_version: Literal["collection-identity-v2"] = IDENTITY_V2_SCHEMA_VERSION
    id: UUID
    revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    revision: int = Field(strict=True, ge=1)
    parent_revision_id: UUID | None = None
    lifecycle_state: ConfigLifecycleState
    config: CollectionConfigV2
    canonical_json: str
    revision_hash: str = Field(pattern=_SHA256_PATTERN)
    capability_registry_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    targets: tuple[FrozenConfigTarget, ...]
    change_reason: str = Field(pattern=_AUDIT_REASON_PATTERN)
    change_request_pub_id: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)
    approved_by_pub_id: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)
    frozen_at: datetime
    activated_at: datetime | None = None

    @field_validator("targets")
    @classmethod
    def canonicalize_targets(
        cls, values: tuple[FrozenConfigTarget, ...]
    ) -> tuple[FrozenConfigTarget, ...]:
        if len(values) != len({value.target_key for value in values}):
            raise ValueError("duplicate_frozen_config_target")
        return tuple(sorted(values, key=lambda value: value.target_key))

    @field_validator("frozen_at", "activated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware_datetime(value, field=info.field_name)

    @model_validator(mode="after")
    def content_matches_domain_canonicalization(self) -> Self:
        if self.canonical_json != self.config.canonical_json:
            raise ValueError("canonical_json_mismatch")
        if self.revision_hash != self.config.revision_hash:
            raise ValueError("revision_hash_mismatch")
        if self.lifecycle_state is ConfigLifecycleState.ACTIVE and self.activated_at is None:
            raise ValueError("active_config_requires_activated_at")
        expected_keys = tuple(target.target_key for target in self.config.collection_targets)
        if tuple(target.target_key for target in self.targets) != expected_keys:
            raise ValueError("frozen_targets_do_not_match_config")
        return self

    @property
    def revision_row_values(self) -> dict[str, object]:
        return {
            "id": self.id,
            "pub_id": self.revision_pub_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "revision": self.revision,
            "parent_revision_id": self.parent_revision_id,
            "lifecycle_state": self.lifecycle_state.value,
            "schema_version": self.config.schema_version,
            "question_set_revision": self.config.question_set_revision,
            "canonical_json": self.canonical_json,
            "revision_hash": self.revision_hash,
            "capability_registry_revision": self.capability_registry_revision,
            "comparison_policy_revision": self.config.comparison_policy_revision,
            "samples_per_cell": self.config.samples_per_cell,
            "province_codes_json": _canonical_json(list(self.config.province_codes)),
            "schedule_policy_json": _canonical_json(
                self.config.model_dump(mode="json")["schedule_policy"]
            ),
            "change_reason": self.change_reason,
            "change_request_pub_id": self.change_request_pub_id,
            "approved_by_pub_id": self.approved_by_pub_id,
            "frozen_at": self.frozen_at,
            "activated_at": self.activated_at,
        }

    @property
    def target_row_values(self) -> tuple[dict[str, object], ...]:
        rows: list[dict[str, object]] = []
        for target in self.targets:
            rows.append(
                {
                    "id": target.id,
                    "pub_id": target.pub_id,
                    "tenant_id": self.tenant_id,
                    "project_id": self.project_id,
                    "config_revision_id": self.id,
                    "target_key": target.target_key,
                    "platform": target.target.platform,
                    "collection_surface": target.target.collection_surface.value,
                    "product_variant": target.target.product_variant,
                    "interaction_modes_json": _canonical_json(
                        list(target.target.interaction_modes)
                    ),
                    "capability_revisions_json": _canonical_json(
                        dict(target.capability_revision_mapping)
                    ),
                }
            )
        return tuple(rows)

    @property
    def persistence_plan(self) -> ConfigPersistencePlan:
        """Ordered candidate-parent -> targets -> frozen transition values."""

        return build_config_persistence_plan(self)


class CandidateValidation(FrozenIdentityModel):
    state: Literal[ConfigLifecycleState.CANDIDATE] = ConfigLifecycleState.CANDIDATE
    canonical_json: str
    revision_hash: str = Field(pattern=_SHA256_PATTERN)
    capability_registry_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    capability_revisions: tuple[str, ...]


class QuestionSlotRef(FrozenIdentityModel):
    question_slot_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    question_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)


class CampaignActors(FrozenIdentityModel):
    created_by_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    approved_by_pub_id: str | None = Field(default=None, pattern=_OPAQUE_ID_PATTERN)
    triggered_by_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)


class NonPrimarySlotRequest(FrozenIdentityModel):
    question_slot_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    platform: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    province_code: str
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    sample_ordinal: int = Field(strict=True, ge=1)
    slot_role: SlotRole
    reason: str = Field(pattern=_AUDIT_REASON_PATTERN)

    @model_validator(mode="after")
    def role_must_be_non_primary(self) -> Self:
        if self.slot_role is SlotRole.PRIMARY:
            raise ValueError("non_primary_request_forbids_primary_role")
        # Reuse SlotIdentity for province and delimiter validation.
        SlotIdentity(
            campaign_id="validation",
            question_slot_id=self.question_slot_id,
            platform=self.platform,
            collection_surface=self.collection_surface,
            product_variant=self.product_variant,
            province_code=self.province_code,
            interaction_mode=self.interaction_mode,
            sample_ordinal=self.sample_ordinal,
            slot_role=self.slot_role,
        )
        return self


class CampaignFreezeRequest(FrozenIdentityModel):
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    config_revision: FrozenConfigRevision
    question_slots: tuple[QuestionSlotRef, ...] = Field(min_length=1)
    time_window_key: str = Field(pattern=_TIME_WINDOW_PATTERN)
    run_trigger_source: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    trigger_idempotency_key: str = Field(pattern=_OPAQUE_ID_PATTERN)
    actors: CampaignActors
    binding_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    supplementary_slots: tuple[NonPrimarySlotRequest, ...] = ()
    frozen_at: datetime

    @field_validator("question_slots")
    @classmethod
    def canonicalize_question_slots(
        cls, values: tuple[QuestionSlotRef, ...]
    ) -> tuple[QuestionSlotRef, ...]:
        seen_ids: set[str] = set()
        for value in values:
            if value.question_slot_id in seen_ids:
                raise ValueError(f"duplicate_question_slot:{value.question_slot_id}")
            seen_ids.add(value.question_slot_id)
        return tuple(sorted(values, key=lambda value: value.question_slot_id))

    @field_validator("supplementary_slots")
    @classmethod
    def canonicalize_supplementary_slots(
        cls, values: tuple[NonPrimarySlotRequest, ...]
    ) -> tuple[NonPrimarySlotRequest, ...]:
        ordered = tuple(sorted(values, key=_supplementary_request_order_key))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if _supplementary_request_order_key(previous) == _supplementary_request_order_key(
                current
            ):
                raise ValueError("duplicate_supplementary_slot")
        return ordered

    @field_validator("frozen_at")
    @classmethod
    def frozen_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field="frozen_at")

    @model_validator(mode="after")
    def config_scope_matches_campaign(self) -> Self:
        config = self.config_revision
        if config.tenant_id != self.tenant_id:
            raise ValueError("campaign_config_tenant_mismatch")
        if config.project_id != self.project_id:
            raise ValueError("campaign_config_project_mismatch")
        if config.lifecycle_state not in {
            ConfigLifecycleState.FROZEN,
            ConfigLifecycleState.ACTIVE,
        }:
            raise ValueError("campaign_requires_frozen_or_active_config")
        return self


class FrozenCampaignTarget(FrozenIdentityModel):
    id: UUID
    pub_id: str = Field(max_length=30)
    config_target_id: UUID
    config_target_pub_id: str = Field(max_length=30)
    target: CollectionTarget
    capability_bindings: tuple[CapabilityRevisionBinding, ...]
    binding_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)

    @property
    def target_key(self) -> str:
        return self.target.target_key

    @property
    def capability_revision_mapping(self) -> Mapping[str, str]:
        return {
            binding.interaction_mode: binding.capability_revision
            for binding in self.capability_bindings
        }


class FrozenSamplingLeg(FrozenIdentityModel):
    id: UUID
    pub_id: str = Field(max_length=30)
    campaign_target_id: UUID
    campaign_target_pub_id: str = Field(max_length=30)
    leg_key: str
    platform: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    collection_surface: CollectionSurface
    product_variant: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    province_code: str
    interaction_mode: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)


class FrozenCampaignSlot(FrozenIdentityModel):
    id: UUID
    pub_id: str = Field(max_length=30)
    ordinal: int = Field(strict=True, ge=0)
    slot_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    campaign_target_id: UUID
    campaign_target_pub_id: str = Field(max_length=30)
    sampling_leg_id: UUID
    sampling_leg_pub_id: str = Field(max_length=30)
    identity: SlotIdentity
    question_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    role_reason: str | None = Field(default=None, pattern=_AUDIT_REASON_PATTERN)
    related_primary_slot_key: str | None = None

    @model_validator(mode="after")
    def primary_and_non_primary_metadata_are_consistent(self) -> Self:
        if self.identity.slot_role is SlotRole.PRIMARY:
            if self.role_reason is not None or self.related_primary_slot_key is not None:
                raise ValueError("primary_slot_forbids_supplementary_metadata")
        elif self.role_reason is None or self.related_primary_slot_key is None:
            raise ValueError("non_primary_slot_requires_reason_and_primary_link")
        return self

    @property
    def slot_key(self) -> str:
        return self.identity.slot_key


class CampaignSupplementarySlotSpec(FrozenIdentityModel):
    """Compact explicit non-primary member anchored to one primary ordinal."""

    request: NonPrimarySlotRequest
    primary_ordinal: int = Field(strict=True, ge=0)


class CampaignAssemblyBlueprint(FrozenIdentityModel):
    """Compact immutable specification for an ``assembling`` campaign."""

    schema_version: Literal["collection-identity-v2"] = IDENTITY_V2_SCHEMA_VERSION
    id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    config_revision_id: UUID
    config_revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    config_revision_hash: str = Field(pattern=_SHA256_PATTERN)
    question_set_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    question_slots: tuple[QuestionSlotRef, ...]
    time_window_key: str = Field(pattern=_TIME_WINDOW_PATTERN)
    run_trigger_source: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    trigger_idempotency_key: str = Field(pattern=_OPAQUE_ID_PATTERN)
    actors: CampaignActors
    binding_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    targets: tuple[FrozenCampaignTarget, ...]
    legs: tuple[FrozenSamplingLeg, ...]
    province_codes: tuple[str, ...]
    samples_per_cell: int = Field(strict=True, ge=1)
    supplementary_specs: tuple[CampaignSupplementarySlotSpec, ...] = ()
    specification_schema_version: Literal["collection-campaign-membership-v1"] = (
        CAMPAIGN_MEMBERSHIP_SCHEMA_VERSION
    )
    membership_specification_json: str
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    expected_primary_slot_count: int = Field(strict=True, ge=1)
    expected_non_primary_slot_count: int = Field(strict=True, ge=0)
    expected_slot_count: int = Field(strict=True, ge=1)
    requested_frozen_at: datetime
    state: Literal["assembling"] = "assembling"

    @field_validator("requested_frozen_at")
    @classmethod
    def requested_frozen_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field="requested_frozen_at")

    @model_validator(mode="after")
    def counts_and_specification_are_consistent(self) -> Self:
        if self.expected_non_primary_slot_count != len(self.supplementary_specs):
            raise ValueError("campaign_non_primary_count_mismatch")
        if self.expected_slot_count != (
            self.expected_primary_slot_count + self.expected_non_primary_slot_count
        ):
            raise ValueError("campaign_total_count_mismatch")
        if sha256(self.membership_specification_json.encode("utf-8")).hexdigest() != (
            self.specification_hash
        ):
            raise ValueError("campaign_specification_hash_mismatch")
        return self

    @property
    def campaign_row_values(self) -> dict[str, object]:
        return {
            "id": self.id,
            "pub_id": self.campaign_pub_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "config_revision_id": self.config_revision_id,
            "config_revision_hash": self.config_revision_hash,
            "question_set_revision": self.question_set_revision,
            "time_window_key": self.time_window_key,
            "run_trigger_source": self.run_trigger_source,
            "trigger_idempotency_key": self.trigger_idempotency_key,
            "binding_policy_revision": self.binding_policy_revision,
            "specification_schema_version": self.specification_schema_version,
            "membership_specification_json": self.membership_specification_json,
            "specification_hash": self.specification_hash,
            "slot_generator_version": self.slot_generator_version,
            "membership_digest_version": self.membership_digest_version,
            "expected_primary_slot_count": self.expected_primary_slot_count,
            "expected_non_primary_slot_count": self.expected_non_primary_slot_count,
            "expected_slot_count": self.expected_slot_count,
            "materialized_slot_count": 0,
            "materialization_state": "pending",
            "materialization_cursor": 0,
            "membership_hash": None,
            "created_by_pub_id": self.actors.created_by_pub_id,
            "approved_by_pub_id": self.actors.approved_by_pub_id,
            "triggered_by_pub_id": self.actors.triggered_by_pub_id,
            "frozen_at": None,
            "state": self.state,
        }

    @property
    def campaign_target_row_values(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "id": target.id,
                "pub_id": target.pub_id,
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "campaign_id": self.id,
                "config_target_id": target.config_target_id,
                "target_key": target.target_key,
                "platform": target.target.platform,
                "collection_surface": target.target.collection_surface.value,
                "product_variant": target.target.product_variant,
                "interaction_modes_json": _canonical_json(list(target.target.interaction_modes)),
                "capability_revisions_json": _canonical_json(
                    dict(target.capability_revision_mapping)
                ),
                "binding_policy_revision": target.binding_policy_revision,
            }
            for target in self.targets
        )

    @property
    def leg_row_values(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "id": leg.id,
                "pub_id": leg.pub_id,
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "campaign_id": self.id,
                "campaign_target_id": leg.campaign_target_id,
                "leg_key": leg.leg_key,
                "platform": leg.platform,
                "collection_surface": leg.collection_surface.value,
                "product_variant": leg.product_variant,
                "province_code": leg.province_code,
                "interaction_mode": leg.interaction_mode,
            }
            for leg in self.legs
        )

    @property
    def persistence_plan(self) -> CampaignPersistencePlan:
        """Header/static rows plus bounded slot chunks and final CAS values."""

        return build_campaign_persistence_plan(self)

    @property
    def requires_persisted_freeze_confirmation(self) -> Literal[True]:
        """A prepared blueprint is never itself scheduler admission proof."""

        return True


class FrozenCampaign(FrozenIdentityModel):
    """Persisted freeze proof; an assembly blueprint cannot validate as this type."""

    schema_version: Literal["collection-identity-v2"] = IDENTITY_V2_SCHEMA_VERSION
    id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    config_revision_id: UUID
    config_revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    config_revision_hash: str = Field(pattern=_SHA256_PATTERN)
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    expected_slot_count: int = Field(strict=True, ge=1)
    materialized_slot_count: int = Field(strict=True, ge=1)
    materialization_state: Literal["complete"] = "complete"
    materialization_cursor: int = Field(strict=True, ge=1)
    membership_hash: str = Field(pattern=_SHA256_PATTERN)
    frozen_at: datetime
    state: Literal["frozen"] = "frozen"

    @field_validator("frozen_at")
    @classmethod
    def frozen_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field="frozen_at")

    @model_validator(mode="after")
    def membership_is_complete(self) -> Self:
        if (
            self.materialized_slot_count != self.expected_slot_count
            or self.materialization_cursor != self.expected_slot_count
        ):
            raise ValueError("frozen_campaign_membership_incomplete")
        return self


class PersistedCampaignFreezeConfirmation(FrozenIdentityModel):
    """Fresh database result returned only after the final freeze CAS commits."""

    campaign_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    expected_slot_count: int = Field(strict=True, ge=1)
    materialized_slot_count: int = Field(strict=True, ge=1)
    materialization_state: Literal["complete"] = "complete"
    materialization_cursor: int = Field(strict=True, ge=1)
    membership_hash: str = Field(pattern=_SHA256_PATTERN)
    frozen_at: datetime
    state: Literal["frozen"] = "frozen"

    @field_validator("frozen_at")
    @classmethod
    def frozen_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field="frozen_at")


class CampaignWorkflowReference(FrozenIdentityModel):
    """Constant-size scheduler/workflow input; no questions or slot rows."""

    schema_version: Literal["collection-campaign-workflow-ref-v1"] = (
        "collection-campaign-workflow-ref-v1"
    )
    tenant_id: UUID
    project_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    config_revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    config_revision_hash: str = Field(pattern=_SHA256_PATTERN)
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    membership_hash: str = Field(pattern=_SHA256_PATTERN)
    partition_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    start_slot_ordinal: int = Field(strict=True, ge=0)
    end_slot_ordinal_exclusive: int = Field(strict=True, ge=1)
    cursor: int = Field(strict=True, ge=0)
    page_size: int = Field(strict=True, ge=1, le=MAX_CAMPAIGN_EXECUTION_PAGE_SIZE)

    @model_validator(mode="after")
    def range_and_cursor_are_bounded(self) -> Self:
        if not (self.start_slot_ordinal <= self.cursor < self.end_slot_ordinal_exclusive):
            raise ValueError("workflow_reference_cursor_out_of_partition")
        return self

    @property
    def payload_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class PersistencePlanStep(FrozenIdentityModel):
    ordinal: int = Field(strict=True, ge=1)
    action: Literal["insert", "update"]
    table: str = Field(pattern=_DOMAIN_TOKEN_PATTERN)
    row_count: int = Field(strict=True, ge=1)
    requires_parent_state: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_PATTERN)
    produces_parent_state: str | None = Field(default=None, pattern=_DOMAIN_TOKEN_PATTERN)


class ConfigPersistencePlan(FrozenIdentityModel):
    """Values for one transaction that cannot expose a half-frozen config."""

    frozen: FrozenConfigRevision

    @property
    def ordered_steps(self) -> tuple[PersistencePlanStep, ...]:
        return (
            PersistencePlanStep(
                ordinal=1,
                action="insert",
                table="collection_config_revision_v2",
                row_count=1,
                produces_parent_state=ConfigLifecycleState.CANDIDATE.value,
            ),
            PersistencePlanStep(
                ordinal=2,
                action="insert",
                table="collection_config_target_v2",
                row_count=len(self.frozen.targets),
                requires_parent_state=ConfigLifecycleState.CANDIDATE.value,
            ),
            PersistencePlanStep(
                ordinal=3,
                action="update",
                table="collection_config_revision_v2",
                row_count=1,
                requires_parent_state=ConfigLifecycleState.CANDIDATE.value,
                produces_parent_state=ConfigLifecycleState.FROZEN.value,
            ),
        )

    @property
    def parent_insert_values(self) -> dict[str, object]:
        return {
            **self.frozen.revision_row_values,
            "lifecycle_state": ConfigLifecycleState.CANDIDATE.value,
            "frozen_at": None,
            "activated_at": None,
        }

    @property
    def target_insert_values(self) -> tuple[dict[str, object], ...]:
        return self.frozen.target_row_values

    @property
    def finalization_match_values(self) -> dict[str, object]:
        return {
            "id": self.frozen.id,
            "tenant_id": self.frozen.tenant_id,
            "project_id": self.frozen.project_id,
            "revision_hash": self.frozen.revision_hash,
            "lifecycle_state": ConfigLifecycleState.CANDIDATE.value,
        }

    @property
    def finalization_values(self) -> dict[str, object]:
        return {
            "lifecycle_state": ConfigLifecycleState.FROZEN.value,
            "frozen_at": self.frozen.frozen_at,
        }


class CampaignMaterializationCheckpoint(FrozenIdentityModel):
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    next_slot_ordinal: int = Field(strict=True, ge=0)
    materialized_slot_count: int = Field(strict=True, ge=0)
    membership_chain_hash: str = Field(pattern=_SHA256_PATTERN)


class CampaignSlotChunk(FrozenIdentityModel):
    """One bounded, deterministic, independently idempotent slot range."""

    campaign_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    batch_id: UUID
    batch_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    idempotency_key: str = Field(pattern=_OPAQUE_ID_PATTERN)
    start_slot_ordinal: int = Field(strict=True, ge=0)
    end_slot_ordinal_exclusive: int = Field(strict=True, ge=1)
    prior_membership_chain_hash: str = Field(pattern=_SHA256_PATTERN)
    membership_chain_hash: str = Field(pattern=_SHA256_PATTERN)
    chunk_hash: str = Field(pattern=_SHA256_PATTERN)
    slots: tuple[FrozenCampaignSlot, ...] = Field(min_length=1)
    expected_slot_count: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def range_is_contiguous(self) -> Self:
        if self.end_slot_ordinal_exclusive - self.start_slot_ordinal != len(self.slots):
            raise ValueError("campaign_chunk_range_count_mismatch")
        if tuple(slot.ordinal for slot in self.slots) != tuple(
            range(self.start_slot_ordinal, self.end_slot_ordinal_exclusive)
        ):
            raise ValueError("campaign_chunk_slot_ordinal_gap")
        return self

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    @property
    def next_cursor(self) -> int:
        return self.end_slot_ordinal_exclusive

    @property
    def is_complete(self) -> bool:
        return self.next_cursor == self.expected_slot_count

    @property
    def batch_insert_values(self) -> dict[str, object]:
        return {
            "id": self.batch_id,
            "pub_id": self.batch_pub_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "campaign_id": self.campaign_id,
            "specification_hash": self.specification_hash,
            "slot_generator_version": self.slot_generator_version,
            "start_slot_ordinal": self.start_slot_ordinal,
            "end_slot_ordinal_exclusive": self.end_slot_ordinal_exclusive,
            "slot_count": self.slot_count,
            "chunk_hash": self.chunk_hash,
            "prior_membership_chain_hash": self.prior_membership_chain_hash,
            "membership_chain_hash": self.membership_chain_hash,
            "idempotency_key": self.idempotency_key,
            "batch_state": "preparing",
            "committed_at": None,
        }

    @property
    def slot_row_values(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "id": slot.id,
                "pub_id": slot.pub_id,
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "campaign_id": self.campaign_id,
                "campaign_target_id": slot.campaign_target_id,
                "sampling_leg_id": slot.sampling_leg_id,
                "materialization_batch_id": self.batch_id,
                "slot_ordinal": slot.ordinal,
                "slot_key": slot.slot_key,
                "slot_identity_hash": slot.slot_identity_hash,
                "question_slot_id": slot.identity.question_slot_id,
                "question_revision": slot.question_revision,
                "platform": slot.identity.platform,
                "collection_surface": slot.identity.collection_surface.value,
                "product_variant": slot.identity.product_variant,
                "province_code": slot.identity.province_code,
                "interaction_mode": slot.identity.interaction_mode,
                "sample_ordinal": slot.identity.sample_ordinal,
                "slot_role": slot.identity.slot_role.value,
                "role_reason": slot.role_reason,
                "related_primary_slot_key": slot.related_primary_slot_key,
            }
            for slot in self.slots
        )

    @property
    def checkpoint(self) -> CampaignMaterializationCheckpoint:
        return CampaignMaterializationCheckpoint(
            campaign_pub_id=self.campaign_pub_id,
            specification_hash=self.specification_hash,
            next_slot_ordinal=self.next_cursor,
            materialized_slot_count=self.next_cursor,
            membership_chain_hash=self.membership_chain_hash,
        )

    @property
    def campaign_checkpoint_match_values(self) -> dict[str, object]:
        return {
            "id": self.campaign_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "specification_hash": self.specification_hash,
            "slot_generator_version": self.slot_generator_version,
            "state": "assembling",
            "materialization_cursor": self.start_slot_ordinal,
            "materialized_slot_count": self.start_slot_ordinal,
        }

    @property
    def campaign_checkpoint_values(self) -> dict[str, object]:
        return {
            "materialization_cursor": self.next_cursor,
            "materialized_slot_count": self.next_cursor,
            "materialization_state": "complete" if self.is_complete else "materializing",
        }


class CampaignFinalizationPlan(FrozenIdentityModel):
    campaign: CampaignAssemblyBlueprint
    checkpoint: CampaignMaterializationCheckpoint

    @property
    def finalization_match_values(self) -> dict[str, object]:
        return {
            "id": self.campaign.id,
            "tenant_id": self.campaign.tenant_id,
            "project_id": self.campaign.project_id,
            "specification_hash": self.campaign.specification_hash,
            "slot_generator_version": self.campaign.slot_generator_version,
            "state": "assembling",
            "materialization_state": "complete",
            "materialization_cursor": self.campaign.expected_slot_count,
            "materialized_slot_count": self.campaign.expected_slot_count,
            "expected_slot_count": self.campaign.expected_slot_count,
            "membership_hash": None,
        }

    @property
    def finalization_values(self) -> dict[str, object]:
        return {
            "membership_hash": self.checkpoint.membership_chain_hash,
            "frozen_at": self.campaign.requested_frozen_at,
            "state": "frozen",
        }


class CampaignPersistencePlan(FrozenIdentityModel):
    """Compact header/static rows plus a bounded resumable slot protocol."""

    blueprint: CampaignAssemblyBlueprint

    @property
    def ordered_steps(self) -> tuple[PersistencePlanStep, ...]:
        return (
            PersistencePlanStep(
                ordinal=1,
                action="insert",
                table="collection_campaign",
                row_count=1,
                produces_parent_state="assembling",
            ),
            PersistencePlanStep(
                ordinal=2,
                action="insert",
                table="collection_campaign_target",
                row_count=len(self.blueprint.targets),
                requires_parent_state="assembling",
            ),
            PersistencePlanStep(
                ordinal=3,
                action="insert",
                table="collection_sampling_leg",
                row_count=len(self.blueprint.legs),
                requires_parent_state="assembling",
            ),
        )

    @property
    def parent_insert_values(self) -> dict[str, object]:
        return dict(self.blueprint.campaign_row_values)

    @property
    def campaign_target_insert_values(self) -> tuple[dict[str, object], ...]:
        return self.blueprint.campaign_target_row_values

    @property
    def leg_insert_values(self) -> tuple[dict[str, object], ...]:
        return self.blueprint.leg_row_values

    @property
    def initial_checkpoint(self) -> CampaignMaterializationCheckpoint:
        return initial_campaign_checkpoint(self.blueprint)

    def iter_slot_chunks(
        self,
        *,
        start_cursor: int = 0,
        chunk_size: int,
        checkpoint_digest: str | None = None,
    ) -> Iterator[CampaignSlotChunk]:
        return iter_campaign_slot_chunks(
            self.blueprint,
            start_cursor=start_cursor,
            chunk_size=chunk_size,
            checkpoint_digest=checkpoint_digest,
        )

    def validate_complete(
        self, checkpoint: CampaignMaterializationCheckpoint
    ) -> CampaignFinalizationPlan:
        _validate_campaign_checkpoint(self.blueprint, checkpoint, require_complete=True)
        return CampaignFinalizationPlan(campaign=self.blueprint, checkpoint=checkpoint)


def build_config_persistence_plan(frozen: FrozenConfigRevision) -> ConfigPersistencePlan:
    if frozen.lifecycle_state is not ConfigLifecycleState.FROZEN:
        raise IdentityV2Error(
            "config_insert_plan_requires_frozen_blueprint",
            lifecycle_state=frozen.lifecycle_state.value,
        )
    return ConfigPersistencePlan(frozen=frozen)


def build_campaign_persistence_plan(
    blueprint: CampaignAssemblyBlueprint,
) -> CampaignPersistencePlan:
    return CampaignPersistencePlan(blueprint=blueprint)


_LIFECYCLE_TRANSITIONS: Mapping[ConfigLifecycleState, frozenset[ConfigLifecycleState]] = {
    ConfigLifecycleState.DRAFT: frozenset(
        {ConfigLifecycleState.CANDIDATE, ConfigLifecycleState.RETIRED}
    ),
    ConfigLifecycleState.CANDIDATE: frozenset(
        {
            ConfigLifecycleState.DRAFT,
            ConfigLifecycleState.FROZEN,
            ConfigLifecycleState.RETIRED,
        }
    ),
    ConfigLifecycleState.FROZEN: frozenset(
        {ConfigLifecycleState.ACTIVE, ConfigLifecycleState.RETIRED}
    ),
    ConfigLifecycleState.ACTIVE: frozenset(
        {ConfigLifecycleState.SUPERSEDED, ConfigLifecycleState.RETIRED}
    ),
    ConfigLifecycleState.SUPERSEDED: frozenset({ConfigLifecycleState.RETIRED}),
    ConfigLifecycleState.RETIRED: frozenset(),
}


def transition_config_lifecycle(
    current: ConfigLifecycleState, target: ConfigLifecycleState
) -> ConfigLifecycleState:
    """Validate a lifecycle transition without mutating persisted content."""

    if target not in _LIFECYCLE_TRANSITIONS[current]:
        raise IdentityV2Error(
            "invalid_config_lifecycle_transition",
            current=current.value,
            target=target.value,
        )
    return target


def validate_config_candidate(
    config: CollectionConfigV2,
    capability_registry: CapabilityRegistry,
    *,
    current_state: ConfigLifecycleState = ConfigLifecycleState.DRAFT,
) -> CandidateValidation:
    """Perform canonical schema/static-capability validation for candidate state."""

    if current_state is not ConfigLifecycleState.CANDIDATE:
        transition_config_lifecycle(current_state, ConfigLifecycleState.CANDIDATE)
    capabilities = _validate_capabilities(config, capability_registry)
    return CandidateValidation(
        canonical_json=config.canonical_json,
        revision_hash=config.revision_hash,
        capability_registry_revision=capability_registry.registry_revision,
        capability_revisions=tuple(capability.capability_revision for capability in capabilities),
    )


def freeze_config(request: ConfigFreezeRequest) -> FrozenConfigRevision:
    """Freeze one canonical config and its exact capability revisions."""

    if request.current_state is not ConfigLifecycleState.CANDIDATE:
        raise IdentityV2Error(
            "config_freeze_requires_candidate",
            current_state=request.current_state.value,
        )
    transition_config_lifecycle(request.current_state, ConfigLifecycleState.FROZEN)
    capabilities = _validate_capabilities(request.config, request.capability_registry)
    capabilities_by_target: dict[
        tuple[str, CollectionSurface, str], list[CapabilityDeclaration]
    ] = {}
    targets_by_key = {target.target_key: target for target in request.config.collection_targets}
    for capability in capabilities:
        key = (
            capability.platform,
            capability.collection_surface,
            capability.product_variant,
        )
        capabilities_by_target.setdefault(key, []).append(capability)

    revision_id = _stable_uuid("config-revision", request.revision_pub_id)
    frozen_targets: list[FrozenConfigTarget] = []
    for target_key in sorted(targets_by_key):
        target = targets_by_key[target_key]
        bindings = tuple(
            CapabilityRevisionBinding(
                interaction_mode=capability.interaction_mode,
                capability_revision=capability.capability_revision,
                status=capability.status,
                production_allowed=capability.production_allowed,
                region_policy_revision=capability.region_policy_revision,
            )
            for capability in capabilities_by_target[target.identity]
        )
        pub_id = _stable_pub_id("cgt2", request.revision_pub_id, target_key)
        frozen_targets.append(
            FrozenConfigTarget(
                id=_stable_uuid("config-target", pub_id),
                pub_id=pub_id,
                target=target,
                capability_bindings=bindings,
            )
        )

    return FrozenConfigRevision(
        id=revision_id,
        revision_pub_id=request.revision_pub_id,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        revision=request.revision,
        parent_revision_id=request.parent_revision_id,
        lifecycle_state=ConfigLifecycleState.FROZEN,
        config=request.config,
        canonical_json=request.config.canonical_json,
        revision_hash=request.config.revision_hash,
        capability_registry_revision=request.capability_registry.registry_revision,
        targets=tuple(frozen_targets),
        change_reason=request.change_reason,
        change_request_pub_id=request.change_request_pub_id,
        approved_by_pub_id=request.approved_by_pub_id,
        frozen_at=request.frozen_at,
    )


validate_and_freeze_config = freeze_config


def assert_frozen_config_unchanged(
    frozen: FrozenConfigRevision, proposed_config: CollectionConfigV2
) -> None:
    """Reject semantic in-place edits to frozen/active revisions."""

    if frozen.lifecycle_state not in {
        ConfigLifecycleState.FROZEN,
        ConfigLifecycleState.ACTIVE,
        ConfigLifecycleState.SUPERSEDED,
        ConfigLifecycleState.RETIRED,
    }:
        raise IdentityV2Error(
            "immutability_check_requires_immutable_revision",
            lifecycle_state=frozen.lifecycle_state.value,
        )
    if proposed_config.revision_hash != frozen.revision_hash:
        raise IdentityV2Error(
            "immutable_frozen_revision",
            frozen_hash=frozen.revision_hash,
            proposed_hash=proposed_config.revision_hash,
        )


def activate_frozen_config(
    frozen: FrozenConfigRevision,
    *,
    activated_at: datetime,
    readiness_passed: bool,
) -> FrozenConfigRevision:
    """Apply an externally computed dynamic-readiness decision, fail closed."""

    _require_aware_datetime(activated_at, field="activated_at")
    if not readiness_passed:
        raise IdentityV2Error("dynamic_resource_admission_failed")
    for target in frozen.targets:
        for binding in target.capability_bindings:
            if not binding.production_allowed:
                raise IdentityV2Error(
                    "capability_not_production_allowed",
                    target_key=target.target_key,
                    interaction_mode=binding.interaction_mode,
                    capability_revision=binding.capability_revision,
                )
    transition_config_lifecycle(frozen.lifecycle_state, ConfigLifecycleState.ACTIVE)
    return frozen.model_copy(
        update={
            "lifecycle_state": ConfigLifecycleState.ACTIVE,
            "activated_at": activated_at,
        }
    )


def build_non_primary_slot_request(
    *,
    question_slot_id: str,
    target: CollectionTarget,
    province_code: str,
    interaction_mode: str,
    sample_ordinal: int,
    slot_role: SlotRole,
    reason: str,
) -> NonPrimarySlotRequest:
    """Build an explicit supplementary/top-up member before campaign freeze."""

    if slot_role is SlotRole.PRIMARY:
        raise IdentityV2Error(
            "non_primary_slot_role_required",
            slot_role=slot_role.value,
        )
    if not re.fullmatch(_AUDIT_REASON_PATTERN, reason):
        raise IdentityV2Error("invalid_non_primary_slot_reason")
    return NonPrimarySlotRequest(
        question_slot_id=question_slot_id,
        platform=target.platform,
        collection_surface=target.collection_surface,
        product_variant=target.product_variant,
        province_code=province_code,
        interaction_mode=interaction_mode,
        sample_ordinal=sample_ordinal,
        slot_role=slot_role,
        reason=reason,
    )


def freeze_campaign(request: CampaignFreezeRequest) -> CampaignAssemblyBlueprint:
    """Freeze a compact deterministic specification, never expanded slot rows."""

    config_revision = request.config_revision
    campaign_id = _stable_uuid("campaign", request.campaign_pub_id)
    targets: list[FrozenCampaignTarget] = []
    legs: list[FrozenSamplingLeg] = []

    for config_target in config_revision.targets:
        campaign_target_pub_id = _stable_pub_id(
            "cmt2", request.campaign_pub_id, config_target.target_key
        )
        campaign_target = FrozenCampaignTarget(
            id=_stable_uuid("campaign-target", campaign_target_pub_id),
            pub_id=campaign_target_pub_id,
            config_target_id=config_target.id,
            config_target_pub_id=config_target.pub_id,
            target=config_target.target,
            capability_bindings=config_target.capability_bindings,
            binding_policy_revision=request.binding_policy_revision,
        )
        targets.append(campaign_target)
        for province_code in config_revision.config.province_codes:
            for interaction_mode in config_target.target.interaction_modes:
                legs.append(
                    _build_sampling_leg(
                        campaign_pub_id=request.campaign_pub_id,
                        campaign_target=campaign_target,
                        province_code=province_code,
                        interaction_mode=interaction_mode,
                    )
                )

    expected_primary_count = _expected_primary_slot_count(
        config_revision,
        question_count=len(request.question_slots),
    )
    supplementary_specs = tuple(
        sorted(
            (
                CampaignSupplementarySlotSpec(
                    request=supplementary,
                    primary_ordinal=_primary_ordinal_for_supplementary(
                        config_revision,
                        request.question_slots,
                        supplementary,
                    ),
                )
                for supplementary in request.supplementary_slots
            ),
            key=lambda value: (
                value.primary_ordinal,
                _slot_role_order(value.request.slot_role),
            ),
        )
    )
    for previous, current in zip(supplementary_specs, supplementary_specs[1:], strict=False):
        if (
            previous.primary_ordinal == current.primary_ordinal
            and previous.request.slot_role is current.request.slot_role
        ):
            raise IdentityV2Error(
                "duplicate_campaign_slot",
                primary_ordinal=current.primary_ordinal,
                slot_role=current.request.slot_role.value,
            )

    expected_non_primary_count = len(supplementary_specs)
    expected_slot_count = _checked_slot_add(
        expected_primary_count,
        expected_non_primary_count,
    )
    membership_payload: dict[str, object] = {
        "schema_version": CAMPAIGN_MEMBERSHIP_SCHEMA_VERSION,
        "campaign_id": request.campaign_pub_id,
        "config_revision_pub_id": config_revision.revision_pub_id,
        "config_revision_hash": config_revision.revision_hash,
        "question_set_revision": config_revision.config.question_set_revision,
        "question_slots": [question.model_dump(mode="json") for question in request.question_slots],
        "time_window_key": request.time_window_key,
        "binding_policy_revision": request.binding_policy_revision,
        "slot_generator_version": CAMPAIGN_SLOT_GENERATOR_VERSION,
        "membership_digest_version": CAMPAIGN_MEMBERSHIP_DIGEST_VERSION,
        "province_codes": list(config_revision.config.province_codes),
        "samples_per_cell": config_revision.config.samples_per_cell,
        "expected_primary_slot_count": expected_primary_count,
        "expected_non_primary_slot_count": expected_non_primary_count,
        "expected_slot_count": expected_slot_count,
        "targets": [
            {
                "target_key": target.target_key,
                "capability_revisions": dict(target.capability_revision_mapping),
            }
            for target in targets
        ],
        "supplementary_slots": [
            {
                **spec.request.model_dump(mode="json"),
                "primary_ordinal": spec.primary_ordinal,
            }
            for spec in supplementary_specs
        ],
    }
    specification_json = _canonical_json(membership_payload)
    specification_hash = sha256(specification_json.encode("utf-8")).hexdigest()
    return CampaignAssemblyBlueprint(
        id=campaign_id,
        campaign_pub_id=request.campaign_pub_id,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        config_revision_id=config_revision.id,
        config_revision_pub_id=config_revision.revision_pub_id,
        config_revision_hash=config_revision.revision_hash,
        question_set_revision=config_revision.config.question_set_revision,
        question_slots=request.question_slots,
        time_window_key=request.time_window_key,
        run_trigger_source=request.run_trigger_source,
        trigger_idempotency_key=request.trigger_idempotency_key,
        actors=request.actors,
        binding_policy_revision=request.binding_policy_revision,
        targets=tuple(targets),
        legs=tuple(legs),
        province_codes=config_revision.config.province_codes,
        samples_per_cell=config_revision.config.samples_per_cell,
        supplementary_specs=supplementary_specs,
        membership_specification_json=specification_json,
        specification_hash=specification_hash,
        expected_primary_slot_count=expected_primary_count,
        expected_non_primary_slot_count=expected_non_primary_count,
        expected_slot_count=expected_slot_count,
        requested_frozen_at=request.frozen_at,
    )


def slot_for_retry(
    campaign: CampaignAssemblyBlueprint,
    *,
    slot_ordinal: int,
    slot_key: str,
) -> FrozenCampaignSlot:
    """Re-derive one exact slot; retry never scans or creates another ordinal."""

    slot = campaign_slot_at(campaign, slot_ordinal)
    if slot.slot_key != slot_key:
        raise IdentityV2Error(
            "campaign_slot_retry_identity_mismatch",
            slot_ordinal=slot_ordinal,
        )
    return slot


def campaign_slot_at(
    campaign: CampaignAssemblyBlueprint,
    ordinal: int,
) -> FrozenCampaignSlot:
    """Derive exactly one slot from its stable zero-based membership ordinal."""

    _validate_slot_ordinal(campaign, ordinal)
    low = 0
    high = campaign.expected_primary_slot_count - 1
    while low <= high:
        midpoint = (low + high) // 2
        global_ordinal = _global_ordinal_for_primary(campaign, midpoint)
        if global_ordinal <= ordinal:
            low = midpoint + 1
        else:
            high = midpoint - 1
    primary_ordinal = high
    if primary_ordinal < 0:
        raise IdentityV2Error("campaign_slot_ordinal_not_found", slot_ordinal=ordinal)
    if _global_ordinal_for_primary(campaign, primary_ordinal) == ordinal:
        return _primary_slot_at(campaign, primary_ordinal)
    supplementary_index = ordinal - primary_ordinal - 1
    if (
        supplementary_index < 0
        or supplementary_index >= campaign.expected_non_primary_slot_count
        or campaign.supplementary_specs[supplementary_index].primary_ordinal != primary_ordinal
    ):
        raise IdentityV2Error("campaign_slot_ordinal_not_found", slot_ordinal=ordinal)
    return _supplementary_slot_at(campaign, supplementary_index)


def initial_campaign_checkpoint(
    campaign: CampaignAssemblyBlueprint,
) -> CampaignMaterializationCheckpoint:
    return CampaignMaterializationCheckpoint(
        campaign_pub_id=campaign.campaign_pub_id,
        specification_hash=campaign.specification_hash,
        next_slot_ordinal=0,
        materialized_slot_count=0,
        membership_chain_hash=_campaign_membership_seed(campaign),
    )


def campaign_membership_digest_at_cursor(
    campaign: CampaignAssemblyBlueprint,
    *,
    cursor: int,
) -> str:
    """Recompute a cursor checkpoint in constant memory and canonical order."""

    if isinstance(cursor, bool) or not isinstance(cursor, int):
        raise IdentityV2Error("campaign_cursor_must_be_integer")
    if cursor < 0 or cursor > campaign.expected_slot_count:
        raise IdentityV2Error(
            "campaign_cursor_out_of_range",
            cursor=cursor,
            expected_slot_count=campaign.expected_slot_count,
        )
    digest = _campaign_membership_seed(campaign)
    for ordinal in range(cursor):
        digest = _advance_membership_chain(digest, campaign_slot_at(campaign, ordinal))
    return digest


def iter_campaign_slot_chunks(
    campaign: CampaignAssemblyBlueprint,
    *,
    start_cursor: int = 0,
    chunk_size: int,
    checkpoint_digest: str | None = None,
) -> Iterator[CampaignSlotChunk]:
    """Yield bounded chunks; chunk size never changes slot identity or final digest."""

    if isinstance(start_cursor, bool) or not isinstance(start_cursor, int):
        raise IdentityV2Error("campaign_cursor_must_be_integer")
    if start_cursor < 0 or start_cursor > campaign.expected_slot_count:
        raise IdentityV2Error(
            "campaign_cursor_out_of_range",
            cursor=start_cursor,
            expected_slot_count=campaign.expected_slot_count,
        )
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise IdentityV2Error("campaign_chunk_size_must_be_integer")
    if chunk_size < 1 or chunk_size > MAX_CAMPAIGN_SLOT_CHUNK_SIZE:
        raise IdentityV2Error(
            "campaign_chunk_size_out_of_range",
            chunk_size=chunk_size,
            maximum=MAX_CAMPAIGN_SLOT_CHUNK_SIZE,
        )
    if checkpoint_digest is None:
        membership_digest = campaign_membership_digest_at_cursor(
            campaign,
            cursor=start_cursor,
        )
    elif not re.fullmatch(_SHA256_PATTERN, checkpoint_digest):
        raise IdentityV2Error("campaign_checkpoint_digest_invalid")
    else:
        membership_digest = checkpoint_digest

    cursor = start_cursor
    while cursor < campaign.expected_slot_count:
        end_cursor = min(cursor + chunk_size, campaign.expected_slot_count)
        slots = tuple(campaign_slot_at(campaign, ordinal) for ordinal in range(cursor, end_cursor))
        next_membership_digest = membership_digest
        for slot in slots:
            next_membership_digest = _advance_membership_chain(next_membership_digest, slot)
        chunk_hash = _campaign_chunk_hash(
            campaign,
            start_cursor=cursor,
            end_cursor=end_cursor,
            slots=slots,
        )
        batch_pub_id = _stable_pub_id(
            "cmb2",
            campaign.campaign_pub_id,
            campaign.specification_hash,
            str(cursor),
            str(end_cursor),
            chunk_hash,
        )
        yield CampaignSlotChunk(
            campaign_id=campaign.id,
            campaign_pub_id=campaign.campaign_pub_id,
            tenant_id=campaign.tenant_id,
            project_id=campaign.project_id,
            specification_hash=campaign.specification_hash,
            batch_id=_stable_uuid("campaign-materialization-batch", batch_pub_id),
            batch_pub_id=batch_pub_id,
            idempotency_key=batch_pub_id,
            start_slot_ordinal=cursor,
            end_slot_ordinal_exclusive=end_cursor,
            prior_membership_chain_hash=membership_digest,
            membership_chain_hash=next_membership_digest,
            chunk_hash=chunk_hash,
            slots=slots,
            expected_slot_count=campaign.expected_slot_count,
        )
        cursor = end_cursor
        membership_digest = next_membership_digest


def advance_campaign_checkpoint(
    campaign: CampaignAssemblyBlueprint,
    checkpoint: CampaignMaterializationCheckpoint,
    chunk: CampaignSlotChunk,
) -> CampaignMaterializationCheckpoint:
    """Pure exact-match transition used after one independently committed chunk."""

    _validate_campaign_checkpoint(campaign, checkpoint, require_complete=False)
    _validate_chunk_lineage(campaign, chunk)
    if (
        chunk.start_slot_ordinal != checkpoint.next_slot_ordinal
        or chunk.prior_membership_chain_hash != checkpoint.membership_chain_hash
    ):
        raise IdentityV2Error(
            "campaign_chunk_checkpoint_mismatch",
            checkpoint_cursor=checkpoint.next_slot_ordinal,
            chunk_start=chunk.start_slot_ordinal,
        )
    expected = next(
        iter_campaign_slot_chunks(
            campaign,
            start_cursor=chunk.start_slot_ordinal,
            chunk_size=chunk.slot_count,
            checkpoint_digest=checkpoint.membership_chain_hash,
        )
    )
    if (
        expected.chunk_hash != chunk.chunk_hash
        or expected.membership_chain_hash != chunk.membership_chain_hash
        or expected.slots != chunk.slots
    ):
        raise IdentityV2Error("campaign_chunk_exact_match_failed")
    return chunk.checkpoint


def confirm_campaign_frozen(
    campaign: CampaignAssemblyBlueprint,
    checkpoint: CampaignMaterializationCheckpoint,
    confirmation: PersistedCampaignFreezeConfirmation,
) -> FrozenCampaign:
    """Construct scheduler-safe proof only from a complete chain and fresh DB row."""

    _validate_campaign_checkpoint(campaign, checkpoint, require_complete=True)
    if (
        confirmation.campaign_id != campaign.id
        or confirmation.campaign_pub_id != campaign.campaign_pub_id
        or confirmation.tenant_id != campaign.tenant_id
        or confirmation.project_id != campaign.project_id
        or confirmation.specification_hash != campaign.specification_hash
        or confirmation.slot_generator_version != campaign.slot_generator_version
        or confirmation.membership_digest_version != campaign.membership_digest_version
        or confirmation.expected_slot_count != campaign.expected_slot_count
        or confirmation.materialized_slot_count != campaign.expected_slot_count
        or confirmation.materialization_cursor != campaign.expected_slot_count
        or confirmation.membership_hash != checkpoint.membership_chain_hash
    ):
        raise IdentityV2Error("persisted_campaign_freeze_confirmation_mismatch")
    return FrozenCampaign(
        id=campaign.id,
        campaign_pub_id=campaign.campaign_pub_id,
        tenant_id=campaign.tenant_id,
        project_id=campaign.project_id,
        config_revision_id=campaign.config_revision_id,
        config_revision_pub_id=campaign.config_revision_pub_id,
        config_revision_hash=campaign.config_revision_hash,
        specification_hash=campaign.specification_hash,
        slot_generator_version=campaign.slot_generator_version,
        membership_digest_version=campaign.membership_digest_version,
        expected_slot_count=campaign.expected_slot_count,
        materialized_slot_count=confirmation.materialized_slot_count,
        materialization_cursor=confirmation.materialization_cursor,
        membership_hash=confirmation.membership_hash,
        frozen_at=confirmation.frozen_at,
    )


def build_campaign_workflow_reference(
    campaign: FrozenCampaign,
    *,
    partition_pub_id: str,
    start_slot_ordinal: int,
    end_slot_ordinal_exclusive: int,
    cursor: int,
    page_size: int,
) -> CampaignWorkflowReference:
    """Build a bounded workflow payload; assembling blueprints fail closed."""

    if not isinstance(campaign, FrozenCampaign):
        raise IdentityV2Error("scheduler_requires_persisted_frozen_campaign")
    for field, value in (
        ("start_slot_ordinal", start_slot_ordinal),
        ("end_slot_ordinal_exclusive", end_slot_ordinal_exclusive),
        ("cursor", cursor),
        ("page_size", page_size),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise IdentityV2Error("workflow_reference_integer_required", field=field)
    if (
        start_slot_ordinal < 0
        or end_slot_ordinal_exclusive <= start_slot_ordinal
        or end_slot_ordinal_exclusive > campaign.expected_slot_count
    ):
        raise IdentityV2Error("workflow_reference_partition_out_of_range")
    return CampaignWorkflowReference(
        tenant_id=campaign.tenant_id,
        project_id=campaign.project_id,
        campaign_pub_id=campaign.campaign_pub_id,
        config_revision_pub_id=campaign.config_revision_pub_id,
        config_revision_hash=campaign.config_revision_hash,
        specification_hash=campaign.specification_hash,
        slot_generator_version=campaign.slot_generator_version,
        membership_digest_version=campaign.membership_digest_version,
        membership_hash=campaign.membership_hash,
        partition_pub_id=partition_pub_id,
        start_slot_ordinal=start_slot_ordinal,
        end_slot_ordinal_exclusive=end_slot_ordinal_exclusive,
        cursor=cursor,
        page_size=page_size,
    )


def _validate_campaign_checkpoint(
    campaign: CampaignAssemblyBlueprint,
    checkpoint: CampaignMaterializationCheckpoint,
    *,
    require_complete: bool,
) -> None:
    if (
        checkpoint.campaign_pub_id != campaign.campaign_pub_id
        or checkpoint.specification_hash != campaign.specification_hash
        or checkpoint.slot_generator_version != campaign.slot_generator_version
    ):
        raise IdentityV2Error("campaign_checkpoint_lineage_mismatch")
    if checkpoint.materialized_slot_count != checkpoint.next_slot_ordinal:
        raise IdentityV2Error("campaign_checkpoint_count_cursor_mismatch")
    if checkpoint.next_slot_ordinal > campaign.expected_slot_count:
        raise IdentityV2Error("campaign_checkpoint_exceeds_expected_count")
    if not require_complete:
        return
    if checkpoint.next_slot_ordinal != campaign.expected_slot_count:
        raise IdentityV2Error(
            "campaign_materialization_incomplete",
            expected_slot_count=campaign.expected_slot_count,
            materialized_slot_count=checkpoint.materialized_slot_count,
        )
    expected_digest = campaign_membership_digest_at_cursor(
        campaign,
        cursor=campaign.expected_slot_count,
    )
    if checkpoint.membership_chain_hash != expected_digest:
        raise IdentityV2Error("campaign_membership_digest_mismatch")


def _validate_chunk_lineage(
    campaign: CampaignAssemblyBlueprint,
    chunk: CampaignSlotChunk,
) -> None:
    if (
        chunk.campaign_id != campaign.id
        or chunk.campaign_pub_id != campaign.campaign_pub_id
        or chunk.tenant_id != campaign.tenant_id
        or chunk.project_id != campaign.project_id
        or chunk.specification_hash != campaign.specification_hash
        or chunk.slot_generator_version != campaign.slot_generator_version
        or chunk.expected_slot_count != campaign.expected_slot_count
    ):
        raise IdentityV2Error("campaign_chunk_lineage_mismatch")


def _campaign_membership_seed(campaign: CampaignAssemblyBlueprint) -> str:
    payload = {
        "digest_version": campaign.membership_digest_version,
        "specification_hash": campaign.specification_hash,
        "slot_generator_version": campaign.slot_generator_version,
        "expected_slot_count": campaign.expected_slot_count,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _advance_membership_chain(previous_hash: str, slot: FrozenCampaignSlot) -> str:
    membership_record = {
        "ordinal": slot.ordinal,
        "slot_identity_hash": slot.slot_identity_hash,
        "question_revision": slot.question_revision,
        "role_reason": slot.role_reason,
        "related_primary_slot_key": slot.related_primary_slot_key,
    }
    return sha256(
        bytes.fromhex(previous_hash) + _canonical_json(membership_record).encode("utf-8")
    ).hexdigest()


def _campaign_chunk_hash(
    campaign: CampaignAssemblyBlueprint,
    *,
    start_cursor: int,
    end_cursor: int,
    slots: tuple[FrozenCampaignSlot, ...],
) -> str:
    digest = sha256(
        _canonical_json(
            {
                "specification_hash": campaign.specification_hash,
                "slot_generator_version": campaign.slot_generator_version,
                "start_slot_ordinal": start_cursor,
                "end_slot_ordinal_exclusive": end_cursor,
            }
        ).encode("utf-8")
    )
    for slot in slots:
        digest.update(slot.ordinal.to_bytes(8, "big", signed=False))
        digest.update(bytes.fromhex(slot.slot_identity_hash))
    return digest.hexdigest()


def _validate_capabilities(
    config: CollectionConfigV2, capability_registry: CapabilityRegistry
) -> tuple[CapabilityDeclaration, ...]:
    try:
        return capability_registry.validate_config(config)
    except StaticCapabilityError as exc:
        raise IdentityV2Error(
            exc.code,
            target_key=exc.target_key,
            interaction_mode=exc.interaction_mode,
            capability_status=(
                exc.capability_status.value if exc.capability_status is not None else None
            ),
        ) from exc


def _supplementary_request_order_key(
    value: NonPrimarySlotRequest,
) -> tuple[str, str, str, str, int, int]:
    target_key = (
        "collection-target-v1"
        f"|platform={value.platform}"
        f"|collection_surface={value.collection_surface.value}"
        f"|product_variant={value.product_variant}"
    )
    return (
        target_key,
        value.question_slot_id,
        value.province_code,
        value.interaction_mode,
        value.sample_ordinal,
        _slot_role_order(value.slot_role),
    )


def _slot_role_order(role: SlotRole) -> int:
    if role is SlotRole.PRIMARY:
        return 0
    if role is SlotRole.SUPPLEMENTARY:
        return 1
    return 2


def _checked_slot_add(left: int, right: int) -> int:
    result = left + right
    if result > _MAX_SLOT_COUNT:
        raise IdentityV2Error("campaign_slot_count_overflow")
    return result


def _checked_slot_multiply(*values: int) -> int:
    result = 1
    for value in values:
        if value and result > _MAX_SLOT_COUNT // value:
            raise IdentityV2Error("campaign_slot_count_overflow")
        result *= value
    return result


def _expected_primary_slot_count(
    config_revision: FrozenConfigRevision,
    *,
    question_count: int,
) -> int:
    mode_count = 0
    for target in config_revision.targets:
        mode_count = _checked_slot_add(mode_count, len(target.target.interaction_modes))
    return _checked_slot_multiply(
        question_count,
        len(config_revision.config.province_codes),
        config_revision.config.samples_per_cell,
        mode_count,
    )


def _primary_ordinal_for_supplementary(
    config_revision: FrozenConfigRevision,
    question_slots: tuple[QuestionSlotRef, ...],
    supplementary: NonPrimarySlotRequest,
) -> int:
    question_index = next(
        (
            index
            for index, question in enumerate(question_slots)
            if question.question_slot_id == supplementary.question_slot_id
        ),
        None,
    )
    if question_index is None:
        raise IdentityV2Error(
            "supplementary_question_not_in_campaign",
            question_slot_id=supplementary.question_slot_id,
        )
    try:
        province_index = config_revision.config.province_codes.index(supplementary.province_code)
    except ValueError as exc:
        raise IdentityV2Error(
            "supplementary_leg_not_configured",
            province_code=supplementary.province_code,
            interaction_mode=supplementary.interaction_mode,
        ) from exc
    if supplementary.sample_ordinal > config_revision.config.samples_per_cell:
        raise IdentityV2Error(
            "supplementary_primary_slot_not_found",
            sample_ordinal=supplementary.sample_ordinal,
        )

    offset = 0
    question_count = len(question_slots)
    province_count = len(config_revision.config.province_codes)
    sample_count = config_revision.config.samples_per_cell
    for target in config_revision.targets:
        mode_count = len(target.target.interaction_modes)
        target_count = _checked_slot_multiply(
            question_count,
            province_count,
            mode_count,
            sample_count,
        )
        if target.target.identity != (
            supplementary.platform,
            supplementary.collection_surface,
            supplementary.product_variant,
        ):
            offset = _checked_slot_add(offset, target_count)
            continue
        try:
            mode_index = target.target.interaction_modes.index(supplementary.interaction_mode)
        except ValueError as exc:
            raise IdentityV2Error(
                "supplementary_leg_not_configured",
                province_code=supplementary.province_code,
                interaction_mode=supplementary.interaction_mode,
            ) from exc
        local_ordinal = (
            ((question_index * province_count + province_index) * mode_count + mode_index)
            * sample_count
            + supplementary.sample_ordinal
            - 1
        )
        return _checked_slot_add(offset, local_ordinal)
    raise IdentityV2Error(
        "supplementary_target_not_configured",
        platform=supplementary.platform,
        collection_surface=supplementary.collection_surface.value,
        product_variant=supplementary.product_variant,
    )


def _count_supplementary_before(
    campaign: CampaignAssemblyBlueprint,
    primary_ordinal: int,
) -> int:
    low = 0
    high = len(campaign.supplementary_specs)
    while low < high:
        midpoint = (low + high) // 2
        if campaign.supplementary_specs[midpoint].primary_ordinal < primary_ordinal:
            low = midpoint + 1
        else:
            high = midpoint
    return low


def _global_ordinal_for_primary(
    campaign: CampaignAssemblyBlueprint,
    primary_ordinal: int,
) -> int:
    return primary_ordinal + _count_supplementary_before(campaign, primary_ordinal)


def _primary_coordinates_at(
    campaign: CampaignAssemblyBlueprint,
    primary_ordinal: int,
) -> tuple[FrozenCampaignTarget, QuestionSlotRef, str, str, int]:
    if (
        isinstance(primary_ordinal, bool)
        or not isinstance(primary_ordinal, int)
        or primary_ordinal < 0
        or primary_ordinal >= campaign.expected_primary_slot_count
    ):
        raise IdentityV2Error(
            "campaign_primary_ordinal_out_of_range",
            primary_ordinal=primary_ordinal,
        )
    remaining = primary_ordinal
    question_count = len(campaign.question_slots)
    province_count = len(campaign.province_codes)
    sample_count = campaign.samples_per_cell
    for target in campaign.targets:
        mode_count = len(target.target.interaction_modes)
        target_count = question_count * province_count * mode_count * sample_count
        if remaining >= target_count:
            remaining -= target_count
            continue
        question_index, remaining = divmod(
            remaining,
            province_count * mode_count * sample_count,
        )
        province_index, remaining = divmod(remaining, mode_count * sample_count)
        mode_index, sample_index = divmod(remaining, sample_count)
        return (
            target,
            campaign.question_slots[question_index],
            campaign.province_codes[province_index],
            target.target.interaction_modes[mode_index],
            sample_index + 1,
        )
    raise IdentityV2Error(
        "campaign_primary_ordinal_out_of_range",
        primary_ordinal=primary_ordinal,
    )


def _validate_slot_ordinal(campaign: CampaignAssemblyBlueprint, ordinal: int) -> None:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise IdentityV2Error("campaign_slot_ordinal_must_be_integer")
    if ordinal < 0 or ordinal >= campaign.expected_slot_count:
        raise IdentityV2Error(
            "campaign_slot_ordinal_out_of_range",
            slot_ordinal=ordinal,
            expected_slot_count=campaign.expected_slot_count,
        )


def _primary_slot_at(
    campaign: CampaignAssemblyBlueprint,
    primary_ordinal: int,
) -> FrozenCampaignSlot:
    target, question, province_code, interaction_mode, sample_ordinal = _primary_coordinates_at(
        campaign, primary_ordinal
    )
    identity = SlotIdentity(
        campaign_id=campaign.campaign_pub_id,
        question_slot_id=question.question_slot_id,
        platform=target.target.platform,
        collection_surface=target.target.collection_surface,
        product_variant=target.target.product_variant,
        province_code=province_code,
        interaction_mode=interaction_mode,
        sample_ordinal=sample_ordinal,
        slot_role=SlotRole.PRIMARY,
    )
    return _build_frozen_slot(
        ordinal=_global_ordinal_for_primary(campaign, primary_ordinal),
        identity=identity,
        question_revision=question.question_revision,
        campaign_target=target,
        leg=_build_sampling_leg(
            campaign_pub_id=campaign.campaign_pub_id,
            campaign_target=target,
            province_code=province_code,
            interaction_mode=interaction_mode,
        ),
    )


def _supplementary_slot_at(
    campaign: CampaignAssemblyBlueprint,
    supplementary_index: int,
) -> FrozenCampaignSlot:
    if (
        isinstance(supplementary_index, bool)
        or not isinstance(supplementary_index, int)
        or supplementary_index < 0
        or supplementary_index >= campaign.expected_non_primary_slot_count
    ):
        raise IdentityV2Error(
            "campaign_supplementary_index_out_of_range",
            supplementary_index=supplementary_index,
        )
    spec = campaign.supplementary_specs[supplementary_index]
    request = spec.request
    target = next(
        target
        for target in campaign.targets
        if target.target.identity
        == (request.platform, request.collection_surface, request.product_variant)
    )
    question = next(
        question
        for question in campaign.question_slots
        if question.question_slot_id == request.question_slot_id
    )
    identity = SlotIdentity(
        campaign_id=campaign.campaign_pub_id,
        question_slot_id=request.question_slot_id,
        platform=request.platform,
        collection_surface=request.collection_surface,
        product_variant=request.product_variant,
        province_code=request.province_code,
        interaction_mode=request.interaction_mode,
        sample_ordinal=request.sample_ordinal,
        slot_role=request.slot_role,
    )
    primary_identity = identity.model_copy(update={"slot_role": SlotRole.PRIMARY})
    return _build_frozen_slot(
        ordinal=spec.primary_ordinal + 1 + supplementary_index,
        identity=identity,
        question_revision=question.question_revision,
        campaign_target=target,
        leg=_build_sampling_leg(
            campaign_pub_id=campaign.campaign_pub_id,
            campaign_target=target,
            province_code=request.province_code,
            interaction_mode=request.interaction_mode,
        ),
        role_reason=request.reason,
        related_primary_slot_key=primary_identity.slot_key,
    )


def _build_leg_key(
    *,
    campaign_pub_id: str,
    target: CollectionTarget,
    province_code: str,
    interaction_mode: str,
) -> str:
    return (
        "collection-leg-v1"
        f"|campaign_id={campaign_pub_id}"
        f"|platform={target.platform}"
        f"|collection_surface={target.collection_surface.value}"
        f"|product_variant={target.product_variant}"
        f"|province_code={province_code}"
        f"|interaction_mode={interaction_mode}"
    )


def _build_sampling_leg(
    *,
    campaign_pub_id: str,
    campaign_target: FrozenCampaignTarget,
    province_code: str,
    interaction_mode: str,
) -> FrozenSamplingLeg:
    leg_key = _build_leg_key(
        campaign_pub_id=campaign_pub_id,
        target=campaign_target.target,
        province_code=province_code,
        interaction_mode=interaction_mode,
    )
    leg_pub_id = _stable_pub_id("csl2", leg_key)
    return FrozenSamplingLeg(
        id=_stable_uuid("sampling-leg", leg_pub_id),
        pub_id=leg_pub_id,
        campaign_target_id=campaign_target.id,
        campaign_target_pub_id=campaign_target.pub_id,
        leg_key=leg_key,
        platform=campaign_target.target.platform,
        collection_surface=campaign_target.target.collection_surface,
        product_variant=campaign_target.target.product_variant,
        province_code=province_code,
        interaction_mode=interaction_mode,
    )


def _build_frozen_slot(
    *,
    ordinal: int,
    identity: SlotIdentity,
    question_revision: str,
    campaign_target: FrozenCampaignTarget,
    leg: FrozenSamplingLeg,
    role_reason: str | None = None,
    related_primary_slot_key: str | None = None,
) -> FrozenCampaignSlot:
    pub_id = _stable_pub_id("cps2", identity.slot_key)
    return FrozenCampaignSlot(
        id=_stable_uuid("campaign-slot", pub_id),
        pub_id=pub_id,
        ordinal=ordinal,
        slot_identity_hash=sha256(identity.slot_key.encode("utf-8")).hexdigest(),
        campaign_target_id=campaign_target.id,
        campaign_target_pub_id=campaign_target.pub_id,
        sampling_leg_id=leg.id,
        sampling_leg_pub_id=leg.pub_id,
        identity=identity,
        question_revision=question_revision,
        role_reason=role_reason,
        related_primary_slot_key=related_primary_slot_key,
    )


def _slot_base_key(identity: SlotIdentity) -> str:
    primary = identity.model_copy(update={"slot_role": SlotRole.PRIMARY})
    return primary.slot_key


def _stable_pub_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[: 29 - len(prefix)]}"


def _stable_uuid(kind: str, public_id: str) -> UUID:
    return uuid5(_IDENTITY_NAMESPACE, f"{kind}|{public_id}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_aware_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return value
