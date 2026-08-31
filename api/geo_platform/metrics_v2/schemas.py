from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PublicId = Annotated[
    str,
    Field(min_length=5, max_length=120, pattern=r"^[a-z][a-z0-9]*_[A-Za-z0-9_-]+$"),
]
MetricState = Literal["ready", "limited", "insufficient", "experimental", "failed"]
EligibilityStatus = Literal[
    "included_hit",
    "included_miss",
    "excluded",
    "not_applicable",
    "analysis_unknown",
    "analysis_failed",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotWindow(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start > self.end:
            raise ValueError("window_start_after_end")
        if (self.end - self.start).days > 366:
            raise ValueError("window_too_large")
        return self


class SnapshotFilters(StrictModel):
    model: list[str] = Field(default_factory=list, max_length=100)
    region: list[str] = Field(default_factory=list, max_length=100)
    mode: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("model", "region", "mode")
    @classmethod
    def normalize_filter_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("invalid_filter_value")
        return sorted(set(normalized))


class SnapshotRequest(StrictModel):
    window: SnapshotWindow
    filters: SnapshotFilters = Field(default_factory=SnapshotFilters)
    focal_entity_ids: list[str] = Field(max_length=100)
    aggregation_method: Literal["query_macro"] = "query_macro"
    publication_channel: Literal["shadow"] = "shadow"
    idempotency_key: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    )

    @field_validator("focal_entity_ids")
    @classmethod
    def normalize_entities(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in normalized):
            raise ValueError("invalid_focal_entity_id")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate_focal_entity_id")
        return sorted(normalized)


class SnapshotRequestAccepted(StrictModel):
    schema_version: Literal["metric-snapshot-request-v2"] = "metric-snapshot-request-v2"
    job_pub_id: PublicId | None = None
    snapshot_set_pub_id: PublicId | None = None
    status: Literal["pending", "running", "succeeded"]
    reused: bool = False
    scope_hash: Hash


class MetricDefinitionView(StrictModel):
    metric_name: str = Field(min_length=1, max_length=160)
    metric_version: str = Field(min_length=1, max_length=80)
    business_question: str = Field(min_length=1, max_length=1_000)
    definition_hash: Hash
    status: Literal["draft", "experimental", "published", "retired", "legacy"]
    unit_type: Literal["answer", "claim", "relation", "citation", "dimension", "design_cell"]
    outcome_source: Literal["deterministic_expression", "semantic_decision", "hybrid"]
    aggregation_methods: list[str]
    required_semantic_capabilities: list[str]
    decision_task_refs: list[dict[str, Any]]
    query_predicate: dict[str, Any]
    answer_eligibility_predicate: dict[str, Any]
    outcome_expression: dict[str, Any]
    denominator_description: str
    semantic_rubric_ref: str | None = None


class MetricCatalogView(StrictModel):
    schema_version: Literal["metric-catalog-v2"] = "metric-catalog-v2"
    definitions: list[MetricDefinitionView]


class CoverageView(StrictModel):
    collection: float | None = Field(default=None, ge=0, le=1)
    query_context: float | None = Field(default=None, ge=0, le=1)
    semantic: float | None = Field(default=None, ge=0, le=1)
    evidence: float | None = Field(default=None, ge=0, le=1)
    semantic_by_capability: dict[str, float] = Field(default_factory=dict)

    @field_validator("semantic_by_capability")
    @classmethod
    def validate_capability_coverage(cls, values: dict[str, float]) -> dict[str, float]:
        if any(value < 0 or value > 1 for value in values.values()):
            raise ValueError("invalid_capability_coverage")
        return values


class IntervalView(StrictModel):
    lower: float | None = Field(default=None, ge=0)
    upper: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("interval_lower_above_upper")
        return self


class MetricSnapshotView(StrictModel):
    snapshot_pub_id: PublicId
    snapshot_hash: Hash
    focal_entity_id: str
    metric_name: str
    metric_version: str
    metric_definition_hash: Hash
    state: MetricState
    state_reason_codes: list[str] = Field(default_factory=list)
    value: float | None = None
    observed_value: float | None = None
    answer_weighted_value: float | None = None
    raw_numerator: float = Field(ge=0)
    raw_denominator: float = Field(ge=0)
    weighted_numerator: float = Field(ge=0)
    weighted_denominator: float = Field(ge=0)
    coverage: CoverageView
    decision_method_mix: dict[str, float] = Field(default_factory=dict)
    adjudication_sensitivity: IntervalView
    missing_bounds: IntervalView
    unique_query_count: int = Field(ge=0)
    candidate_answer_count: int = Field(ge=0)
    known_answer_count: int = Field(ge=0)
    unknown_answer_count: int = Field(ge=0)
    failed_answer_count: int = Field(default=0, ge=0)
    not_applicable_answer_count: int = Field(ge=0)
    excluded_answer_count: int = Field(ge=0)
    design_cell_count: int = Field(ge=0)
    contribution_set_hash: Hash
    query_contribution_set_hash: Hash
    design_contribution_set_hash: Hash

    @model_validator(mode="after")
    def validate_publishable_value(self) -> Self:
        if self.state in {"insufficient", "experimental", "failed"} and self.value is not None:
            raise ValueError("non_publishable_metric_has_value")
        return self


class SnapshotSetView(StrictModel):
    schema_version: Literal["metric-snapshot-set-v2"] = "metric-snapshot-set-v2"
    snapshot_set_pub_id: PublicId
    snapshot_set_hash: Hash
    project_pub_id: PublicId
    state: Literal["ready", "partial", "failed"]
    as_of: datetime
    window: SnapshotWindow
    filters: SnapshotFilters
    focal_entity_ids: list[str]
    aggregation_method: Literal["query_macro"]
    design_basis: Literal["planned_cells", "observed_cells"]
    scope_hash: Hash
    dependency_bundle_hash: Hash
    metrics: list[MetricSnapshotView]


class MetricSnapshotDetailView(MetricSnapshotView):
    snapshot_set_pub_id: PublicId
    formula: dict[str, Any]
    denominator_description: str
    required_semantic_capabilities: list[str]
    decision_task_refs: list[dict[str, Any]]
    bootstrap: dict[str, Any] = Field(default_factory=dict)
    calibration_artifact_hashes: list[str] = Field(default_factory=list)


class SupportingDecisionView(StrictModel):
    decision_pub_id: PublicId
    decision_hash: Hash
    task: str
    version: str
    method: Literal["deterministic", "model", "hybrid", "human"]
    status: Literal["accepted", "abstained", "review_required", "failed"]
    result: dict[str, Any]
    reason_codes: list[str] = Field(default_factory=list)
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    rubric_hash: Hash
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    rationale_summary: str | None = Field(default=None, max_length=2_000)


class SupportingEventView(StrictModel):
    event_pub_id: PublicId
    event_type: str
    subject_entity_id: str | None = None
    object_entity_id: str | None = None
    event_value: dict[str, Any]
    answer_text_start: int | None = Field(default=None, ge=0)
    answer_text_end: int | None = Field(default=None, ge=1)
    answer_excerpt: str | None = Field(default=None, max_length=5_000)


class AnswerContributionView(StrictModel):
    answer_pub_id: PublicId
    query_pub_id: str | None = None
    query_key: str
    query_text: str | None = Field(default=None, max_length=20_000)
    analysis_lenses: list[str]
    requested_operations: list[str]
    exposure_role: str
    model: str
    region: str
    mode: str
    capture_time: datetime
    eligibility_status: EligibilityStatus
    reason_codes: list[str] = Field(min_length=1)
    outcome_value: Any = None
    numerator_contribution: float
    denominator_contribution: float
    query_weight: float = Field(ge=0)
    design_cell_weight: float = Field(ge=0)
    repeat_weight: float = Field(ge=0)
    final_weight: float = Field(ge=0)
    weighted_numerator: float
    weighted_denominator: float
    semantic_manifest_pub_id: str | None = None
    supporting_events: list[SupportingEventView] = Field(default_factory=list)
    supporting_decisions: list[SupportingDecisionView] = Field(default_factory=list)
    answer_excerpt: str | None = Field(default=None, max_length=5_000)
    answer_detail_href: str
    contribution_hash: Hash


class ContributionTotalsView(StrictModel):
    snapshot_candidate_count: int = Field(ge=0)
    filtered_count: int = Field(ge=0)
    raw_numerator: float = Field(ge=0)
    raw_denominator: float = Field(ge=0)
    weighted_numerator: float = Field(ge=0)
    weighted_denominator: float = Field(ge=0)
    contribution_set_hash: Hash


class ContributionPageView(StrictModel):
    schema_version: Literal["metric-contributions-v2"] = "metric-contributions-v2"
    snapshot_pub_id: PublicId
    totals: ContributionTotalsView
    data: list[AnswerContributionView]
    next_cursor: str | None = None
    has_more: bool


class QueryContributionView(StrictModel):
    query_key: str
    query_pub_id: str | None = None
    query_text: str | None = None
    query_weight: float = Field(ge=0)
    numerator: float = Field(ge=0)
    denominator: float = Field(ge=0)
    value: float | None = None
    unknown_weight: float = Field(ge=0)
    design_cell_count: int = Field(ge=0)
    answer_count: int = Field(ge=0)
    contribution_hash: Hash


class QueryContributionPageView(StrictModel):
    schema_version: Literal["metric-query-contributions-v2"] = "metric-query-contributions-v2"
    snapshot_pub_id: PublicId
    snapshot_candidate_count: int = Field(ge=0)
    filtered_count: int = Field(ge=0)
    data: list[QueryContributionView]
    next_cursor: str | None = None
    has_more: bool


class SemanticEventDetailView(StrictModel):
    schema_version: Literal["answer-semantic-event-v2"] = "answer-semantic-event-v2"
    event_pub_id: PublicId
    project_pub_id: PublicId
    answer_pub_id: PublicId
    semantic_manifest_pub_id: PublicId
    event_type: str
    subject_entity_id: str | None = None
    object_entity_id: str | None = None
    event_value: dict[str, Any]
    qualifiers: dict[str, Any]
    answer_text_start: int | None = None
    answer_text_end: int | None = None
    offset_unit: Literal["unicode_code_point_v1"]
    answer_excerpt: str | None = None
    answer_excerpt_hash: Hash | None = None
    derivation_method: Literal["deterministic", "model", "hybrid", "human"]
    decision_record_pub_ids: list[str]
    review_status: str
    event_fingerprint: Hash
    answer_detail_href: str


class SemanticDecisionDetailView(StrictModel):
    schema_version: Literal["semantic-decision-record-v2"] = "semantic-decision-record-v2"
    decision_pub_id: PublicId
    project_pub_id: PublicId
    task_name: str
    task_version: str
    subject_type: str
    subject_key: str
    method: Literal["deterministic", "model", "hybrid", "human"]
    status: Literal["accepted", "abstained", "review_required", "failed"]
    result: dict[str, Any]
    rationale_summary: str | None = Field(default=None, max_length=2_000)
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str]
    evidence_refs: list[dict[str, Any]]
    evidence_spans: list[dict[str, Any]]
    judge_policy_hash: Hash
    rubric_ref: str
    rubric_hash: Hash
    decision_hash: Hash
    created_at: datetime


class JobView(StrictModel):
    schema_version: Literal["metric-job-v2", "semantic-decision-job-v2"]
    job_pub_id: PublicId
    project_pub_id: PublicId
    status: Literal["pending", "running", "succeeded", "abstained", "review_required", "failed"]
    state_reason_codes: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    snapshot_set_pub_id: str | None = None
    selected_decision_pub_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExportCreate(StrictModel):
    format: Literal["xlsx", "csv_zip"] = "xlsx"


class ExportView(StrictModel):
    schema_version: Literal["metric-export-v2"] = "metric-export-v2"
    export_pub_id: PublicId
    snapshot_set_pub_id: PublicId
    status: Literal["pending", "running", "succeeded", "failed"]
    format: Literal["xlsx", "csv_zip"]
    artifact_hash: Hash | None = None
    download_url: str | None = None
    expires_at: datetime | None = None


class PublishRequest(StrictModel):
    publication_channel: Literal["shadow", "official"]
    expected_generation: int = Field(ge=0)
    expected_snapshot_set_hash: Hash


class PublicationView(StrictModel):
    schema_version: Literal["metric-publication-v2"] = "metric-publication-v2"
    project_pub_id: PublicId
    scope_hash: Hash
    snapshot_set_pub_id: PublicId
    publication_channel: Literal["shadow", "official"]
    generation: int = Field(ge=1)
    published_at: datetime


class RecomputeRequest(StrictModel):
    project_pub_id: PublicId
    window: SnapshotWindow
    focal_entity_ids: list[str] = Field(min_length=1, max_length=100)
    trigger_reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[\x20-\x7e]+$")


class SemanticBackfillCandidateView(StrictModel):
    answer_pub_id: PublicId
    query_text: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=120)
    mode: str = Field(min_length=1, max_length=80)
    channel: str = Field(min_length=1, max_length=80)
    capture_time: datetime
    preparation_state: Literal["ready", "unknown"]
    reason_codes: list[str] = Field(default_factory=list)


class SemanticBackfillModelView(StrictModel):
    model: str
    label: str
    provider: str
    tier: Literal["economy", "premium"]
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)
    context_window_tokens: int = Field(gt=0)
    recommended: bool
    catalog_revision: str
    pricing_observed_at: str
    pricing_source_url: str
    pricing_currency: Literal["USD"]
    token_price_unit: Literal["per_million_tokens"]
    pricing_notice: Literal["catalog_snapshot_provider_invoice_authoritative"]


class SemanticBackfillOptionsView(StrictModel):
    schema_version: Literal["semantic-backfill-options-v2"] = "semantic-backfill-options-v2"
    project_pub_id: PublicId
    as_of: datetime
    candidate_count: int = Field(ge=0)
    candidates: list[SemanticBackfillCandidateView]
    next_cursor: str | None = None
    max_batch_size: int = Field(ge=1, le=100)
    default_model: str
    models: list[SemanticBackfillModelView]


class SemanticBackfillPlanRequest(StrictModel):
    answer_pub_ids: list[PublicId] = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=120)
    as_of: datetime

    @field_validator("answer_pub_ids")
    @classmethod
    def unique_answer_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate_answer_pub_id")
        return sorted(values)


class SemanticBackfillPlanView(StrictModel):
    schema_version: Literal["semantic-backfill-plan-v2"] = "semantic-backfill-plan-v2"
    project_pub_id: PublicId
    model: str
    as_of: datetime
    window: SnapshotWindow
    focal_entity_ids: list[str] = Field(min_length=1, max_length=100)
    selected_answer_count: int = Field(ge=0)
    executable_answer_count: int = Field(ge=0)
    preparation_unknown_count: int = Field(ge=0)
    estimated_atomic_decisions: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    estimated_cost_high_usd: float = Field(ge=0)
    budget_limit_usd: float = Field(ge=0)
    selection_hash: Hash
    confirmation_token: Hash
    start_allowed: bool
    blocker_codes: list[str] = Field(default_factory=list)
    estimate_notice: Literal["bounded_estimate_provider_invoice_authoritative"] = (
        "bounded_estimate_provider_invoice_authoritative"
    )


class SemanticBackfillStartRequest(SemanticBackfillPlanRequest):
    selection_hash: Hash
    confirmation_token: Hash


class SemanticBackfillStartView(StrictModel):
    schema_version: Literal["semantic-backfill-start-v2"] = "semantic-backfill-start-v2"
    project_pub_id: PublicId
    workflow_id: str
    job_pub_id: PublicId
    selection_hash: Hash
    status: Literal["started", "reused"]
    selected_answer_count: int = Field(ge=1, le=100)
    model: str


class SemanticBackfillStatusView(StrictModel):
    schema_version: Literal["semantic-backfill-status-v2"] = "semantic-backfill-status-v2"
    project_pub_id: PublicId
    selection_hash: Hash
    workflow_id: str
    status: Literal["running", "succeeded", "failed"]
    processed_answer_count: int = Field(ge=0)
    metric_evaluation_count: int = Field(ge=0)
    snapshot_set_pub_id: PublicId | None = None
    failure_code: str | None = None


class DecisionOverrideRequest(StrictModel):
    project_pub_id: PublicId
    result: dict[str, Any]
    rationale_summary: str = Field(min_length=1, max_length=1_000)
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    expected_decision_hash: Hash


class DecisionOverrideView(StrictModel):
    schema_version: Literal["semantic-decision-override-v2"] = "semantic-decision-override-v2"
    decision_pub_id: PublicId
    supersedes_pub_id: PublicId
    decision_hash: Hash
    recompute_job_pub_id: PublicId
    recompute_job_pub_ids: list[PublicId] = Field(default_factory=list)
