#!/usr/bin/env python3
"""Generate one unreviewed LLM knowledge-patch baseline for the pilot evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
API_ROOT = PROJECT_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.knowledge.service import gateway as configured_gateway  # noqa: E402

from domain.brandrank.entities import classify_entity, load_entity_master  # noqa: E402
from domain.brandrank.rules import load_domain  # noqa: E402
from domain.knowledge_evolution.contracts import ModelPrompt  # noqa: E402

DEFAULT_GOLD = PROJECT_ROOT / "domain/knowledge_evolution/domains/brand_eval_v1.json"
RELATIONS = [
    "self",
    "same_legal_entity",
    "official_abbreviation",
    "english_name",
    "historical_name",
    "trade_name",
    "product_of",
    "business_unit_of",
    "subsidiary_of",
    "brand_family_member",
]
OBJECT_TYPES = [
    "legal_entity",
    "company",
    "group",
    "brand",
    "brand_family",
    "sub_brand",
    "business_unit",
    "product",
    "tool",
    "institution",
    "unknown",
]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["patches"],
        "properties": {
            "patches": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "surface_form",
                        "identity_entity_id",
                        "identity_entity_type",
                        "roll_up_entity_id",
                        "relationship",
                        "eligible",
                        "scopes",
                        "confidence",
                        "evidence_refs",
                        "missing_evidence",
                    ],
                    "properties": {
                        "surface_form": {"type": "string"},
                        "identity_entity_id": {"type": ["string", "null"]},
                        "identity_entity_type": {"type": "string", "enum": OBJECT_TYPES},
                        "roll_up_entity_id": {"type": ["string", "null"]},
                        "relationship": {
                            "type": ["string", "null"],
                            "enum": [*RELATIONS, None],
                        },
                        "eligible": {"type": "boolean"},
                        "scopes": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-release-dir", type=Path, required=True)
    parser.add_argument("--baseline-release-id", required=True)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    analysis_domain = str(gold["analysis_domain"])
    master = load_entity_master(
        analysis_domain,
        knowledge_release_dir=str(args.knowledge_release_dir),
        release_id=args.baseline_release_id,
    )
    rules = load_domain(analysis_domain)
    observation_index: dict[str, dict[str, Any]] = {}
    surfaces: list[str] = []
    allowed_evidence: set[str] = set()
    allowed_scopes: set[str] = set()
    for case in gold["cases"]:
        scopes = tuple(str(value) for value in case.get("comparison_scopes", []))
        allowed_scopes.update(scopes)
        for mention in case["mentions"]:
            row = classify_entity(
                mention,
                rules=rules,
                master=master,
                comparison_scopes=scopes,
            )
            evidence = sorted(str(value) for value in row.get("evidence_urls", []) if value)
            allowed_evidence.update(evidence)
            if mention not in observation_index:
                surfaces.append(mention)
                observation_index[mention] = {
                    "surface_form": mention,
                    "safe_contexts": [],
                    "requested_scopes": [],
                    "evidence_refs": [],
                }
            observation = observation_index[mention]
            observation["safe_contexts"] = list(
                dict.fromkeys(
                    [
                        *observation["safe_contexts"],
                        *(str(value) for value in case.get("contexts", [])),
                    ]
                )
            )
            observation["requested_scopes"] = sorted({*observation["requested_scopes"], *scopes})
            observation["evidence_refs"] = sorted({*observation["evidence_refs"], *evidence})
    observations = [observation_index[surface] for surface in surfaces]
    for entity in master.entities:
        allowed_evidence.update(entity.evidence_urls)
    for identity in master.identity_index.values():
        allowed_evidence.update(identity.evidence_urls)
    prompt = ModelPrompt(
        prompt_id="llm-direct-brand-knowledge-patch-baseline",
        prompt_version="1",
        system_message=(
            "You generate a proposed brand knowledge patch from post-cutoff observations. "
            "No expert review or historical replay is available. Use only supplied IDs and "
            "evidence references; abstain with null IDs when evidence is insufficient. "
            "Confidence is the probability that a non-null identity mapping is correct; "
            "set it to zero when abstaining."
        ),
        user_message=json.dumps(
            {
                "task": "propose_unreviewed_cross_request_knowledge_patch",
                "analysis_domain": analysis_domain,
                "rules": [
                    "Identity, relationship, roll-up and competitor eligibility are separate.",
                    "Do not infer legal identity from spelling similarity.",
                    "Use null rather than inventing an ID.",
                    "Scope eligibility to the supplied scenario where necessary.",
                    "An abstention has null identity, roll-up and relationship, unknown type, "
                    "false eligibility and zero confidence.",
                ],
                "known_roll_up_entities": [
                    {
                        "entity_id": entity.entity_id,
                        "canonical_name": entity.canonical_name,
                        "entity_type": entity.entity_type,
                        "aliases": list(entity.aliases),
                        "evidence_refs": list(entity.evidence_urls),
                    }
                    for entity in sorted(master.entities, key=lambda value: value.entity_id)
                ],
                "known_identity_objects": [
                    {
                        "entity_id": identity.entity_id,
                        "canonical_name": identity.canonical_name,
                        "entity_type": identity.entity_type,
                        "evidence_refs": list(identity.evidence_urls),
                    }
                    for identity in sorted(
                        {
                            value.entity_id: value for value in master.identity_index.values()
                        }.values(),
                        key=lambda value: value.entity_id,
                    )
                ],
                "allowed_evidence_refs": sorted(allowed_evidence),
                "allowed_scopes": sorted(allowed_scopes),
                "observations": observations,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        output_schema=_schema(len(surfaces)),
    )
    settings = get_settings()
    gateway = configured_gateway(settings)
    if gateway is None:
        raise SystemExit("configured_model_gateway_required")
    result = gateway.infer(prompt)
    schema_error = next(Draft7Validator(prompt.output_schema).iter_errors(result.payload), None)
    if schema_error is not None:
        raise SystemExit("llm_patch_schema_invalid")
    rows = result.payload.get("patches")
    if not isinstance(rows, list) or len(rows) != len(surfaces):
        raise SystemExit("llm_patch_invalid_count")
    known_ids = {entity.entity_id for entity in master.entities} | {
        identity.entity_id for identity in master.identity_index.values()
    }
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "surface_form",
            "identity_entity_id",
            "identity_entity_type",
            "roll_up_entity_id",
            "relationship",
            "eligible",
            "scopes",
            "confidence",
            "evidence_refs",
            "missing_evidence",
        }:
            raise SystemExit("llm_patch_invalid_shape")
        surface = str(row["surface_form"])
        if surface not in surfaces or surface in seen:
            raise SystemExit("llm_patch_surface_invalid")
        seen.add(surface)
        for field in ("identity_entity_id", "roll_up_entity_id"):
            value = row[field]
            if value is not None and value not in known_ids:
                raise SystemExit("llm_patch_invented_id")
        if any(value not in allowed_evidence for value in row["evidence_refs"]):
            raise SystemExit("llm_patch_evidence_ref_invalid")
        if any(value not in allowed_scopes for value in row["scopes"]):
            raise SystemExit("llm_patch_scope_invalid")
        unresolved = row["identity_entity_id"] is None
        if (
            unresolved != (row["roll_up_entity_id"] is None)
            or unresolved != (row["relationship"] is None)
            or unresolved != (row["identity_entity_type"] == "unknown")
            or (unresolved and (row["eligible"] or row["confidence"] != 0))
        ):
            raise SystemExit("llm_patch_contradiction")
    if seen != set(surfaces):
        raise SystemExit("llm_patch_surface_incomplete")

    document = {
        "schema_version": "llm-brand-patch-v1",
        "knowledge_status": "model_inferred",
        "review_status": "unreviewed",
        "provider": result.provider,
        "model": result.model,
        "model_version": result.model_version,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.prompt_version,
        "prompt_hash": _hash(
            {
                "system": prompt.system_message,
                "user": prompt.user_message,
                "schema": prompt.output_schema,
            }
        ),
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "baseline_release_id": args.baseline_release_id,
        "structured_gold_labels_exposed_to_model": False,
        "input_limitations": [
            "Safe contexts are the evaluation fixture descriptions.",
            "Allowed stable IDs, names, aliases and evidence-reference URLs came only "
            "from the pre-cutoff baseline release; expected structured labels were withheld.",
            "Observation contexts necessarily contain evidence-bearing task statements; "
            "they are the post-cutoff input to be learned, not hidden labels.",
            "The model did not retrieve or read the referenced source pages in this run.",
        ],
        "patches": sorted(rows, key=lambda value: surfaces.index(value["surface_form"])),
    }
    document["output_hash"] = _hash(document["patches"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "model": result.model,
                "output_hash": document["output_hash"],
                "patch_count": len(rows),
            }
        )
    )


if __name__ == "__main__":
    main()
