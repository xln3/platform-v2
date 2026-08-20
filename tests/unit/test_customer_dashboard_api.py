from datetime import UTC, date, datetime
from inspect import getsource

import pytest
from fastapi import HTTPException
from geo_platform.customer_dashboard import service as customer_service
from geo_platform.customer_dashboard.router import customer_dashboard, customer_metric_catalog
from geo_platform.identity.policy import Principal, Role

from domain.metrics.customer import build_customer_metric_bundle


def _principal() -> Principal:
    return Principal(
        subject="customer-safe",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_safe",
        user_pub_id="usr_safe",
    )


def test_metric_catalog_is_versioned_and_contains_no_operational_success_metrics() -> None:
    result = customer_metric_catalog(principal=_principal())

    assert result.schema_version == "customer-metric-catalog-v1"
    assert len(result.metrics) >= 40
    serialized = result.model_dump_json()
    for forbidden in ("success_rate", "failed_tasks", "completed_tasks", "total_tasks"):
        assert forbidden not in serialized


def test_dashboard_route_validates_window_and_returns_the_strict_projection(monkeypatch) -> None:
    document = build_customer_metric_bundle(
        project_pub_id="prj_safe",
        brand_name="安全品牌",
        competitor_names=(),
        answers=(),
        generated_at=datetime(2026, 8, 17, tzinfo=UTC),
        window_start=date(2026, 8, 1),
        window_end=date(2026, 8, 17),
    )
    monkeypatch.setattr(
        customer_service.CustomerDashboardService,
        "dashboard",
        lambda self, **kwargs: document,
    )

    result = customer_dashboard(
        project_pub_id="prj_safe",
        start=date(2026, 8, 1),
        end=date(2026, 8, 17),
        model=None,
        region=None,
        mode=None,
        principal=_principal(),
    )

    assert result.project_pub_id == "prj_safe"
    assert result.brand_name == "安全品牌"
    assert result.state == "building"
    assert result.window.start == "2026-08-01"
    assert result.window.end == "2026-08-17"

    with pytest.raises(HTTPException) as error:
        customer_dashboard(
            project_pub_id="prj_safe",
            start=date(2026, 8, 18),
            end=date(2026, 8, 17),
            model=None,
            region=None,
            mode=None,
            principal=_principal(),
        )
    assert error.value.status_code == 422


def test_dashboard_loader_has_no_collection_task_dependency() -> None:
    source = getsource(customer_service.CustomerDashboardService.dashboard)
    normalized = source.lower()

    for forbidden in (
        "collection_task",
        "total_tasks",
        "completed_tasks",
        "failed_tasks",
        "success_rate",
    ):
        assert forbidden not in normalized
