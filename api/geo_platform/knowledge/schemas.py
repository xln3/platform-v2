"""Versioned public contracts for the knowledge-evolution service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.knowledge_evolution.contracts import (
    DecisionScope,
    KnowledgeStatus,
    ReasoningPolicy,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RuntimeResolveRequest(StrictModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    namespace: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    domain: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    task: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    items: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)
    policy: ReasoningPolicy = ReasoningPolicy.DETERMINISTIC_ONLY
    policy_id: str = Field(default="caller-policy", min_length=1, max_length=120)
    policy_version: str = Field(default="1", min_length=1, max_length=120)
    adopt_model_inferred: bool = False
    on_model_failure: Literal["fail", "degrade"] = "degrade"
    expected_release_id: str | None = Field(default=None, min_length=1, max_length=128)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    allow_external_model: bool = False
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    max_latency_ms: int | None = Field(default=None, ge=1, le=600_000)
    max_cost_usd: float | None = Field(default=None, ge=0, le=100)

    @field_validator("context")
    @classmethod
    def context_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Bound generic payloads without teaching the core any domain fields.
        if len(repr(value).encode()) > 256 * 1024:
            raise ValueError("context_too_large")
        return value


class ReleaseRefView(StrictModel):
    release_id: str
    content_hash: str
    schema_version: str
    source: str
    degraded: bool


class KnowledgeModelOptionView(StrictModel):
    model: str
    label: str
    provider: str
    model_version: str
    capability: str
    strict_output_verified: bool
    tool_capability_status: Literal["verified", "not_required", "not_verified"]
    verified_at: str | None
    verification_reference: str | None
    input_usd_per_million_tokens: float | None
    output_usd_per_million_tokens: float | None
    pricing_status: Literal["catalog_snapshot", "unknown"]
    pricing_currency: Literal["USD"]
    token_price_unit: Literal["per_million_tokens"]
    pricing_observed_at: str | None
    pricing_source_url: str | None
    pricing_notice: Literal["catalog_snapshot_provider_invoice_authoritative"]
    catalog_revision: str
    is_default: bool
    recommended: bool


class KnowledgeModelCatalogView(StrictModel):
    status: Literal["ready", "unavailable"]
    catalog_revision: str
    default_model: str | None
    models: list[KnowledgeModelOptionView]
    unavailable_reason: str | None


class DecisionView(StrictModel):
    input_id: str
    input_value: str
    value: dict[str, Any]
    knowledge_status: KnowledgeStatus
    decision_scope: DecisionScope
    confidence: float
    reasons: list[str]
    alternative_hypotheses: list[str]
    uncertainty: list[str]
    evidence_refs: list[str]
    adopted: bool
    model_provider: str | None
    model_name: str | None
    model_version: str | None
    requested_model_name: str | None = None
    model_identity_source: str | None = None
    prompt_id: str | None
    prompt_version: str | None
    knowledge_release_id: str | None
    knowledge_content_hash: str | None
    policy_id: str | None
    policy_version: str | None
    tool_summary: list[dict[str, Any]]


class RuntimeResolveResponse(StrictModel):
    request_id: str
    domain: str
    task: str
    policy: ReasoningPolicy
    policy_id: str
    policy_version: str
    release: ReleaseRefView
    decisions: list[DecisionView]
    model_hypotheses: list[DecisionView]
    prompt_id: str | None
    prompt_version: str | None
    model_provider: str | None
    model_name: str | None
    model_version: str | None
    requested_model_name: str | None = None
    model_identity_source: str | None = None
    model_catalog_revision: str | None = None
    model_inference_used: bool = False
    model_inference_adopted: bool = False
    provider_call_attempted: bool = False
    latency_ms: int
    cache_status: str
    degradation: list[str]
    observation_count: int
    usage: dict[str, Any]


class ObservationInput(StrictModel):
    namespace: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    domain: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    task: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    surface_form: str = Field(min_length=1, max_length=1_000)
    normalized_key: str = Field(min_length=1, max_length=1_000)
    source_type: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:/-]+$")
    source_ref_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    safe_context: str | None = Field(default=None, max_length=2_000)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    visibility: Literal["private", "tenant", "public"] = "private"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(repr(value).encode()) > 64 * 1024:
            raise ValueError("payload_too_large")
        return value


class ObservationBatchRequest(StrictModel):
    observations: list[ObservationInput] = Field(min_length=1, max_length=500)


class IngestReceipt(StrictModel):
    accepted: int
    duplicate: int
    receipt_id: str


class CandidateView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    aggregation_key: str
    surface_forms: list[str]
    observation_count: int
    source_count: int
    state: str
    priority: int
    policy_version: str
    evidence_version: str
    reopen_reason: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class CandidatePage(StrictModel):
    data: list[CandidateView]
    total: int


class ProposalCreate(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)
    candidate_pub_id: str | None = Field(default=None, max_length=40)
    operation: Literal["create", "update", "merge", "split", "retire"]
    target_stable_id: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any]
    alternatives: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    confidence: dict[str, Any] = Field(default_factory=dict)
    model_provider: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=120)
    model_version: str | None = Field(default=None, max_length=120)
    prompt_id: str | None = Field(default=None, max_length=120)
    prompt_version: str | None = Field(default=None, max_length=120)
    policy_version: str = Field(min_length=1, max_length=120)


class ProposalView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    candidate_pub_id: str | None
    operation: str
    target_stable_id: str | None
    payload: dict[str, Any]
    alternatives: list[dict[str, Any]]
    confidence: dict[str, Any]
    model_provider: str | None
    model_name: str | None
    model_version: str | None
    prompt_id: str | None
    prompt_version: str | None
    policy_version: str
    state: str
    created_by: str
    created_at: datetime


class EvidenceCreate(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)
    candidate_pub_id: str | None = Field(default=None, max_length=40)
    proposal_pub_id: str | None = Field(default=None, max_length=40)
    source_uri: str | None = Field(default=None, max_length=2_000)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    publisher: str = Field(min_length=1, max_length=240)
    claim: str = Field(min_length=1, max_length=4_000)
    stance: Literal["supports", "opposes", "neutral"]
    summary: str = Field(min_length=1, max_length=4_000)
    trust_tier: Literal["authoritative", "primary", "secondary", "unverified"]
    visibility: Literal["private", "tenant", "public"]
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    acquired_at: datetime

    @field_validator("source_uri")
    @classmethod
    def public_evidence_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("evidence_uri_must_be_public_https")
        return value


class EvidenceView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    candidate_pub_id: str | None
    proposal_pub_id: str | None
    source_uri: str | None
    content_hash: str
    publisher: str
    claim: str
    stance: str
    summary: str
    trust_tier: str
    visibility: str
    data_classification: str
    acquired_at: datetime
    created_by: str
    created_at: datetime


class AdjudicationCreate(StrictModel):
    decision: Literal["approved", "rejected", "deferred"]
    reason: str = Field(min_length=10, max_length=4_000)
    policy_version: str = Field(min_length=1, max_length=120)
    before_value: dict[str, Any] = Field(default_factory=dict)
    after_value: dict[str, Any] = Field(default_factory=dict)


class AdjudicationView(StrictModel):
    pub_id: str
    proposal_pub_id: str
    decision: str
    reason: str
    policy_version: str
    before_value: dict[str, Any]
    after_value: dict[str, Any]
    decided_by: str
    decided_at: datetime


class CandidateReopen(StrictModel):
    reason: str = Field(min_length=10, max_length=2_000)
    policy_version: str | None = Field(default=None, max_length=120)
    evidence_version: str | None = Field(default=None, max_length=120)
    manual_override: bool = False


class ChangeSetCreate(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)
    base_release_id: str | None = Field(default=None, max_length=128)
    changes: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    dependency_ids: list[str] = Field(default_factory=list, max_length=100)
    conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    visibility: Literal["private", "tenant", "public"] = "tenant"


class ChangeSetView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    base_release_id: str | None
    changes: list[dict[str, Any]]
    dependency_ids: list[str]
    conflicts: list[dict[str, Any]]
    visibility: str
    state: str
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


class ReleaseCreate(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)
    release_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]+$")
    schema_version: str = Field(min_length=1, max_length=80)
    change_set_pub_ids: list[str] = Field(min_length=1, max_length=100)
    quality_report: dict[str, Any] = Field(default_factory=dict)
    activate: bool = True


class ReleaseView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    release_id: str
    parent_release_id: str | None
    schema_version: str
    content_hash: str
    artifact_uri: str
    quality_report: dict[str, Any]
    state: str
    created_by: str
    created_at: datetime


class ActivationRequest(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)


class ConnectorRunCreate(StrictModel):
    namespace: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=160)
    adapter: str = Field(min_length=1, max_length=120)
    operation: Literal["import", "export", "publish", "reconcile"]
    base_release_id: str | None = Field(default=None, max_length=128)
    upstream_release_id: str | None = Field(default=None, max_length=128)
    local_release_id: str | None = Field(default=None, max_length=128)
    cursor: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cursor")
    @classmethod
    def cursor_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(repr(value).encode()) > 64 * 1024:
            raise ValueError("connector_cursor_too_large")
        return value


class ConnectorRunView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    adapter: str
    operation: str
    status: str
    base_release_id: str | None
    upstream_release_id: str | None
    local_release_id: str | None
    cursor: dict[str, Any]
    result: dict[str, Any]
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None


class ServiceStatus(StrictModel):
    status: Literal["ok", "degraded", "not_ready"]
    domains: list[str]
    active_release: str | None
    previous_release: str | None
    release_verified: bool
    checks: dict[str, str]


class ModelMetricView(StrictModel):
    model: str
    inference_count: int
    provider_call_count: int
    error_count: int
    cache_hit_count: int
    cache_hit_rate: float
    provider_latency_avg_ms: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_unknown_count: int


class MetricsView(StrictModel):
    observations: int
    candidate_backlog: int
    review_ready: int
    conflicts: int
    inference_count: int
    model_error_count: int
    model_call_count: int
    model_latency_avg_ms: float | None
    model_cost_usd: float
    cache_hits: int
    active_release_id: str | None
    release_age_seconds: int | None
    oldest_candidate_age_seconds: int | None
    connector_last_attempt_at: datetime | None
    connector_last_success_at: datetime | None
    export_lag_seconds: int | None
    requested_model_metrics: list[ModelMetricView] = Field(default_factory=list)
    actual_model_metrics: list[ModelMetricView] = Field(default_factory=list)


class AuditEventView(StrictModel):
    pub_id: str
    namespace: str
    domain: str
    actor: str
    action: str
    resource_type: str
    resource_pub_id: str
    receipt: dict[str, Any]
    occurred_at: datetime


class KnowledgeEventView(StrictModel):
    schema_version: Literal["knowledge-event-v1"]
    event_id: str
    event_type: str
    occurred_at: datetime
    tenant: str
    namespace: str
    domain: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    payload_hash: str
