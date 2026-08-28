"""Temporal workflows for deterministic V2 evaluation, snapshots, and replay."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.metrics_v2 import (
        claim_recompute_job_activity,
        evaluate_answer_metric_activity,
        evaluate_metric_answers_activity,
        finish_recompute_job_activity,
        load_metric_snapshot_inputs_activity,
        load_metrics_backfill_batch_activity,
        persist_metric_evaluations_activity,
        persist_metric_snapshot_set_activity,
        publish_snapshot_set_activity,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


def _metrics_queue(payload: dict[str, Any]) -> str:
    return str(payload.get("metrics_task_queue") or "geo-platform-v2-metrics")


@workflow.defn(name="AnswerMetricEvaluationWorkflowV2")
class AnswerMetricEvaluationWorkflowV2:
    """Create reusable deterministic evaluation rows from frozen semantics."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        queue = _metrics_queue(payload)
        result: dict[str, Any] = await workflow.execute_activity(
            evaluate_answer_metric_activity,
            payload,
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )
        persisted: dict[str, Any] = await workflow.execute_activity(
            persist_metric_evaluations_activity,
            {
                "tenant_pub_id": payload["tenant_pub_id"],
                "project_pub_id": payload["project_pub_id"],
                **result,
            },
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )
        return {**persisted, **result}


@workflow.defn(name="MetricSnapshotSetWorkflowV2")
class MetricSnapshotSetWorkflowV2:
    """Freeze, calculate, atomically persist, and optionally publish one set."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        queue = _metrics_queue(payload)
        job_pub_id = payload.get("job_pub_id")
        if job_pub_id:
            await workflow.execute_activity(
                claim_recompute_job_activity,
                {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "job_pub_id": job_pub_id,
                    "workflow_id": workflow.info().workflow_id,
                    "run_id": workflow.info().run_id,
                },
                task_queue=queue,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
        try:
            frozen: dict[str, Any] = await workflow.execute_activity(
                load_metric_snapshot_inputs_activity,
                payload,
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            result: dict[str, Any] = await workflow.execute_activity(
                evaluate_metric_answers_activity,
                payload | frozen,
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_RETRY,
            )
            persisted: dict[str, Any] = await workflow.execute_activity(
                persist_metric_snapshot_set_activity,
                {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "result": result,
                },
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            publication = None
            if payload.get("publication_channel"):
                publication = await workflow.execute_activity(
                    publish_snapshot_set_activity,
                    {
                        "tenant_pub_id": payload["tenant_pub_id"],
                        "snapshot_set_pub_id": persisted["snapshot_set_pub_id"],
                        "snapshot_set_hash": persisted["snapshot_set_hash"],
                        "publication_channel": payload["publication_channel"],
                        "expected_generation": int(payload.get("expected_generation") or 0),
                        "published_by": payload["published_by"],
                    },
                    task_queue=queue,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY,
                )
            if job_pub_id:
                await workflow.execute_activity(
                    finish_recompute_job_activity,
                    {
                        "tenant_pub_id": payload["tenant_pub_id"],
                        "job_pub_id": job_pub_id,
                        "status": "succeeded",
                        "snapshot_set_pub_id": persisted["snapshot_set_pub_id"],
                        "input_count": len(frozen.get("subjects", [])),
                        "output_count": int(result["snapshot_count"]),
                    },
                    task_queue=queue,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY,
                )
            return {**persisted, "publication": publication}
        except Exception:
            if job_pub_id:
                await workflow.execute_activity(
                    finish_recompute_job_activity,
                    {
                        "tenant_pub_id": payload["tenant_pub_id"],
                        "job_pub_id": job_pub_id,
                        "status": "failed",
                        "failure_codes": ["snapshot_build_failed"],
                    },
                    task_queue=queue,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY,
                )
            raise


@workflow.defn(name="ProjectMetricsRefreshWorkflowV2")
class ProjectMetricsRefreshWorkflowV2:
    """Coalesce explicit dirty scopes without time-estimate sleeps."""

    def __init__(self) -> None:
        self._pending_scopes: list[dict[str, Any]] = []

    @workflow.signal
    async def mark_dirty(self, scope: dict[str, Any]) -> None:
        self._pending_scopes.append(scope)

    @workflow.query
    def pending_count(self) -> int:
        return len(self._pending_scopes)

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        queue = _metrics_queue(payload)
        self._pending_scopes.append(dict(payload["scope_request"]))
        outputs: list[dict[str, Any]] = []
        while self._pending_scopes:
            current = self._pending_scopes
            self._pending_scopes = []
            # Keep the last request for each immutable scope hash; duplicate
            # events still converge at repository uniqueness/advisory locking.
            by_scope = {str(item["scope_hash"]): item for item in current}
            for scope_hash in sorted(by_scope):
                item = by_scope[scope_hash]
                result = await workflow.execute_child_workflow(
                    MetricSnapshotSetWorkflowV2.run,
                    item | {"metrics_task_queue": queue},
                    id=f"{workflow.info().workflow_id}:scope:{scope_hash[:20]}",
                    task_queue=queue,
                )
                outputs.append(result)
        return {"refreshed": len(outputs), "snapshot_sets": outputs}


@workflow.defn(name="MetricsBackfillWorkflowV2")
class MetricsBackfillWorkflowV2:
    """Replay one bounded stable-keyset page; missing decisions remain unknown."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        queue = _metrics_queue(payload)
        batch: dict[str, Any] = await workflow.execute_activity(
            load_metrics_backfill_batch_activity,
            payload,
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )
        if bool(payload.get("dry_run", False)):
            return batch
        evaluations: dict[str, Any] = await workflow.execute_activity(
            evaluate_answer_metric_activity,
            payload | batch,
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )
        persisted: dict[str, Any] = await workflow.execute_activity(
            persist_metric_evaluations_activity,
            {
                "tenant_pub_id": payload["tenant_pub_id"],
                "project_pub_id": payload["project_pub_id"],
                **evaluations,
            },
            task_queue=queue,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )
        return {
            **persisted,
            "processed_count": len(batch.get("subjects", [])),
            "next_cursor": batch.get("next_cursor"),
            "done": batch.get("next_cursor") is None,
            "batch_hash": batch.get("batch_hash"),
            "unknown_count": batch.get("unknown_count", 0),
        }
