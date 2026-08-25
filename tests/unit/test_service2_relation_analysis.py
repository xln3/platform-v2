from __future__ import annotations

from hashlib import sha256

import pytest
from geo_platform.config import Settings
from geo_platform.intake import research
from geo_platform.service2_corpus.analysis_models import (
    AnalysisModelNotAllowed,
    configured_model_ids,
    model_catalog,
    resolve_model,
)
from geo_platform.service2_corpus.schemas import AnalysisModelOptionView

from workflows.activities.service2_relation_analysis import (
    PROMPT_VERSION,
    RelationAnalysisSchemaError,
    RelationAnalysisUnavailable,
    RelationProviderResponse,
    Service2RelationAnalysisConfig,
    Service2WebSearchAnalyzer,
    config_from_settings,
)

SOURCE_TEXT = "前文。甲公司被评为明显不如乙公司。后文。"
QUOTE = "甲公司被评为明显不如乙公司"
SOURCE_HASH = sha256(SOURCE_TEXT.encode()).hexdigest()


def _config(*, api_key: str = "unit-secret-canary", model: str = "gpt-5.6-luna"):
    return Service2RelationAnalysisConfig(
        api_key=api_key,
        model=model,
        base_url="https://api.inferera.com",
        base_url_fallback="",
        text_char_limit=100_000,
    )


def _finding(*, level: str = "L2b", source_urls: object | None = None):
    return {
        "level": level,
        "relation_direction": "target_degraded",
        "textual_speaker": "页面作者",
        "target_entity": "甲公司",
        "beneficiary_entity": "乙公司",
        "evidence_quote": QUOTE,
        "context_quote": SOURCE_TEXT,
        "quote_start": SOURCE_TEXT.index(QUOTE),
        "context_start": 0,
        "fact_anchor_state": "absent",
        "flags": {
            "comparison_present": False,
            "peer_elevated": False,
            "scope_narrowed": False,
            "industry_wide": False,
            "direct_target_negative": False,
            "secondary_position": True,
            "comparison_manipulated": False,
            "key_fact_omitted": False,
        },
        "comparison_dimensions": [],
        "omitted_facts": [],
        "confidence": 0.86,
        "factcheck": {
            "claim": QUOTE,
            "verdict": "supported",
            "boundary": None,
            "source_urls": (
                ["https://facts.example.com/article"] if source_urls is None else source_urls
            ),
        },
    }


def _provider_response(
    findings: list[dict],
    *,
    sources: list[dict[str, str]] | None = None,
    usage: dict[str, int] | None = None,
) -> RelationProviderResponse:
    provider_sources = (
        [{"title": "供应商引用", "url": "https://facts.example.com/article"}]
        if sources is None
        else sources
    )
    return RelationProviderResponse(
        data={"findings": findings},
        sources=tuple(provider_sources),
        usage=usage or {"input_tokens": 100, "output_tokens": 20},
        audit=research.ResearchCallAudit(
            transport="responses",
            resolved_model="gpt-5.6-luna",
            provider_request_id="req_unit",
            provider_response_id="resp_unit",
            resolved_provider="openai",
            provider_resolution_source="provider_response",
            gateway_host="api.inferera.com",
            protocol_route="/v1/responses",
            web_search_observed=True,
            search_event_count=1,
            provider_citation_count=len(provider_sources),
            source_origin="provider_citation",
        ),
    )


def test_config_keeps_credentials_server_only_and_out_of_repr() -> None:
    settings = Settings(
        _env_file=None,
        service2_analysis_llm_api_key="unit-secret-canary",
        research_llm_api_key="fallback-secret-canary",
    )
    config = config_from_settings(settings, model="gpt-5.6-luna")

    assert config.api_key == "unit-secret-canary"
    assert "unit-secret-canary" not in repr(config)
    serialized_catalog = repr(model_catalog(settings))
    assert "unit-secret-canary" not in serialized_catalog
    assert "api_key" not in serialized_catalog


def test_missing_server_credential_and_oversized_text_fail_closed_without_a_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config(api_key=""))
    called = False

    def provider_call(_prompt: str):
        nonlocal called
        called = True
        return {}, [], {}

    monkeypatch.setattr(analyzer, "_call", provider_call)
    with pytest.raises(RelationAnalysisUnavailable, match="llm_api_key_missing"):
        analyzer.analyze(
            project_brand="甲公司",
            known_entities=("甲公司", "乙公司"),
            url="https://page.example.com/post",
            source_text=SOURCE_TEXT,
            snapshot_text_sha256=SOURCE_HASH,
        )
    assert called is False

    bounded = Service2WebSearchAnalyzer(
        Service2RelationAnalysisConfig(
            api_key="unit-secret-canary",
            model="gpt-5.6-luna",
            base_url="https://api.inferera.com",
            base_url_fallback="",
            text_char_limit=4,
        )
    )
    with pytest.raises(RelationAnalysisUnavailable, match="source_text_outside_model_bound"):
        bounded.analyze(
            project_brand="甲公司",
            known_entities=("甲公司",),
            url="https://page.example.com/post",
            source_text=SOURCE_TEXT,
            snapshot_text_sha256=SOURCE_HASH,
        )


def test_exact_snapshot_offsets_sources_and_unknown_attribution_are_rebound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [_finding()],
            sources=[{"title": "公开核查材料", "url": "https://facts.example.com/article"}],
            usage={"input_tokens": 321, "output_tokens": 123},
        ),
    )

    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )

    assert result.rejected_candidates == ()
    assert result.input_tokens == 321 and result.output_tokens == 123
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding is not None
    assert finding.quote_start == SOURCE_TEXT.index(QUOTE)
    assert finding.quote_end == finding.quote_start + len(QUOTE)
    assert SOURCE_TEXT[finding.quote_start : finding.quote_end] == finding.evidence_quote
    assert finding.context_start == 0 and finding.context_end == len(SOURCE_TEXT)
    assert finding.snapshot_text_sha256 == SOURCE_HASH
    assert finding.model == "gpt-5.6-luna"
    assert finding.prompt_version == PROMPT_VERSION
    assert finding.publisher.party is None and finding.publisher.confidence == "unknown"
    assert finding.commissioner.party is None and finding.commissioner.confidence == "unknown"
    assert finding.factcheck_verdict == "supported"
    assert finding.factcheck_evidence == [
        {"url": "https://facts.example.com/article", "title": "公开核查材料"}
    ]


@pytest.mark.parametrize(
    "source_urls",
    [["https://invented.example.com/not-returned"], "https://facts.example.com/article"],
)
def test_unbound_or_malformed_factcheck_sources_are_downgraded_to_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
    source_urls: object,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [_finding(source_urls=source_urls)],
            sources=[{"title": "实际搜索结果", "url": "https://facts.example.com/article"}],
        ),
    )

    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding is not None
    assert finding.factcheck_verdict == "unverifiable"
    assert finding.factcheck_evidence == []
    assert finding.factcheck_boundary


def test_l4_and_invalid_candidates_are_rejected_without_losing_valid_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [_finding(level="L4"), _finding()],
            sources=[{"title": "公开材料", "url": "https://facts.example.com/article"}],
        ),
    )

    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )

    assert len(result.findings) == 1
    assert "finding_level_invalid" in result.rejected_candidates


def test_l1_keeps_factcheck_instead_of_skipping_the_factual_negative_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [_finding(level="L1")],
            sources=[{"title": "核查材料", "url": "https://facts.example.com/article"}],
        ),
    )

    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )

    assert len(result.findings) == 1
    assert result.findings[0].level == "L1"
    assert result.findings[0].is_disparagement is False
    assert result.findings[0].factcheck_verdict == "supported"
    assert result.findings[0].factcheck_evidence


def test_repeated_quote_uses_the_provider_bound_offset_and_ambiguous_legacy_input_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_text = f"{QUOTE}。中段。{QUOTE}。"
    second_offset = source_text.rindex(QUOTE)
    second = _finding()
    second.update(
        {
            "context_quote": source_text,
            "context_start": 0,
            "quote_start": second_offset,
        }
    )
    missing = _finding()
    missing.pop("quote_start")
    missing.update({"context_quote": source_text, "context_start": 0})
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [second, missing],
            sources=[{"title": "核查材料", "url": "https://facts.example.com/article"}],
        ),
    )

    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=source_text,
        snapshot_text_sha256=sha256(source_text.encode()).hexdigest(),
    )

    assert len(result.findings) == 1
    assert result.findings[0].quote_start == second_offset
    assert "finding_quote_start_ambiguous" in result.rejected_candidates


def test_unique_legacy_quote_without_offsets_is_bound_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _finding()
    legacy.pop("quote_start")
    legacy.pop("context_start")
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [legacy],
            sources=[{"title": "核查材料", "url": "https://facts.example.com/article"}],
        ),
    )
    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )
    assert len(result.findings) == 1
    assert result.findings[0].context_start == 0
    assert result.findings[0].quote_start == SOURCE_TEXT.index(QUOTE)


def test_findings_are_not_silently_truncated_at_twenty_and_hard_limit_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response(
            [_finding() for _ in range(25)],
            sources=[{"title": "核查材料", "url": "https://facts.example.com/article"}],
        ),
    )
    result = analyzer.analyze(
        project_brand="甲公司",
        known_entities=("甲公司", "乙公司"),
        url="https://page.example.com/post",
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )
    assert len(result.findings) == 25

    monkeypatch.setattr(
        analyzer,
        "_call",
        lambda _prompt, *, idempotency_key: _provider_response([_finding() for _ in range(501)]),
    )
    with pytest.raises(RelationAnalysisSchemaError, match="findings_response_limit_exceeded"):
        analyzer.analyze(
            project_brand="甲公司",
            known_entities=("甲公司", "乙公司"),
            url="https://page.example.com/post",
            source_text=SOURCE_TEXT,
            snapshot_text_sha256=SOURCE_HASH,
        )


def test_provider_search_telemetry_is_mandatory_and_model_authored_sources_are_not_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = Service2WebSearchAnalyzer(_config())
    no_search = _provider_response([_finding()])
    no_search = RelationProviderResponse(
        data=no_search.data,
        sources=no_search.sources,
        usage=no_search.usage,
        audit=research.ResearchCallAudit(
            transport="responses",
            resolved_model="gpt-5.6-luna",
            provider_request_id="req_no_search",
            web_search_observed=False,
            search_event_count=0,
            provider_citation_count=0,
            source_origin="none",
        ),
    )
    monkeypatch.setattr(analyzer, "_call", lambda _prompt, *, idempotency_key: no_search)
    with pytest.raises(RelationAnalysisUnavailable, match="web_search_not_observed"):
        analyzer.analyze(
            project_brand="甲公司",
            known_entities=("甲公司", "乙公司"),
            url="https://page.example.com/post",
            source_text=SOURCE_TEXT,
            snapshot_text_sha256=SOURCE_HASH,
        )

    authored = RelationProviderResponse(
        data={"findings": [_finding()]},
        sources=({"title": "模型写出的 URL", "url": "https://facts.example.com/article"},),
        usage={},
        audit=research.ResearchCallAudit(
            transport="responses",
            resolved_model="gpt-5.6-luna",
            provider_request_id="req_model_output",
            web_search_observed=True,
            search_event_count=1,
            provider_citation_count=0,
            source_origin="model_output",
        ),
    )
    monkeypatch.setattr(analyzer, "_call", lambda _prompt, *, idempotency_key: authored)
    with pytest.raises(RelationAnalysisUnavailable, match="provider_citation_not_observed"):
        analyzer.analyze(
            project_brand="甲公司",
            known_entities=("甲公司", "乙公司"),
            url="https://page.example.com/post",
            source_text=SOURCE_TEXT,
            snapshot_text_sha256=SOURCE_HASH,
        )


def test_analyzer_reuses_brand_research_transport_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    class ClientContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    def build_client(config: research.LlmConfig, base_url: str):
        calls.append(("build", config.model, base_url))
        return ClientContext()

    def run_once(
        _client: object,
        model: str,
        _prompt: str,
        *,
        instructions: str,
        idempotency_key: str,
    ):
        calls.append(("run", model, instructions))
        assert idempotency_key == "service2-test"
        return (
            {"findings": []},
            [],
            {},
            research.ResearchCallAudit(
                transport="chat_enable_search",
                resolved_model=model,
                provider_request_id="req_unit",
                web_search_observed=True,
                search_event_count=1,
                provider_citation_count=0,
                source_origin="provider_grounding",
            ),
        )

    monkeypatch.setattr(research, "_build_client", build_client)
    monkeypatch.setattr(research, "_run_once_audited", run_once)
    analyzer = Service2WebSearchAnalyzer(_config(model="claude-opus-5"))

    response = analyzer._call("strict service2 prompt", idempotency_key="service2-test")

    assert response.data == {"findings": []} and response.sources == ()
    assert calls[0] == ("build", "claude-opus-5", "https://api.inferera.com")
    assert calls[1][0:2] == ("run", "claude-opus-5")
    assert "web_search" in calls[1][2]


def test_model_catalog_is_allow_listed_with_capability_and_price_disclosure() -> None:
    settings = Settings(
        _env_file=None,
        research_llm_model="gpt-5.6-luna",
        research_llm_models="gpt-5.6-luna,claude-opus-5",
    )

    research_models = research.available_models(settings)
    service2_models = configured_model_ids(settings)
    assert service2_models == research_models == ["gpt-5.6-luna", "claude-opus-5"]
    assert service2_models is not research_models
    service2_models.append("local-only-mutation")
    assert research.available_models(settings) == ["gpt-5.6-luna", "claude-opus-5"]
    assert resolve_model(settings, None) == "gpt-5.6-luna"
    assert resolve_model(settings, "claude-opus-5") == "claude-opus-5"
    with pytest.raises(AnalysisModelNotAllowed):
        resolve_model(settings, "not-allowed")
    rows = model_catalog(settings)
    assert all(AnalysisModelOptionView.model_validate(row) for row in rows)
    assert [row["model"] for row in rows] == ["gpt-5.6-luna", "claude-opus-5"]
    assert all(row["capability"] and row["web_search_mode"] for row in rows)
    assert all(row["input_usd_per_million_tokens"] is not None for row in rows)
    assert all(row["output_usd_per_million_tokens"] is not None for row in rows)
    assert all(row["pricing_observed_at"] == "2026-08-25" for row in rows)
    assert all(row["catalog_revision"] for row in rows)
    assert all(row["pricing_currency"] == "USD" for row in rows)
    assert all(row["web_search_pricing_status"] for row in rows)
    assert all(row["web_search_audit_status"] == "verified_provider_citation" for row in rows)
    assert all(
        row["web_search_audit_policy"] == "provider_search_event_and_provider_citation_required"
        for row in rows
    )


def test_service2_filters_configured_models_without_a_complete_search_and_price_audit() -> None:
    settings = Settings(
        _env_file=None,
        research_llm_model="gpt-5.6-luna",
        research_llm_models=(
            "unreviewed-model,gpt-5.6-luna,qwen3.7-plus,gemini-3.1-pro-preview-search,claude-opus-5"
        ),
    )
    assert research.available_models(settings)[0] == "gpt-5.6-luna"
    assert "unreviewed-model" in research.available_models(settings)
    assert configured_model_ids(settings) == ["gpt-5.6-luna", "claude-opus-5"]
    assert "qwen3.7-plus" not in configured_model_ids(settings)
    assert "gemini-3.1-pro-preview-search" not in configured_model_ids(settings)
    with pytest.raises(AnalysisModelNotAllowed):
        resolve_model(settings, "unreviewed-model")
