"""delta 防稀释（config_version 过滤）单元测试。

粒度核查结论（钉住设计依据）：metric_daily.dimensions JSONB 自 INV-1 fanout 起
携带 ``config_version_pub_id`` 键（workflows/activities/collection.py
``_analysis_dimensions`` 盖章），即 metric_daily 本身已按冻结配置分桶——
``previous_period_delta(config_version=...)`` 复用 ``aggregate`` 的
``dimensions @>`` 过滤，无需回退 analytics.answer 实时聚合，且与不过滤时走
完全同一条聚合代码路径（含只排显式 ineligible 的读纪律）。

fake 连接按 aggregate 的 SQL 语义（窗口 + jsonb 包含 + ineligible 排除 + 分组
求和）在 Python 侧模拟 metric_daily，绝不打真 DB。覆盖：
- 不传 config_version → 过滤器为空对象（行为与旧实现一致）；
- 传 config_version → 过滤器精确为 {"config_version_pub_id": "<pub_id>"}，
  两个窗口（本期/前一等长窗口）都施加同一过滤；
- 其他配置/无配置键的行不计入；显式 ineligible 的行仍被排除；
- delta 数学：current/previous/delta 只由命中行算出。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from geo_platform.analytics import service as service_module
from geo_platform.analytics.service import AnalyticsService

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


def _metric_row(
    metric: str,
    day: date,
    *,
    numerator: int,
    denominator: int,
    value: Decimal | None = None,
    dimensions: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "metric_name": metric,
        "metric_date": day,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "state": "ready",
        "metric_version": "mv1",
        "scorer_version": "sv1",
        "trace_token": f"trace-{metric}",
        "dimensions": dimensions or {},
    }


class _MetricDailyFakeConnection:
    """按 aggregate() 的 SQL 语义在 Python 侧模拟 analytics.metric_daily。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        params = params or ()
        self.calls.append((" ".join(sql.split()), params))
        if "FROM analytics.metric_daily" not in sql:
            raise AssertionError(f"unexpected SQL: {sql}")
        tenant_pub_id, project_pub_id, start, end, dimensions_json, ineligible_json = params
        required = json.loads(dimensions_json)
        ineligible = json.loads(ineligible_json)

        def matches(row: dict[str, Any]) -> bool:
            if row["metric_date"] < start or row["metric_date"] > end:
                return False
            dimensions = row["dimensions"]
            if any(dimensions.get(key) != value for key, value in required.items()):
                return False
            # NOT (dimensions @> '{"eligible":"false"}')：只排显式不合格。
            return not all(
                dimensions.get(key) == value for key, value in ineligible.items()
            )

        groups: dict[str, dict[str, Any]] = {}
        for row in self._rows:
            if row["metric_name"] is None or not matches(row):
                continue
            key = f"{row['metric_name']}|{row['metric_version']}|{row['scorer_version']}"
            group = groups.setdefault(
                key,
                {
                    "metric_name": row["metric_name"],
                    "numerator": 0,
                    "denominator": 0,
                    "weighted_value_sum": Decimal(0),
                    "has_value": False,
                    "is_experimental": False,
                    "metric_version": row["metric_version"],
                    "scorer_version": row["scorer_version"],
                    "trace_tokens": [],
                },
            )
            group["numerator"] += row["numerator"]
            group["denominator"] += row["denominator"]
            if row["value"] is not None:
                group["has_value"] = True
                group["weighted_value_sum"] += row["value"] * row["denominator"]
            group["is_experimental"] = group["is_experimental"] or row["state"] == "experimental"
            if row["trace_token"] not in group["trace_tokens"]:
                group["trace_tokens"].append(row["trace_token"])
        return _Result(
            [
                {
                    **group,
                    "weighted_value_sum": (
                        group["weighted_value_sum"] if group["has_value"] else None
                    ),
                }
                for group in groups.values()
            ]
        )


def _delta(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    config_version: str | None = None,
    start: date = date(2026, 8, 8),
    end: date = date(2026, 8, 9),
) -> tuple[dict[str, Any], _MetricDailyFakeConnection]:
    connection = _MetricDailyFakeConnection(rows)

    class _FakeTenantConnection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _MetricDailyFakeConnection:
            return connection

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(service_module, "tenant_connection", _FakeTenantConnection)
    result = AnalyticsService(dsn="postgresql://fake").previous_period_delta(
        tenant_pub_id=_TENANT,
        project_pub_id=_PROJECT,
        start=start,
        end=end,
        config_version=config_version,
    )
    return result, connection


def test_delta_without_config_version_keeps_legacy_empty_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _metric_row("mention_rate", date(2026, 8, 8), numerator=3, denominator=4),
        _metric_row("mention_rate", date(2026, 8, 6), numerator=1, denominator=2),
    ]
    result, connection = _delta(monkeypatch, rows)
    filters = [json.loads(params[4]) for _sql, params in connection.calls]
    assert filters == [{}, {}]  # 本期 + 前一窗口，均不过滤
    assert result["mention_rate"]["current"] == Decimal(3) / Decimal(4)
    assert result["mention_rate"]["previous"] == Decimal(1) / Decimal(2)
    assert result["mention_rate"]["delta"] == Decimal(3) / Decimal(4) - Decimal(1) / Decimal(2)


def test_delta_with_config_version_filters_both_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        # 命中 cfg_a 的行：本期 2/4，前期 1/4。
        _metric_row(
            "mention_rate",
            date(2026, 8, 8),
            numerator=2,
            denominator=4,
            dimensions={"config_version_pub_id": "cfg_a", "model": "doubao"},
        ),
        _metric_row(
            "mention_rate",
            date(2026, 8, 6),
            numerator=1,
            denominator=4,
            dimensions={"config_version_pub_id": "cfg_a"},
        ),
        # 其他配置 / 无配置键的行绝不计入（防稀释核心断言）。
        _metric_row(
            "mention_rate",
            date(2026, 8, 8),
            numerator=40,
            denominator=40,
            dimensions={"config_version_pub_id": "cfg_b"},
        ),
        _metric_row(
            "mention_rate",
            date(2026, 8, 8),
            numerator=40,
            denominator=40,
            dimensions={"model": "doubao"},
        ),
        # 显式 ineligible 的命中行仍被排除（INV-1 读纪律不变）。
        _metric_row(
            "mention_rate",
            date(2026, 8, 8),
            numerator=9,
            denominator=9,
            dimensions={"config_version_pub_id": "cfg_a", "eligible": "false"},
        ),
    ]
    result, connection = _delta(monkeypatch, rows, config_version="cfg_a")
    filters = [json.loads(params[4]) for _sql, params in connection.calls]
    assert filters == [
        {"config_version_pub_id": "cfg_a"},
        {"config_version_pub_id": "cfg_a"},
    ]
    assert result["mention_rate"]["current"] == Decimal("0.5")
    assert result["mention_rate"]["previous"] == Decimal("0.25")
    assert result["mention_rate"]["delta"] == Decimal("0.25")


def test_delta_with_config_version_without_matching_rows_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _metric_row(
            "mention_rate",
            date(2026, 8, 8),
            numerator=1,
            denominator=1,
            dimensions={"config_version_pub_id": "cfg_b"},
        ),
    ]
    result, _connection = _delta(monkeypatch, rows, config_version="cfg_nonexistent")
    assert result == {}


def test_delta_average_rank_uses_weighted_sum_with_config_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _metric_row(
            "average_rank",
            date(2026, 8, 8),
            numerator=2,
            denominator=2,
            value=Decimal("2.5"),
            dimensions={"config_version_pub_id": "cfg_a"},
        ),
        _metric_row(
            "average_rank",
            date(2026, 8, 8),
            numerator=1,
            denominator=1,
            value=Decimal("9"),
            dimensions={"config_version_pub_id": "cfg_b"},
        ),
    ]
    result, _connection = _delta(monkeypatch, rows, config_version="cfg_a")
    assert result["average_rank"]["current"] == Decimal("2.5")
    # 前期窗口无命中行 → previous/delta 为 None（不发明数字）。
    assert result["average_rank"]["previous"] is None
    assert result["average_rank"]["delta"] is None
