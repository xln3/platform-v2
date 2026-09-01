"""Credential-free, task-specific model admission for knowledge inference."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..config import Settings

CATALOG_REVISION = "knowledge-inference-model-catalog-20260828.2"
PRICING_OBSERVED_AT = "2026-08-25"
PRICING_SOURCE_URL = "https://api.inferera.com/api/v1/models?type=llm&sort_by=order"


@dataclass(frozen=True, slots=True)
class KnowledgeModelOption:
    model: str
    label: str
    provider: str
    capability: str
    strict_output_verified: bool
    tool_capability_status: Literal["verified", "not_required", "not_verified"]
    verified_at: str | None
    verification_reference: str | None
    input_usd_per_million_tokens: float | None
    output_usd_per_million_tokens: float | None
    recommended: bool = False

    def public_view(
        self,
        *,
        default_model: str,
        model_version: str,
        catalog_revision: str,
    ) -> dict[str, Any]:
        pricing_status = (
            "catalog_snapshot"
            if self.input_usd_per_million_tokens is not None
            and self.output_usd_per_million_tokens is not None
            else "unknown"
        )
        return {
            **asdict(self),
            "model_version": model_version,
            "is_default": self.model == default_model,
            "catalog_revision": catalog_revision,
            "pricing_status": pricing_status,
            "pricing_currency": "USD",
            "token_price_unit": "per_million_tokens",
            "pricing_observed_at": PRICING_OBSERVED_AT if pricing_status != "unknown" else None,
            "pricing_source_url": PRICING_SOURCE_URL if pricing_status != "unknown" else None,
            "pricing_notice": "catalog_snapshot_provider_invoice_authoritative",
        }


_CURATED: dict[str, KnowledgeModelOption] = {
    # This exact gateway shape and the brand-entity-resolution-v5 strict JSON
    # schema were exercised in the 2026-08-27 production validation.
    "gpt-5.6-luna": KnowledgeModelOption(
        model="gpt-5.6-luna",
        label="GPT 5.6 Luna",
        provider="GPT",
        capability="知识实体判断、长上下文与严格 JSON Schema 输出",
        strict_output_verified=True,
        tool_capability_status="not_required",
        verified_at="2026-08-27",
        verification_reference="docs/knowledge/VALIDATION_REPORT_20260827.md",
        input_usd_per_million_tokens=0.2,
        output_usd_per_million_tokens=1.2,
        recommended=True,
    ),
    "qwen3.7-plus": KnowledgeModelOption(
        model="qwen3.7-plus",
        label="Qwen 3.7 Plus",
        provider="Qwen",
        capability="知识实体判断与严格 JSON Schema 输出；响应较慢，价格待运维复核",
        strict_output_verified=True,
        tool_capability_status="not_required",
        verified_at="2026-08-28",
        verification_reference=("docs/knowledge/evidence/knowledge-model-admission-20260828.json"),
        input_usd_per_million_tokens=None,
        output_usd_per_million_tokens=None,
    ),
    # Service 2 has separately verified this model's web-search behavior. It is
    # intentionally not admitted here until the knowledge prompt/schema call is
    # independently exercised and recorded.
    "claude-opus-5": KnowledgeModelOption(
        model="claude-opus-5",
        label="Claude Opus 5",
        provider="Claude",
        capability="复杂语义与长上下文；知识严格结构输出尚待独立准入",
        strict_output_verified=False,
        tool_capability_status="not_verified",
        verified_at=None,
        verification_reference=None,
        input_usd_per_million_tokens=5.0,
        output_usd_per_million_tokens=25.0,
    ),
}


class KnowledgeModelError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class KnowledgeModelNotAllowed(KnowledgeModelError):
    def __init__(self) -> None:
        super().__init__("knowledge_model_not_allowed")


class KnowledgeModelNotApplicable(KnowledgeModelError):
    def __init__(self) -> None:
        super().__init__("knowledge_model_not_applicable")


def default_model(settings: Settings) -> str:
    return (settings.knowledge_llm_model or settings.research_llm_model).strip()


def _configured_candidates(settings: Settings) -> tuple[str, ...]:
    configured = tuple(
        dict.fromkeys(
            value.strip() for value in settings.knowledge_llm_models.split(",") if value.strip()
        )
    )
    default = default_model(settings)
    if not configured:
        # Compatibility boundary for deployments that predate the allow-list.
        return (default,) if default else ()
    return configured


def configured_model_ids(settings: Settings) -> tuple[str, ...]:
    candidates = _configured_candidates(settings)
    if not settings.knowledge_llm_models.strip():
        return candidates
    configured_default = default_model(settings)
    if not configured_default or configured_default not in candidates:
        raise KnowledgeModelError("knowledge_default_model_not_allowed")
    admitted = tuple(
        model
        for model in candidates
        if (option := _CURATED.get(model)) is not None and option.strict_output_verified
    )
    if len(admitted) != len(candidates):
        raise KnowledgeModelError("knowledge_model_configuration_contains_unadmitted_model")
    if not admitted:
        raise KnowledgeModelError("no_admitted_knowledge_model_configured")
    return admitted


def resolve_model(settings: Settings, requested: str | None) -> str:
    allowed = configured_model_ids(settings)
    if not allowed:
        raise KnowledgeModelError("no_knowledge_model_configured")
    candidate = (requested or "").strip() or default_model(settings)
    if candidate not in allowed:
        raise KnowledgeModelNotAllowed()
    return candidate


def catalog_revision(settings: Settings) -> str:
    payload = json.dumps(
        {
            "base": CATALOG_REVISION,
            "models": _configured_candidates(settings),
            "default": default_model(settings),
            "model_version": settings.knowledge_llm_model_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{CATALOG_REVISION}+{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


def gateway_configuration_available(settings: Settings) -> bool:
    api_key = settings.knowledge_llm_api_key or settings.research_llm_api_key
    base_url = settings.knowledge_llm_base_url or settings.research_llm_base_url
    return bool(api_key.strip() and base_url.strip())


def model_catalog(settings: Settings) -> dict[str, Any]:
    revision = catalog_revision(settings)
    try:
        models = configured_model_ids(settings)
    except KnowledgeModelError as exc:
        return {
            "status": "unavailable",
            "catalog_revision": revision,
            "default_model": None,
            "models": [],
            "unavailable_reason": exc.code,
        }
    if not gateway_configuration_available(settings):
        return {
            "status": "unavailable",
            "catalog_revision": revision,
            "default_model": None,
            "models": [],
            "unavailable_reason": "knowledge_model_gateway_unconfigured",
        }
    if any(
        (option := _CURATED.get(model)) is None or not option.strict_output_verified
        for model in models
    ):
        # A legacy single-model deployment remains callable for compatibility,
        # but it is not advertised as a browser-selectable admission.
        return {
            "status": "unavailable",
            "catalog_revision": revision,
            "default_model": None,
            "models": [],
            "unavailable_reason": "knowledge_model_verification_missing",
        }
    default = default_model(settings)
    return {
        "status": "ready",
        "catalog_revision": revision,
        "default_model": default,
        "models": [
            _CURATED[model].public_view(
                default_model=default,
                model_version=settings.knowledge_llm_model_version,
                catalog_revision=revision,
            )
            for model in models
        ],
        "unavailable_reason": None,
    }


__all__ = [
    "CATALOG_REVISION",
    "KnowledgeModelError",
    "KnowledgeModelNotAllowed",
    "KnowledgeModelNotApplicable",
    "catalog_revision",
    "configured_model_ids",
    "default_model",
    "gateway_configuration_available",
    "model_catalog",
    "resolve_model",
]
