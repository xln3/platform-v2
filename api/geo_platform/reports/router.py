# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.evidence.dlp import assert_secret_free
from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService

from ..config import get_settings
from ..identity.policy import Principal, Role, get_principal
from ..tenancy.database import get_db
from ..tenancy.models import Membership, Tenant, User
from ..tenancy.psycopg import tenant_connection
from ..tenancy.repository import set_tenant_context
from .service import ReportRevisionIdempotencyConflict, ReportRevisionIncomplete, ReportService

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


class ReportRevisionCreate(StrictModel):
    components: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ReviewCreate(StrictModel):
    decision: str
    rationale: str


class DeliveryCreate(StrictModel):
    recipient_pub_id: str


class DeliveryConfirm(StrictModel):
    confirmation_comment: str


class DeliveryView(StrictModel):
    pub_id: str
    report_pub_id: str
    recipient_pub_id: str
    delivered_at: datetime
    confirmed_at: datetime | None
    confirmation_comment: str | None


class CommentCreate(StrictModel):
    body: str
    parent_pub_id: str | None = None


class ActionCreate(StrictModel):
    description: str
    owner_pub_id: str | None = None
    baseline: dict[str, Any]


class ActionUpdate(StrictModel):
    state: str
    outcome: dict[str, Any] | None = None


class EffectRetestCreate(StrictModel):
    measured_at: datetime
    result: dict[str, Any]


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


class ReportComponentView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    component_type: str
    ordinal: int
    payload: dict[str, Any]
    source: str
    created_at: datetime


class ReportFrozenFactView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    ordinal: int
    payload: dict[str, Any]
    payload_hash: str
    created_at: datetime


class ReportArtifactView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    format: str
    evidence_pub_id: str
    mime_type: str
    byte_size: int
    sha256: str
    created_at: datetime


class ReportEvidenceBindingView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    evidence_pub_id: str
    purpose: str
    kind: str
    access_class: str
    mime_type: str
    byte_size: int
    sha256: str
    anchor_count: int
    capture_time: datetime
    created_at: datetime


class ReportReviewView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    reviewer_pub_id: str
    decision: str
    rationale: str
    created_at: datetime


class ReportCommentView(StrictModel):
    pub_id: str
    report_version_pub_id: str
    parent_pub_id: str | None
    author_pub_id: str
    body: str
    resolved_at: datetime | None
    created_at: datetime


class ReportEventView(StrictModel):
    pub_id: str
    report_version_pub_id: str | None
    event_type: str
    actor_pub_id: str
    data: dict[str, Any]
    created_at: datetime


class ReportVersionView(StrictModel):
    pub_id: str
    version_number: int
    window_start: datetime
    window_end: datetime
    filters: dict[str, Any]
    metric_version: str
    scorer_version: str
    fact_snapshot_hash: str
    status: str
    components: list[ReportComponentView]
    frozen_facts: list[ReportFrozenFactView]
    artifacts: list[ReportArtifactView]
    evidence_bindings: list[ReportEvidenceBindingView]
    reviews: list[ReportReviewView]
    comments: list[ReportCommentView]
    events: list[ReportEventView]


class EffectRetestView(StrictModel):
    pub_id: str
    action_pub_id: str
    measured_at: datetime
    result: dict[str, Any]
    recorded_by_pub_id: str
    created_at: datetime


class OptimizationActionView(StrictModel):
    pub_id: str
    description: str
    owner_pub_id: str | None
    state: str
    baseline: dict[str, Any] | None
    outcome: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    effect_retests: list[EffectRetestView]


class ReportDetail(StrictModel):
    pub_id: str
    project_pub_id: str
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    versions: list[ReportVersionView]
    optimization_actions: list[OptimizationActionView]


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


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


def _active_customer_user_pub_id(
    session: Session,
    *,
    tenant_pub_id: str,
    user_pub_id: str,
) -> str:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail={"code": "delivery_recipient_not_found"})
    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    target = session.scalar(
        select(User.pub_id)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.tenant_id == tenant.id,
            Membership.role == Role.CUSTOMER.value,
            Membership.state == "active",
            Membership.revoked_at.is_(None),
            User.pub_id == user_pub_id,
            User.disabled_at.is_(None),
            User.is_service_account.is_(False),
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "delivery_recipient_not_found"})
    return target


def _require_customer_delivered_report(
    principal: Principal,
    *,
    report_pub_id: str,
    version_pub_id: str | None = None,
) -> None:
    if principal.role != Role.CUSTOMER:
        return
    with tenant_connection(_dsn(), principal.tenant_pub_id) as connection:
        allowed = connection.execute(
            """
            SELECT 1
            FROM reporting.report report
            JOIN reporting.report_delivery delivery
              ON delivery.tenant_pub_id=report.tenant_pub_id
             AND delivery.report_pub_id=report.pub_id
             AND delivery.recipient_pub_id=%s
            WHERE report.tenant_pub_id=%s
              AND report.pub_id=%s
              AND report.state='published'
              AND (
                %s::text IS NULL
                OR EXISTS (
                  SELECT 1
                  FROM reporting.report_version version
                  WHERE version.tenant_pub_id=report.tenant_pub_id
                    AND version.report_pub_id=report.pub_id
                    AND version.pub_id=%s
                    AND version.status='published'
                )
              )
            """,
            (
                principal.actor_pub_id,
                principal.tenant_pub_id,
                report_pub_id,
                version_pub_id,
                version_pub_id,
            ),
        ).fetchone()
    if allowed is None:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"})


@router.post("", status_code=201)
def create_report(
    body: ReportCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("report:write")
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
        created_by_pub_id=principal.actor_pub_id,
        provenance=RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("report:write",),
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


@router.post("/{report_pub_id}/versions", status_code=201)
def create_report_revision(
    report_pub_id: str,
    body: ReportRevisionCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("report:write")
    try:
        result = _service().create_revision(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            fact_rows=None,
            sections=body.components,
            created_by_pub_id=principal.actor_pub_id,
            provenance=RedactedProvenance(
                platform_account_pub_id=None,
                browser_profile_version_pub_id=None,
                session_event_pub_id=None,
                channel=CaptureChannel.API,
                authorization_scope=("report:write",),
                adapter_version="reports-api-v1",
                capture_time=datetime.now(UTC),
                access_class=AccessClass.CUSTOMER_PRIVATE,
            ),
            idempotency_key_hash=sha256(idempotency_key.encode()).hexdigest(),
        )
    except ReportRevisionIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc
    except ReportRevisionIncomplete as exc:
        raise HTTPException(
            status_code=409, detail={"code": "previous_revision_incomplete"}
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"code": "published_report_immutable"}) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "report_or_evidence_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_report_revision"}) from exc
    return {
        "report_pub_id": result["report_pub_id"],
        "report_version_pub_id": result["report_version_pub_id"],
        "version_number": result["version_number"],
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
    principal.require("report:review")
    if body.decision not in {"approved", "changes_requested", "rejected"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_review_decision"})
    try:
        review_pub_id = _service().review(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            reviewer_pub_id=principal.actor_pub_id,
            decision=body.decision,
            rationale=body.rationale,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from exc
    return {"review_pub_id": review_pub_id}


@router.post("/{report_pub_id}/versions/{version_pub_id}/publish", status_code=204)
def publish_report(
    report_pub_id: str,
    version_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    principal.require("report:publish")
    try:
        _service().publish(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            reviewer_pub_id=principal.actor_pub_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"code": "report_review_required"}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from exc
    return Response(status_code=204)


@router.post("/{report_pub_id}/deliveries", status_code=201)
def deliver_report(
    report_pub_id: str,
    body: DeliveryCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("report:deliver")
    try:
        assert_secret_free(body.recipient_pub_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "sensitive_input_rejected"}) from exc
    recipient_pub_id = _active_customer_user_pub_id(
        session,
        tenant_pub_id=principal.tenant_pub_id,
        user_pub_id=body.recipient_pub_id,
    )
    try:
        delivery_pub_id = _service().deliver(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            recipient_pub_id=recipient_pub_id,
            delivered_by_pub_id=principal.actor_pub_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"code": "report_not_published"}) from exc
    return {"delivery_pub_id": delivery_pub_id, "report_pub_id": report_pub_id}


@router.get("/{report_pub_id}/deliveries", response_model=list[DeliveryView])
def report_deliveries(
    report_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[DeliveryView]:
    principal.require("project:read")
    recipient_pub_id = None
    if principal.role == Role.CUSTOMER:
        recipient_pub_id = _active_customer_user_pub_id(
            session,
            tenant_pub_id=principal.tenant_pub_id,
            user_pub_id=principal.actor_pub_id,
        )
    try:
        rows = _service().list_deliveries(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            recipient_pub_id=recipient_pub_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from exc
    return [DeliveryView.model_validate(row) for row in rows]


@router.post("/{report_pub_id}/deliveries/{delivery_pub_id}/confirm")
def confirm_report_delivery(
    report_pub_id: str,
    delivery_pub_id: str,
    body: DeliveryConfirm,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    if principal.role != Role.CUSTOMER:
        raise HTTPException(
            status_code=403,
            detail={"code": "delivery_confirmation_customer_required"},
        )
    try:
        assert_secret_free(body.confirmation_comment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "sensitive_input_rejected"}) from exc
    try:
        _service().confirm_delivery(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            delivery_pub_id=delivery_pub_id,
            recipient_pub_id=principal.actor_pub_id,
            confirmation_comment=body.confirmation_comment,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "delivery_not_found"}) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "delivery_recipient_mismatch"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "confirmation_idempotency_conflict"}
        ) from exc
    return {"delivery_pub_id": delivery_pub_id, "state": "confirmed"}


@router.post("/{report_pub_id}/versions/{version_pub_id}/comments", status_code=201)
def comment_report(
    report_pub_id: str,
    version_pub_id: str,
    body: CommentCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    _require_customer_delivered_report(
        principal,
        report_pub_id=report_pub_id,
        version_pub_id=version_pub_id,
    )
    try:
        comment_pub_id = _service().comment(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            version_pub_id=version_pub_id,
            author_pub_id=principal.actor_pub_id,
            body=body.body,
            parent_pub_id=body.parent_pub_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from exc
    return {"comment_pub_id": comment_pub_id, "report_pub_id": report_pub_id}


@router.post("/{report_pub_id}/actions", status_code=201)
def create_action(
    report_pub_id: str,
    body: ActionCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("report:write")
    try:
        action_pub_id = _service().create_optimization_action(
            tenant_pub_id=principal.tenant_pub_id,
            report_pub_id=report_pub_id,
            description=body.description,
            owner_pub_id=body.owner_pub_id,
            baseline=body.baseline,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from exc
    return {"action_pub_id": action_pub_id}


@router.patch("/{report_pub_id}/actions/{action_pub_id}", status_code=204)
def update_action(
    report_pub_id: str,
    action_pub_id: str,
    body: ActionUpdate,
    principal: Principal = Depends(get_principal),
) -> Response:
    principal.require("report:write")
    if body.state not in {"accepted", "in_progress", "done", "rejected"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_action_state"})
    with tenant_connection(_dsn(), principal.tenant_pub_id) as connection:
        exists = connection.execute(
            """
            SELECT 1 FROM reporting.optimization_action
            WHERE tenant_pub_id=%s AND report_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, report_pub_id, action_pub_id),
        ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail={"code": "action_not_found"})
    try:
        _service().update_optimization_action(
            tenant_pub_id=principal.tenant_pub_id,
            action_pub_id=action_pub_id,
            state=body.state,
            outcome=body.outcome,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_action_update"}) from exc
    return Response(status_code=204)


@router.post("/{report_pub_id}/actions/{action_pub_id}/effect-retests", status_code=201)
def create_effect_retest(
    report_pub_id: str,
    action_pub_id: str,
    body: EffectRetestCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("report:write")
    with tenant_connection(_dsn(), principal.tenant_pub_id) as connection:
        exists = connection.execute(
            """
            SELECT 1 FROM reporting.optimization_action
            WHERE tenant_pub_id=%s AND report_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, report_pub_id, action_pub_id),
        ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail={"code": "action_not_found"})
    retest_pub_id = _service().record_effect_retest(
        tenant_pub_id=principal.tenant_pub_id,
        action_pub_id=action_pub_id,
        measured_at=body.measured_at,
        result=body.result,
        recorded_by_pub_id=principal.actor_pub_id,
    )
    return {"effect_retest_pub_id": retest_pub_id}


@router.get("", response_model=ReportPage)
def list_reports(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> ReportPage:
    principal.require("project:read")
    customer_recipient_pub_id = principal.actor_pub_id if principal.role == Role.CUSTOMER else None
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT report.pub_id,report.project_pub_id,report.title,report.state,
                   report.created_at,report.updated_at
            FROM reporting.report report
            WHERE report.tenant_pub_id=%s
              AND (%s::text IS NULL OR report.pub_id>%s::text)
              AND (
                %s::text IS NULL
                OR (
                  report.state='published'
                  AND EXISTS (
                    SELECT 1
                    FROM reporting.report_delivery delivery
                    WHERE delivery.tenant_pub_id=report.tenant_pub_id
                      AND delivery.report_pub_id=report.pub_id
                      AND delivery.recipient_pub_id=%s
                  )
                )
              )
            ORDER BY report.pub_id LIMIT %s
            """,
            (
                principal.tenant_pub_id,
                cursor,
                cursor,
                customer_recipient_pub_id,
                customer_recipient_pub_id,
                limit + 1,
            ),
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


@router.get("/{report_pub_id}", response_model=ReportDetail)
def report_detail(
    report_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ReportDetail:
    principal.require("project:read")
    customer_recipient_pub_id = principal.actor_pub_id if principal.role == Role.CUSTOMER else None
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        report = connection.execute(
            """
            SELECT report.pub_id,report.project_pub_id,report.title,report.state,
                   report.created_at,report.updated_at
            FROM reporting.report report
            WHERE report.tenant_pub_id=%s
              AND report.pub_id=%s
              AND (
                %s::text IS NULL
                OR (
                  report.state='published'
                  AND EXISTS (
                    SELECT 1
                    FROM reporting.report_delivery delivery
                    WHERE delivery.tenant_pub_id=report.tenant_pub_id
                      AND delivery.report_pub_id=report.pub_id
                      AND delivery.recipient_pub_id=%s
                  )
                )
              )
            """,
            (
                principal.tenant_pub_id,
                report_pub_id,
                customer_recipient_pub_id,
                customer_recipient_pub_id,
            ),
        ).fetchone()
        versions = connection.execute(
            """
            SELECT pub_id,version_number,window_start,window_end,filters,metric_version,
                   scorer_version,fact_snapshot_hash,status
            FROM reporting.report_version
            WHERE tenant_pub_id=%s
              AND report_pub_id=%s
              AND (%s::text IS NULL OR status='published')
            ORDER BY version_number
            """,
            (principal.tenant_pub_id, report_pub_id, customer_recipient_pub_id),
        ).fetchall()
        version_ids = [row["pub_id"] for row in versions]
        components = (
            connection.execute(
                """
                SELECT pub_id,report_version_pub_id,component_type,ordinal,payload,source,created_at
                FROM reporting.report_component
                WHERE tenant_pub_id=%s AND report_version_pub_id=ANY(%s)
                ORDER BY report_version_pub_id,ordinal,pub_id
                """,
                (principal.tenant_pub_id, version_ids),
            ).fetchall()
            if version_ids
            else []
        )
        frozen_facts = (
            connection.execute(
                """
                SELECT pub_id,report_version_pub_id,ordinal,payload,payload_hash,created_at
                FROM reporting.report_frozen_fact
                WHERE tenant_pub_id=%s AND report_version_pub_id=ANY(%s)
                ORDER BY report_version_pub_id,ordinal
                """,
                (principal.tenant_pub_id, version_ids),
            ).fetchall()
            if version_ids
            else []
        )
        artifacts = (
            connection.execute(
                """
                SELECT ra.pub_id,ra.report_version_pub_id,ra.format,ra.evidence_pub_id,
                       ea.mime_type,ea.byte_size,ea.sha256,ra.created_at
                FROM reporting.report_artifact ra
                JOIN evidence.evidence_asset ea
                  ON ea.pub_id=ra.evidence_pub_id AND ea.tenant_pub_id=ra.tenant_pub_id
                WHERE ra.tenant_pub_id=%s AND ra.report_version_pub_id=ANY(%s)
                  AND ea.deleted_at IS NULL
                ORDER BY ra.report_version_pub_id,ra.format
                """,
                (principal.tenant_pub_id, version_ids),
            ).fetchall()
            if version_ids
            else []
        )
        evidence_bindings = (
            connection.execute(
                """
                SELECT ref.pub_id,ref.report_version_pub_id,ref.evidence_pub_id,ref.purpose,
                       ea.kind,ea.access_class,ea.mime_type,ea.byte_size,ea.sha256,
                       count(anchor.id)::bigint AS anchor_count,ea.capture_time,ref.created_at
                FROM reporting.report_evidence_reference ref
                JOIN evidence.evidence_asset ea
                  ON ea.tenant_pub_id=ref.tenant_pub_id AND ea.pub_id=ref.evidence_pub_id
                LEFT JOIN evidence.evidence_anchor anchor
                  ON anchor.tenant_pub_id=ref.tenant_pub_id
                 AND anchor.evidence_pub_id=ref.evidence_pub_id
                WHERE ref.tenant_pub_id=%s AND ref.report_version_pub_id=ANY(%s)
                  AND ea.deleted_at IS NULL
                GROUP BY ref.pub_id,ref.report_version_pub_id,ref.evidence_pub_id,ref.purpose,
                         ea.kind,ea.access_class,ea.mime_type,ea.byte_size,ea.sha256,
                         ea.capture_time,ref.created_at
                ORDER BY ref.report_version_pub_id,ref.created_at,ref.pub_id
                """,
                (principal.tenant_pub_id, version_ids),
            ).fetchall()
            if version_ids
            else []
        )
        reviews = (
            connection.execute(
                """
                SELECT pub_id,report_version_pub_id,reviewer_pub_id,decision,rationale,created_at
                FROM reporting.report_review
                WHERE tenant_pub_id=%s AND report_version_pub_id=ANY(%s)
                ORDER BY created_at,pub_id
                """,
                (principal.tenant_pub_id, version_ids),
            ).fetchall()
            if version_ids and customer_recipient_pub_id is None
            else []
        )
        comments = (
            connection.execute(
                """
                SELECT pub_id,report_version_pub_id,parent_pub_id,author_pub_id,body,
                       resolved_at,created_at
                FROM reporting.report_comment
                WHERE tenant_pub_id=%s AND report_version_pub_id=ANY(%s)
                  AND (%s::text IS NULL OR author_pub_id=%s)
                ORDER BY created_at,pub_id
                """,
                (
                    principal.tenant_pub_id,
                    version_ids,
                    customer_recipient_pub_id,
                    customer_recipient_pub_id,
                ),
            ).fetchall()
            if version_ids
            else []
        )
        events = (
            connection.execute(
                """
                SELECT pub_id,report_version_pub_id,event_type,actor_pub_id,data,created_at
                FROM reporting.report_event
                WHERE tenant_pub_id=%s AND report_pub_id=%s
                ORDER BY created_at,pub_id
                """,
                (principal.tenant_pub_id, report_pub_id),
            ).fetchall()
            if customer_recipient_pub_id is None
            else []
        )
        actions = (
            connection.execute(
                """
                SELECT pub_id,description,owner_pub_id,state,baseline,outcome,created_at,updated_at
                FROM reporting.optimization_action
                WHERE tenant_pub_id=%s AND report_pub_id=%s ORDER BY created_at,pub_id
                """,
                (principal.tenant_pub_id, report_pub_id),
            ).fetchall()
            if customer_recipient_pub_id is None
            else []
        )
        action_ids = [row["pub_id"] for row in actions]
        retests = (
            connection.execute(
                """
                SELECT pub_id,action_pub_id,measured_at,result,recorded_by_pub_id,created_at
                FROM reporting.effect_retest
                WHERE tenant_pub_id=%s AND action_pub_id=ANY(%s)
                ORDER BY measured_at,pub_id
                """,
                (principal.tenant_pub_id, action_ids),
            ).fetchall()
            if action_ids
            else []
        )
    if report is None:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"})
    enriched_versions = []
    for version in versions:
        version_pub_id = version["pub_id"]
        enriched_versions.append(
            {
                **dict(version),
                "components": [
                    dict(row)
                    for row in components
                    if row["report_version_pub_id"] == version_pub_id
                ],
                "frozen_facts": [
                    dict(row)
                    for row in frozen_facts
                    if row["report_version_pub_id"] == version_pub_id
                ],
                "artifacts": [
                    dict(row) for row in artifacts if row["report_version_pub_id"] == version_pub_id
                ],
                "evidence_bindings": [
                    dict(row)
                    for row in evidence_bindings
                    if row["report_version_pub_id"] == version_pub_id
                ],
                "reviews": [
                    dict(row) for row in reviews if row["report_version_pub_id"] == version_pub_id
                ],
                "comments": [
                    dict(row) for row in comments if row["report_version_pub_id"] == version_pub_id
                ],
                "events": [
                    dict(row)
                    for row in events
                    if row["report_version_pub_id"] in {None, version_pub_id}
                ],
            }
        )
    enriched_actions = [
        {
            **dict(action),
            "effect_retests": [
                dict(row) for row in retests if row["action_pub_id"] == action["pub_id"]
            ],
        }
        for action in actions
    ]
    return ReportDetail(
        **dict(report),
        versions=[ReportVersionView.model_validate(item) for item in enriched_versions],
        optimization_actions=[
            OptimizationActionView.model_validate(item) for item in enriched_actions
        ],
    )


@router.get("/{report_pub_id}/versions/{version_pub_id}/artifacts/{format_name}")
def report_artifact(
    report_pub_id: str,
    version_pub_id: str,
    format_name: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    principal.require("project:read")
    if format_name not in {"html", "pdf", "docx", "xlsx"}:
        raise HTTPException(status_code=404, detail={"code": "artifact_not_found"})
    customer_recipient_pub_id = principal.actor_pub_id if principal.role == Role.CUSTOMER else None
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        artifact = connection.execute(
            """
            SELECT ea.object_key,ea.sha256,ea.mime_type
            FROM reporting.report_artifact ra
            JOIN reporting.report_version rv
              ON rv.pub_id=ra.report_version_pub_id AND rv.tenant_pub_id=ra.tenant_pub_id
            JOIN reporting.report report
              ON report.pub_id=rv.report_pub_id AND report.tenant_pub_id=rv.tenant_pub_id
            JOIN evidence.evidence_asset ea
              ON ea.pub_id=ra.evidence_pub_id AND ea.tenant_pub_id=ra.tenant_pub_id
            WHERE ra.tenant_pub_id=%s AND rv.report_pub_id=%s
              AND ra.report_version_pub_id=%s AND ra.format=%s AND ea.deleted_at IS NULL
              AND (
                %s::text IS NULL
                OR (
                  report.state='published'
                  AND rv.status='published'
                  AND EXISTS (
                    SELECT 1
                    FROM reporting.report_delivery delivery
                    WHERE delivery.tenant_pub_id=report.tenant_pub_id
                      AND delivery.report_pub_id=report.pub_id
                      AND delivery.recipient_pub_id=%s
                  )
                )
              )
            """,
            (
                principal.tenant_pub_id,
                report_pub_id,
                version_pub_id,
                format_name,
                customer_recipient_pub_id,
                customer_recipient_pub_id,
            ),
        ).fetchone()
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "artifact_not_found"})
    payload = _service().evidence.store.get_verified(artifact["object_key"], artifact["sha256"])
    disposition = "inline" if format_name in {"html", "pdf"} else "attachment"
    return Response(
        content=payload,
        media_type=artifact["mime_type"],
        headers={
            "Content-Disposition": f'{disposition}; filename="report.{format_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
