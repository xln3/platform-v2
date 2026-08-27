#!/usr/bin/env python3
"""Evaluate every reasoning policy against the versioned brand gold set."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
API_ROOT = PROJECT_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.knowledge.service import gateway as configured_gateway  # noqa: E402

from domain.knowledge_evolution.contracts import (  # noqa: E402
    ObservationDraft,
    ReasoningPolicy,
    RuntimeRequest,
)
from domain.knowledge_evolution.domains.brand import BrandEntityResolutionPack  # noqa: E402
from domain.knowledge_evolution.registry import DomainRegistry  # noqa: E402
from domain.knowledge_evolution.runtime import ReasoningEngine  # noqa: E402

DEFAULT_GOLD = PROJECT_ROOT / "domain" / "knowledge_evolution" / "domains" / "brand_eval_v1.json"


class EvaluationPersistence:
    def __init__(self) -> None:
        self.cache: dict[str, dict[str, Any]] = {}
        self.observations: list[ObservationDraft] = []
        self.traces: list[dict[str, Any]] = []

    def cache_get(self, key: str) -> dict[str, Any] | None:
        value = self.cache.get(key)
        return dict(value) if value is not None else None

    def cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        self.cache[key] = dict(value)

    def record_observations(self, tenant: str, observations: tuple[ObservationDraft, ...]) -> int:
        del tenant
        self.observations.extend(observations)
        return len(observations)

    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        del tenant
        self.traces.append(dict(trace))


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return float(ordered[index])


def _evaluate_policy(
    gold: dict[str, Any],
    *,
    policy: ReasoningPolicy,
    allow_external_model: bool,
) -> dict[str, Any]:
    settings = get_settings()
    persistence = EvaluationPersistence()
    pack = BrandEntityResolutionPack(
        snapshot_dir=settings.siliconindex_snapshot_dir,
        knowledge_release_dir=settings.knowledge_release_dir,
    )
    registry = DomainRegistry()
    registry.register(pack)
    model_gateway = configured_gateway(settings) if allow_external_model else None
    engine = ReasoningEngine(registry, persistence, model_gateway)
    errors: list[dict[str, Any]] = []
    identity_total = identity_correct = 0
    identity_type_total = identity_type_correct = 0
    roll_up_total = roll_up_correct = 0
    relation_total = relation_correct = 0
    eligibility_total = eligibility_correct = 0
    dedupe_total = dedupe_correct = 0
    brier_terms: list[float] = []
    latencies: list[int] = []
    degradations: dict[str, int] = {}
    total_cost = 0.0

    for case in gold["cases"]:
        mentions = list(case["mentions"])
        contexts = list(case.get("contexts") or [])
        response = engine.decide(
            RuntimeRequest(
                request_id=f"eval-{policy.value}-{case['id']}",
                tenant="evaluation",
                namespace="brand-evaluation",
                domain="brand/entity-resolution",
                task="gold_set_evaluation",
                items=tuple(
                    {
                        "id": f"{case['id']}-{index}",
                        "value": mention,
                        "contexts": contexts,
                        "source_type": "versioned_gold_set",
                    }
                    for index, mention in enumerate(mentions)
                ),
                context={
                    "analysis_domain": gold["analysis_domain"],
                    "comparison_scopes": case.get("comparison_scopes") or [],
                    "allowed_evidence_refs": [],
                },
                policy=policy,
                policy_id="brand-eval",
                policy_version=gold["schema_version"],
                adopt_model_inferred=True,
                on_model_failure="degrade",
                data_classification="public",
                allow_external_model=allow_external_model,
            )
        )
        latencies.append(response.latency_ms)
        total_cost += float(response.usage.get("cost_usd") or 0)
        for code in response.degradation:
            degradations[code] = degradations.get(code, 0) + 1
        expected = case["expected"]
        expected_id = expected["entity_id"]
        expected_roll_up_id = expected.get("roll_up_entity_id", expected_id)
        expected_identities = expected.get("identity_by_mention") or {}
        expected_identity_types = expected.get("identity_types") or {}
        seen_ids: set[str] = set()
        case_failed = False
        for mention, decision in zip(mentions, response.decisions, strict=True):
            identity = decision.value.get("identity") or {}
            relation = decision.value.get("relation") or {}
            roll_up = decision.value.get("roll_up") or {}
            comparison = decision.value.get("comparison") or {}
            actual_id = identity.get("entity_id")
            actual_roll_up_id = roll_up.get("entity_id")
            if decision.knowledge_status.value == "unresolved":
                actual_id = None
                actual_roll_up_id = None
            expected_identity_id = expected_identities.get(mention, expected_id)
            identity_total += 1
            identity_match = actual_id == expected_identity_id
            identity_correct += int(identity_match)
            expected_identity_type = expected_identity_types.get(mention)
            identity_type_match = (
                expected_identity_type is None
                or identity.get("entity_type") == expected_identity_type
            )
            if expected_identity_type is not None:
                identity_type_total += 1
                identity_type_correct += int(identity_type_match)
            roll_up_total += 1
            roll_up_match = actual_roll_up_id == expected_roll_up_id
            roll_up_correct += int(roll_up_match)
            # Confidence is confidence in an affirmative identity, not in a
            # correct abstention.  Gold negatives therefore have target 0.
            affirmative_identity = expected_identity_id is not None and identity_match
            brier_terms.append((decision.confidence - float(affirmative_identity)) ** 2)
            relation_total += 1
            expected_relation = expected["relationships"][mention]
            actual_relation = relation.get("type")
            relation_correct += int(actual_relation == expected_relation)
            eligibility_total += 1
            actual_eligible = bool(comparison.get("eligible"))
            eligibility_correct += int(actual_eligible == expected["eligible"])
            if actual_roll_up_id is not None:
                seen_ids.add(str(actual_roll_up_id))
            else:
                seen_ids.add(f"unresolved:{mention}")
            if (
                not identity_match
                or not identity_type_match
                or not roll_up_match
                or actual_relation != expected_relation
                or actual_eligible != expected["eligible"]
            ):
                case_failed = True
        dedupe_total += 1
        dedupe_match = len(seen_ids) == expected["deduped_entity_count"]
        dedupe_correct += int(dedupe_match)
        if case_failed or not dedupe_match:
            errors.append(
                {
                    "case_id": case["id"],
                    "expected": expected,
                    "actual": [asdict(decision) for decision in response.decisions],
                    "degradation": list(response.degradation),
                }
            )

    traces = persistence.traces
    model_calls = sum(
        trace.get("model_provider") is not None and trace.get("cache_status") == "miss"
        for trace in traces
    )
    return {
        "policy": policy.value,
        "cases": len(gold["cases"]),
        "external_model_allowed": allow_external_model,
        "model_configured": model_gateway is not None,
        "model_calls": model_calls,
        "cache_hits": sum(trace.get("cache_status") == "hit" for trace in traces),
        "observation_count": len(persistence.observations),
        "identity_accuracy": identity_correct / identity_total,
        "identity_type_accuracy": (
            identity_type_correct / identity_type_total if identity_type_total else None
        ),
        "roll_up_accuracy": roll_up_correct / roll_up_total,
        "relation_accuracy": relation_correct / relation_total,
        "eligibility_accuracy": eligibility_correct / eligibility_total,
        "dedupe_accuracy": dedupe_correct / dedupe_total,
        "identity_brier_score": statistics.fmean(brier_terms),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "cost_usd": total_cost,
        "degradation_counts": degradations,
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--policies",
        default=",".join(policy.value for policy in ReasoningPolicy),
    )
    parser.add_argument("--allow-external-model", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    policies = [ReasoningPolicy(value.strip()) for value in args.policies.split(",")]
    report = {
        "schema_version": "brand-evaluation-report-v1",
        "gold_schema_version": gold["schema_version"],
        "gold_path": str(args.gold),
        "results": [
            _evaluate_policy(
                gold,
                policy=policy,
                allow_external_model=args.allow_external_model,
            )
            for policy in policies
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
