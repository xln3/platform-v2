"""Answer semantic manifest and typed, non-exclusive event contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .canonical_hash import canonical_hash, canonical_set_hash

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_EVENT_VALUE_ADAPTERS: dict[SemanticEventType, TypeAdapter[Any]] = {}


class FrozenSemanticModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )


class SemanticEventType(StrEnum):
    ENTITY_MENTION = "entity_mention"
    RECOMMENDATION_RELATION = "recommendation_relation"
    SENTIMENT_OR_STANCE = "sentiment_or_stance"
    RECOMMENDATION_LIST_RANK = "recommendation_list_rank"
    MARKET_RANK_CLAIM = "market_rank_claim"
    PAIRWISE_PREFERENCE = "pairwise_preference"
    MENTION_ORDER = "mention_order"
    SOURCE_RESULT_RANK = "source_result_rank"
    FACTUAL_CLAIM = "factual_claim"
    CLAIM_EVIDENCE_VERDICT = "claim_evidence_verdict"
    CITATION_RELATION = "citation_relation"
    RISK_EVENT = "risk_event"


class DerivationMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HYBRID = "hybrid"
    HUMAN = "human"


class ConfidenceState(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class EventReviewStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    OVERRIDDEN = "overridden"


class MentionRole(StrEnum):
    ASSERTED_BODY = "asserted_body"
    QUOTED_BODY = "quoted_body"
    PROMPT_ECHO = "prompt_echo"
    CITATION_METADATA = "citation_metadata"


class RecommendationPolarity(StrEnum):
    POSITIVE = "positive"
    CONDITIONAL_POSITIVE = "conditional_positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class StancePolarity(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class PairwiseRelation(StrEnum):
    SUBJECT_BETTER = "subject_better"
    OBJECT_BETTER = "object_better"
    TIE = "tie"
    DIFFERENT_SCENARIOS = "different_scenarios"
    UNKNOWN = "unknown"


class ClaimVerifiability(StrEnum):
    VERIFIABLE = "verifiable"
    UNVERIFIABLE = "unverifiable"
    UNKNOWN = "unknown"


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"
    UNKNOWN = "unknown"


class CitationSupportState(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MENTIONS = "mentions"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskVerdict(StrEnum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    UNKNOWN = "unknown"


class EntityMentionValue(FrozenSemanticModel):
    surface: str = Field(min_length=1)
    mention_role: MentionRole
    substantive: bool

    @model_validator(mode="after")
    def non_body_roles_are_not_substantive(self) -> Self:
        if self.substantive and self.mention_role in {
            MentionRole.PROMPT_ECHO,
            MentionRole.CITATION_METADATA,
        }:
            raise ValueError("non_body_mention_cannot_be_substantive")
        return self


class RecommendationRelationValue(FrozenSemanticModel):
    polarity: RecommendationPolarity
    strength: Decimal = Field(ge=0, le=1)
    scenario: str

    @model_validator(mode="after")
    def conditional_has_scenario(self) -> Self:
        if self.polarity is RecommendationPolarity.CONDITIONAL_POSITIVE and not self.scenario:
            raise ValueError("conditional_recommendation_requires_scenario")
        return self


class SentimentOrStanceValue(FrozenSemanticModel):
    polarity: StancePolarity
    aspect: str


class RecommendationListRankValue(FrozenSemanticModel):
    rank: int = Field(ge=1)
    list_size: int = Field(ge=1)
    list_id: str = Field(min_length=1)
    ordered: bool

    @model_validator(mode="after")
    def rank_is_in_ordered_list(self) -> Self:
        if not self.ordered:
            raise ValueError("recommendation_rank_requires_ordered_list")
        if self.rank > self.list_size:
            raise ValueError("recommendation_rank_out_of_range")
        return self


class MarketRankClaimValue(FrozenSemanticModel):
    rank_low: int = Field(ge=1)
    rank_high: int = Field(ge=1)
    market_scope: str = Field(min_length=1)
    time_scope: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def rank_range_is_ordered(self) -> Self:
        if self.rank_high < self.rank_low:
            raise ValueError("market_rank_range_invalid")
        return self


class PairwisePreferenceValue(FrozenSemanticModel):
    relation: PairwiseRelation


class MentionOrderValue(FrozenSemanticModel):
    ordinal: int = Field(ge=1)
    entity_count: int = Field(ge=1)

    @model_validator(mode="after")
    def ordinal_is_in_range(self) -> Self:
        if self.ordinal > self.entity_count:
            raise ValueError("mention_order_out_of_range")
        return self


class SourceResultRankValue(FrozenSemanticModel):
    ordinal: int = Field(ge=1)
    source_id: str = Field(min_length=1)


class FactualClaimValue(FrozenSemanticModel):
    claim_text: str = Field(min_length=1)
    verifiability: ClaimVerifiability
    time_scope: str = Field(min_length=1)
    claim_fingerprint: str = Field(pattern=_HASH_PATTERN)


class ClaimEvidenceVerdictValue(FrozenSemanticModel):
    claim_event_pub_id: str = Field(min_length=1)
    verdict: ClaimVerdict
    verification_as_of: datetime
    evidence_snapshot_refs: tuple[str, ...]

    @model_validator(mode="after")
    def evidence_is_frozen_when_required(self) -> Self:
        if (
            self.verdict
            in {
                ClaimVerdict.SUPPORTED,
                ClaimVerdict.CONTRADICTED,
                ClaimVerdict.UNSUPPORTED,
            }
            and not self.evidence_snapshot_refs
        ):
            raise ValueError("claim_verdict_requires_frozen_evidence")
        if self.verification_as_of.tzinfo is None or self.verification_as_of.utcoffset() is None:
            raise ValueError("verification_as_of_must_be_timezone_aware")
        return self


class CitationRelationValue(FrozenSemanticModel):
    citation_pub_id: str = Field(min_length=1)
    claim_event_pub_id: str = Field(min_length=1)
    support_state: CitationSupportState


class RiskEventValue(FrozenSemanticModel):
    risk_type: str = Field(min_length=1)
    severity: RiskSeverity
    verdict: RiskVerdict


EventValue = Annotated[
    EntityMentionValue
    | RecommendationRelationValue
    | SentimentOrStanceValue
    | RecommendationListRankValue
    | MarketRankClaimValue
    | PairwisePreferenceValue
    | MentionOrderValue
    | SourceResultRankValue
    | FactualClaimValue
    | ClaimEvidenceVerdictValue
    | CitationRelationValue
    | RiskEventValue,
    Field(union_mode="left_to_right"),
]

_EVENT_VALUE_MODELS: dict[SemanticEventType, type[FrozenSemanticModel]] = {
    SemanticEventType.ENTITY_MENTION: EntityMentionValue,
    SemanticEventType.RECOMMENDATION_RELATION: RecommendationRelationValue,
    SemanticEventType.SENTIMENT_OR_STANCE: SentimentOrStanceValue,
    SemanticEventType.RECOMMENDATION_LIST_RANK: RecommendationListRankValue,
    SemanticEventType.MARKET_RANK_CLAIM: MarketRankClaimValue,
    SemanticEventType.PAIRWISE_PREFERENCE: PairwisePreferenceValue,
    SemanticEventType.MENTION_ORDER: MentionOrderValue,
    SemanticEventType.SOURCE_RESULT_RANK: SourceResultRankValue,
    SemanticEventType.FACTUAL_CLAIM: FactualClaimValue,
    SemanticEventType.CLAIM_EVIDENCE_VERDICT: ClaimEvidenceVerdictValue,
    SemanticEventType.CITATION_RELATION: CitationRelationValue,
    SemanticEventType.RISK_EVENT: RiskEventValue,
}
_SPAN_REQUIRED_EVENT_TYPES = frozenset(
    {
        SemanticEventType.ENTITY_MENTION,
        SemanticEventType.RECOMMENDATION_RELATION,
        SemanticEventType.SENTIMENT_OR_STANCE,
        SemanticEventType.RECOMMENDATION_LIST_RANK,
        SemanticEventType.MARKET_RANK_CLAIM,
        SemanticEventType.PAIRWISE_PREFERENCE,
        SemanticEventType.MENTION_ORDER,
        SemanticEventType.SOURCE_RESULT_RANK,
        SemanticEventType.FACTUAL_CLAIM,
        SemanticEventType.RISK_EVENT,
    }
)


class QualifierSpan(FrozenSemanticModel):
    role: str = Field(min_length=1, max_length=100)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt_hash: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def interval_is_nonempty(self) -> Self:
        if self.end <= self.start:
            raise ValueError("qualifier_span_must_be_nonempty")
        return self


class AnswerSemanticEvent(FrozenSemanticModel):
    pub_id: str = Field(pattern=r"^ase_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    tenant_pub_id: str = Field(min_length=1)
    project_pub_id: str = Field(min_length=1)
    answer_pub_id: str = Field(min_length=1)
    semantic_manifest_pub_id: str = Field(pattern=r"^asm_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    event_index: int = Field(ge=0)
    event_type: SemanticEventType
    subject_entity_id: str | None = None
    object_entity_id: str | None = None
    event_value: dict[str, Any]
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    answer_text_start: int | None = Field(default=None, ge=0)
    answer_text_end: int | None = Field(default=None, gt=0)
    offset_unit: str = Field(default="unicode_code_point_v1", pattern=r"^unicode_code_point_v1$")
    answer_excerpt_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    extractor_version: str = Field(min_length=1)
    scorer_version: str = Field(min_length=1)
    derivation_method: DerivationMethod
    decision_record_pub_ids: tuple[str, ...] = Field(min_length=1)
    decision_policy_version: str = Field(min_length=1)
    provenance_hash: str = ""
    calibrated_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    confidence_state: ConfidenceState
    review_status: EventReviewStatus = EventReviewStatus.ACCEPTED
    override_reason: str | None = None
    event_fingerprint: str = ""
    created_at: datetime

    @field_validator("decision_record_pub_ids")
    @classmethod
    def decision_refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(sorted(set(value)))
        if not canonical:
            raise ValueError("semantic_event_requires_accepted_decision")
        return canonical

    @model_validator(mode="after")
    def typed_value_span_subject_and_hashes_are_valid(self) -> Self:
        value_model = _EVENT_VALUE_MODELS[self.event_type].model_validate(self.event_value)
        object.__setattr__(self, "event_value", value_model.model_dump(mode="python"))
        _validate_subject_contract(self)
        span_fields = (self.answer_text_start, self.answer_text_end, self.answer_excerpt_hash)
        if any(item is not None for item in span_fields) and not all(
            item is not None for item in span_fields
        ):
            raise ValueError("semantic_event_primary_span_incomplete")
        if self.answer_text_start is not None and self.answer_text_end is not None:
            if self.answer_text_end <= self.answer_text_start:
                raise ValueError("semantic_event_primary_span_empty")
        if self.event_type in _SPAN_REQUIRED_EVENT_TYPES and self.answer_text_start is None:
            raise ValueError("semantic_event_requires_answer_span")
        if "spans" in self.qualifiers:
            TypeAdapter(tuple[QualifierSpan, ...]).validate_python(self.qualifiers["spans"])
        if (self.calibrated_confidence is None) != (
            self.confidence_state is ConfidenceState.UNKNOWN
        ):
            raise ValueError("semantic_event_confidence_state_mismatch")
        if (self.review_status is EventReviewStatus.OVERRIDDEN) != (
            self.override_reason is not None
        ):
            raise ValueError("semantic_event_override_reason_mismatch")
        provenance = {
            "decision_policy_version": self.decision_policy_version,
            "decision_record_pub_ids": self.decision_record_pub_ids,
            "derivation_method": self.derivation_method,
            "extractor_version": self.extractor_version,
            "scorer_version": self.scorer_version,
        }
        calculated_provenance = canonical_hash(provenance)
        if self.provenance_hash and self.provenance_hash != calculated_provenance:
            raise ValueError("semantic_event_provenance_hash_mismatch")
        object.__setattr__(self, "provenance_hash", calculated_provenance)
        semantic_identity = {
            "answer_excerpt_hash": self.answer_excerpt_hash,
            "answer_text_end": self.answer_text_end,
            "answer_text_start": self.answer_text_start,
            "event_type": self.event_type,
            "event_value": self.event_value,
            "object_entity_id": self.object_entity_id,
            "qualifiers": self.qualifiers,
            "subject_entity_id": self.subject_entity_id,
        }
        calculated_fingerprint = canonical_hash(semantic_identity)
        if self.event_fingerprint and self.event_fingerprint != calculated_fingerprint:
            raise ValueError("semantic_event_fingerprint_mismatch")
        object.__setattr__(self, "event_fingerprint", calculated_fingerprint)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("semantic_event_created_at_must_be_timezone_aware")
        return self


class CapabilityStatus(StrEnum):
    READY = "ready"
    ABSTAINED = "abstained"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


class CapabilityAnalysis(FrozenSemanticModel):
    status: CapabilityStatus
    decision_record_pub_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @field_validator("decision_record_pub_ids", "reason_codes")
    @classmethod
    def values_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def accepted_refs_match_status(self) -> Self:
        if self.status is CapabilityStatus.READY and not self.decision_record_pub_ids:
            raise ValueError("ready_capability_requires_decision_record")
        if self.status is CapabilityStatus.NOT_REQUESTED and (
            self.decision_record_pub_ids or self.reason_codes
        ):
            raise ValueError("not_requested_capability_cannot_have_decision")
        return self


class ManifestStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


CORE_CAPABILITIES = (
    "entity_mention",
    "recommendation_relation",
    "rank_semantics",
    "claim_verification",
)


class AnswerSemanticManifest(FrozenSemanticModel):
    pub_id: str = Field(pattern=r"^asm_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    tenant_pub_id: str = Field(min_length=1)
    project_pub_id: str = Field(min_length=1)
    answer_pub_id: str = Field(min_length=1)
    analysis_run_pub_id: str = Field(min_length=1)
    query_context_fact_pub_id: str = Field(min_length=1)
    answer_text_hash: str = Field(pattern=_HASH_PATTERN)
    input_hash: str = Field(pattern=_HASH_PATTERN)
    event_schema_version: str = Field(
        default="answer-semantic-events-v2", pattern=r"^answer-semantic-events-v2$"
    )
    extractor_bundle: dict[str, Any]
    decision_task_bundle: dict[str, Any]
    extractor_bundle_hash: str = Field(pattern=_HASH_PATTERN)
    decision_task_bundle_hash: str = Field(pattern=_HASH_PATTERN)
    entity_dictionary_hash: str = Field(pattern=_HASH_PATTERN)
    status: ManifestStatus = Field(validation_alias=AliasChoices("status", "overall_status"))
    capability_statuses: dict[str, CapabilityAnalysis]
    decision_record_pub_ids: tuple[str, ...]
    decision_set_hash: str = Field(pattern=_HASH_PATTERN)
    failure_code: str | None = None
    failure_detail: str | None = Field(default=None, max_length=1_000)
    event_count: int = Field(ge=0)
    evidenced_event_count: int = Field(ge=0)
    event_set_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    supersedes_pub_id: str | None = None
    created_at: datetime
    completed_at: datetime

    @property
    def overall_status(self) -> ManifestStatus:
        return self.status

    @model_validator(mode="after")
    def hashes_counts_and_status_are_coherent(self) -> Self:
        if not set(CORE_CAPABILITIES) <= set(self.capability_statuses):
            raise ValueError("semantic_manifest_core_capabilities_missing")
        if self.extractor_bundle_hash != canonical_hash(self.extractor_bundle):
            raise ValueError("extractor_bundle_hash_mismatch")
        if self.decision_task_bundle_hash != canonical_hash(self.decision_task_bundle):
            raise ValueError("decision_task_bundle_hash_mismatch")
        canonical_decisions = tuple(sorted(set(self.decision_record_pub_ids)))
        object.__setattr__(self, "decision_record_pub_ids", canonical_decisions)
        if self.decision_set_hash != canonical_set_hash(canonical_decisions):
            raise ValueError("decision_set_hash_mismatch")
        referenced = {
            decision
            for capability in self.capability_statuses.values()
            for decision in capability.decision_record_pub_ids
        }
        if not referenced <= set(canonical_decisions):
            raise ValueError("capability_references_decision_outside_manifest")
        expected_status = derive_manifest_status(self.capability_statuses)
        if self.status is not expected_status:
            raise ValueError("semantic_manifest_status_mismatch")
        if self.evidenced_event_count > self.event_count:
            raise ValueError("evidenced_event_count_exceeds_event_count")
        if self.status is ManifestStatus.FAILED:
            if self.event_set_hash is not None or not self.failure_code:
                raise ValueError("failed_manifest_state_invalid")
        elif self.event_set_hash is None:
            raise ValueError("nonfailed_manifest_requires_event_set_hash")
        if self.completed_at < self.created_at:
            raise ValueError("semantic_manifest_completion_before_creation")
        for timestamp in (self.created_at, self.completed_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("semantic_manifest_datetime_must_be_timezone_aware")
        return self


def validate_event_evidence(
    event: AnswerSemanticEvent,
    *,
    answer_text: str,
    answer_text_hash: str,
) -> None:
    """Recompute every Unicode-code-point slice and fail on any mismatch."""

    if sha256(answer_text.encode()).hexdigest() != answer_text_hash:
        raise ValueError("answer_text_hash_mismatch")
    if event.answer_text_start is not None and event.answer_text_end is not None:
        excerpt = _checked_slice(answer_text, event.answer_text_start, event.answer_text_end)
        if sha256(excerpt.encode()).hexdigest() != event.answer_excerpt_hash:
            raise ValueError("answer_excerpt_hash_mismatch")
        if event.event_type is SemanticEventType.ENTITY_MENTION:
            if excerpt != event.event_value["surface"]:
                raise ValueError("entity_mention_surface_span_mismatch")
        if (
            event.event_type
            in {
                SemanticEventType.MARKET_RANK_CLAIM,
                SemanticEventType.FACTUAL_CLAIM,
            }
            and excerpt != event.event_value["claim_text"]
        ):
            raise ValueError("claim_text_span_mismatch")
    for raw_span in event.qualifiers.get("spans", ()):
        span = QualifierSpan.model_validate(raw_span)
        excerpt = _checked_slice(answer_text, span.start, span.end)
        if sha256(excerpt.encode()).hexdigest() != span.excerpt_hash:
            raise ValueError("qualifier_excerpt_hash_mismatch")


def derive_manifest_status(
    capability_statuses: dict[str, CapabilityAnalysis],
) -> ManifestStatus:
    requested = [
        capability.status
        for capability in capability_statuses.values()
        if capability.status is not CapabilityStatus.NOT_REQUESTED
    ]
    if not requested:
        return ManifestStatus.READY
    if CapabilityStatus.REVIEW_REQUIRED in requested:
        return ManifestStatus.REVIEW_REQUIRED
    if all(status is CapabilityStatus.FAILED for status in requested):
        return ManifestStatus.FAILED
    if any(status in {CapabilityStatus.ABSTAINED, CapabilityStatus.FAILED} for status in requested):
        return ManifestStatus.PARTIAL
    return ManifestStatus.READY


def build_answer_semantic_manifest(
    *,
    pub_id: str,
    tenant_pub_id: str,
    project_pub_id: str,
    answer_pub_id: str,
    analysis_run_pub_id: str,
    query_context_fact_pub_id: str,
    answer_text_hash: str,
    input_hash: str,
    extractor_bundle: dict[str, Any],
    decision_task_bundle: dict[str, Any],
    entity_dictionary_hash: str,
    capability_statuses: dict[str, CapabilityAnalysis],
    events: tuple[AnswerSemanticEvent, ...],
    created_at: datetime,
    completed_at: datetime,
    failure_code: str | None = None,
    failure_detail: str | None = None,
    supersedes_pub_id: str | None = None,
) -> AnswerSemanticManifest:
    completed_capabilities = dict(capability_statuses)
    for capability in CORE_CAPABILITIES:
        completed_capabilities.setdefault(
            capability, CapabilityAnalysis(status=CapabilityStatus.NOT_REQUESTED)
        )
    decision_ids = tuple(
        sorted(
            {
                decision
                for capability in completed_capabilities.values()
                for decision in capability.decision_record_pub_ids
            }
        )
    )
    event_indices = [event.event_index for event in events]
    if sorted(event_indices) != list(range(len(events))):
        raise ValueError("semantic_event_indices_must_be_contiguous")
    if len({event.event_fingerprint for event in events}) != len(events):
        raise ValueError("semantic_event_fingerprints_must_be_unique")
    for event in events:
        if (
            event.tenant_pub_id != tenant_pub_id
            or event.project_pub_id != project_pub_id
            or event.answer_pub_id != answer_pub_id
            or event.semantic_manifest_pub_id != pub_id
        ):
            raise ValueError("semantic_event_manifest_scope_mismatch")
        if not set(event.decision_record_pub_ids) <= set(decision_ids):
            raise ValueError("semantic_event_decision_outside_manifest")
    status = derive_manifest_status(completed_capabilities)
    event_hash = (
        None
        if status is ManifestStatus.FAILED
        else canonical_set_hash(event.event_fingerprint for event in events)
    )
    return AnswerSemanticManifest(
        pub_id=pub_id,
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        answer_pub_id=answer_pub_id,
        analysis_run_pub_id=analysis_run_pub_id,
        query_context_fact_pub_id=query_context_fact_pub_id,
        answer_text_hash=answer_text_hash,
        input_hash=input_hash,
        extractor_bundle=extractor_bundle,
        decision_task_bundle=decision_task_bundle,
        extractor_bundle_hash=canonical_hash(extractor_bundle),
        decision_task_bundle_hash=canonical_hash(decision_task_bundle),
        entity_dictionary_hash=entity_dictionary_hash,
        status=status,
        capability_statuses=completed_capabilities,
        decision_record_pub_ids=decision_ids,
        decision_set_hash=canonical_set_hash(decision_ids),
        failure_code=failure_code,
        failure_detail=failure_detail,
        event_count=len(events),
        evidenced_event_count=sum(event.answer_text_start is not None for event in events),
        event_set_hash=event_hash,
        supersedes_pub_id=supersedes_pub_id,
        created_at=created_at,
        completed_at=completed_at,
    )


def _validate_subject_contract(event: AnswerSemanticEvent) -> None:
    subject_required = {
        SemanticEventType.ENTITY_MENTION,
        SemanticEventType.RECOMMENDATION_RELATION,
        SemanticEventType.SENTIMENT_OR_STANCE,
        SemanticEventType.RECOMMENDATION_LIST_RANK,
        SemanticEventType.MARKET_RANK_CLAIM,
        SemanticEventType.PAIRWISE_PREFERENCE,
        SemanticEventType.MENTION_ORDER,
        SemanticEventType.RISK_EVENT,
    }
    if event.event_type in subject_required and not event.subject_entity_id:
        raise ValueError("semantic_event_subject_required")
    if event.event_type is SemanticEventType.PAIRWISE_PREFERENCE and not event.object_entity_id:
        raise ValueError("pairwise_preference_object_required")


def _checked_slice(text: str, start: int, end: int) -> str:
    if start < 0 or end <= start or end > len(text):
        raise ValueError("semantic_event_span_out_of_bounds")
    return text[start:end]
