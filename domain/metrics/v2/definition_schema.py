"""Strict, non-executable schema for the V2 measurement-protocol DSL."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast

from .canonical_hash import canonical_hash

DEFINITION_SCHEMA_VERSION = "metric-definition-v2"
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*@[A-Za-z0-9][A-Za-z0-9_.+-]*$")


class DefinitionValidationError(ValueError):
    """A measurement protocol is invalid or attempts unsupported behavior."""


class DefinitionStatus(StrEnum):
    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    PUBLISHED = "published"
    RETIRED = "retired"
    LEGACY = "legacy"


class UnitType(StrEnum):
    ANSWER = "answer"
    CLAIM = "claim"
    RELATION = "relation"
    CITATION = "citation"
    DIMENSION = "dimension"
    DESIGN_CELL = "design_cell"


class OutcomeSource(StrEnum):
    DETERMINISTIC_EXPRESSION = "deterministic_expression"
    SEMANTIC_DECISION = "semantic_decision"
    HYBRID = "hybrid"


class AggregationMethod(StrEnum):
    QUERY_MACRO = "query_macro"
    ANSWER_WEIGHTED = "answer_weighted"


@dataclass(frozen=True, slots=True)
class RequiredSemanticCapability:
    name: str
    task_ref: str
    accepted_status: str = "ready"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    version: str
    unit_type: UnitType
    focal_entity_required: bool
    outcome_source: OutcomeSource
    query_predicate: Mapping[str, Any]
    required_semantic_capabilities: tuple[RequiredSemanticCapability, ...]
    required_event_types: tuple[str, ...]
    outcome: Mapping[str, Any]
    missing_policy: str
    default_aggregation_method: AggregationMethod
    allowed_aggregation_methods: tuple[AggregationMethod, ...]
    status: DefinitionStatus
    definition_schema_version: str
    decision_task_refs: tuple[str, ...] = ()
    semantic_rubric_ref: str | None = None
    applicability: Mapping[str, Any] | None = None
    reason_codes: Mapping[str, str] = field(default_factory=dict)
    publication_gate: Mapping[str, Any] = field(default_factory=dict)
    adjudication_uncertainty_policy: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    definition_hash: str = ""
    raw_definition: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def default_aggregation(self) -> AggregationMethod:
        """Compatibility spelling used in the prose example."""

        return self.default_aggregation_method


_TOP_LEVEL_KEYS = frozenset(
    {
        "name",
        "version",
        "definition_schema_version",
        "definition_hash",
        "status",
        "unit_type",
        "focal_entity_required",
        "outcome_source",
        "query_predicate",
        "required_semantic_capabilities",
        "required_event_types",
        "decision_task_refs",
        "semantic_rubric_ref",
        "outcome",
        "applicability",
        "missing_policy",
        "allowed_aggregation_methods",
        "default_aggregation_method",
        "default_aggregation",
        "reason_codes",
        "publication_gate",
        "adjudication_uncertainty_policy",
        "metadata",
    }
)

_EVENT_TYPES = frozenset(
    {
        "entity_mention",
        "recommendation_relation",
        "sentiment_or_stance",
        "recommendation_list_rank",
        "market_rank_claim",
        "pairwise_preference",
        "mention_order",
        "source_result_rank",
        "factual_claim",
        "claim_evidence_verdict",
        "citation_relation",
        "risk_event",
    }
)

_EVENT_FIELDS = frozenset(
    {
        "surface",
        "mention_role",
        "substantive",
        "polarity",
        "strength",
        "scenario",
        "aspect",
        "rank",
        "list_size",
        "list_id",
        "ordered",
        "rank_low",
        "rank_high",
        "market_scope",
        "time_scope",
        "claim_text",
        "relation",
        "ordinal",
        "entity_count",
        "source_id",
        "verifiability",
        "claim_fingerprint",
        "claim_event_pub_id",
        "verdict",
        "verification_as_of",
        "evidence_snapshot_refs",
        "citation_pub_id",
        "support_state",
        "risk_type",
        "severity",
        "subject_entity_id",
        "object_entity_id",
        "subject_resolution",
        "attribution_correct",
        "attribution_known",
        "stale",
        "time_attribute_known",
        "claim_type",
    }
)

_PREDICATE_NODES = frozenset(
    {
        "all",
        "any",
        "not",
        "query_has_lens",
        "query_has_operation",
        "exposure_is",
        "event_exists",
        "event_value_equals",
        "event_numeric_compare",
        "capability_status_is",
        "decision_exists",
        "decision_value_equals",
        "decision_numeric_compare",
        "manifest_status_is",
        "answer_field_equals",
        "all_answers",
        "event_applicable_only",
        "custom_missing_policy",
    }
)

_OUTCOME_NODES = frozenset({"binary_outcome", "count_outcome", "numeric_outcome"})
_SELECTOR_KEYS = frozenset({"type", "subject", "object", "where"})
_COMPARE_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
_MISSING_POLICIES = frozenset(
    {
        "unknown_if_required_analysis_unready",
        "unknown_if_evidence_unready",
        "exclude_if_not_applicable",
    }
)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DefinitionValidationError(f"{path} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise DefinitionValidationError(f"{path} keys must be strings")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise DefinitionValidationError(f"{path} must be an array")
    return cast(Sequence[Any], value)


def _only_keys(value: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DefinitionValidationError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _safe_ref(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value):
        raise DefinitionValidationError(f"{path} must be a versioned reference")
    return value


def _validate_selector(value: Any, path: str) -> None:
    selector = _mapping(value, path)
    _only_keys(selector, _SELECTOR_KEYS, path)
    event_type = selector.get("type")
    if event_type not in _EVENT_TYPES:
        raise DefinitionValidationError(f"{path}.type is not a supported semantic event")
    for entity_key in ("subject", "object"):
        entity = selector.get(entity_key)
        if entity is not None and not isinstance(entity, str):
            raise DefinitionValidationError(f"{path}.{entity_key} must be a string")
    where = selector.get("where", {})
    where_mapping = _mapping(where, f"{path}.where")
    unknown_fields = sorted(set(where_mapping) - _EVENT_FIELDS)
    if unknown_fields:
        raise DefinitionValidationError(
            f"{path}.where uses unknown event fields: {', '.join(unknown_fields)}"
        )
    for key, expected in where_mapping.items():
        if isinstance(expected, Mapping | list | tuple | set):
            raise DefinitionValidationError(f"{path}.where.{key} must be a scalar")


def _validate_predicate(node: Any, path: str) -> None:
    expression = _mapping(node, path)
    if len(expression) != 1:
        raise DefinitionValidationError(f"{path} must contain exactly one DSL node")
    name, value = next(iter(expression.items()))
    if name not in _PREDICATE_NODES:
        raise DefinitionValidationError(f"{path} uses unknown DSL node: {name}")
    if name in {"all", "any"}:
        children = _sequence(value, f"{path}.{name}")
        if not children:
            raise DefinitionValidationError(f"{path}.{name} cannot be empty")
        for index, child in enumerate(children):
            _validate_predicate(child, f"{path}.{name}[{index}]")
        return
    if name == "not":
        _validate_predicate(value, f"{path}.not")
        return
    if name == "query_has_lens":
        if value not in {"ai_impression", "ai_recommendation"}:
            raise DefinitionValidationError(f"{path}.query_has_lens is invalid")
        return
    if name == "query_has_operation":
        if value not in {
            "describe",
            "fact_lookup",
            "evaluate",
            "recommend",
            "compare",
            "rank",
            "explain",
        }:
            raise DefinitionValidationError(f"{path}.query_has_operation is invalid")
        return
    if name == "exposure_is":
        if value not in {
            "brand_neutral",
            "focal_named_only",
            "focal_named_with_others",
            "other_brand_named",
            "unknown",
        }:
            raise DefinitionValidationError(f"{path}.exposure_is is invalid")
        return
    if name == "event_exists":
        _validate_selector(value, f"{path}.event_exists")
        return
    if name in {"event_value_equals", "event_numeric_compare"}:
        comparison = _mapping(value, f"{path}.{name}")
        allowed = _SELECTOR_KEYS | {"field", "value", "op"}
        _only_keys(comparison, frozenset(allowed), f"{path}.{name}")
        _validate_selector(
            {key: item for key, item in comparison.items() if key in _SELECTOR_KEYS},
            f"{path}.{name}",
        )
        field_name = comparison.get("field")
        if field_name not in _EVENT_FIELDS:
            raise DefinitionValidationError(f"{path}.{name}.field is invalid")
        if "value" not in comparison:
            raise DefinitionValidationError(f"{path}.{name}.value is required")
        if name == "event_numeric_compare":
            if comparison.get("op") not in _COMPARE_OPS:
                raise DefinitionValidationError(f"{path}.{name}.op is invalid")
            try:
                Decimal(str(comparison["value"]))
            except (InvalidOperation, ValueError) as exc:
                raise DefinitionValidationError(f"{path}.{name}.value must be numeric") from exc
        return
    if name == "capability_status_is":
        condition = _mapping(value, f"{path}.{name}")
        _only_keys(condition, frozenset({"name", "status"}), f"{path}.{name}")
        if not isinstance(condition.get("name"), str) or condition.get("status") not in {
            "ready",
            "abstained",
            "review_required",
            "failed",
            "not_requested",
        }:
            raise DefinitionValidationError(f"{path}.{name} is invalid")
        return
    if name == "decision_exists":
        _safe_ref(value, f"{path}.{name}")
        return
    if name in {"decision_value_equals", "decision_numeric_compare"}:
        condition = _mapping(value, f"{path}.{name}")
        allowed = frozenset({"task_ref", "field", "value", "op"})
        _only_keys(condition, allowed, f"{path}.{name}")
        _safe_ref(condition.get("task_ref"), f"{path}.{name}.task_ref")
        if not isinstance(condition.get("field"), str) or not condition["field"]:
            raise DefinitionValidationError(f"{path}.{name}.field is invalid")
        if "value" not in condition:
            raise DefinitionValidationError(f"{path}.{name}.value is required")
        if name == "decision_numeric_compare" and condition.get("op") not in _COMPARE_OPS:
            raise DefinitionValidationError(f"{path}.{name}.op is invalid")
        return
    if name == "manifest_status_is":
        if value not in {"ready", "partial", "review_required", "failed"}:
            raise DefinitionValidationError(f"{path}.{name} is invalid")
        return
    if name == "answer_field_equals":
        condition = _mapping(value, f"{path}.{name}")
        _only_keys(condition, frozenset({"field", "value"}), f"{path}.{name}")
        field_name = condition.get("field")
        if not isinstance(field_name, str) or not _SAFE_NAME.fullmatch(field_name):
            raise DefinitionValidationError(f"{path}.{name}.field is invalid")
        return
    if name == "all_answers":
        if value is not True:
            raise DefinitionValidationError(f"{path}.all_answers must be true")
        return
    if name == "event_applicable_only":
        _validate_selector(value, f"{path}.event_applicable_only")
        return
    if name == "custom_missing_policy":
        if value not in _MISSING_POLICIES:
            raise DefinitionValidationError(f"{path}.custom_missing_policy is invalid")


def _validate_count_outcome(value: Any, path: str) -> None:
    config = _mapping(value, path)
    allowed = frozenset(
        {
            "from_events",
            "from_decisions",
            "numerator_where",
            "denominator_where",
            "numerator_labels",
            "denominator_labels",
            "denominator",
            "partial_credit",
            "numerator_subject",
            "denominator_subject",
            "unique_by",
            "credit_total",
        }
    )
    _only_keys(config, allowed, path)
    source_count = int("from_events" in config) + int("from_decisions" in config)
    if source_count != 1:
        raise DefinitionValidationError(f"{path} requires exactly one outcome source")
    if "from_events" in config:
        event_type = config["from_events"]
        if event_type not in _EVENT_TYPES:
            raise DefinitionValidationError(f"{path}.from_events is invalid")
        for field_name in ("numerator_where", "denominator_where"):
            where = _mapping(config.get(field_name, {}), f"{path}.{field_name}")
            unknown = sorted(set(where) - _EVENT_FIELDS)
            if unknown:
                raise DefinitionValidationError(
                    f"{path}.{field_name} has unknown event fields: {', '.join(unknown)}"
                )
        for entity_key in ("numerator_subject", "denominator_subject"):
            if entity_key in config and not isinstance(config[entity_key], str):
                raise DefinitionValidationError(f"{path}.{entity_key} must be a string")
        if config.get("unique_by") not in {None, "subject_entity_id", "event_pub_id"}:
            raise DefinitionValidationError(f"{path}.unique_by is invalid")
        if config.get("credit_total") not in {None, "one_per_answer"}:
            raise DefinitionValidationError(f"{path}.credit_total is invalid")
    else:
        source = config["from_decisions"]
        if not isinstance(source, str) or not source:
            raise DefinitionValidationError(f"{path}.from_decisions is invalid")
    if "partial_credit" in config:
        try:
            partial = Decimal(str(config["partial_credit"]))
        except (InvalidOperation, ValueError) as exc:
            raise DefinitionValidationError(f"{path}.partial_credit must be numeric") from exc
        if not Decimal("0") <= partial <= Decimal("1"):
            raise DefinitionValidationError(f"{path}.partial_credit must be between 0 and 1")


def _validate_numeric_outcome(value: Any, path: str) -> None:
    config = _mapping(value, path)
    allowed = frozenset({"from_events", "field", "aggregate", "where", "subject", "object"})
    _only_keys(config, allowed, path)
    if config.get("from_events") not in _EVENT_TYPES:
        raise DefinitionValidationError(f"{path}.from_events is invalid")
    if config.get("field") not in _EVENT_FIELDS:
        raise DefinitionValidationError(f"{path}.field is invalid")
    if config.get("aggregate") not in {"sum", "mean", "min", "max", "first"}:
        raise DefinitionValidationError(f"{path}.aggregate is invalid")
    for entity_key in ("subject", "object"):
        if entity_key in config and not isinstance(config[entity_key], str):
            raise DefinitionValidationError(f"{path}.{entity_key} must be a string")
    where = _mapping(config.get("where", {}), f"{path}.where")
    unknown = sorted(set(where) - _EVENT_FIELDS)
    if unknown:
        raise DefinitionValidationError(f"{path}.where has unknown event fields: {unknown}")


def _validate_outcome(value: Any, path: str) -> None:
    outcome = _mapping(value, path)
    if len(outcome) != 1:
        raise DefinitionValidationError(f"{path} must contain exactly one outcome node")
    name, config = next(iter(outcome.items()))
    if name not in _OUTCOME_NODES:
        raise DefinitionValidationError(f"{path} uses unknown outcome node: {name}")
    if name == "binary_outcome":
        _validate_predicate(config, f"{path}.binary_outcome")
    elif name == "count_outcome":
        _validate_count_outcome(config, f"{path}.count_outcome")
    else:
        _validate_numeric_outcome(config, f"{path}.numeric_outcome")


def _parse_capabilities(value: Any) -> tuple[RequiredSemanticCapability, ...]:
    capabilities: list[RequiredSemanticCapability] = []
    seen: set[str] = set()
    for index, raw in enumerate(_sequence(value, "required_semantic_capabilities")):
        item = _mapping(raw, f"required_semantic_capabilities[{index}]")
        _only_keys(item, frozenset({"name", "task_ref", "accepted_status"}), "capability")
        name = item.get("name")
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise DefinitionValidationError(f"capability {index} has invalid name")
        if name in seen:
            raise DefinitionValidationError(f"duplicate required capability: {name}")
        seen.add(name)
        task_ref = _safe_ref(item.get("task_ref"), f"capability {name}.task_ref")
        accepted_status = item.get("accepted_status", "ready")
        if accepted_status != "ready":
            raise DefinitionValidationError("V2 published metrics only accept ready capabilities")
        capabilities.append(RequiredSemanticCapability(name, task_ref, accepted_status))
    return tuple(capabilities)


def validate_metric_definition(raw: Mapping[str, Any]) -> MetricDefinition:
    """Validate and compile one data-only measurement protocol."""

    payload = _mapping(raw, "definition")
    _only_keys(payload, _TOP_LEVEL_KEYS, "definition")
    name = payload.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise DefinitionValidationError("definition.name must be snake_case")
    version = payload.get("version")
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise DefinitionValidationError("definition.version must be semantic versioning")
    schema_version = payload.get("definition_schema_version", DEFINITION_SCHEMA_VERSION)
    if schema_version != DEFINITION_SCHEMA_VERSION:
        raise DefinitionValidationError(f"unsupported definition schema: {schema_version}")
    unit_type_raw = payload.get("unit_type")
    outcome_source_raw = payload.get("outcome_source")
    if not isinstance(unit_type_raw, str) or not isinstance(outcome_source_raw, str):
        raise DefinitionValidationError("unit_type and outcome_source must be strings")
    try:
        unit_type = UnitType(unit_type_raw)
        outcome_source = OutcomeSource(outcome_source_raw)
        status = DefinitionStatus(payload.get("status", "published"))
    except ValueError as exc:
        raise DefinitionValidationError(str(exc)) from exc
    focal_required = payload.get("focal_entity_required")
    if not isinstance(focal_required, bool):
        raise DefinitionValidationError("focal_entity_required must be boolean")
    query_predicate = _mapping(payload.get("query_predicate"), "query_predicate")
    _validate_predicate(query_predicate, "query_predicate")
    outcome = _mapping(payload.get("outcome"), "outcome")
    _validate_outcome(outcome, "outcome")
    applicability_raw = payload.get("applicability")
    applicability = None
    if applicability_raw is not None:
        applicability = _mapping(applicability_raw, "applicability")
        _validate_predicate(applicability, "applicability")
    capabilities = _parse_capabilities(payload.get("required_semantic_capabilities", []))
    event_types_raw = _sequence(payload.get("required_event_types", []), "required_event_types")
    event_types: list[str] = []
    for event_type in event_types_raw:
        if event_type not in _EVENT_TYPES:
            raise DefinitionValidationError(f"unknown required event type: {event_type}")
        if event_type in event_types:
            raise DefinitionValidationError(f"duplicate required event type: {event_type}")
        event_types.append(event_type)
    task_refs_raw = _sequence(payload.get("decision_task_refs", []), "decision_task_refs")
    task_refs = tuple(_safe_ref(item, "decision_task_refs[]") for item in task_refs_raw)
    missing_policy = payload.get("missing_policy", "unknown_if_required_analysis_unready")
    if missing_policy not in _MISSING_POLICIES:
        raise DefinitionValidationError(f"unknown missing policy: {missing_policy}")
    aggregations_raw = payload.get(
        "allowed_aggregation_methods", ["query_macro", "answer_weighted"]
    )
    try:
        aggregations = tuple(
            AggregationMethod(item)
            for item in _sequence(aggregations_raw, "allowed_aggregation_methods")
        )
        default_aggregation = AggregationMethod(
            payload.get(
                "default_aggregation_method",
                payload.get("default_aggregation", "query_macro"),
            )
        )
    except ValueError as exc:
        raise DefinitionValidationError(str(exc)) from exc
    if len(set(aggregations)) != len(aggregations) or not aggregations:
        raise DefinitionValidationError("allowed aggregations must be a non-empty unique list")
    if default_aggregation not in aggregations:
        raise DefinitionValidationError("default aggregation must be allowed")
    rubric_ref = payload.get("semantic_rubric_ref")
    if rubric_ref is not None and not isinstance(rubric_ref, str):
        raise DefinitionValidationError("semantic_rubric_ref must be a string")
    for mapping_name in (
        "reason_codes",
        "publication_gate",
        "adjudication_uncertainty_policy",
        "metadata",
    ):
        _mapping(payload.get(mapping_name, {}), mapping_name)
    # Lifecycle is control-plane state, not measurement semantics.  Keeping it
    # outside the immutable content hash lets the exact same reviewed protocol
    # move experimental -> published without hash drift or a fake new version.
    hash_payload = {
        key: item for key, item in payload.items() if key not in {"definition_hash", "status"}
    }
    computed_hash = canonical_hash(hash_payload)
    declared_hash = payload.get("definition_hash")
    if declared_hash is not None and declared_hash != computed_hash:
        raise DefinitionValidationError("definition hash drift detected")
    return MetricDefinition(
        name=name,
        version=version,
        unit_type=unit_type,
        focal_entity_required=focal_required,
        outcome_source=outcome_source,
        query_predicate=query_predicate,
        required_semantic_capabilities=capabilities,
        required_event_types=tuple(event_types),
        decision_task_refs=task_refs,
        semantic_rubric_ref=rubric_ref,
        outcome=outcome,
        applicability=applicability,
        missing_policy=missing_policy,
        allowed_aggregation_methods=aggregations,
        default_aggregation_method=default_aggregation,
        status=status,
        definition_schema_version=schema_version,
        reason_codes=dict(payload.get("reason_codes", {})),
        publication_gate=dict(payload.get("publication_gate", {})),
        adjudication_uncertainty_policy=dict(payload.get("adjudication_uncertainty_policy", {})),
        metadata=dict(payload.get("metadata", {})),
        definition_hash=computed_hash,
        raw_definition=dict(hash_payload),
    )
