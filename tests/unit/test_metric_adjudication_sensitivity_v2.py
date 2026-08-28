from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from domain.metrics.v2.models import DecisionMethod, EligibilityStatus, MetricEvaluation
from domain.metrics.v2.weighting import (
    CalibrationErrorArtifact,
    WeightingInput,
    assign_query_macro_weights,
    calculate_adjudication_sensitivity,
    calculate_missing_bounds,
)


def _evaluation(index: int, status: EligibilityStatus, hit: bool) -> MetricEvaluation:
    return MetricEvaluation(
        answer_pub_id=f"ans_{index}",
        query_key=f"qry_{index}",
        focal_entity_id="brand_sbang",
        metric_name="fixture_rate_v2",
        metric_version="2.0.0",
        metric_definition_hash="a" * 64,
        eligibility_status=status,
        reason_codes=(status.value,),
        outcome_value=hit if status is not EligibilityStatus.ANALYSIS_UNKNOWN else None,
        numerator_contribution=(
            Decimal(int(hit)) if status is not EligibilityStatus.ANALYSIS_UNKNOWN else Decimal("0")
        ),
        denominator_contribution=(
            Decimal("1") if status is not EligibilityStatus.ANALYSIS_UNKNOWN else Decimal("0")
        ),
    )


def _weighted(include_unknown: bool = False):
    inputs = [
        WeightingInput(
            _evaluation(0, EligibilityStatus.INCLUDED_HIT, True),
            "cell_0",
            decision_method=DecisionMethod.MODEL,
        ),
        WeightingInput(
            _evaluation(1, EligibilityStatus.INCLUDED_MISS, False),
            "cell_1",
            decision_method=DecisionMethod.HYBRID,
        ),
    ]
    if include_unknown:
        inputs.append(
            WeightingInput(
                _evaluation(2, EligibilityStatus.ANALYSIS_UNKNOWN, False),
                "cell_2",
                decision_method=DecisionMethod.MODEL,
            )
        )
    return assign_query_macro_weights(inputs)


def _artifacts() -> tuple[CalibrationErrorArtifact, ...]:
    return (
        CalibrationErrorArtifact(
            artifact_hash="model-calibration-v1",
            method=DecisionMethod.MODEL,
            false_positive_upper_bound=Decimal("0.10"),
            false_negative_upper_bound=Decimal("0.20"),
        ),
        CalibrationErrorArtifact(
            artifact_hash="hybrid-calibration-v1",
            method=DecisionMethod.HYBRID,
            false_positive_upper_bound=Decimal("0.03"),
            false_negative_upper_bound=Decimal("0.08"),
        ),
    )


def test_sensitivity_applies_false_positive_to_hits_and_false_negative_to_misses() -> None:
    result = calculate_adjudication_sensitivity(_weighted(), _artifacts())
    assert result.lower == Decimal("0.45")
    assert result.upper == Decimal("0.54")
    assert result.calibration_artifact_hashes == (
        "hybrid-calibration-v1",
        "model-calibration-v1",
    )


def test_unknown_weight_changes_missing_bounds_but_not_adjudication_sensitivity() -> None:
    without_unknown = _weighted()
    with_unknown = _weighted(include_unknown=True)
    assert calculate_missing_bounds(without_unknown) != calculate_missing_bounds(with_unknown)
    assert calculate_adjudication_sensitivity(
        without_unknown, _artifacts()
    ) == calculate_adjudication_sensitivity(with_unknown, _artifacts())


def test_calibration_change_changes_sensitivity_but_not_missing_bounds() -> None:
    weighted = _weighted()
    artifacts = _artifacts()
    changed = (
        replace(artifacts[0], false_positive_upper_bound=Decimal("0.20")),
        artifacts[1],
    )
    assert calculate_adjudication_sensitivity(
        weighted, artifacts
    ) != calculate_adjudication_sensitivity(weighted, changed)
    assert calculate_missing_bounds(weighted) == calculate_missing_bounds(weighted)
