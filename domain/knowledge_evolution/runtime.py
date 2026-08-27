"""Policy-driven runtime reasoning with governed model output and feedback."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from .contracts import (
    Decision,
    GatewayResult,
    KnowledgeStatus,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    RuntimeRequest,
    RuntimeResponse,
)
from .registry import DomainRegistry


class ReasoningError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelGateway(Protocol):
    provider: str
    model: str
    model_version: str

    def infer(self, prompt: ModelPrompt) -> GatewayResult: ...


class RuntimePersistence(Protocol):
    def cache_get(self, key: str) -> dict[str, Any] | None: ...

    def cache_put(self, key: str, value: Mapping[str, Any]) -> None: ...

    def record_observations(
        self, tenant: str, observations: tuple[ObservationDraft, ...]
    ) -> int: ...

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None: ...


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _gateway_to_cache(result: GatewayResult) -> dict[str, Any]:
    return {
        "payload": result.payload,
        "provider": result.provider,
        "model": result.model,
        "model_version": result.model_version,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "tool_summary": list(result.tool_summary),
    }


def _gateway_from_cache(value: Mapping[str, Any]) -> GatewayResult:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("cached_model_payload_invalid")
    raw_tools = value.get("tool_summary") or []
    if not isinstance(raw_tools, list) or not all(isinstance(item, dict) for item in raw_tools):
        raise ValueError("cached_tool_summary_invalid")
    return GatewayResult(
        payload=payload,
        provider=str(value.get("provider") or "unknown"),
        model=str(value.get("model") or "unknown"),
        model_version=str(value.get("model_version") or "unknown"),
        # A cache hit preserves model provenance and output, but incurs no new
        # provider latency, tokens, or charge.  Replaying original usage here
        # would overstate both budgets and operational cost.
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        tool_summary=tuple(raw_tools),
    )


class ReasoningEngine:
    def __init__(
        self,
        registry: DomainRegistry,
        persistence: RuntimePersistence,
        gateway: ModelGateway | None,
    ) -> None:
        self.registry = registry
        self.persistence = persistence
        self.gateway = gateway

    def _cache_key(
        self, request: RuntimeRequest, prompt: ModelPrompt, release_id: str, release_hash: str
    ) -> str:
        gateway = self.gateway
        return _canonical_hash(
            {
                "tenant": request.tenant,
                "namespace": request.namespace,
                "domain": request.domain,
                "task": request.task,
                "items": request.items,
                "context": request.context,
                "knowledge_release": release_id,
                "knowledge_hash": release_hash,
                "reasoning_policy": request.policy.value,
                "policy_id": request.policy_id,
                "policy_version": request.policy_version,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.prompt_version,
                "model_provider": gateway.provider if gateway else "unavailable",
                "model": gateway.model if gateway else "unavailable",
                "model_version": gateway.model_version if gateway else "unavailable",
                "tool_version": self.registry.get(request.domain).tool_version,
            }
        )

    def decide(self, request: RuntimeRequest) -> RuntimeResponse:
        started = time.perf_counter()
        if request.on_model_failure not in {"fail", "degrade"}:
            raise ReasoningError("invalid_model_failure_policy")
        pack = self.registry.get(request.domain)
        release = pack.release_ref(request)
        if request.expected_release_id and request.expected_release_id != release.release_id:
            raise ReasoningError("knowledge_release_mismatch")
        deterministic = pack.deterministic_resolve(request)
        unresolved = any(
            decision.knowledge_status == KnowledgeStatus.UNRESOLVED for decision in deterministic
        )
        call_model = (
            request.policy == ReasoningPolicy.LLM_REQUIRED
            or request.policy == ReasoningPolicy.EXPLORATORY
            or (request.policy == ReasoningPolicy.LLM_ASSISTED and unresolved)
        )
        degradation: list[str] = []
        model_result: GatewayResult | None = None
        model_decisions: tuple[Decision, ...] = ()
        prompt: ModelPrompt | None = None
        cache_status = "bypass"

        if call_model:
            prompt = pack.build_model_prompt(request, deterministic)
            model_failure: str | None = None
            if not request.allow_external_model:
                model_failure = "model_denied_by_data_policy"
            elif request.data_classification in {"confidential", "restricted"}:
                model_failure = "model_denied_by_data_policy"
            elif self.gateway is None:
                model_failure = "model_unavailable"
            if model_failure is None:
                assert prompt is not None
                cache_key = self._cache_key(
                    request, prompt, release.release_id, release.content_hash
                )
                try:
                    cached = self.persistence.cache_get(cache_key)
                except Exception:  # noqa: BLE001 - optional cache boundary is sanitized
                    cached = None
                    degradation.append("semantic_cache_read_failed")
                if cached is not None:
                    try:
                        model_result = _gateway_from_cache(cached)
                        cache_status = "hit"
                    except (TypeError, ValueError):
                        degradation.append("invalid_semantic_cache_entry")
                if model_result is None:
                    cache_status = "miss"
                    try:
                        assert self.gateway is not None
                        model_result = self.gateway.infer(prompt)
                    except Exception as exc:  # noqa: BLE001 - provider boundary is sanitized
                        gateway_code = getattr(exc, "code", None)
                        model_failure = (
                            str(gateway_code)
                            if isinstance(gateway_code, str)
                            and gateway_code.isascii()
                            and 1 <= len(gateway_code) <= 120
                            else f"model_error:{type(exc).__name__}"
                        )
                    else:
                        try:
                            self.persistence.cache_put(cache_key, _gateway_to_cache(model_result))
                        except Exception:  # noqa: BLE001 - optional cache boundary is sanitized
                            degradation.append("semantic_cache_write_failed")
            if model_failure is not None:
                if request.on_model_failure == "fail":
                    raise ReasoningError(model_failure)
                degradation.append(model_failure)
            elif model_result is not None:
                try:
                    model_decisions = pack.validate_model_output(
                        model_result.payload,
                        request=request,
                        deterministic=deterministic,
                    )
                    if request.policy == ReasoningPolicy.LLM_ASSISTED:
                        unresolved_ids = {
                            decision.input_id
                            for decision in deterministic
                            if decision.knowledge_status == KnowledgeStatus.UNRESOLVED
                        }
                        model_decisions = tuple(
                            decision
                            for decision in model_decisions
                            if decision.input_id in unresolved_ids
                        )
                except (TypeError, ValueError) as exc:
                    if request.on_model_failure == "fail":
                        raise ReasoningError("invalid_model_output") from exc
                    degradation.append("invalid_model_output")
                    model_decisions = ()

        budget_exceeded = False
        if model_result is not None:
            if (
                request.max_latency_ms is not None
                and model_result.latency_ms > request.max_latency_ms
            ):
                degradation.append("latency_budget_exceeded")
                budget_exceeded = True
            if request.max_cost_usd is not None:
                if model_result.cost_usd is None:
                    degradation.append("cost_budget_unverifiable")
                    budget_exceeded = True
                elif model_result.cost_usd > request.max_cost_usd:
                    degradation.append("cost_budget_exceeded")
                    budget_exceeded = True

        normalized_model: list[Decision] = []
        for decision in model_decisions:
            normalized_model.append(
                Decision(
                    input_id=decision.input_id,
                    input_value=decision.input_value,
                    value=decision.value,
                    knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                    decision_scope=decision.decision_scope,
                    confidence=decision.confidence,
                    reasons=decision.reasons,
                    alternative_hypotheses=decision.alternative_hypotheses,
                    uncertainty=decision.uncertainty,
                    evidence_refs=decision.evidence_refs,
                    adopted=request.adopt_model_inferred and not budget_exceeded,
                    model_provider=model_result.provider if model_result else None,
                    model_name=model_result.model if model_result else None,
                    model_version=model_result.model_version if model_result else None,
                    prompt_id=prompt.prompt_id if prompt else None,
                    prompt_version=prompt.prompt_version if prompt else None,
                )
            )
        inferred_by_input = {item.input_id: item for item in normalized_model if item.adopted}
        selected_decisions = tuple(
            inferred_by_input.get(item.input_id, item) for item in deterministic
        )
        decisions = tuple(
            replace(
                item,
                knowledge_release_id=release.release_id,
                knowledge_content_hash=release.content_hash,
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                tool_summary=model_result.tool_summary if model_result else (),
            )
            for item in selected_decisions
        )
        hypotheses = tuple(
            replace(
                item,
                knowledge_release_id=release.release_id,
                knowledge_content_hash=release.content_hash,
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                tool_summary=model_result.tool_summary if model_result else (),
            )
            for item in normalized_model
        )
        observations = pack.observations(request, (*decisions, *hypotheses))
        try:
            observation_count = self.persistence.record_observations(request.tenant, observations)
        except Exception:  # noqa: BLE001 - feedback must not block current reasoning
            observation_count = 0
            degradation.append("observation_persistence_failed")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        input_hash = _canonical_hash({"items": request.items, "context": request.context})
        try:
            self.persistence.record_trace(
                request.tenant,
                {
                    "request_id": request.request_id,
                    "namespace": request.namespace,
                    "domain": request.domain,
                    "task": request.task,
                    "input_hash": input_hash,
                    "policy_id": request.policy_id,
                    "policy_version": request.policy_version,
                    "reasoning_policy": request.policy.value,
                    "knowledge_release_id": release.release_id,
                    "knowledge_content_hash": release.content_hash,
                    "prompt_id": prompt.prompt_id if prompt else None,
                    "prompt_version": prompt.prompt_version if prompt else None,
                    "model_provider": model_result.provider if model_result else None,
                    "model": model_result.model if model_result else None,
                    "model_version": model_result.model_version if model_result else None,
                    "tool_version": pack.tool_version,
                    "adopt_model_inferred": request.adopt_model_inferred,
                    "adopted_model_decisions": sum(item.adopted for item in normalized_model),
                    "latency_ms": elapsed_ms,
                    "model_latency_ms": model_result.latency_ms if model_result else None,
                    "input_tokens": model_result.input_tokens if model_result else None,
                    "output_tokens": model_result.output_tokens if model_result else None,
                    "cost_usd": model_result.cost_usd if model_result else None,
                    "cache_status": cache_status,
                    "degradation": degradation,
                    "data_classification": request.data_classification,
                    "tool_summary": list(model_result.tool_summary) if model_result else [],
                },
            )
        except Exception:  # noqa: BLE001 - tracing must not block current reasoning
            degradation.append("trace_persistence_failed")
        return RuntimeResponse(
            request_id=request.request_id,
            domain=request.domain,
            task=request.task,
            policy=request.policy,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            release=release,
            decisions=decisions,
            model_hypotheses=hypotheses,
            prompt_id=prompt.prompt_id if prompt else None,
            prompt_version=prompt.prompt_version if prompt else None,
            model_provider=model_result.provider if model_result else None,
            model_name=model_result.model if model_result else None,
            model_version=model_result.model_version if model_result else None,
            latency_ms=elapsed_ms,
            cache_status=cache_status,
            degradation=tuple(degradation),
            observation_count=observation_count,
            usage={
                "input_tokens": model_result.input_tokens if model_result else None,
                "output_tokens": model_result.output_tokens if model_result else None,
                "cost_usd": model_result.cost_usd if model_result else None,
                "model_latency_ms": model_result.latency_ms if model_result else None,
                "tool_summary": list(model_result.tool_summary) if model_result else [],
            },
        )
