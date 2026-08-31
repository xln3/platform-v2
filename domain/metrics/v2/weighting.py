"""Query-macro weighting, missing bounds, and adjudication sensitivity."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from .models import (
    AdjudicationSensitivity,
    DecisionMethod,
    EligibilityStatus,
    MetricEvaluation,
    MissingBounds,
    WeightedMetricEvaluation,
)

ZERO = Decimal("0")
ONE = Decimal("1")
SNAPSHOT_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class WeightingInput:
    evaluation: MetricEvaluation
    design_cell_key: str
    model: str = ""
    region: str = ""
    mode: str = ""
    decision_method: DecisionMethod | None = None

    @property
    def query_key(self) -> str:
        return self.evaluation.query_key


@dataclass(frozen=True, slots=True)
class CalibrationErrorArtifact:
    artifact_hash: str
    method: DecisionMethod
    false_positive_upper_bound: Decimal
    false_negative_upper_bound: Decimal
    task_ref: str = ""

    def __post_init__(self) -> None:
        for value in (self.false_positive_upper_bound, self.false_negative_upper_bound):
            if not ZERO <= value <= ONE:
                raise ValueError("calibration error bounds must be between zero and one")


def _validate_distribution(weights: Mapping[str, Decimal], label: str) -> None:
    if not weights:
        raise ValueError(f"{label} weights cannot be empty")
    if any(weight < ZERO for weight in weights.values()):
        raise ValueError(f"{label} weights cannot be negative")
    if sum(weights.values(), ZERO) != ONE:
        raise ValueError(f"{label} weights must sum exactly to one")


def assign_query_macro_weights(
    records: Iterable[WeightingInput],
    *,
    query_weights: Mapping[str, Decimal] | None = None,
    design_cell_weights: Mapping[str, Mapping[str, Decimal]] | None = None,
    planned_design_cells: Mapping[str, Iterable[str]] | None = None,
) -> tuple[WeightedMetricEvaluation, ...]:
    """Expand query/design/repeat weights without letting repeats inflate a query.

    ``final_weight`` is the formal known-sample weight. ``missing_bound_weight``
    gives every observed applicable repeat its share of the full candidate
    universe and is intentionally separate from the point-estimate weight.
    """

    materialized = tuple(records)
    candidate_statuses = {
        EligibilityStatus.INCLUDED_HIT,
        EligibilityStatus.INCLUDED_MISS,
        EligibilityStatus.ANALYSIS_UNKNOWN,
        EligibilityStatus.ANALYSIS_FAILED,
    }
    applicable = tuple(
        record
        for record in materialized
        if record.evaluation.eligibility_status in candidate_statuses
    )
    query_keys = sorted({record.query_key for record in applicable})
    if not query_keys:
        return ()
    if query_weights is None:
        equal_query_weight = ONE / Decimal(len(query_keys))
        resolved_query_weights = {key: equal_query_weight for key in query_keys}
    else:
        missing_query_weights = set(query_keys) - set(query_weights)
        if missing_query_weights:
            raise ValueError(
                "query weights must cover exactly the applicable queries or a normalized superset"
            )
        selected = {key: Decimal(query_weights[key]) for key in query_keys}
        supplied = {key: Decimal(weight) for key, weight in query_weights.items()}
        if set(supplied) == set(query_keys):
            _validate_distribution(selected, "query")
        else:
            _validate_distribution(supplied, "query scope")
        selected_total = sum(selected.values(), ZERO)
        if selected_total <= ZERO:
            raise ValueError("applicable query weights must have positive total")
        resolved_query_weights = {key: weight / selected_total for key, weight in selected.items()}
        _validate_distribution(resolved_query_weights, "query")

    cells_by_query: dict[str, set[str]] = defaultdict(set)
    for record in applicable:
        cells_by_query[record.query_key].add(record.design_cell_key)
    if planned_design_cells is not None:
        for query_key in query_keys:
            planned = set(planned_design_cells.get(query_key, ()))
            if not cells_by_query[query_key].issubset(planned):
                raise ValueError("observed design cell is absent from the frozen plan")
            cells_by_query[query_key] = planned
    resolved_design_weights: dict[str, dict[str, Decimal]] = {}
    for query_key in query_keys:
        cells = sorted(cells_by_query[query_key])
        if not cells:
            raise ValueError(f"query {query_key} has no design cells")
        explicit = None if design_cell_weights is None else design_cell_weights.get(query_key)
        if explicit is None:
            equal_cell_weight = ONE / Decimal(len(cells))
            resolved_design_weights[query_key] = {cell: equal_cell_weight for cell in cells}
        else:
            weights = {key: Decimal(weight) for key, weight in explicit.items()}
            if set(weights) != set(cells):
                raise ValueError(
                    f"design weights for {query_key} must cover exactly the frozen cells"
                )
            _validate_distribution(weights, f"design cell for {query_key}")
            resolved_design_weights[query_key] = weights

    groups: dict[tuple[str, str], list[WeightingInput]] = defaultdict(list)
    for record in applicable:
        groups[(record.query_key, record.design_cell_key)].append(record)

    weighted: list[WeightedMetricEvaluation] = []
    for record in applicable:
        evaluation = record.evaluation
        group = groups[(record.query_key, record.design_cell_key)]
        known = [
            item
            for item in group
            if item.evaluation.eligibility_status
            in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
        ]
        query_weight = resolved_query_weights[record.query_key]
        design_weight = resolved_design_weights[record.query_key][record.design_cell_key]
        base_weight = query_weight * design_weight
        is_known = evaluation.eligibility_status in {
            EligibilityStatus.INCLUDED_HIT,
            EligibilityStatus.INCLUDED_MISS,
        }
        repeat_weight = ONE / Decimal(len(known)) if is_known and known else ZERO
        final_weight = base_weight * repeat_weight
        candidate_repeat_weight = ONE / Decimal(len(group))
        missing_bound_weight = base_weight * candidate_repeat_weight
        weighted.append(
            WeightedMetricEvaluation(
                evaluation=evaluation,
                query_key=record.query_key,
                design_cell_key=record.design_cell_key,
                query_weight=query_weight,
                design_cell_weight=design_weight,
                repeat_weight=repeat_weight,
                final_weight=final_weight,
                weighted_numerator=final_weight * evaluation.numerator_contribution,
                weighted_denominator=final_weight * evaluation.denominator_contribution,
                missing_bound_weight=missing_bound_weight,
                model=record.model,
                region=record.region,
                mode=record.mode,
                decision_method=record.decision_method,
            )
        )
    return tuple(
        sorted(
            weighted,
            key=lambda item: (
                item.query_key,
                item.model,
                item.region,
                item.mode,
                item.design_cell_key,
                item.evaluation.answer_pub_id,
            ),
        )
    )


def calculate_missing_bounds(
    weighted: Iterable[WeightedMetricEvaluation],
    *,
    additional_unknown_weight: Decimal = ZERO,
) -> MissingBounds:
    """Calculate point estimate and worst-case binary missing bounds separately."""

    records = tuple(weighted)
    known = tuple(
        item
        for item in records
        if item.evaluation.eligibility_status
        in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
    )
    unknown = tuple(
        item
        for item in records
        if item.evaluation.eligibility_status
        in {EligibilityStatus.ANALYSIS_UNKNOWN, EligibilityStatus.ANALYSIS_FAILED}
    )
    weighted_numerator = sum((item.weighted_numerator for item in known), ZERO)
    weighted_denominator = sum((item.weighted_denominator for item in known), ZERO)
    observed = weighted_numerator / weighted_denominator if weighted_denominator > ZERO else None
    known_universe_weight = sum((item.missing_bound_weight for item in known), ZERO)
    unknown_weight = sum((item.missing_bound_weight for item in unknown), ZERO)
    unknown_weight += Decimal(additional_unknown_weight)
    candidate_weight = known_universe_weight + unknown_weight
    if candidate_weight <= ZERO:
        return MissingBounds(None, ZERO, None, None, ZERO, ZERO, ZERO)
    known_hit_weight = sum(
        (
            item.missing_bound_weight
            * item.evaluation.numerator_contribution
            / item.evaluation.denominator_contribution
            for item in known
            if item.evaluation.denominator_contribution > ZERO
        ),
        ZERO,
    )
    coverage = known_universe_weight / candidate_weight
    lower = known_hit_weight / candidate_weight
    upper = (known_hit_weight + unknown_weight) / candidate_weight
    return MissingBounds(
        observed_value=observed,
        coverage=coverage,
        lower_bound=max(ZERO, min(ONE, lower)),
        upper_bound=max(ZERO, min(ONE, upper)),
        known_weight=known_universe_weight,
        unknown_weight=unknown_weight,
        candidate_weight=candidate_weight,
    )


def calculate_answer_weighted_value(
    evaluations: Iterable[MetricEvaluation],
) -> Decimal | None:
    known = tuple(
        item
        for item in evaluations
        if item.eligibility_status
        in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
    )
    denominator = sum((item.denominator_contribution for item in known), ZERO)
    if denominator <= ZERO:
        return None
    return sum((item.numerator_contribution for item in known), ZERO) / denominator


def calculate_adjudication_sensitivity(
    weighted: Iterable[WeightedMetricEvaluation],
    artifacts: Iterable[CalibrationErrorArtifact],
) -> AdjudicationSensitivity:
    """Apply conservative class-error bounds only to known decision-method weight.

    Missing/unknown units never enter this calculation. False-positive bounds
    can move observed hits down; false-negative bounds can move observed misses
    up. This keeps measurement sensitivity distinct from missing bounds.
    """

    records = tuple(
        item
        for item in weighted
        if item.evaluation.eligibility_status
        in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
    )
    denominator = sum((item.weighted_denominator for item in records), ZERO)
    numerator = sum((item.weighted_numerator for item in records), ZERO)
    artifacts_by_method: dict[DecisionMethod, list[CalibrationErrorArtifact]] = defaultdict(list)
    for artifact in artifacts:
        artifacts_by_method[artifact.method].append(artifact)
    hashes: set[str] = set()
    downward = ZERO
    upward = ZERO
    for record in records:
        if record.decision_method is None:
            continue
        method_artifacts = artifacts_by_method.get(record.decision_method, [])
        if not method_artifacts:
            continue
        # Multiple required calibrated tasks compound conservatively; cap at one.
        false_positive = min(
            ONE,
            sum((item.false_positive_upper_bound for item in method_artifacts), ZERO),
        )
        false_negative = min(
            ONE,
            sum((item.false_negative_upper_bound for item in method_artifacts), ZERO),
        )
        hashes.update(item.artifact_hash for item in method_artifacts)
        outcome_rate = (
            record.evaluation.numerator_contribution / record.evaluation.denominator_contribution
            if record.evaluation.denominator_contribution > ZERO
            else ZERO
        )
        downward += record.weighted_denominator * outcome_rate * false_positive
        upward += record.weighted_denominator * (ONE - outcome_rate) * false_negative
    if denominator <= ZERO:
        return AdjudicationSensitivity(None, None, downward, upward, tuple(sorted(hashes)))
    downward_rate = (downward / denominator).quantize(SNAPSHOT_QUANTUM)
    upward_rate = (upward / denominator).quantize(SNAPSHOT_QUANTUM)
    observed = numerator / denominator
    lower = max(ZERO, observed - downward_rate).quantize(SNAPSHOT_QUANTUM)
    upper = min(ONE, observed + upward_rate).quantize(SNAPSHOT_QUANTUM)
    return AdjudicationSensitivity(
        lower,
        upper,
        downward_rate,
        upward_rate,
        tuple(sorted(hashes)),
    )


# Concise aliases used by callers and fixtures.
compute_missing_bounds = calculate_missing_bounds
compute_adjudication_sensitivity = calculate_adjudication_sensitivity
