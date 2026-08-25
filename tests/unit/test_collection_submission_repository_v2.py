from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import TracebackType
from typing import Literal, Self, cast
from uuid import UUID

import pytest
from geo_platform.collection.quota_v2 import (
    ConnectionProtocol,
    QuotaBlocker,
    ReserveQuotaRequest,
    ReserveQuotaResult,
)
from geo_platform.collection.submission_repository_v2 import (
    S10_FUNCTION_CONTRACTS,
    PostgresSubmissionRepository,
    QuotaReservationBlocked,
    QuotaReserver,
    RepositoryConnection,
    RepositoryScope,
    SubmissionRepositoryError,
)
from geo_platform.collection.submission_v2 import (
    CaptureAdmissionDecision,
    DurableCaptureAttempt,
    DurableSubmissionRepository,
    PreparedSubmissionRef,
    PrepareWorkItem,
    ResolvedSubmissionContext,
    SlotOutcomeFact,
    SubmissionWorkItem,
)
from pydantic import ValidationError

from domain.collection.submission import (
    CaptureChannel,
    CaptureDataClassification,
    CaptureDisposition,
    CaptureExistingCommand,
    CaptureNormalizationDecision,
    CaptureProvenance,
    CaptureStagingRef,
    CaptureTruth,
    ImmutableCaptureLink,
    LeaseFenceRef,
    OperationIdentity,
    OperationKeyMaterial,
    OperationRef,
    OutboxEventRef,
    OwnerAuthorityRef,
    OwnerClaimCasCommand,
    OwnerClaimTruth,
    PreflightCommand,
    PreflightDecision,
    PreflightObservation,
    PrepareResult,
    PrepareSubmissionCommand,
    QuotaTerminalEffect,
    RequestManifest,
    SlotOutcome,
    SubmissionOperationTruth,
    SurfaceProductRef,
    TerminalReason,
    TerminalSubmissionTransition,
    TerminalSubmissionTruth,
    WorkflowOperationInput,
    apply_capture_disposition,
    apply_preflight_not_sent,
    authority_digest,
    begin_capture,
    canonical_json,
    capture_command_digest,
    deterministic_capture_staging_intent,
    deterministic_operation_key,
    deterministic_outbox_key,
    deterministic_provider_idempotency_key,
    initial_capture_truth,
    lease_fence_set_digest,
    link_immutable_capture,
    normalize_capture,
    operation_ref,
    prepare_submission,
    request_manifest_digest,
    verify_preflight,
)
from domain.collection.surface import CaptureState, CollectionSurface, QuotaScopeKind, SendState

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
OPERATION_ID = UUID("00000000-0000-0000-0000-000000000003")
BINDING_ID = UUID("00000000-0000-0000-0000-000000000004")
REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000005")
RESERVATION_ID = UUID("00000000-0000-0000-0000-000000000006")
MANIFEST_ID = UUID("00000000-0000-0000-0000-000000000007")
CAPTURE_TRUTH_ID = UUID("00000000-0000-0000-0000-000000000008")
OUTBOX_ID = UUID("00000000-0000-0000-0000-000000000009")


@dataclass
class FakeCursor:
    one: Sequence[object] | None = None
    many: Sequence[Sequence[object]] = ()

    def fetchone(self) -> Sequence[object] | None:
        return self.one

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self.many


@dataclass
class FakeTransaction:
    connection: FakeConnection

    def __enter__(self) -> None:
        self.connection.transaction_depth += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_value, traceback
        self.connection.transaction_depth -= 1
        if exc_type is None:
            self.connection.commits += 1
        else:
            self.connection.rollbacks += 1
            self.connection.rollback_exception_names.append(exc_type.__name__)
        return False


@dataclass
class FakeConnection:
    responder: Callable[[str, Mapping[str, object] | None], FakeCursor]
    statements: list[tuple[str, Mapping[str, object] | None]] = field(default_factory=list)
    transaction_depth: int = 0
    commits: int = 0
    rollbacks: int = 0
    rollback_exception_names: list[str] = field(default_factory=list)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc_value, traceback
        return False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> FakeCursor:
        assert self.transaction_depth > 0
        self.statements.append((query, params))
        if query == "SET LOCAL TIME ZONE 'UTC'":
            return FakeCursor(("ok",))
        return self.responder(query, params)


def _identity() -> OperationIdentity:
    target = SurfaceProductRef(
        platform="doubao",
        collection_surface=CollectionSurface.CONSUMER_WEB,
        product_variant="web-chat",
        target_key=(
            "collection-target-v1|platform=doubao|collection_surface=consumer_web|"
            "product_variant=web-chat"
        ),
    )
    material = OperationKeyMaterial(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        campaign_pub_id="campaign-1",
        slot_pub_id="slot-1",
        target_key=target.target_key,
        leg_key="leg-1",
        logical_item_key="slot-key-1",
        generation=1,
        operation_policy_revision="operation-policy-v1",
    )
    manifest = RequestManifest(
        request_protocol_version="provider-request-v1",
        request_schema_revision="adapter-request-v1",
        request_payload_ref="request-content-1",
        request_payload_sha256=sha256(b"request").hexdigest(),
    )
    operation_key = deterministic_operation_key(material)
    return OperationIdentity(
        material=material,
        surface_product=target,
        operation_pub_id="operation-1",
        operation_key=operation_key,
        request_manifest=manifest,
        request_manifest_sha256=request_manifest_digest(manifest),
        provider_idempotency_key=deterministic_provider_idempotency_key(operation_key),
    )


def _prepared() -> tuple[PrepareWorkItem, PrepareResult]:
    identity = _identity()
    command = PrepareSubmissionCommand(identity=identity, prepared_at=NOW)
    result = prepare_submission(command)
    work = PrepareWorkItem(
        workflow=WorkflowOperationInput(
            operation=operation_ref(identity),
            expected_state_version=1,
        ),
        frozen_slot_ref=identity.material.slot_pub_id,
        binding_revision_pub_id="binding-1",
        quota_registry_revision="quota-registry-v1",
        request_manifest_ref=identity.request_manifest.request_payload_ref,
    )
    return work, result


def _operation_row(identity: OperationIdentity) -> tuple[object, ...]:
    manifest = identity.request_manifest
    return (
        OPERATION_ID,
        identity.operation_pub_id,
        identity.operation_key,
        identity.material.generation,
        identity.material.operation_policy_revision,
        "NOT_SENT",
        1,
        NOW,
        identity.material.campaign_pub_id,
        identity.material.slot_pub_id,
        identity.material.target_key,
        identity.material.leg_key,
        identity.material.logical_item_key,
        identity.surface_product.platform,
        identity.surface_product.collection_surface.value,
        identity.surface_product.product_variant,
        manifest.request_protocol_version,
        manifest.request_schema_revision,
        manifest.request_payload_ref,
        manifest.request_payload_sha256,
        identity.request_manifest_sha256,
        sha256(identity.provider_idempotency_key.encode()).hexdigest(),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _authority() -> OwnerAuthorityRef:
    fence = LeaseFenceRef(
        lease_pub_id="lease-1",
        binding_resource_pub_id="resource-1",
        resource_role="browser_owner",
        owner_handle="owner-1",
        generation=1,
        acquired_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    return OwnerAuthorityRef(
        grant_pub_id="grant-1",
        grant_revision=3,
        binding_revision_pub_id="binding-1",
        owner_handle="owner-1",
        checked_at=NOW,
        valid_until=NOW + timedelta(minutes=10),
        lease_fences=(fence,),
        fence_set_sha256=lease_fence_set_digest((fence,)),
    )


def _claim(identity: OperationIdentity) -> OwnerClaimTruth:
    del identity
    authority = _authority()
    return OwnerClaimTruth(
        claim_pub_id="claim-1",
        owner_handle=authority.owner_handle,
        grant_pub_id=authority.grant_pub_id,
        grant_revision=authority.grant_revision,
        authority_sha256=authority_digest(authority),
        fence_set_sha256=authority.fence_set_sha256,
        dispatch_key="dispatch-1",
        owner_dispatch_ref="owner-dispatch-1",
        owner_wal_evidence_sha256=sha256(b"wal").hexdigest(),
        claimed_at=NOW + timedelta(minutes=1),
    )


def _sending_operation(identity: OperationIdentity) -> SubmissionOperationTruth:
    return SubmissionOperationTruth(
        identity=identity,
        send_state=SendState.SENDING,
        state_version=2,
        prepared_at=NOW,
        claim=_claim(identity),
    )


def _sending_operation_row(identity: OperationIdentity) -> tuple[object, ...]:
    row = list(_operation_row(identity))
    claim = _claim(identity)
    row[5] = "SENDING"
    row[6] = 2
    row[22] = claim.claim_pub_id
    row[23] = claim.owner_handle
    row[24] = claim.grant_pub_id
    row[25] = claim.grant_revision
    row[26] = claim.authority_sha256
    row[27] = claim.fence_set_sha256
    row[28] = claim.dispatch_key
    row[29] = claim.owner_dispatch_ref
    row[30] = claim.owner_wal_evidence_sha256
    row[31] = claim.claimed_at
    return tuple(row)


def _preflight_terminal_operation_row(
    identity: OperationIdentity,
    transition: TerminalSubmissionTransition,
    *,
    persisted_fence_hash: str,
) -> tuple[object, ...]:
    terminal = transition.operation.terminal
    assert terminal is not None
    row = list(_operation_row(identity))
    row[5] = terminal.send_state.value
    row[6] = transition.operation.state_version
    row[32] = terminal.reason.value
    row[33] = terminal.evidence_ref
    row[34] = terminal.evidence_sha256
    row[35] = None
    row[36] = terminal.resolved_at
    row[37] = None
    row[38] = persisted_fence_hash
    return tuple(row)


def _terminal_operation_row(operation: SubmissionOperationTruth) -> tuple[object, ...]:
    terminal = operation.terminal
    assert operation.claim is not None
    assert terminal is not None
    row = list(_sending_operation_row(operation.identity))
    row[5] = operation.send_state.value
    row[6] = operation.state_version
    row[32] = terminal.reason.value
    row[33] = terminal.evidence_ref
    row[34] = terminal.evidence_sha256
    row[35] = terminal.non_submission_proof_ref
    row[36] = terminal.resolved_at
    row[37] = terminal.provider_submission_ref
    row[38] = terminal.terminated_fence_set_sha256
    return tuple(row)


def _terminal_operation(identity: OperationIdentity) -> SubmissionOperationTruth:
    return SubmissionOperationTruth(
        identity=identity,
        send_state=SendState.CONFIRMED_SENT,
        state_version=3,
        prepared_at=NOW,
        claim=_claim(identity),
        terminal=TerminalSubmissionTruth(
            send_state=SendState.CONFIRMED_SENT,
            reason=TerminalReason.SUBMITTED,
            boundary_entered=True,
            evidence_ref="submission-evidence-1",
            evidence_sha256=sha256(b"submission-evidence").hexdigest(),
            resolved_at=NOW + timedelta(minutes=2),
            provider_submission_ref="provider-submission-1",
        ),
    )


def _submission_context(identity: OperationIdentity) -> ResolvedSubmissionContext:
    return ResolvedSubmissionContext(
        prepare=PrepareSubmissionCommand(identity=identity, prepared_at=NOW),
        authority=_authority(),
        owner_dispatch_ref="owner-dispatch-1",
        owner_wal_evidence_sha256=sha256(b"wal").hexdigest(),
        capture_policy_revision="capture-policy-v1",
    )


def _capture_command(capture: CaptureTruth) -> CaptureExistingCommand:
    authority = _authority()
    return CaptureExistingCommand(
        operation=capture.operation,
        source_send_state=capture.source_send_state,
        expected_capture_version=capture.state_version,
        attempt_ref="capture-attempt-1",
        staging_intent=deterministic_capture_staging_intent(
            operation=capture.operation,
            attempt_ref="capture-attempt-1",
        ),
        capture_policy_revision="capture-policy-v1",
        requested_surface_product=capture.expected_surface_product,
        authority=authority,
        authority_sha256=authority_digest(authority),
        requested_at=NOW + timedelta(minutes=3),
    )


def _raw_capture(identity: OperationIdentity) -> CaptureDisposition:
    return CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref="capture-attempt-1",
        evidence_ref="capture-evidence-1",
        evidence_sha256=sha256(b"capture-evidence").hexdigest(),
        observed_at=NOW + timedelta(minutes=4),
        observed_surface_product=identity.surface_product,
        provenance=CaptureProvenance(
            capture_channel=CaptureChannel.WEB_DOM,
            capture_protocol_revision="capture-protocol-v1",
            observed_product_version="web-product-v1",
            capture_adapter_revision="capture-adapter-v1",
            data_classification=CaptureDataClassification.CUSTOMER_PRIVATE,
            dlp_policy_revision="dlp-policy-v1",
            retention_until=NOW + timedelta(days=1),
        ),
        staging=CaptureStagingRef(
            staging_key=deterministic_capture_staging_intent(
                operation=operation_ref(identity),
                attempt_ref="capture-attempt-1",
            ).staging_key,
            object_ref=deterministic_capture_staging_intent(
                operation=operation_ref(identity),
                attempt_ref="capture-attempt-1",
            ).object_ref,
            content_sha256=sha256(b"capture-content").hexdigest(),
            byte_size=123,
            media_type="application/json",
            capture_schema_revision="capture-schema-v1",
            staged_at=NOW + timedelta(minutes=5),
        ),
    )


def _capture_row(
    capture: CaptureTruth,
    *,
    command: CaptureExistingCommand | None = None,
    raw: CaptureDisposition | None = None,
) -> tuple[object, ...]:
    identity = _identity()
    terminal = capture.capture_state in {
        CaptureState.COMPLETED,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.NOT_OBSERVABLE,
    }
    provenance = capture.provenance if terminal else None
    staging = raw.staging if raw is not None else None
    storage_state = (
        "quarantined"
        if capture.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
        else "staging"
    )
    return (
        capture.capture_state.value,
        capture.state_version,
        command.attempt_ref if command is not None else None,
        capture_command_digest(command) if command is not None else None,
        command.requested_at if command is not None else None,
        capture.updated_at if terminal else None,
        canonical_json(command) if command is not None else None,
        capture.source_send_state.value,
        NOW + timedelta(minutes=2),
        NOW,
        identity.surface_product.platform,
        identity.surface_product.collection_surface.value,
        identity.surface_product.product_variant,
        identity.surface_product.target_key,
        capture.owner_handle,
        capture.fence_set_sha256,
        staging.staging_key if staging is not None else ("capture-key-1" if terminal else None),
        staging.object_ref if staging is not None else None,
        staging.content_sha256 if staging is not None else None,
        staging.byte_size if staging is not None else None,
        staging.media_type if staging is not None else None,
        staging.capture_schema_revision if staging is not None else None,
        (
            staging.staged_at
            if staging is not None
            else (raw.observed_at if raw is not None else None)
        ),
        raw.evidence_ref if raw is not None else None,
        raw.evidence_sha256 if raw is not None else None,
        raw.observed_surface_product.platform if raw is not None else None,
        (raw.observed_surface_product.collection_surface.value if raw is not None else None),
        raw.observed_surface_product.product_variant if raw is not None else None,
        capture.capture_state.value if terminal else None,
        storage_state if terminal else None,
        provenance.capture_channel.value if provenance is not None else None,
        provenance.capture_protocol_revision if provenance is not None else None,
        provenance.observed_product_version if provenance is not None else None,
        provenance.capture_adapter_revision if provenance is not None else None,
        provenance.data_classification.value if provenance is not None else None,
        provenance.dlp_policy_revision if provenance is not None else None,
        provenance.retention_until if provenance is not None else None,
    )


def _fence_material() -> str:
    fence = _authority().lease_fences[0]
    return canonical_json(
        {
            "fences": [
                {
                    "binding_resource_pub_id": fence.binding_resource_pub_id,
                    "generation": fence.generation,
                    "lease_pub_id": fence.lease_pub_id,
                    "owner_handle": fence.owner_handle,
                    "resource_role": fence.resource_role,
                }
            ],
            "version": "lease-fence-identity-v1",
        }
    )


def _reconciliation_claim_hash(ref: OperationRef, claim_ref: str) -> str:
    return sha256(
        canonical_json(
            {
                "operation": ref.model_dump(mode="json"),
                "reconciliation_claim_ref": claim_ref,
                "version": "collection-reconciliation-claim-v1",
            }
        ).encode()
    ).hexdigest()


def _submission_work(identity: OperationIdentity) -> SubmissionWorkItem:
    return SubmissionWorkItem(
        prepared=PreparedSubmissionRef(
            workflow=WorkflowOperationInput(
                operation=operation_ref(identity),
                expected_state_version=1,
            ),
            reservation_pub_id="reservation-1",
        ),
        grant_pub_id="grant-1",
        lease_pub_ids=("lease-1",),
        cursor_ref="cursor-1",
        claim_pub_id="claim-1",
        reconciliation_claim_ref="reconciliation-claim-1",
        capture_attempt_ref="capture-attempt-1",
    )


def _unexpected_quota_reserve(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest,
) -> ReserveQuotaResult:
    del connection, request
    raise AssertionError("unexpected quota reservation")


def _repository(
    connection: FakeConnection,
    *,
    quota_reserver: QuotaReserver = _unexpected_quota_reserve,
) -> PostgresSubmissionRepository:
    return PostgresSubmissionRepository(
        scope=RepositoryScope(tenant_id=TENANT_ID, project_id=PROJECT_ID),
        connection_factory=lambda: cast(RepositoryConnection, connection),
        prepared_by_pub_id="submission-worker-v2",
        quota_reserver=quota_reserver,
    )


def _function_probe_row(params: Mapping[str, object] | None) -> tuple[object, ...]:
    assert params is not None
    signature = str(params["signature"])
    contract = next(
        contract for contract in S10_FUNCTION_CONTRACTS if contract.regprocedure == signature
    )
    return signature, contract.database_result, True, True, True, True


def test_capabilities_expose_only_implemented_restricted_vertical_slices() -> None:
    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        if "set_config" in query:
            return FakeCursor(("ok",))
        return FakeCursor(_function_probe_row(params))

    connection = FakeConnection(respond)
    repository: DurableSubmissionRepository = _repository(connection)
    capabilities = repository.capabilities()

    assert capabilities.atomic_prepare_and_reserve
    assert capabilities.durable_owner_claim_cas
    assert capabilities.durable_capture_command
    assert capabilities.durable_capture_admission
    assert capabilities.idempotent_outbox_delivery
    assert capabilities.atomic_terminal_and_quota
    assert capabilities.terminal_replay_integrity
    assert capabilities.immutable_capture_link
    assert capabilities.atomic_fact_and_outbox
    assert not capabilities.durable_analysis_command
    probe_count = sum("to_regprocedure" in query for query, _ in connection.statements)
    assert probe_count == len(S10_FUNCTION_CONTRACTS)
    assert repository.capabilities() == capabilities
    assert sum("to_regprocedure" in query for query, _ in connection.statements) == probe_count


def test_restricted_function_inventory_records_exact_result_shapes() -> None:
    contracts = {contract.name: contract for contract in S10_FUNCTION_CONTRACTS}

    assert contracts["create_collection_submission_operation_v2"].result_columns == (
        "operation_id",
        "created",
    )
    assert len(contracts["create_collection_submission_operation_v2"].argument_types) == 15
    assert contracts["claim_collection_submission_v2"].result_columns == (
        "dispatch_id",
        "persisted_claim_pub_id",
        "claim_acquired",
    )
    assert contracts["begin_collection_capture_v2"].result_columns == (
        "capture_state_version",
        "capture_attempt_ordinal",
        "attempt_acquired",
    )
    assert contracts["record_collection_slot_outcome_v2"].result_columns == (
        "slot_outcome_id",
        "fact_version",
        "outbox_id",
        "recorded",
    )
    assert len(contracts["prepare_collection_submission_request_v2"].argument_types) == 12
    assert len(contracts["claim_collection_submission_v2"].argument_types) == 17
    assert len(contracts["begin_collection_capture_v2"].argument_types) == 13
    assert len(contracts["finalize_collection_submission_v2"].argument_types) == 22
    stage_arguments = contracts["stage_collection_capture_manifest_v2"].argument_types
    assert len(stage_arguments) == 32
    assert stage_arguments[:5] == ("uuid", "uuid", "uuid", "uuid", "integer")
    assert stage_arguments[5:15] == ("text",) * 10
    assert stage_arguments[15] == "bigint"
    assert stage_arguments[16:29] == ("text",) * 13
    assert stage_arguments[29:] == ("timestamptz",) * 3
    assert contracts["link_collection_capture_v2"].database_result == (
        "TABLE(observation_id uuid, analysis_admission_id uuid, linked boolean)"
    )
    assert all("assert_collection_" not in contract.name for contract in contracts.values())


def test_schema_probe_reports_missing_exact_signature_without_writes() -> None:
    missing = "platform.stage_collection_capture_manifest_v2"

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        assert params is not None
        signature = str(params["signature"])
        if signature.startswith(missing):
            return FakeCursor((None, None, None, None, None, None))
        return FakeCursor(_function_probe_row(params))

    connection = FakeConnection(respond)
    repository = _repository(connection)
    absent = repository.missing_function_contracts()

    assert len(absent) == 1
    assert absent[0].startswith(missing)
    assert all(
        "INSERT " not in query and "UPDATE " not in query for query, _ in connection.statements
    )
    capabilities = repository.capabilities()
    assert capabilities.atomic_fact_and_outbox
    assert not capabilities.durable_capture_command
    assert not capabilities.durable_capture_admission


def test_schema_probe_rejects_exact_result_shape_drift_before_capability_use() -> None:
    drifted = "platform.link_collection_capture_v2"

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        if "set_config" in query:
            return FakeCursor(("ok",))
        signature, result, *security = _function_probe_row(params)
        if str(signature).startswith(drifted):
            result = "TABLE(observation_id uuid, linked boolean)"
        return FakeCursor((signature, result, *security))

    connection = FakeConnection(respond)
    repository = _repository(connection)

    assert repository.missing_function_contracts() == (
        next(
            contract.regprocedure
            for contract in S10_FUNCTION_CONTRACTS
            if contract.name == "link_collection_capture_v2"
        ),
    )
    assert not repository.capabilities().immutable_capture_link
    assert all(
        "INSERT " not in query and "UPDATE " not in query for query, _ in connection.statements
    )


@pytest.mark.parametrize("drift_index", [2, 3, 4, 5])
def test_schema_probe_rejects_security_or_execute_drift(drift_index: int) -> None:
    drifted = "platform.create_collection_submission_operation_v2"

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        if "set_config" in query:
            return FakeCursor(("ok",))
        row = list(_function_probe_row(params))
        if str(row[0]).startswith(drifted):
            row[drift_index] = False
        return FakeCursor(tuple(row))

    repository = _repository(FakeConnection(respond))

    assert repository.missing_function_contracts() == (
        next(
            contract.regprocedure
            for contract in S10_FUNCTION_CONTRACTS
            if contract.name == "create_collection_submission_operation_v2"
        ),
    )
    assert not repository.capabilities().atomic_prepare_and_reserve


def test_database_transaction_sets_utc_and_tenant_before_business_sql() -> None:
    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.lstrip().startswith("SELECT operation.id,"):
            return FakeCursor(None)
        raise AssertionError(query)

    connection = FakeConnection(respond)

    assert _repository(connection).load_operation(operation_ref(_identity())) is None
    assert connection.statements[0] == ("SET LOCAL TIME ZONE 'UTC'", None)
    assert "set_config('app.tenant_id'" in connection.statements[1][0]
    assert connection.statements[1][1] == {"tenant_id": TENANT_ID}


def test_atomic_prepare_reserves_all_scopes_and_returns_database_reservation_ref() -> None:
    work, prepared = _prepared()
    identity = _identity()

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if "create_collection_submission_operation_v2" in query:
            return FakeCursor((OPERATION_ID, True))
        if query.startswith("SELECT operation.id,"):
            return FakeCursor(_operation_row(identity))
        if query.startswith("SELECT operation.id"):
            return FakeCursor((OPERATION_ID,))
        if query.startswith("SELECT binding.id"):
            return FakeCursor((BINDING_ID, REGISTRY_ID))
        if query.startswith("SELECT reservation.pub_id"):
            return FakeCursor(("qrs-database-generated",))
        if "prepare_collection_submission_request_v2" in query:
            return FakeCursor((MANIFEST_ID, CAPTURE_TRUTH_ID, True))
        if query.startswith("SELECT reservation.requested_units"):
            return FakeCursor((1, "reserved", 3, 3, 3, 0, 0, 0))
        raise AssertionError(query)

    quota_requests: list[ReserveQuotaRequest] = []

    def reserve(
        connection: ConnectionProtocol,
        request: ReserveQuotaRequest,
    ) -> ReserveQuotaResult:
        del connection
        quota_requests.append(request)
        return ReserveQuotaResult(
            reserved=True,
            idempotent=False,
            reservation_set_hash=sha256(b"effects").hexdigest(),
            reservation_id=RESERVATION_ID,
        )

    connection = FakeConnection(respond)
    durable = _repository(connection, quota_reserver=reserve).atomic_prepare_and_reserve(
        work,
        prepared,
    )

    assert durable.operation == prepared.operation
    assert durable.reservation_pub_id == "qrs-database-generated"
    assert durable.quota.reserved_units == 1
    assert len(quota_requests) == 1
    request = quota_requests[0]
    assert request.binding_id == BINDING_ID
    assert request.registry_id == REGISTRY_ID
    assert connection.rollbacks == 0
    assert connection.commits == 1
    sql = "\n".join(query for query, _ in connection.statements)
    assert "create_collection_submission_operation_v2" in sql
    assert "INSERT INTO platform.collection_submission_operation" not in sql


def test_quota_blocker_crosses_outer_transaction_and_prevents_manifest_write() -> None:
    work, prepared = _prepared()

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if "create_collection_submission_operation_v2" in query:
            return FakeCursor((OPERATION_ID, True))
        if query.startswith("SELECT operation.id"):
            return FakeCursor((OPERATION_ID,))
        if query.startswith("SELECT binding.id"):
            return FakeCursor((BINDING_ID, REGISTRY_ID))
        raise AssertionError(query)

    blocker = QuotaBlocker(
        bucket_hash=sha256(b"bucket").hexdigest(),
        scope_kind=QuotaScopeKind.PROJECT,
        starts_at=NOW,
        ends_at=NOW + timedelta(days=1),
        limit_units=1,
        reserved_units=1,
        consumed_units=0,
        unknown_units=0,
        requested_units=1,
    )

    def blocked(
        connection: ConnectionProtocol,
        request: ReserveQuotaRequest,
    ) -> ReserveQuotaResult:
        del connection, request
        return ReserveQuotaResult(
            reserved=False,
            idempotent=False,
            reservation_set_hash=None,
            blockers=(blocker,),
        )

    connection = FakeConnection(respond)
    with pytest.raises(QuotaReservationBlocked, match="quota_capacity_blocked"):
        _repository(connection, quota_reserver=blocked).atomic_prepare_and_reserve(
            work,
            prepared,
        )

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.rollback_exception_names == ["_RollbackAtomicPreparation"]
    assert not any(
        "prepare_collection_submission_request_v2" in query for query, _ in connection.statements
    )


def test_sending_operation_preparation_replay_reuses_live_quota_without_reserving() -> None:
    work, _initial = _prepared()
    identity = _identity()
    sending = _sending_operation(identity)
    prepared = prepare_submission(
        PrepareSubmissionCommand(identity=identity, prepared_at=NOW),
        existing=sending,
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if "create_collection_submission_operation_v2" in query:
            return FakeCursor((OPERATION_ID, False))
        if query.startswith("SELECT reservation.id, reservation.pub_id"):
            return FakeCursor((RESERVATION_ID, "qrs-terminal-replay"))
        if "prepare_collection_submission_request_v2" in query:
            return FakeCursor((MANIFEST_ID, CAPTURE_TRUTH_ID, False))
        if query.startswith("SELECT operation.id,"):
            return FakeCursor(_sending_operation_row(identity))
        if query.startswith("SELECT reservation.requested_units"):
            return FakeCursor((1, "reserved", 3, 3, 3, 0, 0, 0))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    durable = _repository(connection).atomic_prepare_and_reserve(work, prepared)

    assert durable.operation == sending
    assert durable.reservation_pub_id == "qrs-terminal-replay"
    assert durable.quota.reserved_units == 1
    sql = "\n".join(query for query, _ in connection.statements)
    assert "SELECT reservation.id, reservation.pub_id" in sql
    assert "SELECT binding.id" not in sql


def test_mark_outbox_published_uses_restricted_cas_and_never_direct_update() -> None:
    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT outbox.id"):
            return FakeCursor((OUTBOX_ID, 4, "pending"))
        if "advance_collection_governance_outbox_v2" in query:
            return FakeCursor((5,))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    _repository(connection).mark_outbox_published("outbox-v1-event")

    sql = "\n".join(query for query, _ in connection.statements)
    assert "advance_collection_governance_outbox_v2" in sql
    assert "UPDATE platform.collection_governance_outbox_v2" not in sql


def test_owner_claim_uses_restricted_function_and_reloads_exact_sending_truth() -> None:
    identity = _identity()
    claim = _claim(identity)
    command = OwnerClaimCasCommand(
        operation=operation_ref(identity),
        expected_state_version=1,
        next_state_version=2,
        authority=_authority(),
        claim=claim,
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,") and "execution_grant.grant_hash" in query:
            return FakeCursor(
                (OPERATION_ID, BINDING_ID, sha256(b"grant").hexdigest(), "gateway-v1")
            )
        if "claim_collection_submission_v2" in query:
            assert params is not None
            assert params["authority_snapshot_json"] == canonical_json(command.authority)
            return FakeCursor((MANIFEST_ID, claim.claim_pub_id, True))
        if query.startswith("SELECT operation.id,"):
            return FakeCursor(_sending_operation_row(identity))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    observation = _repository(connection).compare_and_swap(command)

    assert observation.persisted == _sending_operation(identity)
    assert observation.status.value == "freshly_applied"
    sql = "\n".join(query for query, _ in connection.statements)
    assert "claim_collection_submission_v2" in sql
    assert "UPDATE platform.collection_submission_operation" not in sql


def test_owner_claim_command_rejects_authority_snapshot_drift() -> None:
    identity = _identity()
    authority = _authority()
    drifted = authority.model_copy(update={"binding_revision_pub_id": "binding-drifted"})

    with pytest.raises(ValidationError, match="claim_authority_snapshot_mismatch"):
        OwnerClaimCasCommand.model_validate(
            {
                "operation": operation_ref(identity),
                "expected_state_version": 1,
                "next_state_version": 2,
                "authority": drifted,
                "claim": _claim(identity),
            }
        )


def test_active_owner_cannot_be_reconciliation_claimed() -> None:
    identity = _identity()
    operation = _sending_operation(identity)
    claim = operation.claim
    assert claim is not None
    work = _submission_work(identity)

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT dispatch.id"):
            return FakeCursor(
                (
                    MANIFEST_ID,
                    "not_required",
                    "active",
                    1,
                    None,
                    None,
                    None,
                    "gateway-v1",
                    "owner-dispatch-1",
                    BINDING_ID,
                    claim.fence_set_sha256,
                )
            )
        raise AssertionError(query)

    connection = FakeConnection(respond)
    result = _repository(connection).claim_reconciliation(work=work, operation=operation)

    assert not result.acquired
    assert not result.owner_session_terminated
    assert not any(
        "claim_collection_dispatch_reconciliation_v2" in query for query, _ in connection.statements
    )


def test_preflight_not_sent_uses_exact_grant_without_fabricating_dispatch_owner() -> None:
    identity = _identity()
    operation = prepare_submission(
        PrepareSubmissionCommand(identity=identity, prepared_at=NOW)
    ).operation
    authority = _authority()
    preflight = PreflightCommand(
        operation=operation_ref(identity),
        expected_state_version=1,
        authority=authority,
    )
    observation = PreflightObservation(
        operation=operation_ref(identity),
        authority_sha256=authority_digest(authority),
        decision=PreflightDecision.CONFIRMED_NOT_SENT,
        observed_at=NOW + timedelta(minutes=1),
        evidence_ref="preflight-evidence-1",
        evidence_sha256=sha256(b"preflight-evidence").hexdigest(),
        not_sent_reason=TerminalReason.PREFLIGHT_NOT_SENT,
    )
    transition = apply_preflight_not_sent(
        operation,
        verify_preflight(operation, preflight, observation),
    )
    assert transition.operation.terminal is not None
    assert transition.operation.terminal.terminated_fence_set_sha256 == authority.fence_set_sha256
    assert transition.operation.terminal.non_submission_proof_ref is None
    work = _submission_work(identity)
    operation_rows = iter(
        (
            _operation_row(identity),
            _preflight_terminal_operation_row(
                identity,
                transition,
                persisted_fence_hash=authority.fence_set_sha256,
            ),
        )
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,") and "operation.pub_id" in query:
            if "array_agg" in query:
                return FakeCursor(
                    (
                        OPERATION_ID,
                        BINDING_ID,
                        canonical_json(
                            {
                                "fences": [
                                    {
                                        "binding_resource_pub_id": (fence.binding_resource_pub_id),
                                        "generation": fence.generation,
                                        "lease_pub_id": fence.lease_pub_id,
                                        "owner_handle": fence.owner_handle,
                                        "resource_role": fence.resource_role,
                                    }
                                    for fence in authority.lease_fences
                                ],
                                "version": "lease-fence-identity-v1",
                            }
                        ),
                        ["lease-1"],
                    )
                )
            return FakeCursor(next(operation_rows))
        if query.startswith("SELECT dispatch.id"):
            return FakeCursor(None)
        if "finalize_collection_submission_v2" in query:
            assert params is not None
            assert params["dispatch_id"] is None
            assert params["owner_gateway_revision"] is None
            assert params["owner_dispatch_ref"] is None
            assert params["execution_grant_id"] == BINDING_ID
            assert params["terminated_fence_set_hash"] == authority.fence_set_sha256
            return FakeCursor((2, MANIFEST_ID, OUTBOX_ID, True))
        if query.startswith("SELECT outbox.event_key"):
            event = transition.outbox
            return FakeCursor(
                (
                    event.outbox_key,
                    event.event_type,
                    event.aggregate_ref,
                    event.aggregate_version,
                    event.payload_sha256,
                    event.occurred_at,
                )
            )
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).atomic_terminal_and_quota(work, transition)

    assert persisted == transition.operation
    sql = "\n".join(query for query, _ in connection.statements)
    assert "finalize_collection_submission_v2" in sql
    assert "UPDATE platform.collection_submission_operation" not in sql


def test_reconciliation_terminal_replay_restores_exact_persisted_claim() -> None:
    identity = _identity()
    operation = _terminal_operation(identity)
    terminal = operation.terminal
    claim = operation.claim
    assert terminal is not None
    assert claim is not None
    payload_hash = sha256(canonical_json(terminal).encode()).hexdigest()
    event = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type="collection.submission.terminal",
            aggregate_ref=identity.operation_pub_id,
            aggregate_version=operation.state_version,
            payload_sha256=payload_hash,
        ),
        event_type="collection.submission.terminal",
        aggregate_ref=identity.operation_pub_id,
        aggregate_version=operation.state_version,
        payload_sha256=payload_hash,
        occurred_at=terminal.resolved_at,
    )
    transition = TerminalSubmissionTransition(
        operation=operation,
        quota_effect=QuotaTerminalEffect.SETTLE_CONSUMED,
        outbox=event,
    )
    work = _submission_work(identity)
    repository = _repository(FakeConnection(lambda query, params: FakeCursor()))
    reconciliation_hash = repository._reconciliation_claim_hash(
        operation=operation_ref(identity),
        reconciliation_claim_ref=work.reconciliation_claim_ref,
    )
    operation_rows = iter((_terminal_operation_row(operation),) * 2)

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,") and "operation.pub_id" in query:
            if "array_agg" in query:
                authority = _authority()
                return FakeCursor(
                    (
                        OPERATION_ID,
                        BINDING_ID,
                        canonical_json(
                            {
                                "fences": [
                                    {
                                        "binding_resource_pub_id": (fence.binding_resource_pub_id),
                                        "generation": fence.generation,
                                        "lease_pub_id": fence.lease_pub_id,
                                        "owner_handle": fence.owner_handle,
                                        "resource_role": fence.resource_role,
                                    }
                                    for fence in authority.lease_fences
                                ],
                                "version": "lease-fence-identity-v1",
                            }
                        ),
                        ["lease-1"],
                    )
                )
            return FakeCursor(next(operation_rows))
        if query.startswith("SELECT dispatch.id"):
            return FakeCursor(
                (
                    MANIFEST_ID,
                    "resolved",
                    "resolved",
                    4,
                    None,
                    work.reconciliation_claim_ref,
                    reconciliation_hash,
                    "gateway-v1",
                    claim.owner_dispatch_ref,
                    BINDING_ID,
                    claim.fence_set_sha256,
                )
            )
        if "finalize_collection_submission_v2" in query:
            assert params is not None
            assert params["reconciliation_claim_ref"] == work.reconciliation_claim_ref
            assert params["reconciliation_claim_hash"] == reconciliation_hash
            assert params["expected_reconciliation_version"] == 3
            return FakeCursor((operation.state_version, MANIFEST_ID, OUTBOX_ID, False))
        if query.startswith("SELECT outbox.event_key"):
            return FakeCursor(
                (
                    event.outbox_key,
                    event.event_type,
                    event.aggregate_ref,
                    event.aggregate_version,
                    event.payload_sha256,
                    event.occurred_at,
                )
            )
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).atomic_terminal_and_quota(work, transition)

    assert persisted == operation


def test_slot_fact_and_outbox_use_one_restricted_atomic_entry() -> None:
    identity = _identity()
    operation = operation_ref(identity)
    fact = SlotOutcomeFact(
        operation=operation,
        outcome=SlotOutcome.CONFIRMED_SENT_CAPTURE_PENDING,
        operation_state_version=3,
        fact_version=1,
        is_final_primary=False,
        recorded_at=NOW + timedelta(minutes=3),
    )
    payload_hash = sha256(canonical_json(fact).encode()).hexdigest()
    event = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type="collection.slot.outcome",
            aggregate_ref=operation.operation_pub_id,
            aggregate_version=1,
            payload_sha256=payload_hash,
        ),
        event_type="collection.slot.outcome",
        aggregate_ref=operation.operation_pub_id,
        aggregate_version=1,
        payload_sha256=payload_hash,
        occurred_at=fact.recorded_at,
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,") and "manifest.id" in query:
            return FakeCursor((OPERATION_ID, None, 1, None))
        if "record_collection_slot_outcome_v2" in query:
            return FakeCursor((MANIFEST_ID, 1, OUTBOX_ID, True))
        if query.startswith("SELECT outcome.outcome_state"):
            return FakeCursor(
                (
                    fact.outcome.value,
                    fact.operation_state_version,
                    None,
                    None,
                    None,
                    False,
                    1,
                    fact.recorded_at,
                    payload_hash,
                )
            )
        if query.startswith("SELECT outbox.event_key"):
            return FakeCursor(
                (
                    event.outbox_key,
                    event.event_type,
                    event.aggregate_ref,
                    event.aggregate_version,
                    event.payload_sha256,
                    event.occurred_at,
                )
            )
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).atomic_fact_and_outbox(
        _submission_work(identity),
        fact,
        event,
    )

    assert persisted == fact
    sql = "\n".join(query for query, _ in connection.statements)
    assert "record_collection_slot_outcome_v2" in sql
    assert "INSERT INTO platform.collection_slot_outcome_v2" not in sql


def test_analysis_methods_fail_before_opening_a_database_connection() -> None:
    connection = FakeConnection(lambda query, params: FakeCursor())
    repository = _repository(connection)

    with pytest.raises(
        SubmissionRepositoryError,
        match="durable_analysis_execution_not_implemented",
    ):
        repository.load_analysis(operation_ref(_identity()))

    assert connection.statements == []


def test_existing_capture_truth_is_exact_replay_not_direct_dml() -> None:
    capture = initial_capture_truth(_terminal_operation(_identity()))

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(_capture_row(capture))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).store_capture(
        expected_state_version=None,
        capture=capture,
    )

    assert persisted == capture
    assert not any(
        query.lstrip().startswith(("INSERT", "UPDATE", "DELETE"))
        for query, _ in connection.statements
    )


@pytest.mark.parametrize(
    ("live_count", "terminated_count", "expected"),
    (
        (1, 0, CaptureAdmissionDecision.DIRECT_OWNER_LIVE),
        (0, 1, CaptureAdmissionDecision.NO_LIVE_AUTHORITY),
        (0, 0, CaptureAdmissionDecision.NO_LIVE_AUTHORITY),
    ),
)
def test_capture_admission_only_allows_an_exact_fully_live_direct_owner(
    live_count: int,
    terminated_count: int,
    expected: CaptureAdmissionDecision,
) -> None:
    identity = _identity()
    operation = _terminal_operation(identity)
    capture = initial_capture_truth(operation)
    work = _submission_work(identity)
    authority = _authority()

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,"):
            return FakeCursor(_terminal_operation_row(operation))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(_capture_row(capture))
        if query.startswith("SELECT operation.send_state_version"):
            return FakeCursor(
                (
                    operation.state_version,
                    operation.send_state.value,
                    capture.state_version,
                    capture.capture_state.value,
                    "resolved",
                    "resolved",
                    2,
                    None,
                    None,
                    authority.grant_pub_id,
                    "issued",
                    authority.valid_until,
                    None,
                    authority.owner_handle,
                    authority.fence_set_sha256,
                    _fence_material(),
                    ["lease-1"],
                    1,
                    live_count,
                    terminated_count,
                )
            )
        raise AssertionError(query)

    admission = _repository(FakeConnection(respond)).resolve_capture_admission(
        work=work,
        operation=operation,
        capture=capture,
    )

    assert admission.decision is expected
    assert admission.reconciliation_claim_ref is None


def test_capture_admission_requires_exact_terminated_reconciled_authority() -> None:
    identity = _identity()
    operation = _terminal_operation(identity)
    capture = initial_capture_truth(operation)
    work = _submission_work(identity)
    authority = _authority()
    ref = operation_ref(identity)

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id,"):
            return FakeCursor(_terminal_operation_row(operation))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(_capture_row(capture))
        if query.startswith("SELECT operation.send_state_version"):
            return FakeCursor(
                (
                    operation.state_version,
                    operation.send_state.value,
                    capture.state_version,
                    capture.capture_state.value,
                    "resolved",
                    "resolved",
                    4,
                    work.reconciliation_claim_ref,
                    _reconciliation_claim_hash(ref, work.reconciliation_claim_ref),
                    authority.grant_pub_id,
                    "revoked",
                    authority.valid_until,
                    NOW + timedelta(minutes=3),
                    authority.owner_handle,
                    authority.fence_set_sha256,
                    _fence_material(),
                    ["lease-1"],
                    1,
                    0,
                    1,
                )
            )
        raise AssertionError(query)

    admission = _repository(FakeConnection(respond)).resolve_capture_admission(
        work=work,
        operation=operation,
        capture=capture,
    )

    assert admission.decision is CaptureAdmissionDecision.RECONCILED_NO_AUTHORITY
    assert admission.reconciliation_claim_ref == work.reconciliation_claim_ref


def test_capture_attempt_persists_canonical_command_through_restricted_entry() -> None:
    identity = _identity()
    work = _submission_work(identity)
    context = _submission_context(identity)
    capture = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(capture)
    expected = begin_capture(capture, command)
    capture_loads = iter((_capture_row(capture), _capture_row(expected, command=command)))

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(next(capture_loads))
        if query.startswith("SELECT operation.id,") and "authority_snapshot_json" in query:
            return FakeCursor(
                (
                    OPERATION_ID,
                    MANIFEST_ID,
                    capture.owner_handle,
                    capture.fence_set_sha256,
                    command.authority_sha256,
                    canonical_json(command.authority),
                    command.authority.grant_pub_id,
                )
            )
        if "begin_collection_capture_v2" in query:
            assert params is not None
            assert params["capture_command_json"] == canonical_json(command)
            assert params["capture_request_sha256"] == capture_command_digest(command)
            return FakeCursor((expected.state_version, 1, True))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    attempt = _repository(connection).start_or_resume_capture_attempt(
        work=work,
        context=context,
        capture=capture,
        requested_at=command.requested_at,
    )

    assert attempt == DurableCaptureAttempt(
        capture=expected,
        command=command,
        freshly_started=True,
    )
    sql = "\n".join(query for query, _ in connection.statements)
    assert "begin_collection_capture_v2" in sql
    assert "UPDATE platform.collection_capture_truth_v2" not in sql


def test_active_capture_attempt_replays_stored_command_without_new_begin() -> None:
    identity = _identity()
    work = _submission_work(identity)
    context = _submission_context(identity)
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(_capture_row(capturing, command=command))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    attempt = _repository(connection).start_or_resume_capture_attempt(
        work=work,
        context=context,
        capture=capturing,
        requested_at=NOW + timedelta(minutes=6),
    )

    assert attempt.command == command
    assert not attempt.freshly_started
    assert not any("begin_collection_capture_v2" in query for query, _ in connection.statements)


def test_capture_resolution_round_trips_all_provenance_fields_atomically() -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    attempt = DurableCaptureAttempt(
        capture=capturing,
        command=command,
        freshly_started=True,
    )
    raw = _raw_capture(identity)
    normalized = normalize_capture(command, raw)
    expected = apply_capture_disposition(capturing, normalized)
    capture_loads = iter(
        (
            _capture_row(capturing, command=command),
            _capture_row(expected, command=command, raw=raw),
        )
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(next(capture_loads))
        if query.startswith("SELECT operation.id,") and "authority_snapshot_json" in query:
            return FakeCursor(
                (
                    OPERATION_ID,
                    MANIFEST_ID,
                    capturing.owner_handle,
                    capturing.fence_set_sha256,
                    command.authority_sha256,
                    canonical_json(command.authority),
                    command.authority.grant_pub_id,
                )
            )
        if "stage_collection_capture_manifest_v2" in query:
            assert params is not None
            assert params["capture_key"] == command.staging_intent.staging_key
            assert params["content_object_ref"] == command.staging_intent.object_ref
            assert params["reason_code"] == CaptureState.COMPLETED.value
            assert params["capture_channel"] == raw.provenance.capture_channel.value
            assert params["capture_protocol_revision"] == (raw.provenance.capture_protocol_revision)
            assert params["observed_product_version"] == (raw.provenance.observed_product_version)
            assert params["capture_adapter_revision"] == (raw.provenance.capture_adapter_revision)
            assert params["data_classification"] == (raw.provenance.data_classification.value)
            assert params["dlp_policy_revision"] == raw.provenance.dlp_policy_revision
            assert params["retention_until"] == raw.provenance.retention_until
            return FakeCursor((CAPTURE_TRUTH_ID,))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).resolve_capture_attempt(
        attempt=attempt,
        raw=raw,
        normalized=normalized,
    )

    assert persisted == expected
    assert persisted.provenance == raw.provenance
    sql = "\n".join(query for query, _ in connection.statements)
    assert "stage_collection_capture_manifest_v2" in sql
    assert "INSERT INTO platform.collection_capture_manifest_v2" not in sql


def test_surface_mismatch_stages_stable_invalid_surface_reason() -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    attempt = DurableCaptureAttempt(
        capture=capturing,
        command=command,
        freshly_started=True,
    )
    observed = SurfaceProductRef(
        platform=identity.surface_product.platform,
        collection_surface=identity.surface_product.collection_surface,
        product_variant="different-web-product",
        target_key=(
            "collection-target-v1|platform=doubao|collection_surface=consumer_web|"
            "product_variant=different-web-product"
        ),
    )
    raw = _raw_capture(identity).model_copy(update={"observed_surface_product": observed})
    normalized = normalize_capture(command, raw)
    assert normalized.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    expected = apply_capture_disposition(capturing, normalized)
    capture_loads = iter(
        (
            _capture_row(capturing, command=command),
            _capture_row(expected, command=command, raw=raw),
        )
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(next(capture_loads))
        if query.startswith("SELECT operation.id,") and "authority_snapshot_json" in query:
            return FakeCursor(
                (
                    OPERATION_ID,
                    MANIFEST_ID,
                    capturing.owner_handle,
                    capturing.fence_set_sha256,
                    command.authority_sha256,
                    canonical_json(command.authority),
                    command.authority.grant_pub_id,
                )
            )
        if "stage_collection_capture_manifest_v2" in query:
            assert params is not None
            assert params["capture_key"] == command.staging_intent.staging_key
            assert params["content_object_ref"] == command.staging_intent.object_ref
            assert params["reason_code"] == "invalid_surface_or_product"
            return FakeCursor((CAPTURE_TRUTH_ID,))
        raise AssertionError(query)

    persisted = _repository(FakeConnection(respond)).resolve_capture_attempt(
        attempt=attempt,
        raw=raw,
        normalized=normalized,
    )

    assert persisted == expected
    assert persisted.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH


@pytest.mark.parametrize(
    "capture_state",
    (CaptureState.FAILED, CaptureState.NOT_OBSERVABLE),
)
def test_failed_capture_manifest_reuses_predeclared_staging_key_without_object(
    capture_state: Literal[CaptureState.FAILED, CaptureState.NOT_OBSERVABLE],
) -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    attempt = DurableCaptureAttempt(
        capture=capturing,
        command=command,
        freshly_started=True,
    )
    observed_at = NOW + timedelta(minutes=4)
    raw = CaptureDisposition(
        capture_state=capture_state,
        attempt_ref=command.attempt_ref,
        evidence_ref="capture-failed-evidence",
        evidence_sha256=sha256(b"capture-failed-evidence").hexdigest(),
        observed_at=observed_at,
        observed_surface_product=identity.surface_product,
        provenance=CaptureProvenance(
            capture_channel=CaptureChannel.WEB_DOM,
            capture_protocol_revision="capture-protocol-v1",
            observed_product_version="web-product-v1",
            capture_adapter_revision="capture-adapter-v1",
            data_classification=CaptureDataClassification.CUSTOMER_PRIVATE,
            dlp_policy_revision="dlp-policy-v1",
            retention_until=observed_at + timedelta(days=1),
        ),
    )
    normalized = normalize_capture(command, raw)
    expected = apply_capture_disposition(capturing, normalized)
    capture_loads = iter(
        (
            _capture_row(capturing, command=command),
            _capture_row(expected, command=command, raw=raw),
        )
    )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT truth.capture_state"):
            return FakeCursor(next(capture_loads))
        if query.startswith("SELECT operation.id,") and "authority_snapshot_json" in query:
            return FakeCursor(
                (
                    OPERATION_ID,
                    MANIFEST_ID,
                    capturing.owner_handle,
                    capturing.fence_set_sha256,
                    command.authority_sha256,
                    canonical_json(command.authority),
                    command.authority.grant_pub_id,
                )
            )
        if "stage_collection_capture_manifest_v2" in query:
            assert params is not None
            assert params["capture_key"] == command.staging_intent.staging_key
            assert params["reason_code"] == capture_state.value
            assert params["content_object_ref"] is None
            assert params["content_hash"] is None
            assert params["content_size_bytes"] is None
            assert params["mime_type"] is None
            assert params["capture_schema_revision"] is None
            return FakeCursor((CAPTURE_TRUTH_ID,))
        raise AssertionError(query)

    persisted = _repository(FakeConnection(respond)).resolve_capture_attempt(
        attempt=attempt,
        raw=raw,
        normalized=normalized,
    )

    assert persisted == expected
    assert persisted.staging is None


def test_capture_mapper_preserves_surface_quarantine_after_manifest_is_orphaned() -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    raw = _raw_capture(identity).model_copy(
        update={
            "observed_surface_product": SurfaceProductRef(
                platform=identity.surface_product.platform,
                collection_surface=identity.surface_product.collection_surface,
                product_variant="different-web-product",
                target_key=(
                    "collection-target-v1|platform=doubao|"
                    "collection_surface=consumer_web|"
                    "product_variant=different-web-product"
                ),
            )
        }
    )
    expected = apply_capture_disposition(capturing, normalize_capture(command, raw))
    row = list(_capture_row(expected, command=command, raw=raw))
    row[29] = "orphaned"

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.lstrip().startswith("SELECT truth.capture_state"):
            return FakeCursor(tuple(row))
        raise AssertionError(query)

    persisted = _repository(FakeConnection(respond)).load_capture(operation_ref(identity))

    assert persisted is not None
    assert persisted.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    assert persisted == expected


def test_capture_mapper_does_not_infer_surface_mismatch_from_storage_state_alone() -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    raw = _raw_capture(identity)
    expected = apply_capture_disposition(capturing, normalize_capture(command, raw))
    row = list(_capture_row(expected, command=command, raw=raw))
    row[29] = "orphaned"

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        del params
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.lstrip().startswith("SELECT truth.capture_state"):
            return FakeCursor(tuple(row))
        raise AssertionError(query)

    persisted = _repository(FakeConnection(respond)).load_capture(operation_ref(identity))

    assert persisted is not None
    assert persisted.normalization is CaptureNormalizationDecision.ACCEPTED
    assert persisted == expected


def test_capture_link_uses_restricted_immutable_entry_and_exact_reload() -> None:
    identity = _identity()
    initial = initial_capture_truth(_terminal_operation(identity))
    command = _capture_command(initial)
    capturing = begin_capture(initial, command)
    raw = _raw_capture(identity)
    normalized = normalize_capture(command, raw)
    completed = apply_capture_disposition(capturing, normalized)
    link = link_immutable_capture(completed, linked_at=NOW + timedelta(minutes=6))

    def link_row(value: ImmutableCaptureLink) -> tuple[object, ...]:
        requested = value.requested_surface_product
        observed = value.observed_surface_product
        return (
            value.capture_link_key,
            value.staging_key,
            value.content_sha256,
            requested.platform,
            requested.collection_surface.value,
            requested.product_variant,
            requested.target_key,
            observed.platform,
            observed.collection_surface.value,
            observed.product_variant,
            value.capture_state_version,
            value.linked_at,
        )

    def respond(query: str, params: Mapping[str, object] | None) -> FakeCursor:
        query = query.lstrip()
        if "set_config" in query:
            return FakeCursor(("ok",))
        if query.startswith("SELECT operation.id, dispatch.id"):
            assert params is not None
            assert params["staging_key"] == link.staging_key
            assert params["content_sha256"] == link.content_sha256
            assert params["capture_state_version"] == link.capture_state_version
            return FakeCursor((OPERATION_ID, BINDING_ID, MANIFEST_ID, link.capture_state_version))
        if "link_collection_capture_v2" in query:
            assert params is not None
            assert params["capture_link_key"] == link.capture_link_key
            assert params["linked_at"] == link.linked_at
            assert params["analysis_contract_revision"] is None
            return FakeCursor((MANIFEST_ID, None, True))
        if query.startswith("SELECT manifest.capture_link_key"):
            return FakeCursor(link_row(link))
        raise AssertionError(query)

    connection = FakeConnection(respond)
    persisted = _repository(connection).store_capture_link(link)

    assert persisted == link
    sql = "\n".join(query for query, _ in connection.statements)
    assert "link_collection_capture_v2" in sql
    assert "UPDATE platform.collection_capture_manifest_v2" not in sql
