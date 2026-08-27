#!/usr/bin/env python3
"""Run a time-split pilot comparison of seven knowledge-update strategies.

The non-project methods are controlled operational analogues, not claims of a
faithful reproduction of another paper's full training recipe.  The report
records that limitation so these numbers cannot be reused as a novelty claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.brandrank.entities import EntityMaster, _parse_master, classify_entity  # noqa: E402
from domain.brandrank.rules import load_domain  # noqa: E402
from domain.knowledge_evolution.release import KnowledgeReleaseStore  # noqa: E402
from domain.siliconindex import project_brand_domain  # noqa: E402

DEFAULT_GOLD = PROJECT_ROOT / "domain/knowledge_evolution/domains/brand_eval_v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _entities(document: Mapping[str, Any], analysis_domain: str) -> list[dict[str, Any]]:
    domains = document.get("analysis_domains")
    projected = domains.get(analysis_domain) if isinstance(domains, Mapping) else None
    if not isinstance(projected, Mapping) or not isinstance(projected.get("entities"), list):
        raise ValueError(f"analysis_domain_missing:{analysis_domain}")
    return [deepcopy(value) for value in projected["entities"] if isinstance(value, dict)]


def _document(
    template: Mapping[str, Any], analysis_domain: str, entities: list[dict[str, Any]]
) -> dict[str, Any]:
    value: dict[str, Any] = deepcopy(dict(template))
    value["analysis_domains"][analysis_domain]["entities"] = sorted(
        entities, key=lambda row: str(row.get("entity_id") or "")
    )
    return value


def _master(document: Mapping[str, Any], analysis_domain: str, label: str) -> EntityMaster:
    projected = deepcopy(document["analysis_domains"][analysis_domain])
    projected.update(
        {
            "source_system": "controlled_method_comparison",
            "source_release_id": label,
            "source_content_hash": _hash(document),
        }
    )
    return _parse_master(
        projected,
        domain=analysis_domain,
        source_label=label,
        source_mode="time_split_evaluation",
    )


def _merge_methods(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], analysis_domain: str
) -> dict[str, dict[str, Any]]:
    baseline_entities = _entities(baseline, analysis_domain)
    candidate_entities = _entities(candidate, analysis_domain)
    base = {str(value["entity_id"]): value for value in baseline_entities}
    current = {str(value["entity_id"]): value for value in candidate_entities}

    appended = deepcopy(base)
    for entity_id, value in current.items():
        if entity_id not in appended:
            appended[entity_id] = deepcopy(value)

    dynamic = deepcopy(base)
    for entity_id, value in current.items():
        if entity_id not in dynamic:
            dynamic[entity_id] = deepcopy(value)
            continue
        for field in ("aliases", "alias_relationships", "alias_identities"):
            dynamic[entity_id][field] = deepcopy(value.get(field, {} if field != "aliases" else []))

    writeback = deepcopy(dynamic)
    for entity_id, value in current.items():
        if entity_id not in writeback:
            continue
        writeback[entity_id]["evidence_urls"] = deepcopy(value.get("evidence_urls", []))
        writeback[entity_id]["competitor_eligible"] = bool(value.get("competitor_eligible"))
        # Evidence-only writeback has no explicit applicability representation.
        writeback[entity_id]["eligibility_mode"] = (
            "always" if value.get("competitor_eligible") else "never"
        )
        writeback[entity_id]["competitor_scopes"] = []

    return {
        "traditional_rag_no_update": _document(baseline, analysis_domain, list(base.values())),
        "append_only_documents": _document(baseline, analysis_domain, list(appended.values())),
        "expert_feedback_edit": _document(candidate, analysis_domain, list(current.values())),
        "evidence_writeback_proxy": _document(baseline, analysis_domain, list(writeback.values())),
        "dynamic_entity_resolution_proxy": _document(
            baseline, analysis_domain, list(dynamic.values())
        ),
        "scoped_replay_risk_governed": _document(
            candidate, analysis_domain, list(current.values())
        ),
    }


def _llm_patch(path: Path | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    if path is None:
        return {}, None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "llm-brand-patch-v1":
        raise ValueError("llm_patch_invalid")
    rows = document.get("patches")
    if not isinstance(rows, list):
        raise ValueError("llm_patch_invalid")
    by_surface: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict) or not raw.get("surface_form"):
            raise ValueError("llm_patch_invalid")
        surface = str(raw["surface_form"])
        if surface in by_surface:
            raise ValueError("llm_patch_duplicate_surface")
        by_surface[surface] = raw
    return by_surface, {
        key: document.get(key)
        for key in (
            "provider",
            "model",
            "model_version",
            "prompt_hash",
            "output_hash",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "baseline_release_id",
            "input_limitations",
        )
    }


def _decision_from_master(
    master: EntityMaster,
    *,
    mention: str,
    scopes: tuple[str, ...],
    analysis_domain: str,
) -> dict[str, Any]:
    row = classify_entity(
        mention,
        rules=load_domain(analysis_domain),
        master=master,
        comparison_scopes=scopes,
    )
    reviewed = row.get("review_status") == "reviewed" and row.get("entity_type") != "unknown"
    return {
        "identity_id": row.get("identity_entity_id") if reviewed else None,
        "identity_type": row.get("identity_entity_type") if reviewed else "unknown",
        "roll_up_id": row.get("entity_id") if reviewed else None,
        "relation": (row.get("relationship_to_canonical") if reviewed else "unresolved"),
        "eligible": bool(row.get("competitor_eligible")) if reviewed else False,
        "confidence": 0.99 if reviewed else 0.0,
    }


def _decision_from_patch(
    patch: Mapping[str, Any] | None,
    *,
    requested_scopes: tuple[str, ...],
) -> dict[str, Any]:
    if patch is None:
        return {
            "identity_id": None,
            "identity_type": "unknown",
            "roll_up_id": None,
            "relation": None,
            "eligible": False,
            "confidence": 0.0,
        }
    asserted_scopes = {str(value) for value in patch.get("scopes", []) if isinstance(value, str)}
    eligible = patch.get("eligible") is True and (
        not asserted_scopes or bool(asserted_scopes.intersection(requested_scopes))
    )
    return {
        "identity_id": patch.get("identity_entity_id"),
        "identity_type": patch.get("identity_entity_type"),
        "roll_up_id": patch.get("roll_up_entity_id"),
        "relation": patch.get("relationship") or "unresolved",
        "eligible": eligible,
        "confidence": float(patch.get("confidence") or 0),
    }


def _evaluate(
    gold: Mapping[str, Any],
    *,
    method: str,
    master: EntityMaster | None = None,
    patches: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    totals = {
        "identity": 0,
        "identity_type": 0,
        "roll_up": 0,
        "relation": 0,
        "eligibility": 0,
        "dedupe": 0,
    }
    correct = {key: 0 for key in totals}
    wrong_merge = missed_merge = wrong_competitor = missed_competitor = 0
    cross_scene_misuse = 0
    brier: list[float] = []
    case_pass: list[bool] = []
    case_rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for raw_case in gold["cases"]:
        started = time.perf_counter()
        expected = raw_case["expected"]
        scopes = tuple(str(value) for value in raw_case.get("comparison_scopes", []))
        expected_identities = expected.get("identity_by_mention") or {}
        expected_types = expected.get("identity_types") or {}
        decisions: list[dict[str, Any]] = []
        passed = True
        rollups: set[str] = set()
        for mention in raw_case["mentions"]:
            decision = (
                _decision_from_master(
                    master,
                    mention=mention,
                    scopes=scopes,
                    analysis_domain=str(gold["analysis_domain"]),
                )
                if master is not None
                else _decision_from_patch(
                    (patches or {}).get(mention),
                    requested_scopes=scopes,
                )
            )
            decisions.append(decision)
            expected_identity = expected_identities.get(mention, expected.get("entity_id"))
            checks = {
                "identity": decision["identity_id"] == expected_identity,
                "roll_up": decision["roll_up_id"]
                == expected.get("roll_up_entity_id", expected.get("entity_id")),
                "relation": decision["relation"] == expected["relationships"][mention],
                "eligibility": decision["eligible"] == bool(expected["eligible"]),
            }
            if mention in expected_types:
                checks["identity_type"] = decision["identity_type"] == expected_types[mention]
            for dimension, matched in checks.items():
                totals[dimension] += 1
                correct[dimension] += int(matched)
                passed = passed and matched
            target = float(expected_identity is not None and checks["identity"])
            brier.append((decision["confidence"] - target) ** 2)
            rollups.add(
                str(decision["roll_up_id"])
                if decision["roll_up_id"] is not None
                else f"unresolved:{mention}"
            )
            wrong_competitor += int(decision["eligible"] and not expected["eligible"])
            missed_competitor += int(not decision["eligible"] and expected["eligible"])
            cross_scene_misuse += int(
                decision["eligible"]
                and not expected["eligible"]
                and bool(raw_case.get("comparison_scopes"))
            )
        expected_dedupe = int(expected["deduped_entity_count"])
        actual_dedupe = len(rollups)
        dedupe_match = actual_dedupe == expected_dedupe
        totals["dedupe"] += 1
        correct["dedupe"] += int(dedupe_match)
        wrong_merge += int(actual_dedupe < expected_dedupe)
        missed_merge += int(actual_dedupe > expected_dedupe)
        passed = passed and dedupe_match
        case_pass.append(passed)
        latencies.append((time.perf_counter() - started) * 1000)
        case_rows.append({"case_id": raw_case["id"], "passed": passed, "decisions": decisions})
    return {
        "method": method,
        "implementation_fidelity": (
            "project_implementation"
            if method == "scoped_replay_risk_governed"
            else "controlled_operational_analogue"
        ),
        "case_count": len(case_pass),
        "case_accuracy": sum(case_pass) / len(case_pass),
        "dimension_accuracy": {
            key: correct[key] / totals[key] if totals[key] else None for key in totals
        },
        "identity_resolution_brier_score": statistics.fmean(brier),
        "wrong_merge_count": wrong_merge,
        "missed_merge_count": missed_merge,
        "wrong_competitor_entry_count": wrong_competitor,
        "missed_competitor_count": missed_competitor,
        "cross_scene_misuse_count": cross_scene_misuse,
        "latency_ms": {
            "p50": statistics.median(latencies),
            "p95": sorted(latencies)[round((len(latencies) - 1) * 0.95)],
            "p99": sorted(latencies)[round((len(latencies) - 1) * 0.99)],
        },
        "case_pass": case_pass,
        "cases": case_rows,
    }


def _binomial_two_sided(successes: int, trials: int) -> float | None:
    if trials == 0:
        return None
    tail = float(sum(math.comb(trials, value) for value in range(successes + 1))) / float(2**trials)
    return float(min(1.0, 2 * tail))


def _statistics(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], *, seed: int
) -> dict[str, Any]:
    left = [bool(value) for value in baseline["case_pass"]]
    right = [bool(value) for value in candidate["case_pass"]]
    deltas = [
        float(candidate_pass) - float(baseline_pass)
        for baseline_pass, candidate_pass in zip(left, right, strict=True)
    ]
    generator = random.Random(seed)
    bootstrapped = [
        statistics.fmean(generator.choice(deltas) for _ in deltas) for _ in range(10_000)
    ]
    bootstrapped.sort()
    discordant_left = sum(
        baseline_pass and not candidate_pass
        for baseline_pass, candidate_pass in zip(left, right, strict=True)
    )
    discordant_right = sum(
        candidate_pass and not baseline_pass
        for baseline_pass, candidate_pass in zip(left, right, strict=True)
    )
    discordant = discordant_left + discordant_right
    return {
        "project_minus_method_case_accuracy_delta": statistics.fmean(deltas),
        "project_minus_method_paired_bootstrap_95_ci": [
            bootstrapped[249],
            bootstrapped[9749],
        ],
        "bootstrap_resamples": 10_000,
        "seed": seed,
        "mcnemar_exact_two_sided_p": _binomial_two_sided(
            min(discordant_left, discordant_right), discordant
        ),
        "discordant_method_only_correct": discordant_left,
        "discordant_project_only_correct": discordant_right,
    }


def _no_applicability(document: Mapping[str, Any], analysis_domain: str) -> dict[str, Any]:
    rows = _entities(document, analysis_domain)
    for row in rows:
        if row.get("competitor_eligible"):
            row["eligibility_mode"] = "always"
            row["competitor_scopes"] = []
    return _document(document, analysis_domain, rows)


def _unsafe_pending_document(
    candidate: Mapping[str, Any], analysis_domain: str, snapshot: Path | None
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    projection = project_brand_domain(snapshot, analysis_domain=analysis_domain)
    rows = [deepcopy(value) for value in projection["entities"]]
    for row in rows:
        row["review_status"] = "reviewed"
        row["knowledge_status"] = "published"
    return _document(candidate, analysis_domain, rows)


def _partitioned_without_cross_project_aggregation(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], analysis_domain: str
) -> list[dict[str, Any]]:
    base = {str(row["entity_id"]): row for row in _entities(baseline, analysis_domain)}
    current = {str(row["entity_id"]): row for row in _entities(candidate, analysis_domain)}
    outputs = []
    for partition in range(5):
        rows = deepcopy(base)
        for entity_id, candidate_row in current.items():
            baseline_row = rows.get(entity_id)
            if baseline_row is None:
                if int(hashlib.sha256(entity_id.encode()).hexdigest(), 16) % 5 == partition:
                    rows[entity_id] = deepcopy(candidate_row)
                continue
            aliases = set(baseline_row.get("aliases", []))
            raw_identities = candidate_row.get("alias_identities") or {}
            identity_groups: dict[str, list[str]] = {}
            if isinstance(raw_identities, dict):
                for alias, identity in raw_identities.items():
                    if not isinstance(identity, dict):
                        continue
                    identity_id = str(identity.get("entity_id") or "")
                    if identity_id:
                        identity_groups.setdefault(identity_id, []).append(str(alias))
            selected_identity_aliases: set[str] = set()
            for identity_id, identity_aliases in identity_groups.items():
                if int(hashlib.sha256(identity_id.encode()).hexdigest(), 16) % 5 != partition:
                    continue
                selected_identity_aliases.update(identity_aliases)
                for alias in identity_aliases:
                    aliases.add(alias)
                    baseline_row.setdefault("alias_relationships", {})[alias] = candidate_row.get(
                        "alias_relationships", {}
                    ).get(alias)
                    baseline_row.setdefault("alias_identities", {})[alias] = deepcopy(
                        raw_identities[alias]
                    )
            new_plain_aliases = {
                str(alias)
                for alias in candidate_row.get("aliases", [])
                if alias not in set(baseline_row.get("aliases", []))
                and alias not in selected_identity_aliases
            }
            if (
                new_plain_aliases
                and int(hashlib.sha256(f"aliases:{entity_id}".encode()).hexdigest(), 16) % 5
                == partition
            ):
                for alias in new_plain_aliases:
                    aliases.add(alias)
                    baseline_row.setdefault("alias_relationships", {})[alias] = candidate_row.get(
                        "alias_relationships", {}
                    ).get(alias)
            baseline_row["aliases"] = sorted(aliases)
        outputs.append(_document(baseline, analysis_domain, list(rows.values())))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-release-dir", type=Path, required=True)
    parser.add_argument("--baseline-release-id", required=True)
    parser.add_argument("--candidate-release-id", required=True)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--llm-patch", type=Path)
    parser.add_argument("--siliconindex-snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20_260_827)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    store = KnowledgeReleaseStore(args.knowledge_release_dir)
    baseline_documents, baseline_manifest = store.load_documents(args.baseline_release_id)
    candidate_documents, candidate_manifest = store.load_documents(args.candidate_release_id)
    baseline = baseline_documents["brand/entity-resolution"]
    candidate = candidate_documents["brand/entity-resolution"]
    analysis_domain = str(gold["analysis_domain"])
    method_documents = _merge_methods(baseline, candidate, analysis_domain)
    patches, llm_metadata = _llm_patch(args.llm_patch)
    if llm_metadata is not None and llm_metadata.get("baseline_release_id") != (
        args.baseline_release_id
    ):
        raise ValueError("llm_patch_temporal_baseline_mismatch")

    results = [
        _evaluate(
            gold,
            method=name,
            master=_master(document, analysis_domain, name),
        )
        for name, document in method_documents.items()
        if name != "scoped_replay_risk_governed"
    ]
    results.insert(
        2,
        _evaluate(
            gold,
            method="llm_direct_knowledge_patch",
            patches=patches,
        ),
    )
    project_result = _evaluate(
        gold,
        method="scoped_replay_risk_governed",
        master=_master(candidate, analysis_domain, "scoped_replay_risk_governed"),
    )
    results.append(project_result)
    baseline_result = results[0]

    no_applicability = _evaluate(
        gold,
        method="ablation_no_applicability",
        master=_master(
            _no_applicability(candidate, analysis_domain),
            analysis_domain,
            "ablation_no_applicability",
        ),
    )
    unsafe_document = _unsafe_pending_document(
        candidate, analysis_domain, args.siliconindex_snapshot
    )
    unsafe_result = (
        _evaluate(
            gold,
            method="ablation_no_opposing_evidence_or_human_gate",
            master=_master(
                unsafe_document,
                analysis_domain,
                "ablation_no_opposing_evidence_or_human_gate",
            ),
        )
        if unsafe_document is not None
        else {"method": "ablation_no_opposing_evidence_or_human_gate", "status": "not_run"}
    )
    partitions = [
        _evaluate(
            gold,
            method=f"no_cross_project_aggregation_partition_{index}",
            master=_master(document, analysis_domain, f"partition-{index}"),
        )
        for index, document in enumerate(
            _partitioned_without_cross_project_aggregation(baseline, candidate, analysis_domain)
        )
    ]
    no_cross_project = {
        "method": "ablation_no_cross_project_aggregation",
        "partition_count": len(partitions),
        "mean_case_accuracy": statistics.fmean(
            float(result["case_accuracy"]) for result in partitions
        ),
        "partition_case_accuracy": [result["case_accuracy"] for result in partitions],
    }
    comparisons = {
        str(result["method"]): _statistics(
            result,
            project_result,
            seed=args.seed,
        )
        for result in results
        if result["method"] != project_result["method"]
    }
    baseline_pass = baseline_result["case_pass"]
    project_pass = project_result["case_pass"]
    discoverable = sum(
        not left and right for left, right in zip(baseline_pass, project_pass, strict=True)
    )
    for result in results:
        passed = result["case_pass"]
        corrected = sum(
            not left and current for left, current in zip(baseline_pass, passed, strict=True)
        )
        changed = sum(left != current for left, current in zip(baseline_pass, passed, strict=True))
        result["candidate_discovery_rate"] = corrected / discoverable if discoverable else None
        result["correct_publication_rate"] = corrected / changed if changed else None

    report = {
        "schema_version": "knowledge-update-method-comparison-v1",
        "status": "reproducible_pilot_not_paper_grade",
        "generated_at_epoch": int(time.time()),
        "time_split": {
            "knowledge_cutoff": gold["time_cutoff"],
            "baseline_release_id": args.baseline_release_id,
            "baseline_content_hash": baseline_manifest["content_hash"],
            "candidate_release_id": args.candidate_release_id,
            "candidate_content_hash": candidate_manifest["content_hash"],
            "gold_hash": _hash(gold),
            "future_knowledge_in_baseline": False,
            "llm_patch_uses_pre_cutoff_baseline": llm_metadata is None
            or llm_metadata.get("baseline_release_id") == args.baseline_release_id,
        },
        "method_limitations": (
            "RAG, WriteBack-RAG and DynamicER rows are controlled operational analogues on "
            "one common read model; they are not reproductions of author training pipelines."
        ),
        "evaluation_validity": {
            "temporal_cutoff_declared": True,
            "independent_held_out_set": False,
            "gold_fixture_also_used_by_candidate_release_gate": True,
            "supports_research_novelty_or_superiority_claim": False,
            "interpretation": (
                "This is an engineering acceptance pilot. It detects regressions and "
                "illustrates method behavior, but cannot establish a research claim."
            ),
        },
        "llm_direct_run": llm_metadata,
        "results": results,
        "paired_statistics_against_project": comparisons,
        "ablations": [
            no_applicability,
            {
                "method": "ablation_no_historical_replay",
                "proxy": "unsafe pending publication",
                "result": unsafe_result,
            },
            unsafe_result,
            no_cross_project,
            {
                "method": "ablation_single_model",
                "result_method": "llm_direct_knowledge_patch",
                "case_accuracy": results[2]["case_accuracy"],
            },
            {
                "method": "ablation_no_human_review",
                "result_method": "llm_direct_knowledge_patch",
                "case_accuracy": results[2]["case_accuracy"],
            },
        ],
        "operational_metrics_not_measured": [
            "human_review_minutes_per_method",
            "provider_price_when_gateway_omits_cost",
            "discovery_to_effective_wall_clock_for_counterfactual_methods",
        ],
    }
    report["report_hash"] = _hash(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "report_hash": report["report_hash"]}))


if __name__ == "__main__":
    main()
