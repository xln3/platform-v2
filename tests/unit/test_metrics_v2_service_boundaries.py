from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from geo_platform.analytics import router as analytics_router
from geo_platform.brandrank import router as brandrank_router
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.metrics_v2 import router as metrics_router
from geo_platform.metrics_v2.repository import MetricsV2Repository
from geo_platform.metrics_v2.service import MetricsV2Service
from geo_platform.sop import router as sop_router
from geo_platform.variants import router as variants_router

from domain.metrics.v2.snapshot_engine import MetricSnapshotEngine
from workflows.activities.metrics_v2 import _physical_window_boundary
from workflows.workers import metrics as metrics_worker
from workflows.workers import s02 as s02_worker

ROOT = Path(__file__).resolve().parents[2]


def test_same_day_snapshot_window_has_distinct_physical_boundaries() -> None:
    assert _physical_window_boundary("2026-08-26", end=False) == datetime(2026, 8, 26, tzinfo=UTC)
    assert _physical_window_boundary("2026-08-26", end=True) == datetime(
        2026, 8, 26, 23, 59, 59, 999999, tzinfo=UTC
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_metrics_worker_has_no_model_or_decision_adapter_dependency() -> None:
    imports = _imported_modules(ROOT / "workflows/workers/metrics.py")
    forbidden_prefixes = (
        "openai",
        "anthropic",
        "domain.analysis",
        "workflows.activities.semantic_decisions",
        "geo_platform.adapters",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )
    assert all("metric" in activity.__module__ for activity in metrics_worker.METRICS_ACTIVITIES)


def test_metrics_get_boundary_does_not_import_engine_or_model_sdk() -> None:
    imports = _imported_modules(ROOT / "api/geo_platform/metrics_v2/router.py")
    imports |= _imported_modules(ROOT / "api/geo_platform/metrics_v2/service.py")
    forbidden_prefixes = (
        "openai",
        "anthropic",
        "domain.metrics.v2.snapshot_engine",
        "workflows.activities.metrics_v2",
        "workflows.activities.semantic_decisions_v2",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )


def test_uncalibrated_published_policy_is_not_an_official_dependency_blocker() -> None:
    source = inspect.getsource(MetricsV2Repository.publish_snapshot_set_cas)

    assert "policy.calibration_artifact_hash IS NULL" not in source
    assert "policy.calibration_artifact_hash IS NOT NULL" in source
    assert "ANY(snapshot.calibration_artifact_hashes)" in source


def test_snapshot_loader_projects_query_and_answer_decisions_together() -> None:
    source = inspect.getsource(MetricsV2Repository.load_snapshot_build_inputs)

    assert "context_decision_pub_ids" in source
    assert "bound_decision_ids" in source
    assert "reason_codes" in source


def test_s02_worker_does_not_register_formal_analysis_or_report_workflows() -> None:
    workflow_names = {workflow.__name__ for workflow in s02_worker.S02_WORKFLOWS}
    activity_names = {activity.__name__ for activity in s02_worker.S02_ACTIVITIES}

    assert "AnswerAnalysisWorkflow" not in workflow_names
    assert "ReportProductionWorkflow" not in workflow_names
    assert not any("report" in name.casefold() for name in activity_names)


def test_official_consumer_handlers_do_not_reference_legacy_calculators() -> None:
    handlers = (
        analytics_router.official_overview,
        analytics_router.official_breakdown,
        analytics_router.official_delta,
        brandrank_router.official_brand_visibility,
        sop_router.get_official_metrics,
        sop_router.get_official_before_after,
        variants_router.official_metrics,
    )
    forbidden = (
        "MetricRegistry",
        "_mention_rate",
        "build_customer_metric_bundle",
        "entity_metric",
        "calculate_appearance_rate",
        "infer_recommendation",
        "answer_analysis",
        "competitor_ranks",
    )

    for handler in handlers:
        source = inspect.getsource(handler)
        assert all(name not in source for name in forbidden), handler.__name__


class FakeReadService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def current_snapshot_set(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"snapshot_set_pub_id": "mss_read_only"}


def _fail_engine(*args: object, **kwargs: object) -> None:
    raise AssertionError("GET attempted to execute the metric engine")


def test_metrics_get_only_reads_repository_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeReadService()
    monkeypatch.setattr(metrics_router, "_service", lambda: service)
    monkeypatch.setattr(MetricSnapshotEngine, "build_set", _fail_engine)
    response = Response()
    principal = Principal("customer", Role.CUSTOMER, "tnt_read_only")

    result = metrics_router.current_snapshot_set_v2(
        response=response,
        project_pub_id="prj_read_only",
        start=None,
        end=None,
        model=[],
        region=[],
        mode=[],
        focal_entity_id=[],
        publication_channel="official",
        principal=principal,
    )

    assert result == {"snapshot_set_pub_id": "mss_read_only"}
    assert service.calls[0]["publication_channel"] == "official"
    assert response.headers["cache-control"] == "private, no-store"


class _OverrideRepository:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create_override(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error == "conflict":
            raise RuntimeError("metrics_v2_decision_hash_conflict")
        if self.error == "invalid":
            raise ValueError("metrics_v2_override_result_invalid")
        return {
            "decision_pub_id": "sdr_corrected",
            "supersedes_pub_id": "sdr_original",
            "decision_hash": "b" * 64,
            "recompute_job_pub_id": "mrj_corrected",
        }


def _override_client(
    monkeypatch: pytest.MonkeyPatch, repository: _OverrideRepository
) -> TestClient:
    service = MetricsV2Service(repository=repository)  # type: ignore[arg-type]
    monkeypatch.setattr(metrics_router, "_service", lambda: service)
    app = FastAPI()
    app.include_router(metrics_router.router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        "customer-subject",
        Role.CUSTOMER,
        "tnt_customer",
        "usr_customer",
    )
    return TestClient(app)


def test_customer_can_submit_project_scoped_exception_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _OverrideRepository()
    client = _override_client(monkeypatch, repository)

    response = client.post(
        "/api/v2/metrics/operations/semantic-decisions/sdr_original/overrides",
        json={
            "project_pub_id": "prj_customer",
            "result": {"substantive": False},
            "rationale_summary": "该段只是引用标题，不是回答正文中的实质提及。",
            "reason_codes": ["customer_fact_correction"],
            "expected_decision_hash": "a" * 64,
        },
    )

    assert response.status_code == 201
    assert response.json()["decision_pub_id"] == "sdr_corrected"
    assert repository.calls[0]["tenant_pub_id"] == "tnt_customer"
    assert repository.calls[0]["project_pub_id"] == "prj_customer"
    assert repository.calls[0]["actor_pub_id"] == "usr_customer"


def test_override_compare_and_swap_conflict_is_http_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _override_client(monkeypatch, _OverrideRepository(error="conflict"))

    response = client.post(
        "/api/v2/metrics/operations/semantic-decisions/sdr_original/overrides",
        json={
            "project_pub_id": "prj_customer",
            "result": {"substantive": False},
            "rationale_summary": "基于当前事实提交纠错。",
            "reason_codes": ["customer_fact_correction"],
            "expected_decision_hash": "a" * 64,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "semantic_decision_override_conflict"}}


def test_override_schema_invalid_result_is_http_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _override_client(monkeypatch, _OverrideRepository(error="invalid"))

    response = client.post(
        "/api/v2/metrics/operations/semantic-decisions/sdr_original/overrides",
        json={
            "project_pub_id": "prj_customer",
            "result": {"unexpected": "shape"},
            "rationale_summary": "提交纠错。",
            "reason_codes": ["customer_fact_correction"],
            "expected_decision_hash": "a" * 64,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "metrics_v2_override_result_invalid"}}
