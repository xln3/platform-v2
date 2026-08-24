"""Isolated Temporal orchestration for frozen collection v2 partitions.

This module intentionally has no v1 compatibility branch.  Its input, activity
commands, activity receipts, Continue-As-New input, query state, and result are
all constant-size scalar contracts.  Per-slot target and query material is
loaded from the frozen campaign page behind bounded activities and never enters
workflow history.
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
        COLLECTION_V2_PAGE_REQUEST_SCHEMA,
        COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
        MAX_COLLECTION_V2_PAGE_SIZE,
        CollectionV2ContractError,
        CollectionV2PageReceipt,
        CollectionV2PageRequest,
        CollectionV2ReconciliationReceipt,
        CollectionV2ReconciliationRequest,
        execute_collection_v2_page,
        reconcile_collection_v2_partition,
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
    """Strict O(1) workflow input for one persisted campaign-wide partition."""

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
    reconciliation_checkpoint_ref: str
    capability_policy_revision: str
    control_policy_revision: str
    comparison_policy_revision: str
    scheduling_window_start_utc: str
    scheduling_window_end_utc: str
    idempotency_key: str
    generation: int = 1
    continue_as_new_after_pages: int = 25

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
        ):
            _require_digest(getattr(self, field), field=field)
        _require_int(self.start_slot_ordinal, field="start_slot_ordinal", minimum=0)
        _require_int(
            self.end_slot_ordinal_exclusive,
            field="end_slot_ordinal_exclusive",
            minimum=1,
        )
        _require_int(self.cursor, field="cursor", minimum=0)
        _require_int(
            self.page_size,
            field="page_size",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGE_SIZE,
        )
        _require_int(self.generation, field="generation", minimum=1)
        _require_int(
            self.continue_as_new_after_pages,
            field="continue_as_new_after_pages",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGES_PER_RUN,
        )
        if not (self.start_slot_ordinal <= self.cursor <= self.end_slot_ordinal_exclusive):
            raise CollectionV2ContractError("workflow_cursor_out_of_partition")
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
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            capability_policy_revision=self.capability_policy_revision,
            control_policy_revision=self.control_policy_revision,
        )

    def with_page_receipt(self, receipt: CollectionV2PageReceipt) -> CollectionV2WorkflowInput:
        if (
            receipt.campaign_pub_id != self.campaign_pub_id
            or receipt.partition_pub_id != self.partition_pub_id
            or receipt.partition_digest != self.partition_digest
        ):
            raise CollectionV2ContractError("page_receipt_identity_drift")
        if receipt.prior_cursor != self.cursor:
            raise CollectionV2ContractError("page_receipt_prior_cursor_drift")
        if receipt.page_item_count > self.page_size:
            raise CollectionV2ContractError("page_receipt_exceeds_bounded_page")
        if receipt.next_cursor > self.end_slot_ordinal_exclusive:
            raise CollectionV2ContractError("page_receipt_escaped_partition")
        return replace(
            self,
            cursor=receipt.next_cursor,
            checkpoint_ref=receipt.checkpoint_ref,
            checkpoint_digest=receipt.checkpoint_digest,
            reconciliation_checkpoint_ref=receipt.reconciliation_checkpoint_ref,
        )

    def with_reconciliation_receipt(
        self,
        receipt: CollectionV2ReconciliationReceipt,
    ) -> CollectionV2WorkflowInput:
        if (
            receipt.campaign_pub_id != self.campaign_pub_id
            or receipt.partition_pub_id != self.partition_pub_id
            or receipt.partition_digest != self.partition_digest
            or receipt.cursor != self.cursor
        ):
            raise CollectionV2ContractError("reconciliation_receipt_identity_drift")
        return replace(
            self,
            checkpoint_ref=receipt.checkpoint_ref,
            checkpoint_digest=receipt.checkpoint_digest,
            reconciliation_checkpoint_ref=receipt.reconciliation_checkpoint_ref,
        )

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
    """Build the launch DTO only from Stage 1's persisted frozen reference.

    The additional values are all constant-size scheduler facts that the Stage 1
    reference intentionally does not own.  In particular, this function never
    invents a partition digest, policy revision, or scheduling window.  Target
    identity remains a per-slot frozen fact resolved by the bounded activity.
    """

    from geo_platform.collection.identity_v2 import CampaignWorkflowReference

    if not isinstance(reference, CampaignWorkflowReference):
        raise CollectionV2ContractError("persisted_campaign_workflow_reference_required")
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
        reconciliation_checkpoint_ref=reconciliation_checkpoint_ref,
        capability_policy_revision=capability_policy_revision,
        control_policy_revision=control_policy_revision,
        comparison_policy_revision=comparison_policy_revision,
        scheduling_window_start_utc=scheduling_window_start_utc,
        scheduling_window_end_utc=scheduling_window_end_utc,
        idempotency_key=idempotency_key,
        continue_as_new_after_pages=continue_as_new_after_pages,
    )


@dataclass(frozen=True)
class CollectionV2WorkflowResult:
    schema_version: str
    state: Literal["completed", "cancelled", "reconciliation_pending"]
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    cursor: int
    checkpoint_ref: str
    checkpoint_digest: str
    reconciliation_checkpoint_ref: str
    processed_slot_count: int
    generation: int
    last_page_digest: str | None = None
    reconciliation_outcome_ref: str | None = None


@dataclass(frozen=True)
class CollectionV2WorkflowStatus:
    partition_pub_id: str | None
    cursor: int
    paused: bool
    cancel_requested: bool
    pages_in_generation: int
    generation: int
    checkpoint_ref: str | None


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


@workflow.defn(name=COLLECTION_V2_WORKFLOW_TYPE)
class GeoCollectionV2Workflow:
    """Coordinate bounded durable pages without carrying slot state in history."""

    def __init__(self) -> None:
        self._paused = False
        self._cancel_requested = False
        self._pages_in_generation = 0
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
            generation=current.generation if current is not None else 0,
            checkpoint_ref=current.checkpoint_ref if current is not None else None,
        )

    def _result(
        self,
        data: CollectionV2WorkflowInput,
        state: Literal["completed", "cancelled", "reconciliation_pending"],
    ) -> CollectionV2WorkflowResult:
        return CollectionV2WorkflowResult(
            schema_version=COLLECTION_V2_RESULT_SCHEMA,
            state=state,
            campaign_pub_id=data.campaign_pub_id,
            partition_pub_id=data.partition_pub_id,
            partition_digest=data.partition_digest,
            cursor=data.cursor,
            checkpoint_ref=data.checkpoint_ref,
            checkpoint_digest=data.checkpoint_digest,
            reconciliation_checkpoint_ref=data.reconciliation_checkpoint_ref,
            processed_slot_count=data.cursor - data.start_slot_ordinal,
            generation=data.generation,
            last_page_digest=self._last_page_digest,
            reconciliation_outcome_ref=self._reconciliation_outcome_ref,
        )

    async def _reconcile(
        self,
        data: CollectionV2WorkflowInput,
    ) -> tuple[CollectionV2WorkflowInput, CollectionV2ReconciliationReceipt]:
        receipt = await workflow.execute_activity(
            reconcile_collection_v2_partition,
            CollectionV2ReconciliationRequest(
                schema_version=COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
                tenant_pub_id=data.tenant_pub_id,
                campaign_pub_id=data.campaign_pub_id,
                partition_pub_id=data.partition_pub_id,
                partition_digest=data.partition_digest,
                membership_digest=data.membership_digest,
                cursor=data.cursor,
                checkpoint_ref=data.checkpoint_ref,
                checkpoint_digest=data.checkpoint_digest,
                reconciliation_checkpoint_ref=data.reconciliation_checkpoint_ref,
                control_policy_revision=data.control_policy_revision,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RECONCILIATION_RETRY_POLICY,
        )
        updated = data.with_reconciliation_receipt(receipt)
        self._current = updated
        self._reconciliation_outcome_ref = receipt.outcome_ref
        return updated, receipt

    @workflow.run
    async def run(self, data: CollectionV2WorkflowInput) -> CollectionV2WorkflowResult:
        self._current = data
        while data.cursor < data.end_slot_ordinal_exclusive:
            await workflow.wait_condition(lambda: not self._paused or self._cancel_requested)
            if self._cancel_requested:
                data, reconciliation = await self._reconcile(data)
                if reconciliation.state == "pending":
                    return self._result(data, "reconciliation_pending")
                return self._result(data, "cancelled")

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

            if receipt.requires_reconciliation or self._cancel_requested:
                data, reconciliation = await self._reconcile(data)
                if reconciliation.state == "pending":
                    return self._result(data, "reconciliation_pending")
                if self._cancel_requested:
                    return self._result(data, "cancelled")

            if data.cursor >= data.end_slot_ordinal_exclusive:
                return self._result(data, "completed")
            if self._paused:
                continue
            if self._pages_in_generation >= data.continue_as_new_after_pages:
                workflow.continue_as_new(data.next_generation())

        return self._result(data, "completed")


__all__ = [
    "COLLECTION_V2_OUTBOX_TYPE",
    "COLLECTION_V2_PAYLOAD_SCHEMA",
    "COLLECTION_V2_RESULT_SCHEMA",
    "COLLECTION_V2_TASK_QUEUE",
    "COLLECTION_V2_WORKFLOW_TYPE",
    "MAX_COLLECTION_V2_PAGES_PER_RUN",
    "MAX_COLLECTION_V2_WORKFLOW_PAYLOAD_BYTES",
    "CollectionV2WorkflowInput",
    "CollectionV2WorkflowResult",
    "CollectionV2WorkflowStatus",
    "GeoCollectionV2Workflow",
    "build_collection_v2_workflow_input",
]
