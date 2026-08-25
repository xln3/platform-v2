"""Strict HTTP contracts for the Service 2 all-U corpus plane."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.scoring.service2_source_corpus import (
    AttributionConfidence,
    DisparagementLevel,
    FactAnchorState,
    Ledger,
    RelationDirection,
    has_public_evidence_candidate,
    has_reviewable_evidence,
)

from .analysis_models import DEFAULT_SERVICE2_ANALYSIS_MODEL


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class BatchCreate(StrictModel):
    run_pub_ids: list[str] = Field(min_length=1, max_length=500)
    analysis_model: str = Field(
        default=DEFAULT_SERVICE2_ANALYSIS_MODEL,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    window_start: datetime
    window_end: datetime
    source_snapshot_boundary: datetime
    corpus_policy_version: Literal["service2-all-u-occurrence-v1"] = "service2-all-u-occurrence-v1"
    judgment_policy_version: Literal["service2-entity-relation-v1"] = "service2-entity-relation-v1"

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if len(set(self.run_pub_ids)) != len(self.run_pub_ids):
            raise ValueError("duplicate_run_pub_ids")
        if any(not value.strip() for value in self.run_pub_ids):
            raise ValueError("empty_run_pub_id")
        if any(
            value.tzinfo is None
            for value in (self.window_start, self.window_end, self.source_snapshot_boundary)
        ):
            raise ValueError("timezone_required")
        if self.window_start > self.window_end:
            raise ValueError("window_start_after_end")
        if self.source_snapshot_boundary < self.window_end:
            raise ValueError("snapshot_boundary_before_window_end")
        return self


BatchStatus = Literal[
    "draft",
    "queued",
    "running",
    "paused",
    "cancel_requested",
    "cancelled",
    "review",
    "frozen",
    "failed",
]
CorpusProcessingState = Literal[
    "queued",
    "fetching",
    "retry_wait",
    "manual_evidence_required",
    "processed",
    "partial",
    "blocked",
    "gone",
    "unobservable",
    "failed",
    "cancelled",
]
CorpusFetchState = Literal[
    "queued",
    "fetching",
    "succeeded",
    "partial",
    "blocked",
    "gone",
    "retry_wait",
    "failed",
    "unobserved",
]
CorpusReviewState = Literal[
    "unreviewed",
    "not_applicable",
    "in_review",
    "accepted",
    "rejected",
]
FindingReviewState = Literal["unreviewed", "accepted", "rejected", "needs_changes"]
FindingLevel = Literal["L0", "L1", "L2a", "L2b", "L3a", "L3b", "L4"]


class CoverageSummary(StrictModel):
    selected_queries: int = Field(ge=0)
    successful_queries: int = Field(ge=0)
    failed_queries: int = Field(ge=0)
    successful_queries_with_u: int = Field(ge=0)
    successful_queries_without_u: int = Field(ge=0)
    query_failure_codes: dict[str, int]
    query_outcomes_complete: bool
    query_coverage_complete: bool
    expected_occurrences: int = Field(ge=0)
    materialized_items: int = Field(ge=0)
    distinct_urls: int = Field(ge=0)
    processing_states: dict[str, int]
    fetch_states: dict[str, int]
    entered_judgment: int = Field(ge=0)
    findings: int = Field(ge=0)
    reviewed_findings: int = Field(ge=0)
    eligible_cases: int = Field(ge=0)
    coverage_complete: bool


class BatchView(StrictModel):
    schema_version: Literal["formal-service2-source-corpus-v2"] = "formal-service2-source-corpus-v2"
    batch_pub_id: str
    project_pub_id: str
    service_entitlement_pub_id: str
    service_entitlement_revision: str
    run_pub_ids: list[str]
    analysis_model: str
    window_start: datetime
    window_end: datetime
    source_snapshot_boundary: datetime
    corpus_policy_version: str
    judgment_policy_version: str
    status: BatchStatus
    version: int = Field(ge=1)
    workflow_id: str | None
    frozen_at: datetime | None
    manifest_hash: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    coverage: CoverageSummary


class BatchPage(StrictModel):
    data: list[BatchView]
    next_cursor: str | None
    has_more: bool


class CorpusItemView(StrictModel):
    item_pub_id: str
    occurrence_pub_id: str
    run_pub_id: str
    answer_pub_id: str
    source_url_pub_id: str
    snapshot_pub_id: str | None
    source_document_pub_id: str | None
    fetch_attempt_pub_id: str | None
    raw_url: str
    canonical_url: str
    site_host: str
    occurrence_ordinal: int = Field(ge=1)
    u_rank: int | None = Field(default=None, ge=1)
    captured_at: datetime
    platform: str
    model: str
    region: str
    collection_surface: str | None
    question: str
    retrieval_query: str | None
    u_state: str
    fetch_state: CorpusFetchState
    processing_state: CorpusProcessingState
    entity_state: str
    judgment_state: str
    review_state: CorpusReviewState
    entered_judgment: bool
    finding_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    failure_code: str | None
    manual_evidence_state: str
    version: int = Field(ge=1)


class CorpusPage(StrictModel):
    schema_version: Literal["internal-service2-corpus-items-v1"] = (
        "internal-service2-corpus-items-v1"
    )
    batch_pub_id: str
    data: list[CorpusItemView]
    filtered_count: int = Field(ge=0)
    all_u_total: int = Field(ge=0)
    next_cursor: str | None
    has_more: bool


class AttributionInput(StrictModel):
    party: str | None = Field(default=None, max_length=500)
    confidence: AttributionConfidence = AttributionConfidence.UNKNOWN
    evidence: list[dict[str, object]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_unknown(self) -> Self:
        if self.confidence == AttributionConfidence.UNKNOWN and self.party:
            raise ValueError("unknown_attribution_cannot_name_party")
        if self.confidence != AttributionConfidence.UNKNOWN and not has_reviewable_evidence(
            tuple(self.evidence)
        ):
            raise ValueError("attribution_confidence_requires_reviewable_evidence")
        return self


class OrthogonalFlagsInput(StrictModel):
    comparison_present: bool = False
    peer_elevated: bool = False
    scope_narrowed: bool = False
    industry_wide: bool = False
    direct_target_negative: bool = False
    secondary_position: bool = False
    comparison_manipulated: bool = False
    key_fact_omitted: bool = False


class FindingCreate(StrictModel):
    corpus_item_pub_id: str
    snapshot_pub_id: str
    ledger: Ledger
    level: DisparagementLevel
    relation_direction: RelationDirection
    textual_speaker: str = Field(min_length=1, max_length=1000)
    target_entity: str = Field(min_length=1, max_length=1000)
    beneficiary_entity: str | None = Field(default=None, max_length=1000)
    is_disparagement: bool
    fact_anchor_state: FactAnchorState
    evidence_quote: str = Field(min_length=1, max_length=20_000)
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=1)
    context_text: str = Field(min_length=1, max_length=50_000)
    context_start: int = Field(ge=0)
    context_end: int = Field(ge=1)
    snapshot_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_anchor_pub_id: str | None = None
    flags: OrthogonalFlagsInput
    comparison_dimensions: list[str] = Field(default_factory=list, max_length=30)
    omitted_facts: list[str] = Field(default_factory=list, max_length=30)
    method: Literal["llm", "human", "dictionary_experimental"]
    model: str = Field(max_length=120)
    prompt_version: str = Field(min_length=1, max_length=80)
    policy_version: Literal["service2-entity-relation-v1"] = "service2-entity-relation-v1"
    confidence: float = Field(ge=0, le=1)
    publisher: AttributionInput = Field(default_factory=AttributionInput)
    commissioner: AttributionInput = Field(default_factory=AttributionInput)
    factcheck_claim: str | None = Field(default=None, max_length=20_000)
    factcheck_verdict: Literal["supported", "refuted", "mixed", "unverifiable"] | None = None
    factcheck_evidence: list[dict[str, object]] = Field(default_factory=list, max_length=100)
    factcheck_boundary: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_factcheck(self) -> Self:
        if self.factcheck_verdict is None:
            if self.factcheck_evidence or self.factcheck_boundary:
                raise ValueError("factcheck_verdict_required_for_result")
            return self
        if not self.factcheck_claim or not self.factcheck_claim.strip():
            raise ValueError("factcheck_claim_required_for_verdict")
        if self.factcheck_verdict == "unverifiable":
            if not self.factcheck_boundary or not self.factcheck_boundary.strip():
                raise ValueError("unverifiable_factcheck_requires_boundary")
        elif not has_public_evidence_candidate(tuple(self.factcheck_evidence)):
            raise ValueError("factcheck_verdict_requires_evidence")
        return self


class FindingView(StrictModel):
    finding_pub_id: str
    batch_pub_id: str
    corpus_item_pub_id: str
    occurrence_pub_id: str
    snapshot_pub_id: str
    canonical_url: str
    ledger: Ledger
    level: DisparagementLevel
    relation_direction: RelationDirection
    textual_speaker: str
    target_entity: str
    beneficiary_entity: str | None
    is_disparagement: bool
    fact_anchor_state: FactAnchorState
    evidence_quote: str
    quote_start: int
    quote_end: int
    context_text: str
    context_start: int
    context_end: int
    snapshot_text_sha256: str
    visual_anchor_pub_id: str | None
    visual_evidence_pub_id: str | None
    visual_bbox: tuple[float, float, float, float] | None
    visual_page_number: int | None
    visual_validation_status: Literal["verified", "unavailable", "mismatch", "needs_review"]
    flags: OrthogonalFlagsInput
    comparison_dimensions: list[str]
    omitted_facts: list[str]
    method: Literal["llm", "human", "dictionary_experimental"]
    policy_version: str
    confidence: float
    validation_status: Literal["exact", "needs_review", "rejected", "experimental"]
    validation_failures: list[str]
    publisher: AttributionInput
    commissioner: AttributionInput
    factcheck_claim: str | None
    factcheck_verdict: Literal["supported", "refuted", "mixed", "unverifiable"] | None
    factcheck_evidence: list[dict[str, object]]
    factcheck_boundary: str | None
    current_review_state: FindingReviewState
    version: int = Field(ge=1)
    created_at: datetime


class FindingPage(StrictModel):
    schema_version: Literal["internal-service2-findings-v1"] = "internal-service2-findings-v1"
    batch_pub_id: str
    data: list[FindingView]
    filtered_count: int = Field(ge=0)
    all_findings_total: int = Field(ge=0)
    next_cursor: str | None
    has_more: bool


class FindingReviewCreate(StrictModel):
    decision: Literal["accepted", "rejected", "needs_changes"]
    reason_code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_:-]+$")
    rationale: str = Field(min_length=1, max_length=4000)


class LifecycleReceipt(StrictModel):
    batch_pub_id: str
    status: BatchStatus
    version: int = Field(ge=1)
    replayed: bool


class FrozenManifestView(StrictModel):
    schema_version: Literal["formal-service2-source-corpus-v2"] = "formal-service2-source-corpus-v2"
    batch_pub_id: str
    manifest_pub_id: str
    revision: int = Field(ge=1)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    facts: dict[str, object]
    created_at: datetime


class FrozenManifestOptionView(StrictModel):
    schema_version: Literal["formal-service2-source-corpus-v2"] = "formal-service2-source-corpus-v2"
    batch_pub_id: str
    manifest_pub_id: str
    revision: int = Field(ge=1)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    window_start: datetime
    window_end: datetime
    created_at: datetime


class AnalysisModelOptionView(StrictModel):
    model: str
    label: str
    provider: str
    tier: str
    capability: str
    web_search_mode: str
    input_usd_per_million_tokens: float | None
    output_usd_per_million_tokens: float | None
    context_window_tokens: int | None
    web_search_audit_status: Literal["verified_provider_citation"]
    web_search_audited_at: str
    auditable_source_mode: Literal["provider_citation", "provider_tool"]
    recommended: bool
    catalog_revision: str
    pricing_observed_at: str
    pricing_source_url: str
    pricing_currency: Literal["USD"]
    token_price_unit: Literal["per_million_tokens"]
    web_search_usd_per_call: float | None
    web_search_pricing_status: Literal["not_published_in_catalog_snapshot"]
    pricing_notice: Literal["catalog_snapshot_provider_invoice_authoritative"]
    web_search_audit_policy: Literal["provider_search_event_and_provider_citation_required"]


class AnalysisModelCatalogView(StrictModel):
    default_model: str
    models: list[AnalysisModelOptionView]
    credential_source: Literal["server_environment_only"] = "server_environment_only"


__all__ = [
    "AnalysisModelCatalogView",
    "AnalysisModelOptionView",
    "BatchCreate",
    "BatchPage",
    "BatchView",
    "CorpusFetchState",
    "CorpusPage",
    "CorpusProcessingState",
    "CorpusReviewState",
    "FindingCreate",
    "FindingLevel",
    "FindingPage",
    "FindingReviewState",
    "FindingReviewCreate",
    "FindingView",
    "FrozenManifestView",
    "LifecycleReceipt",
]
