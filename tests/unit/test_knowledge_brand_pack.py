from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
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
from domain.knowledge_evolution.release import KnowledgeReleaseStore
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
                "applicability": {
                    "tasks": ["resolve_mentions"],
                    "industries": ["cybersecurity"],
                    "regions": [],
                    "audiences": [],
                    "valid_from": None,
                    "valid_until": None,
                    "counterexamples": [],
                },
                "confidence": {
                    "identity": 0.8,
                    "relation": 0.7,
                    "roll_up": 0.8,
                    "eligibility": 0.75,
                },
                "reasons": ["输入上下文将其描述为云安全产品。"],
                "alternative_hypotheses": ["可能是既有厂商的产品线。"],
                "uncertainty": ["缺少官方主体证据。"],
                "missing_evidence": ["官方主体关系。"],
                "impact_if_wrong": "medium",
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


def test_brand_model_rejects_identity_attached_to_an_unrelated_rollup() -> None:
    payload = _payload()
    row = payload["decisions"][0]
    row["identity"] = {
        "decision": "existing",
        "entity_id": "CYB-OBJ-TENCENT-CLOUD",
        "canonical_name": "腾讯云",
        "entity_type": "business_unit",
    }
    row["relation"] = {"type": "business_unit_of"}
    row["roll_up"] = {
        "entity_id": "CYB-BR-HUAWEI",
        "display_name": "华为",
    }

    engine, _, _ = _engine(payload)
    response = engine.decide(_request(adopt=True))

    assert response.decisions[0].knowledge_status.value == "unresolved"
    assert response.model_hypotheses == ()
    assert response.degradation == ("invalid_model_output",)


@pytest.mark.parametrize(
    ("value", "identity", "roll_up", "identity_type", "relationship"),
    [
        ("腾讯云", "腾讯云", "腾讯", "business_unit", "business_unit_of"),
        ("华为云", "华为云", "华为", "business_unit", "business_unit_of"),
        ("绿盟", "绿盟科技", "绿盟科技", "brand", "official_abbreviation"),
        ("NSFOCUS", "绿盟科技", "绿盟科技", "brand", "english_name"),
        ("BJCA", "数字认证", "数字认证", "brand", "trade_name"),
        (
            "绿盟科技集团股份有限公司",
            "绿盟科技集团股份有限公司",
            "绿盟科技",
            "legal_entity",
            "same_legal_entity",
        ),
    ],
)
def test_reviewed_hard_cases_keep_identity_relation_and_rollup_separate(
    value: str,
    identity: str,
    roll_up: str,
    identity_type: str,
    relationship: str,
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
    assert decision.value["identity"]["canonical_name"] == identity
    assert decision.value["identity"]["entity_type"] == identity_type
    assert decision.value["relation"]["type"] == relationship
    assert decision.value["roll_up"]["display_name"] == roll_up


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
        ["腾讯云", "腾讯"],
        rules=load_domain("cybersecurity"),
        master=load_entity_master("cybersecurity"),
    )
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "腾讯"
    assert rows[0]["raw_aliases"] == ["腾讯云", "腾讯"]


def test_tencent_security_business_unit_is_published_with_claim_specific_evidence() -> None:
    rows = normalize_answer_entities(
        ["腾讯安全"],
        rules=load_domain("cybersecurity"),
        master=load_entity_master("cybersecurity"),
    )
    assert rows[0]["identity_entity_id"] == "CYB-OBJ-TENCENT-SECURITY"
    assert rows[0]["identity_entity_type"] == "business_unit"
    assert rows[0]["entity_id"] == "CYB-BR-TENCENT"
    assert rows[0]["knowledge_status"] == "published"
    assert rows[0]["competitor_eligible"] is True
    assert rows[0]["relationship_to_canonical"] == "business_unit_of"
    assert rows[0]["evidence_urls"]


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


def test_brand_release_gate_requires_evidence_for_concrete_identity_objects() -> None:
    result = BrandEntityResolutionPack().validate_release(
        (
            {
                "stable_id": "brand:test",
                "object_type": "brand_family",
                "attributes": {
                    "evidence_urls": ["https://example.test/brand"],
                    "alias_identities": {
                        "测试公司": {
                            "entity_id": "object:test-legal",
                            "canonical_name": "测试公司有限公司",
                            "entity_type": "legal_entity",
                            "evidence_urls": [],
                        }
                    },
                },
                "visibility": "public",
                "review_status": "reviewed",
            },
        ),
        (),
    )
    assert result["passed"] is False
    assert "alias_identity_evidence_required:object:test-legal" in result["issues"]


def test_brand_release_impact_requires_reproducible_historical_replay() -> None:
    pack = BrandEntityResolutionPack()
    missing = pack.validate_release_impact(({"kind": "knowledge_object"},), {})
    assert missing["passed"] is False
    assert "historical_replay_required" in missing["issues"]

    report = {
        "historical_replay": {
            "schema_version": "historical-replay-v1",
            "evaluation_set_hash": "sha256:" + "a" * 64,
            "time_cutoff": "2026-08-26T23:59:59+08:00",
            "evaluated_request_count": 12,
            "baseline_error_count": 3,
            "candidate_error_count": 1,
            "corrected_error_count": 2,
            "new_error_count": 0,
            "allowed_new_error_count": 0,
            "passed": True,
        }
    }
    accepted = pack.validate_release_impact(({"kind": "knowledge_object"},), report)
    assert accepted == {
        "passed": True,
        "replay_required": True,
        "issues": [],
        "change_count": 1,
        "evaluation_set_hash": "sha256:" + "a" * 64,
        "evaluated_request_count": 12,
        "new_error_count": 0,
        "allowed_new_error_count": 0,
    }

    report["historical_replay"]["new_error_count"] = 1
    rejected = pack.validate_release_impact(({"kind": "knowledge_object"},), report)
    assert rejected["passed"] is False
    assert "historical_replay_regression_budget_exceeded" in rejected["issues"]


def test_brand_release_impact_is_executed_server_side_and_bound_to_candidate(
    tmp_path: Path,
) -> None:
    projected = json.loads(
        (
            Path(__file__).parents[2]
            / "domain/brandrank/rules_data/siliconindex_projection_cybersecurity.json"
        ).read_text(encoding="utf-8")
    )
    candidate_document = {
        "schema_version": "brand-knowledge-v1",
        "domain": "brand/entity-resolution",
        "analysis_domains": {"cybersecurity": projected},
    }
    store = KnowledgeReleaseStore(tmp_path)
    store.publish(
        release_id="knowledge-baseline",
        schema_version="knowledge-release-v1",
        documents={"brand/entity-resolution": candidate_document},
        parent_release_id=None,
        quality_report={},
        activate=True,
    )
    result = BrandEntityResolutionPack(knowledge_release_dir=str(tmp_path)).evaluate_release_impact(
        changes=({"kind": "knowledge_object"},),
        candidate_document=candidate_document,
        parent_release_id="knowledge-baseline",
        candidate_release_id="knowledge-candidate",
    )
    assert result["execution"] == "server"
    assert result["runner"] == "brand-domain-pack-v2"
    assert result["candidate_release_id"] == "knowledge-candidate"
    assert result["candidate_state_hash"].startswith("sha256:")
    assert result["evaluated_request_count"] == 22
    assert result["passed"] is True
