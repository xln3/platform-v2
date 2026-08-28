from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest
from geo_platform.metrics_v2.export import build_metrics_xlsx, spreadsheet_safe
from geo_platform.metrics_v2.schemas import MetricSnapshotView, SnapshotRequest
from pydantic import ValidationError

HASH = "a" * 64


def _snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "snapshot_pub_id": "msn_example",
        "snapshot_hash": HASH,
        "focal_entity_id": "entity-example",
        "metric_name": "ai_recommendation_organic_mention_rate_v2",
        "metric_version": "2.0.0",
        "metric_definition_hash": HASH,
        "state": "ready",
        "state_reason_codes": [],
        "value": 0.5,
        "observed_value": 0.5,
        "answer_weighted_value": 0.5,
        "raw_numerator": 2,
        "raw_denominator": 4,
        "weighted_numerator": 0.5,
        "weighted_denominator": 1,
        "coverage": {
            "collection": 1,
            "query_context": 1,
            "semantic": 1,
            "evidence": 1,
            "semantic_by_capability": {"substantive_entity_mention": 1},
        },
        "decision_method_mix": {"deterministic": 1},
        "adjudication_sensitivity": {"lower": 0.49, "upper": 0.51},
        "missing_bounds": {"lower": 0.5, "upper": 0.5},
        "unique_query_count": 12,
        "candidate_answer_count": 4,
        "known_answer_count": 4,
        "unknown_answer_count": 0,
        "not_applicable_answer_count": 0,
        "excluded_answer_count": 0,
        "design_cell_count": 4,
        "contribution_set_hash": HASH,
        "query_contribution_set_hash": HASH,
        "design_contribution_set_hash": HASH,
    }
    value.update(overrides)
    return value


def test_snapshot_request_is_shadow_only_and_canonicalizes_filters() -> None:
    request = SnapshotRequest.model_validate(
        {
            "window": {"start": date(2026, 8, 1), "end": date(2026, 8, 27)},
            "filters": {"model": ["豆包", "DeepSeek", "豆包"], "region": [], "mode": []},
            "focal_entity_ids": ["brand-target"],
            "aggregation_method": "query_macro",
            "publication_channel": "shadow",
        }
    )
    assert request.filters.model == ["DeepSeek", "豆包"]
    with pytest.raises(ValidationError):
        SnapshotRequest.model_validate(
            {
                **request.model_dump(),
                "publication_channel": "official",
            }
        )


@pytest.mark.parametrize("state", ["insufficient", "experimental", "failed"])
def test_non_publishable_snapshot_cannot_expose_formal_value(state: str) -> None:
    with pytest.raises(ValidationError):
        MetricSnapshotView.model_validate(_snapshot(state=state, value=0.5))
    accepted = MetricSnapshotView.model_validate(_snapshot(state=state, value=None))
    assert accepted.value is None


def test_metric_export_has_all_trace_sheets_and_formula_text_is_inert() -> None:
    bundle = {
        name: [{"value": '=HYPERLINK("https://invalid.example")', "hash": HASH}]
        for name in (
            "README",
            "METRICS",
            "QUERIES",
            "ANSWERS",
            "DECISIONS",
            "EVENTS",
            "EXCLUSIONS",
            "DESIGN_CELLS",
            "HASHES",
        )
    }
    payload = build_metrics_xlsx(bundle)
    with ZipFile(BytesIO(payload)) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        sheets = "".join(
            archive.read(name).decode()
            for name in archive.namelist()
            if name.startswith("xl/worksheets/")
        )
    assert all(name in workbook for name in bundle)
    assert "&#x27;=HYPERLINK" in sheets
    assert "<f>" not in sheets
    assert spreadsheet_safe("+SUM(A1:A2)") == "'+SUM(A1:A2)"
