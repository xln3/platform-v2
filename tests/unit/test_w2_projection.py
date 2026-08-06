"""W2 analytics 通道单元测试：outbox 事件类型登记 + projection 新分支。

照 tests 既有 projection 测试模式：fake ClickHouseWriter 记录 insert 调用，
断言 source_audit.recorded 事件只投影受控字段（quote/rationale 留 PG）。
"""

from __future__ import annotations

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
    return {
        "event_id": "evt_source_audit_test",
        "tenant_pub_id": "tnt_0123456789abcdef",
        "event_type": "source_audit.recorded",
        "payload": {
            "project_pub_id": "prj_0123456789abcdef",
            "run_pub_id": "run_0123456789abcdef",
            "source_document_pub_id": "srd_0123456789abcdef01234567",
            "source_audit_pub_id": "sra_0123456789abcdef01234567",
            "url": "https://a.example.com/article",
            "host": "a.example.com",
            "dimension": "transcript",
            "verdict": "accurate",
            "audit_status": "ok",
            "model": "gpt-5.6-luna",
            "prompt_version": "source-audit-v1",
            "event_time": "2026-08-05T12:30:00+00:00",
        },
    }


def test_source_audit_event_type_registered_in_outbox() -> None:
    assert "source_audit.recorded" in ANALYTICS_EVENT_TYPES


def test_projection_routes_source_audit_event_to_fact_table() -> None:
    writer = _FakeWriter()
    AnalyticsProjection(writer).publish(_event())  # type: ignore[arg-type]
    assert len(writer.inserts) == 1
    table, rows = writer.inserts[0]
    assert table == "geo_analytics.source_audit_fact"
    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_pub_id"] == "tnt_0123456789abcdef"
    assert row["project_pub_id"] == "prj_0123456789abcdef"
    assert row["run_pub_id"] == "run_0123456789abcdef"
    assert row["source_document_pub_id"] == "srd_0123456789abcdef01234567"
    assert row["source_audit_pub_id"] == "sra_0123456789abcdef01234567"
    assert row["dimension"] == "transcript"
    assert row["verdict"] == "accurate"
    assert row["audit_status"] == "ok"
    assert row["model"] == "gpt-5.6-luna"
    assert row["prompt_version"] == "source-audit-v1"
    assert row["event_id"] == "evt_source_audit_test"
    assert row["event_time"].year == 2026
    # quote/rationale 不投影（留 PG 供复查，CH 只承载分布维度）
    assert "quote_source" not in row and "quote_answer" not in row and "rationale" not in row


def test_projection_rejects_unknown_event_type() -> None:
    writer = _FakeWriter()
    event = _event()
    event["event_type"] = "source_audit.unknown"
    with pytest.raises(ValueError, match="unsupported analytics event"):
        AnalyticsProjection(writer).publish(event)  # type: ignore[arg-type]
    assert writer.inserts == []
