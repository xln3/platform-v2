from __future__ import annotations

import asyncio

from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from domain.reporting.libreoffice import report_runtime_preflight
from workflows.activities.analysis_jobs import mark_analysis_job
from workflows.activities.s02 import (
    analyze_answer_activity,
    capture_evidence_activity,
    extract_brands_activity,
    fail_formal_report_activity,
    finalize_formal_report_activity,
    finalize_report_activity,
    freeze_report_activity,
    persist_investigation_verdict_activity,
    preflight_formal_report_runtime_activity,
    prepare_evidence_activity,
    produce_formal_report_activity,
    produce_report_activity,
    score_investigation_activity,
)
from workflows.definitions.s02 import (
    AnswerAnalysisWorkflow,
    AntiGeoInvestigationWorkflow,
    EvidenceCaptureWorkflow,
    ReportProductionWorkflow,
)

S02_WORKFLOWS = (
    AnswerAnalysisWorkflow,
    EvidenceCaptureWorkflow,
    ReportProductionWorkflow,
    AntiGeoInvestigationWorkflow,
)
S02_ACTIVITIES = (
    mark_analysis_job,
    analyze_answer_activity,
    extract_brands_activity,
    prepare_evidence_activity,
    capture_evidence_activity,
    freeze_report_activity,
    produce_report_activity,
    finalize_report_activity,
    preflight_formal_report_runtime_activity,
    produce_formal_report_activity,
    fail_formal_report_activity,
    finalize_formal_report_activity,
    score_investigation_activity,
    persist_investigation_verdict_activity,
)


async def run_s02_worker(
    *,
    address: str = "127.0.0.1:17233",
    namespace: str = "default",
    task_queue: str = "geo-platform-v2-s02",
) -> None:
    # Fail the execution node before it accepts formal-report tasks.  The workflow
    # repeats this check as an activity so dependency drift is also caught per run.
    await asyncio.to_thread(report_runtime_preflight)
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-s02-worker")
    client = await Client.connect(
        address,
        namespace=namespace,
        interceptors=[TracingInterceptor()],
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
        # Formal report activities launch LibreOffice and can consume substantial
        # memory.  Keep a hard process-level ceiling so report bursts cannot starve
        # answer analysis and evidence work that shares this queue.
        max_concurrent_activities=2,
    )
    await worker.run()


if __name__ == "__main__":
    settings = get_settings()
    try:
        asyncio.run(
            run_s02_worker(
                address=settings.temporal_address,
                namespace=settings.temporal_namespace,
                task_queue=settings.s02_temporal_task_queue,
            )
        )
    except KeyboardInterrupt:
        pass
