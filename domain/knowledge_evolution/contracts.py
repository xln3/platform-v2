"""Stable contracts shared by runtime, governance, and domain plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReasoningPolicy(StrEnum):
    DETERMINISTIC_ONLY = "deterministic_only"
    LLM_ASSISTED = "llm_assisted"
    LLM_REQUIRED = "llm_required"
    EXPLORATORY = "exploratory"


class KnowledgeStatus(StrEnum):
    PUBLISHED = "published"
    REVIEWED_LOCAL = "reviewed_local"
    MODEL_INFERRED = "model_inferred"
    UNRESOLVED = "unresolved"


class DecisionScope(StrEnum):
    REQUEST = "request"
    PROJECT_STAGING = "project_staging"
    DOMAIN_CANDIDATE = "domain_candidate"
    GLOBAL_RELEASE = "global_release"


@dataclass(frozen=True, slots=True)
class ReleaseRef:
    release_id: str
    content_hash: str
    schema_version: str
    source: str
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    input_id: str
    input_value: str
    value: dict[str, Any]
    knowledge_status: KnowledgeStatus
    decision_scope: DecisionScope
    confidence: float
    reasons: tuple[str, ...] = ()
    alternative_hypotheses: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    adopted: bool = False
    model_provider: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    knowledge_release_id: str | None = None
    knowledge_content_hash: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    tool_summary: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    request_id: str
    tenant: str
    namespace: str
    domain: str
    task: str
    items: tuple[dict[str, Any], ...]
    context: dict[str, Any]
    policy: ReasoningPolicy
    policy_id: str
    policy_version: str
    adopt_model_inferred: bool = False
    on_model_failure: str = "degrade"
    expected_release_id: str | None = None
    data_classification: str = "internal"
    allow_external_model: bool = False
    max_latency_ms: int | None = None
    max_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ModelPrompt:
    prompt_id: str
    prompt_version: str
    system_message: str
    user_message: str
    output_schema: dict[str, Any]
    tools: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class GatewayResult:
    payload: dict[str, Any]
    provider: str
    model: str
    model_version: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    tool_summary: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    namespace: str
    domain: str
    task: str
    surface_form: str
    normalized_key: str
    source_type: str
    source_ref_hash: str
    idempotency_key: str
    safe_context: str | None
    data_classification: str
    visibility: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeResponse:
    request_id: str
    domain: str
    task: str
    policy: ReasoningPolicy
    policy_id: str
    policy_version: str
    release: ReleaseRef
    decisions: tuple[Decision, ...]
    model_hypotheses: tuple[Decision, ...]
    prompt_id: str | None
    prompt_version: str | None
    model_provider: str | None
    model_name: str | None
    model_version: str | None
    latency_ms: int
    cache_status: str
    degradation: tuple[str, ...]
    observation_count: int
    usage: dict[str, Any] = field(default_factory=dict)
