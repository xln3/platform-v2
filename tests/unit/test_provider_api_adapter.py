"""provider_api 采集模态（workflows/activities/provider_api_adapter.py）单测。

覆盖：配置门（缺 Key/Model → 题级 wall/adapter_not_configured 等长占位，不
raise）、mode 门、五平台请求形状（Responses/chat、联网开关有无）、响应抽取
（正文/引用/检索词，字段缺失诚实为空）、HTTP/传输错误映射、原始响应证据
落盘且绝不泄露 API Key。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workflows.activities.collection import (
    PROVIDER_API_ADAPTER_SLUGS,
    CollectionBatchInput,
    CollectionTaskInput,
)
from workflows.activities.provider_api_adapter import (
    PROVIDER_API_PLATFORM_SLUGS,
    PROVIDER_API_PROFILES,
    ProviderApiConfig,
    ProviderApiNotConfiguredError,
    ProviderApiProfile,
    ProviderHttpResponse,
    run_provider_api_batch,
)


def _task(
    business_key: str = "b" * 64, query: str = "GEO 是什么？", mode: str = "normal"
) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=business_key,
        query=query,
        model="doubao_api",
        region="api",
        mode=mode,
        adapter="doubao_api",
    )


def _batch(*items: CollectionTaskInput) -> CollectionBatchInput:
    return CollectionBatchInput(
        tenant_pub_id="ten_test",
        run_pub_id="run_test",
        items=list(items),
    )


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    profile: ProviderApiProfile,
    *,
    model: str = "test-model",
) -> None:
    monkeypatch.setenv(profile.env_api_key, "sk-test-secret-key")
    monkeypatch.setenv(profile.env_model, model)
    monkeypatch.setenv("GEO_PROVIDER_API_EVIDENCE_DIR", str(tmp_path))


class _FakePost:
    """post_json 注入 fake：记录请求、按剧本返回。"""

    def __init__(self, *, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float,
    ) -> ProviderHttpResponse:
        self.calls.append(
            {"url": url, "headers": headers, "payload": payload, "timeout_s": timeout_s}
        )
        return ProviderHttpResponse(status=self.status, body=self.body)


_ARK_OK_BODY: dict[str, Any] = {
    "id": "resp-1",
    "status": "completed",
    "output": [
        {
            "type": "web_search_call",
            "id": "ws_1",
            "action": {"type": "search", "query": "GEO 生成引擎优化"},
        },
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "GEO 是生成引擎优化[1]。",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://example.com/geo-intro",
                            "title": "GEO 介绍",
                            "start_index": 9,
                            "end_index": 12,
                        },
                        {
                            "type": "url_citation",
                            "url": "https://example.com/geo-guide",
                            "title": "GEO 指南",
                        },
                    ],
                }
            ],
        },
    ],
    "usage": {"tool_usage": {"web_search": 1}},
}

_QIANFAN_OK_BODY: dict[str, Any] = {
    "id": "as-1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "文心答案[1]"},
            "finish_reason": "stop",
        }
    ],
    "search_results": [
        {"index": 1, "url": "https://example.com/a", "title": "来源A"},
        {"index": 2, "url": "https://example.com/b", "title": "来源B"},
    ],
}

_DASHSCOPE_OK_BODY: dict[str, Any] = {
    "output": {
        "choices": [
            {
                "message": {"role": "assistant", "content": "通义答案[1]"},
                "finish_reason": "stop",
            }
        ],
        "search_info": {
            "extra_tool_info": [],
            "search_results": [
                {"index": 1, "url": "https://example.com/q1", "title": "来源甲"},
                {"index": 2, "url": "https://example.com/q2", "title": "来源乙"},
            ],
        },
    },
    "usage": {"total_tokens": 100},
    "request_id": "req-1",
}

_HUNYUAN_OK_BODY: dict[str, Any] = {
    "id": "hy-1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "元宝答案，命中搜索[1]"},
            "finish_reason": "stop",
        }
    ],
    "search_info": [
        {"url": "https://example.com/h1", "title": "混元来源一"},
        {"url": "https://example.com/h2", "title": "混元来源二"},
    ],
}

_CHAT_OK_BODY: dict[str, Any] = {
    "id": "chat-1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "纯模型答案"},
            "finish_reason": "stop",
        }
    ],
}


def test_profile_slugs_match_collection_vocabulary() -> None:
    assert tuple(PROVIDER_API_PROFILES) == PROVIDER_API_PLATFORM_SLUGS
    assert set(PROVIDER_API_PROFILES) == set(PROVIDER_API_ADAPTER_SLUGS)


def test_config_missing_key_and_model_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = PROVIDER_API_PROFILES["doubao_api"]
    for env_name in (
        profile.env_api_key,
        profile.env_model,
        profile.env_base_url,
        profile.env_timeout_s,
    ):
        monkeypatch.delenv(env_name, raising=False)
    with pytest.raises(ProviderApiNotConfiguredError, match="API_KEY"):
        ProviderApiConfig.from_env(profile)
    monkeypatch.setenv(profile.env_api_key, "sk-x")
    with pytest.raises(ProviderApiNotConfiguredError, match="MODEL"):
        ProviderApiConfig.from_env(profile)
    monkeypatch.setenv(profile.env_model, "m")
    monkeypatch.setenv(profile.env_timeout_s, "5")
    with pytest.raises(ProviderApiNotConfiguredError, match="TIMEOUT_S"):
        ProviderApiConfig.from_env(profile)


@pytest.mark.asyncio
async def test_unconfigured_platform_yields_per_item_wall_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["deepseek_api"]
    for env_name in (profile.env_api_key, profile.env_model):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("GEO_PROVIDER_API_EVIDENCE_DIR", str(tmp_path))
    post = _FakePost()
    batch = _batch(_task("a" * 64), _task("b" * 64))
    result = await run_provider_api_batch(batch, profile=profile, post_json=post)
    assert len(result.results) == 2
    assert all(item.status == "wall" for item in result.results)
    assert all(item.error_type == "adapter_not_configured" for item in result.results)
    assert post.calls == []  # 未配置绝不发请求


@pytest.mark.asyncio
async def test_non_normal_mode_rejected_per_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["doubao_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_ARK_OK_BODY)
    result = await run_provider_api_batch(
        _batch(_task(mode="deep_think")), profile=profile, post_json=post
    )
    assert result.results[0].status == "wall"
    assert result.results[0].error_type == "unsupported_mode"
    assert post.calls == []


@pytest.mark.asyncio
async def test_doubao_ark_responses_ok_extracts_answer_citations_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["doubao_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_ARK_OK_BODY)
    item = _task()
    result = await run_provider_api_batch(_batch(item), profile=profile, post_json=post)

    (ok,) = result.results
    assert ok.status == "ok"
    assert ok.quality_state == "live_valid"
    assert ok.answer_text == "GEO 是生成引擎优化[1]。"
    assert [(c["url"], c["platform_ordinal"]) for c in ok.citations] == [
        ("https://example.com/geo-intro", 1),
        ("https://example.com/geo-guide", 2),
    ]
    assert ok.citations[0]["title"] == "GEO 介绍"
    assert ok.search_queries == [{"query": "GEO 生成引擎优化", "ordinal": 1}]

    # 请求形状：Responses API + web_search 工具，采样参数一律不发。
    call = post.calls[0]
    assert call["url"] == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert call["headers"]["Authorization"] == "Bearer sk-test-secret-key"
    payload = call["payload"]
    assert payload["model"] == "test-model"
    assert payload["input"][0]["content"][0] == {"type": "input_text", "text": "GEO 是什么？"}
    assert payload["tools"] == [{"type": "web_search", "max_keyword": 3, "limit": 10}]
    assert "temperature" not in payload and "max_tokens" not in payload

    # 原始响应证据落盘且不含密钥。
    (evidence,) = ok.evidence
    assert evidence.kind == "provider_api_raw"
    assert evidence.relation_type == "answer_provider_api_raw"
    assert evidence.mime_type == "application/json"
    raw = Path(evidence.path).read_text(encoding="utf-8")
    assert json.loads(raw)["status"] == "completed"
    assert "sk-test-secret-key" not in raw


@pytest.mark.asyncio
async def test_yiyan_qianfan_ok_extracts_search_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["yiyan_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_QIANFAN_OK_BODY)
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)

    (ok,) = result.results
    assert ok.status == "ok"
    assert ok.answer_text == "文心答案[1]"
    assert [(c["url"], c["platform_ordinal"]) for c in ok.citations] == [
        ("https://example.com/a", 1),
        ("https://example.com/b", 2),
    ]
    payload = post.calls[0]["payload"]
    assert post.calls[0]["url"] == "https://qianfan.baidubce.com/v2/chat/completions"
    assert payload["web_search"]["enable"] is True
    assert payload["web_search"]["enable_citation"] is True
    assert payload["messages"] == [{"role": "user", "content": "GEO 是什么？"}]


@pytest.mark.asyncio
async def test_tongyi_dashscope_native_returns_search_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["tongyi_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_DASHSCOPE_OK_BODY)
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)

    (ok,) = result.results
    assert ok.status == "ok"
    assert ok.answer_text == "通义答案[1]"
    assert [(c["url"], c["platform_ordinal"]) for c in ok.citations] == [
        ("https://example.com/q1", 1),
        ("https://example.com/q2", 2),
    ]
    call = post.calls[0]
    # 原生 Generation 端点（非兼容模式）+ enable_source 回传来源。
    assert call["url"] == (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    )
    payload = call["payload"]
    assert payload["input"]["messages"] == [{"role": "user", "content": "GEO 是什么？"}]
    assert payload["parameters"]["enable_search"] is True
    assert payload["parameters"]["search_options"]["enable_source"] is True
    assert payload["parameters"]["search_options"]["enable_citation"] is True


@pytest.mark.asyncio
async def test_yuanbao_hunyuan_enhancement_returns_search_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["yuanbao_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_HUNYUAN_OK_BODY)
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)

    (ok,) = result.results
    assert ok.status == "ok"
    assert ok.answer_text == "元宝答案，命中搜索[1]"
    assert [(c["url"], c["platform_ordinal"]) for c in ok.citations] == [
        ("https://example.com/h1", 1),
        ("https://example.com/h2", 2),
    ]
    payload = post.calls[0]["payload"]
    assert payload["enable_enhancement"] is True
    assert payload["search_info"] is True
    assert payload["citation"] is True


@pytest.mark.asyncio
async def test_deepseek_official_api_sends_plain_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # deepseek 官方 API 无联网搜索参数——诚实不发，引用为空。
    profile = PROVIDER_API_PROFILES["deepseek_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_CHAT_OK_BODY)
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)

    (ok,) = result.results
    assert ok.status == "ok"
    assert ok.answer_text == "纯模型答案"
    assert ok.citations == []
    payload = post.calls[0]["payload"]
    assert "enable_search" not in payload and "web_search" not in payload
    assert "tools" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "item_status", "error_type"),
    (
        (401, "wall", "provider_api_auth_rejected"),
        (403, "wall", "provider_api_auth_rejected"),
        (400, "wall", "provider_api_bad_request"),
        (429, "incomplete", "provider_api_rate_limited"),
        (500, "incomplete", "provider_api_server_error"),
        (503, "incomplete", "provider_api_server_error"),
    ),
)
async def test_http_error_mapping(
    http_status: int,
    item_status: str,
    error_type: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = PROVIDER_API_PROFILES["deepseek_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=http_status, body={"error": {"message": "boom"}})
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)
    (item,) = result.results
    assert item.status == item_status
    assert item.error_type == error_type
    assert "boom" in (item.error_message or "")
    # 失败题也带原始响应证据（与 raw_capture 失败题落证据同口径）。
    assert [e.kind for e in item.evidence] == ["provider_api_raw"]


@pytest.mark.asyncio
async def test_empty_answer_is_honest_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["deepseek_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(
        status=200, body={"choices": [{"message": {"role": "assistant", "content": ""}}]}
    )
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)
    (item,) = result.results
    assert item.status == "incomplete"
    assert item.error_type == "answer_capture_incomplete"
    assert [e.kind for e in item.evidence] == ["provider_api_raw"]


@pytest.mark.asyncio
async def test_ark_non_completed_status_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["doubao_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body={"status": "incomplete", "output": []})
    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=post)
    (item,) = result.results
    assert item.status == "incomplete"
    assert item.error_type == "provider_api_incomplete_status"


@pytest.mark.asyncio
async def test_transport_failure_is_retryable_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workflows.activities.provider_api_adapter import (
        ERROR_TIMEOUT,
        ProviderApiTransportError,
    )

    profile = PROVIDER_API_PROFILES["deepseek_api"]
    _configure(monkeypatch, tmp_path, profile)

    async def failing_post(
        url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
    ) -> ProviderHttpResponse:
        raise ProviderApiTransportError(ERROR_TIMEOUT, "request timed out")

    result = await run_provider_api_batch(_batch(_task()), profile=profile, post_json=failing_post)
    (item,) = result.results
    assert item.status == "incomplete"
    assert item.error_type == "provider_api_timeout"


@pytest.mark.asyncio
async def test_batch_results_equal_length_and_ordered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = PROVIDER_API_PROFILES["doubao_api"]
    _configure(monkeypatch, tmp_path, profile)
    post = _FakePost(status=200, body=_ARK_OK_BODY)
    items = [_task("a" * 64), _task("b" * 64), _task("c" * 64)]
    result = await run_provider_api_batch(_batch(*items), profile=profile, post_json=post)
    assert [r.business_key for r in result.results] == [item.business_key for item in items]
    assert result.captcha_pause is None


def test_api_slugs_batch_routed_in_workflow_definition() -> None:
    """workflow 侧词表接线：五个 *_api slug 走 batch 路径且映射到 collection.py
    的具名 fail-closed stub（workers/main.py 在 multi 门下替换为 live 实现）。"""
    from workflows.activities import collection as collection_activity
    from workflows.definitions.collection import BATCH_ACTIVITY_BY_SLUG, BATCH_CAPABLE_ADAPTERS

    for slug in PROVIDER_API_PLATFORM_SLUGS:
        assert slug in BATCH_CAPABLE_ADAPTERS
        assert BATCH_ACTIVITY_BY_SLUG[slug] is getattr(collection_activity, f"collect_{slug}_batch")
