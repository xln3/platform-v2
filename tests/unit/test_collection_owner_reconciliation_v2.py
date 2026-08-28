from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

import pytest
from geo_platform.collection import owner_reconciliation_v2 as reconciliation_module
from geo_platform.collection.owner_reconciliation_v2 import (
    AcceptedOwnerNotSentProof,
    AcceptedOwnerNotSentProofStore,
    DeadOwnerReconciliationAdmission,
    OwnerNotSentProofRequest,
    OwnerReconciliationError,
    OwnerWalReconciliationGateway,
    build_owner_not_sent_proof_request,
    owner_not_sent_proof_request_digest,
    owner_reconciliation_evidence_digest,
    owner_reconciliation_evidence_ref,
)
from geo_platform.collection.resource_owner_gateway_v2 import (
    SubmissionOwnerSendBoundaryRecord,
    SubmissionOwnerSendJournalSnapshot,
    SubmissionOwnerSendOutcomeRecord,
    build_submission_owner_send_outcome_record,
    submission_owner_send_boundary_digest,
)
from geo_platform.collection.submission_repository_v2 import RepositoryScope
from geo_platform.collection.submission_v2 import (
    DurableReconciliationClaim,
    ReconciliationEvidence,
)
from pydantic import ValidationError

from domain.collection.execution_governance import GatewayKind
from domain.collection.submission import (
    FreshSubmissionClaim,
    OperationIdentity,
    OperationKeyMaterial,
    OwnerClaimTruth,
    RequestManifest,
    SendingReconciliationCommand,
    SubmissionOperationTruth,
    SubmitDisposition,
    SubmitOnceCommand,
    SurfaceProductRef,
    TerminalReason,
    deterministic_dispatch_key,
    deterministic_operation_key,
    deterministic_provider_idempotency_key,
    operation_ref,
    request_manifest_digest,
)
from domain.collection.surface import CollectionSurface, CollectionTarget, SendState

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000701")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000702")
OTHER_TENANT_ID = UUID("00000000-0000-0000-0000-000000000799")
OWNER_GATEWAY_REVISION = "owner-gateway-v1"


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _identity(
    *,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
) -> OperationIdentity:
    target = CollectionTarget(
        platform="openai",
        collection_surface=CollectionSurface.PROVIDER_API,
        product_variant="responses",
        interaction_modes=("normal",),
    )
    product = SurfaceProductRef(
        platform=target.platform,
        collection_surface=target.collection_surface,
        product_variant=target.product_variant,
        target_key=target.target_key,
    )
    material = OperationKeyMaterial(
        tenant_id=tenant_id,
        project_id=project_id,
        campaign_pub_id="campaign-owner-reconciliation",
        slot_pub_id="slot-owner-reconciliation",
        target_key=target.target_key,
        leg_key="leg-owner-reconciliation",
        logical_item_key="logical-owner-reconciliation",
        generation=1,
        operation_policy_revision="operation-policy-v1",
    )
    manifest = RequestManifest(
        request_protocol_version="provider-request-v1",
        request_schema_revision="request-schema-v1",
        request_payload_ref="request-payload-owner-reconciliation",
        request_payload_sha256=_hash("request-payload"),
    )
    operation_key = deterministic_operation_key(material)
    return OperationIdentity(
        material=material,
        surface_product=product,
        operation_pub_id="operation-owner-reconciliation",
        operation_key=operation_key,
        request_manifest=manifest,
        request_manifest_sha256=request_manifest_digest(manifest),
        provider_idempotency_key=deterministic_provider_idempotency_key(operation_key),
    )


def _sending_operation(
    *,
    identity: OperationIdentity | None = None,
) -> SubmissionOperationTruth:
    identity = identity or _identity()
    ref = operation_ref(identity)
    claim = OwnerClaimTruth(
        claim_pub_id="claim-owner-reconciliation",
        owner_handle="owner-provider-api",
        grant_pub_id="grant-owner-reconciliation",
        grant_revision=4,
        authority_sha256=_hash("authority"),
        fence_set_sha256=_hash("fence-set"),
        dispatch_key=deterministic_dispatch_key(ref),
        owner_dispatch_ref="owner-dispatch-reconciliation",
        owner_wal_evidence_sha256=_hash("owner-authorization-wal"),
        claimed_at=NOW,
    )
    return SubmissionOperationTruth(
        identity=identity,
        send_state=SendState.SENDING,
        state_version=2,
        prepared_at=NOW - timedelta(minutes=1),
        claim=claim,
    )


def _repository_claim(
    operation: SubmissionOperationTruth,
    *,
    acquired: bool = True,
    owner_session_terminated: bool = True,
) -> DurableReconciliationClaim:
    return DurableReconciliationClaim(
        operation=operation_ref(operation.identity),
        reconciliation_claim_ref="reconciliation-claim-owner-dead",
        acquired=acquired,
        owner_session_terminated=owner_session_terminated,
    )


def _admission(operation: SubmissionOperationTruth) -> DeadOwnerReconciliationAdmission:
    return DeadOwnerReconciliationAdmission.from_repository_claim(
        repository_claim=_repository_claim(operation),
        sending_operation=operation,
        owner_gateway_revision=OWNER_GATEWAY_REVISION,
    )


def _submit_command(operation: SubmissionOperationTruth) -> SubmitOnceCommand:
    assert operation.claim is not None
    identity = operation.identity
    return SubmitOnceCommand(
        fresh_claim=FreshSubmissionClaim(
            operation=operation_ref(identity),
            claim=operation.claim,
            claimed_state_version=operation.state_version,
        ),
        request_manifest=identity.request_manifest,
        request_manifest_sha256=identity.request_manifest_sha256,
        provider_idempotency_key=identity.provider_idempotency_key,
    )


def _boundary(
    operation: SubmissionOperationTruth,
    *,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
    owner_gateway_revision: str = OWNER_GATEWAY_REVISION,
) -> SubmissionOwnerSendBoundaryRecord:
    command = _submit_command(operation)
    entered_at = NOW + timedelta(seconds=1)
    digest = submission_owner_send_boundary_digest(
        tenant_id=tenant_id,
        project_id=project_id,
        collection_surface=CollectionSurface.PROVIDER_API,
        gateway_kind=GatewayKind.PROVIDER_REQUEST,
        owner_protocol_revision=owner_gateway_revision,
        command=command,
        entered_at=entered_at,
    )
    return SubmissionOwnerSendBoundaryRecord(
        tenant_id=tenant_id,
        project_id=project_id,
        collection_surface=CollectionSurface.PROVIDER_API,
        gateway_kind=GatewayKind.PROVIDER_REQUEST,
        owner_protocol_revision=owner_gateway_revision,
        command=command,
        entered_at=entered_at,
        evidence_sha256=digest,
    )


def _journal(
    operation: SubmissionOperationTruth,
    *,
    boundary: SubmissionOwnerSendBoundaryRecord | None = None,
    outcome: SubmissionOwnerSendOutcomeRecord | None = None,
) -> SubmissionOwnerSendJournalSnapshot:
    assert operation.claim is not None
    return SubmissionOwnerSendJournalSnapshot(
        owner_dispatch_ref=operation.claim.owner_dispatch_ref,
        owner_authorization_evidence_sha256=operation.claim.owner_wal_evidence_sha256,
        boundary=boundary,
        outcome=outcome,
    )


class MutableJournalReader:
    def __init__(self, snapshot: SubmissionOwnerSendJournalSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def load_send_journal(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerSendJournalSnapshot:
        self.calls.append(owner_dispatch_ref)
        return self.snapshot


class FixedClock:
    def __init__(self, current: datetime = NOW + timedelta(seconds=3)) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True)
class SqlStep:
    query: str
    one: Sequence[object] | None = None
    many: tuple[Sequence[object], ...] = ()


class ScriptedCursor:
    def __init__(self, step: SqlStep) -> None:
        self._step = step

    def fetchone(self) -> Sequence[object] | None:
        return self._step.one

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self._step.many


class ScriptedTransaction:
    def __init__(self, connection: ScriptedConnection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        self._connection.transaction_entered += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        del exc_value, traceback
        if exc_type is None:
            self._connection.committed += 1
        else:
            self._connection.rolled_back += 1
        return False


class ScriptedConnection:
    def __init__(self, steps: Sequence[SqlStep]) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []
        self.transaction_entered = 0
        self.committed = 0
        self.rolled_back = 0

    def __enter__(self) -> ScriptedConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        del exc_type, exc_value, traceback
        return False

    def transaction(self) -> ScriptedTransaction:
        return ScriptedTransaction(self)

    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> ScriptedCursor:
        if not self.steps:
            raise AssertionError(f"unexpected SQL: {query}")
        step = self.steps.pop(0)
        assert query == step.query
        self.calls.append((query, params))
        return ScriptedCursor(step)


class ScriptedConnectionFactory:
    def __init__(self, steps: Sequence[SqlStep]) -> None:
        self.connection = ScriptedConnection(steps)

    def __call__(self) -> ScriptedConnection:
        return self.connection


class InMemoryAcceptedProofStore:
    def __init__(self) -> None:
        self.requests: list[OwnerNotSentProofRequest] = []
        self.accepted_by_digest: dict[str, AcceptedOwnerNotSentProof] = {}

    def accept_owner_not_sent(
        self,
        request: OwnerNotSentProofRequest,
    ) -> AcceptedOwnerNotSentProof:
        self.requests.append(request)
        digest = owner_not_sent_proof_request_digest(request)
        accepted = self.accepted_by_digest.get(digest)
        if accepted is None:
            proof_id = UUID(digest[:32])
            accepted = AcceptedOwnerNotSentProof(
                proof_id=proof_id,
                proof_pub_id=f"crp_{proof_id.hex[:26]}",
                request=request,
                request_sha256=digest,
                owner_gateway_revision=request.owner_gateway_revision,
                owner_evidence_ref=request.owner_evidence_ref,
                evidence_sha256=request.evidence_sha256,
                terminated_lease_count=1,
                terminated_lease_set_sha256=_hash(f"terminated-leases-{digest}"),
                reason_code=reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
                accepted_at=NOW + timedelta(seconds=4),
            )
            self.accepted_by_digest[digest] = accepted
        return accepted


class FixedAcceptedProofStore:
    def __init__(self, response: AcceptedOwnerNotSentProof) -> None:
        self.response = response

    def accept_owner_not_sent(
        self,
        request: OwnerNotSentProofRequest,
    ) -> AcceptedOwnerNotSentProof:
        del request
        return self.response


def _gateway(
    operation: SubmissionOperationTruth,
    reader: MutableJournalReader,
    *,
    proof_store: AcceptedOwnerNotSentProofStore | None = None,
    clock: FixedClock | None = None,
) -> OwnerWalReconciliationGateway:
    return OwnerWalReconciliationGateway(
        admission=_admission(operation),
        journal_reader=reader,
        clock=clock or FixedClock(),
        not_sent_proof_store=proof_store,
    )


def _reconciliation_command(
    operation: SubmissionOperationTruth,
    evidence: ReconciliationEvidence,
) -> SendingReconciliationCommand:
    assert operation.claim is not None
    claim = operation.claim
    return SendingReconciliationCommand(
        operation=operation_ref(operation.identity),
        expected_state_version=operation.state_version,
        claim_pub_id=claim.claim_pub_id,
        owner_handle=claim.owner_handle,
        authority_sha256=claim.authority_sha256,
        dispatch_key=claim.dispatch_key,
        owner_dispatch_ref=claim.owner_dispatch_ref,
        owner_wal_evidence_sha256=claim.owner_wal_evidence_sha256,
        durable_evidence_ref=evidence.durable_evidence_ref,
        durable_evidence_sha256=evidence.durable_evidence_sha256,
        observed_at=evidence.observed_at,
    )


DB_OPERATION_ID = UUID("10000000-0000-0000-0000-000000000001")
DB_DISPATCH_ID = UUID("10000000-0000-0000-0000-000000000002")
DB_GRANT_ID = UUID("10000000-0000-0000-0000-000000000003")
DB_LEASE_ID = UUID("10000000-0000-0000-0000-000000000004")
DB_PROOF_ID = UUID("10000000-0000-0000-0000-000000000005")


def _postgres_request() -> tuple[SubmissionOperationTruth, OwnerNotSentProofRequest]:
    operation = _sending_operation()
    admission = _admission(operation)
    snapshot = _journal(operation)
    digest = owner_reconciliation_evidence_digest(admission=admission, journal=snapshot)
    evidence = ReconciliationEvidence(
        durable_evidence_ref=owner_reconciliation_evidence_ref(digest),
        durable_evidence_sha256=digest,
        observed_at=NOW + timedelta(seconds=3),
    )
    return operation, build_owner_not_sent_proof_request(
        admission=admission,
        evidence=evidence,
    )


def _authority_row(
    request: OwnerNotSentProofRequest,
    *,
    updates: Mapping[int, object] | None = None,
) -> tuple[object, ...]:
    claim = request.claim
    values: list[object] = [
        DB_OPERATION_ID,
        request.operation.operation_pub_id,
        request.operation.operation_key,
        request.operation.generation,
        "SENDING",
        request.expected_state_version,
        "not_required",
        request.operation.request_manifest_sha256,
        sha256(request.operation.provider_idempotency_key.encode()).hexdigest(),
        DB_DISPATCH_ID,
        "sdp_10000000000000000000000000",
        claim.claim_pub_id,
        claim.owner_handle,
        claim.authority_sha256,
        claim.dispatch_key,
        claim.owner_dispatch_ref,
        claim.owner_wal_evidence_sha256,
        claim.fence_set_sha256,
        request.owner_gateway_revision,
        "owner_lost",
        "in_progress",
        3,
        request.reconciliation_claim_ref,
        reconciliation_module.owner_reconciliation_claim_digest(
            operation=request.operation,
            reconciliation_claim_ref=request.reconciliation_claim_ref,
        ),
        claim.claimed_at,
        DB_GRANT_ID,
        claim.grant_pub_id,
        claim.grant_revision,
        request.owner_gateway_revision,
    ]
    for index, value in (updates or {}).items():
        values[index] = value
    return tuple(values)


def _proof_row(
    request: OwnerNotSentProofRequest,
    *,
    updates: Mapping[int, object] | None = None,
) -> tuple[object, ...]:
    values: list[object] = [
        DB_PROOF_ID,
        f"crp_{DB_PROOF_ID.hex[:26]}",
        request.tenant_id,
        request.project_id,
        DB_OPERATION_ID,
        f"{DB_OPERATION_ID}:{request.evidence_sha256}",
        "owner_proved_not_sent",
        request.owner_gateway_revision,
        request.owner_evidence_ref,
        request.evidence_sha256,
        1,
        _hash("postgres-terminated-lease-set"),
        "accepted",
        reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
        "geo_worker",
        NOW + timedelta(seconds=4),
    ]
    for index, value in (updates or {}).items():
        values[index] = value
    return tuple(values)


def _postgres_steps(
    request: OwnerNotSentProofRequest,
    *,
    authority_updates: Mapping[int, object] | None = None,
    proof_updates: Mapping[int, object] | None = None,
) -> tuple[SqlStep, ...]:
    return (
        SqlStep(reconciliation_module.SET_OWNER_NOT_SENT_TIMEZONE_SQL),
        SqlStep(reconciliation_module.SET_OWNER_NOT_SENT_TENANT_SQL),
        SqlStep(
            reconciliation_module.LOCK_OWNER_NOT_SENT_AUTHORITY_SQL,
            many=(_authority_row(request, updates=authority_updates),),
        ),
        SqlStep(reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL, one=(DB_PROOF_ID,)),
        SqlStep(
            reconciliation_module.LOCK_OWNER_NOT_SENT_AUTHORITY_SQL,
            many=(_authority_row(request, updates=authority_updates),),
        ),
        SqlStep(
            reconciliation_module.LOCK_OWNER_NOT_SENT_LEASES_SQL,
            many=((DB_LEASE_ID, "released", True),),
        ),
        SqlStep(
            reconciliation_module.LOCK_OWNER_NOT_SENT_CAPACITY_SQL,
            many=(("credential", 0, DB_LEASE_ID, True),),
        ),
        SqlStep(
            reconciliation_module.LOAD_OWNER_NOT_SENT_FENCE_HASH_SQL,
            one=(request.terminated_fence_set_sha256,),
        ),
        SqlStep(
            reconciliation_module.LOAD_ACCEPTED_OWNER_NOT_SENT_PROOF_SQL,
            one=_proof_row(request, updates=proof_updates),
        ),
    )


def test_admission_requires_exact_exclusive_dead_owner_repository_proof() -> None:
    operation = _sending_operation()
    inactive = _repository_claim(
        operation,
        acquired=False,
        owner_session_terminated=False,
    )
    with pytest.raises(ValidationError, match="dead_owner_reconciliation_not_acquired"):
        DeadOwnerReconciliationAdmission(
            repository_claim=inactive,
            sending_operation=operation,
            terminated_fence_set_sha256=operation.claim.fence_set_sha256,  # type: ignore[union-attr]
            owner_gateway_revision=OWNER_GATEWAY_REVISION,
        )

    forged_live_claim = DurableReconciliationClaim.model_construct(
        operation=operation_ref(operation.identity),
        reconciliation_claim_ref="reconciliation-claim-owner-dead",
        acquired=True,
        owner_session_terminated=False,
    )
    with pytest.raises(ValidationError, match="live_owner_cannot_be_reconciled"):
        DeadOwnerReconciliationAdmission(
            repository_claim=forged_live_claim,
            sending_operation=operation,
            terminated_fence_set_sha256=operation.claim.fence_set_sha256,  # type: ignore[union-attr]
            owner_gateway_revision=OWNER_GATEWAY_REVISION,
        )

    with pytest.raises(ValidationError, match="dead_owner_terminated_fence_set_drift"):
        DeadOwnerReconciliationAdmission(
            repository_claim=_repository_claim(operation),
            sending_operation=operation,
            terminated_fence_set_sha256=_hash("different-fence-set"),
            owner_gateway_revision=OWNER_GATEWAY_REVISION,
        )


def test_postgres_proof_adapter_calls_locking_function_then_deep_reads_crp() -> None:
    _, request = _postgres_request()
    factory = ScriptedConnectionFactory(_postgres_steps(request))
    store = reconciliation_module.PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=factory,
        scope=RepositoryScope(tenant_id=TENANT_ID, project_id=PROJECT_ID),
    )

    accepted = store.accept_owner_not_sent(request)

    assert accepted.proof_id == DB_PROOF_ID
    assert accepted.proof_pub_id == f"crp_{DB_PROOF_ID.hex[:26]}"
    assert accepted.owner_gateway_revision == request.owner_gateway_revision
    assert accepted.owner_evidence_ref == request.owner_evidence_ref
    assert accepted.evidence_sha256 == request.evidence_sha256
    assert accepted.terminated_lease_set_sha256 != request.terminated_fence_set_sha256
    assert factory.connection.steps == []
    assert factory.connection.transaction_entered == 1
    assert factory.connection.committed == 1
    assert factory.connection.rolled_back == 0

    calls = dict(factory.connection.calls)
    assert calls[reconciliation_module.LOCK_OWNER_NOT_SENT_AUTHORITY_SQL] == {
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "operation_pub_id": request.operation.operation_pub_id,
        "operation_key": request.operation.operation_key,
        "operation_generation": request.operation.generation,
    }
    assert calls[reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL] == {
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "operation_id": DB_OPERATION_ID,
        "owner_gateway_revision": request.owner_gateway_revision,
        "owner_evidence_ref": request.owner_evidence_ref,
        "evidence_hash": request.evidence_sha256,
        "reason_code": reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
    }
    assert "FOR UPDATE OF operation, dispatch" not in (
        reconciliation_module.LOCK_OWNER_NOT_SENT_AUTHORITY_SQL
    )
    assert "record_collection_not_sent_proof_v2" in (
        reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL
    )
    assert "INSERT INTO" not in reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL


def test_postgres_admission_projection_sources_revision_from_locked_dispatch() -> None:
    operation, request = _postgres_request()
    steps = _postgres_steps(request)[:3]
    factory = ScriptedConnectionFactory(steps)
    store = reconciliation_module.PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=factory,
        scope=RepositoryScope(tenant_id=TENANT_ID, project_id=PROJECT_ID),
    )

    admission = store.project_admission(
        repository_claim=_repository_claim(operation),
        sending_operation=operation,
    )

    assert admission.owner_gateway_revision == OWNER_GATEWAY_REVISION
    assert admission.repository_claim.reconciliation_claim_ref == request.reconciliation_claim_ref
    assert factory.connection.committed == 1


@pytest.mark.parametrize(
    ("authority_updates", "error_code"),
    (
        ({12: "different-owner"}, "postgres_owner_reconciliation_dispatch_claim_drift"),
        ({20: "pending"}, "postgres_owner_reconciliation_exclusive_claim_drift"),
        ({23: _hash("wrong-claim")}, "postgres_owner_reconciliation_exclusive_claim_drift"),
        ({18: "owner-gateway-v2"}, "postgres_owner_reconciliation_gateway_revision_drift"),
        ({17: _hash("wrong-fence")}, "postgres_owner_reconciliation_dispatch_claim_drift"),
    ),
)
def test_postgres_authority_drift_rolls_back_before_proof_function(
    authority_updates: Mapping[int, object],
    error_code: str,
) -> None:
    _, request = _postgres_request()
    factory = ScriptedConnectionFactory(
        _postgres_steps(request, authority_updates=authority_updates)
    )
    store = reconciliation_module.PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=factory,
        scope=RepositoryScope(tenant_id=TENANT_ID, project_id=PROJECT_ID),
    )

    with pytest.raises(OwnerReconciliationError, match=error_code):
        store.accept_owner_not_sent(request)

    assert factory.connection.committed == 0
    assert factory.connection.rolled_back == 1
    assert all(
        query != reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL
        for query, _ in factory.connection.calls
    )


@pytest.mark.parametrize(
    "proof_updates",
    (
        {7: "owner-gateway-v2"},
        {8: "different-owner-evidence"},
        {13: "different-reason"},
        {1: "crp_ffffffffffffffffffffffffff"},
    ),
)
def test_postgres_existing_proof_row_drift_rolls_back_function_call(
    proof_updates: Mapping[int, object],
) -> None:
    _, request = _postgres_request()
    factory = ScriptedConnectionFactory(_postgres_steps(request, proof_updates=proof_updates))
    store = reconciliation_module.PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=factory,
        scope=RepositoryScope(tenant_id=TENANT_ID, project_id=PROJECT_ID),
    )

    with pytest.raises(OwnerReconciliationError, match="postgres_owner_not_sent_proof_"):
        store.accept_owner_not_sent(request)

    assert factory.connection.committed == 0
    assert factory.connection.rolled_back == 1
    assert any(
        query == reconciliation_module.RECORD_OWNER_NOT_SENT_PROOF_SQL
        for query, _ in factory.connection.calls
    )


def test_no_boundary_converges_to_recomputable_confirmed_not_sent_proof() -> None:
    operation = _sending_operation()
    snapshot = _journal(operation)
    reader = MutableJournalReader(snapshot)
    proof_store = InMemoryAcceptedProofStore()
    gateway = _gateway(operation, reader, proof_store=proof_store)

    evidence = gateway.observe_sending(operation)
    expected_digest = owner_reconciliation_evidence_digest(
        admission=gateway.admission,
        journal=snapshot,
    )
    assert evidence.durable_evidence_sha256 == expected_digest
    assert evidence.durable_evidence_ref == owner_reconciliation_evidence_ref(expected_digest)

    command = _reconciliation_command(operation, evidence)
    disposition = gateway.reconcile_sending(command)
    assert disposition.send_state is SendState.CONFIRMED_NOT_SENT
    assert disposition.reason is TerminalReason.POST_CLAIM_NOT_SENT
    assert not disposition.boundary_entered
    assert disposition.terminated_fence_set_sha256 == operation.claim.fence_set_sha256  # type: ignore[union-attr]
    request = build_owner_not_sent_proof_request(
        admission=gateway.admission,
        evidence=evidence,
    )
    assert request.owner_gateway_revision == OWNER_GATEWAY_REVISION
    accepted = proof_store.accepted_by_digest[owner_not_sent_proof_request_digest(request)]
    assert disposition.non_submission_proof_ref == accepted.proof_pub_id
    assert gateway.reconcile_sending(command) == disposition
    assert len(proof_store.accepted_by_digest) == 1

    restarted = _gateway(
        operation,
        reader,
        proof_store=proof_store,
        clock=FixedClock(NOW + timedelta(seconds=30)),
    )
    restarted_evidence = restarted.observe_sending(operation)
    assert restarted_evidence.observed_at != evidence.observed_at
    assert restarted_evidence.durable_evidence_ref == evidence.durable_evidence_ref
    assert restarted_evidence.durable_evidence_sha256 == evidence.durable_evidence_sha256
    restarted_disposition = restarted.reconcile_sending(
        _reconciliation_command(operation, restarted_evidence)
    )
    assert restarted_disposition.non_submission_proof_ref == accepted.proof_pub_id
    assert len(proof_store.accepted_by_digest) == 1
    assert all(
        owner_dispatch_ref == operation.claim.owner_dispatch_ref  # type: ignore[union-attr]
        for owner_dispatch_ref in reader.calls
    )


def test_no_boundary_without_accepted_database_proof_fails_closed() -> None:
    operation = _sending_operation()
    reader = MutableJournalReader(_journal(operation))
    gateway = _gateway(operation, reader)
    evidence = gateway.observe_sending(operation)

    with pytest.raises(
        OwnerReconciliationError,
        match="accepted_owner_not_sent_proof_store_unavailable",
    ):
        gateway.reconcile_sending(_reconciliation_command(operation, evidence))


def test_forged_or_cross_claim_accepted_proof_reference_is_rejected() -> None:
    operation = _sending_operation()
    snapshot = _journal(operation)
    reader = MutableJournalReader(snapshot)
    admission = _admission(operation)
    evidence_digest = owner_reconciliation_evidence_digest(
        admission=admission,
        journal=snapshot,
    )
    evidence = ReconciliationEvidence(
        durable_evidence_ref=owner_reconciliation_evidence_ref(evidence_digest),
        durable_evidence_sha256=evidence_digest,
        observed_at=NOW + timedelta(seconds=3),
    )
    request = build_owner_not_sent_proof_request(admission=admission, evidence=evidence)
    request_digest = owner_not_sent_proof_request_digest(request)
    forged_reference = AcceptedOwnerNotSentProof.model_construct(
        proof_id=UUID(request_digest[:32]),
        proof_pub_id=f"nsp_{request_digest[:26]}",
        request=request,
        request_sha256=request_digest,
        owner_gateway_revision=request.owner_gateway_revision,
        owner_evidence_ref=request.owner_evidence_ref,
        evidence_sha256=request.evidence_sha256,
        terminated_lease_count=1,
        terminated_lease_set_sha256=_hash("forged-lease-set"),
        reason_code=reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
        accepted_at=NOW + timedelta(seconds=4),
    )
    forged_gateway = OwnerWalReconciliationGateway(
        admission=admission,
        journal_reader=reader,
        clock=FixedClock(),
        not_sent_proof_store=FixedAcceptedProofStore(forged_reference),
    )
    with pytest.raises(
        OwnerReconciliationError,
        match="accepted_owner_not_sent_proof_invalid",
    ):
        forged_gateway.reconcile_sending(_reconciliation_command(operation, evidence))

    other_request = request.model_copy(
        update={"reconciliation_claim_ref": "different-reconciliation-claim"}
    )
    other_digest = owner_not_sent_proof_request_digest(other_request)
    other_proof_id = UUID(other_digest[:32])
    cross_claim_proof = AcceptedOwnerNotSentProof(
        proof_id=other_proof_id,
        proof_pub_id=f"crp_{other_proof_id.hex[:26]}",
        request=other_request,
        request_sha256=other_digest,
        owner_gateway_revision=other_request.owner_gateway_revision,
        owner_evidence_ref=other_request.owner_evidence_ref,
        evidence_sha256=other_request.evidence_sha256,
        terminated_lease_count=1,
        terminated_lease_set_sha256=_hash("cross-claim-lease-set"),
        reason_code=reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
        accepted_at=NOW + timedelta(seconds=4),
    )
    cross_claim_gateway = OwnerWalReconciliationGateway(
        admission=admission,
        journal_reader=reader,
        clock=FixedClock(),
        not_sent_proof_store=FixedAcceptedProofStore(cross_claim_proof),
    )
    with pytest.raises(
        OwnerReconciliationError,
        match="accepted_owner_not_sent_proof_drift",
    ):
        cross_claim_gateway.reconcile_sending(_reconciliation_command(operation, evidence))

    wrong_revision_request = request.model_copy(
        update={"owner_gateway_revision": "owner-gateway-v2"}
    )
    wrong_revision_digest = owner_not_sent_proof_request_digest(wrong_revision_request)
    wrong_revision_proof_id = UUID(wrong_revision_digest[:32])
    wrong_revision_proof = AcceptedOwnerNotSentProof(
        proof_id=wrong_revision_proof_id,
        proof_pub_id=f"crp_{wrong_revision_proof_id.hex[:26]}",
        request=wrong_revision_request,
        request_sha256=wrong_revision_digest,
        owner_gateway_revision=wrong_revision_request.owner_gateway_revision,
        owner_evidence_ref=wrong_revision_request.owner_evidence_ref,
        evidence_sha256=wrong_revision_request.evidence_sha256,
        terminated_lease_count=1,
        terminated_lease_set_sha256=_hash("wrong-revision-lease-set"),
        reason_code=reconciliation_module.POSTGRES_OWNER_NOT_SENT_REASON,
        accepted_at=NOW + timedelta(seconds=4),
    )
    wrong_revision_gateway = OwnerWalReconciliationGateway(
        admission=admission,
        journal_reader=reader,
        clock=FixedClock(),
        not_sent_proof_store=FixedAcceptedProofStore(wrong_revision_proof),
    )
    with pytest.raises(
        OwnerReconciliationError,
        match="accepted_owner_not_sent_proof_drift",
    ):
        wrong_revision_gateway.reconcile_sending(_reconciliation_command(operation, evidence))


def test_gateway_revision_is_bound_to_evidence_proof_and_owner_wal_boundary() -> None:
    operation = _sending_operation()
    snapshot = _journal(operation)
    repository_claim = _repository_claim(operation)
    revision_v1 = DeadOwnerReconciliationAdmission.from_repository_claim(
        repository_claim=repository_claim,
        sending_operation=operation,
        owner_gateway_revision=OWNER_GATEWAY_REVISION,
    )
    revision_v2 = DeadOwnerReconciliationAdmission.from_repository_claim(
        repository_claim=repository_claim,
        sending_operation=operation,
        owner_gateway_revision="owner-gateway-v2",
    )
    digest_v1 = owner_reconciliation_evidence_digest(
        admission=revision_v1,
        journal=snapshot,
    )
    digest_v2 = owner_reconciliation_evidence_digest(
        admission=revision_v2,
        journal=snapshot,
    )
    assert digest_v1 != digest_v2

    evidence_v1 = ReconciliationEvidence(
        durable_evidence_ref=owner_reconciliation_evidence_ref(digest_v1),
        durable_evidence_sha256=digest_v1,
        observed_at=NOW + timedelta(seconds=3),
    )
    request_v1 = build_owner_not_sent_proof_request(
        admission=revision_v1,
        evidence=evidence_v1,
    )
    assert request_v1.owner_gateway_revision == OWNER_GATEWAY_REVISION

    mismatched_boundary = _boundary(
        operation,
        owner_gateway_revision="owner-gateway-v2",
    )
    gateway = OwnerWalReconciliationGateway(
        admission=revision_v1,
        journal_reader=MutableJournalReader(_journal(operation, boundary=mismatched_boundary)),
        clock=FixedClock(),
    )
    with pytest.raises(
        OwnerReconciliationError,
        match="owner_reconciliation_gateway_revision_drift",
    ):
        gateway.observe_sending(operation)


def test_boundary_without_outcome_is_conservatively_send_unknown() -> None:
    operation = _sending_operation()
    boundary = _boundary(operation)
    reader = MutableJournalReader(_journal(operation, boundary=boundary))
    gateway = _gateway(operation, reader)

    evidence = gateway.observe_sending(operation)
    disposition = gateway.reconcile_sending(_reconciliation_command(operation, evidence))

    assert disposition.send_state is SendState.SEND_UNKNOWN
    assert disposition.reason is TerminalReason.SEND_UNKNOWN
    assert disposition.boundary_entered
    assert disposition.provider_submission_ref is None
    assert disposition.non_submission_proof_ref is None
    assert disposition.terminated_fence_set_sha256 is None


@pytest.mark.parametrize(
    ("source", "expected_provider_ref", "requires_accepted_proof"),
    (
        (
            SubmitDisposition(
                send_state=SendState.CONFIRMED_SENT,
                reason=TerminalReason.SUBMITTED,
                boundary_entered=True,
                evidence_ref="provider-evidence",
                evidence_sha256=_hash("provider-evidence"),
                provider_submission_ref="provider-submission",
                resolved_at=NOW + timedelta(seconds=2),
            ),
            "provider-submission",
            False,
        ),
        (
            SubmitDisposition(
                send_state=SendState.SEND_UNKNOWN,
                reason=TerminalReason.SEND_UNKNOWN,
                boundary_entered=True,
                evidence_ref="provider-unknown-evidence",
                evidence_sha256=_hash("provider-unknown-evidence"),
                resolved_at=NOW + timedelta(seconds=2),
            ),
            None,
            False,
        ),
        (
            SubmitDisposition(
                send_state=SendState.CONFIRMED_NOT_SENT,
                reason=TerminalReason.POST_CLAIM_NOT_SENT,
                boundary_entered=False,
                evidence_ref="provider-not-sent-evidence",
                evidence_sha256=_hash("provider-not-sent-evidence"),
                non_submission_proof_ref="owner-original-not-sent-proof",
                terminated_fence_set_sha256=_hash("fence-set"),
                resolved_at=NOW + timedelta(seconds=2),
            ),
            None,
            True,
        ),
    ),
)
def test_durable_outcome_semantics_win_over_reconciliation_inference(
    source: SubmitDisposition,
    expected_provider_ref: str | None,
    requires_accepted_proof: bool,
) -> None:
    operation = _sending_operation()
    boundary = _boundary(operation)
    outcome = build_submission_owner_send_outcome_record(boundary, source)
    reader = MutableJournalReader(_journal(operation, boundary=boundary, outcome=outcome))
    proof_store = InMemoryAcceptedProofStore()
    gateway = _gateway(operation, reader, proof_store=proof_store)

    evidence = gateway.observe_sending(operation)
    disposition = gateway.reconcile_sending(_reconciliation_command(operation, evidence))

    assert disposition.send_state is source.send_state
    assert disposition.reason is source.reason
    assert disposition.boundary_entered is source.boundary_entered
    assert disposition.provider_submission_ref == expected_provider_ref
    assert disposition.terminated_fence_set_sha256 == source.terminated_fence_set_sha256
    assert disposition.evidence_ref == evidence.durable_evidence_ref
    assert disposition.evidence_sha256 == evidence.durable_evidence_sha256
    if requires_accepted_proof:
        assert disposition.non_submission_proof_ref is not None
        assert disposition.non_submission_proof_ref.startswith("crp_")
        assert disposition.non_submission_proof_ref != source.non_submission_proof_ref
        assert len(proof_store.accepted_by_digest) == 1
    else:
        assert disposition.non_submission_proof_ref is None
        assert proof_store.accepted_by_digest == {}


def test_scope_command_and_journal_replay_drift_fail_closed() -> None:
    operation = _sending_operation()
    empty_reader = MutableJournalReader(_journal(operation))
    gateway = _gateway(operation, empty_reader)

    other_scope_operation = _sending_operation(
        identity=_identity(tenant_id=OTHER_TENANT_ID),
    )
    with pytest.raises(OwnerReconciliationError, match="owner_reconciliation_scope_drift"):
        gateway.observe_sending(other_scope_operation)

    evidence = gateway.observe_sending(operation)
    drifted_command = _reconciliation_command(operation, evidence).model_copy(
        update={"owner_handle": "different-owner"}
    )
    with pytest.raises(
        OwnerReconciliationError,
        match="owner_reconciliation_command_claim_drift",
    ):
        gateway.reconcile_sending(drifted_command)

    empty_reader.snapshot = _journal(operation, boundary=_boundary(operation))
    with pytest.raises(OwnerReconciliationError, match="owner_reconciliation_evidence_drift"):
        gateway.reconcile_sending(_reconciliation_command(operation, evidence))


def test_cross_scope_boundary_and_tampered_outcome_fail_closed() -> None:
    operation = _sending_operation()
    cross_scope_boundary = _boundary(operation, tenant_id=OTHER_TENANT_ID)
    reader = MutableJournalReader(_journal(operation, boundary=cross_scope_boundary))
    gateway = _gateway(operation, reader)
    with pytest.raises(OwnerReconciliationError, match="owner_reconciliation_scope_drift"):
        gateway.observe_sending(operation)

    boundary = _boundary(operation)
    outcome = build_submission_owner_send_outcome_record(
        boundary,
        SubmitDisposition(
            send_state=SendState.SEND_UNKNOWN,
            reason=TerminalReason.SEND_UNKNOWN,
            boundary_entered=True,
            evidence_ref="unknown-evidence",
            evidence_sha256=_hash("unknown-evidence"),
            resolved_at=NOW + timedelta(seconds=2),
        ),
    )
    tampered = outcome.model_copy(update={"evidence_sha256": _hash("tampered")})
    reader.snapshot = SubmissionOwnerSendJournalSnapshot.model_construct(
        owner_dispatch_ref=operation.claim.owner_dispatch_ref,  # type: ignore[union-attr]
        owner_authorization_evidence_sha256=(
            operation.claim.owner_wal_evidence_sha256  # type: ignore[union-attr]
        ),
        boundary=boundary,
        outcome=tampered,
    )
    with pytest.raises(OwnerReconciliationError, match="owner_reconciliation_journal_invalid"):
        gateway.observe_sending(operation)


def test_gateway_exposes_neither_submit_nor_journal_write_capability() -> None:
    operation = _sending_operation()
    reader = MutableJournalReader(_journal(operation))
    gateway = _gateway(operation, reader)

    assert not hasattr(gateway, "submit_once")
    assert not hasattr(reader, "append_send_boundary")
    assert not hasattr(reader, "append_send_outcome")
    assert "ResourceOwnerSubmitTransport" not in reconciliation_module.__dict__
    assert "AuthorizedSubmitOnceGateway" not in reconciliation_module.__dict__
