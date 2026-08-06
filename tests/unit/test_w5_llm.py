"""W5 LLM 扩写单元测试：未配置诚实降级 / 输出解析 / 失败如实（MockTransport，不触网）。"""

from __future__ import annotations

import httpx
import pytest
from geo_platform.intake import research
from geo_platform.variants import llm


def _config(api_key: str = "k") -> research.LlmConfig:
    return research.LlmConfig(
        api_key=api_key,
        model="gpt-5.6-luna",
        base_url="https://example.com",
        base_url_fallback="https://fallback.example.com",
        max_rounds=1,
    )


def _factory(payload: dict, status: int = 200) -> llm.ClientFactory:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)

    def factory(config: research.LlmConfig) -> httpx.Client:
        return httpx.Client(base_url="https://example.com/v1", transport=transport)

    return factory


def test_expand_disabled_without_api_key() -> None:
    with pytest.raises(llm.LlmDisabled):
        llm.expand_queries(
            brand="中意人寿",
            product_lines=["重疾险"],
            gap_intents=["推荐"],
            max_variants=5,
            config=_config(api_key=""),
        )


def test_expand_parses_output_text() -> None:
    payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": '["上海重疾险推荐有哪些","重疾险怎么选"]'}
                ]
            }
        ]
    }
    result = llm.expand_queries(
        brand="中意人寿",
        product_lines=["重疾险"],
        gap_intents=["推荐", "选购"],
        max_variants=5,
        config=_config(),
        client_factory=_factory(payload),
    )
    assert result == ["上海重疾险推荐有哪些", "重疾险怎么选"]
    assert llm.PROMPT_VERSION == "w5-expansion-v1"


def test_expand_upstream_error_is_honest_failure() -> None:
    with pytest.raises(llm.LlmFailed):
        llm.expand_queries(
            brand="中意人寿",
            product_lines=[],
            gap_intents=["推荐"],
            max_variants=5,
            config=_config(),
            client_factory=_factory({}, status=500),
        )


def test_expand_garbage_output_is_honest_failure() -> None:
    payload = {"output": [{"content": [{"type": "output_text", "text": "无法生成"}]}]}
    with pytest.raises(llm.LlmFailed):
        llm.expand_queries(
            brand="中意人寿",
            product_lines=[],
            gap_intents=["推荐"],
            max_variants=5,
            config=_config(),
            client_factory=_factory(payload),
        )
