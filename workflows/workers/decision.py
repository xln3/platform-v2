"""Dedicated V2 semantic-decision worker.

This process is the only V2 worker permitted to execute a judge adapter.  It
does not calculate aggregate metrics or render reports.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from workflows.activities.semantic_decisions_v2 import (
    adjudicate_decision_activity,
    commit_semantic_decision_backfill_cursor_activity,
    create_decision_request_activity,
    derive_events_activity,
    persist_decision_activity,
    persist_events_activity,
    run_model_judge_activity,
    validate_decision_output_activity,
)
from workflows.definitions.semantic_decisions_v2 import (
    AnswerSemanticEventWorkflowV2,
    SemanticDecisionBackfillWorkflowV2,
    SemanticDecisionWorkflowV2,
)

DECISION_WORKFLOWS = (
    SemanticDecisionWorkflowV2,
    AnswerSemanticEventWorkflowV2,
    SemanticDecisionBackfillWorkflowV2,
)
DECISION_ACTIVITIES = (
    create_decision_request_activity,
    run_model_judge_activity,
    validate_decision_output_activity,
    adjudicate_decision_activity,
    persist_decision_activity,
    derive_events_activity,
    persist_events_activity,
    commit_semantic_decision_backfill_cursor_activity,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-decision-worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    structlog.get_logger().info(
        "temporal_decision_worker_connected",
        address=settings.temporal_address,
        task_queue=settings.decision_temporal_task_queue,
        max_concurrent_activities=settings.semantic_decision_max_concurrent_activities,
        judge_policy_version=settings.semantic_decision_judge_policy_version or None,
    )
    with ThreadPoolExecutor(
        max_workers=settings.semantic_decision_max_concurrent_activities,
        thread_name_prefix="geo-decision-activity",
    ) as executor:
        worker = Worker(
            client,
            task_queue=settings.decision_temporal_task_queue,
            workflows=list(DECISION_WORKFLOWS),
            activities=list(DECISION_ACTIVITIES),
            activity_executor=executor,
            max_concurrent_activities=settings.semantic_decision_max_concurrent_activities,
        )
        await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
