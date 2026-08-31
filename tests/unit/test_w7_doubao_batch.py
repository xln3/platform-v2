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
    plan_batch_segments,
    plan_collection_segments,
    plan_instance_segments,
    plan_mode_instance_segments,
    plan_persistence_segments,
    plan_versioned_batch_segments,
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


# ---------------------------------------------------------------------------
# adapter-batch-collect-v2（浏览器矩阵化）：(adapter, region) 切段 + patch 门
# ---------------------------------------------------------------------------


def _region_task(
    key: str,
    adapter: str,
    region: str,
    mode: str = "normal",
) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key,
        query=f"q-{key}",
        model="m",
        region=region,
        mode=mode,
        adapter=adapter,
    )


def test_plan_instance_segments_splits_on_region_change() -> None:
    """同平台双 region → 两段；同段序保持；region 空白/大小写按原串分组。"""
    tasks = [
        _region_task("a", "doubao", "CN-SH"),
        _region_task("b", "doubao", "CN-SH"),
        _region_task("c", "doubao", "CN-BJ"),  # 同平台换地域 → 新段
        _region_task("d", "deepseek", "CN-BJ"),  # 换平台 → 新段
        _region_task("e", "doubao", "CN-BJ"),  # 回到 doubao 但不相邻 → 新段
    ]
    segments = plan_instance_segments(tasks)
    assert [(key, [t.business_key for t in items]) for key, items in segments] == [
        (("doubao", "CN-SH"), ["a", "b"]),
        (("doubao", "CN-BJ"), ["c"]),
        (("deepseek", "CN-BJ"), ["d"]),
        (("doubao", "CN-BJ"), ["e"]),
    ]
    assert [t.business_key for _, items in segments for t in items] == [
        t.business_key for t in tasks
    ]


def test_plan_batch_segments_gate_unpatched_keeps_v1_grouping() -> None:
    """未打 adapter-batch-collect-v2 补丁的历史重放：旧 (adapter) 分组零变化。"""
    tasks = [
        _region_task("a", "doubao", "CN-SH"),
        _region_task("b", "doubao", "CN-BJ"),
    ]
    assert plan_batch_segments(False, tasks) == plan_adapter_segments(tasks) == [("doubao", tasks)]


def test_plan_batch_segments_gate_patched_uses_instance_grouping() -> None:
    tasks = [
        _region_task("a", "doubao", "CN-SH"),
        _region_task("b", "doubao", "CN-BJ"),
        _region_task("c", "fixed", "CN-BJ"),
    ]
    segments = plan_batch_segments(True, tasks)
    assert [(slug, [t.business_key for t in items]) for slug, items in segments] == [
        ("doubao", ["a"]),
        ("doubao", ["b"]),
        ("fixed", ["c"]),
    ]


def test_plan_batch_segments_empty_and_blank_region() -> None:
    assert plan_batch_segments(True, []) == []
    assert plan_batch_segments(False, []) == []
    # 缺 region 的任务按空串键分组（activity 侧归一失败 → region_exit_mismatch
    # 诚实报错；分组层不吞不编）
    segments = plan_batch_segments(True, [_region_task("a", "doubao", "")])
    assert [(slug, len(items)) for slug, items in segments] == [("doubao", 1)]


# ---------------------------------------------------------------------------
# adapter-batch-mode-segments-v3：模式额度墙不能连坐同实例的另一模式
# ---------------------------------------------------------------------------


def test_plan_mode_instance_segments_splits_same_instance_on_mode_change() -> None:
    tasks = [
        _region_task("n-1", "doubao", "CN-BJ", "normal"),
        _region_task("n-2", "doubao", "CN-BJ", "normal"),
        _region_task("d-1", "doubao", "CN-BJ", "deep_think"),
        _region_task("n-3", "doubao", "CN-BJ", "normal"),
    ]

    segments = plan_mode_instance_segments(tasks)

    assert [(key, [item.business_key for item in items]) for key, items in segments] == [
        (("doubao", "CN-BJ", "normal"), ["n-1", "n-2"]),
        (("doubao", "CN-BJ", "deep_think"), ["d-1"]),
        (("doubao", "CN-BJ", "normal"), ["n-3"]),
    ]


def test_versioned_batch_segments_preserves_v1_v2_and_enables_v3() -> None:
    tasks = [
        _region_task("n", "doubao", "CN-BJ", "normal"),
        _region_task("d", "doubao", "CN-BJ", "deep_think"),
        _region_task("s", "doubao", "CN-SH", "normal"),
    ]

    v1 = plan_versioned_batch_segments(False, False, tasks)
    v2 = plan_versioned_batch_segments(True, False, tasks)
    v3 = plan_versioned_batch_segments(True, True, tasks)

    assert [[item.business_key for item in items] for _, items in v1] == [["n", "d", "s"]]
    assert [[item.business_key for item in items] for _, items in v2] == [
        ["n", "d"],
        ["s"],
    ]
    assert [[item.business_key for item in items] for _, items in v3] == [
        ["n"],
        ["d"],
        ["s"],
    ]


def test_plan_persistence_segments_splits_every_query_without_changing_order() -> None:
    tasks = [
        _region_task("a", "doubao", "CN-BJ", "normal"),
        _region_task("b", "doubao", "CN-BJ", "normal"),
        _region_task("c", "deepseek", "CN-SH", "normal"),
    ]
    segments = [("doubao", tasks[:2]), ("deepseek", tasks[2:])]

    assert plan_persistence_segments(False, segments) == segments
    assert [
        (slug, [item.business_key for item in items])
        for slug, items in plan_persistence_segments(True, segments)
    ] == [("doubao", ["a"]), ("doubao", ["b"]), ("deepseek", ["c"])]
