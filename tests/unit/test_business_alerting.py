import pytest
from geo_platform.alert_receiver import safe_alert_projection
from geo_platform.business_metrics import ADMISSION_REASONS, BusinessMetricsSnapshot


def test_alert_receiver_projects_only_bounded_non_sensitive_labels() -> None:
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
                        "description": "must-not-be-logged",
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
        }
    ]


def test_alert_receiver_rejects_non_alertmanager_payload() -> None:
    with pytest.raises(ValueError, match="invalid_alertmanager_payload"):
        safe_alert_projection({"status": "firing"})


def test_business_snapshot_has_a_fixed_low_cardinality_admission_matrix() -> None:
    snapshot = BusinessMetricsSnapshot()
    assert tuple(snapshot.analysis_admission_backlog) == ADMISSION_REASONS
    assert set(snapshot.analysis_admission_backlog.values()) == {0}
