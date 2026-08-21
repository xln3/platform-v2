"""Semantic, source-audit, risk and post-analysis worker."""

from __future__ import annotations

import asyncio

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from workflows.activities.analysis_jobs import mark_analysis_job
from workflows.activities.content_contribution import analyze_content_contribution
from workflows.activities.content_strategy import analyze_content_strategy
from workflows.activities.disparagement import judge_run_disparagement
from workflows.activities.disparagement_factcheck import factcheck_disparagement_cases
from workflows.activities.own_content_disparagement import judge_own_content_disparagement
from workflows.activities.page_inspection import inspect_run_source_pages
from workflows.activities.post_analysis import (
    analyze_post_content,
    annotate_post_snapshot,
    begin_post_analysis_task,
    finalize_post_analysis_task,
)
from workflows.activities.s02 import analyze_answer_activity, extract_brands_activity
from workflows.activities.site_suggestions import generate_site_audit_suggestions
from workflows.activities.source_audit import audit_run_sources
from workflows.definitions.own_content import OwnContentDisparagementWorkflow
from workflows.definitions.page_inspection import PageInspectionWorkflow
from workflows.definitions.post_analysis import PostAnalysisWorkflow
from workflows.definitions.post_collection_analysis import PostCollectionAnalysisWorkflow
from workflows.definitions.s02 import AnswerAnalysisWorkflow

ANALYSIS_WORKFLOWS = (
    AnswerAnalysisWorkflow,
    PageInspectionWorkflow,
    PostCollectionAnalysisWorkflow,
    PostAnalysisWorkflow,
    OwnContentDisparagementWorkflow,
)
ANALYSIS_ACTIVITIES = (
    mark_analysis_job,
    analyze_answer_activity,
    extract_brands_activity,
    audit_run_sources,
    analyze_content_contribution,
    analyze_content_strategy,
    inspect_run_source_pages,
    generate_site_audit_suggestions,
    judge_run_disparagement,
    factcheck_disparagement_cases,
    analyze_post_content,
    annotate_post_snapshot,
    begin_post_analysis_task,
    finalize_post_analysis_task,
    judge_own_content_disparagement,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-analysis-worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    structlog.get_logger().info(
        "temporal_analysis_worker_connected",
        address=settings.temporal_address,
        task_queue=settings.analysis_temporal_task_queue,
    )
    worker = Worker(
        client,
        task_queue=settings.analysis_temporal_task_queue,
        workflows=list(ANALYSIS_WORKFLOWS),
        activities=list(ANALYSIS_ACTIVITIES),
        max_concurrent_activities=8,
    )
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
