"""Fail-closed PostgreSQL persistence adapter for collection submission v2.

This module is intentionally narrower than the no-I/O coordinator.  It owns
database transactions and calls only the restricted ``s10`` entry functions;
provider, browser, app, object-store, and event-bus I/O belong to gateways and
must happen after the transaction returning their durable command has closed.

The adapter is tenant/project scoped because :class:`OperationRef` deliberately
does not carry tenancy data while PostgreSQL RLS requires ``app.tenant_id``.
Schema capabilities are explicit.  A missing or mismatched entry function never
falls back to direct table DML.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Literal, Never, Protocol, Self, cast
from uuid import UUID

from domain.collection.submission import (
    AnalysisCommand,
    AnalysisTruth,
    CaptureChannel,
    CaptureDataClassification,
    CaptureDisposition,
    CaptureExistingCommand,
    CaptureNormalizationDecision,
    CaptureProvenance,
    CaptureStagingRef,
    CaptureTruth,
    ImmutableCaptureLink,
    OperationIdentity,
    OperationKeyMaterial,
    OperationRef,
    OutboxEventRef,
    OwnerClaimCasCommand,
    OwnerClaimCasObservation,
    OwnerClaimCasStatus,
    OwnerClaimTruth,
    PrepareDisposition,
    PrepareResult,
    QuotaTerminalEffect,
    RequestManifest,
    SlotOutcome,
    SubmissionOperationTruth,
    SurfaceProductRef,
    TerminalReason,
    TerminalSubmissionTransition,
    TerminalSubmissionTruth,
    apply_capture_disposition,
    authority_digest,
    begin_capture,
    canonical_json,
    capture_command_digest,
    deterministic_capture_staging_intent,
    deterministic_provider_idempotency_key,
    normalize_capture,
    operation_ref,
    request_manifest_digest,
)
from domain.collection.surface import CaptureState, CollectionSurface, SendState

from .quota_v2 import (
    ConnectionProtocol,
    ReserveQuotaRequest,
    ReserveQuotaResult,
    reserve_quota_after_operation_admission,
)
from .submission_v2 import (
    AtomicPreparationResult,
    CaptureAdmissionDecision,
    DurableAnalysisAttempt,
    DurableCaptureAdmission,
    DurableCaptureAttempt,
    DurableReconciliationClaim,
    PrepareWorkItem,
    QuotaConservationSnapshot,
    RepositoryCapabilities,
    ResolvedPreparationContext,
    ResolvedSubmissionContext,
    SlotOutcomeFact,
    SubmissionCoordinatorError,
    SubmissionWorkItem,
)


class SubmissionRepositoryError(SubmissionCoordinatorError):
    """Stable, non-secret PostgreSQL adapter failure."""


class QuotaReservationBlocked(SubmissionRepositoryError):
    """Capacity denial raised only after the outer prepare transaction rolls back."""

    def __init__(self, result: ReserveQuotaResult) -> None:
        self.result = result
        super().__init__("quota_capacity_blocked")


class _RollbackAtomicPreparation(Exception):
    """Internal sentinel which must cross the outer transaction boundary."""

    def __init__(self, result: ReserveQuotaResult) -> None:
        self.result = result
        super().__init__("rollback_atomic_preparation")


class RepositoryCursor(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class RepositoryConnection(ConnectionProtocol, Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...


class RepositoryConnectionFactory(Protocol):
    def __call__(self) -> RepositoryConnection: ...


class PreparationContextLoader(Protocol):
    """Resolve one bounded request manifest reference from durable storage only."""

    def __call__(
        self,
        connection: ConnectionProtocol,
        scope: RepositoryScope,
        work: PrepareWorkItem,
    ) -> ResolvedPreparationContext: ...


class SubmissionContextLoader(Protocol):
    """Resolve bounded owner-WAL metadata without contacting an external owner."""

    def __call__(
        self,
        connection: ConnectionProtocol,
        scope: RepositoryScope,
        work: SubmissionWorkItem,
    ) -> ResolvedSubmissionContext: ...


class QuotaReserver(Protocol):
    def __call__(
        self,
        connection: ConnectionProtocol,
        request: ReserveQuotaRequest,
    ) -> ReserveQuotaResult: ...


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    tenant_id: UUID
    project_id: UUID


@dataclass(frozen=True, slots=True)
class RestrictedFunctionContract:
    """Exact function identity and result shape audited by the adapter."""

    name: str
    argument_types: tuple[str, ...]
    result_columns: tuple[str, ...]
    database_result: str

    @property
    def regprocedure(self) -> str:
        return f"platform.{self.name}({','.join(self.argument_types)})"


S10_FUNCTION_CONTRACTS: tuple[RestrictedFunctionContract, ...] = (
    RestrictedFunctionContract(
        "create_collection_submission_operation_v2",
        (
            "uuid",
            "uuid",
            "text",
            "integer",
            "text",
            "text",
            "timestamptz",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
        ),
        ("operation_id", "created"),
        "TABLE(operation_id uuid, created boolean)",
    ),
    RestrictedFunctionContract(
        "prepare_collection_submission_request_v2",
        (
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "timestamptz",
        ),
        ("request_manifest_id", "capture_truth_id", "prepared"),
        "TABLE(request_manifest_id uuid, capture_truth_id uuid, prepared boolean)",
    ),
    RestrictedFunctionContract(
        "claim_collection_submission_v2",
        (
            "uuid",
            "uuid",
            "uuid",
            "text",
            "integer",
            "uuid",
            "integer",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "timestamptz",
        ),
        ("dispatch_id", "persisted_claim_pub_id", "claim_acquired"),
        "TABLE(dispatch_id uuid, persisted_claim_pub_id text, claim_acquired boolean)",
    ),
    RestrictedFunctionContract(
        "mark_collection_dispatch_reconciliation_ready_v2",
        ("uuid", "uuid", "uuid", "uuid", "integer", "text", "text", "timestamptz"),
        ("reconciliation_version",),
        "integer",
    ),
    RestrictedFunctionContract(
        "claim_collection_dispatch_reconciliation_v2",
        ("uuid", "uuid", "uuid", "uuid", "integer", "text", "text"),
        ("reconciliation_version",),
        "integer",
    ),
    RestrictedFunctionContract(
        "begin_collection_capture_v2",
        (
            "uuid",
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "timestamptz",
        ),
        ("capture_state_version", "capture_attempt_ordinal", "attempt_acquired"),
        (
            "TABLE(capture_state_version integer, capture_attempt_ordinal integer, "
            "attempt_acquired boolean)"
        ),
    ),
    RestrictedFunctionContract(
        "stage_collection_capture_manifest_v2",
        ("uuid",) * 4
        + ("integer",)
        + ("text",) * 10
        + ("bigint",)
        + ("text",) * 13
        + ("timestamptz",) * 3,
        ("capture_manifest_id",),
        "uuid",
    ),
    RestrictedFunctionContract(
        "finalize_collection_submission_v2",
        ("uuid",) * 5
        + ("integer",)
        + ("text",) * 11
        + ("timestamptz", "text", "text", "text", "integer"),
        (
            "send_state_version",
            "transition_evidence_id",
            "outbox_id",
            "finalized",
        ),
        (
            "TABLE(send_state_version integer, transition_evidence_id uuid, "
            "outbox_id uuid, finalized boolean)"
        ),
    ),
    RestrictedFunctionContract(
        "record_collection_slot_outcome_v2",
        ("uuid",) * 3
        + ("integer",) * 2
        + ("uuid",)
        + ("integer",) * 2
        + ("text",) * 3
        + ("boolean", "text", "text", "timestamptz"),
        ("slot_outcome_id", "fact_version", "outbox_id", "recorded"),
        ("TABLE(slot_outcome_id uuid, fact_version integer, outbox_id uuid, recorded boolean)"),
    ),
    RestrictedFunctionContract(
        "link_collection_capture_v2",
        (
            "uuid",
            "uuid",
            "uuid",
            "uuid",
            "uuid",
            "integer",
            "text",
            "text",
            "timestamptz",
        ),
        (
            "observation_id",
            "analysis_admission_id",
            "linked",
        ),
        "TABLE(observation_id uuid, analysis_admission_id uuid, linked boolean)",
    ),
    RestrictedFunctionContract(
        "advance_collection_governance_outbox_v2",
        ("uuid", "uuid", "uuid", "integer", "text", "text"),
        ("outbox_version",),
        "integer",
    ),
)


SET_LOCAL_TIMEZONE_SQL = "SET LOCAL TIME ZONE 'UTC'"
SET_TENANT_SQL = "SELECT set_config('app.tenant_id', CAST(%(tenant_id)s AS text), true)"

CREATE_OPERATION_SQL = """
SELECT operation_id, created
FROM platform.create_collection_submission_operation_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_pub_id)s,
  %(operation_generation)s,
  %(operation_key)s,
  %(operation_policy_revision)s,
  %(prepared_at)s,
  %(slot_pub_id)s,
  %(logical_item_key)s,
  %(campaign_pub_id)s,
  %(target_key)s,
  %(leg_key)s,
  %(platform)s,
  %(collection_surface)s,
  %(product_variant)s
)
"""

RESOLVE_PREPARATION_GOVERNANCE_SQL = """
SELECT binding.id, quota_registry.id
FROM platform.collection_binding_revision_v2 AS binding
JOIN platform.collection_quota_registry_revision AS quota_registry
  ON quota_registry.id = binding.quota_registry_id
 AND quota_registry.tenant_id = binding.tenant_id
 AND quota_registry.project_id = binding.project_id
JOIN platform.collection_submission_operation AS operation
  ON operation.tenant_id = binding.tenant_id
 AND operation.project_id = binding.project_id
 AND operation.platform = binding.platform
 AND operation.collection_surface = binding.collection_surface
 AND operation.product_variant = binding.product_variant
WHERE binding.tenant_id = %(tenant_id)s
  AND binding.project_id = %(project_id)s
  AND binding.pub_id = %(binding_revision_pub_id)s
  AND quota_registry.registry_revision = %(quota_registry_revision)s
  AND operation.id = %(operation_id)s
  AND binding.lifecycle_state = 'active'
  AND binding.activated_at IS NOT NULL
  AND binding.effective_from <= CURRENT_TIMESTAMP
  AND (binding.expires_at IS NULL OR binding.expires_at > CURRENT_TIMESTAMP)
  AND binding.suspended_at IS NULL
  AND binding.revoked_at IS NULL
  AND binding.superseded_at IS NULL
FOR KEY SHARE OF binding, quota_registry
"""

LOAD_RESERVATION_PUBLIC_ID_SQL = """
SELECT reservation.pub_id
FROM platform.collection_quota_reservation AS reservation
WHERE reservation.id = %(reservation_id)s
  AND reservation.tenant_id = %(tenant_id)s
  AND reservation.project_id = %(project_id)s
  AND reservation.operation_id = %(operation_id)s
  AND reservation.reservation_state = 'reserved'
"""

LOAD_REPLAY_RESERVATION_SQL = """
SELECT reservation.id, reservation.pub_id
FROM platform.collection_quota_reservation AS reservation
JOIN platform.collection_binding_revision_v2 AS binding
  ON binding.id = reservation.binding_revision_id
 AND binding.tenant_id = reservation.tenant_id
 AND binding.project_id = reservation.project_id
JOIN platform.collection_quota_registry_revision AS registry
  ON registry.id = reservation.quota_registry_id
 AND registry.tenant_id = reservation.tenant_id
 AND registry.project_id = reservation.project_id
WHERE reservation.tenant_id = %(tenant_id)s
  AND reservation.project_id = %(project_id)s
  AND reservation.operation_id = %(operation_id)s
  AND binding.pub_id = %(binding_revision_pub_id)s
  AND registry.registry_revision = %(quota_registry_revision)s
"""

PREPARE_REQUEST_SQL = """
SELECT request_manifest_id, capture_truth_id, prepared
FROM platform.prepare_collection_submission_request_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  1,
  %(request_payload_hash)s,
  %(request_manifest_hash)s,
  %(request_protocol_revision)s,
  %(adapter_request_revision)s,
  %(request_content_ref)s,
  %(provider_idempotency_key_hash)s,
  %(prepared_by_pub_id)s,
  %(prepared_at)s
)
"""

RESOLVE_CLAIM_SQL = """
SELECT operation.id,
       execution_grant.id,
       execution_grant.grant_hash,
       execution_grant.gateway_protocol_revision
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_execution_grant_v2 AS execution_grant
  ON execution_grant.operation_id = operation.id
 AND execution_grant.tenant_id = operation.tenant_id
 AND execution_grant.project_id = operation.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND execution_grant.pub_id = %(grant_pub_id)s
  AND execution_grant.grant_revision = %(grant_revision)s
"""

CLAIM_SUBMISSION_SQL = """
SELECT dispatch_id, persisted_claim_pub_id, claim_acquired
FROM platform.claim_collection_submission_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(claim_pub_id)s,
  %(expected_send_state_version)s,
  %(execution_grant_id)s,
  %(grant_revision)s,
  %(grant_hash)s,
  %(fence_set_hash)s,
  %(owner_handle)s,
  %(authority_snapshot_json)s,
  %(authority_hash)s,
  %(dispatch_key)s,
  %(owner_gateway_revision)s,
  %(owner_dispatch_ref)s,
  %(owner_wal_evidence_hash)s,
  %(claimed_at)s
)
"""

RESOLVE_CAPTURE_ATTEMPT_SQL = """
SELECT operation.id,
       dispatch.id,
       dispatch.owner_handle,
       dispatch.grant_resource_set_hash,
       dispatch.authority_sha256,
       dispatch.authority_snapshot_json,
       execution_grant.pub_id
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_submission_dispatch_v2 AS dispatch
  ON dispatch.operation_id = operation.id
 AND dispatch.tenant_id = operation.tenant_id
 AND dispatch.project_id = operation.project_id
JOIN platform.collection_execution_grant_v2 AS execution_grant
  ON execution_grant.id = dispatch.execution_grant_id
 AND execution_grant.tenant_id = dispatch.tenant_id
 AND execution_grant.project_id = dispatch.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND execution_grant.pub_id = %(grant_pub_id)s
"""

BEGIN_CAPTURE_SQL = """
SELECT capture_state_version, capture_attempt_ordinal, attempt_acquired
FROM platform.begin_collection_capture_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(dispatch_id)s,
  %(expected_capture_state_version)s,
  %(fence_set_hash)s,
  %(authority_sha256)s,
  %(owner_handle)s,
  %(capture_attempt_ref)s,
  %(capture_policy_revision)s,
  %(capture_request_sha256)s,
  %(capture_command_json)s,
  %(requested_at)s
)
"""

STAGE_CAPTURE_SQL = """
SELECT platform.stage_collection_capture_manifest_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(dispatch_id)s,
  %(expected_capture_state_version)s,
  %(fence_set_hash)s,
  %(owner_handle)s,
  %(capture_attempt_ref)s,
  %(capture_request_sha256)s,
  %(capture_key)s,
  %(capture_state)s,
  %(capture_channel)s,
  %(capture_protocol_revision)s,
  %(content_object_ref)s,
  %(content_hash)s,
  %(content_size_bytes)s,
  %(mime_type)s,
  %(capture_schema_revision)s,
  %(capture_manifest_hash)s,
  %(capture_evidence_ref)s,
  %(capture_evidence_hash)s,
  %(observed_platform)s,
  %(observed_surface)s,
  %(observed_product_variant)s,
  %(observed_product_version)s,
  %(capture_adapter_revision)s,
  %(data_classification)s,
  %(dlp_policy_revision)s,
  %(reason_code)s,
  %(captured_at)s,
  %(staged_at)s,
  %(retention_until)s
)
"""

LOAD_DISPATCH_RECONCILIATION_SQL = """
SELECT dispatch.id,
       dispatch.reconciliation_state,
       dispatch.owner_execution_state,
       dispatch.reconciliation_version,
       dispatch.reconcile_after,
       dispatch.reconciliation_claim_ref,
       dispatch.reconciliation_claim_hash,
       dispatch.owner_gateway_revision,
       dispatch.owner_dispatch_ref,
       dispatch.execution_grant_id,
       dispatch.grant_resource_set_hash
FROM platform.collection_submission_dispatch_v2 AS dispatch
JOIN platform.collection_submission_operation AS operation
  ON operation.id = dispatch.operation_id
 AND operation.tenant_id = dispatch.tenant_id
 AND operation.project_id = dispatch.project_id
WHERE dispatch.tenant_id = %(tenant_id)s
  AND dispatch.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
"""

RESOLVE_CAPTURE_ADMISSION_SQL = """
SELECT operation.send_state_version,
       operation.send_state,
       truth.capture_state_version,
       truth.capture_state,
       dispatch.reconciliation_state,
       dispatch.owner_execution_state,
       dispatch.reconciliation_version,
       dispatch.reconciliation_claim_ref,
       dispatch.reconciliation_claim_hash,
       execution_grant.pub_id,
       execution_grant.grant_state,
       execution_grant.expires_at,
       execution_grant.revoked_at,
       dispatch.owner_handle,
       dispatch.grant_resource_set_hash,
       '{"fences":[' || string_agg(
         '{"binding_resource_pub_id":"' || grant_resource.resource_pub_id ||
         '","generation":' || grant_resource.fence_generation::text ||
         ',"lease_pub_id":"' || lease.pub_id ||
         '","owner_handle":"' || grant_resource.owner_gateway_handle ||
         '","resource_role":"' || grant_resource.resource_role || '"}',
         ',' ORDER BY grant_resource.resource_role,
           grant_resource.resource_pub_id, lease.pub_id
       ) || '],"version":"lease-fence-identity-v1"}',
       array_agg(
         lease.pub_id
         ORDER BY grant_resource.resource_role, grant_resource.resource_ordinal
       ),
       count(*),
       count(*) FILTER (WHERE
         execution_grant.grant_state = 'issued'
         AND execution_grant.issued_at IS NOT NULL
         AND execution_grant.revoked_at IS NULL
         AND execution_grant.expires_at > CURRENT_TIMESTAMP
         AND lease.lease_state = 'active'
         AND lease.released_at IS NULL
         AND lease.revoked_at IS NULL
         AND lease.expires_at > CURRENT_TIMESTAMP
         AND lease.fencing_token = grant_resource.fence_generation
         AND grant_resource.lease_expires_at = lease.expires_at
         AND capacity.capacity_state = 'leased'
         AND capacity.current_fencing_token = grant_resource.fence_generation
         AND registration.state = 'active'
         AND grant_resource.owner_gateway_handle = dispatch.owner_handle
       ),
       count(*) FILTER (WHERE
         lease.lease_state IN ('released','expired','preempted','quarantined')
         AND (lease.lease_state <> 'released' OR lease.released_at IS NOT NULL)
         AND (lease.lease_state <> 'expired' OR lease.expires_at <= CURRENT_TIMESTAMP)
         AND (lease.lease_state NOT IN ('preempted','quarantined')
              OR lease.revoked_at IS NOT NULL)
         AND capacity.current_fencing_token >= grant_resource.fence_generation
         AND (capacity.current_fencing_token > grant_resource.fence_generation
              OR capacity.capacity_state <> 'leased')
       )
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_submission_dispatch_v2 AS dispatch
  ON dispatch.operation_id = operation.id
 AND dispatch.tenant_id = operation.tenant_id
 AND dispatch.project_id = operation.project_id
JOIN platform.collection_execution_grant_v2 AS execution_grant
  ON execution_grant.id = dispatch.execution_grant_id
 AND execution_grant.tenant_id = dispatch.tenant_id
 AND execution_grant.project_id = dispatch.project_id
JOIN platform.collection_execution_grant_resource AS grant_resource
  ON grant_resource.execution_grant_id = execution_grant.id
 AND grant_resource.tenant_id = execution_grant.tenant_id
 AND grant_resource.project_id = execution_grant.project_id
JOIN platform.resource_lease AS lease
  ON lease.id = grant_resource.resource_lease_id
 AND lease.tenant_id = grant_resource.tenant_id
 AND lease.project_id = grant_resource.project_id
JOIN platform.collection_resource_capacity_unit AS capacity
  ON capacity.id = grant_resource.capacity_unit_id
 AND capacity.tenant_id = grant_resource.tenant_id
 AND capacity.project_id = grant_resource.project_id
JOIN platform.resource_registration AS registration
  ON registration.id = grant_resource.resource_registration_id
 AND registration.tenant_id = grant_resource.tenant_id
 AND registration.project_id = grant_resource.project_id
JOIN platform.collection_capture_truth_v2 AS truth
  ON truth.operation_id = operation.id
 AND truth.tenant_id = operation.tenant_id
 AND truth.project_id = operation.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND execution_grant.pub_id = %(grant_pub_id)s
GROUP BY operation.id, operation.tenant_id, operation.project_id,
         operation.send_state_version, operation.send_state,
         truth.capture_state_version, truth.capture_state,
         dispatch.id, dispatch.reconciliation_state,
         dispatch.owner_execution_state, dispatch.reconciliation_version,
         dispatch.reconciliation_claim_ref, dispatch.reconciliation_claim_hash,
         dispatch.owner_handle, dispatch.grant_resource_set_hash,
         execution_grant.id, execution_grant.pub_id,
         execution_grant.grant_state, execution_grant.expires_at,
         execution_grant.revoked_at
"""

RESOLVE_TERMINAL_GRANT_SQL = """
SELECT operation.id,
       execution_grant.id,
       '{"fences":[' || string_agg(
         '{"binding_resource_pub_id":"' || grant_resource.resource_pub_id ||
         '","generation":' || grant_resource.fence_generation::text ||
         ',"lease_pub_id":"' || lease.pub_id ||
         '","owner_handle":"' || grant_resource.owner_gateway_handle ||
         '","resource_role":"' || grant_resource.resource_role || '"}',
         ',' ORDER BY grant_resource.resource_role,
           grant_resource.resource_pub_id, lease.pub_id
       ) || '],"version":"lease-fence-identity-v1"}',
       array_agg(
         lease.pub_id
         ORDER BY grant_resource.resource_role, grant_resource.resource_ordinal
       )
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_execution_grant_v2 AS execution_grant
  ON execution_grant.operation_id = operation.id
 AND execution_grant.tenant_id = operation.tenant_id
 AND execution_grant.project_id = operation.project_id
JOIN platform.collection_execution_grant_resource AS grant_resource
  ON grant_resource.execution_grant_id = execution_grant.id
 AND grant_resource.tenant_id = execution_grant.tenant_id
 AND grant_resource.project_id = execution_grant.project_id
JOIN platform.resource_lease AS lease
  ON lease.id = grant_resource.resource_lease_id
 AND lease.tenant_id = grant_resource.tenant_id
 AND lease.project_id = grant_resource.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND execution_grant.pub_id = %(grant_pub_id)s
GROUP BY operation.id, operation.tenant_id, operation.project_id, execution_grant.id
"""

CLAIM_RECONCILIATION_SQL = """
SELECT platform.claim_collection_dispatch_reconciliation_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(dispatch_id)s,
  %(expected_reconciliation_version)s,
  %(reconciliation_claim_ref)s,
  %(reconciliation_claim_hash)s
)
"""

FINALIZE_SUBMISSION_SQL = """
SELECT send_state_version, transition_evidence_id, outbox_id, finalized
FROM platform.finalize_collection_submission_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(dispatch_id)s,
  %(execution_grant_id)s,
  %(expected_send_state_version)s,
  %(target_send_state)s,
  %(terminal_reason)s,
  %(transition_key)s,
  %(owner_gateway_revision)s,
  %(owner_dispatch_ref)s,
  %(evidence_ref)s,
  %(evidence_hash)s,
  %(non_submission_proof_ref)s,
  %(provider_submission_ref)s,
  %(terminated_fence_set_hash)s,
  %(reason_code)s,
  %(resolved_at)s,
  %(terminal_payload_sha256)s,
  %(reconciliation_claim_ref)s,
  %(reconciliation_claim_hash)s,
  %(expected_reconciliation_version)s
)
"""

LOAD_EXACT_OUTBOX_SQL = """
SELECT outbox.event_key,
       outbox.event_type,
       outbox.aggregate_pub_id,
       outbox.aggregate_version,
       outbox.payload_hash,
       outbox.occurred_at
FROM platform.collection_governance_outbox_v2 AS outbox
WHERE outbox.tenant_id = %(tenant_id)s
  AND outbox.project_id = %(project_id)s
  AND outbox.event_key = %(event_key)s
"""

RESOLVE_FACT_BASIS_SQL = """
SELECT operation.id,
       manifest.id,
       truth.capture_state_version,
       outcome.fact_version
FROM platform.collection_submission_operation AS operation
LEFT JOIN platform.collection_capture_truth_v2 AS truth
  ON truth.operation_id = operation.id
 AND truth.tenant_id = operation.tenant_id
 AND truth.project_id = operation.project_id
LEFT JOIN platform.collection_capture_manifest_v2 AS manifest
  ON manifest.id = truth.current_capture_manifest_id
 AND manifest.tenant_id = truth.tenant_id
 AND manifest.project_id = truth.project_id
LEFT JOIN LATERAL (
    SELECT prior.fact_version
    FROM platform.collection_slot_outcome_v2 AS prior
    WHERE prior.operation_id = operation.id
      AND prior.tenant_id = operation.tenant_id
      AND prior.project_id = operation.project_id
    ORDER BY prior.fact_version DESC
    LIMIT 1
) AS outcome ON true
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
"""

RECORD_SLOT_OUTCOME_SQL = """
SELECT slot_outcome_id, fact_version, outbox_id, recorded
FROM platform.record_collection_slot_outcome_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(expected_operation_state_version)s,
  %(expected_prior_fact_version)s,
  %(capture_manifest_id)s,
  %(capture_state_version)s,
  %(analysis_state_version)s,
  %(capture_link_key)s,
  %(outcome_key)s,
  %(outcome_state)s,
  %(is_final_primary)s,
  %(outcome_payload_sha256)s,
  %(reason_code)s,
  %(recorded_at)s
)
"""

RESOLVE_CAPTURE_LINK_SQL = """
SELECT operation.id, dispatch.id, manifest.id, truth.capture_state_version
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_submission_dispatch_v2 AS dispatch
  ON dispatch.operation_id = operation.id
 AND dispatch.tenant_id = operation.tenant_id
 AND dispatch.project_id = operation.project_id
JOIN platform.collection_capture_truth_v2 AS truth
  ON truth.operation_id = operation.id
 AND truth.tenant_id = operation.tenant_id
 AND truth.project_id = operation.project_id
JOIN platform.collection_capture_manifest_v2 AS manifest
  ON manifest.id = truth.current_capture_manifest_id
 AND manifest.tenant_id = truth.tenant_id
 AND manifest.project_id = truth.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND manifest.capture_key = %(staging_key)s
  AND manifest.content_hash = %(content_sha256)s
  AND truth.capture_state_version = %(capture_state_version)s
"""

LINK_CAPTURE_SQL = """
SELECT observation_id, analysis_admission_id, linked
FROM platform.link_collection_capture_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(dispatch_id)s,
  %(capture_manifest_id)s,
  %(expected_capture_state_version)s,
  %(capture_link_key)s,
  %(analysis_contract_revision)s,
  %(linked_at)s
)
"""

PROBE_FUNCTION_SQL = """
SELECT resolved.function_identity::text,
       pg_get_function_result(resolved.function_identity),
       procedure.prosecdef,
       COALESCE(
         'search_path=pg_catalog, platform'=ANY(procedure.proconfig),
         false
       ),
       has_function_privilege(
         'geo_worker',resolved.function_identity,'EXECUTE'
       ),
       NOT EXISTS (
         SELECT 1
           FROM aclexplode(
             COALESCE(procedure.proacl,acldefault('f',procedure.proowner))
           ) privilege
          WHERE privilege.grantee=0
            AND privilege.privilege_type='EXECUTE'
       )
FROM (
  SELECT to_regprocedure(%(signature)s) AS function_identity
) AS resolved
LEFT JOIN pg_proc procedure ON procedure.oid=resolved.function_identity
"""

LOAD_OPERATION_SQL = """
SELECT operation.id,
       operation.pub_id,
       operation.operation_key,
       operation.operation_generation,
       operation.operation_policy_revision,
       operation.send_state,
       operation.send_state_version,
       operation.prepared_at,
       campaign.pub_id,
       slot.pub_id,
       target.target_key,
       leg.leg_key,
       operation.slot_key,
       operation.platform,
       operation.collection_surface,
       operation.product_variant,
       manifest.request_protocol_revision,
       manifest.adapter_request_revision,
       manifest.request_content_ref,
       manifest.request_payload_hash,
       manifest.request_manifest_hash,
       manifest.provider_idempotency_key_hash,
       dispatch.claim_pub_id,
       dispatch.owner_handle,
       execution_grant.pub_id,
       dispatch.grant_revision,
       dispatch.authority_sha256,
       dispatch.grant_resource_set_hash,
       dispatch.dispatch_key,
       dispatch.owner_dispatch_ref,
       dispatch.owner_wal_evidence_hash,
       dispatch.claimed_at,
       terminal.terminal_reason,
       terminal.evidence_ref,
       terminal.evidence_hash,
       terminal.non_submission_proof_ref,
       terminal.recorded_at,
       terminal.provider_reference_ref,
       terminal.terminated_fence_set_hash
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_campaign AS campaign
  ON campaign.id = operation.campaign_id
 AND campaign.tenant_id = operation.tenant_id
 AND campaign.project_id = operation.project_id
JOIN platform.collection_campaign_target AS target
  ON target.id = operation.campaign_target_id
 AND target.tenant_id = operation.tenant_id
 AND target.project_id = operation.project_id
JOIN platform.collection_sampling_leg AS leg
  ON leg.id = operation.sampling_leg_id
 AND leg.tenant_id = operation.tenant_id
 AND leg.project_id = operation.project_id
JOIN platform.collection_primary_slot AS slot
  ON slot.id = operation.primary_slot_id
 AND slot.tenant_id = operation.tenant_id
 AND slot.project_id = operation.project_id
JOIN platform.collection_submission_request_manifest_v2 AS manifest
  ON manifest.operation_id = operation.id
 AND manifest.tenant_id = operation.tenant_id
 AND manifest.project_id = operation.project_id
LEFT JOIN platform.collection_submission_dispatch_v2 AS dispatch
  ON dispatch.operation_id = operation.id
 AND dispatch.tenant_id = operation.tenant_id
 AND dispatch.project_id = operation.project_id
LEFT JOIN platform.collection_execution_grant_v2 AS execution_grant
  ON execution_grant.id = dispatch.execution_grant_id
 AND execution_grant.tenant_id = dispatch.tenant_id
 AND execution_grant.project_id = dispatch.project_id
LEFT JOIN LATERAL (
    SELECT evidence.terminal_reason,
           evidence.evidence_ref,
           evidence.evidence_hash,
           evidence.non_submission_proof_ref,
           evidence.recorded_at,
           evidence.provider_reference_ref,
           evidence.terminated_fence_set_hash
    FROM platform.collection_submission_transition_evidence_v2 AS evidence
    WHERE evidence.operation_id = operation.id
      AND evidence.tenant_id = operation.tenant_id
      AND evidence.project_id = operation.project_id
      AND evidence.evidence_state = 'accepted'
      AND evidence.to_send_state = operation.send_state
      AND evidence.to_send_state_version = operation.send_state_version
      AND evidence.to_send_state IN
          ('CONFIRMED_SENT', 'SEND_UNKNOWN', 'CONFIRMED_NOT_SENT')
    ORDER BY evidence.recorded_at DESC, evidence.id DESC
    LIMIT 1
) AS terminal ON true
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
"""

LOAD_CAPTURE_SQL = """
SELECT truth.capture_state,
       truth.capture_state_version,
       truth.current_attempt_ref,
       truth.active_request_sha256,
       truth.capture_requested_at,
       truth.capture_resolved_at,
       truth.active_command_json,
       operation.send_state,
       operation.send_resolved_at,
       operation.prepared_at,
       operation.platform,
       operation.collection_surface,
       operation.product_variant,
       target.target_key,
       dispatch.owner_handle,
       dispatch.grant_resource_set_hash,
       manifest.capture_key,
       manifest.content_object_ref,
       manifest.content_hash,
       manifest.content_size_bytes,
       manifest.mime_type,
       manifest.capture_schema_revision,
       manifest.staged_at,
       manifest.capture_evidence_ref,
       manifest.capture_evidence_hash,
       manifest.observed_platform,
       manifest.observed_surface,
       manifest.observed_product_variant,
       manifest.capture_state,
       manifest.storage_state,
       manifest.capture_channel,
       manifest.capture_protocol_revision,
       manifest.observed_product_version,
       manifest.capture_adapter_revision,
       manifest.data_classification,
       manifest.dlp_policy_revision,
       manifest.retention_until
FROM platform.collection_capture_truth_v2 AS truth
JOIN platform.collection_submission_operation AS operation
  ON operation.id = truth.operation_id
 AND operation.tenant_id = truth.tenant_id
 AND operation.project_id = truth.project_id
JOIN platform.collection_campaign_target AS target
  ON target.id = operation.campaign_target_id
 AND target.tenant_id = operation.tenant_id
 AND target.project_id = operation.project_id
LEFT JOIN platform.collection_submission_dispatch_v2 AS dispatch
  ON dispatch.operation_id = operation.id
 AND dispatch.tenant_id = operation.tenant_id
 AND dispatch.project_id = operation.project_id
LEFT JOIN platform.collection_capture_manifest_v2 AS manifest
  ON manifest.id = truth.current_capture_manifest_id
 AND manifest.tenant_id = truth.tenant_id
 AND manifest.project_id = truth.project_id
WHERE truth.tenant_id = %(tenant_id)s
  AND truth.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
"""

LOAD_CAPTURE_LINK_SQL = """
SELECT manifest.capture_link_key,
       manifest.capture_key,
       manifest.content_hash,
       operation.platform,
       operation.collection_surface,
       operation.product_variant,
       target.target_key,
       manifest.observed_platform,
       manifest.observed_surface,
       manifest.observed_product_variant,
       truth.capture_state_version,
       manifest.linked_at
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_campaign_target AS target
  ON target.id = operation.campaign_target_id
 AND target.tenant_id = operation.tenant_id
 AND target.project_id = operation.project_id
JOIN platform.collection_capture_truth_v2 AS truth
  ON truth.operation_id = operation.id
 AND truth.tenant_id = operation.tenant_id
 AND truth.project_id = operation.project_id
JOIN platform.collection_capture_manifest_v2 AS manifest
  ON manifest.id = truth.current_capture_manifest_id
 AND manifest.tenant_id = truth.tenant_id
 AND manifest.project_id = truth.project_id
WHERE operation.tenant_id = %(tenant_id)s
  AND operation.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND manifest.is_current = true
  AND manifest.storage_state = 'linked'
  AND manifest.capture_link_key IS NOT NULL
"""

LOAD_FACT_SQL = """
SELECT outcome.outcome_state,
       outcome.operation_state_version,
       outcome.capture_state_version,
       outcome.analysis_state_version,
       outcome.capture_link_key,
       outcome.is_final_primary,
       outcome.fact_version,
       outcome.decided_at,
       outcome.outcome_hash
FROM platform.collection_slot_outcome_v2 AS outcome
JOIN platform.collection_submission_operation AS operation
  ON operation.id = outcome.operation_id
 AND operation.tenant_id = outcome.tenant_id
 AND operation.project_id = outcome.project_id
WHERE outcome.tenant_id = %(tenant_id)s
  AND outcome.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
ORDER BY outcome.fact_version DESC
LIMIT 1
"""

LOAD_PENDING_OUTBOX_SQL = """
SELECT outbox.event_key,
       outbox.event_type,
       outbox.aggregate_pub_id,
       outbox.aggregate_version,
       outbox.payload_hash,
       outbox.occurred_at
FROM platform.collection_governance_outbox_v2 AS outbox
JOIN platform.collection_submission_operation AS operation
  ON operation.id = outbox.operation_id
 AND operation.tenant_id = outbox.tenant_id
 AND operation.project_id = outbox.project_id
WHERE outbox.tenant_id = %(tenant_id)s
  AND outbox.project_id = %(project_id)s
  AND operation.pub_id = %(operation_pub_id)s
  AND operation.operation_key = %(operation_key)s
  AND operation.operation_generation = %(operation_generation)s
  AND outbox.publish_state = 'pending'
  AND outbox.available_at <= CURRENT_TIMESTAMP
ORDER BY outbox.occurred_at, outbox.event_key
"""

LOAD_OUTBOX_FOR_MARK_SQL = """
SELECT outbox.id, outbox.version, outbox.publish_state
FROM platform.collection_governance_outbox_v2 AS outbox
WHERE outbox.tenant_id = %(tenant_id)s
  AND outbox.project_id = %(project_id)s
  AND outbox.event_key = %(event_key)s
"""

ADVANCE_OUTBOX_SQL = """
SELECT platform.advance_collection_governance_outbox_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(outbox_id)s,
  %(expected_version)s,
  'published',
  NULL
)
"""

LOAD_QUOTA_SNAPSHOT_SQL = """
SELECT reservation.requested_units,
       reservation.reservation_state,
       reservation.expected_effect_count,
       count(effect.id),
       count(*) FILTER (WHERE effect.effect_state = 'reserved'),
       count(*) FILTER (WHERE effect.effect_state = 'settled_consumed'),
       count(*) FILTER (WHERE effect.effect_state = 'settled_unknown'),
       count(*) FILTER (WHERE effect.effect_state = 'released')
FROM platform.collection_quota_reservation AS reservation
JOIN platform.collection_quota_reservation_effect AS effect
  ON effect.reservation_id = reservation.id
 AND effect.tenant_id = reservation.tenant_id
 AND effect.project_id = reservation.project_id
WHERE reservation.tenant_id = %(tenant_id)s
  AND reservation.project_id = %(project_id)s
  AND reservation.pub_id = %(reservation_pub_id)s
GROUP BY reservation.id, reservation.requested_units,
         reservation.reservation_state, reservation.expected_effect_count
"""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SubmissionRepositoryError(f"database_{field}_invalid")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SubmissionRepositoryError(f"database_{field}_invalid")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise SubmissionRepositoryError(f"database_{field}_invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SubmissionRepositoryError(f"database_{field}_invalid")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _optional_integer(value: object, field: str) -> int | None:
    return None if value is None else _integer(value, field)


def _optional_datetime(value: object, field: str) -> datetime | None:
    return None if value is None else _aware_datetime(value, field)


def _text_array(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise SubmissionRepositoryError(f"database_{field}_invalid")
    return tuple(_text(item, field) for item in value)


def _uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise SubmissionRepositoryError(f"database_{field}_invalid") from exc
    raise SubmissionRepositoryError(f"database_{field}_invalid")


def _require_row(row: Sequence[object] | None, *, size: int, code: str) -> Sequence[object]:
    if row is None or len(row) != size:
        raise SubmissionRepositoryError(code)
    return row


def _operation_params(scope: RepositoryScope, operation: OperationRef) -> dict[str, object]:
    return {
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "operation_pub_id": operation.operation_pub_id,
        "operation_key": operation.operation_key,
        "operation_generation": operation.generation,
    }


def _prepare_params(
    scope: RepositoryScope,
    work: PrepareWorkItem,
    prepared: PrepareResult,
) -> dict[str, object]:
    identity = prepared.operation.identity
    material = identity.material
    if material.tenant_id != scope.tenant_id or material.project_id != scope.project_id:
        raise SubmissionRepositoryError("preparation_scope_mismatch")
    if work.frozen_slot_ref != material.slot_pub_id:
        raise SubmissionRepositoryError("preparation_slot_reference_mismatch")
    if work.workflow.operation != operation_ref(identity):
        raise SubmissionRepositoryError("preparation_operation_reference_mismatch")
    # A preparation command always names the initial NOT_SENT version.  On an
    # exact recovery replay the durable operation may already have advanced;
    # its immutable identity and prepared_at remain the replay authority.
    if work.workflow.expected_state_version != 1:
        raise SubmissionRepositoryError("preparation_expected_version_mismatch")
    return {
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "operation_pub_id": identity.operation_pub_id,
        "operation_generation": material.generation,
        "operation_key": identity.operation_key,
        "operation_policy_revision": material.operation_policy_revision,
        "prepared_at": prepared.operation.prepared_at,
        "slot_pub_id": material.slot_pub_id,
        "logical_item_key": material.logical_item_key,
        "campaign_pub_id": material.campaign_pub_id,
        "target_key": material.target_key,
        "leg_key": material.leg_key,
        "platform": identity.surface_product.platform,
        "collection_surface": identity.surface_product.collection_surface.value,
        "product_variant": identity.surface_product.product_variant,
        "binding_revision_pub_id": work.binding_revision_pub_id,
        "quota_registry_revision": work.quota_registry_revision,
    }


def _set_tenant(connection: ConnectionProtocol, scope: RepositoryScope) -> None:
    connection.execute(SET_LOCAL_TIMEZONE_SQL)
    connection.execute(SET_TENANT_SQL, {"tenant_id": scope.tenant_id})


def _quota_snapshot_on_connection(
    connection: ConnectionProtocol,
    scope: RepositoryScope,
    reservation_pub_id: str,
) -> QuotaConservationSnapshot:
    row = connection.execute(
        LOAD_QUOTA_SNAPSHOT_SQL,
        {
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "reservation_pub_id": reservation_pub_id,
        },
    ).fetchone()
    values = _require_row(row, size=8, code="quota_snapshot_missing")
    requested = _integer(values[0], "quota_requested_units")
    state = _text(values[1], "quota_reservation_state")
    expected_effect_count = _integer(values[2], "quota_expected_effect_count")
    actual_effect_count = _integer(values[3], "quota_actual_effect_count")
    counts = tuple(_integer(value, "quota_effect_state_count") for value in values[4:8])
    if expected_effect_count < 1 or actual_effect_count != expected_effect_count:
        raise SubmissionRepositoryError("quota_effect_set_incomplete")
    state_index = {
        "reserved": 0,
        "settled_consumed": 1,
        "settled_unknown": 2,
        "released": 3,
    }.get(state)
    if (
        state_index is None
        or counts[state_index] != expected_effect_count
        or sum(counts) != (expected_effect_count)
    ):
        raise SubmissionRepositoryError("quota_effect_set_not_atomic")
    units = [0, 0, 0, 0]
    units[state_index] = requested
    return QuotaConservationSnapshot(
        requested_units=requested,
        reserved_units=units[0],
        consumed_units=units[1],
        unknown_units=units[2],
        released_units=units[3],
    )


def _load_operation_on_connection(
    connection: ConnectionProtocol,
    scope: RepositoryScope,
    operation: OperationRef,
) -> SubmissionOperationTruth:
    row = _require_row(
        connection.execute(LOAD_OPERATION_SQL, _operation_params(scope, operation)).fetchone(),
        size=39,
        code="operation_reload_failed",
    )
    persisted = _operation_from_row(scope, row)
    if operation_ref(persisted.identity) != operation:
        raise SubmissionRepositoryError("operation_reference_drift")
    return persisted


def _validate_exact_outbox(
    connection: ConnectionProtocol,
    scope: RepositoryScope,
    expected: OutboxEventRef,
) -> None:
    row = _require_row(
        connection.execute(
            LOAD_EXACT_OUTBOX_SQL,
            {
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "event_key": expected.outbox_key,
            },
        ).fetchone(),
        size=6,
        code="exact_outbox_missing",
    )
    actual = OutboxEventRef(
        outbox_key=_text(row[0], "outbox_event_key"),
        event_type=_text(row[1], "outbox_event_type"),
        aggregate_ref=_text(row[2], "outbox_aggregate_ref"),
        aggregate_version=_integer(row[3], "outbox_aggregate_version"),
        payload_sha256=_text(row[4], "outbox_payload_hash"),
        occurred_at=_aware_datetime(row[5], "outbox_occurred_at"),
    )
    if actual != expected:
        raise SubmissionRepositoryError("exact_outbox_payload_drift")


def _surface(
    *, platform: object, collection_surface: object, product_variant: object, target_key: object
) -> SurfaceProductRef:
    return SurfaceProductRef(
        platform=_text(platform, "platform"),
        collection_surface=CollectionSurface(_text(collection_surface, "collection_surface")),
        product_variant=_text(product_variant, "product_variant"),
        target_key=_text(target_key, "target_key"),
    )


def _operation_from_row(scope: RepositoryScope, row: Sequence[object]) -> SubmissionOperationTruth:
    values = _require_row(row, size=39, code="operation_row_shape_invalid")
    manifest = RequestManifest(
        request_protocol_version=_text(values[16], "request_protocol_revision"),
        request_schema_revision=_text(values[17], "adapter_request_revision"),
        request_payload_ref=_text(values[18], "request_content_ref"),
        request_payload_sha256=_text(values[19], "request_payload_hash"),
    )
    manifest_hash = _text(values[20], "request_manifest_hash")
    if request_manifest_digest(manifest) != manifest_hash:
        raise SubmissionRepositoryError("request_manifest_hash_drift")
    operation_key = _text(values[2], "operation_key")
    provider_key = deterministic_provider_idempotency_key(operation_key)
    if sha256(provider_key.encode()).hexdigest() != _text(
        values[21], "provider_idempotency_key_hash"
    ):
        raise SubmissionRepositoryError("provider_idempotency_key_hash_drift")
    surface = _surface(
        platform=values[13],
        collection_surface=values[14],
        product_variant=values[15],
        target_key=values[10],
    )
    identity = OperationIdentity(
        material=OperationKeyMaterial(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            campaign_pub_id=_text(values[8], "campaign_pub_id"),
            slot_pub_id=_text(values[9], "slot_pub_id"),
            target_key=surface.target_key,
            leg_key=_text(values[11], "leg_key"),
            logical_item_key=_text(values[12], "slot_key"),
            generation=_integer(values[3], "operation_generation"),
            operation_policy_revision=_text(values[4], "operation_policy_revision"),
        ),
        surface_product=surface,
        operation_pub_id=_text(values[1], "operation_pub_id"),
        operation_key=operation_key,
        request_manifest=manifest,
        request_manifest_sha256=manifest_hash,
        provider_idempotency_key=provider_key,
    )
    send_state = SendState(_text(values[5], "send_state"))
    claim: OwnerClaimTruth | None = None
    if values[22] is not None:
        claim = OwnerClaimTruth(
            claim_pub_id=_text(values[22], "claim_pub_id"),
            owner_handle=_text(values[23], "owner_handle"),
            grant_pub_id=_text(values[24], "grant_pub_id"),
            grant_revision=_integer(values[25], "grant_revision"),
            authority_sha256=_text(values[26], "authority_sha256"),
            fence_set_sha256=_text(values[27], "fence_set_sha256"),
            dispatch_key=_text(values[28], "dispatch_key"),
            owner_dispatch_ref=_text(values[29], "owner_dispatch_ref"),
            owner_wal_evidence_sha256=_text(values[30], "owner_wal_evidence_hash"),
            claimed_at=_aware_datetime(values[31], "claimed_at"),
        )
    terminal: TerminalSubmissionTruth | None = None
    if send_state in {
        SendState.CONFIRMED_SENT,
        SendState.SEND_UNKNOWN,
        SendState.CONFIRMED_NOT_SENT,
    }:
        reason = TerminalReason(_text(values[32], "terminal_reason"))
        non_submission_proof_ref = _optional_text(values[35], "non_submission_proof_ref")
        persisted_fence_hash = _optional_text(values[38], "terminated_fence_set_hash")
        is_post_claim_not_sent = reason is TerminalReason.POST_CLAIM_NOT_SENT
        is_preflight_not_sent = reason in {
            TerminalReason.PREFLIGHT_NOT_SENT,
            TerminalReason.UNAVAILABLE,
            TerminalReason.INVALID_SURFACE_OR_PRODUCT,
        }
        if is_post_claim_not_sent != (
            non_submission_proof_ref is not None and persisted_fence_hash is not None
        ):
            raise SubmissionRepositoryError("terminal_non_submission_proof_shape_invalid")
        if is_preflight_not_sent != (
            non_submission_proof_ref is None and persisted_fence_hash is not None
        ):
            raise SubmissionRepositoryError("preflight_terminal_fence_shape_invalid")
        if (
            not is_post_claim_not_sent
            and not is_preflight_not_sent
            and (non_submission_proof_ref is not None or persisted_fence_hash is not None)
        ):
            raise SubmissionRepositoryError("submitted_terminal_fence_shape_invalid")
        terminal = TerminalSubmissionTruth(
            send_state=send_state,
            reason=reason,
            boundary_entered=reason in {TerminalReason.SUBMITTED, TerminalReason.SEND_UNKNOWN},
            evidence_ref=_text(values[33], "terminal_evidence_ref"),
            evidence_sha256=_text(values[34], "terminal_evidence_hash"),
            resolved_at=_aware_datetime(values[36], "terminal_recorded_at"),
            provider_submission_ref=_optional_text(values[37], "provider_submission_ref"),
            non_submission_proof_ref=non_submission_proof_ref,
            terminated_fence_set_sha256=persisted_fence_hash,
        )
    operation = SubmissionOperationTruth(
        identity=identity,
        send_state=send_state,
        state_version=_integer(values[6], "send_state_version"),
        prepared_at=_aware_datetime(values[7], "prepared_at"),
        claim=claim,
        terminal=terminal,
    )
    return operation


def _capture_record_from_row(
    operation: OperationRef,
    row: Sequence[object],
) -> tuple[CaptureTruth, str | None]:
    values = _require_row(row, size=37, code="capture_row_shape_invalid")
    state = CaptureState(_text(values[0], "capture_state"))
    active_command_json = _optional_text(values[6], "capture_active_command_json")
    source_send_state = SendState(_text(values[7], "capture_source_send_state"))
    if source_send_state not in {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}:
        raise SubmissionRepositoryError("capture_source_send_state_invalid")
    owner_handle = _text(values[14], "capture_owner_handle")
    fence_hash = _text(values[15], "capture_fence_set_hash")
    expected_surface = _surface(
        platform=values[10],
        collection_surface=values[11],
        product_variant=values[12],
        target_key=values[13],
    )
    updated_at = (
        _optional_datetime(values[5], "capture_resolved_at")
        or _optional_datetime(values[4], "capture_requested_at")
        or _optional_datetime(values[8], "send_resolved_at")
        or _aware_datetime(values[9], "operation_prepared_at")
    )
    staging: CaptureStagingRef | None = None
    evidence_ref: str | None = None
    evidence_hash: str | None = None
    observed: SurfaceProductRef | None = None
    provenance: CaptureProvenance | None = None
    normalization: CaptureNormalizationDecision | None = None
    terminal_states = {
        CaptureState.COMPLETED,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.NOT_OBSERVABLE,
    }
    if state in terminal_states:
        if CaptureState(_text(values[28], "manifest_capture_state")) is not state:
            raise SubmissionRepositoryError("capture_manifest_truth_state_drift")
        evidence_ref = _text(values[23], "capture_evidence_ref")
        evidence_hash = _text(values[24], "capture_evidence_hash")
        observed_platform = _text(values[25], "observed_platform")
        observed_surface = _text(values[26], "observed_surface")
        observed_variant = _text(values[27], "observed_product_variant")
        observed = _surface(
            platform=observed_platform,
            collection_surface=observed_surface,
            product_variant=observed_variant,
            target_key=(
                "collection-target-v1|platform="
                f"{observed_platform}|collection_surface={observed_surface}|"
                f"product_variant={observed_variant}"
            ),
        )
        provenance = CaptureProvenance(
            capture_channel=CaptureChannel(_text(values[30], "capture_channel")),
            capture_protocol_revision=_text(values[31], "capture_protocol_revision"),
            observed_product_version=_text(values[32], "observed_product_version"),
            capture_adapter_revision=_text(values[33], "capture_adapter_revision"),
            data_classification=CaptureDataClassification(
                _text(values[34], "capture_data_classification")
            ),
            dlp_policy_revision=_text(values[35], "dlp_policy_revision"),
            retention_until=_aware_datetime(values[36], "capture_retention_until"),
        )
        storage_state = _text(values[29], "capture_storage_state")
        if observed != expected_surface:
            if storage_state not in {"quarantined", "orphaned"}:
                raise SubmissionRepositoryError("capture_surface_mismatch_storage_state_invalid")
            normalization = CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
        else:
            normalization = CaptureNormalizationDecision.ACCEPTED
    if state in {CaptureState.COMPLETED, CaptureState.PARTIAL}:
        staging = CaptureStagingRef(
            staging_key=_text(values[16], "capture_key"),
            object_ref=_text(values[17], "content_object_ref"),
            content_sha256=_text(values[18], "content_hash"),
            byte_size=_integer(values[19], "content_size_bytes"),
            media_type=_text(values[20], "mime_type"),
            capture_schema_revision=_text(values[21], "capture_schema_revision"),
            staged_at=_aware_datetime(values[22], "staged_at"),
        )
    if state is CaptureState.CAPTURING and active_command_json is None:
        raise SubmissionRepositoryError("capture_active_command_missing")
    return (
        CaptureTruth(
            operation=operation,
            source_send_state=cast(
                Literal[SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN],
                source_send_state,
            ),
            expected_surface_product=expected_surface,
            owner_handle=owner_handle,
            fence_set_sha256=fence_hash,
            capture_state=state,
            state_version=_integer(values[1], "capture_state_version"),
            active_attempt_ref=(
                _optional_text(values[2], "capture_attempt_ref")
                if state is CaptureState.CAPTURING
                else None
            ),
            active_request_sha256=(
                _optional_text(values[3], "capture_request_sha256")
                if state is CaptureState.CAPTURING
                else None
            ),
            staging=staging,
            evidence_ref=evidence_ref,
            evidence_sha256=evidence_hash,
            observed_surface_product=observed,
            provenance=provenance,
            normalization=normalization,
            updated_at=updated_at,
        ),
        active_command_json,
    )


class PostgresSubmissionRepository:
    """PostgreSQL-backed, scope-bound collection submission store.

    Write capabilities which cannot be represented by the current restricted
    function signatures remain false.  Coordinator capability gates therefore
    stop before any external gateway is invoked.
    """

    def __init__(
        self,
        *,
        scope: RepositoryScope,
        connection_factory: RepositoryConnectionFactory,
        prepared_by_pub_id: str,
        analysis_contract_revision: str | None = None,
        send_unknown_reconcile_delay: timedelta = timedelta(minutes=5),
        preparation_context_loader: PreparationContextLoader | None = None,
        submission_context_loader: SubmissionContextLoader | None = None,
        quota_reserver: QuotaReserver = reserve_quota_after_operation_admission,
    ) -> None:
        self._scope = scope
        self._connection_factory = connection_factory
        self._prepared_by_pub_id = prepared_by_pub_id
        self._analysis_contract_revision = analysis_contract_revision
        self._send_unknown_reconcile_delay = send_unknown_reconcile_delay
        self._preparation_context_loader = preparation_context_loader
        self._submission_context_loader = submission_context_loader
        self._quota_reserver = quota_reserver
        self._missing_contracts_cache: tuple[str, ...] | None = None
        self._contract_probe_lock = Lock()

    def capabilities(self) -> RepositoryCapabilities:
        missing = set(self.missing_function_contracts())

        def available(*names: str) -> bool:
            return all(
                contract.regprocedure not in missing
                for contract in S10_FUNCTION_CONTRACTS
                if contract.name in names
            ) and all(
                any(contract.name == name for contract in S10_FUNCTION_CONTRACTS) for name in names
            )

        prepare = available(
            "create_collection_submission_operation_v2",
            "prepare_collection_submission_request_v2",
        )
        claim = available("claim_collection_submission_v2")
        reconciliation = available(
            "mark_collection_dispatch_reconciliation_ready_v2",
            "claim_collection_dispatch_reconciliation_v2",
        )
        capture = available(
            "begin_collection_capture_v2",
            "stage_collection_capture_manifest_v2",
        )
        finalize = available("finalize_collection_submission_v2")
        capture_link = available("link_collection_capture_v2")
        fact = available("record_collection_slot_outcome_v2")
        outbox = available("advance_collection_governance_outbox_v2")
        return RepositoryCapabilities(
            atomic_prepare_and_reserve=prepare,
            durable_owner_claim_cas=claim,
            durable_owner_reconciliation=reconciliation,
            exclusive_reconciliation_claim=reconciliation,
            atomic_terminal_and_quota=finalize,
            terminal_replay_integrity=finalize,
            durable_capture_command=capture,
            durable_capture_admission=(claim and reconciliation and finalize and capture),
            immutable_capture_link=capture_link,
            durable_analysis_command=False,
            atomic_fact_and_outbox=fact,
            idempotent_outbox_delivery=outbox,
            quota_effect_ledger_conservation=prepare and finalize,
        )

    def missing_function_contracts(self) -> tuple[str, ...]:
        """Return missing exact s10 signatures without mutating schema state."""

        cached = self._missing_contracts_cache
        if cached is not None:
            return cached
        with self._contract_probe_lock:
            cached = self._missing_contracts_cache
            if cached is not None:
                return cached
            missing: list[str] = []
            try:
                with self._connection_factory() as connection:
                    with connection.transaction():
                        _set_tenant(connection, self._scope)
                        for contract in S10_FUNCTION_CONTRACTS:
                            row = connection.execute(
                                PROBE_FUNCTION_SQL,
                                {"signature": contract.regprocedure},
                            ).fetchone()
                            if (
                                row is None
                                or len(row) != 6
                                or row[0] is None
                                or row[1] != contract.database_result
                                or row[2] is not True
                                or row[3] is not True
                                or row[4] is not True
                                or row[5] is not True
                            ):
                                missing.append(contract.regprocedure)
            except Exception:
                missing = [contract.regprocedure for contract in S10_FUNCTION_CONTRACTS]
            cached = tuple(missing)
            self._missing_contracts_cache = cached
            return cached

    def resolve_preparation_context(self, work: PrepareWorkItem) -> ResolvedPreparationContext:
        loader = self._preparation_context_loader
        if loader is None:
            raise SubmissionRepositoryError("preparation_context_loader_unavailable")
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                context = loader(connection, self._scope, work)
        if operation_ref(context.prepare.identity) != work.workflow.operation:
            raise SubmissionRepositoryError("preparation_context_operation_drift")
        request_payload_ref = context.prepare.identity.request_manifest.request_payload_ref
        if request_payload_ref != work.request_manifest_ref:
            raise SubmissionRepositoryError("preparation_context_manifest_ref_drift")
        return context

    def resolve_context(self, work: SubmissionWorkItem) -> ResolvedSubmissionContext:
        loader = self._submission_context_loader
        if loader is None:
            raise SubmissionRepositoryError("submission_context_loader_unavailable")
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                context = loader(connection, self._scope, work)
        if operation_ref(context.prepare.identity) != work.workflow.operation:
            raise SubmissionRepositoryError("submission_context_operation_drift")
        return context

    def atomic_prepare_and_reserve(
        self,
        work: PrepareWorkItem,
        prepared: PrepareResult,
    ) -> AtomicPreparationResult:
        """Insert operation, reserve every authoritative scope, then manifest.

        ``reserve_quota`` uses a nested savepoint.  A capacity result is raised
        through the outer transaction with a private sentinel so the operation
        insert is rolled back as well; only after that rollback is it translated
        to the public stable error.
        """

        params = _prepare_params(self._scope, work, prepared)
        try:
            with self._connection_factory() as connection:
                try:
                    with connection.transaction():
                        _set_tenant(connection, self._scope)
                        operation_row = _require_row(
                            connection.execute(
                                CREATE_OPERATION_SQL,
                                params,
                            ).fetchone(),
                            size=2,
                            code="prepared_operation_identity_drift",
                        )
                        operation_id = _uuid(operation_row[0], "operation_id")
                        _boolean(operation_row[1], "operation_created")
                        advanced_replay = (
                            prepared.disposition is PrepareDisposition.EXACT_REPLAY
                            and prepared.operation.send_state is not SendState.NOT_SENT
                        )
                        if advanced_replay:
                            reservation_row = _require_row(
                                connection.execute(
                                    LOAD_REPLAY_RESERVATION_SQL,
                                    {**params, "operation_id": operation_id},
                                ).fetchone(),
                                size=2,
                                code="replayed_quota_reservation_missing",
                            )
                            _uuid(reservation_row[0], "quota_reservation_id")
                            reservation_pub_id = _text(
                                reservation_row[1],
                                "quota_reservation_pub_id",
                            )
                        else:
                            governance = _require_row(
                                connection.execute(
                                    RESOLVE_PREPARATION_GOVERNANCE_SQL,
                                    {**params, "operation_id": operation_id},
                                ).fetchone(),
                                size=2,
                                code="preparation_governance_unavailable",
                            )
                            binding_id = _uuid(governance[0], "binding_revision_id")
                            registry_id = _uuid(governance[1], "quota_registry_id")
                            reservation = self._quota_reserver(
                                connection,
                                ReserveQuotaRequest(
                                    tenant_id=self._scope.tenant_id,
                                    project_id=self._scope.project_id,
                                    operation_id=operation_id,
                                    binding_id=binding_id,
                                    registry_id=registry_id,
                                    requested_units=1,
                                ),
                            )
                            if not reservation.reserved or reservation.reservation_id is None:
                                raise _RollbackAtomicPreparation(reservation)
                            reservation_row = _require_row(
                                connection.execute(
                                    LOAD_RESERVATION_PUBLIC_ID_SQL,
                                    {
                                        "tenant_id": self._scope.tenant_id,
                                        "project_id": self._scope.project_id,
                                        "operation_id": operation_id,
                                        "reservation_id": reservation.reservation_id,
                                    },
                                ).fetchone(),
                                size=1,
                                code="quota_reservation_public_id_missing",
                            )
                            reservation_pub_id = _text(
                                reservation_row[0],
                                "quota_reservation_pub_id",
                            )
                        identity = prepared.operation.identity
                        manifest = identity.request_manifest
                        prepared_row = _require_row(
                            connection.execute(
                                PREPARE_REQUEST_SQL,
                                {
                                    "tenant_id": self._scope.tenant_id,
                                    "project_id": self._scope.project_id,
                                    "operation_id": operation_id,
                                    "request_payload_hash": manifest.request_payload_sha256,
                                    "request_manifest_hash": identity.request_manifest_sha256,
                                    "request_protocol_revision": (
                                        manifest.request_protocol_version
                                    ),
                                    "adapter_request_revision": (manifest.request_schema_revision),
                                    "request_content_ref": manifest.request_payload_ref,
                                    "provider_idempotency_key_hash": sha256(
                                        identity.provider_idempotency_key.encode()
                                    ).hexdigest(),
                                    "prepared_by_pub_id": self._prepared_by_pub_id,
                                    "prepared_at": prepared.operation.prepared_at,
                                },
                            ).fetchone(),
                            size=3,
                            code="request_manifest_prepare_failed",
                        )
                        _uuid(prepared_row[0], "request_manifest_id")
                        _uuid(prepared_row[1], "capture_truth_id")
                        _boolean(prepared_row[2], "request_manifest_prepared")
                        persisted_row = _require_row(
                            connection.execute(
                                LOAD_OPERATION_SQL,
                                _operation_params(self._scope, work.workflow.operation),
                            ).fetchone(),
                            size=39,
                            code="prepared_operation_reload_failed",
                        )
                        persisted = _operation_from_row(self._scope, persisted_row)
                        if persisted != prepared.operation:
                            raise SubmissionRepositoryError(
                                "atomic_preparation_persisted_truth_drift"
                            )
                        quota = _quota_snapshot_on_connection(
                            connection,
                            self._scope,
                            reservation_pub_id,
                        )
                        result = AtomicPreparationResult(
                            operation=persisted,
                            reservation_pub_id=reservation_pub_id,
                            quota=quota,
                        )
                except _RollbackAtomicPreparation:
                    raise
        except _RollbackAtomicPreparation as blocked:
            raise QuotaReservationBlocked(blocked.result) from None
        return result

    def load_operation(self, operation: OperationRef) -> SubmissionOperationTruth | None:
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                row = connection.execute(
                    LOAD_OPERATION_SQL,
                    _operation_params(self._scope, operation),
                ).fetchone()
        if row is None:
            return None
        persisted = _operation_from_row(self._scope, row)
        if operation_ref(persisted.identity) != operation:
            raise SubmissionRepositoryError("operation_reference_drift")
        return persisted

    def assert_operation_integrity(
        self,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
    ) -> None:
        """Fail closed unless the caller's complete bounded truth matches PostgreSQL."""

        if operation_ref(operation.identity) != work.workflow.operation:
            raise SubmissionRepositoryError("operation_integrity_reference_drift")
        persisted = self.load_operation(work.workflow.operation)
        if persisted is None:
            raise SubmissionRepositoryError("operation_integrity_truth_missing")
        if persisted != operation:
            raise SubmissionRepositoryError("operation_integrity_truth_drift")

    def compare_and_swap(self, command: OwnerClaimCasCommand) -> OwnerClaimCasObservation:
        claim = command.claim
        params = _operation_params(self._scope, command.operation)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                resolved = _require_row(
                    connection.execute(
                        RESOLVE_CLAIM_SQL,
                        {
                            **params,
                            "grant_pub_id": claim.grant_pub_id,
                            "grant_revision": claim.grant_revision,
                        },
                    ).fetchone(),
                    size=4,
                    code="claim_authority_not_resolved",
                )
                operation_id = _uuid(resolved[0], "operation_id")
                result = _require_row(
                    connection.execute(
                        CLAIM_SUBMISSION_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": operation_id,
                            "claim_pub_id": claim.claim_pub_id,
                            "expected_send_state_version": command.expected_state_version,
                            "execution_grant_id": _uuid(resolved[1], "execution_grant_id"),
                            "grant_revision": claim.grant_revision,
                            "grant_hash": _text(resolved[2], "grant_hash"),
                            "fence_set_hash": claim.fence_set_sha256,
                            "owner_handle": claim.owner_handle,
                            "authority_snapshot_json": canonical_json(command.authority),
                            "authority_hash": claim.authority_sha256,
                            "dispatch_key": claim.dispatch_key,
                            "owner_gateway_revision": _text(resolved[3], "owner_gateway_revision"),
                            "owner_dispatch_ref": claim.owner_dispatch_ref,
                            "owner_wal_evidence_hash": (claim.owner_wal_evidence_sha256),
                            "claimed_at": claim.claimed_at,
                        },
                    ).fetchone(),
                    size=3,
                    code="submission_claim_failed",
                )
                _uuid(result[0], "dispatch_id")
                if _text(result[1], "persisted_claim_pub_id") != claim.claim_pub_id:
                    raise SubmissionRepositoryError("submission_claim_identity_drift")
                acquired = _boolean(result[2], "claim_acquired")
                persisted = _load_operation_on_connection(
                    connection,
                    self._scope,
                    command.operation,
                )
        return OwnerClaimCasObservation(
            status=(
                OwnerClaimCasStatus.FRESHLY_APPLIED if acquired else OwnerClaimCasStatus.NOT_APPLIED
            ),
            persisted=persisted,
        )

    def claim_reconciliation(
        self,
        *,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
    ) -> DurableReconciliationClaim:
        if operation.send_state is not SendState.SENDING or operation.claim is None:
            raise SubmissionRepositoryError("reconciliation_requires_sending")
        if operation_ref(operation.identity) != work.workflow.operation:
            raise SubmissionRepositoryError("reconciliation_operation_reference_drift")
        params = _operation_params(self._scope, work.workflow.operation)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                row = _require_row(
                    connection.execute(LOAD_DISPATCH_RECONCILIATION_SQL, params).fetchone(),
                    size=11,
                    code="reconciliation_dispatch_missing",
                )
                dispatch_id = _uuid(row[0], "dispatch_id")
                reconciliation_state = _text(row[1], "reconciliation_state")
                owner_state = _text(row[2], "owner_execution_state")
                version = _integer(row[3], "reconciliation_version")
                claim_hash = self._reconciliation_claim_hash(
                    operation=work.workflow.operation,
                    reconciliation_claim_ref=work.reconciliation_claim_ref,
                )
                if reconciliation_state == "not_required" and owner_state == "active":
                    return DurableReconciliationClaim(
                        operation=work.workflow.operation,
                        reconciliation_claim_ref=work.reconciliation_claim_ref,
                        owner_session_terminated=False,
                        acquired=False,
                    )
                if reconciliation_state == "pending" and owner_state == "owner_lost":
                    result = _require_row(
                        connection.execute(
                            CLAIM_RECONCILIATION_SQL,
                            {
                                "tenant_id": self._scope.tenant_id,
                                "project_id": self._scope.project_id,
                                "operation_id": self._operation_id_from_ref(
                                    connection, work.workflow.operation
                                ),
                                "dispatch_id": dispatch_id,
                                "expected_reconciliation_version": version,
                                "reconciliation_claim_ref": (work.reconciliation_claim_ref),
                                "reconciliation_claim_hash": claim_hash,
                            },
                        ).fetchone(),
                        size=1,
                        code="reconciliation_claim_failed",
                    )
                    if _integer(result[0], "reconciliation_version") != version + 1:
                        raise SubmissionRepositoryError("reconciliation_claim_version_drift")
                    return DurableReconciliationClaim(
                        operation=work.workflow.operation,
                        reconciliation_claim_ref=work.reconciliation_claim_ref,
                        owner_session_terminated=True,
                        acquired=True,
                    )
                if reconciliation_state == "in_progress" and owner_state == "owner_lost":
                    if (
                        _optional_text(row[5], "reconciliation_claim_ref")
                        != work.reconciliation_claim_ref
                        or _optional_text(row[6], "reconciliation_claim_hash") != claim_hash
                    ):
                        raise SubmissionRepositoryError("reconciliation_claim_owned_elsewhere")
                    return DurableReconciliationClaim(
                        operation=work.workflow.operation,
                        reconciliation_claim_ref=work.reconciliation_claim_ref,
                        owner_session_terminated=True,
                        acquired=True,
                    )
        raise SubmissionRepositoryError("reconciliation_dispatch_state_invalid")

    def atomic_terminal_and_quota(
        self,
        work: SubmissionWorkItem,
        transition: TerminalSubmissionTransition,
    ) -> SubmissionOperationTruth:
        terminal = transition.operation.terminal
        if terminal is None:
            raise SubmissionRepositoryError("terminal_transition_truth_missing")
        operation = work.workflow.operation
        if operation_ref(transition.operation.identity) != operation:
            raise SubmissionRepositoryError("terminal_operation_reference_drift")
        expected_quota_effect = {
            SendState.CONFIRMED_SENT: QuotaTerminalEffect.SETTLE_CONSUMED,
            SendState.SEND_UNKNOWN: QuotaTerminalEffect.SETTLE_UNKNOWN,
            SendState.CONFIRMED_NOT_SENT: QuotaTerminalEffect.RELEASE,
        }[terminal.send_state]
        if transition.quota_effect is not expected_quota_effect:
            raise SubmissionRepositoryError("terminal_quota_effect_drift")
        expected_payload_hash = sha256(canonical_json(terminal).encode()).hexdigest()
        if (
            transition.outbox.event_type != "collection.submission.terminal"
            or transition.outbox.aggregate_ref != operation.operation_pub_id
            or transition.outbox.aggregate_version != transition.operation.state_version
            or transition.outbox.payload_sha256 != expected_payload_hash
            or transition.outbox.occurred_at != terminal.resolved_at
        ):
            raise SubmissionRepositoryError("terminal_outbox_payload_drift")
        expected_version = transition.operation.state_version - 1
        params = _operation_params(self._scope, operation)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                current = _load_operation_on_connection(connection, self._scope, operation)
                if current.state_version not in {
                    expected_version,
                    transition.operation.state_version,
                }:
                    raise SubmissionRepositoryError("terminal_operation_version_drift")
                grant = _require_row(
                    connection.execute(
                        RESOLVE_TERMINAL_GRANT_SQL,
                        {**params, "grant_pub_id": work.grant_pub_id},
                    ).fetchone(),
                    size=4,
                    code="terminal_execution_grant_missing",
                )
                operation_id = _uuid(grant[0], "operation_id")
                execution_grant_id = _uuid(grant[1], "execution_grant_id")
                grant_fence_hash = sha256(
                    _text(grant[2], "execution_grant_fence_material").encode()
                ).hexdigest()
                grant_lease_refs = _text_array(grant[3], "execution_grant_lease_refs")
                if tuple(sorted(grant_lease_refs)) != tuple(sorted(work.lease_pub_ids)):
                    raise SubmissionRepositoryError("terminal_execution_grant_lease_drift")
                dispatch = connection.execute(
                    LOAD_DISPATCH_RECONCILIATION_SQL,
                    params,
                ).fetchone()
                dispatch_id: UUID | None = None
                owner_gateway_revision: str | None = None
                owner_dispatch_ref: str | None = None
                terminated_fence_hash = terminal.terminated_fence_set_sha256
                reconciliation_claim_ref: str | None = None
                reconciliation_claim_hash: str | None = None
                reconciliation_version: int | None = None
                if dispatch is None:
                    if (
                        transition.operation.claim is not None
                        or terminal.send_state is not SendState.CONFIRMED_NOT_SENT
                        or terminal.reason
                        not in {
                            TerminalReason.PREFLIGHT_NOT_SENT,
                            TerminalReason.UNAVAILABLE,
                            TerminalReason.INVALID_SURFACE_OR_PRODUCT,
                        }
                    ):
                        raise SubmissionRepositoryError("terminal_dispatch_truth_missing")
                    if terminated_fence_hash != grant_fence_hash:
                        raise SubmissionRepositoryError("terminal_terminated_fence_drift")
                else:
                    values = _require_row(
                        dispatch,
                        size=11,
                        code="terminal_dispatch_row_shape_invalid",
                    )
                    if transition.operation.claim is None:
                        raise SubmissionRepositoryError("terminal_dispatch_claim_truth_missing")
                    dispatch_id = _uuid(values[0], "dispatch_id")
                    owner_gateway_revision = _text(values[7], "owner_gateway_revision")
                    owner_dispatch_ref = _text(values[8], "owner_dispatch_ref")
                    if _uuid(values[9], "dispatch_execution_grant_id") != execution_grant_id:
                        raise SubmissionRepositoryError("terminal_dispatch_grant_drift")
                    if _text(values[10], "dispatch_fence_set_hash") != grant_fence_hash:
                        raise SubmissionRepositoryError("terminal_dispatch_fence_drift")
                    if terminal.reason is TerminalReason.POST_CLAIM_NOT_SENT and (
                        terminated_fence_hash != grant_fence_hash
                    ):
                        raise SubmissionRepositoryError("terminal_terminated_fence_drift")
                    reconciliation_state = _text(values[1], "reconciliation_state")
                    owner_execution_state = _text(values[2], "owner_execution_state")
                    persisted_reconciliation_version = _integer(values[3], "reconciliation_version")
                    persisted_claim_ref = _optional_text(values[5], "reconciliation_claim_ref")
                    persisted_claim_hash = _optional_text(values[6], "reconciliation_claim_hash")
                    if reconciliation_state == "not_required":
                        if (
                            owner_execution_state != "active"
                            or persisted_reconciliation_version != 1
                            or persisted_claim_ref is not None
                            or persisted_claim_hash is not None
                        ):
                            raise SubmissionRepositoryError(
                                "owner_terminal_reconciliation_shape_invalid"
                            )
                    elif reconciliation_state == "in_progress":
                        if (
                            owner_execution_state != "owner_lost"
                            or persisted_reconciliation_version < 3
                            or persisted_claim_ref != work.reconciliation_claim_ref
                            or persisted_claim_hash is None
                            or persisted_claim_hash
                            != self._reconciliation_claim_hash(
                                operation=operation,
                                reconciliation_claim_ref=persisted_claim_ref,
                            )
                        ):
                            raise SubmissionRepositoryError("terminal_reconciliation_claim_drift")
                        reconciliation_claim_ref = persisted_claim_ref
                        reconciliation_claim_hash = persisted_claim_hash
                        reconciliation_version = persisted_reconciliation_version
                    elif reconciliation_state == "resolved":
                        if owner_execution_state != "resolved":
                            raise SubmissionRepositoryError(
                                "terminal_reconciliation_resolved_shape_invalid"
                            )
                        if persisted_claim_ref is None:
                            if (
                                persisted_claim_hash is not None
                                or persisted_reconciliation_version != 2
                            ):
                                raise SubmissionRepositoryError(
                                    "owner_terminal_replay_shape_invalid"
                                )
                        else:
                            if (
                                persisted_reconciliation_version < 4
                                or persisted_claim_ref != work.reconciliation_claim_ref
                                or persisted_claim_hash is None
                                or persisted_claim_hash
                                != self._reconciliation_claim_hash(
                                    operation=operation,
                                    reconciliation_claim_ref=persisted_claim_ref,
                                )
                            ):
                                raise SubmissionRepositoryError(
                                    "terminal_reconciliation_replay_drift"
                                )
                            reconciliation_claim_ref = persisted_claim_ref
                            reconciliation_claim_hash = persisted_claim_hash
                            reconciliation_version = persisted_reconciliation_version - 1
                    else:
                        raise SubmissionRepositoryError(
                            "terminal_dispatch_reconciliation_state_invalid"
                        )
                transition_key = self._opaque_hash_ref(
                    "terminal",
                    canonical_json(terminal),
                )
                result = _require_row(
                    connection.execute(
                        FINALIZE_SUBMISSION_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": operation_id,
                            "dispatch_id": dispatch_id,
                            "execution_grant_id": execution_grant_id,
                            "expected_send_state_version": expected_version,
                            "target_send_state": terminal.send_state.value,
                            "terminal_reason": terminal.reason.value,
                            "transition_key": transition_key,
                            "owner_gateway_revision": owner_gateway_revision,
                            "owner_dispatch_ref": owner_dispatch_ref,
                            "evidence_ref": terminal.evidence_ref,
                            "evidence_hash": terminal.evidence_sha256,
                            "non_submission_proof_ref": (terminal.non_submission_proof_ref),
                            "provider_submission_ref": (terminal.provider_submission_ref),
                            "terminated_fence_set_hash": terminated_fence_hash,
                            "reason_code": terminal.reason.value,
                            "resolved_at": terminal.resolved_at,
                            "terminal_payload_sha256": (transition.outbox.payload_sha256),
                            "reconciliation_claim_ref": reconciliation_claim_ref,
                            "reconciliation_claim_hash": reconciliation_claim_hash,
                            "expected_reconciliation_version": reconciliation_version,
                        },
                    ).fetchone(),
                    size=4,
                    code="terminal_finalize_failed",
                )
                if _integer(result[0], "send_state_version") != (
                    transition.operation.state_version
                ):
                    raise SubmissionRepositoryError("terminal_send_state_version_drift")
                _uuid(result[1], "transition_evidence_id")
                _uuid(result[2], "terminal_outbox_id")
                _boolean(result[3], "terminal_finalized")
                persisted = _load_operation_on_connection(connection, self._scope, operation)
                if persisted != transition.operation:
                    raise SubmissionRepositoryError("terminal_persisted_truth_drift")
                _validate_exact_outbox(connection, self._scope, transition.outbox)
        return persisted

    def atomic_fact_and_outbox(
        self,
        work: SubmissionWorkItem,
        fact: SlotOutcomeFact,
        outbox: OutboxEventRef,
    ) -> SlotOutcomeFact:
        if fact.operation != work.workflow.operation:
            raise SubmissionRepositoryError("fact_operation_reference_drift")
        if fact.analysis_state_version is not None:
            raise SubmissionRepositoryError("stage3_analysis_fact_is_not_supported")
        expected_payload = sha256(canonical_json(fact).encode()).hexdigest()
        if outbox.payload_sha256 != expected_payload:
            raise SubmissionRepositoryError("fact_outbox_payload_drift")
        params = _operation_params(self._scope, fact.operation)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                basis = _require_row(
                    connection.execute(RESOLVE_FACT_BASIS_SQL, params).fetchone(),
                    size=4,
                    code="fact_basis_missing",
                )
                operation_id = _uuid(basis[0], "operation_id")
                manifest_id = None if basis[1] is None else _uuid(basis[1], "capture_manifest_id")
                capture_version = _optional_integer(basis[2], "capture_state_version")
                if (fact.capture_state_version is None) != (manifest_id is None) or (
                    fact.capture_state_version is not None
                    and fact.capture_state_version != capture_version
                ):
                    raise SubmissionRepositoryError("fact_capture_basis_drift")
                prior_version = _optional_integer(basis[3], "prior_fact_version") or 0
                if prior_version not in {fact.fact_version - 1, fact.fact_version}:
                    raise SubmissionRepositoryError("fact_prior_version_drift")
                outcome_key = self._opaque_hash_ref(
                    "outcome",
                    canonical_json(fact),
                )
                result = _require_row(
                    connection.execute(
                        RECORD_SLOT_OUTCOME_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": operation_id,
                            "expected_operation_state_version": (fact.operation_state_version),
                            "expected_prior_fact_version": fact.fact_version - 1,
                            "capture_manifest_id": (
                                manifest_id if fact.capture_state_version is not None else None
                            ),
                            "capture_state_version": fact.capture_state_version,
                            "analysis_state_version": None,
                            "capture_link_key": fact.capture_link_key,
                            "outcome_key": outcome_key,
                            "outcome_state": fact.outcome.value,
                            "is_final_primary": fact.is_final_primary,
                            "outcome_payload_sha256": expected_payload,
                            "reason_code": fact.outcome.value,
                            "recorded_at": fact.recorded_at,
                        },
                    ).fetchone(),
                    size=4,
                    code="slot_outcome_record_failed",
                )
                _uuid(result[0], "slot_outcome_id")
                if _integer(result[1], "fact_version") != fact.fact_version:
                    raise SubmissionRepositoryError("slot_outcome_version_drift")
                _uuid(result[2], "slot_outcome_outbox_id")
                _boolean(result[3], "slot_outcome_recorded")
                persisted = self._load_fact_on_connection(connection, fact.operation)
                if persisted != fact:
                    raise SubmissionRepositoryError("slot_outcome_persisted_truth_drift")
                _validate_exact_outbox(connection, self._scope, outbox)
        return persisted

    def store_capture_link(self, link: ImmutableCaptureLink) -> ImmutableCaptureLink:
        params = _operation_params(self._scope, link.operation)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                basis = _require_row(
                    connection.execute(
                        RESOLVE_CAPTURE_LINK_SQL,
                        {
                            **params,
                            "staging_key": link.staging_key,
                            "content_sha256": link.content_sha256,
                            "capture_state_version": link.capture_state_version,
                        },
                    ).fetchone(),
                    size=4,
                    code="capture_link_basis_missing",
                )
                result = _require_row(
                    connection.execute(
                        LINK_CAPTURE_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": _uuid(basis[0], "operation_id"),
                            "dispatch_id": _uuid(basis[1], "dispatch_id"),
                            "capture_manifest_id": _uuid(basis[2], "capture_manifest_id"),
                            "expected_capture_state_version": _integer(
                                basis[3], "capture_state_version"
                            ),
                            "capture_link_key": link.capture_link_key,
                            "analysis_contract_revision": (self._analysis_contract_revision),
                            "linked_at": link.linked_at,
                        },
                    ).fetchone(),
                    size=3,
                    code="capture_link_failed",
                )
                _uuid(result[0], "observation_id")
                analysis_admission_id = (
                    None if result[1] is None else _uuid(result[1], "analysis_admission_id")
                )
                if (self._analysis_contract_revision is None) != (analysis_admission_id is None):
                    raise SubmissionRepositoryError("capture_link_analysis_admission_shape_drift")
                _boolean(result[2], "capture_linked")
                persisted = self._load_capture_link_on_connection(connection, link.operation)
                if persisted != link:
                    raise SubmissionRepositoryError("capture_link_persisted_truth_drift")
        return persisted

    def load_capture(self, operation: OperationRef) -> CaptureTruth | None:
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                record = self._load_capture_record_on_connection(connection, operation)
        return None if record is None else record[0]

    def resolve_capture_admission(
        self,
        *,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
        capture: CaptureTruth,
    ) -> DurableCaptureAdmission:
        """Classify persisted capture authority without reviving an old fence."""

        ref = operation_ref(operation.identity)
        if (
            ref != work.workflow.operation
            or capture.operation != ref
            or operation.send_state not in {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}
            or operation.claim is None
            or capture.capture_state not in {CaptureState.NOT_STARTED, CaptureState.CAPTURING}
        ):
            raise SubmissionRepositoryError("capture_admission_input_shape_invalid")
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                persisted_operation = _load_operation_on_connection(
                    connection,
                    self._scope,
                    ref,
                )
                if persisted_operation != operation:
                    raise SubmissionRepositoryError("capture_admission_operation_drift")
                persisted_capture = self._load_capture_record_on_connection(connection, ref)
                if persisted_capture is None or persisted_capture[0] != capture:
                    raise SubmissionRepositoryError("capture_admission_capture_drift")
                row = _require_row(
                    connection.execute(
                        RESOLVE_CAPTURE_ADMISSION_SQL,
                        {
                            **_operation_params(self._scope, ref),
                            "grant_pub_id": work.grant_pub_id,
                        },
                    ).fetchone(),
                    size=20,
                    code="capture_admission_basis_missing",
                )

        if (
            _integer(row[0], "capture_admission_operation_version") != operation.state_version
            or SendState(_text(row[1], "capture_admission_send_state")) is not operation.send_state
            or _integer(row[2], "capture_admission_capture_version") != capture.state_version
            or CaptureState(_text(row[3], "capture_admission_capture_state"))
            is not capture.capture_state
        ):
            raise SubmissionRepositoryError("capture_admission_truth_drift")
        if (
            _text(row[4], "capture_admission_reconciliation_state") != "resolved"
            or _text(row[5], "capture_admission_owner_execution_state") != "resolved"
        ):
            raise SubmissionRepositoryError("capture_admission_dispatch_not_resolved")
        reconciliation_version = _integer(row[6], "capture_admission_reconciliation_version")
        reconciliation_claim_ref = _optional_text(
            row[7], "capture_admission_reconciliation_claim_ref"
        )
        reconciliation_claim_hash = _optional_text(
            row[8], "capture_admission_reconciliation_claim_hash"
        )
        grant_pub_id = _text(row[9], "capture_admission_grant_pub_id")
        _text(row[10], "capture_admission_grant_state")
        _aware_datetime(row[11], "capture_admission_grant_expires_at")
        if row[12] is not None:
            _aware_datetime(row[12], "capture_admission_grant_revoked_at")
        owner_handle = _text(row[13], "capture_admission_owner_handle")
        fence_hash = _text(row[14], "capture_admission_fence_hash")
        calculated_fence_hash = sha256(
            _text(row[15], "capture_admission_fence_material").encode()
        ).hexdigest()
        lease_refs = _text_array(row[16], "capture_admission_lease_refs")
        resource_count = _integer(row[17], "capture_admission_resource_count")
        live_count = _integer(row[18], "capture_admission_live_count")
        terminated_count = _integer(row[19], "capture_admission_terminated_count")
        if (
            grant_pub_id != work.grant_pub_id
            or operation.claim.grant_pub_id != work.grant_pub_id
            or owner_handle != operation.claim.owner_handle
            or owner_handle != capture.owner_handle
            or fence_hash != operation.claim.fence_set_sha256
            or fence_hash != capture.fence_set_sha256
            or calculated_fence_hash != fence_hash
            or resource_count < 1
            or resource_count != len(work.lease_pub_ids)
            or tuple(sorted(lease_refs)) != tuple(sorted(work.lease_pub_ids))
            or live_count < 0
            or terminated_count < 0
            or live_count > resource_count
            or terminated_count > resource_count
        ):
            raise SubmissionRepositoryError("capture_admission_authority_shape_drift")

        if (reconciliation_claim_ref is None) != (reconciliation_claim_hash is None):
            raise SubmissionRepositoryError("capture_admission_reconciliation_marker_mixed")
        if reconciliation_claim_ref is not None:
            if (
                reconciliation_version < 4
                or reconciliation_claim_ref != work.reconciliation_claim_ref
                or reconciliation_claim_hash
                != self._reconciliation_claim_hash(
                    operation=ref,
                    reconciliation_claim_ref=reconciliation_claim_ref,
                )
                or live_count != 0
                or terminated_count != resource_count
            ):
                raise SubmissionRepositoryError("capture_admission_reconciled_shape_drift")
            decision = CaptureAdmissionDecision.RECONCILED_NO_AUTHORITY
        elif reconciliation_version != 2:
            raise SubmissionRepositoryError("capture_admission_direct_version_drift")
        elif live_count == resource_count:
            decision = CaptureAdmissionDecision.DIRECT_OWNER_LIVE
        else:
            decision = CaptureAdmissionDecision.NO_LIVE_AUTHORITY
        return DurableCaptureAdmission(
            operation=ref,
            operation_state_version=operation.state_version,
            capture_state_version=capture.state_version,
            decision=decision,
            reconciliation_claim_ref=reconciliation_claim_ref,
        )

    def store_capture(
        self,
        *,
        expected_state_version: int | None,
        capture: CaptureTruth,
    ) -> CaptureTruth:
        persisted = self.load_capture(capture.operation)
        if persisted is None:
            raise SubmissionRepositoryError("capture_truth_missing_after_prepare")
        if expected_state_version is not None and persisted.state_version != expected_state_version:
            raise SubmissionRepositoryError("capture_store_version_drift")
        if persisted != capture:
            raise SubmissionRepositoryError("capture_store_exact_truth_drift")
        return persisted

    def start_or_resume_capture_attempt(
        self,
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        capture: CaptureTruth,
        requested_at: datetime,
    ) -> DurableCaptureAttempt:
        self._validate_capture_context(work=work, context=context, capture=capture)
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                current_record = self._load_capture_record_on_connection(
                    connection,
                    capture.operation,
                )
                if current_record is None or current_record[0] != capture:
                    raise SubmissionRepositoryError("capture_attempt_source_truth_drift")
                if capture.capture_state is CaptureState.CAPTURING:
                    command = self._active_capture_command(
                        capture=capture,
                        active_command_json=current_record[1],
                    )
                    self._validate_capture_command_context(
                        work=work,
                        context=context,
                        capture=capture,
                        command=command,
                    )
                    return DurableCaptureAttempt(
                        capture=capture,
                        command=command,
                        freshly_started=False,
                    )

                command = CaptureExistingCommand(
                    operation=capture.operation,
                    source_send_state=capture.source_send_state,
                    expected_capture_version=capture.state_version,
                    attempt_ref=work.capture_attempt_ref,
                    staging_intent=deterministic_capture_staging_intent(
                        operation=capture.operation,
                        attempt_ref=work.capture_attempt_ref,
                    ),
                    capture_policy_revision=context.capture_policy_revision,
                    requested_surface_product=capture.expected_surface_product,
                    authority=context.authority,
                    authority_sha256=authority_digest(context.authority),
                    requested_at=requested_at,
                )
                expected = begin_capture(capture, command)
                operation_id, dispatch_id = self._resolve_capture_basis(
                    connection,
                    command=command,
                    capture=capture,
                )
                request_hash = capture_command_digest(command)
                result = _require_row(
                    connection.execute(
                        BEGIN_CAPTURE_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": operation_id,
                            "dispatch_id": dispatch_id,
                            "expected_capture_state_version": capture.state_version,
                            "fence_set_hash": capture.fence_set_sha256,
                            "authority_sha256": command.authority_sha256,
                            "owner_handle": capture.owner_handle,
                            "capture_attempt_ref": command.attempt_ref,
                            "capture_policy_revision": command.capture_policy_revision,
                            "capture_request_sha256": request_hash,
                            "capture_command_json": canonical_json(command),
                            "requested_at": command.requested_at,
                        },
                    ).fetchone(),
                    size=3,
                    code="capture_attempt_begin_failed",
                )
                if _integer(result[0], "capture_state_version") != expected.state_version:
                    raise SubmissionRepositoryError("capture_attempt_version_drift")
                if _integer(result[1], "capture_attempt_ordinal") < 1:
                    raise SubmissionRepositoryError("capture_attempt_ordinal_invalid")
                acquired = _boolean(result[2], "capture_attempt_acquired")
                persisted_record = self._load_capture_record_on_connection(
                    connection,
                    capture.operation,
                )
                if persisted_record is None or persisted_record[0] != expected:
                    raise SubmissionRepositoryError("capture_attempt_persisted_truth_drift")
                persisted_command = self._active_capture_command(
                    capture=expected,
                    active_command_json=persisted_record[1],
                )
                if persisted_command != command:
                    raise SubmissionRepositoryError("capture_attempt_persisted_command_drift")
        return DurableCaptureAttempt(
            capture=expected,
            command=command,
            freshly_started=acquired,
        )

    def resolve_capture_attempt(
        self,
        *,
        attempt: DurableCaptureAttempt,
        raw: CaptureDisposition,
        normalized: CaptureDisposition,
    ) -> CaptureTruth:
        if normalize_capture(attempt.command, raw) != normalized:
            raise SubmissionRepositoryError("capture_normalization_drift")
        expected = apply_capture_disposition(attempt.capture, normalized)
        staging = raw.staging
        staged_at = staging.staged_at if staging is not None else raw.observed_at
        capture_material = canonical_json(
            {
                "command_sha256": capture_command_digest(attempt.command),
                "normalized": normalized,
                "raw": raw,
                "version": "collection-capture-manifest-identity-v1",
            }
        )
        capture_key = attempt.command.staging_intent.staging_key
        if staging is not None and (
            staging.staging_key != capture_key
            or staging.object_ref != attempt.command.staging_intent.object_ref
        ):
            raise SubmissionRepositoryError("capture_staging_intent_drift")
        manifest_hash = sha256(capture_material.encode()).hexdigest()
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                current_record = self._load_capture_record_on_connection(
                    connection,
                    attempt.capture.operation,
                )
                if current_record is None or current_record[0] != attempt.capture:
                    raise SubmissionRepositoryError("capture_resolution_source_truth_drift")
                persisted_command = self._active_capture_command(
                    capture=attempt.capture,
                    active_command_json=current_record[1],
                )
                if persisted_command != attempt.command:
                    raise SubmissionRepositoryError("capture_resolution_command_drift")
                operation_id, dispatch_id = self._resolve_capture_basis(
                    connection,
                    command=attempt.command,
                    capture=attempt.capture,
                )
                result = _require_row(
                    connection.execute(
                        STAGE_CAPTURE_SQL,
                        {
                            "tenant_id": self._scope.tenant_id,
                            "project_id": self._scope.project_id,
                            "operation_id": operation_id,
                            "dispatch_id": dispatch_id,
                            "expected_capture_state_version": attempt.capture.state_version,
                            "fence_set_hash": attempt.capture.fence_set_sha256,
                            "owner_handle": attempt.capture.owner_handle,
                            "capture_attempt_ref": attempt.command.attempt_ref,
                            "capture_request_sha256": capture_command_digest(attempt.command),
                            "capture_key": capture_key,
                            "capture_state": raw.capture_state.value,
                            "capture_channel": raw.provenance.capture_channel.value,
                            "capture_protocol_revision": (raw.provenance.capture_protocol_revision),
                            "content_object_ref": (
                                staging.object_ref if staging is not None else None
                            ),
                            "content_hash": (
                                staging.content_sha256 if staging is not None else None
                            ),
                            "content_size_bytes": (
                                staging.byte_size if staging is not None else None
                            ),
                            "mime_type": staging.media_type if staging is not None else None,
                            "capture_schema_revision": (
                                staging.capture_schema_revision if staging is not None else None
                            ),
                            "capture_manifest_hash": manifest_hash,
                            "capture_evidence_ref": raw.evidence_ref,
                            "capture_evidence_hash": raw.evidence_sha256,
                            "observed_platform": raw.observed_surface_product.platform,
                            "observed_surface": (
                                raw.observed_surface_product.collection_surface.value
                            ),
                            "observed_product_variant": (
                                raw.observed_surface_product.product_variant
                            ),
                            "observed_product_version": (raw.provenance.observed_product_version),
                            "capture_adapter_revision": (raw.provenance.capture_adapter_revision),
                            "data_classification": raw.provenance.data_classification.value,
                            "dlp_policy_revision": raw.provenance.dlp_policy_revision,
                            "reason_code": (
                                "invalid_surface_or_product"
                                if normalized.normalization
                                is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
                                else normalized.capture_state.value
                            ),
                            "captured_at": raw.observed_at,
                            "staged_at": staged_at,
                            "retention_until": raw.provenance.retention_until,
                        },
                    ).fetchone(),
                    size=1,
                    code="capture_manifest_stage_failed",
                )
                _uuid(result[0], "capture_manifest_id")
                persisted_record = self._load_capture_record_on_connection(
                    connection,
                    attempt.capture.operation,
                )
                if persisted_record is None or persisted_record[0] != expected:
                    raise SubmissionRepositoryError("capture_resolution_persisted_truth_drift")
        return expected

    def load_capture_link(self, operation: OperationRef) -> ImmutableCaptureLink | None:
        row = self._one_read(LOAD_CAPTURE_LINK_SQL, _operation_params(self._scope, operation))
        if row is None:
            return None
        values = _require_row(row, size=12, code="capture_link_row_shape_invalid")
        return ImmutableCaptureLink(
            capture_link_key=_text(values[0], "capture_link_key"),
            operation=operation,
            staging_key=_text(values[1], "staging_key"),
            content_sha256=_text(values[2], "capture_content_sha256"),
            requested_surface_product=_surface(
                platform=values[3],
                collection_surface=values[4],
                product_variant=values[5],
                target_key=values[6],
            ),
            observed_surface_product=_surface(
                platform=values[7],
                collection_surface=values[8],
                product_variant=values[9],
                target_key=(
                    "collection-target-v1|platform="
                    f"{_text(values[7], 'observed_platform')}|collection_surface="
                    f"{_text(values[8], 'observed_surface')}|product_variant="
                    f"{_text(values[9], 'observed_product_variant')}"
                ),
            ),
            capture_state_version=_integer(values[10], "capture_state_version"),
            linked_at=_aware_datetime(values[11], "capture_linked_at"),
        )

    def load_fact(self, operation: OperationRef) -> SlotOutcomeFact | None:
        row = self._one_read(LOAD_FACT_SQL, _operation_params(self._scope, operation))
        if row is None:
            return None
        values = _require_row(row, size=9, code="slot_outcome_row_shape_invalid")
        fact = SlotOutcomeFact(
            operation=operation,
            outcome=SlotOutcome(_text(values[0], "slot_outcome_state")),
            operation_state_version=_integer(values[1], "operation_state_version"),
            capture_state_version=_optional_integer(values[2], "capture_state_version"),
            analysis_state_version=_optional_integer(values[3], "analysis_state_version"),
            capture_link_key=_optional_text(values[4], "capture_link_key"),
            is_final_primary=_boolean(values[5], "is_final_primary"),
            fact_version=_integer(values[6], "fact_version"),
            recorded_at=_aware_datetime(values[7], "fact_recorded_at"),
        )
        expected_hash = sha256(canonical_json(fact).encode()).hexdigest()
        if expected_hash != _text(values[8], "outcome_hash"):
            raise SubmissionRepositoryError("slot_outcome_payload_hash_drift")
        return fact

    def pending_outbox(self, operation: OperationRef) -> tuple[OutboxEventRef, ...]:
        rows = self._many_read(LOAD_PENDING_OUTBOX_SQL, _operation_params(self._scope, operation))
        events: list[OutboxEventRef] = []
        for row in rows:
            values = _require_row(row, size=6, code="outbox_row_shape_invalid")
            events.append(
                OutboxEventRef(
                    outbox_key=_text(values[0], "outbox_event_key"),
                    event_type=_text(values[1], "outbox_event_type"),
                    aggregate_ref=_text(values[2], "outbox_aggregate_ref"),
                    aggregate_version=_integer(values[3], "outbox_aggregate_version"),
                    payload_sha256=_text(values[4], "outbox_payload_hash"),
                    occurred_at=_aware_datetime(values[5], "outbox_occurred_at"),
                )
            )
        return tuple(events)

    def mark_outbox_published(self, outbox_key: str) -> None:
        """CAS a published marker after external publish has returned.

        The event-bus call is intentionally absent.  A crash before this method
        leaves the same deterministic event pending for idempotent redelivery.
        """

        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                row = connection.execute(
                    LOAD_OUTBOX_FOR_MARK_SQL,
                    {
                        "tenant_id": self._scope.tenant_id,
                        "project_id": self._scope.project_id,
                        "event_key": outbox_key,
                    },
                ).fetchone()
                values = _require_row(row, size=3, code="outbox_mark_target_missing")
                state = _text(values[2], "outbox_publish_state")
                if state == "published":
                    return
                if state != "pending":
                    raise SubmissionRepositoryError("outbox_mark_target_not_pending")
                changed = connection.execute(
                    ADVANCE_OUTBOX_SQL,
                    {
                        "tenant_id": self._scope.tenant_id,
                        "project_id": self._scope.project_id,
                        "outbox_id": _uuid(values[0], "outbox_id"),
                        "expected_version": _integer(values[1], "outbox_version"),
                    },
                ).fetchone()
                _require_row(changed, size=1, code="outbox_publish_cas_failed")

    def quota_snapshot(self, reservation_pub_id: str) -> QuotaConservationSnapshot:
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                return _quota_snapshot_on_connection(
                    connection,
                    self._scope,
                    reservation_pub_id,
                )

    @staticmethod
    def _validate_capture_context(
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        capture: CaptureTruth,
    ) -> None:
        authority = context.authority
        if (
            work.workflow.operation != capture.operation
            or operation_ref(context.prepare.identity) != capture.operation
            or context.prepare.identity.surface_product != capture.expected_surface_product
        ):
            raise SubmissionRepositoryError("capture_context_operation_drift")
        if work.grant_pub_id != authority.grant_pub_id or tuple(
            sorted(work.lease_pub_ids)
        ) != tuple(sorted(fence.lease_pub_id for fence in authority.lease_fences)):
            raise SubmissionRepositoryError("capture_context_authority_reference_drift")
        if (
            capture.owner_handle != authority.owner_handle
            or capture.fence_set_sha256 != authority.fence_set_sha256
        ):
            raise SubmissionRepositoryError("capture_context_owner_fence_drift")

    @staticmethod
    def _validate_capture_command_context(
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        capture: CaptureTruth,
        command: CaptureExistingCommand,
    ) -> None:
        PostgresSubmissionRepository._validate_capture_context(
            work=work,
            context=context,
            capture=capture,
        )
        if (
            command.operation != capture.operation
            or command.source_send_state is not capture.source_send_state
            or command.expected_capture_version + 1 != capture.state_version
            or command.attempt_ref != work.capture_attempt_ref
            or command.capture_policy_revision != context.capture_policy_revision
            or command.requested_surface_product != capture.expected_surface_product
            or command.authority != context.authority
            or command.authority_sha256 != authority_digest(context.authority)
        ):
            raise SubmissionRepositoryError("capture_active_command_context_drift")

    def _resolve_capture_basis(
        self,
        connection: ConnectionProtocol,
        *,
        command: CaptureExistingCommand,
        capture: CaptureTruth,
    ) -> tuple[UUID, UUID]:
        row = _require_row(
            connection.execute(
                RESOLVE_CAPTURE_ATTEMPT_SQL,
                {
                    **_operation_params(self._scope, capture.operation),
                    "grant_pub_id": command.authority.grant_pub_id,
                },
            ).fetchone(),
            size=7,
            code="capture_dispatch_basis_missing",
        )
        if (
            _text(row[2], "capture_dispatch_owner") != capture.owner_handle
            or _text(row[3], "capture_dispatch_fence_hash") != capture.fence_set_sha256
            or _text(row[4], "capture_dispatch_authority_hash") != command.authority_sha256
            or _text(row[5], "capture_dispatch_authority_snapshot")
            != canonical_json(command.authority)
            or _text(row[6], "capture_execution_grant_pub_id") != command.authority.grant_pub_id
        ):
            raise SubmissionRepositoryError("capture_dispatch_authority_drift")
        return _uuid(row[0], "operation_id"), _uuid(row[1], "dispatch_id")

    def _load_capture_record_on_connection(
        self,
        connection: ConnectionProtocol,
        operation: OperationRef,
    ) -> tuple[CaptureTruth, str | None] | None:
        row = connection.execute(
            LOAD_CAPTURE_SQL,
            _operation_params(self._scope, operation),
        ).fetchone()
        return None if row is None else _capture_record_from_row(operation, row)

    @staticmethod
    def _active_capture_command(
        *,
        capture: CaptureTruth,
        active_command_json: str | None,
    ) -> CaptureExistingCommand:
        if capture.capture_state is not CaptureState.CAPTURING or active_command_json is None:
            raise SubmissionRepositoryError("capture_active_command_missing")
        try:
            command = CaptureExistingCommand.model_validate_json(active_command_json)
        except ValueError as exc:
            raise SubmissionRepositoryError("capture_active_command_invalid") from exc
        if canonical_json(command) != active_command_json:
            raise SubmissionRepositoryError("capture_active_command_not_canonical")
        if (
            command.operation != capture.operation
            or command.attempt_ref != capture.active_attempt_ref
            or capture_command_digest(command) != capture.active_request_sha256
        ):
            raise SubmissionRepositoryError("capture_active_command_truth_drift")
        return command

    def _operation_id_from_ref(
        self,
        connection: ConnectionProtocol,
        operation: OperationRef,
    ) -> UUID:
        row = _require_row(
            connection.execute(
                """
                SELECT id
                FROM platform.collection_submission_operation
                WHERE tenant_id = %(tenant_id)s
                  AND project_id = %(project_id)s
                  AND pub_id = %(operation_pub_id)s
                  AND operation_key = %(operation_key)s
                  AND operation_generation = %(operation_generation)s
                """,
                _operation_params(self._scope, operation),
            ).fetchone(),
            size=1,
            code="operation_internal_id_missing",
        )
        return _uuid(row[0], "operation_id")

    def _load_fact_on_connection(
        self,
        connection: ConnectionProtocol,
        operation: OperationRef,
    ) -> SlotOutcomeFact:
        row = _require_row(
            connection.execute(
                LOAD_FACT_SQL,
                _operation_params(self._scope, operation),
            ).fetchone(),
            size=9,
            code="slot_outcome_reload_failed",
        )
        fact = SlotOutcomeFact(
            operation=operation,
            outcome=SlotOutcome(_text(row[0], "slot_outcome_state")),
            operation_state_version=_integer(row[1], "operation_state_version"),
            capture_state_version=_optional_integer(row[2], "capture_state_version"),
            analysis_state_version=_optional_integer(row[3], "analysis_state_version"),
            capture_link_key=_optional_text(row[4], "capture_link_key"),
            is_final_primary=_boolean(row[5], "is_final_primary"),
            fact_version=_integer(row[6], "fact_version"),
            recorded_at=_aware_datetime(row[7], "fact_recorded_at"),
        )
        if sha256(canonical_json(fact).encode()).hexdigest() != _text(row[8], "outcome_hash"):
            raise SubmissionRepositoryError("slot_outcome_payload_hash_drift")
        return fact

    def _load_capture_link_on_connection(
        self,
        connection: ConnectionProtocol,
        operation: OperationRef,
    ) -> ImmutableCaptureLink:
        row = _require_row(
            connection.execute(
                LOAD_CAPTURE_LINK_SQL,
                _operation_params(self._scope, operation),
            ).fetchone(),
            size=12,
            code="capture_link_reload_failed",
        )
        return self._capture_link_from_row(operation, row)

    @staticmethod
    def _opaque_hash_ref(prefix: str, material: str) -> str:
        return f"{prefix}-{sha256(material.encode()).hexdigest()}"

    @staticmethod
    def _reconciliation_claim_hash(
        *,
        operation: OperationRef,
        reconciliation_claim_ref: str,
    ) -> str:
        return sha256(
            canonical_json(
                {
                    "operation": operation.model_dump(mode="json"),
                    "reconciliation_claim_ref": reconciliation_claim_ref,
                    "version": "collection-reconciliation-claim-v1",
                }
            ).encode()
        ).hexdigest()

    @staticmethod
    def _capture_link_from_row(
        operation: OperationRef,
        row: Sequence[object],
    ) -> ImmutableCaptureLink:
        values = _require_row(row, size=12, code="capture_link_row_shape_invalid")
        return ImmutableCaptureLink(
            capture_link_key=_text(values[0], "capture_link_key"),
            operation=operation,
            staging_key=_text(values[1], "staging_key"),
            content_sha256=_text(values[2], "capture_content_sha256"),
            requested_surface_product=_surface(
                platform=values[3],
                collection_surface=values[4],
                product_variant=values[5],
                target_key=values[6],
            ),
            observed_surface_product=_surface(
                platform=values[7],
                collection_surface=values[8],
                product_variant=values[9],
                target_key=(
                    "collection-target-v1|platform="
                    f"{_text(values[7], 'observed_platform')}|collection_surface="
                    f"{_text(values[8], 'observed_surface')}|product_variant="
                    f"{_text(values[9], 'observed_product_variant')}"
                ),
            ),
            capture_state_version=_integer(values[10], "capture_state_version"),
            linked_at=_aware_datetime(values[11], "capture_linked_at"),
        )

    def _one_read(
        self,
        query: str,
        params: Mapping[str, object],
    ) -> Sequence[object] | None:
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                return connection.execute(query, params).fetchone()

    def _many_read(
        self,
        query: str,
        params: Mapping[str, object],
    ) -> Sequence[Sequence[object]]:
        with self._connection_factory() as connection:
            with connection.transaction():
                _set_tenant(connection, self._scope)
                return connection.execute(query, params).fetchall()

    @staticmethod
    def _unsupported(code: str) -> Never:
        raise SubmissionRepositoryError(code)

    # These methods make the fail-closed boundary obvious to callers while the
    # migration/coordinator signatures are being aligned.  There is no direct
    # DML fallback hidden behind them.
    def load_analysis(self, operation: OperationRef) -> AnalysisTruth | None:
        del operation
        self._unsupported("durable_analysis_execution_not_implemented")

    def store_analysis(
        self,
        *,
        expected_state_version: int | None,
        analysis: AnalysisTruth,
    ) -> AnalysisTruth:
        del expected_state_version, analysis
        self._unsupported("durable_analysis_execution_not_implemented")

    def queue_or_resume_analysis_attempt(
        self,
        *,
        analysis: AnalysisTruth,
        command: AnalysisCommand,
    ) -> DurableAnalysisAttempt:
        del analysis, command
        self._unsupported("durable_analysis_execution_not_implemented")

    def load_active_analysis_attempt(
        self,
        operation: OperationRef,
    ) -> DurableAnalysisAttempt | None:
        del operation
        self._unsupported("durable_analysis_execution_not_implemented")


def psycopg_connection_factory(dsn: str) -> RepositoryConnectionFactory:
    """Build a production connection factory without opening a connection."""

    normalized = dsn.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect() -> RepositoryConnection:
        import psycopg

        return psycopg.connect(normalized)  # type: ignore[return-value]

    return connect


__all__ = [
    "PostgresSubmissionRepository",
    "PreparationContextLoader",
    "QuotaReservationBlocked",
    "QuotaReserver",
    "RepositoryConnection",
    "RepositoryConnectionFactory",
    "RepositoryScope",
    "RestrictedFunctionContract",
    "S10_FUNCTION_CONTRACTS",
    "SubmissionContextLoader",
    "SubmissionRepositoryError",
    "psycopg_connection_factory",
]
