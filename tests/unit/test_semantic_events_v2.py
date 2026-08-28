from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from decision_v2_fixtures import (
    ANSWER_ID,
    ENTITY_ID,
    NOW,
    OTHER_ENTITY_ID,
    PROJECT_ID,
    TENANT_ID,
    digest,
    make_record,
    policy_for,
)
from pydantic import ValidationError

from domain.analysis.v2.decision_models import DecisionStatus
from domain.analysis.v2.event_derivation import (
    EventDerivationContext,
    capability_analyses_from_decisions,
    derive_answer_semantic_events,
)
from domain.metrics.v2.semantic_events import (
    AnswerSemanticEvent,
    CapabilityAnalysis,
    CapabilityStatus,
    ClaimEvidenceVerdictValue,
    ConfidenceState,
    DerivationMethod,
    ManifestStatus,
    SemanticEventType,
    build_answer_semantic_manifest,
    validate_event_evidence,
)

MANIFEST_ID = "asm_manifest_test_0001"


def _context(*records) -> EventDerivationContext:
    return EventDerivationContext(
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        semantic_manifest_pub_id=MANIFEST_ID,
        extractor_version="semantic-event-deriver-v2.0.0",
        scorer_version="calibration-v2.0.0",
        policy_versions_by_hash={
            record.judge_policy_hash: policy_for(record.task_name).policy_ref for record in records
        },
        created_at=NOW,
    )


def _manual_entity_event(text: str, start: int, end: int) -> AnswerSemanticEvent:
    return AnswerSemanticEvent(
        pub_id="ase_entity_manual_0001",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        semantic_manifest_pub_id=MANIFEST_ID,
        event_index=0,
        event_type="entity_mention",
        subject_entity_id=ENTITY_ID,
        event_value={
            "surface": text[start:end],
            "mention_role": "asserted_body",
            "substantive": True,
        },
        qualifiers={},
        answer_text_start=start,
        answer_text_end=end,
        answer_excerpt_hash=digest(text[start:end]),
        extractor_version="semantic-event-deriver-v2.0.0",
        scorer_version="calibration-v2.0.0",
        derivation_method="deterministic",
        decision_record_pub_ids=("sdr_mention_test_0001",),
        decision_policy_version="semantic-v2-shadow-hybrid@2.0.0",
        calibrated_confidence=Decimal("0.99"),
        confidence_state="high",
        created_at=NOW,
    )


def test_unicode_evidence_validation_uses_code_points_not_bytes() -> None:
    text = "😀e\u0301盛邦安全适合政企"
    surface = "e\u0301盛邦安全"
    start = text.index(surface)
    event = _manual_entity_event(text, start, start + len(surface))

    validate_event_evidence(event, answer_text=text, answer_text_hash=digest(text))

    assert event.answer_text_start == 1
    assert text[event.answer_text_start : event.answer_text_end] == surface


def test_evidence_validation_rejects_hash_range_and_surface_mismatches() -> None:
    text = "盛邦安全适合政企"
    event = _manual_entity_event(text, 0, len("盛邦安全"))

    with pytest.raises(ValueError, match="answer_text_hash_mismatch"):
        validate_event_evidence(event, answer_text=text, answer_text_hash="0" * 64)

    tampered = AnswerSemanticEvent.model_validate(
        event.model_dump(mode="python")
        | {"answer_excerpt_hash": digest("安全"), "event_fingerprint": "", "provenance_hash": ""}
    )
    with pytest.raises(ValueError, match="answer_excerpt_hash_mismatch"):
        validate_event_evidence(tampered, answer_text=text, answer_text_hash=digest(text))


def test_coreference_recommendation_derives_relation_but_not_literal_mention() -> None:
    text = "可以考虑它，但仅适合大型政企"
    recommendation = make_record(
        "recommendation-relation",
        {
            "subject_entity_id": ENTITY_ID,
            "surface": None,
            "polarity": "conditional_positive",
            "strength": 0.93,
            "scenario": "仅适合大型政企",
            "stance_owner": "assistant",
            "subject_resolution": "query_context_coreference",
            "start": 0,
            "end": len(text),
            "excerpt_hash": digest(text),
        },
    )

    events = derive_answer_semantic_events((recommendation,), context=_context(recommendation))

    assert [event.event_type for event in events] == [SemanticEventType.RECOMMENDATION_RELATION]
    assert events[0].event_value["polarity"] == "conditional_positive"
    assert events[0].qualifiers["subject_resolution"] == "query_context_coreference"
    assert all(event.event_type is not SemanticEventType.ENTITY_MENTION for event in events)


def test_market_rank_claim_remains_distinct_from_recommendation_rank() -> None:
    text = "业内第2"
    raw = {
        "event_type": "market_rank_claim",
        "subject_entity_id": ENTITY_ID,
        "object_entity_id": None,
        "rank": None,
        "list_size": None,
        "list_id": None,
        "ordered": None,
        "rank_low": 2,
        "rank_high": 2,
        "market_scope": "中国网络安全市场",
        "time_scope": "当前",
        "claim_text": text,
        "relation": None,
        "ordinal": None,
        "entity_count": None,
        "source_id": None,
        "start": 0,
        "end": len(text),
        "excerpt_hash": digest(text),
    }
    decision = make_record("rank-semantics", {"rank_events": [raw]})

    events = derive_answer_semantic_events((decision,), context=_context(decision))

    assert len(events) == 1
    assert events[0].event_type is SemanticEventType.MARKET_RANK_CLAIM
    assert events[0].event_value["rank_low"] == 2
    assert all(
        event.event_type is not SemanticEventType.RECOMMENDATION_LIST_RANK for event in events
    )


def test_reviewed_recommendation_does_not_erase_accepted_mention_event() -> None:
    text = "盛邦安全"
    mention = make_record(
        "substantive-entity-mention",
        {
            "entity_id": ENTITY_ID,
            "surface": text,
            "substantive": True,
            "mention_role": "asserted_body",
            "start": 0,
            "end": len(text),
            "excerpt_hash": digest(text),
            "reason_codes": [],
        },
    )
    recommendation_review = make_record(
        "recommendation-relation",
        {},
        decision_id="sdr_recommendation_review_0001",
        status=DecisionStatus.REVIEW_REQUIRED,
        reason_codes=("judge_disagreement",),
    )

    events = derive_answer_semantic_events(
        (recommendation_review, mention), context=_context(recommendation_review, mention)
    )
    capabilities = capability_analyses_from_decisions((recommendation_review, mention))

    assert [event.event_type for event in events] == [SemanticEventType.ENTITY_MENTION]
    assert capabilities["substantive_entity_mention"].status is CapabilityStatus.READY
    assert capabilities["recommendation_relation"].status is CapabilityStatus.REVIEW_REQUIRED


def test_only_accepted_decisions_can_derive_events() -> None:
    failed = make_record(
        "recommendation-relation",
        {},
        status=DecisionStatus.FAILED,
        reason_codes=("model_timeout",),
    )

    assert derive_answer_semantic_events((failed,), context=_context(failed)) == ()


def test_failed_decision_wins_over_review_for_the_same_capability() -> None:
    extraction_review = make_record(
        "claim-extraction",
        {},
        decision_id="sdr_claim_extraction_review_0001",
        status=DecisionStatus.REVIEW_REQUIRED,
        reason_codes=("semantic_evidence_insufficient",),
    )
    verdict_failed = make_record(
        "claim-evidence-verdict",
        {},
        decision_id="sdr_claim_verdict_failed_0001",
        status=DecisionStatus.FAILED,
        reason_codes=("llm_api_timeout",),
    )

    capability = capability_analyses_from_decisions((extraction_review, verdict_failed))[
        "claim_evidence_verdict"
    ]

    assert capability.status is CapabilityStatus.FAILED
    assert "llm_api_timeout" in capability.reason_codes


def test_empty_ready_manifest_is_distinct_from_failed_or_partial_analysis() -> None:
    ready = build_answer_semantic_manifest(
        pub_id=MANIFEST_ID,
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        analysis_run_pub_id="analysis_run_test_0001",
        query_context_fact_pub_id="qcf_test_0001",
        answer_text_hash=digest("没有事件"),
        input_hash=digest("input"),
        extractor_bundle={"entity": "v2"},
        decision_task_bundle={},
        entity_dictionary_hash=digest("dictionary"),
        capability_statuses={},
        events=(),
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    failed = build_answer_semantic_manifest(
        pub_id="asm_manifest_test_0002",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        analysis_run_pub_id="analysis_run_test_0002",
        query_context_fact_pub_id="qcf_test_0001",
        answer_text_hash=digest("失败"),
        input_hash=digest("input-failed"),
        extractor_bundle={"entity": "v2"},
        decision_task_bundle={"recommendation": "2.0.0"},
        entity_dictionary_hash=digest("dictionary"),
        capability_statuses={
            "recommendation_relation": CapabilityAnalysis(
                status=CapabilityStatus.FAILED,
                reason_codes=("model_timeout",),
            )
        },
        events=(),
        failure_code="semantic_analysis_failed",
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )

    assert ready.status is ManifestStatus.READY
    assert ready.event_count == 0
    assert ready.event_set_hash is not None
    assert failed.status is ManifestStatus.FAILED
    assert failed.event_set_hash is None


def test_mixed_failure_and_review_manifest_is_partial_not_review_required() -> None:
    manifest = build_answer_semantic_manifest(
        pub_id="asm_manifest_mixed_failure_review_0001",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        analysis_run_pub_id="analysis_run_mixed_failure_review_0001",
        query_context_fact_pub_id="qcf_test_0001",
        answer_text_hash=digest("混合失败与复核"),
        input_hash=digest("input-mixed-failure-review"),
        extractor_bundle={"entity": "v2"},
        decision_task_bundle={"claim": "2.1.0"},
        entity_dictionary_hash=digest("dictionary"),
        capability_statuses={
            "claim_evidence_verdict": CapabilityAnalysis(
                status=CapabilityStatus.FAILED,
                reason_codes=("model_unavailable_for_policy",),
            ),
            "citation_claim_support": CapabilityAnalysis(
                status=CapabilityStatus.REVIEW_REQUIRED,
                reason_codes=("semantic_evidence_insufficient",),
            ),
        },
        events=(),
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )

    assert manifest.status is ManifestStatus.PARTIAL


def test_manifest_hashes_decisions_and_events_independent_of_input_order() -> None:
    text = "盛邦安全和奇安信"
    first = _manual_entity_event(text, 0, len("盛邦安全"))
    second = AnswerSemanticEvent.model_validate(
        first.model_dump(mode="python")
        | {
            "pub_id": "ase_entity_manual_0002",
            "event_index": 1,
            "subject_entity_id": OTHER_ENTITY_ID,
            "event_value": {
                "surface": "奇安信",
                "mention_role": "asserted_body",
                "substantive": True,
            },
            "answer_text_start": text.index("奇安信"),
            "answer_text_end": text.index("奇安信") + len("奇安信"),
            "answer_excerpt_hash": digest("奇安信"),
            "decision_record_pub_ids": ("sdr_mention_test_0002",),
            "event_fingerprint": "",
            "provenance_hash": "",
        }
    )
    capabilities = {
        "entity_mention": CapabilityAnalysis(
            status=CapabilityStatus.READY,
            decision_record_pub_ids=("sdr_mention_test_0002", "sdr_mention_test_0001"),
        )
    }
    common = dict(
        pub_id=MANIFEST_ID,
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        answer_pub_id=ANSWER_ID,
        analysis_run_pub_id="analysis_run_test_0001",
        query_context_fact_pub_id="qcf_test_0001",
        answer_text_hash=digest(text),
        input_hash=digest("input"),
        extractor_bundle={"entity": "v2"},
        decision_task_bundle={"mention": "2.0.0"},
        entity_dictionary_hash=digest("dictionary"),
        capability_statuses=capabilities,
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    ordered = build_answer_semantic_manifest(events=(first, second), **common)
    reordered_first = AnswerSemanticEvent.model_validate(
        second.model_dump(mode="python") | {"event_index": 0}
    )
    reordered_second = AnswerSemanticEvent.model_validate(
        first.model_dump(mode="python") | {"event_index": 1}
    )
    reversed_manifest = build_answer_semantic_manifest(
        events=(reordered_first, reordered_second), **common
    )

    assert ordered.event_set_hash == reversed_manifest.event_set_hash
    assert ordered.decision_set_hash == reversed_manifest.decision_set_hash


def test_event_values_are_strictly_typed_and_evidence_grounded() -> None:
    with pytest.raises(ValidationError, match="claim_verdict_requires_frozen_evidence"):
        ClaimEvidenceVerdictValue(
            claim_event_pub_id="ase_claim_test_0001",
            verdict="unsupported",
            verification_as_of=NOW,
            evidence_snapshot_refs=(),
        )
    with pytest.raises(ValidationError, match="non_body_mention_cannot_be_substantive"):
        AnswerSemanticEvent(
            pub_id="ase_prompt_echo_test_0001",
            tenant_pub_id=TENANT_ID,
            project_pub_id=PROJECT_ID,
            answer_pub_id=ANSWER_ID,
            semantic_manifest_pub_id=MANIFEST_ID,
            event_index=0,
            event_type="entity_mention",
            subject_entity_id=ENTITY_ID,
            event_value={
                "surface": "盛邦安全",
                "mention_role": "prompt_echo",
                "substantive": True,
            },
            qualifiers={},
            answer_text_start=0,
            answer_text_end=4,
            answer_excerpt_hash=digest("盛邦安全"),
            extractor_version="v2",
            scorer_version="v2",
            derivation_method=DerivationMethod.MODEL,
            decision_record_pub_ids=("sdr_prompt_echo_test_0001",),
            decision_policy_version="policy@2.0.0",
            calibrated_confidence=Decimal("0.95"),
            confidence_state=ConfidenceState.HIGH,
            created_at=NOW,
        )
