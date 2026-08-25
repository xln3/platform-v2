"""Isolated Temporal orchestration for frozen collection v2 partitions.

This module intentionally has no v1 compatibility branch. Its input, activity
commands, receipts, Continue-As-New input, query state, and result are bounded
scalar contracts. Per-slot material stays behind the page activity boundary and
never enters workflow history.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

if TYPE_CHECKING:
    from geo_platform.collection.identity_v2 import CampaignWorkflowReference

with workflow.unsafe.imports_passed_through():
    from workflows.activities.collection_v2 import (
        COLLECTION_V2_FINALIZATION_REQUEST_SCHEMA,
        COLLECTION_V2_PAGE_REQUEST_SCHEMA,
        COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
        MAX_COLLECTION_V2_CHECKPOINT_VERSION,
        MAX_COLLECTION_V2_PAGE_SIZE,
        CollectionV2ContractError,
        CollectionV2FinalizationReceipt,
        CollectionV2FinalizationRequest,
        CollectionV2PageReceipt,
        CollectionV2PageRequest,
        CollectionV2ReconciliationReceipt,
        CollectionV2ReconciliationRequest,
        execute_collection_v2_page,
        initial_collection_v2_reconciliation_checkpoint_digest,
        reconcile_collection_v2_partition,
        seed_collection_v2_checkpoint_chain,
        seed_collection_v2_reconciliation_chain,
        verify_collection_v2_partition_complete,
    )

COLLECTION_V2_OUTBOX_TYPE: Literal["geo_collection_v2"] = "geo_collection_v2"
COLLECTION_V2_PAYLOAD_SCHEMA: Literal["collection-workflow-v2"] = "collection-workflow-v2"
COLLECTION_V2_RESULT_SCHEMA: Literal["collection-workflow-result-v2"] = (
    "collection-workflow-result-v2"
)
COLLECTION_V2_TASK_QUEUE: Literal["geo-platform-v2-collection-v2"] = "geo-platform-v2-collection-v2"
COLLECTION_V2_WORKFLOW_TYPE: Literal["GeoCollectionV2Workflow"] = "GeoCollectionV2Workflow"

MAX_COLLECTION_V2_WORKFLOW_PAYLOAD_BYTES = 8_192
MAX_COLLECTION_V2_PAGES_PER_RUN = 100
MAX_COLLECTION_V2_RECONCILIATION_ATTEMPTS_PER_RUN = 100
COLLECTION_V2_RECONCILIATION_RETRY_DELAY = timedelta(seconds=1)

CollectionV2Phase = Literal["dispatching", "reconciling", "finalizing"]
CollectionV2TerminalState = Literal["completed", "cancelled"]

_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_ref(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _OPAQUE_REF.fullmatch(value) is None:
        raise CollectionV2ContractError(f"invalid_reference:{field}")


def _require_digest(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CollectionV2ContractError(f"invalid_sha256:{field}")


def _require_int(value: int, *, field: str, minimum: int, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectionV2ContractError(f"integer_required:{field}")
    if value < minimum or (maximum is not None and value > maximum):
        raise CollectionV2ContractError(f"integer_out_of_range:{field}")


def _parse_utc(value: str, *, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise CollectionV2ContractError(f"invalid_utc_timestamp:{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectionV2ContractError(f"invalid_utc_timestamp:{field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CollectionV2ContractError(f"utc_timestamp_required:{field}")
    return parsed.astimezone(UTC)


def _canonical_payload(instance: Any) -> str:
    return json.dumps(asdict(instance), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class CollectionV2WorkflowInput:
    """Strict O(1) workflow state for one persisted campaign-wide partition."""

    schema_version: str
    tenant_pub_id: str
    project_pub_id: str
    config_revision_pub_id: str
    config_revision_hash: str
    campaign_pub_id: str
    specification_hash: str
    partition_pub_id: str
    partition_digest: str
    membership_digest_version: str
    membership_digest: str
    canonical_enumeration_version: str
    slot_generator_version: str
    start_slot_ordinal: int
    end_slot_ordinal_exclusive: int
    cursor: int
    page_size: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    capability_policy_revision: str
    control_policy_revision: str
    comparison_policy_revision: str
    scheduling_window_start_utc: str
    scheduling_window_end_utc: str
    idempotency_key: str
    generation: int = 1
    continue_as_new_after_pages: int = 25
    continue_as_new_after_reconciliation_attempts: int = 10
    phase: CollectionV2Phase = "dispatching"
    cancel_requested: bool = False
    reconciliation_attempt: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != COLLECTION_V2_PAYLOAD_SCHEMA:
            raise CollectionV2ContractError(f"unsupported_schema:{self.schema_version}")
        for field in (
            "tenant_pub_id",
            "project_pub_id",
            "config_revision_pub_id",
            "campaign_pub_id",
            "partition_pub_id",
            "membership_digest_version",
            "canonical_enumeration_version",
            "slot_generator_version",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
            "capability_policy_revision",
            "control_policy_revision",
            "comparison_policy_revision",
            "idempotency_key",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "config_revision_hash",
            "specification_hash",
            "partition_digest",
            "membership_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        for field in (
            "start_slot_ordinal",
            "end_slot_ordinal_exclusive",
            "cursor",
            "checkpoint_version",
            "reconciliation_checkpoint_version",
            "reconciliation_attempt",
        ):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        _require_int(
            self.page_size,
            field="page_size",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGE_SIZE,
        )
        _require_int(
            self.generation,
            field="generation",
            minimum=1,
            maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
        )
        _require_int(
            self.continue_as_new_after_pages,
            field="continue_as_new_after_pages",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGES_PER_RUN,
        )
        _require_int(
            self.continue_as_new_after_reconciliation_attempts,
            field="continue_as_new_after_reconciliation_attempts",
            minimum=1,
            maximum=MAX_COLLECTION_V2_RECONCILIATION_ATTEMPTS_PER_RUN,
        )
        if not isinstance(self.cancel_requested, bool):
            raise CollectionV2ContractError("boolean_required:cancel_requested")
        if self.phase not in {"dispatching", "reconciling", "finalizing"}:
            raise CollectionV2ContractError("invalid_workflow_phase")
        if self.end_slot_ordinal_exclusive <= self.start_slot_ordinal:
            raise CollectionV2ContractError("workflow_partition_not_increasing")
        if not (self.start_slot_ordinal <= self.cursor <= self.end_slot_ordinal_exclusive):
            raise CollectionV2ContractError("workflow_cursor_out_of_partition")
        if self.phase == "dispatching" and self.cursor >= self.end_slot_ordinal_exclusive:
            raise CollectionV2ContractError("dispatching_cursor_not_before_partition_end")
        if self.phase == "dispatching" and self.cancel_requested:
            raise CollectionV2ContractError("cancelled_workflow_must_reconcile")
        if self.phase == "finalizing" and self.cursor != self.end_slot_ordinal_exclusive:
            raise CollectionV2ContractError("finalizing_cursor_not_at_partition_end")
        if self.phase == "finalizing" and self.cancel_requested:
            raise CollectionV2ContractError("cancelled_workflow_must_reconcile")
        window_start = _parse_utc(
            self.scheduling_window_start_utc,
            field="scheduling_window_start_utc",
        )
        window_end = _parse_utc(
            self.scheduling_window_end_utc,
            field="scheduling_window_end_utc",
        )
        if window_end <= window_start:
            raise CollectionV2ContractError("scheduling_window_not_increasing")
        if len(self.payload_json.encode("utf-8")) > MAX_COLLECTION_V2_WORKFLOW_PAYLOAD_BYTES:
            raise CollectionV2ContractError("workflow_payload_too_large")

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)

    def page_request(self) -> CollectionV2PageRequest:
        if self.phase != "dispatching":
            raise CollectionV2ContractError("page_request_requires_dispatching_phase")
        return CollectionV2PageRequest(
            schema_version=COLLECTION_V2_PAGE_REQUEST_SCHEMA,
            tenant_pub_id=self.tenant_pub_id,
            campaign_pub_id=self.campaign_pub_id,
            partition_pub_id=self.partition_pub_id,
            partition_digest=self.partition_digest,
            membership_digest=self.membership_digest,
            cursor=self.cursor,
            page_size=self.page_size,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            checkpoint_version=self.checkpoint_version,
            checkpoint_chain_digest=self.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=self.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=self.reconciliation_checkpoint_version,
            reconciliation_chain_digest=self.reconciliation_chain_digest,
            capability_policy_revision=self.capability_policy_revision,
            control_policy_revision=self.control_policy_revision,
        )

    def reconciliation_request(self) -> CollectionV2ReconciliationRequest:
        if self.phase != "reconciling":
            raise CollectionV2ContractError("reconciliation_request_requires_reconciling_phase")
        return CollectionV2ReconciliationRequest(
            schema_version=COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
            tenant_pub_id=self.tenant_pub_id,
            campaign_pub_id=self.campaign_pub_id,
            partition_pub_id=self.partition_pub_id,
            partition_digest=self.partition_digest,
            membership_digest=self.membership_digest,
            cursor=self.cursor,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            checkpoint_version=self.checkpoint_version,
            checkpoint_chain_digest=self.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=self.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=self.reconciliation_checkpoint_version,
            reconciliation_chain_digest=self.reconciliation_chain_digest,
            control_policy_revision=self.control_policy_revision,
        )

    def finalization_request(self) -> CollectionV2FinalizationRequest:
        if self.phase != "finalizing":
            raise CollectionV2ContractError("finalization_request_requires_finalizing_phase")
        return CollectionV2FinalizationRequest(
            schema_version=COLLECTION_V2_FINALIZATION_REQUEST_SCHEMA,
            tenant_pub_id=self.tenant_pub_id,
            campaign_pub_id=self.campaign_pub_id,
            partition_pub_id=self.partition_pub_id,
            partition_digest=self.partition_digest,
            membership_digest=self.membership_digest,
            cursor=self.cursor,
            end_slot_ordinal_exclusive=self.end_slot_ordinal_exclusive,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            checkpoint_version=self.checkpoint_version,
            checkpoint_chain_digest=self.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=self.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=self.reconciliation_checkpoint_version,
            reconciliation_chain_digest=self.reconciliation_chain_digest,
        )

    def with_page_receipt(self, receipt: CollectionV2PageReceipt) -> CollectionV2WorkflowInput:
        if self.phase != "dispatching":
            raise CollectionV2ContractError("page_receipt_requires_dispatching_phase")
        if (
            receipt.campaign_pub_id != self.campaign_pub_id
            or receipt.partition_pub_id != self.partition_pub_id
            or receipt.partition_digest != self.partition_digest
        ):
            raise CollectionV2ContractError("page_receipt_identity_drift")
        if receipt.prior_cursor != self.cursor:
            raise CollectionV2ContractError("page_receipt_prior_cursor_drift")
        if (
            receipt.prior_checkpoint_ref != self.checkpoint_ref
            or receipt.prior_checkpoint_digest != self.checkpoint_digest
            or receipt.prior_checkpoint_version != self.checkpoint_version
            or receipt.prior_checkpoint_chain_digest != self.checkpoint_chain_digest
        ):
            raise CollectionV2ContractError("page_receipt_prior_checkpoint_drift")
        if (
            receipt.prior_reconciliation_checkpoint_ref != self.reconciliation_checkpoint_ref
            or receipt.prior_reconciliation_checkpoint_digest
            != self.reconciliation_checkpoint_digest
            or receipt.prior_reconciliation_checkpoint_version
            != self.reconciliation_checkpoint_version
            or receipt.prior_reconciliation_chain_digest != self.reconciliation_chain_digest
        ):
            raise CollectionV2ContractError("page_receipt_prior_reconciliation_drift")
        if receipt.page_item_count > self.page_size:
            raise CollectionV2ContractError("page_receipt_exceeds_bounded_page")
        if receipt.next_cursor > self.end_slot_ordinal_exclusive:
            raise CollectionV2ContractError("page_receipt_escaped_partition")
        if receipt.requires_reconciliation:
            next_phase: CollectionV2Phase = "reconciling"
        elif receipt.next_cursor == self.end_slot_ordinal_exclusive:
            next_phase = "finalizing"
        else:
            next_phase = "dispatching"
        return replace(
            self,
            cursor=receipt.next_cursor,
            checkpoint_ref=receipt.checkpoint_ref,
            checkpoint_digest=receipt.checkpoint_digest,
            checkpoint_version=receipt.checkpoint_version,
            checkpoint_chain_digest=receipt.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=receipt.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=receipt.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=receipt.reconciliation_checkpoint_version,
            reconciliation_chain_digest=receipt.reconciliation_chain_digest,
            phase=next_phase,
            reconciliation_attempt=0,
        )

    def with_reconciliation_receipt(
        self,
        receipt: CollectionV2ReconciliationReceipt,
    ) -> CollectionV2WorkflowInput:
        if self.phase != "reconciling":
            raise CollectionV2ContractError("reconciliation_receipt_requires_reconciling_phase")
        if (
            receipt.campaign_pub_id != self.campaign_pub_id
            or receipt.partition_pub_id != self.partition_pub_id
            or receipt.partition_digest != self.partition_digest
            or receipt.cursor != self.cursor
        ):
            raise CollectionV2ContractError("reconciliation_receipt_identity_drift")
        if (
            receipt.prior_checkpoint_ref != self.checkpoint_ref
            or receipt.prior_checkpoint_digest != self.checkpoint_digest
            or receipt.prior_checkpoint_version != self.checkpoint_version
            or receipt.prior_checkpoint_chain_digest != self.checkpoint_chain_digest
        ):
            raise CollectionV2ContractError("reconciliation_receipt_prior_checkpoint_drift")
        if (
            receipt.prior_reconciliation_checkpoint_ref != self.reconciliation_checkpoint_ref
            or receipt.prior_reconciliation_checkpoint_digest
            != self.reconciliation_checkpoint_digest
            or receipt.prior_reconciliation_checkpoint_version
            != self.reconciliation_checkpoint_version
            or receipt.prior_reconciliation_chain_digest != self.reconciliation_chain_digest
        ):
            raise CollectionV2ContractError("reconciliation_receipt_prior_reconciliation_drift")
        if receipt.state == "pending":
            next_phase: CollectionV2Phase = "reconciling"
            next_attempt = self.reconciliation_attempt + 1
        elif self.cancel_requested:
            next_phase = "reconciling"
            next_attempt = 0
        elif self.cursor == self.end_slot_ordinal_exclusive:
            next_phase = "finalizing"
            next_attempt = 0
        else:
            next_phase = "dispatching"
            next_attempt = 0
        return replace(
            self,
            checkpoint_ref=receipt.checkpoint_ref,
            checkpoint_digest=receipt.checkpoint_digest,
            checkpoint_version=receipt.checkpoint_version,
            checkpoint_chain_digest=receipt.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=receipt.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=receipt.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=receipt.reconciliation_checkpoint_version,
            reconciliation_chain_digest=receipt.reconciliation_chain_digest,
            phase=next_phase,
            reconciliation_attempt=next_attempt,
        )

    def request_cancel(self) -> CollectionV2WorkflowInput:
        return replace(self, phase="reconciling", cancel_requested=True)

    def validate_finalization_receipt(self, receipt: CollectionV2FinalizationReceipt) -> None:
        if self.phase != "finalizing":
            raise CollectionV2ContractError("finalization_receipt_requires_finalizing_phase")
        if (
            receipt.campaign_pub_id != self.campaign_pub_id
            or receipt.partition_pub_id != self.partition_pub_id
            or receipt.partition_digest != self.partition_digest
            or receipt.cursor != self.cursor
        ):
            raise CollectionV2ContractError("finalization_receipt_identity_drift")
        if (
            receipt.checkpoint_ref != self.checkpoint_ref
            or receipt.checkpoint_digest != self.checkpoint_digest
            or receipt.checkpoint_version != self.checkpoint_version
            or receipt.checkpoint_chain_digest != self.checkpoint_chain_digest
        ):
            raise CollectionV2ContractError("finalization_receipt_checkpoint_drift")
        if (
            receipt.reconciliation_checkpoint_ref != self.reconciliation_checkpoint_ref
            or receipt.reconciliation_checkpoint_digest != self.reconciliation_checkpoint_digest
            or receipt.reconciliation_checkpoint_version != self.reconciliation_checkpoint_version
            or receipt.reconciliation_chain_digest != self.reconciliation_chain_digest
        ):
            raise CollectionV2ContractError("finalization_receipt_reconciliation_drift")

    def next_generation(self) -> CollectionV2WorkflowInput:
        return replace(self, generation=self.generation + 1)


def build_collection_v2_workflow_input(
    reference: CampaignWorkflowReference,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    partition_digest: str,
    canonical_enumeration_version: str,
    checkpoint_ref: str,
    checkpoint_digest: str,
    reconciliation_checkpoint_ref: str,
    capability_policy_revision: str,
    control_policy_revision: str,
    comparison_policy_revision: str,
    scheduling_window_start_utc: str,
    scheduling_window_end_utc: str,
    idempotency_key: str,
    continue_as_new_after_pages: int = 25,
) -> CollectionV2WorkflowInput:
    """Build launch state from the persisted frozen partition reference."""

    from geo_platform.collection.identity_v2 import CampaignWorkflowReference

    if not isinstance(reference, CampaignWorkflowReference):
        raise CollectionV2ContractError("persisted_campaign_workflow_reference_required")
    reconciliation_checkpoint_digest = initial_collection_v2_reconciliation_checkpoint_digest(
        partition_digest=partition_digest,
        cursor=reference.cursor,
        reconciliation_checkpoint_ref=reconciliation_checkpoint_ref,
    )
    checkpoint_version = 0
    reconciliation_checkpoint_version = 0
    checkpoint_chain_digest = seed_collection_v2_checkpoint_chain(
        partition_digest=partition_digest,
        cursor=reference.cursor,
        checkpoint_ref=checkpoint_ref,
        checkpoint_digest=checkpoint_digest,
        checkpoint_version=checkpoint_version,
    )
    reconciliation_chain_digest = seed_collection_v2_reconciliation_chain(
        partition_digest=partition_digest,
        cursor=reference.cursor,
        reconciliation_checkpoint_ref=reconciliation_checkpoint_ref,
        reconciliation_checkpoint_digest=reconciliation_checkpoint_digest,
        reconciliation_checkpoint_version=reconciliation_checkpoint_version,
    )
    initial_phase: CollectionV2Phase = (
        "finalizing" if reference.cursor == reference.end_slot_ordinal_exclusive else "dispatching"
    )
    return CollectionV2WorkflowInput(
        schema_version=COLLECTION_V2_PAYLOAD_SCHEMA,
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        config_revision_pub_id=reference.config_revision_pub_id,
        config_revision_hash=reference.config_revision_hash,
        campaign_pub_id=reference.campaign_pub_id,
        specification_hash=reference.specification_hash,
        partition_pub_id=reference.partition_pub_id,
        partition_digest=partition_digest,
        membership_digest_version=reference.membership_digest_version,
        membership_digest=reference.membership_hash,
        canonical_enumeration_version=canonical_enumeration_version,
        slot_generator_version=reference.slot_generator_version,
        start_slot_ordinal=reference.start_slot_ordinal,
        end_slot_ordinal_exclusive=reference.end_slot_ordinal_exclusive,
        cursor=reference.cursor,
        page_size=reference.page_size,
        checkpoint_ref=checkpoint_ref,
        checkpoint_digest=checkpoint_digest,
        checkpoint_version=checkpoint_version,
        checkpoint_chain_digest=checkpoint_chain_digest,
        reconciliation_checkpoint_ref=reconciliation_checkpoint_ref,
        reconciliation_checkpoint_digest=reconciliation_checkpoint_digest,
        reconciliation_checkpoint_version=reconciliation_checkpoint_version,
        reconciliation_chain_digest=reconciliation_chain_digest,
        capability_policy_revision=capability_policy_revision,
        control_policy_revision=control_policy_revision,
        comparison_policy_revision=comparison_policy_revision,
        scheduling_window_start_utc=scheduling_window_start_utc,
        scheduling_window_end_utc=scheduling_window_end_utc,
        idempotency_key=idempotency_key,
        continue_as_new_after_pages=continue_as_new_after_pages,
        phase=initial_phase,
    )


@dataclass(frozen=True)
class CollectionV2WorkflowResult:
    schema_version: str
    state: CollectionV2TerminalState
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    cursor: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    processed_slot_count: int
    generation: int
    last_page_digest: str | None = None
    reconciliation_outcome_ref: str | None = None
    terminal_proof_ref: str | None = None
    terminal_proof_digest: str | None = None
    terminal_chain_digest: str | None = None


@dataclass(frozen=True)
class CollectionV2WorkflowStatus:
    partition_pub_id: str | None
    cursor: int
    paused: bool
    cancel_requested: bool
    pages_in_generation: int
    reconciliation_attempts_in_generation: int
    reconciliation_attempt: int
    phase: CollectionV2Phase | None
    generation: int
    checkpoint_ref: str | None
    checkpoint_version: int
    reconciliation_checkpoint_version: int


_PAGE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)
_RECONCILIATION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=10,
)
_FINALIZATION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


@workflow.defn(name=COLLECTION_V2_WORKFLOW_TYPE)
class GeoCollectionV2Workflow:
    """Coordinate bounded durable pages without carrying slot state in history."""

    def __init__(self) -> None:
        self._paused = False
        self._cancel_requested = False
        self._pages_in_generation = 0
        self._reconciliation_attempts_in_generation = 0
        self._current: CollectionV2WorkflowInput | None = None
        self._last_page_digest: str | None = None
        self._reconciliation_outcome_ref: str | None = None

    @workflow.signal
    async def pause(self) -> None:
        self._paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._paused = False

    @workflow.signal
    async def cancel(self) -> None:
        self._cancel_requested = True

    @workflow.query
    def status(self) -> CollectionV2WorkflowStatus:
        current = self._current
        return CollectionV2WorkflowStatus(
            partition_pub_id=current.partition_pub_id if current is not None else None,
            cursor=current.cursor if current is not None else 0,
            paused=self._paused,
            cancel_requested=self._cancel_requested,
            pages_in_generation=self._pages_in_generation,
            reconciliation_attempts_in_generation=(self._reconciliation_attempts_in_generation),
            reconciliation_attempt=(current.reconciliation_attempt if current is not None else 0),
            phase=current.phase if current is not None else None,
            generation=current.generation if current is not None else 0,
            checkpoint_ref=current.checkpoint_ref if current is not None else None,
            checkpoint_version=current.checkpoint_version if current is not None else 0,
            reconciliation_checkpoint_version=(
                current.reconciliation_checkpoint_version if current is not None else 0
            ),
        )

    def _result(
        self,
        data: CollectionV2WorkflowInput,
        state: CollectionV2TerminalState,
        *,
        finalization: CollectionV2FinalizationReceipt | None = None,
    ) -> CollectionV2WorkflowResult:
        if state == "completed" and finalization is None:
            raise CollectionV2ContractError("completed_result_requires_terminal_proof")
        if state == "cancelled" and finalization is not None:
            raise CollectionV2ContractError("cancelled_result_forbids_terminal_proof")
        return CollectionV2WorkflowResult(
            schema_version=COLLECTION_V2_RESULT_SCHEMA,
            state=state,
            campaign_pub_id=data.campaign_pub_id,
            partition_pub_id=data.partition_pub_id,
            partition_digest=data.partition_digest,
            cursor=data.cursor,
            checkpoint_ref=data.checkpoint_ref,
            checkpoint_digest=data.checkpoint_digest,
            checkpoint_version=data.checkpoint_version,
            checkpoint_chain_digest=data.checkpoint_chain_digest,
            reconciliation_checkpoint_ref=data.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=data.reconciliation_checkpoint_digest,
            reconciliation_checkpoint_version=data.reconciliation_checkpoint_version,
            reconciliation_chain_digest=data.reconciliation_chain_digest,
            processed_slot_count=data.cursor - data.start_slot_ordinal,
            generation=data.generation,
            last_page_digest=self._last_page_digest,
            reconciliation_outcome_ref=self._reconciliation_outcome_ref,
            terminal_proof_ref=(
                finalization.terminal_proof_ref if finalization is not None else None
            ),
            terminal_proof_digest=(
                finalization.terminal_proof_digest if finalization is not None else None
            ),
            terminal_chain_digest=(
                finalization.terminal_chain_digest if finalization is not None else None
            ),
        )

    async def _reconcile(
        self,
        data: CollectionV2WorkflowInput,
    ) -> tuple[CollectionV2WorkflowInput, CollectionV2ReconciliationReceipt]:
        receipt = await workflow.execute_activity(
            reconcile_collection_v2_partition,
            data.reconciliation_request(),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RECONCILIATION_RETRY_POLICY,
        )
        updated = data.with_reconciliation_receipt(receipt)
        self._current = updated
        self._reconciliation_outcome_ref = receipt.outcome_ref
        self._reconciliation_attempts_in_generation += 1
        return updated, receipt

    @workflow.run
    async def run(self, data: CollectionV2WorkflowInput) -> CollectionV2WorkflowResult:
        self._current = data
        self._cancel_requested = data.cancel_requested
        while True:
            if self._cancel_requested and not data.cancel_requested:
                data = data.request_cancel()
                self._current = data

            if data.phase == "reconciling":
                data, reconciliation = await self._reconcile(data)
                if reconciliation.state == "pending":
                    await workflow.sleep(COLLECTION_V2_RECONCILIATION_RETRY_DELAY)
                    if (
                        self._reconciliation_attempts_in_generation
                        >= data.continue_as_new_after_reconciliation_attempts
                    ):
                        workflow.continue_as_new(data.next_generation())
                    continue
                if self._cancel_requested:
                    return self._result(data, "cancelled")
                continue

            if data.phase == "finalizing":
                finalization = await workflow.execute_activity(
                    verify_collection_v2_partition_complete,
                    data.finalization_request(),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_FINALIZATION_RETRY_POLICY,
                )
                data.validate_finalization_receipt(finalization)
                if self._cancel_requested:
                    data = data.request_cancel()
                    self._current = data
                    continue
                return self._result(data, "completed", finalization=finalization)

            await workflow.wait_condition(lambda: not self._paused or self._cancel_requested)
            if self._cancel_requested:
                data = data.request_cancel()
                self._current = data
                continue

            receipt = await workflow.execute_activity(
                execute_collection_v2_page,
                data.page_request(),
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=_PAGE_RETRY_POLICY,
            )
            data = data.with_page_receipt(receipt)
            self._current = data
            self._last_page_digest = receipt.page_digest
            self._pages_in_generation += 1

            if self._cancel_requested:
                data = data.request_cancel()
                self._current = data
                continue
            if data.phase != "dispatching" or self._paused:
                continue
            if self._pages_in_generation >= data.continue_as_new_after_pages:
                workflow.continue_as_new(data.next_generation())


__all__ = [
    "COLLECTION_V2_OUTBOX_TYPE",
    "COLLECTION_V2_PAYLOAD_SCHEMA",
    "COLLECTION_V2_RECONCILIATION_RETRY_DELAY",
    "COLLECTION_V2_RESULT_SCHEMA",
    "COLLECTION_V2_TASK_QUEUE",
    "COLLECTION_V2_WORKFLOW_TYPE",
    "MAX_COLLECTION_V2_PAGES_PER_RUN",
    "MAX_COLLECTION_V2_RECONCILIATION_ATTEMPTS_PER_RUN",
    "MAX_COLLECTION_V2_WORKFLOW_PAYLOAD_BYTES",
    "CollectionV2WorkflowInput",
    "CollectionV2WorkflowResult",
    "CollectionV2WorkflowStatus",
    "GeoCollectionV2Workflow",
    "build_collection_v2_workflow_input",
]
