"""I/O activities for the deterministic V2 metric engine.

No model client is imported by this module.  Inputs are frozen facts, manifests,
events, and decisions; output rows are immutable and persisted atomically.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from geo_platform.config import get_settings
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.metrics.v2 import (
    AnswerSemanticEvent,
    DecisionMethod,
    DecisionStatus,
    DesignCoordinates,
    EvaluationInput,
    ExposureRole,
    MetricEvaluator,
    MetricSnapshotEngine,
    QueryContextFact,
    SemanticCapabilityStatus,
    SemanticDecisionFact,
    SnapshotBuildRequest,
    canonical_hash,
    load_definitions,
    validate_metric_definition,
)
from domain.metrics.v2.query_context import (
    AnalysisLens,
    BrandStructureType,
    ClassificationSource,
    ClassificationState,
    DerivationMethod,
    RequestedOperation,
)
from domain.metrics.v2.weighting import CalibrationErrorArtifact


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(child) for child in value]
    return value


def _decimal_map(value: object) -> dict[str, Decimal] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("weight mapping must be an object")
    return {str(key): Decimal(str(item)) for key, item in value.items()}


def _planned_repeat_counts(value: object) -> dict[tuple[str, str], int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("planned repeat counts must be an object")
    result: dict[tuple[str, str], int] = {}
    for raw_key, raw_count in value.items():
        parts = str(raw_key).split("\u001f", 1)
        if len(parts) != 2:
            raise ValueError("planned repeat key must be query_key\\u001fdesign_cell_key")
        result[(parts[0], parts[1])] = int(raw_count)
    return result


def _metric_definitions(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """Resolve the definition set frozen by the repository.

    Production activities must evaluate the exact database artifacts included
    in the dependency bundle.  The package registry remains available only for
    in-memory fixtures and explicitly supplied workflow tests.
    """

    documents = payload.get("definition_documents")
    if documents is not None:
        if not isinstance(documents, list) or not documents:
            raise ValueError("metrics_v2_definition_documents_required")
        definitions = tuple(validate_metric_definition(dict(item)) for item in documents)
        refs = payload.get("definition_refs")
        if isinstance(refs, list) and refs:
            expected = {
                (
                    str(item["name"]),
                    str(item["version"]),
                    str(item["definition_hash"]),
                )
                for item in refs
            }
            actual = {(item.name, item.version, item.definition_hash) for item in definitions}
            if actual != expected:
                raise ValueError("metrics_v2_frozen_definition_set_mismatch")
        return definitions

    registry = load_definitions()
    refs = payload.get("definition_refs")
    return (
        tuple(registry.get(str(item["name"]), str(item["version"])) for item in refs)
        if isinstance(refs, list) and refs
        else registry.all()
    )


def _query_context(payload: dict[str, Any]) -> QueryContextFact:
    return QueryContextFact(
        query_key=str(payload["query_key"]),
        query_text_hash=str(payload["query_text_hash"]),
        analysis_lenses=frozenset(AnalysisLens(item) for item in payload["analysis_lenses"]),
        requested_operations=frozenset(
            RequestedOperation(item) for item in payload["requested_operations"]
        ),
        detected_entity_ids=frozenset(map(str, payload.get("detected_entity_ids", []))),
        brand_structure_type=BrandStructureType(payload["brand_structure_type"]),
        classification_state=ClassificationState(payload["classification_state"]),
        classifier_version=str(payload["classifier_version"]),
        decision_task_bundle_hash=str(payload["decision_task_bundle_hash"]),
        entity_dictionary_hash=str(payload["entity_dictionary_hash"]),
        primary_lens=(
            AnalysisLens(payload["primary_lens"]) if payload.get("primary_lens") else None
        ),
        query_subtypes=tuple(map(str, payload.get("query_subtypes", []))),
        classification_source=ClassificationSource(payload.get("classification_source", "hybrid")),
        derivation_method=DerivationMethod(payload.get("derivation_method") or "hybrid"),
        review_status=str(payload.get("review_status") or "not_required"),
        override_reason=payload.get("override_reason"),
        decision_record_pub_ids=tuple(map(str, payload.get("decision_record_pub_ids", []))),
    )


def _subject(payload: dict[str, Any]) -> EvaluationInput:
    decisions = {
        str(task_ref): SemanticDecisionFact(
            task_ref=str(task_ref),
            status=DecisionStatus(item["status"]),
            value=dict(item.get("value") or {}),
            decision_pub_id=item.get("decision_pub_id"),
            method=(DecisionMethod(item["method"]) if item.get("method") else None),
            calibrated=bool(item.get("calibrated", False)),
            policy_matches=bool(item.get("policy_matches", True)),
            evidence_ready=bool(item.get("evidence_ready", True)),
            calibration_artifact_hash=item.get("calibration_artifact_hash"),
        )
        for task_ref, item in dict(payload.get("decisions") or {}).items()
    }
    statuses = {
        str(name): SemanticCapabilityStatus(value)
        for name, value in dict(payload.get("capability_statuses") or {}).items()
    }
    return EvaluationInput(
        answer_pub_id=str(payload["answer_pub_id"]),
        query_context=_query_context(dict(payload["query_context"])),
        focal_entity_id=str(payload["focal_entity_id"]),
        exposure_role=ExposureRole(payload["exposure_role"]),
        collection_eligible=bool(payload.get("collection_eligible", True)),
        capability_statuses=statuses,
        events=tuple(
            AnswerSemanticEvent.model_validate(item) for item in payload.get("events", [])
        ),
        decisions=decisions,
        answer_fields=dict(payload.get("answer_fields") or {}),
        query_context_fact_pub_id=str(payload.get("query_context_fact_pub_id") or ""),
        semantic_manifest_pub_id=str(payload.get("semantic_manifest_pub_id") or ""),
        semantic_decision_set_hash=str(payload.get("semantic_decision_set_hash") or ""),
        event_invariants_valid=bool(payload.get("event_invariants_valid", True)),
        evidence_spans_valid=bool(payload.get("evidence_spans_valid", True)),
        evidence_retrieval_ready=bool(payload.get("evidence_retrieval_ready", True)),
    )


@activity.defn(name="claim_metric_recompute_job_v2")
async def claim_recompute_job_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    return repository.claim_recompute_job(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        job_pub_id=str(payload["job_pub_id"]),
        workflow_id=str(payload["workflow_id"]),
        run_id=str(payload["run_id"]),
    )


@activity.defn(name="load_metric_snapshot_inputs_v2")
async def load_metric_snapshot_inputs_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Freeze all rows at an explicit ``as_of`` inside the repository."""

    frozen = payload.get("frozen_inputs")
    if isinstance(frozen, dict):
        return frozen
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "load_snapshot_build_inputs"):
        raise ApplicationError(
            "snapshot input loader is unavailable",
            type="metrics_snapshot_input_repository_contract_missing",
            non_retryable=True,
        )
    return repository.load_snapshot_build_inputs(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        scope=dict(payload["scope"]),
        as_of=str(payload["as_of"]),
        definition_refs=tuple(payload.get("definition_refs", [])),
    )


@activity.defn(name="evaluate_metric_answers_v2")
async def evaluate_metric_answers_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the pure engine and return the complete three-level trace."""

    definitions = _metric_definitions(payload)
    subjects = tuple(_subject(item) for item in payload.get("subjects", []))
    coordinates = {
        str(answer_pub_id): DesignCoordinates(
            design_cell_key=str(item["design_cell_key"]),
            model=str(item.get("model") or ""),
            region=str(item.get("region") or ""),
            mode=str(item.get("mode") or ""),
        )
        for answer_pub_id, item in dict(payload.get("design_coordinates_by_answer") or {}).items()
    }
    calibration = tuple(
        CalibrationErrorArtifact(
            artifact_hash=str(item["artifact_hash"]),
            method=DecisionMethod(item["method"]),
            false_positive_upper_bound=Decimal(str(item["false_positive_upper_bound"])),
            false_negative_upper_bound=Decimal(str(item["false_negative_upper_bound"])),
            task_ref=str(item.get("task_ref") or ""),
        )
        for item in payload.get("calibration_artifacts", [])
    )
    request = SnapshotBuildRequest(
        definitions=definitions,
        subjects=subjects,
        focal_entity_ids=tuple(map(str, payload.get("focal_entity_ids", []))),
        as_of=datetime.fromisoformat(str(payload["as_of"]).replace("Z", "+00:00")),
        scope=dict(payload["scope"]),
        dependency_bundle=dict(payload["dependency_bundle"]),
        design_coordinates_by_answer=coordinates,
        query_weights=_decimal_map(payload.get("query_weights")),
        design_cell_weights=(
            {
                str(query_key): {
                    str(cell): Decimal(str(weight)) for cell, weight in dict(weights).items()
                }
                for query_key, weights in dict(payload["design_cell_weights"]).items()
            }
            if payload.get("design_cell_weights") is not None
            else None
        ),
        planned_design_cells=(
            {
                str(query_key): tuple(map(str, cells))
                for query_key, cells in dict(payload["planned_design_cells"]).items()
            }
            if payload.get("planned_design_cells") is not None
            else None
        ),
        planned_repeat_counts=_planned_repeat_counts(payload.get("planned_repeat_counts")),
        calibration_artifacts=calibration,
        collection_coverage=Decimal(str(payload.get("collection_coverage", 1))),
        query_context_coverage=Decimal(str(payload.get("query_context_coverage", 1))),
        evidence_coverage=Decimal(str(payload.get("evidence_coverage", 1))),
        coverage_gate=Decimal(str(payload.get("coverage_gate", "0.98"))),
        design_basis=str(payload.get("design_basis") or "planned_cells"),
        minimum_queries_for_ready=int(payload.get("minimum_queries_for_ready", 10)),
    )
    result = MetricSnapshotEngine().build_set(request)
    rendered = _json_safe(result)
    if not isinstance(rendered, dict):
        raise TypeError("snapshot engine result must be an object")
    return rendered


@activity.defn(name="evaluate_answer_metric_v2")
async def evaluate_answer_metric_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate reusable answer × metric × focal-entity rows without weights."""

    definitions = _metric_definitions(payload)
    evaluator = MetricEvaluator()
    evaluations = [
        evaluator.evaluate(definition, _subject(subject))
        for subject in payload.get("subjects", [])
        for definition in definitions
    ]
    return {"evaluations": _json_safe(evaluations)}


@activity.defn(name="persist_metric_evaluations_v2")
async def persist_metric_evaluations_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "persist_metric_evaluations"):
        raise ApplicationError(
            "metric evaluation persistence boundary is unavailable",
            type="metric_evaluation_repository_contract_missing",
            non_retryable=True,
        )
    return repository.persist_metric_evaluations(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=str(payload["project_pub_id"]),
        evaluations=tuple(dict(item) for item in payload.get("evaluations", [])),
    )


def _snapshot_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item["metric_name"]), str(item["metric_version"]), str(item["focal_entity_id"]))


def _physical_window_boundary(value: object, *, end: bool) -> object:
    """Store inclusive business dates as a non-empty physical timestamp range."""

    parsed: date | None = None
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) == 10:
        parsed = date.fromisoformat(value)
    if parsed is None:
        return value
    return datetime.combine(parsed, time.max if end else time.min, UTC)


@activity.defn(name="persist_metric_snapshot_set_v2")
async def persist_metric_snapshot_set_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Map pure-domain output to physical rows and commit them atomically."""

    from geo_platform.metrics_v2.repository import MetricsV2Repository

    tenant_pub_id = str(payload["tenant_pub_id"])
    project_pub_id = str(payload["project_pub_id"])
    result = dict(payload["result"])
    scope = dict(result["scope"])
    now = datetime.now(UTC)
    set_pub_id = str(
        payload.get("snapshot_set_pub_id") or f"mss_{result['snapshot_set_hash'][:26]}"
    )
    window = dict(scope.get("window") or {})
    window_start = scope.get("window_start") or window.get("start")
    window_end = scope.get("window_end") or window.get("end")
    if not window_start or not window_end:
        raise ApplicationError(
            "snapshot scope window is incomplete",
            type="metric_snapshot_scope_invalid",
            non_retryable=True,
        )
    filters = dict(scope.get("filters") or {})
    set_row = {
        "pub_id": set_pub_id,
        "window_start": _physical_window_boundary(window_start, end=False),
        "window_end": _physical_window_boundary(window_end, end=True),
        "as_of": result["as_of"],
        "focal_entity_ids": list(result["focal_entity_ids"]),
        "filters": filters,
        "filter_hash": canonical_hash(filters),
        "scope_hash": result["scope_hash"],
        "aggregation_method": result["aggregation_method"],
        "design_basis": result["design_basis"],
        "query_set_hash": result["query_set_hash"],
        "design_set_hash": result["design_set_hash"],
        "dependency_bundle": result["dependency_bundle"],
        "dependency_bundle_hash": result["dependency_bundle_hash"],
        "state": result["state"],
        "failure_codes": list(result.get("failure_codes", [])),
        "snapshot_count": int(result["snapshot_count"]),
        "snapshot_set_hash": result["snapshot_set_hash"],
        "created_at": now,
    }
    snapshots: list[dict[str, Any]] = []
    snapshot_ids: dict[tuple[str, str, str], str] = {}
    for item in result["snapshots"]:
        identity = _snapshot_identity(item)
        snapshot_pub_id = f"msn_{item['snapshot_hash'][:26]}"
        snapshot_ids[identity] = snapshot_pub_id
        snapshots.append(
            {
                "pub_id": snapshot_pub_id,
                **{key: value for key, value in item.items() if key != "focal_entity_ids"},
                "created_at": now,
            }
        )
    contributions = []
    for item in result["answer_contributions"]:
        identity = _snapshot_identity(item)
        contributions.append(
            {
                "pub_id": f"mct_{item['contribution_hash'][:26]}",
                "snapshot_pub_id": snapshot_ids[identity],
                "answer_pub_id": item["answer_pub_id"],
                "query_key": item["query_key"],
                "focal_entity_id": item["focal_entity_id"],
                "metric_name": item["metric_name"],
                "metric_version": item["metric_version"],
                "model": item["model"],
                "region": item["region"],
                "mode": item["mode"],
                "capture_time": item["capture_time"],
                "eligibility_status": item["eligibility_status"],
                "reason_codes": item["reason_codes"],
                "outcome_value": item["outcome_value"],
                "numerator_contribution": item["numerator_contribution"],
                "denominator_contribution": item["denominator_contribution"],
                "query_weight": item["query_weight"],
                "design_cell_weight": item["design_cell_weight"],
                "repeat_weight": item["repeat_weight"],
                "final_weight": item["final_weight"],
                "weighted_numerator": item["weighted_numerator"],
                "weighted_denominator": item["weighted_denominator"],
                "query_context_fact_pub_id": item["query_context_fact_pub_id"],
                "semantic_manifest_pub_id": item["semantic_manifest_pub_id"],
                "supporting_event_pub_ids": item["supporting_event_pub_ids"],
                "supporting_decision_pub_ids": item["supporting_decision_pub_ids"],
                "semantic_decision_set_hash": item["semantic_decision_set_hash"],
                "dimension_snapshot": item["dimension_snapshot"],
                "answer_detail_ref": item["answer_detail_ref"],
                "contribution_hash": item["contribution_hash"],
                "created_at": now,
            }
        )
    query_contributions = []
    for item in result["query_contributions"]:
        identity = _snapshot_identity(item)
        query_contributions.append(
            {
                "pub_id": f"mqc_{item['contribution_hash'][:26]}",
                "snapshot_pub_id": snapshot_ids[identity],
                "query_key": item["query_key"],
                "focal_entity_id": item["focal_entity_id"],
                "metric_name": item["metric_name"],
                "metric_version": item["metric_version"],
                "query_context_fact_pub_id": item["query_context_fact_pub_id"],
                "query_numerator": item["numerator"],
                "query_denominator": item["denominator"],
                "query_value": item["value"],
                "unknown_weight": item["unknown_weight"],
                "query_weight": item["query_weight"],
                "design_cell_count": item["design_cell_count"],
                "answer_count": item["answer_count"],
                "known_answer_count": item["known_answer_count"],
                "unknown_answer_count": item["unknown_answer_count"],
                "reason_codes": item["reason_codes"],
                "contribution_hash": item["contribution_hash"],
                "created_at": now,
            }
        )
    design_contributions = []
    for item in result["design_cell_contributions"]:
        identity = _snapshot_identity(item)
        valid = int(item["observed_repeat_count"])
        failed = max(0, int(item["planned_repeat_count"]) - valid)
        design_contributions.append(
            {
                "pub_id": f"mdc_{item['contribution_hash'][:26]}",
                "snapshot_pub_id": snapshot_ids[identity],
                "query_key": item["query_key"],
                "model": item["model"],
                "region": item["region"],
                "mode": item["mode"],
                "planned_repeat_count": item["planned_repeat_count"],
                "valid_repeat_count": valid,
                "failed_repeat_count": failed,
                "known_repeat_count": item["known_repeat_count"],
                "cell_weight": item["design_cell_weight"],
                "state": item["status"],
                "reason_codes": (["planned_cell_missing"] if item["status"] == "missing" else []),
                "contribution_hash": item["contribution_hash"],
                "created_at": now,
            }
        )
    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    return repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        snapshot_set=set_row,
        snapshots=snapshots,
        contributions=contributions,
        query_contributions=query_contributions,
        design_contributions=design_contributions,
    )


@activity.defn(name="finish_metric_recompute_job_v2")
async def finish_recompute_job_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    return repository.finish_recompute_job(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        job_pub_id=str(payload["job_pub_id"]),
        status=str(payload["status"]),
        snapshot_set_pub_id=payload.get("snapshot_set_pub_id"),
        input_count=int(payload.get("input_count") or 0),
        output_count=int(payload.get("output_count") or 0),
        skipped_count=int(payload.get("skipped_count") or 0),
        failure_codes=tuple(map(str, payload.get("failure_codes", []))),
        cursor_state=dict(payload.get("cursor_state") or {}),
    )


@activity.defn(name="publish_metric_snapshot_set_v2")
async def publish_snapshot_set_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    return repository.publish_snapshot_set_cas(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        set_pub_id=str(payload["snapshot_set_pub_id"]),
        publication_channel=str(payload.get("publication_channel") or "shadow"),
        expected_generation=int(payload.get("expected_generation") or 0),
        expected_snapshot_set_hash=str(payload["snapshot_set_hash"]),
        published_by=str(payload["published_by"]),
    )


@activity.defn(name="load_metrics_backfill_batch_v2")
async def load_metrics_backfill_batch_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from geo_platform.metrics_v2.repository import MetricsV2Repository

    settings = get_settings()
    repository = MetricsV2Repository(settings.worker_postgres_dsn or settings.postgres_dsn)
    if not hasattr(repository, "load_metrics_backfill_batch"):
        raise ApplicationError(
            "metrics backfill repository boundary is unavailable",
            type="metrics_backfill_repository_contract_missing",
            non_retryable=True,
        )
    return repository.load_metrics_backfill_batch(
        tenant_pub_id=str(payload["tenant_pub_id"]),
        project_pub_id=payload.get("project_pub_id"),
        cursor=payload.get("cursor"),
        limit=min(int(payload.get("limit") or 500), 2000),
        as_of=payload.get("as_of"),
        dry_run=bool(payload.get("dry_run", False)),
    )
