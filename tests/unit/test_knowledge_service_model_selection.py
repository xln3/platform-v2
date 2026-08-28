from __future__ import annotations

from typing import Any

import pytest
from geo_platform.config import Settings
from geo_platform.knowledge import service
from geo_platform.knowledge.inference_models import (
    KnowledgeModelNotAllowed,
    KnowledgeModelNotApplicable,
)
from geo_platform.knowledge.schemas import RuntimeResolveRequest


def _settings() -> Settings:
    return Settings(
        knowledge_llm_api_key="test-key",
        knowledge_llm_base_url="https://models.example",
        knowledge_llm_model="gpt-5.6-luna",
        knowledge_llm_models="gpt-5.6-luna,qwen3.7-plus",
    )


def _body(*, policy: str = "llm_required", model: str | None = None) -> RuntimeResolveRequest:
    return RuntimeResolveRequest.model_validate(
        {
            "namespace": "shared",
            "domain": "brand/entity-resolution",
            "task": "resolve-brand-identity",
            "items": [{"id": "item-1", "value": "示例品牌"}],
            "context": {"analysis_domain": "cybersecurity"},
            "policy": policy,
            "policy_id": "model-selection-test",
            "policy_version": "1",
            "allow_external_model": True,
            **({"model": model} if model is not None else {}),
        }
    )


class SelectionObserved(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, "gpt-5.6-luna"), ("qwen3.7-plus", "qwen3.7-plus")],
)
def test_service_resolves_default_or_explicit_model_into_request_and_gateway(
    monkeypatch: pytest.MonkeyPatch,
    requested: str | None,
    expected: str,
) -> None:
    observed: dict[str, Any] = {}

    class CapturingEngine:
        def __init__(self, registry: object, repository: object, gateway: object) -> None:
            del registry, repository
            observed["gateway"] = gateway

        def decide(self, request: object) -> None:
            observed["request"] = request
            raise SelectionObserved

    def selected_gateway(settings: Settings, model: str | None = None) -> object:
        del settings
        observed["gateway_model"] = model
        return {"model": model}

    monkeypatch.setattr(service, "KnowledgeRepository", lambda *args, **kwargs: object())
    monkeypatch.setattr(service, "registry", lambda settings: object())
    monkeypatch.setattr(service, "gateway", selected_gateway)
    monkeypatch.setattr(service, "ReasoningEngine", CapturingEngine)

    with pytest.raises(SelectionObserved):
        service.resolve(
            session=object(),  # type: ignore[arg-type]
            settings=_settings(),
            tenant_pub_id="tnt_fixture",
            request_id="req-fixture",
            body=_body(model=requested),
        )

    assert observed["gateway_model"] == expected
    assert observed["gateway"] == {"model": expected}
    runtime = observed["request"]
    assert runtime.model == expected
    assert str(runtime.model_catalog_revision).startswith(
        "knowledge-inference-model-catalog-20260828.2+"
    )


def test_service_rejects_unallowed_model_before_repository_or_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        service,
        "KnowledgeRepository",
        lambda *args, **kwargs: touched.append("repo"),
    )
    monkeypatch.setattr(service, "gateway", lambda *args, **kwargs: touched.append("gateway"))

    with pytest.raises(KnowledgeModelNotAllowed, match="knowledge_model_not_allowed"):
        service.resolve(
            session=object(),  # type: ignore[arg-type]
            settings=_settings(),
            tenant_pub_id="tnt_fixture",
            request_id="req-fixture",
            body=_body(model="browser-injected-model"),
        )

    assert touched == []


def test_service_rejects_deterministic_policy_with_model_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(service, "gateway", lambda *args, **kwargs: touched.append("gateway"))

    with pytest.raises(KnowledgeModelNotApplicable, match="knowledge_model_not_applicable"):
        service.resolve(
            session=object(),  # type: ignore[arg-type]
            settings=_settings(),
            tenant_pub_id="tnt_fixture",
            request_id="req-fixture",
            body=_body(policy="deterministic_only", model="gpt-5.6-luna"),
        )

    assert touched == []
