from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from domain.knowledge_evolution.client import KnowledgeHttpClient
from domain.knowledge_evolution.contracts import (
    Decision,
    DecisionScope,
    GatewayResult,
    KnowledgeStatus,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    ReleaseRef,
    RuntimeRequest,
)
from domain.knowledge_evolution.domains.source_type_fixture import SourceTypeFixturePack
from domain.knowledge_evolution.gateway import GatewayError, OpenAICompatibleGateway
from domain.knowledge_evolution.merge import three_way_merge
from domain.knowledge_evolution.registry import DomainRegistry
from domain.knowledge_evolution.release import KnowledgeReleaseError, KnowledgeReleaseStore
from domain.knowledge_evolution.runtime import ReasoningEngine, ReasoningError


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
        assert tenant == "tenant-a"
        self.observations.extend(observations)
        return len(observations)

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        assert tenant == "tenant-a"
        self.traces.append(dict(trace))


class FailingOptionalPersistence(MemoryPersistence):
    def cache_get(self, key: str) -> dict[str, Any] | None:
        del key
        raise RuntimeError("private cache read detail")

    def cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        del key, value
        raise RuntimeError("private cache write detail")

    def record_observations(self, tenant: str, observations: tuple[ObservationDraft, ...]) -> int:
        del tenant, observations
        raise RuntimeError("private observation detail")

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        del tenant, trace
        raise RuntimeError("private trace detail")


class FakeGateway:
    provider = "fake-provider"
    model = "fake-model"
    model_version = "2026-08"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        self.calls += 1
        if self.fail:
            raise TimeoutError("provider secret must not escape")
        return GatewayResult(
            payload={"value": "model-class"},
            provider=self.provider,
            model=self.model,
            model_version=self.model_version,
            latency_ms=12,
            input_tokens=20,
            output_tokens=5,
            cost_usd=0.01,
            tool_summary=({"tool": "catalog", "status": "ok"},),
        )


class CodedFailureGateway(FakeGateway):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        del prompt
        self.calls += 1
        raise GatewayError(self.code)


class UnpricedGateway(FakeGateway):
    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        result = super().infer(prompt)
        return replace(result, cost_usd=None)


class FixturePack:
    domain_id = "test/entity"
    policy_version = "test-policy-1"
    prompt_id = "test-prompt"
    prompt_version = "test-prompt-1"
    tool_version = "test-tool-1"

    def __init__(self, release_id: str = "release-1") -> None:
        self.release_id = release_id

    def release_ref(self, request: RuntimeRequest) -> ReleaseRef:
        del request
        return ReleaseRef(self.release_id, f"sha256:{self.release_id}", "1", "test")

    def deterministic_resolve(self, request: RuntimeRequest) -> tuple[Decision, ...]:
        return tuple(
            Decision(
                input_id=str(item["id"]),
                input_value=str(item["value"]),
                value={"class": "known" if item["value"] == "known" else None},
                knowledge_status=(
                    KnowledgeStatus.PUBLISHED
                    if item["value"] == "known"
                    else KnowledgeStatus.UNRESOLVED
                ),
                decision_scope=(
                    DecisionScope.GLOBAL_RELEASE
                    if item["value"] == "known"
                    else DecisionScope.DOMAIN_CANDIDATE
                ),
                confidence=1 if item["value"] == "known" else 0,
                adopted=item["value"] == "known",
            )
            for item in request.items
        )

    def build_model_prompt(
        self, request: RuntimeRequest, deterministic: tuple[Decision, ...]
    ) -> ModelPrompt:
        del request, deterministic
        return ModelPrompt(
            self.prompt_id,
            self.prompt_version,
            "system",
            "user",
            {"type": "object"},
        )

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        del deterministic
        return tuple(
            Decision(
                input_id=str(item["id"]),
                input_value=str(item["value"]),
                value={"class": payload["value"]},
                knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                decision_scope=DecisionScope.REQUEST,
                confidence=0.8,
            )
            for item in request.items
            if item["value"] != "known"
        )

    def observations(
        self, request: RuntimeRequest, decisions: tuple[Decision, ...]
    ) -> tuple[ObservationDraft, ...]:
        selected = {row.input_id: row for row in decisions}
        return tuple(
            ObservationDraft(
                namespace=request.namespace,
                domain=request.domain,
                task=request.task,
                surface_form=row.input_value,
                normalized_key=row.input_value.casefold(),
                source_type="test",
                source_ref_hash="sha256:test",
                idempotency_key=f"idempotency-{row.input_id}",
                safe_context=None,
                data_classification="internal",
                visibility="private",
            )
            for row in selected.values()
            if row.knowledge_status != KnowledgeStatus.PUBLISHED
        )

    def project_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return {"objects": list(objects), "assertions": list(assertions)}


class OverreachingFixturePack(FixturePack):
    """Fixture model that tries to replace both known and unresolved results."""

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        del deterministic
        return tuple(
            Decision(
                input_id=str(item["id"]),
                input_value=str(item["value"]),
                value={"class": payload["value"]},
                knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                decision_scope=DecisionScope.REQUEST,
                confidence=0.8,
            )
            for item in request.items
        )


class InvalidModelOutputFixturePack(FixturePack):
    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        del payload, request, deterministic
        raise ValueError("provider output details must not escape")


def _request(**changes: Any) -> RuntimeRequest:
    base = RuntimeRequest(
        request_id="request-1",
        tenant="tenant-a",
        namespace="test",
        domain="test/entity",
        task="resolve",
        items=({"id": "one", "value": "unknown"},),
        context={},
        policy=ReasoningPolicy.LLM_ASSISTED,
        policy_id="caller-policy",
        policy_version="1",
        allow_external_model=True,
    )
    return replace(base, **changes)


def _engine(
    persistence: MemoryPersistence, gateway: FakeGateway | None, pack: FixturePack | None = None
) -> ReasoningEngine:
    registry = DomainRegistry()
    registry.register(pack or FixturePack())
    return ReasoningEngine(registry, persistence, gateway)


def test_assisted_model_is_request_hypothesis_until_explicitly_adopted() -> None:
    persistence = MemoryPersistence()
    gateway = FakeGateway()
    not_adopted = _engine(persistence, gateway).decide(_request())

    assert not_adopted.decisions[0].knowledge_status == KnowledgeStatus.UNRESOLVED
    assert not_adopted.model_hypotheses[0].knowledge_status == KnowledgeStatus.MODEL_INFERRED
    assert not_adopted.model_hypotheses[0].adopted is False
    assert not_adopted.model_hypotheses[0].knowledge_release_id == "release-1"
    assert not_adopted.model_hypotheses[0].policy_id == "caller-policy"
    assert not_adopted.model_hypotheses[0].tool_summary[0]["tool"] == "catalog"

    adopted = _engine(MemoryPersistence(), FakeGateway()).decide(
        _request(request_id="request-2", adopt_model_inferred=True)
    )
    assert adopted.decisions[0].knowledge_status == KnowledgeStatus.MODEL_INFERRED
    assert adopted.decisions[0].adopted is True
    assert adopted.decisions[0].decision_scope == DecisionScope.REQUEST


def test_policy_model_failure_privacy_and_cache_contracts() -> None:
    persistence = MemoryPersistence()
    gateway = FakeGateway(fail=True)
    degraded = _engine(persistence, gateway).decide(_request())
    assert degraded.decisions[0].knowledge_status == KnowledgeStatus.UNRESOLVED
    assert degraded.degradation == ("model_error:TimeoutError",)
    assert "secret" not in str(degraded)

    failing_persistence = MemoryPersistence()
    with pytest.raises(ReasoningError, match="model_error:TimeoutError"):
        _engine(failing_persistence, FakeGateway(fail=True)).decide(
            _request(on_model_failure="fail", model="fake-model")
        )
    assert failing_persistence.observations == []
    assert len(failing_persistence.traces) == 1
    assert failing_persistence.traces[0]["requested_model"] == "fake-model"
    assert failing_persistence.traces[0]["model"] is None
    assert failing_persistence.traces[0]["model_call_attempted"] is True
    assert failing_persistence.traces[0]["degradation"] == ["model_error:TimeoutError"]

    invalid_persistence = MemoryPersistence()
    with pytest.raises(ReasoningError, match="invalid_model_output"):
        _engine(
            invalid_persistence,
            FakeGateway(),
            InvalidModelOutputFixturePack(),
        ).decide(_request(on_model_failure="fail", model="fake-model"))
    assert invalid_persistence.observations == []
    assert invalid_persistence.cache == {}
    assert len(invalid_persistence.traces) == 1
    assert invalid_persistence.traces[0]["model"] == "fake-model"
    assert invalid_persistence.traces[0]["degradation"] == ["invalid_model_output"]

    denied_gateway = FakeGateway()
    denied = _engine(MemoryPersistence(), denied_gateway).decide(
        _request(allow_external_model=False)
    )
    assert denied_gateway.calls == 0
    assert denied.degradation == ("model_denied_by_data_policy",)

    for classification in ("confidential", "restricted"):
        classified_gateway = FakeGateway()
        classified = _engine(MemoryPersistence(), classified_gateway).decide(
            _request(
                request_id=classification,
                allow_external_model=True,
                data_classification=classification,
            )
        )
        assert classified_gateway.calls == 0
        assert classified.degradation == ("model_denied_by_data_policy",)

    cached_persistence = MemoryPersistence()
    cached_gateway = FakeGateway()
    engine = _engine(cached_persistence, cached_gateway)
    engine.decide(_request(request_id="cache-1"))
    cached = engine.decide(_request(request_id="cache-2"))
    assert cached.cache_status == "hit"
    assert cached_gateway.calls == 1
    assert cached.usage["cost_usd"] == 0.0
    assert cached.usage["input_tokens"] == 0
    assert cached.usage["model_latency_ms"] == 0

    changed_release = _engine(cached_persistence, cached_gateway, FixturePack("release-2"))
    changed_release.decide(_request(request_id="cache-3"))
    assert cached_gateway.calls == 2

    corrupt_key = next(iter(cached_persistence.cache))
    cached_persistence.cache[corrupt_key] = {"payload": []}
    recovered = engine.decide(_request(request_id="cache-corrupt"))
    assert recovered.cache_status == "miss"
    assert "invalid_semantic_cache_entry" in recovered.degradation
    assert cached_gateway.calls == 3
    assert isinstance(cached_persistence.cache[corrupt_key]["payload"], dict)

    cached_persistence.cache[corrupt_key]["requested_model"] = "other-model"
    lineage_recovered = engine.decide(_request(request_id="cache-lineage-corrupt"))
    assert lineage_recovered.cache_status == "miss"
    assert "invalid_semantic_cache_entry" in lineage_recovered.degradation
    assert cached_gateway.calls == 4
    assert cached_persistence.cache[corrupt_key]["requested_model"] is None

    cached_persistence.cache[corrupt_key]["payload"] = {"wrong": "domain-shape"}
    domain_recovered = engine.decide(_request(request_id="cache-domain-corrupt"))
    assert domain_recovered.cache_status == "miss"
    assert "invalid_semantic_cache_entry" in domain_recovered.degradation
    assert cached_gateway.calls == 5
    assert cached_persistence.cache[corrupt_key]["payload"] == {"value": "model-class"}


def test_requested_model_and_catalog_revision_are_cache_boundaries_and_traced() -> None:
    persistence = MemoryPersistence()
    first_gateway = FakeGateway()
    first_gateway.model = "model-a"
    first = _engine(persistence, first_gateway).decide(
        _request(
            request_id="model-a-1",
            model="model-a",
            model_catalog_revision="catalog-1",
        )
    )
    repeated = _engine(persistence, first_gateway).decide(
        _request(
            request_id="model-a-2",
            model="model-a",
            model_catalog_revision="catalog-1",
        )
    )
    assert first.cache_status == "miss"
    assert repeated.cache_status == "hit"
    assert first_gateway.calls == 1
    assert first.requested_model_name == "model-a"
    assert first.model_name == "model-a"
    assert first.provider_call_attempted is True
    assert repeated.provider_call_attempted is False
    assert persistence.traces[0]["requested_model"] == "model-a"
    assert persistence.traces[0]["model_catalog_revision"] == "catalog-1"

    second_gateway = FakeGateway()
    second_gateway.model = "model-b"
    _engine(persistence, second_gateway).decide(
        _request(
            request_id="model-b-1",
            model="model-b",
            model_catalog_revision="catalog-1",
        )
    )
    assert second_gateway.calls == 1
    assert len(persistence.cache) == 2

    _engine(persistence, first_gateway).decide(
        _request(
            request_id="model-a-catalog-2",
            model="model-a",
            model_catalog_revision="catalog-2",
        )
    )
    assert first_gateway.calls == 2
    assert len(persistence.cache) == 3


def test_deterministic_policy_rejects_an_explicit_model() -> None:
    with pytest.raises(ReasoningError, match="knowledge_model_not_applicable"):
        _engine(MemoryPersistence(), None).decide(
            _request(policy=ReasoningPolicy.DETERMINISTIC_ONLY, model="fake-model")
        )


def test_optional_persistence_failures_do_not_block_the_current_decision() -> None:
    response = _engine(FailingOptionalPersistence(), FakeGateway()).decide(
        _request(request_id="optional-persistence", adopt_model_inferred=True)
    )
    assert response.decisions[0].knowledge_status == KnowledgeStatus.MODEL_INFERRED
    assert response.observation_count == 0
    assert response.degradation == (
        "semantic_cache_read_failed",
        "semantic_cache_write_failed",
        "observation_persistence_failed",
        "trace_persistence_failed",
    )
    assert "private" not in str(response)


@pytest.mark.parametrize("code", ["model_timeout", "model_invalid_json", "tool_failure"])
def test_gateway_failure_codes_are_disclosed_without_provider_details(code: str) -> None:
    response = _engine(MemoryPersistence(), CodedFailureGateway(code)).decide(_request())
    assert response.degradation == (code,)


@pytest.mark.parametrize(
    "policy",
    [ReasoningPolicy.LLM_ASSISTED, ReasoningPolicy.LLM_REQUIRED, ReasoningPolicy.EXPLORATORY],
)
def test_each_model_enabled_policy_calls_gateway_for_unresolved_input(
    policy: ReasoningPolicy,
) -> None:
    gateway = FakeGateway()
    response = _engine(MemoryPersistence(), gateway).decide(
        _request(policy=policy, request_id=f"policy-{policy.value}")
    )
    assert gateway.calls == 1
    assert response.model_hypotheses


def test_assisted_policy_cannot_replace_a_deterministic_known_decision() -> None:
    response = _engine(MemoryPersistence(), FakeGateway(), OverreachingFixturePack()).decide(
        _request(
            request_id="assisted-known-guard",
            adopt_model_inferred=True,
            items=(
                {"id": "known", "value": "known"},
                {"id": "unknown", "value": "unknown"},
            ),
        )
    )
    by_id = {decision.input_id: decision for decision in response.decisions}
    assert by_id["known"].knowledge_status == KnowledgeStatus.PUBLISHED
    assert by_id["known"].value == {"class": "known"}
    assert by_id["unknown"].knowledge_status == KnowledgeStatus.MODEL_INFERRED
    assert [decision.input_id for decision in response.model_hypotheses] == ["unknown"]


def test_latency_budget_prevents_adoption_but_keeps_hypothesis() -> None:
    response = _engine(MemoryPersistence(), FakeGateway()).decide(
        _request(adopt_model_inferred=True, max_latency_ms=5)
    )
    assert response.decisions[0].knowledge_status == KnowledgeStatus.UNRESOLVED
    assert response.model_hypotheses[0].adopted is False
    assert "latency_budget_exceeded" in response.degradation

    cost_limited = _engine(MemoryPersistence(), FakeGateway()).decide(
        _request(
            request_id="cost-limited",
            adopt_model_inferred=True,
            max_cost_usd=0.001,
        )
    )
    assert cost_limited.decisions[0].knowledge_status == KnowledgeStatus.UNRESOLVED
    assert "cost_budget_exceeded" in cost_limited.degradation

    unpriced = _engine(MemoryPersistence(), UnpricedGateway()).decide(
        _request(
            request_id="unpriced",
            adopt_model_inferred=True,
            max_cost_usd=0.01,
        )
    )
    assert unpriced.decisions[0].knowledge_status == KnowledgeStatus.UNRESOLVED
    assert "cost_budget_unverifiable" in unpriced.degradation


def test_deterministic_policy_never_calls_gateway_and_fixture_is_non_brand() -> None:
    persistence = MemoryPersistence()
    gateway = FakeGateway()
    registry = DomainRegistry()
    registry.register(SourceTypeFixturePack())
    response = ReasoningEngine(registry, persistence, gateway).decide(
        RuntimeRequest(
            request_id="fixture-1",
            tenant="tenant-a",
            namespace="test",
            domain="source/type-fixture",
            task="classify",
            items=(
                {"id": "official", "value": "official"},
                {"id": "unknown", "value": "forum"},
            ),
            context={},
            policy=ReasoningPolicy.DETERMINISTIC_ONLY,
            policy_id="fixture",
            policy_version="1",
        )
    )
    assert gateway.calls == 0
    assert [row.knowledge_status for row in response.decisions] == [
        KnowledgeStatus.PUBLISHED,
        KnowledgeStatus.UNRESOLVED,
    ]
    assert response.observation_count == 1


def test_three_way_merge_never_silently_chooses_conflicting_writer() -> None:
    clean = three_way_merge(
        {"name": "base", "scope": "old"},
        {"name": "upstream", "scope": "old"},
        {"name": "base", "scope": "local"},
    )
    assert clean.conflicts == ()
    assert clean.merged == {"name": "upstream", "scope": "local"}

    conflict = three_way_merge(
        {"name": "base"},
        {"name": "upstream"},
        {"name": "local"},
    )
    assert len(conflict.conflicts) == 1
    assert conflict.conflicts[0].path == "/name"


def test_immutable_release_verification_fallback_and_rollback(tmp_path: Path) -> None:
    store = KnowledgeReleaseStore(tmp_path)
    first = store.publish(
        release_id="release-1",
        schema_version="1",
        documents={"test/entity": {"value": 1}},
        parent_release_id=None,
        quality_report={"gate": "passed"},
        activate=True,
    )
    store.publish(
        release_id="release-2",
        schema_version="1",
        documents={"test/entity": {"value": 2}},
        parent_release_id="release-1",
        quality_report={"gate": "passed"},
        activate=True,
    )
    assert first["content_hash"].startswith("sha256:")
    assert store.previous_release_id() == "release-1"

    artifact = tmp_path / "release-2" / str(store.manifest("release-2")["artifacts"]["test/entity"])
    artifact.write_text('{"value":999}\n', encoding="utf-8")
    document, manifest, degraded = store.load_domain_resilient("test/entity")
    assert document == {"value": 1}
    assert manifest["release_id"] == "release-1"
    assert degraded is True

    store.rollback("release-1")
    assert store.current_release_id() == "release-1"
    with pytest.raises(KnowledgeReleaseError, match="immutable_release_content_mismatch"):
        store.publish(
            release_id="release-1",
            schema_version="1",
            documents={"test/entity": {"value": "changed"}},
            parent_release_id=None,
            quality_report={},
        )


def test_http_sdk_is_business_module_independent() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/runtime/resolve")
        body = request.read().decode()
        assert '"tenant"' not in body
        return httpx.Response(200, json={"decisions": [], "request_id": "request-1"})

    client = KnowledgeHttpClient(
        "https://knowledge.example",
        headers={"Authorization": "Bearer opaque"},
        transport=httpx.MockTransport(handler),
    )
    result = client.resolve(_request(policy=ReasoningPolicy.DETERMINISTIC_ONLY))
    assert result["request_id"] == "request-1"


def test_http_sdk_installs_verified_replica_and_resolves_new_request_offline(
    tmp_path: Path,
) -> None:
    import httpx

    documents = {
        "source/type-fixture": {
            "schema_version": "source-type-v2",
            "domain": "source/type-fixture",
            "entries": [
                {
                    "key": "forum",
                    "source_type": "social_source",
                    "knowledge_status": "reviewed_local",
                }
            ],
        }
    }
    server_store = KnowledgeReleaseStore(tmp_path / "server")
    manifest = server_store.publish(
        release_id="knowledge-2026-08-27.99",
        schema_version="knowledge-release-v1",
        documents=documents,
        parent_release_id=None,
        quality_report={"quality_gate": "passed"},
        activate=True,
    )

    def online(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime/resolve"):
            return httpx.Response(
                200,
                json={
                    "request_id": "online",
                    "release": {
                        "release_id": manifest["release_id"],
                        "content_hash": manifest["content_hash"],
                        "schema_version": manifest["schema_version"],
                        "source": "knowledge_service",
                        "degraded": False,
                    },
                    "decisions": [],
                    "degradation": [],
                },
            )
        if request.url.path.endswith("/replica"):
            return httpx.Response(200, json={"manifest": manifest, "documents": documents})
        return httpx.Response(404)

    replica_dir = tmp_path / "client-replica"
    initial_request = RuntimeRequest(
        request_id="install-replica",
        tenant="tenant-a",
        namespace="shared",
        domain="source/type-fixture",
        task="classify",
        items=({"id": "unknown", "value": "unknown"},),
        context={},
        policy=ReasoningPolicy.DETERMINISTIC_ONLY,
        policy_id="replica-test",
        policy_version="1",
    )
    KnowledgeHttpClient(
        "https://knowledge.example",
        transport=httpx.MockTransport(online),
        replica_dir=replica_dir,
    ).resolve(initial_request)
    assert KnowledgeReleaseStore(replica_dir).current_release_id() == manifest["release_id"]

    registry = DomainRegistry()
    registry.register(SourceTypeFixturePack(knowledge_release_dir=str(replica_dir)))

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    result = KnowledgeHttpClient(
        "https://knowledge.example",
        transport=httpx.MockTransport(offline),
        replica_dir=replica_dir,
        local_registry=registry,
    ).resolve(
        replace(
            initial_request,
            request_id="new-offline-request",
            items=({"id": "forum", "value": "forum"},),
        )
    )
    assert result["cache_status"] == "client_local_replica"
    assert result["release"]["degraded"] is True
    assert result["decisions"][0]["value"] == {"source_type": "social_source"}
    assert "local_replica_deterministic_only" in result["degradation"]


def test_structured_gateway_executes_allowlisted_tool_and_returns_safe_summary() -> None:
    import json

    import httpx

    calls = 0

    def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.read())
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert body["model"] == "fixture-model"
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "catalog_lookup",
                                            "arguments": '{"query":"known"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )
        assert body["messages"][-1]["role"] == "tool"
        assert json.loads(body["messages"][-1]["content"]) == {"matches": ["known"]}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"value":"resolved"}'}}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 4},
            },
        )

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        tool_handlers={"catalog_lookup": lambda arguments: {"matches": [arguments["query"]]}},
        transport=httpx.MockTransport(transport_handler),
    )
    result = gateway.infer(
        ModelPrompt(
            prompt_id="tool-test",
            prompt_version="1",
            system_message="Use tools.",
            user_message="Resolve.",
            output_schema={"type": "object"},
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "catalog_lookup",
                        "description": "Lookup a catalog value.",
                        "parameters": {"type": "object"},
                    },
                },
            ),
        )
    )
    assert result.payload == {"value": "resolved"}
    assert result.input_tokens == 25
    assert result.output_tokens == 6
    assert result.tool_summary[0]["tool"] == "catalog_lookup"
    assert "known" not in str(result.tool_summary)
    assert result.requested_model == "fixture-model"
    assert result.model == "fixture-model"
    assert result.model_identity_source == "requested_fallback"


def test_structured_gateway_records_provider_resolved_model_separately() -> None:
    import httpx

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-alias",
        model_version="1",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "model": "fixture-model-20260828",
                    "choices": [{"message": {"content": '{"value":"resolved"}'}}],
                },
            )
        ),
    )

    result = gateway.infer(ModelPrompt("resolved-model", "1", "system", "user", {"type": "object"}))

    assert result.requested_model == "fixture-alias"
    assert result.model == "fixture-model-20260828"
    assert result.model_identity_source == "provider_response"


def test_structured_gateway_sanitizes_tool_failure() -> None:
    import httpx

    def transport_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "failing_tool",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    def failing_tool(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        del arguments
        raise RuntimeError("private tool detail")

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        tool_handlers={"failing_tool": failing_tool},
        transport=httpx.MockTransport(transport_handler),
    )
    with pytest.raises(GatewayError, match="tool_failure") as exc_info:
        gateway.infer(
            ModelPrompt(
                "tool-test",
                "1",
                "system",
                "user",
                {"type": "object"},
                tools=(
                    {
                        "type": "function",
                        "function": {"name": "failing_tool"},
                    },
                ),
            )
        )
    assert "private" not in str(exc_info.value)


def test_structured_gateway_retries_a_transient_provider_failure() -> None:
    import httpx

    calls = 0

    def transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "transient"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"value":"resolved"}'}}]},
        )

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        max_retries=1,
        transport=httpx.MockTransport(transport_handler),
    )
    result = gateway.infer(ModelPrompt("retry-test", "1", "system", "user", {"type": "object"}))
    assert result.payload == {"value": "resolved"}
    assert calls == 2


@pytest.mark.parametrize("status", [429, 500])
def test_structured_gateway_discloses_terminal_upstream_status_without_body(
    status: int,
) -> None:
    import httpx

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, json={"error": "private provider detail"})
        ),
    )

    with pytest.raises(GatewayError, match=f"model_upstream_{status}") as exc_info:
        gateway.infer(ModelPrompt("status-test", "1", "system", "user", {"type": "object"}))
    assert "private provider detail" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            {"content": b"not-json", "headers": {"Content-Type": "application/json"}},
            "model_invalid_json",
        ),
        ({"json": {"choices": []}}, "model_invalid_shape"),
    ],
)
def test_structured_gateway_rejects_malformed_provider_responses(
    response: dict[str, Any],
    code: str,
) -> None:
    import httpx

    def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, **response)

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        max_retries=0,
        transport=httpx.MockTransport(transport_handler),
    )
    with pytest.raises(GatewayError, match=code):
        gateway.infer(ModelPrompt("shape-test", "1", "system", "user", {"type": "object"}))


def test_structured_gateway_sanitizes_transport_timeout() -> None:
    import httpx

    def transport_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout detail", request=request)

    gateway = OpenAICompatibleGateway(
        api_key="test-secret",
        base_url="https://model.example",
        base_url_fallback="",
        provider="fixture",
        model="fixture-model",
        model_version="1",
        max_retries=0,
        transport=httpx.MockTransport(transport_handler),
    )
    with pytest.raises(GatewayError, match="model_timeout") as exc_info:
        gateway.infer(ModelPrompt("timeout-test", "1", "system", "user", {"type": "object"}))
    assert "private timeout detail" not in str(exc_info.value)
