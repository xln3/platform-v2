import pytest
from geo_platform.alert_receiver import safe_alert_projection
from geo_platform.business_metrics import ADMISSION_REASONS, BusinessMetricsSnapshot


def test_alert_receiver_projects_only_whitelisted_fields_and_redacts_annotations() -> None:
    projected = safe_alert_projection(
        {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "GeoCollectionAnalysisAdmissionBlocked",
                        "severity": "warning",
                        "category": "analysis",
                        "service": "s02-worker",
                        "tenant_pub_id": "must-not-be-logged",
                        "authorization": "must-not-be-logged",
                    },
                    "annotations": {
                        "description": (
                            "authorization=secret-token contact ops at "
                            "https://internal.invalid/alert"
                        ),
                    },
                }
            ]
        }
    )
    assert projected == [
        {
            "status": "firing",
            "alertname": "GeoCollectionAnalysisAdmissionBlocked",
            "severity": "warning",
            "category": "analysis",
            "service": "s02-worker",
            "description": "[credential redacted] contact ops at [link redacted]",
        }
    ]


def test_alert_receiver_rejects_non_alertmanager_payload() -> None:
    with pytest.raises(ValueError, match="invalid_alertmanager_payload"):
        safe_alert_projection({"status": "firing"})


def test_business_snapshot_has_a_fixed_low_cardinality_admission_matrix() -> None:
    snapshot = BusinessMetricsSnapshot()
    assert tuple(snapshot.analysis_admission_backlog) == ADMISSION_REASONS
    assert set(snapshot.analysis_admission_backlog.values()) == {0}


def test_business_snapshot_has_fixed_low_cardinality_outbox_matrices() -> None:
    from geo_platform.analytics.outbox import ANALYTICS_EVENT_TYPES
    from geo_platform.business_metrics import OUTBOX_EVENT_TYPE_LABELS

    # 事件词表单源=ANALYTICS_EVENT_TYPES；unknown 是钳位桶
    assert set(OUTBOX_EVENT_TYPE_LABELS) == {*ANALYTICS_EVENT_TYPES, "unknown"}
    snapshot = BusinessMetricsSnapshot()
    assert tuple(snapshot.analytics_outbox_backlog) == OUTBOX_EVENT_TYPE_LABELS
    assert tuple(snapshot.analytics_outbox_quarantined) == OUTBOX_EVENT_TYPE_LABELS
    assert set(snapshot.analytics_outbox_backlog.values()) == {0}
    assert set(snapshot.analytics_outbox_quarantined.values()) == {0}


def test_outbox_alerts_are_wired_to_exporter_gauges() -> None:
    """business-alerts.yaml 的两条新告警表达式必须引用 exporter 真实暴露的
    gauge 名（否则告警永远取不到数——2026-08-08 复核要求指标可溯源）。"""
    from pathlib import Path

    import yaml
    from geo_platform.business_metrics import (
        ANALYTICS_OUTBOX_BACKLOG,
        ANALYTICS_OUTBOX_QUARANTINED,
    )

    rules_path = (
        Path(__file__).resolve().parents[2] / "deploy/production/observability/business-alerts.yaml"
    )
    document = yaml.safe_load(rules_path.read_text())
    alerts = {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}
    backlog = alerts["GeoAnalyticsOutboxBacklog"]
    quarantined = alerts["GeoAnalyticsOutboxQuarantined"]
    assert ANALYTICS_OUTBOX_BACKLOG._name in backlog["expr"]
    assert ANALYTICS_OUTBOX_QUARANTINED._name in quarantined["expr"]
    assert quarantined["labels"]["severity"] == "critical"
