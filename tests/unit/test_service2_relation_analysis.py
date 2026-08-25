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

from workflows.activities.service2_relation_analysis import (
    PROMPT_VERSION,
    RelationAnalysisUnavailable,
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
        lambda _prompt: (
            {"findings": [_finding()]},
            [{"title": "公开核查材料", "url": "https://facts.example.com/article"}],
            {"input_tokens": 321, "output_tokens": 123},
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
        lambda _prompt: (
            {"findings": [_finding(source_urls=source_urls)]},
            [{"title": "实际搜索结果", "url": "https://facts.example.com/article"}],
            {},
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
        lambda _prompt: (
            {"findings": [_finding(level="L4"), _finding()]},
            [{"title": "公开材料", "url": "https://facts.example.com/article"}],
            {},
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

    def run_once(_client: object, model: str, _prompt: str, *, instructions: str):
        calls.append(("run", model, instructions))
        return {"findings": []}, [], {}

    monkeypatch.setattr(research, "_build_client", build_client)
    monkeypatch.setattr(research, "_run_once", run_once)
    analyzer = Service2WebSearchAnalyzer(_config(model="qwen3.7-plus"))

    data, sources, usage = analyzer._call("strict service2 prompt")

    assert data == {"findings": []} and sources == [] and usage == {}
    assert calls[0] == ("build", "qwen3.7-plus", "https://api.inferera.com")
    assert calls[1][0:2] == ("run", "qwen3.7-plus")
    assert "web_search" in calls[1][2]


def test_model_catalog_is_allow_listed_with_capability_and_price_disclosure() -> None:
    settings = Settings(
        _env_file=None,
        research_llm_model="qwen3.7-plus",
        research_llm_models="qwen3.7-plus,claude-opus-5",
    )

    research_models = research.available_models(settings)
    service2_models = configured_model_ids(settings)
    assert service2_models == research_models == ["qwen3.7-plus", "claude-opus-5"]
    assert service2_models is not research_models
    service2_models.append("local-only-mutation")
    assert research.available_models(settings) == ["qwen3.7-plus", "claude-opus-5"]
    assert resolve_model(settings, None) == "qwen3.7-plus"
    assert resolve_model(settings, "claude-opus-5") == "claude-opus-5"
    with pytest.raises(AnalysisModelNotAllowed):
        resolve_model(settings, "not-allowed")
    rows = model_catalog(settings)
    assert [row["model"] for row in rows] == ["qwen3.7-plus", "claude-opus-5"]
    assert all(row["capability"] and row["web_search_mode"] for row in rows)
    assert all(row["input_usd_per_million_tokens"] is not None for row in rows)
    assert all(row["output_usd_per_million_tokens"] is not None for row in rows)
    assert all(row["pricing_observed_at"] == "2026-08-25" for row in rows)
