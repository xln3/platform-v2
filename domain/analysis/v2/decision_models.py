"""Atomic semantic-decision attempts, evidence, and final records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from domain.analysis.v2._canonical import (
    FrozenDomainModel,
    OpaqueRef,
    Sha256Hex,
    canonical_hash,
    contains_forbidden_secret,
    hash_model_payload,
)
from domain.analysis.v2.decision_task_schema import SubjectType


class DecisionMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HYBRID = "hybrid"
    HUMAN = "human"


class DecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    ABSTAINED = "abstained"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class AttemptRole(StrEnum):
    PROPOSER = "proposer"
    VERIFIER = "verifier"
    ADJUDICATOR = "adjudicator"
    HUMAN = "human"


class AttemptValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    ERROR = "error"


class EvidenceSpan(FrozenDomainModel):
    """A Unicode-code-point, half-open span over a versioned source text."""

    source_ref: OpaqueRef
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt_hash: Sha256Hex
    source_text_hash: Sha256Hex
    offset_unit: str = Field(default="unicode_code_point_v1", pattern=r"^unicode_code_point_v1$")
    role: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def interval_is_nonempty(self) -> Self:
        if self.end <= self.start:
            raise ValueError("evidence_span_must_be_nonempty")
        return self


class SemanticDecisionAttempt(FrozenDomainModel):
    """One bounded proposal, verification, adjudication, or human attempt.

    ``validated_output`` contains only the structured business output.  A short
    evidence-backed rationale is permitted; private reasoning and raw provider
    responses have no field in this contract.
    """

    pub_id: str = Field(pattern=r"^sda_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    tenant_pub_id: OpaqueRef
    project_pub_id: OpaqueRef
    decision_job_pub_id: str = Field(pattern=r"^sdj_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    attempt_index: int = Field(ge=0)
    role: AttemptRole
    method: DecisionMethod
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    model_revision: str | None = Field(default=None, min_length=1, max_length=200)
    inference_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template_ref: OpaqueRef
    prompt_template_hash: Sha256Hex
    rubric_hash: Sha256Hex
    output_schema_hash: Sha256Hex
    request_payload_hash: Sha256Hex
    response_payload_hash: Sha256Hex | None = None
    validated_output: dict[str, Any] | None = None
    rationale_summary: str | None = Field(default=None, max_length=1_000)
    validation_status: AttemptValidationStatus
    reason_codes: tuple[str, ...] = ()
    fast_path_name: str | None = Field(default=None, min_length=1, max_length=100)
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_amount: float | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    created_at: datetime

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not code or len(code) > 100 for code in value):
            raise ValueError("attempt_reason_code_invalid")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def method_output_and_metadata_are_coherent(self) -> Self:
        model_metadata = (self.provider, self.model, self.model_revision)
        if self.method in {DecisionMethod.MODEL, DecisionMethod.HYBRID}:
            if any(item is None for item in model_metadata):
                raise ValueError("model_attempt_requires_resolved_model_metadata")
        elif any(item is not None for item in model_metadata):
            raise ValueError("non_model_attempt_cannot_have_model_metadata")
        if self.method is DecisionMethod.HUMAN and self.role is not AttemptRole.HUMAN:
            raise ValueError("human_method_requires_human_role")
        if self.role is AttemptRole.HUMAN and self.method is not DecisionMethod.HUMAN:
            raise ValueError("human_role_requires_human_method")
        if self.validation_status is AttemptValidationStatus.VALID:
            if self.validated_output is None or self.response_payload_hash is None:
                raise ValueError("valid_attempt_requires_validated_output_and_response_hash")
        elif self.validated_output is not None:
            raise ValueError("invalid_attempt_cannot_expose_validated_output")
        if (self.cost_amount is None) != (self.cost_currency is None):
            raise ValueError("attempt_cost_amount_currency_mismatch")
        if contains_forbidden_secret(self.inference_config):
            raise ValueError("attempt_inference_config_contains_secret")
        if contains_forbidden_secret(self.validated_output or {}):
            raise ValueError("attempt_output_contains_secret")
        _reject_private_reasoning(self.validated_output or {})
        return self


class SemanticDecisionRecord(FrozenDomainModel):
    """An immutable, context-complete final decision consumed by events."""

    decision_pub_id: str = Field(pattern=r"^sdr_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    tenant_pub_id: OpaqueRef
    project_pub_id: OpaqueRef
    decision_job_pub_id: str = Field(pattern=r"^sdj_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    task_name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,99}$")
    task_version: str = Field(min_length=5, max_length=100)
    task_definition_hash: Sha256Hex
    subject_type: SubjectType
    subject_key: Sha256Hex
    subject_ref: dict[str, Any]
    metric_name: str | None = Field(default=None, min_length=1, max_length=200)
    metric_version: str | None = Field(default=None, min_length=1, max_length=100)
    input_snapshot_ref: OpaqueRef
    input_hash: Sha256Hex
    context_hash: Sha256Hex
    method: DecisionMethod
    status: DecisionStatus
    result: dict[str, Any]
    rationale_summary: str | None = Field(default=None, max_length=1_000)
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    calibration_bucket: str | None = Field(default=None, min_length=1, max_length=100)
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[OpaqueRef, ...] = ()
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    selected_attempt_pub_ids: tuple[str, ...] = ()
    judge_policy_hash: Sha256Hex
    rubric_ref: OpaqueRef
    rubric_hash: Sha256Hex
    output_schema_hash: Sha256Hex
    supersedes_pub_id: str | None = Field(
        default=None, pattern=r"^sdr_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    decision_hash: str = ""
    created_at: datetime

    @property
    def task_ref(self) -> str:
        return f"{self.task_name}@{self.task_version}"

    def calculated_decision_hash(self) -> str:
        return hash_model_payload(
            self,
            excluded_fields=frozenset({"decision_pub_id", "decision_hash", "created_at"}),
        )

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not code or len(code) > 100 for code in value):
            raise ValueError("decision_reason_code_invalid")
        return tuple(sorted(set(value)))

    @field_validator("evidence_refs", "selected_attempt_pub_ids")
    @classmethod
    def refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("evidence_spans")
    @classmethod
    def spans_are_canonical(cls, value: tuple[EvidenceSpan, ...]) -> tuple[EvidenceSpan, ...]:
        def identity(span: EvidenceSpan) -> tuple[str, int, int, str, str]:
            return (
                span.source_ref,
                span.start,
                span.end,
                span.role,
                span.excerpt_hash,
            )

        ordered = tuple(sorted(value, key=identity))
        if len({identity(span) for span in ordered}) != len(ordered):
            raise ValueError("decision_evidence_spans_must_be_unique")
        return ordered

    @model_validator(mode="after")
    def decision_is_context_complete(self) -> Self:
        if self.subject_key != subject_key_for(self.subject_ref):
            raise ValueError("decision_subject_key_mismatch")
        if (self.metric_name is None) != (self.metric_version is None):
            raise ValueError("metric_name_version_must_be_paired")
        if self.supersedes_pub_id == self.decision_pub_id:
            raise ValueError("decision_cannot_supersede_itself")
        if self.status is DecisionStatus.ACCEPTED:
            if not self.selected_attempt_pub_ids:
                raise ValueError("accepted_decision_requires_selected_attempt")
            if not self.result:
                raise ValueError("accepted_decision_requires_result")
            if self.method is not DecisionMethod.HUMAN and self.calibrated_confidence is None:
                raise ValueError("automatic_acceptance_requires_calibrated_confidence")
        elif self.selected_attempt_pub_ids:
            raise ValueError("nonaccepted_decision_cannot_select_attempts")
        if (self.calibrated_confidence is None) != (self.calibration_bucket is None):
            raise ValueError("calibration_confidence_bucket_mismatch")
        if contains_forbidden_secret(self.result):
            raise ValueError("decision_result_contains_secret")
        _reject_private_reasoning(self.result)
        calculated = self.calculated_decision_hash()
        if self.decision_hash and self.decision_hash != calculated:
            raise ValueError("decision_hash_mismatch")
        object.__setattr__(self, "decision_hash", calculated)
        return self


def subject_key_for(subject_ref: dict[str, Any]) -> str:
    """Hash the complete composite subject reference, independent of key order."""

    if not subject_ref:
        raise ValueError("subject_ref_must_be_nonempty")
    return canonical_hash(subject_ref)


def _reject_private_reasoning(value: object) -> None:
    forbidden_keys = {
        "chain_of_thought",
        "chainOfThought",
        "hidden_reasoning",
        "private_reasoning",
        "reasoning_tokens",
    }
    if isinstance(value, dict):
        if forbidden_keys & value.keys():
            raise ValueError("private_reasoning_must_not_be_persisted")
        for item in value.values():
            _reject_private_reasoning(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_private_reasoning(item)
