from __future__ import annotations

import asyncio

from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from domain.reporting.libreoffice import report_runtime_preflight
from workflows.activities.report_v2 import (
    validate_formal_metric_snapshot_binding_activity,
)
from workflows.activities.s02 import (
    fail_formal_report_activity,
    finalize_formal_report_activity,
    finalize_report_activity,
    freeze_report_activity,
    preflight_formal_report_runtime_activity,
    produce_formal_report_activity,
    produce_report_activity,
)
from workflows.definitions.report_v2 import FormalSnapshotReportWorkflowV2
from workflows.definitions.s02 import ReportProductionWorkflow

REPORT_WORKFLOWS = (
    ReportProductionWorkflow,
    FormalSnapshotReportWorkflowV2,
)
REPORT_ACTIVITIES = (
    freeze_report_activity,
    produce_report_activity,
    finalize_report_activity,
    preflight_formal_report_runtime_activity,
    validate_formal_metric_snapshot_binding_activity,
    produce_formal_report_activity,
    fail_formal_report_activity,
    finalize_formal_report_activity,
)


async def run_report_worker(
    *,
    address: str = "127.0.0.1:17233",
    namespace: str = "default",
    task_queue: str = "geo-platform-v2-report",
) -> None:
    await asyncio.to_thread(report_runtime_preflight)
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-report-worker")
    client = await Client.connect(
        address,
        namespace=namespace,
        interceptors=[TracingInterceptor()],
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=list(REPORT_WORKFLOWS),
        activities=list(REPORT_ACTIVITIES),
        max_concurrent_activities=2,
    )
    await worker.run()


if __name__ == "__main__":
    settings = get_settings()
    try:
        asyncio.run(
            run_report_worker(
                address=settings.temporal_address,
                namespace=settings.temporal_namespace,
                task_queue=settings.report_temporal_task_queue,
            )
        )
    except KeyboardInterrupt:
        pass


__all__ = ["REPORT_ACTIVITIES", "REPORT_WORKFLOWS", "run_report_worker"]
