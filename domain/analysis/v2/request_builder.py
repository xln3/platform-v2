"""Build reference-only workflow requests for live and historical answers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from domain.analysis.v2._canonical import canonical_hash
from domain.analysis.v2.decision_task_loader import (
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)

_QUERY_SUBJECT_TYPES = frozenset({"query", "query_dimension"})


def instantiate_decision_task_request(
    template: Mapping[str, Any], subject_ref: Mapping[str, Any]
) -> dict[str, Any]:
    """Instantiate a frozen task template for a concrete atomic subject.

    Claim and citation subjects are discovered only after the parent claim
    extraction decision has completed.  Keeping this helper pure lets a
    Temporal workflow expand that fan-out deterministically without loading a
    registry, source text, or mutable configuration into workflow history.
    """

    task_ref = str(template.get("task_ref") or "")
    material_hashes = template.get("input_material_hashes")
    if not task_ref or not isinstance(material_hashes, Mapping):
        raise ValueError("semantic_v2_decision_template_invalid")
    concrete_subject = {str(key): value for key, value in subject_ref.items()}
    if not concrete_subject:
        raise ValueError("semantic_v2_decision_subject_missing")
    input_hash = canonical_hash(
        {
            "input_material_hashes": dict(material_hashes),
            "subject_ref": concrete_subject,
            "task_ref": task_ref,
        }
    )
    # Source text never enters the request, but its frozen hashes and the
    # answer/query references must survive template instantiation.  The
    # decision activity uses these values to hydrate and verify the immutable
    # tenant row without copying raw text into Temporal workflow history.
    request = dict(template)
    request["input_material_hashes"] = {
        str(key): str(value) for key, value in material_hashes.items()
    }
    request.update(
        {
            "subject_ref": concrete_subject,
            "input_hash": input_hash,
            "idempotency_key": canonical_hash(
                {
                    "context_hash": str(template["context_hash"]),
                    "input_hash": input_hash,
                    "subject_ref": concrete_subject,
                    "task_ref": task_ref,
                }
            ),
        }
    )
    return request


def build_answer_semantic_workflow_request(
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    answer_pub_id: str,
    analysis_run_pub_id: str,
    query_key: str,
    query_text_hash: str,
    answer_text_hash: str,
    managed_entities: Sequence[Mapping[str, str]],
    classification_source: str,
    created_at: datetime,
    query_pub_id: str | None = None,
    citation_pub_ids: Sequence[str] = (),
    rubric_dimensions: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Return the complete V2 orchestration envelope without source text.

    ``managed_entities`` are a closed candidate dictionary, not accepted
    labels. All semantic outcomes still require a versioned DecisionTask.
    """

    entities: list[dict[str, Any]] = sorted(
        (
            {
                "candidate_id": str(item["candidate_id"]),
                "candidate_type": str(item["candidate_type"]),
                "labels": [str(item["label"])],
                "metadata": {},
            }
            for item in managed_entities
        ),
        key=lambda item: (item["candidate_type"], item["candidate_id"]),
    )
    if not entities:
        raise ValueError("semantic_v2_entity_dictionary_missing")
    if any(not item["candidate_id"] or not item["labels"][0] for item in entities):
        raise ValueError("semantic_v2_entity_dictionary_invalid")
    citations = sorted(set(map(str, citation_pub_ids)))
    if any(not item for item in citations):
        raise ValueError("semantic_v2_citation_reference_invalid")
    dimensions = sorted(
        (
            {
                "dimension_id": str(item["dimension_id"]),
                "focal_entity_id": str(item["focal_entity_id"]),
                "rubric_hash": str(item["rubric_hash"]),
            }
            for item in rubric_dimensions
        ),
        key=lambda item: (item["focal_entity_id"], item["dimension_id"]),
    )
    if any(
        not item["dimension_id"] or not item["focal_entity_id"] or len(item["rubric_hash"]) != 64
        for item in dimensions
    ):
        raise ValueError("semantic_v2_dimension_rubric_invalid")
    if classification_source not in {"live", "historical_backfill", "manual_override"}:
        raise ValueError("semantic_v2_classification_source_invalid")
    for name, value in (
        ("query_text_hash", query_text_hash),
        ("answer_text_hash", answer_text_hash),
    ):
        if len(value) != 64:
            raise ValueError(f"semantic_v2_{name}_invalid")

    tasks = load_builtin_task_definitions()
    policies = load_builtin_judge_policies(tasks=tasks)
    policy_by_task = {
        task_ref: next(policy for policy in policies if task_ref in policy.compatible_task_refs)
        for task_ref in tasks.topological_refs
    }
    entity_dictionary_hash = canonical_hash(entities)
    decision_task_bundle_hash = canonical_hash(
        [
            {"task_ref": item.task_ref, "definition_hash": item.definition_hash}
            for item in tasks.definitions
        ]
    )
    context_hash = canonical_hash(
        {
            "dimension_rubric_set_hash": canonical_hash(dimensions),
            "entity_dictionary_hash": entity_dictionary_hash,
            "project_pub_id": project_pub_id,
            "query_key": query_key,
        }
    )

    def decision_template(task_ref: str) -> dict[str, Any]:
        task = tasks.get(task_ref)
        policy = policy_by_task[task_ref]
        query_scoped = task.subject_type.value in _QUERY_SUBJECT_TYPES
        return {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "task_ref": task_ref,
            "input_snapshot_ref": (
                f"capture://query/{query_key}"
                if query_scoped
                else f"capture://answer/{answer_pub_id}"
            ),
            "source_answer_pub_id": answer_pub_id,
            "source_query_pub_id": query_pub_id,
            "input_material_hashes": (
                {"query_text_hash": query_text_hash}
                if query_scoped
                else {
                    "answer_text_hash": answer_text_hash,
                    "query_text_hash": query_text_hash,
                }
            ),
            "context_hash": context_hash,
            "judge_policy_hash": policy.policy_hash,
            "judge_policy_ref": policy.policy_ref,
            "dependency_task_refs": list(task.dependency_task_refs),
            "official_use": False,
            # Historical replay is already governed page by page. Keep one
            # bounded model attempt per atomic task so a timeout is disclosed
            # without an exponential continue-as-new chain blocking the page.
            "max_auto_rejudge_generations": 0,
        }

    templates = {task_ref: decision_template(task_ref) for task_ref in tasks.topological_refs}

    def decision_request(task_ref: str, subject_ref: dict[str, Any]) -> dict[str, Any]:
        return instantiate_decision_task_request(
            templates[task_ref],
            subject_ref,
        )

    query_subject = {"query_pub_id": query_key}
    query_tasks = [
        decision_request("query-intent@2.0.0", query_subject),
        decision_request("query-brand-entity-resolution@2.0.0", query_subject),
    ]
    for dimension in dimensions:
        query_tasks.append(
            decision_request(
                "requested-dimension-applicability@2.0.0",
                {
                    "query_pub_id": query_key,
                    "focal_entity_id": dimension["focal_entity_id"],
                    "dimension_id": dimension["dimension_id"],
                },
            )
            | {"rubric_hash": dimension["rubric_hash"]}
        )
    answer_tasks: list[dict[str, Any]] = []
    for entity in entities:
        entity_id = str(entity["candidate_id"])
        surface_key = canonical_hash({"entity_id": entity_id, "surface": str(entity["labels"][0])})
        answer_tasks.extend(
            (
                decision_request(
                    "answer-entity-resolution@2.0.0",
                    {
                        "answer_pub_id": answer_pub_id,
                        "query_pub_id": query_key,
                        "surface_key": surface_key,
                    },
                ),
                decision_request(
                    "substantive-entity-mention@2.0.0",
                    {"answer_pub_id": answer_pub_id, "entity_id": entity_id},
                ),
                decision_request(
                    "recommendation-relation@2.0.0",
                    {
                        "answer_pub_id": answer_pub_id,
                        "query_pub_id": query_key,
                        "entity_id": entity_id,
                    },
                ),
                decision_request(
                    "stance-and-pairwise@2.0.0",
                    {
                        "answer_pub_id": answer_pub_id,
                        "subject_entity_id": entity_id,
                        "object_entity_id": None,
                    },
                ),
            )
        )
    focal_ids = [
        str(item["candidate_id"]) for item in entities if item["candidate_type"] == "brand"
    ]
    competitor_ids = [
        str(item["candidate_id"]) for item in entities if item["candidate_type"] == "competitor"
    ]
    for focal_entity_id in focal_ids:
        for competitor_entity_id in competitor_ids:
            answer_tasks.append(
                decision_request(
                    "stance-and-pairwise@2.0.0",
                    {
                        "answer_pub_id": answer_pub_id,
                        "subject_entity_id": focal_entity_id,
                        "object_entity_id": competitor_entity_id,
                    },
                )
            )
    for dimension in dimensions:
        answer_tasks.append(
            decision_request(
                "answer-dimension-coverage@2.0.0",
                {
                    "answer_pub_id": answer_pub_id,
                    "query_pub_id": query_key,
                    "focal_entity_id": dimension["focal_entity_id"],
                    "dimension_id": dimension["dimension_id"],
                },
            )
            | {"rubric_hash": dimension["rubric_hash"]}
        )
    answer_tasks.extend(
        (
            decision_request(
                "rank-semantics@2.0.0",
                {"answer_pub_id": answer_pub_id, "query_pub_id": query_key},
            ),
            decision_request("claim-extraction@2.0.0", {"answer_pub_id": answer_pub_id}),
            decision_request(
                "risk-adjudication@2.0.0",
                {
                    "answer_pub_id": answer_pub_id,
                    "risk_subject_key": canonical_hash(
                        {"answer_pub_id": answer_pub_id, "risk_scope": "all"}
                    ),
                },
            ),
        )
    )

    manifest_input_hash = canonical_hash(
        {
            "answer_text_hash": answer_text_hash,
            "citation_ref_set_hash": canonical_hash(citations),
            "context_hash": context_hash,
            "decision_task_bundle_hash": decision_task_bundle_hash,
            "dimension_rubric_set_hash": canonical_hash(dimensions),
        }
    )
    semantic_manifest_pub_id = f"asm_{manifest_input_hash[:26]}"
    extractor_bundle = {
        "event_schema_version": "answer-semantic-events-v2",
        "extractor_version": "semantic-event-derivation-v2",
        "scorer_version": "semantic-event-scorer-v2",
    }
    extractor_bundle_hash = canonical_hash(extractor_bundle)
    required_capabilities = {
        "substantive_entity_mention",
        "recommendation_relation",
        "rank_semantics",
        "stance_and_pairwise",
        "claim_evidence_verdict",
        "risk_adjudication",
    }
    if dimensions:
        required_capabilities.update(
            {"requested_dimension_applicability", "answer_dimension_coverage"}
        )
    if citations:
        required_capabilities.add("citation_claim_support")
    return {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project_pub_id,
        "answer_pub_id": answer_pub_id,
        "semantic_manifest_pub_id": semantic_manifest_pub_id,
        "extractor_version": extractor_bundle["extractor_version"],
        "scorer_version": extractor_bundle["scorer_version"],
        "policy_versions_by_hash": {policy.policy_hash: policy.policy_ref for policy in policies},
        "dynamic_task_templates": {
            task_ref: templates[task_ref]
            for task_ref in (
                "claim-verifiability@2.0.0",
                "claim-evidence-verdict@2.0.0",
                "citation-claim-support@2.0.0",
            )
        },
        "dynamic_inputs": {
            "citation_pub_ids": citations,
            "maximum_claims": 100,
            "maximum_citations": 50,
        },
        "required_capabilities": sorted(required_capabilities),
        "query_context_request": {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "candidate_input": {
                "source_ref": f"project://{project_pub_id}/managed-entities",
                "source_hash": entity_dictionary_hash,
                "candidates": entities,
            },
            "decision_tasks": query_tasks,
            "query_key": query_key,
            "query_pub_id": query_pub_id,
            "query_text_hash": query_text_hash,
            "classifier_version": "query-context-v2",
            "decision_task_bundle_hash": decision_task_bundle_hash,
            "entity_dictionary_hash": entity_dictionary_hash,
            "classification_source": classification_source,
            "derivation_method": "hybrid",
            "focal_entity_ids": [str(item["candidate_id"]) for item in entities],
            "created_at": created_at.isoformat(),
        },
        "decision_tasks": answer_tasks,
        "manifest": {
            "pub_id": semantic_manifest_pub_id,
            "answer_pub_id": answer_pub_id,
            "analysis_run_pub_id": analysis_run_pub_id,
            "query_context_fact_pub_id": f"qcf_pending_{query_text_hash[:16]}",
            "answer_text_hash": answer_text_hash,
            "input_hash": manifest_input_hash,
            "event_schema_version": "answer-semantic-events-v2",
            "extractor_bundle": extractor_bundle,
            "decision_task_bundle": {
                "task_refs": list(tasks.topological_refs),
                "bundle_hash": decision_task_bundle_hash,
            },
            "extractor_bundle_hash": extractor_bundle_hash,
            "decision_task_bundle_hash": decision_task_bundle_hash,
            "entity_dictionary_hash": entity_dictionary_hash,
            "failure_code": None,
            "failure_detail": None,
            "supersedes_pub_id": None,
            "created_at": created_at.isoformat(),
        },
    }


__all__ = [
    "build_answer_semantic_workflow_request",
    "instantiate_decision_task_request",
]
