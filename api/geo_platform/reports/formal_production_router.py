# ruff: noqa: B008
"""Tenant-safe API for formal quotation-service report production."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from sqlalchemy import select
from sqlalchemy import text as production_router_text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from domain.evidence.dlp import assert_secret_free
from geo_platform.collection.workflow_outbox import (
    WorkflowSignalConflictError,
    enqueue_workflow_signal,
    workflow_signal_replayed,
)
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.pagination import decode_keyset_cursor, encode_keyset_cursor
from geo_platform.projects.models import Project
from geo_platform.tenancy.database import get_db
from geo_platform.tenancy.models import Tenant
from geo_platform.tenancy.repository import set_tenant_context

from ..config import get_settings
from ..observability import connect_temporal
from .formal_production import (
    LEGACY_SERVICE_CATALOG,
    FormalProductionConflict,
    FormalProductionInvalid,
    FormalProductionNotFound,
    FormalReportProductionService,
    FormalWindow,
    formal_review_contract_hash,
)

router = APIRouter(prefix="/api/v2/reports/formal-productions", tags=["formal_reports"])

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


class WindowView(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> WindowView:
        if self.start > self.end:
            raise ValueError("window_start_after_end")
        return self


class FormalMetricSnapshotFilters(StrictModel):
    model: list[str] = Field(default_factory=list, max_length=100)
    region: list[str] = Field(default_factory=list, max_length=100)
    mode: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def normalize_values(self) -> Self:
        for field_name in ("model", "region", "mode"):
            values = [value.strip() for value in getattr(self, field_name)]
            if any(not value or len(value) > 120 for value in values):
                raise ValueError("metric_snapshot_filter_invalid")
            setattr(self, field_name, sorted(set(values)))
        return self


class _FormalProductionCreateBase(StrictModel):
    project_pub_id: str = Field(min_length=5, max_length=120)
    services: list[int]
    sop_project_pub_id: str | None = Field(default=None, min_length=5, max_length=120)
    window_start: date
    window_end: date
    document_status: Literal["internal_review", "delivery_candidate"] = "internal_review"
    candidate_group_strategy: Literal["preregistered_scope_v1"] = "preregistered_scope_v1"
    version: str = Field(default="V1.0", pattern=r"^V[1-9]\d*\.\d+$", max_length=20)
    prepared_by: str = Field(min_length=1, max_length=160)
    prepared_date: date
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=160)
    reviewed_date: date | None = None
    before_window: WindowView | None = None
    after_window: WindowView | None = None

    @model_validator(mode="after")
    def validate_governance(self) -> Self:
        services = self.services
        if len(set(services)) != len(services):
            raise ValueError("duplicate_services")
        if (self.reviewed_by is None) != (self.reviewed_date is None):
            raise ValueError("review_record_incomplete")
        if self.document_status == "delivery_candidate" and self.reviewed_by is None:
            raise ValueError("candidate_review_record_required")
        return self


def _omit_runtime_default_from_schema(schema: dict[str, Any]) -> None:
    # The wrapper below supplies this default before union validation.  Omitting
    # it from the property schema keeps generated clients compatible with legacy
    # callers that predate the service-catalog field.
    schema.pop("default", None)


class LegacyFormalProductionCreate(_FormalProductionCreateBase):
    # Pydantic intentionally narrows the inherited runtime-validation field;
    # mypy treats mutable-list attribute overrides as invariant.
    services: list[Literal[1, 2, 3, 4]] = Field(  # type: ignore[assignment]
        min_length=1, max_length=4
    )
    service_catalog_version: Literal["legacy_report_services_v1"] = Field(
        default="legacy_report_services_v1",
        json_schema_extra=_omit_runtime_default_from_schema,
    )
    sop_project_pub_id: None = None


class QuotationFormalProductionCreate(_FormalProductionCreateBase):
    services: list[Literal[1, 2, 3, 4, 5]] = Field(  # type: ignore[assignment]
        min_length=1, max_length=5
    )
    service_catalog_version: Literal["quotation_services_v2"]
    service2_manifest_pub_id: str | None = Field(default=None, min_length=5, max_length=120)
    service2_manifest_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", max_length=64
    )
    metric_snapshot_set_pub_id: str = Field(
        min_length=7,
        max_length=120,
        pattern=r"^mss_[0-9A-Za-z_-]+$",
    )
    metric_snapshot_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    metric_snapshot_filters: FormalMetricSnapshotFilters

    @model_validator(mode="after")
    def validate_service2_manifest_binding(self) -> Self:
        selected = 2 in self.services
        bound = (
            self.service2_manifest_pub_id is not None and self.service2_manifest_hash is not None
        )
        if selected != bound:
            raise ValueError(
                "service2_manifest_binding_required"
                if selected
                else "service2_manifest_not_applicable"
            )
        return self


class FormalProductionCreate(
    RootModel[LegacyFormalProductionCreate | QuotationFormalProductionCreate]
):
    """Versioned create contract with an explicit legacy compatibility path."""

    @model_validator(mode="before")
    @classmethod
    def default_missing_catalog_to_legacy(cls, value: object) -> object:
        if isinstance(value, dict) and "service_catalog_version" not in value:
            return {**value, "service_catalog_version": LEGACY_SERVICE_CATALOG}
        return value


class FormalArtifactView(StrictModel):
    format: Literal["docx", "pdf", "xlsx", "zip", "manifest"]
    sha256: str
    byte_size: int
    mime_type: str
    download_url: str


class FormalOutputView(StrictModel):
    service_number: Literal[1, 2, 3, 4, 5]
    service_code: Literal[
        "ranking_test",
        "outbound_disparagement_audit",
        "inbound_disparagement_audit",
        "official_site_audit",
        "content_publishing_pilot",
        "legacy_ranking_assessment",
        "legacy_content_ecosystem_risk",
        "legacy_official_site_efficiency",
        "legacy_pilot_comparison",
    ]
    report_pub_id: str
    report_version_pub_id: str
    fact_snapshot_hash: str
    artifacts: list[FormalArtifactView]


class FormalProductionView(StrictModel):
    pub_id: str
    project_pub_id: str
    services: list[Literal[1, 2, 3, 4, 5]]
    service_catalog_version: Literal["legacy_report_services_v1", "quotation_services_v2"]
    sop_project_pub_id: str | None
    service2_manifest_pub_id: str | None = None
    service2_manifest_hash: str | None = None
    metric_snapshot_set_pub_id: str | None = None
    metric_snapshot_set_hash: str | None = None
    metric_snapshot_filters: dict[str, object] = Field(default_factory=dict)
    metric_snapshot_dependency_hash: str | None = None
    status: Literal["queued", "running", "failed", "awaiting_review", "signed"]
    document_status: Literal[
        "pre_formal", "formal", "internal_review", "delivery_candidate", "approved_signed"
    ]
    window_start: date
    window_end: date
    before_window: WindowView | None
    after_window: WindowView | None
    candidate_group_strategy: Literal["evidence_completeness_v1", "preregistered_scope_v1"]
    document_governance: dict[str, object]
    workflow_id: str
    fact_snapshot_hash: str | None
    outputs: list[FormalOutputView]
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class FormalProductionPage(StrictModel):
    items: list[FormalProductionView]
    next_cursor: str | None
    has_more: bool


# ── 生产进度（细粒度阶段透出，与 FormalSnapshotReportWorkflowV2 状态机对齐）──

FormalProductionStage = Literal[
    "queued",
    "binding_snapshot",
    "preflight",
    "running",
    "awaiting_review",
    "finalizing",
    "signed",
]

# 有序阶段全集：queued 是库内入队态，其后为 workflow 内部状态机，signed 为成功终态。
FORMAL_PRODUCTION_STAGES: tuple[FormalProductionStage, ...] = (
    "queued",
    "binding_snapshot",
    "preflight",
    "running",
    "awaiting_review",
    "finalizing",
    "signed",
)

# 库内粗粒度 status → 阶段的诚实降级映射（Temporal 不可达/workflow 已关闭时使用）。
# 库内 running 覆盖 binding_snapshot/preflight/running 三段，无法细分时锚到 running。
_DB_STATUS_STAGE: dict[str, FormalProductionStage] = {
    "queued": "queued",
    "running": "running",
    "awaiting_review": "awaiting_review",
    "signed": "signed",
    # failed 不在此表：失败定位只能来自 error_code（下表），映射不出就诚实不给阶段。
}

# 失败阶段定位：workflow 失败时 state 被覆盖为 "failed"，只有 error_code 能定位失败点。
# metric_snapshot_binding_failed 覆盖绑定+预检守卫段（workflow 不保留二者区分），锚到最早阶段。
_FAILURE_STAGE_BY_ERROR_CODE: dict[str, FormalProductionStage] = {
    "metric_snapshot_set_not_ready": "binding_snapshot",
    "metric_snapshot_binding_failed": "binding_snapshot",
    "production_failed": "running",
    "changes_requested": "finalizing",
}


class FormalProductionStageView(StrictModel):
    stage: FormalProductionStage
    status: Literal["done", "current", "pending", "failed"]
    entered_at: datetime | None = None


class FormalProductionProgressView(StrictModel):
    production_pub_id: str
    source: Literal["workflow", "db_fallback"]
    failed: bool
    error_code: str | None
    stages: list[FormalProductionStageView]


def _progress_stages(
    *,
    current_stage: FormalProductionStage | None,
    failed_stage: FormalProductionStage | None,
    created_at: datetime | None,
    updated_at: datetime | None,
) -> list[FormalProductionStageView]:
    """有序阶段数组。

    时间戳诚实口径：库内只有行级 created_at/updated_at——queued 用 created_at，
    枢轴阶段（当前/失败/终态 signed）用 updated_at（行级最后一次状态迁移），
    其余阶段一律 None，不造逐阶段时间。
    """
    pivot: int | None = None
    if failed_stage is not None:
        pivot = FORMAL_PRODUCTION_STAGES.index(failed_stage)
    elif current_stage is not None:
        pivot = FORMAL_PRODUCTION_STAGES.index(current_stage)
    views: list[FormalProductionStageView] = []
    for index, stage in enumerate(FORMAL_PRODUCTION_STAGES):
        status: Literal["done", "current", "pending", "failed"]
        if failed_stage is not None and index == pivot:
            status = "failed"
        elif pivot is not None and index < pivot:
            status = "done"
        elif pivot is not None and index == pivot:
            # signed 是成功终态：落在 signed 上时全链路已完成，不显示"进行中"。
            status = "done" if current_stage == "signed" else "current"
        else:
            status = "pending"
        entered_at: datetime | None = None
        if stage == "queued":
            entered_at = created_at
        elif pivot is not None and index == pivot:
            entered_at = updated_at
        views.append(FormalProductionStageView(stage=stage, status=status, entered_at=entered_at))
    return views


async def _query_workflow_state(workflow_id: str) -> str | None:
    """查询 workflow 细粒度状态机；不可达/已关闭/查询失败/形状未知一律返回 None 由调用方降级。"""
    try:
        client = await connect_temporal(get_settings())
        handle = client.get_workflow_handle(workflow_id)
        state = await handle.query("state")
    except Exception:
        return None
    return state if isinstance(state, str) else None


class FormalReviewCreate(StrictModel):
    decision: Literal["approved", "changes_requested"]
    rationale: str = Field(min_length=1, max_length=1000)


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def _service() -> FormalReportProductionService:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return FormalReportProductionService(
        dsn=_dsn(), evidence=EvidenceService(dsn=_dsn(), store=store)
    )


def _tenant_project(
    session: Session, *, tenant_pub_id: str, project_pub_id: str
) -> tuple[Tenant, Project]:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == tenant.id,
            Project.pub_id == project_pub_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return tenant, project


@router.post("", response_model=FormalProductionView, status_code=201)
def create_formal_production(
    body: FormalProductionCreate,
    response: Response,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FormalProductionView:
    payload = body.root
    principal.require("formal_report:produce")
    tenant, _ = _tenant_project(
        session,
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=payload.project_pub_id,
    )
    try:
        row, created = _service().enqueue(
            session,
            tenant_pub_id=principal.tenant_pub_id,
            tenant_id=tenant.id,
            project_pub_id=payload.project_pub_id,
            services=payload.services,
            window=FormalWindow(payload.window_start, payload.window_end),
            document_status=payload.document_status,
            candidate_group_strategy=payload.candidate_group_strategy,
            before_window=(
                FormalWindow(payload.before_window.start, payload.before_window.end)
                if payload.before_window
                else None
            ),
            after_window=(
                FormalWindow(payload.after_window.start, payload.after_window.end)
                if payload.after_window
                else None
            ),
            document_governance={
                "version": payload.version,
                "prepared_by": payload.prepared_by,
                "prepared_date": payload.prepared_date.isoformat(),
                "reviewed_by": payload.reviewed_by,
                "reviewed_date": (
                    payload.reviewed_date.isoformat() if payload.reviewed_date else None
                ),
            },
            service_catalog_version=payload.service_catalog_version,
            sop_project_pub_id=payload.sop_project_pub_id,
            service2_manifest_pub_id=getattr(payload, "service2_manifest_pub_id", None),
            service2_manifest_hash=getattr(payload, "service2_manifest_hash", None),
            metric_snapshot_set_pub_id=getattr(payload, "metric_snapshot_set_pub_id", None),
            metric_snapshot_set_hash=getattr(payload, "metric_snapshot_set_hash", None),
            metric_snapshot_filters=(
                payload.metric_snapshot_filters.model_dump()
                if isinstance(payload, QuotationFormalProductionCreate)
                else None
            ),
            idempotency_key=idempotency_key,
            created_by_pub_id=principal.actor_pub_id,
            task_queue=get_settings().report_temporal_task_queue,
        )
        session.commit()
    except FormalProductionNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    except FormalProductionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except FormalProductionInvalid as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    if not created:
        response.status_code = 200
    response.headers["Idempotency-Key"] = idempotency_key
    return FormalProductionView.model_validate(row)


@router.get("", response_model=FormalProductionPage)
def list_formal_productions(
    project_pub_id: str | None = None,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> FormalProductionPage:
    principal.require("formal_report:read")
    filters = {"project_pub_id": project_pub_id}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="formal-report-productions",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    try:
        rows = _service().list_productions(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            cursor_created_at=anchor.created_at if anchor else None,
            cursor_pub_id=anchor.pub_id if anchor else None,
            limit=limit,
        )
    except FormalProductionInvalid as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="formal-report-productions",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last["created_at"],
            pub_id=last["pub_id"],
        )
    return FormalProductionPage(
        items=[FormalProductionView.model_validate(row) for row in visible],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/{production_pub_id}", response_model=FormalProductionView)
def formal_production(
    production_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> FormalProductionView:
    principal.require("formal_report:read")
    try:
        row = _service().get(
            tenant_pub_id=principal.tenant_pub_id,
            production_pub_id=production_pub_id,
        )
    except FormalProductionNotFound as exc:
        raise HTTPException(
            status_code=404, detail={"code": "formal_production_not_found"}
        ) from exc
    return FormalProductionView.model_validate(row)


@router.get("/{production_pub_id}/progress", response_model=FormalProductionProgressView)
async def formal_production_progress(
    production_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> FormalProductionProgressView:
    principal.require("formal_report:read")
    try:
        row = await run_in_threadpool(
            _service().get,
            tenant_pub_id=principal.tenant_pub_id,
            production_pub_id=production_pub_id,
        )
    except FormalProductionNotFound as exc:
        raise HTTPException(
            status_code=404, detail={"code": "formal_production_not_found"}
        ) from exc
    raw_error_code = row.get("error_code")
    error_code = str(raw_error_code) if raw_error_code else None
    workflow_state = await _query_workflow_state(str(row["workflow_id"]))
    failed = False
    current_stage: FormalProductionStage | None = None
    failed_stage: FormalProductionStage | None = None
    if workflow_state in FORMAL_PRODUCTION_STAGES:
        source: Literal["workflow", "db_fallback"] = "workflow"
        current_stage = cast(FormalProductionStage, workflow_state)
    elif workflow_state == "failed":
        # workflow 查询成功且已到失败终态；失败点只能由库内 error_code 定位。
        source = "workflow"
        failed = True
        failed_stage = _FAILURE_STAGE_BY_ERROR_CODE.get(error_code or "")
    else:
        # workflow 已关闭/查询失败/Temporal 不可达/状态未知：诚实降级为库内粗粒度 status。
        source = "db_fallback"
        db_status = str(row["status"])
        if db_status == "failed":
            failed = True
            failed_stage = _FAILURE_STAGE_BY_ERROR_CODE.get(error_code or "")
        else:
            current_stage = _DB_STATUS_STAGE.get(db_status)
    return FormalProductionProgressView(
        production_pub_id=str(row["pub_id"]),
        source=source,
        failed=failed,
        error_code=error_code if failed else None,
        stages=_progress_stages(
            current_stage=current_stage,
            failed_stage=failed_stage,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ),
    )


@router.post("/{production_pub_id}/review", response_model=FormalProductionView, status_code=202)
def review_formal_production(
    production_pub_id: str,
    body: FormalReviewCreate,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FormalProductionView:
    principal.require("report:review")
    try:
        assert_secret_free(body.rationale)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "sensitive_input_rejected"}) from exc
    try:
        current = _service().get(
            tenant_pub_id=principal.tenant_pub_id,
            production_pub_id=production_pub_id,
        )
    except FormalProductionNotFound as exc:
        raise HTTPException(
            status_code=404, detail={"code": "formal_production_not_found"}
        ) from exc
    if body.decision == "approved" and current["document_status"] != "delivery_candidate":
        raise HTTPException(status_code=409, detail={"code": "delivery_candidate_required"})
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == principal.tenant_pub_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail={"code": "formal_production_not_found"})
    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    decision = {
        "approved": body.decision == "approved",
        "reviewer_pub_id": principal.actor_pub_id,
        "rationale": body.rationale,
    }
    review_hash = formal_review_contract_hash(
        approved=body.decision == "approved",
        reviewer_pub_id=principal.actor_pub_id,
        rationale=body.rationale,
    )
    try:
        session.execute(
            production_router_text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": f"{principal.tenant_pub_id}:{production_pub_id}:formal-review"},
        )
        claimed = (
            session.execute(
                production_router_text(
                    """
                    SELECT status,document_status,review_request_hash
                    FROM reporting.formal_report_production
                    WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:production_pub_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_pub_id": principal.tenant_pub_id,
                    "production_pub_id": production_pub_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if claimed is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "formal_production_not_found"},
            )
        if body.decision == "approved" and claimed["document_status"] != "delivery_candidate":
            raise HTTPException(
                status_code=409,
                detail={"code": "delivery_candidate_required"},
            )
        replayed = workflow_signal_replayed(
            session,
            tenant_pub_id=principal.tenant_pub_id,
            workflow_id=str(current["workflow_id"]),
            signal_name="review",
            args=[decision],
            idempotency_key=idempotency_key,
        )
        if replayed:
            if claimed["review_request_hash"] != review_hash:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "formal_review_conflict"},
                )
            return FormalProductionView.model_validate(current)
        if claimed["review_request_hash"] is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "formal_review_conflict"},
            )
        if claimed["status"] != "awaiting_review":
            raise HTTPException(
                status_code=409,
                detail={"code": "formal_production_not_reviewable"},
            )
        claimed_hash = session.execute(
            production_router_text(
                """
                UPDATE reporting.formal_report_production
                SET review_request_hash=:review_hash,updated_at=now()
                WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:production_pub_id
                  AND status='awaiting_review' AND review_request_hash IS NULL
                RETURNING review_request_hash
                """
            ),
            {
                "review_hash": review_hash,
                "tenant_pub_id": principal.tenant_pub_id,
                "production_pub_id": production_pub_id,
            },
        ).scalar_one_or_none()
        if claimed_hash != review_hash:
            raise HTTPException(
                status_code=409,
                detail={"code": "formal_review_conflict"},
            )
        enqueue_workflow_signal(
            session,
            tenant_pub_id=principal.tenant_pub_id,
            workflow_id=str(current["workflow_id"]),
            signal_name="review",
            args=[decision],
            idempotency_key=idempotency_key,
        )
        session.commit()
    except WorkflowSignalConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc
    except HTTPException:
        session.rollback()
        raise
    return FormalProductionView.model_validate(current)


@router.get("/{production_pub_id}/artifacts/{service_number}/{format_name}")
def formal_artifact(
    production_pub_id: str,
    service_number: int,
    format_name: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    if principal.role != Role.CUSTOMER:
        principal.require("formal_report:read")
    try:
        service = _service()
        payload, mime_type, digest = service.artifact(
            tenant_pub_id=principal.tenant_pub_id,
            production_pub_id=production_pub_id,
            service_number=service_number,
            format_name=format_name,
            customer_recipient_pub_id=(
                principal.actor_pub_id if principal.role == Role.CUSTOMER else None
            ),
        )
        filename = service.artifact_filename(
            tenant_pub_id=principal.tenant_pub_id,
            production_pub_id=production_pub_id,
            service_number=service_number,
            format_name=format_name,
        )
    except FormalProductionNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "formal_artifact_not_found"}) from exc
    disposition = "inline" if format_name == "pdf" else "attachment"
    return Response(
        content=payload,
        media_type=mime_type,
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="service-{service_number}.{format_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-SHA256": digest,
        },
    )


__all__ = ["router"]
