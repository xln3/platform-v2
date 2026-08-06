from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any

from .clickhouse import ClickHouseWriter


class AnalyticsProjection:
    def __init__(self, writer: ClickHouseWriter) -> None:
        self.writer = writer

    def publish(self, event: Mapping[str, Any]) -> None:
        if event["event_type"] == "disparagement.recorded":
            # W3 拉踩检测：一条窗级判定一行 fact（evidence_quote 留在 PG，
            # 只投影维度与判定结果）。
            payload = event["payload"]
            self.writer.insert_json_each_row(
                "geo_analytics.disparagement_fact",
                [
                    {
                        "tenant_pub_id": event["tenant_pub_id"],
                        "project_pub_id": payload["project_pub_id"],
                        "run_pub_id": payload["run_pub_id"],
                        "judgment_pub_id": payload["judgment_pub_id"],
                        "subject_type": payload["subject_type"],
                        "subject_pub_id": payload["subject_pub_id"],
                        "platform": payload["platform"],
                        "subject_brand": payload["subject_brand"],
                        "target_brand": payload["target_brand"],
                        "attitude": payload["attitude"],
                        "disparagement": int(payload["disparagement"]),
                        "confidence": float(payload["confidence"]),
                        "method": payload["method"],
                        "model": payload["model"],
                        "prompt_version": payload["prompt_version"],
                        "judgment_status": payload["judgment_status"],
                        "event_time": datetime.fromisoformat(
                            payload["event_time"].replace("Z", "+00:00")
                        ),
                        "event_id": event["event_id"],
                    }
                ],
            )
            return
        if event["event_type"] == "source_audit.recorded":
            # W2 信源准确性核对：一条 source_audit 判定一行 fact（quote/rationale 留在 PG，
            # 只投影维度与判定结果）。
            payload = event["payload"]
            self.writer.insert_json_each_row(
                "geo_analytics.source_audit_fact",
                [
                    {
                        "tenant_pub_id": event["tenant_pub_id"],
                        "project_pub_id": payload["project_pub_id"],
                        "run_pub_id": payload["run_pub_id"],
                        "source_document_pub_id": payload["source_document_pub_id"],
                        "source_audit_pub_id": payload["source_audit_pub_id"],
                        "url": payload["url"],
                        "host": payload["host"],
                        "dimension": payload["dimension"],
                        "verdict": payload["verdict"],
                        "audit_status": payload["audit_status"],
                        "model": payload["model"],
                        "prompt_version": payload["prompt_version"],
                        "event_time": datetime.fromisoformat(
                            payload["event_time"].replace("Z", "+00:00")
                        ),
                        "event_id": event["event_id"],
                    }
                ],
            )
            return
        if event["event_type"] == "intelligence.feature.recorded":
            payload = event["payload"]
            self.writer.insert_json_each_row(
                "geo_analytics.feature_fact",
                [
                    {
                        "tenant_pub_id": event["tenant_pub_id"],
                        "investigation_pub_id": payload["investigation_pub_id"],
                        "subject_pub_id": payload["subject_pub_id"],
                        "event_id": event["event_id"],
                        "feature_name": payload["feature_name"],
                        "feature_value": payload["feature_value"],
                        "rule_version": payload["rule_version"],
                        "model_version": payload["model_version"],
                        "event_time": datetime.fromisoformat(
                            payload["event_time"].replace("Z", "+00:00")
                        ),
                    }
                ],
            )
            return
        if event["event_type"] != "analytics.answer.analyzed":
            raise ValueError(f"unsupported analytics event: {event['event_type']}")
        payload = event["payload"]
        dimensions = payload["dimensions"]
        # ClickHouse receives only explicitly controlled dimensions. Account/profile/session
        # provenance remains in PostgreSQL and is never projected.
        allowed_dimensions = {
            key: str(dimensions.get(key, "")) for key in ("model", "region", "mode", "channel")
        }
        self.writer.insert_json_each_row(
            "geo_analytics.answer_fact",
            [
                {
                    "tenant_pub_id": event["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "answer_pub_id": payload["answer_pub_id"],
                    "run_pub_id": str(payload.get("run_pub_id", "")),
                    "query_pub_id": str(payload.get("query_pub_id", "")),
                    "event_time": datetime.fromisoformat(
                        payload["event_time"].replace("Z", "+00:00")
                    ),
                    **allowed_dimensions,
                    "account_dimension_opaque": None,
                    "mentioned": int(payload["mentioned"]),
                    "rank": payload["rank"],
                    "sentiment": payload["sentiment"] or "unknown",
                    "recommended": None,
                    "citation_count": payload["citation_count"],
                    "scorer_version": payload["scorer_version"],
                    "metric_version": payload["metric_version"],
                    "input_hash": payload["input_hash"],
                    "event_id": event["event_id"],
                }
            ],
        )
        self.writer.insert_json_each_row(
            "geo_analytics.citation_fact",
            [
                {
                    "tenant_pub_id": event["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "answer_pub_id": payload["answer_pub_id"],
                    "citation_pub_id": citation["citation_pub_id"],
                    "event_time": datetime.fromisoformat(
                        payload["event_time"].replace("Z", "+00:00")
                    ),
                    "canonical_host": citation["canonical_host"],
                    "canonical_url": citation["canonical_url"],
                    "content_hash": citation["content_hash"],
                    "own_source": int(citation["own_source"]),
                    "event_id": event["event_id"],
                }
                for citation in payload.get("citations", [])
            ],
        )
        self.writer.insert_json_each_row(
            "geo_analytics.run_event",
            [
                {
                    "tenant_pub_id": event["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "run_pub_id": payload.get("run_pub_id") or payload["analysis_run_pub_id"],
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "event_time": datetime.fromisoformat(
                        payload["event_time"].replace("Z", "+00:00")
                    ),
                    "status": "ready",
                    "adapter_version": "",
                    "payload_json": "{}",
                }
            ],
        )
        self.writer.insert_json_each_row(
            "geo_analytics.metric_daily",
            [
                {
                    "tenant_pub_id": event["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "metric_date": payload["event_time"][:10],
                    "metric_name": metric["metric_name"],
                    "dimensions_hash": metric["dimensions_hash"],
                    "dimensions_json": _canonical_json(metric["dimensions"]),
                    "value": metric["value"],
                    "numerator": metric["numerator"],
                    "denominator": metric["denominator"],
                    "state": metric["state"],
                    "metric_version": payload["metric_version"],
                    "scorer_version": payload["scorer_version"],
                    "trace_token": metric["trace_token"],
                    "updated_at": datetime.fromisoformat(
                        payload["event_time"].replace("Z", "+00:00")
                    ),
                }
                for metric in payload.get("metrics", [])
            ],
        )

    def consistency_hash(self, *, event_id: str) -> str:
        count = self.writer.count_event("geo_analytics.answer_fact", event_id)
        return sha256(f"{event_id}:{count}".encode()).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
