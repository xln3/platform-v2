from __future__ import annotations

import asyncio
import signal

import structlog
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import ANALYTICS_EVENT_TYPES, OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.collection.workflow_outbox import WorkflowStartOutbox
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-outbox-worker")
    log = structlog.get_logger()
    temporal = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    workflow_starter = WorkflowStartOutbox(
        dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
        temporal=temporal,
    )
    projection = AnalyticsProjection(
        ClickHouseWriter(
            endpoint=settings.clickhouse_url,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
        )
    )
    consumer = OutboxConsumer(
        dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
        consumer_name=settings.outbox_consumer_name,
        publish=projection.publish,
        # 单一词表来源：新事件类型（W2 source_audit / W3 disparagement）只需在
        # analytics/outbox.py 注册——此处硬编码子集曾静默漏消费（2026-08-06 生产发现）。
        event_types=ANALYTICS_EVENT_TYPES,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    log.info(
        "outbox_worker_started",
        consumer_name=settings.outbox_consumer_name,
        batch_size=settings.outbox_batch_size,
    )
    while not stopping.is_set():
        try:
            workflow_started = await workflow_starter.dispatch_one()
            workflow_signalled = await workflow_starter.dispatch_signal_one()
            workflow_reconciled = await workflow_starter.reconcile_one()
            processed = await asyncio.to_thread(
                consumer.drain,
                limit=settings.outbox_batch_size,
            )
            if processed or workflow_started or workflow_signalled or workflow_reconciled:
                log.info(
                    "outbox_batch_published",
                    processed=processed,
                    workflow_started=workflow_started,
                    workflow_signalled=workflow_signalled,
                    workflow_reconciled=workflow_reconciled,
                )
                continue
        except Exception as error:
            # Driver exceptions may embed credential-bearing DSNs. Emit only the
            # exception class; operators can correlate via the bounded event name.
            log.error(
                "outbox_batch_failed",
                error_type=type(error).__name__,
            )
        try:
            await asyncio.wait_for(
                stopping.wait(),
                timeout=settings.outbox_poll_interval_seconds,
            )
        except TimeoutError:
            pass

    log.info("outbox_worker_stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
