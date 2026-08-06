from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256


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
    expected_calibration_error: Decimal
    explanation_completeness_rate: Decimal
    sample_count: int
    positive_count: int
    negative_count: int
    dataset_version: str
    scorer_version: str
    dataset_sha256: str


REQUIRED_EXPLANATION_FIELDS = frozenset(
    {
        "evidence_sufficiency",
        "independent_source_count",
        "uncertainty",
        "rule_version",
        "model_version",
        "human_verdict_state",
    }
)


def evaluate(
    cases: Iterable[EvaluationCase],
    *,
    dataset_version: str,
    scorer_version: str,
    decision_threshold: Decimal = Decimal("0.5"),
    calibration_bins: int = 10,
) -> EvaluationMetrics:
    rows = tuple(cases)
    if not rows:
        raise ValueError("evaluation set must not be empty")
    if not dataset_version.strip() or not scorer_version.strip():
        raise ValueError("dataset and scorer versions are required")
    if not Decimal("0") < decision_threshold < Decimal("1"):
        raise ValueError("decision threshold must be between zero and one")
    if calibration_bins < 2 or calibration_bins > 100:
        raise ValueError("calibration bins must be between 2 and 100")
    cluster_ids = [row.propagation_cluster_id for row in rows]
    if any(not cluster_id.strip() for cluster_id in cluster_ids):
        raise ValueError("propagation cluster id is required")
    if len(set(cluster_ids)) != len(cluster_ids):
        raise ValueError("evaluation set contains duplicate propagation clusters")
    for row in rows:
        if not Decimal("0") <= row.probability <= Decimal("1"):
            raise ValueError("probability must be between zero and one")
        if row.predicted_positive != (row.probability >= decision_threshold):
            raise ValueError("predicted label does not match the declared threshold")
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
    complete = sum(REQUIRED_EXPLANATION_FIELDS <= row.explanation_fields_present for row in rows)
    canonical_rows = [
        {
            "actual_positive": row.actual_positive,
            "explanation_fields_present": sorted(row.explanation_fields_present),
            "predicted_positive": row.predicted_positive,
            "probability": str(row.probability),
            "propagation_cluster_id": row.propagation_cluster_id,
        }
        for row in sorted(rows, key=lambda item: item.propagation_cluster_id)
    ]
    fingerprint = sha256(
        json.dumps(
            {
                "cases": canonical_rows,
                "dataset_version": dataset_version,
                "decision_threshold": str(decision_threshold),
                "scorer_version": scorer_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return EvaluationMetrics(
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        brier_score=brier,
        expected_calibration_error=_expected_calibration_error(rows, calibration_bins),
        explanation_completeness_rate=Decimal(complete) / Decimal(len(rows)),
        sample_count=len(rows),
        positive_count=true_positive + false_negative,
        negative_count=true_negative + false_positive,
        dataset_version=dataset_version,
        scorer_version=scorer_version,
        dataset_sha256=fingerprint,
    )


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return Decimal(numerator) / Decimal(denominator) if denominator else None


def _expected_calibration_error(rows: tuple[EvaluationCase, ...], calibration_bins: int) -> Decimal:
    total = Decimal(len(rows))
    width = Decimal("1") / Decimal(calibration_bins)
    error = Decimal("0")
    for index in range(calibration_bins):
        lower = width * index
        upper = Decimal("1") if index + 1 == calibration_bins else width * (index + 1)
        bucket = tuple(
            row
            for row in rows
            if row.probability >= lower
            and (row.probability <= upper if upper == Decimal("1") else row.probability < upper)
        )
        if not bucket:
            continue
        average_probability = sum(
            (row.probability for row in bucket), start=Decimal("0")
        ) / Decimal(len(bucket))
        observed_rate = Decimal(sum(row.actual_positive for row in bucket)) / Decimal(len(bucket))
        error += Decimal(len(bucket)) / total * abs(average_probability - observed_rate)
    return error
