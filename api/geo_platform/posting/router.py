# ruff: noqa: B008
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .catalog import CatalogInvalid, RequestedTarget, resolve_targets
from .docx import DOCX_MIME, DocxInvalid, parse_docx
from .service import PostingInvalidState, PostingNotFound, PostingService

router = APIRouter(prefix="/api/v2/posting", tags=["posting"])

ProviderName = Literal[
    "prfabu",
    "toumeiw",
    "mtpfw",
    "meititejia",
    "meijiehezi",
    "pinda",
]
BatchStatus = Literal[
    "draft",
    "queued",
    "processing",
    "partially_submitted",
    "submitted",
    "published",
    "blocked",
    "failed",
    "canceled",
]
TargetStatus = Literal[
    "selected",
    "queued",
    "submitting",
    "submitted",
    "reviewing",
    "published",
    "balance_insufficient",
    "provider_session_expired",
    "provider_confirmation_required",
    "unsupported_provider",
    "rejected",
    "failed",
    "canceled",
]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetCreate(StrictModel):
    catalog_type: Literal["news", "wemedia"]
    provider: ProviderName
    media_name: str = Field(min_length=1, max_length=500)
    media_platform: str = Field(default="", max_length=160)


class TargetView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    batch_pub_id: str
    catalog_type: Literal["news", "wemedia"]
    provider: ProviderName
    media_name: str
    media_platform: str
    provider_media_id: str
    quoted_price: Decimal
    status: TargetStatus
    external_order_id: str
    public_url: str
    provider_message: str
    submitted_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EventView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    batch_pub_id: str
    target_pub_id: str | None
    event_type: str
    from_status: str
    to_status: str
    message: str
    payload: dict[str, Any]
    actor_pub_id: str
    created_at: datetime


class BatchView(StrictModel):
    pub_id: str
    tenant_pub_id: str
    source_filename: str
    source_sha256: str
    catalog_sha256: str
    title: str
    content_text: str
    image_count: int
    customer_name: str
    release_time: date | None
    auto_submit: bool
    spend_confirmed_at: datetime | None
    max_total_amount: Decimal | None
    quoted_total_amount: Decimal
    status: BatchStatus
    note: str
    sop_project_pub_id: str | None
    article_version_pub_id: str | None
    approval_state: Literal["draft", "pending", "approved", "rejected"]
    approval_requested_by_pub_id: str | None
    approved_by_pub_id: str | None
    approved_at: datetime | None
    created_by_pub_id: str
    created_at: datetime
    updated_at: datetime
    targets: list[TargetView]
    events: list[EventView]


class BatchSummary(StrictModel):
    pub_id: str
    tenant_pub_id: str
    source_filename: str
    source_sha256: str
    catalog_sha256: str
    title: str
    content_excerpt: str
    image_count: int
    customer_name: str
    release_time: date | None
    auto_submit: bool
    spend_confirmed_at: datetime | None
    max_total_amount: Decimal | None
    quoted_total_amount: Decimal
    status: BatchStatus
    note: str
    sop_project_pub_id: str | None
    article_version_pub_id: str | None
    approval_state: Literal["draft", "pending", "approved", "rejected"]
    approval_requested_by_pub_id: str | None
    approved_by_pub_id: str | None
    approved_at: datetime | None
    created_by_pub_id: str
    created_at: datetime
    updated_at: datetime
    target_count: int
    submitted_count: int
    published_count: int


class SubmitRequest(StrictModel):
    confirm_spend: Literal[True]
    max_total_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class ApprovalDecision(StrictModel):
    rationale: str = Field(min_length=4, max_length=1_000)


class TargetBackfill(StrictModel):
    status: Literal["submitted", "reviewing", "published", "rejected", "failed"]
    public_url: str = Field(default="", max_length=1_000, pattern=r"^(?:https?://[^\s]+)?$")
    provider_message: str = Field(min_length=2, max_length=1_000)


class AttributionCreate(StrictModel):
    target_pub_id: str | None = Field(default=None, pattern=r"^ptg_[A-Za-z0-9_-]+$")
    sop_publication_pub_id: str | None = Field(default=None, max_length=120)
    retest_run_pub_id: str | None = Field(default=None, max_length=120)
    public_url: str = Field(default="", max_length=1_000, pattern=r"^(?:https?://[^\s]+)?$")
    relation_type: Literal["published_as", "retested_by", "cited_by", "correlated_with"]
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=1_000)


class AttributionView(AttributionCreate):
    pub_id: str
    tenant_pub_id: str
    batch_pub_id: str
    created_by_pub_id: str
    created_at: datetime


def _service() -> PostingService:
    settings = get_settings()
    dsn = (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    return PostingService(dsn=dsn)


def _posting_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PostingNotFound):
        return HTTPException(status_code=404, detail={"code": "posting_not_found"})
    if isinstance(exc, DocxInvalid):
        return HTTPException(status_code=422, detail={"code": str(exc)})
    if isinstance(exc, CatalogInvalid):
        return HTTPException(status_code=409, detail={"code": str(exc)})
    return HTTPException(status_code=409, detail={"code": "posting_invalid_state"})


def _parse_targets(raw: str) -> list[TargetCreate]:
    try:
        value = json.loads(raw)
        return TypeAdapter(list[TargetCreate]).validate_python(value)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "posting_targets_invalid"},
        ) from exc


@router.post("/batches", response_model=BatchView, status_code=201)
async def create_batch(
    idempotency_key: IdempotencyKey,
    document: Annotated[UploadFile, File(description="上游产出的图文 DOCX")],
    targets_json: Annotated[str, Form(min_length=2, max_length=50_000)],
    title: Annotated[str, Form(max_length=300)] = "",
    customer_name: Annotated[str, Form(max_length=300)] = "",
    release_time: Annotated[date | None, Form()] = None,
    auto_submit: Annotated[bool, Form()] = False,
    confirm_spend: Annotated[bool, Form()] = False,
    max_total_amount: Annotated[
        Decimal | None,
        Form(gt=0, max_digits=12, decimal_places=2),
    ] = None,
    note: Annotated[str, Form(max_length=1_000)] = "",
    sop_project_pub_id: Annotated[str | None, Form(max_length=120)] = None,
    article_version_pub_id: Annotated[str | None, Form(max_length=120)] = None,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("account:operate")
    if document.content_type not in {DOCX_MIME, "application/octet-stream", None, ""}:
        raise HTTPException(status_code=415, detail={"code": "docx_content_type_required"})
    payload = await document.read()
    parsed_targets = _parse_targets(targets_json)
    try:
        parsed = parse_docx(payload, document.filename or "article.docx")
        catalog = resolve_targets(
            [
                RequestedTarget(
                    catalog_type=item.catalog_type,
                    provider=item.provider,
                    media_name=item.media_name,
                    media_platform=item.media_platform,
                )
                for item in parsed_targets
            ]
        )
        service = _service()
        batch, _created = service.create_batch(
            tenant_pub_id=principal.tenant_pub_id,
            actor_pub_id=principal.actor_pub_id,
            idempotency_key=idempotency_key,
            document=parsed,
            catalog=catalog,
            title=title.strip() or parsed.title,
            customer_name=customer_name.strip(),
            release_time=release_time,
            auto_submit=auto_submit,
            confirm_spend=confirm_spend,
            max_total_amount=max_total_amount,
            note=note.strip(),
            sop_project_pub_id=sop_project_pub_id,
            article_version_pub_id=article_version_pub_id,
        )
    except (DocxInvalid, CatalogInvalid, PostingInvalidState, PostingNotFound) as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(batch)


@router.get("/batches", response_model=list[BatchSummary])
def list_batches(
    status: BatchStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> list[BatchSummary]:
    principal.require("account:read")
    rows = _service().list_batches(
        tenant_pub_id=principal.tenant_pub_id,
        status=status,
        limit=limit,
    )
    return [BatchSummary.model_validate(row) for row in rows]


@router.get("/batches/{batch_pub_id}", response_model=BatchView)
def get_batch(
    batch_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("account:read")
    try:
        row = _service().get_batch(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
        )
    except PostingNotFound as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(row)


@router.post("/batches/{batch_pub_id}/submit", response_model=BatchView, status_code=202)
def submit_batch(
    batch_pub_id: str,
    body: SubmitRequest,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("account:operate")
    service = _service()
    try:
        row = service.enqueue_batch(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
            max_total_amount=body.max_total_amount,
        )
    except (PostingNotFound, PostingInvalidState) as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(row)


@router.post("/batches/{batch_pub_id}/approve", response_model=BatchView, status_code=202)
def approve_batch(
    batch_pub_id: str,
    body: ApprovalDecision,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("posting:approve")
    service = _service()
    try:
        row = service.decide_approval(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
            approve=True,
            rationale=body.rationale.strip(),
        )
    except (PostingNotFound, PostingInvalidState) as exc:
        raise _posting_error(exc) from exc
    background_tasks.add_task(
        service.execute_batch,
        tenant_pub_id=principal.tenant_pub_id,
        batch_pub_id=batch_pub_id,
        actor_pub_id=principal.actor_pub_id,
    )
    return BatchView.model_validate(row)


@router.post("/batches/{batch_pub_id}/reject", response_model=BatchView)
def reject_batch(
    batch_pub_id: str,
    body: ApprovalDecision,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("posting:approve")
    try:
        row = _service().decide_approval(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
            approve=False,
            rationale=body.rationale.strip(),
        )
    except (PostingNotFound, PostingInvalidState) as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(row)


@router.patch(
    "/batches/{batch_pub_id}/targets/{target_pub_id}",
    response_model=BatchView,
)
def backfill_target(
    batch_pub_id: str,
    target_pub_id: str,
    body: TargetBackfill,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("account:operate")
    try:
        row = _service().backfill_target(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            target_pub_id=target_pub_id,
            actor_pub_id=principal.actor_pub_id,
            status=body.status,
            public_url=body.public_url,
            provider_message=body.provider_message.strip(),
        )
    except (PostingNotFound, PostingInvalidState) as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(row)


@router.post(
    "/batches/{batch_pub_id}/attributions",
    response_model=AttributionView,
    status_code=201,
)
def create_attribution(
    batch_pub_id: str,
    body: AttributionCreate,
    principal: Principal = Depends(get_principal),
) -> AttributionView:
    principal.require("account:operate")
    try:
        row = _service().create_attribution(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            target_pub_id=body.target_pub_id,
            sop_publication_pub_id=body.sop_publication_pub_id,
            retest_run_pub_id=body.retest_run_pub_id,
            public_url=body.public_url,
            relation_type=body.relation_type,
            evidence_sha256=body.evidence_sha256,
            note=body.note.strip(),
            actor_pub_id=principal.actor_pub_id,
        )
    except PostingNotFound as exc:
        raise _posting_error(exc) from exc
    return AttributionView.model_validate(row)


@router.get("/batches/{batch_pub_id}/attributions", response_model=list[AttributionView])
def list_attributions(
    batch_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> list[AttributionView]:
    principal.require("account:read")
    return [
        AttributionView.model_validate(row)
        for row in _service().list_attributions(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
        )
    ]


@router.post("/batches/{batch_pub_id}/refresh", response_model=BatchView)
def refresh_batch(
    batch_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> BatchView:
    principal.require("account:operate")
    try:
        row = _service().refresh_batch(
            tenant_pub_id=principal.tenant_pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
        )
    except PostingNotFound as exc:
        raise _posting_error(exc) from exc
    return BatchView.model_validate(row)
