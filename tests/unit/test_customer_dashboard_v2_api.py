from __future__ import annotations

from datetime import UTC, date, datetime
from inspect import getsource
from typing import Any

import pytest
from geo_platform.customer_dashboard.service import CustomerDashboardV2Service

HASH = "a" * 64
METRIC_NAME = "ai_recommendation_organic_mention_rate_v2"


def _metric() -> dict[str, Any]:
    return {
        "snapshot_pub_id": "msn_customer_v2",
        "snapshot_hash": HASH,
        "focal_entity_id": "entity_target",
        "metric_name": METRIC_NAME,
        "metric_version": "2.0.0",
        "metric_definition_hash": HASH,
        "state": "ready",
        "state_reason_codes": [],
        "value": 0.5,
        "observed_value": 0.5,
        "answer_weighted_value": 0.5,
        "raw_numerator": 1,
        "raw_denominator": 2,
        "weighted_numerator": 0.5,
        "weighted_denominator": 1,
        "coverage": {
            "collection": 1,
            "query_context": 1,
            "semantic": 1,
            "evidence": 1,
            "semantic_by_capability": {"entity_mention": 1},
        },
        "decision_method_mix": {"hybrid": 1},
        "adjudication_sensitivity": {"lower": 0.48, "upper": 0.52},
        "missing_bounds": {"lower": 0.5, "upper": 0.5},
        "unique_query_count": 2,
        "candidate_answer_count": 2,
        "known_answer_count": 2,
        "unknown_answer_count": 0,
        "not_applicable_answer_count": 0,
        "excluded_answer_count": 0,
        "design_cell_count": 2,
        "contribution_set_hash": HASH,
        "query_contribution_set_hash": HASH,
        "design_contribution_set_hash": HASH,
    }


def _snapshot_set() -> dict[str, Any]:
    return {
        "schema_version": "metric-snapshot-set-v2",
        "snapshot_set_pub_id": "mss_customer_v2",
        "snapshot_set_hash": HASH,
        "project_pub_id": "prj_customer_v2",
        "state": "ready",
        "as_of": datetime(2026, 8, 20, tzinfo=UTC),
        "window": {"start": date(2026, 8, 1), "end": date(2026, 8, 20)},
        "filters": {"model": [], "region": [], "mode": []},
        "focal_entity_ids": ["entity_target"],
        "aggregation_method": "query_macro",
        "design_basis": "planned_cells",
        "scope_hash": HASH,
        "dependency_bundle_hash": HASH,
        "metrics": [_metric()],
    }


class FakeRepository:
    def __init__(self) -> None:
        self.current_calls: list[dict[str, Any]] = []
        self.contribution_calls: list[dict[str, Any]] = []

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "metric_name": METRIC_NAME,
                "metric_version": "2.0.0",
                "definition_hash": HASH,
                "business_question": "中性推荐回答中目标品牌被实质提及的比例是多少？",
                "denominator_description": "全部语义已知的中性 AI 推荐有效回答。",
                "outcome_source": "hybrid",
                "query_predicate": {"exposure_is": "brand_neutral"},
                "outcome_expression": {"event_exists": {"type": "entity_mention"}},
                "required_semantic_capabilities": ["entity_mention"],
                "decision_task_refs": [{"task_ref": "substantive-entity-mention@2.0.0"}],
                "semantic_rubric_ref": "rubric://entity-mention/2.0.0",
            }
        ]

    def current_snapshot_set(self, **kwargs: Any) -> dict[str, Any]:
        self.current_calls.append(kwargs)
        return _snapshot_set()

    def get_snapshot_set(self, **kwargs: Any) -> dict[str, Any]:
        return _snapshot_set()

    def get_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        return {**_metric(), "snapshot_set_pub_id": "mss_customer_v2"}

    def list_contributions(self, **kwargs: Any) -> dict[str, Any]:
        self.contribution_calls.append(kwargs)
        return {
            "schema_version": "metric-contributions-v2",
            "snapshot_pub_id": "msn_customer_v2",
            "totals": {
                "snapshot_candidate_count": 2,
                "filtered_count": 1,
                "raw_numerator": 1,
                "raw_denominator": 2,
                "weighted_numerator": 0.5,
                "weighted_denominator": 1,
                "contribution_set_hash": HASH,
            },
            "data": [
                {
                    "answer_pub_id": "ans_customer_v2",
                    "query_pub_id": "qry_customer_v2",
                    "query_key": "query-customer-v2",
                    "query_text": "推荐几家网络安全公司",
                    "analysis_lenses": ["ai_recommendation"],
                    "requested_operations": ["recommend"],
                    "exposure_role": "brand_neutral",
                    "model": "DeepSeek",
                    "region": "中国",
                    "mode": "normal",
                    "capture_time": datetime(2026, 8, 19, tzinfo=UTC),
                    "eligibility_status": "included_hit",
                    "reason_codes": ["substantive_entity_mention"],
                    "outcome_value": True,
                    "numerator_contribution": 1,
                    "denominator_contribution": 1,
                    "query_weight": 0.5,
                    "design_cell_weight": 1,
                    "repeat_weight": 1,
                    "final_weight": 0.5,
                    "weighted_numerator": 0.5,
                    "weighted_denominator": 0.5,
                    "semantic_manifest_pub_id": "asm_customer_v2",
                    "supporting_events": [],
                    "supporting_decisions": [],
                    "answer_excerpt": "盛邦安全提供网络安全服务。",
                    "answer_detail_href": (
                        "/api/v2/customer-dashboard/projects/prj_customer_v2/"
                        "answer-library/answers/ans_customer_v2?snapshot_id=als_existing"
                    ),
                    "contribution_hash": HASH,
                }
            ],
            "next_cursor": None,
            "has_more": False,
        }


def _service(monkeypatch: pytest.MonkeyPatch) -> tuple[CustomerDashboardV2Service, FakeRepository]:
    repository = FakeRepository()
    service = CustomerDashboardV2Service(dsn="postgresql://unused", repository=repository)
    monkeypatch.setattr(
        CustomerDashboardV2Service,
        "_brand_name",
        lambda self, **kwargs: "目标品牌",
    )
    return service, repository


def test_dashboard_v2_reads_an_exact_snapshot_metric_without_v1_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _service(monkeypatch)

    document = service.dashboard(
        tenant_pub_id="tnt_customer_v2",
        project_pub_id="prj_customer_v2",
        business_view="ai_recommendation",
        exposure_role="brand_neutral",
        metric_names=(METRIC_NAME,),
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
        publication_channel="official",
    )

    assert document["schema_version"] == "customer-dashboard-v2"
    assert document["snapshot_set_pub_id"] == "mss_customer_v2"
    assert document["snapshot_set_hash"] == HASH
    assert document["requested_metric_names"] == [METRIC_NAME]
    assert document["metrics"][0]["metric_name"] == METRIC_NAME
    assert document["metrics"][0]["raw_denominator"] == 2
    assert repository.current_calls[0]["publication_channel"] == "official"


def test_dashboard_v2_rejects_metric_names_from_another_cohort_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _service(monkeypatch)

    with pytest.raises(ValueError, match="customer_metric_outside_requested_cohort_v2"):
        service.dashboard(
            tenant_pub_id="tnt_customer_v2",
            project_pub_id="prj_customer_v2",
            business_view="ai_impression",
            exposure_role="brand_neutral",
            metric_names=(METRIC_NAME,),
            start=None,
            end=None,
        )

    assert repository.current_calls == []


def test_trace_v2_keeps_set_hash_and_snapshot_identity_in_the_read_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _service(monkeypatch)

    document = service.trace(
        tenant_pub_id="tnt_customer_v2",
        project_pub_id="prj_customer_v2",
        snapshot_set_pub_id="mss_customer_v2",
        expected_snapshot_set_hash=HASH,
        snapshot_pub_id="msn_customer_v2",
        business_view="ai_recommendation",
        exposure_role="brand_neutral",
        cursor=None,
        limit=50,
    )

    assert document["schema_version"] == "customer-metric-trace-v2"
    assert document["snapshot_set_pub_id"] == "mss_customer_v2"
    assert document["contributions"]["snapshot_pub_id"] == "msn_customer_v2"
    answer_href = document["contributions"]["data"][0]["answer_detail_href"]
    assert "?snapshot_id=als_existing&metric_snapshot_set_pub_id=mss_customer_v2" in answer_href
    assert f"metric_snapshot_set_hash={HASH}" in answer_href
    assert repository.contribution_calls == [
        {
            "tenant_pub_id": "tnt_customer_v2",
            "snapshot_pub_id": "msn_customer_v2",
            "cursor": None,
            "limit": 50,
            "eligibility_status": None,
            "reason_code": None,
            "query": None,
            "model": None,
            "region": None,
            "mode": None,
            "hit": None,
        }
    ]


def test_customer_v2_service_has_no_legacy_formula_or_model_read_dependency() -> None:
    source = getsource(CustomerDashboardV2Service).lower()
    for forbidden in (
        "build_customer_metric_bundle",
        "infer_recommendation",
        "metricregistry",
        "llm",
        "chat_completion",
    ):
        assert forbidden not in source
