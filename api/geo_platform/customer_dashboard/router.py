# ruff: noqa: B008
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from domain.metrics.customer import metric_catalog

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .answer_library import CustomerAnswerLibraryService
from .schemas import (
    CustomerAnswerLibraryDetailView,
    CustomerAnswerLibraryMetaDetailView,
    CustomerAnswerLibraryPageView,
    CustomerAnswerLibraryQuestionRunsView,
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


def _customer_window(start: date | None, end: date | None) -> tuple[date, date]:
    effective_end = end or datetime.now(UTC).date()
    effective_start = start or (effective_end - timedelta(days=29))
    if effective_start > effective_end or (effective_end - effective_start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    return effective_start, effective_end


def _customer_snapshot(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_answer_library_snapshot"})
    normalized = value.astimezone(UTC)
    now = datetime.now(UTC)
    if normalized > now + timedelta(minutes=1):
        raise HTTPException(status_code=422, detail={"code": "invalid_answer_library_snapshot"})
    return normalized


def _secure_projection_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _answer_library_not_found(exc: LookupError) -> HTTPException:
    code = str(exc)
    allowed = {
        "project_not_found",
        "answer_library_snapshot_invalid",
        "answer_library_snapshot_not_found",
        "answer_library_meta_query_not_found",
        "answer_library_question_not_found",
        "answer_library_answer_not_found",
    }
    return HTTPException(
        status_code=404,
        detail={"code": code if code in allowed else "answer_library_not_found"},
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


@router.get(
    "/projects/{project_pub_id}/answer-library",
    response_model=CustomerAnswerLibraryPageView,
    operation_id="getCustomerAnswerLibraryPage",
)
def customer_answer_library(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    snapshot_id: str | None = Query(default=None, pattern=r"^als_[0-9a-f]{24}$"),
    snapshot_at: datetime | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=8, ge=1, le=20),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryPageView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    if (snapshot_id is None) != (snapshot_at is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "incomplete_answer_library_snapshot"},
        )
    cutoff = datetime.now(UTC) if snapshot_at is None else _customer_snapshot(snapshot_at)
    _secure_projection_headers(response)
    try:
        document = CustomerAnswerLibraryService(dsn=_dsn()).library_page(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            start=effective_start,
            end=effective_end,
            snapshot_at=cutoff,
            snapshot_id=snapshot_id,
            search=search,
            model=model,
            region=region,
            mode=mode,
            offset=offset,
            limit=limit,
        )
    except LookupError as exc:
        raise _answer_library_not_found(exc) from exc
    return CustomerAnswerLibraryPageView.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/answer-library/meta-queries/{meta_query_id}",
    response_model=CustomerAnswerLibraryMetaDetailView,
    operation_id="getCustomerAnswerLibraryMetaQuery",
)
def customer_answer_library_meta_query(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    meta_query_id: str = Path(pattern=r"^amq_[0-9a-f]{24}$"),
    snapshot_id: str = Query(pattern=r"^als_[0-9a-f]{24}$"),
    snapshot_at: datetime = Query(),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryMetaDetailView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    cutoff = _customer_snapshot(snapshot_at)
    _secure_projection_headers(response)
    try:
        document = CustomerAnswerLibraryService(dsn=_dsn()).meta_query(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            meta_query_id=meta_query_id,
            snapshot_id=snapshot_id,
            snapshot_at=cutoff,
            start=effective_start,
            end=effective_end,
            model=model,
            region=region,
            mode=mode,
        )
    except LookupError as exc:
        raise _answer_library_not_found(exc) from exc
    return CustomerAnswerLibraryMetaDetailView.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/answer-library/questions/{question_id}/answers",
    response_model=CustomerAnswerLibraryQuestionRunsView,
    operation_id="getCustomerAnswerLibraryQuestionRuns",
)
def customer_answer_library_question_runs(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    question_id: str = Path(pattern=r"^aq_[0-9a-f]{24}$"),
    snapshot_id: str = Query(pattern=r"^als_[0-9a-f]{24}$"),
    snapshot_at: datetime = Query(),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryQuestionRunsView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    cutoff = _customer_snapshot(snapshot_at)
    _secure_projection_headers(response)
    try:
        document = CustomerAnswerLibraryService(dsn=_dsn()).question_runs(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            question_id=question_id,
            snapshot_id=snapshot_id,
            snapshot_at=cutoff,
            start=effective_start,
            end=effective_end,
            model=model,
            region=region,
            mode=mode,
            offset=offset,
            limit=limit,
        )
    except LookupError as exc:
        raise _answer_library_not_found(exc) from exc
    return CustomerAnswerLibraryQuestionRunsView.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/answer-library/answers/{answer_pub_id}",
    response_model=CustomerAnswerLibraryDetailView,
    operation_id="getCustomerAnswerLibraryDetail",
)
def customer_answer_library_detail(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    answer_pub_id: str = Path(pattern=r"^ans_[A-Za-z0-9_-]{1,116}$"),
    snapshot_id: str = Query(pattern=r"^als_[0-9a-f]{24}$"),
    snapshot_at: datetime = Query(),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryDetailView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    cutoff = _customer_snapshot(snapshot_at)
    _secure_projection_headers(response)
    try:
        document = CustomerAnswerLibraryService(dsn=_dsn()).answer_detail(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            answer_pub_id=answer_pub_id,
            snapshot_id=snapshot_id,
            snapshot_at=cutoff,
            start=effective_start,
            end=effective_end,
        )
    except LookupError as exc:
        raise _answer_library_not_found(exc) from exc
    return CustomerAnswerLibraryDetailView.model_validate(document)
