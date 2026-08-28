"""Immutable DecisionTask and JudgePolicy schemas.

The definitions answer two separate questions: a task says *what* is judged,
while a judge policy says *how* it may be judged.  Their content hashes are
derived from all interpretation-affecting fields and verified on load.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from domain.analysis.v2._canonical import (
    FrozenDomainModel,
    NonEmptyText,
    OpaqueRef,
    Sha256Hex,
    contains_forbidden_secret,
    hash_model_payload,
)

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_TASK_REF_RE = re.compile(
    r"^[a-z][a-z0-9-]{1,99}@"
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class DefinitionStatus(StrEnum):
    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    PUBLISHED = "published"
    RETIRED = "retired"


class SubjectType(StrEnum):
    QUERY = "query"
    ANSWER = "answer"
    ANSWER_ENTITY = "answer_entity"
    QUERY_DIMENSION = "query_dimension"
    ANSWER_DIMENSION = "answer_dimension"
    CLAIM = "claim"
    RELATION = "relation"
    CITATION = "citation"


class DecisionMethodPolicy(StrEnum):
    DETERMINISTIC_ONLY = "deterministic_only"
    MODEL_REQUIRED = "model_required"
    HYBRID = "hybrid"
    HUMAN_REQUIRED = "human_required"


class CandidateMode(StrEnum):
    NONE = "none"
    CLOSED = "closed"
    CLOSED_WITH_OPEN_SURFACE_DISCOVERY = "closed_with_open_surface_discovery"


class CandidatePolicy(FrozenDomainModel):
    mode: CandidateMode = CandidateMode.NONE
    candidate_paths: tuple[str, ...] = ()
    allow_null: bool = True
    unresolved_labels: tuple[str, ...] = ("unmanaged", "ambiguous", "unknown")
    deterministic_fast_paths: tuple[str, ...] = ()

    @field_validator("candidate_paths", "deterministic_fast_paths")
    @classmethod
    def entries_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("candidate_policy_entries_must_be_unique")
        return tuple(sorted(value))


class EvidenceRequirements(FrozenDomainModel):
    requires_answer_spans: bool = False
    minimum_span_count: int = Field(default=0, ge=0, le=100)
    requires_subject: bool = False
    requires_frozen_evidence_bundle: bool = False
    requires_complete_retrieval: bool = False
    requires_independent_verifier: bool = False
    allowed_truth_as_of_policies: tuple[str, ...] = ()

    @field_validator("allowed_truth_as_of_policies")
    @classmethod
    def truth_policies_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def span_count_is_coherent(self) -> Self:
        if self.requires_answer_spans and self.minimum_span_count < 1:
            raise ValueError("required_answer_spans_need_positive_minimum")
        if self.requires_complete_retrieval and not self.requires_frozen_evidence_bundle:
            raise ValueError("complete_retrieval_requires_evidence_bundle")
        return self


class AbstentionPolicy(FrozenDomainModel):
    allowed_reason_codes: tuple[str, ...] = (
        "candidate_out_of_set",
        "chunk_incomplete",
        "evidence_not_closed",
        "evidence_retrieval_failed",
        "low_calibrated_confidence",
        "llm_api_adapter_unavailable",
        "llm_api_auth_missing",
        "llm_api_budget_exhausted",
        "llm_api_network_error",
        "llm_api_rate_limited",
        "llm_api_timeout",
        "model_timeout",
        "model_unavailable_for_policy",
        "structured_output_invalid",
    )
    semantic_unknown_is_valid_result: bool = True
    fallback_to_heuristics: bool = False

    @model_validator(mode="after")
    def weak_fallback_is_forbidden(self) -> Self:
        if self.fallback_to_heuristics:
            raise ValueError("heuristic_fallback_forbidden")
        if len(self.allowed_reason_codes) != len(set(self.allowed_reason_codes)):
            raise ValueError("abstention_reason_codes_must_be_unique")
        object.__setattr__(self, "allowed_reason_codes", tuple(sorted(self.allowed_reason_codes)))
        return self


class AdjudicationPolicy(FrozenDomainModel):
    required_roles: tuple[str, ...] = ("proposer",)
    disagreement_action: str = "review"
    human_override_allowed: bool = True
    high_severity_requires: str | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        allowed_roles = {"proposer", "verifier", "adjudicator", "human"}
        if not self.required_roles or not set(self.required_roles) <= allowed_roles:
            raise ValueError("adjudication_required_roles_invalid")
        if len(self.required_roles) != len(set(self.required_roles)):
            raise ValueError("adjudication_required_roles_must_be_unique")
        if self.disagreement_action not in {"review", "adjudicate", "human_review"}:
            raise ValueError("adjudication_disagreement_action_invalid")
        if self.high_severity_requires not in {None, "double_judge", "human"}:
            raise ValueError("high_severity_requirement_invalid")
        return self


class CalibrationGate(FrozenDomainModel):
    artifact_required: bool = True
    minimum_support_per_label: int = Field(default=1, ge=1)
    required_metrics: dict[str, float] = Field(default_factory=dict)
    required_slices: tuple[str, ...] = ()

    @field_validator("required_slices")
    @classmethod
    def slices_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("required_metrics")
    @classmethod
    def metric_thresholds_are_probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("calibration_gate_requires_metrics")
        if any(not 0 <= threshold <= 1 for threshold in value.values()):
            raise ValueError("calibration_metric_threshold_out_of_range")
        return value


class DecisionTaskDefinition(FrozenDomainModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,99}$")
    version: str
    subject_type: SubjectType
    subject_ref_schema: dict[str, Any]
    business_question: NonEmptyText
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    dependency_task_refs: tuple[str, ...] = ()
    candidate_policy: CandidatePolicy = CandidatePolicy()
    decision_method_policy: DecisionMethodPolicy
    rubric_ref: OpaqueRef
    rubric_hash: Sha256Hex
    prompt_template_ref: OpaqueRef
    prompt_template_hash: Sha256Hex
    evidence_requirements: EvidenceRequirements = EvidenceRequirements()
    abstention_policy: AbstentionPolicy = AbstentionPolicy()
    adjudication_policy: AdjudicationPolicy = AdjudicationPolicy()
    calibration_gate: CalibrationGate
    definition_hash: str = ""
    status: DefinitionStatus = DefinitionStatus.DRAFT
    published_at: datetime | None = None
    created_at: datetime

    @property
    def task_ref(self) -> str:
        return f"{self.name}@{self.version}"

    def calculated_definition_hash(self) -> str:
        return hash_model_payload(
            self,
            excluded_fields=frozenset({"created_at", "definition_hash", "published_at", "status"}),
        )

    @field_validator("version")
    @classmethod
    def version_is_semver(cls, value: str) -> str:
        if not _SEMVER_RE.fullmatch(value):
            raise ValueError("task_version_must_be_semver")
        return value

    @field_validator("dependency_task_refs")
    @classmethod
    def dependencies_are_valid(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("dependency_task_refs_must_be_unique")
        if any(not _TASK_REF_RE.fullmatch(ref) for ref in value):
            raise ValueError("dependency_task_ref_invalid")
        return tuple(sorted(value))

    @field_validator("subject_ref_schema", "input_schema", "output_schema")
    @classmethod
    def schemas_are_closed_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("task_schema_root_must_be_object")
        if value.get("additionalProperties") is not False:
            raise ValueError("task_schema_must_forbid_additional_properties")
        return value

    @model_validator(mode="after")
    def hash_and_lifecycle_are_valid(self) -> Self:
        if self.task_ref in self.dependency_task_refs:
            raise ValueError("task_cannot_depend_on_itself")
        if contains_forbidden_secret(self.model_dump(mode="python")):
            raise ValueError("task_definition_contains_secret")
        calculated = self.calculated_definition_hash()
        if self.definition_hash and self.definition_hash != calculated:
            raise ValueError("definition_hash_mismatch")
        object.__setattr__(self, "definition_hash", calculated)
        if self.status in {DefinitionStatus.PUBLISHED, DefinitionStatus.RETIRED}:
            if self.published_at is None:
                raise ValueError("published_definition_requires_published_at")
        elif self.published_at is not None:
            raise ValueError("unpublished_definition_cannot_have_published_at")
        return self


class JudgeStageRole(StrEnum):
    DETERMINISTIC = "deterministic"
    PROPOSER = "proposer"
    VERIFIER = "verifier"
    ADJUDICATOR = "adjudicator"
    HUMAN = "human"


class JudgeStageMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HUMAN = "human"


class JudgeStage(FrozenDomainModel):
    role: JudgeStageRole
    method: JudgeStageMethod
    route_name: str | None = None
    required: bool = True

    @model_validator(mode="after")
    def method_matches_role(self) -> Self:
        if (
            self.role is JudgeStageRole.DETERMINISTIC
            and self.method is not JudgeStageMethod.DETERMINISTIC
        ):
            raise ValueError("deterministic_stage_method_invalid")
        if self.role is JudgeStageRole.HUMAN and self.method is not JudgeStageMethod.HUMAN:
            raise ValueError("human_stage_method_invalid")
        if self.method is JudgeStageMethod.MODEL and not self.route_name:
            raise ValueError("model_stage_requires_route")
        if self.method is not JudgeStageMethod.MODEL and self.route_name is not None:
            raise ValueError("non_model_stage_cannot_have_route")
        return self


class ModelRoute(FrozenDomainModel):
    route_name: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    resolved_revision: str = Field(min_length=1, max_length=200)
    processing_region: str = Field(min_length=1, max_length=100)
    allowed_data_classifications: tuple[str, ...]
    retention_policy: str = Field(min_length=1, max_length=200)


class TimeoutRetryPolicy(FrozenDomainModel):
    timeout_ms: int = Field(ge=1, le=600_000)
    max_attempts: int = Field(ge=1, le=10)
    retryable_error_codes: tuple[str, ...] = ()


class FallbackPolicy(FrozenDomainModel):
    action: str = "review"
    calibrated_route_name: str | None = None

    @model_validator(mode="after")
    def action_is_safe(self) -> Self:
        if self.action not in {"abstain", "review", "calibrated_route"}:
            raise ValueError("fallback_action_forbidden")
        if (self.action == "calibrated_route") != (self.calibrated_route_name is not None):
            raise ValueError("calibrated_fallback_route_mismatch")
        return self


class JudgePolicyDefinition(FrozenDomainModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,99}$")
    version: str
    compatible_task_refs: tuple[str, ...]
    method_pipeline: tuple[JudgeStage, ...]
    model_routes: tuple[ModelRoute, ...] = ()
    inference_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    timeout_retry_policy: TimeoutRetryPolicy
    acceptance_thresholds: dict[str, float]
    disagreement_policy: str
    evidence_budget: dict[str, int] = Field(default_factory=dict)
    cost_budget: dict[str, float] = Field(default_factory=dict)
    fallback_policy: FallbackPolicy = FallbackPolicy()
    calibration_artifact_hash: Sha256Hex | None = None
    policy_hash: str = ""
    status: DefinitionStatus = DefinitionStatus.DRAFT
    published_at: datetime | None = None
    created_at: datetime

    @property
    def policy_ref(self) -> str:
        return f"{self.name}@{self.version}"

    def calculated_policy_hash(self) -> str:
        return hash_model_payload(
            self,
            excluded_fields=frozenset({"created_at", "policy_hash", "published_at", "status"}),
        )

    @field_validator("version")
    @classmethod
    def version_is_semver(cls, value: str) -> str:
        if not _SEMVER_RE.fullmatch(value):
            raise ValueError("judge_policy_version_must_be_semver")
        return value

    @field_validator("compatible_task_refs")
    @classmethod
    def tasks_are_valid(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("compatible_task_refs_must_be_nonempty_unique")
        if any(not _TASK_REF_RE.fullmatch(ref) for ref in value):
            raise ValueError("compatible_task_ref_invalid")
        return tuple(sorted(value))

    @field_validator("acceptance_thresholds")
    @classmethod
    def thresholds_are_probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        if "default" not in value:
            raise ValueError("acceptance_threshold_default_required")
        if any(not 0 <= threshold <= 1 for threshold in value.values()):
            raise ValueError("acceptance_threshold_out_of_range")
        return value

    @model_validator(mode="after")
    def hash_routes_and_lifecycle_are_valid(self) -> Self:
        route_names = [route.route_name for route in self.model_routes]
        if len(route_names) != len(set(route_names)):
            raise ValueError("model_route_names_must_be_unique")
        known_routes = set(route_names)
        used_routes = {
            stage.route_name for stage in self.method_pipeline if stage.route_name is not None
        }
        if not used_routes <= known_routes:
            raise ValueError("method_pipeline_references_unknown_route")
        if self.fallback_policy.calibrated_route_name not in known_routes | {None}:
            raise ValueError("fallback_references_unknown_route")
        if self.disagreement_policy not in {"review", "adjudicate", "human_review"}:
            raise ValueError("judge_disagreement_policy_invalid")
        if contains_forbidden_secret(
            {
                "model_routes": self.model_routes,
                "inference_configs": self.inference_configs,
                "cost_budget": self.cost_budget,
            }
        ):
            raise ValueError("judge_policy_contains_secret")
        calculated = self.calculated_policy_hash()
        if self.policy_hash and self.policy_hash != calculated:
            raise ValueError("policy_hash_mismatch")
        object.__setattr__(self, "policy_hash", calculated)
        if self.status in {DefinitionStatus.PUBLISHED, DefinitionStatus.RETIRED}:
            if self.published_at is None:
                raise ValueError("published_policy_requires_published_at")
        elif self.published_at is not None:
            raise ValueError("unpublished_policy_cannot_have_published_at")
        return self


def validate_policy_compatibility(
    task: DecisionTaskDefinition, policy: JudgePolicyDefinition
) -> None:
    """Fail closed when a policy pipeline cannot satisfy a task method contract."""

    if task.task_ref not in policy.compatible_task_refs:
        raise ValueError("judge_policy_incompatible_task")
    methods = {stage.method for stage in policy.method_pipeline if stage.required}
    roles = {stage.role for stage in policy.method_pipeline if stage.required}
    expected = task.decision_method_policy
    if expected is DecisionMethodPolicy.DETERMINISTIC_ONLY:
        if methods != {JudgeStageMethod.DETERMINISTIC}:
            raise ValueError("deterministic_task_policy_pipeline_invalid")
    elif expected is DecisionMethodPolicy.MODEL_REQUIRED:
        if JudgeStageMethod.MODEL not in methods:
            raise ValueError("model_required_task_has_no_model_stage")
    elif expected is DecisionMethodPolicy.HYBRID:
        if not {JudgeStageMethod.DETERMINISTIC, JudgeStageMethod.MODEL} <= methods:
            raise ValueError("hybrid_task_policy_pipeline_invalid")
    elif expected is DecisionMethodPolicy.HUMAN_REQUIRED:
        if JudgeStageRole.HUMAN not in roles:
            raise ValueError("human_required_task_has_no_human_stage")
