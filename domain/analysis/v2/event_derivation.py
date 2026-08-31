"""Deterministically derive answer events from accepted atomic decisions only."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from domain.analysis.v2._canonical import canonical_hash
from domain.analysis.v2.decision_models import (
    DecisionMethod,
    DecisionStatus,
    SemanticDecisionRecord,
)
from domain.metrics.v2.semantic_events import (
    AnswerSemanticEvent,
    CapabilityAnalysis,
    CapabilityStatus,
    ConfidenceState,
    DerivationMethod,
    SemanticEventType,
)


@dataclass(frozen=True, slots=True)
class EventDerivationContext:
    tenant_pub_id: str
    project_pub_id: str
    answer_pub_id: str
    semantic_manifest_pub_id: str
    extractor_version: str
    scorer_version: str
    policy_versions_by_hash: dict[str, str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _EventDraft:
    event_type: SemanticEventType
    subject_entity_id: str | None
    object_entity_id: str | None
    event_value: dict[str, Any]
    qualifiers: dict[str, Any]
    start: int | None
    end: int | None
    excerpt_hash: str | None
    decisions: tuple[SemanticDecisionRecord, ...]

    @property
    def semantic_key(self) -> str:
        return canonical_hash(
            {
                "end": self.end,
                "event_type": self.event_type,
                "event_value": self.event_value,
                "excerpt_hash": self.excerpt_hash,
                "object_entity_id": self.object_entity_id,
                "qualifiers": self.qualifiers,
                "start": self.start,
                "subject_entity_id": self.subject_entity_id,
            }
        )


def derive_answer_semantic_events(
    decisions: tuple[SemanticDecisionRecord, ...],
    *,
    context: EventDerivationContext,
) -> tuple[AnswerSemanticEvent, ...]:
    """Return stable events; nonaccepted records can never create a fact."""

    accepted = tuple(
        sorted(
            (
                decision
                for decision in decisions
                if decision.status is DecisionStatus.ACCEPTED
                and decision.subject_ref.get("answer_pub_id") == context.answer_pub_id
            ),
            key=lambda item: item.decision_pub_id,
        )
    )
    missing_policies = {
        decision.judge_policy_hash
        for decision in accepted
        if decision.judge_policy_hash not in context.policy_versions_by_hash
    }
    if missing_policies:
        raise ValueError("decision_policy_version_missing")
    extraction_claims = _extraction_claims(accepted)
    drafts: list[_EventDraft] = []
    for decision in accepted:
        drafts.extend(_drafts_for_decision(decision, extraction_claims))
    merged = _merge_duplicate_drafts(drafts)
    provisional = [_event_from_draft(draft, context=context, index=0) for draft in merged]
    ordered = sorted(provisional, key=lambda event: event.event_fingerprint)
    return tuple(
        AnswerSemanticEvent.model_validate(
            event.model_dump(mode="python")
            | {
                "event_index": index,
                "pub_id": (
                    "ase_"
                    + canonical_hash(
                        {
                            "event_fingerprint": event.event_fingerprint,
                            "semantic_manifest_pub_id": context.semantic_manifest_pub_id,
                        }
                    )[:26]
                ),
            }
        )
        for index, event in enumerate(ordered)
    )


def capability_analyses_from_decisions(
    decisions: tuple[SemanticDecisionRecord, ...],
) -> dict[str, CapabilityAnalysis]:
    """Keep task failures isolated to their own manifest capability."""

    task_capabilities = {
        "substantive-entity-mention": ("substantive_entity_mention",),
        "recommendation-relation": ("recommendation_relation",),
        "rank-semantics": ("rank_semantics",),
        "stance-and-pairwise": ("stance_and_pairwise",),
        "requested-dimension-applicability": ("requested_dimension_applicability",),
        "answer-dimension-coverage": ("answer_dimension_coverage",),
        # A successful empty extraction proves that both downstream claim and
        # citation populations are empty, not unavailable.  Any downstream
        # failure joins the same capability and lowers it from ready.
        "claim-extraction": ("claim_evidence_verdict", "citation_claim_support"),
        "claim-verifiability": ("claim_evidence_verdict",),
        "claim-evidence-verdict": ("claim_evidence_verdict",),
        "citation-claim-support": ("citation_claim_support",),
        "risk-adjudication": ("risk_adjudication",),
    }
    grouped: dict[str, list[SemanticDecisionRecord]] = {}
    for decision in decisions:
        for capability in task_capabilities.get(decision.task_name, ()):
            grouped.setdefault(capability, []).append(decision)
    answer: dict[str, CapabilityAnalysis] = {}
    for capability, records in grouped.items():
        statuses = {record.status for record in records}
        if DecisionStatus.FAILED in statuses:
            status = CapabilityStatus.FAILED
        elif DecisionStatus.REVIEW_REQUIRED in statuses:
            status = CapabilityStatus.REVIEW_REQUIRED
        elif DecisionStatus.ABSTAINED in statuses:
            status = CapabilityStatus.ABSTAINED
        else:
            status = CapabilityStatus.READY
        answer[capability] = CapabilityAnalysis(
            status=status,
            decision_record_pub_ids=tuple(record.decision_pub_id for record in records),
            reason_codes=tuple(code for record in records for code in record.reason_codes),
        )
    return answer


def _drafts_for_decision(
    decision: SemanticDecisionRecord,
    extraction_claims: dict[str, tuple[SemanticDecisionRecord, dict[str, Any]]],
) -> list[_EventDraft]:
    result = decision.result
    task = decision.task_name
    if task == "substantive-entity-mention":
        if result.get("substantive") is not True:
            return []
        return [
            _draft(
                SemanticEventType.ENTITY_MENTION,
                decision,
                subject=result["entity_id"],
                value={
                    "surface": result["surface"],
                    "mention_role": result["mention_role"],
                    "substantive": True,
                },
            )
        ]
    if task == "recommendation-relation":
        # A governed neutral/unknown result can represent the absence of a
        # recommendation for an entity inherited from query context. It has
        # no answer span and therefore must not be materialized as evidence.
        if result.get("polarity") in {"neutral", "unknown"} and any(
            result.get(field) is None for field in ("start", "end", "excerpt_hash")
        ):
            return []
        qualifiers = {
            "stance_owner": result["stance_owner"],
            "subject_resolution": result["subject_resolution"],
        }
        return [
            _draft(
                SemanticEventType.RECOMMENDATION_RELATION,
                decision,
                subject=result["subject_entity_id"],
                value={
                    "polarity": result["polarity"],
                    "strength": result["strength"],
                    "scenario": result["scenario"],
                },
                qualifiers=qualifiers,
            )
        ]
    if task == "rank-semantics":
        return [_rank_draft(decision, event) for event in result.get("rank_events", ())]
    if task == "stance-and-pairwise":
        if result["kind"] == "stance":
            return [
                _draft(
                    SemanticEventType.SENTIMENT_OR_STANCE,
                    decision,
                    subject=result["subject_entity_id"],
                    value={"polarity": result["polarity"], "aspect": result["aspect"]},
                    qualifiers={"scenario": result["scenario"]},
                )
            ]
        return [
            _draft(
                SemanticEventType.PAIRWISE_PREFERENCE,
                decision,
                subject=result["subject_entity_id"],
                object_entity=result["object_entity_id"],
                value={"relation": result["relation"]},
                qualifiers={"aspect": result["aspect"], "scenario": result["scenario"]},
            )
        ]
    if task == "claim-verifiability":
        claim_fingerprint = result["claim_fingerprint"]
        extraction = extraction_claims.get(claim_fingerprint)
        if extraction is None:
            raise ValueError("claim_verifiability_parent_claim_missing")
        extraction_decision, claim = extraction
        return [
            _draft(
                SemanticEventType.FACTUAL_CLAIM,
                decision,
                subject=claim.get("subject_entity_id"),
                value={
                    "claim_text": claim["claim_text"],
                    "verifiability": result["verifiability"],
                    "time_scope": claim["time_scope"],
                    "claim_fingerprint": claim_fingerprint,
                },
                qualifiers={
                    "subject": claim["subject"],
                    "predicate": claim["predicate"],
                    "object": claim["object"],
                    "required_evidence_types": result["required_evidence_types"],
                },
                extra_decisions=(extraction_decision,),
                span_source=claim,
            )
        ]
    if task == "claim-evidence-verdict":
        return [
            _draft(
                SemanticEventType.CLAIM_EVIDENCE_VERDICT,
                decision,
                subject=decision.subject_ref.get("entity_id"),
                value={
                    "claim_event_pub_id": result["claim_event_pub_id"],
                    "verdict": result["verdict"],
                    "verification_as_of": result["verification_as_of"],
                    "evidence_snapshot_refs": result["evidence_snapshot_refs"],
                },
                no_primary_span=True,
            )
        ]
    if task == "citation-claim-support":
        return [
            _draft(
                SemanticEventType.CITATION_RELATION,
                decision,
                subject=None,
                value={
                    "citation_pub_id": result["citation_pub_id"],
                    "claim_event_pub_id": result["claim_event_pub_id"],
                    "support_state": result["support_state"],
                },
                qualifiers={"evidence_snapshot_refs": result["evidence_snapshot_refs"]},
                no_primary_span=True,
            )
        ]
    if task == "risk-adjudication":
        return [
            _draft(
                SemanticEventType.RISK_EVENT,
                decision,
                subject=result["subject_entity_id"],
                object_entity=result["object_entity_id"],
                value={
                    "risk_type": result["risk_type"],
                    "severity": result["severity"],
                    "verdict": result["verdict"],
                },
            )
        ]
    return []


def _rank_draft(decision: SemanticDecisionRecord, raw: dict[str, Any]) -> _EventDraft:
    event_type = SemanticEventType(raw["event_type"])
    value_fields = {
        SemanticEventType.RECOMMENDATION_LIST_RANK: ("rank", "list_size", "list_id", "ordered"),
        SemanticEventType.MARKET_RANK_CLAIM: (
            "rank_low",
            "rank_high",
            "market_scope",
            "time_scope",
            "claim_text",
        ),
        SemanticEventType.PAIRWISE_PREFERENCE: ("relation",),
        SemanticEventType.MENTION_ORDER: ("ordinal", "entity_count"),
        SemanticEventType.SOURCE_RESULT_RANK: ("ordinal", "source_id"),
    }[event_type]
    return _draft(
        event_type,
        decision,
        subject=raw.get("subject_entity_id"),
        object_entity=raw.get("object_entity_id"),
        value={field: raw[field] for field in value_fields},
        span_source=raw,
    )


def _draft(
    event_type: SemanticEventType,
    decision: SemanticDecisionRecord,
    *,
    subject: str | None,
    value: dict[str, Any],
    object_entity: str | None = None,
    qualifiers: dict[str, Any] | None = None,
    extra_decisions: tuple[SemanticDecisionRecord, ...] = (),
    span_source: dict[str, Any] | None = None,
    no_primary_span: bool = False,
) -> _EventDraft:
    source = decision.result if span_source is None else span_source
    normalized_qualifiers = dict(qualifiers or {})
    if "spans" in source:
        normalized_qualifiers["spans"] = source["spans"]
    return _EventDraft(
        event_type=event_type,
        subject_entity_id=subject,
        object_entity_id=object_entity,
        event_value=value,
        qualifiers=normalized_qualifiers,
        start=None if no_primary_span else source.get("start"),
        end=None if no_primary_span else source.get("end"),
        excerpt_hash=None if no_primary_span else source.get("excerpt_hash"),
        decisions=_unique_decisions((decision, *extra_decisions)),
    )


def _extraction_claims(
    decisions: tuple[SemanticDecisionRecord, ...],
) -> dict[str, tuple[SemanticDecisionRecord, dict[str, Any]]]:
    answer: dict[str, tuple[SemanticDecisionRecord, dict[str, Any]]] = {}
    for decision in decisions:
        if decision.task_name != "claim-extraction":
            continue
        for claim in decision.result.get("claims", ()):
            fingerprint = claim["claim_fingerprint"]
            if fingerprint in answer:
                raise ValueError("duplicate_claim_fingerprint")
            answer[fingerprint] = (decision, claim)
    return answer


def _merge_duplicate_drafts(drafts: list[_EventDraft]) -> tuple[_EventDraft, ...]:
    by_key: dict[str, _EventDraft] = {}
    for draft in drafts:
        existing = by_key.get(draft.semantic_key)
        if existing is None:
            by_key[draft.semantic_key] = draft
            continue
        decisions = _unique_decisions((*existing.decisions, *draft.decisions))
        by_key[draft.semantic_key] = replace(existing, decisions=decisions)
    return tuple(by_key[key] for key in sorted(by_key))


def _event_from_draft(
    draft: _EventDraft,
    *,
    context: EventDerivationContext,
    index: int,
) -> AnswerSemanticEvent:
    confidence_values = [
        Decimal(str(decision.calibrated_confidence))
        for decision in draft.decisions
        if decision.calibrated_confidence is not None
    ]
    confidence = min(confidence_values) if confidence_values else None
    methods = {decision.method for decision in draft.decisions}
    method = _combined_method(methods)
    policy_versions = "+".join(
        sorted(
            {
                context.policy_versions_by_hash[decision.judge_policy_hash]
                for decision in draft.decisions
            }
        )
    )
    return AnswerSemanticEvent(
        pub_id="ase_pending",
        tenant_pub_id=context.tenant_pub_id,
        project_pub_id=context.project_pub_id,
        answer_pub_id=context.answer_pub_id,
        semantic_manifest_pub_id=context.semantic_manifest_pub_id,
        event_index=index,
        event_type=draft.event_type,
        subject_entity_id=draft.subject_entity_id,
        object_entity_id=draft.object_entity_id,
        event_value=draft.event_value,
        qualifiers=draft.qualifiers,
        answer_text_start=draft.start,
        answer_text_end=draft.end,
        answer_excerpt_hash=draft.excerpt_hash,
        extractor_version=context.extractor_version,
        scorer_version=context.scorer_version,
        derivation_method=method,
        decision_record_pub_ids=tuple(decision.decision_pub_id for decision in draft.decisions),
        decision_policy_version=policy_versions,
        calibrated_confidence=confidence,
        confidence_state=_confidence_state(confidence),
        created_at=context.created_at,
    )


def _combined_method(methods: set[DecisionMethod]) -> DerivationMethod:
    if DecisionMethod.HUMAN in methods:
        return DerivationMethod.HUMAN
    if DecisionMethod.HYBRID in methods or len(methods) > 1:
        return DerivationMethod.HYBRID
    return DerivationMethod(next(iter(methods)).value)


def _confidence_state(confidence: Decimal | None) -> ConfidenceState:
    if confidence is None:
        return ConfidenceState.UNKNOWN
    if confidence >= Decimal("0.95"):
        return ConfidenceState.HIGH
    if confidence >= Decimal("0.80"):
        return ConfidenceState.MEDIUM
    return ConfidenceState.LOW


def _unique_decisions(
    decisions: tuple[SemanticDecisionRecord, ...],
) -> tuple[SemanticDecisionRecord, ...]:
    by_id = {decision.decision_pub_id: decision for decision in decisions}
    return tuple(by_id[pub_id] for pub_id in sorted(by_id))
