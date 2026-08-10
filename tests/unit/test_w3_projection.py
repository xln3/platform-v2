"""W3 analytics 通道单元测试：outbox 事件类型登记 + projection 新分支。

照 tests/unit/test_w2_projection.py 模式：fake ClickHouseWriter 记录 insert 调用，
断言 disparagement.recorded 事件只投影受控字段（evidence_quote 留 PG）。
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
        "event_id": "evt_disparagement_test",
        "tenant_pub_id": "tnt_0123456789abcdef",
        "event_type": "disparagement.recorded",
        "payload": {
            "project_pub_id": "prj_0123456789abcdef",
            "run_pub_id": "run_0123456789abcdef",
            "judgment_pub_id": "dpj_0123456789abcdef01234567",
            "subject_type": "answer",
            "subject_pub_id": "ans_0123456789abcdef01234567",
            "platform": "doubao",
            "source_url": "",
            "subject_brand": "",
            "target_brand": "友邦",
            "attitude": "negative",
            "disparagement": True,
            "confidence": 0.9,
            "method": "llm",
            "model": "gpt-5.6-luna",
            "prompt_version": "disparage-v2",
            "judgment_status": "ok",
            "event_time": "2026-08-05T12:30:00+00:00",
        },
    }


def test_disparagement_event_type_registered_in_outbox() -> None:
    assert "disparagement.recorded" in ANALYTICS_EVENT_TYPES


def test_projection_routes_disparagement_event_to_fact_table() -> None:
    writer = _FakeWriter()
    AnalyticsProjection(writer).publish(_event())  # type: ignore[arg-type]
    assert len(writer.inserts) == 1
    table, rows = writer.inserts[0]
    assert table == "geo_analytics.disparagement_fact"
    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_pub_id"] == "tnt_0123456789abcdef"
    assert row["project_pub_id"] == "prj_0123456789abcdef"
    assert row["run_pub_id"] == "run_0123456789abcdef"
    assert row["judgment_pub_id"] == "dpj_0123456789abcdef01234567"
    assert row["subject_type"] == "answer"
    assert row["platform"] == "doubao"
    assert row["target_brand"] == "友邦"
    assert row["attitude"] == "negative"
    assert row["disparagement"] == 1
    assert row["confidence"] == pytest.approx(0.9)
    assert row["method"] == "llm"
    assert row["prompt_version"] == "disparage-v2"
    assert row["judgment_status"] == "ok"
    assert row["event_id"] == "evt_disparagement_test"
    assert row["event_time"].year == 2026
    # evidence_quote 不投影（留 PG 供复查，CH 只承载分布维度）
    assert "evidence_quote" not in row and "source_url" not in row
