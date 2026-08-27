#!/usr/bin/env python3
"""Compare active and candidate brand knowledge on a time-frozen gold set."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.knowledge_evolution.contracts import ReasoningPolicy, RuntimeRequest  # noqa: E402
from domain.knowledge_evolution.domains.brand import BrandEntityResolutionPack  # noqa: E402

DEFAULT_GOLD = PROJECT_ROOT / "domain" / "knowledge_evolution" / "domains" / "brand_eval_v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _evaluate(pack: BrandEntityResolutionPack, gold: dict[str, Any]) -> dict[str, Any]:
    failed: dict[str, list[str]] = {}
    release = None
    for case in gold["cases"]:
        mentions = list(case["mentions"])
        request = RuntimeRequest(
            request_id=f"release-replay-{case['id']}",
            tenant="release-replay",
            namespace="shared",
            domain="brand/entity-resolution",
            task="historical_release_replay",
            items=tuple(
                {
                    "id": f"{case['id']}-{index}",
                    "value": mention,
                    "contexts": list(case.get("contexts") or []),
                    "source_type": "time_frozen_gold_set",
                }
                for index, mention in enumerate(mentions)
            ),
            context={
                "analysis_domain": gold["analysis_domain"],
                "comparison_scopes": list(case.get("comparison_scopes") or []),
            },
            policy=ReasoningPolicy.DETERMINISTIC_ONLY,
            policy_id="brand-release-replay",
            policy_version=str(gold["schema_version"]),
            data_classification="public",
        )
        if release is None:
            release = pack.release_ref(request)
        decisions = pack.deterministic_resolve(request)
        expected = case["expected"]
        expected_identities = expected.get("identity_by_mention") or {}
        expected_types = expected.get("identity_types") or {}
        expected_roll_up = expected.get("roll_up_entity_id", expected.get("entity_id"))
        roll_ups: set[str] = set()
        errors: list[str] = []
        for mention, decision in zip(mentions, decisions, strict=True):
            identity = decision.value.get("identity") or {}
            relation = decision.value.get("relation") or {}
            roll_up = decision.value.get("roll_up") or {}
            comparison = decision.value.get("comparison") or {}
            unresolved = decision.knowledge_status.value == "unresolved"
            actual_identity = None if unresolved else identity.get("entity_id")
            actual_roll_up = None if unresolved else roll_up.get("entity_id")
            if actual_identity != expected_identities.get(mention, expected.get("entity_id")):
                errors.append(f"identity:{mention}")
            expected_type = expected_types.get(mention)
            if expected_type is not None and identity.get("entity_type") != expected_type:
                errors.append(f"identity_type:{mention}")
            if actual_roll_up != expected_roll_up:
                errors.append(f"roll_up:{mention}")
            if relation.get("type") != expected["relationships"][mention]:
                errors.append(f"relation:{mention}")
            if bool(comparison.get("eligible")) != bool(expected["eligible"]):
                errors.append(f"eligibility:{mention}")
            roll_ups.add(str(actual_roll_up) if actual_roll_up else f"unresolved:{mention}")
        if len(roll_ups) != int(expected["deduped_entity_count"]):
            errors.append("dedupe")
        if errors:
            failed[str(case["id"])] = sorted(set(errors))
    assert release is not None
    return {
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "failed_cases": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-knowledge-release-dir", type=Path, required=True)
    parser.add_argument("--candidate-snapshot-dir", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--allowed-new-error-count", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.allowed_new_error_count < 0:
        raise SystemExit("allowed_new_error_count_must_be_non_negative")
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    baseline = _evaluate(
        BrandEntityResolutionPack(
            knowledge_release_dir=str(args.baseline_knowledge_release_dir),
        ),
        gold,
    )
    candidate = _evaluate(
        BrandEntityResolutionPack(snapshot_dir=str(args.candidate_snapshot_dir)),
        gold,
    )
    baseline_errors = set(baseline["failed_cases"])
    candidate_errors = set(candidate["failed_cases"])
    corrected = sorted(baseline_errors - candidate_errors)
    new_errors = sorted(candidate_errors - baseline_errors)
    historical_replay = {
        "schema_version": "historical-replay-v1",
        "evaluation_set_hash": "sha256:" + hashlib.sha256(_canonical(gold)).hexdigest(),
        "time_cutoff": gold["time_cutoff"],
        "evaluated_request_count": len(gold["cases"]),
        "baseline_error_count": len(baseline_errors),
        "candidate_error_count": len(candidate_errors),
        "corrected_error_count": len(corrected),
        "new_error_count": len(new_errors),
        "allowed_new_error_count": args.allowed_new_error_count,
        "passed": len(new_errors) <= args.allowed_new_error_count,
        "baseline_release_id": baseline["release_id"],
        "baseline_content_hash": baseline["content_hash"],
        "candidate_release_id": candidate["release_id"],
        "candidate_content_hash": candidate["content_hash"],
        "corrected_case_ids": corrected,
        "new_error_case_ids": new_errors,
    }
    report = {
        "schema_version": "brand-release-replay-report-v1",
        "historical_replay": historical_replay,
        "baseline_failed_cases": baseline["failed_cases"],
        "candidate_failed_cases": candidate["failed_cases"],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not historical_replay["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
