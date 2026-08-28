from __future__ import annotations

import asyncio

from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from workflows.activities.s02 import (
    capture_evidence_activity,
    persist_investigation_verdict_activity,
    prepare_evidence_activity,
    score_investigation_activity,
)
from workflows.definitions.s02 import (
    AntiGeoInvestigationWorkflow,
    EvidenceCaptureWorkflow,
)

S02_WORKFLOWS = (
    EvidenceCaptureWorkflow,
    AntiGeoInvestigationWorkflow,
)
S02_ACTIVITIES = (
    prepare_evidence_activity,
    capture_evidence_activity,
    score_investigation_activity,
    persist_investigation_verdict_activity,
)


async def run_s02_worker(
    *,
    address: str = "127.0.0.1:17233",
    namespace: str = "default",
    task_queue: str = "geo-platform-v2-s02",
) -> None:
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
