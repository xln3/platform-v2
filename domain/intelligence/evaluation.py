from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    propagation_cluster_id: str
    probability: Decimal
    actual_positive: bool
    predicted_positive: bool
    explanation_fields_present: frozenset[str]


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    precision: Decimal | None
    recall: Decimal | None
    false_positive_rate: Decimal | None
    brier_score: Decimal
    explanation_completeness_rate: Decimal
    sample_count: int


_REQUIRED_EXPLANATION_FIELDS = frozenset(
    {
        "evidence_sufficiency",
        "independent_source_count",
        "uncertainty",
        "rule_version",
        "model_version",
        "human_verdict_state",
    }
)


def evaluate(cases: Iterable[EvaluationCase]) -> EvaluationMetrics:
    rows = tuple(cases)
    if not rows:
        raise ValueError("evaluation set must not be empty")
    true_positive = sum(row.predicted_positive and row.actual_positive for row in rows)
    false_positive = sum(row.predicted_positive and not row.actual_positive for row in rows)
    false_negative = sum(not row.predicted_positive and row.actual_positive for row in rows)
    true_negative = sum(not row.predicted_positive and not row.actual_positive for row in rows)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    false_positive_rate = _ratio(false_positive, false_positive + true_negative)
    brier = sum(
        (
            (row.probability - (Decimal("1") if row.actual_positive else Decimal("0"))) ** 2
            for row in rows
        ),
        start=Decimal("0"),
    ) / Decimal(len(rows))
    complete = sum(_REQUIRED_EXPLANATION_FIELDS <= row.explanation_fields_present for row in rows)
    return EvaluationMetrics(
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        brier_score=brier,
        explanation_completeness_rate=Decimal(complete) / Decimal(len(rows)),
        sample_count=len(rows),
    )


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return Decimal(numerator) / Decimal(denominator) if denominator else None
