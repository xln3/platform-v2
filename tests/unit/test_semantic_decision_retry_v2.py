from __future__ import annotations

import inspect

from geo_platform.metrics_v2.repository import _semantic_manifest_event_type

from workflows.activities.semantic_judge_llm import SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS
from workflows.definitions.semantic_decisions_v2 import (
    _MODEL_ACTIVITY_TIMEOUT,
    QueryContextClassificationWorkflowV2,
    _manifest_status_from_capability_states,
    _next_auto_rejudge_payload,
    _ready_decision_tasks,
    _record_dynamic_template_failure,
)


def _decision(reason_code: str) -> dict[str, object]:
    return {
        "decision_pub_id": "sdr_failed_0001",
        "status": "failed",
        "reason_codes": [reason_code],
    }


def test_retryable_llm_failure_creates_new_immutable_generation() -> None:
    payload = {
        "idempotency_key": "original-request",
        "rejudge_generation": 0,
        "attempts": [{"pub_id": "sda_failed_0001"}],
        "decision_pub_id": "sdr_forced_by_caller",
    }

    retry = _next_auto_rejudge_payload(payload, _decision("llm_api_rate_limited"))

    assert retry is not None
    assert retry["rejudge_generation"] == 1
    assert retry["supersedes_decision_pub_id"] == "sdr_failed_0001"
    assert retry["idempotency_key"] == "original-request:auto-rejudge:1"
    assert "attempts" not in retry
    assert "decision_pub_id" not in retry
    assert payload["rejudge_generation"] == 0


def test_auth_and_budget_failures_wait_for_operator_configuration() -> None:
    payload = {"idempotency_key": "original-request"}

    assert _next_auto_rejudge_payload(payload, _decision("llm_api_auth_missing")) is None
    assert _next_auto_rejudge_payload(payload, _decision("llm_api_budget_exhausted")) is None


def test_auto_rejudge_is_bounded() -> None:
    payload = {
        "idempotency_key": "original-request",
        "rejudge_generation": 2,
        "max_auto_rejudge_generations": 2,
    }

    assert _next_auto_rejudge_payload(payload, _decision("llm_api_timeout")) is None


def test_auto_rejudge_can_be_explicitly_disabled() -> None:
    payload = {
        "idempotency_key": "historical-backfill",
        "rejudge_generation": 0,
        "max_auto_rejudge_generations": 0,
    }

    assert _next_auto_rejudge_payload(payload, _decision("llm_api_timeout")) is None


def test_activity_timeout_exceeds_adapter_total_deadline_and_persistence_margin() -> None:
    assert _MODEL_ACTIVITY_TIMEOUT.total_seconds() >= (SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS + 360)


def test_query_context_workflow_propagates_and_updates_dependency_statuses() -> None:
    source = inspect.getsource(QueryContextClassificationWorkflowV2)

    assert 'dependency_statuses = dict(payload.get("dependency_statuses") or {})' in source
    assert '"dependency_statuses": dependency_statuses' in source
    assert "decision['task_name']" in source
    assert "decision['task_version']" in source
    assert 'decision["status"]' in source


def test_ready_decision_tasks_returns_one_dag_layer_in_stable_order() -> None:
    pending = [
        (0, {"task_ref": "root-a", "dependency_task_refs": []}),
        (1, {"task_ref": "child", "dependency_task_refs": ["root-a"]}),
        (2, {"task_ref": "root-b", "dependency_task_refs": []}),
    ]

    assert [index for index, _item in _ready_decision_tasks(pending, {})] == [0, 2]
    assert [
        index
        for index, _item in _ready_decision_tasks(
            pending[1:], {"root-a": "accepted", "root-b": "accepted"}
        )
    ] == [1, 2]


def test_ready_decision_tasks_fails_closed_without_hanging_on_missing_dependency() -> None:
    pending = [(7, {"task_ref": "orphan", "dependency_task_refs": ["missing"]})]

    assert _ready_decision_tasks(pending, {}) == pending


def test_failed_manifest_has_dedicated_outbox_event() -> None:
    assert _semantic_manifest_event_type("failed") == "answer.semantic_events.failed.v2"
    assert (
        _semantic_manifest_event_type("review_required")
        == "answer.semantic_events.review_required.v2"
    )


def test_missing_dynamic_template_forces_mapped_capability_failure() -> None:
    forced: dict[str, list[str]] = {}

    result = _record_dynamic_template_failure(forced, "claim-evidence-verdict@2.0.0")

    assert result["status"] == "failed"
    assert result["reason_codes"] == ["dynamic_task_template_missing"]
    assert forced == {"claim_evidence_verdict": ["dynamic_task_template_missing"]}


def test_workflow_manifest_status_does_not_let_review_hide_failure() -> None:
    assert _manifest_status_from_capability_states({"failed"}) == "failed"
    assert _manifest_status_from_capability_states({"failed", "not_requested"}) == "failed"
    assert _manifest_status_from_capability_states({"failed", "review_required"}) == "partial"
    assert _manifest_status_from_capability_states({"abstained", "review_required"}) == "partial"
    assert _manifest_status_from_capability_states({"review_required"}) == "review_required"
