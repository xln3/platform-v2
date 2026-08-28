"""Data-only contracts for deterministic V2 metric evaluation and snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from .query_context import ExposureRole, QueryContextFact

ZERO = Decimal("0")
ONE = Decimal("1")


class EligibilityStatus(StrEnum):
    INCLUDED_HIT = "included_hit"
    INCLUDED_MISS = "included_miss"
    EXCLUDED = "excluded"
    NOT_APPLICABLE = "not_applicable"
    ANALYSIS_UNKNOWN = "analysis_unknown"


class MetricSnapshotState(StrEnum):
    READY = "ready"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"
    EXPERIMENTAL = "experimental"
    FAILED = "failed"


class SemanticCapabilityStatus(StrEnum):
    READY = "ready"
    ABSTAINED = "abstained"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"
    MISSING = "missing"


class DecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    ABSTAINED = "abstained"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    MISSING = "missing"


class DecisionMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HYBRID = "hybrid"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class SemanticDecisionFact:
    task_ref: str
    status: DecisionStatus
    value: Mapping[str, Any] = field(default_factory=dict)
    decision_pub_id: str | None = None
    method: DecisionMethod | None = None
    calibrated: bool = False
    policy_matches: bool = True
    evidence_ready: bool = True
    calibration_artifact_hash: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    answer_pub_id: str
    query_context: QueryContextFact
    focal_entity_id: str
    exposure_role: ExposureRole
    collection_eligible: bool = True
    capability_statuses: Mapping[str, SemanticCapabilityStatus | str] = field(default_factory=dict)
    events: tuple[Any, ...] = ()
    decisions: Mapping[str, SemanticDecisionFact] = field(default_factory=dict)
    answer_fields: Mapping[str, Any] = field(default_factory=dict)
    query_context_fact_pub_id: str = ""
    semantic_manifest_pub_id: str = ""
    semantic_decision_set_hash: str = ""
    event_invariants_valid: bool = True
    evidence_spans_valid: bool = True
    evidence_retrieval_ready: bool = True

    @classmethod
    def from_manifest(
        cls,
        *,
        query_context: QueryContextFact,
        focal_entity_id: str,
        exposure_role: ExposureRole,
        manifest: Any,
        events: tuple[Any, ...],
        decisions: Mapping[str, SemanticDecisionFact],
        collection_eligible: bool = True,
        evidence_spans_valid: bool = True,
        evidence_retrieval_ready: bool = True,
    ) -> Self:
        """Project the typed semantic manifest into the metrics boundary."""

        manifest_status = getattr(manifest.status, "value", manifest.status)
        return cls(
            answer_pub_id=str(manifest.answer_pub_id),
            query_context=query_context,
            focal_entity_id=focal_entity_id,
            exposure_role=exposure_role,
            collection_eligible=collection_eligible,
            capability_statuses=manifest.capability_statuses,
            events=tuple(events),
            decisions=decisions,
            answer_fields={"manifest_status": str(manifest_status)},
            query_context_fact_pub_id=str(manifest.query_context_fact_pub_id),
            semantic_manifest_pub_id=str(manifest.pub_id),
            semantic_decision_set_hash=str(manifest.decision_set_hash),
            event_invariants_valid=str(manifest_status) not in {"failed", "review_required"},
            evidence_spans_valid=evidence_spans_valid,
            evidence_retrieval_ready=evidence_retrieval_ready,
        )


@dataclass(frozen=True, slots=True)
class MetricEvaluation:
    answer_pub_id: str
    query_key: str
    focal_entity_id: str
    metric_name: str
    metric_version: str
    metric_definition_hash: str
    eligibility_status: EligibilityStatus
    reason_codes: tuple[str, ...]
    outcome_value: Any
    numerator_contribution: Decimal
    denominator_contribution: Decimal
    supporting_event_pub_ids: tuple[str, ...] = ()
    supporting_decision_pub_ids: tuple[str, ...] = ()
    query_context_fact_pub_id: str = ""
    semantic_manifest_pub_id: str = ""
    semantic_decision_set_hash: str = ""
    evaluation_hash: str = ""


@dataclass(frozen=True, slots=True)
class WeightedMetricEvaluation:
    evaluation: MetricEvaluation
    query_key: str
    design_cell_key: str
    query_weight: Decimal
    design_cell_weight: Decimal
    repeat_weight: Decimal
    final_weight: Decimal
    weighted_numerator: Decimal
    weighted_denominator: Decimal
    missing_bound_weight: Decimal
    model: str = ""
    region: str = ""
    mode: str = ""
    decision_method: DecisionMethod | None = None


@dataclass(frozen=True, slots=True)
class MissingBounds:
    observed_value: Decimal | None
    coverage: Decimal
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    known_weight: Decimal
    unknown_weight: Decimal
    candidate_weight: Decimal


@dataclass(frozen=True, slots=True)
class AdjudicationSensitivity:
    lower: Decimal | None
    upper: Decimal | None
    downward_error_weight: Decimal
    upward_error_weight: Decimal
    calibration_artifact_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    metric_name: str
    metric_version: str
    metric_definition_hash: str
    focal_entity_id: str
    state: MetricSnapshotState
    state_reason_codes: tuple[str, ...]
    value: Decimal | None
    observed_value: Decimal | None
    answer_weighted_value: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    weighted_numerator: Decimal
    weighted_denominator: Decimal
    raw_numerator: Decimal
    raw_denominator: Decimal
    candidate_answer_count: int
    known_answer_count: int
    unknown_answer_count: int
    not_applicable_answer_count: int
    excluded_answer_count: int
    unique_query_count: int
    semantic_coverage: Decimal
    contribution_set_hash: str
    snapshot_hash: str
    adjudication_sensitivity_low: Decimal | None = None
    adjudication_sensitivity_high: Decimal | None = None
    calibration_artifact_hashes: tuple[str, ...] = ()
    semantic_lower_bound: Decimal | None = None
    semantic_upper_bound: Decimal | None = None
    decision_abstained_count: int = 0
    decision_review_required_count: int = 0
    design_cell_count: int = 0
    effective_sample_size: Decimal = ZERO
    collection_coverage: Decimal = ONE
    query_context_coverage: Decimal = ONE
    evidence_coverage: Decimal = ONE
    semantic_coverage_by_capability: Mapping[str, Decimal] = field(default_factory=dict)
    decision_method_mix: Mapping[str, Decimal] = field(default_factory=dict)
    bootstrap_low: Decimal | None = None
    bootstrap_high: Decimal | None = None
    bootstrap_method: str | None = None
    bootstrap_seed: str | None = None
    query_contribution_set_hash: str = ""
    design_contribution_set_hash: str = ""


@dataclass(frozen=True, slots=True)
class MetricContribution:
    metric_name: str
    metric_version: str
    metric_definition_hash: str
    focal_entity_id: str
    answer_pub_id: str
    query_key: str
    design_cell_key: str
    eligibility_status: EligibilityStatus
    reason_codes: tuple[str, ...]
    outcome_value: Any
    numerator_contribution: Decimal
    denominator_contribution: Decimal
    query_weight: Decimal
    design_cell_weight: Decimal
    repeat_weight: Decimal
    final_weight: Decimal
    weighted_numerator: Decimal
    weighted_denominator: Decimal
    missing_bound_weight: Decimal
    supporting_event_pub_ids: tuple[str, ...]
    supporting_decision_pub_ids: tuple[str, ...]
    semantic_decision_set_hash: str
    evaluation_hash: str
    query_context_fact_pub_id: str = ""
    semantic_manifest_pub_id: str = ""
    model: str = ""
    region: str = ""
    mode: str = ""
    capture_time: datetime | None = None
    dimension_snapshot: Mapping[str, Any] = field(default_factory=dict)
    answer_detail_ref: str = ""
    contribution_hash: str = ""


@dataclass(frozen=True, slots=True)
class MetricQueryContribution:
    metric_name: str
    metric_version: str
    focal_entity_id: str
    query_key: str
    numerator: Decimal
    denominator: Decimal
    value: Decimal | None
    unknown_weight: Decimal
    query_weight: Decimal
    design_cell_count: int
    answer_count: int
    known_answer_count: int = 0
    unknown_answer_count: int = 0
    query_context_fact_pub_id: str = ""
    reason_codes: tuple[str, ...] = ()
    contribution_hash: str = ""


@dataclass(frozen=True, slots=True)
class MetricDesignCellContribution:
    metric_name: str
    metric_version: str
    focal_entity_id: str
    query_key: str
    design_cell_key: str
    model: str
    region: str
    mode: str
    planned_repeat_count: int
    observed_repeat_count: int
    known_repeat_count: int
    unknown_repeat_count: int
    design_cell_weight: Decimal
    query_weight: Decimal
    status: str
    contribution_hash: str = ""


@dataclass(frozen=True, slots=True)
class MetricSnapshotSet:
    scope_hash: str
    dependency_bundle_hash: str
    as_of: datetime
    aggregation_method: str
    snapshots: tuple[MetricSnapshot, ...]
    snapshot_set_hash: str
    state: str
    answer_contributions: tuple[MetricContribution, ...] = ()
    query_contributions: tuple[MetricQueryContribution, ...] = ()
    design_cell_contributions: tuple[MetricDesignCellContribution, ...] = ()
    scope: Mapping[str, Any] = field(default_factory=dict)
    dependency_bundle: Mapping[str, Any] = field(default_factory=dict)
    design_basis: str = "planned_cells"
    focal_entity_ids: tuple[str, ...] = ()
    query_set_hash: str = ""
    design_set_hash: str = ""
    snapshot_count: int = 0
    failure_codes: tuple[str, ...] = ()
