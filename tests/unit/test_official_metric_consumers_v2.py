from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from geo_platform.analytics import router as analytics_router
from geo_platform.brandrank import router as brandrank_router
from geo_platform.identity.policy import Principal, Role
from geo_platform.metrics_v2.consumer_projection import OfficialMetricsConsumer, OfficialScope
from geo_platform.sop import router as sop_router
from geo_platform.variants import router as variants_router

HASH_A = "a" * 64
HASH_B = "b" * 64


def _metric(*, set_suffix: str, design_hash: str = HASH_A, value: float = 0.5) -> dict[str, Any]:
    return {
        "snapshot_pub_id": f"msn_{set_suffix}",
        "snapshot_hash": HASH_A,
        "focal_entity_id": "entity_target",
        "metric_name": "ai_recommendation_organic_mention_rate_v2",
        "metric_version": "2.0.0",
        "metric_definition_hash": HASH_A,
        "state": "ready",
        "state_reason_codes": [],
        "value": value,
        "observed_value": value,
        "answer_weighted_value": value,
        "raw_numerator": 1,
        "raw_denominator": 2,
        "weighted_numerator": value,
        "weighted_denominator": 1,
        "coverage": {
            "collection": 1,
            "query_context": 1,
            "semantic": 1,
            "evidence": 1,
            "semantic_by_capability": {"entity_mention": 1},
        },
        "decision_method_mix": {"hybrid": 1},
        "adjudication_sensitivity": {"lower": value, "upper": value},
        "missing_bounds": {"lower": value, "upper": value},
        "unique_query_count": 1,
        "candidate_answer_count": 2,
        "known_answer_count": 2,
        "unknown_answer_count": 0,
        "not_applicable_answer_count": 0,
        "excluded_answer_count": 0,
        "design_cell_count": 1,
        "contribution_set_hash": HASH_A,
        "query_contribution_set_hash": HASH_A,
        "design_contribution_set_hash": design_hash,
    }


def _set(
    *,
    suffix: str = "official",
    start: date = date(2026, 8, 1),
    end: date = date(2026, 8, 20),
    design_hash: str = HASH_A,
    value: float = 0.5,
) -> dict[str, Any]:
    return {
        "schema_version": "metric-snapshot-set-v2",
        "snapshot_set_pub_id": f"mss_{suffix}",
        "snapshot_set_hash": HASH_A,
        "project_pub_id": "prj_official",
        "state": "ready",
        "as_of": datetime(2026, 8, 21, tzinfo=UTC),
        "window": {"start": start, "end": end},
        "filters": {"model": [], "region": [], "mode": []},
        "focal_entity_ids": ["entity_target"],
        "aggregation_method": "query_macro",
        "design_basis": "planned_cells",
        "scope_hash": HASH_A,
        "dependency_bundle_hash": HASH_B,
        "metrics": [
            _metric(set_suffix=suffix, design_hash=design_hash, value=value),
        ],
    }


class FakeRepository:
    def __init__(self, *, mismatched_support: bool = False) -> None:
        self.current_calls: list[dict[str, Any]] = []
        self.mismatched_support = mismatched_support

    def current_snapshot_set(self, **kwargs: Any) -> dict[str, Any]:
        self.current_calls.append(kwargs)
        start = date.fromisoformat(kwargs["start"])
        end = date.fromisoformat(kwargs["end"])
        current = end == date(2026, 8, 20)
        return _set(
            suffix="retest" if current else "baseline",
            start=start,
            end=end,
            design_hash=HASH_B if current and self.mismatched_support else HASH_A,
            value=0.75 if current else 0.5,
        )

    def list_contributions(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "data": [
                {
                    "answer_pub_id": "ans_official",
                    "query_key": "query-one",
                    "model": "deepseek",
                    "region": "cn",
                    "mode": "normal",
                    "capture_time": datetime(2026, 8, 18, tzinfo=UTC),
                    "eligibility_status": "included_hit",
                    "weighted_numerator": 0.5,
                    "weighted_denominator": 0.5,
                    "contribution_hash": HASH_A,
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }

    def list_query_contributions(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "data": [
                {
                    "query_key": "query-one",
                    "query_pub_id": "qry_official",
                    "query_text": "推荐网络安全公司",
                    "value": 0.5,
                    "numerator": 1,
                    "denominator": 2,
                    "unknown_weight": 0,
                    "answer_count": 2,
                    "contribution_hash": HASH_A,
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }


def _scope() -> OfficialScope:
    return OfficialScope(
        tenant_pub_id="tnt_official",
        project_pub_id="prj_official",
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
    )


def test_shared_projection_forces_official_and_preserves_member_hashes() -> None:
    repository = FakeRepository()
    document = OfficialMetricsConsumer(repository).overview(_scope())

    assert repository.current_calls[0]["publication_channel"] == "official"
    assert document["binding"]["snapshot_set_pub_id"] == "mss_retest"
    assert document["binding"]["snapshot_set_hash"] == HASH_A
    assert document["metrics"][0]["snapshot_pub_id"] == "msn_retest"
    assert document["metrics"][0]["contribution_set_hash"] == HASH_A


def test_breakdown_reads_persisted_contributions_not_answers_or_rank_sql() -> None:
    document = OfficialMetricsConsumer(FakeRepository()).breakdown(_scope(), group_by="model")

    assert document["binding"]["publication_channel"] == "official"
    assert document["rows"][0]["group"] == {"model": "deepseek"}
    assert document["rows"][0]["value"] == 1.0


def test_delta_is_null_when_common_design_support_cannot_be_proven() -> None:
    document = OfficialMetricsConsumer(FakeRepository(mismatched_support=True)).delta(_scope())
    metric = document["paired_metric_delta"][0]

    assert metric["paired_delta"] is None
    assert metric["state"] == "incompatible"
    assert metric["reason_codes"] == ["common_support_not_identical"]
    assert document["composition_changed"] is True


class FakeConsumer:
    def overview(self, scope: OfficialScope) -> dict[str, Any]:
        return OfficialMetricsConsumer(FakeRepository()).overview(scope)

    def brandrank(self, scope: OfficialScope) -> dict[str, Any]:
        return OfficialMetricsConsumer(FakeRepository()).brandrank(scope)

    def breakdown(self, scope: OfficialScope, *, group_by: str) -> dict[str, Any]:
        assert group_by == "model"
        return OfficialMetricsConsumer(FakeRepository()).breakdown(scope, group_by="model")

    def delta(self, scope: OfficialScope) -> dict[str, Any]:
        return OfficialMetricsConsumer(FakeRepository()).delta(scope)


def _fail_v1(*args: object, **kwargs: object) -> None:
    raise AssertionError("official consumer attempted a V1 calculator")


def test_all_formal_routes_survive_v1_calculators_being_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from domain.brandrank import metrics as brandrank_metrics
    from domain.metrics import core as metric_core
    from domain.metrics import customer as customer_metrics
    from domain.reporting import service1_metrics

    monkeypatch.setattr(metric_core.MetricRegistry, "compute", _fail_v1)
    monkeypatch.setattr(customer_metrics, "build_customer_metric_bundle", _fail_v1)
    monkeypatch.setattr(customer_metrics, "infer_recommendation", _fail_v1)
    monkeypatch.setattr(service1_metrics, "entity_metric", _fail_v1)
    monkeypatch.setattr(brandrank_metrics, "calculate_appearance_rate", _fail_v1)
    monkeypatch.setattr(analytics_router, "_official_consumer", FakeConsumer)
    monkeypatch.setattr(brandrank_router, "_official_consumer", FakeConsumer)
    monkeypatch.setattr(sop_router, "_official_consumer", FakeConsumer)
    monkeypatch.setattr(variants_router, "_official_consumer", FakeConsumer)
    principal = Principal("operator", Role.OPERATOR, "tnt_official")

    analytics = analytics_router.official_overview(
        project_pub_id="prj_official",
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
        principal=principal,
    )
    brandrank = brandrank_router.official_brand_visibility(
        project_pub_id="prj_official",
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
        focal_entity_id=None,
        principal=principal,
    )
    sop = sop_router.get_official_metrics(
        project_pub_id="prj_official",
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
        focal_entity_id=None,
        principal=principal,
    )
    variants = variants_router.official_metrics(
        project_pub_id="prj_official",
        start=date(2026, 8, 1),
        end=date(2026, 8, 20),
        focal_entity_id=None,
        principal=principal,
    )

    assert analytics["binding"]["snapshot_set_pub_id"] == "mss_retest"
    assert brandrank["binding"] == analytics["binding"]
    assert sop["binding"] == analytics["binding"]
    assert variants["binding"] == analytics["binding"]
