from __future__ import annotations

import pytest
from geo_platform.config import Settings
from geo_platform.knowledge.inference_models import (
    KnowledgeModelError,
    KnowledgeModelNotAllowed,
    catalog_revision,
    configured_model_ids,
    model_catalog,
    resolve_model,
)


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "knowledge_llm_api_key": "test-key",
        "knowledge_llm_base_url": "https://models.example",
        "knowledge_llm_model": "gpt-5.6-luna",
        "knowledge_llm_models": "gpt-5.6-luna,qwen3.7-plus",
    }
    values.update(changes)
    return Settings(**values)


def test_catalog_exposes_only_task_verified_models_without_credentials() -> None:
    settings = _settings()
    catalog = model_catalog(settings)

    assert catalog["status"] == "ready"
    assert catalog["default_model"] == "gpt-5.6-luna"
    assert catalog["catalog_revision"] == catalog_revision(settings)
    assert [row["model"] for row in catalog["models"]] == [
        "gpt-5.6-luna",
        "qwen3.7-plus",
    ]
    assert all(row["strict_output_verified"] is True for row in catalog["models"])
    assert catalog["models"][1]["pricing_status"] == "unknown"
    rendered = repr(catalog)
    assert "test-key" not in rendered
    assert "models.example" not in rendered


def test_catalog_is_explicitly_unavailable_without_gateway_credentials() -> None:
    catalog = model_catalog(_settings(knowledge_llm_api_key="", research_llm_api_key=""))

    assert catalog == {
        "status": "unavailable",
        "catalog_revision": catalog["catalog_revision"],
        "default_model": None,
        "models": [],
        "unavailable_reason": "knowledge_model_gateway_unconfigured",
    }


def test_explicit_allow_list_rejects_models_without_knowledge_schema_admission() -> None:
    settings = _settings(knowledge_llm_models="gpt-5.6-luna,claude-opus-5")

    with pytest.raises(
        KnowledgeModelError,
        match="knowledge_model_configuration_contains_unadmitted_model",
    ):
        configured_model_ids(settings)
    assert model_catalog(settings)["status"] == "unavailable"


def test_explicit_allow_list_cannot_implicitly_reintroduce_an_omitted_default() -> None:
    settings = _settings(knowledge_llm_models="qwen3.7-plus")

    with pytest.raises(KnowledgeModelError, match="knowledge_default_model_not_allowed"):
        configured_model_ids(settings)
    catalog = model_catalog(settings)
    assert catalog["status"] == "unavailable"
    assert catalog["unavailable_reason"] == "knowledge_default_model_not_allowed"


def test_explicit_catalog_order_does_not_change_the_configured_default() -> None:
    settings = _settings(knowledge_llm_models="qwen3.7-plus,gpt-5.6-luna")

    catalog = model_catalog(settings)
    assert catalog["default_model"] == "gpt-5.6-luna"
    assert [row["model"] for row in catalog["models"]] == [
        "qwen3.7-plus",
        "gpt-5.6-luna",
    ]
    assert [row["is_default"] for row in catalog["models"]] == [False, True]
    assert resolve_model(settings, None) == "gpt-5.6-luna"


def test_requested_model_must_be_in_server_allow_list() -> None:
    settings = _settings()

    assert resolve_model(settings, None) == "gpt-5.6-luna"
    assert resolve_model(settings, "gpt-5.6-luna") == "gpt-5.6-luna"
    assert resolve_model(settings, "qwen3.7-plus") == "qwen3.7-plus"
    with pytest.raises(KnowledgeModelNotAllowed, match="knowledge_model_not_allowed"):
        resolve_model(settings, "browser-injected-model")


def test_legacy_single_model_remains_callable_but_is_not_advertised_unverified() -> None:
    settings = _settings(
        knowledge_llm_model="legacy-deployment-alias",
        knowledge_llm_models="",
    )

    assert resolve_model(settings, None) == "legacy-deployment-alias"
    catalog = model_catalog(settings)
    assert catalog["status"] == "unavailable"
    assert catalog["models"] == []
    assert catalog["unavailable_reason"] == "knowledge_model_verification_missing"
