"""Public-web acquisition worker; never receives logged-in collection tasks."""

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

from workflows.activities.own_site_snapshot import capture_own_site_snapshots
from workflows.activities.post_analysis import fetch_post_snapshot
from workflows.activities.source_fetch import fetch_run_sources

SOURCE_ACTIVITIES = (
    capture_own_site_snapshots,
    fetch_post_snapshot,
    fetch_run_sources,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-source-worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    structlog.get_logger().info(
        "temporal_source_worker_connected",
        address=settings.temporal_address,
        task_queue=settings.source_temporal_task_queue,
    )
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="geo-public-web") as executor:
        worker = Worker(
            client,
            task_queue=settings.source_temporal_task_queue,
            activities=list(SOURCE_ACTIVITIES),
            activity_executor=executor,
            max_concurrent_activities=4,
        )
        await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
