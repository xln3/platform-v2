"""W5 LLM 扩写（补充地位，真实种子优先）。

纪律（对齐 intake/research.py）：
  * key 只走 settings（GEO_RESEARCH_LLM_*，经 research.config_from_settings 复用同一
    配置，**不新增 LLM env**，严禁入库/日志）；未配置 → LlmDisabled，调用方跳过扩写
    并在生成摘要如实标注 llm_note，绝不阻塞主流程；
  * 产出必须带 model + prompt_version 落库（source_type="llm_expansion"）；
  * 零合成：prompt 明示不要编造品牌事实，只生成"用户可能的问法"文本。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from ..intake import research

PROMPT_VERSION = "w5-expansion-v1"
_TIMEOUT_SECONDS = 120.0
_MAX_EXPANSIONS = 30

_SYSTEM_PROMPT = (
    "你是 GEO（生成式引擎优化）查询变体助手。给定品牌/产品与意图清单，"
    "生成中国用户向 AI 助手提问时真实会用的中文问句。\n"
    "纪律：只生成问句文本，不要编造该品牌的任何事实、卖点或数据；"
    '每条问句不超过 40 字；以严格 JSON 数组返回，如 ["问句一","问句二"]。'
)


class LlmDisabled(RuntimeError):
    """GEO_RESEARCH_LLM_API_KEY 未配置 → 扩写跳过（诚实降级，不阻塞主流程）。"""


class LlmFailed(RuntimeError):
    """上游调用失败 / JSON 抽取失败 → 扩写跳过并如实标注。"""


ClientFactory = Callable[[research.LlmConfig], httpx.Client]


def _default_client_factory(config: research.LlmConfig) -> httpx.Client:
    base = config.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=_TIMEOUT_SECONDS,
        trust_env=False,
    )


def _extract_json_array(text: str) -> list[str]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise LlmFailed("no_json_array")
    try:
        payload: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LlmFailed("json_decode_failed") from exc
    if not isinstance(payload, list):
        raise LlmFailed("payload_not_array")
    return [item.strip() for item in payload if isinstance(item, str) and item.strip()]


def expand_queries(
    *,
    brand: str,
    product_lines: list[str],
    gap_intents: list[str],
    max_variants: int,
    config: research.LlmConfig,
    client_factory: ClientFactory = _default_client_factory,
) -> list[str]:
    """对空格意图扩写问句；返回经长度/数量截断的文本清单（调用方再做归一化/聚类）。"""
    if not config.api_key:
        raise LlmDisabled("research_llm_api_key_missing")
    limit = max(1, min(max_variants, _MAX_EXPANSIONS))
    user_prompt = (
        f"品牌：{brand}\n"
        f"产品/服务：{'、'.join(product_lines) if product_lines else brand}\n"
        f"需要补充的问句意图：{'、'.join(gap_intents)}\n"
        f"请生成不超过 {limit} 条问句，意图尽量均匀覆盖。"
    )
    body = {
        "model": config.model,
        "input": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        with client_factory(config) as client:
            response = client.post("/responses", json=body)
    except httpx.HTTPError as exc:
        raise LlmFailed("http_error") from exc
    if response.status_code != 200:
        raise LlmFailed(f"upstream_{response.status_code}")
    text = ""
    for item in response.json().get("output", []):
        if isinstance(item, dict):
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text += str(part.get("text", ""))
    if not text:
        text = str(response.json().get("output_text", ""))
    if not text:
        raise LlmFailed("empty_output")
    return _extract_json_array(text)[:limit]
