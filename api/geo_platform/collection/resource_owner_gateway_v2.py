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
from datetime import UTC, datetime
from typing import NoReturn, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.collection.execution_governance import (
    ExecutionAction,
    GatewayKind,
    SideEffectAuthorization,
)
from domain.collection.submission import (
    LeaseFenceRef,
    OwnerAuthorityRef,
    SubmitDisposition,
    SubmitOnceCommand,
    WorkflowOperationInput,
    authority_digest,
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
            workflow=workflow,
            collection_surface=snapshot.owner.collection_surface,
            gateway_kind=snapshot.owner.gateway_kind,
            owner_protocol_revision=snapshot.owner.protocol_revision,
            assertion=assertion,
            authority=authority,
        )
    except ValueError as exc:
        raise ResourceOwnerGatewayError("submission_owner_projection_invalid") from exc


class SubmissionOwnerAuthorizationLoader(Protocol):
    """Load the exact authorization paired with one fresh submit command.

    A production implementation must run inside the serialized resource-owner
    turn and must not return a cached authorization.  No such implementation is
    registered by this module.
    """

    def load(self, command: SubmitOnceCommand) -> SubmissionOwnerAuthorization: ...


class ResourceOwnerSubmitTransport(Protocol):
    """Surface-specific transport running inside the serialized owner boundary."""

    def submit_once(
        self,
        command: SubmitOnceCommand,
        *,
        authorization: SideEffectAuthorization,
    ) -> SubmitDisposition: ...


class AuthorizedSubmitOnceGateway:
    """Validate a fresh claim, then invoke exactly one injected owner transport."""

    def __init__(
        self,
        *,
        collection_surface: CollectionSurface,
        gateway_kind: GatewayKind,
        owner_gateway_pub_id: str,
        owner_protocol_revision: str,
        authorization_loader: SubmissionOwnerAuthorizationLoader,
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
        result = self._transport.submit_once(
            command,
            authorization=authorization.assertion,
        )
        if not isinstance(result, SubmitDisposition):
            _fail("resource_owner_transport_result_invalid")
        if result.resolved_at < command.fresh_claim.claim.claimed_at:
            _fail("resource_owner_result_precedes_claim")
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


__all__ = [
    "AuthorizedSubmitOnceGateway",
    "ResourceOwnerGatewayError",
    "ResourceOwnerSubmitTransport",
    "SubmissionOwnerAuthorization",
    "SubmissionOwnerAuthorizationLoader",
    "authorize_submission_owner",
]
