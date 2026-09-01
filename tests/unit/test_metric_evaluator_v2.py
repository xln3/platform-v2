from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from domain.metrics.v2.definition_loader import load_definitions
from domain.metrics.v2.evaluator import MetricEvaluator
from domain.metrics.v2.models import (
    DecisionMethod,
    DecisionStatus,
    EligibilityStatus,
    EvaluationInput,
    SemanticCapabilityStatus,
    SemanticDecisionFact,
)
from domain.metrics.v2.query_context import (
    AnalysisLens,
    BrandStructureType,
    ClassificationState,
    ExposureRole,
    QueryContextFact,
    RequestedOperation,
    hash_query_text,
)

TARGET = "brand_sbang"
OTHER = "brand_other"


def _query(
    key: str,
    *,
    lenses: frozenset[AnalysisLens] = frozenset({AnalysisLens.AI_RECOMMENDATION}),
    operations: frozenset[RequestedOperation] = frozenset({RequestedOperation.RECOMMEND}),
    state: ClassificationState = ClassificationState.READY,
    primary: AnalysisLens | None = AnalysisLens.AI_IMPRESSION,
) -> QueryContextFact:
    return QueryContextFact(
        query_key=key,
        query_text_hash=hash_query_text(key),
        primary_lens=primary,
        analysis_lenses=lenses,
        requested_operations=operations,
        detected_entity_ids=frozenset(),
        brand_structure_type=BrandStructureType.BRAND_NEUTRAL,
        classification_state=state,
        classifier_version="query-context@2",
        decision_task_bundle_hash="a" * 64,
        entity_dictionary_hash="b" * 64,
    )


def _decision(task_ref: str, **value: object) -> SemanticDecisionFact:
    return SemanticDecisionFact(
        task_ref=task_ref,
        status=DecisionStatus.ACCEPTED,
        value={"label": "known", **value},
        decision_pub_id="dec_" + task_ref.split("@", 1)[0],
        method=DecisionMethod.HYBRID,
        calibrated=True,
    )


def _event(
    event_id: str,
    event_type: str,
    subject: str | None,
    **value: object,
) -> dict[str, object]:
    return {
        "event_pub_id": event_id,
        "event_type": event_type,
        "subject_entity_id": subject,
        "event_value": value,
    }


def _answer(answer_id: str, events: list[dict[str, object]]) -> EvaluationInput:
    tasks = (
        "substantive-entity-mention@2.0.0",
        "recommendation-relation@2.0.0",
        "rank-semantics@2.0.0",
    )
    return EvaluationInput(
        answer_pub_id=answer_id,
        query_context=_query("q_" + answer_id),
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.BRAND_NEUTRAL,
        capability_statuses={
            "substantive_entity_mention": SemanticCapabilityStatus.READY,
            "recommendation_relation": SemanticCapabilityStatus.READY,
            "rank_semantics": SemanticCapabilityStatus.READY,
        },
        events=tuple(events),
        decisions={task: _decision(task) for task in tasks},
        semantic_decision_set_hash="c" * 64,
    )


def _fixture_a() -> tuple[EvaluationInput, ...]:
    return (
        _answer(
            "a1",
            [
                _event(
                    "evt_a1_m",
                    "entity_mention",
                    TARGET,
                    surface="盛邦安全",
                    substantive=True,
                    mention_role="asserted_body",
                ),
                _event("evt_a1_rec", "recommendation_relation", TARGET, polarity="positive"),
                _event(
                    "evt_a1_rank",
                    "recommendation_list_rank",
                    TARGET,
                    rank=3,
                    ordered=True,
                    list_id="list_a1",
                    list_size=3,
                ),
            ],
        ),
        _answer(
            "a2",
            [
                _event(
                    "evt_a2_rank",
                    "recommendation_list_rank",
                    OTHER,
                    rank=1,
                    ordered=True,
                    list_id="list_a2",
                    list_size=2,
                )
            ],
        ),
        _answer(
            "a3",
            [
                _event(
                    "evt_a3_m",
                    "entity_mention",
                    TARGET,
                    surface="盛邦安全",
                    substantive=True,
                    mention_role="asserted_body",
                ),
                _event("evt_a3_rec", "recommendation_relation", TARGET, polarity="positive"),
            ],
        ),
        _answer("a4", []),
    )


def _raw_value(name: str) -> Decimal | None:
    definition = load_definitions().get(name, "2.0.0")
    evaluations = [MetricEvaluator().evaluate(definition, item) for item in _fixture_a()]
    numerator = sum(
        (
            item.numerator_contribution
            for item in evaluations
            if item.eligibility_status
            in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
        ),
        Decimal("0"),
    )
    denominator = sum(
        (
            item.denominator_contribution
            for item in evaluations
            if item.eligibility_status
            in {EligibilityStatus.INCLUDED_HIT, EligibilityStatus.INCLUDED_MISS}
        ),
        Decimal("0"),
    )
    return numerator / denominator if denominator else None


def test_fixture_a_has_the_six_required_deterministic_values() -> None:
    assert _raw_value("ai_recommendation_organic_mention_rate_v2") == Decimal("0.5")
    assert _raw_value("ai_recommendation_organic_recommendation_rate_v2") == Decimal("0.5")
    assert _raw_value("ai_recommendation_rankable_response_rate_v2") == Decimal("0.5")
    assert _raw_value("ai_recommendation_organic_top3_visibility_rate_v2") == Decimal("0.25")
    assert _raw_value("ai_recommendation_organic_top3_given_rankable_rate_v2") == Decimal("0.5")
    assert _raw_value("ai_recommendation_mean_rank_given_target_ranked_v2") == Decimal("3")


def test_formal_and_conditional_top3_have_distinct_fixed_denominators() -> None:
    registry = load_definitions()
    formal = registry.get("ai_recommendation_organic_top3_visibility_rate_v2", "2.0.0")
    conditional = registry.get("ai_recommendation_organic_top3_given_rankable_rate_v2", "2.0.0")
    a3 = _fixture_a()[2]
    formal_result = MetricEvaluator().evaluate(formal, a3)
    conditional_result = MetricEvaluator().evaluate(conditional, a3)
    assert formal_result.eligibility_status is EligibilityStatus.INCLUDED_MISS
    assert conditional_result.eligibility_status is EligibilityStatus.NOT_APPLICABLE
    assert conditional_result.reason_codes == ("no_rankable_list",)


def test_query_predicate_false_has_priority_over_an_unknown_exposure() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    subject = _answer("priority", [])
    subject = EvaluationInput(
        answer_pub_id=subject.answer_pub_id,
        query_context=_query(
            "q_priority",
            lenses=frozenset({AnalysisLens.AI_IMPRESSION}),
            operations=frozenset({RequestedOperation.DESCRIBE}),
        ),
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.UNKNOWN,
    )
    result = MetricEvaluator().evaluate(definition, subject)
    assert result.eligibility_status is EligibilityStatus.EXCLUDED
    assert result.reason_codes == ("query_lens_mismatch",)


def test_primary_lens_never_controls_metric_admission() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    subject = _fixture_a()[0]
    assert subject.query_context.primary_lens is AnalysisLens.AI_IMPRESSION
    assert (
        MetricEvaluator().evaluate(definition, subject).eligibility_status
        is EligibilityStatus.INCLUDED_HIT
    )


def test_capability_failure_is_failed_and_never_a_semantic_unknown_or_miss() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    subject = _answer("failed", [])
    subject = EvaluationInput(
        answer_pub_id=subject.answer_pub_id,
        query_context=subject.query_context,
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.BRAND_NEUTRAL,
        capability_statuses={"substantive_entity_mention": SemanticCapabilityStatus.FAILED},
    )
    result = MetricEvaluator().evaluate(definition, subject)
    assert result.eligibility_status is EligibilityStatus.ANALYSIS_FAILED
    assert result.reason_codes == ("semantic_analysis_failed",)
    assert result.denominator_contribution == 0


def test_llm_api_failure_reason_survives_metric_evaluation() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    task_ref = "substantive-entity-mention@2.0.0"
    base = _answer("llm_failed", [])
    subject = EvaluationInput(
        answer_pub_id=base.answer_pub_id,
        query_context=base.query_context,
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.BRAND_NEUTRAL,
        capability_statuses={"substantive_entity_mention": SemanticCapabilityStatus.FAILED},
        decisions={
            task_ref: SemanticDecisionFact(
                task_ref=task_ref,
                status=DecisionStatus.FAILED,
                decision_pub_id="sdr_llm_failed",
                method=DecisionMethod.MODEL,
                reason_codes=("llm_api_rate_limited",),
            )
        },
    )

    result = MetricEvaluator().evaluate(definition, subject)

    assert result.eligibility_status is EligibilityStatus.ANALYSIS_FAILED
    assert result.reason_codes == ("llm_api_rate_limited",)
    assert result.supporting_decision_pub_ids == ("sdr_llm_failed",)


def test_required_failure_pre_scan_wins_over_earlier_review_unknown() -> None:
    definition = load_definitions().get(
        "ai_impression_requested_dimension_coverage_v2", "2.0.0"
    )
    review_ref = "requested-dimension-applicability@2.0.0"
    failed_ref = "answer-dimension-coverage@2.0.0"
    subject = EvaluationInput(
        answer_pub_id="ans_required_failure_priority",
        query_context=_query(
            "q_required_failure_priority",
            lenses=frozenset({AnalysisLens.AI_IMPRESSION}),
            operations=frozenset({RequestedOperation.DESCRIBE}),
            state=ClassificationState.REVIEW_REQUIRED,
        ),
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.BRAND_NEUTRAL,
        capability_statuses={
            "requested_dimension_applicability": SemanticCapabilityStatus.REVIEW_REQUIRED,
            "answer_dimension_coverage": SemanticCapabilityStatus.FAILED,
        },
        decisions={
            review_ref: SemanticDecisionFact(
                task_ref=review_ref,
                status=DecisionStatus.REVIEW_REQUIRED,
                decision_pub_id="sdr_review_first",
                reason_codes=("semantic_evidence_insufficient",),
            ),
            failed_ref: SemanticDecisionFact(
                task_ref=failed_ref,
                status=DecisionStatus.FAILED,
                decision_pub_id="sdr_failed_second",
                reason_codes=("model_unavailable_for_policy",),
            ),
        },
    )

    result = MetricEvaluator().evaluate(definition, subject)

    assert result.eligibility_status is EligibilityStatus.ANALYSIS_FAILED
    assert result.reason_codes == ("model_unavailable_for_policy",)
    assert result.supporting_decision_pub_ids == ("sdr_failed_second",)


def test_execution_integrity_failures_are_not_semantic_unknown() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    base = _answer("integrity_failed", [])

    cases = (
        (replace(base, event_invariants_valid=False), "semantic_event_integrity_failed"),
        (replace(base, evidence_spans_valid=False), "evidence_span_integrity_failed"),
        (replace(base, evidence_retrieval_ready=False), "evidence_retrieval_failed"),
    )
    for subject, expected_reason in cases:
        result = MetricEvaluator().evaluate(definition, subject)
        assert result.eligibility_status is EligibilityStatus.ANALYSIS_FAILED
        assert result.reason_codes == (expected_reason,)


def test_accepted_uncalibrated_strong_model_decision_remains_metric_eligible() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    task_ref = "substantive-entity-mention@2.0.0"
    subject = _answer(
        "uncalibrated",
        [
            _event(
                "evt_uncalibrated",
                "entity_mention",
                TARGET,
                surface="盛邦安全",
                substantive=True,
                mention_role="asserted_body",
            )
        ],
    )
    subject = replace(
        subject,
        decisions={
            **subject.decisions,
            task_ref: SemanticDecisionFact(
                task_ref=task_ref,
                status=DecisionStatus.ACCEPTED,
                value={"substantive": True, "mention_role": "asserted_body"},
                decision_pub_id="sdr_uncalibrated",
                method=DecisionMethod.MODEL,
                calibrated=False,
                policy_matches=True,
                evidence_ready=True,
                reason_codes=("accepted_without_calibration",),
            ),
        },
    )

    result = MetricEvaluator().evaluate(definition, subject)

    assert result.eligibility_status is EligibilityStatus.INCLUDED_HIT
    assert result.supporting_decision_pub_ids == ("sdr_uncalibrated",)


def test_claim_metrics_keep_one_answer_evaluation_but_count_three_claims() -> None:
    registry = load_definitions()
    events = [
        _event(f"evt_claim_{index}", "factual_claim", TARGET, claim_text=f"claim {index}")
        for index in range(3)
    ]
    events.extend(
        _event(
            f"evt_verdict_{index}",
            "claim_evidence_verdict",
            TARGET,
            verdict=verdict,
        )
        for index, verdict in enumerate(("supported", "supported", "unsupported"))
    )
    task = "claim-evidence-verdict@2.0.0"
    subject = EvaluationInput(
        answer_pub_id="claim_answer",
        query_context=_query(
            "claim_query",
            lenses=frozenset({AnalysisLens.AI_IMPRESSION}),
            operations=frozenset({RequestedOperation.FACT_LOOKUP}),
        ),
        focal_entity_id=TARGET,
        exposure_role=ExposureRole.FOCAL_NAMED_ONLY,
        capability_statuses={"claim_verification": SemanticCapabilityStatus.READY},
        events=tuple(events),
        decisions={task: _decision(task)},
    )
    accuracy = MetricEvaluator().evaluate(registry.get("claim_accuracy_rate_v2"), subject)
    unsupported = MetricEvaluator().evaluate(registry.get("unsupported_claim_rate_v2"), subject)
    assert (accuracy.numerator_contribution, accuracy.denominator_contribution) == (
        Decimal("2"),
        Decimal("3"),
    )
    assert (unsupported.numerator_contribution, unsupported.denominator_contribution) == (
        Decimal("1"),
        Decimal("3"),
    )
    assert len(accuracy.supporting_event_pub_ids) == 3
