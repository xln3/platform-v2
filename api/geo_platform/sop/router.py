# ruff: noqa: B008
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..metrics_v2.consumer_projection import OfficialMetricsConsumer, OfficialScope
from ..metrics_v2.repository import MetricsV2Repository
from .pagination_policy import (
    SOP_DEFAULT_PAGE_NUMBER,
    SOP_DEFAULT_PAGE_SIZE,
    SOP_MAX_PAGE_NUMBER,
    SOP_MAX_PAGE_SIZE,
    SOP_MIN_PAGE_NUMBER,
    SOP_MIN_PAGE_SIZE,
)
from .service import SopInvalidState, SopNotFound, SopPageResult, SopService

router = APIRouter(prefix="/api/v2/sop", tags=["sop"])

CaptureStatus = Literal[
    "success",
    "captcha",
    "login_wall",
    "interrupted",
    "incomplete",
    "risk_control",
    "search_disabled",
    "sources_unloaded",
]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    ),
]
SopPageNumber = Annotated[int, Query(ge=SOP_MIN_PAGE_NUMBER, le=SOP_MAX_PAGE_NUMBER)]
SopPageSize = Annotated[int, Query(ge=SOP_MIN_PAGE_SIZE, le=SOP_MAX_PAGE_SIZE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageMeta(StrictModel):
    page: int = Field(ge=SOP_MIN_PAGE_NUMBER, le=SOP_MAX_PAGE_NUMBER)
    page_size: int = Field(ge=SOP_MIN_PAGE_SIZE, le=SOP_MAX_PAGE_SIZE)
    total_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class SopPage[PageItem: BaseModel](StrictModel):
    data: list[PageItem]
    page: PageMeta


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand_standard_name: str = Field(min_length=1, max_length=200)
    brand_profile: dict[str, Any] = Field(default_factory=dict)
    target_platforms: list[dict[str, Any] | str] = Field(default_factory=list)
    success_definition: list[dict[str, Any] | str] = Field(default_factory=list)


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand_standard_name: str | None = Field(default=None, min_length=1, max_length=200)
    brand_profile: dict[str, Any] | None = None
    target_platforms: list[dict[str, Any] | str] | None = None
    success_definition: list[dict[str, Any] | str] | None = None
    status: Literal["active", "archived"] | None = None


class ProjectView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    name: str
    brand_standard_name: str
    brand_profile: dict[str, Any]
    target_platforms: list[Any]
    success_definition: list[Any]
    status: str
    created_by_pub_id: str
    created_at: datetime
    updated_at: datetime


class QuerySetCreate(StrictModel):
    note: str = ""


class QuerySetView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    version_no: int
    note: str
    status: str
    frozen_at: datetime | None
    created_at: datetime
    item_count: int | None = None


class QueryItemCreate(StrictModel):
    query_text: str = Field(min_length=1)
    layer: Literal["A", "B", "C", "D", "E", "F", "G"]
    contains_brand: bool = False
    intent: str = ""
    persona: str = ""
    decision_stage: str = ""
    expected_facts: str = ""
    priority: Literal["P0", "P1", "P2"] = "P1"


class QueryItemsCreate(StrictModel):
    items: list[QueryItemCreate] = Field(min_length=1, max_length=500)


class QueryItemView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    query_set_pub_id: str
    ordinal: int
    query_text: str
    layer: str
    contains_brand: bool
    intent: str
    persona: str
    decision_stage: str
    expected_facts: str
    priority: str
    created_at: datetime


class AnswerCreateBase(StrictModel):
    query_item_pub_id: str
    sample_index: int = Field(default=1, ge=1)
    platform: str = Field(min_length=1)
    region: str = ""
    account_label: str = ""
    mode: str = ""
    asked_at: datetime
    capture_status: CaptureStatus
    answer_text: str = ""
    reasoning_summary: str = ""
    search_terms: list[Any] = Field(default_factory=list)
    search_results: list[Any] = Field(default_factory=list)
    citations: list[Any] = Field(default_factory=list)
    brand_mentioned: bool | None = None
    mention_context: str = ""
    key_facts: list[Any] = Field(default_factory=list)
    evidence_ref: str = ""
    note: str = ""


class BaselineAnswerCreate(AnswerCreateBase):
    pass


class BaselineAnswerView(AnswerCreateBase):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    created_at: datetime


class InsightCreate(StrictModel):
    insight_type: Literal["query_rewrite", "source_selection", "answer_usage", "statistics", "note"]
    payload: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class InsightView(InsightCreate):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    created_at: datetime


class EvidenceCreate(StrictModel):
    claim_text: str = Field(min_length=1)
    source_name: str = ""
    source_url: str = ""
    source_level: Literal["official", "third_party", "experience"]
    verified_at: datetime | None = None
    can_prove: str = ""
    cannot_prove: str = ""
    allowed_public: bool = False
    evidence_ref: str = ""


class EvidenceUpdate(StrictModel):
    claim_text: str | None = Field(default=None, min_length=1)
    source_name: str | None = None
    source_url: str | None = None
    source_level: Literal["official", "third_party", "experience"] | None = None
    verified_at: datetime | None = None
    can_prove: str | None = None
    cannot_prove: str | None = None
    allowed_public: bool | None = None
    evidence_ref: str | None = None


class EvidenceView(EvidenceCreate):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    created_at: datetime
    updated_at: datetime


class OpportunityCreate(StrictModel):
    target_query: str = Field(min_length=1)
    current_gap: str = ""
    current_sources: list[Any] = Field(default_factory=list)
    brand_material: str = ""
    needed_evidence: str = ""
    recommended_platform: str = ""
    expected_change: str = ""


class OpportunityUpdate(StrictModel):
    target_query: str | None = Field(default=None, min_length=1)
    current_gap: str | None = None
    current_sources: list[Any] | None = None
    brand_material: str | None = None
    needed_evidence: str | None = None
    recommended_platform: str | None = None
    expected_change: str | None = None
    status: Literal["candidate", "selected", "rejected", "fulfilled"] | None = None


class OpportunityView(OpportunityCreate):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class ArticleCreate(StrictModel):
    title: str = Field(min_length=1)
    opportunity_pub_id: str | None = None


class ArticleUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1)
    status: Literal["draft", "in_review", "ready", "published", "archived"] | None = None


class ArticleView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    opportunity_pub_id: str | None
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    version_count: int | None = None
    latest_version_no: int | None = None
    maturity_level: str | None = None


class ArticleVersionCreate(StrictModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    change_note: str = ""


class ArticleVersionUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1)
    change_note: str | None = None
    readiness_checklist: dict[str, Any] | None = None
    publication_ready: bool | None = None


class ArticleVersionView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    article_pub_id: str
    version_no: int
    title: str
    body: str | None = None
    body_sha256: str
    change_note: str
    readiness_checklist: dict[str, Any]
    publication_ready: bool
    created_at: datetime
    check_count: int | None = None


class CheckCreate(StrictModel):
    check_type: Literal[
        "ai_dialogue",
        "fact_verification",
        "readability",
        "extractability",
        "title_match",
        "entity_disambiguation",
        "source_completeness",
        "keyword_stuffing",
        "compliance",
        "rag_recall",
        "synonym_test",
        "other",
    ]
    result: Literal["pass", "warn", "fail"]
    findings: str = ""
    checked_by: str = ""
    checked_at: datetime


class CheckView(CheckCreate):
    pub_id: str
    tenant_pub_id: str
    article_version_pub_id: str
    created_at: datetime


class ArticleVersionDetail(ArticleVersionView):
    body: str


class PublicationCreate(StrictModel):
    platform: str = Field(min_length=1)
    account_label: str = ""
    submitted_at: datetime | None = None


class PublicationUpdate(StrictModel):
    status: (
        Literal[
            "submitted",
            "reviewing",
            "published",
            "public",
            "rejected",
            "withdrawn",
            "login_only",
        ]
        | None
    ) = None
    public_url: str | None = None
    content_id: str | None = None
    published_at: datetime | None = None
    public_checked_at: datetime | None = None
    public_http_status: int | None = Field(default=None, ge=100, le=599)
    evidence: dict[str, Any] | None = None
    note: str | None = None


class PublicationView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    article_version_pub_id: str
    platform: str
    account_label: str
    title: str
    body_sha256: str
    status: str
    public_url: str
    content_id: str
    submitted_at: datetime | None
    published_at: datetime | None
    public_checked_at: datetime | None
    public_http_status: int | None
    evidence: dict[str, Any]
    note: str
    created_at: datetime
    updated_at: datetime


class ObservationCreate(StrictModel):
    checkpoint: Literal["immediate", "h24", "d3", "d7", "d14", "custom"]
    checkpoint_label: str = ""
    observed_at: datetime
    page_accessible: bool | None = None
    search_engine_indexed: bool | None = None
    platform_search_visible: bool | None = None
    ai_retrieved: bool | None = None
    ai_cited: bool | None = None
    note: str = ""


class ObservationView(ObservationCreate):
    pub_id: str
    tenant_pub_id: str
    publication_pub_id: str
    created_at: datetime


class PublicationDetail(PublicationView):
    retest_count: int
    comparison_count: int


class RetestAnswerCreate(AnswerCreateBase):
    article_appeared: bool | None = None
    article_position: int | None = Field(default=None, ge=1)
    article_cited: bool | None = None
    citation_position: int | None = Field(default=None, ge=1)
    brand_attribution_correct: bool | None = None
    new_facts: list[Any] = Field(default_factory=list)
    errors_introduced: str = ""


class RetestAnswerView(RetestAnswerCreate):
    pub_id: str
    tenant_pub_id: str
    publication_pub_id: str
    created_at: datetime


class ComparisonCreate(StrictModel):
    query_item_pub_id: str
    baseline_answer_pub_id: str | None = None
    retest_answer_pub_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    new_info_location: str = ""
    from_article_confidence: Literal["high", "medium", "low", "none"] = "none"
    attribution_correct: bool | None = None
    conclusion: str = ""
    next_actions: list[Any] = Field(default_factory=list)


class ComparisonView(ComparisonCreate):
    pub_id: str
    tenant_pub_id: str
    publication_pub_id: str
    created_at: datetime
    updated_at: datetime


class ExperimentCreate(StrictModel):
    hypothesis: str = Field(min_length=1)
    change_description: str = ""
    controlled_conditions: dict[str, Any] = Field(default_factory=dict)
    query_set_pub_id: str | None = None
    observation_window: str = ""


class ExperimentUpdate(StrictModel):
    hypothesis: str | None = Field(default=None, min_length=1)
    change_description: str | None = None
    controlled_conditions: dict[str, Any] | None = None
    query_set_pub_id: str | None = None
    observation_window: str | None = None
    result: str | None = None
    next_step: str | None = None
    status: Literal["planned", "running", "done", "abandoned"] | None = None


class ExperimentView(ExperimentCreate):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    result: str
    next_step: str
    status: str
    created_at: datetime
    updated_at: datetime


class WorkLogCreate(StrictModel):
    entry_type: Literal["progress", "failure", "blocker", "decision", "note"]
    failure_class: (
        Literal[
            "captcha",
            "login_wall",
            "no_retrieval",
            "sources_unloaded",
            "not_public",
            "not_indexed",
            "not_cited",
            "wrong_attribution",
            "over_extrapolation",
            "other",
        ]
        | None
    ) = None
    content: str = Field(min_length=1)


class WorkLogView(WorkLogCreate):
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    actor_pub_id: str
    created_at: datetime


class StepView(StrictModel):
    key: str
    stage: str
    name: str
    status: Literal["done", "in_progress", "empty"]
    metrics: dict[str, Any]


class DashboardArticle(StrictModel):
    article_pub_id: str
    title: str
    status: str
    version_count: int
    publication_ready: bool
    has_publication: bool
    maturity_level: Literal["L0", "L1", "L2", "L3", "L4"]


class DashboardView(StrictModel):
    project: ProjectView
    steps: list[StepView]
    articles: SopPage[DashboardArticle]


class ComparisonQueryView(StrictModel):
    query_item_pub_id: str
    query_text: str
    baseline_mentioned: bool | None
    retest_mentioned: bool | None
    article_appeared: bool | None
    article_cited: bool | None
    from_article_confidence: str | None


class ComparisonSummaryView(StrictModel):
    project_pub_id: str
    retrieval: dict[str, Any]
    citation: dict[str, Any]
    brand: dict[str, Any]
    answer: dict[str, Any]
    per_query: list[ComparisonQueryView]


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _service() -> SopService:
    return SopService(dsn=_dsn())


def _official_consumer() -> OfficialMetricsConsumer:
    return OfficialMetricsConsumer(MetricsV2Repository(_dsn()))


@contextmanager
def _service_errors() -> Iterator[None]:
    try:
        yield
    except SopNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found"}) from exc
    except SopInvalidState as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_state"}) from exc


def _echo_idempotency(response: Response, key: str | None) -> None:
    if key is not None:
        response.headers["Idempotency-Key"] = key


def _write_fields(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True, exclude_none=True)


def _page(
    result: SopPageResult,
    *,
    validator: Callable[[Mapping[str, Any]], BaseModel],
) -> dict[str, Any]:
    data = [validator(row) for row in result.data]
    return {
        "data": data,
        "page": {
            "page": result.page.page,
            "page_size": result.page.page_size,
            "total_count": result.page.total_count,
            "total_pages": result.page.total_pages,
        },
    }


def _reject_legacy_pagination(request: Request) -> None:
    if "cursor" in request.query_params or "limit" in request.query_params:
        raise HTTPException(status_code=422, detail={"code": "legacy_pagination_removed"})


router.dependencies.append(Depends(_reject_legacy_pagination))


@router.post("/projects", response_model=ProjectView, status_code=201)
def create_project(
    body: ProjectCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ProjectView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_project(
            tenant_pub_id=principal.tenant_pub_id,
            created_by_pub_id=principal.actor_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ProjectView.model_validate(row)


@router.get("/projects", response_model=SopPage[ProjectView])
def list_projects(
    status: Literal["active", "archived"] | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    rows = _service().list_projects(
        tenant_pub_id=principal.tenant_pub_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return _page(rows, validator=ProjectView.model_validate)


@router.get("/projects/{project_pub_id}", response_model=ProjectView)
def get_project(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ProjectView:
    principal.require("sop:read")
    with _service_errors():
        row = _service().get_project(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
        )
    return ProjectView.model_validate(row)


@router.patch("/projects/{project_pub_id}", response_model=ProjectView)
def update_project(
    project_pub_id: str,
    body: ProjectUpdate,
    principal: Principal = Depends(get_principal),
) -> ProjectView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_project(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            fields=_write_fields(body),
        )
    return ProjectView.model_validate(row)


@router.get("/projects/{project_pub_id}/dashboard", response_model=DashboardView)
def get_dashboard(
    project_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> DashboardView:
    principal.require("sop:read")
    with _service_errors():
        row = _service().dashboard(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            article_page=page,
            article_page_size=page_size,
        )
    articles = row.get("articles")
    if not isinstance(articles, SopPageResult):
        raise HTTPException(status_code=500, detail={"code": "invalid_dashboard_page"})
    return DashboardView.model_validate(
        {**row, "articles": _page(articles, validator=DashboardArticle.model_validate)}
    )


@router.post(
    "/projects/{project_pub_id}/query-sets",
    response_model=QuerySetView,
    status_code=201,
)
def create_query_set(
    project_pub_id: str,
    body: QuerySetCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> QuerySetView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_query_set(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            note=body.note,
        )
    _echo_idempotency(response, idempotency_key)
    return QuerySetView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/query-sets",
    response_model=SopPage[QuerySetView],
)
def list_query_sets(
    project_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_query_sets(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=QuerySetView.model_validate)


@router.post(
    "/query-sets/{query_set_pub_id}/items",
    response_model=list[QueryItemView],
    status_code=201,
)
def add_query_items(
    query_set_pub_id: str,
    body: QueryItemsCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> list[QueryItemView]:
    principal.require("sop:write")
    with _service_errors():
        rows = _service().add_query_items(
            tenant_pub_id=principal.tenant_pub_id,
            query_set_pub_id=query_set_pub_id,
            items=[item.model_dump() for item in body.items],
        )
    _echo_idempotency(response, idempotency_key)
    return [QueryItemView.model_validate(row) for row in rows]


@router.post("/query-sets/{query_set_pub_id}/freeze", response_model=QuerySetView)
def freeze_query_set(
    query_set_pub_id: str,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> QuerySetView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().freeze_query_set(
            tenant_pub_id=principal.tenant_pub_id,
            query_set_pub_id=query_set_pub_id,
        )
    _echo_idempotency(response, idempotency_key)
    return QuerySetView.model_validate(row)


@router.get(
    "/query-sets/{query_set_pub_id}/items",
    response_model=SopPage[QueryItemView],
)
def list_query_items(
    query_set_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_query_items(
            tenant_pub_id=principal.tenant_pub_id,
            query_set_pub_id=query_set_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=QueryItemView.model_validate)


@router.post(
    "/projects/{project_pub_id}/baseline-answers",
    response_model=BaselineAnswerView,
    status_code=201,
)
def create_baseline_answer(
    project_pub_id: str,
    body: BaselineAnswerCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> BaselineAnswerView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_baseline_answer(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return BaselineAnswerView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/baseline-answers",
    response_model=SopPage[BaselineAnswerView],
)
def list_baseline_answers(
    project_pub_id: str,
    query_item_pub_id: str | None = None,
    platform: str | None = None,
    capture_status: CaptureStatus | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_baseline_answers(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            query_item_pub_id=query_item_pub_id,
            platform=platform,
            capture_status=capture_status,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=BaselineAnswerView.model_validate)


@router.post(
    "/projects/{project_pub_id}/insights",
    response_model=InsightView,
    status_code=201,
)
def create_insight(
    project_pub_id: str,
    body: InsightCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> InsightView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_insight(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return InsightView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/insights",
    response_model=SopPage[InsightView],
)
def list_insights(
    project_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_insights(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=InsightView.model_validate)


@router.post(
    "/projects/{project_pub_id}/evidence",
    response_model=EvidenceView,
    status_code=201,
)
def create_evidence(
    project_pub_id: str,
    body: EvidenceCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> EvidenceView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_evidence(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return EvidenceView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/evidence",
    response_model=SopPage[EvidenceView],
)
def list_evidence(
    project_pub_id: str,
    source_level: Literal["official", "third_party", "experience"] | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_evidence(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            source_level=source_level,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=EvidenceView.model_validate)


@router.patch("/evidence/{evidence_pub_id}", response_model=EvidenceView)
def update_evidence(
    evidence_pub_id: str,
    body: EvidenceUpdate,
    principal: Principal = Depends(get_principal),
) -> EvidenceView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_evidence(
            tenant_pub_id=principal.tenant_pub_id,
            evidence_pub_id=evidence_pub_id,
            fields=_write_fields(body),
        )
    return EvidenceView.model_validate(row)


@router.post(
    "/projects/{project_pub_id}/opportunities",
    response_model=OpportunityView,
    status_code=201,
)
def create_opportunity(
    project_pub_id: str,
    body: OpportunityCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> OpportunityView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_opportunity(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return OpportunityView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/opportunities",
    response_model=SopPage[OpportunityView],
)
def list_opportunities(
    project_pub_id: str,
    status: Literal["candidate", "selected", "rejected", "fulfilled"] | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_opportunities(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=OpportunityView.model_validate)


@router.patch("/opportunities/{opportunity_pub_id}", response_model=OpportunityView)
def update_opportunity(
    opportunity_pub_id: str,
    body: OpportunityUpdate,
    principal: Principal = Depends(get_principal),
) -> OpportunityView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_opportunity(
            tenant_pub_id=principal.tenant_pub_id,
            opportunity_pub_id=opportunity_pub_id,
            fields=_write_fields(body),
        )
    return OpportunityView.model_validate(row)


@router.post(
    "/projects/{project_pub_id}/articles",
    response_model=ArticleView,
    status_code=201,
)
def create_article(
    project_pub_id: str,
    body: ArticleCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ArticleView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_article(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ArticleView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/articles",
    response_model=SopPage[ArticleView],
)
def list_articles(
    project_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_articles(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=ArticleView.model_validate)


@router.get("/articles/{article_pub_id}", response_model=ArticleView)
def get_article(
    article_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ArticleView:
    principal.require("sop:read")
    with _service_errors():
        row = _service().get_article(
            tenant_pub_id=principal.tenant_pub_id,
            article_pub_id=article_pub_id,
        )
    return ArticleView.model_validate(row)


@router.patch("/articles/{article_pub_id}", response_model=ArticleView)
def update_article(
    article_pub_id: str,
    body: ArticleUpdate,
    principal: Principal = Depends(get_principal),
) -> ArticleView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_article(
            tenant_pub_id=principal.tenant_pub_id,
            article_pub_id=article_pub_id,
            fields=_write_fields(body),
        )
    return ArticleView.model_validate(row)


@router.post(
    "/articles/{article_pub_id}/versions",
    response_model=ArticleVersionView,
    status_code=201,
)
def create_article_version(
    article_pub_id: str,
    body: ArticleVersionCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ArticleVersionView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_article_version(
            tenant_pub_id=principal.tenant_pub_id,
            article_pub_id=article_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ArticleVersionView.model_validate(row)


@router.get(
    "/articles/{article_pub_id}/versions",
    response_model=SopPage[ArticleVersionView],
)
def list_article_versions(
    article_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_article_versions(
            tenant_pub_id=principal.tenant_pub_id,
            article_pub_id=article_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=ArticleVersionView.model_validate)


@router.get("/article-versions/{version_pub_id}", response_model=ArticleVersionDetail)
def get_article_version(
    version_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ArticleVersionDetail:
    principal.require("sop:read")
    with _service_errors():
        row = _service().get_article_version(
            tenant_pub_id=principal.tenant_pub_id,
            version_pub_id=version_pub_id,
        )
    return ArticleVersionDetail.model_validate(row)


@router.patch("/article-versions/{version_pub_id}", response_model=ArticleVersionView)
def update_article_version(
    version_pub_id: str,
    body: ArticleVersionUpdate,
    principal: Principal = Depends(get_principal),
) -> ArticleVersionView:
    principal.require("sop:write")
    fields = _write_fields(body)
    checklist = fields.pop("readiness_checklist", None)
    with _service_errors():
        row = _service().update_article_version(
            tenant_pub_id=principal.tenant_pub_id,
            version_pub_id=version_pub_id,
            readiness_checklist=checklist,
            fields=fields,
        )
    return ArticleVersionView.model_validate(row)


@router.post(
    "/article-versions/{version_pub_id}/checks",
    response_model=CheckView,
    status_code=201,
)
def create_check(
    version_pub_id: str,
    body: CheckCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> CheckView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_check(
            tenant_pub_id=principal.tenant_pub_id,
            version_pub_id=version_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return CheckView.model_validate(row)


@router.get(
    "/article-versions/{version_pub_id}/checks",
    response_model=SopPage[CheckView],
)
def list_checks(
    version_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_checks(
            tenant_pub_id=principal.tenant_pub_id,
            version_pub_id=version_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=CheckView.model_validate)


@router.post(
    "/article-versions/{version_pub_id}/publications",
    response_model=PublicationView,
    status_code=201,
)
def create_publication(
    version_pub_id: str,
    body: PublicationCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> PublicationView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_publication(
            tenant_pub_id=principal.tenant_pub_id,
            version_pub_id=version_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return PublicationView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/publications",
    response_model=SopPage[PublicationView],
)
def list_publications(
    project_pub_id: str,
    status: str | None = None,
    platform: str | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_publications(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            status=status,
            platform=platform,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=PublicationView.model_validate)


@router.get("/publications/{publication_pub_id}", response_model=PublicationDetail)
def get_publication(
    publication_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> PublicationDetail:
    principal.require("sop:read")
    with _service_errors():
        row = _service().get_publication(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
        )
    return PublicationDetail.model_validate(row)


@router.patch("/publications/{publication_pub_id}", response_model=PublicationView)
def update_publication(
    publication_pub_id: str,
    body: PublicationUpdate,
    principal: Principal = Depends(get_principal),
) -> PublicationView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_publication(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            fields=_write_fields(body),
        )
    return PublicationView.model_validate(row)


@router.post(
    "/publications/{publication_pub_id}/observations",
    response_model=ObservationView,
    status_code=201,
)
def create_observation(
    publication_pub_id: str,
    body: ObservationCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ObservationView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_observation(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ObservationView.model_validate(row)


@router.get(
    "/publications/{publication_pub_id}/observations",
    response_model=SopPage[ObservationView],
)
def list_observations(
    publication_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_observations(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=ObservationView.model_validate)


@router.post(
    "/publications/{publication_pub_id}/retest-answers",
    response_model=RetestAnswerView,
    status_code=201,
)
def create_retest_answer(
    publication_pub_id: str,
    body: RetestAnswerCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> RetestAnswerView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_retest_answer(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return RetestAnswerView.model_validate(row)


@router.get(
    "/publications/{publication_pub_id}/retest-answers",
    response_model=SopPage[RetestAnswerView],
)
def list_retest_answers(
    publication_pub_id: str,
    query_item_pub_id: str | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_retest_answers(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            query_item_pub_id=query_item_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=RetestAnswerView.model_validate)


@router.post(
    "/publications/{publication_pub_id}/comparisons",
    response_model=ComparisonView,
    status_code=201,
)
def upsert_comparison(
    publication_pub_id: str,
    body: ComparisonCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ComparisonView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().upsert_comparison(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ComparisonView.model_validate(row)


@router.get(
    "/publications/{publication_pub_id}/comparisons",
    response_model=SopPage[ComparisonView],
)
def list_comparisons(
    publication_pub_id: str,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_comparisons(
            tenant_pub_id=principal.tenant_pub_id,
            publication_pub_id=publication_pub_id,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=ComparisonView.model_validate)


@router.get(
    "/projects/{project_pub_id}/metrics/official",
    response_model=None,
    operation_id="getSopOfficialMetricsV2",
)
def get_official_metrics(
    project_pub_id: str,
    start: date,
    end: date,
    purpose: Literal["baseline", "goal", "retest"] = "goal",
    focal_entity_id: list[str] | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Bind a SOP baseline, goal, or retest view to one official V2 set."""

    principal.require("sop:read")
    try:
        result = _official_consumer().overview(
            OfficialScope(
                tenant_pub_id=principal.tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                focal_entity_ids=tuple(focal_entity_id or ()),
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "official_metric_snapshot_set_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_official_metric_scope"}
        ) from exc
    return {**result, "schema_version": "sop-official-metrics-v2", "purpose": purpose}


@router.get(
    "/projects/{project_pub_id}/metrics/before-after",
    response_model=None,
    operation_id="getSopOfficialBeforeAfterV2",
)
def get_official_before_after(
    project_pub_id: str,
    retest_start: date,
    retest_end: date,
    focal_entity_id: list[str] | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """SOP effect view with fail-closed common-support compatibility."""

    principal.require("sop:read")
    try:
        result = _official_consumer().delta(
            OfficialScope(
                tenant_pub_id=principal.tenant_pub_id,
                project_pub_id=project_pub_id,
                start=retest_start,
                end=retest_end,
                focal_entity_ids=tuple(focal_entity_id or ()),
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "official_metric_snapshot_set_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_official_metric_scope"}
        ) from exc
    return {**result, "schema_version": "sop-official-before-after-v2"}


@router.get(
    "/projects/{project_pub_id}/comparison-summary",
    response_model=ComparisonSummaryView,
    deprecated=True,
    tags=["sop-legacy"],
)
def get_comparison_summary(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ComparisonSummaryView:
    principal.require("sop:read")
    with _service_errors():
        row = _service().comparison_summary(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
        )
    return ComparisonSummaryView.model_validate(row)


@router.post(
    "/projects/{project_pub_id}/experiments",
    response_model=ExperimentView,
    status_code=201,
)
def create_experiment(
    project_pub_id: str,
    body: ExperimentCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> ExperimentView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_experiment(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            fields=body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return ExperimentView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/experiments",
    response_model=SopPage[ExperimentView],
)
def list_experiments(
    project_pub_id: str,
    status: Literal["planned", "running", "done", "abandoned"] | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_experiments(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            status=status,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=ExperimentView.model_validate)


@router.patch("/experiments/{experiment_pub_id}", response_model=ExperimentView)
def update_experiment(
    experiment_pub_id: str,
    body: ExperimentUpdate,
    principal: Principal = Depends(get_principal),
) -> ExperimentView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().update_experiment(
            tenant_pub_id=principal.tenant_pub_id,
            experiment_pub_id=experiment_pub_id,
            fields=_write_fields(body),
        )
    return ExperimentView.model_validate(row)


@router.post(
    "/projects/{project_pub_id}/work-logs",
    response_model=WorkLogView,
    status_code=201,
)
def create_work_log(
    project_pub_id: str,
    body: WorkLogCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> WorkLogView:
    principal.require("sop:write")
    with _service_errors():
        row = _service().create_work_log(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            actor_pub_id=principal.actor_pub_id,
            **body.model_dump(),
        )
    _echo_idempotency(response, idempotency_key)
    return WorkLogView.model_validate(row)


@router.get(
    "/projects/{project_pub_id}/work-logs",
    response_model=SopPage[WorkLogView],
)
def list_work_logs(
    project_pub_id: str,
    entry_type: Literal["progress", "failure", "blocker", "decision", "note"] | None = None,
    page: SopPageNumber = SOP_DEFAULT_PAGE_NUMBER,
    page_size: SopPageSize = SOP_DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("sop:read")
    with _service_errors():
        rows = _service().list_work_logs(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            entry_type=entry_type,
            page=page,
            page_size=page_size,
        )
    return _page(rows, validator=WorkLogView.model_validate)
