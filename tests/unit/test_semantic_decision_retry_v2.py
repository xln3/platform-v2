from __future__ import annotations

from workflows.activities.semantic_judge_llm import SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS
from workflows.definitions.semantic_decisions_v2 import (
    _MODEL_ACTIVITY_TIMEOUT,
    _next_auto_rejudge_payload,
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


def test_activity_timeout_exceeds_adapter_total_deadline_and_persistence_margin() -> None:
    assert _MODEL_ACTIVITY_TIMEOUT.total_seconds() >= (SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS + 360)
