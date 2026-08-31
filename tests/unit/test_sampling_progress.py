import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import geo_platform.analytics.router as analytics_router
from geo_platform.analytics.sampling_progress import (
    parse_sampling_configs,
    sampling_columns,
    sampling_plan_items,
    select_sampling_campaign,
    uses_quotation_appendices,
    variant_label,
)
from geo_platform.identity.policy import Principal, Role


def _row(
    revision: int,
    queries: list[str],
    *,
    model: str = "doubao",
    region: str = "北京",
    mode: str = "deep_think",
    modes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pub_id": f"cfv_{revision}",
        "revision": revision,
        "snapshot_json": json.dumps(
            {
                "query_groups": [
                    {
                        "name": "候选组",
                        "items": [
                            {"text": query, "priority": index + 1}
                            for index, query in enumerate(queries)
                        ],
                    }
                ],
                "models": [model],
                "regions": [region],
                "modes": modes or [mode],
            }
        ),
    }


def test_select_sampling_campaign_merges_split_legs_and_topups() -> None:
    configs = parse_sampling_configs(
        [
            _row(48, ["问题三"], model="doubao", region="北京"),
            _row(47, ["问题一", "问题三"], model="doubao", region="上海"),
            _row(46, ["问题三"], model="doubao", region="北京"),
            _row(45, ["问题一", "问题二"], model="doubao", region="上海"),
            _row(42, ["问题一", "问题二", "问题三"], model="deepseek", region="上海"),
            _row(41, ["问题一", "问题二", "问题三"], model="deepseek", region="北京"),
            _row(
                40,
                ["问题一", "问题二", "问题三"],
                model="yiyan",
                region="北京",
                mode="normal",
            ),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 42
    assert [config.revision for config in campaign] == [48, 47, 46, 45, 42, 41]
    assert [item.query_text for item in sampling_plan_items(baseline)] == [
        "问题一",
        "问题二",
        "问题三",
    ]


def test_select_sampling_campaign_bridges_quick_topup_to_deep_think_full_plan() -> None:
    configs = parse_sampling_configs(
        [
            _row(44, ["问题二"], model="doubao", region="北京", mode="normal"),
            _row(43, ["问题一"], model="doubao", region="上海", mode="normal"),
            _row(
                42,
                ["问题一", "问题二", "问题三"],
                model="deepseek",
                region="上海",
            ),
            _row(
                41,
                ["问题一", "问题二", "问题三"],
                model="deepseek",
                region="北京",
            ),
            _row(
                40,
                ["问题一", "问题二", "问题三"],
                model="doubao",
                region="上海",
            ),
            _row(
                39,
                ["问题一", "问题二", "问题三"],
                model="doubao",
                region="北京",
            ),
            _row(
                38,
                ["旧批次问题"],
                model="yiyan",
                region="北京",
                mode="normal",
            ),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 42
    assert [config.revision for config in campaign] == [44, 43, 42, 41, 40, 39]
    assert [
        (column.model, column.region, column.mode, column.modes)
        for column in sampling_columns(campaign, baseline=baseline)
    ] == [
        ("doubao", "北京", "deep_think", ("deep_think", "normal")),
        ("doubao", "上海", "deep_think", ("deep_think", "normal")),
        ("deepseek", "北京", "deep_think", ("deep_think",)),
        ("deepseek", "上海", "deep_think", ("deep_think",)),
    ]


def test_select_sampling_campaign_ignores_interleaved_independent_configs() -> None:
    configs = parse_sampling_configs(
        [
            _row(48, ["问题三"], model="doubao", region="北京", mode="normal"),
            _row(47, ["无关问题一", "无关问题二"], model="yiyan", region="上海"),
            _row(46, ["问题一", "无关问题三"], model="deepseek", region="上海"),
            _row(45, ["问题一", "问题二"], model="doubao", region="上海"),
            _row(
                42,
                ["问题一", "问题二", "问题三"],
                model="deepseek",
                region="上海",
            ),
            _row(
                41,
                ["问题一", "问题二", "问题三"],
                model="deepseek",
                region="北京",
            ),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 42
    assert [config.revision for config in campaign] == [48, 45, 42, 41]
    # Partial configs can contribute answers, but cannot invent formal sampling legs.
    assert [
        (column.model, column.region, column.mode)
        for column in sampling_columns(campaign, baseline=baseline)
    ] == [
        ("deepseek", "北京", "deep_think"),
        ("deepseek", "上海", "deep_think"),
    ]


def test_select_sampling_campaign_merges_older_full_legs_across_canaries() -> None:
    queries = [f"问题{index:02d}" for index in range(24)]
    old_queries = [f"旧批次问题{index:02d}" for index in range(24)]
    configs = parse_sampling_configs(
        [
            _row(11, queries, model="yiyan", region="北京", mode="normal"),
            _row(10, [queries[0]], model="yiyan", region="北京", mode="normal"),
            _row(9, [queries[0]], model="yuanbao", region="北京", mode="normal"),
            _row(8, [queries[0]], model="doubao", region="北京", mode="normal"),
            _row(7, queries, model="tongyi", region="北京", mode="normal"),
            _row(6, queries, model="deepseek", region="北京", mode="normal"),
            _row(5, ["独立 canary"], model="doubao", region="上海", mode="normal"),
            _row(4, old_queries, model="deepseek", region="上海", mode="normal"),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 11
    assert [config.revision for config in campaign] == [11, 10, 9, 8, 7, 6]
    assert [
        (column.model, column.region, column.mode)
        for column in sampling_columns(campaign, baseline=baseline)
    ] == [
        ("deepseek", "北京", "normal"),
        ("yiyan", "北京", "normal"),
        ("tongyi", "北京", "normal"),
    ]


def test_select_sampling_campaign_uses_explicit_catalog_definition() -> None:
    configs = parse_sampling_configs(
        [
            _row(48, ["无关问题"], model="yiyan", region="上海"),
            _row(47, ["问题三"], model="doubao", region="北京", mode="normal"),
            _row(46, ["问题一", "新增问题"], model="deepseek", region="上海"),
            _row(
                42,
                ["问题一", "问题二", "问题三"],
                model="deepseek",
                region="上海",
            ),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs, baseline_pub_id="cfv_42")

    assert baseline is not None
    assert baseline.revision == 42
    assert [config.revision for config in campaign] == [47, 46, 42]


def test_select_sampling_campaign_fails_closed_for_missing_catalog_definition() -> None:
    configs = parse_sampling_configs([_row(3, ["问题"])])

    assert select_sampling_campaign(configs, baseline_pub_id="cfv_missing") == (None, [])


def test_select_sampling_campaign_recovers_plan_behind_more_than_100_topups() -> None:
    full_plan = [f"问题{index:03d}" for index in range(136)]
    topups = [
        _row(revision, [full_plan[(revision - 43) % len(full_plan)]])
        for revision in range(197, 42, -1)
    ]
    configs = parse_sampling_configs(
        topups
        + [
            _row(42, full_plan, model="deepseek", region="上海"),
            _row(41, full_plan, model="deepseek", region="北京"),
        ]
    )

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 42
    assert len(sampling_plan_items(baseline)) == 136
    assert [config.revision for config in campaign[-2:]] == [42, 41]


def test_select_sampling_campaign_keeps_latest_independent_plan() -> None:
    configs = parse_sampling_configs([_row(3, ["新问题"]), _row(2, ["旧问题一", "旧问题二"])])

    baseline, campaign = select_sampling_campaign(configs)

    assert baseline is not None
    assert baseline.revision == 3
    assert [config.revision for config in campaign] == [3]


def test_sampling_columns_use_product_and_product_display_order() -> None:
    configs = parse_sampling_configs(
        [
            _row(4, ["问题"], model="yiyan", region="上海"),
            _row(3, ["问题"], model="deepseek", region="北京"),
            _row(2, ["问题"], model="doubao", region="上海"),
            _row(1, ["问题"], model="doubao", region="北京"),
        ]
    )

    columns = sampling_columns(configs)

    assert [(column.model, column.region, column.mode) for column in columns] == [
        ("doubao", "北京", "deep_think"),
        ("doubao", "上海", "deep_think"),
        ("deepseek", "北京", "deep_think"),
        ("yiyan", "上海", "deep_think"),
    ]
    assert [column.key for column in columns] == ["leg-1", "leg-2", "leg-3", "leg-4"]


def test_sampling_columns_keep_six_formal_legs_and_merge_doubao_fallback_modes() -> None:
    queries = [f"问题{index:03d}" for index in range(136)]
    rows = [
        _row(48, [queries[0]], model="doubao", region="北京", mode="normal"),
        _row(47, [queries[1]], model="doubao", region="上海", mode="normal"),
    ]
    revision = 42
    for model in ("doubao", "deepseek", "yiyan"):
        for region in ("北京", "上海"):
            rows.append(_row(revision, queries, model=model, region=region))
            revision -= 1
    configs = parse_sampling_configs(rows)
    baseline = next(config for config in configs if config.revision == 42)

    columns = sampling_columns(configs, baseline=baseline)

    assert len(columns) == 6
    assert len(queries) * len(columns) == 816
    assert {(column.model, column.region, column.mode, column.modes) for column in columns} == {
        ("doubao", "北京", "deep_think", ("deep_think", "normal")),
        ("doubao", "上海", "deep_think", ("deep_think", "normal")),
        ("deepseek", "北京", "deep_think", ("deep_think",)),
        ("deepseek", "上海", "deep_think", ("deep_think",)),
        ("yiyan", "北京", "deep_think", ("deep_think",)),
        ("yiyan", "上海", "deep_think", ("deep_think",)),
    }


def test_sampling_columns_preserve_genuine_dual_mode_formal_targets() -> None:
    queries = ["问题一", "问题二"]
    configs = parse_sampling_configs(
        [
            _row(3, ["问题一"], mode="experimental"),
            _row(2, queries, modes=["normal", "deep_think"]),
        ]
    )

    columns = sampling_columns(configs, baseline=configs[1])

    assert [(column.mode, column.modes) for column in columns] == [
        ("normal", ("normal",)),
        ("deep_think", ("deep_think",)),
    ]


def test_quotation_shape_and_variant_labels_match_progress_document() -> None:
    snapshot = {
        "query_groups": [
            {
                "name": f"候选组 {group}",
                "items": [
                    {"text": f"问题 {group}-{variant}", "priority": variant + 1}
                    for variant in range(4)
                ],
            }
            for group in range(1, 35)
        ],
        "models": ["doubao"],
        "regions": ["北京"],
        "modes": ["deep_think"],
    }
    configs = parse_sampling_configs(
        [{"pub_id": "cfv_formal", "revision": 1, "snapshot_json": json.dumps(snapshot)}]
    )
    items = sampling_plan_items(configs[0])

    assert uses_quotation_appendices(items) is True
    assert [variant_label(index) for index in range(5)] == [
        "原词/优化句",
        "变体A",
        "变体B",
        "变体C",
        "变体D",
    ]


def test_sampling_progress_route_projects_counts_and_establishes_both_tenant_contexts(
    monkeypatch: Any,
) -> None:
    captured_at = datetime(2026, 8, 13, 0, 53, tzinfo=UTC)
    quick_captured_at = captured_at.replace(hour=1)
    deep_latest_captured_at = captured_at.replace(hour=2)
    calls: list[tuple[str, object]] = []
    source_answers: list[dict[str, Any]] = [
        {
            "pub_id": "ans_newest",
            "query_text": "问题一",
            "model": "doubao",
            "region": "北京",
            "mode": "deep_think",
            "eligible": True,
            "degraded": False,
            "capture_time": deep_latest_captured_at,
        },
        {
            "pub_id": "ans_oldest",
            "query_text": "问题一",
            "model": "doubao",
            "region": "北京",
            "mode": "deep_think",
            "eligible": True,
            "degraded": False,
            "capture_time": captured_at,
        },
        {
            "pub_id": "ans_quick",
            "query_text": "问题一",
            "model": "doubao",
            "region": "北京",
            "mode": "normal",
            "eligible": True,
            "degraded": False,
            "capture_time": quick_captured_at,
        },
        {
            "pub_id": "ans_ineligible",
            "query_text": "问题一",
            "model": "doubao",
            "region": "北京",
            "mode": "normal",
            "eligible": False,
            "degraded": False,
            "capture_time": captured_at.replace(hour=3),
        },
        {
            "pub_id": "ans_degraded_only_cell",
            "query_text": "问题二",
            "model": "doubao",
            "region": "北京",
            "mode": "deep_think",
            "eligible": True,
            "degraded": True,
            "capture_time": captured_at.replace(hour=4),
        },
    ]

    class Result:
        def __init__(self, *, rows: list[dict[str, object]] | None = None) -> None:
            self.rows = rows or []

        def fetchall(self) -> list[dict[str, object]]:
            return self.rows

        def fetchone(self) -> dict[str, object] | None:
            return self.rows[0] if self.rows else None

    class Connection:
        def execute(self, sql: str, params: object = None) -> Result:
            calls.append((sql, params))
            if "FROM platform.tenant WHERE" in sql:
                return Result(rows=[{"id": "00000000-0000-0000-0000-000000000001"}])
            if "set_config('app.tenant_id'" in sql:
                return Result()
            if "FROM platform.answer_library_catalog" in sql:
                return Result(
                    rows=[
                        {
                            "catalog_config_pub_id": "cfv_7",
                            "campaign_started_at": captured_at,
                        }
                    ]
                )
            if "FROM platform.monitoring_config_version" in sql:
                return Result(
                    rows=[
                        _row(8, ["问题一"], mode="normal"),
                        _row(7, ["问题一", "问题二"]),
                    ]
                )
            if "FROM analytics.answer" in sql:
                assert "eligible IS TRUE AND degraded IS FALSE" in sql
                grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
                for answer in source_answers:
                    if answer["eligible"] is not True or answer["degraded"] is not False:
                        continue
                    key = (
                        str(answer["query_text"]),
                        str(answer["model"]),
                        str(answer["region"]),
                        str(answer["mode"]),
                    )
                    grouped.setdefault(key, []).append(answer)
                rows: list[dict[str, object]] = []
                for (query_text, model, region, mode), answers in grouped.items():
                    answers.sort(
                        key=lambda answer: (answer["capture_time"], answer["pub_id"]),
                        reverse=True,
                    )
                    rows.append(
                        {
                            "query_text": query_text,
                            "model": model,
                            "region": region,
                            "mode": mode,
                            "completed_samples": len(answers),
                            "latest_capture_time": answers[0]["capture_time"],
                            "answer_pub_ids": [answer["pub_id"] for answer in answers],
                            "answer_capture_times": [answer["capture_time"] for answer in answers],
                        }
                    )
                return Result(rows=rows)
            if "AS live_runs" in sql:
                return Result(rows=[{"live_runs": 1}])
            raise AssertionError(sql)

    @contextmanager
    def fake_tenant_connection(*_args: object, **_kwargs: object):
        yield Connection()

    monkeypatch.setattr(analytics_router, "tenant_connection", fake_tenant_connection)
    monkeypatch.setattr(analytics_router, "_dsn", lambda: "postgresql://unused")
    result = analytics_router.sampling_progress(
        "prj_test",
        page=1,
        page_size=100,
        principal=Principal(
            subject="test",
            role=Role.ADMIN,
            tenant_pub_id="tnt_test",
            user_pub_id="usr_test",
        ),
    )

    assert result.config_revision_start == 7
    assert result.config_revision_end == 8
    assert result.answer_count == 3
    assert result.observed_cells == 1
    assert result.total_cells == 2
    assert result.live_runs == 1
    assert result.page.model_dump() == {
        "page": 1,
        "page_size": 100,
        "total_count": 2,
        "total_pages": 1,
    }
    assert len(result.columns) == 1
    assert result.columns[0].mode == "deep_think"
    assert result.columns[0].modes == ["deep_think", "normal"]
    cell = result.rows[0].cells[0]
    assert cell.completed_samples == 3
    assert cell.latest_capture_time == deep_latest_captured_at
    assert cell.answer_pub_ids == ["ans_newest", "ans_quick", "ans_oldest"]
    assert result.rows[1].cells == []
    assert {"ans_ineligible", "ans_degraded_only_cell"}.isdisjoint(cell.answer_pub_ids)
    assert [
        (breakdown.mode, breakdown.completed_samples, breakdown.answer_pub_ids)
        for breakdown in cell.mode_breakdown
    ] == [
        ("deep_think", 2, ["ans_newest", "ans_oldest"]),
        ("normal", 1, ["ans_quick"]),
    ]
    answer_sql = next(sql for sql, _ in calls if "FROM analytics.answer" in sql)
    assert "array_agg(pub_id ORDER BY capture_time DESC,pub_id DESC)" in answer_sql
    assert "array_agg(capture_time ORDER BY capture_time DESC,pub_id DESC)" in answer_sql
    assert "eligible IS TRUE AND degraded IS FALSE" in answer_sql
    assert any("set_config('app.tenant_id'" in sql for sql, _ in calls)
    config_sql = next(sql for sql, _ in calls if "FROM platform.monitoring_config_version" in sql)
    assert "LIMIT 100" not in config_sql
    assert any("FROM platform.answer_library_catalog" in sql for sql, _ in calls)
    config_params = next(params for sql, params in calls if sql == config_sql)
    assert config_params == ("tnt_test", "prj_test", captured_at, captured_at)

    second_page = analytics_router.sampling_progress(
        "prj_test",
        page=2,
        page_size=1,
        principal=Principal(
            subject="test",
            role=Role.ADMIN,
            tenant_pub_id="tnt_test",
            user_pub_id="usr_test",
        ),
    )
    assert second_page.page.model_dump() == {
        "page": 2,
        "page_size": 1,
        "total_count": 2,
        "total_pages": 2,
    }
    assert [row.query_text for row in second_page.rows] == ["问题二"]
    assert second_page.total_cells == 2
    assert second_page.answer_count == 3
