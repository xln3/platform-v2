"""Curated, allow-listed web-search models for Service 2 analysis.

The model catalog is configuration, never a source of credentials.  Prices are
an operator-facing snapshot used for an informed choice; the provider invoice
remains authoritative.  Runtime calls still require a server-side environment
secret and never accept an API key from an HTTP request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from geo_platform.config import Settings
from geo_platform.intake.research import available_models

DEFAULT_SERVICE2_ANALYSIS_MODEL = "gpt-5.6-luna"
DEFAULT_SERVICE2_ANALYSIS_MODELS = (
    "gpt-5.6-luna",
    "claude-opus-5",
)
MODEL_CATALOG_REVISION = "service2-analysis-model-catalog-20260825.2"
PRICING_OBSERVED_AT = "2026-08-25"
PRICING_SOURCE_URL = "https://api.inferera.com/api/v1/models?type=llm&sort_by=order"
WEB_SEARCH_AUDITED_AT = "2026-08-25"
WEB_SEARCH_AUDIT_POLICY = "provider_search_event_and_provider_citation_required"


@dataclass(frozen=True, slots=True)
class AnalysisModelOption:
    model: str
    label: str
    provider: str
    tier: str
    capability: str
    web_search_mode: str
    input_usd_per_million_tokens: float | None
    output_usd_per_million_tokens: float | None
    context_window_tokens: int | None
    web_search_audit_status: str
    web_search_audited_at: str
    auditable_source_mode: str
    recommended: bool = False

    def public_view(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "catalog_revision": MODEL_CATALOG_REVISION,
            "pricing_observed_at": PRICING_OBSERVED_AT,
            "pricing_source_url": PRICING_SOURCE_URL,
            "pricing_currency": "USD",
            "token_price_unit": "per_million_tokens",
            "web_search_usd_per_call": None,
            "web_search_pricing_status": "not_published_in_catalog_snapshot",
            "pricing_notice": "catalog_snapshot_provider_invoice_authoritative",
            "web_search_audit_policy": WEB_SEARCH_AUDIT_POLICY,
        }


_CURATED: dict[str, AnalysisModelOption] = {
    "gpt-5.6-luna": AnalysisModelOption(
        model="gpt-5.6-luna",
        label="GPT 5.6 Luna",
        provider="gpt",
        tier="economy",
        capability="低成本高吞吐；适合全量初筛与严格结构化抽取",
        web_search_mode="responses_web_search",
        input_usd_per_million_tokens=0.2,
        output_usd_per_million_tokens=1.2,
        context_window_tokens=1_050_000,
        web_search_audit_status="verified_provider_citation",
        web_search_audited_at=WEB_SEARCH_AUDITED_AT,
        auditable_source_mode="provider_citation",
        recommended=True,
    ),
    "claude-opus-5": AnalysisModelOption(
        model="claude-opus-5",
        label="Claude Opus 5",
        provider="claude",
        tier="premium",
        capability="复杂语义、隐含比较和长文本判断能力强；成本最高",
        web_search_mode="anthropic_server_web_search",
        input_usd_per_million_tokens=5.0,
        output_usd_per_million_tokens=25.0,
        context_window_tokens=1_000_000,
        web_search_audit_status="verified_provider_citation",
        web_search_audited_at=WEB_SEARCH_AUDITED_AT,
        auditable_source_mode="provider_tool",
    ),
}


class AnalysisModelNotAllowed(ValueError):
    """The requested model is outside the server-side allow-list."""


def configured_model_ids(settings: Settings) -> list[str]:
    """Return an independent, fully audited web-search allow-list projection."""

    audited = [model for model in available_models(settings) if model in _CURATED]
    if not audited:
        raise AnalysisModelNotAllowed("no_audited_web_search_model_configured")
    return list(audited)


def resolve_model(settings: Settings, requested: str | None) -> str:
    allowed = configured_model_ids(settings)
    candidate = (requested or "").strip() or allowed[0]
    if candidate not in allowed:
        raise AnalysisModelNotAllowed(candidate)
    return candidate


def model_catalog(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in configured_model_ids(settings):
        rows.append(_CURATED[model].public_view())
    return rows


def model_snapshot(model: str) -> dict[str, Any]:
    """Return a deep, credential-free execution snapshot for a selected model."""

    option = _CURATED.get(model)
    if option is None:
        raise AnalysisModelNotAllowed(model)
    return option.public_view()


__all__ = [
    "AnalysisModelNotAllowed",
    "DEFAULT_SERVICE2_ANALYSIS_MODEL",
    "DEFAULT_SERVICE2_ANALYSIS_MODELS",
    "MODEL_CATALOG_REVISION",
    "WEB_SEARCH_AUDIT_POLICY",
    "configured_model_ids",
    "model_catalog",
    "model_snapshot",
    "resolve_model",
]
