import asyncio
from concurrent.futures import ThreadPoolExecutor

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from temporalio.client import Client
from temporalio.worker import Worker

from workflows.activities.collection import (
    collect_with_adapter,
    finalize_account_revocation,
    persist_collection_result,
    prepare_collection_session,
    publish_downstream_event,
    release_collection_session,
)
from workflows.definitions.collection import GeoCollectionWorkflow
from workflows.definitions.health import PlatformHealthWorkflow
from workflows.definitions.session import (
    AccountRevocationWorkflow,
    HumanInterventionWorkflow,
    PlatformSessionLifecycleWorkflow,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await log.ainfo(
        "temporal_worker_connected",
        address=settings.temporal_address,
        task_queue=settings.temporal_task_queue,
    )
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="geo-browser-activity") as executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[
                PlatformHealthWorkflow,
                GeoCollectionWorkflow,
                HumanInterventionWorkflow,
                PlatformSessionLifecycleWorkflow,
                AccountRevocationWorkflow,
            ],
            activities=[
                collect_with_adapter,
                finalize_account_revocation,
                persist_collection_result,
                prepare_collection_session,
                publish_downstream_event,
                release_collection_session,
            ],
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
