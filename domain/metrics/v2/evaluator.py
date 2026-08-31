"""Deterministic interpreter for the bounded V2 metric-definition DSL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from .canonical_hash import canonical_hash
from .definition_schema import MetricDefinition
from .models import (
    DecisionStatus,
    EligibilityStatus,
    EvaluationInput,
    MetricEvaluation,
    SemanticCapabilityStatus,
    SemanticDecisionFact,
)
from .query_context import ClassificationState


class TruthValue(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class EvaluationInvariantError(ValueError):
    """The accepted semantic inputs violate a domain invariant."""


def _event_attr(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        if name in event:
            return event[name]
        value = event.get("event_value", {})
    else:
        if hasattr(event, name):
            return getattr(event, name)
        value = getattr(event, "event_value", {})
    if isinstance(value, Mapping):
        return value.get(name, default)
    return default


def _event_value(event: Any) -> Mapping[str, Any]:
    if isinstance(event, Mapping):
        value = event.get("event_value", {})
    else:
        value = getattr(event, "event_value", {})
    return value if isinstance(value, Mapping) else {}


def _event_id(event: Any) -> str | None:
    for name in ("event_pub_id", "pub_id", "event_id"):
        value = _event_attr(event, name)
        if isinstance(value, str) and value:
            return value
    return None


def _as_status(value: object | None) -> str:
    if value is None:
        return "missing"
    if isinstance(value, SemanticCapabilityStatus):
        return value.value
    nested_status = getattr(value, "status", None)
    if nested_status is not None:
        return _as_status(nested_status)
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


_CAPABILITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "substantive_entity_mention": ("entity_mention",),
    "claim_evidence_verdict": ("claim_verification",),
    "stance_and_pairwise": ("sentiment_or_stance", "pairwise_preference"),
}


def _capability_status(subject: EvaluationInput, name: str) -> str:
    candidates = (name, *_CAPABILITY_ALIASES.get(name, ()))
    for candidate in candidates:
        if candidate in subject.capability_statuses:
            return _as_status(subject.capability_statuses[candidate])
    return "missing"


def _decision(subject: EvaluationInput, task_ref: str) -> SemanticDecisionFact | None:
    direct = subject.decisions.get(task_ref)
    if direct is not None:
        return direct
    task_name = task_ref.split("@", 1)[0]
    return subject.decisions.get(task_name)


def _decision_failure_reason(decision: SemanticDecisionFact, fallback: str) -> str:
    """Preserve actionable infrastructure failures through metric projections."""

    execution_failures = {
        "chunk_incomplete",
        "decision_method_not_allowed",
        "dependency_failed",
        "evidence_retrieval_failed",
        "judge_policy_not_published_for_official_use",
        "model_timeout",
        "model_unavailable_for_policy",
        "output_schema_hash_mismatch",
        "prompt_template_hash_mismatch",
        "required_judge_role_missing",
        "rubric_hash_mismatch",
        "structured_output_invalid",
        "truth_as_of_policy_invalid",
    }
    return next(
        (
            code
            for code in decision.reason_codes
            if code.startswith(("llm_api_", "upstream_")) or code in execution_failures
        ),
        fallback,
    )


def _failed_query_context_decisions(
    subject: EvaluationInput,
) -> tuple[SemanticDecisionFact, ...]:
    task_names = {"query-intent", "query-brand-entity-resolution"}
    by_id: dict[str, SemanticDecisionFact] = {}
    for task_ref, decision in sorted(subject.decisions.items()):
        if task_ref.split("@", 1)[0] not in task_names:
            continue
        if decision.status is not DecisionStatus.FAILED:
            continue
        identity = decision.decision_pub_id or task_ref
        by_id[identity] = decision
    return tuple(by_id[key] for key in sorted(by_id))


def _decision_result_is_unknown(value: Mapping[str, Any]) -> bool:
    semantic_label_keys = {
        "label",
        "polarity",
        "relation",
        "verdict",
        "resolution",
        "result_label",
    }
    return (
        any(
            key in semantic_label_keys and (item is None or item == "unknown")
            for key, item in value.items()
        )
        or value.get("is_unknown") is True
    )


def _unknown_scalar(value: Any) -> bool:
    return value is None or value == "unknown"


def _compare(left: Any, operator: str, right: Any) -> TruthValue:
    if _unknown_scalar(left):
        return TruthValue.UNKNOWN
    try:
        left_decimal = Decimal(str(left))
        right_decimal = Decimal(str(right))
    except (InvalidOperation, ValueError):
        return TruthValue.UNKNOWN
    comparisons = {
        "eq": left_decimal == right_decimal,
        "ne": left_decimal != right_decimal,
        "lt": left_decimal < right_decimal,
        "lte": left_decimal <= right_decimal,
        "gt": left_decimal > right_decimal,
        "gte": left_decimal >= right_decimal,
    }
    return TruthValue.TRUE if comparisons[operator] else TruthValue.FALSE


def _resolve_entity(reference: Any, subject: EvaluationInput) -> Any:
    return subject.focal_entity_id if reference == "$focal_entity" else reference


def _base_event_matches(event: Any, selector: Mapping[str, Any], subject: EvaluationInput) -> bool:
    if _event_attr(event, "event_type") != selector.get("type"):
        return False
    expected_subject = selector.get("subject")
    actual_subject = _event_attr(event, "subject_entity_id")
    if expected_subject == "$other_than_focal":
        if actual_subject in {None, subject.focal_entity_id}:
            return False
    elif expected_subject is not None and actual_subject != _resolve_entity(
        expected_subject, subject
    ):
        return False
    expected_object = selector.get("object")
    return expected_object is None or _event_attr(event, "object_entity_id") == _resolve_entity(
        expected_object, subject
    )


def _where_truth(event: Any, where: Mapping[str, Any]) -> TruthValue:
    saw_unknown = False
    value = _event_value(event)
    for field_name, expected in where.items():
        actual = _event_attr(event, field_name, value.get(field_name))
        if _unknown_scalar(actual):
            saw_unknown = True
            continue
        if isinstance(expected, list | tuple):
            if actual not in expected:
                return TruthValue.FALSE
        elif actual != expected:
            return TruthValue.FALSE
    return TruthValue.UNKNOWN if saw_unknown else TruthValue.TRUE


class _Interpreter:
    def __init__(self, subject: EvaluationInput) -> None:
        self.subject = subject
        self.supporting_events: set[str] = set()
        self.supporting_decisions: set[str] = set()

    def _candidate_events(self, selector: Mapping[str, Any]) -> tuple[Any, ...]:
        events = tuple(
            event
            for event in self.subject.events
            if _base_event_matches(event, selector, self.subject)
        )
        for event in events:
            event_id = _event_id(event)
            if event_id:
                self.supporting_events.add(event_id)
        return events

    def predicate(self, node: Mapping[str, Any]) -> TruthValue:
        name, value = next(iter(node.items()))
        if name == "all":
            results = tuple(self.predicate(child) for child in value)
            if TruthValue.FALSE in results:
                return TruthValue.FALSE
            if TruthValue.UNKNOWN in results:
                return TruthValue.UNKNOWN
            return TruthValue.TRUE
        if name == "any":
            results = tuple(self.predicate(child) for child in value)
            if TruthValue.TRUE in results:
                return TruthValue.TRUE
            if TruthValue.UNKNOWN in results:
                return TruthValue.UNKNOWN
            return TruthValue.FALSE
        if name == "not":
            result = self.predicate(value)
            if result is TruthValue.UNKNOWN:
                return result
            return TruthValue.FALSE if result is TruthValue.TRUE else TruthValue.TRUE
        if name == "query_has_lens":
            if self.subject.query_context.classification_state is not ClassificationState.READY:
                return TruthValue.UNKNOWN
            return (
                TruthValue.TRUE
                if value in self.subject.query_context.analysis_lenses
                else TruthValue.FALSE
            )
        if name == "query_has_operation":
            if self.subject.query_context.classification_state is not ClassificationState.READY:
                return TruthValue.UNKNOWN
            return (
                TruthValue.TRUE
                if value in self.subject.query_context.requested_operations
                else TruthValue.FALSE
            )
        if name == "exposure_is":
            if self.subject.exposure_role.value == "unknown":
                return TruthValue.UNKNOWN
            return (
                TruthValue.TRUE if self.subject.exposure_role.value == value else TruthValue.FALSE
            )
        if name in {"event_exists", "event_applicable_only"}:
            events = self._candidate_events(value)
            saw_unknown = False
            for event in events:
                result = _where_truth(event, value.get("where", {}))
                if result is TruthValue.TRUE:
                    return result
                saw_unknown = saw_unknown or result is TruthValue.UNKNOWN
            return TruthValue.UNKNOWN if saw_unknown else TruthValue.FALSE
        if name in {"event_value_equals", "event_numeric_compare"}:
            selector = {
                key: item
                for key, item in value.items()
                if key in {"type", "subject", "object", "where"}
            }
            events = self._candidate_events(selector)
            saw_unknown = False
            for event in events:
                where_result = _where_truth(event, selector.get("where", {}))
                if where_result is TruthValue.FALSE:
                    continue
                actual = _event_attr(event, value["field"], _event_value(event).get(value["field"]))
                if name == "event_value_equals":
                    result = (
                        TruthValue.UNKNOWN
                        if _unknown_scalar(actual)
                        else TruthValue.TRUE
                        if actual == value["value"]
                        else TruthValue.FALSE
                    )
                else:
                    result = _compare(actual, value["op"], value["value"])
                if where_result is TruthValue.UNKNOWN and result is not TruthValue.FALSE:
                    result = TruthValue.UNKNOWN
                if result is TruthValue.TRUE:
                    return result
                saw_unknown = saw_unknown or result is TruthValue.UNKNOWN
            return TruthValue.UNKNOWN if saw_unknown else TruthValue.FALSE
        if name == "capability_status_is":
            actual = _capability_status(self.subject, value["name"])
            if actual == "missing":
                return TruthValue.UNKNOWN
            return TruthValue.TRUE if actual == value["status"] else TruthValue.FALSE
        if name == "decision_exists":
            decision = _decision(self.subject, value)
            if decision and decision.decision_pub_id:
                self.supporting_decisions.add(decision.decision_pub_id)
            return (
                TruthValue.TRUE
                if decision is not None and decision.status is DecisionStatus.ACCEPTED
                else TruthValue.FALSE
            )
        if name in {"decision_value_equals", "decision_numeric_compare"}:
            decision = _decision(self.subject, value["task_ref"])
            if decision is None or decision.status is not DecisionStatus.ACCEPTED:
                return TruthValue.UNKNOWN
            if decision.decision_pub_id:
                self.supporting_decisions.add(decision.decision_pub_id)
            actual = decision.value.get(value["field"])
            if name == "decision_value_equals":
                if _unknown_scalar(actual):
                    return TruthValue.UNKNOWN
                return TruthValue.TRUE if actual == value["value"] else TruthValue.FALSE
            return _compare(actual, value["op"], value["value"])
        if name == "manifest_status_is":
            actual = self.subject.answer_fields.get("manifest_status")
            if actual is None:
                return TruthValue.UNKNOWN
            return TruthValue.TRUE if actual == value else TruthValue.FALSE
        if name == "answer_field_equals":
            actual = self.subject.answer_fields.get(value["field"])
            if actual is None:
                return TruthValue.UNKNOWN
            return TruthValue.TRUE if actual == value["value"] else TruthValue.FALSE
        if name == "all_answers":
            return TruthValue.TRUE
        if name == "custom_missing_policy":
            return TruthValue.TRUE
        raise EvaluationInvariantError(f"validated definition contains unsupported node: {name}")

    def outcome(self, node: Mapping[str, Any]) -> tuple[TruthValue, Any, Decimal, Decimal]:
        name, config = next(iter(node.items()))
        if name == "binary_outcome":
            result = self.predicate(config)
            if result is TruthValue.UNKNOWN:
                return result, None, Decimal("0"), Decimal("0")
            hit = result is TruthValue.TRUE
            return result, hit, Decimal(int(hit)), Decimal("1")
        if name == "count_outcome":
            if "from_events" in config:
                selector = {
                    "type": config["from_events"],
                    **(
                        {"subject": config["denominator_subject"]}
                        if "denominator_subject" in config
                        else {}
                    ),
                }
                events = self._candidate_events(selector)
                denominator_events = tuple(
                    event
                    for event in events
                    if _where_truth(event, config.get("denominator_where", {})) is TruthValue.TRUE
                )
                numerator_events = tuple(
                    event
                    for event in denominator_events
                    if _where_truth(event, config.get("numerator_where", {})) is TruthValue.TRUE
                    and (
                        "numerator_subject" not in config
                        or _base_event_matches(
                            event,
                            {
                                "type": config["from_events"],
                                "subject": config["numerator_subject"],
                            },
                            self.subject,
                        )
                    )
                )
                unique_by = config.get("unique_by")
                if unique_by is not None:
                    denominator_events = tuple(
                        {
                            _event_attr(event, unique_by): event for event in denominator_events
                        }.values()
                    )
                    numerator_events = tuple(
                        {
                            _event_attr(event, unique_by): event for event in numerator_events
                        }.values()
                    )
                denominator = Decimal(len(denominator_events))
                numerator = Decimal(len(numerator_events))
                if config.get("credit_total") == "one_per_answer" and denominator:
                    numerator /= denominator
                    denominator = Decimal("1")
                return (
                    TruthValue.TRUE if numerator else TruthValue.FALSE,
                    {"numerator": numerator, "denominator": denominator},
                    numerator,
                    denominator,
                )
            decision_name = config["from_decisions"]
            matching = [
                decision
                for task_ref, decision in self.subject.decisions.items()
                if task_ref.split("@", 1)[0] == decision_name
                and decision.status is DecisionStatus.ACCEPTED
            ]
            labels = tuple(config.get("numerator_labels", ()))
            denominator_labels = tuple(config.get("denominator_labels", ()))
            partial_credit = Decimal(str(config.get("partial_credit", 0)))
            numerator = Decimal("0")
            denominator = Decimal("0")
            for decision in matching:
                items = decision.value.get("items", ())
                if not isinstance(items, list | tuple):
                    items = (decision.value,)
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    label = item.get("label")
                    if label in {"unknown", None}:
                        continue
                    if denominator_labels and label not in denominator_labels:
                        continue
                    denominator += Decimal("1")
                    if label in labels:
                        numerator += (
                            partial_credit if label == "partially_covered" else Decimal("1")
                        )
                if decision.decision_pub_id:
                    self.supporting_decisions.add(decision.decision_pub_id)
            return (
                TruthValue.TRUE if numerator else TruthValue.FALSE,
                {"numerator": numerator, "denominator": denominator},
                numerator,
                denominator,
            )
        if name == "numeric_outcome":
            selector = {
                key: item
                for key, item in {
                    "type": config["from_events"],
                    "subject": config.get("subject"),
                    "object": config.get("object"),
                }.items()
                if item is not None
            }
            events = tuple(
                event
                for event in self._candidate_events(selector)
                if _where_truth(event, config.get("where", {})) is TruthValue.TRUE
            )
            values: list[Decimal] = []
            for event in events:
                raw_value = _event_attr(
                    event, config["field"], _event_value(event).get(config["field"])
                )
                try:
                    values.append(Decimal(str(raw_value)))
                except (InvalidOperation, ValueError):
                    return TruthValue.UNKNOWN, None, Decimal("0"), Decimal("0")
            if not values:
                return TruthValue.FALSE, None, Decimal("0"), Decimal("0")
            aggregate = config["aggregate"]
            if aggregate == "sum":
                numeric_result = sum(values, Decimal("0"))
                denominator = Decimal("1")
            elif aggregate == "mean":
                numeric_result = sum(values, Decimal("0"))
                denominator = Decimal(len(values))
            elif aggregate == "min":
                numeric_result = min(values)
                denominator = Decimal("1")
            elif aggregate == "max":
                numeric_result = max(values)
                denominator = Decimal("1")
            else:
                numeric_result = values[0]
                denominator = Decimal("1")
            return (
                TruthValue.TRUE,
                numeric_result / denominator,
                numeric_result,
                denominator,
            )
        raise EvaluationInvariantError(f"validated definition contains unsupported outcome: {name}")


def _reason(definition: MetricDefinition, key: str, fallback: str) -> str:
    return definition.reason_codes.get(key, fallback)


def _query_failure_reason(
    definition: MetricDefinition, subject: EvaluationInput, interpreter: _Interpreter
) -> tuple[TruthValue, str]:
    """Evaluate the full predicate, then retain the fixed reason priority."""

    result = interpreter.predicate(definition.query_predicate)
    if result is TruthValue.TRUE:
        return result, ""
    # Re-evaluating bounded nodes is deterministic and lets reasons be stable regardless
    # of JSON child order.
    checks = (
        ("query_has_lens", "query_lens_mismatch"),
        ("query_has_operation", "query_operation_mismatch"),
        ("exposure_is", "exposure_mismatch"),
    )

    def find_nodes(node: Mapping[str, Any], target: str) -> list[Mapping[str, Any]]:
        name, value = next(iter(node.items()))
        if name == target:
            return [node]
        if name in {"all", "any"}:
            return [found for child in value for found in find_nodes(child, target)]
        if name == "not":
            return find_nodes(value, target)
        return []

    for node_name, reason in checks:
        node_results = [
            interpreter.predicate(node)
            for node in find_nodes(definition.query_predicate, node_name)
        ]
        if TruthValue.FALSE in node_results:
            return TruthValue.FALSE, reason
    if result is TruthValue.FALSE:
        return result, "query_context_predicate_mismatch"
    if subject.exposure_role.value == "unknown":
        return result, "unknown_entity_resolution"
    return result, "unknown_query_context"


class MetricEvaluator:
    """Evaluate a frozen answer once, following section 23.2 state priority."""

    def evaluate(self, definition: MetricDefinition, subject: EvaluationInput) -> MetricEvaluation:
        if definition.focal_entity_required and not subject.focal_entity_id:
            raise EvaluationInvariantError("metric requires a focal entity")
        interpreter = _Interpreter(subject)
        if not subject.collection_eligible:
            return self._result(
                definition,
                subject,
                EligibilityStatus.EXCLUDED,
                "collection_ineligible",
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if subject.query_context.classification_state is ClassificationState.FAILED:
            failed_query_decisions = _failed_query_context_decisions(subject)
            for decision in failed_query_decisions:
                if decision.decision_pub_id:
                    interpreter.supporting_decisions.add(decision.decision_pub_id)
            reason = next(
                (
                    candidate
                    for decision in failed_query_decisions
                    if (candidate := _decision_failure_reason(decision, ""))
                ),
                "query_context_analysis_failed",
            )
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                reason,
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        predicate_result, predicate_reason = _query_failure_reason(definition, subject, interpreter)
        if predicate_result is TruthValue.FALSE:
            return self._result(
                definition,
                subject,
                EligibilityStatus.EXCLUDED,
                predicate_reason,
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        required_task_refs: set[str] = set(definition.decision_task_refs)
        required_task_refs.update(
            capability.task_ref for capability in definition.required_semantic_capabilities
        )
        # Pre-scan the whole required boundary so an earlier review/unknown state
        # cannot hide a known execution failure in another required capability.
        for capability in definition.required_semantic_capabilities:
            if _capability_status(subject, capability.name) != "failed":
                continue
            failed_decision = _decision(subject, capability.task_ref)
            if failed_decision and failed_decision.decision_pub_id:
                interpreter.supporting_decisions.add(failed_decision.decision_pub_id)
            reason = (
                _decision_failure_reason(failed_decision, "semantic_analysis_failed")
                if failed_decision is not None
                else "semantic_analysis_failed"
            )
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                reason,
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        for task_ref in sorted(required_task_refs):
            failed_decision = _decision(subject, task_ref)
            if failed_decision is None or failed_decision.status is not DecisionStatus.FAILED:
                continue
            if failed_decision.decision_pub_id:
                interpreter.supporting_decisions.add(failed_decision.decision_pub_id)
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                _decision_failure_reason(failed_decision, "decision_failed"),
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if predicate_result is TruthValue.UNKNOWN:
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_UNKNOWN,
                predicate_reason,
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        for capability in definition.required_semantic_capabilities:
            capability_status = _capability_status(subject, capability.name)
            if capability_status != capability.accepted_status:
                failed_decision = _decision(subject, capability.task_ref)
                if failed_decision and failed_decision.decision_pub_id:
                    interpreter.supporting_decisions.add(failed_decision.decision_pub_id)
                reason = {
                    "failed": "semantic_analysis_failed",
                    "abstained": "decision_abstained",
                    "review_required": "semantic_review_required",
                    "not_requested": "required_decision_missing",
                    "missing": "required_decision_missing",
                }.get(capability_status, "semantic_result_unknown")
                if failed_decision is not None:
                    reason = _decision_failure_reason(failed_decision, reason)
                return self._result(
                    definition,
                    subject,
                    (
                        EligibilityStatus.ANALYSIS_FAILED
                        if capability_status == "failed"
                        else EligibilityStatus.ANALYSIS_UNKNOWN
                    ),
                    reason,
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
        for task_ref in sorted(required_task_refs):
            decision = _decision(subject, task_ref)
            if decision is None:
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.ANALYSIS_UNKNOWN,
                    "required_decision_missing",
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
            if decision.decision_pub_id:
                interpreter.supporting_decisions.add(decision.decision_pub_id)
            if decision.status is not DecisionStatus.ACCEPTED:
                reason = {
                    DecisionStatus.FAILED: "decision_failed",
                    DecisionStatus.ABSTAINED: "decision_abstained",
                    DecisionStatus.REVIEW_REQUIRED: "decision_review_required",
                    DecisionStatus.MISSING: "required_decision_missing",
                }.get(decision.status, "required_decision_missing")
                reason = _decision_failure_reason(decision, reason)
                return self._result(
                    definition,
                    subject,
                    (
                        EligibilityStatus.ANALYSIS_FAILED
                        if decision.status is DecisionStatus.FAILED
                        else EligibilityStatus.ANALYSIS_UNKNOWN
                    ),
                    reason,
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
            if _decision_result_is_unknown(decision.value):
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.ANALYSIS_UNKNOWN,
                    "semantic_result_unknown",
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
            if not decision.policy_matches:
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.ANALYSIS_UNKNOWN,
                    "decision_policy_mismatch",
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
            if not decision.evidence_ready:
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.ANALYSIS_FAILED,
                    "evidence_retrieval_failed",
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
        if not subject.event_invariants_valid:
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                "semantic_event_integrity_failed",
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if not subject.evidence_spans_valid:
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                "evidence_span_integrity_failed",
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if not subject.evidence_retrieval_ready:
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_FAILED,
                "evidence_retrieval_failed",
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if definition.applicability is not None:
            applicability = interpreter.predicate(definition.applicability)
            if applicability is TruthValue.UNKNOWN:
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.ANALYSIS_UNKNOWN,
                    _reason(definition, "unknown", "required_event_unknown"),
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
            if applicability is TruthValue.FALSE:
                return self._result(
                    definition,
                    subject,
                    EligibilityStatus.NOT_APPLICABLE,
                    _reason(definition, "not_applicable", "no_applicable_claim"),
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    interpreter,
                )
        outcome_truth, outcome_value, numerator, denominator = interpreter.outcome(
            definition.outcome
        )
        if outcome_truth is TruthValue.UNKNOWN:
            return self._result(
                definition,
                subject,
                EligibilityStatus.ANALYSIS_UNKNOWN,
                _reason(definition, "unknown", "semantic_result_unknown"),
                None,
                Decimal("0"),
                Decimal("0"),
                interpreter,
            )
        if denominator == 0:
            return self._result(
                definition,
                subject,
                EligibilityStatus.NOT_APPLICABLE,
                _reason(definition, "not_applicable", "no_applicable_claim"),
                outcome_value,
                numerator,
                denominator,
                interpreter,
            )
        hit = numerator > 0
        return self._result(
            definition,
            subject,
            EligibilityStatus.INCLUDED_HIT if hit else EligibilityStatus.INCLUDED_MISS,
            _outcome_reason(definition, subject, hit),
            outcome_value,
            numerator,
            denominator,
            interpreter,
        )

    @staticmethod
    def _result(
        definition: MetricDefinition,
        subject: EvaluationInput,
        status: EligibilityStatus,
        reason: str,
        outcome: Any,
        numerator: Decimal,
        denominator: Decimal,
        interpreter: _Interpreter,
    ) -> MetricEvaluation:
        result = MetricEvaluation(
            answer_pub_id=subject.answer_pub_id,
            query_key=subject.query_context.query_key,
            focal_entity_id=subject.focal_entity_id,
            metric_name=definition.name,
            metric_version=definition.version,
            metric_definition_hash=definition.definition_hash,
            eligibility_status=status,
            reason_codes=(reason,),
            outcome_value=outcome,
            numerator_contribution=numerator,
            denominator_contribution=denominator,
            supporting_event_pub_ids=tuple(sorted(interpreter.supporting_events)),
            supporting_decision_pub_ids=tuple(sorted(interpreter.supporting_decisions)),
            query_context_fact_pub_id=subject.query_context_fact_pub_id,
            semantic_manifest_pub_id=subject.semantic_manifest_pub_id,
            semantic_decision_set_hash=subject.semantic_decision_set_hash,
        )
        hashed = canonical_hash(result)
        return replace(result, evaluation_hash=hashed)


def _outcome_reason(definition: MetricDefinition, subject: EvaluationInput, hit: bool) -> str:
    family = definition.metadata.get("metric_family")
    events = tuple(subject.events)
    if family in {"topk", "topk_given_rankable"}:
        rank_events = tuple(
            event
            for event in events
            if _event_attr(event, "event_type") == "recommendation_list_rank"
            and _event_attr(event, "ordered", _event_value(event).get("ordered")) is True
        )
        if not rank_events:
            return "no_rankable_list"
        target_ranks = tuple(
            event
            for event in rank_events
            if _event_attr(event, "subject_entity_id") == subject.focal_entity_id
        )
        if not target_ranks:
            return "target_not_in_ranked_list"
        return "rank_within_k" if hit else "rank_above_k"
    if family in {
        "recommendation",
        "prompted_recommendation_distribution",
        "competitor_anchored",
        "unsolicited_recommendation",
    }:
        relations = tuple(
            _event_attr(event, "polarity", _event_value(event).get("polarity"))
            for event in events
            if _event_attr(event, "event_type") == "recommendation_relation"
            and _event_attr(event, "subject_entity_id") == subject.focal_entity_id
        )
        polarity_reasons = {
            "positive": "recommendation_positive",
            "conditional_positive": "recommendation_conditional_positive",
            "negative": "recommendation_negative",
            "neutral": "recommendation_neutral_or_absent",
            "neutral_or_absent": "recommendation_neutral_or_absent",
        }
        if len(relations) == 1 and relations[0] in polarity_reasons:
            return polarity_reasons[relations[0]]
    return _reason(
        definition,
        "hit" if hit else "miss",
        "included_hit" if hit else "included_miss",
    )


def evaluate_metric(definition: MetricDefinition, subject: EvaluationInput) -> MetricEvaluation:
    return MetricEvaluator().evaluate(definition, subject)
