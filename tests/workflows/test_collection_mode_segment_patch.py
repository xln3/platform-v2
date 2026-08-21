"""Replay guard for adapter-batch-mode-segments-v3.

The already-published v2 history contains only ``adapter-batch-collect-v2`` and
groups adjacent tasks by adapter+region.  Current code must see the absent v3
marker as false during replay, while fresh executions record v3 and additionally
isolate mode-scoped quota failures.
"""

from __future__ import annotations

import uuid

from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from workflows.activities.collection import CollectionTaskInput
from workflows.definitions.collection import (
    ADAPTER_BATCH_MODE_SEGMENTS_PATCH,
    plan_batch_segments,
    plan_versioned_batch_segments,
)

_WORKFLOW_NAME = "CollectionSegmentPatchReplayWorkflow"


def _render_segments(
    segments: list[tuple[str, list[CollectionTaskInput]]],
) -> list[list[str]]:
    return [[item.business_key for item in items] for _slug, items in segments]


@workflow.defn(name=_WORKFLOW_NAME)
class V2CollectionSegmentWorkflow:
    """Clone of the published v2 marker order and grouping semantics."""

    @workflow.run
    async def run(self, tasks: list[CollectionTaskInput]) -> list[list[str]]:
        patched_v2 = workflow.patched("adapter-batch-collect-v2")
        return _render_segments(plan_batch_segments(patched_v2, tasks))


@workflow.defn(name=_WORKFLOW_NAME)
class CurrentCollectionSegmentWorkflow:
    """Current marker order: the published v2 marker, then the new v3 marker."""

    @workflow.run
    async def run(self, tasks: list[CollectionTaskInput]) -> list[list[str]]:
        patched_v2 = workflow.patched("adapter-batch-collect-v2")
        patched_mode_v3 = workflow.patched(ADAPTER_BATCH_MODE_SEGMENTS_PATCH)
        return _render_segments(plan_versioned_batch_segments(patched_v2, patched_mode_v3, tasks))


def _mixed_mode_tasks() -> list[CollectionTaskInput]:
    return [
        CollectionTaskInput(
            business_key="normal",
            query="normal query",
            model="doubao",
            region="CN-BJ",
            mode="normal",
            adapter="doubao",
        ),
        CollectionTaskInput(
            business_key="deep",
            query="deep query",
            model="doubao",
            region="CN-BJ",
            mode="deep_think",
            adapter="doubao",
        ),
    ]


async def test_v2_history_replays_and_fresh_v3_execution_splits_modes() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        legacy_queue = f"collection-segment-v2-{uuid.uuid4().hex}"
        async with Worker(
            environment.client,
            task_queue=legacy_queue,
            workflows=[V2CollectionSegmentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await environment.client.start_workflow(
                V2CollectionSegmentWorkflow.run,
                _mixed_mode_tasks(),
                id=f"collection-segment/v2/{uuid.uuid4().hex}",
                task_queue=legacy_queue,
            )
            v2_result = await handle.result()
            v2_history: WorkflowHistory = await handle.fetch_history()

        assert v2_result == [["normal", "deep"]]

        # The v3 marker is absent from this history, so current code must retain
        # the exact v2 grouping and completion payload during replay.
        await Replayer(
            workflows=[CurrentCollectionSegmentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(v2_history)

        current_queue = f"collection-segment-v3-{uuid.uuid4().hex}"
        async with Worker(
            environment.client,
            task_queue=current_queue,
            workflows=[CurrentCollectionSegmentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            v3_result = await environment.client.execute_workflow(
                CurrentCollectionSegmentWorkflow.run,
                _mixed_mode_tasks(),
                id=f"collection-segment/v3/{uuid.uuid4().hex}",
                task_queue=current_queue,
            )

    assert v3_result == [["normal"], ["deep"]]
