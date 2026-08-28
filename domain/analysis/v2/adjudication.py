"""Fail-closed semantic decision adjudication.

No majority vote and no keyword fallback are implemented here.  Agreement,
evidence closure, schema validity, candidate closure, and an offline-calibrated
confidence are all required before automatic acceptance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import Field, model_validator

from domain.analysis.v2._canonical import FrozenDomainModel, Sha256Hex, canonical_hash
from domain.analysis.v2.candidates import CandidateBoundaryError, CandidateSet, validate_fast_path
from domain.analysis.v2.decision_models import (
    AttemptRole,
    AttemptValidationStatus,
    DecisionMethod,
    DecisionStatus,
    EvidenceSpan,
    SemanticDecisionAttempt,
    SemanticDecisionRecord,
)
from domain.analysis.v2.decision_task_schema import (
    DecisionMethodPolicy,
    DecisionTaskDefinition,
    DefinitionStatus,
    JudgePolicyDefinition,
    SubjectType,
    validate_policy_compatibility,
)
from domain.analysis.v2.output_validation import validate_decision_output, validate_subject_ref


class AdjudicationRequest(FrozenDomainModel):
    task: DecisionTaskDefinition
    judge_policy: JudgePolicyDefinition
    attempts: tuple[SemanticDecisionAttempt, ...]
    calibrated_confidences: dict[str, float] = Field(default_factory=dict)
    candidate_set: CandidateSet | None = None
    answer_text: str | None = None
    expected_answer_text_hash: Sha256Hex | None = None
    evidence_context: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    dependency_statuses: dict[str, DecisionStatus] = Field(default_factory=dict)
    required_chunks_complete: bool = True
    explicit_human_override: bool = False
    official_use: bool = False

    @model_validator(mode="after")
    def attempts_and_confidences_are_coherent(self) -> Self:
        pub_ids = [attempt.pub_id for attempt in self.attempts]
        if len(pub_ids) != len(set(pub_ids)):
            raise ValueError("adjudication_attempt_pub_ids_must_be_unique")
        indices = [attempt.attempt_index for attempt in self.attempts]
        if len(indices) != len(set(indices)):
            raise ValueError("adjudication_attempt_indices_must_be_unique")
        if not set(self.calibrated_confidences) <= set(pub_ids):
            raise ValueError("calibration_references_unknown_attempt")
        if any(not 0 <= value <= 1 for value in self.calibrated_confidences.values()):
            raise ValueError("calibrated_confidence_out_of_range")
        return self


class DecisionAdjudication(FrozenDomainModel):
    method: DecisionMethod
    status: DecisionStatus
    result: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...]
    selected_attempt_pub_ids: tuple[str, ...] = ()
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    calibration_bucket: str | None = None
    evidence_refs: tuple[str, ...] = ()
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    rationale_summary: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def terminal_state_is_coherent(self) -> Self:
        if self.status is DecisionStatus.ACCEPTED:
            if not self.result or not self.selected_attempt_pub_ids:
                raise ValueError("accepted_adjudication_is_incomplete")
        elif self.selected_attempt_pub_ids:
            raise ValueError("nonaccepted_adjudication_cannot_select_attempts")
        if (self.calibrated_confidence is None) != (self.calibration_bucket is None):
            raise ValueError("adjudication_calibration_bucket_mismatch")
        return self

    def to_record(
        self,
        *,
        decision_pub_id: str,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_job_pub_id: str,
        task: DecisionTaskDefinition,
        subject_type: SubjectType,
        subject_key: str,
        subject_ref: dict[str, Any],
        input_snapshot_ref: str,
        input_hash: str,
        context_hash: str,
        judge_policy_hash: str,
        created_at: datetime,
        metric_name: str | None = None,
        metric_version: str | None = None,
        supersedes_pub_id: str | None = None,
    ) -> SemanticDecisionRecord:
        if subject_type is not task.subject_type:
            raise ValueError("decision_subject_type_task_mismatch")
        validate_subject_ref(task=task, subject_ref=subject_ref).raise_for_errors()
        return SemanticDecisionRecord(
            decision_pub_id=decision_pub_id,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            decision_job_pub_id=decision_job_pub_id,
            task_name=task.name,
            task_version=task.version,
            task_definition_hash=task.definition_hash,
            subject_type=subject_type,
            subject_key=subject_key,
            subject_ref=subject_ref,
            metric_name=metric_name,
            metric_version=metric_version,
            input_snapshot_ref=input_snapshot_ref,
            input_hash=input_hash,
            context_hash=context_hash,
            method=self.method,
            status=self.status,
            result=self.result,
            rationale_summary=self.rationale_summary,
            calibrated_confidence=self.calibrated_confidence,
            calibration_bucket=self.calibration_bucket,
            reason_codes=self.reason_codes,
            evidence_refs=self.evidence_refs,
            evidence_spans=self.evidence_spans,
            selected_attempt_pub_ids=self.selected_attempt_pub_ids,
            judge_policy_hash=judge_policy_hash,
            rubric_ref=task.rubric_ref,
            rubric_hash=task.rubric_hash,
            output_schema_hash=canonical_hash(task.output_schema),
            supersedes_pub_id=supersedes_pub_id,
            created_at=created_at,
        )


def adjudicate_decision(request: AdjudicationRequest) -> DecisionAdjudication:
    """Resolve validated attempts into one atomic result or an honest unknown state."""

    try:
        validate_policy_compatibility(request.task, request.judge_policy)
    except ValueError as error:
        return _terminal(
            request,
            DecisionStatus.FAILED,
            _method_for_request(request),
            (str(error),),
        )
    if request.official_use and request.judge_policy.status is not DefinitionStatus.PUBLISHED:
        return _terminal(
            request,
            DecisionStatus.ABSTAINED,
            _method_for_request(request),
            ("judge_policy_not_calibrated_for_official_use",),
        )
    if any(
        request.dependency_statuses.get(ref) is not DecisionStatus.ACCEPTED
        for ref in request.task.dependency_task_refs
    ):
        return _terminal(
            request,
            DecisionStatus.ABSTAINED,
            _method_for_request(request),
            ("dependency_unknown",),
        )
    if not request.required_chunks_complete:
        return _terminal(
            request,
            DecisionStatus.REVIEW_REQUIRED,
            _method_for_request(request),
            ("chunk_incomplete",),
        )
    evidence_error = _evidence_requirement_error(request)
    if evidence_error is not None:
        return _terminal(
            request,
            DecisionStatus.ABSTAINED,
            _method_for_request(request),
            (evidence_error,),
        )

    valid: list[SemanticDecisionAttempt] = []
    invalid_codes: set[str] = set()
    for attempt in sorted(request.attempts, key=lambda item: item.attempt_index):
        method_error = _attempt_method_error(request.task, attempt)
        route_error = _attempt_route_error(request.judge_policy, attempt)
        hash_error = _attempt_contract_hash_error(request.task, attempt)
        if method_error or route_error or hash_error:
            invalid_codes.add(method_error or route_error or hash_error or "attempt_invalid")
            continue
        if attempt.validation_status is not AttemptValidationStatus.VALID:
            invalid_codes.update(attempt.reason_codes or ("structured_output_invalid",))
            continue
        checked = validate_decision_output(
            task=request.task,
            output=attempt.validated_output,
            candidate_set=request.candidate_set,
            answer_text=request.answer_text,
            expected_answer_text_hash=request.expected_answer_text_hash,
            evidence_context=request.evidence_context,
        )
        if not checked.is_valid:
            invalid_codes.update(checked.reason_codes)
            continue
        valid.append(attempt)

    if request.explicit_human_override:
        return _adjudicate_human_override(request, valid, invalid_codes)
    selected, selection_error = _select_attempts(request, valid)
    if selection_error is not None:
        status = _status_for_reason(selection_error)
        return _terminal(
            request,
            status,
            _method_for_request(request),
            tuple(sorted({selection_error, *invalid_codes})),
        )
    assert selected
    result = selected[-1].validated_output
    assert result is not None
    confidence = min(
        (request.calibrated_confidences.get(attempt.pub_id, -1.0) for attempt in selected),
        default=-1.0,
    )
    if confidence < 0:
        return _terminal(
            request,
            DecisionStatus.REVIEW_REQUIRED,
            _method_for_selected(selected),
            ("calibration_unavailable",),
        )
    threshold = _acceptance_threshold(request.judge_policy, result)
    if confidence < threshold:
        return _terminal(
            request,
            DecisionStatus.ABSTAINED,
            _method_for_selected(selected),
            ("low_calibrated_confidence",),
        )
    reason_codes = ["accepted"]
    if _contains_semantic_unknown(result):
        reason_codes.append("semantic_unknown")
    return DecisionAdjudication(
        method=_method_for_selected(selected),
        status=DecisionStatus.ACCEPTED,
        result=result,
        reason_codes=tuple(reason_codes),
        selected_attempt_pub_ids=tuple(attempt.pub_id for attempt in selected),
        calibrated_confidence=confidence,
        calibration_bucket=_calibration_bucket(confidence),
        evidence_refs=request.evidence_refs,
        evidence_spans=request.evidence_spans,
        rationale_summary=selected[-1].rationale_summary,
    )


def _select_attempts(
    request: AdjudicationRequest,
    valid: list[SemanticDecisionAttempt],
) -> tuple[tuple[SemanticDecisionAttempt, ...], str | None]:
    by_role = {role: [attempt for attempt in valid if attempt.role is role] for role in AttemptRole}
    required_roles = tuple(
        AttemptRole(role) for role in request.task.adjudication_policy.required_roles
    )
    missing = [role for role in required_roles if not by_role[role]]
    if missing:
        return (), _missing_attempt_reason(request.attempts)
    selected = tuple(by_role[role][-1] for role in required_roles)
    canonical_results = {canonical_hash(attempt.validated_output) for attempt in selected}
    if len(canonical_results) == 1:
        return selected, None
    action = request.task.adjudication_policy.disagreement_action
    if action == "adjudicate" and by_role[AttemptRole.ADJUDICATOR]:
        adjudicator = by_role[AttemptRole.ADJUDICATOR][-1]
        return (*selected, adjudicator), None
    return (), "judge_disagreement"


def _adjudicate_human_override(
    request: AdjudicationRequest,
    valid: list[SemanticDecisionAttempt],
    invalid_codes: set[str],
) -> DecisionAdjudication:
    if not request.task.adjudication_policy.human_override_allowed:
        return _terminal(
            request,
            DecisionStatus.REVIEW_REQUIRED,
            DecisionMethod.HUMAN,
            ("human_override_not_allowed",),
        )
    humans = [attempt for attempt in valid if attempt.role is AttemptRole.HUMAN]
    if not humans:
        return _terminal(
            request,
            DecisionStatus.REVIEW_REQUIRED,
            DecisionMethod.HUMAN,
            tuple(sorted({"human_attempt_missing", *invalid_codes})),
        )
    chosen = humans[-1]
    assert chosen.validated_output is not None
    return DecisionAdjudication(
        method=DecisionMethod.HUMAN,
        status=DecisionStatus.ACCEPTED,
        result=chosen.validated_output,
        reason_codes=("accepted", "human_override"),
        selected_attempt_pub_ids=(chosen.pub_id,),
        evidence_refs=request.evidence_refs,
        evidence_spans=request.evidence_spans,
        rationale_summary=chosen.rationale_summary,
    )


def _attempt_method_error(
    task: DecisionTaskDefinition, attempt: SemanticDecisionAttempt
) -> str | None:
    if attempt.role is AttemptRole.HUMAN:
        return (
            None if task.adjudication_policy.human_override_allowed else "human_method_not_allowed"
        )
    expected = task.decision_method_policy
    if expected is DecisionMethodPolicy.DETERMINISTIC_ONLY:
        return (
            None
            if attempt.method is DecisionMethod.DETERMINISTIC
            else "decision_method_not_allowed"
        )
    if expected is DecisionMethodPolicy.MODEL_REQUIRED:
        return None if attempt.method is DecisionMethod.MODEL else "decision_method_not_allowed"
    if expected is DecisionMethodPolicy.HUMAN_REQUIRED:
        return "decision_method_not_allowed"
    if attempt.method is DecisionMethod.DETERMINISTIC:
        try:
            validate_fast_path(task.candidate_policy, attempt.fast_path_name)
        except CandidateBoundaryError as error:
            return error.code
        return None
    return (
        None
        if attempt.method in {DecisionMethod.MODEL, DecisionMethod.HYBRID}
        else "decision_method_not_allowed"
    )


def _attempt_route_error(
    policy: JudgePolicyDefinition, attempt: SemanticDecisionAttempt
) -> str | None:
    if attempt.method not in {DecisionMethod.MODEL, DecisionMethod.HYBRID}:
        return None
    matching = [
        route
        for route in policy.model_routes
        if route.provider == attempt.provider
        and route.model == attempt.model
        and route.resolved_revision == attempt.model_revision
    ]
    return None if matching else "model_unavailable_for_policy"


def _attempt_contract_hash_error(
    task: DecisionTaskDefinition, attempt: SemanticDecisionAttempt
) -> str | None:
    if attempt.prompt_template_hash != task.prompt_template_hash:
        return "prompt_template_hash_mismatch"
    if attempt.rubric_hash != task.rubric_hash:
        return "rubric_hash_mismatch"
    if attempt.output_schema_hash != canonical_hash(task.output_schema):
        return "output_schema_hash_mismatch"
    return None


def _evidence_requirement_error(request: AdjudicationRequest) -> str | None:
    requirements = request.task.evidence_requirements
    if requirements.requires_answer_spans and (
        len(request.evidence_spans) < requirements.minimum_span_count
    ):
        return "evidence_not_closed"
    if requirements.requires_frozen_evidence_bundle:
        if (
            not request.evidence_refs
            or request.evidence_context.get("evidence_bundle_status") != "ready"
        ):
            return "evidence_retrieval_failed"
    if requirements.requires_complete_retrieval:
        if request.evidence_context.get("retrieval_protocol_complete") is not True:
            return "evidence_retrieval_failed"
    if requirements.allowed_truth_as_of_policies:
        if request.evidence_context.get("truth_as_of_policy") not in set(
            requirements.allowed_truth_as_of_policies
        ):
            return "truth_as_of_policy_invalid"
    return None


def _missing_attempt_reason(attempts: tuple[SemanticDecisionAttempt, ...]) -> str:
    reason_codes = {code for attempt in attempts for code in attempt.reason_codes}
    priority = (
        "model_unavailable_for_policy",
        "model_timeout",
        "evidence_retrieval_failed",
        "structured_output_invalid",
    )
    return next((code for code in priority if code in reason_codes), "required_judge_role_missing")


def _status_for_reason(reason: str) -> DecisionStatus:
    if reason in {"model_unavailable_for_policy", "evidence_retrieval_failed"}:
        return DecisionStatus.ABSTAINED
    if reason == "model_timeout":
        return DecisionStatus.FAILED
    return DecisionStatus.REVIEW_REQUIRED


def _acceptance_threshold(policy: JudgePolicyDefinition, result: dict[str, Any]) -> float:
    label = next(
        (
            result.get(name)
            for name in ("polarity", "verdict", "status", "substantive", "relation")
            if isinstance(result.get(name), str)
        ),
        None,
    )
    if label is not None and label in policy.acceptance_thresholds:
        return policy.acceptance_thresholds[label]
    return policy.acceptance_thresholds["default"]


def _contains_semantic_unknown(value: object) -> bool:
    if value == "unknown":
        return True
    if isinstance(value, dict):
        return any(_contains_semantic_unknown(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_semantic_unknown(item) for item in value)
    return False


def _calibration_bucket(confidence: float) -> str:
    if confidence >= 0.95:
        return "0.95-1.00"
    if confidence >= 0.90:
        return "0.90-0.95"
    if confidence >= 0.80:
        return "0.80-0.90"
    return "below-0.80"


def _method_for_selected(selected: tuple[SemanticDecisionAttempt, ...]) -> DecisionMethod:
    methods = {attempt.method for attempt in selected}
    if DecisionMethod.HUMAN in methods:
        return DecisionMethod.HUMAN
    if DecisionMethod.HYBRID in methods or len(methods) > 1:
        return DecisionMethod.HYBRID
    return next(iter(methods))


def _method_for_request(request: AdjudicationRequest) -> DecisionMethod:
    return {
        DecisionMethodPolicy.DETERMINISTIC_ONLY: DecisionMethod.DETERMINISTIC,
        DecisionMethodPolicy.MODEL_REQUIRED: DecisionMethod.MODEL,
        DecisionMethodPolicy.HYBRID: DecisionMethod.HYBRID,
        DecisionMethodPolicy.HUMAN_REQUIRED: DecisionMethod.HUMAN,
    }[request.task.decision_method_policy]


def _terminal(
    request: AdjudicationRequest,
    status: DecisionStatus,
    method: DecisionMethod,
    reason_codes: tuple[str, ...],
) -> DecisionAdjudication:
    return DecisionAdjudication(
        method=method,
        status=status,
        reason_codes=tuple(sorted(set(reason_codes))),
        evidence_refs=request.evidence_refs,
        evidence_spans=request.evidence_spans,
    )
