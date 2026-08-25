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
    "qwen3.7-plus",
    "gemini-3.1-pro-preview-search",
    "claude-opus-5",
)
PRICING_OBSERVED_AT = "2026-08-25"
PRICING_SOURCE_URL = "https://api.inferera.com/api/v1/models?type=llm&sort_by=order"


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
    recommended: bool = False

    def public_view(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pricing_observed_at": PRICING_OBSERVED_AT,
            "pricing_source_url": PRICING_SOURCE_URL,
            "pricing_notice": "catalog_snapshot_provider_invoice_authoritative",
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
        recommended=True,
    ),
    "qwen3.7-plus": AnalysisModelOption(
        model="qwen3.7-plus",
        label="Qwen 3.7 Plus",
        provider="qwen",
        tier="economy_cn",
        capability="中文语境与性价比较均衡；服务端联网搜索",
        web_search_mode="chat_enable_search",
        input_usd_per_million_tokens=0.282,
        output_usd_per_million_tokens=1.128,
        context_window_tokens=991_000,
    ),
    "gemini-3.1-pro-preview-search": AnalysisModelOption(
        model="gemini-3.1-pro-preview-search",
        label="Gemini 3.1 Pro Preview Search",
        provider="gemini",
        tier="balanced",
        capability="长上下文与内建 grounding；适合复杂对比和事实核查",
        web_search_mode="chat_builtin_search",
        input_usd_per_million_tokens=2.0,
        output_usd_per_million_tokens=12.0,
        context_window_tokens=1_048_576,
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
    ),
}


class AnalysisModelNotAllowed(ValueError):
    """The requested model is outside the server-side allow-list."""


def configured_model_ids(settings: Settings) -> list[str]:
    """Return an independent copy of the brand-research web-search allow-list."""

    return list(available_models(settings))


def resolve_model(settings: Settings, requested: str | None) -> str:
    allowed = configured_model_ids(settings)
    candidate = (requested or "").strip() or allowed[0]
    if candidate not in allowed:
        raise AnalysisModelNotAllowed(candidate)
    return candidate


def model_catalog(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in configured_model_ids(settings):
        option = _CURATED.get(model)
        if option is None:
            rows.append(
                {
                    "model": model,
                    "label": model,
                    "provider": model.split("-", 1)[0],
                    "tier": "configured",
                    "capability": "由部署配置允许；价格与联网能力需运维复核",
                    "web_search_mode": "configured",
                    "input_usd_per_million_tokens": None,
                    "output_usd_per_million_tokens": None,
                    "context_window_tokens": None,
                    "recommended": False,
                    "pricing_observed_at": PRICING_OBSERVED_AT,
                    "pricing_source_url": PRICING_SOURCE_URL,
                    "pricing_notice": "catalog_snapshot_provider_invoice_authoritative",
                }
            )
            continue
        rows.append(option.public_view())
    return rows


__all__ = [
    "AnalysisModelNotAllowed",
    "DEFAULT_SERVICE2_ANALYSIS_MODEL",
    "DEFAULT_SERVICE2_ANALYSIS_MODELS",
    "configured_model_ids",
    "model_catalog",
    "resolve_model",
]
