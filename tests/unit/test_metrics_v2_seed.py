from __future__ import annotations

from datetime import UTC, datetime

from domain.analysis.v2 import (
    build_answer_semantic_workflow_request,
    instantiate_decision_task_request,
)
from tools.seed_metrics_v2_definitions import build_seed_bundle


def test_seed_bundle_is_complete_unique_and_never_official() -> None:
    artifacts = build_seed_bundle()

    assert len(artifacts) == 100
    assert {item.kind for item in artifacts} == {
        "decision_task",
        "judge_policy",
        "metric_definition",
    }
    assert len({(item.kind, item.name, item.version) for item in artifacts}) == 100
    assert len({item.content_hash for item in artifacts}) == 100
    assert all(item.document["status"] == "experimental" for item in artifacts)
    assert all(item.document.get("published_at") is None for item in artifacts)


def test_live_and_backfill_request_builder_is_reference_only() -> None:
    request = build_answer_semantic_workflow_request(
        tenant_pub_id="ten_fixture",
        project_pub_id="prj_fixture",
        answer_pub_id="ans_fixture",
        analysis_run_pub_id="arun_fixture",
        query_key="q" * 64,
        query_pub_id="qry_fixture",
        query_text_hash="a" * 64,
        answer_text_hash="b" * 64,
        managed_entities=(
            {
                "candidate_id": "brd_fixture",
                "candidate_type": "brand",
                "label": "candidate-label",
            },
        ),
        classification_source="historical_backfill",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    rendered = repr(request)
    assert "raw query sentinel" not in rendered
    assert "raw answer sentinel" not in rendered
    assert request["manifest"]["answer_text_hash"] == "b" * 64
    assert request["query_context_request"]["query_pub_id"] == "qry_fixture"
    assert len(request["query_context_request"]["decision_tasks"]) == 2
    assert {item["task_ref"] for item in request["decision_tasks"]} >= {
        "answer-entity-resolution@2.1.0",
        "substantive-entity-mention@2.1.0",
        "recommendation-relation@2.1.0",
        "rank-semantics@2.1.0",
        "claim-extraction@2.1.0",
        "risk-adjudication@2.1.0",
    }
    assert all(item["official_use"] is False for item in request["decision_tasks"])
    assert all(item["max_auto_rejudge_generations"] == 0 for item in request["decision_tasks"])
    assert len(request["manifest"]["decision_task_bundle"]["task_refs"]) == 14
    assert all(
        task_ref.endswith("@2.1.0")
        for task_ref in request["manifest"]["decision_task_bundle"]["task_refs"]
    )
    assert all(
        policy_ref.startswith("semantic-v2-primary-")
        for policy_ref in request["policy_versions_by_hash"].values()
    )


def test_request_builder_freezes_all_applicable_static_and_dynamic_task_fanout() -> None:
    request = build_answer_semantic_workflow_request(
        tenant_pub_id="ten_fixture",
        project_pub_id="prj_fixture",
        answer_pub_id="ans_fixture",
        analysis_run_pub_id="arun_fixture",
        query_key="q" * 64,
        query_text_hash="a" * 64,
        answer_text_hash="b" * 64,
        managed_entities=(
            {
                "candidate_id": "brd_fixture",
                "candidate_type": "brand",
                "label": "focal-label",
            },
            {
                "candidate_id": "cmp_fixture",
                "candidate_type": "competitor",
                "label": "competitor-label",
            },
        ),
        citation_pub_ids=("cit_fixture",),
        rubric_dimensions=(
            {
                "dimension_id": "dim_quality",
                "focal_entity_id": "brd_fixture",
                "rubric_hash": "c" * 64,
            },
        ),
        classification_source="live",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    query_refs = [item["task_ref"] for item in request["query_context_request"]["decision_tasks"]]
    answer_refs = [item["task_ref"] for item in request["decision_tasks"]]
    assert "requested-dimension-applicability@2.1.0" in query_refs
    assert "answer-dimension-coverage@2.1.0" in answer_refs
    assert answer_refs.count("stance-and-pairwise@2.1.0") == 3
    assert set(request["dynamic_task_templates"]) == {
        "claim-verifiability@2.1.0",
        "claim-evidence-verdict@2.1.0",
        "citation-claim-support@2.1.0",
    }
    assert set(request["required_capabilities"]) >= {
        "substantive_entity_mention",
        "recommendation_relation",
        "rank_semantics",
        "stance_and_pairwise",
        "requested_dimension_applicability",
        "answer_dimension_coverage",
        "claim_evidence_verdict",
        "citation_claim_support",
        "risk_adjudication",
    }
    template = request["dynamic_task_templates"]["claim-verifiability@2.1.0"]
    subject = {"answer_pub_id": "ans_fixture", "claim_fingerprint": "d" * 64}
    first = instantiate_decision_task_request(template, subject)
    second = instantiate_decision_task_request(dict(reversed(list(template.items()))), subject)
    assert first == second
    assert first["input_material_hashes"] == template["input_material_hashes"]
    assert first["source_answer_pub_id"] == "ans_fixture"
    assert "answer_text" not in first and "query_text" not in first
    assert len(first["input_hash"]) == len(first["idempotency_key"]) == 64
