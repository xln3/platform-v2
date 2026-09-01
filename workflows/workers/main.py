import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.observability import configure_tracing
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from workflows.activities.answer_dom_anchor import preflight_answer_evidence_ocr
from workflows.activities.captcha_assist import (
    captcha_assist_start,
    captcha_assist_stop,
)
from workflows.activities.collection import (
    collect_deepseek_api_batch,
    collect_deepseek_batch,
    collect_doubao_api_batch,
    collect_doubao_batch,
    collect_tongyi_api_batch,
    collect_tongyi_batch,
    collect_with_adapter,
    collect_yiyan_api_batch,
    collect_yiyan_batch,
    collect_yuanbao_api_batch,
    collect_yuanbao_batch,
    finalize_account_revocation,
    mark_collection_run_terminal,
    persist_collection_result,
    prepare_collection_session,
    publish_downstream_event,
    release_collection_session,
)
from workflows.activities.disparagement import judge_run_disparagement
from workflows.activities.disparagement_factcheck import factcheck_disparagement_cases
from workflows.activities.own_content_disparagement import judge_own_content_disparagement
from workflows.activities.own_site_snapshot import capture_own_site_snapshots
from workflows.activities.post_analysis import (
    analyze_post_content,
    annotate_post_snapshot,
    begin_post_analysis_task,
    fetch_post_snapshot,
    finalize_post_analysis_task,
)
from workflows.activities.site_suggestions import generate_site_audit_suggestions
from workflows.activities.source_audit import audit_run_sources
from workflows.activities.source_fetch import fetch_run_sources
from workflows.definitions.collection import GeoCollectionWorkflow
from workflows.definitions.health import PlatformHealthWorkflow
from workflows.definitions.own_content import OwnContentDisparagementWorkflow
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
# batch 会话复用 activity（collect_<slug>_batch，W8 起五平台）：默认= collection.py
# 的 fail-closed 实现（诚实 fast-fail，杜绝未注册 activity 被 Temporal 无限重试）；
# doubao/multi 模式按门控替换为 live 实现。
_collect_with_adapter_impl = collect_with_adapter
_collect_doubao_batch_impl = collect_doubao_batch
_collect_deepseek_batch_impl = collect_deepseek_batch
_collect_tongyi_batch_impl = collect_tongyi_batch
_collect_yiyan_batch_impl = collect_yiyan_batch
_collect_yuanbao_batch_impl = collect_yuanbao_batch
# provider_api 模态五 slug（2026-08-31 起）：默认= collection.py 的 fail-closed
# 实现；multi 模式替换为 provider_api_adapter 的 live 实现（Key 未配置时题级
# adapter_not_configured 诚实占位，不炸整 run——见该模块 docstring）。
_collect_doubao_api_batch_impl = collect_doubao_api_batch
_collect_deepseek_api_batch_impl = collect_deepseek_api_batch
_collect_tongyi_api_batch_impl = collect_tongyi_api_batch
_collect_yiyan_api_batch_impl = collect_yiyan_api_batch
_collect_yuanbao_api_batch_impl = collect_yuanbao_api_batch
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
    from workflows.activities.provider_api_adapter import (
        collect_deepseek_api_batch as _live_collect_deepseek_api_batch,
    )
    from workflows.activities.provider_api_adapter import (
        collect_doubao_api_batch as _live_collect_doubao_api_batch,
    )
    from workflows.activities.provider_api_adapter import (
        collect_tongyi_api_batch as _live_collect_tongyi_api_batch,
    )
    from workflows.activities.provider_api_adapter import (
        collect_yiyan_api_batch as _live_collect_yiyan_api_batch,
    )
    from workflows.activities.provider_api_adapter import (
        collect_yuanbao_api_batch as _live_collect_yuanbao_api_batch,
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
    _collect_doubao_api_batch_impl = _live_collect_doubao_api_batch
    _collect_deepseek_api_batch_impl = _live_collect_deepseek_api_batch
    _collect_tongyi_api_batch_impl = _live_collect_tongyi_api_batch
    _collect_yiyan_api_batch_impl = _live_collect_yiyan_api_batch
    _collect_yuanbao_api_batch_impl = _live_collect_yuanbao_api_batch


COLLECTION_WORKFLOWS = (
    PlatformHealthWorkflow,
    GeoCollectionWorkflow,
    HumanInterventionWorkflow,
    PlatformSessionLifecycleWorkflow,
    AccountRevocationWorkflow,
)
COLLECTION_ACTIVITIES: tuple[Callable[..., Any], ...] = (
    _collect_with_adapter_impl,
    _collect_doubao_batch_impl,
    _collect_deepseek_batch_impl,
    _collect_tongyi_batch_impl,
    _collect_yiyan_batch_impl,
    _collect_yuanbao_batch_impl,
    _collect_doubao_api_batch_impl,
    _collect_deepseek_api_batch_impl,
    _collect_tongyi_api_batch_impl,
    _collect_yiyan_api_batch_impl,
    _collect_yuanbao_api_batch_impl,
    captcha_assist_start,
    captcha_assist_stop,
    finalize_account_revocation,
    mark_collection_run_terminal,
    persist_collection_result,
    prepare_collection_session,
    publish_downstream_event,
    release_collection_session,
)

# Temporary drain switch for workflow histories created before
# collection-analysis-detached-v1. New deployments leave this off; operators
# may enable it on one collector only while old histories finish.
_legacy_analysis_enabled = os.environ.get(
    "GEO_LEGACY_ANALYSIS_ON_COLLECTION_WORKER", ""
).strip().lower() in {"1", "true", "yes", "on"}
LEGACY_ANALYSIS_WORKFLOWS = (
    PostAnalysisWorkflow,
    OwnContentDisparagementWorkflow,
)
LEGACY_ANALYSIS_ACTIVITIES: tuple[Callable[..., Any], ...] = (
    analyze_post_content,
    annotate_post_snapshot,
    audit_run_sources,
    begin_post_analysis_task,
    capture_own_site_snapshots,
    factcheck_disparagement_cases,
    fetch_post_snapshot,
    fetch_run_sources,
    finalize_post_analysis_task,
    generate_site_audit_suggestions,
    judge_own_content_disparagement,
    judge_run_disparagement,
)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="geo-platform-v2-worker")
    log = structlog.get_logger()
    # Fail before connecting/registering/polling Temporal. Construction alone does
    # not prove that bundled OCR models and ONNX Runtime can execute on this node, so
    # the preflight performs real inference on a controlled in-memory PNG.
    ocr_version = await asyncio.to_thread(preflight_answer_evidence_ocr)
    log.info("answer_evidence_ocr_preflight_passed", ocr_version=ocr_version)
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
            workflows=list(COLLECTION_WORKFLOWS)
            + (list(LEGACY_ANALYSIS_WORKFLOWS) if _legacy_analysis_enabled else []),
            activities=list(COLLECTION_ACTIVITIES)
            + (list(LEGACY_ANALYSIS_ACTIVITIES) if _legacy_analysis_enabled else []),
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
