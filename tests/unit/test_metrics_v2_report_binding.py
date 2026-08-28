from __future__ import annotations

from datetime import date

import pytest

from domain.reporting.metric_snapshot_binding import (
    MetricSnapshotBindingError,
    bind_metric_snapshot_set,
    frozen_metric_projection,
)

HASH = "a" * 64


def _document(*, state: str = "ready", value: float | None = 0.5) -> dict[str, object]:
    return {
        "snapshot_set_pub_id": "mss_report",
        "snapshot_set_hash": HASH,
        "state": "ready",
        "project_pub_id": "prj_report",
        "window": {"start": "2026-08-01", "end": "2026-08-27"},
        "filters": {"model": [], "region": [], "mode": []},
        "aggregation_method": "query_macro",
        "metrics": [
            {
                "snapshot_pub_id": "msn_report",
                "snapshot_hash": HASH,
                "focal_entity_id": "entity_target",
                "metric_name": "ai_recommendation_organic_mention_rate_v2",
                "metric_version": "2.0.0",
                "state": state,
                "value": value,
                "observed_value": 0.5,
                "raw_numerator": 2,
                "raw_denominator": 4,
                "unique_query_count": 3,
                "coverage": {"semantic": 1},
                "metric_definition_hash": HASH,
                "contribution_set_hash": HASH,
                "query_contribution_set_hash": HASH,
                "design_contribution_set_hash": HASH,
            }
        ],
    }


def _bind(document: dict[str, object]):
    return bind_metric_snapshot_set(
        document,
        expected_project_pub_id="prj_report",
        expected_set_pub_id="mss_report",
        expected_set_hash=HASH,
        expected_window_start=date(2026, 8, 1),
        expected_window_end=date(2026, 8, 27),
        expected_filters={"model": [], "region": [], "mode": []},
        required_metric_names=["ai_recommendation_organic_mention_rate_v2"],
    )


def test_formal_projection_is_bound_to_exact_snapshot_set_and_member_hashes() -> None:
    binding = _bind(_document())
    projection = frozen_metric_projection(binding)
    assert projection["metric_snapshot_set_pub_id"] == "mss_report"
    assert projection["metric_snapshot_set_hash"] == HASH
    assert projection["aggregation_method"] == "query_macro"
    assert projection["metrics"] == [
        {
            "snapshot_pub_id": "msn_report",
            "snapshot_hash": HASH,
            "focal_entity_id": "entity_target",
            "metric_name": "ai_recommendation_organic_mention_rate_v2",
            "metric_version": "2.0.0",
            "state": "ready",
            "value": "0.5",
            "observed_value": "0.5",
            "raw_numerator": "2",
            "raw_denominator": "4",
            "unique_query_count": 3,
            "semantic_coverage": "1",
            "definition_hash": HASH,
            "contribution_set_hash": HASH,
            "query_contribution_set_hash": HASH,
            "design_contribution_set_hash": HASH,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("snapshot_set_pub_id", "mss_other", "metric_snapshot_set_id_mismatch"),
        ("snapshot_set_hash", "b" * 64, "metric_snapshot_set_hash_mismatch"),
        ("project_pub_id", "prj_other", "metric_snapshot_set_project_mismatch"),
    ],
)
def test_report_rejects_snapshot_identity_scope_or_hash_drift(
    field: str, value: str, code: str
) -> None:
    document = _document()
    document[field] = value
    with pytest.raises(MetricSnapshotBindingError, match=code):
        _bind(document)


@pytest.mark.parametrize("state", ["insufficient", "experimental", "failed"])
def test_report_refuses_conclusions_for_non_publishable_metric(state: str) -> None:
    with pytest.raises(MetricSnapshotBindingError, match="required_members_not_publishable"):
        _bind(_document(state=state, value=None))


def test_non_publishable_metric_cannot_smuggle_a_formal_value() -> None:
    with pytest.raises(MetricSnapshotBindingError, match="non_publishable_metric_value_present"):
        _bind(_document(state="insufficient", value=0.5))


def test_failed_snapshot_set_is_not_renderable() -> None:
    document = _document()
    document["state"] = "failed"
    with pytest.raises(MetricSnapshotBindingError, match="metric_snapshot_set_not_renderable"):
        _bind(document)


def test_member_snapshot_hash_is_required_and_validated() -> None:
    document = _document()
    metrics = document["metrics"]
    assert isinstance(metrics, list) and isinstance(metrics[0], dict)
    metrics[0]["snapshot_hash"] = "invalid"
    with pytest.raises(MetricSnapshotBindingError, match="metric_snapshot_hash_invalid"):
        _bind(document)
