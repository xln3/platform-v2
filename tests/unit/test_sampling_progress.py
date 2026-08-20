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
                "modes": [mode],
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
    assert [config.revision for config in campaign] == [44, 43, 42, 41]
    assert [
        (column.model, column.region, column.mode) for column in sampling_columns(campaign)
    ] == [
        ("doubao", "北京", "normal"),
        ("doubao", "上海", "normal"),
        ("deepseek", "北京", "deep_think"),
        ("deepseek", "上海", "deep_think"),
    ]


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
    calls: list[tuple[str, object]] = []

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
            if "FROM platform.monitoring_config_version" in sql:
                return Result(rows=[_row(7, ["问题一", "问题二"])])
            if "FROM analytics.answer" in sql:
                return Result(
                    rows=[
                        {
                            "query_text": "问题一",
                            "model": "doubao",
                            "region": "北京",
                            "mode": "deep_think",
                            "completed_samples": 2,
                            "latest_capture_time": captured_at,
                            "answer_pub_ids": ["ans_newest", "ans_oldest"],
                        }
                    ]
                )
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
        Principal(
            subject="test",
            role=Role.ADMIN,
            tenant_pub_id="tnt_test",
            user_pub_id="usr_test",
        ),
    )

    assert result.config_revision_start == 7
    assert result.config_revision_end == 7
    assert result.answer_count == 2
    assert result.observed_cells == 1
    assert result.total_cells == 2
    assert result.live_runs == 1
    assert result.rows[0].cells[0].latest_capture_time == captured_at
    assert result.rows[0].cells[0].answer_pub_ids == ["ans_newest", "ans_oldest"]
    answer_sql = next(sql for sql, _ in calls if "FROM analytics.answer" in sql)
    assert "array_agg(pub_id ORDER BY capture_time DESC,pub_id DESC)" in answer_sql
    assert any("set_config('app.tenant_id'" in sql for sql, _ in calls)
    config_sql = next(sql for sql, _ in calls if "FROM platform.monitoring_config_version" in sql)
    assert "LIMIT 100" not in config_sql
