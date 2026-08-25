"""Physically isolated worker for the new collection v2 Temporal contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from workflows.activities.collection_v2 import (
    execute_collection_v2_page,
    reconcile_collection_v2_partition,
    verify_collection_v2_partition_complete,
)
from workflows.definitions.collection_v2 import (
    COLLECTION_V2_TASK_QUEUE,
    GeoCollectionV2Workflow,
)

COLLECTION_V2_WORKFLOWS = (GeoCollectionV2Workflow,)
COLLECTION_V2_ACTIVITIES: tuple[Callable[..., Any], ...] = (
    execute_collection_v2_page,
    reconcile_collection_v2_partition,
    verify_collection_v2_partition_complete,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-collection-v2-worker")
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    structlog.get_logger().info(
        "temporal_collection_v2_worker_connected",
        address=settings.temporal_address,
        task_queue=COLLECTION_V2_TASK_QUEUE,
    )
    worker = Worker(
        client,
        task_queue=COLLECTION_V2_TASK_QUEUE,
        workflows=list(COLLECTION_V2_WORKFLOWS),
        activities=list(COLLECTION_V2_ACTIVITIES),
        max_concurrent_activities=8,
    )
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


__all__ = [
    "COLLECTION_V2_ACTIVITIES",
    "COLLECTION_V2_TASK_QUEUE",
    "COLLECTION_V2_WORKFLOWS",
    "run_worker",
]
