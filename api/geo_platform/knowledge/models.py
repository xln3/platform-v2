"""SQLAlchemy mappings for the isolated ``knowledge`` control-plane schema."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, MetaData, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..tenancy.database import NAMING_CONVENTION


def now_utc() -> datetime:
    return datetime.now(UTC)


class KnowledgeBase(DeclarativeBase):
    metadata = MetaData(schema="knowledge", naming_convention=NAMING_CONVENTION)


class TenantScoped:
    tenant_pub_id: Mapped[str] = mapped_column(String(30), index=True)
    namespace: Mapped[str] = mapped_column(String(120), index=True)
    domain: Mapped[str] = mapped_column(String(160), index=True)


class Observation(TenantScoped, KnowledgeBase):
    __tablename__ = "observation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    task: Mapped[str] = mapped_column(String(120))
    surface_form: Mapped[str] = mapped_column(Text)
    normalized_key: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(80))
    source_ref_hash: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    safe_context: Mapped[str | None] = mapped_column(Text)
    data_classification: Mapped[str] = mapped_column(String(30))
    visibility: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="observed")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Candidate(TenantScoped, KnowledgeBase):
    __tablename__ = "candidate"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    aggregation_key: Mapped[str] = mapped_column(String(80))
    surface_forms: Mapped[list[str]] = mapped_column(JSONB, default=list)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(40), default="aggregated")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    policy_version: Mapped[str] = mapped_column(String(120), default="unknown")
    evidence_version: Mapped[str] = mapped_column(String(120), default="none")
    reopen_reason: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CandidateObservation(KnowledgeBase):
    __tablename__ = "candidate_observation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_pub_id: Mapped[str] = mapped_column(String(30), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge.candidate.id"))
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge.observation.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeObject(TenantScoped, KnowledgeBase):
    __tablename__ = "knowledge_object"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    stable_id: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(80))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    origin: Mapped[str] = mapped_column(String(80))
    review_status: Mapped[str] = mapped_column(String(30))
    visibility: Mapped[str] = mapped_column(String(30))
    sync_status: Mapped[str] = mapped_column(String(30), default="local_ahead")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Assertion(TenantScoped, KnowledgeBase):
    __tablename__ = "assertion"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    assertion_key: Mapped[str] = mapped_column(String(200))
    subject_stable_id: Mapped[str] = mapped_column(String(200))
    predicate: Mapped[str] = mapped_column(String(120))
    object_stable_id: Mapped[str | None] = mapped_column(String(200))
    object_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    epistemic_status: Mapped[str] = mapped_column(String(30))
    review_status: Mapped[str] = mapped_column(String(30))
    confidence_ppm: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Proposal(TenantScoped, KnowledgeBase):
    __tablename__ = "proposal"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge.candidate.id"))
    operation: Mapped[str] = mapped_column(String(40))
    target_stable_id: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(120))
    model_version: Mapped[str | None] = mapped_column(String(120))
    prompt_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(40), default="proposed")
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Evidence(TenantScoped, KnowledgeBase):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge.candidate.id"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge.proposal.id"))
    source_uri: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(80))
    publisher: Mapped[str] = mapped_column(String(240))
    claim: Mapped[str] = mapped_column(Text)
    stance: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text)
    trust_tier: Mapped[str] = mapped_column(String(30))
    visibility: Mapped[str] = mapped_column(String(30))
    data_classification: Mapped[str] = mapped_column(String(30))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Adjudication(TenantScoped, KnowledgeBase):
    __tablename__ = "adjudication"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge.proposal.id"))
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(120))
    before_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    after_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decided_by: Mapped[str] = mapped_column(String(255))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ChangeSet(TenantScoped, KnowledgeBase):
    __tablename__ = "change_set"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    base_release_id: Mapped[str | None] = mapped_column(String(128))
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    dependency_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(40), default="draft")
    created_by: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeRelease(TenantScoped, KnowledgeBase):
    __tablename__ = "knowledge_release"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    release_id: Mapped[str] = mapped_column(String(128))
    parent_release_id: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(80))
    artifact_uri: Mapped[str] = mapped_column(Text)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="published")
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeReleaseObject(TenantScoped, KnowledgeBase):
    """Immutable membership of one logical object in one knowledge release."""

    __tablename__ = "knowledge_release_object"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.knowledge_release.id")
    )
    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.knowledge_object.id")
    )
    stable_id: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeReleaseAssertion(TenantScoped, KnowledgeBase):
    """Immutable membership of one assertion version in one knowledge release."""

    __tablename__ = "knowledge_release_assertion"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.knowledge_release.id")
    )
    assertion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge.assertion.id"))
    assertion_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReleaseActivation(TenantScoped, KnowledgeBase):
    __tablename__ = "release_activation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    release_id: Mapped[str] = mapped_column(String(128))
    previous_release_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ConnectorRun(TenantScoped, KnowledgeBase):
    __tablename__ = "connector_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    adapter: Mapped[str] = mapped_column(String(120))
    operation: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    base_release_id: Mapped[str | None] = mapped_column(String(128))
    upstream_release_id: Mapped[str | None] = mapped_column(String(128))
    local_release_id: Mapped[str | None] = mapped_column(String(128))
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(160))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InferenceTrace(TenantScoped, KnowledgeBase):
    __tablename__ = "inference_trace"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    request_id: Mapped[str] = mapped_column(String(128))
    task: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(80))
    reasoning_policy: Mapped[str] = mapped_column(String(40))
    policy_id: Mapped[str] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(120))
    knowledge_release_id: Mapped[str] = mapped_column(String(128))
    knowledge_content_hash: Mapped[str] = mapped_column(String(80))
    prompt_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(120))
    model_version: Mapped[str | None] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(120))
    adopt_model_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    adopted_model_decisions: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer)
    model_latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    cache_status: Mapped[str] = mapped_column(String(30))
    degradation: Mapped[list[str]] = mapped_column(JSONB, default=list)
    tool_summary: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    data_classification: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SemanticCache(TenantScoped, KnowledgeBase):
    __tablename__ = "semantic_cache"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cache_key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)


class AuditEvent(TenantScoped, KnowledgeBase):
    __tablename__ = "audit_event"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_pub_id: Mapped[str] = mapped_column(String(40))
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
