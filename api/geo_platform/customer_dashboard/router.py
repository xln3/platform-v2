# ruff: noqa: B008
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from domain.metrics.customer import metric_catalog

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .schemas import (
    CustomerAnswerPageView,
    CustomerDashboardView,
    CustomerMetricCatalogView,
    CustomerMetricSpecView,
)
from .service import CustomerDashboardService

router = APIRouter(prefix="/api/v2/customer-dashboard", tags=["customer-dashboard"])


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


@router.get(
    "/metrics/catalog",
    response_model=CustomerMetricCatalogView,
    operation_id="getCustomerMetricCatalog",
)
def customer_metric_catalog(
    principal: Principal = Depends(get_principal),
) -> CustomerMetricCatalogView:
    principal.require("project:read")
    return CustomerMetricCatalogView(
        schema_version="customer-metric-catalog-v1",
        metrics=[CustomerMetricSpecView(**asdict(item)) for item in metric_catalog()],
    )


@router.get(
    "/projects/{project_pub_id}",
    response_model=CustomerDashboardView,
    operation_id="getCustomerDashboard",
)
def customer_dashboard(
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    principal: Principal = Depends(get_principal),
) -> CustomerDashboardView:
    principal.require("project:read")
    effective_end = end or datetime.now(UTC).date()
    effective_start = start or (effective_end - timedelta(days=29))
    if effective_start > effective_end or (effective_end - effective_start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    try:
        document = CustomerDashboardService(dsn=_dsn()).dashboard(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            start=effective_start,
            end=effective_end,
            model=model,
            region=region,
            mode=mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    return CustomerDashboardView.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/answers",
    response_model=CustomerAnswerPageView,
    operation_id="getCustomerAnswerPage",
)
def customer_answers(
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    mentioned: bool | None = Query(default=None),
    sentiment: Literal["positive", "neutral", "negative", "unknown"] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerPageView:
    principal.require("project:read")
    effective_end = end or datetime.now(UTC).date()
    effective_start = start or (effective_end - timedelta(days=29))
    if effective_start > effective_end or (effective_end - effective_start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    try:
        document = CustomerDashboardService(dsn=_dsn()).answer_page(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            start=effective_start,
            end=effective_end,
            search=search,
            model=model,
            region=region,
            mode=mode,
            mentioned=mentioned,
            sentiment=sentiment,
            offset=offset,
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    return CustomerAnswerPageView.model_validate(document)
