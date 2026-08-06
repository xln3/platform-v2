from decimal import Decimal

import pytest

from domain.intelligence.core import assert_cluster_split
from domain.intelligence.evaluation import EvaluationCase, evaluate

EXPLANATION = frozenset(
    {
        "evidence_sufficiency",
        "independent_source_count",
        "uncertainty",
        "rule_version",
        "model_version",
        "human_verdict_state",
    }
)


def test_fixed_case_evaluation_reports_precision_recall_fpr_calibration_and_explanation() -> None:
    cases = (
        EvaluationCase("cluster_a", Decimal("0.9"), True, True, EXPLANATION),
        EvaluationCase("cluster_b", Decimal("0.7"), False, True, EXPLANATION),
        EvaluationCase("cluster_c", Decimal("0.4"), True, False, EXPLANATION),
        EvaluationCase(
            "cluster_d",
            Decimal("0.1"),
            False,
            False,
            EXPLANATION - {"human_verdict_state"},
        ),
    )
    metrics = evaluate(
        cases,
        dataset_version="anti-geo-fixture-v1",
        scorer_version="anti-geo-rules-v1",
    )
    assert metrics.precision == Decimal("0.5")
    assert metrics.recall == Decimal("0.5")
    assert metrics.false_positive_rate == Decimal("0.5")
    assert metrics.brier_score == Decimal("0.2175")
    assert metrics.expected_calibration_error == Decimal("0.375")
    assert metrics.explanation_completeness_rate == Decimal("0.75")
    assert metrics.positive_count == metrics.negative_count == 2
    assert metrics.dataset_version == "anti-geo-fixture-v1"
    assert metrics.scorer_version == "anti-geo-rules-v1"
    assert len(metrics.dataset_sha256) == 64
    assert_cluster_split(
        [case.propagation_cluster_id for case in cases[:2]],
        [case.propagation_cluster_id for case in cases[2:]],
    )


@pytest.mark.parametrize(
    ("cases", "match"),
    [
        (
            (EvaluationCase("cluster_a", Decimal("1.1"), True, True, EXPLANATION),),
            "probability",
        ),
        (
            (
                EvaluationCase("cluster_a", Decimal("0.9"), True, True, EXPLANATION),
                EvaluationCase("cluster_a", Decimal("0.1"), False, False, EXPLANATION),
            ),
            "duplicate",
        ),
        (
            (EvaluationCase("cluster_a", Decimal("0.9"), True, False, EXPLANATION),),
            "threshold",
        ),
    ],
)
def test_evaluation_rejects_invalid_or_gameable_dataset(
    cases: tuple[EvaluationCase, ...], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        evaluate(cases, dataset_version="fixture-v1", scorer_version="rules-v1")
