#!/usr/bin/env python3
"""Measure middleware overhead for every policy with a synthetic model boundary.

This is intentionally not a provider benchmark.  The gateway reports fixed
metering while returning schema-valid brand decisions without network I/O, so
the result isolates orchestration, validation, caching, and observation cost.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
API_ROOT = PROJECT_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from geo_platform.config import get_settings  # noqa: E402

from domain.knowledge_evolution.contracts import (  # noqa: E402
    GatewayResult,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    RuntimeRequest,
)
from domain.knowledge_evolution.domains.brand import BrandEntityResolutionPack  # noqa: E402
from domain.knowledge_evolution.registry import DomainRegistry  # noqa: E402
from domain.knowledge_evolution.runtime import ReasoningEngine  # noqa: E402


class BenchmarkPersistence:
    def __init__(self) -> None:
        self.cache: dict[str, dict[str, Any]] = {}
        self.traces: list[dict[str, Any]] = []
        self.observation_count = 0

    def cache_get(self, key: str) -> dict[str, Any] | None:
        value = self.cache.get(key)
        return dict(value) if value is not None else None

    def cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        self.cache[key] = dict(value)

    def record_observations(self, tenant: str, observations: tuple[ObservationDraft, ...]) -> int:
        del tenant
        self.observation_count += len(observations)
        return len(observations)

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        del tenant
        self.traces.append(dict(trace))


class SyntheticBrandGateway:
    provider = "synthetic-local"
    model = "schema-valid-fixture"
    model_version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        self.calls += 1
        body = json.loads(prompt.user_message)
        deterministic = {row["input_id"]: row["value"] for row in body["deterministic_results"]}
        decisions = []
        for item in body["items"]:
            input_id = item["input_id"]
            prior = deterministic[input_id]
            prior_identity = prior["identity"]
            known = prior_identity.get("review_status") == "reviewed"
            prior_roll_up = prior["roll_up"]
            decisions.append(
                {
                    "input_id": input_id,
                    "identity": {
                        "decision": "existing" if known else "propose_new",
                        "entity_id": prior_identity.get("entity_id") if known else None,
                        "canonical_name": prior_identity.get("canonical_name") or item["value"],
                        "entity_type": prior_identity.get("entity_type") if known else "brand",
                    },
                    "relation": {"type": prior["relation"].get("type") if known else "uncertain"},
                    "roll_up": {
                        "entity_id": prior_roll_up.get("entity_id") if known else None,
                        "display_name": prior_roll_up.get("display_name")
                        if known
                        else item["value"],
                    },
                    "comparison": {
                        "eligible": prior["comparison"].get("eligible") if known else False,
                        "scopes": prior["comparison"].get("scopes") if known else [],
                    },
                    "applicability": {
                        "tasks": [body["task"]],
                        "industries": [body["analysis_domain"]],
                        "regions": [],
                        "audiences": [],
                        "valid_from": None,
                        "valid_until": None,
                        "counterexamples": [],
                    },
                    "confidence": {
                        "identity": 0.99 if known else 0.55,
                        "relation": 0.99 if known else 0.4,
                        "roll_up": 0.99 if known else 0.5,
                        "eligibility": 0.99 if known else 0.5,
                    },
                    "reasons": ["synthetic benchmark fixture"],
                    "alternative_hypotheses": [],
                    "uncertainty": [] if known else ["requires evidence"],
                    "missing_evidence": [] if known else ["independent public source"],
                    "impact_if_wrong": "medium",
                    "evidence_refs": [],
                }
            )
        return GatewayResult(
            payload={"decisions": decisions},
            provider=self.provider,
            model=self.model,
            model_version=self.model_version,
            latency_ms=8,
            input_tokens=800,
            output_tokens=180,
            cost_usd=0.002,
        )


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _run(policy: ReasoningPolicy, *, iterations: int, variants: int) -> dict[str, Any]:
    settings = get_settings()
    persistence = BenchmarkPersistence()
    gateway = SyntheticBrandGateway()
    registry = DomainRegistry()
    registry.register(
        BrandEntityResolutionPack(
            snapshot_dir=settings.siliconindex_snapshot_dir,
            knowledge_release_dir=settings.knowledge_release_dir,
        )
    )
    engine = ReasoningEngine(registry, persistence, gateway)
    elapsed_ms: list[float] = []
    errors = 0
    cost = 0.0
    for index in range(iterations):
        variant = index % variants
        started = time.perf_counter_ns()
        try:
            response = engine.decide(
                RuntimeRequest(
                    request_id=f"benchmark-{policy.value}-{index}",
                    tenant="benchmark",
                    namespace="benchmark",
                    domain="brand/entity-resolution",
                    task="resolve",
                    items=(
                        {"id": "known", "value": "腾讯云", "contexts": []},
                        {
                            "id": "unknown",
                            "value": f"未收录品牌-{variant}",
                            "contexts": [f"公开基准上下文 {variant}"],
                        },
                    ),
                    context={
                        "analysis_domain": "cybersecurity",
                        "comparison_scopes": ["cloud_security"],
                        "allowed_evidence_refs": [],
                    },
                    policy=policy,
                    policy_id="benchmark",
                    policy_version="1",
                    adopt_model_inferred=True,
                    on_model_failure="degrade",
                    data_classification="public",
                    allow_external_model=True,
                )
            )
            errors += int(bool(response.degradation))
            cost += float(response.usage.get("cost_usd") or 0)
        except Exception:  # noqa: BLE001 - benchmark counts the guarded boundary
            errors += 1
        elapsed_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    cache_hits = sum(trace.get("cache_status") == "hit" for trace in persistence.traces)
    return {
        "policy": policy.value,
        "iterations": iterations,
        "input_variants": variants,
        "p50_ms": _percentile(elapsed_ms, 0.50),
        "p95_ms": _percentile(elapsed_ms, 0.95),
        "p99_ms": _percentile(elapsed_ms, 0.99),
        "mean_ms": statistics.fmean(elapsed_ms),
        "model_calls": gateway.calls,
        "model_call_rate": gateway.calls / iterations,
        "cache_hits": cache_hits,
        "cache_hit_rate": cache_hits / iterations,
        "cost_usd": cost,
        "error_rate": errors / iterations,
        "observations_emitted": persistence.observation_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--variants", type=int, default=25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 1 or args.variants < 1:
        raise SystemExit("iterations and variants must be positive")
    report = {
        "schema_version": "knowledge-runtime-benchmark-v1",
        "benchmark_kind": "synthetic_provider_no_network",
        "limitations": (
            "Measures middleware orchestration, validation, cache, and observation overhead; "
            "it does not measure a live provider or network."
        ),
        "results": [
            _run(policy, iterations=args.iterations, variants=args.variants)
            for policy in ReasoningPolicy
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
