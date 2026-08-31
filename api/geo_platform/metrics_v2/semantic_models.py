"""Server-governed model catalog for Metrics V2 semantic backfills.

The browser receives model names and a frozen public price snapshot only.  API
credentials and gateway routing stay in ``Settings`` and are never accepted
from an operations request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from geo_platform.config import Settings

CATALOG_REVISION = "metrics-v2-semantic-models-20260828.1"
PRICING_OBSERVED_AT = "2026-08-28"
PRICING_SOURCE_URL = "https://api.inferera.com/api/v1/models?type=llm&sort_by=order"


@dataclass(frozen=True, slots=True)
class SemanticModelOption:
    model: str
    label: str
    provider: str
    tier: str
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    context_window_tokens: int
    recommended: bool = False

    def public_view(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "input_usd_per_million_tokens": float(self.input_usd_per_million_tokens),
            "output_usd_per_million_tokens": float(self.output_usd_per_million_tokens),
            "catalog_revision": CATALOG_REVISION,
            "pricing_observed_at": PRICING_OBSERVED_AT,
            "pricing_source_url": PRICING_SOURCE_URL,
            "pricing_currency": "USD",
            "token_price_unit": "per_million_tokens",
            "pricing_notice": "catalog_snapshot_provider_invoice_authoritative",
        }


_CATALOG: dict[str, SemanticModelOption] = {
    "glm-5.3-flash": SemanticModelOption(
        model="glm-5.3-flash",
        label="GLM 5.3 Flash",
        provider="Z.AI",
        tier="economy",
        input_usd_per_million_tokens=Decimal("0.11268"),
        output_usd_per_million_tokens=Decimal("0.39438"),
        context_window_tokens=1_000_000,
        recommended=True,
    ),
    "gpt-5.6-luna": SemanticModelOption(
        model="gpt-5.6-luna",
        label="GPT 5.6 Luna",
        provider="OpenAI-compatible",
        tier="economy",
        input_usd_per_million_tokens=Decimal("0.2"),
        output_usd_per_million_tokens=Decimal("1.2"),
        context_window_tokens=1_050_000,
    ),
    "gpt-5.6-sol": SemanticModelOption(
        model="gpt-5.6-sol",
        label="GPT 5.6 Sol",
        provider="OpenAI-compatible",
        tier="premium",
        input_usd_per_million_tokens=Decimal("4"),
        output_usd_per_million_tokens=Decimal("20"),
        context_window_tokens=1_050_000,
    ),
}


class SemanticModelNotAllowed(ValueError):
    """Requested model is outside the server-side semantic allow-list."""


def configured_model_ids(settings: Settings) -> list[str]:
    configured = [
        value.strip() for value in settings.semantic_decision_llm_models.split(",") if value.strip()
    ]
    default_model = settings.semantic_decision_llm_model.strip() or "gpt-5.6-sol"
    ordered = list(dict.fromkeys([default_model, *configured]))
    allowed = [model for model in ordered if model in _CATALOG]
    if not allowed:
        raise SemanticModelNotAllowed("no_semantic_model_configured")
    return allowed


def resolve_model(settings: Settings, requested: str | None) -> str:
    allowed = configured_model_ids(settings)
    candidate = (requested or "").strip() or allowed[0]
    if candidate not in allowed:
        raise SemanticModelNotAllowed(candidate)
    return candidate


def model_catalog(settings: Settings) -> list[dict[str, Any]]:
    return [_CATALOG[model].public_view() for model in configured_model_ids(settings)]


def model_option(settings: Settings, requested: str | None) -> SemanticModelOption:
    return _CATALOG[resolve_model(settings, requested)]


def estimated_cost_usd(
    *, model: SemanticModelOption, input_tokens: int, output_tokens: int
) -> Decimal:
    million = Decimal(1_000_000)
    return (
        Decimal(max(0, input_tokens)) * model.input_usd_per_million_tokens / million
        + Decimal(max(0, output_tokens)) * model.output_usd_per_million_tokens / million
    ).quantize(Decimal("0.000001"))


__all__ = [
    "CATALOG_REVISION",
    "SemanticModelNotAllowed",
    "SemanticModelOption",
    "configured_model_ids",
    "estimated_cost_usd",
    "model_catalog",
    "model_option",
    "resolve_model",
]
