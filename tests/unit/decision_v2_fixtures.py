from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from domain.analysis.v2._canonical import canonical_hash
from domain.analysis.v2.candidates import Candidate, CandidateSet
from domain.analysis.v2.decision_models import (
    AttemptRole,
    AttemptValidationStatus,
    DecisionMethod,
    DecisionStatus,
    EvidenceSpan,
    SemanticDecisionAttempt,
    SemanticDecisionRecord,
    subject_key_for,
)
from domain.analysis.v2.decision_task_loader import (
    DecisionTaskRegistry,
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.analysis.v2.decision_task_schema import (
    DecisionTaskDefinition,
    JudgePolicyDefinition,
)

NOW = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
ANSWER_ID = "ans_test_0001"
TENANT_ID = "tenant_test"
PROJECT_ID = "project_test"
ENTITY_ID = "brand_shengbang"
OTHER_ENTITY_ID = "brand_qianxin"


def digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def registry() -> DecisionTaskRegistry:
    return load_builtin_task_definitions()


def task(name: str, *, version: str = "2.0.0") -> DecisionTaskDefinition:
    return registry().get(f"{name}@{version}")


def policy_for(task_name: str, *, version: str = "2.0.0") -> JudgePolicyDefinition:
    tasks = registry()
    task_ref = f"{task_name}@{version}"
    return next(
        policy
        for policy in load_builtin_judge_policies(tasks=tasks)
        if task_ref in policy.compatible_task_refs
    )


def candidate_set() -> CandidateSet:
    return CandidateSet(
        candidates=(
            Candidate(candidate_id=ENTITY_ID, candidate_type="brand", labels=("盛邦安全",)),
            Candidate(candidate_id=OTHER_ENTITY_ID, candidate_type="brand", labels=("奇安信",)),
        ),
        source_ref="entity-dictionary-v2",
        source_hash=digest("entity-dictionary-v2"),
    )


def evidence_span(text: str, start: int, end: int, *, role: str = "predicate") -> EvidenceSpan:
    return EvidenceSpan(
        source_ref=ANSWER_ID,
        start=start,
        end=end,
        excerpt_hash=digest(text[start:end]),
        source_text_hash=digest(text),
        role=role,
    )


def make_attempt(
    task_definition: DecisionTaskDefinition,
    output: dict[str, Any] | None,
    *,
    role: AttemptRole = AttemptRole.PROPOSER,
    method: DecisionMethod = DecisionMethod.MODEL,
    index: int = 0,
    status: AttemptValidationStatus = AttemptValidationStatus.VALID,
    reason_codes: tuple[str, ...] = (),
    fast_path_name: str | None = None,
    verifier_route: bool = False,
) -> SemanticDecisionAttempt:
    provider = model = revision = None
    inference_config: dict[str, Any] = {}
    if method in {DecisionMethod.MODEL, DecisionMethod.HYBRID}:
        route = policy_for(task_definition.name, version=task_definition.version).model_routes[0]
        provider = route.provider
        model = route.model
        revision = "fixture-verifier-revision" if verifier_route else "fixture-primary-revision"
        inference_config = {"temperature": 0, "route_name": route.route_name}
    response_hash = canonical_hash(output) if output is not None else None
    return SemanticDecisionAttempt(
        pub_id=f"sda_attempt_{index:04d}_{role.value}",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        decision_job_pub_id="sdj_job_test_0001",
        attempt_index=index,
        role=role,
        method=method,
        provider=provider,
        model=model,
        model_revision=revision,
        inference_config=inference_config,
        prompt_template_ref=task_definition.prompt_template_ref,
        prompt_template_hash=task_definition.prompt_template_hash,
        rubric_hash=task_definition.rubric_hash,
        output_schema_hash=canonical_hash(task_definition.output_schema),
        request_payload_hash=digest(f"request-{index}"),
        response_payload_hash=response_hash,
        validated_output=output if status is AttemptValidationStatus.VALID else None,
        rationale_summary=(
            "短理由，有明确证据。" if status is AttemptValidationStatus.VALID else None
        ),
        validation_status=status,
        reason_codes=reason_codes,
        fast_path_name=fast_path_name,
        latency_ms=10,
        created_at=NOW,
    )


def make_record(
    task_name: str,
    result: dict[str, Any],
    *,
    decision_id: str | None = None,
    status: DecisionStatus = DecisionStatus.ACCEPTED,
    method: DecisionMethod = DecisionMethod.MODEL,
    subject_ref: dict[str, Any] | None = None,
    confidence: float | None = 0.97,
    reason_codes: tuple[str, ...] = ("accepted",),
    supersedes_pub_id: str | None = None,
) -> SemanticDecisionRecord:
    definition = task(task_name)
    policy = policy_for(task_name)
    reference = subject_ref or {"answer_pub_id": ANSWER_ID, "entity_id": ENTITY_ID}
    if status is not DecisionStatus.ACCEPTED:
        result = {}
        confidence = None
    return SemanticDecisionRecord(
        decision_pub_id=decision_id or f"sdr_{task_name.replace('-', '_')}_0001",
        tenant_pub_id=TENANT_ID,
        project_pub_id=PROJECT_ID,
        decision_job_pub_id="sdj_job_test_0001",
        task_name=definition.name,
        task_version=definition.version,
        task_definition_hash=definition.definition_hash,
        subject_type=definition.subject_type,
        subject_key=subject_key_for(reference),
        subject_ref=reference,
        input_snapshot_ref="snapshot-answer-test-v2",
        input_hash=digest("input"),
        context_hash=digest("context"),
        method=method,
        status=status,
        result=result,
        rationale_summary="证据支持该结构化标签。" if status is DecisionStatus.ACCEPTED else None,
        calibrated_confidence=confidence,
        calibration_bucket="0.95-1.00" if confidence is not None else None,
        reason_codes=reason_codes,
        selected_attempt_pub_ids=("sda_attempt_selected_0001",)
        if status is DecisionStatus.ACCEPTED
        else (),
        judge_policy_hash=policy.policy_hash,
        rubric_ref=definition.rubric_ref,
        rubric_hash=definition.rubric_hash,
        output_schema_hash=canonical_hash(definition.output_schema),
        supersedes_pub_id=supersedes_pub_id,
        created_at=NOW,
    )
