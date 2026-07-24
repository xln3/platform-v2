from __future__ import annotations

import asyncio
import signal

import structlog
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    projection = AnalyticsProjection(
        ClickHouseWriter(
            endpoint=settings.clickhouse_url,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
        )
    )
    consumer = OutboxConsumer(
        dsn=settings.postgres_dsn,
        consumer_name=settings.outbox_consumer_name,
        publish=projection.publish,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    await log.ainfo(
        "outbox_worker_started",
        consumer_name=settings.outbox_consumer_name,
        batch_size=settings.outbox_batch_size,
    )
    while not stopping.is_set():
        try:
            processed = await asyncio.to_thread(
                consumer.drain,
                limit=settings.outbox_batch_size,
            )
            if processed:
                await log.ainfo("outbox_batch_published", processed=processed)
                continue
        except Exception as error:
            # Driver exceptions may embed credential-bearing DSNs. Emit only the
            # exception class; operators can correlate via the bounded event name.
            await log.aerror(
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

    await log.ainfo("outbox_worker_stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
