from __future__ import annotations

from decimal import Decimal

import pytest

from domain.metrics.v2.models import EligibilityStatus, MetricEvaluation
from domain.metrics.v2.weighting import (
    WeightingInput,
    assign_query_macro_weights,
    calculate_answer_weighted_value,
)


def _evaluation(answer_id: str, query_key: str, hit: bool) -> MetricEvaluation:
    return MetricEvaluation(
        answer_pub_id=answer_id,
        query_key=query_key,
        focal_entity_id="brand_sbang",
        metric_name="fixture_rate_v2",
        metric_version="2.0.0",
        metric_definition_hash="a" * 64,
        eligibility_status=(
            EligibilityStatus.INCLUDED_HIT if hit else EligibilityStatus.INCLUDED_MISS
        ),
        reason_codes=("hit" if hit else "miss",),
        outcome_value=hit,
        numerator_contribution=Decimal(int(hit)),
        denominator_contribution=Decimal("1"),
    )


def _fixture(q1_repeats: int) -> tuple[MetricEvaluation, ...]:
    return tuple(
        [_evaluation(f"q1_a{index}", "q1", True) for index in range(q1_repeats)]
        + [_evaluation("q2_a1", "q2", False)]
    )


def _weighted(q1_repeats: int):
    return assign_query_macro_weights(
        WeightingInput(item, design_cell_key=f"{item.query_key}:default")
        for item in _fixture(q1_repeats)
    )


def test_repeated_answers_do_not_inflate_query_macro_weight() -> None:
    weighted = _weighted(10)
    q1 = [item for item in weighted if item.query_key == "q1"]
    q2 = [item for item in weighted if item.query_key == "q2"]
    assert all(item.final_weight == Decimal("0.05") for item in q1)
    assert q2[0].final_weight == Decimal("0.5")
    assert sum((item.final_weight for item in weighted), Decimal("0")) == 1
    numerator = sum((item.weighted_numerator for item in weighted), Decimal("0"))
    denominator = sum((item.weighted_denominator for item in weighted), Decimal("0"))
    assert numerator / denominator == Decimal("0.5")
    assert calculate_answer_weighted_value(_fixture(10)) == Decimal(10) / Decimal(11)


def test_another_ninety_repeats_change_composition_but_not_query_macro() -> None:
    weighted = _weighted(100)
    assert all(item.final_weight == Decimal("0.005") for item in weighted if item.query_key == "q1")
    numerator = sum((item.weighted_numerator for item in weighted), Decimal("0"))
    denominator = sum((item.weighted_denominator for item in weighted), Decimal("0"))
    assert numerator / denominator == Decimal("0.5")
    assert calculate_answer_weighted_value(_fixture(100)) == Decimal(100) / Decimal(101)


def test_explicit_weight_distributions_must_exactly_cover_and_sum_to_one() -> None:
    records = [WeightingInput(item, design_cell_key="cell") for item in _fixture(1)]
    with pytest.raises(ValueError, match="sum exactly"):
        assign_query_macro_weights(
            records,
            query_weights={"q1": Decimal("0.4"), "q2": Decimal("0.5")},
        )
    with pytest.raises(ValueError, match="cover exactly"):
        assign_query_macro_weights(records, query_weights={"q1": Decimal("1")})
