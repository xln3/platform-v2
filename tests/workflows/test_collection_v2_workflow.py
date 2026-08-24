from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from dataclasses import asdict, fields

import pytest
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from workflows.activities.collection_v2 import (
    COLLECTION_V2_PAGE_RECEIPT_SCHEMA,
    COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA,
    MAX_COLLECTION_V2_PAGE_SIZE,
    CollectionV2ContractError,
    CollectionV2PageReceipt,
    CollectionV2PageRequest,
    CollectionV2ReconciliationReceipt,
    CollectionV2ReconciliationRequest,
)
from workflows.definitions.collection_v2 import (
    COLLECTION_V2_PAYLOAD_SCHEMA,
    CollectionV2WorkflowInput,
    CollectionV2WorkflowResult,
    GeoCollectionV2Workflow,
    build_collection_v2_workflow_input,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _workflow_input(
    *,
    end_slot_ordinal_exclusive: int,
    page_size: int = 2,
    continue_as_new_after_pages: int = 25,
) -> CollectionV2WorkflowInput:
    return CollectionV2WorkflowInput(
        schema_version=COLLECTION_V2_PAYLOAD_SCHEMA,
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        config_revision_pub_id="cfr_test",
        config_revision_hash=_digest("config"),
        campaign_pub_id="cmp_test",
        specification_hash=_digest("specification"),
        partition_pub_id="partition-test",
        partition_digest=_digest("partition"),
        membership_digest_version="collection-membership-chain-v1",
        membership_digest=_digest("membership"),
        canonical_enumeration_version="collection-enumeration-v1",
        slot_generator_version="collection-slot-generator-v1",
        start_slot_ordinal=0,
        end_slot_ordinal_exclusive=end_slot_ordinal_exclusive,
        cursor=0,
        page_size=page_size,
        checkpoint_ref="checkpoint-0",
        checkpoint_digest=_digest("checkpoint-0"),
        reconciliation_checkpoint_ref="reconciliation-0",
        capability_policy_revision="capability-policy-v1",
        control_policy_revision="control-policy-v1",
        comparison_policy_revision="comparison-policy-v1",
        scheduling_window_start_utc="2026-08-24T00:00:00Z",
        scheduling_window_end_utc="2026-08-25T00:00:00Z",
        idempotency_key="campaign-partition-test",
        continue_as_new_after_pages=continue_as_new_after_pages,
    )


class _ActivityHarness:
    def __init__(
        self,
        *,
        end_cursor: int,
        block_pages: bool = False,
        reconciliation_state: str = "settled",
        resolved_targets_by_cursor: dict[int, tuple[str, ...]] | None = None,
    ) -> None:
        self.end_cursor = end_cursor
        self.block_pages = block_pages
        self.reconciliation_state = reconciliation_state
        self.resolved_targets_by_cursor = resolved_targets_by_cursor or {}
        self.resolved_target_pages: list[tuple[str, ...]] = []
        self.page_requests: list[CollectionV2PageRequest] = []
        self.reconciliation_requests: list[CollectionV2ReconciliationRequest] = []
        self.started: asyncio.Queue[CollectionV2PageRequest] = asyncio.Queue()
        self.releases: asyncio.Queue[None] = asyncio.Queue()

    @activity.defn(name="execute_collection_v2_page")
    async def execute_page(self, request: CollectionV2PageRequest) -> CollectionV2PageReceipt:
        self.page_requests.append(request)
        await self.started.put(request)
        if self.block_pages:
            await self.releases.get()
        next_cursor = min(request.cursor + request.page_size, self.end_cursor)
        resolved_targets = self.resolved_targets_by_cursor.get(request.cursor)
        page_digest_seed = f"page:{request.cursor}:{next_cursor}"
        if resolved_targets is not None:
            assert len(resolved_targets) == next_cursor - request.cursor
            self.resolved_target_pages.append(resolved_targets)
            page_digest_seed += ":" + ":".join(resolved_targets)
        return CollectionV2PageReceipt(
            schema_version=COLLECTION_V2_PAGE_RECEIPT_SCHEMA,
            campaign_pub_id=request.campaign_pub_id,
            partition_pub_id=request.partition_pub_id,
            partition_digest=request.partition_digest,
            prior_cursor=request.cursor,
            next_cursor=next_cursor,
            page_item_count=next_cursor - request.cursor,
            page_digest=_digest(page_digest_seed),
            checkpoint_ref=f"checkpoint-{next_cursor}",
            checkpoint_digest=_digest(f"checkpoint-{next_cursor}"),
            reconciliation_checkpoint_ref=f"reconciliation-{next_cursor}",
        )

    @activity.defn(name="reconcile_collection_v2_partition")
    async def reconcile(
        self,
        request: CollectionV2ReconciliationRequest,
    ) -> CollectionV2ReconciliationReceipt:
        self.reconciliation_requests.append(request)
        state = self.reconciliation_state
        assert state in {"settled", "pending"}
        return CollectionV2ReconciliationReceipt(
            schema_version=COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA,
            campaign_pub_id=request.campaign_pub_id,
            partition_pub_id=request.partition_pub_id,
            partition_digest=request.partition_digest,
            cursor=request.cursor,
            checkpoint_ref=request.checkpoint_ref,
            checkpoint_digest=request.checkpoint_digest,
            reconciliation_checkpoint_ref=request.reconciliation_checkpoint_ref,
            state=state,  # type: ignore[arg-type]
            outcome_ref="reconciliation-settled" if state == "settled" else None,
        )


async def _wait_for_status(
    handle: object,
    *,
    cursor: int,
    paused: bool,
) -> None:
    for _ in range(100):
        status = await handle.query(GeoCollectionV2Workflow.status)  # type: ignore[attr-defined]
        if status.cursor == cursor and status.paused is paused:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("workflow status did not converge")


def test_workflow_payload_is_scalar_and_constant_size_for_one_or_279k_slots() -> None:
    small = _workflow_input(end_slot_ordinal_exclusive=1)
    large = _workflow_input(end_slot_ordinal_exclusive=279_000)

    small_payload = small.payload_json
    large_payload = large.payload_json
    assert abs(len(large_payload.encode()) - len(small_payload.encode())) < 16
    assert len(large_payload.encode()) < 8_192
    assert "tasks" not in large_payload
    assert "slots" not in large_payload
    assert "question" not in large_payload
    assert "target_pub_id" not in large_payload
    assert all(
        value is None or isinstance(value, str | int | bool)
        for value in json.loads(large_payload).values()
    )

    with pytest.raises(CollectionV2ContractError, match="unsupported_schema"):
        _workflow_input(end_slot_ordinal_exclusive=1).__class__(
            **(asdict(small) | {"schema_version": "collection-workflow-v1"})
        )

    with pytest.raises(TypeError, match="target_pub_id"):
        CollectionV2WorkflowInput(**(asdict(small) | {"target_pub_id": "cgt_fallback"}))

    for contract in (
        CollectionV2WorkflowInput,
        CollectionV2WorkflowResult,
        CollectionV2PageRequest,
        CollectionV2PageReceipt,
        CollectionV2ReconciliationRequest,
        CollectionV2ReconciliationReceipt,
    ):
        assert "target_pub_id" not in {field.name for field in fields(contract)}


def test_scheduler_builder_requires_and_exactly_maps_frozen_campaign_reference() -> None:
    from geo_platform.collection.identity_v2 import CampaignWorkflowReference

    reference = CampaignWorkflowReference(
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        campaign_pub_id="cmp_test",
        config_revision_pub_id="cfr_test",
        config_revision_hash=_digest("config"),
        specification_hash=_digest("specification"),
        membership_hash=_digest("membership"),
        partition_pub_id="partition-test",
        start_slot_ordinal=10,
        end_slot_ordinal_exclusive=20,
        cursor=12,
        page_size=4,
    )
    payload = build_collection_v2_workflow_input(
        reference,
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        partition_digest=_digest("partition"),
        canonical_enumeration_version="collection-enumeration-v1",
        checkpoint_ref="checkpoint-12",
        checkpoint_digest=_digest("checkpoint-12"),
        reconciliation_checkpoint_ref="reconciliation-12",
        capability_policy_revision="capability-policy-v1",
        control_policy_revision="control-policy-v1",
        comparison_policy_revision="comparison-policy-v1",
        scheduling_window_start_utc="2026-08-24T00:00:00Z",
        scheduling_window_end_utc="2026-08-25T00:00:00Z",
        idempotency_key="campaign-partition-test",
    )

    assert payload.schema_version == "collection-workflow-v2"
    assert payload.campaign_pub_id == reference.campaign_pub_id
    assert payload.partition_pub_id == reference.partition_pub_id
    assert payload.membership_digest == reference.membership_hash
    assert payload.start_slot_ordinal == 10
    assert payload.end_slot_ordinal_exclusive == 20
    assert payload.cursor == 12
    assert payload.page_size == 4
    assert "target_pub_id" not in inspect.signature(build_collection_v2_workflow_input).parameters

    with pytest.raises(
        CollectionV2ContractError,
        match="persisted_campaign_workflow_reference_required",
    ):
        build_collection_v2_workflow_input(
            object(),  # type: ignore[arg-type]
            tenant_pub_id="tnt_test",
            project_pub_id="prj_test",
            partition_digest=_digest("partition"),
            canonical_enumeration_version="collection-enumeration-v1",
            checkpoint_ref="checkpoint-12",
            checkpoint_digest=_digest("checkpoint-12"),
            reconciliation_checkpoint_ref="reconciliation-12",
            capability_policy_revision="capability-policy-v1",
            control_policy_revision="control-policy-v1",
            comparison_policy_revision="comparison-policy-v1",
            scheduling_window_start_utc="2026-08-24T00:00:00Z",
            scheduling_window_end_utc="2026-08-25T00:00:00Z",
            idempotency_key="campaign-partition-test",
        )


def test_workflow_page_size_matches_stage1_scheduler_contract() -> None:
    from geo_platform.collection.identity_v2 import MAX_CAMPAIGN_EXECUTION_PAGE_SIZE

    assert MAX_COLLECTION_V2_PAGE_SIZE == MAX_CAMPAIGN_EXECUTION_PAGE_SIZE == 2_048
    assert (
        _workflow_input(
            end_slot_ordinal_exclusive=2_048,
            page_size=2_048,
        ).page_size
        == 2_048
    )
    with pytest.raises(CollectionV2ContractError, match="integer_out_of_range:page_size"):
        _workflow_input(end_slot_ordinal_exclusive=2_049, page_size=2_049)


async def test_v2_pages_by_reference_and_continue_as_new_preserves_identity() -> None:
    harness = _ActivityHarness(end_cursor=5)
    task_queue = f"collection-v2-can-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[GeoCollectionV2Workflow],
            activities=[harness.execute_page, harness.reconcile],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionV2Workflow.run,
                _workflow_input(
                    end_slot_ordinal_exclusive=5,
                    page_size=2,
                    continue_as_new_after_pages=1,
                ),
                id=f"collection-v2/can/{uuid.uuid4().hex}",
                task_queue=task_queue,
            )

    assert result.state == "completed"
    assert result.cursor == 5
    assert result.processed_slot_count == 5
    assert result.generation == 3
    assert [request.cursor for request in harness.page_requests] == [0, 2, 4]
    assert {request.partition_pub_id for request in harness.page_requests} == {"partition-test"}
    assert {request.partition_digest for request in harness.page_requests} == {_digest("partition")}
    for request in harness.page_requests:
        assert all(
            value is None or isinstance(value, str | int | bool)
            for value in asdict(request).values()
        )
        assert "query" not in request.payload_json
        assert "slots" not in request.payload_json
        assert "target_pub_id" not in request.payload_json


async def test_campaign_partition_page_backend_resolves_cross_target_slots() -> None:
    harness = _ActivityHarness(
        end_cursor=2,
        resolved_targets_by_cursor={0: ("cgt_consumer_web", "cgt_provider_api")},
    )
    task_queue = f"collection-v2-cross-target-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[GeoCollectionV2Workflow],
            activities=[harness.execute_page, harness.reconcile],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionV2Workflow.run,
                _workflow_input(end_slot_ordinal_exclusive=2, page_size=2),
                id=f"collection-v2/cross-target/{uuid.uuid4().hex}",
                task_queue=task_queue,
            )

    assert result.state == "completed"
    assert result.cursor == 2
    assert harness.resolved_target_pages == [("cgt_consumer_web", "cgt_provider_api")]
    assert "target_pub_id" not in asdict(harness.page_requests[0])
    assert "target_pub_id" not in asdict(result)


async def test_pause_resume_cancel_stops_new_pages_and_reconciles_latest_checkpoint() -> None:
    harness = _ActivityHarness(end_cursor=10, block_pages=True)
    task_queue = f"collection-v2-signal-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[GeoCollectionV2Workflow],
            activities=[harness.execute_page, harness.reconcile],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionV2Workflow.run,
                _workflow_input(end_slot_ordinal_exclusive=10, page_size=2),
                id=f"collection-v2/signal/{uuid.uuid4().hex}",
                task_queue=task_queue,
            )
            first = await asyncio.wait_for(harness.started.get(), timeout=5)
            assert first.cursor == 0
            await handle.signal(GeoCollectionV2Workflow.pause)
            harness.releases.put_nowait(None)
            await _wait_for_status(handle, cursor=2, paused=True)
            assert len(harness.page_requests) == 1

            await handle.signal(GeoCollectionV2Workflow.resume)
            second = await asyncio.wait_for(harness.started.get(), timeout=5)
            assert second.cursor == 2
            await handle.signal(GeoCollectionV2Workflow.cancel)
            harness.releases.put_nowait(None)
            result = await handle.result()

    assert result.state == "cancelled"
    assert result.cursor == 4
    assert len(harness.page_requests) == 2
    assert len(harness.reconciliation_requests) == 1
    reconciliation = harness.reconciliation_requests[0]
    assert reconciliation.cursor == 4
    assert reconciliation.checkpoint_ref == "checkpoint-4"
    assert reconciliation.reconciliation_checkpoint_ref == "reconciliation-4"
    assert result.reconciliation_outcome_ref == "reconciliation-settled"


async def test_fresh_v2_history_replays_deterministically() -> None:
    harness = _ActivityHarness(end_cursor=1)
    task_queue = f"collection-v2-replay-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[GeoCollectionV2Workflow],
            activities=[harness.execute_page, harness.reconcile],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionV2Workflow.run,
                _workflow_input(end_slot_ordinal_exclusive=1, page_size=1),
                id=f"collection-v2/replay/{uuid.uuid4().hex}",
                task_queue=task_queue,
            )
            assert (await handle.result()).state == "completed"
            history: WorkflowHistory = await handle.fetch_history()

    await Replayer(workflows=[GeoCollectionV2Workflow]).replay_workflow(history)
