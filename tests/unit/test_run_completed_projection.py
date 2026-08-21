"""collection.run.completed 进分析消费词表 + run_event 投影单测。

照 test_w2_projection.py 既有模式：fake ClickHouseWriter 记录 insert 调用，
断言只投影受控字段、无 project_pub_id 时诚实置空、occurred_at 回退为事件时间。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.analytics.outbox import ANALYTICS_EVENT_TYPES
from geo_platform.analytics.projection import AnalyticsProjection


class _FakeWriter:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_json_each_row(self, table: str, rows: Any) -> int:
        materialized = [dict(row) for row in rows]
        self.inserts.append((table, materialized))
        return len(materialized)


def _event() -> dict[str, Any]:
    # 生产者（workflows/activities/collection.py publish_downstream_event）冻结形状：
    # payload 无 project_pub_id / event_time，时间取自 outbox 行 occurred_at
    return {
        "event_id": "evt_run_completed_test",
        "tenant_pub_id": "tnt_0123456789abcdef",
        "event_type": "collection.run.completed",
        "occurred_at": datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
        "payload": {
            "run_pub_id": "run_0123456789abcdef",
            "workflow_id": "geo-collection/tnt_0123456789abcdef/prj_x/run_0123456789abcdef",
            "state": "completed_with_failures",
            "total_tasks": 10,
            "completed_tasks": 8,
            "failed_tasks": 2,
        },
    }


def test_run_completed_event_type_registered_in_outbox() -> None:
    assert "collection.run.completed" in ANALYTICS_EVENT_TYPES
    assert "answer.capture.completed" in ANALYTICS_EVENT_TYPES


def test_projection_routes_capture_completion_without_analysis_fields() -> None:
    writer = _FakeWriter()
    event = {
        "event_id": "evt_answer_capture_test",
        "tenant_pub_id": "tnt_0123456789abcdef",
        "event_type": "answer.capture.completed",
        "occurred_at": datetime(2026, 8, 5, 12, 31, tzinfo=UTC),
        "payload": {
            "answer_pub_id": "ans_0123456789abcdef",
            "project_pub_id": "prj_0123456789abcdef",
            "run_pub_id": "run_0123456789abcdef",
            "business_key": "query|model|region|mode",
            "capture_state": "completed",
            "quality_state": "accepted",
            "response_hash": "a" * 64,
        },
    }
    AnalyticsProjection(writer).publish(event)  # type: ignore[arg-type]
    table, rows = writer.inserts[0]
    assert table == "geo_analytics.run_event"
    assert rows[0]["status"] == "capture_completed"
    assert rows[0]["project_pub_id"] == "prj_0123456789abcdef"
    assert json.loads(rows[0]["payload_json"]) == {
        "answer_pub_id": "ans_0123456789abcdef",
        "business_key": "query|model|region|mode",
        "capture_state": "completed",
        "quality_state": "accepted",
        "response_hash": "a" * 64,
    }


def test_projection_routes_run_completed_to_run_event() -> None:
    writer = _FakeWriter()
    AnalyticsProjection(writer).publish(_event())  # type: ignore[arg-type]
    assert len(writer.inserts) == 1
    table, rows = writer.inserts[0]
    assert table == "geo_analytics.run_event"
    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_pub_id"] == "tnt_0123456789abcdef"
    assert row["project_pub_id"] == ""  # payload 无此字段：诚实置空，绝不编造
    assert row["run_pub_id"] == "run_0123456789abcdef"
    assert row["event_id"] == "evt_run_completed_test"
    assert row["event_type"] == "collection.run.completed"
    assert row["status"] == "completed_with_failures"  # status = payload run state
    assert row["adapter_version"] == ""
    assert row["event_time"] == datetime(2026, 8, 5, 12, 30, tzinfo=UTC)  # occurred_at 回退
    counts = json.loads(row["payload_json"])
    assert counts == {
        "completed_tasks": 8,
        "failed_tasks": 2,
        "state": "completed_with_failures",
        "total_tasks": 10,
        "workflow_id": "geo-collection/tnt_0123456789abcdef/prj_x/run_0123456789abcdef",
    }


def test_projection_run_completed_prefers_payload_event_time() -> None:
    writer = _FakeWriter()
    event = _event()
    event["payload"]["event_time"] = "2026-08-05T13:00:00+00:00"
    AnalyticsProjection(writer).publish(event)  # type: ignore[arg-type]
    _table, rows = writer.inserts[0]
    assert rows[0]["event_time"] == datetime(2026, 8, 5, 13, 0, tzinfo=UTC)


def test_projection_still_rejects_unknown_event_type() -> None:
    writer = _FakeWriter()
    event = _event()
    event["event_type"] = "collection.run.unknown"
    with pytest.raises(ValueError, match="unsupported analytics event"):
        AnalyticsProjection(writer).publish(event)  # type: ignore[arg-type]
    assert writer.inserts == []
