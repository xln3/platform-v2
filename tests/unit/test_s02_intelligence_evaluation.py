from decimal import Decimal

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
    metrics = evaluate(cases)
    assert metrics.precision == Decimal("0.5")
    assert metrics.recall == Decimal("0.5")
    assert metrics.false_positive_rate == Decimal("0.5")
    assert metrics.brier_score == Decimal("0.2175")
    assert metrics.explanation_completeness_rate == Decimal("0.75")
    assert_cluster_split(
        [case.propagation_cluster_id for case in cases[:2]],
        [case.propagation_cluster_id for case in cases[2:]],
    )
