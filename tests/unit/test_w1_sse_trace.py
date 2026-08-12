"""W1：豆包 SSE 结构化 trace 证据的单元测试。

手写 SSE body 直调解析函数（先例：tests/unit/test_deepseek_adapter.py 的
test_sse_assembly_real_patch_stream），覆盖 thinking_chain / queries /
search_blocks / stats / deep_think_active / 体积截断纪律 / 解析失败诚实路径。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from workflows.activities import doubao_adapter
from workflows.activities.doubao_adapter import (
    _SSE_TRACE_MAX_BYTES,
    _assemble_final_message,
    _build_sse_trace_record,
    _parse_sse_events,
    _sse_trace_from_body,
)


def _notify_event(blocks: list[dict[str, Any]]) -> str:
    payload = {
        "message": {
            "message_id": "msg-1",
            "user_type": 2,
            "conversation_id": "conv-1",
            "section_id": "sec-1",
        },
        "content": {"content_block": blocks},
    }
    return f"event: STREAM_MSG_NOTIFY\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _text_block(block_id: str, parent_id: str, text: str) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "block_type": 10000,
        "parent_id": parent_id,
        "content": {"text_block": {"text": text}},
    }


def _thinking_root(block_id: str = "think-root") -> dict[str, Any]:
    return {
        "block_id": block_id,
        "block_type": 10040,
        "parent_id": "",
        "content": {"thinking_block": {"finish_title": "已深度思考", "streaming_title": "思考中"}},
    }


def _search_block(
    block_id: str,
    parent_id: str,
    queries: list[str],
    results: list[dict[str, Any]],
    summary: str = "检索摘要",
) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "block_type": 10025,
        "parent_id": parent_id,
        "content": {
            "search_query_result_block": {
                "queries": queries,
                "summary": summary,
                "results": results,
            }
        },
    }


def _result(url: str, title: str, summary: str, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "text_card": {
            "url": url,
            "title": title,
            "summary": summary,
            "sitename": "example.com",
            "index": index,
        },
    }


def _body_with_thinking_and_search() -> str:
    blocks = [
        _thinking_root(),
        _text_block("think-text-1", "think-root", "先拆解用户问题，确定需要检索产品信息。"),
        _search_block(
            "search-1",
            "think-root",
            ["中意人寿 重疾险", "中意人寿 产品"],
            [
                _result("https://example.com/a", "标题A", "摘要A", 0),
                _result("https://example.com/b?utm_source=x", "标题B", "摘要B", 1),
            ],
        ),
        _text_block("answer-1", "", "正式回答正文。"),
    ]
    return (
        'event: SSE_ACK\ndata: {"ack_client_meta":{"conversation_id":"conv-1",'
        '"section_id":"sec-1"}}\n\n' + _notify_event(blocks)
    )


def test_trace_extracts_thinking_queries_search_and_stats() -> None:
    body = _body_with_thinking_and_search()
    trace = _sse_trace_from_body(body)
    assert trace is not None

    assert trace["deep_think_active"] is True
    assert trace["thinking_title"] == "已深度思考"
    assert trace["conversation_id"] == "conv-1"
    assert trace["section_id"] == "sec-1"
    assert trace["message_id"] == "msg-1"

    reasoning = [s for s in trace["thinking_chain"] if s["kind"] == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["text"] == "先拆解用户问题，确定需要检索产品信息。"
    assert "finish_title" in reasoning[0] and "streaming_title" in reasoning[0]
    search_steps = [s for s in trace["thinking_chain"] if s["kind"] == "search"]
    assert len(search_steps) == 1
    assert search_steps[0]["queries"] == ["中意人寿 重疾险", "中意人寿 产品"]
    assert search_steps[0]["n_results"] == 2

    # 扁平化检索词：按出现顺序编号
    assert trace["queries"] == [
        {"query": "中意人寿 重疾险", "ordinal": 1},
        {"query": "中意人寿 产品", "ordinal": 2},
    ]

    assert len(trace["search_blocks"]) == 1
    block = trace["search_blocks"][0]
    assert block["scene"] == 1
    assert block["queries"] == ["中意人寿 重疾险", "中意人寿 产品"]
    assert block["summary"] == "检索摘要"
    assert [r["url"] for r in block["results"]] == [
        "https://example.com/a",
        "https://example.com/b?utm_source=x",
    ]
    assert block["results"][0]["title"] == "标题A"
    assert block["results"][0]["site"] == "example.com"
    assert block["results"][0]["rank"] == 0
    assert block["results"][1]["summary"] == "摘要B"

    stats = trace["stats"]
    assert stats["event_count"] == 2
    assert stats["events_by_type"] == {"SSE_ACK": 1, "STREAM_MSG_NOTIFY": 1}
    assert stats["sse_body_bytes"] == len(body.encode("utf-8"))
    assert stats["truncated"] is False

    payload = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(payload.encode("utf-8")) <= _SSE_TRACE_MAX_BYTES


def test_trace_without_thinking_block_marks_deep_think_inactive() -> None:
    body = _notify_event([_text_block("answer-1", "", "普通回答。")])
    trace = _sse_trace_from_body(body)
    assert trace is not None
    assert trace["deep_think_active"] is False
    assert trace["thinking_title"] is None
    assert trace["thinking_chain"] == []
    assert trace["queries"] == []


def test_trace_deduplicates_thinking_and_top_level_copies_of_same_search() -> None:
    """Live Doubao emits one search payload twice with different block IDs.

    The child copy belongs to the surfaced thinking chain and the top-level copy
    renders the same ten cards in the answer.  They are one candidate set, not two
    search scenes, so content identity—not block_id—must drive deduplication.
    """
    results = [
        _result("https://example.com/a", "标题A", "摘要A", 0),
        _result("https://example.com/b", "标题B", "摘要B", 1),
    ]
    body = _notify_event(
        [
            _thinking_root(),
            _search_block(
                "thinking-search-copy",
                "think-root",
                ["盛邦安全 RayGate 能力", "盛邦安全 RaySpace 能力"],
                results,
                summary="搜索 2 个关键词，参考 2 篇资料",
            ),
            _search_block(
                "top-level-search-copy",
                "",
                ["盛邦安全 RayGate 能力", "盛邦安全 RaySpace 能力"],
                results,
                summary="搜索 2 个关键词，参考 2 篇资料",
            ),
        ]
    )

    trace = _sse_trace_from_body(body)

    assert trace is not None
    assert len(trace["search_blocks"]) == 1
    assert len(trace["search_blocks"][0]["results"]) == 2
    assert trace["queries"] == [
        {"query": "盛邦安全 RayGate 能力", "ordinal": 1},
        {"query": "盛邦安全 RaySpace 能力", "ordinal": 2},
    ]
    assert len([step for step in trace["thinking_chain"] if step["kind"] == "search"]) == 1
    assert trace["stats"]["search_block_duplicates_dropped"] == 1


def test_trace_truncation_keeps_payload_within_budget() -> None:
    """超限先截 results 再截 thinking 文本，stats.truncated 如实标注。"""
    blocks: list[dict[str, Any]] = [_thinking_root()]
    for i in range(40):
        blocks.append(
            _search_block(
                f"search-{i}",
                "think-root",
                [f"检索词{i}"],
                [
                    _result(f"https://example.com/r{i}-{j}", f"标题{j}", "摘" * 800, j)
                    for j in range(50)
                ],
            )
        )
    body = _notify_event(blocks)
    trace = _sse_trace_from_body(body)
    assert trace is not None

    payload = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(payload.encode("utf-8")) <= _SSE_TRACE_MAX_BYTES
    assert trace["stats"]["truncated"] is True
    # 截断纪律：先砍 results（每块 ≤20），本例体量需砍到 ≤5
    assert all(len(b["results"]) <= 20 for b in trace["search_blocks"])
    # 事件计数不受截断影响
    assert trace["stats"]["event_count"] == 1


def test_trace_build_from_parsed_events_directly() -> None:
    """直调 _parse/_assemble/_build 三段（单元粒度）。"""
    body = _body_with_thinking_and_search()
    events = _parse_sse_events(body)
    assembled = _assemble_final_message(events)
    record = _build_sse_trace_record(events, assembled)
    assert record["version"] == 1
    assert record["queries"][0]["ordinal"] == 1


def test_trace_parse_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析异常 → None（不出证据、不编造），与 _rich_record_from_sse 同款诚实路径。"""

    def _boom(body: str) -> list[dict[str, Any]]:
        raise RuntimeError("corrupt stream")

    monkeypatch.setattr(doubao_adapter, "_parse_sse_events", _boom)
    assert _sse_trace_from_body("event: x\ndata: {}\n\n") is None
