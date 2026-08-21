# ruff: noqa: B008
"""Independent source-page analysis API."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .service import (
    SourceAnalysisInvalid,
    SourceAnalysisNotFound,
    SourceAnalysisNotReady,
    SourceAnalysisService,
)

router = APIRouter(prefix="/api/v2/source-analysis", tags=["source_analysis"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AliasEvidence(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    evidence_url: str | None = Field(default=None, max_length=2048)
    capture_pub_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def require_provenance(self) -> AliasEvidence:
        if not (self.evidence_url or self.capture_pub_id):
            raise ValueError("alias must carry evidence_url or capture_pub_id")
        return self


class AnchorSource(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    categories: list[str] = Field(min_length=1, max_length=30)


class LinkedEntity(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    relation: Literal["parent", "subsidiary", "sub_brand", "spokesperson", "product_line"]


class SourceProfileWrite(StrictModel):
    object_name: str = Field(min_length=1, max_length=200)
    object_kind: Literal["brand", "product"]
    categories: list[str] = Field(min_length=1, max_length=30)
    aliases: list[AliasEvidence] = Field(default_factory=list, max_length=50)
    own_domains: list[str] = Field(default_factory=list, max_length=30)
    peers: list[str] = Field(default_factory=list, max_length=100)
    anchor_sources: list[AnchorSource] = Field(default_factory=list, max_length=50)
    linked_entities: list[LinkedEntity] = Field(default_factory=list, max_length=100)
    hard_anchor_available: bool
    decision_mode: Literal["selection", "reputation"]

    @model_validator(mode="after")
    def require_declared_anchors(self) -> SourceProfileWrite:
        if self.hard_anchor_available and not self.anchor_sources:
            raise ValueError("hard_anchor_available requires anchor_sources")
        return self


class SourceProfileView(StrictModel):
    pub_id: str
    revision: int
    state: Literal["active", "retired"]
    object_name: str
    object_kind: Literal["brand", "product"]
    categories: list[str]
    aliases: list[AliasEvidence]
    own_domains: list[str]
    peers: list[str]
    anchor_sources: list[AnchorSource]
    linked_entities: list[LinkedEntity]
    hard_anchor_available: bool
    decision_mode: Literal["selection", "reputation"]
    profile_type: Literal["I", "II", "III", "IV"]
    profile_hash: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class RunInspectionRequest(StrictModel):
    profile_pub_id: str | None = Field(default=None, max_length=30)


class AnalysisJobView(StrictModel):
    pub_id: str
    run_pub_id: str
    profile_pub_id: str
    state: Literal["queued", "running", "completed", "partial", "failed", "skipped"]
    policy_version: str
    input_hash: str
    workflow_id: str
    created_at: datetime
    updated_at: datetime


class PageMeta(StrictModel):
    next_cursor: str | None
    has_more: bool


class InspectionSummary(StrictModel):
    pub_id: str
    run_pub_id: str
    source_document_pub_id: str
    url: str
    host: str
    page_title: str | None
    publisher: str | None
    authors: list[str]
    profile_pub_id: str
    profile_revision: int
    policy_version: str
    prompt_version: str
    model: str
    status: Literal["completed", "partial", "unverifiable"]
    page_summary: dict[str, Any]
    transmission: dict[str, Any]
    attribution: dict[str, Any]
    quality: dict[str, Any]
    finding_count: int
    statement_count: int
    exposure_count: int
    created_at: datetime
    updated_at: datetime


class InspectionPage(StrictModel):
    data: list[InspectionSummary]
    page: PageMeta


class EvidenceSpanView(StrictModel):
    pub_id: str
    chain_ordinal: int
    quote: str
    text_start: int
    text_end: int
    quote_hash: str
    verification: Literal["exact"]


class EvidenceChainLinkView(StrictModel):
    connector: Literal["because", "and", "but", "compared_with", "therefore"]
    fact_type: Literal["source_quote", "authority_fact", "recomputable", "absence"]
    explanation: str
    quote: str | None = None
    occurrence: int | None = None
    text_start: int | None = None
    text_end: int | None = None
    quote_hash: str | None = None
    authority_source: str | None = None
    authority_url: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    authority_category: str | None = None
    algorithm: str | None = None
    inputs: dict[str, Any] | list[dict[str, str]] | None = None
    result: Any | None = None
    search_terms: list[str] | None = None
    search_scope: str | None = None
    operator: Literal["any", "all"] | None = None
    match_count: int | None = None


class FindingView(StrictModel):
    pub_id: str
    ordinal: int
    code: Literal["A0", "A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "C1", "C2", "C3", "C4"]
    ledger: Literal["statement", "exposure"]
    variant: str
    finding_status: Literal["confirmed", "needs_review"]
    summary: str
    action: str
    evidence_chain: list[EvidenceChainLinkView]
    self_check: dict[str, Any]
    validation: dict[str, Any]
    spans: list[EvidenceSpanView]


class InspectionDetail(StrictModel):
    pub_id: str
    run_pub_id: str
    source_document_pub_id: str
    profile_pub_id: str
    profile_revision: int
    url: str
    host: str
    page_title: str | None
    site_name: str | None
    publisher: str | None
    authors: list[str]
    published_at: datetime | None
    published_at_confidence: str
    policy_version: str
    prompt_version: str
    model: str
    content_sha256: str
    status: Literal["completed", "partial", "unverifiable"]
    page_summary: dict[str, Any]
    transmission: dict[str, Any]
    attribution: dict[str, Any]
    quality: dict[str, Any]
    object_name: str
    object_kind: Literal["brand", "product"]
    categories: list[str]
    aliases: list[AliasEvidence]
    own_domains: list[str]
    peers: list[str]
    anchor_sources: list[AnchorSource]
    linked_entities: list[LinkedEntity]
    hard_anchor_available: bool
    decision_mode: Literal["selection", "reputation"]
    profile_type: Literal["I", "II", "III", "IV"]
    profile_hash: str
    findings: list[FindingView]
    created_at: datetime
    updated_at: datetime


def _service() -> SourceAnalysisService:
    settings = get_settings()
    dsn = (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    return SourceAnalysisService(dsn=dsn)


@contextmanager
def _errors():  # type: ignore[no-untyped-def]
    try:
        yield
    except SourceAnalysisNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found"}) from exc
    except SourceAnalysisInvalid as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile"}) from exc
    except SourceAnalysisNotReady as exc:
        raise HTTPException(status_code=409, detail={"code": "source_documents_not_ready"}) from exc


@router.put("/projects/{project_pub_id}/profile", response_model=SourceProfileView)
def put_profile(
    project_pub_id: str,
    body: SourceProfileWrite,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> SourceProfileView:
    principal.require("project:write")
    with _errors():
        row, created = _service().put_profile(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            created_by=principal.actor_pub_id,
            payload=body.model_dump(mode="json"),
        )
    response.status_code = 201 if created else 200
    return SourceProfileView.model_validate(row)


@router.get("/projects/{project_pub_id}/profile", response_model=SourceProfileView)
def get_profile(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> SourceProfileView:
    principal.require("project:read")
    with _errors():
        row = _service().get_active_profile(
            tenant_pub_id=principal.tenant_pub_id, project_pub_id=project_pub_id
        )
    return SourceProfileView.model_validate(row)


@router.get("/projects/{project_pub_id}/profiles", response_model=list[SourceProfileView])
def list_profiles(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> list[SourceProfileView]:
    principal.require("project:read")
    with _errors():
        rows = _service().list_profiles(
            tenant_pub_id=principal.tenant_pub_id, project_pub_id=project_pub_id
        )
    return [SourceProfileView.model_validate(row) for row in rows]


@router.post(
    "/projects/{project_pub_id}/runs/{run_pub_id}/inspect",
    response_model=AnalysisJobView,
    status_code=201,
)
def enqueue_run_inspection(
    project_pub_id: str,
    run_pub_id: str,
    body: RunInspectionRequest,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> AnalysisJobView:
    """Re-run page inspection for an existing source snapshot with a frozen profile."""

    principal.require("project:write")
    settings = get_settings()
    with _errors():
        row, created = _service().enqueue_run_inspection(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            run_pub_id=run_pub_id,
            profile_pub_id=body.profile_pub_id,
            task_queue=settings.analysis_temporal_task_queue,
            model=(settings.audit_llm_model or settings.research_llm_model).strip(),
        )
    response.status_code = 201 if created else 200
    return AnalysisJobView.model_validate(row)


@router.get("/projects/{project_pub_id}/inspections", response_model=InspectionPage)
def list_inspections(
    project_pub_id: str,
    run_pub_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> InspectionPage:
    principal.require("project:read")
    with _errors():
        rows = _service().list_inspections(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            run_pub_id=run_pub_id,
            cursor=cursor,
            limit=limit,
        )
    has_more = len(rows) > limit
    visible = rows[:limit]
    return InspectionPage(
        data=[InspectionSummary.model_validate(row) for row in visible],
        page=PageMeta(
            next_cursor=str(visible[-1]["pub_id"]) if has_more and visible else None,
            has_more=has_more,
        ),
    )


@router.get(
    "/projects/{project_pub_id}/inspections/{inspection_pub_id}",
    response_model=InspectionDetail,
)
def get_inspection(
    project_pub_id: str,
    inspection_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> InspectionDetail:
    principal.require("project:read")
    with _errors():
        row = _service().get_inspection(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            inspection_pub_id=inspection_pub_id,
        )
    return InspectionDetail.model_validate(row)


__all__ = ["router"]
