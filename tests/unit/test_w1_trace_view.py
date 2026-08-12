"""W1：trace 回放端点整形逻辑的单元测试（纯函数层，无需 DB/CAS）。

端点本身（tenant 隔离 SQL + object_store.get_verified）属集成测试；这里锁
resolve_task_trace / build_task_trace_view 的契约：sse_evidence_missing /
sse_blob_missing 分支、响应字段口径、截断标注。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from geo_platform.collection.router import (
    build_task_trace_view,
    resolve_task_trace,
)

_TRACE_RECORD = {
    "version": 1,
    "deep_think_active": True,
    "thinking_title": "已深度思考",
    "thinking_chain": [
        {"kind": "reasoning", "block_id": "t1", "text": "先拆解问题。"},
        {
            "kind": "search",
            "block_id": "s1",
            "queries": ["中意人寿 重疾险"],
            "summary": "检索摘要",
            "n_results": 1,
        },
    ],
    "search_blocks": [
        {
            "scene": 1,
            "queries": ["中意人寿 重疾险"],
            "summary": "检索摘要",
            "results": [
                {
                    "title": "标题A",
                    "url": "https://example.com/a",
                    "site": "example.com",
                    "rank": 0,
                    "summary": "摘要A",
                }
            ],
        }
    ],
    "queries": [{"query": "中意人寿 重疾险", "ordinal": 1}],
    "stats": {"event_count": 2, "events_by_type": {"STREAM_MSG_NOTIFY": 1}, "truncated": False},
    "conversation_id": "conv-1",
    "section_id": "sec-1",
    "message_id": "msg-1",
}

_KWARGS = {
    "task_pub_id": "ans_test123",
    "matrix": {
        "query": "中意人寿的重疾险有哪些",
        "mode": "deep_think",
        "model": "doubao",
        "region": "Beijing",
    },
    "answer_text": "正式回答正文。",
    "tick_time": "2026-08-05T01:00:00+00:00",
    "stored_search_queries": [{"query": "中意人寿 重疾险", "ordinal": 1}],
}


def test_resolve_task_trace_missing_evidence_is_404() -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_task_trace(
            **dict(_KWARGS),
            asset_rows=[],
            blob_loader=lambda key, sha: b"{}",
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {"code": "sse_evidence_missing"}


def test_resolve_task_trace_blob_failure_is_distinct_404() -> None:
    def _broken(key: str, sha: str) -> bytes:
        raise ValueError("object integrity verification failed")

    with pytest.raises(HTTPException) as exc_info:
        resolve_task_trace(
            **dict(_KWARGS),
            asset_rows=[{"object_key": "sha256/ab/cd/x", "sha256": "0" * 64}],
            blob_loader=_broken,
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {"code": "sse_blob_missing"}


def test_resolve_task_trace_blob_not_json_object_is_404() -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_task_trace(
            **dict(_KWARGS),
            asset_rows=[{"object_key": "k", "sha256": "0" * 64}],
            blob_loader=lambda key, sha: b"[1,2,3]",
        )
    assert exc_info.value.detail == {"code": "sse_blob_missing"}


def test_resolve_task_trace_shapes_response_like_legacy() -> None:
    blob = json.dumps(_TRACE_RECORD, ensure_ascii=False).encode()
    view = resolve_task_trace(
        **dict(_KWARGS),
        asset_rows=[{"object_key": "k", "sha256": "0" * 64}],
        blob_loader=lambda key, sha: blob,
    )

    assert view.answer.id == "ans_test123"
    assert view.answer.query == "中意人寿的重疾险有哪些"
    assert view.answer.mode == "deep_think"
    assert view.answer.engine == "doubao"
    assert view.answer.region == "Beijing"
    assert view.answer.tick_time == "2026-08-05T01:00:00+00:00"
    assert view.answer.response_text == "正式回答正文。"

    assert view.deep_think_active is True
    assert view.thinking_title == "已深度思考"

    assert [s.kind for s in view.reasoning] == ["surfaced_reasoning", "search"]
    assert view.reasoning[0].text == "先拆解问题。"
    assert view.reasoning[1].queries == ["中意人寿 重疾险"]

    assert len(view.search_blocks) == 1
    block = view.search_blocks[0]
    assert block.scene == 1
    assert block.result_count == 1
    assert block.results[0].title == "标题A"
    assert block.results[0].url == "https://example.com/a"
    assert block.results[0].site == "example.com"
    assert block.results[0].rank == 0
    assert block.results[0].summary == "摘要A"
    assert block.results[0].status == "search_hit"

    assert view.opened_pages_observed is False
    assert view.opened_pages == []
    assert view.answer_reference_pages == []

    assert len(view.search_queries) == 1
    assert view.search_queries[0].query == "中意人寿 重疾险"
    assert view.search_queries[0].ordinal == 1

    assert view.totals.queries == 1
    assert view.totals.results == 1
    assert view.totals.opened_pages is None
    assert view.totals.answer_reference_pages == 0
    assert view.totals.surfaced_reasoning_steps == 1
    assert view.totals.response_text_truncated is False
    assert "仅展示平台明确传输到浏览器" in view.disclosure


def test_build_task_trace_view_separates_search_open_and_answer_reference_pages() -> None:
    record = {
        "engine": "deepseek",
        "source_taxonomy_version": 2,
        "opened_pages_observed": True,
        "deep_think_active": True,
        "thinking_chain": [],
        "search_blocks": [
            {
                "scene": None,
                "queries": ["攻击面管理"],
                "summary": "",
                "results": [
                    {"title": "候选 A", "url": "https://example.com/a", "rank": 1},
                    {"title": "候选 B", "url": "https://example.com/b", "rank": 2},
                ],
            }
        ],
        "opened_pages": [{"title": "已打开 B", "url": "https://example.com/b", "rank": 1}],
        "answer_reference_pages": [
            {"title": "答案引用 B", "url": "https://example.com/b", "rank": 1}
        ],
    }
    view = build_task_trace_view(**dict(_KWARGS), trace_record=record)

    assert [row.status for row in view.search_blocks[0].results] == ["search_hit", "search_hit"]
    assert [row.status for row in view.opened_pages] == ["opened_page"]
    assert [row.status for row in view.answer_reference_pages] == ["answer_reference"]
    assert view.opened_pages_observed is True
    assert view.totals.results == 2
    assert view.totals.opened_pages == 1
    assert view.totals.answer_reference_pages == 1


def test_build_task_trace_view_defensively_deduplicates_persisted_search_copies() -> None:
    """Already-stored traces receive the same truthful projection without backfill."""
    duplicate_block = {
        "scene": 1,
        "queries": ["盛邦安全 RayGate 能力", "盛邦安全 RaySpace 能力"],
        "summary": "搜索 2 个关键词，参考 2 篇资料",
        "results": [
            {"title": "标题A", "url": "https://example.com/a", "rank": 1},
            {"title": "标题B", "url": "https://example.com/b", "rank": 2},
            {"title": "标题A重复", "url": "https://example.com/a", "rank": 3},
        ],
    }
    record = {
        "engine": "doubao",
        "deep_think_active": True,
        "thinking_chain": [],
        "search_blocks": [duplicate_block, dict(duplicate_block, scene=2)],
    }
    view = build_task_trace_view(
        **dict(
            _KWARGS,
            stored_search_queries=[
                {"query": "盛邦安全 RayGate 能力", "ordinal": 1},
                {"query": "盛邦安全 RaySpace 能力", "ordinal": 2},
                {"query": "盛邦安全 RayGate 能力", "ordinal": 3},
                {"query": "盛邦安全 RaySpace 能力", "ordinal": 4},
            ],
        ),
        trace_record=record,
    )

    assert len(view.search_blocks) == 1
    assert view.search_blocks[0].result_count == 2
    assert [row.url for row in view.search_blocks[0].results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert view.totals.results == 2
    assert view.totals.queries == 2
    assert [row.query for row in view.search_queries] == [
        "盛邦安全 RayGate 能力",
        "盛邦安全 RaySpace 能力",
    ]
    assert [row.ordinal for row in view.search_queries] == [1, 2]


def test_build_task_trace_view_marks_legacy_deepseek_sources_unclassified() -> None:
    record = dict(_TRACE_RECORD, engine="deepseek")
    view = build_task_trace_view(**dict(_KWARGS), trace_record=record)

    assert view.search_blocks[0].results[0].status == "legacy_unclassified"
    assert view.opened_pages_observed is False
    assert view.totals.opened_pages is None


def test_build_task_trace_view_truncates_long_response_text() -> None:
    view = build_task_trace_view(
        **dict(_KWARGS, answer_text="长" * 6_000),
        trace_record=_TRACE_RECORD,
    )
    assert len(view.answer.response_text) == 5_000
    assert view.totals.response_text_truncated is True


def test_build_task_trace_view_truncates_long_reasoning_text() -> None:
    record = dict(_TRACE_RECORD)
    record["thinking_chain"] = [{"kind": "reasoning", "text": "想" * 9_999}]
    view = build_task_trace_view(**dict(_KWARGS), trace_record=record)
    assert view.reasoning[0].text is not None
    assert len(view.reasoning[0].text) == 5_000


def test_build_task_trace_view_missing_result_title_falls_back() -> None:
    record = dict(_TRACE_RECORD)
    record["search_blocks"] = [
        {"scene": 1, "queries": [], "summary": "", "results": [{"url": "https://example.com/x"}]}
    ]
    view = build_task_trace_view(**dict(_KWARGS), trace_record=record)
    assert view.search_blocks[0].results[0].title == "未命名来源"
    assert view.search_blocks[0].results[0].site is None


def test_build_task_trace_view_consumes_dom_transport_record() -> None:
    """transport="dom" 记录（文心/元宝/通义 DOM 探针产出：无 version/queries/stats
    键，search_block scene=None）照常整形成回放响应——词表消费与 SSE 来源一致。"""
    record = {
        "engine": "yuanbao",
        "transport": "dom",
        "deep_think_active": True,
        "thinking_chain": [{"kind": "reasoning", "text": "想了一下"}],
        "search_blocks": [
            {
                "scene": None,
                "queries": [],
                "summary": "",
                "results": [
                    {
                        "title": "标题A",
                        "url": "https://example.com/a",
                        "site": None,
                        "rank": 1,
                        "summary": "",
                    }
                ],
            }
        ],
    }
    view = build_task_trace_view(**dict(_KWARGS, stored_search_queries=[]), trace_record=record)
    assert view.deep_think_active is True
    assert view.thinking_title is None
    assert [s.kind for s in view.reasoning] == ["surfaced_reasoning"]
    assert view.reasoning[0].text == "想了一下"
    assert len(view.search_blocks) == 1
    assert view.search_blocks[0].scene is None
    assert view.search_blocks[0].result_count == 1
    assert view.search_blocks[0].results[0].title == "标题A"
    assert view.search_blocks[0].results[0].rank == 1
    assert view.search_queries == []
    assert view.totals.queries == 0
    assert view.totals.results == 1
    assert view.totals.surfaced_reasoning_steps == 1
