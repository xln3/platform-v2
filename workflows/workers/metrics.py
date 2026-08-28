"""Dedicated deterministic V2 metrics worker (intentionally model-free)."""

from __future__ import annotations

import asyncio

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

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
from workflows.definitions.metrics_v2 import (
    AnswerMetricEvaluationWorkflowV2,
    MetricsBackfillWorkflowV2,
    MetricSnapshotSetWorkflowV2,
    ProjectMetricsRefreshWorkflowV2,
)

METRICS_WORKFLOWS = (
    AnswerMetricEvaluationWorkflowV2,
    MetricSnapshotSetWorkflowV2,
    ProjectMetricsRefreshWorkflowV2,
    MetricsBackfillWorkflowV2,
)
METRICS_ACTIVITIES = (
    claim_recompute_job_activity,
    load_metric_snapshot_inputs_activity,
    evaluate_answer_metric_activity,
    evaluate_metric_answers_activity,
    persist_metric_evaluations_activity,
    persist_metric_snapshot_set_activity,
    publish_snapshot_set_activity,
    finish_recompute_job_activity,
    load_metrics_backfill_batch_activity,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-metrics-worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    structlog.get_logger().info(
        "temporal_metrics_worker_connected",
        address=settings.temporal_address,
        task_queue=settings.metrics_temporal_task_queue,
        max_concurrent_activities=settings.metrics_max_concurrent_activities,
        max_concurrent_snapshot_activities=settings.metrics_snapshot_max_concurrent_activities,
    )
    worker = Worker(
        client,
        task_queue=settings.metrics_temporal_task_queue,
        workflows=list(METRICS_WORKFLOWS),
        activities=list(METRICS_ACTIVITIES),
        max_concurrent_activities=settings.metrics_max_concurrent_activities,
    )
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
