from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from domain.brandrank.entities import load_entity_master, normalize_answer_entities
from domain.brandrank.rules import load_domain
from domain.knowledge_evolution.contracts import (
    GatewayResult,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    RuntimeRequest,
)
from domain.knowledge_evolution.domains.brand import (
    BrandEntityResolutionPack,
    apply_adopted_model_decisions,
)
from domain.knowledge_evolution.registry import DomainRegistry
from domain.knowledge_evolution.runtime import ReasoningEngine


class MemoryPersistence:
    def __init__(self) -> None:
        self.cache: dict[str, dict[str, Any]] = {}
        self.observations: list[ObservationDraft] = []
        self.traces: list[dict[str, Any]] = []

    def cache_get(self, key: str) -> dict[str, Any] | None:
        return self.cache.get(key)

    def cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        self.cache[key] = dict(value)

    def record_observations(self, tenant: str, observations: tuple[ObservationDraft, ...]) -> int:
        assert tenant == "tenant-brand"
        self.observations.extend(observations)
        return len(observations)

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        assert tenant == "tenant-brand"
        self.traces.append(dict(trace))


class BrandGateway:
    provider = "fixture"
    model = "fixture-brand-model"
    model_version = "1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[ModelPrompt] = []

    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        self.calls.append(prompt)
        return GatewayResult(
            payload=self.payload,
            provider=self.provider,
            model=self.model,
            model_version=self.model_version,
            latency_ms=7,
        )


def _request(*, adopt: bool) -> RuntimeRequest:
    return RuntimeRequest(
        request_id="brand-request-adopt" if adopt else "brand-request-observe",
        tenant="tenant-brand",
        namespace="geo-brandrank",
        domain="brand/entity-resolution",
        task="resolve_mentions",
        items=(
            {
                "id": "unknown",
                "value": "云盾X",
                "contexts": ["云盾X提供云安全防护。"],
                "source_ref": "answer-hash-1",
            },
        ),
        context={
            "analysis_domain": "cybersecurity",
            "comparison_scopes": ["cloud_security"],
            "allowed_evidence_refs": [],
        },
        policy=ReasoningPolicy.LLM_ASSISTED,
        policy_id="brandrank-runtime",
        policy_version="1",
        adopt_model_inferred=adopt,
        allow_external_model=True,
    )


def _payload(*, entity_id: str | None = None) -> dict[str, Any]:
    return {
        "decisions": [
            {
                "input_id": "unknown",
                "identity": {
                    "decision": "existing" if entity_id else "propose_new",
                    "entity_id": entity_id,
                    "canonical_name": "云盾X",
                    "entity_type": "product",
                },
                "relation": {"type": "independent"},
                "roll_up": {"entity_id": entity_id, "display_name": "云盾X"},
                "comparison": {"eligible": True, "scopes": ["cloud_security"]},
                "confidence": {
                    "identity": 0.8,
                    "relation": 0.7,
                    "roll_up": 0.8,
                    "eligibility": 0.75,
                },
                "reasons": ["输入上下文将其描述为云安全产品。"],
                "alternative_hypotheses": ["可能是既有厂商的产品线。"],
                "uncertainty": ["缺少官方主体证据。"],
                "evidence_refs": [],
            }
        ]
    }


def _engine(payload: dict[str, Any]) -> tuple[ReasoningEngine, BrandGateway, MemoryPersistence]:
    registry = DomainRegistry()
    registry.register(BrandEntityResolutionPack())
    gateway = BrandGateway(payload)
    persistence = MemoryPersistence()
    return ReasoningEngine(registry, persistence, gateway), gateway, persistence


def test_brand_model_hypothesis_can_affect_only_explicitly_adopted_request() -> None:
    engine, gateway, persistence = _engine(_payload())
    observed = engine.decide(_request(adopt=False))
    assert observed.decisions[0].knowledge_status.value == "unresolved"
    assert observed.model_hypotheses[0].knowledge_status.value == "model_inferred"
    assert observed.model_hypotheses[0].adopted is False
    assert persistence.observations[0].payload["knowledge_status"] == "model_inferred"
    assert "separate decisions" in gateway.calls[0].user_message

    adopted, _, _ = _engine(_payload())
    response = adopted.decide(_request(adopt=True))
    assert response.decisions[0].knowledge_status.value == "model_inferred"
    assert response.decisions[0].adopted is True

    base = load_entity_master("cybersecurity")
    overlay = apply_adopted_model_decisions(base, response.decisions)
    rows = normalize_answer_entities(
        ["云盾X"],
        rules=load_domain("cybersecurity"),
        master=overlay,
        comparison_scopes=["cloud_security"],
    )
    assert rows[0]["competitor_eligible"] is True
    assert rows[0]["knowledge_status"] == "model_inferred"
    assert rows[0]["review_status"] == "model_inferred"


def test_brand_model_rejects_invented_existing_identity() -> None:
    engine, _, _ = _engine(_payload(entity_id="invented-brand-id"))
    response = engine.decide(_request(adopt=True))
    assert response.decisions[0].knowledge_status.value == "unresolved"
    assert response.model_hypotheses == ()
    assert response.degradation == ("invalid_model_output",)


@pytest.mark.parametrize(
    ("value", "canonical", "relationship"),
    [
        ("腾讯云", "腾讯", "business_unit_of"),
        ("华为云", "华为", "business_unit_of"),
        ("绿盟", "绿盟科技", "official_abbreviation"),
        ("NSFOCUS", "绿盟科技", "english_name"),
        ("BJCA", "数字认证", "trade_name"),
    ],
)
def test_reviewed_hard_cases_keep_identity_relation_and_rollup_separate(
    value: str, canonical: str, relationship: str
) -> None:
    pack = BrandEntityResolutionPack()
    request = RuntimeRequest(
        request_id=f"hard-case-{value}",
        tenant="tenant-brand",
        namespace="test",
        domain="brand/entity-resolution",
        task="resolve",
        items=({"id": "one", "value": value},),
        context={"analysis_domain": "cybersecurity", "comparison_scopes": []},
        policy=ReasoningPolicy.DETERMINISTIC_ONLY,
        policy_id="test",
        policy_version="1",
    )
    decision = pack.deterministic_resolve(request)[0]
    assert decision.value["identity"]["canonical_name"] == canonical
    assert decision.value["relation"]["type"] == relationship
    assert decision.value["roll_up"]["display_name"] == canonical


def test_newland_is_scope_eligible_only_for_ctid() -> None:
    master = load_entity_master("cybersecurity")
    rules = load_domain("cybersecurity")
    ordinary = normalize_answer_entities(["新大陆"], rules=rules, master=master)
    ctid = normalize_answer_entities(
        ["新大陆"], rules=rules, master=master, comparison_scopes=["ctid"]
    )
    assert ordinary[0]["competitor_eligible"] is False
    assert ctid[0]["competitor_eligible"] is True


def test_tencent_aliases_collapse_once_inside_one_answer() -> None:
    rows = normalize_answer_entities(
        ["腾讯云", "腾讯", "腾讯安全"],
        rules=load_domain("cybersecurity"),
        master=load_entity_master("cybersecurity"),
    )
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "腾讯"
    assert rows[0]["raw_aliases"] == ["腾讯云", "腾讯", "腾讯安全"]


def test_brand_release_gate_rejects_non_public_evidence_uri() -> None:
    result = BrandEntityResolutionPack().validate_release(
        (
            {
                "stable_id": "brand:test",
                "object_type": "company",
                "attributes": {"evidence_urls": ["http://private.example/evidence"]},
                "visibility": "public",
                "review_status": "reviewed",
            },
        ),
        (),
    )
    assert result["passed"] is False
    assert result["issues"] == ["public_evidence_uri_invalid:brand:test"]
