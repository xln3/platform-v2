# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .service import ReportService

router = APIRouter(prefix="/api/v2/reports", tags=["reports"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportCreate(StrictModel):
    project_pub_id: str
    title: str
    window_start: datetime
    window_end: datetime
    filters: dict[str, Any]
    metric_version: str
    scorer_version: str
    fact_rows: list[dict[str, Any]]
    components: list[dict[str, Any]]
    workflow_operation_id: str | None = None


class ReviewCreate(StrictModel):
    decision: str
    rationale: str


class CommentCreate(StrictModel):
    body: str
    parent_pub_id: str | None = None


class ActionCreate(StrictModel):
    description: str
    owner_pub_id: str | None = None
    baseline: dict[str, Any]


class ReportSummary(StrictModel):
    pub_id: str
    project_pub_id: str
    title: str
    state: str
    created_at: datetime
    updated_at: datetime


class ReportPage(StrictModel):
    data: list[ReportSummary]
    page: dict[str, str | bool | None]


def _dsn() -> str:
    return get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")


def _service() -> ReportService:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return ReportService(
        dsn=_dsn(),
        evidence=EvidenceService(dsn=_dsn(), store=store),
    )


@router.post("", status_code=201)
def create_report(
    body: ReportCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:write")
    result = _service().produce(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=body.project_pub_id,
        title=body.title,
        window_start=body.window_start,
        window_end=body.window_end,
        filters=body.filters,
        metric_version=body.metric_version,
        scorer_version=body.scorer_version,
        fact_rows=body.fact_rows,
        sections=body.components,
        created_by_pub_id=principal.subject,
        provenance=RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("project:write",),
            adapter_version="reports-api-v1",
            capture_time=datetime.now(UTC),
            access_class=AccessClass.CUSTOMER_PRIVATE,
        ),
        workflow_operation_id=body.workflow_operation_id,
    )
    return {
        "report_pub_id": result["report_pub_id"],
        "report_version_pub_id": result["report_version_pub_id"],
        "state": result["state"],
        "artifacts": result["artifacts"],
        "fact_snapshot_hash": result["freeze"].fact_snapshot_hash,
    }


@router.post("/{report_pub_id}/versions/{version_pub_id}/reviews", status_code=201)
def review_report(
    report_pub_id: str,
    version_pub_id: str,
    body: ReviewCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    if body.decision not in {"approved", "changes_requested", "rejected"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_review_decision"})
    review_pub_id = _service().review(
        tenant_pub_id=principal.tenant_pub_id,
        report_pub_id=report_pub_id,
        version_pub_id=version_pub_id,
        reviewer_pub_id=principal.subject,
        decision=body.decision,
        rationale=body.rationale,
    )
    return {"review_pub_id": review_pub_id}


@router.post("/{report_pub_id}/versions/{version_pub_id}/publish", status_code=204)
def publish_report(
    report_pub_id: str,
    version_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    principal.require("project:write")
    try:
        _service().publish(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            reviewer_pub_id=principal.subject,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"code": "report_review_required"}) from exc
    return Response(status_code=204)


@router.post("/{report_pub_id}/versions/{version_pub_id}/comments", status_code=201)
def comment_report(
    report_pub_id: str,
    version_pub_id: str,
    body: CommentCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    comment_pub_id = _service().comment(
        tenant_pub_id=principal.tenant_pub_id,
        version_pub_id=version_pub_id,
        author_pub_id=principal.subject,
        body=body.body,
        parent_pub_id=body.parent_pub_id,
    )
    return {"comment_pub_id": comment_pub_id, "report_pub_id": report_pub_id}


@router.post("/{report_pub_id}/actions", status_code=201)
def create_action(
    report_pub_id: str,
    body: ActionCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:write")
    action_pub_id = _service().create_optimization_action(
        tenant_pub_id=principal.tenant_pub_id,
        report_pub_id=report_pub_id,
        description=body.description,
        owner_pub_id=body.owner_pub_id,
        baseline=body.baseline,
    )
    return {"action_pub_id": action_pub_id}


@router.get("", response_model=ReportPage)
def list_reports(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> ReportPage:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id,project_pub_id,title,state,created_at,updated_at
            FROM reporting.report
            WHERE tenant_pub_id=%s AND (%s::text IS NULL OR pub_id>%s::text)
            ORDER BY pub_id LIMIT %s
            """,
            (principal.tenant_pub_id, cursor, cursor, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    data = rows[:limit]
    return ReportPage(
        data=[ReportSummary(**dict(row)) for row in data],
        page={
            "next_cursor": data[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.get("/{report_pub_id}")
def report_detail(
    report_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        report = connection.execute(
            """
            SELECT pub_id,project_pub_id,title,state,created_at,updated_at
            FROM reporting.report WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, report_pub_id),
        ).fetchone()
        versions = connection.execute(
            """
            SELECT pub_id,version_number,window_start,window_end,filters,metric_version,
                   scorer_version,fact_snapshot_hash,status
            FROM reporting.report_version
            WHERE tenant_pub_id=%s AND report_pub_id=%s ORDER BY version_number
            """,
            (principal.tenant_pub_id, report_pub_id),
        ).fetchall()
    if report is None:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"})
    return {**dict(report), "versions": [dict(row) for row in versions]}
