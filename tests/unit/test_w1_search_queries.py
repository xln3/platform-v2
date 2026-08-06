"""W1：search_queries 结构化落库的纯函数层单元测试。

持久化（persist_collection_result 写 search_queries_json）需要 PG，属集成测试
范畴；这里锁纯函数契约：_normalize_search_queries 的校验/截断/DLP 语义。
"""

from __future__ import annotations

import pytest

from workflows.activities.collection import (
    _MAX_SEARCH_QUERIES,
    CollectionTaskResult,
    _normalize_search_queries,
)


def test_normalize_search_queries_happy_path() -> None:
    items = [
        {"query": " 中意人寿 重疾险 ", "ordinal": 1},
        {"query": "中意人寿 产品", "ordinal": 2},
    ]
    assert _normalize_search_queries(items) == [
        {"query": "中意人寿 重疾险", "ordinal": 1},
        {"query": "中意人寿 产品", "ordinal": 2},
    ]


def test_normalize_search_queries_empty_is_honest() -> None:
    assert _normalize_search_queries([]) == []


def test_normalize_search_queries_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        _normalize_search_queries(["not-a-dict"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="text is invalid"):
        _normalize_search_queries([{"query": "", "ordinal": 1}])
    with pytest.raises(ValueError, match="text is invalid"):
        _normalize_search_queries([{"query": "x" * 501, "ordinal": 1}])
    with pytest.raises(ValueError, match="ordinal is invalid"):
        _normalize_search_queries([{"query": "q", "ordinal": 0}])
    with pytest.raises(ValueError, match="ordinal is invalid"):
        _normalize_search_queries([{"query": "q", "ordinal": "1"}])
    with pytest.raises(ValueError, match="ordinal is invalid"):
        _normalize_search_queries([{"query": "q", "ordinal": True}])
    with pytest.raises(ValueError, match="too many search queries"):
        _normalize_search_queries(
            [{"query": f"q{i}", "ordinal": i + 1} for i in range(_MAX_SEARCH_QUERIES + 1)]
        )


def test_normalize_search_queries_secret_like_text_stored_raw() -> None:
    """秘密样式文本原文存储（2026-08-06 原始采集原则）：公开检索词不是平台秘密。"""
    rows = _normalize_search_queries([{"query": "password= hunter2", "ordinal": 1}])
    assert rows == [{"query": "password= hunter2", "ordinal": 1}]


def test_collection_task_result_search_queries_default_empty() -> None:
    result = CollectionTaskResult(
        business_key="k",
        answer_text="a",
        screenshot_ref="file:///tmp/x.png",
        quality_state="live_valid",
    )
    assert result.search_queries == []
