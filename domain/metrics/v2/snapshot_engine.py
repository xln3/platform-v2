"""Pure in-memory deterministic V2 metric snapshot engine.

Persistence, transactions, outbox delivery, and model execution intentionally do
not live here. Repositories can freeze inputs in a repeatable-read transaction,
call this engine, and atomically persist its immutable result.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from .canonical_hash import canonical_hash, canonical_set_hash
from .definition_schema import DefinitionStatus, MetricDefinition
from .evaluator import MetricEvaluator
from .models import (
    EligibilityStatus,
    EvaluationInput,
    MetricContribution,
    MetricDesignCellContribution,
    MetricQueryContribution,
    MetricSnapshot,
    MetricSnapshotSet,
    MetricSnapshotState,
)
from .weighting import (
    CalibrationErrorArtifact,
    WeightingInput,
    assign_query_macro_weights,
    calculate_adjudication_sensitivity,
    calculate_answer_weighted_value,
    calculate_missing_bounds,
)

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class DesignCoordinates:
    design_cell_key: str
    model: str = ""
    region: str = ""
    mode: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotBuildRequest:
    definitions: tuple[MetricDefinition, ...]
    subjects: tuple[EvaluationInput, ...]
    as_of: datetime
    scope: Mapping[str, Any]
    dependency_bundle: Mapping[str, Any]
    focal_entity_ids: tuple[str, ...] = ()
    design_coordinates_by_answer: Mapping[str, DesignCoordinates] | None = None
    query_weights: Mapping[str, Decimal] | None = None
    design_cell_weights: Mapping[str, Mapping[str, Decimal]] | None = None
    planned_design_cells: Mapping[str, tuple[str, ...]] | None = None
    planned_repeat_counts: Mapping[tuple[str, str], int] | None = None
    calibration_artifacts: tuple[CalibrationErrorArtifact, ...] = ()
    collection_coverage: Decimal = ONE
    query_context_coverage: Decimal = ONE
    evidence_coverage: Decimal = ONE
    coverage_gate: Decimal = Decimal("0.98")
    design_basis: str = "planned_cells"
    minimum_queries_for_ready: int = 10

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("snapshot as_of must be timezone-aware")
        if self.design_basis not in {"planned_cells", "observed_cells"}:
            raise ValueError("design_basis must be planned_cells or observed_cells")
        for coverage in (
            self.collection_coverage,
            self.query_context_coverage,
            self.evidence_coverage,
            self.coverage_gate,
        ):
            if not ZERO <= coverage <= ONE:
                raise ValueError("coverage values must be between zero and one")


def _snapshot_state(
    *,
    definition: MetricDefinition,
    denominator: Decimal,
    coverages: tuple[Decimal, ...],
    design_basis: str,
    unique_query_count: int,
    minimum_queries_for_ready: int,
    coverage_gate: Decimal,
    sensitivity_width: Decimal | None,
) -> tuple[MetricSnapshotState, tuple[str, ...]]:
    if definition.status is DefinitionStatus.EXPERIMENTAL:
        return MetricSnapshotState.EXPERIMENTAL, ("metric_definition_experimental",)
    if denominator <= ZERO:
        return MetricSnapshotState.INSUFFICIENT, ("empty_known_denominator",)
    if any(coverage < coverage_gate for coverage in coverages):
        return MetricSnapshotState.INSUFFICIENT, ("coverage_below_publication_gate",)
    maximum_sensitivity_width = definition.publication_gate.get(
        "maximum_adjudication_sensitivity_width"
    )
    if maximum_sensitivity_width is not None and sensitivity_width is not None:
        if sensitivity_width > Decimal(str(maximum_sensitivity_width)):
            return MetricSnapshotState.INSUFFICIENT, (
                "adjudication_sensitivity_above_publication_gate",
            )
    limited: list[str] = []
    if design_basis == "observed_cells":
        limited.append("historical_design_unknown")
    if unique_query_count < minimum_queries_for_ready:
        limited.append("unique_query_count_below_10")
    release_role = definition.metadata.get("release_role")
    if release_role in {"conditional_diagnostic", "diagnostic"}:
        limited.append("conditional_or_diagnostic_metric")
    if limited:
        return MetricSnapshotState.LIMITED, tuple(limited)
    return MetricSnapshotState.READY, ()


def _planned_missing_weight(
    weighted: tuple[Any, ...],
    planned_design_cells: Mapping[str, tuple[str, ...]] | None,
    explicit_design_weights: Mapping[str, Mapping[str, Decimal]] | None,
) -> Decimal:
    if not weighted or planned_design_cells is None:
        return ZERO
    observed_by_query: dict[str, set[str]] = defaultdict(set)
    query_weight_by_query: dict[str, Decimal] = {}
    for item in weighted:
        observed_by_query[item.query_key].add(item.design_cell_key)
        query_weight_by_query[item.query_key] = item.query_weight
    missing = ZERO
    for query_key, observed_cells in observed_by_query.items():
        planned = tuple(sorted(set(planned_design_cells.get(query_key, ()))))
        if not planned:
            continue
        explicit = (
            None if explicit_design_weights is None else explicit_design_weights.get(query_key)
        )
        if explicit is None:
            weights = {cell: ONE / Decimal(len(planned)) for cell in planned}
        else:
            weights = {cell: Decimal(explicit[cell]) for cell in planned}
        missing += query_weight_by_query[query_key] * sum(
            (weight for cell, weight in weights.items() if cell not in observed_cells), ZERO
        )
    return missing


def _capability_coverages(
    definition: MetricDefinition,
    subjects: tuple[EvaluationInput, ...],
    evaluations: tuple[Any, ...],
) -> dict[str, Decimal]:
    applicable_indexes = tuple(
        index
        for index, evaluation in enumerate(evaluations)
        if evaluation.eligibility_status is not EligibilityStatus.EXCLUDED
    )
    if not applicable_indexes:
        return {item.name: ZERO for item in definition.required_semantic_capabilities}
    result: dict[str, Decimal] = {}
    aliases = {
        "substantive_entity_mention": ("entity_mention",),
        "claim_evidence_verdict": ("claim_verification",),
        "stance_and_pairwise": ("sentiment_or_stance", "pairwise_preference"),
    }
    for requirement in definition.required_semantic_capabilities:
        ready = 0
        names = (requirement.name, *aliases.get(requirement.name, ()))
        for index in applicable_indexes:
            statuses = subjects[index].capability_statuses
            value = next((statuses[name] for name in names if name in statuses), None)
            rendered = getattr(value, "value", value)
            ready += rendered == requirement.accepted_status
        result[requirement.name] = Decimal(ready) / Decimal(len(applicable_indexes))
    return result


class MetricSnapshotEngine:
    """Build a byte-stable set from already frozen facts and decisions."""

    def __init__(self, evaluator: MetricEvaluator | None = None) -> None:
        self._evaluator = evaluator or MetricEvaluator()

    def build_set(self, request: SnapshotBuildRequest) -> MetricSnapshotSet:
        coordinates = request.design_coordinates_by_answer or {}
        snapshots: list[MetricSnapshot] = []
        all_answer_contributions: list[MetricContribution] = []
        all_query_contributions: list[MetricQueryContribution] = []
        all_design_contributions: list[MetricDesignCellContribution] = []
        definitions = sorted(request.definitions, key=lambda item: (item.name, item.version))
        focal_entities = sorted(
            set(request.focal_entity_ids)
            | {subject.focal_entity_id for subject in request.subjects}
        )
        if definitions and not focal_entities:
            raise ValueError("snapshot build requires at least one focal entity")
        for definition in definitions:
            for focal_entity_id in focal_entities:
                subjects = tuple(
                    subject
                    for subject in request.subjects
                    if subject.focal_entity_id == focal_entity_id
                )
                evaluations = tuple(
                    self._evaluator.evaluate(definition, subject) for subject in subjects
                )
                weighting_inputs: list[WeightingInput] = []
                for subject, evaluation in zip(subjects, evaluations, strict=True):
                    coordinate = coordinates.get(subject.answer_pub_id)
                    if coordinate is None:
                        coordinate = DesignCoordinates(design_cell_key="observed:default")
                    methods = {
                        decision.method
                        for decision in subject.decisions.values()
                        if decision.method is not None
                    }
                    method = next(iter(methods)) if len(methods) == 1 else None
                    weighting_inputs.append(
                        WeightingInput(
                            evaluation=evaluation,
                            design_cell_key=coordinate.design_cell_key,
                            model=coordinate.model,
                            region=coordinate.region,
                            mode=coordinate.mode,
                            decision_method=method,
                        )
                    )
                weighted = assign_query_macro_weights(
                    weighting_inputs,
                    query_weights=request.query_weights,
                    design_cell_weights=request.design_cell_weights,
                    planned_design_cells=request.planned_design_cells,
                )
                semantic_bounds = calculate_missing_bounds(weighted)
                planned_missing_weight = _planned_missing_weight(
                    weighted,
                    request.planned_design_cells,
                    request.design_cell_weights,
                )
                declared_collection_missing_weight = ZERO
                if request.collection_coverage < ONE and semantic_bounds.candidate_weight > ZERO:
                    if request.collection_coverage == ZERO:
                        declared_collection_missing_weight = ONE
                    else:
                        declared_collection_missing_weight = semantic_bounds.candidate_weight * (
                            ONE / request.collection_coverage - ONE
                        )
                collection_missing_weight = max(
                    planned_missing_weight, declared_collection_missing_weight
                )
                bounds = calculate_missing_bounds(
                    weighted, additional_unknown_weight=collection_missing_weight
                )
                sensitivity = calculate_adjudication_sensitivity(
                    weighted, request.calibration_artifacts
                )
                weighted_numerator = sum((item.weighted_numerator for item in weighted), ZERO)
                weighted_denominator = sum((item.weighted_denominator for item in weighted), ZERO)
                raw_numerator = sum(
                    (
                        item.numerator_contribution
                        for item in evaluations
                        if item.eligibility_status
                        in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
                    ),
                    ZERO,
                )
                raw_denominator = sum(
                    (
                        item.denominator_contribution
                        for item in evaluations
                        if item.eligibility_status
                        in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
                    ),
                    ZERO,
                )
                statuses = Counter(item.eligibility_status for item in evaluations)
                applicable_queries = {
                    item.query_key
                    for item in evaluations
                    if item.eligibility_status
                    in {
                        EligibilityStatus.INCLUDED_HIT,
                        EligibilityStatus.INCLUDED_MISS,
                        EligibilityStatus.ANALYSIS_UNKNOWN,
                    }
                }
                calculated_collection_coverage = (
                    semantic_bounds.candidate_weight
                    / (semantic_bounds.candidate_weight + collection_missing_weight)
                    if semantic_bounds.candidate_weight + collection_missing_weight > ZERO
                    else ZERO
                )
                collection_coverage = min(
                    request.collection_coverage, calculated_collection_coverage
                )
                sensitivity_width = (
                    sensitivity.upper - sensitivity.lower
                    if sensitivity.lower is not None and sensitivity.upper is not None
                    else None
                )
                state, state_reasons = _snapshot_state(
                    definition=definition,
                    denominator=weighted_denominator,
                    coverages=(
                        collection_coverage,
                        request.query_context_coverage,
                        semantic_bounds.coverage,
                        request.evidence_coverage,
                    ),
                    design_basis=request.design_basis,
                    unique_query_count=len(applicable_queries),
                    minimum_queries_for_ready=request.minimum_queries_for_ready,
                    coverage_gate=request.coverage_gate,
                    sensitivity_width=sensitivity_width,
                )
                weighted_by_answer = {item.evaluation.answer_pub_id: item for item in weighted}
                if len(weighted_by_answer) != len(weighted):
                    raise ValueError("answer_pub_id must be unique within one metric evaluation")
                answer_contributions: list[MetricContribution] = []
                subjects_by_answer = {item.answer_pub_id: item for item in subjects}
                for evaluation in evaluations:
                    weighted_item = weighted_by_answer.get(evaluation.answer_pub_id)
                    coordinate = coordinates.get(
                        evaluation.answer_pub_id,
                        DesignCoordinates(design_cell_key="excluded"),
                    )
                    contribution = MetricContribution(
                        metric_name=definition.name,
                        metric_version=definition.version,
                        metric_definition_hash=definition.definition_hash,
                        focal_entity_id=focal_entity_id,
                        answer_pub_id=evaluation.answer_pub_id,
                        query_key=evaluation.query_key,
                        design_cell_key=(
                            weighted_item.design_cell_key
                            if weighted_item is not None
                            else coordinate.design_cell_key
                        ),
                        eligibility_status=evaluation.eligibility_status,
                        reason_codes=evaluation.reason_codes,
                        outcome_value=evaluation.outcome_value,
                        numerator_contribution=evaluation.numerator_contribution,
                        denominator_contribution=evaluation.denominator_contribution,
                        query_weight=(weighted_item.query_weight if weighted_item else ZERO),
                        design_cell_weight=(
                            weighted_item.design_cell_weight if weighted_item else ZERO
                        ),
                        repeat_weight=(weighted_item.repeat_weight if weighted_item else ZERO),
                        final_weight=(weighted_item.final_weight if weighted_item else ZERO),
                        weighted_numerator=(
                            weighted_item.weighted_numerator if weighted_item else ZERO
                        ),
                        weighted_denominator=(
                            weighted_item.weighted_denominator if weighted_item else ZERO
                        ),
                        missing_bound_weight=(
                            weighted_item.missing_bound_weight if weighted_item else ZERO
                        ),
                        supporting_event_pub_ids=evaluation.supporting_event_pub_ids,
                        supporting_decision_pub_ids=evaluation.supporting_decision_pub_ids,
                        semantic_decision_set_hash=evaluation.semantic_decision_set_hash,
                        evaluation_hash=evaluation.evaluation_hash,
                        query_context_fact_pub_id=evaluation.query_context_fact_pub_id,
                        semantic_manifest_pub_id=evaluation.semantic_manifest_pub_id,
                        model=coordinate.model,
                        region=coordinate.region,
                        mode=coordinate.mode,
                        capture_time=subjects_by_answer[evaluation.answer_pub_id].answer_fields.get(
                            "capture_time"
                        ),
                        dimension_snapshot=dict(
                            subjects_by_answer[evaluation.answer_pub_id].answer_fields.get(
                                "dimension_snapshot", {}
                            )
                        ),
                        answer_detail_ref=str(
                            subjects_by_answer[evaluation.answer_pub_id].answer_fields.get(
                                "answer_detail_ref", ""
                            )
                        ),
                    )
                    contribution = replace(
                        contribution, contribution_hash=canonical_hash(contribution)
                    )
                    answer_contributions.append(contribution)
                contribution_hash = canonical_set_hash(answer_contributions)

                query_contributions: list[MetricQueryContribution] = []
                for query_key in sorted({item.query_key for item in evaluations}):
                    query_evaluations = tuple(
                        item for item in evaluations if item.query_key == query_key
                    )
                    query_weighted = tuple(item for item in weighted if item.query_key == query_key)
                    query_numerator = sum(
                        (item.weighted_numerator for item in query_weighted), ZERO
                    )
                    query_denominator = sum(
                        (item.weighted_denominator for item in query_weighted), ZERO
                    )
                    query_weight = query_weighted[0].query_weight if query_weighted else ZERO
                    local_numerator = (
                        query_numerator / query_weight if query_weight > ZERO else ZERO
                    )
                    local_denominator = (
                        query_denominator / query_weight if query_weight > ZERO else ZERO
                    )
                    query_contribution = MetricQueryContribution(
                        metric_name=definition.name,
                        metric_version=definition.version,
                        focal_entity_id=focal_entity_id,
                        query_key=query_key,
                        numerator=local_numerator,
                        denominator=local_denominator,
                        value=(
                            local_numerator / local_denominator
                            if local_denominator > ZERO
                            else None
                        ),
                        unknown_weight=sum(
                            (
                                item.missing_bound_weight
                                for item in query_weighted
                                if item.evaluation.eligibility_status
                                is EligibilityStatus.ANALYSIS_UNKNOWN
                            ),
                            ZERO,
                        ),
                        query_weight=query_weight,
                        design_cell_count=len({item.design_cell_key for item in query_weighted}),
                        answer_count=len(query_evaluations),
                        known_answer_count=sum(
                            item.eligibility_status
                            in {
                                EligibilityStatus.INCLUDED_HIT,
                                EligibilityStatus.INCLUDED_MISS,
                            }
                            for item in query_evaluations
                        ),
                        unknown_answer_count=sum(
                            item.eligibility_status is EligibilityStatus.ANALYSIS_UNKNOWN
                            for item in query_evaluations
                        ),
                        query_context_fact_pub_id=next(
                            (
                                item.query_context_fact_pub_id
                                for item in query_evaluations
                                if item.query_context_fact_pub_id
                            ),
                            "",
                        ),
                        reason_codes=tuple(
                            sorted(
                                {code for item in query_evaluations for code in item.reason_codes}
                            )
                        ),
                    )
                    query_contributions.append(
                        replace(
                            query_contribution,
                            contribution_hash=canonical_hash(query_contribution),
                        )
                    )
                query_contribution_hash = canonical_set_hash(query_contributions)

                design_contributions: list[MetricDesignCellContribution] = []
                applicable_query_keys = sorted({item.query_key for item in weighted})
                for query_key in applicable_query_keys:
                    query_records = tuple(item for item in weighted if item.query_key == query_key)
                    observed_cells = {item.design_cell_key for item in query_records}
                    planned_cells = (
                        set(request.planned_design_cells.get(query_key, ()))
                        if request.planned_design_cells is not None
                        else observed_cells
                    )
                    for design_cell_key in sorted(planned_cells):
                        cell_records = tuple(
                            item
                            for item in query_records
                            if item.design_cell_key == design_cell_key
                        )
                        known_repeats = sum(
                            item.evaluation.eligibility_status
                            in {
                                EligibilityStatus.INCLUDED_HIT,
                                EligibilityStatus.INCLUDED_MISS,
                            }
                            for item in cell_records
                        )
                        unknown_repeats = sum(
                            item.evaluation.eligibility_status is EligibilityStatus.ANALYSIS_UNKNOWN
                            for item in cell_records
                        )
                        explicit_weights = (
                            request.design_cell_weights.get(query_key)
                            if request.design_cell_weights is not None
                            else None
                        )
                        cell_weight = (
                            Decimal(explicit_weights[design_cell_key])
                            if explicit_weights is not None
                            else ONE / Decimal(len(planned_cells))
                        )
                        exemplar = cell_records[0] if cell_records else None
                        planned_repeats = (
                            request.planned_repeat_counts.get((query_key, design_cell_key), 1)
                            if request.planned_repeat_counts is not None
                            else max(1, len(cell_records))
                        )
                        status = (
                            "missing"
                            if not cell_records
                            else "unknown"
                            if not known_repeats
                            else "partial"
                            if unknown_repeats
                            else "ready"
                        )
                        design_contribution = MetricDesignCellContribution(
                            metric_name=definition.name,
                            metric_version=definition.version,
                            focal_entity_id=focal_entity_id,
                            query_key=query_key,
                            design_cell_key=design_cell_key,
                            model=exemplar.model if exemplar else "",
                            region=exemplar.region if exemplar else "",
                            mode=exemplar.mode if exemplar else "",
                            planned_repeat_count=planned_repeats,
                            observed_repeat_count=len(cell_records),
                            known_repeat_count=known_repeats,
                            unknown_repeat_count=unknown_repeats,
                            design_cell_weight=cell_weight,
                            query_weight=(
                                exemplar.query_weight
                                if exemplar is not None
                                else query_records[0].query_weight
                            ),
                            status=status,
                        )
                        design_contributions.append(
                            replace(
                                design_contribution,
                                contribution_hash=canonical_hash(design_contribution),
                            )
                        )
                design_contribution_hash = canonical_set_hash(design_contributions)
                all_answer_contributions.extend(answer_contributions)
                all_query_contributions.extend(query_contributions)
                all_design_contributions.extend(design_contributions)
                method_weights: dict[str, Decimal] = defaultdict(lambda: ZERO)
                for item in weighted:
                    if item.decision_method is not None:
                        method_weights[item.decision_method.value] += item.weighted_denominator
                if weighted_denominator > ZERO:
                    method_mix = {
                        method: weight / weighted_denominator
                        for method, weight in sorted(method_weights.items())
                    }
                else:
                    method_mix = {}
                sum_weights = sum((item.final_weight for item in weighted), ZERO)
                sum_squared_weights = sum(
                    (item.final_weight * item.final_weight for item in weighted), ZERO
                )
                effective_sample_size = (
                    sum_weights * sum_weights / sum_squared_weights
                    if sum_squared_weights > ZERO
                    else ZERO
                )
                capability_coverages = _capability_coverages(definition, subjects, evaluations)
                formal_value = bounds.observed_value
                if state in {
                    MetricSnapshotState.INSUFFICIENT,
                    MetricSnapshotState.FAILED,
                    MetricSnapshotState.EXPERIMENTAL,
                }:
                    formal_value = None
                snapshot = MetricSnapshot(
                    metric_name=definition.name,
                    metric_version=definition.version,
                    metric_definition_hash=definition.definition_hash,
                    focal_entity_id=focal_entity_id,
                    state=state,
                    state_reason_codes=state_reasons,
                    value=formal_value,
                    observed_value=bounds.observed_value,
                    answer_weighted_value=calculate_answer_weighted_value(evaluations),
                    lower_bound=bounds.lower_bound,
                    upper_bound=bounds.upper_bound,
                    weighted_numerator=weighted_numerator,
                    weighted_denominator=weighted_denominator,
                    raw_numerator=raw_numerator,
                    raw_denominator=raw_denominator,
                    candidate_answer_count=len(evaluations),
                    known_answer_count=(
                        statuses[EligibilityStatus.INCLUDED_HIT]
                        + statuses[EligibilityStatus.INCLUDED_MISS]
                    ),
                    unknown_answer_count=statuses[EligibilityStatus.ANALYSIS_UNKNOWN],
                    not_applicable_answer_count=statuses[EligibilityStatus.NOT_APPLICABLE],
                    excluded_answer_count=statuses[EligibilityStatus.EXCLUDED],
                    unique_query_count=len(applicable_queries),
                    semantic_coverage=bounds.coverage,
                    contribution_set_hash=contribution_hash,
                    snapshot_hash="",
                    adjudication_sensitivity_low=sensitivity.lower,
                    adjudication_sensitivity_high=sensitivity.upper,
                    calibration_artifact_hashes=sensitivity.calibration_artifact_hashes,
                    semantic_lower_bound=semantic_bounds.lower_bound,
                    semantic_upper_bound=semantic_bounds.upper_bound,
                    decision_abstained_count=sum(
                        "decision_abstained" in item.reason_codes for item in evaluations
                    ),
                    decision_review_required_count=sum(
                        bool(
                            {"decision_review_required", "semantic_review_required"}
                            & set(item.reason_codes)
                        )
                        for item in evaluations
                    ),
                    design_cell_count=len(
                        {(item.query_key, item.design_cell_key) for item in weighted}
                    ),
                    effective_sample_size=effective_sample_size,
                    collection_coverage=collection_coverage,
                    query_context_coverage=request.query_context_coverage,
                    evidence_coverage=request.evidence_coverage,
                    semantic_coverage_by_capability=capability_coverages,
                    decision_method_mix=method_mix,
                    query_contribution_set_hash=query_contribution_hash,
                    design_contribution_set_hash=design_contribution_hash,
                )
                snapshot = replace(snapshot, snapshot_hash=canonical_hash(snapshot))
                snapshots.append(snapshot)
        snapshots_tuple = tuple(
            sorted(
                snapshots,
                key=lambda item: (item.metric_name, item.metric_version, item.focal_entity_id),
            )
        )
        scope_hash = canonical_hash(request.scope)
        dependency_hash = canonical_hash(request.dependency_bundle)
        query_set_hash = canonical_set_hash(
            {"query_key": query_key}
            for query_key in sorted(
                {subject.query_context.query_key for subject in request.subjects}
            )
        )
        design_set_hash = canonical_set_hash(
            {
                "query_key": query_key,
                "design_cell_key": design_cell_key,
                "planned_repeat_count": planned_repeat_count,
            }
            for query_key, design_cell_key, planned_repeat_count in sorted(
                {
                    (item.query_key, item.design_cell_key, item.planned_repeat_count)
                    for item in all_design_contributions
                }
            )
        )
        set_material = {
            "scope_hash": scope_hash,
            "dependency_bundle_hash": dependency_hash,
            "as_of": request.as_of,
            "aggregation_method": "query_macro",
            "design_basis": request.design_basis,
            "focal_entity_ids": focal_entities,
            "query_set_hash": query_set_hash,
            "design_set_hash": design_set_hash,
            "snapshot_hashes": [item.snapshot_hash for item in snapshots_tuple],
        }
        return MetricSnapshotSet(
            scope_hash=scope_hash,
            dependency_bundle_hash=dependency_hash,
            as_of=request.as_of,
            aggregation_method="query_macro",
            snapshots=snapshots_tuple,
            snapshot_set_hash=canonical_hash(set_material),
            state=(
                "failed"
                if any(item.state is MetricSnapshotState.FAILED for item in snapshots_tuple)
                else "ready"
                if all(item.state is MetricSnapshotState.READY for item in snapshots_tuple)
                else "partial"
            ),
            answer_contributions=tuple(
                sorted(
                    all_answer_contributions,
                    key=lambda item: (
                        item.metric_name,
                        item.metric_version,
                        item.focal_entity_id,
                        item.query_key,
                        item.design_cell_key,
                        item.answer_pub_id,
                    ),
                )
            ),
            query_contributions=tuple(
                sorted(
                    all_query_contributions,
                    key=lambda item: (
                        item.metric_name,
                        item.metric_version,
                        item.focal_entity_id,
                        item.query_key,
                    ),
                )
            ),
            design_cell_contributions=tuple(
                sorted(
                    all_design_contributions,
                    key=lambda item: (
                        item.metric_name,
                        item.metric_version,
                        item.focal_entity_id,
                        item.query_key,
                        item.design_cell_key,
                    ),
                )
            ),
            scope=dict(request.scope),
            dependency_bundle=dict(request.dependency_bundle),
            design_basis=request.design_basis,
            focal_entity_ids=tuple(focal_entities),
            query_set_hash=query_set_hash,
            design_set_hash=design_set_hash,
            snapshot_count=len(snapshots_tuple),
        )
