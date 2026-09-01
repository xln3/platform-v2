from __future__ import annotations

from datetime import timedelta

import pytest
from decision_v2_fixtures import (
    ANSWER_ID,
    ENTITY_ID,
    NOW,
    PROJECT_ID,
    TENANT_ID,
    candidate_set,
    digest,
    evidence_span,
    make_attempt,
    policy_for,
    task,
)

from domain.analysis.v2.adjudication import AdjudicationRequest, adjudicate_decision
from domain.analysis.v2.decision_models import (
    AttemptRole,
    AttemptValidationStatus,
    DecisionMethod,
    DecisionStatus,
    subject_key_for,
)
from domain.analysis.v2.decision_task_schema import JudgePolicyDefinition


def _mention_output(text: str) -> dict[str, object]:
    return {
        "entity_id": ENTITY_ID,
        "surface": text,
        "substantive": True,
        "mention_role": "asserted_body",
        "start": 0,
        "end": len(text),
        "excerpt_hash": digest(text),
        "reason_codes": [],
    }


def _recommendation_output(text: str, polarity: str) -> dict[str, object]:
    return {
        "subject_entity_id": ENTITY_ID,
        "surface": None,
        "polarity": polarity,
        "strength": 0.9,
        "scenario": "大型政企" if polarity == "conditional_positive" else "",
        "stance_owner": "assistant",
        "subject_resolution": "query_context_coreference",
        "start": 0,
        "end": len(text),
        "excerpt_hash": digest(text),
    }


def _request(
    task_name: str,
    text: str,
    attempts: tuple,
    *,
    confidences: dict[str, float] | None = None,
    official_use: bool = False,
    human_override: bool = False,
    include_request_span: bool = True,
    judge_policy: JudgePolicyDefinition | None = None,
    task_version: str = "2.0.0",
    dependency_statuses: dict[str, DecisionStatus] | None = None,
    evidence_context: dict[str, object] | None = None,
    required_chunks_complete: bool = True,
) -> AdjudicationRequest:
    definition = task(task_name, version=task_version)
    return AdjudicationRequest(
        task=definition,
        judge_policy=judge_policy or policy_for(task_name, version=task_version),
        attempts=attempts,
        calibrated_confidences=confidences or {},
        candidate_set=candidate_set(),
        answer_text=text,
        expected_answer_text_hash=digest(text),
        evidence_refs=("answer-snapshot-v2",),
        evidence_spans=(evidence_span(text, 0, len(text)),) if include_request_span else (),
        answer_source_ref=ANSWER_ID,
        evidence_context=evidence_context or {},
        dependency_statuses=(
            dependency_statuses
            if dependency_statuses is not None
            else {ref: DecisionStatus.ACCEPTED for ref in definition.dependency_task_refs}
        ),
        required_chunks_complete=required_chunks_complete,
        explicit_human_override=human_override,
        official_use=official_use,
    )


def test_calibrated_deterministic_fast_path_still_creates_versioned_decision() -> None:
    text = "盛邦安全"
    definition = task("substantive-entity-mention")
    attempt = make_attempt(
        definition,
        _mention_output(text),
        method=DecisionMethod.DETERMINISTIC,
        fast_path_name="exact_substantive_body_mention",
    )

    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (attempt,),
            confidences={attempt.pub_id: 0.995},
        )
    )

    assert outcome.status is DecisionStatus.ACCEPTED
    assert outcome.method is DecisionMethod.DETERMINISTIC
    assert outcome.selected_attempt_pub_ids == (attempt.pub_id,)

    subject_ref = {"answer_pub_id": ANSWER_ID, "entity_id": ENTITY_ID}
    record = outcome.to_record(
        decision_pub_id="sdr_deterministic_fast_path_0001",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        decision_job_pub_id="sdj_job_test_0001",
        task=definition,
        subject_type=definition.subject_type,
        subject_key=subject_key_for(subject_ref),
        subject_ref=subject_ref,
        input_snapshot_ref="answer-snapshot-v2",
        input_hash=digest("input"),
        context_hash=digest("context"),
        judge_policy_hash=policy_for(definition.name).policy_hash,
        created_at=NOW,
    )
    assert record.task_ref == "substantive-entity-mention@2.0.0"
    assert record.judge_policy_hash == policy_for(definition.name).policy_hash


def test_deterministic_string_rule_cannot_emit_model_required_recommendation() -> None:
    text = "值得推荐"
    definition = task("recommendation-relation")
    attempt = make_attempt(
        definition,
        _recommendation_output(text, "positive"),
        method=DecisionMethod.DETERMINISTIC,
    )

    outcome = adjudicate_decision(
        _request(definition.name, text, (attempt,), confidences={attempt.pub_id: 1.0})
    )

    assert outcome.status is DecisionStatus.FAILED
    assert "decision_method_not_allowed" in outcome.reason_codes


def test_single_strong_proposer_does_not_wait_for_optional_nested_review() -> None:
    text = "可以考虑它，但仅适合大型政企"
    recommendation_task = task("recommendation-relation", version="2.1.0")
    proposer = make_attempt(
        recommendation_task,
        _recommendation_output(text, "conditional_positive"),
        role=AttemptRole.PROPOSER,
        index=0,
    )
    verifier = make_attempt(
        recommendation_task,
        _recommendation_output(text, "negative"),
        role=AttemptRole.VERIFIER,
        index=1,
        verifier_route=True,
    )
    recommendation = adjudicate_decision(
        _request(
            recommendation_task.name,
            text,
            (proposer, verifier),
            confidences={proposer.pub_id: 0.98, verifier.pub_id: 0.98},
            task_version="2.1.0",
        )
    )

    assert recommendation.status is DecisionStatus.ACCEPTED
    assert recommendation.result["polarity"] == "conditional_positive"
    assert recommendation.selected_attempt_pub_ids == (proposer.pub_id,)


@pytest.mark.parametrize(
    ("reason_code", "expected_status"),
    [
        ("model_unavailable_for_policy", DecisionStatus.FAILED),
        ("model_timeout", DecisionStatus.FAILED),
        ("llm_api_rate_limited", DecisionStatus.FAILED),
        ("llm_api_budget_exhausted", DecisionStatus.FAILED),
        ("structured_output_invalid", DecisionStatus.FAILED),
    ],
)
def test_machine_failures_stay_unknown_without_dictionary_fallback(
    reason_code: str, expected_status: DecisionStatus
) -> None:
    text = "推荐盛邦安全"
    definition = task("recommendation-relation")
    failed = make_attempt(
        definition,
        None,
        status=AttemptValidationStatus.ERROR,
        reason_codes=(reason_code,),
    )

    outcome = adjudicate_decision(_request(definition.name, text, (failed,)))

    assert outcome.status is expected_status
    assert reason_code in outcome.reason_codes
    assert outcome.result == {}
    assert outcome.selected_attempt_pub_ids == ()


def test_valid_model_output_without_prebuilt_calibration_is_accepted_and_audited() -> None:
    text = "可以考虑它，但仅适合大型政企"
    definition = task("recommendation-relation", version="2.1.0")
    output = _recommendation_output(text, "conditional_positive")
    proposer = make_attempt(definition, output, index=0)
    outcome = adjudicate_decision(
        _request(definition.name, text, (proposer,), task_version="2.1.0")
    )

    assert outcome.status is DecisionStatus.ACCEPTED
    assert outcome.calibrated_confidence is None
    assert "accepted_without_calibration" in outcome.reason_codes


def test_llm_infrastructure_failure_precedes_missing_evidence() -> None:
    text = "该主张需要外部证据"
    definition = task("claim-evidence-verdict")
    failed = make_attempt(
        definition,
        None,
        status=AttemptValidationStatus.ERROR,
        reason_codes=("llm_api_timeout",),
    )

    outcome = adjudicate_decision(_request(definition.name, text, (failed,)))

    assert outcome.status is DecisionStatus.FAILED
    assert outcome.reason_codes == ("llm_api_timeout",)


def test_evidence_retrieval_execution_failure_is_not_semantic_unknown() -> None:
    text = "该主张需要外部证据"
    definition = task("claim-evidence-verdict")

    outcome = adjudicate_decision(_request(definition.name, text, ()))

    assert outcome.status is DecisionStatus.FAILED
    assert outcome.reason_codes == ("evidence_retrieval_failed",)


def test_known_evidence_failure_precedes_dependency_unknown() -> None:
    text = "该主张需要外部证据"
    definition = task("claim-evidence-verdict")
    dependencies = {
        ref: DecisionStatus.ABSTAINED for ref in definition.dependency_task_refs
    }

    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (),
            dependency_statuses=dependencies,
            evidence_context={
                "evidence_bundle_status": "failed",
                "retrieval_protocol_complete": False,
            },
        )
    )

    assert outcome.status is DecisionStatus.FAILED
    assert outcome.reason_codes == ("evidence_retrieval_failed",)


def test_truncated_evidence_is_chunk_failure_before_dependency_unknown() -> None:
    text = "该主张需要外部证据"
    definition = task("claim-evidence-verdict")
    dependencies = {
        ref: DecisionStatus.ABSTAINED for ref in definition.dependency_task_refs
    }

    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (),
            dependency_statuses=dependencies,
            evidence_context={"evidence_material_truncated": True},
        )
    )

    assert outcome.status is DecisionStatus.FAILED
    assert outcome.reason_codes == ("chunk_incomplete",)


def test_validated_model_output_spans_become_immutable_record_evidence() -> None:
    text = "可以考虑它，但仅适合大型政企"
    definition = task("recommendation-relation", version="2.1.0")
    proposer = make_attempt(definition, _recommendation_output(text, "conditional_positive"))

    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (proposer,),
            include_request_span=False,
            task_version="2.1.0",
        )
    )

    assert outcome.status is DecisionStatus.ACCEPTED
    assert len(outcome.evidence_spans) == 1
    assert outcome.evidence_spans[0].source_ref == ANSWER_ID
    assert outcome.evidence_spans[0].excerpt_hash == digest(text)


def test_human_override_is_a_new_superseding_record_not_an_update() -> None:
    text = "可以考虑它，但仅适合大型政企"
    definition = task("recommendation-relation")
    output = _recommendation_output(text, "conditional_positive")
    human = make_attempt(
        definition,
        output,
        role=AttemptRole.HUMAN,
        method=DecisionMethod.HUMAN,
        index=2,
    )
    outcome = adjudicate_decision(_request(definition.name, text, (human,), human_override=True))
    subject_ref = {"answer_pub_id": ANSWER_ID, "query_pub_id": "qry_test", "entity_id": ENTITY_ID}
    record = outcome.to_record(
        decision_pub_id="sdr_human_override_0002",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        decision_job_pub_id="sdj_job_test_0002",
        task=definition,
        subject_type=definition.subject_type,
        subject_key=subject_key_for(subject_ref),
        subject_ref=subject_ref,
        input_snapshot_ref="answer-snapshot-v2",
        input_hash=digest("input-human"),
        context_hash=digest("context"),
        judge_policy_hash=policy_for(definition.name).policy_hash,
        supersedes_pub_id="sdr_previous_decision_0001",
        created_at=NOW,
    )

    assert outcome.status is DecisionStatus.ACCEPTED
    assert record.method is DecisionMethod.HUMAN
    assert record.supersedes_pub_id == "sdr_previous_decision_0001"
    assert "human_override" in record.reason_codes


def test_same_frozen_decision_material_has_same_hash_despite_new_id_and_time() -> None:
    text = "盛邦安全"
    definition = task("substantive-entity-mention")
    attempt = make_attempt(
        definition,
        _mention_output(text),
        method=DecisionMethod.DETERMINISTIC,
        fast_path_name="exact_substantive_body_mention",
    )
    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (attempt,),
            confidences={attempt.pub_id: 0.99},
        )
    )
    subject_ref = {"answer_pub_id": ANSWER_ID, "entity_id": ENTITY_ID}
    common = dict(
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        decision_job_pub_id="sdj_job_test_0001",
        task=definition,
        subject_type=definition.subject_type,
        subject_key=subject_key_for(subject_ref),
        subject_ref=subject_ref,
        input_snapshot_ref="answer-snapshot-v2",
        input_hash=digest("input"),
        context_hash=digest("context"),
        judge_policy_hash=policy_for(definition.name).policy_hash,
    )
    first = outcome.to_record(decision_pub_id="sdr_same_material_0001", created_at=NOW, **common)
    duplicate = outcome.to_record(
        decision_pub_id="sdr_same_material_0002",
        created_at=NOW + timedelta(seconds=30),
        **common,
    )

    assert first.decision_hash == duplicate.decision_hash


def test_experimental_policy_cannot_auto_accept_official_decision() -> None:
    text = "盛邦安全"
    definition = task("substantive-entity-mention")
    attempt = make_attempt(
        definition,
        _mention_output(text),
        method=DecisionMethod.DETERMINISTIC,
        fast_path_name="exact_substantive_body_mention",
    )

    original = policy_for(definition.name)
    experimental = JudgePolicyDefinition.model_validate(
        original.model_dump(mode="python", exclude={"policy_hash"})
        | {
            "status": "experimental",
            "published_at": None,
            "calibration_artifact_hash": None,
        }
    )
    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (attempt,),
            confidences={attempt.pub_id: 1.0},
            official_use=True,
            judge_policy=experimental,
        )
    )

    assert outcome.status is DecisionStatus.ABSTAINED
    assert outcome.reason_codes == ("judge_policy_not_published_for_official_use",)


def test_published_policy_can_accept_official_decision_without_calibration() -> None:
    text = "盛邦安全"
    definition = task("substantive-entity-mention")
    attempt = make_attempt(
        definition,
        _mention_output(text),
        method=DecisionMethod.DETERMINISTIC,
        fast_path_name="exact_substantive_body_mention",
    )
    original = policy_for(definition.name)
    published = JudgePolicyDefinition.model_validate(
        original.model_dump(mode="python", exclude={"policy_hash"})
        | {
            "status": "published",
            "published_at": NOW,
            "calibration_artifact_hash": None,
        }
    )

    outcome = adjudicate_decision(
        _request(
            definition.name,
            text,
            (attempt,),
            official_use=True,
            judge_policy=published,
        )
    )

    assert outcome.status is DecisionStatus.ACCEPTED
    assert outcome.calibrated_confidence is None
    assert "accepted_without_calibration" in outcome.reason_codes
