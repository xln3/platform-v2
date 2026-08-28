"""Temporal activity boundaries for V2 semantic decisions.

The workflow history only carries immutable references, hashes, structured
outputs, and short machine reason codes.  Source query/answer text remains in
the tenant database and is loaded inside an activity when an adapter needs it.
No weak semantic fallback is implemented here: an unavailable or invalid judge
produces an explicit non-accepted result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from geo_platform.config import get_settings
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.analysis.v2 import (
    AdjudicationRequest,
    SemanticDecisionAttempt,
    SemanticDecisionRecord,
    adjudicate_decision,
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.analysis.v2._canonical import canonical_hash
from domain.analysis.v2.candidates import Candidate, CandidateSet
from domain.analysis.v2.decision_models import (
    AttemptRole,
    AttemptValidationStatus,
    DecisionMethod,
    DecisionStatus,
    subject_key_for,
)
from domain.analysis.v2.event_derivation import (
    EventDerivationContext,
    capability_analyses_from_decisions,
    derive_answer_semantic_events,
)
from domain.analysis.v2.output_validation import (
    validate_decision_output,
    validate_subject_ref,
)
from domain.metrics.v2.query_context import (
    AnalysisLens,
    BrandStructureType,
    ExposureRole,
    RequestedOperation,
    derive_brand_structure,
    derive_exposure_role,
)
from workflows.activities.semantic_judge_llm import (
    SemanticJudgeFailure,
    execute_semantic_judge,
    load_frozen_semantic_context,
    load_frozen_semantic_source,
)
from workflows.activities.semantic_judge_llm import (
    config_from_settings as semantic_judge_config_from_settings,
)


def _task(task_ref: str):  # type: ignore[no-untyped-def]
    return load_builtin_task_definitions().get(task_ref)


def _policy(*, policy_hash: str | None = None, policy_ref: str | None = None):  # type: ignore[no-untyped-def]
    tasks = load_builtin_task_definitions()
    policies = load_builtin_judge_policies(tasks=tasks)
    matches = [
        policy
        for policy in policies
        if (policy_hash is None or policy.policy_hash == policy_hash)
        and (policy_ref is None or policy.policy_ref == policy_ref)
    ]
    if len(matches) != 1:
        raise ApplicationError(
            "judge policy could not be resolved",
            type="judge_policy_missing",
            non_retryable=True,
        )
    return matches[0]


def _sha256(value: object) -> str:
    return canonical_hash(value)


@activity.defn(name="create_semantic_decision_request_v2")
async def create_decision_request_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Idempotently create a decision job and its requested outbox event."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    return repository.create_decision_request(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        task_ref=str(payload["task_ref"]),
        subject_ref=dict(payload["subject_ref"]),
        input_snapshot_ref=str(payload["input_snapshot_ref"]),
        input_hash=str(payload["input_hash"]),
        context_hash=str(payload["context_hash"]),
        judge_policy_hash=str(payload["judge_policy_hash"]),
        idempotency_key=str(payload["idempotency_key"]),
        rejudge_generation=int(payload.get("rejudge_generation") or 0),
        supersedes_decision_pub_id=payload.get("supersedes_decision_pub_id"),
        workflow_id=str(payload.get("workflow_id") or ""),
        run_id=str(payload.get("run_id") or ""),
    )


@activity.defn(name="build_candidates_v2")
async def build_candidates_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Freeze a closed candidate set prepared by the analysis boundary."""

    source_ref = str(payload.get("source_ref") or "")
    source_hash = str(payload.get("source_hash") or "")
    raw_candidates = payload.get("candidates")
    if not source_ref or not isinstance(raw_candidates, list):
        raise ApplicationError(
            "candidate input is incomplete",
            type="candidate_input_invalid",
            non_retryable=True,
        )
    candidate_set = CandidateSet(
        candidates=tuple(Candidate.model_validate(item) for item in raw_candidates),
        source_ref=source_ref,
        source_hash=source_hash,
    )
    return candidate_set.model_dump(mode="json")


@activity.defn(name="freeze_decision_input_v2")
async def freeze_decision_input_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the immutable reference-only envelope passed to Decision."""

    task = _task(str(payload.get("task_ref") or ""))
    subject_ref = payload.get("subject_ref")
    if not isinstance(subject_ref, dict):
        raise ApplicationError(
            "subject reference is invalid",
            type="decision_subject_ref_invalid",
            non_retryable=True,
        )
    validation = validate_subject_ref(task=task, subject_ref=subject_ref)
    if not validation.is_valid:
        raise ApplicationError(
            "subject reference did not satisfy the task schema",
            type="decision_subject_ref_invalid",
            non_retryable=True,
        )
    input_snapshot_ref = str(payload.get("input_snapshot_ref") or "")
    input_hash = str(payload.get("input_hash") or "")
    context_hash = str(payload.get("context_hash") or "")
    if not input_snapshot_ref or len(input_hash) != 64 or len(context_hash) != 64:
        raise ApplicationError(
            "decision input hash envelope is invalid",
            type="decision_input_hash_invalid",
            non_retryable=True,
        )
    material_hashes = payload.get("input_material_hashes")
    safe_material_hashes: dict[str, str] = {}
    if isinstance(material_hashes, dict):
        safe_material_hashes = {str(key): str(value) for key, value in material_hashes.items()}
        if any(len(value) != 64 for value in safe_material_hashes.values()):
            raise ApplicationError(
                "decision source hash envelope is invalid",
                type="decision_input_hash_invalid",
                non_retryable=True,
            )
    # The returned object is deliberately reference-only.  Raw input is loaded
    # by run_model_judge_v2 using input_snapshot_ref inside its process.
    return {
        "task_ref": task.task_ref,
        "task_definition_hash": task.definition_hash,
        "subject_type": task.subject_type.value,
        "subject_key": subject_key_for(subject_ref),
        "subject_ref": subject_ref,
        "input_snapshot_ref": input_snapshot_ref,
        "input_hash": input_hash,
        "input_material_hashes": safe_material_hashes,
        "context_hash": context_hash,
        "source_answer_pub_id": payload.get("source_answer_pub_id"),
        "source_query_pub_id": payload.get("source_query_pub_id"),
        "candidate_set_hash": payload.get("candidate_set_hash"),
        "evidence_bundle_ref": payload.get("evidence_bundle_ref"),
        "evidence_bundle_hash": payload.get("evidence_bundle_hash"),
    }


@activity.defn(name="retrieve_decision_evidence_v2")
async def retrieve_evidence_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a frozen evidence bundle reference without copying source text."""

    status = str(payload.get("status") or "failed")
    bundle_ref = str(payload.get("bundle_ref") or "")
    bundle_hash = str(payload.get("bundle_hash") or "")
    source_items = payload.get("source_items", [])
    if status not in {"ready", "partial", "failed"}:
        raise ApplicationError(
            "evidence bundle state is invalid",
            type="evidence_bundle_invalid",
            non_retryable=True,
        )
    if not bundle_ref or len(bundle_hash) != 64 or not isinstance(source_items, list):
        raise ApplicationError(
            "evidence bundle reference is invalid",
            type="evidence_bundle_invalid",
            non_retryable=True,
        )
    # Only controlled references and hashes may cross the workflow boundary.
    safe_items = []
    for item in source_items:
        if not isinstance(item, dict):
            raise ApplicationError(
                "evidence item is invalid",
                type="evidence_bundle_invalid",
                non_retryable=True,
            )
        safe_items.append(
            {
                key: item[key]
                for key in (
                    "source_ref",
                    "content_hash",
                    "cas_ref",
                    "fetch_status",
                    "paragraph_start",
                    "paragraph_end",
                )
                if key in item
            }
        )
    return {
        "bundle_ref": bundle_ref,
        "bundle_hash": bundle_hash,
        "status": status,
        "failure_codes": sorted(set(map(str, payload.get("failure_codes", [])))),
        "source_items": safe_items,
    }


@activity.defn(name="run_model_judge_v2")
def run_model_judge_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the blocking DB/HTTP judge work outside the worker event loop."""

    settings = get_settings()
    fixture_attempt = payload.get("fixture_attempt")
    if isinstance(fixture_attempt, dict) and settings.env != "production":
        attempt = SemanticDecisionAttempt.model_validate(fixture_attempt)
        return attempt.model_dump(mode="json")
    task = _task(str(payload.get("task_ref") or ""))
    policy = _policy(
        policy_hash=(
            str(payload["judge_policy_hash"]) if payload.get("judge_policy_hash") else None
        ),
        policy_ref=(str(payload["judge_policy_ref"]) if payload.get("judge_policy_ref") else None),
    )
    stage = next(
        (item for item in policy.method_pipeline if item.method.value == "model"),
        None,
    )
    route = next(
        (item for item in policy.model_routes if stage and item.route_name == stage.route_name),
        None,
    )
    if stage is None or route is None:
        raise ApplicationError(
            "judge policy has no model route",
            type="judge_policy_invalid",
            non_retryable=True,
        )
    attempt_seed = canonical_hash(
        {
            "decision_job_pub_id": payload["decision_job_pub_id"],
            "attempt_index": int(payload.get("attempt_index") or 0),
            "role": stage.role.value,
        }
    )
    config = semantic_judge_config_from_settings(settings)
    common_attempt: dict[str, Any] = {
        "pub_id": f"sda_{attempt_seed[:26]}",
        "tenant_pub_id": str(payload["tenant_pub_id"]),
        "project_pub_id": str(payload["project_pub_id"]),
        "decision_job_pub_id": str(payload["decision_job_pub_id"]),
        "attempt_index": int(payload.get("attempt_index") or 0),
        "role": AttemptRole(stage.role.value),
        "method": DecisionMethod.MODEL,
        "provider": config.provider,
        "model": config.model,
        "model_revision": config.model_revision,
        "inference_config": {
            "route_name": route.route_name,
            "response_format": "json_schema",
            "single_model": True,
            "timeout_ms": int(config.timeout_seconds * 1000),
            "max_attempts": config.max_retries + 1,
        },
        "prompt_template_ref": task.prompt_template_ref,
        "prompt_template_hash": task.prompt_template_hash,
        "rubric_hash": task.rubric_hash,
        "output_schema_hash": canonical_hash(task.output_schema),
        "created_at": datetime.now(UTC),
    }
    failure_hash = canonical_hash(
        {
            "context_hash": payload.get("context_hash"),
            "decision_job_pub_id": payload.get("decision_job_pub_id"),
            "input_hash": payload.get("input_hash"),
            "model": config.model,
            "subject_ref": payload.get("subject_ref"),
            "task_ref": task.task_ref,
        }
    )
    failure_code: str | None = None
    result = None
    if settings.semantic_decision_daily_budget <= 0 or payload.get("llm_budget_exhausted"):
        failure_code = "llm_api_budget_exhausted"
    elif not config.api_key:
        failure_code = "llm_api_auth_missing"
    else:
        candidate_payload = payload.get("candidate_set")
        candidate_set = (
            CandidateSet.model_validate(candidate_payload)
            if isinstance(candidate_payload, dict)
            else None
        )
        try:
            source = load_frozen_semantic_source(
                dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
                payload=payload,
                task=task,
            )
            frozen_context = load_frozen_semantic_context(
                dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
                settings=settings,
                payload=payload,
                task=task,
                source=source,
            )
            validated_evidence_context = (
                frozen_context.evidence_context
                if frozen_context.evidence_context
                else (
                    dict(payload["evidence_context"])
                    if isinstance(payload.get("evidence_context"), dict)
                    else None
                )
            )
            result = execute_semantic_judge(
                config=config,
                task=task,
                source=source,
                subject_ref=dict(payload["subject_ref"]),
                candidate_set=candidate_set,
                evidence_context=validated_evidence_context,
                frozen_context=frozen_context,
            )
        except SemanticJudgeFailure as failure:
            failure_code = failure.code
    if result is None:
        attempt = SemanticDecisionAttempt(
            **common_attempt,
            request_payload_hash=failure_hash,
            validation_status=AttemptValidationStatus.ERROR,
            reason_codes=(failure_code or "llm_api_adapter_unavailable",),
        )
    else:
        common_attempt["inference_config"] = dict(common_attempt["inference_config"]) | {
            "transport_mode": result.transport_mode
        }
        attempt = SemanticDecisionAttempt(
            **common_attempt,
            request_payload_hash=result.request_payload_hash,
            response_payload_hash=result.response_payload_hash,
            validated_output=result.output,
            rationale_summary="单一 LLM 的结构化判定已通过任务、候选集和证据跨度校验。",
            validation_status=AttemptValidationStatus.VALID,
            reason_codes=("llm_api_success",),
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    return attempt.model_dump(mode="json")


@activity.defn(name="validate_decision_output_v2")
async def validate_decision_output_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply task schema, candidate, span, and task-specific validation."""

    task = _task(str(payload.get("task_ref") or ""))
    candidate_payload = payload.get("candidate_set")
    candidate_set = (
        CandidateSet.model_validate(candidate_payload)
        if isinstance(candidate_payload, dict)
        else None
    )
    result = validate_decision_output(
        task=task,
        output=payload.get("output"),
        candidate_set=candidate_set,
        answer_text=payload.get("answer_text"),
        expected_answer_text_hash=payload.get("answer_text_hash"),
        evidence_context=(
            payload["evidence_context"]
            if isinstance(payload.get("evidence_context"), dict)
            else None
        ),
    )
    return {
        "is_valid": result.is_valid,
        "output": result.output,
        "reason_codes": list(result.reason_codes),
        "issues": [
            {"code": issue.code, "path": issue.path, "detail": issue.detail}
            for issue in result.issues
        ],
    }


@activity.defn(name="adjudicate_decision_v2")
async def adjudicate_decision_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve attempts to one immutable accepted/unknown decision record."""

    task = _task(str(payload.get("task_ref") or ""))
    policy = _policy(
        policy_hash=(
            str(payload["judge_policy_hash"]) if payload.get("judge_policy_hash") else None
        ),
        policy_ref=(str(payload["judge_policy_ref"]) if payload.get("judge_policy_ref") else None),
    )
    candidate_payload = payload.get("candidate_set")
    candidate_set = (
        CandidateSet.model_validate(candidate_payload)
        if isinstance(candidate_payload, dict)
        else None
    )
    attempts = tuple(
        SemanticDecisionAttempt.model_validate(item) for item in payload.get("attempts", [])
    )
    source_text = payload.get("answer_text")
    source_text_hash = payload.get("answer_text_hash")
    answer_source_ref = payload.get("input_snapshot_ref")
    if any(attempt.validation_status is AttemptValidationStatus.VALID for attempt in attempts):
        settings = get_settings()
        source = load_frozen_semantic_source(
            dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
            payload=payload,
            task=task,
        )
        source_text = source.source_text
        source_text_hash = source.source_text_hash
        answer_source_ref = source.source_ref
    request = AdjudicationRequest(
        task=task,
        judge_policy=policy,
        attempts=attempts,
        calibrated_confidences={
            str(key): float(value)
            for key, value in dict(payload.get("calibrated_confidences") or {}).items()
        },
        candidate_set=candidate_set,
        answer_text=source_text,
        expected_answer_text_hash=source_text_hash,
        evidence_context=dict(payload.get("evidence_context") or {}),
        evidence_refs=tuple(map(str, payload.get("evidence_refs", []))),
        evidence_spans=tuple(payload.get("evidence_spans", [])),
        answer_source_ref=(str(answer_source_ref) if answer_source_ref else None),
        dependency_statuses=dict(payload.get("dependency_statuses") or {}),
        required_chunks_complete=bool(payload.get("required_chunks_complete", True)),
        explicit_human_override=bool(payload.get("explicit_human_override", False)),
        official_use=bool(payload.get("official_use", False)),
    )
    outcome = adjudicate_decision(request)
    subject_ref = dict(payload["subject_ref"])
    record = outcome.to_record(
        decision_pub_id=str(payload["decision_pub_id"]),
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        decision_job_pub_id=str(payload["decision_job_pub_id"]),
        task=task,
        subject_type=task.subject_type,
        subject_key=subject_key_for(subject_ref),
        subject_ref=subject_ref,
        input_snapshot_ref=str(payload["input_snapshot_ref"]),
        input_hash=str(payload["input_hash"]),
        context_hash=str(payload["context_hash"]),
        judge_policy_hash=policy.policy_hash,
        created_at=datetime.fromisoformat(
            str(payload.get("created_at") or datetime.now(UTC).isoformat()).replace("Z", "+00:00")
        ),
        metric_name=payload.get("metric_name"),
        metric_version=payload.get("metric_version"),
        supersedes_pub_id=payload.get("supersedes_pub_id"),
    )
    return record.model_dump(mode="json")


@activity.defn(name="persist_semantic_decision_v2")
async def persist_decision_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist attempts, final decision, job terminal state, and outbox."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    record = SemanticDecisionRecord.model_validate(payload["decision"])
    attempts = tuple(
        SemanticDecisionAttempt.model_validate(item) for item in payload.get("attempts", [])
    )
    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "persist_decision_atomic"):
        raise ApplicationError(
            "semantic decision persistence boundary is unavailable",
            type="decision_repository_contract_missing",
            non_retryable=True,
        )
    return repository.persist_decision_atomic(
        tenant_pub_id=record.tenant_pub_id,
        project_pub_id=record.project_pub_id,
        decision_job_pub_id=record.decision_job_pub_id,
        attempts=tuple(item.model_dump(mode="python") for item in attempts),
        decision=record.model_dump(mode="python"),
        workflow_id=str(payload.get("workflow_id") or ""),
        run_id=str(payload.get("run_id") or ""),
    )


@activity.defn(name="derive_answer_semantic_events_v2")
async def derive_events_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive typed events solely from accepted immutable decisions."""

    decisions = tuple(
        SemanticDecisionRecord.model_validate(item) for item in payload.get("decisions", [])
    )
    created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
    context = EventDerivationContext(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        answer_pub_id=str(payload["answer_pub_id"]),
        semantic_manifest_pub_id=str(payload["semantic_manifest_pub_id"]),
        extractor_version=str(payload["extractor_version"]),
        scorer_version=str(payload["scorer_version"]),
        policy_versions_by_hash={
            str(key): str(value)
            for key, value in dict(payload.get("policy_versions_by_hash") or {}).items()
        },
        created_at=created_at,
    )
    events = derive_answer_semantic_events(decisions, context=context)
    capabilities = capability_analyses_from_decisions(decisions)
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "capability_statuses": {
            name: value.model_dump(mode="json") for name, value in sorted(capabilities.items())
        },
        "decision_record_pub_ids": sorted(decision.decision_pub_id for decision in decisions),
        "decision_set_hash": _sha256(
            sorted((decision.decision_pub_id, decision.decision_hash) for decision in decisions)
        ),
        "event_set_hash": _sha256(
            sorted((event.pub_id, event.event_fingerprint) for event in events)
        ),
    }


@activity.defn(name="persist_query_context_v2")
async def persist_query_context_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a context fact, all focal exposures, and one outbox event."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "persist_query_context_atomic"):
        raise ApplicationError(
            "query context persistence boundary is unavailable",
            type="query_context_repository_contract_missing",
            non_retryable=True,
        )
    return repository.persist_query_context_atomic(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        fact=dict(payload["fact"]),
        exposures=tuple(dict(item) for item in payload.get("exposures", [])),
    )


@activity.defn(name="derive_query_context_v2")
async def derive_query_context_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive a multi-lens context and focal-relative exposure facts.

    Navigation ``primary_lens`` is projected only after the complete lens set
    has been frozen; metric eligibility must use ``analysis_lenses``.
    """

    decisions = tuple(
        SemanticDecisionRecord.model_validate(item) for item in payload.get("decisions", [])
    )
    accepted = {item.task_name: item for item in decisions if item.status.value == "accepted"}
    intent = accepted.get("query-intent")
    entities = accepted.get("query-brand-entity-resolution")
    query_decisions = tuple(
        item
        for item in decisions
        if item.task_name in {"query-intent", "query-brand-entity-resolution"}
    )
    if any(item.status is DecisionStatus.FAILED for item in query_decisions):
        classification_state = "failed"
        lenses: set[str] = set()
        operations: set[str] = set()
        subtypes: set[str] = set()
        detected: set[str] = set()
        unresolved = True
    elif intent is None or entities is None:
        classification_state = "review_required"
        lenses = set()
        operations = set()
        subtypes = set()
        detected = set()
        unresolved = True
    else:
        classification_state = "ready"
        raw_lenses = set(map(str, intent.result.get("analysis_lenses", [])))
        lenses = set()
        if raw_lenses & {"selection"}:
            lenses.add(AnalysisLens.AI_RECOMMENDATION.value)
        if raw_lenses & {"reputation", "comparison", "factual"}:
            lenses.add(AnalysisLens.AI_IMPRESSION.value)
        if "comparison" in raw_lenses:
            lenses.add(AnalysisLens.AI_RECOMMENDATION.value)
        raw_operations = set(map(str, intent.result.get("requested_operations", [])))
        operation_map = {
            "recommend": RequestedOperation.RECOMMEND.value,
            "rank": RequestedOperation.RANK.value,
            "compare": RequestedOperation.COMPARE.value,
            "describe": RequestedOperation.DESCRIBE.value,
            "verify": RequestedOperation.FACT_LOOKUP.value,
        }
        operations = {operation_map[item] for item in raw_operations if item in operation_map}
        subtypes = set(map(str, intent.result.get("query_subtypes", [])))
        resolutions = entities.result.get("resolutions", [])
        detected = {
            str(item["entity_id"])
            for item in resolutions
            if isinstance(item, dict)
            and item.get("resolution_state") == "resolved"
            and item.get("entity_id")
        }
        unresolved = any(
            isinstance(item, dict) and item.get("resolution_state") != "resolved"
            for item in resolutions
        )
        if not lenses or not operations:
            classification_state = "review_required"
    structure = (
        BrandStructureType.UNKNOWN
        if unresolved
        else derive_brand_structure(tuple(sorted(detected)))
    )
    fact_material = {
        "tenant_pub_id": str(payload["tenant_pub_id"]),
        "project_pub_id": str(payload["project_pub_id"]),
        "query_key": str(payload["query_key"]),
        "query_pub_id": payload.get("query_pub_id"),
        "query_text_hash": str(payload["query_text_hash"]),
        "primary_lens": payload.get("primary_lens"),
        "analysis_lenses": sorted(lenses),
        "requested_operations": sorted(operations),
        "query_subtypes": sorted(subtypes),
        "detected_entity_ids": sorted(detected),
        "brand_structure_type": structure.value,
        "classification_state": classification_state,
        "classifier_version": str(payload["classifier_version"]),
        "decision_task_bundle_hash": str(payload["decision_task_bundle_hash"]),
        "entity_dictionary_hash": str(payload["entity_dictionary_hash"]),
        "classification_source": str(payload.get("classification_source") or "live"),
        "derivation_method": str(payload.get("derivation_method") or "hybrid"),
        "decision_record_pub_ids": sorted(item.decision_pub_id for item in decisions),
        "review_status": str(payload.get("review_status") or "unreviewed"),
        "override_reason": payload.get("override_reason"),
        "supersedes_pub_id": payload.get("supersedes_pub_id"),
    }
    fact_hash = canonical_hash(fact_material)
    fact_pub_id = f"qcf_{fact_hash[:26]}"
    fact_created_at = str(payload.get("created_at") or datetime.now(UTC).isoformat())
    exposures: list[dict[str, Any]] = []
    for focal_entity_id in sorted(set(map(str, payload.get("focal_entity_ids", [])))):
        role = (
            ExposureRole.UNKNOWN
            if classification_state != "ready"
            else derive_exposure_role(
                tuple(sorted(detected)),
                focal_entity_id,
                has_unresolved_brand_surface=unresolved,
            )
        )
        material = {
            "query_context_fact_pub_id": fact_pub_id,
            "query_key": fact_material["query_key"],
            "focal_entity_id": focal_entity_id,
            "exposure_role": role.value,
            "matched_entity_ids": sorted(detected),
        }
        exposure_hash = canonical_hash(material)
        exposures.append(
            {
                "pub_id": f"qef_{exposure_hash[:26]}",
                **material,
                "fact_hash": exposure_hash,
            }
        )
    return {
        "fact": {
            "pub_id": fact_pub_id,
            **fact_material,
            "fact_hash": fact_hash,
            "created_at": fact_created_at,
        },
        "exposures": exposures,
    }


@activity.defn(name="persist_answer_semantic_events_v2")
async def persist_events_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one immutable manifest and its full non-exclusive event set."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "persist_semantic_manifest_atomic"):
        raise ApplicationError(
            "semantic event persistence boundary is unavailable",
            type="semantic_event_repository_contract_missing",
            non_retryable=True,
        )
    return repository.persist_semantic_manifest_atomic(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        manifest=dict(payload["manifest"]),
        events=tuple(dict(item) for item in payload.get("events", [])),
    )


@activity.defn(name="load_semantic_decision_backfill_batch_v2")
async def load_semantic_decision_backfill_batch_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Load a stable keyset page as reference-only work items.

    The SQL adapter is intentionally delegated to the repository.  This keeps
    raw answer text out of Temporal history and lets dry-run capacity estimates
    use exactly the same cursor and population as execution.
    """

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "load_decision_backfill_batch"):
        raise ApplicationError(
            "semantic backfill repository boundary is unavailable",
            type="decision_backfill_repository_contract_missing",
            non_retryable=True,
        )
    return repository.load_decision_backfill_batch(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=payload.get("project_pub_id"),
        cursor=payload.get("cursor"),
        limit=min(int(payload.get("limit") or 100), 1000),
        as_of=payload.get("as_of"),
        dry_run=bool(payload.get("dry_run", False)),
    )


@activity.defn(name="commit_semantic_decision_backfill_cursor_v2")
async def commit_semantic_decision_backfill_cursor_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist cursor, counts, cost, unknowns, and batch hash for audit."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "commit_decision_backfill_cursor"):
        raise ApplicationError(
            "semantic backfill cursor boundary is unavailable",
            type="decision_backfill_repository_contract_missing",
            non_retryable=True,
        )
    return repository.commit_decision_backfill_cursor(**payload)


def safe_payload_fingerprint(payload: dict[str, Any]) -> str:
    """Hash helper used in tests/logging without exposing source text."""

    return sha256(canonical_hash(payload).encode()).hexdigest()
