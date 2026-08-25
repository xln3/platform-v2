from __future__ import annotations

from typing import Any

import pytest
from geo_platform.intake.research import ResearchCallAudit

from workflows.activities import service2_source_corpus as activity_module
from workflows.activities.service2_relation_analysis import (
    RelationAnalysisRequest,
    RelationAnalysisUnavailable,
    RelationProviderResponse,
)


class _Session:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def commit(self) -> None:
        self.events.append("commit")


class _Service:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def claim_model_call(self, _session: Any, **_kwargs: Any):
        self.events.append("claim")
        return {"pub_id": "s2c_fixture", "idempotency_key": "service2-fixture"}, True

    def complete_model_call(self, _session: Any, **_kwargs: Any) -> None:
        self.events.append("complete")

    def fail_model_call(self, _session: Any, **_kwargs: Any) -> None:
        self.events.append("mark_ambiguous")


class _Analyzer:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def _call(self, _prompt: str, *, idempotency_key: str) -> RelationProviderResponse:
        assert idempotency_key == "service2-fixture"
        self.events.append("provider_call")
        return RelationProviderResponse(
            data={"findings": []},
            sources=(),
            usage={"input_tokens": 10, "output_tokens": 2},
            audit=ResearchCallAudit(
                transport="responses",
                resolved_model="gpt-5.6-luna",
                provider_request_id="req_fixture",
                web_search_observed=True,
                search_event_count=1,
                provider_citation_count=1,
                source_origin="provider_citation",
            ),
        )


def test_paid_call_intent_is_committed_before_provider_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        activity_module,
        "_paid_call_crash_probe",
        lambda point: events.append(f"probe:{point}"),
    )

    row, claimed = activity_module._claim_paid_model_call(
        session=_Session(events),
        service=_Service(events),  # type: ignore[arg-type]
        item={"pub_id": "s2i_fixture"},
        snapshot_id="snapshot-fixture",
        input_hash="a" * 64,
        model="gpt-5.6-luna",
        prompt_version="prompt-v1",
        policy_version="policy-v1",
        catalog_snapshot={},
    )

    assert claimed and row["pub_id"] == "s2c_fixture"
    assert events == ["probe:before_intent_claim", "claim", "commit"]


def test_crash_after_provider_success_never_blindly_calls_provider_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    service = _Service(events)
    analyzer = _Analyzer(events)
    session = _Session(events)
    monkeypatch.setattr(activity_module, "set_tenant_context", lambda *_args, **_kwargs: None)

    def crash_after_charge(point: str) -> None:
        events.append(f"probe:{point}")
        if point == "provider_succeeded_before_response_commit":
            raise RuntimeError("simulated_worker_crash")

    monkeypatch.setattr(activity_module, "_paid_call_crash_probe", crash_after_charge)
    call_row = {"pub_id": "s2c_fixture", "idempotency_key": "service2-fixture"}
    with pytest.raises(RuntimeError, match="simulated_worker_crash"):
        activity_module._resolve_paid_provider_response(
            session=session,
            service=service,  # type: ignore[arg-type]
            analyzer=analyzer,  # type: ignore[arg-type]
            request=RelationAnalysisRequest(prompt="prompt", input_hash="a" * 64),
            call_row=call_row,
            claimed=True,
            tenant_id="tenant-id",
            tenant_pub_id="tnt_fixture",
            analysis_model="gpt-5.6-luna",
        )

    assert events.count("provider_call") == 1
    assert "complete" not in events

    # A replay sees the durable pre-call claim. Because no response receipt was
    # committed, it records an ambiguous paid outcome and requires reconciliation;
    # it must not issue a second provider request.
    with pytest.raises(RelationAnalysisUnavailable, match="paid_call_outcome_ambiguous"):
        activity_module._resolve_paid_provider_response(
            session=session,
            service=service,  # type: ignore[arg-type]
            analyzer=analyzer,  # type: ignore[arg-type]
            request=RelationAnalysisRequest(prompt="prompt", input_hash="a" * 64),
            call_row={
                **call_row,
                "state": "claimed",
                "error_code": None,
            },
            claimed=False,
            tenant_id="tenant-id",
            tenant_pub_id="tnt_fixture",
            analysis_model="gpt-5.6-luna",
        )

    assert events.count("provider_call") == 1
    assert events[-2:] == ["mark_ambiguous", "commit"]


def test_stored_response_replay_restores_rls_without_a_second_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        activity_module,
        "set_tenant_context",
        lambda *_args, **_kwargs: events.append("tenant_context"),
    )
    response = activity_module._resolve_paid_provider_response(
        session=_Session(events),
        service=_Service(events),  # type: ignore[arg-type]
        analyzer=_Analyzer(events),  # type: ignore[arg-type]
        request=RelationAnalysisRequest(prompt="prompt", input_hash="a" * 64),
        call_row={
            "state": "succeeded",
            "response_data": {"findings": []},
            "response_sources": [],
            "input_tokens": 10,
            "output_tokens": 2,
            "transport": "responses",
            "resolved_model": "gpt-5.6-luna",
            "provider_request_id": "req_fixture",
            "web_search_observed": True,
            "search_event_count": 1,
            "provider_citation_count": 1,
            "source_origin": "provider_citation",
            "gateway_host": "api.inferera.com",
            "protocol_route": "/v1/responses",
            "provider_response_id": "resp_fixture",
            "resolved_provider": "openai",
            "provider_resolution_source": "provider_response",
        },
        claimed=False,
        tenant_id="tenant-id",
        tenant_pub_id="tnt_fixture",
        analysis_model="gpt-5.6-luna",
    )

    assert response.data == {"findings": []}
    assert events == ["tenant_context"]
