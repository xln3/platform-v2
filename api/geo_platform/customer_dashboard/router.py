# ruff: noqa: B008
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel

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
    CustomerBusinessView,
    CustomerDashboardV2View,
    CustomerDashboardView,
    CustomerExposureRole,
    CustomerMetricCatalogView,
    CustomerMetricSpecView,
    CustomerMetricTraceV2View,
)
from .service import CustomerDashboardService, CustomerDashboardV2Service

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


def _customer_metrics_v2_not_found(exc: LookupError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "customer_metrics_v2_resource_not_found"},
    )


def _bound_metric_snapshot_set(
    *,
    principal: Principal,
    project_pub_id: str,
    set_pub_id: str | None,
    set_hash: str | None,
) -> dict[str, object] | None:
    if (set_pub_id is None) != (set_hash is None):
        raise HTTPException(status_code=422, detail={"code": "incomplete_metric_snapshot_binding"})
    if set_pub_id is None or set_hash is None:
        return None
    try:
        snapshot_set = CustomerDashboardV2Service(dsn=_dsn()).repository.get_snapshot_set(
            tenant_pub_id=principal.tenant_pub_id,
            set_pub_id=set_pub_id,
        )
    except LookupError as exc:
        raise _customer_metrics_v2_not_found(exc) from exc
    if (
        snapshot_set.get("project_pub_id") != project_pub_id
        or snapshot_set.get("snapshot_set_hash") != set_hash
    ):
        raise _customer_metrics_v2_not_found(LookupError("snapshot_binding_mismatch"))
    return snapshot_set


def _metric_binding_cutoff(
    *,
    snapshot_set: dict[str, object] | None,
    requested_snapshot_at: datetime | None,
    start: date,
    end: date,
) -> datetime:
    if snapshot_set is None:
        return (
            datetime.now(UTC)
            if requested_snapshot_at is None
            else _customer_snapshot(requested_snapshot_at)
        )
    raw_as_of = snapshot_set.get("as_of")
    if not isinstance(raw_as_of, datetime):
        raise HTTPException(status_code=404, detail={"code": "metric_snapshot_binding_invalid"})
    as_of = raw_as_of.astimezone(UTC)
    window = snapshot_set.get("window")
    if not isinstance(window, dict) or window.get("start") != start or window.get("end") != end:
        raise HTTPException(status_code=422, detail={"code": "metric_snapshot_window_mismatch"})
    if requested_snapshot_at is not None and _customer_snapshot(requested_snapshot_at) != as_of:
        raise HTTPException(status_code=422, detail={"code": "metric_snapshot_cutoff_mismatch"})
    return as_of


def _with_metric_binding[ProjectionModelT: BaseModel](
    document: ProjectionModelT,
    snapshot_set: dict[str, object] | None,
) -> ProjectionModelT:
    if snapshot_set is None:
        return document
    return document.model_copy(
        update={
            "metric_snapshot_set_pub_id": snapshot_set["snapshot_set_pub_id"],
            "metric_snapshot_set_hash": snapshot_set["snapshot_set_hash"],
        }
    )


@router.get(
    "/metrics/catalog",
    response_model=CustomerMetricCatalogView,
    operation_id="getCustomerMetricCatalog",
    deprecated=True,
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
    deprecated=True,
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
    "/projects/{project_pub_id}/dashboard-v2",
    response_model=CustomerDashboardV2View,
    operation_id="getCustomerDashboardV2",
)
def customer_dashboard_v2(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    business_view: CustomerBusinessView = Query(),
    exposure_role: CustomerExposureRole = Query(),
    metric_name: list[str] = Query(min_length=1, max_length=40),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: list[str] = Query(default_factory=list, max_length=100),
    region: list[str] = Query(default_factory=list, max_length=100),
    mode: list[str] = Query(default_factory=list, max_length=100),
    focal_entity_id: str | None = Query(default=None, min_length=1, max_length=200),
    publication_channel: Literal["official", "shadow"] = Query(default="official"),
    principal: Principal = Depends(get_principal),
) -> CustomerDashboardV2View:
    principal.require("project:read")
    if publication_channel == "shadow" and not principal.allows("metrics:publish"):
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if (start is None) != (end is None) or (
        start is not None and end is not None and (start > end or (end - start).days > 366)
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_metric_window"})
    _secure_projection_headers(response)
    try:
        document = CustomerDashboardV2Service(dsn=_dsn()).dashboard(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            business_view=business_view,
            exposure_role=exposure_role,
            metric_names=metric_name,
            start=start,
            end=end,
            models=model,
            regions=region,
            modes=mode,
            focal_entity_id=focal_entity_id,
            publication_channel=publication_channel,
        )
    except LookupError as exc:
        raise _customer_metrics_v2_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return CustomerDashboardV2View.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/dashboard-v2/snapshot-sets/{snapshot_set_pub_id}"
    "/snapshots/{snapshot_pub_id}/trace",
    response_model=CustomerMetricTraceV2View,
    operation_id="getCustomerMetricTraceV2",
)
def customer_metric_trace_v2(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    snapshot_set_pub_id: str = Path(pattern=r"^mss_[A-Za-z0-9_-]{1,116}$"),
    snapshot_pub_id: str = Path(pattern=r"^msn_[A-Za-z0-9_-]{1,116}$"),
    snapshot_set_hash: str = Query(pattern=r"^[0-9a-f]{64}$"),
    business_view: CustomerBusinessView = Query(),
    exposure_role: CustomerExposureRole = Query(),
    cursor: str | None = Query(default=None, min_length=8, max_length=2_000),
    limit: int = Query(default=50, ge=1, le=100),
    eligibility_status: Literal[
        "included_hit",
        "included_miss",
        "excluded",
        "not_applicable",
        "analysis_unknown",
        "analysis_failed",
    ]
    | None = Query(default=None),
    reason_code: str | None = Query(default=None, min_length=1, max_length=120),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    hit: bool | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> CustomerMetricTraceV2View:
    principal.require("project:read")
    _secure_projection_headers(response)
    try:
        document = CustomerDashboardV2Service(dsn=_dsn()).trace(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            snapshot_set_pub_id=snapshot_set_pub_id,
            expected_snapshot_set_hash=snapshot_set_hash,
            snapshot_pub_id=snapshot_pub_id,
            business_view=business_view,
            exposure_role=exposure_role,
            cursor=cursor,
            limit=limit,
            eligibility_status=eligibility_status,
            reason_code=reason_code,
            query=query,
            model=model,
            region=region,
            mode=mode,
            hit=hit,
        )
    except LookupError as exc:
        raise _customer_metrics_v2_not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return CustomerMetricTraceV2View.model_validate(document)


@router.get(
    "/projects/{project_pub_id}/answers",
    response_model=CustomerAnswerPageView,
    operation_id="getCustomerAnswerPage",
    deprecated=True,
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
    metric_snapshot_set_pub_id: str | None = Query(
        default=None, pattern=r"^mss_[A-Za-z0-9_-]{1,116}$"
    ),
    metric_snapshot_set_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=8, ge=1, le=20),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryPageView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    snapshot_set = _bound_metric_snapshot_set(
        principal=principal,
        project_pub_id=project_pub_id,
        set_pub_id=metric_snapshot_set_pub_id,
        set_hash=metric_snapshot_set_hash,
    )
    if snapshot_set is None and (snapshot_id is None) != (snapshot_at is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "incomplete_answer_library_snapshot"},
        )
    cutoff = _metric_binding_cutoff(
        snapshot_set=snapshot_set,
        requested_snapshot_at=snapshot_at,
        start=effective_start,
        end=effective_end,
    )
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
    result = CustomerAnswerLibraryPageView.model_validate(document)
    return _with_metric_binding(result, snapshot_set)


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
    metric_snapshot_set_pub_id: str | None = Query(
        default=None, pattern=r"^mss_[A-Za-z0-9_-]{1,116}$"
    ),
    metric_snapshot_set_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryMetaDetailView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    snapshot_set = _bound_metric_snapshot_set(
        principal=principal,
        project_pub_id=project_pub_id,
        set_pub_id=metric_snapshot_set_pub_id,
        set_hash=metric_snapshot_set_hash,
    )
    cutoff = _metric_binding_cutoff(
        snapshot_set=snapshot_set,
        requested_snapshot_at=snapshot_at,
        start=effective_start,
        end=effective_end,
    )
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
    result = CustomerAnswerLibraryMetaDetailView.model_validate(document)
    return _with_metric_binding(result, snapshot_set)


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
    metric_snapshot_set_pub_id: str | None = Query(
        default=None, pattern=r"^mss_[A-Za-z0-9_-]{1,116}$"
    ),
    metric_snapshot_set_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
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
    snapshot_set = _bound_metric_snapshot_set(
        principal=principal,
        project_pub_id=project_pub_id,
        set_pub_id=metric_snapshot_set_pub_id,
        set_hash=metric_snapshot_set_hash,
    )
    cutoff = _metric_binding_cutoff(
        snapshot_set=snapshot_set,
        requested_snapshot_at=snapshot_at,
        start=effective_start,
        end=effective_end,
    )
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
    result = CustomerAnswerLibraryQuestionRunsView.model_validate(document)
    return _with_metric_binding(result, snapshot_set)


@router.get(
    "/projects/{project_pub_id}/answer-library/answers/{answer_pub_id}",
    response_model=CustomerAnswerLibraryDetailView,
    operation_id="getCustomerAnswerLibraryDetail",
)
def customer_answer_library_detail(
    response: Response,
    project_pub_id: str = Path(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$"),
    answer_pub_id: str = Path(pattern=r"^ans_[A-Za-z0-9_-]{1,116}$"),
    snapshot_id: str | None = Query(default=None, pattern=r"^als_[0-9a-f]{24}$"),
    snapshot_at: datetime | None = Query(default=None),
    metric_snapshot_set_pub_id: str | None = Query(
        default=None, pattern=r"^mss_[A-Za-z0-9_-]{1,116}$"
    ),
    metric_snapshot_set_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> CustomerAnswerLibraryDetailView:
    principal.require("project:read")
    effective_start, effective_end = _customer_window(start, end)
    snapshot_set = _bound_metric_snapshot_set(
        principal=principal,
        project_pub_id=project_pub_id,
        set_pub_id=metric_snapshot_set_pub_id,
        set_hash=metric_snapshot_set_hash,
    )
    if snapshot_set is None and (snapshot_id is None or snapshot_at is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "incomplete_answer_library_snapshot"},
        )
    cutoff = _metric_binding_cutoff(
        snapshot_set=snapshot_set,
        requested_snapshot_at=snapshot_at,
        start=effective_start,
        end=effective_end,
    )
    _secure_projection_headers(response)
    try:
        library_service = CustomerAnswerLibraryService(dsn=_dsn())
        resolved_snapshot_id = snapshot_id
        if resolved_snapshot_id is None:
            root = library_service.library_page(
                tenant_pub_id=principal.tenant_pub_id,
                project_pub_id=project_pub_id,
                start=effective_start,
                end=effective_end,
                snapshot_at=cutoff,
                snapshot_id=None,
                offset=0,
                limit=1,
            )
            resolved_snapshot_id = str(root["snapshot_id"])
        document = library_service.answer_detail(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            answer_pub_id=answer_pub_id,
            snapshot_id=resolved_snapshot_id,
            snapshot_at=cutoff,
            start=effective_start,
            end=effective_end,
        )
    except LookupError as exc:
        raise _answer_library_not_found(exc) from exc
    result = CustomerAnswerLibraryDetailView.model_validate(document)
    return _with_metric_binding(result, snapshot_set)
