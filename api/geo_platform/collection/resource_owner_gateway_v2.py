"""Governed, dependency-injected submit boundary for collection-v2 owners.

This module contains no provider, browser, device, secret-store, or network
implementation.  It bridges the Stage-2 execution-governance snapshot to the
Stage-3 submission protocol and refuses to invoke a supplied owner transport
unless the fresh durable claim is bound to that exact authorization.

The production Temporal activity remains fail closed until a resource owner
supplies both a current snapshot loader and a surface-specific transport.  The
transport is invoked once; exceptions are deliberately not retried here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, NoReturn, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.collection.execution_governance import (
    ExecutionAction,
    GatewayKind,
    SideEffectAuthorization,
    assert_governance_payload_safe,
)
from domain.collection.submission import (
    LeaseFenceRef,
    OpaqueId,
    OwnerAuthorityRef,
    Sha256Hex,
    SubmitDisposition,
    SubmitOnceCommand,
    WorkflowOperationInput,
    authority_digest,
    canonical_json,
    deterministic_dispatch_key,
    lease_fence_set_digest,
)
from domain.collection.surface import CollectionSurface

from .execution_governance_v2 import (
    GatewayAuthorizationSnapshot,
    authorize_irreversible_action,
)


class ResourceOwnerGatewayError(RuntimeError):
    """Stable fail-closed error raised before an owner transport is invoked."""

    def __init__(self, code: str, **context: str | int) -> None:
        self.code = code
        self.context = context
        detail = ",".join(f"{key}={value}" for key, value in sorted(context.items()))
        super().__init__(f"{code}:{detail}" if detail else code)


def _fail(code: str, **context: str | int) -> NoReturn:
    raise ResourceOwnerGatewayError(code, **context)


def _submission_fences(
    authorization: SideEffectAuthorization,
) -> tuple[LeaseFenceRef, ...]:
    return tuple(
        LeaseFenceRef(
            lease_pub_id=fence.lease_pub_id,
            binding_resource_pub_id=fence.resource_pub_id,
            resource_role=fence.resource_role,
            owner_handle=fence.owner_gateway_pub_id,
            generation=fence.fence_generation,
            acquired_at=fence.acquired_at,
            expires_at=fence.expires_at,
        )
        for fence in authorization.fence_assertions
    )


class SubmissionOwnerAuthorization(BaseModel):
    """Bounded bridge result retained only across preflight and the fresh CAS."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    tenant_id: UUID
    project_id: UUID
    workflow: WorkflowOperationInput
    collection_surface: CollectionSurface
    gateway_kind: GatewayKind
    owner_protocol_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    assertion: SideEffectAuthorization
    authority: OwnerAuthorityRef

    @model_validator(mode="after")
    def bridge_is_exact(self) -> Self:
        operation = self.workflow.operation
        assertion = self.assertion
        authority = self.authority
        expected_gateway = {
            CollectionSurface.PROVIDER_API: GatewayKind.PROVIDER_REQUEST,
            CollectionSurface.CONSUMER_WEB: GatewayKind.RESIDENT_BROWSER,
            CollectionSurface.CONSUMER_APP: GatewayKind.MANAGED_APP_SESSION,
        }[self.collection_surface]
        if self.gateway_kind is not expected_gateway:
            raise ValueError("submission_owner_gateway_surface_mismatch")
        if assertion.action is not ExecutionAction.SUBMIT_QUERY:
            raise ValueError("submission_owner_action_mismatch")
        if (
            assertion.operation_pub_id != operation.operation_pub_id
            or assertion.operation_generation != operation.generation
        ):
            raise ValueError("submission_owner_operation_mismatch")
        if assertion.expected_send_state_version != self.workflow.expected_state_version:
            raise ValueError("submission_owner_state_version_mismatch")
        if (
            authority.grant_pub_id != assertion.grant_pub_id
            or authority.owner_handle != assertion.owner_gateway_pub_id
            or authority.checked_at != assertion.checked_at
        ):
            raise ValueError("submission_owner_authority_identity_mismatch")
        expected_fences = _submission_fences(assertion)
        if authority.lease_fences != expected_fences:
            raise ValueError("submission_owner_fence_projection_mismatch")
        if authority.fence_set_sha256 != lease_fence_set_digest(expected_fences):
            raise ValueError("submission_owner_fence_digest_mismatch")
        return self


def authorize_submission_owner(
    snapshot: GatewayAuthorizationSnapshot,
    workflow: WorkflowOperationInput,
) -> SubmissionOwnerAuthorization:
    """Validate current governance truth and project one submission authority."""

    assertion = authorize_irreversible_action(snapshot)
    if (
        workflow.operation.operation_pub_id != assertion.operation_pub_id
        or workflow.operation.generation != assertion.operation_generation
    ):
        _fail("submission_workflow_operation_mismatch")
    if workflow.expected_state_version != assertion.expected_send_state_version:
        _fail(
            "submission_workflow_state_version_mismatch",
            expected=assertion.expected_send_state_version,
            actual=workflow.expected_state_version,
        )
    fences = _submission_fences(assertion)
    valid_until = min(
        snapshot.grant.expires_at,
        snapshot.binding.expires_at,
        *(fence.expires_at for fence in fences),
    )
    try:
        authority = OwnerAuthorityRef(
            grant_pub_id=snapshot.grant.grant_pub_id,
            grant_revision=snapshot.grant.grant_revision,
            binding_revision_pub_id=snapshot.binding.binding_pub_id,
            owner_handle=snapshot.owner.owner_gateway_pub_id,
            checked_at=assertion.checked_at,
            valid_until=valid_until,
            lease_fences=fences,
            fence_set_sha256=lease_fence_set_digest(fences),
        )
        return SubmissionOwnerAuthorization(
            tenant_id=snapshot.grant.tenant_id,
            project_id=snapshot.grant.project_id,
            workflow=workflow,
            collection_surface=snapshot.owner.collection_surface,
            gateway_kind=snapshot.owner.gateway_kind,
            owner_protocol_revision=snapshot.owner.protocol_revision,
            assertion=assertion,
            authority=authority,
        )
    except ValueError as exc:
        raise ResourceOwnerGatewayError("submission_owner_projection_invalid") from exc


def submission_owner_authorization_wal_digest(
    *,
    owner_authorization: SubmissionOwnerAuthorization,
    claim_pub_id: str,
    dispatch_key: str,
    owner_dispatch_ref: str,
    recorded_at: datetime,
) -> str:
    """Bind one pre-CAS authorization to its planned owner dispatch."""

    return sha256(
        canonical_json(
            {
                "claim_pub_id": claim_pub_id,
                "dispatch_key": dispatch_key,
                "owner_authorization": owner_authorization,
                "owner_dispatch_ref": owner_dispatch_ref,
                "recorded_at": recorded_at,
                "schema_version": "collection-owner-authorization-wal-v2",
            }
        ).encode()
    ).hexdigest()


class SubmissionOwnerAuthorizationWalRecord(BaseModel):
    """Non-secret owner WAL evidence written before the fresh claim CAS."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["collection-owner-authorization-wal-v2"] = (
        "collection-owner-authorization-wal-v2"
    )
    owner_authorization: SubmissionOwnerAuthorization
    claim_pub_id: OpaqueId
    dispatch_key: OpaqueId
    owner_dispatch_ref: OpaqueId
    recorded_at: datetime
    evidence_sha256: Sha256Hex

    @model_validator(mode="after")
    def wal_record_is_exact(self) -> Self:
        authorization = self.owner_authorization
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("owner_authorization_wal_time_must_be_aware")
        if (
            not authorization.authority.checked_at
            <= self.recorded_at
            < (authorization.authority.valid_until)
        ):
            raise ValueError("owner_authorization_wal_time_not_authorized")
        if self.dispatch_key != deterministic_dispatch_key(authorization.workflow.operation):
            raise ValueError("owner_authorization_wal_dispatch_key_mismatch")
        expected = submission_owner_authorization_wal_digest(
            owner_authorization=authorization,
            claim_pub_id=self.claim_pub_id,
            dispatch_key=self.dispatch_key,
            owner_dispatch_ref=self.owner_dispatch_ref,
            recorded_at=self.recorded_at,
        )
        if self.evidence_sha256 != expected:
            raise ValueError("owner_authorization_wal_digest_mismatch")
        assert_governance_payload_safe(self)
        return self


def build_submission_owner_authorization_wal_record(
    authorization: SubmissionOwnerAuthorization,
    *,
    claim_pub_id: str,
    owner_dispatch_ref: str,
    recorded_at: datetime,
) -> SubmissionOwnerAuthorizationWalRecord:
    """Create the exact non-secret WAL record that a claim hash will reference."""

    dispatch_key = deterministic_dispatch_key(authorization.workflow.operation)
    evidence_sha256 = submission_owner_authorization_wal_digest(
        owner_authorization=authorization,
        claim_pub_id=claim_pub_id,
        dispatch_key=dispatch_key,
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=recorded_at,
    )
    return SubmissionOwnerAuthorizationWalRecord(
        owner_authorization=authorization,
        claim_pub_id=claim_pub_id,
        dispatch_key=dispatch_key,
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=recorded_at,
        evidence_sha256=evidence_sha256,
    )


def submission_owner_send_boundary_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    collection_surface: CollectionSurface,
    gateway_kind: GatewayKind,
    owner_protocol_revision: str,
    command: SubmitOnceCommand,
    entered_at: datetime,
) -> str:
    """Bind the first durable post-CAS boundary marker to one exact command."""

    return sha256(
        canonical_json(
            {
                "collection_surface": collection_surface,
                "command": command,
                "entered_at": entered_at,
                "gateway_kind": gateway_kind,
                "owner_protocol_revision": owner_protocol_revision,
                "project_id": project_id,
                "schema_version": "collection-owner-send-boundary-v1",
                "tenant_id": tenant_id,
            }
        ).encode()
    ).hexdigest()


class SubmissionOwnerSendBoundaryRecord(BaseModel):
    """Immutable proof written immediately before one transport invocation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["collection-owner-send-boundary-v1"] = (
        "collection-owner-send-boundary-v1"
    )
    tenant_id: UUID
    project_id: UUID
    collection_surface: CollectionSurface
    gateway_kind: GatewayKind
    owner_protocol_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    command: SubmitOnceCommand
    entered_at: datetime
    evidence_sha256: Sha256Hex

    @model_validator(mode="after")
    def boundary_is_exact(self) -> Self:
        claim = self.command.fresh_claim.claim
        if self.entered_at.tzinfo is None or self.entered_at.utcoffset() is None:
            raise ValueError("owner_send_boundary_time_must_be_aware")
        if self.entered_at < claim.claimed_at:
            raise ValueError("owner_send_boundary_before_claim")
        expected_gateway = {
            CollectionSurface.PROVIDER_API: GatewayKind.PROVIDER_REQUEST,
            CollectionSurface.CONSUMER_WEB: GatewayKind.RESIDENT_BROWSER,
            CollectionSurface.CONSUMER_APP: GatewayKind.MANAGED_APP_SESSION,
        }[self.collection_surface]
        if self.gateway_kind is not expected_gateway:
            raise ValueError("owner_send_boundary_gateway_surface_mismatch")
        expected = submission_owner_send_boundary_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            collection_surface=self.collection_surface,
            gateway_kind=self.gateway_kind,
            owner_protocol_revision=self.owner_protocol_revision,
            command=self.command,
            entered_at=self.entered_at,
        )
        if self.evidence_sha256 != expected:
            raise ValueError("owner_send_boundary_digest_mismatch")
        assert_governance_payload_safe(self)
        return self

    @property
    def owner_dispatch_ref(self) -> str:
        return self.command.fresh_claim.claim.owner_dispatch_ref

    @property
    def owner_authorization_evidence_sha256(self) -> str:
        return self.command.fresh_claim.claim.owner_wal_evidence_sha256


def build_submission_owner_send_boundary_record(
    authorization: SubmissionOwnerAuthorization,
    command: SubmitOnceCommand,
    *,
    entered_at: datetime,
) -> SubmissionOwnerSendBoundaryRecord:
    """Build the post-CAS marker for the already validated owner command."""

    claim = command.fresh_claim.claim
    authority = authorization.authority
    if (
        command.fresh_claim.operation != authorization.workflow.operation
        or command.fresh_claim.claimed_state_version
        != authorization.workflow.expected_state_version + 1
        or claim.owner_handle != authority.owner_handle
        or claim.grant_pub_id != authority.grant_pub_id
        or claim.grant_revision != authority.grant_revision
        or claim.authority_sha256 != authority_digest(authority)
        or claim.fence_set_sha256 != authority.fence_set_sha256
    ):
        _fail("resource_owner_send_boundary_authorization_mismatch")
    if entered_at >= authority.valid_until:
        _fail("resource_owner_send_boundary_authorization_expired")
    evidence_sha256 = submission_owner_send_boundary_digest(
        tenant_id=authorization.tenant_id,
        project_id=authorization.project_id,
        collection_surface=authorization.collection_surface,
        gateway_kind=authorization.gateway_kind,
        owner_protocol_revision=authorization.owner_protocol_revision,
        command=command,
        entered_at=entered_at,
    )
    return SubmissionOwnerSendBoundaryRecord(
        tenant_id=authorization.tenant_id,
        project_id=authorization.project_id,
        collection_surface=authorization.collection_surface,
        gateway_kind=authorization.gateway_kind,
        owner_protocol_revision=authorization.owner_protocol_revision,
        command=command,
        entered_at=entered_at,
        evidence_sha256=evidence_sha256,
    )


def submission_owner_send_outcome_digest(
    *,
    owner_dispatch_ref: str,
    boundary_evidence_sha256: str,
    disposition: SubmitDisposition,
) -> str:
    """Bind one immutable transport outcome to its first boundary marker."""

    return sha256(
        canonical_json(
            {
                "boundary_evidence_sha256": boundary_evidence_sha256,
                "disposition": disposition,
                "owner_dispatch_ref": owner_dispatch_ref,
                "schema_version": "collection-owner-send-outcome-v1",
            }
        ).encode()
    ).hexdigest()


class SubmissionOwnerSendOutcomeRecord(BaseModel):
    """Immutable result appended only after a boundary-crossing transport returns."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["collection-owner-send-outcome-v1"] = "collection-owner-send-outcome-v1"
    owner_dispatch_ref: OpaqueId
    boundary_evidence_sha256: Sha256Hex
    disposition: SubmitDisposition
    evidence_sha256: Sha256Hex

    @model_validator(mode="after")
    def outcome_is_exact(self) -> Self:
        expected = submission_owner_send_outcome_digest(
            owner_dispatch_ref=self.owner_dispatch_ref,
            boundary_evidence_sha256=self.boundary_evidence_sha256,
            disposition=self.disposition,
        )
        if self.evidence_sha256 != expected:
            raise ValueError("owner_send_outcome_digest_mismatch")
        assert_governance_payload_safe(self)
        return self


def build_submission_owner_send_outcome_record(
    boundary: SubmissionOwnerSendBoundaryRecord,
    disposition: SubmitDisposition,
) -> SubmissionOwnerSendOutcomeRecord:
    """Build a terminal owner-local outcome linked to one durable boundary."""

    if disposition.resolved_at < boundary.entered_at:
        _fail("resource_owner_send_outcome_before_boundary")
    evidence_sha256 = submission_owner_send_outcome_digest(
        owner_dispatch_ref=boundary.owner_dispatch_ref,
        boundary_evidence_sha256=boundary.evidence_sha256,
        disposition=disposition,
    )
    return SubmissionOwnerSendOutcomeRecord(
        owner_dispatch_ref=boundary.owner_dispatch_ref,
        boundary_evidence_sha256=boundary.evidence_sha256,
        disposition=disposition,
        evidence_sha256=evidence_sha256,
    )


class FreshSubmissionOwnerSendBoundary(BaseModel):
    """Capability returned only when a boundary file was newly made durable."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    status: Literal["freshly_appended"] = "freshly_appended"
    record: SubmissionOwnerSendBoundaryRecord


class SubmissionOwnerSendJournalSnapshot(BaseModel):
    """Durable owner-local send truth used by recovery without submit authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    owner_dispatch_ref: OpaqueId
    owner_authorization_evidence_sha256: Sha256Hex
    boundary: SubmissionOwnerSendBoundaryRecord | None = None
    outcome: SubmissionOwnerSendOutcomeRecord | None = None

    @model_validator(mode="after")
    def journal_chain_is_exact(self) -> Self:
        if self.boundary is None:
            if self.outcome is not None:
                raise ValueError("owner_send_outcome_without_boundary")
            return self
        if (
            self.boundary.owner_dispatch_ref != self.owner_dispatch_ref
            or self.boundary.owner_authorization_evidence_sha256
            != self.owner_authorization_evidence_sha256
        ):
            raise ValueError("owner_send_boundary_snapshot_mismatch")
        if self.outcome is not None and (
            self.outcome.owner_dispatch_ref != self.owner_dispatch_ref
            or self.outcome.boundary_evidence_sha256 != self.boundary.evidence_sha256
            or self.outcome.disposition.resolved_at < self.boundary.entered_at
        ):
            raise ValueError("owner_send_outcome_snapshot_mismatch")
        return self


class SubmissionOwnerSendJournalStore(Protocol):
    """Append-only post-CAS journal; boundary replay must never return fresh."""

    def append_send_boundary(
        self,
        record: SubmissionOwnerSendBoundaryRecord,
    ) -> FreshSubmissionOwnerSendBoundary: ...

    def append_send_outcome(
        self,
        record: SubmissionOwnerSendOutcomeRecord,
    ) -> SubmissionOwnerSendOutcomeRecord: ...

    def load_send_journal(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerSendJournalSnapshot: ...


class SubmissionOwnerAuthorizationWalReader(Protocol):
    """Read one immutable owner-local WAL record without caching it in process."""

    def load(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerAuthorizationWalRecord | None: ...


class SubmissionOwnerAuthorizationWalStore(
    SubmissionOwnerAuthorizationWalReader,
    Protocol,
):
    """Durably write exact WAL evidence before returning to the claim caller."""

    def put(
        self,
        record: SubmissionOwnerAuthorizationWalRecord,
    ) -> SubmissionOwnerAuthorizationWalRecord: ...


class DurableSubmissionOwnerAuthorizationLoader:
    """Resolve a post-CAS command from the exact durable pre-CAS WAL evidence."""

    def __init__(self, reader: SubmissionOwnerAuthorizationWalReader) -> None:
        self._reader = reader

    def load(self, command: SubmitOnceCommand) -> SubmissionOwnerAuthorization:
        claim = command.fresh_claim.claim
        record = self._reader.load(owner_dispatch_ref=claim.owner_dispatch_ref)
        if record is None:
            _fail("resource_owner_authorization_wal_missing")
        if not isinstance(record, SubmissionOwnerAuthorizationWalRecord):
            _fail("resource_owner_authorization_wal_invalid")
        authorization = record.owner_authorization
        if (
            record.claim_pub_id != claim.claim_pub_id
            or record.dispatch_key != claim.dispatch_key
            or record.owner_dispatch_ref != claim.owner_dispatch_ref
            or record.evidence_sha256 != claim.owner_wal_evidence_sha256
        ):
            _fail("resource_owner_authorization_wal_claim_mismatch")
        if authorization.workflow.operation != command.fresh_claim.operation:
            _fail("resource_owner_authorization_wal_operation_mismatch")
        if command.fresh_claim.claimed_state_version != (
            authorization.workflow.expected_state_version + 1
        ):
            _fail("resource_owner_authorization_wal_state_version_mismatch")
        if record.recorded_at > claim.claimed_at:
            _fail("resource_owner_authorization_wal_after_claim")
        return authorization


class SubmissionOwnerAuthorizationLoader(Protocol):
    """Load the exact authorization paired with one fresh submit command.

    A production implementation must run inside the serialized resource-owner
    turn and must not return a cached authorization.  The durable WAL-backed
    implementation above still requires an owner-local physical record reader.
    """

    def load(self, command: SubmitOnceCommand) -> SubmissionOwnerAuthorization: ...


class ResourceOwnerSubmitTransport(Protocol):
    """One boundary-crossing transport invocation for a serialized owner.

    Safe navigation and pre-submit validation belong before this port.  Once
    called, the durable boundary marker already exists, so an exception is
    conservatively recoverable only as sent-or-unknown and never as a retry.
    A returned ``CONFIRMED_NOT_SENT`` remains valid only when its typed proof
    demonstrates that the external side-effect boundary itself was not crossed.
    """

    def submit_once(
        self,
        command: SubmitOnceCommand,
        *,
        authorization: SideEffectAuthorization,
    ) -> SubmitDisposition: ...


class AuthorizedSubmitOnceGateway:
    """Validate a fresh claim, journal the boundary, and invoke one transport."""

    def __init__(
        self,
        *,
        collection_surface: CollectionSurface,
        gateway_kind: GatewayKind,
        owner_gateway_pub_id: str,
        owner_protocol_revision: str,
        authorization_loader: SubmissionOwnerAuthorizationLoader,
        send_journal: SubmissionOwnerSendJournalStore,
        transport: ResourceOwnerSubmitTransport,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        expected_gateway = {
            CollectionSurface.PROVIDER_API: GatewayKind.PROVIDER_REQUEST,
            CollectionSurface.CONSUMER_WEB: GatewayKind.RESIDENT_BROWSER,
            CollectionSurface.CONSUMER_APP: GatewayKind.MANAGED_APP_SESSION,
        }[collection_surface]
        if gateway_kind is not expected_gateway:
            _fail("configured_gateway_surface_mismatch")
        self._collection_surface = collection_surface
        self._gateway_kind = gateway_kind
        self._owner_gateway_pub_id = owner_gateway_pub_id
        self._owner_protocol_revision = owner_protocol_revision
        self._authorization_loader = authorization_loader
        self._send_journal = send_journal
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition:
        authorization = self._authorization_loader.load(command)
        self._validate_fresh_claim(command, authorization)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            _fail("resource_owner_clock_must_be_timezone_aware")
        if now < command.fresh_claim.claim.claimed_at:
            _fail("resource_owner_clock_precedes_claim")
        if now >= authorization.authority.valid_until:
            _fail("resource_owner_authorization_expired")
        boundary = build_submission_owner_send_boundary_record(
            authorization,
            command,
            entered_at=now,
        )
        fresh_boundary = self._send_journal.append_send_boundary(boundary)
        if (
            not isinstance(fresh_boundary, FreshSubmissionOwnerSendBoundary)
            or fresh_boundary.record != boundary
        ):
            _fail("resource_owner_send_boundary_append_invalid")
        result = self._transport.submit_once(
            command,
            authorization=authorization.assertion,
        )
        if not isinstance(result, SubmitDisposition):
            _fail("resource_owner_transport_result_invalid")
        if result.resolved_at < command.fresh_claim.claim.claimed_at:
            _fail("resource_owner_result_precedes_claim")
        outcome = build_submission_owner_send_outcome_record(boundary, result)
        persisted_outcome = self._send_journal.append_send_outcome(outcome)
        if (
            not isinstance(persisted_outcome, SubmissionOwnerSendOutcomeRecord)
            or persisted_outcome != outcome
        ):
            _fail("resource_owner_send_outcome_append_invalid")
        return result

    def _validate_fresh_claim(
        self,
        command: SubmitOnceCommand,
        authorization: SubmissionOwnerAuthorization,
    ) -> None:
        authority = authorization.authority
        claim = command.fresh_claim.claim
        if authorization.collection_surface is not self._collection_surface:
            _fail("resource_owner_surface_mismatch")
        if authorization.gateway_kind is not self._gateway_kind:
            _fail("resource_owner_gateway_kind_mismatch")
        if authority.owner_handle != self._owner_gateway_pub_id:
            _fail("resource_owner_handle_mismatch")
        if authorization.owner_protocol_revision != self._owner_protocol_revision:
            _fail("resource_owner_protocol_revision_mismatch")
        if command.fresh_claim.operation != authorization.workflow.operation:
            _fail("resource_owner_command_operation_mismatch")
        if (
            command.fresh_claim.claimed_state_version
            != authorization.workflow.expected_state_version + 1
        ):
            _fail("resource_owner_claim_state_version_mismatch")
        if (
            claim.grant_pub_id != authority.grant_pub_id
            or claim.grant_revision != authority.grant_revision
            or claim.owner_handle != authority.owner_handle
            or claim.authority_sha256 != authority_digest(authority)
            or claim.fence_set_sha256 != authority.fence_set_sha256
        ):
            _fail("resource_owner_fresh_claim_authority_mismatch")
        if not authority.checked_at <= claim.claimed_at < authority.valid_until:
            _fail("resource_owner_fresh_claim_not_authorized")


@dataclass(frozen=True)
class PreparedSubmissionOwnerTurn:
    """In-process owner turn prepared before CAS and consumed only after it."""

    authorization: SubmissionOwnerAuthorization
    wal_record: SubmissionOwnerAuthorizationWalRecord
    submit_gateway: AuthorizedSubmitOnceGateway

    @property
    def authority(self) -> OwnerAuthorityRef:
        return self.authorization.authority

    @property
    def owner_wal_evidence_sha256(self) -> str:
        return self.wal_record.evidence_sha256


def prepare_submission_owner_turn(
    snapshot: GatewayAuthorizationSnapshot,
    workflow: WorkflowOperationInput,
    *,
    claim_pub_id: str,
    owner_dispatch_ref: str,
    wal_store: SubmissionOwnerAuthorizationWalStore,
    send_journal: SubmissionOwnerSendJournalStore,
    transport: ResourceOwnerSubmitTransport,
    recorded_at: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PreparedSubmissionOwnerTurn:
    """Authorize, durably bind one claim, and build its post-CAS gateway.

    ``wal_store.put`` must return only after the authorization is durable.  The
    separate post-CAS ``send_journal`` must durably append a fresh boundary before
    transport and an immutable outcome before the gateway returns.  Authorization
    replay may return an equal record, but boundary replay is always no-resend.
    """

    authorization = authorize_submission_owner(snapshot, workflow)
    record = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id=claim_pub_id,
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=recorded_at or snapshot.checked_at,
    )
    persisted = wal_store.put(record)
    if not isinstance(persisted, SubmissionOwnerAuthorizationWalRecord):
        _fail("resource_owner_authorization_wal_write_invalid")
    if persisted != record:
        _fail("resource_owner_authorization_wal_write_conflict")
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=authorization.collection_surface,
        gateway_kind=authorization.gateway_kind,
        owner_gateway_pub_id=authorization.authority.owner_handle,
        owner_protocol_revision=authorization.owner_protocol_revision,
        authorization_loader=DurableSubmissionOwnerAuthorizationLoader(wal_store),
        send_journal=send_journal,
        transport=transport,
        clock=clock,
    )
    return PreparedSubmissionOwnerTurn(
        authorization=authorization,
        wal_record=persisted,
        submit_gateway=gateway,
    )


__all__ = [
    "AuthorizedSubmitOnceGateway",
    "DurableSubmissionOwnerAuthorizationLoader",
    "FreshSubmissionOwnerSendBoundary",
    "PreparedSubmissionOwnerTurn",
    "ResourceOwnerGatewayError",
    "ResourceOwnerSubmitTransport",
    "SubmissionOwnerAuthorization",
    "SubmissionOwnerAuthorizationLoader",
    "SubmissionOwnerAuthorizationWalReader",
    "SubmissionOwnerAuthorizationWalRecord",
    "SubmissionOwnerAuthorizationWalStore",
    "SubmissionOwnerSendBoundaryRecord",
    "SubmissionOwnerSendJournalSnapshot",
    "SubmissionOwnerSendJournalStore",
    "SubmissionOwnerSendOutcomeRecord",
    "authorize_submission_owner",
    "build_submission_owner_authorization_wal_record",
    "build_submission_owner_send_boundary_record",
    "build_submission_owner_send_outcome_record",
    "prepare_submission_owner_turn",
    "submission_owner_authorization_wal_digest",
    "submission_owner_send_boundary_digest",
    "submission_owner_send_outcome_digest",
]
