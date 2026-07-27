"""五平台采集适配器注册表 dispatcher（ADR-0003 后生产接线，见 docs/contract-gaps/S01-003）。

worker 在 ``GEO_COLLECTION_ADAPTER=multi`` 时把本模块的 ``collect_with_adapter`` 注册为
``collect_with_adapter`` activity 的实现；dispatcher 按 ``CollectionTaskInput.adapter``
的平台 slug lazy import ``workflows/activities/<slug>_adapter.py`` 并调用其
``run_<slug>_collection(item, heartbeat=...)``。

fail-closed：slug 不在五平台表内（空/"fixed"/未知一律）、模块不存在、模块缺
``run_<slug>_collection``——全部 ``ApplicationError(type="unsupported_adapter",
non_retryable=True)``（workflow 的 non_retryable_error_types 已含该 type）。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.collection import CollectionTaskInput, CollectionTaskResult

log = structlog.get_logger()

SUPPORTED_ADAPTER_SLUGS: tuple[str, ...] = (
    "doubao",
    "deepseek",
    "yiyan",
    "tongyi",
    "yuanbao",
)


@activity.defn(name="collect_with_adapter")
async def collect_with_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """多平台 dispatcher 注册实现（workers/main.py 按 GEO_COLLECTION_ADAPTER=multi 选择）。"""
    return await dispatch_collection(item, heartbeat=activity.heartbeat)


async def dispatch_collection(
    item: CollectionTaskInput,
    *,
    heartbeat: Callable[[dict[str, Any]], None],
) -> CollectionTaskResult:
    """按 item.adapter 路由到对应平台适配器。与 activity 上下文解耦以便单测。"""
    slug = (item.adapter or "").strip().lower()
    if slug not in SUPPORTED_ADAPTER_SLUGS:
        raise ApplicationError(
            f"unsupported adapter slug: {item.adapter!r} "
            f"(supported: {', '.join(SUPPORTED_ADAPTER_SLUGS)})",
            type="unsupported_adapter",
            non_retryable=True,
        )
    try:
        module = importlib.import_module(f"workflows.activities.{slug}_adapter")
    except ImportError as exc:
        raise ApplicationError(
            f"adapter module unavailable for slug {slug!r}: {type(exc).__name__}: {exc}",
            type="unsupported_adapter",
            non_retryable=True,
        ) from exc
    runner = getattr(module, f"run_{slug}_collection", None)
    if not callable(runner):
        raise ApplicationError(
            f"adapter module workflows.activities.{slug}_adapter "
            f"has no callable run_{slug}_collection",
            type="unsupported_adapter",
            non_retryable=True,
        )
    await log.ainfo("adapter_dispatch", business_key=item.business_key, adapter=slug)
    return await runner(item, heartbeat=heartbeat)
