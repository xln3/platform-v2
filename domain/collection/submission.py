"""Pure at-most-once submission and capture-existing protocol.

This module contains decisions and immutable messages only.  Persistence owners
must serialize the owner claim with compare-and-swap; adapters implement the
ports below.  In particular, a durable ``SENDING`` row is never converted back
into an executable submit capability during recovery.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum, StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Protocol, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.collection.surface import (
    AnalysisState,
    CaptureState,
    CollectionSurface,
    CollectionTarget,
    SendState,
)

SUBMISSION_PROTOCOL_VERSION: Literal["collection-submission-v1"] = "collection-submission-v1"
REQUEST_MANIFEST_VERSION: Literal["collection-request-manifest-v1"] = (
    "collection-request-manifest-v1"
)

OpaqueId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ProtocolId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$"),
]
TargetKey = Annotated[str, Field(min_length=1, max_length=500)]
LegKey = Annotated[str, Field(min_length=1, max_length=1000)]
LogicalItemKey = Annotated[str, Field(min_length=1, max_length=1500)]
MediaType = Annotated[
    str,
    Field(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+/-]{1,127}$",
    ),
]


class SubmissionProtocolError(ValueError):
    """Fail-closed protocol error with a stable, non-secret code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FrozenProtocolModel(BaseModel):
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


def canonical_json(value: BaseModel | dict[str, object]) -> str:
    """Return the protocol's stable JSON encoding."""

    payload = _canonical_json_value(value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_json_value(value: object) -> object:
    """Normalize protocol values before hashing or durable JSON comparison.

    PostgreSQL ``timestamptz`` values round-trip as UTC instants, while gateway
    inputs may use another explicit offset.  Converting every nested datetime
    to the same ``Z`` representation prevents an offset spelling from changing
    an identity or evidence digest.  The remaining conversions retain the
    existing canonical UUID, enum, and tuple representations.
    """

    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise SubmissionProtocolError("datetime_must_be_timezone_aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_json_value(value.value)
    if isinstance(value, dict):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_json_value(item) for item in value]
    return value


def _digest(value: BaseModel | dict[str, object]) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


class RequestManifest(FrozenProtocolModel):
    """Constant-size reference to an immutable prepared request."""

    manifest_version: Literal["collection-request-manifest-v1"] = REQUEST_MANIFEST_VERSION
    request_protocol_version: ProtocolId
    request_schema_revision: OpaqueId
    request_payload_ref: OpaqueId
    request_payload_sha256: Sha256Hex


def request_manifest_digest(manifest: RequestManifest) -> str:
    return _digest(manifest)


class OperationKeyMaterial(FrozenProtocolModel):
    """Only logical identity participates in the operation key.

    Attempts, workers, timestamps and physical resource assignments are absent
    by construction.  ``generation`` is explicit so a deliberate new operation
    can be distinguished from recovery of the same operation.
    """

    protocol_version: Literal["collection-submission-v1"] = SUBMISSION_PROTOCOL_VERSION
    tenant_id: UUID
    project_id: UUID
    campaign_pub_id: OpaqueId
    slot_pub_id: OpaqueId
    target_key: TargetKey
    leg_key: LegKey
    logical_item_key: LogicalItemKey
    generation: int = Field(strict=True, ge=1)
    operation_policy_revision: OpaqueId


class SurfaceProductRef(FrozenProtocolModel):
    platform: OpaqueId
    collection_surface: CollectionSurface
    product_variant: OpaqueId
    target_key: TargetKey

    @model_validator(mode="after")
    def target_key_matches_dimensions(self) -> Self:
        expected = CollectionTarget(
            platform=self.platform,
            collection_surface=self.collection_surface,
            product_variant=self.product_variant,
            interaction_modes=("normal",),
        ).target_key
        if self.target_key != expected:
            raise ValueError("surface_product_target_key_mismatch")
        return self


def deterministic_operation_key(material: OperationKeyMaterial) -> str:
    return f"operation-v1-{_digest(material)}"


def deterministic_provider_idempotency_key(operation_key: str) -> str:
    return f"submit-v1-{sha256(operation_key.encode()).hexdigest()}"


def deterministic_outbox_key(
    *, event_type: str, aggregate_ref: str, aggregate_version: int, payload_sha256: str
) -> str:
    if aggregate_version < 1:
        raise SubmissionProtocolError("outbox_aggregate_version_invalid")
    material = {
        "aggregate_ref": aggregate_ref,
        "aggregate_version": aggregate_version,
        "event_type": event_type,
        "payload_sha256": payload_sha256,
        "version": "collection-outbox-key-v1",
    }
    return f"outbox-v1-{_digest(material)}"


class OperationIdentity(FrozenProtocolModel):
    material: OperationKeyMaterial
    surface_product: SurfaceProductRef
    operation_pub_id: OpaqueId
    operation_key: OpaqueId
    request_manifest: RequestManifest
    request_manifest_sha256: Sha256Hex
    provider_idempotency_key: OpaqueId

    @model_validator(mode="after")
    def identities_match(self) -> Self:
        if self.surface_product.target_key != self.material.target_key:
            raise ValueError("operation_surface_target_key_mismatch")
        expected_operation_key = deterministic_operation_key(self.material)
        if self.operation_key != expected_operation_key:
            raise ValueError("operation_key_mismatch")
        if self.request_manifest_sha256 != request_manifest_digest(self.request_manifest):
            raise ValueError("request_manifest_digest_mismatch")
        expected_provider_key = deterministic_provider_idempotency_key(self.operation_key)
        if self.provider_idempotency_key != expected_provider_key:
            raise ValueError("provider_idempotency_key_mismatch")
        return self


class OperationRef(FrozenProtocolModel):
    """Bounded workflow/coordinator input; never contains a task collection."""

    protocol_version: Literal["collection-submission-v1"] = SUBMISSION_PROTOCOL_VERSION
    operation_pub_id: OpaqueId
    operation_key: OpaqueId
    generation: int = Field(strict=True, ge=1)
    request_manifest_sha256: Sha256Hex
    provider_idempotency_key: OpaqueId


def operation_ref(identity: OperationIdentity) -> OperationRef:
    return OperationRef(
        operation_pub_id=identity.operation_pub_id,
        operation_key=identity.operation_key,
        generation=identity.material.generation,
        request_manifest_sha256=identity.request_manifest_sha256,
        provider_idempotency_key=identity.provider_idempotency_key,
    )


def deterministic_dispatch_key(operation: OperationRef) -> str:
    material = {
        "operation_key": operation.operation_key,
        "generation": operation.generation,
        "protocol_version": operation.protocol_version,
    }
    return f"dispatch-v1-{_digest(material)}"


class WorkflowOperationInput(FrozenProtocolModel):
    operation: OperationRef
    expected_state_version: int = Field(strict=True, ge=1)


class LeaseFenceRef(FrozenProtocolModel):
    lease_pub_id: OpaqueId
    binding_resource_pub_id: OpaqueId
    resource_role: OpaqueId
    owner_handle: OpaqueId
    generation: int = Field(strict=True, ge=1)
    acquired_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def interval_is_valid(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease_interval_invalid")
        return self


def lease_fence_set_digest(fences: tuple[LeaseFenceRef, ...]) -> str:
    ordered = tuple(
        sorted(
            fences,
            key=lambda item: (
                item.resource_role,
                item.binding_resource_pub_id,
                item.lease_pub_id,
            ),
        )
    )
    return _digest(
        {
            "fences": [
                {
                    "binding_resource_pub_id": item.binding_resource_pub_id,
                    "generation": item.generation,
                    "lease_pub_id": item.lease_pub_id,
                    "owner_handle": item.owner_handle,
                    "resource_role": item.resource_role,
                }
                for item in ordered
            ],
            "version": "lease-fence-identity-v1",
        }
    )


class OwnerAuthorityRef(FrozenProtocolModel):
    grant_pub_id: OpaqueId
    grant_revision: int = Field(strict=True, ge=1)
    binding_revision_pub_id: OpaqueId
    owner_handle: OpaqueId
    checked_at: datetime
    valid_until: datetime
    lease_fences: tuple[LeaseFenceRef, ...] = Field(min_length=1, max_length=32)
    fence_set_sha256: Sha256Hex

    @model_validator(mode="after")
    def authority_is_coherent(self) -> Self:
        if self.valid_until <= self.checked_at:
            raise ValueError("authority_interval_invalid")
        identities: set[tuple[str, str]] = set()
        for fence in self.lease_fences:
            if fence.owner_handle != self.owner_handle:
                raise ValueError("lease_owner_mismatch")
            if fence.acquired_at > self.checked_at or fence.expires_at <= self.checked_at:
                raise ValueError("lease_not_usable_at_check")
            if self.valid_until > fence.expires_at:
                raise ValueError("authority_outlives_lease")
            identity = (fence.resource_role, fence.binding_resource_pub_id)
            if identity in identities:
                raise ValueError("duplicate_binding_resource_fence")
            identities.add(identity)
        if self.fence_set_sha256 != lease_fence_set_digest(self.lease_fences):
            raise ValueError("fence_set_digest_mismatch")
        return self


def authority_digest(authority: OwnerAuthorityRef) -> str:
    return _digest(authority)


class OwnerClaimTruth(FrozenProtocolModel):
    claim_pub_id: OpaqueId
    owner_handle: OpaqueId
    grant_pub_id: OpaqueId
    grant_revision: int = Field(strict=True, ge=1)
    authority_sha256: Sha256Hex
    fence_set_sha256: Sha256Hex
    dispatch_key: OpaqueId
    owner_dispatch_ref: OpaqueId
    owner_wal_evidence_sha256: Sha256Hex
    claimed_at: datetime


class TerminalReason(StrEnum):
    SUBMITTED = "submitted"
    SEND_UNKNOWN = "send_unknown"
    PREFLIGHT_NOT_SENT = "preflight_not_sent"
    POST_CLAIM_NOT_SENT = "post_claim_not_sent"
    UNAVAILABLE = "unavailable"
    INVALID_SURFACE_OR_PRODUCT = "invalid_surface_or_product"


class TerminalSubmissionTruth(FrozenProtocolModel):
    send_state: SendState
    reason: TerminalReason
    boundary_entered: bool
    evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    resolved_at: datetime
    provider_submission_ref: OpaqueId | None = None
    non_submission_proof_ref: OpaqueId | None = None
    terminated_fence_set_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def terminal_shape_is_unambiguous(self) -> Self:
        if self.send_state not in {
            SendState.CONFIRMED_SENT,
            SendState.SEND_UNKNOWN,
            SendState.CONFIRMED_NOT_SENT,
        }:
            raise ValueError("non_terminal_send_state")
        if self.send_state is SendState.CONFIRMED_SENT:
            if not self.boundary_entered or self.reason is not TerminalReason.SUBMITTED:
                raise ValueError("confirmed_sent_truth_invalid")
        elif self.send_state is SendState.SEND_UNKNOWN:
            if not self.boundary_entered or self.reason is not TerminalReason.SEND_UNKNOWN:
                raise ValueError("send_unknown_truth_invalid")
        elif self.boundary_entered or self.reason in {
            TerminalReason.SUBMITTED,
            TerminalReason.SEND_UNKNOWN,
        }:
            raise ValueError("confirmed_not_sent_truth_invalid")
        if self.provider_submission_ref is not None and not self.boundary_entered:
            raise ValueError("provider_ref_without_submit_boundary")
        if self.send_state is SendState.CONFIRMED_NOT_SENT:
            if self.terminated_fence_set_sha256 is None:
                raise ValueError("confirmed_not_sent_requires_terminated_fence")
            if self.reason is TerminalReason.POST_CLAIM_NOT_SENT:
                if self.non_submission_proof_ref is None:
                    raise ValueError("post_claim_not_sent_requires_non_submission_proof")
            elif self.non_submission_proof_ref is not None:
                raise ValueError("preflight_not_sent_cannot_have_non_submission_proof")
        elif (
            self.non_submission_proof_ref is not None
            or self.terminated_fence_set_sha256 is not None
        ):
            raise ValueError("submitted_terminal_cannot_have_non_submission_proof")
        return self


class SubmissionOperationTruth(FrozenProtocolModel):
    identity: OperationIdentity
    send_state: SendState
    state_version: int = Field(strict=True, ge=1)
    prepared_at: datetime
    claim: OwnerClaimTruth | None = None
    terminal: TerminalSubmissionTruth | None = None

    @model_validator(mode="after")
    def state_shape_is_unambiguous(self) -> Self:
        if self.send_state is SendState.NOT_SENT:
            if self.claim is not None or self.terminal is not None:
                raise ValueError("not_sent_cannot_have_claim_or_terminal")
        elif self.send_state is SendState.SENDING:
            if self.claim is None or self.terminal is not None:
                raise ValueError("sending_requires_claim_only")
        else:
            if self.terminal is None or self.terminal.send_state is not self.send_state:
                raise ValueError("terminal_truth_required")
            if self.terminal.resolved_at < self.prepared_at:
                raise ValueError("terminal_before_prepare")
            if self.terminal.reason in {
                TerminalReason.PREFLIGHT_NOT_SENT,
                TerminalReason.UNAVAILABLE,
                TerminalReason.INVALID_SURFACE_OR_PRODUCT,
            }:
                if self.claim is not None:
                    raise ValueError("preflight_not_sent_cannot_have_claim")
            elif self.claim is None:
                raise ValueError("post_claim_terminal_requires_claim")
        if self.claim is not None and self.claim.claimed_at < self.prepared_at:
            raise ValueError("claim_before_prepare")
        return self


class PrepareSubmissionCommand(FrozenProtocolModel):
    identity: OperationIdentity
    prepared_at: datetime


class PrepareDisposition(StrEnum):
    CREATED = "created"
    EXACT_REPLAY = "exact_replay"


class PrepareResult(FrozenProtocolModel):
    disposition: PrepareDisposition
    operation: SubmissionOperationTruth


def prepare_submission(
    command: PrepareSubmissionCommand,
    *,
    existing: SubmissionOperationTruth | None = None,
) -> PrepareResult:
    if existing is None:
        return PrepareResult(
            disposition=PrepareDisposition.CREATED,
            operation=SubmissionOperationTruth(
                identity=command.identity,
                send_state=SendState.NOT_SENT,
                state_version=1,
                prepared_at=command.prepared_at,
            ),
        )
    if existing.identity != command.identity or existing.prepared_at != command.prepared_at:
        raise SubmissionProtocolError("prepare_identity_drift")
    return PrepareResult(disposition=PrepareDisposition.EXACT_REPLAY, operation=existing)


class PreflightDecision(StrEnum):
    READY = "ready"
    CONFIRMED_NOT_SENT = "confirmed_not_sent"


class PreflightCommand(FrozenProtocolModel):
    operation: OperationRef
    expected_state_version: int = Field(strict=True, ge=1)
    authority: OwnerAuthorityRef


class PreflightObservation(FrozenProtocolModel):
    operation: OperationRef
    authority_sha256: Sha256Hex
    decision: PreflightDecision
    observed_at: datetime
    evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    not_sent_reason: TerminalReason | None = None

    @model_validator(mode="after")
    def decision_shape(self) -> Self:
        if self.decision is PreflightDecision.READY and self.not_sent_reason is not None:
            raise ValueError("ready_cannot_have_not_sent_reason")
        if self.decision is PreflightDecision.CONFIRMED_NOT_SENT and self.not_sent_reason not in {
            TerminalReason.PREFLIGHT_NOT_SENT,
            TerminalReason.UNAVAILABLE,
            TerminalReason.INVALID_SURFACE_OR_PRODUCT,
        }:
            raise ValueError("preflight_not_sent_reason_required")
        return self


class VerifiedPreflight(FrozenProtocolModel):
    command: PreflightCommand
    observation: PreflightObservation


class PreflightPort(Protocol):
    def preflight(self, command: PreflightCommand) -> PreflightObservation: ...


def verify_preflight(
    operation: SubmissionOperationTruth,
    command: PreflightCommand,
    observation: PreflightObservation,
) -> VerifiedPreflight:
    expected_ref = operation_ref(operation.identity)
    if operation.send_state is not SendState.NOT_SENT:
        raise SubmissionProtocolError("preflight_requires_not_sent")
    if command.operation != expected_ref or observation.operation != expected_ref:
        raise SubmissionProtocolError("preflight_operation_mismatch")
    if command.expected_state_version != operation.state_version:
        raise SubmissionProtocolError("preflight_state_version_mismatch")
    expected_authority_hash = authority_digest(command.authority)
    if observation.authority_sha256 != expected_authority_hash:
        raise SubmissionProtocolError("preflight_authority_mismatch")
    if observation.observed_at < command.authority.checked_at:
        raise SubmissionProtocolError("preflight_before_authority_check")
    if observation.observed_at >= command.authority.valid_until:
        raise SubmissionProtocolError("preflight_authority_expired")
    return VerifiedPreflight(command=command, observation=observation)


class OwnerClaimCasCommand(FrozenProtocolModel):
    operation: OperationRef
    expected_state: Literal[SendState.NOT_SENT] = SendState.NOT_SENT
    expected_state_version: int = Field(strict=True, ge=1)
    next_state: Literal[SendState.SENDING] = SendState.SENDING
    next_state_version: int = Field(strict=True, ge=2)
    authority: OwnerAuthorityRef
    claim: OwnerClaimTruth

    @model_validator(mode="after")
    def version_and_authority_are_exact(self) -> Self:
        if self.next_state_version != self.expected_state_version + 1:
            raise ValueError("claim_version_must_advance_once")
        if (
            self.claim.grant_pub_id != self.authority.grant_pub_id
            or self.claim.grant_revision != self.authority.grant_revision
            or self.claim.owner_handle != self.authority.owner_handle
            or self.claim.fence_set_sha256 != self.authority.fence_set_sha256
            or self.claim.authority_sha256 != authority_digest(self.authority)
        ):
            raise ValueError("claim_authority_snapshot_mismatch")
        if not self.authority.checked_at <= self.claim.claimed_at < self.authority.valid_until:
            raise ValueError("claim_authority_snapshot_not_fresh")
        return self


class RecoveryDecision(StrEnum):
    CLAIM_FRESH = "claim_fresh"
    NO_SUBMIT_NOT_SENT_VERSION_CHANGED = "no_submit_not_sent_version_changed"
    NO_SUBMIT_SENDING = "no_submit_sending"
    NO_RESEND_CONFIRMED_SENT = "no_resend_confirmed_sent"
    NO_RESEND_SEND_UNKNOWN = "no_resend_send_unknown"
    NO_SUBMIT_CONFIRMED_NOT_SENT = "no_submit_confirmed_not_sent"
    CAS_NOT_APPLIED = "cas_not_applied"


class NoSubmitDecision(FrozenProtocolModel):
    operation: OperationRef
    decision: RecoveryDecision
    observed_state: SendState
    observed_state_version: int = Field(strict=True, ge=1)


class ClaimPlan(FrozenProtocolModel):
    cas: OwnerClaimCasCommand


ClaimPlanningResult = ClaimPlan | NoSubmitDecision


def plan_owner_claim(
    operation: SubmissionOperationTruth,
    verified: VerifiedPreflight,
    *,
    claim_pub_id: str,
    owner_dispatch_ref: str,
    owner_wal_evidence_sha256: str,
    claimed_at: datetime,
) -> ClaimPlanningResult:
    ref = operation_ref(operation.identity)
    if verified.command.operation != ref:
        raise SubmissionProtocolError("claim_preflight_operation_mismatch")
    if verified.observation.decision is not PreflightDecision.READY:
        raise SubmissionProtocolError("claim_requires_ready_preflight")
    if claimed_at < verified.observation.observed_at:
        raise SubmissionProtocolError("claim_before_preflight")
    if claimed_at >= verified.command.authority.valid_until:
        raise SubmissionProtocolError("claim_authority_expired")
    if operation.send_state is SendState.NOT_SENT:
        if operation.state_version != verified.command.expected_state_version:
            return NoSubmitDecision(
                operation=ref,
                decision=RecoveryDecision.NO_SUBMIT_NOT_SENT_VERSION_CHANGED,
                observed_state=operation.send_state,
                observed_state_version=operation.state_version,
            )
        claim = OwnerClaimTruth(
            claim_pub_id=claim_pub_id,
            owner_handle=verified.command.authority.owner_handle,
            grant_pub_id=verified.command.authority.grant_pub_id,
            grant_revision=verified.command.authority.grant_revision,
            authority_sha256=authority_digest(verified.command.authority),
            fence_set_sha256=verified.command.authority.fence_set_sha256,
            dispatch_key=deterministic_dispatch_key(ref),
            owner_dispatch_ref=owner_dispatch_ref,
            owner_wal_evidence_sha256=owner_wal_evidence_sha256,
            claimed_at=claimed_at,
        )
        return ClaimPlan(
            cas=OwnerClaimCasCommand(
                operation=ref,
                expected_state_version=operation.state_version,
                next_state_version=operation.state_version + 1,
                authority=verified.command.authority,
                claim=claim,
            )
        )
    decisions = {
        SendState.SENDING: RecoveryDecision.NO_SUBMIT_SENDING,
        SendState.CONFIRMED_SENT: RecoveryDecision.NO_RESEND_CONFIRMED_SENT,
        SendState.SEND_UNKNOWN: RecoveryDecision.NO_RESEND_SEND_UNKNOWN,
        SendState.CONFIRMED_NOT_SENT: RecoveryDecision.NO_SUBMIT_CONFIRMED_NOT_SENT,
    }
    return NoSubmitDecision(
        operation=ref,
        decision=decisions[operation.send_state],
        observed_state=operation.send_state,
        observed_state_version=operation.state_version,
    )


class OwnerClaimCasStatus(StrEnum):
    FRESHLY_APPLIED = "freshly_applied"
    NOT_APPLIED = "not_applied"


class OwnerClaimCasObservation(FrozenProtocolModel):
    status: OwnerClaimCasStatus
    persisted: SubmissionOperationTruth


class OwnerClaimCasPort(Protocol):
    def compare_and_swap(self, command: OwnerClaimCasCommand) -> OwnerClaimCasObservation: ...


class FreshSubmissionClaim(FrozenProtocolModel):
    """Ephemeral submit capability emitted only for a freshly applied CAS."""

    operation: OperationRef
    claim: OwnerClaimTruth
    claimed_state_version: int = Field(strict=True, ge=2)


def confirm_owner_claim(
    plan: ClaimPlan, observation: OwnerClaimCasObservation
) -> FreshSubmissionClaim | NoSubmitDecision:
    persisted = observation.persisted
    ref = operation_ref(persisted.identity)
    if ref != plan.cas.operation:
        raise SubmissionProtocolError("claim_cas_operation_mismatch")
    if observation.status is OwnerClaimCasStatus.NOT_APPLIED:
        return NoSubmitDecision(
            operation=ref,
            decision=RecoveryDecision.CAS_NOT_APPLIED,
            observed_state=persisted.send_state,
            observed_state_version=persisted.state_version,
        )
    if (
        persisted.send_state is not SendState.SENDING
        or persisted.state_version != plan.cas.next_state_version
        or persisted.claim != plan.cas.claim
        or persisted.terminal is not None
    ):
        raise SubmissionProtocolError("fresh_claim_persisted_truth_mismatch")
    return FreshSubmissionClaim(
        operation=ref,
        claim=plan.cas.claim,
        claimed_state_version=persisted.state_version,
    )


class SubmitOnceCommand(FrozenProtocolModel):
    fresh_claim: FreshSubmissionClaim
    request_manifest: RequestManifest
    request_manifest_sha256: Sha256Hex
    provider_idempotency_key: OpaqueId

    @model_validator(mode="after")
    def request_matches_operation(self) -> Self:
        operation = self.fresh_claim.operation
        if self.request_manifest_sha256 != request_manifest_digest(self.request_manifest):
            raise ValueError("submit_request_manifest_mismatch")
        if self.request_manifest_sha256 != operation.request_manifest_sha256:
            raise ValueError("submit_operation_request_mismatch")
        if self.provider_idempotency_key != operation.provider_idempotency_key:
            raise ValueError("submit_provider_key_mismatch")
        return self


class SubmitDisposition(FrozenProtocolModel):
    send_state: SendState
    reason: TerminalReason
    boundary_entered: bool
    evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    resolved_at: datetime
    provider_submission_ref: OpaqueId | None = None
    non_submission_proof_ref: OpaqueId | None = None
    terminated_fence_set_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def disposition_is_safe(self) -> Self:
        TerminalSubmissionTruth(
            send_state=self.send_state,
            reason=self.reason,
            boundary_entered=self.boundary_entered,
            evidence_ref=self.evidence_ref,
            evidence_sha256=self.evidence_sha256,
            resolved_at=self.resolved_at,
            provider_submission_ref=self.provider_submission_ref,
            non_submission_proof_ref=self.non_submission_proof_ref,
            terminated_fence_set_sha256=self.terminated_fence_set_sha256,
        )
        if self.send_state is SendState.CONFIRMED_NOT_SENT:
            if self.non_submission_proof_ref is None or self.terminated_fence_set_sha256 is None:
                raise ValueError("confirmed_not_sent_requires_owner_proof_and_lease_termination")
        elif (
            self.non_submission_proof_ref is not None
            or self.terminated_fence_set_sha256 is not None
        ):
            raise ValueError("submission_result_cannot_carry_non_submission_proof")
        return self


class SubmitOncePort(Protocol):
    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition: ...


class SendingReconciliationCommand(FrozenProtocolModel):
    """Resolve durable ``SENDING`` from owner evidence without submit authority."""

    operation: OperationRef
    expected_state_version: int = Field(strict=True, ge=2)
    claim_pub_id: OpaqueId
    owner_handle: OpaqueId
    authority_sha256: Sha256Hex
    dispatch_key: OpaqueId
    owner_dispatch_ref: OpaqueId
    owner_wal_evidence_sha256: Sha256Hex
    durable_evidence_ref: OpaqueId
    durable_evidence_sha256: Sha256Hex
    observed_at: datetime


class ReconciliationDisposition(FrozenProtocolModel):
    send_state: Literal[
        SendState.CONFIRMED_SENT,
        SendState.SEND_UNKNOWN,
        SendState.CONFIRMED_NOT_SENT,
    ]
    reason: TerminalReason
    boundary_entered: bool
    evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    resolved_at: datetime
    provider_submission_ref: OpaqueId | None = None
    non_submission_proof_ref: OpaqueId | None = None
    terminated_fence_set_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def durable_resolution_is_safe(self) -> Self:
        TerminalSubmissionTruth(
            send_state=self.send_state,
            reason=self.reason,
            boundary_entered=self.boundary_entered,
            evidence_ref=self.evidence_ref,
            evidence_sha256=self.evidence_sha256,
            resolved_at=self.resolved_at,
            provider_submission_ref=self.provider_submission_ref,
            non_submission_proof_ref=self.non_submission_proof_ref,
            terminated_fence_set_sha256=self.terminated_fence_set_sha256,
        )
        if self.send_state is SendState.CONFIRMED_NOT_SENT and (
            self.non_submission_proof_ref is None or self.terminated_fence_set_sha256 is None
        ):
            raise ValueError("reconciled_not_sent_requires_proof_and_lease_termination")
        if self.send_state is not SendState.CONFIRMED_NOT_SENT and (
            self.non_submission_proof_ref is not None
            or self.terminated_fence_set_sha256 is not None
        ):
            raise ValueError("reconciled_submission_cannot_carry_non_submission_proof")
        return self


class SendingReconciliationPort(Protocol):
    def reconcile_sending(
        self, command: SendingReconciliationCommand
    ) -> ReconciliationDisposition: ...


class QuotaTerminalEffect(StrEnum):
    SETTLE_CONSUMED = "settle_consumed"
    SETTLE_UNKNOWN = "settle_unknown"
    RELEASE = "release"


class OutboxEventRef(FrozenProtocolModel):
    outbox_key: OpaqueId
    event_type: OpaqueId
    aggregate_ref: OpaqueId
    aggregate_version: int = Field(strict=True, ge=1)
    payload_sha256: Sha256Hex
    occurred_at: datetime

    @model_validator(mode="after")
    def key_is_deterministic(self) -> Self:
        expected = deterministic_outbox_key(
            event_type=self.event_type,
            aggregate_ref=self.aggregate_ref,
            aggregate_version=self.aggregate_version,
            payload_sha256=self.payload_sha256,
        )
        if self.outbox_key != expected:
            raise ValueError("outbox_key_mismatch")
        return self


class TerminalSubmissionTransition(FrozenProtocolModel):
    operation: SubmissionOperationTruth
    quota_effect: QuotaTerminalEffect
    outbox: OutboxEventRef


def apply_submit_disposition(
    operation: SubmissionOperationTruth,
    command: SubmitOnceCommand,
    disposition: SubmitDisposition,
) -> TerminalSubmissionTransition:
    if operation.send_state is not SendState.SENDING or operation.claim is None:
        raise SubmissionProtocolError("submit_resolution_requires_sending")
    claim = command.fresh_claim
    if (
        operation_ref(operation.identity) != claim.operation
        or operation.claim != claim.claim
        or operation.state_version != claim.claimed_state_version
    ):
        raise SubmissionProtocolError("submit_claim_mismatch")
    if disposition.resolved_at < operation.claim.claimed_at:
        raise SubmissionProtocolError("submit_resolution_before_claim")
    terminal = TerminalSubmissionTruth(
        send_state=disposition.send_state,
        reason=disposition.reason,
        boundary_entered=disposition.boundary_entered,
        evidence_ref=disposition.evidence_ref,
        evidence_sha256=disposition.evidence_sha256,
        resolved_at=disposition.resolved_at,
        provider_submission_ref=disposition.provider_submission_ref,
        non_submission_proof_ref=disposition.non_submission_proof_ref,
        terminated_fence_set_sha256=disposition.terminated_fence_set_sha256,
    )
    if (
        terminal.terminated_fence_set_sha256 is not None
        and terminal.terminated_fence_set_sha256 != operation.claim.fence_set_sha256
    ):
        raise SubmissionProtocolError("submit_terminated_fence_set_mismatch")
    next_version = operation.state_version + 1
    updated = SubmissionOperationTruth(
        identity=operation.identity,
        send_state=terminal.send_state,
        state_version=next_version,
        prepared_at=operation.prepared_at,
        claim=operation.claim,
        terminal=terminal,
    )
    effects = {
        SendState.CONFIRMED_SENT: QuotaTerminalEffect.SETTLE_CONSUMED,
        SendState.SEND_UNKNOWN: QuotaTerminalEffect.SETTLE_UNKNOWN,
        SendState.CONFIRMED_NOT_SENT: QuotaTerminalEffect.RELEASE,
    }
    payload_hash = _digest(terminal)
    event_type = "collection.submission.terminal"
    aggregate_ref = operation.identity.operation_pub_id
    outbox = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type=event_type,
            aggregate_ref=aggregate_ref,
            aggregate_version=next_version,
            payload_sha256=payload_hash,
        ),
        event_type=event_type,
        aggregate_ref=aggregate_ref,
        aggregate_version=next_version,
        payload_sha256=payload_hash,
        occurred_at=terminal.resolved_at,
    )
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=effects[terminal.send_state],
        outbox=outbox,
    )


def reconcile_sending(
    operation: SubmissionOperationTruth,
    command: SendingReconciliationCommand,
    disposition: ReconciliationDisposition,
) -> TerminalSubmissionTransition:
    """Converge an indeterminate owner claim; this path can never submit."""

    if operation.send_state is not SendState.SENDING or operation.claim is None:
        raise SubmissionProtocolError("reconciliation_requires_sending")
    if (
        command.operation != operation_ref(operation.identity)
        or command.expected_state_version != operation.state_version
        or command.claim_pub_id != operation.claim.claim_pub_id
        or command.owner_handle != operation.claim.owner_handle
        or command.authority_sha256 != operation.claim.authority_sha256
        or command.dispatch_key != operation.claim.dispatch_key
        or command.owner_dispatch_ref != operation.claim.owner_dispatch_ref
        or command.owner_wal_evidence_sha256 != operation.claim.owner_wal_evidence_sha256
        or command.durable_evidence_ref != disposition.evidence_ref
        or command.durable_evidence_sha256 != disposition.evidence_sha256
    ):
        raise SubmissionProtocolError("reconciliation_owner_truth_mismatch")
    if command.observed_at < operation.claim.claimed_at:
        raise SubmissionProtocolError("reconciliation_observation_before_claim")
    if disposition.resolved_at < command.observed_at:
        raise SubmissionProtocolError("reconciliation_resolution_before_evidence")
    terminal = TerminalSubmissionTruth(
        send_state=disposition.send_state,
        reason=disposition.reason,
        boundary_entered=disposition.boundary_entered,
        evidence_ref=disposition.evidence_ref,
        evidence_sha256=disposition.evidence_sha256,
        resolved_at=disposition.resolved_at,
        provider_submission_ref=disposition.provider_submission_ref,
        non_submission_proof_ref=disposition.non_submission_proof_ref,
        terminated_fence_set_sha256=disposition.terminated_fence_set_sha256,
    )
    if (
        terminal.terminated_fence_set_sha256 is not None
        and terminal.terminated_fence_set_sha256 != operation.claim.fence_set_sha256
    ):
        raise SubmissionProtocolError("reconciliation_terminated_fence_set_mismatch")
    next_version = operation.state_version + 1
    updated = SubmissionOperationTruth(
        identity=operation.identity,
        send_state=terminal.send_state,
        state_version=next_version,
        prepared_at=operation.prepared_at,
        claim=operation.claim,
        terminal=terminal,
    )
    effects = {
        SendState.CONFIRMED_SENT: QuotaTerminalEffect.SETTLE_CONSUMED,
        SendState.SEND_UNKNOWN: QuotaTerminalEffect.SETTLE_UNKNOWN,
        SendState.CONFIRMED_NOT_SENT: QuotaTerminalEffect.RELEASE,
    }
    payload_hash = _digest(terminal)
    event_type = "collection.submission.terminal"
    aggregate_ref = operation.identity.operation_pub_id
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=effects[terminal.send_state],
        outbox=OutboxEventRef(
            outbox_key=deterministic_outbox_key(
                event_type=event_type,
                aggregate_ref=aggregate_ref,
                aggregate_version=next_version,
                payload_sha256=payload_hash,
            ),
            event_type=event_type,
            aggregate_ref=aggregate_ref,
            aggregate_version=next_version,
            payload_sha256=payload_hash,
            occurred_at=terminal.resolved_at,
        ),
    )


def apply_preflight_not_sent(
    operation: SubmissionOperationTruth, verified: VerifiedPreflight
) -> TerminalSubmissionTransition:
    observation = verified.observation
    if operation.send_state is not SendState.NOT_SENT:
        raise SubmissionProtocolError("stale_preflight_operation_not_not_sent")
    if (
        operation_ref(operation.identity) != verified.command.operation
        or operation.state_version != verified.command.expected_state_version
    ):
        raise SubmissionProtocolError("stale_preflight_operation_version")
    if observation.decision is not PreflightDecision.CONFIRMED_NOT_SENT:
        raise SubmissionProtocolError("preflight_is_not_non_submission")
    terminal = TerminalSubmissionTruth(
        send_state=SendState.CONFIRMED_NOT_SENT,
        reason=observation.not_sent_reason or TerminalReason.PREFLIGHT_NOT_SENT,
        boundary_entered=False,
        evidence_ref=observation.evidence_ref,
        evidence_sha256=observation.evidence_sha256,
        resolved_at=observation.observed_at,
        terminated_fence_set_sha256=verified.command.authority.fence_set_sha256,
    )
    next_version = operation.state_version + 1
    updated = SubmissionOperationTruth(
        identity=operation.identity,
        send_state=SendState.CONFIRMED_NOT_SENT,
        state_version=next_version,
        prepared_at=operation.prepared_at,
        terminal=terminal,
    )
    payload_hash = _digest(terminal)
    event_type = "collection.submission.terminal"
    aggregate_ref = operation.identity.operation_pub_id
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=QuotaTerminalEffect.RELEASE,
        outbox=OutboxEventRef(
            outbox_key=deterministic_outbox_key(
                event_type=event_type,
                aggregate_ref=aggregate_ref,
                aggregate_version=next_version,
                payload_sha256=payload_hash,
            ),
            event_type=event_type,
            aggregate_ref=aggregate_ref,
            aggregate_version=next_version,
            payload_sha256=payload_hash,
            occurred_at=observation.observed_at,
        ),
    )


class CaptureStagingRef(FrozenProtocolModel):
    staging_key: OpaqueId
    object_ref: OpaqueId
    content_sha256: Sha256Hex
    byte_size: int = Field(strict=True, ge=0)
    media_type: MediaType
    capture_schema_revision: OpaqueId
    staged_at: datetime


class CaptureStagingIntent(FrozenProtocolModel):
    """Deterministic object target registered before capture-side upload I/O."""

    staging_key: OpaqueId
    object_ref: OpaqueId


def deterministic_capture_staging_intent(
    *,
    operation: OperationRef,
    attempt_ref: str,
) -> CaptureStagingIntent:
    material = canonical_json(
        {
            "attempt_ref": attempt_ref,
            "operation": operation.model_dump(mode="json"),
            "version": "collection-capture-staging-intent-v1",
        }
    )
    digest = sha256(material.encode()).hexdigest()
    return CaptureStagingIntent(
        staging_key=f"capture-staging-v1-{digest}",
        object_ref=f"capture-object-v1-{digest}",
    )


class CaptureNormalizationDecision(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED_SURFACE_MISMATCH = "quarantined_surface_mismatch"


class CaptureChannel(StrEnum):
    PROVIDER_PAYLOAD = "provider_payload"
    WEB_DOM = "web_dom"
    WEB_SCREENSHOT = "web_screenshot"
    WEB_NETWORK = "web_network"
    APP_UI = "app_ui"
    APP_ACCESSIBILITY = "app_accessibility"
    APP_SCREENSHOT = "app_screenshot"
    APP_NETWORK = "app_network"


class CaptureDataClassification(StrEnum):
    PUBLIC = "public"
    CUSTOMER_PRIVATE = "customer_private"
    RESTRICTED = "restricted"


_CAPTURE_CHANNELS_BY_SURFACE: dict[CollectionSurface, frozenset[CaptureChannel]] = {
    CollectionSurface.PROVIDER_API: frozenset({CaptureChannel.PROVIDER_PAYLOAD}),
    CollectionSurface.CONSUMER_WEB: frozenset(
        {
            CaptureChannel.WEB_DOM,
            CaptureChannel.WEB_SCREENSHOT,
            CaptureChannel.WEB_NETWORK,
        }
    ),
    CollectionSurface.CONSUMER_APP: frozenset(
        {
            CaptureChannel.APP_UI,
            CaptureChannel.APP_ACCESSIBILITY,
            CaptureChannel.APP_SCREENSHOT,
            CaptureChannel.APP_NETWORK,
        }
    ),
}


class CaptureProvenance(FrozenProtocolModel):
    """Bounded capture-source fields required by the durable manifest."""

    capture_channel: CaptureChannel
    capture_protocol_revision: OpaqueId
    observed_product_version: OpaqueId
    capture_adapter_revision: OpaqueId
    data_classification: CaptureDataClassification
    dlp_policy_revision: OpaqueId
    retention_until: datetime

    def supports_surface(self, surface: CollectionSurface) -> bool:
        return self.capture_channel in _CAPTURE_CHANNELS_BY_SURFACE[surface]


class CaptureTruth(FrozenProtocolModel):
    operation: OperationRef
    source_send_state: Literal[SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN]
    expected_surface_product: SurfaceProductRef
    owner_handle: OpaqueId
    fence_set_sha256: Sha256Hex
    capture_state: CaptureState
    state_version: int = Field(strict=True, ge=1)
    active_attempt_ref: OpaqueId | None = None
    active_request_sha256: Sha256Hex | None = None
    staging: CaptureStagingRef | None = None
    evidence_ref: OpaqueId | None = None
    evidence_sha256: Sha256Hex | None = None
    observed_surface_product: SurfaceProductRef | None = None
    provenance: CaptureProvenance | None = None
    normalization: CaptureNormalizationDecision | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def capture_shape(self) -> Self:
        if self.capture_state is CaptureState.CAPTURING:
            if (
                self.active_attempt_ref is None
                or self.active_request_sha256 is None
                or self.staging is not None
                or self.provenance is not None
            ):
                raise ValueError("capturing_requires_attempt_without_staging")
        elif self.capture_state in {CaptureState.COMPLETED, CaptureState.PARTIAL}:
            if (
                self.active_attempt_ref is not None
                or self.active_request_sha256 is not None
                or self.staging is None
                or self.evidence_ref is None
                or self.evidence_sha256 is None
                or self.observed_surface_product is None
                or self.provenance is None
                or self.normalization is not CaptureNormalizationDecision.ACCEPTED
            ):
                raise ValueError("captured_truth_requires_staging_and_evidence")
        elif self.capture_state in {CaptureState.FAILED, CaptureState.NOT_OBSERVABLE}:
            if (
                self.active_attempt_ref is not None
                or self.active_request_sha256 is not None
                or self.staging is not None
                or self.evidence_ref is None
                or self.evidence_sha256 is None
                or self.observed_surface_product is None
                or self.provenance is None
                or self.normalization is None
            ):
                raise ValueError("capture_failure_truth_invalid")
        elif any(
            value is not None
            for value in (
                self.active_attempt_ref,
                self.active_request_sha256,
                self.staging,
                self.evidence_ref,
                self.evidence_sha256,
                self.observed_surface_product,
                self.provenance,
                self.normalization,
            )
        ):
            raise ValueError("not_started_capture_must_be_empty")
        if self.provenance is not None and self.observed_surface_product is not None:
            if not self.provenance.supports_surface(
                self.observed_surface_product.collection_surface
            ):
                raise ValueError("capture_channel_surface_mismatch")
            latest_durable_at = (
                self.staging.staged_at if self.staging is not None else self.updated_at
            )
            if self.provenance.retention_until < latest_durable_at:
                raise ValueError("capture_retention_before_durable_observation")
        return self


class CaptureExistingCommand(FrozenProtocolModel):
    operation: OperationRef
    source_send_state: Literal[SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN]
    expected_capture_version: int = Field(strict=True, ge=1)
    attempt_ref: OpaqueId
    staging_intent: CaptureStagingIntent
    capture_policy_revision: OpaqueId
    requested_surface_product: SurfaceProductRef
    authority: OwnerAuthorityRef
    authority_sha256: Sha256Hex
    requested_at: datetime

    @model_validator(mode="after")
    def authority_is_fresh(self) -> Self:
        if self.staging_intent != deterministic_capture_staging_intent(
            operation=self.operation,
            attempt_ref=self.attempt_ref,
        ):
            raise ValueError("capture_staging_intent_not_deterministic")
        if self.authority_sha256 != authority_digest(self.authority):
            raise ValueError("capture_authority_digest_mismatch")
        if not self.authority.checked_at <= self.requested_at < self.authority.valid_until:
            raise ValueError("capture_authority_not_fresh")
        return self


def capture_command_digest(command: CaptureExistingCommand) -> str:
    return _digest(command)


class CaptureDisposition(FrozenProtocolModel):
    capture_state: Literal[
        CaptureState.COMPLETED,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.NOT_OBSERVABLE,
    ]
    attempt_ref: OpaqueId
    evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    observed_at: datetime
    observed_surface_product: SurfaceProductRef
    provenance: CaptureProvenance
    staging: CaptureStagingRef | None = None
    normalization: CaptureNormalizationDecision | None = None
    normalized_request_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def disposition_shape(self) -> Self:
        has_capture = self.capture_state in {CaptureState.COMPLETED, CaptureState.PARTIAL}
        if has_capture != (self.staging is not None):
            raise ValueError("capture_staging_presence_mismatch")
        if not self.provenance.supports_surface(self.observed_surface_product.collection_surface):
            raise ValueError("capture_channel_surface_mismatch")
        latest_durable_at = self.staging.staged_at if self.staging is not None else self.observed_at
        if self.provenance.retention_until < latest_durable_at:
            raise ValueError("capture_retention_before_durable_observation")
        if (self.normalization is None) != (self.normalized_request_sha256 is None):
            raise ValueError("capture_normalization_shape_invalid")
        if self.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH and (
            self.capture_state is not CaptureState.NOT_OBSERVABLE or self.staging is not None
        ):
            raise ValueError("quarantined_capture_must_not_be_linkable")
        return self


class CaptureExistingPort(Protocol):
    """Capture an already submitted operation; this port has no submit method."""

    def capture_existing(self, command: CaptureExistingCommand) -> CaptureDisposition: ...


class CaptureNormalizationPort(Protocol):
    def normalize_capture(
        self, command: CaptureExistingCommand, observation: CaptureDisposition
    ) -> CaptureDisposition: ...


def normalize_capture(
    command: CaptureExistingCommand, observation: CaptureDisposition
) -> CaptureDisposition:
    """Bind raw capture evidence to the requested surface or quarantine it."""

    if observation.normalization is not None:
        raise SubmissionProtocolError("capture_observation_already_normalized")
    if observation.attempt_ref != command.attempt_ref:
        raise SubmissionProtocolError("capture_attempt_mismatch")
    if observation.observed_at < command.requested_at:
        raise SubmissionProtocolError("capture_observation_before_request")
    if observation.staging is not None and observation.staging.staged_at < observation.observed_at:
        raise SubmissionProtocolError("capture_staged_before_observation")
    if observation.staging is not None and (
        observation.staging.staging_key != command.staging_intent.staging_key
        or observation.staging.object_ref != command.staging_intent.object_ref
    ):
        raise SubmissionProtocolError("capture_staging_intent_mismatch")
    command_hash = capture_command_digest(command)
    if observation.observed_surface_product != command.requested_surface_product:
        return CaptureDisposition(
            capture_state=CaptureState.NOT_OBSERVABLE,
            attempt_ref=observation.attempt_ref,
            evidence_ref=observation.evidence_ref,
            evidence_sha256=observation.evidence_sha256,
            observed_at=observation.observed_at,
            observed_surface_product=observation.observed_surface_product,
            provenance=observation.provenance,
            normalization=CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH,
            normalized_request_sha256=command_hash,
        )
    return CaptureDisposition.model_validate(
        {
            **observation.model_dump(mode="python"),
            "normalization": CaptureNormalizationDecision.ACCEPTED,
            "normalized_request_sha256": command_hash,
        }
    )


def initial_capture_truth(operation: SubmissionOperationTruth) -> CaptureTruth:
    if operation.send_state not in {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}:
        raise SubmissionProtocolError("capture_requires_sent_or_unknown_truth")
    if operation.claim is None:
        raise SubmissionProtocolError("capture_requires_owner_claim_truth")
    return CaptureTruth(
        operation=operation_ref(operation.identity),
        source_send_state=cast(
            Literal[SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN],
            operation.send_state,
        ),
        expected_surface_product=operation.identity.surface_product,
        owner_handle=operation.claim.owner_handle,
        fence_set_sha256=operation.claim.fence_set_sha256,
        capture_state=CaptureState.NOT_STARTED,
        state_version=1,
        updated_at=operation.terminal.resolved_at if operation.terminal else operation.prepared_at,
    )


def begin_capture(truth: CaptureTruth, command: CaptureExistingCommand) -> CaptureTruth:
    if (
        command.operation != truth.operation
        or command.source_send_state is not truth.source_send_state
    ):
        raise SubmissionProtocolError("capture_source_mismatch")
    if command.expected_capture_version != truth.state_version:
        raise SubmissionProtocolError("capture_state_version_mismatch")
    if command.requested_surface_product != truth.expected_surface_product:
        raise SubmissionProtocolError("capture_requested_surface_mismatch")
    if (
        command.authority.owner_handle != truth.owner_handle
        or command.authority.fence_set_sha256 != truth.fence_set_sha256
    ):
        raise SubmissionProtocolError("capture_owner_session_fence_mismatch")
    if truth.capture_state not in {
        CaptureState.NOT_STARTED,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.NOT_OBSERVABLE,
    }:
        raise SubmissionProtocolError("capture_transition_not_allowed")
    if command.requested_at < truth.updated_at:
        raise SubmissionProtocolError("capture_requested_before_truth")
    return CaptureTruth(
        operation=truth.operation,
        source_send_state=truth.source_send_state,
        expected_surface_product=truth.expected_surface_product,
        owner_handle=truth.owner_handle,
        fence_set_sha256=truth.fence_set_sha256,
        capture_state=CaptureState.CAPTURING,
        state_version=truth.state_version + 1,
        active_attempt_ref=command.attempt_ref,
        active_request_sha256=capture_command_digest(command),
        updated_at=command.requested_at,
    )


def apply_capture_disposition(truth: CaptureTruth, disposition: CaptureDisposition) -> CaptureTruth:
    if truth.capture_state is not CaptureState.CAPTURING:
        raise SubmissionProtocolError("capture_resolution_requires_capturing")
    if truth.active_attempt_ref != disposition.attempt_ref:
        raise SubmissionProtocolError("capture_attempt_mismatch")
    if disposition.normalization is None:
        raise SubmissionProtocolError("capture_must_be_normalized_before_apply")
    if disposition.normalized_request_sha256 != truth.active_request_sha256:
        raise SubmissionProtocolError("capture_normalized_request_mismatch")
    if disposition.observed_at < truth.updated_at:
        raise SubmissionProtocolError("capture_observation_before_attempt")
    return CaptureTruth(
        operation=truth.operation,
        source_send_state=truth.source_send_state,
        expected_surface_product=truth.expected_surface_product,
        owner_handle=truth.owner_handle,
        fence_set_sha256=truth.fence_set_sha256,
        capture_state=disposition.capture_state,
        state_version=truth.state_version + 1,
        staging=disposition.staging,
        evidence_ref=disposition.evidence_ref,
        evidence_sha256=disposition.evidence_sha256,
        observed_surface_product=disposition.observed_surface_product,
        provenance=disposition.provenance,
        normalization=disposition.normalization,
        updated_at=disposition.observed_at,
    )


class ImmutableCaptureLink(FrozenProtocolModel):
    capture_link_key: OpaqueId
    operation: OperationRef
    staging_key: OpaqueId
    content_sha256: Sha256Hex
    requested_surface_product: SurfaceProductRef
    observed_surface_product: SurfaceProductRef
    capture_state_version: int = Field(strict=True, ge=1)
    linked_at: datetime


def link_immutable_capture(truth: CaptureTruth, *, linked_at: datetime) -> ImmutableCaptureLink:
    if (
        truth.capture_state not in {CaptureState.COMPLETED, CaptureState.PARTIAL}
        or truth.staging is None
    ):
        raise SubmissionProtocolError("capture_link_requires_staged_capture")
    if (
        truth.normalization is not CaptureNormalizationDecision.ACCEPTED
        or truth.observed_surface_product != truth.expected_surface_product
    ):
        raise SubmissionProtocolError("capture_link_surface_not_accepted")
    if linked_at < truth.updated_at or linked_at < truth.staging.staged_at:
        raise SubmissionProtocolError("capture_link_before_staging_or_observation")
    material = {
        "capture_state_version": truth.state_version,
        "content_sha256": truth.staging.content_sha256,
        "operation_key": truth.operation.operation_key,
        "staging_key": truth.staging.staging_key,
        "version": "immutable-capture-link-v1",
    }
    return ImmutableCaptureLink(
        capture_link_key=f"capture-link-v1-{_digest(material)}",
        operation=truth.operation,
        staging_key=truth.staging.staging_key,
        content_sha256=truth.staging.content_sha256,
        requested_surface_product=truth.expected_surface_product,
        observed_surface_product=truth.observed_surface_product,
        capture_state_version=truth.state_version,
        linked_at=linked_at,
    )


class AnalysisTruth(FrozenProtocolModel):
    capture_link: ImmutableCaptureLink
    analysis_state: AnalysisState
    state_version: int = Field(strict=True, ge=1)
    active_attempt_ref: OpaqueId | None = None
    result_ref: OpaqueId | None = None
    evidence_sha256: Sha256Hex | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def analysis_shape(self) -> Self:
        if self.analysis_state in {AnalysisState.QUEUED, AnalysisState.RUNNING}:
            if self.active_attempt_ref is None or self.result_ref is not None:
                raise ValueError("active_analysis_shape_invalid")
        elif self.analysis_state in {
            AnalysisState.COMPLETED,
            AnalysisState.PARTIAL,
            AnalysisState.FAILED,
        }:
            if self.active_attempt_ref is not None or self.evidence_sha256 is None:
                raise ValueError("terminal_analysis_shape_invalid")
            if self.analysis_state is AnalysisState.COMPLETED and self.result_ref is None:
                raise ValueError("completed_analysis_requires_result")
        elif self.active_attempt_ref is not None or self.result_ref is not None:
            raise ValueError("inactive_analysis_shape_invalid")
        return self


class AnalysisCommand(FrozenProtocolModel):
    capture_link_key: OpaqueId
    capture_content_sha256: Sha256Hex
    expected_analysis_version: int = Field(strict=True, ge=1)
    attempt_ref: OpaqueId
    analyzer_revision: OpaqueId
    analysis_policy_revision: OpaqueId
    requested_at: datetime


class AnalysisDisposition(FrozenProtocolModel):
    analysis_state: Literal[AnalysisState.COMPLETED, AnalysisState.PARTIAL, AnalysisState.FAILED]
    attempt_ref: OpaqueId
    evidence_sha256: Sha256Hex
    completed_at: datetime
    result_ref: OpaqueId | None = None

    @model_validator(mode="after")
    def completed_has_result(self) -> Self:
        if (self.analysis_state is AnalysisState.COMPLETED) != (self.result_ref is not None):
            raise ValueError("analysis_result_presence_mismatch")
        return self


class AnalysisExistingCapturePort(Protocol):
    def analyze_existing_capture(self, command: AnalysisCommand) -> AnalysisDisposition: ...


def initial_analysis_truth(link: ImmutableCaptureLink) -> AnalysisTruth:
    return AnalysisTruth(
        capture_link=link,
        analysis_state=AnalysisState.NOT_REQUESTED,
        state_version=1,
        updated_at=link.linked_at,
    )


def queue_analysis(truth: AnalysisTruth, command: AnalysisCommand) -> AnalysisTruth:
    if (
        command.capture_link_key != truth.capture_link.capture_link_key
        or command.capture_content_sha256 != truth.capture_link.content_sha256
    ):
        raise SubmissionProtocolError("analysis_capture_link_mismatch")
    if command.expected_analysis_version != truth.state_version:
        raise SubmissionProtocolError("analysis_state_version_mismatch")
    if truth.analysis_state not in {
        AnalysisState.NOT_REQUESTED,
        AnalysisState.PARTIAL,
        AnalysisState.FAILED,
    }:
        raise SubmissionProtocolError("analysis_queue_transition_not_allowed")
    if command.requested_at < truth.updated_at:
        raise SubmissionProtocolError("analysis_requested_before_truth")
    return AnalysisTruth(
        capture_link=truth.capture_link,
        analysis_state=AnalysisState.QUEUED,
        state_version=truth.state_version + 1,
        active_attempt_ref=command.attempt_ref,
        updated_at=command.requested_at,
    )


def start_analysis(
    truth: AnalysisTruth, *, attempt_ref: str, started_at: datetime
) -> AnalysisTruth:
    if truth.analysis_state is not AnalysisState.QUEUED or truth.active_attempt_ref != attempt_ref:
        raise SubmissionProtocolError("analysis_start_requires_matching_queue")
    if started_at < truth.updated_at:
        raise SubmissionProtocolError("analysis_start_before_queue")
    return AnalysisTruth(
        capture_link=truth.capture_link,
        analysis_state=AnalysisState.RUNNING,
        state_version=truth.state_version + 1,
        active_attempt_ref=attempt_ref,
        updated_at=started_at,
    )


def apply_analysis_disposition(
    truth: AnalysisTruth, disposition: AnalysisDisposition
) -> AnalysisTruth:
    if truth.analysis_state is not AnalysisState.RUNNING:
        raise SubmissionProtocolError("analysis_resolution_requires_running")
    if truth.active_attempt_ref != disposition.attempt_ref:
        raise SubmissionProtocolError("analysis_attempt_mismatch")
    if disposition.completed_at < truth.updated_at:
        raise SubmissionProtocolError("analysis_completed_before_start")
    return AnalysisTruth(
        capture_link=truth.capture_link,
        analysis_state=disposition.analysis_state,
        state_version=truth.state_version + 1,
        result_ref=disposition.result_ref,
        evidence_sha256=disposition.evidence_sha256,
        updated_at=disposition.completed_at,
    )


class SlotOutcome(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    UNAVAILABLE = "unavailable"
    CONFIRMED_NOT_SENT = "confirmed_not_sent"
    CONFIRMED_SENT_CAPTURE_PENDING = "confirmed_sent_capture_pending"
    CONFIRMED_SENT_CAPTURE_COMPLETE = "confirmed_sent_capture_complete"
    CONFIRMED_SENT_CAPTURE_PARTIAL = "confirmed_sent_capture_partial"
    CONFIRMED_SENT_CAPTURE_FAILED = "confirmed_sent_capture_failed"
    SEND_UNKNOWN = "send_unknown"
    INVALID_SURFACE_OR_PRODUCT = "invalid_surface_or_product"
    ANALYSIS_FAILED = "analysis_failed"
    NOT_OBSERVABLE = "not_observable"


def derive_slot_outcome(
    operation: SubmissionOperationTruth,
    *,
    capture: CaptureTruth | None = None,
    analysis: AnalysisTruth | None = None,
) -> SlotOutcome:
    if operation.send_state is SendState.NOT_SENT:
        if capture is not None or analysis is not None:
            raise SubmissionProtocolError("truth_layer_mismatch")
        return SlotOutcome.NOT_ATTEMPTED
    if operation.send_state is SendState.SENDING:
        raise SubmissionProtocolError("sending_outcome_is_ambiguous")
    if operation.send_state is SendState.SEND_UNKNOWN:
        return SlotOutcome.SEND_UNKNOWN
    if operation.send_state is SendState.CONFIRMED_NOT_SENT:
        reason = operation.terminal.reason if operation.terminal else None
        if reason is TerminalReason.UNAVAILABLE:
            return SlotOutcome.UNAVAILABLE
        if reason is TerminalReason.INVALID_SURFACE_OR_PRODUCT:
            return SlotOutcome.INVALID_SURFACE_OR_PRODUCT
        return SlotOutcome.CONFIRMED_NOT_SENT
    if capture is None:
        if analysis is not None:
            raise SubmissionProtocolError("analysis_requires_capture_truth")
        return SlotOutcome.CONFIRMED_SENT_CAPTURE_PENDING
    if capture.operation != operation_ref(operation.identity):
        raise SubmissionProtocolError("confirmed_sent_capture_truth_mismatch")
    if analysis is not None and analysis.analysis_state is AnalysisState.FAILED:
        return SlotOutcome.ANALYSIS_FAILED
    if capture.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH:
        return SlotOutcome.INVALID_SURFACE_OR_PRODUCT
    outcomes = {
        CaptureState.COMPLETED: SlotOutcome.CONFIRMED_SENT_CAPTURE_COMPLETE,
        CaptureState.PARTIAL: SlotOutcome.CONFIRMED_SENT_CAPTURE_PARTIAL,
        CaptureState.FAILED: SlotOutcome.CONFIRMED_SENT_CAPTURE_FAILED,
        CaptureState.NOT_OBSERVABLE: SlotOutcome.NOT_OBSERVABLE,
    }
    try:
        return outcomes[capture.capture_state]
    except KeyError as exc:
        raise SubmissionProtocolError("capture_outcome_is_ambiguous") from exc
