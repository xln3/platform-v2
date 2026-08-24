"""No-I/O orchestration for the collection-v2 submission protocol.

The coordinator deliberately depends on narrow persistence and gateway ports.  It
does not know how PostgreSQL, Temporal, a browser, an app, or a provider API are
implemented.  In particular, recovery of a durable ``SENDING`` operation is
delegated to a reconciliation-only port and can never recreate a submit
capability.

The current database schema must implement every flag in
``REQUIRED_DURABILITY`` before a real adapter may use this coordinator.  Missing
claim evidence, capture links, facts/outbox, or quota conservation therefore
fails closed before any gateway call.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, Self

from pydantic import Field, model_validator

from domain.collection.submission import (
    AnalysisCommand,
    AnalysisDisposition,
    AnalysisTruth,
    CaptureDisposition,
    CaptureExistingCommand,
    CaptureNormalizationDecision,
    CaptureTruth,
    FrozenProtocolModel,
    ImmutableCaptureLink,
    NoSubmitDecision,
    OpaqueId,
    OperationRef,
    OutboxEventRef,
    OwnerAuthorityRef,
    OwnerClaimCasCommand,
    OwnerClaimCasObservation,
    PreflightCommand,
    PreflightDecision,
    PreflightObservation,
    PrepareResult,
    PrepareSubmissionCommand,
    ReconciliationDisposition,
    SendingReconciliationCommand,
    Sha256Hex,
    SlotOutcome,
    SubmissionOperationTruth,
    SubmitDisposition,
    SubmitOnceCommand,
    TerminalSubmissionTransition,
    WorkflowOperationInput,
    apply_analysis_disposition,
    apply_capture_disposition,
    apply_preflight_not_sent,
    apply_submit_disposition,
    canonical_json,
    capture_command_digest,
    confirm_owner_claim,
    derive_slot_outcome,
    deterministic_outbox_key,
    initial_analysis_truth,
    initial_capture_truth,
    link_immutable_capture,
    normalize_capture,
    operation_ref,
    plan_owner_claim,
    prepare_submission,
    reconcile_sending,
    start_analysis,
    verify_preflight,
)
from domain.collection.surface import AnalysisState, CaptureState, SendState


class SubmissionCoordinatorError(RuntimeError):
    """Fail-closed coordinator error with a stable, non-secret code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CrashPoint(StrEnum):
    BEFORE_RESERVE = "before_reserve"
    AFTER_RESERVE = "after_reserve"
    BEFORE_OWNER_CAS = "before_owner_cas"
    AFTER_OWNER_CAS_BEFORE_SUBMIT = "after_owner_cas_before_submit"
    AFTER_SUBMIT_BEFORE_ACK = "after_submit_before_ack"
    AFTER_TERMINAL_BEFORE_CAPTURE = "after_terminal_before_capture"
    AFTER_STAGING_BEFORE_LINK = "after_staging_before_link"
    AFTER_FACT_BEFORE_OUTBOX_PUBLISH = "after_fact_before_outbox_publish"
    AFTER_OUTBOX_PUBLISH_BEFORE_MARK = "after_outbox_publish_before_mark"
    AFTER_ANALYSIS_QUEUE = "after_analysis_queue"
    AFTER_ANALYSIS_START = "after_analysis_start"
    AFTER_ANALYSIS_GATEWAY = "after_analysis_gateway"


class CrashHook(Protocol):
    def checkpoint(self, point: CrashPoint) -> None: ...


class NoOpCrashHook:
    def checkpoint(self, point: CrashPoint) -> None:
        del point


class Clock(Protocol):
    def now(self) -> datetime: ...


class RepositoryCapabilities(FrozenProtocolModel):
    atomic_prepare_and_reserve: bool
    durable_owner_claim_cas: bool
    durable_owner_reconciliation: bool
    exclusive_reconciliation_claim: bool
    atomic_terminal_and_quota: bool
    terminal_replay_integrity: bool
    durable_capture_command: bool
    immutable_capture_link: bool
    durable_analysis_command: bool
    atomic_fact_and_outbox: bool
    idempotent_outbox_delivery: bool
    quota_effect_ledger_conservation: bool

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name for name, enabled in self.model_dump(mode="python").items() if not enabled
        )


PREPARATION_REQUIRED_DURABILITY = RepositoryCapabilities(
    atomic_prepare_and_reserve=True,
    durable_owner_claim_cas=False,
    durable_owner_reconciliation=False,
    exclusive_reconciliation_claim=False,
    atomic_terminal_and_quota=False,
    terminal_replay_integrity=False,
    durable_capture_command=False,
    immutable_capture_link=False,
    durable_analysis_command=False,
    atomic_fact_and_outbox=False,
    idempotent_outbox_delivery=False,
    quota_effect_ledger_conservation=True,
)

SUBMISSION_REQUIRED_DURABILITY = RepositoryCapabilities(
    atomic_prepare_and_reserve=False,
    durable_owner_claim_cas=True,
    durable_owner_reconciliation=True,
    exclusive_reconciliation_claim=True,
    atomic_terminal_and_quota=True,
    terminal_replay_integrity=True,
    durable_capture_command=True,
    immutable_capture_link=True,
    durable_analysis_command=False,
    atomic_fact_and_outbox=True,
    idempotent_outbox_delivery=True,
    quota_effect_ledger_conservation=True,
)

ANALYSIS_REQUIRED_DURABILITY = RepositoryCapabilities(
    atomic_prepare_and_reserve=False,
    durable_owner_claim_cas=False,
    durable_owner_reconciliation=False,
    exclusive_reconciliation_claim=False,
    atomic_terminal_and_quota=False,
    terminal_replay_integrity=False,
    durable_capture_command=False,
    immutable_capture_link=True,
    durable_analysis_command=True,
    atomic_fact_and_outbox=True,
    idempotent_outbox_delivery=True,
    quota_effect_ledger_conservation=False,
)


class PrepareWorkItem(FrozenProtocolModel):
    """Pre-resource input resolved only from frozen logical/request/quota truth."""

    workflow: WorkflowOperationInput
    frozen_slot_ref: OpaqueId
    binding_revision_pub_id: OpaqueId
    quota_registry_revision: OpaqueId
    request_manifest_ref: OpaqueId


class ResolvedPreparationContext(FrozenProtocolModel):
    prepare: PrepareSubmissionCommand


class PreparedSubmissionRef(FrozenProtocolModel):
    """Constant-size proof reference returned after atomic prepare + reserve."""

    workflow: WorkflowOperationInput
    reservation_pub_id: OpaqueId


class SubmissionWorkItem(FrozenProtocolModel):
    """Post-resource input: prepare/reserve already exists before grants or leases."""

    prepared: PreparedSubmissionRef
    grant_pub_id: OpaqueId
    lease_pub_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=32)
    cursor_ref: OpaqueId
    claim_pub_id: OpaqueId
    reconciliation_claim_ref: OpaqueId
    capture_attempt_ref: OpaqueId

    @property
    def workflow(self) -> WorkflowOperationInput:
        return self.prepared.workflow

    @property
    def reservation_pub_id(self) -> str:
        return self.prepared.reservation_pub_id

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        if len(set(self.lease_pub_ids)) != len(self.lease_pub_ids):
            raise ValueError("duplicate_lease_reference")
        return self


class ResolvedSubmissionContext(FrozenProtocolModel):
    """Bounded immutable truth resolved from the durable references."""

    prepare: PrepareSubmissionCommand
    authority: OwnerAuthorityRef
    owner_dispatch_ref: OpaqueId
    owner_wal_evidence_sha256: Sha256Hex
    capture_policy_revision: OpaqueId


class ReconciliationEvidence(FrozenProtocolModel):
    durable_evidence_ref: OpaqueId
    durable_evidence_sha256: Sha256Hex
    observed_at: datetime


class DurableReconciliationClaim(FrozenProtocolModel):
    operation: OperationRef
    reconciliation_claim_ref: OpaqueId
    owner_session_terminated: bool
    acquired: bool

    @model_validator(mode="after")
    def only_dead_owner_is_reconcilable(self) -> Self:
        if self.acquired and not self.owner_session_terminated:
            raise ValueError("live_owner_cannot_be_reconciled")
        return self


class DurableCaptureAttempt(FrozenProtocolModel):
    capture: CaptureTruth
    command: CaptureExistingCommand
    freshly_started: bool

    @model_validator(mode="after")
    def command_matches_capturing_truth(self) -> Self:
        if self.capture.capture_state is not CaptureState.CAPTURING:
            raise ValueError("durable_capture_attempt_requires_capturing_truth")
        if (
            self.capture.operation != self.command.operation
            or self.capture.active_attempt_ref != self.command.attempt_ref
            or self.capture.active_request_sha256 != capture_command_digest(self.command)
        ):
            raise ValueError("durable_capture_attempt_mismatch")
        return self


class DurableAnalysisAttempt(FrozenProtocolModel):
    analysis: AnalysisTruth
    command: AnalysisCommand
    freshly_queued: bool

    @model_validator(mode="after")
    def command_matches_active_truth(self) -> Self:
        if self.analysis.analysis_state not in {AnalysisState.QUEUED, AnalysisState.RUNNING}:
            raise ValueError("durable_analysis_attempt_requires_active_truth")
        if self.analysis.active_attempt_ref != self.command.attempt_ref:
            raise ValueError("durable_analysis_attempt_mismatch")
        return self


class SlotOutcomeFact(FrozenProtocolModel):
    operation: OperationRef
    outcome: SlotOutcome
    operation_state_version: int = Field(strict=True, ge=1)
    capture_state_version: int | None = Field(default=None, strict=True, ge=1)
    analysis_state_version: int | None = Field(default=None, strict=True, ge=1)
    capture_link_key: OpaqueId | None = None
    is_final_primary: bool
    fact_version: int = Field(strict=True, ge=1)
    recorded_at: datetime

    def same_basis(
        self,
        *,
        outcome: SlotOutcome,
        operation: SubmissionOperationTruth,
        capture: CaptureTruth | None,
        analysis: AnalysisTruth | None,
        link: ImmutableCaptureLink | None,
    ) -> bool:
        return (
            self.outcome is outcome
            and self.operation_state_version == operation.state_version
            and self.capture_state_version
            == (capture.state_version if capture is not None else None)
            and self.analysis_state_version
            == (analysis.state_version if analysis is not None else None)
            and self.capture_link_key == (link.capture_link_key if link is not None else None)
        )


class QuotaConservationSnapshot(FrozenProtocolModel):
    requested_units: int = Field(strict=True, ge=1)
    reserved_units: int = Field(strict=True, ge=0)
    consumed_units: int = Field(strict=True, ge=0)
    unknown_units: int = Field(strict=True, ge=0)
    released_units: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def units_are_conserved(self) -> Self:
        accounted = (
            self.reserved_units + self.consumed_units + self.unknown_units + self.released_units
        )
        if accounted != self.requested_units:
            raise ValueError("quota_units_not_conserved")
        return self

    @property
    def explicitly_retained(self) -> bool:
        return self.reserved_units > 0


class PreparationCoordinatorResult(FrozenProtocolModel):
    prepared: PreparedSubmissionRef
    operation: SubmissionOperationTruth
    quota: QuotaConservationSnapshot


class AtomicPreparationResult(FrozenProtocolModel):
    """Durable truth returned by the operation + quota reservation transaction."""

    operation: SubmissionOperationTruth
    reservation_pub_id: OpaqueId
    quota: QuotaConservationSnapshot

    @model_validator(mode="after")
    def reservation_is_live(self) -> Self:
        if not self.quota.explicitly_retained:
            raise ValueError("atomic_preparation_requires_reserved_quota")
        return self


class CoordinatorResult(FrozenProtocolModel):
    operation: SubmissionOperationTruth
    capture: CaptureTruth | None = None
    capture_link: ImmutableCaptureLink | None = None
    analysis: AnalysisTruth | None = None
    fact: SlotOutcomeFact
    quota: QuotaConservationSnapshot
    no_submit: NoSubmitDecision | None = None


class PreflightGateway(Protocol):
    """Read-only owner preflight; this object has no submit capability."""

    def preflight(self, command: PreflightCommand) -> PreflightObservation: ...


class SubmitOnceGateway(Protocol):
    """The only port that can cross the irreversible submit boundary."""

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition: ...


class ReconciliationGateway(Protocol):
    """Evidence-only recovery port; intentionally has no ``submit_once`` method."""

    def observe_sending(self, operation: SubmissionOperationTruth) -> ReconciliationEvidence: ...

    def reconcile_sending(
        self, command: SendingReconciliationCommand
    ) -> ReconciliationDisposition: ...


class CaptureGateway(Protocol):
    """Capture an existing result; intentionally has no submit method."""

    def capture_existing(self, command: CaptureExistingCommand) -> CaptureDisposition: ...


class AnalysisGateway(Protocol):
    """Analyze an immutable capture; intentionally has no submit method."""

    def analyze_existing_capture(self, command: AnalysisCommand) -> AnalysisDisposition: ...


class OutboxPublisher(Protocol):
    def publish(self, event: OutboxEventRef) -> None: ...


class DurableSubmissionRepository(Protocol):
    """Transactional persistence boundary required by the coordinator.

    A production implementation is schema-gated.  The methods named ``atomic``
    must each commit all represented effects or none of them.
    """

    def capabilities(self) -> RepositoryCapabilities: ...

    def resolve_preparation_context(self, work: PrepareWorkItem) -> ResolvedPreparationContext: ...

    def resolve_context(self, work: SubmissionWorkItem) -> ResolvedSubmissionContext: ...

    def load_operation(self, operation: OperationRef) -> SubmissionOperationTruth | None: ...

    def atomic_prepare_and_reserve(
        self, work: PrepareWorkItem, prepared: PrepareResult
    ) -> AtomicPreparationResult: ...

    def assert_operation_integrity(
        self, work: SubmissionWorkItem, operation: SubmissionOperationTruth
    ) -> None: ...

    def compare_and_swap(self, command: OwnerClaimCasCommand) -> OwnerClaimCasObservation: ...

    def claim_reconciliation(
        self,
        *,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
    ) -> DurableReconciliationClaim: ...

    def atomic_terminal_and_quota(
        self, work: SubmissionWorkItem, transition: TerminalSubmissionTransition
    ) -> SubmissionOperationTruth: ...

    def load_capture(self, operation: OperationRef) -> CaptureTruth | None: ...

    def store_capture(
        self,
        *,
        expected_state_version: int | None,
        capture: CaptureTruth,
    ) -> CaptureTruth: ...

    def start_or_resume_capture_attempt(
        self,
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        capture: CaptureTruth,
        requested_at: datetime,
    ) -> DurableCaptureAttempt: ...

    def resolve_capture_attempt(
        self,
        *,
        attempt: DurableCaptureAttempt,
        raw: CaptureDisposition,
        normalized: CaptureDisposition,
    ) -> CaptureTruth: ...

    def load_capture_link(self, operation: OperationRef) -> ImmutableCaptureLink | None: ...

    def store_capture_link(self, link: ImmutableCaptureLink) -> ImmutableCaptureLink: ...

    def load_analysis(self, operation: OperationRef) -> AnalysisTruth | None: ...

    def store_analysis(
        self,
        *,
        expected_state_version: int | None,
        analysis: AnalysisTruth,
    ) -> AnalysisTruth: ...

    def queue_or_resume_analysis_attempt(
        self,
        *,
        analysis: AnalysisTruth,
        command: AnalysisCommand,
    ) -> DurableAnalysisAttempt: ...

    def load_active_analysis_attempt(
        self, operation: OperationRef
    ) -> DurableAnalysisAttempt | None: ...

    def load_fact(self, operation: OperationRef) -> SlotOutcomeFact | None: ...

    def atomic_fact_and_outbox(
        self,
        work: SubmissionWorkItem,
        fact: SlotOutcomeFact,
        outbox: OutboxEventRef,
    ) -> SlotOutcomeFact: ...

    def pending_outbox(self, operation: OperationRef) -> tuple[OutboxEventRef, ...]: ...

    def mark_outbox_published(self, outbox_key: str) -> None: ...

    def quota_snapshot(self, reservation_pub_id: str) -> QuotaConservationSnapshot: ...


def _validate_context(work: SubmissionWorkItem, context: ResolvedSubmissionContext) -> None:
    expected_ref = work.workflow.operation
    if operation_ref(context.prepare.identity) != expected_ref:
        raise SubmissionCoordinatorError("resolved_operation_reference_mismatch")
    if context.authority.grant_pub_id != work.grant_pub_id:
        raise SubmissionCoordinatorError("resolved_grant_reference_mismatch")
    resolved_leases = tuple(fence.lease_pub_id for fence in context.authority.lease_fences)
    if set(resolved_leases) != set(work.lease_pub_ids):
        raise SubmissionCoordinatorError("resolved_lease_reference_mismatch")


def _validate_preparation_context(
    work: PrepareWorkItem, context: ResolvedPreparationContext
) -> None:
    if operation_ref(context.prepare.identity) != work.workflow.operation:
        raise SubmissionCoordinatorError("resolved_preparation_operation_mismatch")
    if context.prepare.identity.request_manifest.request_payload_ref != work.request_manifest_ref:
        raise SubmissionCoordinatorError("resolved_request_manifest_reference_mismatch")


def _outcome_payload_sha256(fact: SlotOutcomeFact) -> str:
    return sha256(canonical_json(fact).encode()).hexdigest()


def _outcome_outbox(fact: SlotOutcomeFact) -> OutboxEventRef:
    event_type = "collection.slot.outcome"
    payload_sha256 = _outcome_payload_sha256(fact)
    return OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type=event_type,
            aggregate_ref=fact.operation.operation_pub_id,
            aggregate_version=fact.fact_version,
            payload_sha256=payload_sha256,
        ),
        event_type=event_type,
        aggregate_ref=fact.operation.operation_pub_id,
        aggregate_version=fact.fact_version,
        payload_sha256=payload_sha256,
        occurred_at=fact.recorded_at,
    )


def _is_final_primary(outcome: SlotOutcome) -> bool:
    """Only an accepted complete capture may become the formal primary fact."""

    return outcome is SlotOutcome.CONFIRMED_SENT_CAPTURE_COMPLETE


class _FactAndOutbox:
    def __init__(
        self,
        repository: DurableSubmissionRepository,
        publisher: OutboxPublisher,
        clock: Clock,
        crash_hook: CrashHook,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._clock = clock
        self._crash_hook = crash_hook

    def record_and_publish(
        self,
        *,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
        capture: CaptureTruth | None,
        analysis: AnalysisTruth | None,
        link: ImmutableCaptureLink | None,
    ) -> SlotOutcomeFact:
        outcome = derive_slot_outcome(operation, capture=capture, analysis=analysis)
        existing = self._repository.load_fact(operation_ref(operation.identity))
        if existing is not None and existing.same_basis(
            outcome=outcome,
            operation=operation,
            capture=capture,
            analysis=analysis,
            link=link,
        ):
            fact = existing
        else:
            fact = SlotOutcomeFact(
                operation=operation_ref(operation.identity),
                outcome=outcome,
                operation_state_version=operation.state_version,
                capture_state_version=(capture.state_version if capture is not None else None),
                analysis_state_version=(analysis.state_version if analysis is not None else None),
                capture_link_key=(link.capture_link_key if link is not None else None),
                is_final_primary=_is_final_primary(outcome),
                fact_version=1 if existing is None else existing.fact_version + 1,
                recorded_at=self._clock.now(),
            )
        # Exact replay revalidates the fact/outbox pair.  A repository may
        # atomically repair a missing outbox for an exact fact, but must reject
        # any conflicting payload or partially committed newer fact.
        fact = self._repository.atomic_fact_and_outbox(
            work,
            fact,
            _outcome_outbox(fact),
        )
        self._crash_hook.checkpoint(CrashPoint.AFTER_FACT_BEFORE_OUTBOX_PUBLISH)
        for event in self._repository.pending_outbox(fact.operation):
            self._publisher.publish(event)
            self._crash_hook.checkpoint(CrashPoint.AFTER_OUTBOX_PUBLISH_BEFORE_MARK)
            self._repository.mark_outbox_published(event.outbox_key)
        return fact


class CaptureCoordinator:
    """Resume capture/link work without holding any submit capability."""

    def __init__(
        self,
        repository: DurableSubmissionRepository,
        gateway: CaptureGateway,
        outbox_publisher: OutboxPublisher,
        clock: Clock,
        crash_hook: CrashHook,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._clock = clock
        self._crash_hook = crash_hook
        self._facts = _FactAndOutbox(repository, outbox_publisher, clock, crash_hook)

    def resume(
        self,
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        operation: SubmissionOperationTruth,
        explicit_retry: bool = False,
    ) -> tuple[CaptureTruth, ImmutableCaptureLink | None]:
        if operation.send_state not in {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}:
            raise SubmissionCoordinatorError("capture_requires_sent_or_unknown")
        ref = operation_ref(operation.identity)
        capture = self._repository.load_capture(ref)
        if capture is None:
            capture = self._repository.store_capture(
                expected_state_version=None,
                capture=initial_capture_truth(operation),
            )

        retryable = {
            CaptureState.PARTIAL,
            CaptureState.FAILED,
            CaptureState.NOT_OBSERVABLE,
        }
        if explicit_retry and capture.capture_state is CaptureState.COMPLETED:
            raise SubmissionCoordinatorError("completed_capture_is_immutable_final")
        if (
            explicit_retry
            and capture.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
        ):
            raise SubmissionCoordinatorError("quarantined_surface_mismatch_is_not_retryable")
        should_drive = capture.capture_state in {
            CaptureState.NOT_STARTED,
            CaptureState.CAPTURING,
        } or (explicit_retry and capture.capture_state in retryable)
        if should_drive:
            attempt = self._repository.start_or_resume_capture_attempt(
                work=work,
                context=context,
                capture=capture,
                requested_at=self._clock.now(),
            )
            raw = self._gateway.capture_existing(attempt.command)
            normalized = normalize_capture(attempt.command, raw)
            expected_capture = apply_capture_disposition(attempt.capture, normalized)
            capture = self._repository.resolve_capture_attempt(
                attempt=attempt,
                raw=raw,
                normalized=normalized,
            )
            if capture != expected_capture:
                raise SubmissionCoordinatorError("capture_resolution_exact_replay_mismatch")

        link = self._repository.load_capture_link(ref)
        if capture.capture_state in {CaptureState.COMPLETED, CaptureState.PARTIAL} and (
            link is None or link.capture_state_version != capture.state_version
        ):
            if capture.staging is None:
                raise SubmissionCoordinatorError("captured_staging_reference_missing")
            self._crash_hook.checkpoint(CrashPoint.AFTER_STAGING_BEFORE_LINK)
            link = self._repository.store_capture_link(
                link_immutable_capture(
                    capture,
                    linked_at=max(
                        self._clock.now(),
                        capture.updated_at,
                        capture.staging.staged_at,
                    ),
                )
            )
        return capture, link

    def retry(
        self,
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
    ) -> CoordinatorResult:
        """Explicitly retry a retryable capture and publish its new fact atomically."""

        operation = self._repository.load_operation(work.workflow.operation)
        if operation is None:
            raise SubmissionCoordinatorError("capture_retry_operation_missing")
        capture, link = self.resume(
            work=work,
            context=context,
            operation=operation,
            explicit_retry=True,
        )
        fact = self._facts.record_and_publish(
            work=work,
            operation=operation,
            capture=capture,
            analysis=None,
            link=link,
        )
        return CoordinatorResult(
            operation=operation,
            capture=capture,
            capture_link=link,
            fact=fact,
            quota=self._repository.quota_snapshot(work.reservation_pub_id),
        )


class PreparationCoordinator:
    """Atomically establish operation + quota before any resource authority exists."""

    def __init__(
        self,
        repository: DurableSubmissionRepository,
        crash_hook: CrashHook | None = None,
    ) -> None:
        self._repository = repository
        self._crash_hook = crash_hook or NoOpCrashHook()

    def run(self, work: PrepareWorkItem) -> PreparationCoordinatorResult:
        self._require_durability()
        context = self._repository.resolve_preparation_context(work)
        _validate_preparation_context(work, context)
        existing = self._repository.load_operation(work.workflow.operation)
        prepared = prepare_submission(context.prepare, existing=existing)
        self._crash_hook.checkpoint(CrashPoint.BEFORE_RESERVE)
        durable = self._repository.atomic_prepare_and_reserve(work, prepared)
        if durable.operation != prepared.operation:
            raise SubmissionCoordinatorError("atomic_preparation_truth_mismatch")
        self._crash_hook.checkpoint(CrashPoint.AFTER_RESERVE)
        return PreparationCoordinatorResult(
            prepared=PreparedSubmissionRef(
                workflow=work.workflow,
                reservation_pub_id=durable.reservation_pub_id,
            ),
            operation=durable.operation,
            quota=durable.quota,
        )

    def _require_durability(self) -> None:
        actual = self._repository.capabilities().model_dump(mode="python")
        required = PREPARATION_REQUIRED_DURABILITY.model_dump(mode="python")
        missing = tuple(name for name, needed in required.items() if needed and not actual[name])
        if missing:
            raise SubmissionCoordinatorError(
                "preparation_schema_capabilities_missing:" + ",".join(sorted(missing))
            )


class SubmissionCoordinator:
    """Prepare, claim once, terminalize, capture, and publish durable effects."""

    def __init__(
        self,
        repository: DurableSubmissionRepository,
        preflight_gateway: PreflightGateway,
        submit_gateway: SubmitOnceGateway,
        reconciliation_gateway: ReconciliationGateway,
        capture_gateway: CaptureGateway,
        outbox_publisher: OutboxPublisher,
        clock: Clock,
        crash_hook: CrashHook | None = None,
    ) -> None:
        self._repository = repository
        self._preflight_gateway = preflight_gateway
        self._submit_gateway = submit_gateway
        self._reconciliation_gateway = reconciliation_gateway
        self._clock = clock
        self._crash_hook = crash_hook or NoOpCrashHook()
        self._capture = CaptureCoordinator(
            repository,
            capture_gateway,
            outbox_publisher,
            clock,
            self._crash_hook,
        )
        self._facts = _FactAndOutbox(
            repository,
            outbox_publisher,
            clock,
            self._crash_hook,
        )

    def run(self, work: SubmissionWorkItem) -> CoordinatorResult:
        self._require_durability()
        operation = self._repository.load_operation(work.workflow.operation)
        if operation is None:
            raise SubmissionCoordinatorError("prepared_operation_missing")
        self._repository.assert_operation_integrity(work, operation)
        context = self._repository.resolve_context(work)
        _validate_context(work, context)
        prepare_submission(context.prepare, existing=operation)

        no_submit: NoSubmitDecision | None = None
        if operation.send_state is SendState.NOT_SENT:
            operation, no_submit = self._attempt_fresh_submit(work, context, operation)
        if operation.send_state is SendState.SENDING:
            reconciliation_claim = self._repository.claim_reconciliation(
                work=work,
                operation=operation,
            )
            if not reconciliation_claim.acquired:
                raise SubmissionCoordinatorError("sending_owner_still_active_retryable")
            operation = self._reconcile(work, operation)

        capture: CaptureTruth | None = None
        link: ImmutableCaptureLink | None = None
        if operation.send_state in {SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN}:
            self._crash_hook.checkpoint(CrashPoint.AFTER_TERMINAL_BEFORE_CAPTURE)
            capture, link = self._capture.resume(
                work=work,
                context=context,
                operation=operation,
            )

        fact = self._facts.record_and_publish(
            work=work,
            operation=operation,
            capture=capture,
            analysis=None,
            link=link,
        )
        return CoordinatorResult(
            operation=operation,
            capture=capture,
            capture_link=link,
            fact=fact,
            quota=self._repository.quota_snapshot(work.reservation_pub_id),
            no_submit=no_submit,
        )

    def _attempt_fresh_submit(
        self,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        operation: SubmissionOperationTruth,
    ) -> tuple[SubmissionOperationTruth, NoSubmitDecision | None]:
        preflight = PreflightCommand(
            operation=work.workflow.operation,
            expected_state_version=operation.state_version,
            authority=context.authority,
        )
        verified = verify_preflight(
            operation,
            preflight,
            self._preflight_gateway.preflight(preflight),
        )
        if verified.observation.decision is PreflightDecision.CONFIRMED_NOT_SENT:
            transition = apply_preflight_not_sent(operation, verified)
            return self._repository.atomic_terminal_and_quota(work, transition), None

        plan = plan_owner_claim(
            operation,
            verified,
            claim_pub_id=work.claim_pub_id,
            owner_dispatch_ref=context.owner_dispatch_ref,
            owner_wal_evidence_sha256=context.owner_wal_evidence_sha256,
            claimed_at=self._clock.now(),
        )
        if isinstance(plan, NoSubmitDecision):
            latest = self._repository.load_operation(work.workflow.operation)
            if latest is None:
                raise SubmissionCoordinatorError("claim_recovery_operation_missing")
            return latest, plan

        self._crash_hook.checkpoint(CrashPoint.BEFORE_OWNER_CAS)
        claim_result = confirm_owner_claim(plan, self._repository.compare_and_swap(plan.cas))
        if isinstance(claim_result, NoSubmitDecision):
            latest = self._repository.load_operation(work.workflow.operation)
            if latest is None:
                raise SubmissionCoordinatorError("claim_cas_operation_missing")
            return latest, claim_result

        self._crash_hook.checkpoint(CrashPoint.AFTER_OWNER_CAS_BEFORE_SUBMIT)
        command = SubmitOnceCommand(
            fresh_claim=claim_result,
            request_manifest=operation.identity.request_manifest,
            request_manifest_sha256=operation.identity.request_manifest_sha256,
            provider_idempotency_key=operation.identity.provider_idempotency_key,
        )
        disposition = self._submit_gateway.submit_once(command)
        self._crash_hook.checkpoint(CrashPoint.AFTER_SUBMIT_BEFORE_ACK)
        sending = self._repository.load_operation(work.workflow.operation)
        if sending is None:
            raise SubmissionCoordinatorError("sending_operation_missing")
        transition = apply_submit_disposition(sending, command, disposition)
        return self._repository.atomic_terminal_and_quota(work, transition), None

    def _reconcile(
        self,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
    ) -> SubmissionOperationTruth:
        claim = operation.claim
        if claim is None:
            raise SubmissionCoordinatorError("sending_claim_missing")
        evidence = self._reconciliation_gateway.observe_sending(operation)
        command = SendingReconciliationCommand(
            operation=work.workflow.operation,
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
        disposition = self._reconciliation_gateway.reconcile_sending(command)
        return self._repository.atomic_terminal_and_quota(
            work,
            reconcile_sending(operation, command, disposition),
        )

    def _require_durability(self) -> None:
        actual = self._repository.capabilities().model_dump(mode="python")
        required = SUBMISSION_REQUIRED_DURABILITY.model_dump(mode="python")
        missing = tuple(name for name, needed in required.items() if needed and not actual[name])
        if missing:
            raise SubmissionCoordinatorError(
                "schema_capabilities_missing:" + ",".join(sorted(missing))
            )


class AnalysisCoordinator:
    """Retry analysis from an immutable capture without any submit dependency."""

    def __init__(
        self,
        repository: DurableSubmissionRepository,
        gateway: AnalysisGateway,
        outbox_publisher: OutboxPublisher,
        clock: Clock,
        crash_hook: CrashHook | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._clock = clock
        self._crash_hook = crash_hook or NoOpCrashHook()
        self._facts = _FactAndOutbox(
            repository,
            outbox_publisher,
            clock,
            self._crash_hook,
        )

    def retry(
        self,
        *,
        work: SubmissionWorkItem,
        attempt_ref: str,
        analyzer_revision: str,
        analysis_policy_revision: str,
    ) -> CoordinatorResult:
        self._require_durability()
        operation = self._repository.load_operation(work.workflow.operation)
        capture = self._repository.load_capture(work.workflow.operation)
        link = self._repository.load_capture_link(work.workflow.operation)
        if operation is None or capture is None or link is None:
            raise SubmissionCoordinatorError("analysis_requires_immutable_capture")
        analysis = self._repository.load_analysis(work.workflow.operation)
        if analysis is None:
            analysis = self._repository.store_analysis(
                expected_state_version=None,
                analysis=initial_analysis_truth(link),
            )
        if analysis.analysis_state is AnalysisState.COMPLETED:
            raise SubmissionCoordinatorError("completed_analysis_is_immutable_final")
        if analysis.analysis_state in {AnalysisState.QUEUED, AnalysisState.RUNNING}:
            attempt = self._repository.load_active_analysis_attempt(work.workflow.operation)
            if attempt is None:
                raise SubmissionCoordinatorError("active_analysis_command_missing")
            command = attempt.command
            if (
                command.attempt_ref != attempt_ref
                or command.analyzer_revision != analyzer_revision
                or command.analysis_policy_revision != analysis_policy_revision
            ):
                raise SubmissionCoordinatorError("active_analysis_attempt_mismatch")
            analysis = attempt.analysis
        else:
            command = AnalysisCommand(
                capture_link_key=link.capture_link_key,
                capture_content_sha256=link.content_sha256,
                expected_analysis_version=analysis.state_version,
                attempt_ref=attempt_ref,
                analyzer_revision=analyzer_revision,
                analysis_policy_revision=analysis_policy_revision,
                requested_at=self._clock.now(),
            )
            attempt = self._repository.queue_or_resume_analysis_attempt(
                analysis=analysis,
                command=command,
            )
            analysis = attempt.analysis
        self._crash_hook.checkpoint(CrashPoint.AFTER_ANALYSIS_QUEUE)
        if analysis.analysis_state is AnalysisState.QUEUED:
            before_start = analysis.state_version
            analysis = self._repository.store_analysis(
                expected_state_version=before_start,
                analysis=start_analysis(
                    analysis,
                    attempt_ref=command.attempt_ref,
                    started_at=self._clock.now(),
                ),
            )
        if analysis.analysis_state is not AnalysisState.RUNNING:
            raise SubmissionCoordinatorError("analysis_attempt_not_running")
        self._crash_hook.checkpoint(CrashPoint.AFTER_ANALYSIS_START)
        disposition = self._gateway.analyze_existing_capture(command)
        self._crash_hook.checkpoint(CrashPoint.AFTER_ANALYSIS_GATEWAY)
        before_terminal = analysis.state_version
        analysis = self._repository.store_analysis(
            expected_state_version=before_terminal,
            analysis=apply_analysis_disposition(analysis, disposition),
        )
        fact = self._facts.record_and_publish(
            work=work,
            operation=operation,
            capture=capture,
            analysis=analysis,
            link=link,
        )
        return CoordinatorResult(
            operation=operation,
            capture=capture,
            capture_link=link,
            analysis=analysis,
            fact=fact,
            quota=self._repository.quota_snapshot(work.reservation_pub_id),
        )

    def _require_durability(self) -> None:
        actual = self._repository.capabilities().model_dump(mode="python")
        required = ANALYSIS_REQUIRED_DURABILITY.model_dump(mode="python")
        missing = tuple(name for name, needed in required.items() if needed and not actual[name])
        if missing:
            raise SubmissionCoordinatorError(
                "analysis_schema_capabilities_missing:" + ",".join(sorted(missing))
            )
