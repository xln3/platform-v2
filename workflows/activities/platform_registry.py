"""五平台采集适配器注册表 dispatcher（ADR-0003 后生产接线，见 docs/contract-gaps/S01-003）。

worker 在 ``GEO_COLLECTION_ADAPTER=multi`` 时把本模块的 ``collect_with_adapter`` 注册为
``collect_with_adapter`` activity 的实现；dispatcher 按 ``CollectionTaskInput.adapter``
的平台 slug lazy import ``workflows/activities/<slug>_adapter.py`` 并调用其
``run_<slug>_collection(item, heartbeat=..., proxy_url_override=...)``。代理 override
由 worker-local 地域 resolver 产生，绝不进入 Temporal task payload/history。

fail-closed：slug 不在五平台表内（空/"fixed"/未知一律）、模块不存在、模块缺
``run_<slug>_collection``——全部 ``ApplicationError(type="unsupported_adapter",
non_retryable=True)``（workflow 的 non_retryable_error_types 已含该 type）。
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from typing import Any, cast

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.collection import CollectionTaskInput, CollectionTaskResult
from workflows.activities.region_proxy_router import (
    ResolvedRegionProxy,
    resolve_region_proxy,
)

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
    proxy_resolver: Callable[[str, str], Awaitable[ResolvedRegionProxy]] = resolve_region_proxy,
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
    heartbeat(
        {
            "business_key": item.business_key,
            "stage": "proxy_resolution",
            "region": item.region,
        }
    )
    resolution_task = asyncio.ensure_future(proxy_resolver(slug, item.region))
    while True:
        done, _pending = await asyncio.wait({resolution_task}, timeout=10.0)
        if done:
            break
        heartbeat(
            {
                "business_key": item.business_key,
                "stage": "proxy_resolution",
                "region": item.region,
            }
        )
    resolved_proxy = resolution_task.result()
    log.info(
        "adapter_dispatch",
        business_key=item.business_key,
        adapter=slug,
        region=item.region,
        proxy_source=resolved_proxy.source,
        proxy_city=resolved_proxy.city,
        proxy_action=resolved_proxy.provider_action,
    )
    typed_runner = cast(
        Callable[..., Awaitable[CollectionTaskResult]],
        runner,
    )
    return await typed_runner(
        item,
        heartbeat=heartbeat,
        proxy_url_override=resolved_proxy.proxy_url,
    )
