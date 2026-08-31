from __future__ import annotations

import pytest
from decision_v2_fixtures import ENTITY_ID, make_record

from domain.analysis.v2.decision_models import DecisionStatus as AnalysisDecisionStatus
from domain.metrics.v2.definition_loader import load_definitions
from domain.metrics.v2.evaluator import MetricEvaluator
from domain.metrics.v2.models import (
    DecisionMethod,
    DecisionStatus,
    EligibilityStatus,
    EvaluationInput,
    SemanticDecisionFact,
)
from domain.metrics.v2.query_context import (
    BrandStructureType,
    ClassificationState,
    ExposureRole,
    QueryContextFact,
)
from workflows.activities.semantic_decisions_v2 import derive_query_context_activity


@pytest.mark.asyncio
async def test_query_llm_failure_stays_failed_through_metric_evaluation() -> None:
    intent = make_record(
        "query-intent",
        {},
        decision_id="sdr_query_intent_failed_0001",
        status=AnalysisDecisionStatus.FAILED,
        subject_ref={"query_pub_id": "qry_failure_0001"},
        reason_codes=("llm_api_timeout",),
    )
    entities = make_record(
        "query-brand-entity-resolution",
        {"resolutions": []},
        decision_id="sdr_query_entities_accepted_0001",
        subject_ref={"query_pub_id": "qry_failure_0001"},
    )
    derived = await derive_query_context_activity(
        {
            "tenant_pub_id": "tenant_test",
            "project_pub_id": "project_test",
            "query_key": "qry_failure_0001",
            "query_pub_id": "qry_failure_0001",
            "query_text_hash": "a" * 64,
            "classifier_version": "query-context-v2",
            "decision_task_bundle_hash": "b" * 64,
            "entity_dictionary_hash": "c" * 64,
            "decisions": [
                intent.model_dump(mode="json"),
                entities.model_dump(mode="json"),
            ],
            "focal_entity_ids": [ENTITY_ID],
        }
    )
    fact = derived["fact"]
    assert fact["classification_state"] == "failed"
    assert fact["decision_record_pub_ids"] == sorted(
        [intent.decision_pub_id, entities.decision_pub_id]
    )

    query_context = QueryContextFact(
        query_key=fact["query_key"],
        query_text_hash=fact["query_text_hash"],
        analysis_lenses=frozenset(),
        requested_operations=frozenset(),
        detected_entity_ids=frozenset(),
        brand_structure_type=BrandStructureType.UNKNOWN,
        classification_state=ClassificationState.FAILED,
        classifier_version=fact["classifier_version"],
        decision_task_bundle_hash=fact["decision_task_bundle_hash"],
        entity_dictionary_hash=fact["entity_dictionary_hash"],
        decision_record_pub_ids=tuple(fact["decision_record_pub_ids"]),
    )
    subject = EvaluationInput(
        answer_pub_id="ans_query_failure_0001",
        query_context=query_context,
        focal_entity_id=ENTITY_ID,
        exposure_role=ExposureRole.UNKNOWN,
        decisions={
            "query-intent@2.0.0": SemanticDecisionFact(
                task_ref="query-intent@2.0.0",
                status=DecisionStatus.FAILED,
                decision_pub_id=intent.decision_pub_id,
                method=DecisionMethod.MODEL,
                reason_codes=("llm_api_timeout",),
            ),
            "query-brand-entity-resolution@2.0.0": SemanticDecisionFact(
                task_ref="query-brand-entity-resolution@2.0.0",
                status=DecisionStatus.ACCEPTED,
                decision_pub_id=entities.decision_pub_id,
                method=DecisionMethod.MODEL,
            ),
        },
    )
    definition = load_definitions().get(
        "ai_recommendation_organic_mention_rate_v2", "2.0.0"
    )

    evaluation = MetricEvaluator().evaluate(definition, subject)

    assert evaluation.eligibility_status is EligibilityStatus.ANALYSIS_FAILED
    assert evaluation.reason_codes == ("llm_api_timeout",)
    assert evaluation.supporting_decision_pub_ids == (intent.decision_pub_id,)
