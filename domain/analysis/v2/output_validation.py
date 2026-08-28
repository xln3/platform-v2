"""Strict structured-output and semantic invariant validation.

This module deliberately implements the small JSON-Schema subset used by the
version-controlled task definitions so the pure domain has no optional schema
library dependency.  Unknown keywords fail closed when definitions are loaded
by tests, while outputs fail with stable machine-readable codes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from domain.analysis.v2._canonical import canonical_json
from domain.analysis.v2.candidates import (
    CandidateBoundaryError,
    CandidateSet,
    validate_candidate_membership,
)
from domain.analysis.v2.decision_task_schema import DecisionTaskDefinition

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SCHEMA_KEYWORDS = {
    "$id",
    "$schema",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
    "uniqueItems",
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionOutputValidation:
    output: dict[str, Any] | None
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(sorted({issue.code for issue in self.issues}))

    def raise_for_errors(self) -> None:
        if self.issues:
            raise StructuredOutputError(self.issues)


class StructuredOutputError(ValueError):
    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        self.reason_codes = tuple(sorted({issue.code for issue in issues}))
        super().__init__(self.reason_codes[0] if self.reason_codes else "structured_output_invalid")


def validate_schema_definition(schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Validate the supported schema vocabulary before any judge is called."""

    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"unsupported_json_schema_keywords:{path}:{','.join(sorted(unknown))}")
    schema_type = schema.get("type")
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    declared_types = {schema_type} if isinstance(schema_type, str) else set(schema_type or ())
    if not declared_types or not declared_types <= allowed_types:
        raise ValueError(f"json_schema_type_invalid:{path}")
    if "object" in declared_types:
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError(f"json_schema_object_properties_required:{path}")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"json_schema_object_must_be_closed:{path}")
        required = schema.get("required", ())
        if not isinstance(required, Sequence) or isinstance(required, str | bytes):
            raise ValueError(f"json_schema_required_invalid:{path}")
        if not set(required) <= set(properties):
            raise ValueError(f"json_schema_required_property_missing:{path}")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise ValueError(f"json_schema_property_invalid:{path}")
            validate_schema_definition(child, path=f"{path}.{name}")
    if "array" in declared_types:
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ValueError(f"json_schema_array_items_required:{path}")
        validate_schema_definition(items, path=f"{path}[*]")
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in schema:
            alternatives = schema[keyword]
            if not isinstance(alternatives, Sequence) or isinstance(alternatives, str | bytes):
                raise ValueError(f"json_schema_composition_invalid:{path}")
            for index, child in enumerate(alternatives):
                if not isinstance(child, Mapping):
                    raise ValueError(f"json_schema_composition_invalid:{path}")
                validate_schema_definition(child, path=f"{path}.{keyword}[{index}]")


def validate_decision_output(
    *,
    task: DecisionTaskDefinition,
    output: object,
    candidate_set: CandidateSet | None = None,
    answer_text: str | None = None,
    expected_answer_text_hash: str | None = None,
    evidence_context: Mapping[str, Any] | None = None,
) -> DecisionOutputValidation:
    """Validate schema, candidates, spans, and task-specific invariants."""

    issues: list[ValidationIssue] = []
    _validate_instance(output, task.output_schema, "$", issues)
    if not isinstance(output, dict):
        return DecisionOutputValidation(output=None, issues=tuple(_deduplicate_issues(issues)))

    if not issues:
        try:
            validate_candidate_membership(
                output,
                policy=task.candidate_policy,
                candidate_set=candidate_set,
            )
        except CandidateBoundaryError as error:
            issues.append(
                ValidationIssue(error.code, error.path or "$", _safe_value_detail(error.value))
            )

    if expected_answer_text_hash is not None:
        if (
            answer_text is None
            or sha256(answer_text.encode()).hexdigest() != expected_answer_text_hash
        ):
            issues.append(ValidationIssue("answer_text_hash_mismatch", "$"))

    spans = _declared_spans(output)
    if answer_evidence_required(task, output):
        if len(spans) < task.evidence_requirements.minimum_span_count:
            issues.append(ValidationIssue("required_evidence_span_missing", "$"))
    if spans:
        if answer_text is None:
            issues.append(ValidationIssue("answer_text_required_for_span_validation", "$"))
        else:
            for path, span in spans:
                _validate_span(span, answer_text, path, issues)

    _validate_task_invariants(task.name, output, evidence_context or {}, issues)

    deduplicated = tuple(_deduplicate_issues(issues))
    return DecisionOutputValidation(
        output=output if not deduplicated else None, issues=deduplicated
    )


def validate_structured_output(
    output: object, schema: Mapping[str, Any]
) -> DecisionOutputValidation:
    """Validate a correction against its frozen closed output schema.

    Manual correction endpoints do not rerun the model or infer new evidence;
    they must nevertheless submit a complete, schema-valid replacement result.
    """

    issues: list[ValidationIssue] = []
    _validate_instance(output, schema, "$", issues)
    deduplicated = tuple(_deduplicate_issues(issues))
    return DecisionOutputValidation(
        output=output if isinstance(output, dict) and not deduplicated else None,
        issues=deduplicated,
    )


def answer_evidence_required(task: DecisionTaskDefinition, output: Mapping[str, Any]) -> bool:
    """Require answer spans only when the output asserts an answer fact.

    Known empty/negative results are measurements, not missing analysis.  They
    must remain eligible denominator observations without inventing a source
    span that does not exist.
    """

    if not task.evidence_requirements.requires_answer_spans:
        return False
    if task.name == "answer-entity-resolution":
        return bool(output.get("resolutions"))
    if task.name == "substantive-entity-mention":
        return output.get("substantive") is True
    if task.name == "recommendation-relation":
        return output.get("polarity") in {
            "positive",
            "conditional_positive",
            "negative",
        }
    if task.name == "rank-semantics":
        return bool(output.get("rank_events"))
    if task.name == "stance-and-pairwise":
        return output.get("polarity") not in {None, "unknown"} or output.get("relation") not in {
            None,
            "unknown",
        }
    if task.name == "answer-dimension-coverage":
        return output.get("status") in {"covered", "partially_covered"}
    if task.name == "claim-extraction":
        return bool(output.get("claims"))
    if task.name == "risk-adjudication":
        return output.get("verdict") in {"confirmed", "dismissed"}
    return True


def validate_subject_ref(
    *, task: DecisionTaskDefinition, subject_ref: object
) -> DecisionOutputValidation:
    """Validate a complete composite subject before deriving its subject key."""

    issues: list[ValidationIssue] = []
    _validate_instance(subject_ref, task.subject_ref_schema, "$", issues)
    deduplicated = tuple(_deduplicate_issues(issues))
    return DecisionOutputValidation(
        output=subject_ref if isinstance(subject_ref, dict) and not deduplicated else None,
        issues=deduplicated,
    )


def _validate_instance(
    value: object,
    schema: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    for keyword in ("allOf",):
        for child in schema.get(keyword, ()):
            _validate_instance(value, child, path, issues)
    if "anyOf" in schema:
        if not any(_matches_schema(value, child) for child in schema["anyOf"]):
            issues.append(ValidationIssue("structured_output_any_of_invalid", path))
            return
    if "oneOf" in schema:
        if sum(_matches_schema(value, child) for child in schema["oneOf"]) != 1:
            issues.append(ValidationIssue("structured_output_one_of_invalid", path))
            return

    declared = schema.get("type")
    types = (declared,) if isinstance(declared, str) else tuple(declared or ())
    if not any(_is_json_type(value, expected) for expected in types):
        issues.append(ValidationIssue("structured_output_type_invalid", path))
        return
    if "const" in schema and canonical_json(value) != canonical_json(schema["const"]):
        issues.append(ValidationIssue("structured_output_const_invalid", path))
    if "enum" in schema and not any(
        canonical_json(value) == canonical_json(option) for option in schema["enum"]
    ):
        issues.append(ValidationIssue("structured_output_enum_invalid", path))

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        for name in required:
            if name not in value:
                issues.append(
                    ValidationIssue("structured_output_required_missing", f"{path}.{name}")
                )
        if schema.get("additionalProperties") is False:
            for name in set(value) - set(properties):
                issues.append(
                    ValidationIssue("structured_output_additional_property", f"{path}.{name}")
                )
        for name, item in value.items():
            if name in properties:
                _validate_instance(item, properties[name], f"{path}.{name}", issues)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if len(value) < schema.get("minItems", 0):
            issues.append(ValidationIssue("structured_output_too_few_items", path))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            issues.append(ValidationIssue("structured_output_too_many_items", path))
        if schema.get("uniqueItems") and len({canonical_json(item) for item in value}) != len(
            value
        ):
            issues.append(ValidationIssue("structured_output_items_not_unique", path))
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_instance(item, item_schema, f"{path}[{index}]", issues)
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            issues.append(ValidationIssue("structured_output_string_too_short", path))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            issues.append(ValidationIssue("structured_output_string_too_long", path))
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            issues.append(ValidationIssue("structured_output_pattern_invalid", path))
        if "format" in schema and not _format_is_valid(value, schema["format"]):
            issues.append(ValidationIssue("structured_output_format_invalid", path))
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(ValidationIssue("structured_output_number_too_small", path))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(ValidationIssue("structured_output_number_too_large", path))


def _matches_schema(value: object, schema: Mapping[str, Any]) -> bool:
    issues: list[ValidationIssue] = []
    _validate_instance(value, schema, "$", issues)
    return not issues


def _is_json_type(value: object, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray),
        "object": isinstance(value, Mapping),
    }.get(expected, False)


def _format_is_valid(value: str, format_name: str) -> bool:
    if format_name == "sha256":
        return _SHA256_RE.fullmatch(value) is not None
    if format_name == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    return False


def _declared_spans(output: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    found: list[tuple[str, Mapping[str, Any]]] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            if (
                isinstance(value.get("start"), int)
                and not isinstance(value.get("start"), bool)
                and isinstance(value.get("end"), int)
                and not isinstance(value.get("end"), bool)
            ):
                found.append((path, value))
            for name, item in value.items():
                visit(item, f"{path}.{name}")
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(output, "$")
    return tuple(found)


def _validate_span(
    span: Mapping[str, Any],
    answer_text: str,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    start = span.get("start")
    end = span.get("end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(answer_text)
    ):
        issues.append(ValidationIssue("evidence_span_out_of_bounds", path))
        return
    excerpt = answer_text[start:end]
    expected_hash = span.get("excerpt_hash")
    if expected_hash is not None and expected_hash != sha256(excerpt.encode()).hexdigest():
        issues.append(ValidationIssue("evidence_excerpt_hash_mismatch", path))
    anchored = next(
        (
            span.get(name)
            for name in ("surface", "quote", "evidence_text", "claim_text")
            if isinstance(span.get(name), str)
        ),
        None,
    )
    if anchored is not None and anchored != excerpt:
        issues.append(ValidationIssue("evidence_span_text_mismatch", path))


def _validate_task_invariants(
    task_name: str,
    output: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
    issues: list[ValidationIssue],
) -> None:
    if task_name in {"query-brand-entity-resolution", "answer-entity-resolution"}:
        for index, resolution in enumerate(output.get("resolutions", ())):
            state = resolution.get("resolution_state")
            entity_id = resolution.get("entity_id")
            candidates = resolution.get("candidate_entity_ids", ())
            path = f"$.resolutions[{index}]"
            if state == "resolved" and not entity_id:
                issues.append(ValidationIssue("resolved_entity_requires_entity_id", path))
            if state != "resolved" and entity_id is not None:
                issues.append(ValidationIssue("unresolved_entity_must_not_have_entity_id", path))
            if state == "ambiguous" and len(candidates) < 2:
                issues.append(
                    ValidationIssue("ambiguous_entity_requires_multiple_candidates", path)
                )
    elif task_name == "substantive-entity-mention":
        if output.get("substantive") == "unknown" and not output.get("reason_codes"):
            issues.append(ValidationIssue("semantic_unknown_requires_reason_code", "$"))
        if output.get("substantive") is True and output.get("mention_role") in {
            "prompt_echo",
            "citation_metadata",
        }:
            issues.append(ValidationIssue("non_body_mention_cannot_be_substantive", "$"))
    elif task_name == "recommendation-relation":
        polarity = output.get("polarity")
        if polarity == "conditional_positive" and not str(output.get("scenario") or "").strip():
            issues.append(ValidationIssue("conditional_recommendation_requires_scenario", "$"))
        if output.get("subject_resolution") == "query_context_coreference":
            if output.get("surface") is not None:
                issues.append(ValidationIssue("coreference_must_not_invent_answer_surface", "$"))
    elif task_name == "rank-semantics":
        for index, event in enumerate(output.get("rank_events", ())):
            _validate_rank_event(event, f"$.rank_events[{index}]", issues)
    elif task_name == "stance-and-pairwise":
        kind = output.get("kind")
        if kind == "stance" and output.get("relation") is not None:
            issues.append(ValidationIssue("stance_pairwise_fields_mutually_exclusive", "$"))
        if kind == "pairwise" and output.get("polarity") is not None:
            issues.append(ValidationIssue("stance_pairwise_fields_mutually_exclusive", "$"))
    elif task_name == "claim-evidence-verdict":
        _validate_claim_verdict(output, evidence_context, issues)
    elif task_name == "claim-verifiability":
        if (
            evidence_context.get("evidence_material_truncated") is True
            and output.get("verifiability") != "unknown"
        ):
            issues.append(ValidationIssue("truncated_evidence_requires_semantic_unknown", "$"))
    elif task_name == "citation-claim-support":
        if (
            evidence_context.get("evidence_material_truncated") is True
            and output.get("support_state") != "unknown"
        ):
            issues.append(ValidationIssue("truncated_evidence_requires_semantic_unknown", "$"))
        if output.get("support_state") in {"supports", "contradicts"}:
            if not output.get("evidence_snapshot_refs"):
                issues.append(ValidationIssue("citation_verdict_requires_frozen_evidence", "$"))


def _validate_rank_event(
    event: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    kind = event.get("event_type")
    if kind == "recommendation_list_rank":
        rank = event.get("rank")
        list_size = event.get("list_size")
        if event.get("ordered") is not True:
            issues.append(ValidationIssue("recommendation_rank_requires_ordered_list", path))
        if (
            not isinstance(rank, int)
            or not isinstance(list_size, int)
            or not 1 <= rank <= list_size
        ):
            issues.append(ValidationIssue("recommendation_rank_out_of_range", path))
        if not event.get("list_id"):
            issues.append(ValidationIssue("recommendation_rank_requires_list_id", path))
    elif kind == "market_rank_claim":
        low = event.get("rank_low")
        high = event.get("rank_high")
        if not isinstance(low, int) or not isinstance(high, int) or low < 1 or high < low:
            issues.append(ValidationIssue("market_rank_range_invalid", path))
    elif kind == "pairwise_preference":
        if not event.get("object_entity_id"):
            issues.append(ValidationIssue("pairwise_rank_requires_object", path))
    elif kind in {"mention_order", "source_result_rank"}:
        if not isinstance(event.get("ordinal"), int) or event["ordinal"] < 1:
            issues.append(ValidationIssue("ordinal_rank_invalid", path))


def _validate_claim_verdict(
    output: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
    issues: list[ValidationIssue],
) -> None:
    verdict = output.get("verdict")
    bundle_status = evidence_context.get("evidence_bundle_status")
    retrieval_complete = evidence_context.get("retrieval_protocol_complete") is True
    evidence_refs = output.get("evidence_snapshot_refs", ())
    if evidence_context.get("evidence_material_truncated") is True and verdict != "unknown":
        issues.append(ValidationIssue("truncated_evidence_requires_semantic_unknown", "$"))
    if verdict in {"supported", "contradicted"} and not evidence_refs:
        issues.append(ValidationIssue("claim_verdict_requires_frozen_evidence", "$"))
    if verdict == "unsupported" and not (bundle_status == "ready" and retrieval_complete):
        issues.append(ValidationIssue("unsupported_requires_complete_retrieval", "$"))
    if bundle_status in {"partial", "failed"} and verdict != "unknown":
        issues.append(ValidationIssue("evidence_retrieval_failure_requires_unknown", "$"))
    truth_policy = evidence_context.get("truth_as_of_policy")
    if truth_policy not in {"answer_capture_time", "snapshot_as_of"}:
        issues.append(ValidationIssue("truth_as_of_policy_invalid", "$"))


def _safe_value_detail(value: object) -> str | None:
    if value is None or isinstance(value, str | int | float | bool):
        rendered = str(value)
        return rendered[:100]
    return type(value).__name__


def _deduplicate_issues(issues: Sequence[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str, str | None]] = set()
    answer: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.code, issue.path, issue.detail)
        if key not in seen:
            seen.add(key)
            answer.append(issue)
    return answer
