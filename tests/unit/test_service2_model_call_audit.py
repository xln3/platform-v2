from __future__ import annotations

from decimal import Decimal

import httpx
from geo_platform.intake.research import _provider_execution_fields, _provider_resolved_model
from geo_platform.service2_corpus.analysis_models import (
    MODEL_CATALOG_REVISION,
    model_snapshot,
)
from geo_platform.service2_corpus.service import (
    _estimated_model_call_costs,
    _model_call_audit_completeness,
)


def test_catalog_snapshot_is_independent_and_discloses_unknown_search_price() -> None:
    first = model_snapshot("gpt-5.6-luna")
    second = model_snapshot("gpt-5.6-luna")

    assert first == second
    assert first is not second
    assert first["catalog_revision"] == MODEL_CATALOG_REVISION
    assert first["pricing_currency"] == "USD"
    assert first["token_price_unit"] == "per_million_tokens"
    assert first["web_search_usd_per_call"] is None
    assert first["web_search_pricing_status"] == "not_published_in_catalog_snapshot"


def test_token_cost_recomputes_from_frozen_price_and_unknown_search_cost_stays_open() -> None:
    token, search, total, completeness = _estimated_model_call_costs(
        input_tokens=2_000_000,
        output_tokens=500_000,
        search_event_count=2,
        input_price="0.2",
        output_price="1.2",
        search_price=None,
    )

    assert token == Decimal("1.0")
    assert search is None
    assert total is None
    assert completeness == "token_only_search_price_unknown"


def test_total_cost_includes_search_events_when_the_frozen_price_is_known() -> None:
    token, search, total, completeness = _estimated_model_call_costs(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        search_event_count=3,
        input_price="2",
        output_price="12",
        search_price="0.01",
    )

    assert token == Decimal("14")
    assert search == Decimal("0.03")
    assert total == Decimal("14.03")
    assert completeness == "complete"


def test_provider_execution_fields_are_response_observations_not_model_claims() -> None:
    request = httpx.Request("POST", "https://gateway.example/v1/responses")
    response = httpx.Response(200, request=request)

    observed = _provider_execution_fields(
        response,
        {"id": "resp_123", "provider": "openai"},
        route="/v1/responses",
    )
    missing = _provider_execution_fields(
        response,
        {"output_text": "provider=made-up-in-model-text"},
        route="/v1/responses",
    )

    assert observed == {
        "gateway_host": "gateway.example",
        "protocol_route": "/v1/responses",
        "provider_response_id": "resp_123",
        "resolved_provider": "openai",
        "provider_resolution_source": "provider_response",
    }
    assert missing["resolved_provider"] is None
    assert missing["provider_resolution_source"] == "not_observed"


def test_provider_model_never_falls_back_to_the_requested_alias() -> None:
    assert _provider_resolved_model({"model": "gpt-5.6-luna-20260820"}) == ("gpt-5.6-luna-20260820")
    assert _provider_resolved_model({}) == "not_observed"
    assert _provider_resolved_model({"model": "  "}) == "not_observed"


def test_audit_completeness_requires_protocol_usage_pricing_and_real_citations() -> None:
    complete = _model_call_audit_completeness(
        provider_request_id="req_1",
        provider_response_id="resp_1",
        resolved_provider="openai",
        provider_resolution_source="provider_response",
        resolved_model="gpt-5.6-luna-20260820",
        transport="responses",
        gateway_host="api.inferera.com",
        protocol_route="/v1/responses",
        input_tokens=100,
        output_tokens=20,
        web_search_observed=True,
        search_event_count=1,
        provider_citation_count=1,
        source_origin="provider_citation",
        response_sources=[{"url": "https://example.com/evidence"}],
        pricing_snapshot_complete=True,
    )
    incomplete = _model_call_audit_completeness(
        provider_request_id="req_1",
        provider_response_id=None,
        resolved_provider=None,
        provider_resolution_source="not_observed",
        resolved_model="not_observed",
        transport="responses",
        gateway_host=None,
        protocol_route=None,
        input_tokens=0,
        output_tokens=0,
        web_search_observed=True,
        search_event_count=1,
        provider_citation_count=0,
        source_origin="model_output",
        response_sources=[{"url": "https://model-authored.example"}],
        pricing_snapshot_complete=False,
    )

    assert complete == "complete"
    assert incomplete == "missing:res,provider,model,route,usage,cite,price"
    assert len(incomplete) <= 64

    every_field_missing = _model_call_audit_completeness(
        provider_request_id=None,
        provider_response_id=None,
        resolved_provider=None,
        provider_resolution_source="not_observed",
        resolved_model="not_observed",
        transport="unknown",
        gateway_host=None,
        protocol_route=None,
        input_tokens=0,
        output_tokens=0,
        web_search_observed=False,
        search_event_count=0,
        provider_citation_count=0,
        source_origin="none",
        response_sources=[],
        pricing_snapshot_complete=False,
    )
    assert len(every_field_missing) <= 64
