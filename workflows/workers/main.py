import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from workflows.activities.collection import (
    CollectionBatchInput,
    CollectionBatchResult,
    collect_doubao_batch,
    collect_with_adapter,
    finalize_account_revocation,
    mark_collection_run_terminal,
    persist_collection_result,
    prepare_collection_session,
    publish_downstream_event,
    release_collection_session,
)
from workflows.activities.disparagement import judge_run_disparagement
from workflows.activities.own_site_snapshot import capture_own_site_snapshots
from workflows.activities.post_analysis import (
    analyze_post_content,
    annotate_post_snapshot,
    begin_post_analysis_task,
    fetch_post_snapshot,
    finalize_post_analysis_task,
)
from workflows.activities.source_audit import audit_run_sources
from workflows.activities.source_fetch import fetch_run_sources
from workflows.definitions.collection import GeoCollectionWorkflow
from workflows.definitions.health import PlatformHealthWorkflow
from workflows.definitions.post_analysis import PostAnalysisWorkflow
from workflows.definitions.session import (
    AccountRevocationWorkflow,
    HumanInterventionWorkflow,
    PlatformSessionLifecycleWorkflow,
)


# 采集适配器注册门（见 docs/contract-gaps/S01-003-doubao-live-adapter-handoff.md）。
# 默认（GEO_COLLECTION_ADAPTER 未设置）保持 collection.py 原 fail-closed 实现不动；
# "doubao" = 豆包单平台直注册；"multi" = platform_registry 五平台 dispatcher（按
# CollectionTaskInput.adapter 路由，ADR-0003 后生产接线）。
# batch 会话复用 activity（collect_<slug>_batch，W8 起五平台）：doubao/multi 注册
# live 实现；默认与其他模式注册 fail-closed stub（诚实 fast-fail，杜绝未注册
# activity 被 Temporal 无限重试）。
def _fail_closed_batch(slug: str) -> Callable[..., object]:
    @activity.defn(name=f"collect_{slug}_batch")
    async def _stub(batch: CollectionBatchInput) -> CollectionBatchResult:
        activity.heartbeat({"run_pub_id": batch.run_pub_id, "stage": "adapter_started"})
        raise ApplicationError(
            f"no live {slug} batch adapter is registered",
            type="adapter_not_configured",
            non_retryable=True,
        )

    return _stub


_collect_with_adapter_impl = collect_with_adapter
_collect_doubao_batch_impl = collect_doubao_batch
_collect_deepseek_batch_impl = _fail_closed_batch("deepseek")
_collect_tongyi_batch_impl = _fail_closed_batch("tongyi")
_collect_yiyan_batch_impl = _fail_closed_batch("yiyan")
_collect_yuanbao_batch_impl = _fail_closed_batch("yuanbao")
_adapter_mode = os.environ.get("GEO_COLLECTION_ADAPTER", "").strip()
if _adapter_mode == "doubao":
    from workflows.activities.doubao_adapter import (
        collect_doubao_batch as _doubao_collect_doubao_batch,
    )
    from workflows.activities.doubao_adapter import (
        collect_with_adapter as _doubao_collect_with_adapter,
    )

    _collect_with_adapter_impl = _doubao_collect_with_adapter
    _collect_doubao_batch_impl = _doubao_collect_doubao_batch
elif _adapter_mode == "multi":
    from workflows.activities.deepseek_adapter import (
        collect_deepseek_batch as _live_collect_deepseek_batch,
    )
    from workflows.activities.doubao_adapter import (
        collect_doubao_batch as _doubao_collect_doubao_batch,
    )
    from workflows.activities.platform_registry import (
        collect_with_adapter as _multi_collect_with_adapter,
    )
    from workflows.activities.tongyi_adapter import (
        collect_tongyi_batch as _live_collect_tongyi_batch,
    )
    from workflows.activities.yiyan_adapter import (
        collect_yiyan_batch as _live_collect_yiyan_batch,
    )
    from workflows.activities.yuanbao_adapter import (
        collect_yuanbao_batch as _live_collect_yuanbao_batch,
    )

    _collect_with_adapter_impl = _multi_collect_with_adapter
    _collect_doubao_batch_impl = _doubao_collect_doubao_batch
    _collect_deepseek_batch_impl = _live_collect_deepseek_batch
    _collect_tongyi_batch_impl = _live_collect_tongyi_batch
    _collect_yiyan_batch_impl = _live_collect_yiyan_batch
    _collect_yuanbao_batch_impl = _live_collect_yuanbao_batch


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-worker")
    log = structlog.get_logger()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )
    log.info(
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
                PostAnalysisWorkflow,
            ],
            activities=[
                _collect_with_adapter_impl,
                _collect_doubao_batch_impl,
                _collect_deepseek_batch_impl,
                _collect_tongyi_batch_impl,
                _collect_yiyan_batch_impl,
                _collect_yuanbao_batch_impl,
                analyze_post_content,
                annotate_post_snapshot,
                audit_run_sources,
                begin_post_analysis_task,
                capture_own_site_snapshots,
                fetch_post_snapshot,
                fetch_run_sources,
                finalize_account_revocation,
                finalize_post_analysis_task,
                judge_run_disparagement,
                mark_collection_run_terminal,
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
