"""Read-only owner-WAL reconciliation for collection-v2 submissions.

This adapter is deliberately bound to one repository-issued, exclusive
dead-owner admission.  It can inspect the immutable owner send journal and
produce reconciliation evidence, but it has no submit or journal-write port.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal, NoReturn, Protocol, Self, cast
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from domain.collection.submission import (
    FrozenProtocolModel,
    OpaqueId,
    OperationRef,
    OwnerClaimTruth,
    ReconciliationDisposition,
    SendingReconciliationCommand,
    Sha256Hex,
    SubmissionOperationTruth,
    TerminalReason,
    canonical_json,
    operation_ref,
)
from domain.collection.surface import SendState

from .resource_owner_gateway_v2 import SubmissionOwnerSendJournalSnapshot
from .submission_repository_v2 import (
    RepositoryConnection,
    RepositoryConnectionFactory,
    RepositoryScope,
)
from .submission_v2 import (
    Clock,
    DurableReconciliationClaim,
    ReconciliationEvidence,
    ReconciliationGateway,
)


class OwnerReconciliationError(RuntimeError):
    """Stable fail-closed error raised by the reconciliation-only adapter."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise OwnerReconciliationError(code)


class SubmissionOwnerSendJournalReader(Protocol):
    """Narrow view of ``SubmissionOwnerSendJournalStore`` with no write methods."""

    def load_send_journal(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerSendJournalSnapshot: ...


class DeadOwnerReconciliationAdmission(FrozenProtocolModel):
    """Exact capability granted after the repository fenced one dead owner.

    The full bounded ``SENDING`` snapshot retains tenant/project scope and every
    owner claim field.  The repository claim contributes the independently
    persisted exclusive reconciliation identity.
    """

    repository_claim: DurableReconciliationClaim
    sending_operation: SubmissionOperationTruth
    terminated_fence_set_sha256: Sha256Hex
    owner_gateway_revision: OpaqueId

    @model_validator(mode="after")
    def admission_is_exclusive_and_exact(self) -> Self:
        claim = self.sending_operation.claim
        if not self.repository_claim.acquired:
            raise ValueError("dead_owner_reconciliation_not_acquired")
        if not self.repository_claim.owner_session_terminated:
            raise ValueError("dead_owner_session_not_terminated")
        if self.sending_operation.send_state is not SendState.SENDING or claim is None:
            raise ValueError("dead_owner_admission_requires_sending")
        if self.repository_claim.operation != operation_ref(self.sending_operation.identity):
            raise ValueError("dead_owner_repository_operation_drift")
        if self.terminated_fence_set_sha256 != claim.fence_set_sha256:
            raise ValueError("dead_owner_terminated_fence_set_drift")
        return self

    @classmethod
    def from_repository_claim(
        cls,
        *,
        repository_claim: DurableReconciliationClaim,
        sending_operation: SubmissionOperationTruth,
        owner_gateway_revision: str,
    ) -> Self:
        """Bind the repository proof to the exact durable dispatch snapshot.

        ``owner_gateway_revision`` must be projected from the claimed dispatch.
        PostgreSQL freezes that value from the execution grant's
        ``gateway_protocol_revision`` during owner CAS; it is intentionally not
        inferred from the operation or owner handle.
        """

        claim = sending_operation.claim
        if sending_operation.send_state is not SendState.SENDING or claim is None:
            _fail("dead_owner_admission_requires_sending")
        return cls(
            repository_claim=repository_claim,
            sending_operation=sending_operation,
            terminated_fence_set_sha256=claim.fence_set_sha256,
            owner_gateway_revision=owner_gateway_revision,
        )

    @property
    def claim(self) -> OwnerClaimTruth:
        claim = self.sending_operation.claim
        assert claim is not None
        return claim


AcceptedProofPubId = Annotated[str, Field(pattern=r"^crp_[0-9a-f]{26}$")]


class OwnerNotSentProofRequest(FrozenProtocolModel):
    """Exact reconciled not-sent fact submitted to the accepted-proof store."""

    schema_version: Literal["collection-owner-not-sent-proof-request-v1"] = (
        "collection-owner-not-sent-proof-request-v1"
    )
    tenant_id: UUID
    project_id: UUID
    operation: OperationRef
    expected_state_version: int = Field(strict=True, ge=2)
    reconciliation_claim_ref: OpaqueId
    owner_gateway_revision: OpaqueId
    claim: OwnerClaimTruth
    terminated_fence_set_sha256: Sha256Hex
    owner_evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    boundary_entered: Literal[False] = False

    @model_validator(mode="after")
    def request_matches_claim(self) -> Self:
        if self.terminated_fence_set_sha256 != self.claim.fence_set_sha256:
            raise ValueError("owner_not_sent_request_fence_drift")
        return self


def owner_not_sent_proof_request_digest(request: OwnerNotSentProofRequest) -> str:
    """Stable idempotency digest; observation wall-clock time is intentionally absent."""

    return sha256(canonical_json(request).encode()).hexdigest()


class AcceptedOwnerNotSentProof(FrozenProtocolModel):
    """Accepted ``collection_submission_reconciliation_proof`` identity."""

    schema_version: Literal["collection-owner-not-sent-proof-accepted-v1"] = (
        "collection-owner-not-sent-proof-accepted-v1"
    )
    proof_id: UUID
    proof_pub_id: AcceptedProofPubId
    request: OwnerNotSentProofRequest
    request_sha256: Sha256Hex
    owner_gateway_revision: OpaqueId
    owner_evidence_ref: OpaqueId
    evidence_sha256: Sha256Hex
    terminated_lease_count: int = Field(strict=True, ge=1)
    terminated_lease_set_sha256: Sha256Hex
    reason_code: OpaqueId
    accepted_at: datetime

    @model_validator(mode="after")
    def accepted_request_is_exact(self) -> Self:
        if self.request_sha256 != owner_not_sent_proof_request_digest(self.request):
            raise ValueError("accepted_owner_not_sent_proof_digest_drift")
        if self.proof_pub_id != f"crp_{self.proof_id.hex[:26]}":
            raise ValueError("accepted_owner_not_sent_proof_public_id_drift")
        if (
            self.owner_gateway_revision != self.request.owner_gateway_revision
            or self.owner_evidence_ref != self.request.owner_evidence_ref
            or self.evidence_sha256 != self.request.evidence_sha256
        ):
            raise ValueError("accepted_owner_not_sent_proof_request_drift")
        if self.accepted_at < self.request.claim.claimed_at:
            raise ValueError("accepted_owner_not_sent_proof_before_claim")
        return self


class AcceptedOwnerNotSentProofStore(Protocol):
    """Narrow idempotent DB port; it has neither submit nor owner-WAL write authority."""

    def accept_owner_not_sent(
        self,
        request: OwnerNotSentProofRequest,
    ) -> AcceptedOwnerNotSentProof: ...


def build_owner_not_sent_proof_request(
    *,
    admission: DeadOwnerReconciliationAdmission,
    evidence: ReconciliationEvidence,
) -> OwnerNotSentProofRequest:
    material = admission.sending_operation.identity.material
    return OwnerNotSentProofRequest(
        tenant_id=material.tenant_id,
        project_id=material.project_id,
        operation=operation_ref(admission.sending_operation.identity),
        expected_state_version=admission.sending_operation.state_version,
        reconciliation_claim_ref=(admission.repository_claim.reconciliation_claim_ref),
        owner_gateway_revision=admission.owner_gateway_revision,
        claim=admission.claim,
        terminated_fence_set_sha256=admission.terminated_fence_set_sha256,
        owner_evidence_ref=evidence.durable_evidence_ref,
        evidence_sha256=evidence.durable_evidence_sha256,
    )


def owner_reconciliation_evidence_digest(
    *,
    admission: DeadOwnerReconciliationAdmission,
    journal: SubmissionOwnerSendJournalSnapshot,
) -> str:
    """Hash immutable truth so one reconciliation claim reuses it after crashes."""

    return sha256(
        canonical_json(
            {
                "admission": admission,
                "journal": journal,
                "schema_version": "collection-owner-reconciliation-evidence-v1",
            }
        ).encode()
    ).hexdigest()


def owner_reconciliation_evidence_ref(evidence_sha256: str) -> str:
    """Return the deterministic bounded reference for reconciliation evidence."""

    if len(evidence_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in evidence_sha256
    ):
        _fail("owner_reconciliation_evidence_digest_invalid")
    return f"ore_{evidence_sha256}"


POSTGRES_OWNER_NOT_SENT_REASON = "owner_wal_reconciliation_proved_not_sent"

SET_OWNER_NOT_SENT_TIMEZONE_SQL = "SET LOCAL TIME ZONE 'UTC'"
SET_OWNER_NOT_SENT_TENANT_SQL = (
    "SELECT set_config('app.tenant_id', CAST(%(tenant_id)s AS text), true)"
)

LOCK_OWNER_NOT_SENT_AUTHORITY_SQL = """
SELECT operation.id,
       operation.pub_id,
       operation.operation_key,
       operation.operation_generation,
       operation.send_state,
       operation.send_state_version,
       operation.reconciliation_state,
       manifest.request_manifest_hash,
       manifest.provider_idempotency_key_hash,
       dispatch.id,
       dispatch.pub_id,
       dispatch.claim_pub_id,
       dispatch.owner_handle,
       dispatch.authority_sha256,
       dispatch.dispatch_key,
       dispatch.owner_dispatch_ref,
       dispatch.owner_wal_evidence_hash,
       dispatch.grant_resource_set_hash,
       dispatch.owner_gateway_revision,
       dispatch.owner_execution_state,
       dispatch.reconciliation_state,
       dispatch.reconciliation_version,
       dispatch.reconciliation_claim_ref,
       dispatch.reconciliation_claim_hash,
       dispatch.claimed_at,
       execution_grant.id,
       execution_grant.pub_id,
       execution_grant.grant_revision,
       execution_grant.gateway_protocol_revision
FROM platform.collection_submission_operation AS operation
JOIN platform.collection_submission_request_manifest_v2 AS manifest
  ON manifest.operation_id = operation.id
 AND manifest.tenant_id = operation.tenant_id
 AND manifest.project_id = operation.project_id
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
"""

LOCK_OWNER_NOT_SENT_LEASES_SQL = """
SELECT lease.id,
       lease.lease_state,
       CASE
         WHEN lease.lease_state = 'released' THEN lease.released_at IS NOT NULL
         WHEN lease.lease_state = 'expired' THEN lease.expires_at <= CURRENT_TIMESTAMP
         WHEN lease.lease_state IN ('preempted', 'quarantined')
           THEN lease.revoked_at IS NOT NULL
         ELSE false
       END AS lease_is_terminated
FROM platform.resource_lease AS lease
WHERE lease.tenant_id = %(tenant_id)s
  AND lease.project_id = %(project_id)s
  AND lease.operation_id = %(operation_id)s
  AND lease.lease_schema_version = 'collection-resource-lease-v2'
ORDER BY lease.id
"""

LOCK_OWNER_NOT_SENT_CAPACITY_SQL = """
SELECT grant_resource.resource_role,
       grant_resource.resource_ordinal,
       grant_resource.resource_lease_id,
       CASE
         WHEN capacity.current_fencing_token > grant_resource.fence_generation THEN true
         WHEN capacity.current_fencing_token = grant_resource.fence_generation
           THEN capacity.capacity_state <> 'leased'
         ELSE false
       END AS capacity_is_fenced,
       grant_resource.resource_pub_id,
       grant_resource.fence_generation,
       lease.pub_id,
       grant_resource.owner_gateway_handle
FROM platform.collection_execution_grant_resource AS grant_resource
JOIN platform.collection_resource_capacity_unit AS capacity
  ON capacity.id = grant_resource.capacity_unit_id
 AND capacity.tenant_id = grant_resource.tenant_id
 AND capacity.project_id = grant_resource.project_id
JOIN platform.resource_lease AS lease
  ON lease.id = grant_resource.resource_lease_id
 AND lease.tenant_id = grant_resource.tenant_id
 AND lease.project_id = grant_resource.project_id
WHERE grant_resource.execution_grant_id = %(execution_grant_id)s
  AND grant_resource.tenant_id = %(tenant_id)s
  AND grant_resource.project_id = %(project_id)s
ORDER BY grant_resource.resource_role,
         grant_resource.resource_pub_id,
         lease.pub_id
"""

RECORD_OWNER_NOT_SENT_PROOF_SQL = """
SELECT platform.record_collection_not_sent_proof_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(owner_gateway_revision)s,
  %(owner_evidence_ref)s,
  %(evidence_hash)s,
  %(reason_code)s
)
"""

LOAD_ACCEPTED_OWNER_NOT_SENT_PROOF_SQL = """
SELECT proof.id,
       proof.pub_id,
       proof.tenant_id,
       proof.project_id,
       proof.operation_id,
       proof.proof_key,
       proof.proof_kind,
       proof.owner_gateway_revision,
       proof.owner_evidence_ref,
       proof.evidence_hash,
       proof.terminated_lease_count,
       proof.terminated_lease_set_hash,
       proof.proof_state,
       proof.reason_code,
       proof.recorded_by,
       proof.accepted_at
FROM platform.collection_submission_reconciliation_proof AS proof
WHERE proof.id = %(proof_id)s
  AND proof.tenant_id = %(tenant_id)s
  AND proof.project_id = %(project_id)s
  AND proof.operation_id = %(operation_id)s
"""


@dataclass(frozen=True, slots=True)
class _LockedOwnerProofAuthority:
    operation_id: UUID
    operation_pub_id: str
    operation_key: str
    operation_generation: int
    send_state: str
    send_state_version: int
    operation_reconciliation_state: str
    request_manifest_sha256: str
    provider_idempotency_key_sha256: str
    dispatch_id: UUID
    dispatch_pub_id: str
    claim_pub_id: str
    owner_handle: str
    authority_sha256: str
    dispatch_key: str
    owner_dispatch_ref: str
    owner_wal_evidence_sha256: str
    grant_resource_set_sha256: str
    owner_gateway_revision: str
    owner_execution_state: str
    dispatch_reconciliation_state: str
    reconciliation_version: int
    reconciliation_claim_ref: str
    reconciliation_claim_sha256: str
    claimed_at: datetime
    execution_grant_id: UUID
    grant_pub_id: str
    grant_revision: int
    grant_gateway_revision: str


def owner_reconciliation_claim_digest(
    *,
    operation: OperationRef,
    reconciliation_claim_ref: str,
) -> str:
    """Match ``PostgresSubmissionRepository`` reconciliation-claim identity."""

    return sha256(
        canonical_json(
            {
                "operation": operation.model_dump(mode="json"),
                "reconciliation_claim_ref": reconciliation_claim_ref,
                "version": "collection-reconciliation-claim-v1",
            }
        ).encode()
    ).hexdigest()


def _database_row(
    row: Sequence[object] | None,
    *,
    size: int,
    code: str,
) -> Sequence[object]:
    if row is None or len(row) != size:
        _fail(code)
    return row


def _database_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _database_integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    return value


def _database_boolean(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _database_uuid(value: object, code: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise OwnerReconciliationError(code) from exc
    _fail(code)


def _database_datetime(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value


def _database_sha256(value: object, code: str) -> str:
    digest = _database_text(value, code)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        _fail(code)
    return digest


class PostgresAcceptedOwnerNotSentProofStore:
    """Transactional adapter for the accepted reconciliation-proof function.

    This adapter must run as ``geo_worker``.  It performs no direct DML and has
    no owner-WAL or submit capability; the only mutation is the existing
    SECURITY DEFINER ``record_collection_not_sent_proof_v2`` function.
    """

    def __init__(
        self,
        *,
        connection_factory: RepositoryConnectionFactory,
        scope: RepositoryScope,
    ) -> None:
        self._connection_factory = connection_factory
        self._scope = scope

    def project_admission(
        self,
        *,
        repository_claim: DurableReconciliationClaim,
        sending_operation: SubmissionOperationTruth,
    ) -> DeadOwnerReconciliationAdmission:
        """Project gateway revision from the exact claimed dispatch."""

        self._assert_scope(
            sending_operation.identity.material.tenant_id,
            sending_operation.identity.material.project_id,
        )
        try:
            with self._connection_factory() as connection:
                with connection.transaction():
                    self._set_scope(connection)
                    locked = self._load_authority(
                        connection,
                        operation_ref(sending_operation.identity),
                    )
                    self._assert_admission_authority(
                        locked=locked,
                        repository_claim=repository_claim,
                        sending_operation=sending_operation,
                    )
                    return DeadOwnerReconciliationAdmission.from_repository_claim(
                        repository_claim=repository_claim,
                        sending_operation=sending_operation,
                        owner_gateway_revision=locked.owner_gateway_revision,
                    )
        except OwnerReconciliationError:
            raise
        except Exception as exc:
            raise OwnerReconciliationError(
                "postgres_owner_reconciliation_admission_failed"
            ) from exc

    def accept_owner_not_sent(
        self,
        request: OwnerNotSentProofRequest,
    ) -> AcceptedOwnerNotSentProof:
        self._assert_scope(request.tenant_id, request.project_id)
        try:
            with self._connection_factory() as connection:
                with connection.transaction():
                    self._set_scope(connection)
                    observed = self._load_authority(connection, request.operation)
                    self._assert_request_authority(locked=observed, request=request)
                    proof_id = self._record_proof(
                        connection=connection,
                        locked=observed,
                        request=request,
                    )
                    # The SECURITY DEFINER function has now locked the SENDING
                    # operation. Re-read every authority input; any concurrent
                    # drift rolls the function effects back in this transaction.
                    locked = self._load_authority(connection, request.operation)
                    if locked.operation_reconciliation_state != "in_progress" or locked != replace(
                        observed,
                        operation_reconciliation_state="in_progress",
                    ):
                        _fail("postgres_owner_reconciliation_authority_drift")
                    self._assert_request_authority(locked=locked, request=request)
                    current_fence_hash = self._lock_and_assert_terminated_resources(
                        connection=connection,
                        locked=locked,
                    )
                    if (
                        current_fence_hash != request.terminated_fence_set_sha256
                        or current_fence_hash != locked.grant_resource_set_sha256
                    ):
                        _fail("postgres_owner_not_sent_current_fence_drift")
                    return self._load_accepted_proof(
                        connection=connection,
                        locked=locked,
                        request=request,
                        proof_id=proof_id,
                    )
        except OwnerReconciliationError:
            raise
        except Exception as exc:
            raise OwnerReconciliationError("postgres_owner_not_sent_proof_failed") from exc

    def _assert_scope(self, tenant_id: UUID, project_id: UUID) -> None:
        if tenant_id != self._scope.tenant_id or project_id != self._scope.project_id:
            _fail("postgres_owner_not_sent_scope_drift")

    def _set_scope(self, connection: RepositoryConnection) -> None:
        connection.execute(SET_OWNER_NOT_SENT_TIMEZONE_SQL)
        connection.execute(
            SET_OWNER_NOT_SENT_TENANT_SQL,
            {"tenant_id": self._scope.tenant_id},
        )

    def _load_authority(
        self,
        connection: RepositoryConnection,
        operation: OperationRef,
    ) -> _LockedOwnerProofAuthority:
        rows = connection.execute(
            LOCK_OWNER_NOT_SENT_AUTHORITY_SQL,
            {
                "tenant_id": self._scope.tenant_id,
                "project_id": self._scope.project_id,
                "operation_pub_id": operation.operation_pub_id,
                "operation_key": operation.operation_key,
                "operation_generation": operation.generation,
            },
        ).fetchall()
        if len(rows) != 1:
            _fail("postgres_owner_reconciliation_authority_missing_or_ambiguous")
        values = _database_row(
            rows[0],
            size=29,
            code="postgres_owner_reconciliation_authority_row_invalid",
        )
        return _LockedOwnerProofAuthority(
            operation_id=_database_uuid(values[0], "postgres_operation_id_invalid"),
            operation_pub_id=_database_text(values[1], "postgres_operation_pub_id_invalid"),
            operation_key=_database_text(values[2], "postgres_operation_key_invalid"),
            operation_generation=_database_integer(
                values[3], "postgres_operation_generation_invalid"
            ),
            send_state=_database_text(values[4], "postgres_send_state_invalid"),
            send_state_version=_database_integer(values[5], "postgres_send_state_version_invalid"),
            operation_reconciliation_state=_database_text(
                values[6], "postgres_operation_reconciliation_state_invalid"
            ),
            request_manifest_sha256=_database_sha256(
                values[7], "postgres_request_manifest_hash_invalid"
            ),
            provider_idempotency_key_sha256=_database_sha256(
                values[8], "postgres_provider_idempotency_hash_invalid"
            ),
            dispatch_id=_database_uuid(values[9], "postgres_dispatch_id_invalid"),
            dispatch_pub_id=_database_text(values[10], "postgres_dispatch_pub_id_invalid"),
            claim_pub_id=_database_text(values[11], "postgres_claim_pub_id_invalid"),
            owner_handle=_database_text(values[12], "postgres_owner_handle_invalid"),
            authority_sha256=_database_sha256(values[13], "postgres_authority_hash_invalid"),
            dispatch_key=_database_text(values[14], "postgres_dispatch_key_invalid"),
            owner_dispatch_ref=_database_text(values[15], "postgres_owner_dispatch_ref_invalid"),
            owner_wal_evidence_sha256=_database_sha256(
                values[16], "postgres_owner_wal_hash_invalid"
            ),
            grant_resource_set_sha256=_database_sha256(
                values[17], "postgres_grant_resource_set_hash_invalid"
            ),
            owner_gateway_revision=_database_text(
                values[18], "postgres_owner_gateway_revision_invalid"
            ),
            owner_execution_state=_database_text(
                values[19], "postgres_owner_execution_state_invalid"
            ),
            dispatch_reconciliation_state=_database_text(
                values[20], "postgres_dispatch_reconciliation_state_invalid"
            ),
            reconciliation_version=_database_integer(
                values[21], "postgres_reconciliation_version_invalid"
            ),
            reconciliation_claim_ref=_database_text(
                values[22], "postgres_reconciliation_claim_ref_invalid"
            ),
            reconciliation_claim_sha256=_database_sha256(
                values[23], "postgres_reconciliation_claim_hash_invalid"
            ),
            claimed_at=_database_datetime(values[24], "postgres_claimed_at_invalid"),
            execution_grant_id=_database_uuid(values[25], "postgres_execution_grant_id_invalid"),
            grant_pub_id=_database_text(values[26], "postgres_grant_pub_id_invalid"),
            grant_revision=_database_integer(values[27], "postgres_grant_revision_invalid"),
            grant_gateway_revision=_database_text(
                values[28], "postgres_grant_gateway_revision_invalid"
            ),
        )

    def _assert_admission_authority(
        self,
        *,
        locked: _LockedOwnerProofAuthority,
        repository_claim: DurableReconciliationClaim,
        sending_operation: SubmissionOperationTruth,
    ) -> None:
        claim = sending_operation.claim
        if claim is None:
            _fail("postgres_owner_reconciliation_claim_missing")
        if (
            not repository_claim.acquired
            or not repository_claim.owner_session_terminated
            or repository_claim.operation != operation_ref(sending_operation.identity)
        ):
            _fail("postgres_owner_reconciliation_admission_drift")
        self._assert_locked_identity(
            locked=locked,
            operation=operation_ref(sending_operation.identity),
            expected_state_version=sending_operation.state_version,
            reconciliation_claim_ref=repository_claim.reconciliation_claim_ref,
            claim=claim,
            owner_gateway_revision=locked.owner_gateway_revision,
        )

    def _assert_request_authority(
        self,
        *,
        locked: _LockedOwnerProofAuthority,
        request: OwnerNotSentProofRequest,
    ) -> None:
        self._assert_locked_identity(
            locked=locked,
            operation=request.operation,
            expected_state_version=request.expected_state_version,
            reconciliation_claim_ref=request.reconciliation_claim_ref,
            claim=request.claim,
            owner_gateway_revision=request.owner_gateway_revision,
        )
        if locked.grant_resource_set_sha256 != request.terminated_fence_set_sha256:
            _fail("postgres_owner_not_sent_dispatch_fence_drift")

    def _assert_locked_identity(
        self,
        *,
        locked: _LockedOwnerProofAuthority,
        operation: OperationRef,
        expected_state_version: int,
        reconciliation_claim_ref: str,
        claim: OwnerClaimTruth,
        owner_gateway_revision: str,
    ) -> None:
        if (
            locked.operation_pub_id != operation.operation_pub_id
            or locked.operation_key != operation.operation_key
            or locked.operation_generation != operation.generation
            or locked.request_manifest_sha256 != operation.request_manifest_sha256
            or locked.provider_idempotency_key_sha256
            != sha256(operation.provider_idempotency_key.encode()).hexdigest()
        ):
            _fail("postgres_owner_reconciliation_operation_drift")
        if locked.send_state != "SENDING" or locked.send_state_version != expected_state_version:
            _fail("postgres_owner_reconciliation_send_state_drift")
        if locked.operation_reconciliation_state not in {"not_required", "in_progress"}:
            _fail("postgres_owner_reconciliation_operation_state_drift")
        if (
            locked.claim_pub_id != claim.claim_pub_id
            or locked.owner_handle != claim.owner_handle
            or locked.authority_sha256 != claim.authority_sha256
            or locked.dispatch_key != claim.dispatch_key
            or locked.owner_dispatch_ref != claim.owner_dispatch_ref
            or locked.owner_wal_evidence_sha256 != claim.owner_wal_evidence_sha256
            or locked.grant_resource_set_sha256 != claim.fence_set_sha256
            or locked.claimed_at != claim.claimed_at
            or locked.grant_pub_id != claim.grant_pub_id
            or locked.grant_revision != claim.grant_revision
        ):
            _fail("postgres_owner_reconciliation_dispatch_claim_drift")
        expected_claim_hash = owner_reconciliation_claim_digest(
            operation=operation,
            reconciliation_claim_ref=reconciliation_claim_ref,
        )
        if (
            locked.owner_execution_state != "owner_lost"
            or locked.dispatch_reconciliation_state != "in_progress"
            or locked.reconciliation_version < 3
            or locked.reconciliation_claim_ref != reconciliation_claim_ref
            or locked.reconciliation_claim_sha256 != expected_claim_hash
        ):
            _fail("postgres_owner_reconciliation_exclusive_claim_drift")
        if (
            locked.owner_gateway_revision != owner_gateway_revision
            or locked.grant_gateway_revision != owner_gateway_revision
        ):
            _fail("postgres_owner_reconciliation_gateway_revision_drift")

    def _lock_and_assert_terminated_resources(
        self,
        *,
        connection: RepositoryConnection,
        locked: _LockedOwnerProofAuthority,
    ) -> str:
        params = {
            "tenant_id": self._scope.tenant_id,
            "project_id": self._scope.project_id,
            "operation_id": locked.operation_id,
            "execution_grant_id": locked.execution_grant_id,
        }
        lease_rows = connection.execute(LOCK_OWNER_NOT_SENT_LEASES_SQL, params).fetchall()
        if not lease_rows:
            _fail("postgres_owner_not_sent_leases_missing")
        lease_ids: set[UUID] = set()
        for raw in lease_rows:
            values = _database_row(
                raw,
                size=3,
                code="postgres_owner_not_sent_lease_row_invalid",
            )
            lease_id = _database_uuid(values[0], "postgres_owner_not_sent_lease_id_invalid")
            _database_text(values[1], "postgres_owner_not_sent_lease_state_invalid")
            if not _database_boolean(
                values[2], "postgres_owner_not_sent_lease_termination_invalid"
            ):
                _fail("postgres_owner_not_sent_lease_not_terminated")
            if lease_id in lease_ids:
                _fail("postgres_owner_not_sent_duplicate_lease")
            lease_ids.add(lease_id)

        capacity_rows = connection.execute(
            LOCK_OWNER_NOT_SENT_CAPACITY_SQL,
            params,
        ).fetchall()
        capacity_lease_ids: set[UUID] = set()
        canonical_fences: list[str] = []
        for raw in capacity_rows:
            values = _database_row(
                raw,
                size=8,
                code="postgres_owner_not_sent_capacity_row_invalid",
            )
            resource_role = _database_text(
                values[0], "postgres_owner_not_sent_resource_role_invalid"
            )
            ordinal = _database_integer(
                values[1], "postgres_owner_not_sent_resource_ordinal_invalid"
            )
            if ordinal < 0:
                _fail("postgres_owner_not_sent_resource_ordinal_invalid")
            lease_id = _database_uuid(
                values[2], "postgres_owner_not_sent_capacity_lease_id_invalid"
            )
            if not _database_boolean(values[3], "postgres_owner_not_sent_capacity_fence_invalid"):
                _fail("postgres_owner_not_sent_capacity_not_fenced")
            if lease_id in capacity_lease_ids:
                _fail("postgres_owner_not_sent_duplicate_capacity_lease")
            capacity_lease_ids.add(lease_id)
            resource_pub_id = _database_text(
                values[4], "postgres_owner_not_sent_resource_pub_id_invalid"
            )
            fence_generation = _database_integer(
                values[5], "postgres_owner_not_sent_fence_generation_invalid"
            )
            if fence_generation < 1:
                _fail("postgres_owner_not_sent_fence_generation_invalid")
            lease_pub_id = _database_text(values[6], "postgres_owner_not_sent_lease_pub_id_invalid")
            owner_handle = _database_text(values[7], "postgres_owner_not_sent_owner_handle_invalid")
            canonical_fences.append(
                '{"binding_resource_pub_id":"'
                + resource_pub_id
                + '","generation":'
                + str(fence_generation)
                + ',"lease_pub_id":"'
                + lease_pub_id
                + '","owner_handle":"'
                + owner_handle
                + '","resource_role":"'
                + resource_role
                + '"}'
            )
        if not capacity_lease_ids or capacity_lease_ids != lease_ids:
            _fail("postgres_owner_not_sent_resource_lease_set_drift")
        material = (
            '{"fences":[' + ",".join(canonical_fences) + '],"version":"lease-fence-identity-v1"}'
        )
        return sha256(material.encode()).hexdigest()

    def _record_proof(
        self,
        *,
        connection: RepositoryConnection,
        locked: _LockedOwnerProofAuthority,
        request: OwnerNotSentProofRequest,
    ) -> UUID:
        row = _database_row(
            connection.execute(
                RECORD_OWNER_NOT_SENT_PROOF_SQL,
                {
                    "tenant_id": self._scope.tenant_id,
                    "project_id": self._scope.project_id,
                    "operation_id": locked.operation_id,
                    "owner_gateway_revision": request.owner_gateway_revision,
                    "owner_evidence_ref": request.owner_evidence_ref,
                    "evidence_hash": request.evidence_sha256,
                    "reason_code": POSTGRES_OWNER_NOT_SENT_REASON,
                },
            ).fetchone(),
            size=1,
            code="postgres_owner_not_sent_proof_not_recorded",
        )
        return _database_uuid(row[0], "postgres_owner_not_sent_proof_id_invalid")

    def _load_accepted_proof(
        self,
        *,
        connection: RepositoryConnection,
        locked: _LockedOwnerProofAuthority,
        request: OwnerNotSentProofRequest,
        proof_id: UUID,
    ) -> AcceptedOwnerNotSentProof:
        values = _database_row(
            connection.execute(
                LOAD_ACCEPTED_OWNER_NOT_SENT_PROOF_SQL,
                {
                    "proof_id": proof_id,
                    "tenant_id": self._scope.tenant_id,
                    "project_id": self._scope.project_id,
                    "operation_id": locked.operation_id,
                },
            ).fetchone(),
            size=16,
            code="postgres_owner_not_sent_proof_reload_failed",
        )
        persisted_id = _database_uuid(values[0], "postgres_owner_not_sent_proof_id_invalid")
        proof_pub_id = _database_text(values[1], "postgres_owner_not_sent_proof_pub_id_invalid")
        tenant_id = _database_uuid(values[2], "postgres_owner_not_sent_proof_tenant_invalid")
        project_id = _database_uuid(values[3], "postgres_owner_not_sent_proof_project_invalid")
        operation_id = _database_uuid(values[4], "postgres_owner_not_sent_proof_operation_invalid")
        proof_key = _database_text(values[5], "postgres_owner_not_sent_proof_key_invalid")
        proof_kind = _database_text(values[6], "postgres_owner_not_sent_proof_kind_invalid")
        gateway_revision = _database_text(
            values[7], "postgres_owner_not_sent_proof_gateway_revision_invalid"
        )
        evidence_ref = _database_text(
            values[8], "postgres_owner_not_sent_proof_evidence_ref_invalid"
        )
        evidence_sha256 = _database_sha256(
            values[9], "postgres_owner_not_sent_proof_evidence_hash_invalid"
        )
        lease_count = _database_integer(
            values[10], "postgres_owner_not_sent_proof_lease_count_invalid"
        )
        lease_set_sha256 = _database_sha256(
            values[11], "postgres_owner_not_sent_proof_lease_hash_invalid"
        )
        proof_state = _database_text(values[12], "postgres_owner_not_sent_proof_state_invalid")
        reason_code = _database_text(values[13], "postgres_owner_not_sent_proof_reason_invalid")
        _database_text(values[14], "postgres_owner_not_sent_proof_recorder_invalid")
        accepted_at = _database_datetime(
            values[15], "postgres_owner_not_sent_proof_accepted_at_invalid"
        )
        if (
            persisted_id != proof_id
            or tenant_id != self._scope.tenant_id
            or project_id != self._scope.project_id
            or operation_id != locked.operation_id
            or proof_key != f"{locked.operation_id}:{request.evidence_sha256}"
            or proof_kind != "owner_proved_not_sent"
            or proof_state != "accepted"
            or gateway_revision != request.owner_gateway_revision
            or evidence_ref != request.owner_evidence_ref
            or evidence_sha256 != request.evidence_sha256
            or lease_count < 1
            or reason_code != POSTGRES_OWNER_NOT_SENT_REASON
        ):
            _fail("postgres_owner_not_sent_proof_row_drift")
        try:
            return AcceptedOwnerNotSentProof(
                proof_id=persisted_id,
                proof_pub_id=proof_pub_id,
                request=request,
                request_sha256=owner_not_sent_proof_request_digest(request),
                owner_gateway_revision=gateway_revision,
                owner_evidence_ref=evidence_ref,
                evidence_sha256=evidence_sha256,
                terminated_lease_count=lease_count,
                terminated_lease_set_sha256=lease_set_sha256,
                reason_code=reason_code,
                accepted_at=accepted_at,
            )
        except ValueError as exc:
            raise OwnerReconciliationError("postgres_owner_not_sent_proof_row_invalid") from exc


class OwnerWalReconciliationGateway:
    """Resolve one exclusively admitted dead owner from its read-only WAL chain."""

    def __init__(
        self,
        *,
        admission: DeadOwnerReconciliationAdmission,
        journal_reader: SubmissionOwnerSendJournalReader,
        clock: Clock,
        not_sent_proof_store: AcceptedOwnerNotSentProofStore | None = None,
    ) -> None:
        if not isinstance(admission, DeadOwnerReconciliationAdmission):
            _fail("dead_owner_reconciliation_admission_invalid")
        self._admission = admission
        self._journal_reader = journal_reader
        self._clock = clock
        self._not_sent_proof_store = not_sent_proof_store

    @property
    def admission(self) -> DeadOwnerReconciliationAdmission:
        return self._admission

    def observe_sending(self, operation: SubmissionOperationTruth) -> ReconciliationEvidence:
        """Observe the exact immutable journal without acquiring submit authority."""

        self._assert_admitted_operation(operation)
        journal = self._load_exact_journal()
        observed_at = self._now()
        self._assert_observation_is_causal(journal, observed_at)
        evidence_sha256 = owner_reconciliation_evidence_digest(
            admission=self._admission,
            journal=journal,
        )
        return ReconciliationEvidence(
            durable_evidence_ref=owner_reconciliation_evidence_ref(evidence_sha256),
            durable_evidence_sha256=evidence_sha256,
            observed_at=observed_at,
        )

    def reconcile_sending(
        self,
        command: SendingReconciliationCommand,
    ) -> ReconciliationDisposition:
        """Converge from WAL truth; this method can never send or append to WAL."""

        self._assert_exact_command(command)
        journal = self._load_exact_journal()
        self._assert_observation_is_causal(journal, command.observed_at)
        expected_digest = owner_reconciliation_evidence_digest(
            admission=self._admission,
            journal=journal,
        )
        if (
            command.durable_evidence_sha256 != expected_digest
            or command.durable_evidence_ref != owner_reconciliation_evidence_ref(expected_digest)
        ):
            _fail("owner_reconciliation_evidence_drift")

        outcome = journal.outcome
        if outcome is not None:
            source = outcome.disposition
            reconciled_send_state = cast(
                Literal[
                    SendState.CONFIRMED_SENT,
                    SendState.SEND_UNKNOWN,
                    SendState.CONFIRMED_NOT_SENT,
                ],
                source.send_state,
            )
            if (
                source.send_state is SendState.CONFIRMED_NOT_SENT
                and source.terminated_fence_set_sha256
                != self._admission.terminated_fence_set_sha256
            ):
                _fail("owner_reconciliation_outcome_fence_drift")
            accepted_proof: AcceptedOwnerNotSentProof | None = None
            if source.send_state is SendState.CONFIRMED_NOT_SENT:
                accepted_proof = self._accepted_not_sent_proof(command)
            return ReconciliationDisposition(
                send_state=reconciled_send_state,
                reason=source.reason,
                boundary_entered=source.boundary_entered,
                evidence_ref=command.durable_evidence_ref,
                evidence_sha256=command.durable_evidence_sha256,
                provider_submission_ref=source.provider_submission_ref,
                non_submission_proof_ref=(
                    accepted_proof.proof_pub_id if accepted_proof is not None else None
                ),
                terminated_fence_set_sha256=source.terminated_fence_set_sha256,
                resolved_at=(
                    max(command.observed_at, accepted_proof.accepted_at)
                    if accepted_proof is not None
                    else command.observed_at
                ),
            )

        if journal.boundary is not None:
            return ReconciliationDisposition(
                send_state=SendState.SEND_UNKNOWN,
                reason=TerminalReason.SEND_UNKNOWN,
                boundary_entered=True,
                evidence_ref=command.durable_evidence_ref,
                evidence_sha256=command.durable_evidence_sha256,
                resolved_at=command.observed_at,
            )

        accepted_proof = self._accepted_not_sent_proof(command)
        return ReconciliationDisposition(
            send_state=SendState.CONFIRMED_NOT_SENT,
            reason=TerminalReason.POST_CLAIM_NOT_SENT,
            boundary_entered=False,
            evidence_ref=command.durable_evidence_ref,
            evidence_sha256=command.durable_evidence_sha256,
            non_submission_proof_ref=accepted_proof.proof_pub_id,
            terminated_fence_set_sha256=self._admission.terminated_fence_set_sha256,
            resolved_at=max(command.observed_at, accepted_proof.accepted_at),
        )

    def _accepted_not_sent_proof(
        self,
        command: SendingReconciliationCommand,
    ) -> AcceptedOwnerNotSentProof:
        evidence = ReconciliationEvidence(
            durable_evidence_ref=command.durable_evidence_ref,
            durable_evidence_sha256=command.durable_evidence_sha256,
            observed_at=command.observed_at,
        )
        return self._accept_not_sent_proof(
            build_owner_not_sent_proof_request(
                admission=self._admission,
                evidence=evidence,
            )
        )

    def _accept_not_sent_proof(
        self,
        request: OwnerNotSentProofRequest,
    ) -> AcceptedOwnerNotSentProof:
        store = self._not_sent_proof_store
        if store is None:
            _fail("accepted_owner_not_sent_proof_store_unavailable")
        try:
            raw = store.accept_owner_not_sent(request)
        except Exception as exc:
            raise OwnerReconciliationError(
                "accepted_owner_not_sent_proof_store_unavailable"
            ) from exc
        if not isinstance(raw, AcceptedOwnerNotSentProof):
            _fail("accepted_owner_not_sent_proof_invalid")
        try:
            accepted = AcceptedOwnerNotSentProof.model_validate(raw.model_dump(mode="python"))
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise OwnerReconciliationError("accepted_owner_not_sent_proof_invalid") from exc
        if (
            accepted.request != request
            or accepted.request_sha256 != owner_not_sent_proof_request_digest(request)
        ):
            _fail("accepted_owner_not_sent_proof_drift")
        return accepted

    def _assert_admitted_operation(self, operation: SubmissionOperationTruth) -> None:
        expected = self._admission.sending_operation
        actual_material = operation.identity.material
        expected_material = expected.identity.material
        if (
            actual_material.tenant_id != expected_material.tenant_id
            or actual_material.project_id != expected_material.project_id
        ):
            _fail("owner_reconciliation_scope_drift")
        if operation_ref(operation.identity) != operation_ref(expected.identity):
            _fail("owner_reconciliation_operation_drift")
        if operation.state_version != expected.state_version or operation.claim != expected.claim:
            _fail("owner_reconciliation_claim_drift")
        if operation != expected:
            _fail("owner_reconciliation_operation_snapshot_drift")

    def _assert_exact_command(self, command: SendingReconciliationCommand) -> None:
        operation = self._admission.sending_operation
        claim = self._admission.claim
        if command.operation != operation_ref(operation.identity):
            _fail("owner_reconciliation_command_operation_drift")
        if command.expected_state_version != operation.state_version:
            _fail("owner_reconciliation_command_version_drift")
        if (
            command.claim_pub_id != claim.claim_pub_id
            or command.owner_handle != claim.owner_handle
            or command.authority_sha256 != claim.authority_sha256
            or command.dispatch_key != claim.dispatch_key
            or command.owner_dispatch_ref != claim.owner_dispatch_ref
            or command.owner_wal_evidence_sha256 != claim.owner_wal_evidence_sha256
        ):
            _fail("owner_reconciliation_command_claim_drift")

    def _load_exact_journal(self) -> SubmissionOwnerSendJournalSnapshot:
        claim = self._admission.claim
        try:
            raw = self._journal_reader.load_send_journal(
                owner_dispatch_ref=claim.owner_dispatch_ref,
            )
        except Exception as exc:
            raise OwnerReconciliationError("owner_reconciliation_journal_unavailable") from exc
        if not isinstance(raw, SubmissionOwnerSendJournalSnapshot):
            _fail("owner_reconciliation_journal_invalid")
        try:
            journal = SubmissionOwnerSendJournalSnapshot.model_validate(
                raw.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise OwnerReconciliationError("owner_reconciliation_journal_invalid") from exc
        if (
            journal.owner_dispatch_ref != claim.owner_dispatch_ref
            or journal.owner_authorization_evidence_sha256 != claim.owner_wal_evidence_sha256
        ):
            _fail("owner_reconciliation_journal_identity_drift")

        boundary = journal.boundary
        if boundary is not None:
            material = self._admission.sending_operation.identity.material
            boundary_claim = boundary.command.fresh_claim.claim
            if (
                boundary.tenant_id != material.tenant_id
                or boundary.project_id != material.project_id
            ):
                _fail("owner_reconciliation_scope_drift")
            if (
                boundary.command.fresh_claim.operation
                != operation_ref(self._admission.sending_operation.identity)
                or boundary.command.fresh_claim.claimed_state_version
                != self._admission.sending_operation.state_version
                or boundary_claim != claim
            ):
                _fail("owner_reconciliation_boundary_claim_drift")
            if boundary.owner_protocol_revision != self._admission.owner_gateway_revision:
                _fail("owner_reconciliation_gateway_revision_drift")
        return journal

    def _assert_observation_is_causal(
        self,
        journal: SubmissionOwnerSendJournalSnapshot,
        observed_at: datetime,
    ) -> None:
        latest = self._admission.claim.claimed_at
        if journal.boundary is not None:
            latest = max(latest, journal.boundary.entered_at)
        if journal.outcome is not None:
            latest = max(latest, journal.outcome.disposition.resolved_at)
        if observed_at < latest:
            _fail("owner_reconciliation_observation_before_journal")

    def _now(self) -> datetime:
        try:
            now = self._clock.now()
        except Exception as exc:
            raise OwnerReconciliationError("owner_reconciliation_clock_unavailable") from exc
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            _fail("owner_reconciliation_clock_must_be_timezone_aware")
        return now


class OwnerWalReconciliationGatewayBinder:
    """Production composition seam for one repository-authorized recovery.

    The binder owns only the PostgreSQL proof adapter, a read-only view of the
    owner WAL, and a clock.  ``SubmissionCoordinator`` calls it after its
    repository has durably acquired the exact reconciliation claim.  The
    returned gateway exposes neither ``submit_once`` nor owner-WAL append
    methods.
    """

    def __init__(
        self,
        *,
        proof_store: PostgresAcceptedOwnerNotSentProofStore,
        journal_reader: SubmissionOwnerSendJournalReader,
        clock: Clock,
    ) -> None:
        self._proof_store = proof_store
        self._journal_reader = journal_reader
        self._clock = clock

    def bind_reconciliation(
        self,
        *,
        repository_claim: DurableReconciliationClaim,
        sending_operation: SubmissionOperationTruth,
    ) -> ReconciliationGateway:
        admission = self._proof_store.project_admission(
            repository_claim=repository_claim,
            sending_operation=sending_operation,
        )
        return OwnerWalReconciliationGateway(
            admission=admission,
            journal_reader=self._journal_reader,
            clock=self._clock,
            not_sent_proof_store=self._proof_store,
        )


def compose_postgres_owner_reconciliation_gateway(
    *,
    connection_factory: RepositoryConnectionFactory,
    scope: RepositoryScope,
    journal_reader: SubmissionOwnerSendJournalReader,
    clock: Clock,
) -> OwnerWalReconciliationGatewayBinder:
    """Wire the existing PostgreSQL proof and owner-WAL adapters exactly once."""

    return OwnerWalReconciliationGatewayBinder(
        proof_store=PostgresAcceptedOwnerNotSentProofStore(
            connection_factory=connection_factory,
            scope=scope,
        ),
        journal_reader=journal_reader,
        clock=clock,
    )


__all__ = [
    "AcceptedOwnerNotSentProof",
    "AcceptedOwnerNotSentProofStore",
    "DeadOwnerReconciliationAdmission",
    "OwnerNotSentProofRequest",
    "OwnerReconciliationError",
    "OwnerWalReconciliationGateway",
    "OwnerWalReconciliationGatewayBinder",
    "PostgresAcceptedOwnerNotSentProofStore",
    "SubmissionOwnerSendJournalReader",
    "build_owner_not_sent_proof_request",
    "compose_postgres_owner_reconciliation_gateway",
    "owner_not_sent_proof_request_digest",
    "owner_reconciliation_claim_digest",
    "owner_reconciliation_evidence_digest",
    "owner_reconciliation_evidence_ref",
]
