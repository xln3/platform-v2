"""PostgreSQL repository for immutable query-cohort metric snapshots.

Every tenant-scoped operation uses :func:`tenant_connection`; explicit tenant
predicates are retained as defence in depth and make object-level authorization
reviewable at the query site.  Reads never invoke the metric engine or a model.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from psycopg import Connection, errors, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..tenancy.ids import new_pub_id
from ..tenancy.psycopg import tenant_connection

_HASH_KEYS = frozenset(
    {
        "definition_hash",
        "metric_definition_hash",
        "snapshot_set_hash",
        "snapshot_hash",
        "contribution_hash",
    }
)

_RETRYABLE_LLM_FAILURE_CODES = frozenset(
    {
        "llm_api_adapter_unavailable",
        "llm_api_empty_response",
        "llm_api_invalid_json",
        "llm_api_invalid_response",
        "llm_api_network_error",
        "llm_api_rate_limited",
        "llm_api_schema_violation",
        "llm_api_timeout",
        "llm_api_upstream_unavailable",
    }
)


def _is_retryable_llm_failure(reason_code: str) -> bool:
    return reason_code in _RETRYABLE_LLM_FAILURE_CODES


def _semantic_manifest_event_type(status: str) -> str:
    if status == "failed":
        return "answer.semantic_events.failed.v2"
    if status == "review_required":
        return "answer.semantic_events.review_required.v2"
    return "answer.semantic_events.completed.v2"


_TABLE_PREFIX = {
    "metric_snapshot_set_v2": "mss",
    "metric_snapshot_v2": "msn",
    "metric_contribution_v2": "mct",
    "metric_query_contribution_v2": "mqc",
    "metric_design_cell_contribution_v2": "mdc",
}

_INSERT_COLUMNS: dict[str, frozenset[str]] = {
    "query_context_fact_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id query_key query_pub_id query_text_hash
        primary_lens analysis_lenses requested_operations query_subtypes detected_entity_ids
        brand_structure_type classification_state classifier_version decision_task_bundle_hash
        entity_dictionary_hash classification_source derivation_method decision_record_pub_ids
        review_status override_reason supersedes_pub_id fact_hash created_at
        """.split()
    ),
    "query_entity_exposure_fact_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id query_context_fact_pub_id query_key
        focal_entity_id exposure_role matched_entity_ids fact_hash created_at
        """.split()
    ),
    "answer_semantic_manifest_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id answer_pub_id analysis_run_pub_id
        query_context_fact_pub_id answer_text_hash input_hash event_schema_version
        extractor_bundle decision_task_bundle extractor_bundle_hash decision_task_bundle_hash
        entity_dictionary_hash status capability_statuses decision_record_pub_ids
        decision_set_hash failure_code failure_detail event_count evidenced_event_count
        event_set_hash supersedes_pub_id created_at completed_at
        """.split()
    ),
    "answer_semantic_event_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id answer_pub_id semantic_manifest_pub_id event_index
        event_type subject_entity_id object_entity_id event_value qualifiers answer_text_start
        answer_text_end offset_unit answer_excerpt_hash extractor_version scorer_version
        derivation_method decision_record_pub_ids decision_policy_version provenance_hash
        calibrated_confidence confidence_state review_status override_reason event_fingerprint
        created_at
        """.split()
    ),
    "semantic_decision_attempt_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id decision_job_pub_id attempt_index role method
        provider model model_revision inference_config prompt_template_ref prompt_template_hash
        rubric_hash output_schema_hash request_payload_hash response_payload_hash
        validated_output rationale_summary validation_status reason_codes latency_ms
        input_tokens output_tokens cost_amount cost_currency created_at
        """.split()
    ),
    "semantic_decision_record_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id decision_job_pub_id task_name task_version
        task_definition_hash subject_type subject_key subject_ref metric_name metric_version
        input_snapshot_ref input_hash context_hash method status result rationale_summary
        calibrated_confidence calibration_bucket reason_codes evidence_refs evidence_spans
        selected_attempt_pub_ids judge_policy_hash rubric_ref rubric_hash output_schema_hash
        supersedes_pub_id decision_hash created_at
        """.split()
    ),
    "metric_evaluation_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id answer_pub_id query_key focal_entity_id
        metric_name metric_version metric_definition_hash query_context_fact_pub_id
        semantic_manifest_pub_id semantic_decision_pub_ids semantic_decision_set_hash
        eligibility_status reason_codes outcome_value numerator_contribution
        denominator_contribution supporting_event_pub_ids evaluation_hash created_at
        """.split()
    ),
    "metric_snapshot_set_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id window_start window_end as_of focal_entity_ids
        filters filter_hash scope_hash aggregation_method design_basis query_set_hash
        design_set_hash dependency_bundle dependency_bundle_hash state failure_codes
        snapshot_count snapshot_set_hash created_at
        """.split()
    ),
    "metric_snapshot_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id snapshot_set_pub_id focal_entity_id metric_name
        metric_version metric_definition_hash state state_reason_codes value observed_value
        answer_weighted_value lower_bound upper_bound semantic_lower_bound semantic_upper_bound
        weighted_numerator weighted_denominator raw_numerator raw_denominator
        candidate_answer_count known_answer_count unknown_answer_count failed_answer_count
        decision_abstained_count decision_review_required_count not_applicable_answer_count
        excluded_answer_count unique_query_count design_cell_count effective_sample_size
        collection_coverage query_context_coverage semantic_coverage evidence_coverage
        semantic_coverage_by_capability decision_method_mix bootstrap_low bootstrap_high
        bootstrap_method bootstrap_seed adjudication_sensitivity_low
        adjudication_sensitivity_high calibration_artifact_hashes contribution_set_hash
        query_contribution_set_hash design_contribution_set_hash snapshot_hash created_at
        """.split()
    ),
    "metric_contribution_v2": frozenset(
        """
        pub_id snapshot_pub_id tenant_pub_id project_pub_id answer_pub_id query_key
        focal_entity_id metric_name metric_version model region mode capture_time
        eligibility_status reason_codes outcome_value numerator_contribution
        denominator_contribution query_weight design_cell_weight repeat_weight final_weight
        weighted_numerator weighted_denominator query_context_fact_pub_id
        semantic_manifest_pub_id supporting_event_pub_ids supporting_decision_pub_ids
        semantic_decision_set_hash dimension_snapshot answer_detail_ref contribution_hash
        created_at
        """.split()
    ),
    "metric_query_contribution_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id snapshot_pub_id query_key focal_entity_id
        metric_name metric_version query_context_fact_pub_id query_numerator query_denominator
        query_value unknown_weight query_weight design_cell_count answer_count
        known_answer_count unknown_answer_count reason_codes contribution_hash created_at
        """.split()
    ),
    "metric_design_cell_contribution_v2": frozenset(
        """
        pub_id tenant_pub_id project_pub_id snapshot_pub_id query_key model region mode
        planned_repeat_count valid_repeat_count failed_repeat_count known_repeat_count
        cell_weight state reason_codes contribution_hash created_at
        """.split()
    ),
}

_JSON_COLUMNS: dict[str, frozenset[str]] = {
    "query_context_fact_v2": frozenset(),
    "query_entity_exposure_fact_v2": frozenset(),
    "answer_semantic_manifest_v2": frozenset(
        {"extractor_bundle", "decision_task_bundle", "capability_statuses"}
    ),
    "answer_semantic_event_v2": frozenset({"event_value", "qualifiers"}),
    "semantic_decision_attempt_v2": frozenset({"inference_config", "validated_output"}),
    "semantic_decision_record_v2": frozenset(
        {"subject_ref", "result", "evidence_refs", "evidence_spans"}
    ),
    "metric_evaluation_v2": frozenset({"outcome_value"}),
    "metric_snapshot_set_v2": frozenset({"filters", "dependency_bundle"}),
    "metric_snapshot_v2": frozenset({"semantic_coverage_by_capability", "decision_method_mix"}),
    "metric_contribution_v2": frozenset({"outcome_value", "dimension_snapshot"}),
    "metric_query_contribution_v2": frozenset(),
    "metric_design_cell_contribution_v2": frozenset(),
}

# These columns distinguish a semantic JSON null from SQL NULL. In particular,
# an unknown evaluation has no outcome value but still must be an immutable,
# complete row.
_NON_NULL_JSON_COLUMNS: dict[str, frozenset[str]] = {
    table: (
        frozenset({"outcome_value"})
        if table in {"metric_evaluation_v2", "metric_contribution_v2"}
        else frozenset()
    )
    for table in _JSON_COLUMNS
}

_ARRAY_COLUMNS: dict[str, frozenset[str]] = {
    "query_context_fact_v2": frozenset(
        {
            "analysis_lenses",
            "requested_operations",
            "query_subtypes",
            "detected_entity_ids",
            "decision_record_pub_ids",
        }
    ),
    "query_entity_exposure_fact_v2": frozenset({"matched_entity_ids"}),
    "answer_semantic_manifest_v2": frozenset({"decision_record_pub_ids"}),
    "answer_semantic_event_v2": frozenset({"decision_record_pub_ids"}),
    "semantic_decision_attempt_v2": frozenset({"reason_codes"}),
    "semantic_decision_record_v2": frozenset({"reason_codes", "selected_attempt_pub_ids"}),
    "metric_evaluation_v2": frozenset(
        {"semantic_decision_pub_ids", "reason_codes", "supporting_event_pub_ids"}
    ),
    "metric_snapshot_set_v2": frozenset({"focal_entity_ids", "failure_codes"}),
    "metric_snapshot_v2": frozenset({"state_reason_codes", "calibration_artifact_hashes"}),
    "metric_contribution_v2": frozenset(
        {
            "reason_codes",
            "supporting_event_pub_ids",
            "supporting_decision_pub_ids",
        }
    ),
    "metric_query_contribution_v2": frozenset({"reason_codes"}),
    "metric_design_cell_contribution_v2": frozenset({"reason_codes"}),
}


def _canonical_json(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"unsupported_canonical_value:{type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    )


def _canonical_hash(value: object) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()


def _override_recompute_work_items(
    affected_scopes: Sequence[Mapping[str, Any]],
    *,
    allow_official_publication: bool,
) -> dict[tuple[str, str | None], tuple[dict[str, Any], dict[str, Any] | None]]:
    """Build recompute work without granting override callers publish rights."""

    work_items: dict[
        tuple[str, str | None], tuple[dict[str, Any], dict[str, Any] | None]
    ] = {}
    for annotated_scope in affected_scopes:
        scope = {key: value for key, value in annotated_scope.items() if not key.startswith("_")}
        scope_hash = _canonical_hash(scope)
        raw_targets = annotated_scope.get("_publication_targets")
        targets = list(raw_targets) if isinstance(raw_targets, list) else []
        eligible_targets = [
            dict(target)
            for target in targets
            if isinstance(target, Mapping)
            and (
                target.get("publication_channel") == "shadow"
                or (
                    target.get("publication_channel") == "official"
                    and allow_official_publication
                )
            )
        ]
        if not eligible_targets:
            work_items[(scope_hash, None)] = (scope, None)
            continue
        for target in eligible_targets:
            channel = str(target["publication_channel"])
            work_items[(scope_hash, channel)] = (scope, target)
    return work_items


def _wire_value(value: object) -> Any:
    """Convert database-native values to deterministic Temporal/JSON values."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _wire_value(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_wire_value(child) for child in value]
    return value


def _idempotency_hash(value: str) -> str:
    if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
        return value
    return sha256(value.encode()).hexdigest()


def _cursor_encode(payload: Mapping[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(_canonical_json(dict(payload)).encode()).decode()
    return encoded.rstrip("=")


def _cursor_decode(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        document = json.loads(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_metrics_v2_cursor") from exc
    if not isinstance(document, dict) or document.get("v") != 1:
        raise ValueError("invalid_metrics_v2_cursor")
    return document


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value)
    return [value]


def _json_object(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def _float_or_zero(value: object) -> float:
    return 0.0 if value is None else float(value)  # type: ignore[arg-type]


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _answer_href(project_pub_id: str, answer_pub_id: str, stored_ref: object = None) -> str:
    if isinstance(stored_ref, str) and stored_ref.startswith("/"):
        return stored_ref
    return (
        f"/api/v2/customer-dashboard/projects/{project_pub_id}"
        f"/answer-library/answers/{answer_pub_id}"
    )


def _required_capabilities(value: object) -> list[str]:
    if isinstance(value, Mapping):
        return sorted(str(key) for key in value)
    capabilities: list[str] = []
    for item in _json_list(value):
        if isinstance(item, Mapping):
            name = item.get("name")
            if name is not None:
                capabilities.append(str(name))
        else:
            capabilities.append(str(item))
    return sorted(set(capabilities))


def _decision_task_refs(value: object) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _json_list(value):
        if isinstance(item, Mapping):
            refs.append(dict(item))
        else:
            refs.append({"task_ref": str(item)})
    return refs


def _definition_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    definition = _json_object(row.get("definition"))
    return {
        "metric_name": row["name"],
        "metric_version": row["version"],
        "business_question": str(
            definition.get("business_question") or definition.get("description") or row["name"]
        ),
        "definition_hash": row["definition_hash"],
        "status": row["status"],
        "unit_type": row["unit_type"],
        "outcome_source": row["outcome_source"],
        "aggregation_methods": list(row.get("allowed_aggregation_methods") or ()),
        "required_semantic_capabilities": _required_capabilities(
            row.get("required_semantic_capabilities")
        ),
        "decision_task_refs": _decision_task_refs(row.get("decision_task_refs")),
        "query_predicate": _json_object(definition.get("query_predicate")),
        "answer_eligibility_predicate": _json_object(
            definition.get("answer_eligibility_predicate")
            or definition.get("answer_predicate")
            or definition.get("applicability")
        ),
        "outcome_expression": _json_object(
            definition.get("outcome_expression") or definition.get("outcome")
        ),
        "denominator_description": str(
            definition.get("denominator_description")
            or definition.get("missing_policy")
            or "versioned metric denominator"
        ),
        "semantic_rubric_ref": row.get("semantic_rubric_ref"),
    }


def _metric_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_pub_id": row["pub_id"],
        "snapshot_hash": row["snapshot_hash"],
        "focal_entity_id": row["focal_entity_id"],
        "metric_name": row["metric_name"],
        "metric_version": row["metric_version"],
        "metric_definition_hash": row["metric_definition_hash"],
        "state": row["state"],
        "state_reason_codes": list(row.get("state_reason_codes") or ()),
        "value": _float_or_none(row.get("value")),
        "observed_value": _float_or_none(row.get("observed_value")),
        "answer_weighted_value": _float_or_none(row.get("answer_weighted_value")),
        "raw_numerator": _float_or_zero(row.get("raw_numerator")),
        "raw_denominator": _float_or_zero(row.get("raw_denominator")),
        "weighted_numerator": _float_or_zero(row.get("weighted_numerator")),
        "weighted_denominator": _float_or_zero(row.get("weighted_denominator")),
        "coverage": {
            "collection": _float_or_none(row.get("collection_coverage")),
            "query_context": _float_or_none(row.get("query_context_coverage")),
            "semantic": _float_or_none(row.get("semantic_coverage")),
            "evidence": _float_or_none(row.get("evidence_coverage")),
            "semantic_by_capability": _json_object(row.get("semantic_coverage_by_capability")),
        },
        "decision_method_mix": _json_object(row.get("decision_method_mix")),
        "adjudication_sensitivity": {
            "lower": _float_or_none(row.get("adjudication_sensitivity_low")),
            "upper": _float_or_none(row.get("adjudication_sensitivity_high")),
        },
        "missing_bounds": {
            "lower": _float_or_none(row.get("lower_bound")),
            "upper": _float_or_none(row.get("upper_bound")),
        },
        "unique_query_count": int(row.get("unique_query_count") or 0),
        "candidate_answer_count": int(row.get("candidate_answer_count") or 0),
        "known_answer_count": int(row.get("known_answer_count") or 0),
        "unknown_answer_count": int(row.get("unknown_answer_count") or 0),
        "failed_answer_count": int(row.get("failed_answer_count") or 0),
        "not_applicable_answer_count": int(row.get("not_applicable_answer_count") or 0),
        "excluded_answer_count": int(row.get("excluded_answer_count") or 0),
        "design_cell_count": int(row.get("design_cell_count") or 0),
        "contribution_set_hash": row["contribution_set_hash"],
        "query_contribution_set_hash": row["query_contribution_set_hash"],
        "design_contribution_set_hash": row["design_contribution_set_hash"],
    }


def _decision_record_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the storage record in the public/domain field vocabulary."""

    return {
        "decision_pub_id": row["pub_id"],
        "tenant_pub_id": row["tenant_pub_id"],
        "project_pub_id": row["project_pub_id"],
        "decision_job_pub_id": row["decision_job_pub_id"],
        "task_name": row["task_name"],
        "task_version": row["task_version"],
        "task_definition_hash": row["task_definition_hash"],
        "subject_type": row["subject_type"],
        "subject_key": row["subject_key"],
        "subject_ref": _json_object(row["subject_ref"]),
        "metric_name": row.get("metric_name"),
        "metric_version": row.get("metric_version"),
        "input_snapshot_ref": row["input_snapshot_ref"],
        "input_hash": row["input_hash"],
        "context_hash": row["context_hash"],
        "method": row["method"],
        "status": row["status"],
        "result": _json_object(row["result"]),
        "rationale_summary": row.get("rationale_summary"),
        "calibrated_confidence": _float_or_none(row.get("calibrated_confidence")),
        "calibration_bucket": row.get("calibration_bucket"),
        "reason_codes": list(row.get("reason_codes") or ()),
        "evidence_refs": _json_list(row.get("evidence_refs")),
        "evidence_spans": _json_list(row.get("evidence_spans")),
        "selected_attempt_pub_ids": list(row.get("selected_attempt_pub_ids") or ()),
        "judge_policy_hash": row["judge_policy_hash"],
        "rubric_ref": row["rubric_ref"],
        "rubric_hash": row["rubric_hash"],
        "output_schema_hash": row["output_schema_hash"],
        "supersedes_pub_id": row.get("supersedes_pub_id"),
        "decision_hash": row["decision_hash"],
        "created_at": row["created_at"],
    }


def _decision_job_projection(
    row: Mapping[str, Any], *, reused: bool | None = None
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "job_pub_id": row["pub_id"],
        "decision_job_pub_id": row["pub_id"],
        "project_pub_id": row["project_pub_id"],
        "task_ref": f"{row['task_name']}@{row['task_version']}",
        "task_definition_hash": row["task_definition_hash"],
        "subject_type": row["subject_type"],
        "subject_key": row["subject_key"],
        "status": row["status"],
        "state_reason_codes": list(row.get("state_reason_codes") or ()),
        "failure_code": row.get("failure_code"),
        "snapshot_set_pub_id": None,
        "selected_decision_pub_id": row.get("selected_decision_pub_id"),
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
    }
    if reused is not None:
        document["reused"] = reused
    return document


def _recompute_job_projection(
    row: Mapping[str, Any], *, reused: bool | None = None
) -> dict[str, Any]:
    codes = list(row.get("failure_codes") or ())
    document: dict[str, Any] = {
        "job_pub_id": row["pub_id"],
        "project_pub_id": row["project_pub_id"],
        "status": row["status"],
        "state_reason_codes": codes,
        "failure_code": codes[0] if codes else None,
        "snapshot_set_pub_id": row.get("snapshot_set_pub_id"),
        "selected_decision_pub_id": None,
        "input_count": int(row.get("input_count") or 0),
        "output_count": int(row.get("output_count") or 0),
        "skipped_count": int(row.get("skipped_count") or 0),
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
    }
    if reused is not None:
        document["reused"] = reused
    return document


class MetricsV2Repository:
    """Tenant-safe read/write boundary for metrics V2 physical contracts."""

    def __init__(self, dsn: str) -> None:
        # Settings expose SQLAlchemy's explicit psycopg dialect form, while
        # this repository talks to psycopg directly.  Normalize once at the
        # boundary so API and worker callers cannot diverge.
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)

    def catalog(self) -> list[dict[str, Any]]:
        # Definitions are global and versioned; tenant context is deliberately
        # not involved in this one catalog read.
        with Connection.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT name,version,definition,definition_hash,status,unit_type,
                       required_semantic_capabilities,decision_task_refs,outcome_source,
                       semantic_rubric_ref,allowed_aggregation_methods
                FROM analytics.metric_definition
                WHERE definition_hash IS NOT NULL AND status <> 'legacy'
                ORDER BY name,version
                """
            ).fetchall()
        return [_definition_projection(row) for row in rows]

    @staticmethod
    def _snapshot_rows(
        connection: Connection[dict[str, Any]], set_pub_id: str
    ) -> list[dict[str, Any]]:
        return list(
            connection.execute(
                """
                SELECT snapshot.*
                FROM analytics.metric_snapshot_v2 snapshot
                WHERE snapshot.tenant_pub_id=current_setting('app.tenant_pub_id')
                  AND snapshot.snapshot_set_pub_id=%s
                ORDER BY snapshot.metric_name,snapshot.metric_version,
                         snapshot.focal_entity_id,snapshot.pub_id
                """,
                (set_pub_id,),
            ).fetchall()
        )

    @classmethod
    def _snapshot_set_projection(
        cls,
        connection: Connection[dict[str, Any]],
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        metrics = cls._snapshot_rows(connection, str(row["pub_id"]))
        return {
            "schema_version": "metric-snapshot-set-v2",
            "snapshot_set_pub_id": row["pub_id"],
            "snapshot_set_hash": row["snapshot_set_hash"],
            "project_pub_id": row["project_pub_id"],
            "state": row["state"],
            "as_of": row["as_of"],
            "window": {
                "start": _date_value(row["window_start"]),
                "end": _date_value(row["window_end"]),
            },
            "filters": _json_object(row["filters"]),
            "focal_entity_ids": list(row["focal_entity_ids"]),
            "aggregation_method": row["aggregation_method"],
            "design_basis": row["design_basis"],
            "scope_hash": row["scope_hash"],
            "dependency_bundle_hash": row["dependency_bundle_hash"],
            "metrics": [_metric_projection(metric) for metric in metrics],
        }

    def current_snapshot_set(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: str | None = None,
        end: str | None = None,
        models: Sequence[str] = (),
        regions: Sequence[str] = (),
        modes: Sequence[str] = (),
        focal_entity_ids: Sequence[str] = (),
        publication_channel: str = "official",
    ) -> dict[str, Any]:
        if publication_channel not in {"shadow", "official"}:
            raise ValueError("metrics_v2_publication_channel_invalid")
        filters = {
            "model": sorted(set(models)),
            "region": sorted(set(regions)),
            "mode": sorted(set(modes)),
        }
        focal = sorted(set(focal_entity_ids))
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT snapshot_set.*
                FROM analytics.metric_publication_v2 publication
                JOIN analytics.metric_snapshot_set_v2 snapshot_set
                  ON snapshot_set.tenant_pub_id=publication.tenant_pub_id
                 AND snapshot_set.project_pub_id=publication.project_pub_id
                 AND snapshot_set.pub_id=publication.snapshot_set_pub_id
                WHERE publication.tenant_pub_id=%s
                  AND publication.project_pub_id=%s
                  AND publication.publication_channel=%s
                  AND (%s::date IS NULL OR snapshot_set.window_start::date=%s::date)
                  AND (%s::date IS NULL OR snapshot_set.window_end::date=%s::date)
                  AND (%s::boolean OR snapshot_set.filters=%s::jsonb)
                  AND (%s::boolean OR snapshot_set.focal_entity_ids=%s::text[])
                ORDER BY publication.published_at DESC,publication.pub_id DESC
                LIMIT 1
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    publication_channel,
                    start,
                    start,
                    end,
                    end,
                    not any(filters.values()),
                    Jsonb(filters),
                    not focal,
                    focal,
                ),
            ).fetchone()
            if row is None:
                raise LookupError("metrics_v2_snapshot_set_not_found")
            return self._snapshot_set_projection(connection, row)

    def get_snapshot_set(self, *, tenant_pub_id: str, set_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, set_pub_id),
            ).fetchone()
            if row is None:
                raise LookupError("metrics_v2_snapshot_set_not_found")
            return self._snapshot_set_projection(connection, row)

    def get_snapshot(self, *, tenant_pub_id: str, snapshot_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT snapshot.*,definition.definition,
                       definition.required_semantic_capabilities,
                       definition.decision_task_refs
                FROM analytics.metric_snapshot_v2 snapshot
                LEFT JOIN analytics.metric_definition definition
                  ON definition.name=snapshot.metric_name
                 AND definition.version=snapshot.metric_version
                 AND definition.definition_hash=snapshot.metric_definition_hash
                WHERE snapshot.tenant_pub_id=%s AND snapshot.pub_id=%s
                """,
                (tenant_pub_id, snapshot_pub_id),
            ).fetchone()
            if row is None:
                raise LookupError("metrics_v2_snapshot_not_found")
        definition = _json_object(row.get("definition"))
        result = _metric_projection(row)
        result.update(
            {
                "snapshot_set_pub_id": row["snapshot_set_pub_id"],
                "formula": _json_object(
                    definition.get("outcome_expression") or definition.get("outcome")
                ),
                "denominator_description": str(
                    definition.get("denominator_description")
                    or definition.get("missing_policy")
                    or "versioned metric denominator"
                ),
                "required_semantic_capabilities": _required_capabilities(
                    row.get("required_semantic_capabilities")
                ),
                "decision_task_refs": _decision_task_refs(row.get("decision_task_refs")),
                "bootstrap": {
                    "lower": _float_or_none(row.get("bootstrap_low")),
                    "upper": _float_or_none(row.get("bootstrap_high")),
                    "method": row.get("bootstrap_method"),
                    "seed": row.get("bootstrap_seed"),
                },
                "calibration_artifact_hashes": list(row.get("calibration_artifact_hashes") or ()),
            }
        )
        return result

    @staticmethod
    def _snapshot_for_page(
        connection: Connection[dict[str, Any]], snapshot_pub_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT * FROM analytics.metric_snapshot_v2
            WHERE tenant_pub_id=current_setting('app.tenant_pub_id') AND pub_id=%s
            """,
            (snapshot_pub_id,),
        ).fetchone()
        if row is None:
            raise LookupError("metrics_v2_snapshot_not_found")
        return row

    def list_query_contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None = None,
        limit: int = 50,
        query: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_metrics_v2_page_limit")
        filter_hash = _canonical_hash({"query": query})
        decoded = _cursor_decode(cursor)
        if decoded is not None and (
            decoded.get("kind") != "query"
            or decoded.get("snapshot") != snapshot_pub_id
            or decoded.get("filter_hash") != filter_hash
        ):
            raise ValueError("metrics_v2_cursor_scope_mismatch")
        after_query = str(decoded["query_key"]) if decoded else None
        after_pub = str(decoded["pub_id"]) if decoded else None
        search = f"%{query}%" if query else None
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            snapshot = self._snapshot_for_page(connection, snapshot_pub_id)
            count_row = connection.execute(
                """
                    SELECT count(*)
                    FROM analytics.metric_query_contribution_v2 contribution
                    LEFT JOIN analytics.query_context_fact_v2 context
                      ON context.tenant_pub_id=contribution.tenant_pub_id
                     AND context.project_pub_id=contribution.project_pub_id
                     AND context.pub_id=contribution.query_context_fact_pub_id
                    WHERE contribution.tenant_pub_id=%s
                      AND contribution.snapshot_pub_id=%s
                      AND (%s::text IS NULL OR contribution.query_key ILIKE %s
                           OR context.query_pub_id ILIKE %s)
                    """,
                (tenant_pub_id, snapshot_pub_id, search, search, search),
            ).fetchone()
            assert count_row is not None
            filtered_count = int(count_row["count"])
            rows = connection.execute(
                """
                SELECT contribution.*,context.query_pub_id,answer.query_text
                FROM analytics.metric_query_contribution_v2 contribution
                LEFT JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=contribution.tenant_pub_id
                 AND context.project_pub_id=contribution.project_pub_id
                 AND context.pub_id=contribution.query_context_fact_pub_id
                LEFT JOIN LATERAL (
                  SELECT raw.query_text
                  FROM analytics.answer raw
                  WHERE raw.tenant_pub_id=contribution.tenant_pub_id
                    AND raw.project_pub_id=contribution.project_pub_id
                    AND (raw.query_pub_id=context.query_pub_id OR raw.query_pub_id IS NULL)
                    AND raw.query_text IS NOT NULL
                  ORDER BY raw.capture_time,raw.pub_id
                  LIMIT 1
                ) answer ON true
                WHERE contribution.tenant_pub_id=%s
                  AND contribution.snapshot_pub_id=%s
                  AND (%s::text IS NULL OR contribution.query_key ILIKE %s
                       OR context.query_pub_id ILIKE %s)
                  AND (%s::text IS NULL OR
                       (contribution.query_key,contribution.pub_id)>(%s::text,%s::text))
                ORDER BY contribution.query_key,contribution.pub_id
                LIMIT %s
                """,
                (
                    tenant_pub_id,
                    snapshot_pub_id,
                    search,
                    search,
                    search,
                    after_query,
                    after_query,
                    after_pub,
                    limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _cursor_encode(
                {
                    "v": 1,
                    "kind": "query",
                    "snapshot": snapshot_pub_id,
                    "filter_hash": filter_hash,
                    "query_key": last["query_key"],
                    "pub_id": last["pub_id"],
                }
            )
        data = [
            {
                "query_key": row["query_key"],
                "query_pub_id": row.get("query_pub_id"),
                "query_text": row.get("query_text"),
                "query_weight": _float_or_zero(row["query_weight"]),
                "numerator": _float_or_zero(row["query_numerator"]),
                "denominator": _float_or_zero(row["query_denominator"]),
                "value": _float_or_none(row.get("query_value")),
                "unknown_weight": _float_or_zero(row["unknown_weight"]),
                "design_cell_count": int(row["design_cell_count"]),
                "answer_count": int(row["answer_count"]),
                "contribution_hash": row["contribution_hash"],
            }
            for row in page
        ]
        return {
            "schema_version": "metric-query-contributions-v2",
            "snapshot_pub_id": snapshot_pub_id,
            "snapshot_candidate_count": int(snapshot["unique_query_count"]),
            "filtered_count": filtered_count,
            "data": data,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def list_contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None = None,
        limit: int = 50,
        eligibility_status: str | None = None,
        reason_code: str | None = None,
        query: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        hit: bool | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_metrics_v2_page_limit")
        filters = {
            "eligibility_status": eligibility_status,
            "reason_code": reason_code,
            "query": query,
            "model": model,
            "region": region,
            "mode": mode,
            "hit": hit,
        }
        filter_hash = _canonical_hash(filters)
        decoded = _cursor_decode(cursor)
        if decoded is not None and (
            decoded.get("kind") != "answer"
            or decoded.get("snapshot") != snapshot_pub_id
            or decoded.get("filter_hash") != filter_hash
        ):
            raise ValueError("metrics_v2_cursor_scope_mismatch")
        keys = decoded.get("keys") if decoded else None
        if keys is not None and (not isinstance(keys, list) or len(keys) != 6):
            raise ValueError("invalid_metrics_v2_cursor")
        search = f"%{query}%" if query else None
        hit_status = "included_hit" if hit is True else None
        miss_hit = hit is False
        parameters = (
            tenant_pub_id,
            snapshot_pub_id,
            eligibility_status,
            eligibility_status,
            reason_code,
            reason_code,
            search,
            search,
            search,
            model,
            model,
            region,
            region,
            mode,
            mode,
            hit_status,
            hit_status,
            miss_hit,
        )
        predicate = """
          contribution.tenant_pub_id=%s AND contribution.snapshot_pub_id=%s
          AND (%s::text IS NULL OR contribution.eligibility_status=%s)
          AND (%s::text IS NULL OR %s=ANY(contribution.reason_codes))
          AND (%s::text IS NULL OR contribution.query_key ILIKE %s OR raw.query_text ILIKE %s)
          AND (%s::text IS NULL OR contribution.model=%s)
          AND (%s::text IS NULL OR contribution.region=%s)
          AND (%s::text IS NULL OR contribution.mode=%s)
          AND (%s::text IS NULL OR contribution.eligibility_status=%s)
          AND (NOT %s::boolean OR contribution.eligibility_status<>'included_hit')
        """
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            snapshot = self._snapshot_for_page(connection, snapshot_pub_id)
            count_row = connection.execute(
                f"""
                    SELECT count(*)
                    FROM analytics.metric_contribution_v2 contribution
                    JOIN analytics.answer raw
                      ON raw.tenant_pub_id=contribution.tenant_pub_id
                     AND raw.pub_id=contribution.answer_pub_id
                    WHERE {predicate}
                    """,
                parameters,
            ).fetchone()
            assert count_row is not None
            filtered_count = int(count_row["count"])
            after = tuple(keys) if keys is not None else (None,) * 6
            rows = connection.execute(
                f"""
                SELECT contribution.*,raw.query_pub_id,raw.query_text,raw.response_text,
                       context.analysis_lenses,context.requested_operations,
                       exposure.exposure_role
                FROM analytics.metric_contribution_v2 contribution
                JOIN analytics.answer raw
                  ON raw.tenant_pub_id=contribution.tenant_pub_id
                 AND raw.pub_id=contribution.answer_pub_id
                LEFT JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=contribution.tenant_pub_id
                 AND context.project_pub_id=contribution.project_pub_id
                 AND context.pub_id=contribution.query_context_fact_pub_id
                LEFT JOIN analytics.query_entity_exposure_fact_v2 exposure
                  ON exposure.tenant_pub_id=contribution.tenant_pub_id
                 AND exposure.project_pub_id=contribution.project_pub_id
                 AND exposure.query_context_fact_pub_id=contribution.query_context_fact_pub_id
                 AND exposure.focal_entity_id=contribution.focal_entity_id
                WHERE {predicate}
                  AND (%s::text IS NULL OR
                    (contribution.query_key,contribution.model,contribution.region,
                     contribution.mode,contribution.capture_time,contribution.answer_pub_id)
                    >(%s::text,%s::text,%s::text,%s::text,%s::timestamptz,%s::text))
                ORDER BY contribution.query_key,contribution.model,contribution.region,
                         contribution.mode,contribution.capture_time,contribution.answer_pub_id
                LIMIT %s
                """,
                (*parameters, after[0], *after, limit + 1),
            ).fetchall()
            page = rows[:limit]
            event_ids = sorted(
                {
                    str(event_id)
                    for row in page
                    for event_id in (row.get("supporting_event_pub_ids") or ())
                }
            )
            decision_ids = sorted(
                {
                    str(decision_id)
                    for row in page
                    for decision_id in (row.get("supporting_decision_pub_ids") or ())
                }
            )
            event_rows = (
                connection.execute(
                    """
                    SELECT pub_id,event_type,subject_entity_id,object_entity_id,event_value,
                           answer_text_start,answer_text_end,answer_pub_id
                    FROM analytics.answer_semantic_event_v2
                    WHERE tenant_pub_id=%s AND project_pub_id=%s
                      AND pub_id=ANY(%s::text[])
                    """,
                    (tenant_pub_id, snapshot["project_pub_id"], event_ids),
                ).fetchall()
                if event_ids
                else []
            )
            decision_rows = (
                connection.execute(
                    """
                    SELECT pub_id,task_name,task_version,method,status,calibrated_confidence,
                           rubric_hash,evidence_refs,rationale_summary,result,decision_hash,
                           reason_codes
                    FROM analytics.semantic_decision_record_v2
                    WHERE tenant_pub_id=%s AND project_pub_id=%s
                      AND pub_id=ANY(%s::text[])
                    """,
                    (tenant_pub_id, snapshot["project_pub_id"], decision_ids),
                ).fetchall()
                if decision_ids
                else []
            )
        event_map = {str(row["pub_id"]): row for row in event_rows}
        decision_map = {str(row["pub_id"]): row for row in decision_rows}
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _cursor_encode(
                {
                    "v": 1,
                    "kind": "answer",
                    "snapshot": snapshot_pub_id,
                    "filter_hash": filter_hash,
                    "keys": [
                        last["query_key"],
                        last["model"],
                        last["region"],
                        last["mode"],
                        last["capture_time"],
                        last["answer_pub_id"],
                    ],
                }
            )
        data: list[dict[str, Any]] = []
        for row in page:
            answer_text = str(row.get("response_text") or "")
            supporting_events = []
            for event_id in row.get("supporting_event_pub_ids") or ():
                event = event_map.get(str(event_id))
                if event is None:
                    continue
                start = event.get("answer_text_start")
                end = event.get("answer_text_end")
                supporting_events.append(
                    {
                        "event_pub_id": event["pub_id"],
                        "event_type": event["event_type"],
                        "subject_entity_id": event.get("subject_entity_id"),
                        "object_entity_id": event.get("object_entity_id"),
                        "event_value": _json_object(event["event_value"]),
                        "answer_text_start": start,
                        "answer_text_end": end,
                        "answer_excerpt": (
                            answer_text[int(start) : int(end)]
                            if start is not None and end is not None
                            else None
                        ),
                    }
                )
            supporting_decisions = []
            for decision_id in row.get("supporting_decision_pub_ids") or ():
                decision = decision_map.get(str(decision_id))
                if decision is None:
                    continue
                supporting_decisions.append(
                    {
                        "decision_pub_id": decision["pub_id"],
                        "decision_hash": decision["decision_hash"],
                        "task": decision["task_name"],
                        "version": decision["task_version"],
                        "method": decision["method"],
                        "status": decision["status"],
                        "result": _json_object(decision["result"]),
                        "reason_codes": list(decision.get("reason_codes") or ()),
                        "calibrated_confidence": _float_or_none(
                            decision.get("calibrated_confidence")
                        ),
                        "rubric_hash": decision["rubric_hash"],
                        "evidence_refs": _json_list(decision["evidence_refs"]),
                        "rationale_summary": decision.get("rationale_summary"),
                    }
                )
            data.append(
                {
                    "answer_pub_id": row["answer_pub_id"],
                    "query_pub_id": row.get("query_pub_id"),
                    "query_key": row["query_key"],
                    "query_text": row.get("query_text"),
                    "analysis_lenses": list(row.get("analysis_lenses") or ()),
                    "requested_operations": list(row.get("requested_operations") or ()),
                    "exposure_role": row.get("exposure_role") or "unknown",
                    "model": row["model"],
                    "region": row["region"],
                    "mode": row["mode"],
                    "capture_time": row["capture_time"],
                    "eligibility_status": row["eligibility_status"],
                    "reason_codes": list(row["reason_codes"]),
                    "outcome_value": row["outcome_value"],
                    "numerator_contribution": _float_or_zero(row.get("numerator_contribution")),
                    "denominator_contribution": _float_or_zero(row.get("denominator_contribution")),
                    "query_weight": _float_or_zero(row["query_weight"]),
                    "design_cell_weight": _float_or_zero(row["design_cell_weight"]),
                    "repeat_weight": _float_or_zero(row["repeat_weight"]),
                    "final_weight": _float_or_zero(row["final_weight"]),
                    "weighted_numerator": _float_or_zero(row["weighted_numerator"]),
                    "weighted_denominator": _float_or_zero(row["weighted_denominator"]),
                    "semantic_manifest_pub_id": row.get("semantic_manifest_pub_id"),
                    "supporting_events": supporting_events,
                    "supporting_decisions": supporting_decisions,
                    "answer_excerpt": answer_text[:5000] or None,
                    "answer_detail_href": _answer_href(
                        str(row["project_pub_id"]),
                        str(row["answer_pub_id"]),
                        row.get("answer_detail_ref"),
                    ),
                    "contribution_hash": row["contribution_hash"],
                }
            )
        return {
            "schema_version": "metric-contributions-v2",
            "snapshot_pub_id": snapshot_pub_id,
            "totals": {
                "snapshot_candidate_count": int(snapshot["candidate_answer_count"]),
                "filtered_count": filtered_count,
                "raw_numerator": _float_or_zero(snapshot["raw_numerator"]),
                "raw_denominator": _float_or_zero(snapshot["raw_denominator"]),
                "weighted_numerator": _float_or_zero(snapshot["weighted_numerator"]),
                "weighted_denominator": _float_or_zero(snapshot["weighted_denominator"]),
                "contribution_set_hash": snapshot["contribution_set_hash"],
            },
            "data": data,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def get_semantic_event(self, *, tenant_pub_id: str, event_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT event.*,answer.response_text
                FROM analytics.answer_semantic_event_v2 event
                JOIN analytics.answer answer
                  ON answer.tenant_pub_id=event.tenant_pub_id
                 AND answer.pub_id=event.answer_pub_id
                WHERE event.tenant_pub_id=%s AND event.pub_id=%s
                """,
                (tenant_pub_id, event_pub_id),
            ).fetchone()
        if row is None:
            raise LookupError("metrics_v2_semantic_event_not_found")
        start = row.get("answer_text_start")
        end = row.get("answer_text_end")
        answer_text = str(row.get("response_text") or "")
        return {
            "schema_version": "answer-semantic-event-v2",
            "event_pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "answer_pub_id": row["answer_pub_id"],
            "semantic_manifest_pub_id": row["semantic_manifest_pub_id"],
            "event_type": row["event_type"],
            "subject_entity_id": row.get("subject_entity_id"),
            "object_entity_id": row.get("object_entity_id"),
            "event_value": _json_object(row["event_value"]),
            "qualifiers": _json_object(row["qualifiers"]),
            "answer_text_start": start,
            "answer_text_end": end,
            "offset_unit": row["offset_unit"],
            "answer_excerpt": (
                answer_text[int(start) : int(end)]
                if start is not None and end is not None
                else None
            ),
            "answer_excerpt_hash": row.get("answer_excerpt_hash"),
            "derivation_method": row["derivation_method"],
            "decision_record_pub_ids": list(row["decision_record_pub_ids"]),
            "review_status": row["review_status"],
            "event_fingerprint": row["event_fingerprint"],
            "answer_detail_href": _answer_href(
                str(row["project_pub_id"]), str(row["answer_pub_id"])
            ),
        }

    def get_semantic_decision(self, *, tenant_pub_id: str, decision_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT pub_id,project_pub_id,task_name,task_version,subject_type,subject_key,
                       method,status,result,rationale_summary,calibrated_confidence,
                       reason_codes,evidence_refs,evidence_spans,judge_policy_hash,
                       rubric_ref,rubric_hash,decision_hash,created_at
                FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, decision_pub_id),
            ).fetchone()
        if row is None:
            raise LookupError("metrics_v2_semantic_decision_not_found")
        return {
            "schema_version": "semantic-decision-record-v2",
            "decision_pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "task_name": row["task_name"],
            "task_version": row["task_version"],
            "subject_type": row["subject_type"],
            "subject_key": row["subject_key"],
            "method": row["method"],
            "status": row["status"],
            "result": _json_object(row["result"]),
            "rationale_summary": row.get("rationale_summary"),
            "calibrated_confidence": _float_or_none(row.get("calibrated_confidence")),
            "reason_codes": list(row["reason_codes"]),
            "evidence_refs": _json_list(row["evidence_refs"]),
            "evidence_spans": _json_list(row["evidence_spans"]),
            "judge_policy_hash": row["judge_policy_hash"],
            "rubric_ref": row["rubric_ref"],
            "rubric_hash": row["rubric_hash"],
            "decision_hash": row["decision_hash"],
            "created_at": row["created_at"],
        }

    def get_snapshot_job(self, *, tenant_pub_id: str, job_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT pub_id,project_pub_id,status,failure_codes,snapshot_set_pub_id,
                       created_at,started_at,completed_at
                FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, job_pub_id),
            ).fetchone()
        if row is None:
            raise LookupError("metrics_v2_snapshot_job_not_found")
        codes = list(row.get("failure_codes") or ())
        return {
            "job_pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "status": row["status"],
            "state_reason_codes": codes,
            "failure_code": codes[0] if codes else None,
            "snapshot_set_pub_id": row.get("snapshot_set_pub_id"),
            "selected_decision_pub_id": None,
            "created_at": row["created_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }

    def get_decision_job(self, *, tenant_pub_id: str, job_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT pub_id,project_pub_id,status,state_reason_codes,failure_code,
                       selected_decision_pub_id,created_at,started_at,completed_at
                FROM analytics.semantic_decision_job_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, job_pub_id),
            ).fetchone()
        if row is None:
            raise LookupError("metrics_v2_decision_job_not_found")
        return {
            "job_pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "status": row["status"],
            "state_reason_codes": list(row.get("state_reason_codes") or ()),
            "failure_code": row.get("failure_code"),
            "snapshot_set_pub_id": None,
            "selected_decision_pub_id": row.get("selected_decision_pub_id"),
            "created_at": row["created_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }

    def create_decision_request(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        subject_ref: Mapping[str, Any],
        input_snapshot_ref: str,
        input_hash: str,
        context_hash: str,
        judge_policy_hash: str,
        idempotency_key: str,
        task_ref: str | None = None,
        task_name: str | None = None,
        task_version: str | None = None,
        task_definition_hash: str | None = None,
        subject_type: str | None = None,
        subject_key: str | None = None,
        rejudge_generation: int = 0,
        supersedes_decision_pub_id: str | None = None,
        workflow_id: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Idempotently create a Decision job and its requested outbox row.

        ``task_ref`` is the workflow-facing contract.  Explicit task fields are
        accepted as a worker/bootstrap escape hatch, but when both forms are
        present they must describe the same immutable task definition.
        """

        resolved_name = task_name
        resolved_version = task_version
        resolved_hash = task_definition_hash
        resolved_subject_type = subject_type
        if task_ref is not None:
            from domain.analysis.v2 import load_builtin_task_definitions

            task = load_builtin_task_definitions().get(task_ref)
            task_values = (
                task.name,
                task.version,
                task.definition_hash,
                task.subject_type.value,
            )
            supplied = (
                resolved_name,
                resolved_version,
                resolved_hash,
                resolved_subject_type,
            )
            if any(
                value is not None and value != expected
                for value, expected in zip(supplied, task_values, strict=True)
            ):
                raise ValueError("metrics_v2_decision_task_ref_mismatch")
            (
                resolved_name,
                resolved_version,
                resolved_hash,
                resolved_subject_type,
            ) = task_values
        if not all((resolved_name, resolved_version, resolved_hash, resolved_subject_type)):
            raise ValueError("metrics_v2_decision_task_definition_required")
        if not subject_ref:
            raise ValueError("metrics_v2_decision_subject_ref_required")
        resolved_subject_key = subject_key or _canonical_hash(dict(subject_ref))
        canonical_idempotency = _canonical_hash(
            {
                "tenant_pub_id": tenant_pub_id,
                "task_definition_hash": resolved_hash,
                "subject_key": resolved_subject_key,
                "input_hash": input_hash,
                "context_hash": context_hash,
                "judge_policy_hash": judge_policy_hash,
                "rejudge_generation": rejudge_generation,
            }
        )
        # The caller token is retained in the event for request correlation;
        # storage identity follows the canonical physical contract in section
        # 22.7 and therefore cannot be changed by a retrying caller.
        request_key_hash = _idempotency_hash(idempotency_key)
        job_pub_id = f"sdj_{canonical_idempotency[:26]}"
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            task_row = connection.execute(
                """
                SELECT status
                FROM analytics.semantic_decision_task_definition_v2
                WHERE name=%s AND version=%s AND definition_hash=%s
                  AND status IN ('experimental','published')
                """,
                (resolved_name, resolved_version, resolved_hash),
            ).fetchone()
            policy_row = connection.execute(
                """
                SELECT status
                FROM analytics.semantic_judge_policy_v2
                WHERE policy_hash=%s AND status IN ('experimental','published')
                  AND compatible_task_refs @> %s::jsonb
                """,
                (
                    judge_policy_hash,
                    Jsonb([f"{resolved_name}@{resolved_version}"]),
                ),
            ).fetchone()
            if task_row is None:
                raise LookupError("metrics_v2_decision_task_definition_not_available")
            if policy_row is None:
                raise LookupError("metrics_v2_judge_policy_not_available")
            row = connection.execute(
                """
                INSERT INTO analytics.semantic_decision_job_v2
                  (pub_id,tenant_pub_id,project_pub_id,task_name,task_version,
                   task_definition_hash,subject_type,subject_key,subject_ref,
                   input_snapshot_ref,input_hash,context_hash,judge_policy_hash,
                   rejudge_generation,supersedes_decision_pub_id,status,idempotency_key,
                   workflow_id,run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        'pending',%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING *
                """,
                (
                    job_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    resolved_name,
                    resolved_version,
                    resolved_hash,
                    resolved_subject_type,
                    resolved_subject_key,
                    Jsonb(dict(subject_ref)),
                    input_snapshot_ref,
                    input_hash,
                    context_hash,
                    judge_policy_hash,
                    rejudge_generation,
                    supersedes_decision_pub_id,
                    canonical_idempotency,
                    workflow_id or None,
                    run_id or None,
                ),
            ).fetchone()
            reused = row is None
            if row is None:
                row = connection.execute(
                    """
                    SELECT * FROM analytics.semantic_decision_job_v2
                    WHERE tenant_pub_id=%s AND idempotency_key=%s
                    """,
                    (tenant_pub_id, canonical_idempotency),
                ).fetchone()
                if row is None:
                    raise RuntimeError("metrics_v2_decision_request_race")
            else:
                self._insert_outbox(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    event_type="semantic.decision.requested.v2",
                    aggregate_pub_id=job_pub_id,
                    project_pub_id=project_pub_id,
                    subject_hash=canonical_idempotency,
                    payload={
                        "task_ref": f"{resolved_name}@{resolved_version}",
                        "subject_type": resolved_subject_type,
                        "subject_key": resolved_subject_key,
                        "subject_ref": dict(subject_ref),
                        "desired_judge_policy_ref": judge_policy_hash,
                        "rejudge_generation": rejudge_generation,
                        "request_idempotency_hash": request_key_hash,
                        "correlation_id": job_pub_id,
                        "causation_id": supersedes_decision_pub_id,
                    },
                )
                self._insert_workflow_start(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    workflow_type="semantic_decision_v2",
                    workflow_id=f"decision-v2:{job_pub_id}",
                    task_queue="geo-platform-v2-decision",
                    payload={
                        "tenant_pub_id": tenant_pub_id,
                        "project_pub_id": project_pub_id,
                        "job_pub_id": job_pub_id,
                        "decision_job_pub_id": job_pub_id,
                        "task_ref": f"{resolved_name}@{resolved_version}",
                        "subject_ref": dict(subject_ref),
                        "input_snapshot_ref": input_snapshot_ref,
                        "input_hash": input_hash,
                        "context_hash": context_hash,
                        "judge_policy_hash": judge_policy_hash,
                        "idempotency_key": canonical_idempotency,
                        "rejudge_generation": rejudge_generation,
                        "supersedes_decision_pub_id": supersedes_decision_pub_id,
                    },
                )
            decision_row = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND decision_job_pub_id=%s
                """,
                (tenant_pub_id, row["pub_id"]),
            ).fetchone()
        result = _decision_job_projection(row, reused=reused)
        if decision_row is not None:
            result["decision"] = _decision_record_projection(decision_row)
            result["decision_pub_id"] = decision_row["pub_id"]
            result["decision_hash"] = decision_row["decision_hash"]
        return result

    def persist_decision_atomic(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_job_pub_id: str,
        attempts: Sequence[Mapping[str, Any]],
        decision: Mapping[str, Any],
        workflow_id: str = "",
        run_id: str = "",
        manifest: Mapping[str, Any] | None = None,
        events: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Commit attempts, one final Decision, job CAS and outbox atomically."""

        decision_values = dict(decision)
        decision_pub_id = str(
            decision_values.pop("decision_pub_id", decision_values.get("pub_id") or "")
        )
        decision_values["pub_id"] = decision_pub_id
        if not decision_pub_id:
            raise ValueError("metrics_v2_decision_pub_id_required")
        decision_hash = str(decision_values.get("decision_hash") or "")
        if len(decision_hash) != 64:
            raise ValueError("metrics_v2_decision_hash_required")
        now = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            job = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_job_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, project_pub_id, decision_job_pub_id),
            ).fetchone()
            if job is None:
                raise LookupError("metrics_v2_decision_job_not_found")
            existing = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND decision_job_pub_id=%s
                """,
                (tenant_pub_id, project_pub_id, decision_job_pub_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["pub_id"] != decision_pub_id
                    or existing["decision_hash"] != decision_hash
                ):
                    raise RuntimeError("metrics_v2_decision_completion_conflict")
                result = _decision_job_projection(job, reused=True)
                result.update(
                    {
                        "decision_pub_id": existing["pub_id"],
                        "decision_hash": existing["decision_hash"],
                        "decision": _decision_record_projection(existing),
                    }
                )
                return result
            if job["status"] in {"succeeded", "abstained", "review_required"}:
                raise RuntimeError("metrics_v2_decision_job_terminal_without_record")
            if job["status"] == "failed":
                connection.execute(
                    """
                    UPDATE analytics.semantic_decision_job_v2
                    SET status='pending',retry_count=retry_count+1,
                        state_reason_codes='{}',failure_code=NULL,
                        started_at=NULL,completed_at=NULL
                    WHERE tenant_pub_id=%s AND pub_id=%s AND status='failed'
                    """,
                    (tenant_pub_id, decision_job_pub_id),
                )
            if job["status"] in {"pending", "failed"}:
                claimed = connection.execute(
                    """
                    UPDATE analytics.semantic_decision_job_v2
                    SET status='running',started_at=COALESCE(started_at,%s),
                        workflow_id=COALESCE(NULLIF(%s,''),workflow_id),
                        run_id=COALESCE(NULLIF(%s,''),run_id)
                    WHERE tenant_pub_id=%s AND pub_id=%s AND status='pending'
                    RETURNING *
                    """,
                    (now, workflow_id, run_id, tenant_pub_id, decision_job_pub_id),
                ).fetchone()
                if claimed is None:
                    raise RuntimeError("metrics_v2_decision_job_claim_conflict")
                job = claimed
            elif job["status"] != "running":
                raise RuntimeError("metrics_v2_decision_job_not_completable")

            immutable_job_fields = (
                "tenant_pub_id",
                "project_pub_id",
                "decision_job_pub_id",
                "task_name",
                "task_version",
                "task_definition_hash",
                "subject_type",
                "subject_key",
                "input_snapshot_ref",
                "input_hash",
                "context_hash",
                "judge_policy_hash",
            )
            expected_values = {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "decision_job_pub_id": decision_job_pub_id,
                "task_name": job["task_name"],
                "task_version": job["task_version"],
                "task_definition_hash": job["task_definition_hash"],
                "subject_type": job["subject_type"],
                "subject_key": job["subject_key"],
                "input_snapshot_ref": job["input_snapshot_ref"],
                "input_hash": job["input_hash"],
                "context_hash": job["context_hash"],
                "judge_policy_hash": job["judge_policy_hash"],
            }
            for field in immutable_job_fields:
                supplied = decision_values.get(field)
                expected = expected_values[field]
                if supplied is not None and supplied != expected:
                    raise RuntimeError(f"metrics_v2_decision_job_identity_mismatch:{field}")
                decision_values[field] = expected
            supplied_subject = decision_values.get("subject_ref")
            if supplied_subject is not None and dict(supplied_subject) != dict(job["subject_ref"]):
                raise RuntimeError("metrics_v2_decision_job_identity_mismatch:subject_ref")
            decision_values["subject_ref"] = dict(job["subject_ref"])
            if decision_values.get("supersedes_pub_id") is None:
                decision_values["supersedes_pub_id"] = job.get("supersedes_decision_pub_id")

            attempt_ids: set[str] = set()
            for index, attempt in enumerate(attempts):
                attempt_values = dict(attempt)
                attempt_values.pop("fast_path_name", None)
                attempt_values.setdefault("pub_id", new_pub_id("sda"))
                attempt_values.setdefault("attempt_index", index)
                for field, expected in (
                    ("tenant_pub_id", tenant_pub_id),
                    ("project_pub_id", project_pub_id),
                    ("decision_job_pub_id", decision_job_pub_id),
                ):
                    supplied = attempt_values.get(field)
                    if supplied is not None and supplied != expected:
                        raise RuntimeError(f"metrics_v2_decision_attempt_scope_mismatch:{field}")
                    attempt_values[field] = expected
                if attempt_values.get("validated_output") is None:
                    attempt_values["validated_output"] = {}
                attempt_id = str(attempt_values["pub_id"])
                if attempt_id in attempt_ids:
                    raise ValueError("metrics_v2_duplicate_decision_attempt")
                attempt_ids.add(attempt_id)
                self._insert_mapping(connection, "semantic_decision_attempt_v2", attempt_values)
            selected_attempts = set(map(str, decision_values.get("selected_attempt_pub_ids") or ()))
            if not selected_attempts <= attempt_ids:
                raise ValueError("metrics_v2_selected_attempt_not_in_job")
            self._insert_mapping(connection, "semantic_decision_record_v2", decision_values)

            decision_status = str(decision_values["status"])
            terminal_status = {
                "accepted": "succeeded",
                "abstained": "abstained",
                "review_required": "review_required",
                "failed": "failed",
            }.get(decision_status)
            if terminal_status is None:
                raise ValueError("metrics_v2_decision_status_invalid")
            selected_decision = None if terminal_status == "failed" else decision_pub_id
            reason_codes = list(decision_values.get("reason_codes") or ())
            terminal = connection.execute(
                """
                UPDATE analytics.semantic_decision_job_v2
                SET status=%s,selected_decision_pub_id=%s,
                    workflow_id=COALESCE(NULLIF(%s,''),workflow_id),
                    run_id=COALESCE(NULLIF(%s,''),run_id),
                    state_reason_codes=%s,failure_code=%s,completed_at=%s
                WHERE tenant_pub_id=%s AND pub_id=%s AND status='running'
                RETURNING *
                """,
                (
                    terminal_status,
                    selected_decision,
                    workflow_id,
                    run_id,
                    reason_codes,
                    reason_codes[0] if terminal_status == "failed" and reason_codes else None,
                    now,
                    tenant_pub_id,
                    decision_job_pub_id,
                ),
            ).fetchone()
            if terminal is None:
                raise RuntimeError("metrics_v2_decision_completion_cas_failed")

            if manifest is not None:
                self._insert_semantic_manifest_rows(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project_pub_id,
                    manifest=manifest,
                    events=events,
                )
            event_type = {
                "accepted": "semantic.decision.completed.v2",
                "abstained": "semantic.decision.abstained.v2",
                "review_required": "semantic.decision.review_required.v2",
                "failed": "semantic.decision.failed.v2",
            }[decision_status]
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type=event_type,
                aggregate_pub_id=decision_pub_id,
                project_pub_id=project_pub_id,
                subject_hash=decision_hash,
                payload={
                    "decision_job_pub_id": decision_job_pub_id,
                    "decision_pub_id": decision_pub_id,
                    "decision_hash": decision_hash,
                    "correlation_id": decision_job_pub_id,
                    "causation_id": job.get("supersedes_decision_pub_id"),
                },
            )
            recompute_job_pub_id = None
            superseded_decision_pub_id = job.get("supersedes_decision_pub_id")
            if decision_status == "accepted" and superseded_decision_pub_id:
                previous = connection.execute(
                    """
                    SELECT status,reason_codes
                    FROM analytics.semantic_decision_record_v2
                    WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, project_pub_id, superseded_decision_pub_id),
                ).fetchone()
                previous_reasons = set(map(str, (previous or {}).get("reason_codes") or ()))
                if (
                    previous is not None
                    and previous["status"] == "failed"
                    and any(_is_retryable_llm_failure(code) for code in previous_reasons)
                ):
                    recompute_job_pub_id = self._schedule_recovered_decision_recompute(
                        connection,
                        tenant_pub_id=tenant_pub_id,
                        project_pub_id=project_pub_id,
                        previous_decision_pub_id=str(superseded_decision_pub_id),
                        decision_pub_id=decision_pub_id,
                        decision_hash=decision_hash,
                    )
            record = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, decision_pub_id),
            ).fetchone()
            assert record is not None
        result = _decision_job_projection(terminal, reused=False)
        result.update(
            {
                "decision_pub_id": decision_pub_id,
                "decision_hash": decision_hash,
                "decision": _decision_record_projection(record),
            }
        )
        if recompute_job_pub_id is not None:
            result["recompute_job_pub_id"] = recompute_job_pub_id
        return result

    # Worker code used both names while the V2 workflow contract was settling.
    complete_decision_atomic = persist_decision_atomic

    @staticmethod
    def _insert_outbox(
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        event_type: str,
        aggregate_pub_id: str,
        project_pub_id: str,
        subject_hash: str,
        payload: Mapping[str, Any],
    ) -> str:
        event_id = new_pub_id("evt")
        occurred_at = datetime.now(UTC)
        body = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "subject_pub_id": aggregate_pub_id,
            "subject_version_hash": subject_hash,
            **dict(payload),
        }
        connection.execute(
            """
            INSERT INTO integration.outbox_event
              (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                event_id,
                tenant_pub_id,
                event_type,
                aggregate_pub_id,
                aggregate_pub_id,
                Jsonb(body),
                occurred_at,
            ),
        )
        return event_id

    @staticmethod
    def _insert_workflow_start(
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Insert one durable Temporal start command in the caller transaction."""

        row = connection.execute(
            """
            INSERT INTO integration.workflow_start_command
              (command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
               trace_context)
            VALUES (%s,%s,%s,%s,%s,%s,'{}'::jsonb)
            ON CONFLICT (workflow_id) DO NOTHING
            RETURNING workflow_id
            """,
            (
                uuid5(NAMESPACE_URL, f"geo-platform-v2:{workflow_id}"),
                tenant_pub_id,
                workflow_type,
                workflow_id,
                task_queue,
                Jsonb(dict(payload)),
            ),
        ).fetchone()
        return row is not None

    @staticmethod
    def _latest_snapshot_scope(
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_pub_id: str,
    ) -> dict[str, Any] | None:
        scopes = MetricsV2Repository._affected_snapshot_scopes(
            connection,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            decision_pub_id=decision_pub_id,
        )
        return (
            {key: value for key, value in scopes[0].items() if not key.startswith("_")}
            if scopes
            else None
        )

    @staticmethod
    def _affected_snapshot_scopes(
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_pub_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return the latest affected set for every distinct historical scope."""

        rows = connection.execute(
            """
            WITH ranked AS (
              SELECT snapshot_set.window_start,snapshot_set.window_end,
                     snapshot_set.focal_entity_ids,snapshot_set.filters,
                     snapshot_set.aggregation_method,snapshot_set.design_basis,
                     snapshot_set.scope_hash,source_job.scope AS original_scope,
                     snapshot_set.as_of,snapshot_set.created_at,snapshot_set.pub_id,
                     row_number() OVER (
                       PARTITION BY snapshot_set.scope_hash
                       ORDER BY snapshot_set.as_of DESC,snapshot_set.created_at DESC,
                                snapshot_set.pub_id DESC
                     ) AS scope_rank
              FROM analytics.metric_snapshot_set_v2 snapshot_set
              LEFT JOIN LATERAL (
                SELECT job.scope
                FROM analytics.metric_recompute_job_v2 job
                WHERE job.tenant_pub_id=snapshot_set.tenant_pub_id
                  AND job.project_pub_id=snapshot_set.project_pub_id
                  AND job.snapshot_set_pub_id=snapshot_set.pub_id
                  AND job.status='succeeded'
                ORDER BY job.completed_at DESC,job.pub_id DESC LIMIT 1
              ) source_job ON true
              WHERE snapshot_set.tenant_pub_id=%s
                AND snapshot_set.project_pub_id=%s
                AND snapshot_set.state IN ('ready','partial')
                AND EXISTS (
                  SELECT 1
                  FROM analytics.metric_snapshot_v2 snapshot
                  JOIN analytics.metric_contribution_v2 contribution
                    ON contribution.tenant_pub_id=snapshot.tenant_pub_id
                   AND contribution.project_pub_id=snapshot.project_pub_id
                   AND contribution.snapshot_pub_id=snapshot.pub_id
                  WHERE snapshot.tenant_pub_id=snapshot_set.tenant_pub_id
                    AND snapshot.project_pub_id=snapshot_set.project_pub_id
                    AND snapshot.snapshot_set_pub_id=snapshot_set.pub_id
                    AND %s=ANY(contribution.supporting_decision_pub_ids)
                )
            )
            SELECT * FROM ranked WHERE scope_rank=1
            ORDER BY as_of DESC,created_at DESC,pub_id DESC
            """,
            (tenant_pub_id, project_pub_id, decision_pub_id),
        ).fetchall()

        def date_boundary(value: object) -> str:
            if isinstance(value, datetime):
                return value.date().isoformat()
            return str(value)

        publication_rows = connection.execute(
            """
            SELECT scope_hash,publication_channel,generation
            FROM analytics.metric_publication_v2
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND scope_hash=ANY(%s::text[])
            ORDER BY scope_hash,publication_channel
            """,
            (tenant_pub_id, project_pub_id, [str(row["scope_hash"]) for row in rows]),
        ).fetchall()
        publications_by_scope: dict[str, list[dict[str, Any]]] = {}
        for publication in publication_rows:
            publications_by_scope.setdefault(
                str(publication["scope_hash"]), []
            ).append(
                {
                    "publication_channel": str(publication["publication_channel"]),
                    "expected_generation": int(publication["generation"]),
                }
            )
        answer: list[dict[str, Any]] = []
        for row in rows:
            original_scope = _json_object(row.get("original_scope"))
            scope = original_scope or {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "window": {
                    "start": date_boundary(row["window_start"]),
                    "end": date_boundary(row["window_end"]),
                },
                "filters": _json_object(row.get("filters")),
                "focal_entity_ids": list(map(str, row.get("focal_entity_ids") or ())),
                "aggregation_method": str(row["aggregation_method"]),
                "publication_channel": "shadow",
            }
            if _canonical_hash(scope) != str(row["scope_hash"]):
                raise RuntimeError("metrics_v2_historical_scope_reconstruction_mismatch")
            answer.append(
                scope
                | {
                    "_publication_targets": publications_by_scope.get(
                        str(row["scope_hash"]), []
                    )
                }
            )
        return tuple(answer)

    @staticmethod
    def _affected_semantic_scope(
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_pub_id: str,
    ) -> dict[str, Any] | None:
        """Derive a runnable scope when no prior snapshot contribution exists."""

        answer_rows = connection.execute(
            """
            WITH impacted_contexts AS (
              SELECT context.pub_id
              FROM analytics.query_context_fact_v2 context
              WHERE context.tenant_pub_id=%s AND context.project_pub_id=%s
                AND %s=ANY(context.decision_record_pub_ids)
            ), impacted_manifests AS (
              SELECT DISTINCT manifest.answer_pub_id,manifest.query_context_fact_pub_id
              FROM analytics.answer_semantic_manifest_v2 manifest
              WHERE manifest.tenant_pub_id=%s AND manifest.project_pub_id=%s
                AND (
                  %s=ANY(manifest.decision_record_pub_ids)
                  OR manifest.query_context_fact_pub_id IN (
                    SELECT pub_id FROM impacted_contexts
                  )
                )
            )
            SELECT min(answer.capture_time)::date AS window_start,
                   max(answer.capture_time)::date AS window_end,
                   array_agg(DISTINCT impacted.query_context_fact_pub_id)
                     AS context_pub_ids
            FROM impacted_manifests impacted
            JOIN analytics.answer answer
              ON answer.tenant_pub_id=%s
             AND answer.project_pub_id=%s
             AND answer.pub_id=impacted.answer_pub_id
            """,
            (
                tenant_pub_id,
                project_pub_id,
                decision_pub_id,
                tenant_pub_id,
                project_pub_id,
                decision_pub_id,
                tenant_pub_id,
                project_pub_id,
            ),
        ).fetchone()
        if answer_rows is None or answer_rows.get("window_start") is None:
            return None
        context_pub_ids = list(map(str, answer_rows.get("context_pub_ids") or ()))
        focal_rows = connection.execute(
            """
            SELECT DISTINCT focal_entity_id
            FROM analytics.query_entity_exposure_fact_v2
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND query_context_fact_pub_id=ANY(%s::text[])
            ORDER BY focal_entity_id
            """,
            (tenant_pub_id, project_pub_id, context_pub_ids),
        ).fetchall()
        focal_entity_ids = [str(row["focal_entity_id"]) for row in focal_rows]
        if not focal_entity_ids:
            return None
        return {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "window": {
                "start": answer_rows["window_start"].isoformat(),
                "end": answer_rows["window_end"].isoformat(),
            },
            "filters": {"model": [], "region": [], "mode": []},
            "focal_entity_ids": focal_entity_ids,
            "aggregation_method": "query_macro",
            "publication_channel": "shadow",
        }

    @classmethod
    def _refresh_query_contexts_for_successor(
        cls,
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        previous_decision_pub_id: str,
        decision_pub_id: str,
        override_reason: str,
    ) -> dict[str, str]:
        """Derive immutable query-context successors for a corrected query decision."""

        contexts = connection.execute(
            """
            SELECT context.*
            FROM analytics.query_context_fact_v2 context
            WHERE context.tenant_pub_id=%s AND context.project_pub_id=%s
              AND %s=ANY(context.decision_record_pub_ids)
              AND NOT EXISTS (
                SELECT 1 FROM analytics.query_context_fact_v2 successor
                WHERE successor.tenant_pub_id=context.tenant_pub_id
                  AND successor.project_pub_id=context.project_pub_id
                  AND successor.supersedes_pub_id=context.pub_id
              )
            ORDER BY context.created_at,context.pub_id
            """,
            (tenant_pub_id, project_pub_id, previous_decision_pub_id),
        ).fetchall()
        if not contexts:
            return {}

        from domain.analysis.v2.decision_models import DecisionStatus, SemanticDecisionRecord
        from domain.metrics.v2.query_context import (
            AnalysisLens,
            BrandStructureType,
            ExposureRole,
            RequestedOperation,
            derive_brand_structure,
            derive_exposure_role,
        )

        successors: dict[str, str] = {}
        now = datetime.now(UTC)
        for context in contexts:
            decision_ids = tuple(
                decision_pub_id if str(item) == previous_decision_pub_id else str(item)
                for item in (context.get("decision_record_pub_ids") or ())
            )
            rows = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND pub_id=ANY(%s::text[])
                ORDER BY created_at,pub_id
                """,
                (tenant_pub_id, project_pub_id, list(decision_ids)),
            ).fetchall()
            if len(rows) != len(set(decision_ids)):
                raise RuntimeError("metrics_v2_query_successor_decision_missing")
            records = tuple(
                SemanticDecisionRecord.model_validate(_decision_record_projection(row))
                for row in rows
            )
            accepted = {
                record.task_name: record
                for record in records
                if record.status is DecisionStatus.ACCEPTED
            }
            intent = accepted.get("query-intent")
            entities = accepted.get("query-brand-entity-resolution")
            query_records = tuple(
                record
                for record in records
                if record.task_name in {"query-intent", "query-brand-entity-resolution"}
            )
            if any(record.status is DecisionStatus.FAILED for record in query_records):
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
                raw_lenses = set(map(str, intent.result.get("analysis_lenses", ())))
                lenses = set()
                if "selection" in raw_lenses:
                    lenses.add(AnalysisLens.AI_RECOMMENDATION.value)
                if raw_lenses & {"reputation", "comparison", "factual"}:
                    lenses.add(AnalysisLens.AI_IMPRESSION.value)
                if "comparison" in raw_lenses:
                    lenses.add(AnalysisLens.AI_RECOMMENDATION.value)
                operation_map = {
                    "recommend": RequestedOperation.RECOMMEND.value,
                    "rank": RequestedOperation.RANK.value,
                    "compare": RequestedOperation.COMPARE.value,
                    "describe": RequestedOperation.DESCRIBE.value,
                    "verify": RequestedOperation.FACT_LOOKUP.value,
                }
                operations = {
                    operation_map[item]
                    for item in map(str, intent.result.get("requested_operations", ()))
                    if item in operation_map
                }
                subtypes = set(map(str, intent.result.get("query_subtypes", ())))
                resolutions = entities.result.get("resolutions", ())
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
            old_primary = context.get("primary_lens")
            primary_lens = (
                str(old_primary)
                if old_primary is not None and str(old_primary) in lenses
                else (sorted(lenses)[0] if lenses else None)
            )
            material = {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "query_key": str(context["query_key"]),
                "query_pub_id": context.get("query_pub_id"),
                "query_text_hash": str(context["query_text_hash"]),
                "primary_lens": primary_lens,
                "analysis_lenses": sorted(lenses),
                "requested_operations": sorted(operations),
                "query_subtypes": sorted(subtypes),
                "detected_entity_ids": sorted(detected),
                "brand_structure_type": structure.value,
                "classification_state": classification_state,
                "classifier_version": str(context["classifier_version"]),
                "decision_task_bundle_hash": str(context["decision_task_bundle_hash"]),
                "entity_dictionary_hash": str(context["entity_dictionary_hash"]),
                "classification_source": "manual_override",
                "derivation_method": "human",
                "decision_record_pub_ids": sorted(decision_ids),
                "review_status": "overridden",
                "override_reason": override_reason,
                "supersedes_pub_id": str(context["pub_id"]),
            }
            fact_hash = _canonical_hash(material)
            fact_pub_id = f"qcf_{fact_hash[:26]}"
            cls._insert_mapping(
                connection,
                "query_context_fact_v2",
                {"pub_id": fact_pub_id, **material, "fact_hash": fact_hash, "created_at": now},
            )
            focal_rows = connection.execute(
                """
                SELECT focal_entity_id
                FROM analytics.query_entity_exposure_fact_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND query_context_fact_pub_id=%s
                ORDER BY focal_entity_id
                """,
                (tenant_pub_id, project_pub_id, context["pub_id"]),
            ).fetchall()
            for focal_row in focal_rows:
                focal_entity_id = str(focal_row["focal_entity_id"])
                role = (
                    ExposureRole.UNKNOWN
                    if classification_state != "ready"
                    else derive_exposure_role(
                        tuple(sorted(detected)),
                        focal_entity_id,
                        has_unresolved_brand_surface=unresolved,
                    )
                )
                exposure_material = {
                    "query_context_fact_pub_id": fact_pub_id,
                    "query_key": material["query_key"],
                    "focal_entity_id": focal_entity_id,
                    "exposure_role": role.value,
                    "matched_entity_ids": sorted(detected),
                }
                exposure_hash = _canonical_hash(exposure_material)
                cls._insert_mapping(
                    connection,
                    "query_entity_exposure_fact_v2",
                    {
                        "pub_id": f"qef_{exposure_hash[:26]}",
                        "tenant_pub_id": tenant_pub_id,
                        "project_pub_id": project_pub_id,
                        **exposure_material,
                        "fact_hash": exposure_hash,
                        "created_at": now,
                    },
                )
            cls._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="query.context.classified.v2",
                aggregate_pub_id=fact_pub_id,
                project_pub_id=project_pub_id,
                subject_hash=fact_hash,
                payload={
                    "query_key": material["query_key"],
                    "correlation_id": fact_pub_id,
                    "causation_id": context["pub_id"],
                },
            )
            successors[str(context["pub_id"])] = fact_pub_id
        return successors

    def _schedule_recovered_decision_recompute(
        self,
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        previous_decision_pub_id: str,
        decision_pub_id: str,
        decision_hash: str,
    ) -> str | None:
        scope = self._latest_snapshot_scope(
            connection,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            decision_pub_id=previous_decision_pub_id,
        )
        if scope is None:
            return None
        scope_hash = _canonical_hash(scope)
        idempotency_key = _canonical_hash(
            {
                "reason": "semantic_decision_recovered",
                "decision_pub_id": decision_pub_id,
                "decision_hash": decision_hash,
            }
        )
        recompute_job_pub_id = f"mrj_{idempotency_key[:26]}"
        inserted = connection.execute(
            """
            INSERT INTO analytics.metric_recompute_job_v2
              (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
               target_definition_refs,status,idempotency_key,requested_by)
            VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'pending',%s,%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING pub_id
            """,
            (
                recompute_job_pub_id,
                tenant_pub_id,
                project_pub_id,
                Jsonb(scope),
                scope_hash,
                idempotency_key,
                "system_semantic_auto_rejudge",
            ),
        ).fetchone()
        if inserted is None:
            existing = connection.execute(
                """
                SELECT pub_id FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND idempotency_key=%s
                """,
                (tenant_pub_id, idempotency_key),
            ).fetchone()
            return str(existing["pub_id"]) if existing is not None else None
        self._insert_outbox(
            connection,
            tenant_pub_id=tenant_pub_id,
            event_type="metric.snapshot_set.requested.v2",
            aggregate_pub_id=recompute_job_pub_id,
            project_pub_id=project_pub_id,
            subject_hash=scope_hash,
            payload={
                "correlation_id": recompute_job_pub_id,
                "causation_id": decision_pub_id,
            },
        )
        self._insert_workflow_start(
            connection,
            tenant_pub_id=tenant_pub_id,
            workflow_type="metric_snapshot_set_v2",
            workflow_id=f"metrics-v2:{recompute_job_pub_id}",
            task_queue="geo-platform-v2-metrics",
            payload={
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "job_pub_id": recompute_job_pub_id,
                "scope": scope,
                "as_of": datetime.now(UTC).isoformat(),
            },
        )
        return recompute_job_pub_id

    @classmethod
    def _refresh_answer_manifests_for_successor(
        cls,
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        previous_decision_pub_id: str,
        decision_pub_id: str,
        query_context_successors: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """Materialize new manifests/events that consume an immutable successor."""

        context_successors = dict(query_context_successors or {})
        manifests = connection.execute(
            """
            SELECT manifest.*
            FROM analytics.answer_semantic_manifest_v2 manifest
            WHERE manifest.tenant_pub_id=%s AND manifest.project_pub_id=%s
              AND (
                %s=ANY(manifest.decision_record_pub_ids)
                OR manifest.query_context_fact_pub_id=ANY(%s::text[])
              )
              AND NOT EXISTS (
                SELECT 1 FROM analytics.answer_semantic_manifest_v2 successor
                WHERE successor.tenant_pub_id=manifest.tenant_pub_id
                  AND successor.project_pub_id=manifest.project_pub_id
                  AND successor.supersedes_pub_id=manifest.pub_id
              )
            ORDER BY manifest.created_at,manifest.pub_id
            """,
            (
                tenant_pub_id,
                project_pub_id,
                previous_decision_pub_id,
                list(context_successors),
            ),
        ).fetchall()
        if not manifests:
            return ()

        from domain.analysis.v2.decision_models import SemanticDecisionRecord
        from domain.analysis.v2.event_derivation import (
            EventDerivationContext,
            capability_analyses_from_decisions,
            derive_answer_semantic_events,
        )

        created_ids: list[str] = []
        now = datetime.now(UTC)
        for manifest in manifests:
            decision_ids = tuple(
                decision_pub_id if str(item) == previous_decision_pub_id else str(item)
                for item in (manifest.get("decision_record_pub_ids") or ())
            )
            rows = connection.execute(
                """
                SELECT * FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND pub_id=ANY(%s::text[])
                ORDER BY created_at,pub_id
                """,
                (tenant_pub_id, project_pub_id, list(decision_ids)),
            ).fetchall()
            if len(rows) != len(set(decision_ids)):
                raise RuntimeError("metrics_v2_manifest_successor_decision_missing")
            records = tuple(
                SemanticDecisionRecord.model_validate(_decision_record_projection(row))
                for row in rows
            )
            decision_set_hash = _canonical_hash(
                sorted((record.decision_pub_id, record.decision_hash) for record in records)
            )
            query_context_fact_pub_id = context_successors.get(
                str(manifest["query_context_fact_pub_id"]),
                str(manifest["query_context_fact_pub_id"]),
            )
            manifest_pub_id = (
                "asm_"
                + _canonical_hash(
                    {
                        "previous": manifest["pub_id"],
                        "decision_set_hash": decision_set_hash,
                        "query_context_fact_pub_id": query_context_fact_pub_id,
                    }
                )[:26]
            )
            policy_rows = connection.execute(
                """
                SELECT policy_hash,name,version FROM analytics.semantic_judge_policy_v2
                WHERE policy_hash=ANY(%s::text[])
                """,
                (sorted({record.judge_policy_hash for record in records}),),
            ).fetchall()
            policy_versions = {
                str(row["policy_hash"]): f"{row['name']}@{row['version']}" for row in policy_rows
            }
            old_event = connection.execute(
                """
                SELECT extractor_version,scorer_version
                FROM analytics.answer_semantic_event_v2
                WHERE tenant_pub_id=%s AND semantic_manifest_pub_id=%s
                ORDER BY event_index,pub_id LIMIT 1
                """,
                (tenant_pub_id, manifest["pub_id"]),
            ).fetchone()
            context = EventDerivationContext(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                answer_pub_id=str(manifest["answer_pub_id"]),
                semantic_manifest_pub_id=manifest_pub_id,
                extractor_version=str(
                    (old_event or {}).get("extractor_version") or "semantic-event-deriver-v2.0.0"
                ),
                scorer_version=str(
                    (old_event or {}).get("scorer_version") or "semantic-decision-v2"
                ),
                policy_versions_by_hash=policy_versions,
                created_at=now,
            )
            derived_events = derive_answer_semantic_events(records, context=context)
            event_documents: list[dict[str, Any]] = []
            for event in derived_events:
                document = event.model_dump(mode="python")
                document["pub_id"] = (
                    "ase_"
                    + _canonical_hash(
                        {
                            "manifest": manifest_pub_id,
                            "event": event.event_fingerprint,
                        }
                    )[:26]
                )
                document["semantic_manifest_pub_id"] = manifest_pub_id
                event_documents.append(document)
            capabilities = capability_analyses_from_decisions(records)
            capability_document = _json_object(manifest.get("capability_statuses"))
            capability_document.update(
                {name: analysis.model_dump(mode="json") for name, analysis in capabilities.items()}
            )
            capability_states = {
                str(value.get("status"))
                for value in capability_document.values()
                if isinstance(value, Mapping)
            }
            if capability_states == {"failed"}:
                status = "failed"
            elif capability_states & {"failed", "abstained"}:
                status = "partial"
            elif "review_required" in capability_states:
                status = "review_required"
            else:
                status = "ready"
            manifest_values = {
                key: manifest[key]
                for key in _INSERT_COLUMNS["answer_semantic_manifest_v2"]
                if key in manifest
            }
            manifest_values.update(
                {
                    "pub_id": manifest_pub_id,
                    "status": status,
                    "capability_statuses": capability_document,
                    "decision_record_pub_ids": sorted(decision_ids),
                    "decision_set_hash": decision_set_hash,
                    "query_context_fact_pub_id": query_context_fact_pub_id,
                    "failure_code": None,
                    "failure_detail": None,
                    "event_set_hash": (
                        None
                        if status == "failed"
                        else _canonical_hash(
                            sorted(
                                (event["pub_id"], event["event_fingerprint"])
                                for event in event_documents
                            )
                        )
                    ),
                    "supersedes_pub_id": manifest["pub_id"],
                    "created_at": now,
                    "completed_at": now if status != "failed" else None,
                }
            )
            cls._insert_semantic_manifest_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                manifest=manifest_values,
                events=event_documents,
            )
            created_ids.append(manifest_pub_id)
        return tuple(created_ids)

    def request_snapshot(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        scope: Mapping[str, Any],
        scope_hash: str,
        idempotency_key: str,
        requested_by: str,
    ) -> dict[str, Any]:
        idem = _canonical_hash(
            {
                "tenant_pub_id": tenant_pub_id,
                "request_idempotency_hash": _idempotency_hash(idempotency_key),
            }
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            existing_set = connection.execute(
                """
                SELECT pub_id FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND scope_hash=%s
                ORDER BY created_at DESC,pub_id DESC LIMIT 1
                """,
                (tenant_pub_id, project_pub_id, scope_hash),
            ).fetchone()
            if existing_set is not None:
                return {
                    "job_pub_id": None,
                    "snapshot_set_pub_id": existing_set["pub_id"],
                    "status": "succeeded",
                    "reused": True,
                    "scope_hash": scope_hash,
                }
            existing_job = connection.execute(
                """
                SELECT pub_id,scope_hash,status,snapshot_set_pub_id
                FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND idempotency_key=%s
                """,
                (tenant_pub_id, idem),
            ).fetchone()
            if existing_job is not None:
                if existing_job["scope_hash"] != scope_hash:
                    raise RuntimeError("metrics_v2_idempotency_scope_conflict")
                return {
                    "job_pub_id": existing_job["pub_id"],
                    "snapshot_set_pub_id": existing_job.get("snapshot_set_pub_id"),
                    "status": existing_job["status"],
                    "reused": True,
                    "scope_hash": scope_hash,
                }
            job_pub_id = new_pub_id("mrj")
            connection.execute(
                """
                INSERT INTO analytics.metric_recompute_job_v2
                  (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
                   target_definition_refs,status,idempotency_key,requested_by)
                VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'pending',%s,%s)
                """,
                (
                    job_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    Jsonb(dict(scope)),
                    scope_hash,
                    idem,
                    requested_by,
                ),
            )
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="metric.snapshot_set.requested.v2",
                aggregate_pub_id=job_pub_id,
                project_pub_id=project_pub_id,
                subject_hash=scope_hash,
                payload={"correlation_id": job_pub_id, "causation_id": None},
            )
            self._insert_workflow_start(
                connection,
                tenant_pub_id=tenant_pub_id,
                workflow_type="metric_snapshot_set_v2",
                workflow_id=f"metrics-v2:{job_pub_id}",
                task_queue="geo-platform-v2-metrics",
                payload={
                    "tenant_pub_id": tenant_pub_id,
                    "project_pub_id": project_pub_id,
                    "job_pub_id": job_pub_id,
                    "scope": dict(scope),
                    "as_of": datetime.now(UTC).isoformat(),
                    "publication_channel": "shadow",
                    "published_by": requested_by,
                },
            )
        return {
            "job_pub_id": job_pub_id,
            "snapshot_set_pub_id": None,
            "status": "pending",
            "reused": False,
            "scope_hash": scope_hash,
        }

    def request_recompute(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        window: Mapping[str, Any],
        focal_entity_ids: Sequence[str],
        trigger_reason: str,
        idempotency_key: str,
        requested_by: str,
    ) -> dict[str, Any]:
        scope = {
            "project_pub_id": project_pub_id,
            "window": dict(window),
            "focal_entity_ids": sorted(set(focal_entity_ids)),
            "trigger_reason": trigger_reason,
        }
        scope_hash = _canonical_hash(scope)
        idem = _canonical_hash(
            {
                "tenant_pub_id": tenant_pub_id,
                "request_idempotency_hash": _idempotency_hash(idempotency_key),
            }
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            existing = connection.execute(
                """
                SELECT pub_id,project_pub_id,scope_hash,status,failure_codes,
                       snapshot_set_pub_id,created_at,started_at,completed_at
                FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND idempotency_key=%s
                """,
                (tenant_pub_id, idem),
            ).fetchone()
            if existing is not None:
                if existing["scope_hash"] != scope_hash:
                    raise RuntimeError("metrics_v2_idempotency_scope_conflict")
                row = existing
            else:
                job_pub_id = new_pub_id("mrj")
                row = connection.execute(
                    """
                    INSERT INTO analytics.metric_recompute_job_v2
                      (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
                       target_definition_refs,status,idempotency_key,requested_by)
                    VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'pending',%s,%s)
                    RETURNING pub_id,project_pub_id,scope_hash,status,failure_codes,
                              snapshot_set_pub_id,created_at,started_at,completed_at
                    """,
                    (
                        job_pub_id,
                        tenant_pub_id,
                        project_pub_id,
                        Jsonb(scope),
                        scope_hash,
                        idem,
                        requested_by,
                    ),
                ).fetchone()
                assert row is not None
                self._insert_outbox(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    event_type="metric.snapshot_set.requested.v2",
                    aggregate_pub_id=job_pub_id,
                    project_pub_id=project_pub_id,
                    subject_hash=scope_hash,
                    payload={
                        "trigger_reason": trigger_reason,
                        "correlation_id": job_pub_id,
                        "causation_id": None,
                    },
                )
                self._insert_workflow_start(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    workflow_type="metric_snapshot_set_v2",
                    workflow_id=f"metrics-v2:{job_pub_id}",
                    task_queue="geo-platform-v2-metrics",
                    payload={
                        "tenant_pub_id": tenant_pub_id,
                        "project_pub_id": project_pub_id,
                        "job_pub_id": job_pub_id,
                        "scope": scope,
                        "as_of": datetime.now(UTC).isoformat(),
                    },
                )
        codes = list(row.get("failure_codes") or ())
        return {
            "job_pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "status": row["status"],
            "state_reason_codes": codes,
            "failure_code": codes[0] if codes else None,
            "snapshot_set_pub_id": row.get("snapshot_set_pub_id"),
            "selected_decision_pub_id": None,
            "created_at": row["created_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }

    def claim_recompute_job(
        self,
        *,
        tenant_pub_id: str,
        job_pub_id: str,
        workflow_id: str,
        run_id: str,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        """CAS one pending recompute job into running for a metrics worker."""

        now = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, job_pub_id),
            ).fetchone()
            if row is None:
                raise LookupError("metrics_v2_snapshot_job_not_found")
            if row["status"] == "running":
                if row.get("workflow_id") not in {None, "", workflow_id} or row.get(
                    "run_id"
                ) not in {None, "", run_id}:
                    raise RuntimeError("metrics_v2_recompute_job_already_claimed")
                return _recompute_job_projection(row, reused=True)
            if row["status"] == "succeeded":
                return _recompute_job_projection(row, reused=True)
            if row["status"] == "failed":
                if not retry_failed:
                    raise RuntimeError("metrics_v2_recompute_job_failed")
                row = connection.execute(
                    """
                    UPDATE analytics.metric_recompute_job_v2
                    SET status='pending',retry_count=retry_count+1,
                        cursor_state='{}',input_count=0,output_count=0,skipped_count=0,
                        failure_codes='{}',workflow_id=NULL,run_id=NULL,
                        snapshot_set_pub_id=NULL,started_at=NULL,completed_at=NULL
                    WHERE tenant_pub_id=%s AND pub_id=%s AND status='failed'
                    RETURNING *
                    """,
                    (tenant_pub_id, job_pub_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("metrics_v2_recompute_job_retry_conflict")
            claimed = connection.execute(
                """
                UPDATE analytics.metric_recompute_job_v2
                SET status='running',workflow_id=%s,run_id=%s,started_at=%s
                WHERE tenant_pub_id=%s AND pub_id=%s AND status='pending'
                RETURNING *
                """,
                (workflow_id, run_id, now, tenant_pub_id, job_pub_id),
            ).fetchone()
            if claimed is None:
                raise RuntimeError("metrics_v2_recompute_job_claim_conflict")
        return _recompute_job_projection(claimed, reused=False)

    def finish_recompute_job(
        self,
        *,
        tenant_pub_id: str,
        job_pub_id: str,
        status: str,
        snapshot_set_pub_id: str | None = None,
        input_count: int = 0,
        output_count: int = 0,
        skipped_count: int = 0,
        failure_codes: Sequence[str] = (),
        cursor_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """CAS a recompute job to succeeded/failed with audit counters."""

        if status not in {"succeeded", "failed"}:
            raise ValueError("metrics_v2_recompute_terminal_status_invalid")
        if min(input_count, output_count, skipped_count) < 0:
            raise ValueError("metrics_v2_recompute_count_invalid")
        if output_count + skipped_count > input_count:
            raise ValueError("metrics_v2_recompute_count_incoherent")
        if status == "succeeded" and snapshot_set_pub_id is None:
            raise ValueError("metrics_v2_recompute_success_requires_snapshot_set")
        if status == "failed" and snapshot_set_pub_id is not None:
            raise ValueError("metrics_v2_recompute_failure_cannot_select_snapshot_set")
        codes = sorted(set(map(str, failure_codes)))
        now = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM analytics.metric_recompute_job_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, job_pub_id),
            ).fetchone()
            if row is None:
                raise LookupError("metrics_v2_snapshot_job_not_found")
            if row["status"] in {"succeeded", "failed"}:
                same = (
                    row["status"] == status
                    and row.get("snapshot_set_pub_id") == snapshot_set_pub_id
                    and int(row.get("input_count") or 0) == input_count
                    and int(row.get("output_count") or 0) == output_count
                    and int(row.get("skipped_count") or 0) == skipped_count
                    and list(row.get("failure_codes") or ()) == codes
                )
                if not same:
                    raise RuntimeError("metrics_v2_recompute_completion_conflict")
                return _recompute_job_projection(row, reused=True)
            if row["status"] == "pending":
                row = connection.execute(
                    """
                    UPDATE analytics.metric_recompute_job_v2
                    SET status='running',started_at=%s
                    WHERE tenant_pub_id=%s AND pub_id=%s AND status='pending'
                    RETURNING *
                    """,
                    (now, tenant_pub_id, job_pub_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("metrics_v2_recompute_job_claim_conflict")
            project_pub_id = str(row["project_pub_id"])
            if snapshot_set_pub_id is not None:
                snapshot_set = connection.execute(
                    """
                    SELECT state
                    FROM analytics.metric_snapshot_set_v2
                    WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, project_pub_id, snapshot_set_pub_id),
                ).fetchone()
                if snapshot_set is None:
                    raise LookupError("metrics_v2_snapshot_set_not_found")
                if snapshot_set["state"] not in {"ready", "partial"}:
                    raise RuntimeError("metrics_v2_snapshot_set_not_terminal")
            completed = connection.execute(
                """
                UPDATE analytics.metric_recompute_job_v2
                SET status=%s,cursor_state=%s,input_count=%s,output_count=%s,
                    skipped_count=%s,failure_codes=%s,snapshot_set_pub_id=%s,
                    completed_at=%s
                WHERE tenant_pub_id=%s AND pub_id=%s AND status='running'
                RETURNING *
                """,
                (
                    status,
                    Jsonb(dict(cursor_state or {})),
                    input_count,
                    output_count,
                    skipped_count,
                    codes,
                    snapshot_set_pub_id,
                    now,
                    tenant_pub_id,
                    job_pub_id,
                ),
            ).fetchone()
            if completed is None:
                raise RuntimeError("metrics_v2_recompute_completion_cas_failed")
        return _recompute_job_projection(completed, reused=False)

    def publish_snapshot_set_cas(
        self,
        *,
        tenant_pub_id: str,
        set_pub_id: str,
        publication_channel: str,
        expected_generation: int,
        expected_snapshot_set_hash: str,
        published_by: str,
    ) -> dict[str, Any]:
        if publication_channel not in {"shadow", "official"}:
            raise ValueError("metrics_v2_publication_channel_invalid")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            snapshot_set = connection.execute(
                """
                SELECT * FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                FOR SHARE
                """,
                (tenant_pub_id, set_pub_id),
            ).fetchone()
            if snapshot_set is None:
                raise LookupError("metrics_v2_snapshot_set_not_found")
            if snapshot_set["snapshot_set_hash"] != expected_snapshot_set_hash:
                raise RuntimeError("metrics_v2_snapshot_set_hash_mismatch")
            if snapshot_set["state"] not in {"ready", "partial"}:
                raise RuntimeError("metrics_v2_snapshot_set_not_ready")
            if publication_channel == "official":
                if snapshot_set["state"] != "ready":
                    raise RuntimeError("metrics_v2_official_snapshot_not_ready")
                blocked_row = connection.execute(
                    """
                    SELECT count(*)
                    FROM analytics.metric_snapshot_v2 snapshot
                    LEFT JOIN analytics.metric_definition definition
                      ON definition.name=snapshot.metric_name
                     AND definition.version=snapshot.metric_version
                     AND definition.definition_hash=snapshot.metric_definition_hash
                    WHERE snapshot.tenant_pub_id=%s
                      AND snapshot.snapshot_set_pub_id=%s
                      AND (snapshot.state<>'ready' OR definition.status<>'published'
                           OR definition.definition_hash IS NULL)
                    """,
                    (tenant_pub_id, set_pub_id),
                ).fetchone()
                assert blocked_row is not None
                blocked = blocked_row["count"]
                if blocked:
                    raise RuntimeError("metrics_v2_official_snapshot_not_ready")
                dependency_row = connection.execute(
                    """
                    SELECT count(DISTINCT dependency.decision_pub_id) AS blocked
                    FROM analytics.metric_contribution_v2 contribution
                    JOIN analytics.metric_snapshot_v2 snapshot
                      ON snapshot.tenant_pub_id=contribution.tenant_pub_id
                     AND snapshot.project_pub_id=contribution.project_pub_id
                     AND snapshot.pub_id=contribution.snapshot_pub_id
                    CROSS JOIN LATERAL
                      unnest(contribution.supporting_decision_pub_ids)
                        AS dependency(decision_pub_id)
                    LEFT JOIN analytics.semantic_decision_record_v2 decision
                      ON decision.tenant_pub_id=contribution.tenant_pub_id
                     AND decision.project_pub_id=contribution.project_pub_id
                     AND decision.pub_id=dependency.decision_pub_id
                    LEFT JOIN analytics.semantic_decision_task_definition_v2 task
                      ON task.name=decision.task_name
                     AND task.version=decision.task_version
                     AND task.definition_hash=decision.task_definition_hash
                    LEFT JOIN analytics.semantic_judge_policy_v2 policy
                      ON policy.policy_hash=decision.judge_policy_hash
                    WHERE contribution.tenant_pub_id=%s
                      AND snapshot.tenant_pub_id=%s
                      AND snapshot.snapshot_set_pub_id=%s
                      AND (
                        decision.pub_id IS NULL OR decision.status<>'accepted'
                        OR task.status<>'published' OR task.definition_hash IS NULL
                        OR decision.rubric_ref IS DISTINCT FROM task.rubric_ref
                        OR decision.rubric_hash IS DISTINCT FROM task.rubric_hash
                        OR policy.status<>'published' OR policy.policy_hash IS NULL
                        OR NOT (
                          policy.compatible_task_refs ?
                          (decision.task_name || '@' || decision.task_version)
                        )
                        OR (
                          policy.calibration_artifact_hash IS NOT NULL
                          AND NOT (
                            policy.calibration_artifact_hash =
                            ANY(snapshot.calibration_artifact_hashes)
                          )
                        )
                      )
                    """,
                    (tenant_pub_id, tenant_pub_id, set_pub_id),
                ).fetchone()
                assert dependency_row is not None
                if dependency_row["blocked"]:
                    raise RuntimeError("metrics_v2_official_dependency_not_published")
                required_task_row = connection.execute(
                    """
                    WITH required_task AS (
                      SELECT DISTINCT required.task_ref
                      FROM analytics.metric_snapshot_v2 snapshot
                      JOIN analytics.metric_definition definition
                        ON definition.name=snapshot.metric_name
                       AND definition.version=snapshot.metric_version
                       AND definition.definition_hash=snapshot.metric_definition_hash
                      CROSS JOIN LATERAL (
                        SELECT value AS task_ref
                        FROM jsonb_array_elements_text(
                          CASE
                            WHEN jsonb_typeof(definition.decision_task_refs)='array'
                              THEN definition.decision_task_refs
                            ELSE '[]'::jsonb
                          END
                        ) value
                        UNION
                        SELECT value->>'task_ref' AS task_ref
                        FROM jsonb_array_elements(
                          CASE
                            WHEN jsonb_typeof(
                              definition.required_semantic_capabilities
                            )='array'
                              THEN definition.required_semantic_capabilities
                            ELSE '[]'::jsonb
                          END
                        ) value
                      ) required
                      WHERE snapshot.tenant_pub_id=%s
                        AND snapshot.snapshot_set_pub_id=%s
                        AND nullif(required.task_ref,'') IS NOT NULL
                    )
                    SELECT count(*) AS blocked
                    FROM required_task required
                    LEFT JOIN analytics.semantic_decision_task_definition_v2 task
                      ON task.name || '@' || task.version=required.task_ref
                    WHERE task.definition_hash IS NULL OR task.status<>'published'
                    """,
                    (tenant_pub_id, set_pub_id),
                ).fetchone()
                assert required_task_row is not None
                if required_task_row["blocked"]:
                    raise RuntimeError("metrics_v2_official_dependency_not_published")
                missing_required_decision_row = connection.execute(
                    """
                    SELECT count(*) AS blocked
                    FROM analytics.metric_contribution_v2 contribution
                    JOIN analytics.metric_snapshot_v2 snapshot
                      ON snapshot.tenant_pub_id=contribution.tenant_pub_id
                     AND snapshot.project_pub_id=contribution.project_pub_id
                     AND snapshot.pub_id=contribution.snapshot_pub_id
                    JOIN analytics.metric_definition definition
                      ON definition.name=snapshot.metric_name
                     AND definition.version=snapshot.metric_version
                     AND definition.definition_hash=snapshot.metric_definition_hash
                    CROSS JOIN LATERAL (
                      SELECT value AS task_ref
                      FROM jsonb_array_elements_text(
                        CASE
                          WHEN jsonb_typeof(definition.decision_task_refs)='array'
                            THEN definition.decision_task_refs
                          ELSE '[]'::jsonb
                        END
                      ) value
                      UNION
                      SELECT value->>'task_ref' AS task_ref
                      FROM jsonb_array_elements(
                        CASE
                          WHEN jsonb_typeof(
                            definition.required_semantic_capabilities
                          )='array'
                            THEN definition.required_semantic_capabilities
                          ELSE '[]'::jsonb
                        END
                      ) value
                    ) required
                    WHERE contribution.tenant_pub_id=%s
                      AND snapshot.snapshot_set_pub_id=%s
                      AND contribution.eligibility_status IN (
                        'included_hit','included_miss'
                      )
                      AND nullif(required.task_ref,'') IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1
                        FROM unnest(contribution.supporting_decision_pub_ids)
                          support(decision_pub_id)
                        JOIN analytics.semantic_decision_record_v2 decision
                          ON decision.tenant_pub_id=contribution.tenant_pub_id
                         AND decision.project_pub_id=contribution.project_pub_id
                         AND decision.pub_id=support.decision_pub_id
                        WHERE decision.task_name || '@' || decision.task_version =
                                required.task_ref
                          AND decision.status='accepted'
                      )
                    """,
                    (tenant_pub_id, set_pub_id),
                ).fetchone()
                assert missing_required_decision_row is not None
                if missing_required_decision_row["blocked"]:
                    raise RuntimeError("metrics_v2_official_dependency_not_published")
            pointer = connection.execute(
                """
                SELECT * FROM analytics.metric_publication_v2
                WHERE tenant_pub_id=%s AND scope_hash=%s AND publication_channel=%s
                FOR UPDATE
                """,
                (tenant_pub_id, snapshot_set["scope_hash"], publication_channel),
            ).fetchone()
            now = datetime.now(UTC)
            if pointer is None:
                if expected_generation != 0:
                    raise RuntimeError("metrics_v2_publication_generation_conflict")
                generation = 1
                row = connection.execute(
                    """
                    INSERT INTO analytics.metric_publication_v2
                      (pub_id,tenant_pub_id,project_pub_id,scope_hash,snapshot_set_pub_id,
                       publication_channel,generation,published_by,published_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        new_pub_id("mpu"),
                        tenant_pub_id,
                        snapshot_set["project_pub_id"],
                        snapshot_set["scope_hash"],
                        set_pub_id,
                        publication_channel,
                        generation,
                        published_by,
                        now,
                    ),
                ).fetchone()
            else:
                if int(pointer["generation"]) != expected_generation:
                    raise RuntimeError("metrics_v2_publication_generation_conflict")
                if pointer["snapshot_set_pub_id"] == set_pub_id:
                    row = pointer
                else:
                    row = connection.execute(
                        """
                        UPDATE analytics.metric_publication_v2
                        SET snapshot_set_pub_id=%s,generation=generation+1,
                            published_by=%s,published_at=%s
                        WHERE tenant_pub_id=%s AND pub_id=%s AND generation=%s
                        RETURNING *
                        """,
                        (
                            set_pub_id,
                            published_by,
                            now,
                            tenant_pub_id,
                            pointer["pub_id"],
                            expected_generation,
                        ),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("metrics_v2_publication_generation_conflict")
            assert row is not None
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="metric.snapshot_set.published.v2",
                aggregate_pub_id=set_pub_id,
                project_pub_id=str(snapshot_set["project_pub_id"]),
                subject_hash=str(snapshot_set["snapshot_set_hash"]),
                payload={
                    "publication_channel": publication_channel,
                    "generation": row["generation"],
                    "correlation_id": set_pub_id,
                    "causation_id": None,
                },
            )
        return {
            "project_pub_id": row["project_pub_id"],
            "scope_hash": row["scope_hash"],
            "snapshot_set_pub_id": row["snapshot_set_pub_id"],
            "publication_channel": row["publication_channel"],
            "generation": int(row["generation"]),
            "published_at": row["published_at"],
        }

    @staticmethod
    def _insert_mapping(
        connection: Connection[dict[str, Any]], table: str, values: Mapping[str, Any]
    ) -> None:
        allowed = _INSERT_COLUMNS[table]
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"metrics_v2_unknown_{table}_columns:{','.join(sorted(unknown))}")
        ordered = sorted(values)
        if not ordered:
            raise ValueError(f"metrics_v2_empty_{table}_row")
        adapted: list[Any] = []
        for column in ordered:
            value = values[column]
            if column in _JSON_COLUMNS[table] and (
                value is not None or column in _NON_NULL_JSON_COLUMNS[table]
            ):
                adapted.append(Jsonb(value))
            elif column in _ARRAY_COLUMNS[table] and isinstance(value, tuple):
                # psycopg adapts tuples as PostgreSQL composite records, not
                # arrays. Domain models intentionally use immutable tuples.
                adapted.append(list(value))
            else:
                adapted.append(value)
        statement = sql.SQL("INSERT INTO analytics.{} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, ordered)),
            sql.SQL(",").join(sql.Placeholder() for _ in ordered),
        )
        connection.execute(statement, adapted)

    @classmethod
    def _insert_semantic_manifest_rows(
        cls,
        connection: Connection[dict[str, Any]],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        manifest: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        manifest_values = dict(manifest)
        manifest_values.setdefault("pub_id", new_pub_id("asm"))
        manifest_values["tenant_pub_id"] = tenant_pub_id
        manifest_values["project_pub_id"] = project_pub_id
        manifest_values.setdefault("event_schema_version", "answer-semantic-events-v2")
        manifest_values["event_count"] = len(events)
        evidenced_count = sum(
            event.get("answer_text_start") is not None and event.get("answer_text_end") is not None
            for event in events
        )
        supplied_evidenced = manifest_values.get("evidenced_event_count")
        if supplied_evidenced is not None and int(supplied_evidenced) != evidenced_count:
            raise ValueError("metrics_v2_evidenced_event_count_mismatch")
        manifest_values["evidenced_event_count"] = evidenced_count
        if manifest_values.get("status") == "failed":
            if events:
                raise ValueError("metrics_v2_failed_manifest_cannot_have_events")
            manifest_values["event_set_hash"] = None
        elif not manifest_values.get("event_set_hash"):
            manifest_values["event_set_hash"] = _canonical_hash(
                sorted(
                    (
                        str(event.get("pub_id") or ""),
                        str(event.get("event_fingerprint") or ""),
                    )
                    for event in events
                )
            )
        cls._insert_mapping(connection, "answer_semantic_manifest_v2", manifest_values)
        event_ids: list[str] = []
        for index, event in enumerate(events):
            event_values = dict(event)
            event_values.setdefault("pub_id", new_pub_id("ase"))
            event_values.setdefault("event_index", index)
            for field, expected in (
                ("tenant_pub_id", tenant_pub_id),
                ("project_pub_id", project_pub_id),
                ("answer_pub_id", manifest_values["answer_pub_id"]),
                ("semantic_manifest_pub_id", manifest_values["pub_id"]),
            ):
                supplied = event_values.get(field)
                if supplied is not None and supplied != expected:
                    raise RuntimeError(f"metrics_v2_semantic_event_scope_mismatch:{field}")
                event_values[field] = expected
            cls._insert_mapping(connection, "answer_semantic_event_v2", event_values)
            event_ids.append(str(event_values["pub_id"]))
        return {
            "semantic_manifest_pub_id": manifest_values["pub_id"],
            "event_set_hash": manifest_values.get("event_set_hash"),
            "event_pub_ids": event_ids,
        }

    def persist_query_context_atomic(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fact: Mapping[str, Any],
        exposures: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist one immutable query fact and all focal exposures together."""

        fact_values = dict(fact)
        fact_values.setdefault("pub_id", new_pub_id("qcf"))
        fact_values["tenant_pub_id"] = tenant_pub_id
        fact_values["project_pub_id"] = project_pub_id
        fact_hash = str(fact_values.get("fact_hash") or "")
        if len(fact_hash) != 64:
            raise ValueError("metrics_v2_query_context_fact_hash_required")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"query-context:{tenant_pub_id}:{fact_hash}",),
            )
            existing = connection.execute(
                """
                SELECT pub_id,fact_hash FROM analytics.query_context_fact_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (pub_id=%s OR fact_hash=%s)
                ORDER BY (pub_id=%s) DESC LIMIT 1
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    fact_values["pub_id"],
                    fact_hash,
                    fact_values["pub_id"],
                ),
            ).fetchone()
            if existing is not None:
                if existing["fact_hash"] != fact_hash:
                    raise RuntimeError("metrics_v2_query_context_content_conflict")
                return {
                    "query_context_fact_pub_id": existing["pub_id"],
                    "fact_hash": existing["fact_hash"],
                    "reused": True,
                }
            self._insert_mapping(connection, "query_context_fact_v2", fact_values)
            exposure_ids: list[str] = []
            focal_ids: set[str] = set()
            for exposure in exposures:
                exposure_values = dict(exposure)
                exposure_values.setdefault("pub_id", new_pub_id("qef"))
                exposure_values["tenant_pub_id"] = tenant_pub_id
                exposure_values["project_pub_id"] = project_pub_id
                exposure_values["query_context_fact_pub_id"] = fact_values["pub_id"]
                focal_id = str(exposure_values["focal_entity_id"])
                if focal_id in focal_ids:
                    raise ValueError("metrics_v2_duplicate_query_exposure")
                focal_ids.add(focal_id)
                self._insert_mapping(connection, "query_entity_exposure_fact_v2", exposure_values)
                exposure_ids.append(str(exposure_values["pub_id"]))
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="query.context.classified.v2",
                aggregate_pub_id=str(fact_values["pub_id"]),
                project_pub_id=project_pub_id,
                subject_hash=fact_hash,
                payload={
                    "query_key": fact_values["query_key"],
                    "correlation_id": fact_values["pub_id"],
                    "causation_id": fact_values.get("supersedes_pub_id"),
                },
            )
        return {
            "query_context_fact_pub_id": fact_values["pub_id"],
            "fact_hash": fact_hash,
            "exposure_pub_ids": exposure_ids,
            "reused": False,
        }

    def persist_semantic_manifest_atomic(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        manifest: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist a manifest and its complete event set in one transaction."""

        manifest_values = dict(manifest)
        manifest_values.setdefault("pub_id", new_pub_id("asm"))
        identity = _canonical_hash(
            {
                "tenant_pub_id": tenant_pub_id,
                "answer_pub_id": manifest_values.get("answer_pub_id"),
                "query_context_fact_pub_id": manifest_values.get("query_context_fact_pub_id"),
                "input_hash": manifest_values.get("input_hash"),
                "extractor_bundle_hash": manifest_values.get("extractor_bundle_hash"),
                "decision_task_bundle_hash": manifest_values.get("decision_task_bundle_hash"),
                "entity_dictionary_hash": manifest_values.get("entity_dictionary_hash"),
            }
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"semantic-manifest:{identity}",),
            )
            existing = connection.execute(
                """
                SELECT pub_id,decision_set_hash,event_set_hash,event_count
                FROM analytics.answer_semantic_manifest_v2
                WHERE tenant_pub_id=%s AND
                  (pub_id=%s OR
                   (answer_pub_id=%s AND input_hash=%s AND extractor_bundle_hash=%s
                    AND decision_task_bundle_hash=%s AND entity_dictionary_hash=%s
                    AND decision_set_hash=%s))
                ORDER BY (pub_id=%s) DESC LIMIT 1
                """,
                (
                    tenant_pub_id,
                    manifest_values["pub_id"],
                    manifest_values.get("answer_pub_id"),
                    manifest_values.get("input_hash"),
                    manifest_values.get("extractor_bundle_hash"),
                    manifest_values.get("decision_task_bundle_hash"),
                    manifest_values.get("entity_dictionary_hash"),
                    manifest_values.get("decision_set_hash"),
                    manifest_values["pub_id"],
                ),
            ).fetchone()
            if existing is not None:
                expected_event_hash = (
                    None
                    if manifest_values.get("status") == "failed"
                    else manifest_values.get("event_set_hash")
                )
                if (
                    existing["decision_set_hash"] != manifest_values.get("decision_set_hash")
                    or existing["event_set_hash"] != expected_event_hash
                    or int(existing["event_count"]) != len(events)
                ):
                    raise RuntimeError("metrics_v2_semantic_manifest_content_conflict")
                return {
                    "semantic_manifest_pub_id": existing["pub_id"],
                    "event_set_hash": existing["event_set_hash"],
                    "reused": True,
                }
            result = self._insert_semantic_manifest_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                manifest=manifest_values,
                events=events,
            )
            status = str(manifest_values.get("status"))
            event_type = _semantic_manifest_event_type(status)
            subject_hash = str(
                result.get("event_set_hash") or manifest_values.get("decision_set_hash")
            )
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type=event_type,
                aggregate_pub_id=str(result["semantic_manifest_pub_id"]),
                project_pub_id=project_pub_id,
                subject_hash=subject_hash,
                payload={
                    "answer_pub_id": manifest_values.get("answer_pub_id"),
                    "correlation_id": manifest_values.get("answer_pub_id"),
                    "causation_id": manifest_values.get("supersedes_pub_id"),
                },
            )
        return {**result, "reused": False}

    def persist_metric_evaluations(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        evaluations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Idempotently persist reusable answer × metric evaluation facts."""

        inserted = 0
        reused = 0
        pub_ids: list[str] = []
        ordered = sorted(
            (dict(item) for item in evaluations),
            key=lambda item: (
                str(item.get("answer_pub_id")),
                str(item.get("focal_entity_id")),
                str(item.get("metric_name")),
                str(item.get("metric_version")),
            ),
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            for evaluation in ordered:
                evaluation_hash = str(evaluation.get("evaluation_hash") or "")
                if len(evaluation_hash) != 64:
                    raise ValueError("metrics_v2_evaluation_hash_required")
                row = dict(evaluation)
                row["pub_id"] = str(row.pop("pub_id", "") or f"mev_{evaluation_hash[:26]}")
                row["tenant_pub_id"] = tenant_pub_id
                row["project_pub_id"] = project_pub_id
                row["semantic_decision_pub_ids"] = list(
                    row.pop("supporting_decision_pub_ids", ())
                    or row.get("semantic_decision_pub_ids")
                    or ()
                )
                row.setdefault("supporting_event_pub_ids", [])
                existing = connection.execute(
                    """
                    SELECT pub_id,evaluation_hash
                    FROM analytics.metric_evaluation_v2
                    WHERE tenant_pub_id=%s AND answer_pub_id=%s
                      AND focal_entity_id=%s AND metric_name=%s AND metric_version=%s
                      AND query_context_fact_pub_id=%s AND semantic_manifest_pub_id=%s
                      AND semantic_decision_set_hash=%s
                    """,
                    (
                        tenant_pub_id,
                        row["answer_pub_id"],
                        row["focal_entity_id"],
                        row["metric_name"],
                        row["metric_version"],
                        row["query_context_fact_pub_id"],
                        row["semantic_manifest_pub_id"],
                        row["semantic_decision_set_hash"],
                    ),
                ).fetchone()
                if existing is not None:
                    if existing["evaluation_hash"] != evaluation_hash:
                        raise RuntimeError("metrics_v2_evaluation_content_conflict")
                    reused += 1
                    pub_ids.append(str(existing["pub_id"]))
                    continue
                self._insert_mapping(connection, "metric_evaluation_v2", row)
                inserted += 1
                pub_ids.append(str(row["pub_id"]))
        return {
            "evaluation_pub_ids": pub_ids,
            "inserted_count": inserted,
            "reused_count": reused,
        }

    @staticmethod
    def _definition_documents(
        connection: Connection[dict[str, Any]],
        definition_refs: Sequence[object],
    ) -> tuple[list[dict[str, Any]], str]:
        requested: dict[tuple[str, str], str | None] = {}
        for raw_ref in definition_refs:
            if isinstance(raw_ref, Mapping):
                name = str(raw_ref.get("name") or raw_ref.get("metric_name") or "")
                version = str(raw_ref.get("version") or raw_ref.get("metric_version") or "")
                expected_hash = raw_ref.get("definition_hash") or raw_ref.get(
                    "metric_definition_hash"
                )
            else:
                name, separator, version = str(raw_ref).partition("@")
                if not separator:
                    raise ValueError("metrics_v2_definition_ref_invalid")
                expected_hash = None
            if not name or not version:
                raise ValueError("metrics_v2_definition_ref_invalid")
            requested[(name, version)] = str(expected_hash) if expected_hash is not None else None
        rows = connection.execute(
            """
            SELECT name,version,definition,definition_hash,status
            FROM analytics.metric_definition
            WHERE status IN ('experimental','published')
              AND definition_hash IS NOT NULL
            ORDER BY name,version
            """
        ).fetchall()
        if requested:
            rows = [row for row in rows if (row["name"], row["version"]) in requested]
            missing = sorted(set(requested) - {(row["name"], row["version"]) for row in rows})
            if missing:
                raise LookupError(
                    "metrics_v2_definition_not_available:"
                    + ",".join(f"{name}@{version}" for name, version in missing)
                )
        documents: list[dict[str, Any]] = []
        identities: list[dict[str, str]] = []
        for row in rows:
            document = _json_object(row["definition"])
            for field, expected in (
                ("name", str(row["name"])),
                ("version", str(row["version"])),
                ("definition_hash", str(row["definition_hash"])),
            ):
                supplied = document.get(field)
                if supplied is not None and supplied != expected:
                    raise RuntimeError(f"metrics_v2_definition_document_mismatch:{field}")
                document[field] = expected
            explicit_hash = requested.get((row["name"], row["version"]))
            if explicit_hash is not None and explicit_hash != row["definition_hash"]:
                raise RuntimeError("metrics_v2_definition_hash_mismatch")
            document["status"] = row["status"]
            documents.append(document)
            identities.append(
                {
                    "name": str(row["name"]),
                    "version": str(row["version"]),
                    "definition_hash": str(row["definition_hash"]),
                }
            )
        return documents, _canonical_hash(identities)

    def load_snapshot_build_inputs(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        scope: Mapping[str, Any],
        as_of: str,
        definition_refs: Sequence[object] = (),
    ) -> dict[str, Any]:
        """Freeze reference-only snapshot inputs in one repeatable-read view."""

        as_of_at = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if as_of_at.tzinfo is None or as_of_at.utcoffset() is None:
            raise ValueError("metrics_v2_as_of_timezone_required")
        window = _json_object(scope.get("window"))
        raw_start = scope.get("window_start") or window.get("start")
        raw_end = scope.get("window_end") or window.get("end")
        if raw_start is None or raw_end is None:
            raise ValueError("metrics_v2_scope_window_required")

        def boundary(value: object, *, end: bool) -> datetime:
            raw = str(value)
            if len(raw) == 10:
                result = datetime.combine(date.fromisoformat(raw), datetime.min.time(), UTC)
                return result + timedelta(days=1) if end else result
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if result.tzinfo is None or result.utcoffset() is None:
                raise ValueError("metrics_v2_window_timezone_required")
            return result

        window_start = boundary(raw_start, end=False)
        window_end = boundary(raw_end, end=True)
        if window_start >= window_end or window_end > as_of_at + timedelta(days=1):
            raise ValueError("metrics_v2_scope_window_invalid")
        filters = _json_object(scope.get("filters"))
        models = sorted(set(map(str, filters.get("model") or ())))
        regions = sorted(set(map(str, filters.get("region") or ())))
        modes = sorted(set(map(str, filters.get("mode") or ())))
        focal_entity_ids = sorted(set(map(str, scope.get("focal_entity_ids") or ())))
        if not focal_entity_ids:
            raise ValueError("metrics_v2_focal_entity_ids_required")
        base_parameters = (
            tenant_pub_id,
            project_pub_id,
            window_start,
            window_end,
            as_of_at,
            models,
            regions,
            modes,
        )
        base_predicate = """
          answer.tenant_pub_id=%s AND answer.project_pub_id=%s
          AND answer.capture_time >= %s AND answer.capture_time < %s
          AND answer.capture_time <= %s
          AND (cardinality(%s::text[])=0 OR answer.model=ANY(%s::text[]))
          AND (cardinality(%s::text[])=0 OR answer.region=ANY(%s::text[]))
          AND (cardinality(%s::text[])=0 OR answer.mode=ANY(%s::text[]))
        """
        expanded_parameters = (
            *base_parameters[:5],
            models,
            models,
            regions,
            regions,
            modes,
            modes,
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            # tenant_connection sets the RLS GUC in its initial transaction;
            # restart once so the complete input read has a repeatable snapshot.
            connection.commit()
            connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant_pub_id,))
            definition_documents, definition_set_hash = self._definition_documents(
                connection, definition_refs
            )
            count_row = connection.execute(
                f"""
                SELECT count(*) AS total_count,
                       count(*) FILTER (WHERE answer.eligible) AS eligible_count
                FROM analytics.answer answer WHERE {base_predicate}
                """,
                expanded_parameters,
            ).fetchone()
            assert count_row is not None
            rows = connection.execute(
                f"""
                SELECT answer.pub_id AS answer_pub_id,answer.query_pub_id,
                       answer.model,answer.region,answer.mode,answer.channel,
                       answer.eligible,answer.degraded,answer.capture_time,
                       COALESCE(answer.response_hash,
                         encode(public.digest(answer.response_text,'sha256'),'hex'))
                         AS answer_text_hash,
                       context.pub_id AS context_pub_id,context.query_key,
                       context.query_text_hash,context.primary_lens,
                       context.analysis_lenses,context.requested_operations,
                       context.query_subtypes,context.detected_entity_ids,
                       context.brand_structure_type,context.classification_state,
                       context.classifier_version,context.decision_task_bundle_hash,
                       context.entity_dictionary_hash,context.classification_source,
                       context.derivation_method,context.decision_record_pub_ids
                         AS context_decision_pub_ids,
                       context.review_status AS context_review_status,
                       context.override_reason AS context_override_reason,
                       manifest.pub_id AS manifest_pub_id,manifest.status AS manifest_status,
                       manifest.capability_statuses,manifest.decision_record_pub_ids,
                       manifest.decision_set_hash,manifest.event_set_hash,
                       manifest.failure_code AS manifest_failure_code
                FROM analytics.answer answer
                JOIN LATERAL (
                  SELECT candidate.* FROM analytics.answer_semantic_manifest_v2 candidate
                  WHERE candidate.tenant_pub_id=answer.tenant_pub_id
                    AND candidate.project_pub_id=answer.project_pub_id
                    AND candidate.created_at <= %s
                    AND candidate.answer_pub_id=answer.pub_id
                  ORDER BY candidate.created_at DESC,candidate.pub_id DESC LIMIT 1
                ) manifest ON true
                JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=manifest.tenant_pub_id
                 AND context.project_pub_id=manifest.project_pub_id
                 AND context.pub_id=manifest.query_context_fact_pub_id
                 AND context.created_at <= %s
                WHERE {base_predicate}
                ORDER BY answer.capture_time,answer.pub_id
                """,
                (as_of_at, as_of_at, *expanded_parameters),
            ).fetchall()
            context_count_row = connection.execute(
                f"""
                SELECT count(DISTINCT answer.pub_id) AS context_count
                FROM analytics.answer answer
                JOIN analytics.answer_semantic_manifest_v2 manifest
                  ON manifest.tenant_pub_id=answer.tenant_pub_id
                 AND manifest.project_pub_id=answer.project_pub_id
                 AND manifest.answer_pub_id=answer.pub_id
                 AND manifest.created_at <= %s
                JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=manifest.tenant_pub_id
                 AND context.project_pub_id=manifest.project_pub_id
                 AND context.pub_id=manifest.query_context_fact_pub_id
                 AND context.created_at <= %s
                WHERE {base_predicate}
                """,
                (as_of_at, as_of_at, *expanded_parameters),
            ).fetchone()
            assert context_count_row is not None
            context_ids = sorted({str(row["context_pub_id"]) for row in rows})
            manifest_ids = sorted({str(row["manifest_pub_id"]) for row in rows})
            decision_ids = sorted(
                {
                    str(item)
                    for row in rows
                    for item in (
                        *(row.get("context_decision_pub_ids") or ()),
                        *(row.get("decision_record_pub_ids") or ()),
                    )
                }
            )
            exposure_rows = (
                connection.execute(
                    """
                    SELECT query_context_fact_pub_id,focal_entity_id,exposure_role
                    FROM analytics.query_entity_exposure_fact_v2
                    WHERE tenant_pub_id=%s
                      AND query_context_fact_pub_id=ANY(%s::text[])
                      AND focal_entity_id=ANY(%s::text[])
                    """,
                    (tenant_pub_id, context_ids, focal_entity_ids),
                ).fetchall()
                if context_ids
                else []
            )
            event_rows = (
                connection.execute(
                    """
                    SELECT pub_id,tenant_pub_id,project_pub_id,answer_pub_id,
                           semantic_manifest_pub_id,event_index,event_type,
                           subject_entity_id,object_entity_id,event_value,qualifiers,
                           answer_text_start,answer_text_end,offset_unit,
                           answer_excerpt_hash,extractor_version,scorer_version,
                           derivation_method,decision_record_pub_ids,
                           decision_policy_version,provenance_hash,
                           calibrated_confidence,confidence_state,review_status,
                           override_reason,event_fingerprint,created_at
                    FROM analytics.answer_semantic_event_v2
                    WHERE tenant_pub_id=%s
                      AND semantic_manifest_pub_id=ANY(%s::text[])
                    ORDER BY semantic_manifest_pub_id,event_index,pub_id
                    """,
                    (tenant_pub_id, manifest_ids),
                ).fetchall()
                if manifest_ids
                else []
            )
            decision_rows = (
                connection.execute(
                    """
                    WITH RECURSIVE lineage AS (
                      SELECT decision.pub_id AS root_pub_id,decision.pub_id,
                             decision.task_name,decision.task_version,decision.status,
                             decision.result,decision.method,
                             decision.calibrated_confidence,decision.evidence_refs,
                             decision.decision_hash,decision.reason_codes,
                             decision.created_at
                      FROM analytics.semantic_decision_record_v2 decision
                      WHERE decision.tenant_pub_id=%s AND decision.project_pub_id=%s
                        AND decision.pub_id=ANY(%s::text[])
                      UNION ALL
                      SELECT lineage.root_pub_id,successor.pub_id,
                             successor.task_name,successor.task_version,successor.status,
                             successor.result,successor.method,
                             successor.calibrated_confidence,successor.evidence_refs,
                             successor.decision_hash,successor.reason_codes,
                             successor.created_at
                      FROM lineage
                      JOIN analytics.semantic_decision_record_v2 successor
                        ON successor.tenant_pub_id=%s
                       AND successor.project_pub_id=%s
                       AND successor.supersedes_pub_id=lineage.pub_id
                       AND successor.created_at <= %s
                    ), ranked AS (
                      SELECT lineage.*,
                             row_number() OVER (
                               PARTITION BY root_pub_id
                               ORDER BY created_at DESC,pub_id DESC
                             ) AS leaf_rank
                      FROM lineage
                    )
                    SELECT root_pub_id,pub_id,task_name,task_version,status,result,method,
                           calibrated_confidence,evidence_refs,decision_hash,reason_codes
                    FROM ranked WHERE leaf_rank=1
                    ORDER BY root_pub_id
                    """,
                    (
                        tenant_pub_id,
                        project_pub_id,
                        decision_ids,
                        tenant_pub_id,
                        project_pub_id,
                        as_of_at,
                    ),
                ).fetchall()
                if decision_ids
                else []
            )

        exposures = {
            (str(row["query_context_fact_pub_id"]), str(row["focal_entity_id"])): str(
                row["exposure_role"]
            )
            for row in exposure_rows
        }
        events_by_manifest: dict[str, list[dict[str, Any]]] = {}
        for row in event_rows:
            events_by_manifest.setdefault(str(row["semantic_manifest_pub_id"]), []).append(
                _wire_value(dict(row))
            )
        decisions_by_id = {str(row["root_pub_id"]): row for row in decision_rows}
        subjects: list[dict[str, Any]] = []
        coordinates: dict[str, dict[str, str]] = {}
        planned_counts: dict[str, int] = {}
        for row in rows:
            detected = set(map(str, row.get("detected_entity_ids") or ()))
            context_document = {
                "query_key": row["query_key"],
                "query_text_hash": row["query_text_hash"],
                "primary_lens": row.get("primary_lens"),
                "analysis_lenses": list(row.get("analysis_lenses") or ()),
                "requested_operations": list(row.get("requested_operations") or ()),
                "query_subtypes": list(row.get("query_subtypes") or ()),
                "detected_entity_ids": sorted(detected),
                "brand_structure_type": row["brand_structure_type"],
                "classification_state": row["classification_state"],
                "classifier_version": row["classifier_version"],
                "decision_task_bundle_hash": row["decision_task_bundle_hash"],
                "entity_dictionary_hash": row["entity_dictionary_hash"],
                "classification_source": row["classification_source"],
                "derivation_method": row["derivation_method"],
                "decision_record_pub_ids": list(row.get("context_decision_pub_ids") or ()),
                "review_status": row["context_review_status"],
                "override_reason": row.get("context_override_reason"),
            }
            raw_capabilities = _json_object(row.get("capability_statuses"))
            capability_statuses = {
                name: (str(value.get("status")) if isinstance(value, Mapping) else str(value))
                for name, value in raw_capabilities.items()
            }
            decisions: dict[str, dict[str, Any]] = {}
            bound_decision_ids = tuple(
                dict.fromkeys(
                    (
                        *(row.get("context_decision_pub_ids") or ()),
                        *(row.get("decision_record_pub_ids") or ()),
                    )
                )
            )
            for decision_id in bound_decision_ids:
                decision = decisions_by_id.get(str(decision_id))
                if decision is None:
                    continue
                task_ref = f"{decision['task_name']}@{decision['task_version']}"
                decisions[task_ref] = {
                    "status": decision["status"],
                    "value": _wire_value(decision["result"]),
                    "decision_pub_id": decision["pub_id"],
                    "method": decision["method"],
                    "reason_codes": list(decision.get("reason_codes") or ()),
                    "calibrated": decision.get("calibrated_confidence") is not None
                    or decision["method"] == "human",
                    "policy_matches": True,
                    "evidence_ready": True,
                }
            answer_pub_id = str(row["answer_pub_id"])
            design_cell_key = _canonical_hash(
                {
                    "model": row.get("model") or "",
                    "region": row.get("region") or "",
                    "mode": row.get("mode") or "",
                }
            )
            coordinates[answer_pub_id] = {
                "design_cell_key": design_cell_key,
                "model": str(row.get("model") or ""),
                "region": str(row.get("region") or ""),
                "mode": str(row.get("mode") or ""),
            }
            planned_key = f"{row['query_key']}\u001f{design_cell_key}"
            planned_counts[planned_key] = planned_counts.get(planned_key, 0) + 1
            for focal_entity_id in focal_entity_ids:
                exposure_role = exposures.get((str(row["context_pub_id"]), focal_entity_id))
                if exposure_role is None:
                    if row["classification_state"] != "ready":
                        exposure_role = "unknown"
                    elif not detected:
                        exposure_role = "brand_neutral"
                    elif focal_entity_id not in detected:
                        exposure_role = "other_brand_named"
                    elif len(detected) == 1:
                        exposure_role = "focal_named_only"
                    else:
                        exposure_role = "focal_named_with_others"
                subjects.append(
                    {
                        "answer_pub_id": answer_pub_id,
                        "query_context": context_document,
                        "focal_entity_id": focal_entity_id,
                        "exposure_role": exposure_role,
                        "collection_eligible": bool(row["eligible"]),
                        "capability_statuses": capability_statuses,
                        "events": events_by_manifest.get(str(row["manifest_pub_id"]), []),
                        "decisions": decisions,
                        "answer_fields": {
                            "manifest_status": row["manifest_status"],
                            "capture_time": _wire_value(row["capture_time"]),
                            "answer_text_hash": row["answer_text_hash"],
                            "eligible": bool(row["eligible"]),
                            "degraded": bool(row["degraded"]),
                            "channel": row.get("channel"),
                        },
                        "query_context_fact_pub_id": row["context_pub_id"],
                        "semantic_manifest_pub_id": row["manifest_pub_id"],
                        "semantic_decision_set_hash": row["decision_set_hash"],
                        "event_invariants_valid": row["manifest_status"]
                        not in {"failed", "review_required"},
                        "evidence_spans_valid": True,
                        "evidence_retrieval_ready": row.get("manifest_failure_code")
                        != "evidence_retrieval_failed",
                    }
                )
        total_count = int(count_row["total_count"])
        eligible_count = int(count_row["eligible_count"])
        context_count = int(context_count_row["context_count"])
        semantic_count = len(rows)
        dependency_bundle = {
            "metric_definition_set_hash": definition_set_hash,
            "answer_set_hash": _canonical_hash(
                [
                    (row["answer_pub_id"], row["answer_text_hash"], row["capture_time"])
                    for row in rows
                ]
            ),
            "query_context_fact_set_hash": _canonical_hash(
                sorted((row["context_pub_id"], row["query_text_hash"]) for row in rows)
            ),
            "semantic_manifest_set_hash": _canonical_hash(
                sorted((row["manifest_pub_id"], row["decision_set_hash"]) for row in rows)
            ),
            "semantic_event_set_hash": _canonical_hash(
                sorted((row["pub_id"], row["event_fingerprint"]) for row in event_rows)
            ),
            "semantic_decision_set_hash": _canonical_hash(
                sorted((row["pub_id"], row["decision_hash"]) for row in decision_rows)
            ),
            "collection_design_hash": _canonical_hash(coordinates),
            "canonicalization_version": "canonical-json-v1",
            "weighting_version": "query-macro-v2",
            "engine_version": "metric-snapshot-engine-v2",
        }
        denominator = max(total_count, 1)
        return {
            "scope": dict(scope),
            "as_of": _wire_value(as_of_at),
            "definition_documents": definition_documents,
            "definition_refs": [
                {
                    "name": document["name"],
                    "version": document["version"],
                    "definition_hash": document["definition_hash"],
                }
                for document in definition_documents
            ],
            "metric_definition_set_hash": definition_set_hash,
            "dependency_bundle": dependency_bundle,
            "subjects": subjects,
            "focal_entity_ids": focal_entity_ids,
            "design_coordinates_by_answer": coordinates,
            "planned_repeat_counts": planned_counts,
            "collection_coverage": format(Decimal(eligible_count) / denominator, "f"),
            "query_context_coverage": format(Decimal(context_count) / denominator, "f"),
            "semantic_coverage": format(Decimal(semantic_count) / denominator, "f"),
            "evidence_coverage": format(
                Decimal(
                    sum(
                        row.get("manifest_failure_code") != "evidence_retrieval_failed"
                        for row in rows
                    )
                )
                / denominator,
                "f",
            ),
            "design_basis": str(scope.get("design_basis") or "planned_cells"),
        }

    def load_decision_backfill_batch(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str | None,
        cursor: str | None,
        limit: int,
        as_of: str | None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Load a stable reference-only answer page for semantic replay."""

        if not 1 <= limit <= 1000:
            raise ValueError("metrics_v2_backfill_limit_invalid")
        decoded = _cursor_decode(cursor)
        if decoded is not None and decoded.get("kind") != "decision-backfill":
            raise ValueError("metrics_v2_cursor_scope_mismatch")
        bound = (
            str(decoded["as_of"])
            if decoded is not None
            else str(as_of or datetime.now(UTC).isoformat())
        )
        if decoded is not None and decoded.get("project") != project_pub_id:
            raise ValueError("metrics_v2_cursor_scope_mismatch")
        keys = decoded.get("keys") if decoded else None
        if keys is not None and (not isinstance(keys, list) or len(keys) != 2):
            raise ValueError("invalid_metrics_v2_cursor")
        after_capture = keys[0] if keys else None
        after_pub_id = keys[1] if keys else None
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            total_row = connection.execute(
                """
                SELECT count(*) FROM analytics.answer answer
                WHERE answer.tenant_pub_id=%s
                  AND (%s::text IS NULL OR answer.project_pub_id=%s)
                  AND answer.capture_time <= %s::timestamptz
                """,
                (tenant_pub_id, project_pub_id, project_pub_id, bound),
            ).fetchone()
            assert total_row is not None
            rows = connection.execute(
                """
                SELECT answer.pub_id AS answer_pub_id,answer.project_pub_id,
                       answer.query_pub_id,
                       answer.capture_time,answer.query_text,
                       COALESCE(answer.response_hash,
                         encode(public.digest(answer.response_text,'sha256'),'hex'))
                         AS input_hash,
                       analysis.analysis_run_pub_id,
                       context.pub_id AS query_context_fact_pub_id,
                       context.fact_hash AS context_hash
                FROM analytics.answer answer
                LEFT JOIN LATERAL (
                  SELECT candidate.analysis_run_pub_id
                  FROM analytics.answer_analysis candidate
                  JOIN analytics.analysis_run run
                    ON run.pub_id=candidate.analysis_run_pub_id
                   AND run.tenant_pub_id=candidate.tenant_pub_id
                  WHERE candidate.tenant_pub_id=answer.tenant_pub_id
                    AND candidate.answer_pub_id=answer.pub_id
                    AND run.status='ready'
                    AND run.updated_at <= %s::timestamptz
                  ORDER BY run.updated_at DESC,run.created_at DESC,run.pub_id DESC
                  LIMIT 1
                ) analysis ON true
                LEFT JOIN LATERAL (
                  SELECT candidate.pub_id,candidate.fact_hash
                  FROM analytics.query_context_fact_v2 candidate
                  WHERE candidate.tenant_pub_id=answer.tenant_pub_id
                    AND candidate.project_pub_id=answer.project_pub_id
                    AND candidate.created_at <= %s::timestamptz
                    AND (candidate.query_pub_id=answer.query_pub_id
                         OR candidate.query_key=answer.query_pub_id)
                  ORDER BY candidate.created_at DESC,candidate.pub_id DESC LIMIT 1
                ) context ON true
                WHERE answer.tenant_pub_id=%s
                  AND (%s::text IS NULL OR answer.project_pub_id=%s)
                  AND answer.capture_time <= %s::timestamptz
                  AND (%s::timestamptz IS NULL OR
                       (answer.capture_time,answer.pub_id) >
                       (%s::timestamptz,%s::text))
                ORDER BY answer.capture_time,answer.pub_id
                LIMIT %s
                """,
                (
                    bound,
                    bound,
                    tenant_pub_id,
                    project_pub_id,
                    project_pub_id,
                    bound,
                    after_capture,
                    after_capture,
                    after_pub_id,
                    limit + 1,
                ),
            ).fetchall()
            page = rows[:limit]
            project_ids = sorted({str(row["project_pub_id"]) for row in page})
            entity_rows = (
                connection.execute(
                    """
                    SELECT project.pub_id AS project_pub_id,
                           entity.pub_id AS candidate_id,
                           'brand'::text AS candidate_type,entity.name AS label
                    FROM platform.project project
                    JOIN platform.brand entity ON entity.project_id=project.id
                    WHERE project.pub_id=ANY(%s::text[])
                    UNION ALL
                    SELECT project.pub_id AS project_pub_id,
                           entity.pub_id AS candidate_id,
                           'competitor'::text AS candidate_type,entity.name AS label
                    FROM platform.project project
                    JOIN platform.competitor entity ON entity.project_id=project.id
                    WHERE project.pub_id=ANY(%s::text[])
                    ORDER BY project_pub_id,candidate_type,candidate_id
                    """,
                    (project_ids, project_ids),
                ).fetchall()
                if project_ids
                else []
            )
            answer_ids = sorted({str(row["answer_pub_id"]) for row in page})
            citation_rows = (
                connection.execute(
                    """
                    SELECT answer_pub_id,analysis_run_pub_id,pub_id,ordinal
                    FROM analytics.citation_fact
                    WHERE tenant_pub_id=%s
                      AND answer_pub_id=ANY(%s::text[])
                      AND created_at <= %s::timestamptz
                    ORDER BY answer_pub_id,analysis_run_pub_id,ordinal,pub_id
                    """,
                    (tenant_pub_id, answer_ids, bound),
                ).fetchall()
                if answer_ids
                else []
            )

        from domain.analysis.v2 import build_answer_semantic_workflow_request
        from domain.metrics.v2 import derive_query_key, normalize_query_text

        entities_by_project: dict[str, list[dict[str, str]]] = {}
        for entity in entity_rows:
            entities_by_project.setdefault(str(entity["project_pub_id"]), []).append(
                {
                    "candidate_id": str(entity["candidate_id"]),
                    "candidate_type": str(entity["candidate_type"]),
                    "label": str(entity["label"]),
                }
            )
        citations_by_answer_run: dict[tuple[str, str], list[str]] = {}
        for citation in citation_rows:
            citations_by_answer_run.setdefault(
                (
                    str(citation["answer_pub_id"]),
                    str(citation["analysis_run_pub_id"]),
                ),
                [],
            ).append(str(citation["pub_id"]))
        prepared_items: list[dict[str, Any]] = []
        for row in page:
            project = str(row["project_pub_id"])
            query_text = str(row.get("query_text") or "")
            analysis_run_pub_id = row.get("analysis_run_pub_id")
            managed_entities = entities_by_project.get(project, [])
            reason_codes: list[str] = []
            if not query_text.strip():
                reason_codes.append("semantic_v2_query_reference_missing")
            if analysis_run_pub_id is None:
                reason_codes.append("semantic_v2_ready_analysis_missing")
            if not managed_entities:
                reason_codes.append("semantic_v2_entity_dictionary_missing")
            query_text_hash = (
                sha256(normalize_query_text(query_text).encode()).hexdigest()
                if query_text.strip()
                else None
            )
            query_key = (
                derive_query_key(
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project,
                    query_text=query_text,
                    query_pub_id=(str(row["query_pub_id"]) if row.get("query_pub_id") else None),
                )
                if query_text_hash is not None
                else None
            )
            item: dict[str, Any] = {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project,
                "answer_pub_id": row["answer_pub_id"],
                "subject_ref": {"answer_pub_id": row["answer_pub_id"]},
                "input_snapshot_ref": f"answer:{row['answer_pub_id']}",
                "input_hash": row["input_hash"],
                "context_hash": row.get("context_hash") or ("0" * 64),
                "query_context_fact_pub_id": row.get("query_context_fact_pub_id"),
                "analysis_run_pub_id": analysis_run_pub_id,
                "query_key": query_key,
                "query_text_hash": query_text_hash,
                "capture_time": _wire_value(row["capture_time"]),
                "idempotency_key": _canonical_hash(
                    {
                        "tenant_pub_id": tenant_pub_id,
                        "answer_pub_id": row["answer_pub_id"],
                        "input_hash": row["input_hash"],
                        "context_hash": row.get("context_hash") or ("0" * 64),
                    }
                ),
                "preparation_state": "unknown" if reason_codes else "ready",
                "reason_codes": sorted(reason_codes),
            }
            if not reason_codes:
                try:
                    workflow_payload = build_answer_semantic_workflow_request(
                        tenant_pub_id=tenant_pub_id,
                        project_pub_id=project,
                        answer_pub_id=str(row["answer_pub_id"]),
                        analysis_run_pub_id=str(analysis_run_pub_id),
                        query_key=str(query_key),
                        query_pub_id=(
                            str(row["query_pub_id"]) if row.get("query_pub_id") else None
                        ),
                        query_text_hash=str(query_text_hash),
                        answer_text_hash=str(row["input_hash"]),
                        managed_entities=managed_entities,
                        citation_pub_ids=citations_by_answer_run.get(
                            (
                                str(row["answer_pub_id"]),
                                str(analysis_run_pub_id),
                            ),
                            [],
                        ),
                        classification_source="historical_backfill",
                        created_at=row["capture_time"],
                    )
                except ValueError as error:
                    item["preparation_state"] = "unknown"
                    item["reason_codes"] = [str(error)]
                else:
                    item["workflow_payload"] = workflow_payload
            prepared_items.append(item)
        ready_items = [item for item in prepared_items if item["preparation_state"] == "ready"]
        unknown_items = [item for item in prepared_items if item["preparation_state"] == "unknown"]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _cursor_encode(
                {
                    "v": 1,
                    "kind": "decision-backfill",
                    "project": project_pub_id,
                    "as_of": bound,
                    "keys": [_wire_value(last["capture_time"]), last["answer_pub_id"]],
                }
            )
        return {
            "items": prepared_items if dry_run else ready_items,
            "candidate_count": int(total_row["count"]),
            "page_count": len(prepared_items),
            "executable_count": len(ready_items),
            "preparation_unknown_count": len(unknown_items),
            "preparation_unknowns": unknown_items,
            "next_cursor": next_cursor,
            "done": not has_more,
            "as_of": bound,
            "dry_run": dry_run,
            "batch_hash": _canonical_hash(prepared_items),
        }

    def load_metrics_backfill_batch(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str | None,
        cursor: str | None,
        limit: int,
        as_of: str | None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Load one stable metrics replay page using frozen semantic facts."""

        page = self.load_decision_backfill_batch(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            cursor=cursor,
            limit=limit,
            as_of=as_of,
            dry_run=dry_run,
        )
        if dry_run:
            return {
                **page,
                "subjects": [],
                "unknown_count": int(page["preparation_unknown_count"]),
            }
        if project_pub_id is None:
            raise ValueError("metrics_v2_backfill_project_required")
        answer_ids = [str(item["answer_pub_id"]) for item in page["items"]]
        if not answer_ids:
            return {
                **page,
                "subjects": [],
                "unknown_count": int(page["preparation_unknown_count"]),
            }
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            coordinate = connection.execute(
                """
                SELECT min(capture_time) AS first_capture,max(capture_time) AS last_capture
                FROM analytics.answer
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND pub_id=ANY(%s::text[])
                """,
                (tenant_pub_id, project_pub_id, answer_ids),
            ).fetchone()
            focal_rows = connection.execute(
                """
                SELECT DISTINCT exposure.focal_entity_id
                FROM analytics.query_entity_exposure_fact_v2 exposure
                JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=exposure.tenant_pub_id
                 AND context.project_pub_id=exposure.project_pub_id
                 AND context.pub_id=exposure.query_context_fact_pub_id
                JOIN analytics.answer_semantic_manifest_v2 manifest
                  ON manifest.tenant_pub_id=context.tenant_pub_id
                 AND manifest.project_pub_id=context.project_pub_id
                 AND manifest.query_context_fact_pub_id=context.pub_id
                JOIN analytics.answer answer
                  ON answer.tenant_pub_id=manifest.tenant_pub_id
                 AND answer.project_pub_id=manifest.project_pub_id
                 AND answer.pub_id=manifest.answer_pub_id
                WHERE exposure.tenant_pub_id=%s AND exposure.project_pub_id=%s
                  AND answer.pub_id=ANY(%s::text[])
                ORDER BY exposure.focal_entity_id
                """,
                (tenant_pub_id, project_pub_id, answer_ids),
            ).fetchall()
        assert coordinate is not None
        focal_ids = [str(row["focal_entity_id"]) for row in focal_rows]
        if not focal_ids or coordinate["first_capture"] is None:
            return {
                **page,
                "subjects": [],
                "unknown_count": int(page["preparation_unknown_count"]) + len(answer_ids),
                "skipped_reason": "missing_query_exposure",
            }
        frozen = self.load_snapshot_build_inputs(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            scope={
                "window": {
                    "start": _date_value(coordinate["first_capture"]).isoformat(),
                    # Date boundaries are expanded to [start, end + 1 day)
                    # by load_snapshot_build_inputs.
                    "end": _date_value(coordinate["last_capture"]).isoformat(),
                },
                "filters": {"model": [], "region": [], "mode": []},
                "focal_entity_ids": focal_ids,
                "design_basis": "observed_cells",
            },
            as_of=str(page["as_of"]),
        )
        selected = [
            subject
            for subject in frozen["subjects"]
            if str(subject["answer_pub_id"]) in set(answer_ids)
        ]
        return {
            **page,
            "subjects": selected,
            "definition_documents": frozen["definition_documents"],
            "definition_refs": frozen["definition_refs"],
            "metric_definition_set_hash": frozen["metric_definition_set_hash"],
            "unknown_count": int(page["preparation_unknown_count"])
            + sum(
                subject["answer_fields"].get("manifest_status") != "ready" for subject in selected
            ),
        }

    def commit_decision_backfill_cursor(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str | None,
        job_pub_id: str,
        previous_cursor: str | None,
        cursor: str | None,
        input_count: int,
        status_counts: Mapping[str, int],
        cost_amount: float,
        batch_hash: str,
    ) -> dict[str, Any]:
        """Append one idempotent, reference-only semantic backfill audit event."""

        material = {
            "job_pub_id": job_pub_id,
            "previous_cursor_hash": (_canonical_hash(previous_cursor) if previous_cursor else None),
            "cursor_hash": _canonical_hash(cursor) if cursor else None,
            "input_count": input_count,
            "status_counts": dict(sorted(status_counts.items())),
            "cost_amount": format(Decimal(str(cost_amount)), "f"),
            "batch_hash": batch_hash,
        }
        event_hash = _canonical_hash(material)
        event_id = f"evt_{event_hash[:26]}"
        occurred_at = datetime.now(UTC)
        payload = {
            "event_id": event_id,
            "event_type": "semantic.decision.backfill.batch_completed.v2",
            "occurred_at": occurred_at.isoformat(),
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "subject_pub_id": job_pub_id,
            "subject_version_hash": event_hash,
            **material,
        }
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            inserted = connection.execute(
                """
                INSERT INTO integration.outbox_event
                  (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,
                   payload,occurred_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    event_id,
                    tenant_pub_id,
                    "semantic.decision.backfill.batch_completed.v2",
                    job_pub_id,
                    job_pub_id,
                    Jsonb(payload),
                    occurred_at,
                ),
            ).fetchone()
        return {
            "job_pub_id": job_pub_id,
            "batch_hash": batch_hash,
            "audit_event_id": event_id,
            "cursor": cursor,
            "reused": inserted is None,
        }

    def persist_snapshot_set_atomic(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        snapshot_set: Mapping[str, Any],
        snapshots: Sequence[Mapping[str, Any]],
        contributions: Sequence[Mapping[str, Any]] = (),
        query_contributions: Sequence[Mapping[str, Any]] = (),
        design_contributions: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        set_row = dict(snapshot_set)
        set_row.setdefault("pub_id", new_pub_id("mss"))
        set_row["tenant_pub_id"] = tenant_pub_id
        set_row["project_pub_id"] = project_pub_id
        if int(set_row.get("snapshot_count", len(snapshots))) != len(snapshots):
            raise ValueError("metrics_v2_snapshot_count_mismatch")
        set_row["snapshot_count"] = len(snapshots)
        scope_hash = str(set_row["scope_hash"])
        dependency_hash = str(set_row["dependency_bundle_hash"])
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{tenant_pub_id}:{scope_hash}:{dependency_hash}",),
            )
            existing = connection.execute(
                """
                SELECT pub_id,snapshot_set_hash
                FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND scope_hash=%s AND dependency_bundle_hash=%s
                """,
                (tenant_pub_id, scope_hash, dependency_hash),
            ).fetchone()
            if existing is not None:
                if existing["snapshot_set_hash"] != set_row["snapshot_set_hash"]:
                    raise RuntimeError("metrics_v2_snapshot_set_content_conflict")
                return {
                    "snapshot_set_pub_id": existing["pub_id"],
                    "snapshot_set_hash": existing["snapshot_set_hash"],
                    "reused": True,
                }
            self._insert_mapping(connection, "metric_snapshot_set_v2", set_row)
            snapshot_ids: set[str] = set()
            for item in snapshots:
                row = dict(item)
                row.setdefault("pub_id", new_pub_id("msn"))
                row["tenant_pub_id"] = tenant_pub_id
                row["project_pub_id"] = project_pub_id
                row["snapshot_set_pub_id"] = set_row["pub_id"]
                snapshot_ids.add(str(row["pub_id"]))
                self._insert_mapping(connection, "metric_snapshot_v2", row)
            for table, items in (
                ("metric_contribution_v2", contributions),
                ("metric_query_contribution_v2", query_contributions),
                ("metric_design_cell_contribution_v2", design_contributions),
            ):
                for item in items:
                    row = dict(item)
                    row.setdefault("pub_id", new_pub_id(_TABLE_PREFIX[table]))
                    row["tenant_pub_id"] = tenant_pub_id
                    row["project_pub_id"] = project_pub_id
                    if str(row.get("snapshot_pub_id")) not in snapshot_ids:
                        raise ValueError("metrics_v2_contribution_snapshot_mismatch")
                    self._insert_mapping(connection, table, row)
            inserted_count_row = connection.execute(
                """
                    SELECT count(*) FROM analytics.metric_snapshot_v2
                    WHERE tenant_pub_id=%s AND snapshot_set_pub_id=%s
                    """,
                (tenant_pub_id, set_row["pub_id"]),
            ).fetchone()
            assert inserted_count_row is not None
            inserted_count = int(inserted_count_row["count"])
            if inserted_count != len(snapshots):
                raise RuntimeError("metrics_v2_snapshot_atomicity_count_mismatch")
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="metric.snapshot_set.ready.v2",
                aggregate_pub_id=str(set_row["pub_id"]),
                project_pub_id=project_pub_id,
                subject_hash=str(set_row["snapshot_set_hash"]),
                payload={"correlation_id": set_row["pub_id"], "causation_id": None},
            )
        return {
            "snapshot_set_pub_id": set_row["pub_id"],
            "snapshot_set_hash": set_row["snapshot_set_hash"],
            "reused": False,
        }

    # Kept as a descriptive alias for worker code and integration fixtures.
    create_snapshot_set_atomic = persist_snapshot_set_atomic

    def create_override(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        decision_pub_id: str,
        result: Mapping[str, Any],
        rationale_summary: str,
        reason_codes: Sequence[str],
        expected_decision_hash: str,
        actor_pub_id: str,
        allow_official_publication: bool = False,
    ) -> dict[str, Any]:
        """Create a human successor through the API's constrained DB command."""

        normalized_rationale = rationale_summary.strip()
        normalized_reason_codes = sorted({code.strip() for code in reason_codes if code.strip()})
        if not normalized_rationale or len(normalized_rationale) > 1_000:
            raise ValueError("metrics_v2_override_rationale_invalid")
        if not normalized_reason_codes or any(len(code) > 100 for code in normalized_reason_codes):
            raise ValueError("metrics_v2_override_reason_codes_invalid")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            previous = connection.execute(
                """
                SELECT decision.project_pub_id,decision.decision_hash,task.output_schema
                FROM analytics.semantic_decision_record_v2 decision
                JOIN analytics.semantic_decision_task_definition_v2 task
                  ON task.name=decision.task_name
                 AND task.version=decision.task_version
                 AND task.definition_hash=decision.task_definition_hash
                WHERE decision.tenant_pub_id=%s AND decision.project_pub_id=%s
                  AND decision.pub_id=%s
                """,
                (tenant_pub_id, project_pub_id, decision_pub_id),
            ).fetchone()
            if previous is None:
                raise LookupError("metrics_v2_semantic_decision_not_found")
            from domain.analysis.v2.output_validation import validate_structured_output

            validated = validate_structured_output(result, previous["output_schema"])
            if not validated.is_valid:
                raise ValueError("metrics_v2_override_result_invalid")
            if previous["decision_hash"] != expected_decision_hash:
                raise RuntimeError("metrics_v2_decision_hash_conflict")
            successor = connection.execute(
                """
                SELECT pub_id FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND supersedes_pub_id=%s
                """,
                (tenant_pub_id, project_pub_id, decision_pub_id),
            ).fetchone()
            if successor is not None:
                raise RuntimeError("metrics_v2_decision_already_superseded")
            try:
                command = connection.execute(
                    """
                    INSERT INTO analytics.semantic_decision_override_command_v2
                      (tenant_pub_id,project_pub_id,previous_decision_pub_id,result,
                       rationale_summary,reason_codes,expected_decision_hash,actor_pub_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING decision_job_pub_id,new_decision_pub_id,
                              new_decision_hash,recompute_job_pub_id
                    """,
                    (
                        tenant_pub_id,
                        project_pub_id,
                        decision_pub_id,
                        Jsonb(dict(result)),
                        normalized_rationale,
                        normalized_reason_codes,
                        expected_decision_hash,
                        actor_pub_id,
                    ),
                ).fetchone()
            except (errors.SerializationFailure, errors.UniqueViolation) as exc:
                raise RuntimeError("metrics_v2_decision_already_superseded") from exc
            if command is None:
                raise RuntimeError("metrics_v2_override_command_failed")
            new_decision_pub_id = str(command["new_decision_pub_id"])
            decision_hash = str(command["new_decision_hash"])
            recompute_pub_id = str(command["recompute_job_pub_id"])
            job_pub_id = str(command["decision_job_pub_id"])
            project_pub_id = str(previous["project_pub_id"])
            context_successors = self._refresh_query_contexts_for_successor(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                previous_decision_pub_id=decision_pub_id,
                decision_pub_id=new_decision_pub_id,
                override_reason=normalized_rationale,
            )
            self._refresh_answer_manifests_for_successor(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                previous_decision_pub_id=decision_pub_id,
                decision_pub_id=new_decision_pub_id,
                query_context_successors=context_successors,
            )
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="semantic.decision.completed.v2",
                aggregate_pub_id=new_decision_pub_id,
                project_pub_id=project_pub_id,
                subject_hash=decision_hash,
                payload={
                    "decision_job_pub_id": job_pub_id,
                    "correlation_id": new_decision_pub_id,
                    "causation_id": decision_pub_id,
                },
            )
            affected_scopes = list(
                self._affected_snapshot_scopes(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project_pub_id,
                    decision_pub_id=decision_pub_id,
                )
            )
            if not affected_scopes:
                derived_scope = self._affected_semantic_scope(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project_pub_id,
                    decision_pub_id=decision_pub_id,
                )
                if derived_scope is not None:
                    affected_scopes.append(derived_scope)
            work_items = _override_recompute_work_items(
                affected_scopes,
                allow_official_publication=allow_official_publication,
            )
            recompute_job_pub_ids: list[str] = []
            if not work_items:
                no_op_scope = {
                    "reason": "semantic_decision_override_no_impacted_metric_scope",
                    "decision_pub_id": new_decision_pub_id,
                }
                no_op_scope_hash = _canonical_hash(no_op_scope)
                connection.execute(
                    """
                    INSERT INTO analytics.metric_recompute_job_v2
                      (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
                       target_definition_refs,status,cursor_state,idempotency_key,
                       requested_by,completed_at)
                    VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'succeeded',%s,%s,%s,%s)
                    """,
                    (
                        recompute_pub_id,
                        tenant_pub_id,
                        project_pub_id,
                        Jsonb(no_op_scope),
                        no_op_scope_hash,
                        Jsonb({"noop_reason": "no_impacted_metric_scope"}),
                        _canonical_hash(
                            {
                                "decision_hash": decision_hash,
                                "decision_pub_id": new_decision_pub_id,
                                "operation": "semantic_override_noop",
                            }
                        ),
                        actor_pub_id,
                        datetime.now(UTC),
                    ),
                )
                recompute_job_pub_ids.append(recompute_pub_id)
            else:
                for index, (
                    (recompute_scope_hash, publication_channel),
                    (recompute_scope, publication_target),
                ) in enumerate(
                    work_items.items()
                ):
                    scope_job_pub_id = (
                        recompute_pub_id
                        if index == 0
                        else "mrj_"
                        + _canonical_hash(
                            {
                                "decision_pub_id": new_decision_pub_id,
                                "publication_channel": publication_channel,
                                "scope_hash": recompute_scope_hash,
                            }
                        )[:26]
                    )
                    connection.execute(
                        """
                        INSERT INTO analytics.metric_recompute_job_v2
                          (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
                           target_definition_refs,status,idempotency_key,requested_by)
                        VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'pending',%s,%s)
                        """,
                        (
                            scope_job_pub_id,
                            tenant_pub_id,
                            project_pub_id,
                            Jsonb(recompute_scope),
                            recompute_scope_hash,
                            _canonical_hash(
                                {
                                    "decision_hash": decision_hash,
                                    "decision_pub_id": new_decision_pub_id,
                                    "operation": "semantic_override_recompute",
                                    "publication_channel": publication_channel,
                                    "scope_hash": recompute_scope_hash,
                                }
                            ),
                            actor_pub_id,
                        ),
                    )
                    self._insert_outbox(
                        connection,
                        tenant_pub_id=tenant_pub_id,
                        event_type="metric.snapshot_set.requested.v2",
                        aggregate_pub_id=scope_job_pub_id,
                        project_pub_id=project_pub_id,
                        subject_hash=recompute_scope_hash,
                        payload={
                            "correlation_id": scope_job_pub_id,
                            "causation_id": new_decision_pub_id,
                        },
                    )
                    workflow_payload: dict[str, Any] = {
                        "tenant_pub_id": tenant_pub_id,
                        "project_pub_id": project_pub_id,
                        "job_pub_id": scope_job_pub_id,
                        "scope": recompute_scope,
                        "as_of": datetime.now(UTC).isoformat(),
                    }
                    if publication_target is not None:
                        workflow_payload.update(
                            {
                                "publication_channel": publication_channel,
                                "expected_generation": int(
                                    publication_target["expected_generation"]
                                ),
                                "published_by": actor_pub_id,
                            }
                        )
                    self._insert_workflow_start(
                        connection,
                        tenant_pub_id=tenant_pub_id,
                        workflow_type="metric_snapshot_set_v2",
                        workflow_id=f"metrics-v2:{scope_job_pub_id}",
                        task_queue="geo-platform-v2-metrics",
                        payload=workflow_payload,
                    )
                    recompute_job_pub_ids.append(scope_job_pub_id)
        return {
            "decision_pub_id": new_decision_pub_id,
            "supersedes_pub_id": decision_pub_id,
            "decision_hash": decision_hash,
            "recompute_job_pub_id": recompute_pub_id,
            "recompute_job_pub_ids": recompute_job_pub_ids,
        }

    def _create_override_legacy_owner(
        self,
        *,
        tenant_pub_id: str,
        decision_pub_id: str,
        result: Mapping[str, Any],
        rationale_summary: str,
        reason_codes: Sequence[str],
        expected_decision_hash: str,
        actor_pub_id: str,
    ) -> dict[str, Any]:
        """Retained temporarily for migration-owner diagnostics only."""

        now = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            previous = connection.execute(
                """
                SELECT decision.*,job.rejudge_generation
                FROM analytics.semantic_decision_record_v2 decision
                JOIN analytics.semantic_decision_job_v2 job
                  ON job.tenant_pub_id=decision.tenant_pub_id
                 AND job.project_pub_id=decision.project_pub_id
                 AND job.pub_id=decision.decision_job_pub_id
                WHERE decision.tenant_pub_id=%s AND decision.pub_id=%s
                FOR SHARE OF decision,job
                """,
                (tenant_pub_id, decision_pub_id),
            ).fetchone()
            if previous is None:
                raise LookupError("metrics_v2_semantic_decision_not_found")
            if previous["decision_hash"] != expected_decision_hash:
                raise RuntimeError("metrics_v2_decision_hash_conflict")
            successor = connection.execute(
                """
                SELECT pub_id FROM analytics.semantic_decision_record_v2
                WHERE tenant_pub_id=%s AND supersedes_pub_id=%s
                """,
                (tenant_pub_id, decision_pub_id),
            ).fetchone()
            if successor is not None:
                raise RuntimeError("metrics_v2_decision_already_superseded")
            generation = int(previous["rejudge_generation"]) + 1
            decision_hash = _canonical_hash(
                {
                    "supersedes_pub_id": decision_pub_id,
                    "result": dict(result),
                    "rationale_summary": rationale_summary,
                    "reason_codes": sorted(set(reason_codes)),
                    "method": "human",
                    "actor_pub_id": actor_pub_id,
                    "generation": generation,
                }
            )
            job_pub_id = new_pub_id("sdj")
            attempt_pub_id = new_pub_id("sda")
            new_decision_pub_id = new_pub_id("sdr")
            decision_idem = _canonical_hash(
                {
                    "tenant_pub_id": tenant_pub_id,
                    "task_definition_hash": previous["task_definition_hash"],
                    "subject_key": previous["subject_key"],
                    "input_hash": previous["input_hash"],
                    "context_hash": previous["context_hash"],
                    "judge_policy_hash": previous["judge_policy_hash"],
                    "rejudge_generation": generation,
                }
            )
            connection.execute(
                """
                INSERT INTO analytics.semantic_decision_job_v2
                  (pub_id,tenant_pub_id,project_pub_id,task_name,task_version,
                   task_definition_hash,subject_type,subject_key,subject_ref,
                   input_snapshot_ref,input_hash,context_hash,judge_policy_hash,
                   rejudge_generation,supersedes_decision_pub_id,status,idempotency_key,
                   retry_count,state_reason_codes,started_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',%s,
                        0,ARRAY['manual_override'],%s)
                """,
                (
                    job_pub_id,
                    tenant_pub_id,
                    previous["project_pub_id"],
                    previous["task_name"],
                    previous["task_version"],
                    previous["task_definition_hash"],
                    previous["subject_type"],
                    previous["subject_key"],
                    Jsonb(previous["subject_ref"]),
                    previous["input_snapshot_ref"],
                    previous["input_hash"],
                    previous["context_hash"],
                    previous["judge_policy_hash"],
                    generation,
                    decision_pub_id,
                    decision_idem,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO analytics.semantic_decision_attempt_v2
                  (pub_id,tenant_pub_id,project_pub_id,decision_job_pub_id,attempt_index,
                   role,method,inference_config,prompt_template_ref,prompt_template_hash,
                   rubric_hash,output_schema_hash,request_payload_hash,response_payload_hash,
                   validated_output,rationale_summary,validation_status,reason_codes,created_at)
                VALUES (%s,%s,%s,%s,0,'human','human','{}'::jsonb,'manual-override',
                        %s,%s,%s,%s,%s,%s,%s,'accepted',%s,%s)
                """,
                (
                    attempt_pub_id,
                    tenant_pub_id,
                    previous["project_pub_id"],
                    job_pub_id,
                    previous["rubric_hash"],
                    previous["rubric_hash"],
                    previous["output_schema_hash"],
                    expected_decision_hash,
                    decision_hash,
                    Jsonb(dict(result)),
                    rationale_summary,
                    list(reason_codes),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO analytics.semantic_decision_record_v2
                  (pub_id,tenant_pub_id,project_pub_id,decision_job_pub_id,task_name,
                   task_version,task_definition_hash,subject_type,subject_key,subject_ref,
                   metric_name,metric_version,input_snapshot_ref,input_hash,context_hash,
                   method,status,result,rationale_summary,calibrated_confidence,
                   calibration_bucket,reason_codes,evidence_refs,evidence_spans,
                   selected_attempt_pub_ids,judge_policy_hash,rubric_ref,rubric_hash,
                   output_schema_hash,supersedes_pub_id,decision_hash,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'human','accepted',
                        %s,%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    new_decision_pub_id,
                    tenant_pub_id,
                    previous["project_pub_id"],
                    job_pub_id,
                    previous["task_name"],
                    previous["task_version"],
                    previous["task_definition_hash"],
                    previous["subject_type"],
                    previous["subject_key"],
                    Jsonb(previous["subject_ref"]),
                    previous.get("metric_name"),
                    previous.get("metric_version"),
                    previous["input_snapshot_ref"],
                    previous["input_hash"],
                    previous["context_hash"],
                    Jsonb(dict(result)),
                    rationale_summary,
                    list(reason_codes),
                    Jsonb(previous["evidence_refs"]),
                    Jsonb(previous["evidence_spans"]),
                    [attempt_pub_id],
                    previous["judge_policy_hash"],
                    previous["rubric_ref"],
                    previous["rubric_hash"],
                    previous["output_schema_hash"],
                    decision_pub_id,
                    decision_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE analytics.semantic_decision_job_v2
                SET status='succeeded',selected_decision_pub_id=%s,completed_at=%s
                WHERE tenant_pub_id=%s AND pub_id=%s AND status='running'
                """,
                (new_decision_pub_id, now, tenant_pub_id, job_pub_id),
            )
            recompute_idem = _canonical_hash(
                {"decision_pub_id": new_decision_pub_id, "decision_hash": decision_hash}
            )
            recompute_pub_id = new_pub_id("mrj")
            connection.execute(
                """
                INSERT INTO analytics.metric_recompute_job_v2
                  (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
                   target_definition_refs,status,idempotency_key,requested_by)
                VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'pending',%s,%s)
                """,
                (
                    recompute_pub_id,
                    tenant_pub_id,
                    previous["project_pub_id"],
                    Jsonb(
                        {
                            "reason": "semantic_decision_override",
                            "decision_pub_id": new_decision_pub_id,
                        }
                    ),
                    recompute_idem,
                    recompute_idem,
                    actor_pub_id,
                ),
            )
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="semantic.decision.completed.v2",
                aggregate_pub_id=new_decision_pub_id,
                project_pub_id=str(previous["project_pub_id"]),
                subject_hash=decision_hash,
                payload={
                    "decision_job_pub_id": job_pub_id,
                    "correlation_id": new_decision_pub_id,
                    "causation_id": decision_pub_id,
                },
            )
            self._insert_outbox(
                connection,
                tenant_pub_id=tenant_pub_id,
                event_type="metric.snapshot_set.requested.v2",
                aggregate_pub_id=recompute_pub_id,
                project_pub_id=str(previous["project_pub_id"]),
                subject_hash=recompute_idem,
                payload={
                    "correlation_id": recompute_pub_id,
                    "causation_id": new_decision_pub_id,
                },
            )
        return {
            "decision_pub_id": new_decision_pub_id,
            "supersedes_pub_id": decision_pub_id,
            "decision_hash": decision_hash,
            "recompute_job_pub_id": recompute_pub_id,
        }

    def export_bundle(self, *, tenant_pub_id: str, set_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            snapshot_set = connection.execute(
                """
                SELECT * FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, set_pub_id),
            ).fetchone()
            if snapshot_set is None:
                raise LookupError("metrics_v2_snapshot_set_not_found")
            metrics = connection.execute(
                """
                SELECT snapshot.*,definition.definition
                FROM analytics.metric_snapshot_v2 snapshot
                LEFT JOIN analytics.metric_definition definition
                  ON definition.name=snapshot.metric_name
                 AND definition.version=snapshot.metric_version
                 AND definition.definition_hash=snapshot.metric_definition_hash
                WHERE snapshot.tenant_pub_id=%s AND snapshot.snapshot_set_pub_id=%s
                ORDER BY snapshot.metric_name,snapshot.metric_version,
                         snapshot.focal_entity_id,snapshot.pub_id
                """,
                (tenant_pub_id, set_pub_id),
            ).fetchall()
            snapshot_ids = [row["pub_id"] for row in metrics]
            queries = connection.execute(
                """
                SELECT contribution.*,context.query_pub_id,raw.query_text,
                       context.analysis_lenses,context.requested_operations,
                       exposure.exposure_role
                FROM analytics.metric_query_contribution_v2 contribution
                LEFT JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=contribution.tenant_pub_id
                 AND context.project_pub_id=contribution.project_pub_id
                 AND context.pub_id=contribution.query_context_fact_pub_id
                LEFT JOIN analytics.query_entity_exposure_fact_v2 exposure
                  ON exposure.tenant_pub_id=contribution.tenant_pub_id
                 AND exposure.project_pub_id=contribution.project_pub_id
                 AND exposure.query_context_fact_pub_id=contribution.query_context_fact_pub_id
                 AND exposure.focal_entity_id=contribution.focal_entity_id
                LEFT JOIN LATERAL (
                  SELECT answer.query_text
                  FROM analytics.answer answer
                  WHERE answer.tenant_pub_id=contribution.tenant_pub_id
                    AND answer.project_pub_id=contribution.project_pub_id
                    AND (answer.query_pub_id=context.query_pub_id OR answer.query_pub_id IS NULL)
                  ORDER BY answer.capture_time,answer.pub_id LIMIT 1
                ) raw ON true
                WHERE contribution.tenant_pub_id=%s
                  AND contribution.snapshot_pub_id=ANY(%s::text[])
                ORDER BY contribution.snapshot_pub_id,contribution.query_key
                """,
                (tenant_pub_id, snapshot_ids),
            ).fetchall()
            answers = connection.execute(
                """
                SELECT contribution.*,raw.query_pub_id,raw.query_text,raw.response_text,
                       context.analysis_lenses,context.requested_operations,
                       exposure.exposure_role
                FROM analytics.metric_contribution_v2 contribution
                JOIN analytics.answer raw
                  ON raw.tenant_pub_id=contribution.tenant_pub_id
                 AND raw.pub_id=contribution.answer_pub_id
                LEFT JOIN analytics.query_context_fact_v2 context
                  ON context.tenant_pub_id=contribution.tenant_pub_id
                 AND context.project_pub_id=contribution.project_pub_id
                 AND context.pub_id=contribution.query_context_fact_pub_id
                LEFT JOIN analytics.query_entity_exposure_fact_v2 exposure
                  ON exposure.tenant_pub_id=contribution.tenant_pub_id
                 AND exposure.project_pub_id=contribution.project_pub_id
                 AND exposure.query_context_fact_pub_id=contribution.query_context_fact_pub_id
                 AND exposure.focal_entity_id=contribution.focal_entity_id
                WHERE contribution.tenant_pub_id=%s
                  AND contribution.snapshot_pub_id=ANY(%s::text[])
                ORDER BY contribution.snapshot_pub_id,contribution.query_key,
                         contribution.model,contribution.region,contribution.mode,
                         contribution.capture_time,contribution.answer_pub_id
                """,
                (tenant_pub_id, snapshot_ids),
            ).fetchall()
            event_ids = sorted(
                {
                    str(item)
                    for row in answers
                    for item in (row.get("supporting_event_pub_ids") or ())
                }
            )
            decision_ids = sorted(
                {
                    str(item)
                    for row in answers
                    for item in (row.get("supporting_decision_pub_ids") or ())
                }
            )
            events = (
                connection.execute(
                    """
                    SELECT * FROM analytics.answer_semantic_event_v2
                    WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[])
                    ORDER BY answer_pub_id,semantic_manifest_pub_id,event_index,pub_id
                    """,
                    (tenant_pub_id, event_ids),
                ).fetchall()
                if event_ids
                else []
            )
            decisions = (
                connection.execute(
                    """
                    SELECT pub_id,project_pub_id,task_name,task_version,subject_type,
                           subject_key,method,status,result,rationale_summary,
                           calibrated_confidence,reason_codes,evidence_refs,evidence_spans,
                           judge_policy_hash,rubric_ref,rubric_hash,decision_hash,created_at
                    FROM analytics.semantic_decision_record_v2
                    WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[])
                    ORDER BY task_name,subject_key,created_at,pub_id
                    """,
                    (tenant_pub_id, decision_ids),
                ).fetchall()
                if decision_ids
                else []
            )
            design_cells = connection.execute(
                """
                SELECT * FROM analytics.metric_design_cell_contribution_v2
                WHERE tenant_pub_id=%s AND snapshot_pub_id=ANY(%s::text[])
                ORDER BY snapshot_pub_id,query_key,model,region,mode,pub_id
                """,
                (tenant_pub_id, snapshot_ids),
            ).fetchall()
        readme = [
            {
                "schema_version": "metric-export-v2",
                "snapshot_set_pub_id": set_pub_id,
                "snapshot_set_hash": snapshot_set["snapshot_set_hash"],
                "project_pub_id": snapshot_set["project_pub_id"],
                "state": snapshot_set["state"],
                "as_of": snapshot_set["as_of"],
                "window_start": snapshot_set["window_start"],
                "window_end": snapshot_set["window_end"],
                "filters": _json_object(snapshot_set["filters"]),
                "aggregation_method": snapshot_set["aggregation_method"],
                "canonicalization_version": _json_object(snapshot_set["dependency_bundle"]).get(
                    "canonicalization_version", "canonical-json-v1"
                ),
            }
        ]
        exclusions = [
            dict(row)
            for row in answers
            if row["eligibility_status"]
            in {"excluded", "not_applicable", "analysis_unknown", "analysis_failed"}
        ]
        hashes: list[dict[str, Any]] = [
            {
                "object_type": "snapshot_set",
                "object_pub_id": set_pub_id,
                "content_hash": snapshot_set["snapshot_set_hash"],
            }
        ]
        for row in metrics:
            hashes.extend(
                {
                    "object_type": key,
                    "object_pub_id": row["pub_id"],
                    "content_hash": row[key],
                }
                for key in (
                    "snapshot_hash",
                    "contribution_set_hash",
                    "query_contribution_set_hash",
                    "design_contribution_set_hash",
                )
            )
        for row in answers:
            hashes.append(
                {
                    "object_type": "contribution_hash",
                    "object_pub_id": row["pub_id"],
                    "content_hash": row["contribution_hash"],
                }
            )
        return {
            "readme": readme,
            "metrics": [dict(row) for row in metrics],
            "queries": [dict(row) for row in queries],
            "answers": [dict(row) for row in answers],
            "decisions": [dict(row) for row in decisions],
            "events": [dict(row) for row in events],
            "exclusions": exclusions,
            "design_cells": [dict(row) for row in design_cells],
            "hashes": hashes,
        }

    def persist_export_record(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        export_pub_id: str,
        snapshot_set_pub_id: str,
        snapshot_set_hash: str,
        window_start: date,
        window_end: date,
        export_format: str,
        evidence_pub_id: str,
        created_by_pub_id: str,
    ) -> dict[str, Any]:
        export_type = {
            "xlsx": "metric_v2_xlsx",
            "csv_zip": "metric_v2_csv_zip",
        }.get(export_format)
        if export_type is None:
            raise ValueError("invalid_metric_export_format")
        filters = {
            "snapshot_set_pub_id": snapshot_set_pub_id,
            "snapshot_set_hash": snapshot_set_hash,
        }
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            snapshot_set = connection.execute(
                """
                SELECT project_pub_id,snapshot_set_hash,dependency_bundle
                FROM analytics.metric_snapshot_set_v2
                WHERE tenant_pub_id=%s AND pub_id=%s AND project_pub_id=%s
                """,
                (tenant_pub_id, snapshot_set_pub_id, project_pub_id),
            ).fetchone()
            if snapshot_set is None or snapshot_set["snapshot_set_hash"] != snapshot_set_hash:
                raise LookupError("metrics_v2_snapshot_set_not_found")
            dependency = _json_object(snapshot_set["dependency_bundle"])
            row = connection.execute(
                """
                INSERT INTO reporting.data_export
                  (pub_id,tenant_pub_id,project_pub_id,export_type,window_start,window_end,
                   filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
                   evidence_pub_id,created_by_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING pub_id,evidence_pub_id,created_at
                """,
                (
                    export_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    export_type,
                    window_start,
                    window_end,
                    Jsonb(filters),
                    _canonical_hash(filters),
                    str(dependency.get("metric_definition_set_hash") or "metrics-v2"),
                    str(dependency.get("engine_version") or "metrics-v2"),
                    snapshot_set_hash,
                    evidence_pub_id,
                    created_by_pub_id,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("metrics_v2_export_record_insert_failed")
        return dict(row)


__all__ = ["MetricsV2Repository"]
