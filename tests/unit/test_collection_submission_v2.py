from __future__ import annotations

import ast
import inspect
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import pytest
from geo_platform.collection.submission_v2 import (
    AnalysisCoordinator,
    CaptureCoordinator,
    CoordinatorResult,
    CrashHook,
    CrashPoint,
    DurableAnalysisAttempt,
    DurableCaptureAttempt,
    DurableReconciliationClaim,
    PreparationCoordinator,
    PreparedSubmissionRef,
    PrepareWorkItem,
    QuotaConservationSnapshot,
    ReconciliationEvidence,
    RepositoryCapabilities,
    ResolvedPreparationContext,
    ResolvedSubmissionContext,
    SlotOutcomeFact,
    SubmissionCoordinator,
    SubmissionCoordinatorError,
    SubmissionWorkItem,
    SubmitOnceGateway,
)
from pydantic import ValidationError

from domain.collection.submission import (
    AnalysisCommand,
    AnalysisDisposition,
    AnalysisTruth,
    CaptureDisposition,
    CaptureExistingCommand,
    CaptureNormalizationDecision,
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
    OwnerClaimCasObservation,
    OwnerClaimCasStatus,
    PreflightCommand,
    PreflightDecision,
    PreflightObservation,
    PrepareResult,
    PrepareSubmissionCommand,
    QuotaTerminalEffect,
    ReconciliationDisposition,
    RequestManifest,
    SendingReconciliationCommand,
    SlotOutcome,
    SubmissionOperationTruth,
    SubmitDisposition,
    SubmitOnceCommand,
    SurfaceProductRef,
    TerminalReason,
    TerminalSubmissionTransition,
    WorkflowOperationInput,
    apply_analysis_disposition,
    apply_capture_disposition,
    authority_digest,
    begin_capture,
    canonical_json,
    derive_slot_outcome,
    initial_analysis_truth,
    lease_fence_set_digest,
    normalize_capture,
    operation_ref,
    queue_analysis,
    start_analysis,
)
from domain.collection.surface import AnalysisState, CaptureState, CollectionSurface, SendState

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000101")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000102")
HASH_A = sha256(b"a").hexdigest()
HASH_B = sha256(b"b").hexdigest()
HASH_C = sha256(b"c").hexdigest()


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class StepClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class InjectedCrash(RuntimeError):
    pass


class OwnerProcessCrash(RuntimeError):
    pass


class OneShotCrashHook(CrashHook):
    def __init__(self, point: CrashPoint) -> None:
        self.point = point
        self.triggered = False

    def checkpoint(self, point: CrashPoint) -> None:
        if point is self.point and not self.triggered:
            self.triggered = True
            raise InjectedCrash(point.value)


class NoCrashHook(CrashHook):
    def checkpoint(self, point: CrashPoint) -> None:
        del point


def _surface_product(surface: CollectionSurface) -> SurfaceProductRef:
    dimensions = {
        CollectionSurface.PROVIDER_API: ("openai", "responses"),
        CollectionSurface.CONSUMER_WEB: ("doubao", "web-chat"),
        CollectionSurface.CONSUMER_APP: ("doubao", "android-chat"),
    }
    platform, variant = dimensions[surface]
    target_key = (
        f"collection-target-v1|platform={platform}|collection_surface={surface.value}|"
        f"product_variant={variant}"
    )
    return SurfaceProductRef(
        platform=platform,
        collection_surface=surface,
        product_variant=variant,
        target_key=target_key,
    )


def _identity(surface: CollectionSurface) -> OperationIdentity:
    product = _surface_product(surface)
    material = OperationKeyMaterial(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        campaign_pub_id="campaign-coordinator-1",
        slot_pub_id=f"slot-{surface.value}",
        target_key=product.target_key,
        leg_key=f"leg-{surface.value}",
        logical_item_key=f"logical-item-{surface.value}",
        generation=1,
        operation_policy_revision="operation-policy-v1",
    )
    manifest = RequestManifest(
        request_protocol_version="provider-request-v1",
        request_schema_revision="request-schema-v1",
        request_payload_ref=f"payload-{surface.value}",
        request_payload_sha256=_hash({"surface": surface.value}),
    )
    from domain.collection.submission import (
        deterministic_operation_key,
        deterministic_provider_idempotency_key,
        request_manifest_digest,
    )

    operation_key = deterministic_operation_key(material)
    return OperationIdentity(
        material=material,
        surface_product=product,
        operation_pub_id=f"operation-{surface.value}",
        operation_key=operation_key,
        request_manifest=manifest,
        request_manifest_sha256=request_manifest_digest(manifest),
        provider_idempotency_key=deterministic_provider_idempotency_key(operation_key),
    )


def _authority(surface: CollectionSurface) -> OwnerAuthorityRef:
    role = {
        CollectionSurface.PROVIDER_API: "credential",
        CollectionSurface.CONSUMER_WEB: "web_session",
        CollectionSurface.CONSUMER_APP: "app_session",
    }[surface]
    fences = (
        LeaseFenceRef(
            lease_pub_id=f"lease-{surface.value}",
            binding_resource_pub_id=f"resource-{surface.value}",
            resource_role=role,
            owner_handle=f"owner-{surface.value}",
            generation=7,
            acquired_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=2),
        ),
    )
    return OwnerAuthorityRef(
        grant_pub_id=f"grant-{surface.value}",
        grant_revision=3,
        binding_revision_pub_id=f"binding-{surface.value}",
        owner_handle=f"owner-{surface.value}",
        checked_at=NOW,
        valid_until=NOW + timedelta(hours=1),
        lease_fences=fences,
        fence_set_sha256=lease_fence_set_digest(fences),
    )


class BoundaryState(StrEnum):
    NOT_ENTERED = "not_entered"
    ENTERED = "entered"


@dataclass
class DurableLease:
    fence: LeaseFenceRef
    status: Literal["active", "terminated"] = "active"
    terminated_at: datetime | None = None


@dataclass
class DurableOwnerWal:
    owner_dispatch_ref: str
    wal_evidence_sha256: str
    authority_sha256: str
    fence_set_sha256: str
    boundary: BoundaryState = BoundaryState.NOT_ENTERED
    owner_session_active: bool = True
    provider_submission_ref: str | None = None
    provider_evidence_ref: str | None = None
    provider_evidence_sha256: str | None = None
    provider_provenance: dict[str, str] = field(default_factory=dict)
    submit_invocations: int = 0
    reconciliation_claim_ref: str | None = None


class DurableOwnerStore:
    def __init__(self, authority: OwnerAuthorityRef, dispatch_ref: str) -> None:
        wal_payload = {
            "authority_sha256": authority_digest(authority),
            "dispatch_ref": dispatch_ref,
            "fence_set_sha256": authority.fence_set_sha256,
            "version": "owner-submit-wal-v1",
        }
        self.wal = DurableOwnerWal(
            owner_dispatch_ref=dispatch_ref,
            wal_evidence_sha256=_hash(wal_payload),
            authority_sha256=authority_digest(authority),
            fence_set_sha256=authority.fence_set_sha256,
        )
        self.leases = {
            fence.lease_pub_id: DurableLease(fence=fence) for fence in authority.lease_fences
        }

    def mark_owner_dead(self) -> None:
        self.wal.owner_session_active = False

    def exact_leases_active(self) -> bool:
        return all(lease.status == "active" for lease in self.leases.values())

    def terminate_exact_leases(self, *, at: datetime) -> str:
        for lease in self.leases.values():
            lease.status = "terminated"
            lease.terminated_at = at
        if not all(lease.status == "terminated" for lease in self.leases.values()):
            raise SubmissionCoordinatorError("reconciliation_lease_termination_incomplete")
        fences = tuple(lease.fence for lease in self.leases.values())
        return lease_fence_set_digest(fences)


@dataclass(frozen=True)
class QuotaEffect:
    scope_kind: str
    scope_ref: str
    units: int
    state: Literal["reserved", "consumed", "unknown", "released"]


@dataclass(frozen=True)
class QuotaLedgerEntry:
    event_key: str
    scope_kind: str
    scope_ref: str
    units: int
    effect: str


class DurableQuotaStore:
    SCOPE_SPECS = (
        ("provider", "provider-openai"),
        ("project", "project-coordinator"),
        ("mode", "mode-normal"),
    )

    def __init__(self, reservation_ref: str) -> None:
        self.reservation_ref = reservation_ref
        self.effects: dict[tuple[str, str], QuotaEffect] = {}
        self.ledger: dict[str, QuotaLedgerEntry] = {}
        self.effect_set_sha256: str | None = None

    def _planned_hash(self) -> str:
        return _hash(
            {
                "effects": [
                    {"scope_kind": kind, "scope_ref": ref, "units": 1}
                    for kind, ref in self.SCOPE_SPECS
                ],
                "version": "quota-effect-set-v1",
            }
        )

    def _append(self, entry: QuotaLedgerEntry) -> None:
        existing = self.ledger.get(entry.event_key)
        if existing is not None and existing != entry:
            raise SubmissionCoordinatorError("quota_ledger_event_conflict")
        self.ledger[entry.event_key] = entry

    def reserve(self) -> None:
        if self.effects:
            self.validate("reserved")
            return
        self.effect_set_sha256 = self._planned_hash()
        for kind, ref in self.SCOPE_SPECS:
            key = (kind, ref)
            self.effects[key] = QuotaEffect(kind, ref, 1, "reserved")
            self._append(
                QuotaLedgerEntry(
                    event_key=f"{self.reservation_ref}:{kind}:{ref}:reserve",
                    scope_kind=kind,
                    scope_ref=ref,
                    units=1,
                    effect="reserve",
                )
            )

    def terminalize(self, effect: QuotaTerminalEffect) -> None:
        state_by_effect: dict[QuotaTerminalEffect, Literal["consumed", "unknown", "released"]] = {
            QuotaTerminalEffect.SETTLE_CONSUMED: "consumed",
            QuotaTerminalEffect.SETTLE_UNKNOWN: "unknown",
            QuotaTerminalEffect.RELEASE: "released",
        }
        target = state_by_effect[effect]
        if self.effects and all(item.state == target for item in self.effects.values()):
            self.validate(target)
            return
        self.validate("reserved")
        for key, item in tuple(self.effects.items()):
            self.effects[key] = QuotaEffect(
                scope_kind=item.scope_kind,
                scope_ref=item.scope_ref,
                units=item.units,
                state=target,
            )
            self._append(
                QuotaLedgerEntry(
                    event_key=(
                        f"{self.reservation_ref}:{item.scope_kind}:{item.scope_ref}:{target}"
                    ),
                    scope_kind=item.scope_kind,
                    scope_ref=item.scope_ref,
                    units=item.units,
                    effect=target,
                )
            )
        self.validate(target)

    def validate(self, expected_state: str) -> None:
        if self.effect_set_sha256 != self._planned_hash():
            raise SubmissionCoordinatorError("quota_effect_set_hash_mismatch")
        expected_keys = set(self.SCOPE_SPECS)
        if set(self.effects) != expected_keys:
            raise SubmissionCoordinatorError("quota_effect_coverage_mismatch")
        for key, item in self.effects.items():
            if item.units != 1 or item.state != expected_state:
                raise SubmissionCoordinatorError("quota_effect_state_mismatch")
            reserve_key = f"{self.reservation_ref}:{key[0]}:{key[1]}:reserve"
            reserve = self.ledger.get(reserve_key)
            if reserve is None or reserve.effect != "reserve" or reserve.units != 1:
                raise SubmissionCoordinatorError("quota_reserve_ledger_missing")
            terminal_keys = {
                state: f"{self.reservation_ref}:{key[0]}:{key[1]}:{state}"
                for state in ("consumed", "unknown", "released")
            }
            present_terminal = [
                state for state, event_key in terminal_keys.items() if event_key in self.ledger
            ]
            if expected_state == "reserved":
                if present_terminal:
                    raise SubmissionCoordinatorError("quota_terminal_ledger_before_terminal")
            elif present_terminal != [expected_state]:
                raise SubmissionCoordinatorError("quota_terminal_ledger_mismatch")
        expected_ledger_count = len(expected_keys) * (1 if expected_state == "reserved" else 2)
        if len(self.ledger) != expected_ledger_count:
            raise SubmissionCoordinatorError("quota_ledger_cardinality_mismatch")

    def snapshot(self) -> QuotaConservationSnapshot:
        if not self.effects:
            raise SubmissionCoordinatorError("quota_reservation_missing")
        states = {item.state for item in self.effects.values()}
        if len(states) != 1:
            raise SubmissionCoordinatorError("quota_effects_not_atomic")
        state = states.pop()
        self.validate(state)
        return QuotaConservationSnapshot(
            requested_units=1,
            reserved_units=1 if state == "reserved" else 0,
            consumed_units=1 if state == "consumed" else 0,
            unknown_units=1 if state == "unknown" else 0,
            released_units=1 if state == "released" else 0,
        )


@dataclass
class StagingLifecycle:
    staging: CaptureStagingRef
    attempt_ref: str
    state: Literal["staged", "linked", "quarantined"]
    quarantine_reason: str | None = None
    gc_after: datetime | None = None


@dataclass
class ConsumerStore:
    publish_attempts: dict[str, int] = field(default_factory=dict)
    effects: dict[str, OutboxEventRef] = field(default_factory=dict)


class FakeOutboxPublisher:
    def __init__(self, store: ConsumerStore) -> None:
        self.store = store

    def publish(self, event: OutboxEventRef) -> None:
        self.store.publish_attempts[event.outbox_key] = (
            self.store.publish_attempts.get(event.outbox_key, 0) + 1
        )
        existing = self.store.effects.get(event.outbox_key)
        if existing is not None and existing != event:
            raise SubmissionCoordinatorError("consumer_event_key_conflict")
        self.store.effects[event.outbox_key] = event


class InMemoryDurableSubmissionRepository:
    """Crash-persistent fake with the same atomic boundaries required from PostgreSQL."""

    def __init__(
        self,
        *,
        prepare_work: PrepareWorkItem,
        preparation_context: ResolvedPreparationContext,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        owner_store: DurableOwnerStore,
    ) -> None:
        self.prepare_work = prepare_work
        self.preparation_context = preparation_context
        self.work = work
        self.context = context
        self.owner_store = owner_store
        self.operations: dict[str, SubmissionOperationTruth] = {}
        self.quota = DurableQuotaStore(work.reservation_pub_id)
        self.terminal_transitions: dict[str, TerminalSubmissionTransition] = {}
        self.outboxes: dict[str, OutboxEventRef] = {}
        self.published_outboxes: set[str] = set()
        self.captures: dict[str, CaptureTruth] = {}
        self.capture_commands: dict[str, CaptureExistingCommand] = {}
        self.capture_attempts_used: set[str] = set()
        self.capture_links: dict[str, ImmutableCaptureLink] = {}
        self.current_capture_link: dict[str, str] = {}
        self.staging: dict[str, StagingLifecycle] = {}
        self.analyses: dict[str, AnalysisTruth] = {}
        self.analysis_commands: dict[str, AnalysisCommand] = {}
        self.analysis_attempts_used: set[str] = set()
        self.facts: dict[str, SlotOutcomeFact] = {}
        self.fact_history: dict[str, list[SlotOutcomeFact]] = {}
        self.current_primary_fact: dict[str, int] = {}
        self.force_cas_loss = False
        self.prepare_blocked = False
        self.capability_overrides: dict[str, bool] = {}

    @staticmethod
    def _operation_id(ref: OperationRef) -> str:
        return ref.operation_pub_id

    def capabilities(self) -> RepositoryCapabilities:
        values: dict[str, bool] = {
            "atomic_prepare_and_reserve": True,
            "durable_owner_claim_cas": True,
            "durable_owner_reconciliation": True,
            "exclusive_reconciliation_claim": True,
            "atomic_terminal_and_quota": True,
            "terminal_replay_integrity": True,
            "durable_capture_command": True,
            "immutable_capture_link": True,
            "durable_analysis_command": True,
            "atomic_fact_and_outbox": True,
            "idempotent_outbox_delivery": True,
            "quota_effect_ledger_conservation": True,
        }
        values.update(self.capability_overrides)
        return RepositoryCapabilities.model_validate(values)

    def resolve_context(self, work: SubmissionWorkItem) -> ResolvedSubmissionContext:
        if work != self.work:
            raise SubmissionCoordinatorError("unknown_work_item")
        operation = self.operations.get(work.workflow.operation.operation_pub_id)
        if operation is None:
            raise SubmissionCoordinatorError("authority_resolved_before_prepare")
        self.quota.snapshot()
        return self.context

    def resolve_preparation_context(self, work: PrepareWorkItem) -> ResolvedPreparationContext:
        if work != self.prepare_work:
            raise SubmissionCoordinatorError("unknown_prepare_work_item")
        return self.preparation_context

    def load_operation(self, operation: OperationRef) -> SubmissionOperationTruth | None:
        return self.operations.get(self._operation_id(operation))

    def atomic_prepare_and_reserve(
        self, work: PrepareWorkItem, prepared: PrepareResult
    ) -> SubmissionOperationTruth:
        if self.prepare_blocked:
            raise SubmissionCoordinatorError("prepare_blocked")
        if work != self.prepare_work:
            raise SubmissionCoordinatorError("unknown_prepare_work_item")
        operation_id = prepared.operation.identity.operation_pub_id
        existing = self.operations.get(operation_id)
        if existing is not None and existing != prepared.operation:
            raise SubmissionCoordinatorError("prepare_truth_conflict")
        # Validate every blocker before changing either logical truth or quota.
        if operation_id != work.workflow.operation.operation_pub_id:
            raise SubmissionCoordinatorError("prepare_operation_reference_mismatch")
        if work.reservation_pub_id != self.quota.reservation_ref:
            raise SubmissionCoordinatorError("reservation_reference_mismatch")
        self.quota.reserve()
        self.operations.setdefault(operation_id, prepared.operation)
        return self.operations[operation_id]

    def assert_operation_integrity(
        self, work: SubmissionWorkItem, operation: SubmissionOperationTruth
    ) -> None:
        if work != self.work:
            raise SubmissionCoordinatorError("unknown_work_item")
        operation_id = operation.identity.operation_pub_id
        if self.operations.get(operation_id) != operation:
            raise SubmissionCoordinatorError("operation_truth_mismatch")
        if operation.send_state in {SendState.NOT_SENT, SendState.SENDING}:
            if operation_id in self.terminal_transitions:
                raise SubmissionCoordinatorError("nonterminal_has_terminal_effect")
            self.quota.validate("reserved")
            return
        self._validate_terminal(operation)

    def compare_and_swap(self, command: OwnerClaimCasCommand) -> OwnerClaimCasObservation:
        operation_id = command.operation.operation_pub_id
        current = self.operations.get(operation_id)
        if current is None:
            raise SubmissionCoordinatorError("claim_operation_missing")
        if (
            current.send_state is SendState.NOT_SENT
            and current.state_version == command.expected_state_version
        ):
            sending = SubmissionOperationTruth(
                identity=current.identity,
                send_state=SendState.SENDING,
                state_version=command.next_state_version,
                prepared_at=current.prepared_at,
                claim=command.claim,
            )
            self.operations[operation_id] = sending
            return OwnerClaimCasObservation(
                status=(
                    OwnerClaimCasStatus.NOT_APPLIED
                    if self.force_cas_loss
                    else OwnerClaimCasStatus.FRESHLY_APPLIED
                ),
                persisted=sending,
            )
        return OwnerClaimCasObservation(
            status=OwnerClaimCasStatus.NOT_APPLIED,
            persisted=current,
        )

    def claim_reconciliation(
        self,
        *,
        work: SubmissionWorkItem,
        operation: SubmissionOperationTruth,
    ) -> DurableReconciliationClaim:
        if work != self.work or self.load_operation(operation_ref(operation.identity)) != operation:
            raise SubmissionCoordinatorError("reconciliation_operation_mismatch")
        claim = operation.claim
        if operation.send_state is not SendState.SENDING or claim is None:
            raise SubmissionCoordinatorError("reconciliation_requires_sending")
        wal = self.owner_store.wal
        if (
            wal.owner_dispatch_ref != claim.owner_dispatch_ref
            or wal.wal_evidence_sha256 != claim.owner_wal_evidence_sha256
            or wal.authority_sha256 != claim.authority_sha256
            or wal.fence_set_sha256 != claim.fence_set_sha256
        ):
            raise SubmissionCoordinatorError("reconciliation_wal_identity_mismatch")
        if wal.owner_session_active:
            return DurableReconciliationClaim(
                operation=operation_ref(operation.identity),
                reconciliation_claim_ref=work.reconciliation_claim_ref,
                owner_session_terminated=False,
                acquired=False,
            )
        if wal.reconciliation_claim_ref not in {None, work.reconciliation_claim_ref}:
            return DurableReconciliationClaim(
                operation=operation_ref(operation.identity),
                reconciliation_claim_ref=work.reconciliation_claim_ref,
                owner_session_terminated=True,
                acquired=False,
            )
        wal.reconciliation_claim_ref = work.reconciliation_claim_ref
        return DurableReconciliationClaim(
            operation=operation_ref(operation.identity),
            reconciliation_claim_ref=work.reconciliation_claim_ref,
            owner_session_terminated=True,
            acquired=True,
        )

    def atomic_terminal_and_quota(
        self, work: SubmissionWorkItem, transition: TerminalSubmissionTransition
    ) -> SubmissionOperationTruth:
        if work != self.work:
            raise SubmissionCoordinatorError("unknown_work_item")
        operation_id = transition.operation.identity.operation_pub_id
        current = self.operations.get(operation_id)
        if current is None:
            raise SubmissionCoordinatorError("terminal_operation_missing")
        if current.send_state not in {SendState.NOT_SENT, SendState.SENDING}:
            if current != transition.operation:
                raise SubmissionCoordinatorError("terminal_replay_truth_conflict")
            self._validate_terminal(current, expected=transition)
            return current
        if (
            transition.operation.state_version != current.state_version + 1
            or transition.operation.identity != current.identity
            or transition.operation.prepared_at != current.prepared_at
            or transition.operation.claim != current.claim
        ):
            raise SubmissionCoordinatorError("terminal_transition_basis_mismatch")
        existing_outbox = self.outboxes.get(transition.outbox.outbox_key)
        if existing_outbox is not None and existing_outbox != transition.outbox:
            raise SubmissionCoordinatorError("terminal_outbox_conflict")
        self.quota.validate("reserved")
        self.quota.terminalize(transition.quota_effect)
        self.operations[operation_id] = transition.operation
        self.terminal_transitions[operation_id] = transition
        self.outboxes[transition.outbox.outbox_key] = transition.outbox
        self._validate_terminal(transition.operation, expected=transition)
        return transition.operation

    def _validate_terminal(
        self,
        operation: SubmissionOperationTruth,
        *,
        expected: TerminalSubmissionTransition | None = None,
    ) -> None:
        operation_id = operation.identity.operation_pub_id
        transition = self.terminal_transitions.get(operation_id)
        if transition is None or transition.operation != operation:
            raise SubmissionCoordinatorError("terminal_transition_missing_or_conflicting")
        if expected is not None and transition != expected:
            raise SubmissionCoordinatorError("terminal_transition_replay_conflict")
        if self.outboxes.get(transition.outbox.outbox_key) != transition.outbox:
            raise SubmissionCoordinatorError("terminal_outbox_missing_or_conflicting")
        state_by_effect = {
            QuotaTerminalEffect.SETTLE_CONSUMED: "consumed",
            QuotaTerminalEffect.SETTLE_UNKNOWN: "unknown",
            QuotaTerminalEffect.RELEASE: "released",
        }
        self.quota.validate(state_by_effect[transition.quota_effect])

    def load_capture(self, operation: OperationRef) -> CaptureTruth | None:
        return self.captures.get(self._operation_id(operation))

    def store_capture(
        self,
        *,
        expected_state_version: int | None,
        capture: CaptureTruth,
    ) -> CaptureTruth:
        operation_id = self._operation_id(capture.operation)
        current = self.captures.get(operation_id)
        if expected_state_version is None:
            if current is not None:
                if current != capture:
                    raise SubmissionCoordinatorError("capture_create_conflict")
                return current
        elif current is None or current.state_version != expected_state_version:
            raise SubmissionCoordinatorError("capture_compare_and_swap_lost")
        self.captures[operation_id] = capture
        return capture

    def start_or_resume_capture_attempt(
        self,
        *,
        work: SubmissionWorkItem,
        context: ResolvedSubmissionContext,
        capture: CaptureTruth,
        requested_at: datetime,
    ) -> DurableCaptureAttempt:
        operation_id = self._operation_id(capture.operation)
        current = self.captures.get(operation_id)
        if current != capture:
            raise SubmissionCoordinatorError("capture_attempt_stale_truth")
        if capture.capture_state is CaptureState.CAPTURING:
            command = self.capture_commands.get(operation_id)
            if command is None:
                raise SubmissionCoordinatorError("durable_capture_command_missing")
            if command.attempt_ref != work.capture_attempt_ref:
                raise SubmissionCoordinatorError("capture_attempt_reference_mismatch")
            return DurableCaptureAttempt(
                capture=capture,
                command=command,
                freshly_started=False,
            )
        if work.capture_attempt_ref in self.capture_attempts_used:
            raise SubmissionCoordinatorError("capture_attempt_reference_reused")
        command = CaptureExistingCommand(
            operation=capture.operation,
            source_send_state=capture.source_send_state,
            expected_capture_version=capture.state_version,
            attempt_ref=work.capture_attempt_ref,
            capture_policy_revision=context.capture_policy_revision,
            requested_surface_product=capture.expected_surface_product,
            authority=context.authority,
            authority_sha256=authority_digest(context.authority),
            requested_at=requested_at,
        )
        capturing = begin_capture(capture, command)
        self.capture_attempts_used.add(command.attempt_ref)
        self.capture_commands[operation_id] = command
        self.captures[operation_id] = capturing
        return DurableCaptureAttempt(
            capture=capturing,
            command=command,
            freshly_started=True,
        )

    def resolve_capture_attempt(
        self,
        *,
        attempt: DurableCaptureAttempt,
        raw: CaptureDisposition,
        normalized: CaptureDisposition,
    ) -> CaptureTruth:
        operation_id = self._operation_id(attempt.capture.operation)
        if self.captures.get(operation_id) != attempt.capture:
            raise SubmissionCoordinatorError("capture_resolution_stale_attempt")
        if normalize_capture(attempt.command, raw) != normalized:
            raise SubmissionCoordinatorError("capture_normalization_mismatch")
        if raw.staging is not None:
            if raw.staging.staging_key in self.staging:
                existing = self.staging[raw.staging.staging_key]
                if existing.staging != raw.staging or existing.attempt_ref != raw.attempt_ref:
                    raise SubmissionCoordinatorError("capture_staging_key_conflict")
            elif (
                normalized.normalization
                is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
            ):
                self.staging[raw.staging.staging_key] = StagingLifecycle(
                    staging=raw.staging,
                    attempt_ref=raw.attempt_ref,
                    state="quarantined",
                    quarantine_reason="surface_product_mismatch",
                    gc_after=raw.observed_at + timedelta(days=7),
                )
            else:
                self.staging[raw.staging.staging_key] = StagingLifecycle(
                    staging=raw.staging,
                    attempt_ref=raw.attempt_ref,
                    state="staged",
                )
        resolved = apply_capture_disposition(attempt.capture, normalized)
        self.captures[operation_id] = resolved
        return resolved

    def load_capture_link(self, operation: OperationRef) -> ImmutableCaptureLink | None:
        operation_id = self._operation_id(operation)
        key = self.current_capture_link.get(operation_id)
        return self.capture_links.get(key) if key is not None else None

    def store_capture_link(self, link: ImmutableCaptureLink) -> ImmutableCaptureLink:
        lifecycle = self.staging.get(link.staging_key)
        if lifecycle is None or lifecycle.state not in {"staged", "linked"}:
            raise SubmissionCoordinatorError("capture_link_staging_not_linkable")
        existing = self.capture_links.get(link.capture_link_key)
        if existing is not None and existing != link:
            raise SubmissionCoordinatorError("immutable_capture_link_conflict")
        self.capture_links.setdefault(link.capture_link_key, link)
        lifecycle.state = "linked"
        self.current_capture_link[link.operation.operation_pub_id] = link.capture_link_key
        return self.capture_links[link.capture_link_key]

    def load_analysis(self, operation: OperationRef) -> AnalysisTruth | None:
        return self.analyses.get(self._operation_id(operation))

    def store_analysis(
        self,
        *,
        expected_state_version: int | None,
        analysis: AnalysisTruth,
    ) -> AnalysisTruth:
        operation_id = analysis.capture_link.operation.operation_pub_id
        current = self.analyses.get(operation_id)
        if expected_state_version is None:
            if current is not None:
                if current != analysis:
                    raise SubmissionCoordinatorError("analysis_create_conflict")
                return current
        elif current is None or current.state_version != expected_state_version:
            raise SubmissionCoordinatorError("analysis_compare_and_swap_lost")
        self.analyses[operation_id] = analysis
        return analysis

    def queue_or_resume_analysis_attempt(
        self,
        *,
        analysis: AnalysisTruth,
        command: AnalysisCommand,
    ) -> DurableAnalysisAttempt:
        operation_id = analysis.capture_link.operation.operation_pub_id
        if self.analyses.get(operation_id) != analysis:
            raise SubmissionCoordinatorError("analysis_attempt_stale_truth")
        if analysis.analysis_state in {AnalysisState.QUEUED, AnalysisState.RUNNING}:
            persisted = self.analysis_commands.get(operation_id)
            if persisted != command:
                raise SubmissionCoordinatorError("durable_analysis_command_mismatch")
            return DurableAnalysisAttempt(
                analysis=analysis,
                command=command,
                freshly_queued=False,
            )
        if command.attempt_ref in self.analysis_attempts_used:
            raise SubmissionCoordinatorError("analysis_attempt_reference_reused")
        queued = queue_analysis(analysis, command)
        self.analysis_attempts_used.add(command.attempt_ref)
        self.analysis_commands[operation_id] = command
        self.analyses[operation_id] = queued
        return DurableAnalysisAttempt(
            analysis=queued,
            command=command,
            freshly_queued=True,
        )

    def load_active_analysis_attempt(
        self, operation: OperationRef
    ) -> DurableAnalysisAttempt | None:
        operation_id = self._operation_id(operation)
        analysis = self.analyses.get(operation_id)
        command = self.analysis_commands.get(operation_id)
        if analysis is None or command is None:
            return None
        if analysis.analysis_state not in {AnalysisState.QUEUED, AnalysisState.RUNNING}:
            return None
        return DurableAnalysisAttempt(
            analysis=analysis,
            command=command,
            freshly_queued=False,
        )

    def load_fact(self, operation: OperationRef) -> SlotOutcomeFact | None:
        return self.facts.get(self._operation_id(operation))

    def atomic_fact_and_outbox(
        self,
        work: SubmissionWorkItem,
        fact: SlotOutcomeFact,
        outbox: OutboxEventRef,
    ) -> SlotOutcomeFact:
        if work != self.work or fact.operation != work.workflow.operation:
            raise SubmissionCoordinatorError("fact_work_item_mismatch")
        operation_id = fact.operation.operation_pub_id
        current = self.facts.get(operation_id)
        history = self.fact_history.setdefault(operation_id, [])
        if (current is None) != (not history):
            raise SubmissionCoordinatorError("fact_history_partial_commit")
        if current is not None and history[-1] != current:
            raise SubmissionCoordinatorError("fact_history_head_mismatch")
        if current == fact:
            pass
        elif current is None:
            if fact.fact_version != 1:
                raise SubmissionCoordinatorError("fact_initial_version_mismatch")
            history.append(fact)
            self.facts[operation_id] = fact
        else:
            if fact.fact_version != current.fact_version + 1:
                raise SubmissionCoordinatorError("fact_version_gap")
            history.append(fact)
            self.facts[operation_id] = fact
        existing_outbox = self.outboxes.get(outbox.outbox_key)
        if existing_outbox is not None and existing_outbox != outbox:
            raise SubmissionCoordinatorError("fact_outbox_conflict")
        # Missing exact-fact outbox is repaired within this same atomic entry.
        self.outboxes[outbox.outbox_key] = outbox
        if fact.is_final_primary:
            self.current_primary_fact[operation_id] = fact.fact_version
        return self.facts[operation_id]

    def pending_outbox(self, operation: OperationRef) -> tuple[OutboxEventRef, ...]:
        return tuple(
            event
            for event in self.outboxes.values()
            if event.aggregate_ref == operation.operation_pub_id
            and event.outbox_key not in self.published_outboxes
        )

    def mark_outbox_published(self, outbox_key: str) -> None:
        if outbox_key not in self.outboxes:
            raise SubmissionCoordinatorError("outbox_mark_without_event")
        self.published_outboxes.add(outbox_key)

    def quota_snapshot(self, reservation_pub_id: str) -> QuotaConservationSnapshot:
        if reservation_pub_id != self.quota.reservation_ref:
            raise SubmissionCoordinatorError("quota_reservation_reference_mismatch")
        return self.quota.snapshot()


class FakePreflightGateway:
    """Read-only preflight fake; notably, it owns no submit method."""

    def __init__(
        self,
        *,
        clock: StepClock,
        decision: PreflightDecision = PreflightDecision.READY,
    ) -> None:
        self.clock = clock
        self.decision = decision
        self.calls = 0

    def preflight(self, command: PreflightCommand) -> PreflightObservation:
        self.calls += 1
        return PreflightObservation(
            operation=command.operation,
            authority_sha256=authority_digest(command.authority),
            decision=self.decision,
            observed_at=self.clock.now(),
            evidence_ref="preflight-evidence-v1",
            evidence_sha256=_hash({"decision": self.decision.value}),
            not_sent_reason=(
                TerminalReason.UNAVAILABLE
                if self.decision is PreflightDecision.CONFIRMED_NOT_SENT
                else None
            ),
        )


class _SubmitGatewayBase:
    def __init__(self, store: DurableOwnerStore, clock: StepClock) -> None:
        self.store = store
        self.clock = clock

    def _enter_boundary(self, command: SubmitOnceCommand) -> None:
        wal = self.store.wal
        claim = command.fresh_claim.claim
        if (
            claim.owner_dispatch_ref != wal.owner_dispatch_ref
            or claim.owner_wal_evidence_sha256 != wal.wal_evidence_sha256
            or claim.authority_sha256 != wal.authority_sha256
            or claim.fence_set_sha256 != wal.fence_set_sha256
        ):
            raise SubmissionCoordinatorError("submit_wal_identity_mismatch")
        if not wal.owner_session_active:
            raise SubmissionCoordinatorError("submit_owner_session_terminated")
        if not self.store.exact_leases_active():
            raise SubmissionCoordinatorError("submit_lease_fence_not_active")
        if wal.boundary is BoundaryState.ENTERED or wal.submit_invocations != 0:
            raise SubmissionCoordinatorError("irreversible_boundary_already_entered")
        wal.submit_invocations += 1
        wal.boundary = BoundaryState.ENTERED

    def _confirmed(self, *, evidence_ref: str, provenance: dict[str, str]) -> SubmitDisposition:
        wal = self.store.wal
        wal.provider_submission_ref = f"provider-ref-{wal.owner_dispatch_ref}"
        wal.provider_evidence_ref = evidence_ref
        wal.provider_evidence_sha256 = _hash(provenance)
        wal.provider_provenance = provenance
        return SubmitDisposition(
            send_state=SendState.CONFIRMED_SENT,
            reason=TerminalReason.SUBMITTED,
            boundary_entered=True,
            evidence_ref=evidence_ref,
            evidence_sha256=wal.provider_evidence_sha256,
            provider_submission_ref=wal.provider_submission_ref,
            resolved_at=self.clock.now(),
        )


class FakeApiSubmitGateway(_SubmitGatewayBase):
    def __init__(
        self,
        store: DurableOwnerStore,
        clock: StepClock,
        *,
        timeout_after_boundary: bool = False,
    ) -> None:
        super().__init__(store, clock)
        self.timeout_after_boundary = timeout_after_boundary

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition:
        self._enter_boundary(command)
        provenance = {
            "provider": "openai",
            "request_id": "request-provider-api-1",
            "transport": "https",
        }
        if self.timeout_after_boundary:
            self.store.wal.provider_evidence_ref = "api-timeout-after-write"
            self.store.wal.provider_evidence_sha256 = _hash(provenance | {"result": "timeout"})
            self.store.wal.provider_provenance = provenance | {"result": "timeout"}
            return SubmitDisposition(
                send_state=SendState.SEND_UNKNOWN,
                reason=TerminalReason.SEND_UNKNOWN,
                boundary_entered=True,
                evidence_ref="api-timeout-after-write",
                evidence_sha256=_hash(provenance | {"result": "timeout"}),
                resolved_at=self.clock.now(),
            )
        return self._confirmed(evidence_ref="api-provider-ack", provenance=provenance)


class FakeWebSubmitGateway(_SubmitGatewayBase):
    def __init__(
        self,
        store: DurableOwnerStore,
        clock: StepClock,
        *,
        submit_selector_count: int,
    ) -> None:
        super().__init__(store, clock)
        self.submit_selector_count = submit_selector_count

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition:
        if self.submit_selector_count != 1:
            raise SubmissionCoordinatorError("web_submit_selector_not_unique")
        roles = {lease.fence.resource_role for lease in self.store.leases.values()}
        if roles != {"web_session"}:
            raise SubmissionCoordinatorError("web_session_fence_missing")
        self._enter_boundary(command)
        return self._confirmed(
            evidence_ref="web-submit-observation",
            provenance={
                "selector": "button[data-submit-primary]",
                "session_fence": self.store.wal.fence_set_sha256,
            },
        )


class FakeAppSubmitGateway(_SubmitGatewayBase):
    def __init__(
        self,
        store: DurableOwnerStore,
        clock: StepClock,
        *,
        crash_after_boundary: bool = False,
    ) -> None:
        super().__init__(store, clock)
        self.crash_after_boundary = crash_after_boundary

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition:
        roles = {lease.fence.resource_role for lease in self.store.leases.values()}
        if roles != {"app_session"}:
            raise SubmissionCoordinatorError("app_session_fence_missing")
        package_provenance = {
            "package": "com.example.doubao",
            "build": "20260824.1",
            "channel": "production",
            "session_fence": self.store.wal.fence_set_sha256,
        }
        if set(package_provenance) != {"package", "build", "channel", "session_fence"}:
            raise SubmissionCoordinatorError("app_package_provenance_incomplete")
        self._enter_boundary(command)
        if self.crash_after_boundary:
            self.store.wal.provider_evidence_ref = "app-process-crash-after-action"
            self.store.wal.provider_evidence_sha256 = _hash(package_provenance)
            self.store.wal.provider_provenance = package_provenance
            raise OwnerProcessCrash("app_process_crash_after_submit_action")
        return self._confirmed(
            evidence_ref="app-submit-observation",
            provenance=package_provenance,
        )


class FakeReconciliationGateway:
    """Reads only durable WAL/lease truth and never owns a submit capability."""

    def __init__(self, store: DurableOwnerStore, clock: StepClock) -> None:
        self.store = store
        self.clock = clock
        self._observations: dict[str, ReconciliationEvidence] = {}

    def _assert_exclusive_dead_owner(self) -> None:
        wal = self.store.wal
        if wal.owner_session_active or wal.reconciliation_claim_ref is None:
            raise SubmissionCoordinatorError("reconciliation_owner_not_exclusively_fenced")

    def observe_sending(self, operation: SubmissionOperationTruth) -> ReconciliationEvidence:
        self._assert_exclusive_dead_owner()
        if operation.claim is None:
            raise SubmissionCoordinatorError("reconciliation_claim_missing")
        wal = self.store.wal
        material = {
            "boundary": wal.boundary.value,
            "dispatch_ref": wal.owner_dispatch_ref,
            "fence_set_sha256": wal.fence_set_sha256,
            "provider_evidence_ref": wal.provider_evidence_ref,
            "provider_submission_ref": wal.provider_submission_ref,
            "wal_evidence_sha256": wal.wal_evidence_sha256,
        }
        evidence = ReconciliationEvidence(
            durable_evidence_ref=f"reconcile-evidence-{operation.state_version}",
            durable_evidence_sha256=_hash(material),
            observed_at=self.clock.now(),
        )
        self._observations[evidence.durable_evidence_ref] = evidence
        return evidence

    def reconcile_sending(self, command: SendingReconciliationCommand) -> ReconciliationDisposition:
        self._assert_exclusive_dead_owner()
        evidence = self._observations.get(command.durable_evidence_ref)
        if (
            evidence is None
            or evidence.durable_evidence_sha256 != command.durable_evidence_sha256
            or command.owner_dispatch_ref != self.store.wal.owner_dispatch_ref
            or command.owner_wal_evidence_sha256 != self.store.wal.wal_evidence_sha256
        ):
            raise SubmissionCoordinatorError("reconciliation_evidence_mismatch")
        wal = self.store.wal
        if wal.boundary is BoundaryState.NOT_ENTERED:
            terminated_digest = self.store.terminate_exact_leases(at=self.clock.now())
            if terminated_digest != wal.fence_set_sha256:
                raise SubmissionCoordinatorError("terminated_lease_fence_set_mismatch")
            proof_material = {
                "boundary": wal.boundary.value,
                "dispatch_ref": wal.owner_dispatch_ref,
                "lease_states": {
                    key: lease.status for key, lease in sorted(self.store.leases.items())
                },
                "terminated_fence_set_sha256": terminated_digest,
            }
            return ReconciliationDisposition(
                send_state=SendState.CONFIRMED_NOT_SENT,
                reason=TerminalReason.POST_CLAIM_NOT_SENT,
                boundary_entered=False,
                evidence_ref=command.durable_evidence_ref,
                evidence_sha256=command.durable_evidence_sha256,
                non_submission_proof_ref=f"not-sent-proof-{_hash(proof_material)}",
                terminated_fence_set_sha256=terminated_digest,
                resolved_at=self.clock.now(),
            )
        if wal.provider_submission_ref is not None:
            return ReconciliationDisposition(
                send_state=SendState.CONFIRMED_SENT,
                reason=TerminalReason.SUBMITTED,
                boundary_entered=True,
                evidence_ref=command.durable_evidence_ref,
                evidence_sha256=command.durable_evidence_sha256,
                provider_submission_ref=wal.provider_submission_ref,
                resolved_at=self.clock.now(),
            )
        return ReconciliationDisposition(
            send_state=SendState.SEND_UNKNOWN,
            reason=TerminalReason.SEND_UNKNOWN,
            boundary_entered=True,
            evidence_ref=command.durable_evidence_ref,
            evidence_sha256=command.durable_evidence_sha256,
            resolved_at=self.clock.now(),
        )


@dataclass
class CaptureGatewayStore:
    modes_by_attempt: dict[str, str] = field(default_factory=dict)
    invocations: dict[str, int] = field(default_factory=dict)


class FakeCaptureGateway:
    def __init__(self, store: CaptureGatewayStore, clock: StepClock) -> None:
        self.store = store
        self.clock = clock

    def capture_existing(self, command: CaptureExistingCommand) -> CaptureDisposition:
        self.store.invocations[command.attempt_ref] = (
            self.store.invocations.get(command.attempt_ref, 0) + 1
        )
        mode = self.store.modes_by_attempt.get(command.attempt_ref, "completed")
        observed_at = self.clock.now()
        observed_product = command.requested_surface_product
        staging: CaptureStagingRef | None = None
        state = CaptureState.COMPLETED
        if mode == "partial":
            state = CaptureState.PARTIAL
        elif mode == "failed":
            state = CaptureState.FAILED
        elif mode == "not_observable":
            state = CaptureState.NOT_OBSERVABLE
        elif mode == "mismatch":
            state = CaptureState.COMPLETED
            observed_product = _surface_product(CollectionSurface.PROVIDER_API)
            if observed_product == command.requested_surface_product:
                observed_product = _surface_product(CollectionSurface.CONSUMER_WEB)
        if state in {CaptureState.COMPLETED, CaptureState.PARTIAL}:
            staging = CaptureStagingRef(
                staging_key=f"staging-{command.attempt_ref}",
                object_ref=f"object-{command.attempt_ref}",
                content_sha256=_hash({"attempt": command.attempt_ref, "mode": mode}),
                byte_size=128,
                media_type="application/json",
                capture_schema_revision="capture-schema-v1",
                staged_at=observed_at + timedelta(milliseconds=1),
            )
        return CaptureDisposition(
            capture_state=cast(
                Literal[
                    CaptureState.COMPLETED,
                    CaptureState.PARTIAL,
                    CaptureState.FAILED,
                    CaptureState.NOT_OBSERVABLE,
                ],
                state,
            ),
            attempt_ref=command.attempt_ref,
            evidence_ref=f"capture-evidence-{command.attempt_ref}",
            evidence_sha256=_hash({"capture": command.attempt_ref, "mode": mode}),
            observed_at=observed_at,
            observed_surface_product=observed_product,
            staging=staging,
        )


@dataclass
class AnalysisGatewayStore:
    invocations: dict[str, int] = field(default_factory=dict)
    immutable_effects: dict[str, str] = field(default_factory=dict)


class FakeAnalysisGateway:
    def __init__(self, store: AnalysisGatewayStore, clock: StepClock) -> None:
        self.store = store
        self.clock = clock

    def analyze_existing_capture(self, command: AnalysisCommand) -> AnalysisDisposition:
        self.store.invocations[command.attempt_ref] = (
            self.store.invocations.get(command.attempt_ref, 0) + 1
        )
        effect_key = _hash(
            {
                "attempt": command.attempt_ref,
                "capture": command.capture_content_sha256,
                "policy": command.analysis_policy_revision,
            }
        )
        result_ref = f"analysis-result-{effect_key[:16]}"
        existing = self.store.immutable_effects.get(effect_key)
        if existing is not None and existing != result_ref:
            raise SubmissionCoordinatorError("analysis_immutable_effect_conflict")
        self.store.immutable_effects[effect_key] = result_ref
        return AnalysisDisposition(
            analysis_state=AnalysisState.COMPLETED,
            attempt_ref=command.attempt_ref,
            evidence_sha256=_hash({"analysis": effect_key}),
            completed_at=self.clock.now(),
            result_ref=result_ref,
        )


class CoordinatorHarness:
    def __init__(
        self,
        surface: CollectionSurface = CollectionSurface.CONSUMER_WEB,
        *,
        api_timeout: bool = False,
        app_crash: bool = False,
        web_selector_count: int = 1,
        preflight_decision: PreflightDecision = PreflightDecision.READY,
    ) -> None:
        self.surface = surface
        self.api_timeout = api_timeout
        self.app_crash = app_crash
        self.web_selector_count = web_selector_count
        self.preflight_decision = preflight_decision
        self.clock = StepClock()
        identity = _identity(surface)
        authority = _authority(surface)
        dispatch_ref = f"dispatch-{surface.value}"
        self.owner_store = DurableOwnerStore(authority, dispatch_ref)
        self.context = ResolvedSubmissionContext(
            prepare=PrepareSubmissionCommand(identity=identity, prepared_at=NOW),
            authority=authority,
            owner_dispatch_ref=dispatch_ref,
            owner_wal_evidence_sha256=self.owner_store.wal.wal_evidence_sha256,
            capture_policy_revision="capture-policy-v1",
        )
        self.prepare_work = PrepareWorkItem(
            workflow=WorkflowOperationInput(
                operation=operation_ref(identity),
                expected_state_version=1,
            ),
            reservation_pub_id=f"reservation-{surface.value}",
            frozen_slot_ref=f"slot-{surface.value}",
            binding_revision_pub_id=f"binding-{surface.value}",
            quota_registry_revision="quota-registry-v1",
            request_manifest_ref=identity.request_manifest.request_payload_ref,
        )
        prepared_ref = PreparedSubmissionRef(
            workflow=self.prepare_work.workflow,
            reservation_pub_id=self.prepare_work.reservation_pub_id,
        )
        self.work = SubmissionWorkItem(
            prepared=prepared_ref,
            grant_pub_id=authority.grant_pub_id,
            lease_pub_ids=tuple(fence.lease_pub_id for fence in authority.lease_fences),
            cursor_ref="partition-1-cursor-1",
            claim_pub_id=f"claim-{surface.value}",
            reconciliation_claim_ref=f"reconcile-claim-{surface.value}",
            capture_attempt_ref=f"capture-attempt-{surface.value}-1",
        )
        self.repository = InMemoryDurableSubmissionRepository(
            prepare_work=self.prepare_work,
            preparation_context=ResolvedPreparationContext(prepare=self.context.prepare),
            work=self.work,
            context=self.context,
            owner_store=self.owner_store,
        )
        self.consumer_store = ConsumerStore()
        self.capture_store = CaptureGatewayStore()
        self.analysis_store = AnalysisGatewayStore()
        self.preflight_instances: list[FakePreflightGateway] = []
        self.submit_instances: list[object] = []
        self.reconciliation_instances: list[FakeReconciliationGateway] = []
        self.capture_instances: list[FakeCaptureGateway] = []

    def preparation_coordinator(
        self, crash_hook: CrashHook | None = None
    ) -> PreparationCoordinator:
        return PreparationCoordinator(
            self.repository,
            crash_hook or NoCrashHook(),
        )

    def prepare(self) -> None:
        result = self.preparation_coordinator().run(self.prepare_work)
        if result.prepared != self.work.prepared:
            raise AssertionError("prepared reference drift")

    def coordinator(self, crash_hook: CrashHook | None = None) -> SubmissionCoordinator:
        preflight = FakePreflightGateway(
            clock=self.clock,
            decision=self.preflight_decision,
        )
        if self.surface is CollectionSurface.PROVIDER_API:
            submit: SubmitOnceGateway = FakeApiSubmitGateway(
                self.owner_store,
                self.clock,
                timeout_after_boundary=self.api_timeout,
            )
        elif self.surface is CollectionSurface.CONSUMER_WEB:
            submit = FakeWebSubmitGateway(
                self.owner_store,
                self.clock,
                submit_selector_count=self.web_selector_count,
            )
        else:
            submit = FakeAppSubmitGateway(
                self.owner_store,
                self.clock,
                crash_after_boundary=self.app_crash,
            )
        reconciliation = FakeReconciliationGateway(self.owner_store, self.clock)
        capture = FakeCaptureGateway(self.capture_store, self.clock)
        publisher = FakeOutboxPublisher(self.consumer_store)
        self.preflight_instances.append(preflight)
        self.submit_instances.append(submit)
        self.reconciliation_instances.append(reconciliation)
        self.capture_instances.append(capture)
        return SubmissionCoordinator(
            self.repository,
            preflight,
            submit,
            reconciliation,
            capture,
            publisher,
            self.clock,
            crash_hook or NoCrashHook(),
        )

    def capture_coordinator(self) -> CaptureCoordinator:
        return CaptureCoordinator(
            self.repository,
            FakeCaptureGateway(self.capture_store, self.clock),
            FakeOutboxPublisher(self.consumer_store),
            self.clock,
            NoCrashHook(),
        )

    def analysis_coordinator(self, crash_hook: CrashHook | None = None) -> AnalysisCoordinator:
        return AnalysisCoordinator(
            self.repository,
            FakeAnalysisGateway(self.analysis_store, self.clock),
            FakeOutboxPublisher(self.consumer_store),
            self.clock,
            crash_hook or NoCrashHook(),
        )

    def mark_owner_dead_if_sending(self) -> None:
        operation = self.repository.load_operation(self.work.workflow.operation)
        if operation is not None and operation.send_state is SendState.SENDING:
            self.owner_store.mark_owner_dead()

    def run_after_crash(self, point: CrashPoint) -> CoordinatorResult:
        if point in {CrashPoint.BEFORE_RESERVE, CrashPoint.AFTER_RESERVE}:
            with pytest.raises(InjectedCrash, match=point.value):
                self.preparation_coordinator(OneShotCrashHook(point)).run(self.prepare_work)
            self.prepare()
            return self.coordinator().run(self.work)
        self.prepare()
        with pytest.raises(InjectedCrash, match=point.value):
            self.coordinator(OneShotCrashHook(point)).run(self.work)
        self.mark_owner_dead_if_sending()
        return self.coordinator().run(self.work)


def _set_capture_attempt(harness: CoordinatorHarness, attempt_ref: str) -> SubmissionWorkItem:
    work = harness.work.model_copy(update={"capture_attempt_ref": attempt_ref})
    harness.work = work
    harness.repository.work = work
    return work


SEVEN_SUBMISSION_CRASH_POINTS = (
    CrashPoint.BEFORE_RESERVE,
    CrashPoint.AFTER_RESERVE,
    CrashPoint.BEFORE_OWNER_CAS,
    CrashPoint.AFTER_OWNER_CAS_BEFORE_SUBMIT,
    CrashPoint.AFTER_SUBMIT_BEFORE_ACK,
    CrashPoint.AFTER_STAGING_BEFORE_LINK,
    CrashPoint.AFTER_FACT_BEFORE_OUTBOX_PUBLISH,
)


@pytest.mark.parametrize("point", SEVEN_SUBMISSION_CRASH_POINTS)
def test_seven_crash_points_rebuild_process_objects_and_never_resubmit(
    point: CrashPoint,
) -> None:
    harness = CoordinatorHarness()

    result = harness.run_after_crash(point)

    assert harness.owner_store.wal.submit_invocations <= 1
    expected_submission_processes = (
        1 if point in {CrashPoint.BEFORE_RESERVE, CrashPoint.AFTER_RESERVE} else 2
    )
    assert len(harness.submit_instances) == expected_submission_processes
    if expected_submission_processes == 2:
        assert harness.submit_instances[0] is not harness.submit_instances[1]
        assert harness.reconciliation_instances[0] is not harness.reconciliation_instances[1]
    assert result.operation.send_state in {
        SendState.CONFIRMED_SENT,
        SendState.CONFIRMED_NOT_SENT,
    }
    if point is CrashPoint.AFTER_OWNER_CAS_BEFORE_SUBMIT:
        assert result.operation.send_state is SendState.CONFIRMED_NOT_SENT
        assert harness.owner_store.wal.submit_invocations == 0
        assert result.quota.released_units == 1
        assert all(lease.status == "terminated" for lease in harness.owner_store.leases.values())
    else:
        assert result.operation.send_state is SendState.CONFIRMED_SENT
        assert result.quota.consumed_units == 1
    assert len(harness.repository.quota.effects) == 3
    assert len(harness.repository.quota.ledger) == 6
    assert not result.quota.explicitly_retained
    assert harness.repository.pending_outbox(harness.work.workflow.operation) == ()


def test_terminal_before_capture_crash_exposes_pending_not_capture_failed() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    point = CrashPoint.AFTER_TERMINAL_BEFORE_CAPTURE

    with pytest.raises(InjectedCrash, match=point.value):
        harness.coordinator(OneShotCrashHook(point)).run(harness.work)

    operation = harness.repository.load_operation(harness.work.workflow.operation)
    assert operation is not None
    assert operation.send_state is SendState.CONFIRMED_SENT
    assert harness.repository.load_capture(harness.work.workflow.operation) is None
    assert derive_slot_outcome(operation) is SlotOutcome.CONFIRMED_SENT_CAPTURE_PENDING
    recovered = harness.coordinator().run(harness.work)
    assert recovered.capture is not None
    assert recovered.capture.capture_state is CaptureState.COMPLETED
    assert recovered.fact.is_final_primary
    assert harness.owner_store.wal.submit_invocations == 1


def test_preparation_is_a_distinct_pre_resource_atomic_boundary() -> None:
    harness = CoordinatorHarness()
    harness.repository.prepare_blocked = True

    with pytest.raises(SubmissionCoordinatorError, match="prepare_blocked"):
        harness.preparation_coordinator().run(harness.prepare_work)

    assert harness.repository.operations == {}
    assert harness.repository.quota.effects == {}
    with pytest.raises(SubmissionCoordinatorError, match="prepared_operation_missing"):
        harness.coordinator().run(harness.work)

    harness.repository.prepare_blocked = False
    prepared = harness.preparation_coordinator().run(harness.prepare_work)
    assert prepared.prepared == harness.work.prepared
    assert prepared.quota.reserved_units == 1
    assert len(harness.repository.quota.effects) == 3


def test_cas_loser_cannot_reconcile_a_live_owner() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    harness.repository.force_cas_loss = True

    with pytest.raises(
        SubmissionCoordinatorError,
        match="sending_owner_still_active_retryable",
    ):
        harness.coordinator().run(harness.work)

    operation = harness.repository.load_operation(harness.work.workflow.operation)
    assert operation is not None and operation.send_state is SendState.SENDING
    assert harness.owner_store.wal.owner_session_active
    assert harness.owner_store.wal.reconciliation_claim_ref is None
    assert harness.owner_store.exact_leases_active()
    assert harness.owner_store.wal.submit_invocations == 0
    assert harness.repository.load_fact(harness.work.workflow.operation) is None
    assert harness.repository.quota.snapshot().reserved_units == 1

    harness.owner_store.mark_owner_dead()
    recovered = harness.coordinator().run(harness.work)
    assert recovered.operation.send_state is SendState.CONFIRMED_NOT_SENT
    assert recovered.quota.released_units == 1
    assert harness.owner_store.wal.submit_invocations == 0


def test_not_sent_reconciliation_requires_exact_terminated_lease_fence_digest() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    point = CrashPoint.AFTER_OWNER_CAS_BEFORE_SUBMIT
    with pytest.raises(InjectedCrash):
        harness.coordinator(OneShotCrashHook(point)).run(harness.work)
    harness.owner_store.mark_owner_dead()
    lease = next(iter(harness.owner_store.leases.values()))
    lease.fence = lease.fence.model_copy(update={"generation": lease.fence.generation + 1})

    with pytest.raises(
        SubmissionCoordinatorError,
        match="terminated_lease_fence_set_mismatch",
    ):
        harness.coordinator().run(harness.work)

    operation = harness.repository.load_operation(harness.work.workflow.operation)
    assert operation is not None and operation.send_state is SendState.SENDING
    assert harness.repository.quota.snapshot().reserved_units == 1
    assert harness.owner_store.wal.submit_invocations == 0


def test_preflight_not_sent_releases_without_ever_constructing_submit_from_preflight() -> None:
    harness = CoordinatorHarness(
        preflight_decision=PreflightDecision.CONFIRMED_NOT_SENT,
    )
    harness.prepare()

    result = harness.coordinator().run(harness.work)

    assert not hasattr(harness.preflight_instances[0], "submit_once")
    assert result.operation.send_state is SendState.CONFIRMED_NOT_SENT
    assert result.operation.claim is None
    assert result.fact.outcome is SlotOutcome.UNAVAILABLE
    assert not result.fact.is_final_primary
    assert result.quota.released_units == 1
    assert harness.owner_store.wal.submit_invocations == 0


def test_api_timeout_is_durable_unknown_with_provider_provenance_and_no_resend() -> None:
    harness = CoordinatorHarness(
        CollectionSurface.PROVIDER_API,
        api_timeout=True,
    )
    harness.prepare()

    first = harness.coordinator().run(harness.work)
    second = harness.coordinator().run(harness.work)

    assert first.operation.send_state is SendState.SEND_UNKNOWN
    assert second.operation == first.operation
    assert first.quota.unknown_units == 1
    assert harness.owner_store.wal.submit_invocations == 1
    assert harness.owner_store.wal.provider_provenance == {
        "provider": "openai",
        "request_id": "request-provider-api-1",
        "result": "timeout",
        "transport": "https",
    }
    assert not first.fact.is_final_primary


def test_app_process_crash_after_action_recovers_unknown_without_resend() -> None:
    harness = CoordinatorHarness(
        CollectionSurface.CONSUMER_APP,
        app_crash=True,
    )
    harness.prepare()

    with pytest.raises(OwnerProcessCrash, match="app_process_crash"):
        harness.coordinator().run(harness.work)
    harness.owner_store.mark_owner_dead()
    recovered = harness.coordinator().run(harness.work)

    assert recovered.operation.send_state is SendState.SEND_UNKNOWN
    assert recovered.quota.unknown_units == 1
    assert harness.owner_store.wal.submit_invocations == 1
    assert harness.owner_store.wal.provider_provenance["package"] == "com.example.doubao"
    assert harness.owner_store.wal.provider_provenance["build"] == "20260824.1"
    assert harness.owner_store.wal.provider_provenance["channel"] == "production"


def test_web_uses_one_submit_selector_and_exact_session_fence_once() -> None:
    harness = CoordinatorHarness(CollectionSurface.CONSUMER_WEB)
    harness.prepare()

    first = harness.coordinator().run(harness.work)
    second = harness.coordinator().run(harness.work)

    assert first.operation.send_state is SendState.CONFIRMED_SENT
    assert second.operation == first.operation
    assert harness.owner_store.wal.submit_invocations == 1
    assert harness.owner_store.wal.provider_provenance["selector"] == (
        "button[data-submit-primary]"
    )
    assert harness.owner_store.wal.provider_provenance["session_fence"] == (
        harness.context.authority.fence_set_sha256
    )


def test_exact_terminal_replay_revalidates_three_scope_effects_and_unique_ledger() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    ledger_before = dict(harness.repository.quota.ledger)
    transition_before = dict(harness.repository.terminal_transitions)

    second = harness.coordinator().run(harness.work)

    assert second.operation == first.operation
    assert harness.repository.quota.effect_set_sha256 == (harness.repository.quota._planned_hash())
    assert set(harness.repository.quota.effects) == set(DurableQuotaStore.SCOPE_SPECS)
    assert harness.repository.quota.ledger == ledger_before
    assert harness.repository.terminal_transitions == transition_before
    assert len(ledger_before) == 6
    assert harness.owner_store.wal.submit_invocations == 1


@pytest.mark.parametrize(
    "corruption",
    ("terminal_outbox", "quota_effect", "quota_ledger", "terminal_transition"),
)
def test_terminal_partial_commit_or_corruption_fails_closed(corruption: str) -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    harness.coordinator().run(harness.work)
    operation_id = harness.work.workflow.operation.operation_pub_id
    transition = harness.repository.terminal_transitions[operation_id]
    if corruption == "terminal_outbox":
        harness.repository.outboxes.pop(transition.outbox.outbox_key)
    elif corruption == "quota_effect":
        harness.repository.quota.effects.pop(next(iter(harness.repository.quota.effects)))
    elif corruption == "quota_ledger":
        terminal_key = next(
            key
            for key, entry in harness.repository.quota.ledger.items()
            if entry.effect == "consumed"
        )
        harness.repository.quota.ledger.pop(terminal_key)
    else:
        harness.repository.terminal_transitions.pop(operation_id)

    with pytest.raises(SubmissionCoordinatorError):
        harness.coordinator().run(harness.work)

    assert harness.owner_store.wal.submit_invocations == 1


def test_direct_send_unknown_without_atomic_quota_terminal_is_rejected() -> None:
    harness = CoordinatorHarness(CollectionSurface.PROVIDER_API, api_timeout=True)
    harness.prepare()
    harness.coordinator().run(harness.work)
    operation_id = harness.work.workflow.operation.operation_pub_id
    # Simulate a forbidden direct row update that bypassed the terminal transaction.
    harness.repository.terminal_transitions.pop(operation_id)

    with pytest.raises(
        SubmissionCoordinatorError,
        match="terminal_transition_missing_or_conflicting",
    ):
        harness.coordinator().run(harness.work)

    assert harness.repository.quota.snapshot().unknown_units == 1
    assert harness.owner_store.wal.submit_invocations == 1


def test_exact_fact_with_missing_outbox_is_atomically_repaired() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    fact_event = next(
        event
        for event in harness.repository.outboxes.values()
        if event.event_type == "collection.slot.outcome"
    )
    effects_before = dict(harness.consumer_store.effects)
    attempts_before = harness.consumer_store.publish_attempts[fact_event.outbox_key]
    harness.repository.outboxes.pop(fact_event.outbox_key)
    harness.repository.published_outboxes.discard(fact_event.outbox_key)

    replay = harness.coordinator().run(harness.work)

    assert replay.fact == first.fact
    assert harness.repository.outboxes[fact_event.outbox_key] == fact_event
    assert harness.consumer_store.effects == effects_before
    assert harness.consumer_store.publish_attempts[fact_event.outbox_key] == attempts_before + 1
    assert harness.owner_store.wal.submit_invocations == 1


def test_fact_history_partial_commit_fails_closed() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    harness.coordinator().run(harness.work)
    operation_id = harness.work.workflow.operation.operation_pub_id
    harness.repository.fact_history[operation_id].clear()

    with pytest.raises(SubmissionCoordinatorError, match="fact_history_partial_commit"):
        harness.coordinator().run(harness.work)

    assert harness.owner_store.wal.submit_invocations == 1


def test_publish_success_before_mark_retries_delivery_but_consumer_effect_is_once() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    point = CrashPoint.AFTER_OUTBOX_PUBLISH_BEFORE_MARK
    with pytest.raises(InjectedCrash, match=point.value):
        harness.coordinator(OneShotCrashHook(point)).run(harness.work)
    effects_after_crash = dict(harness.consumer_store.effects)
    assert len(effects_after_crash) == 1
    published_key = next(iter(effects_after_crash))
    assert published_key not in harness.repository.published_outboxes

    recovered = harness.coordinator().run(harness.work)

    assert recovered.operation.send_state is SendState.CONFIRMED_SENT
    assert harness.consumer_store.publish_attempts[published_key] == 2
    assert harness.consumer_store.effects[published_key] == effects_after_crash[published_key]
    assert set(harness.consumer_store.effects) == harness.repository.published_outboxes
    assert harness.owner_store.wal.submit_invocations == 1


def test_partial_capture_retry_allocates_new_attempt_and_preserves_old_immutable_link() -> None:
    harness = CoordinatorHarness()
    first_attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[first_attempt] = "partial"
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    assert first.capture is not None and first.capture.capture_state is CaptureState.PARTIAL
    assert first.capture_link is not None
    first_link = first.capture_link
    assert not first.fact.is_final_primary
    assert harness.repository.current_primary_fact == {}

    second_attempt = "capture-attempt-consumer-web-2"
    work = _set_capture_attempt(harness, second_attempt)
    harness.capture_store.modes_by_attempt[second_attempt] = "completed"
    recovered = harness.capture_coordinator().retry(work=work, context=harness.context)

    assert recovered.capture is not None
    assert recovered.capture.capture_state is CaptureState.COMPLETED
    assert recovered.capture_link is not None and recovered.capture_link != first_link
    assert harness.repository.capture_links[first_link.capture_link_key] == first_link
    assert len(harness.repository.capture_links) == 2
    assert harness.repository.load_capture_link(work.workflow.operation) == recovered.capture_link
    assert recovered.fact.is_final_primary
    assert harness.repository.current_primary_fact[work.workflow.operation.operation_pub_id] == (
        recovered.fact.fact_version
    )
    final_facts = [
        fact
        for fact in harness.repository.fact_history[work.workflow.operation.operation_pub_id]
        if fact.is_final_primary
    ]
    assert final_facts == [recovered.fact]
    assert harness.owner_store.wal.submit_invocations == 1


def test_surface_mismatch_staging_is_quarantined_with_gc_and_cannot_be_retried() -> None:
    harness = CoordinatorHarness()
    attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[attempt] = "mismatch"
    harness.prepare()

    result = harness.coordinator().run(harness.work)

    assert result.capture is not None
    assert result.capture.capture_state is CaptureState.NOT_OBSERVABLE
    assert result.capture.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    assert result.fact.outcome is SlotOutcome.INVALID_SURFACE_OR_PRODUCT
    assert not result.fact.is_final_primary
    lifecycle = harness.repository.staging[f"staging-{attempt}"]
    assert lifecycle.state == "quarantined"
    assert lifecycle.quarantine_reason == "surface_product_mismatch"
    assert lifecycle.gc_after is not None
    assert harness.repository.capture_links == {}

    work = _set_capture_attempt(harness, "capture-attempt-consumer-web-2")
    with pytest.raises(
        SubmissionCoordinatorError,
        match="quarantined_surface_mismatch_is_not_retryable",
    ):
        harness.capture_coordinator().retry(work=work, context=harness.context)
    assert lifecycle.state == "quarantined"
    assert len(harness.capture_store.invocations) == 1
    assert harness.owner_store.wal.submit_invocations == 1


def test_accepted_not_observable_can_be_explicitly_retried_without_submit() -> None:
    harness = CoordinatorHarness()
    first_attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[first_attempt] = "not_observable"
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    assert first.capture is not None
    assert first.capture.capture_state is CaptureState.NOT_OBSERVABLE
    assert first.capture.normalization is CaptureNormalizationDecision.ACCEPTED

    second_attempt = "capture-attempt-consumer-web-2"
    work = _set_capture_attempt(harness, second_attempt)
    result = harness.capture_coordinator().retry(work=work, context=harness.context)

    assert result.capture is not None
    assert result.capture.capture_state is CaptureState.COMPLETED
    assert result.fact.is_final_primary
    assert harness.owner_store.wal.submit_invocations == 1


def test_capture_retry_fails_before_gateway_when_authority_is_stale() -> None:
    harness = CoordinatorHarness()
    first_attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[first_attempt] = "failed"
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    assert first.capture is not None and first.capture.capture_state is CaptureState.FAILED
    second_attempt = "capture-attempt-consumer-web-2"
    work = _set_capture_attempt(harness, second_attempt)
    harness.clock.advance(timedelta(hours=2))
    calls_before = dict(harness.capture_store.invocations)

    with pytest.raises(ValidationError, match="capture_authority_not_fresh"):
        harness.capture_coordinator().retry(work=work, context=harness.context)

    assert harness.capture_store.invocations == calls_before
    capture = harness.repository.load_capture(work.workflow.operation)
    assert capture is not None and capture.capture_state is CaptureState.FAILED
    assert second_attempt not in harness.repository.capture_attempts_used
    assert harness.owner_store.wal.submit_invocations == 1


def test_capturing_recovery_reuses_durable_command_and_rejects_mismatched_attempt() -> None:
    harness = CoordinatorHarness()
    first_attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[first_attempt] = "failed"
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    assert first.capture is not None and first.capture.capture_state is CaptureState.FAILED

    second_attempt = "capture-attempt-consumer-web-2"
    second_work = _set_capture_attempt(harness, second_attempt)
    attempt = harness.repository.start_or_resume_capture_attempt(
        work=second_work,
        context=harness.context,
        capture=first.capture,
        requested_at=harness.clock.now(),
    )
    assert attempt.command.attempt_ref == second_attempt
    assert not attempt.command.model_dump(mode="python").get("submit_once")

    third_work = _set_capture_attempt(harness, "capture-attempt-consumer-web-3")
    calls_before = dict(harness.capture_store.invocations)
    operation = harness.repository.load_operation(third_work.workflow.operation)
    assert operation is not None
    with pytest.raises(
        SubmissionCoordinatorError,
        match="capture_attempt_reference_mismatch",
    ):
        harness.capture_coordinator().resume(
            work=third_work,
            context=harness.context,
            operation=operation,
            explicit_retry=True,
        )
    assert harness.capture_store.invocations == calls_before


def test_capturing_recovery_with_same_attempt_reuses_durable_active_command() -> None:
    harness = CoordinatorHarness()
    first_attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[first_attempt] = "failed"
    harness.prepare()
    first = harness.coordinator().run(harness.work)
    assert first.capture is not None and first.capture.capture_state is CaptureState.FAILED

    second_attempt = "capture-attempt-consumer-web-2"
    work = _set_capture_attempt(harness, second_attempt)
    durable = harness.repository.start_or_resume_capture_attempt(
        work=work,
        context=harness.context,
        capture=first.capture,
        requested_at=harness.clock.now(),
    )
    persisted_command = harness.repository.capture_commands[
        work.workflow.operation.operation_pub_id
    ]
    assert durable.command == persisted_command
    assert durable.capture.capture_state is CaptureState.CAPTURING

    operation = harness.repository.load_operation(work.workflow.operation)
    assert operation is not None
    capture, link = harness.capture_coordinator().resume(
        work=work,
        context=harness.context,
        operation=operation,
        explicit_retry=True,
    )

    assert capture.capture_state is CaptureState.COMPLETED
    assert link is not None
    assert harness.repository.capture_commands[operation.identity.operation_pub_id] == (
        persisted_command
    )
    assert harness.capture_store.invocations[second_attempt] == 1
    assert harness.owner_store.wal.submit_invocations == 1


def test_capture_retry_records_fact_and_outbox_in_the_same_entry() -> None:
    harness = CoordinatorHarness()
    attempt = harness.work.capture_attempt_ref
    harness.capture_store.modes_by_attempt[attempt] = "failed"
    harness.prepare()
    failed = harness.coordinator().run(harness.work)
    assert failed.fact.outcome is SlotOutcome.CONFIRMED_SENT_CAPTURE_FAILED
    second_attempt = "capture-attempt-consumer-web-2"
    work = _set_capture_attempt(harness, second_attempt)

    recovered = harness.capture_coordinator().retry(work=work, context=harness.context)

    outcome_events = [
        event
        for event in harness.repository.outboxes.values()
        if event.event_type == "collection.slot.outcome"
    ]
    assert recovered.fact.fact_version == failed.fact.fact_version + 1
    assert len(outcome_events) == 2
    assert all(
        event.outbox_key in harness.repository.published_outboxes for event in outcome_events
    )
    assert harness.owner_store.wal.submit_invocations == 1


@pytest.mark.parametrize(
    "point",
    (
        CrashPoint.AFTER_ANALYSIS_QUEUE,
        CrashPoint.AFTER_ANALYSIS_START,
        CrashPoint.AFTER_ANALYSIS_GATEWAY,
    ),
)
def test_analysis_queue_running_and_gateway_crashes_resume_without_submit(
    point: CrashPoint,
) -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    submission = harness.coordinator().run(harness.work)
    assert submission.capture_link is not None
    submit_calls = harness.owner_store.wal.submit_invocations
    with pytest.raises(InjectedCrash, match=point.value):
        harness.analysis_coordinator(OneShotCrashHook(point)).retry(
            work=harness.work,
            attempt_ref="analysis-attempt-1",
            analyzer_revision="analyzer-v1",
            analysis_policy_revision="analysis-policy-v1",
        )
    durable = harness.repository.load_analysis(harness.work.workflow.operation)
    assert durable is not None
    expected_state = {
        CrashPoint.AFTER_ANALYSIS_QUEUE: AnalysisState.QUEUED,
        CrashPoint.AFTER_ANALYSIS_START: AnalysisState.RUNNING,
        CrashPoint.AFTER_ANALYSIS_GATEWAY: AnalysisState.RUNNING,
    }[point]
    assert durable.analysis_state is expected_state

    recovered = harness.analysis_coordinator().retry(
        work=harness.work,
        attempt_ref="analysis-attempt-1",
        analyzer_revision="analyzer-v1",
        analysis_policy_revision="analysis-policy-v1",
    )

    assert recovered.analysis is not None
    assert recovered.analysis.analysis_state is AnalysisState.COMPLETED
    assert harness.owner_store.wal.submit_invocations == submit_calls == 1
    expected_invocations = 2 if point is CrashPoint.AFTER_ANALYSIS_GATEWAY else 1
    assert harness.analysis_store.invocations["analysis-attempt-1"] == expected_invocations
    assert len(harness.analysis_store.immutable_effects) == 1
    with pytest.raises(
        SubmissionCoordinatorError,
        match="completed_analysis_is_immutable_final",
    ):
        harness.analysis_coordinator().retry(
            work=harness.work,
            attempt_ref="analysis-attempt-1",
            analyzer_revision="analyzer-v1",
            analysis_policy_revision="analysis-policy-v1",
        )
    assert harness.owner_store.wal.submit_invocations == 1


def test_failed_analysis_allocates_new_attempt_against_same_immutable_capture() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    submission = harness.coordinator().run(harness.work)
    assert submission.capture_link is not None
    initial = harness.repository.store_analysis(
        expected_state_version=None,
        analysis=initial_analysis_truth(submission.capture_link),
    )
    command = AnalysisCommand(
        capture_link_key=submission.capture_link.capture_link_key,
        capture_content_sha256=submission.capture_link.content_sha256,
        expected_analysis_version=initial.state_version,
        attempt_ref="analysis-attempt-failed",
        analyzer_revision="analyzer-v1",
        analysis_policy_revision="analysis-policy-v1",
        requested_at=harness.clock.now(),
    )
    queued_attempt = harness.repository.queue_or_resume_analysis_attempt(
        analysis=initial,
        command=command,
    )
    running = harness.repository.store_analysis(
        expected_state_version=queued_attempt.analysis.state_version,
        analysis=start_analysis(
            queued_attempt.analysis,
            attempt_ref=command.attempt_ref,
            started_at=harness.clock.now(),
        ),
    )
    failed = harness.repository.store_analysis(
        expected_state_version=running.state_version,
        analysis=apply_analysis_disposition(
            running,
            AnalysisDisposition(
                analysis_state=AnalysisState.FAILED,
                attempt_ref=command.attempt_ref,
                evidence_sha256=HASH_C,
                completed_at=harness.clock.now(),
            ),
        ),
    )
    assert failed.analysis_state is AnalysisState.FAILED

    recovered = harness.analysis_coordinator().retry(
        work=harness.work,
        attempt_ref="analysis-attempt-retry",
        analyzer_revision="analyzer-v1",
        analysis_policy_revision="analysis-policy-v1",
    )

    assert recovered.analysis is not None
    assert recovered.analysis.analysis_state is AnalysisState.COMPLETED
    assert recovered.analysis.capture_link == submission.capture_link
    assert harness.owner_store.wal.submit_invocations == 1


def test_submission_capability_gate_excludes_stage5_analysis_but_analysis_fails_closed() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    harness.repository.capability_overrides["durable_analysis_command"] = False
    harness.repository.capability_overrides["atomic_prepare_and_reserve"] = False

    submission = harness.coordinator().run(harness.work)

    assert submission.operation.send_state is SendState.CONFIRMED_SENT
    calls_before = dict(harness.analysis_store.invocations)
    with pytest.raises(
        SubmissionCoordinatorError,
        match="analysis_schema_capabilities_missing:durable_analysis_command",
    ):
        harness.analysis_coordinator().retry(
            work=harness.work,
            attempt_ref="analysis-attempt-1",
            analyzer_revision="analyzer-v1",
            analysis_policy_revision="analysis-policy-v1",
        )
    assert harness.analysis_store.invocations == calls_before
    assert harness.owner_store.wal.submit_invocations == 1


def test_submission_missing_atomic_terminal_capability_fails_before_gateway() -> None:
    harness = CoordinatorHarness()
    harness.prepare()
    harness.repository.capability_overrides["atomic_terminal_and_quota"] = False

    with pytest.raises(
        SubmissionCoordinatorError,
        match="schema_capabilities_missing:atomic_terminal_and_quota",
    ):
        harness.coordinator().run(harness.work)

    assert harness.owner_store.wal.submit_invocations == 0


def test_preparation_missing_atomic_reserve_capability_fails_without_rows() -> None:
    harness = CoordinatorHarness()
    harness.repository.capability_overrides["atomic_prepare_and_reserve"] = False

    with pytest.raises(
        SubmissionCoordinatorError,
        match="preparation_schema_capabilities_missing:atomic_prepare_and_reserve",
    ):
        harness.preparation_coordinator().run(harness.prepare_work)

    assert harness.repository.operations == {}
    assert harness.repository.quota.effects == {}


def test_workflow_inputs_remain_constant_size_and_reject_task_arrays() -> None:
    harness = CoordinatorHarness()
    prepare_payload = canonical_json(harness.prepare_work)
    submission_payload = canonical_json(harness.work)
    assert len(prepare_payload) < 4096
    assert len(submission_payload) < 4096
    assert "tasks" not in PrepareWorkItem.model_fields
    assert "tasks" not in SubmissionWorkItem.model_fields
    with pytest.raises(ValidationError, match="tasks"):
        SubmissionWorkItem.model_validate(
            {**harness.work.model_dump(mode="python"), "tasks": ["x"] * 1000}
        )


def test_ports_and_import_graph_expose_no_v1_submit_reconstruction_path() -> None:
    import geo_platform.collection.submission_v2 as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
    forbidden = {
        name
        for name in imported_modules
        if "run_service" in name
        or name.startswith("workflows")
        or ".adapters" in name
        or name.endswith(".adapters")
    }
    assert forbidden == set()
    assert "submit_once" not in FakePreflightGateway.__dict__
    assert "submit_once" not in FakeReconciliationGateway.__dict__
    assert "submit_once" not in FakeCaptureGateway.__dict__
    assert "submit_once" not in FakeAnalysisGateway.__dict__
    assert "submit_once" in FakeWebSubmitGateway.__dict__
    assert Path(inspect.getsourcefile(module) or "").name == "submission_v2.py"
