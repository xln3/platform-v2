#!/usr/bin/env python3
"""provider_api 采集模态冒烟 CLI（2026-08-31 起）：不经过 run/workflow，直接对
一个 ``*_api`` 平台 slug 发一次真实官方 API 采集，打印答案/引用/检索词与证据
路径——用于 API Key 配置后的接入验收（先本工具冒烟，再去运营端发起 run）。

用法：
    set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a
    .venv/bin/python tools/smoke_provider_api.py --platform doubao_api \
        --query "网络空间资产搜索引擎哪家强？"

退出码：0=ok；2=采集失败（wall/incomplete，原因打印到 stderr）；3=配置缺失。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from workflows.activities.collection import CollectionBatchInput, CollectionTaskInput
from workflows.activities.provider_api_adapter import (
    PROVIDER_API_PLATFORM_SLUGS,
    PROVIDER_API_PROFILES,
    ProviderApiNotConfiguredError,
    ProviderApiConfig,
    run_provider_api_batch,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="provider_api 单题真实采集冒烟")
    parser.add_argument("--platform", required=True, choices=PROVIDER_API_PLATFORM_SLUGS)
    parser.add_argument("--query", required=True, help="采集问题原文")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    profile = PROVIDER_API_PROFILES[args.platform]
    try:
        ProviderApiConfig.from_env(profile)
    except ProviderApiNotConfiguredError as exc:
        print(f"配置缺失：{exc}", file=sys.stderr)
        return 3
    item = CollectionTaskInput(
        business_key="smoke" + "0" * 59,
        query=args.query,
        model=args.platform,
        region="api",
        mode="normal",
        adapter=args.platform,
    )
    result = await run_provider_api_batch(
        CollectionBatchInput(tenant_pub_id="ten_smoke", run_pub_id="run_smoke", items=[item]),
        profile=profile,
    )
    (outcome,) = result.results
    if outcome.status != "ok":
        print(f"采集失败：{outcome.status}/{outcome.error_type}: {outcome.error_message}", file=sys.stderr)
        return 2
    print(f"== 答案（{len(outcome.answer_text or '')} 字）==")
    print(outcome.answer_text)
    print(f"\n== 引用（{len(outcome.citations)} 条）==")
    for citation in outcome.citations:
        print(f"[{citation.get('platform_ordinal')}] {citation.get('title') or ''} {citation['url']}")
    print(f"\n== 检索词（{len(outcome.search_queries)} 条）==")
    for entry in outcome.search_queries:
        print(f"[{entry['ordinal']}] {entry['query']}")
    print("\n== 证据 ==")
    for evidence in outcome.evidence:
        print(f"{evidence.kind} ({evidence.mime_type}): {evidence.path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
