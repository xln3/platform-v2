"""doubao-batch-collect-v1 路由/失败落库的纯函数测试（无浏览器、无 DB、无 Temporal）。"""

from __future__ import annotations

from geo_platform.collection.models import CollectionRun

from workflows.activities.collection import (
    CollectionBatchItemResult,
    CollectionTaskInput,
    _derive_run_state,
)
from workflows.definitions.collection import (
    DOUBAO_BATCH_MAX_TIMEOUT_MINUTES,
    doubao_batch_timeout_minutes,
    plan_adapter_segments,
    plan_collection_segments,
    task_result_from_batch_item,
)


def _task(key: str, adapter: str) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key,
        query=f"q-{key}",
        model="m",
        region="CN-BJ",
        mode="normal",
        adapter=adapter,
    )


def test_plan_segments_groups_consecutive_doubao_preserving_order() -> None:
    tasks = [
        _task("a", "doubao"),
        _task("b", "doubao"),
        _task("c", "fixed"),
        _task("d", "doubao"),
        _task("e", "deepseek"),
        _task("f", "doubao"),
    ]
    segments = plan_collection_segments(tasks)
    assert [(is_doubao, [t.business_key for t in items]) for is_doubao, items in segments] == [
        (True, ["a", "b"]),
        (False, ["c"]),
        (True, ["d"]),
        (False, ["e"]),
        (True, ["f"]),
    ]
    # 全量题序原样保持
    assert [t.business_key for _, items in segments for t in items] == [
        t.business_key for t in tasks
    ]


def test_plan_segments_edge_cases() -> None:
    assert plan_collection_segments([]) == []
    only = plan_collection_segments([_task("a", "doubao"), _task("b", "doubao")])
    assert [(d, len(items)) for d, items in only] == [(True, 2)]
    none = plan_collection_segments([_task("a", "fixed"), _task("b", "")])
    assert [(d, len(items)) for d, items in none] == [(False, 2)]
    # adapter 大小写/空白不敏感
    mixed = plan_collection_segments([_task("a", " Doubao "), _task("b", "doubao")])
    assert [(d, len(items)) for d, items in mixed] == [(True, 2)]


def test_doubao_batch_timeout_formula() -> None:
    assert doubao_batch_timeout_minutes(3, 15.0) == 45.0
    assert doubao_batch_timeout_minutes(1, 15.0) == 15.0
    # 封顶 120 分钟
    assert doubao_batch_timeout_minutes(20, 15.0) == DOUBAO_BATCH_MAX_TIMEOUT_MINUTES
    # 0 题防御（不会发生）：按 1 题计
    assert doubao_batch_timeout_minutes(0, 15.0) == 15.0


def test_task_result_from_batch_item_maps_ok_fields() -> None:
    item = CollectionBatchItemResult(
        business_key="k1",
        status="ok",
        answer_text="答案",
        screenshot_ref="file:///tmp/x.png",
        quality_state="live_valid",
        citations=[{"url": "https://example.com", "title": None, "cited_text": None}],
        search_queries=[{"query": "q", "ordinal": 1}],
    )
    result = task_result_from_batch_item(item)
    assert result.business_key == "k1"
    assert result.answer_text == "答案"
    assert result.screenshot_ref == "file:///tmp/x.png"
    assert result.quality_state == "live_valid"
    assert result.citations == item.citations
    assert result.search_queries == item.search_queries


def test_derive_run_state() -> None:
    run = CollectionRun()
    run.total_tasks = 3
    run.completed_tasks = 3
    run.failed_tasks = 0
    assert _derive_run_state(run) == "completed"
    run.completed_tasks = 2
    run.failed_tasks = 1
    assert _derive_run_state(run) == "completed_with_failures"
    run.completed_tasks = 1
    run.failed_tasks = 1
    assert _derive_run_state(run) == "running"
    # 无失败题时与旧二分行为等价：未完成即 running
    run.failed_tasks = 0
    assert _derive_run_state(run) == "running"


def test_plan_adapter_segments_groups_by_slug_preserving_order() -> None:
    tasks = [
        _task("a", "doubao"),
        _task("b", "doubao"),
        _task("c", "deepseek"),
        _task("d", "fixed"),
        _task("e", "yuanbao"),
        _task("f", "yuanbao"),
        _task("g", "doubao"),
    ]
    segments = plan_adapter_segments(tasks)
    assert [(slug, [t.business_key for t in items]) for slug, items in segments] == [
        ("doubao", ["a", "b"]),
        ("deepseek", ["c"]),
        ("fixed", ["d"]),
        ("yuanbao", ["e", "f"]),
        ("doubao", ["g"]),
    ]
    assert [t.business_key for _, items in segments for t in items] == [
        t.business_key for t in tasks
    ]


def test_plan_adapter_segments_edge_cases() -> None:
    assert plan_adapter_segments([]) == []
    # adapter 大小写/空白不敏感；空 adapter 归空 slug 段
    mixed = plan_adapter_segments([_task("a", " Doubao "), _task("b", "doubao"), _task("c", "")])
    assert [(slug, len(items)) for slug, items in mixed] == [("doubao", 2), ("", 1)]
