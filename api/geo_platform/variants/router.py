# ruff: noqa: B008
"""W5 查询变体 API：变体清单（意图簇分组）/ 生成 / 批量确认 / 覆盖率 / 草稿导出。

INV-25 确认门：变体状态机 pending → confirmed/rejected；仅 confirmed 出现在
/variants/draft（config draft 的 QueryGroup 形状），未确认变体永远进不了草稿。
写端点走与 projects 域一致的 AuditLog 幂等回执（Idempotency-Key 必传）。
"""

import hashlib
import json
import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..intake import research
from ..metrics_v2.consumer_projection import OfficialMetricsConsumer, OfficialScope
from ..metrics_v2.repository import MetricsV2Repository
from ..projects.models import Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from . import matrix, service, textutil
from .models import QueryVariant

router = APIRouter(prefix="/api/v2/projects", tags=["query-variants"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]

_MAX_GAP_LIST = 200


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def _official_consumer() -> OfficialMetricsConsumer:
    return OfficialMetricsConsumer(MetricsV2Repository(_dsn()))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VariantView(StrictModel):
    pub_id: str
    text: str
    source_type: str
    source_ref: str
    intent: str
    audience: str
    region: str
    product_line: str
    marginal_coverage_cell: dict[str, str]
    cluster_id: str | None
    cluster_size: int
    verified: bool
    status: str
    model: str | None
    prompt_version: str | None
    created_at: str
    confirmed_at: str | None


class IntentGroup(StrictModel):
    intent: str
    count: int
    variants: list[VariantView]


class VariantListResponse(StrictModel):
    status: str
    total: int
    groups: list[IntentGroup]


class GenerateRequest(StrictModel):
    window_days: int | None = Field(default=90, ge=1, le=365)
    use_llm: bool = False
    max_variants: int = Field(default=100, ge=1, le=500)
    legacy_recycle_answer_analysis: bool = Field(
        default=False,
        description=(
            "Explicit audit-only V1 recycle. Never enabled by the formal V2 metrics path."
        ),
    )


class GenerateResponse(StrictModel):
    seeds_upserted: int
    seeds_dropped_dlp: int
    variants_created: int
    variants_skipped_existing: int
    gap_variants_created: int
    llm_variants_created: int
    llm_note: str
    recycled_zero_mention: int
    verified_marked: int
    coverage_before: dict[str, Any]
    coverage_after: dict[str, Any]


class ConfirmRequest(StrictModel):
    variant_pub_ids: list[str] = Field(min_length=1, max_length=500)
    decision: Literal["confirmed", "rejected"] = "confirmed"


class ConfirmResponse(StrictModel):
    decision: str
    updated: int
    skipped: int
    missing: list[str]
    variants: list[dict[str, str]]


class CoverageBucket(StrictModel):
    total_cells: int
    covered_cells: int
    coverage_ratio: float
    duplicate_rate: float
    unclassified_count: int


class CoverageResponse(StrictModel):
    axes: dict[str, list[str]]
    truncated: bool
    existing_pool: CoverageBucket
    with_variants: CoverageBucket
    gaps: list[dict[str, str]]


class DraftItem(StrictModel):
    text: str
    priority: int


class DraftResponse(StrictModel):
    name: str
    items: list[DraftItem]


def _project(
    session: Session, tenant_id: object, project_pub_id: str, *, lock: bool = False
) -> Project:
    statement = select(Project).where(
        Project.tenant_id == tenant_id, Project.pub_id == project_pub_id
    )
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return project


def _variant_view(row: QueryVariant) -> VariantView:
    try:
        cell = json.loads(row.marginal_coverage_cell or "{}")
    except json.JSONDecodeError:
        cell = {}
    return VariantView(
        pub_id=row.pub_id,
        text=row.text,
        source_type=row.source_type,
        source_ref=row.source_ref,
        intent=row.intent,
        audience=row.audience,
        region=row.region,
        product_line=row.product_line,
        marginal_coverage_cell={str(k): str(v) for k, v in cell.items()},
        cluster_id=row.cluster_id,
        cluster_size=row.cluster_size,
        verified=row.verified,
        status=row.status,
        model=row.model,
        prompt_version=row.prompt_version,
        created_at=row.created_at.isoformat(),
        confirmed_at=row.confirmed_at.isoformat() if row.confirmed_at else None,
    )


def _replay_or_none(
    session: Session, *, tenant_id: object, action: str, payload_hash: str
) -> dict[str, Any] | None:
    prior = session.scalar(
        select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
    )
    if prior is None:
        return None
    receipt = json.loads(prior.receipt)
    if receipt.get("payload_hash") != payload_hash:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
    result = receipt.get("result")
    if not isinstance(result, dict):
        raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
    return result


def _audit(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    principal: Principal,
    action: str,
    resource_pub_id: str,
    payload_hash: str,
    result: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant_id,
            actor_pub_id=principal.actor_pub_id,
            action=action,
            resource_type="query_variant",
            resource_pub_id=resource_pub_id,
            receipt=json.dumps(
                {"payload_hash": payload_hash, "result": result}, ensure_ascii=False
            ),
        )
    )


@router.get("/{project_pub_id}/variants", response_model=VariantListResponse)
def list_variants(
    project_pub_id: str,
    status: Literal["pending", "confirmed", "rejected"] = Query(default="pending"),
    limit: int = Query(default=500, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> VariantListResponse:
    """按意图簇分组的变体清单（含 marginal_coverage/source/verified/重复簇信息）。"""
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    rows = list(
        session.scalars(
            select(QueryVariant)
            .where(
                QueryVariant.tenant_id == repository.tenant.id,
                QueryVariant.project_id == project.id,
                QueryVariant.status == status,
            )
            .order_by(QueryVariant.intent.asc(), QueryVariant.created_at.desc())
            .limit(limit)
        ).all()
    )
    groups: dict[str, list[VariantView]] = {}
    for row in rows:
        groups.setdefault(row.intent, []).append(_variant_view(row))
    return VariantListResponse(
        status=status,
        total=len(rows),
        groups=[
            IntentGroup(intent=intent, count=len(items), variants=items)
            for intent, items in groups.items()
        ],
    )


@router.post(
    "/{project_pub_id}/variants/generate", response_model=GenerateResponse, status_code=201
)
def generate(
    project_pub_id: str,
    body: GenerateRequest,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> GenerateResponse:
    """种子聚合 + 矩阵空格生成 + 零提及闭环（LLM 扩写可选，默认关）。"""
    principal.require("change:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    payload_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    action = (
        "query_variant.generated:"
        + hashlib.sha256(f"{project.pub_id}:{idempotency_key}".encode()).hexdigest()
    )
    replay = _replay_or_none(
        session, tenant_id=repository.tenant.id, action=action, payload_hash=payload_hash
    )
    if replay is not None:
        return GenerateResponse(**replay)
    llm_config = None
    if body.use_llm:
        llm_config = research.config_from_settings(get_settings())
    summary = service.generate_variants(
        session,
        tenant_id=repository.tenant.id,
        tenant_pub_id=repository.tenant.pub_id,
        project=project,
        window_days=body.window_days,
        use_llm=body.use_llm,
        llm_config=llm_config,
        max_variants=body.max_variants,
        legacy_recycle_answer_analysis=body.legacy_recycle_answer_analysis,
        pub_id_factory=new_pub_id,
    )
    result = GenerateResponse(
        seeds_upserted=summary.seeds_upserted,
        seeds_dropped_dlp=summary.seeds_dropped_dlp,
        variants_created=summary.variants_created,
        variants_skipped_existing=summary.variants_skipped_existing,
        gap_variants_created=summary.gap_variants_created,
        llm_variants_created=summary.llm_variants_created,
        llm_note=summary.llm_note,
        recycled_zero_mention=summary.recycled_zero_mention,
        verified_marked=summary.verified_marked,
        coverage_before=summary.coverage_before,
        coverage_after=summary.coverage_after,
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_pub_id=project.pub_id,
        payload_hash=payload_hash,
        result=result.model_dump(),
    )
    session.commit()
    return result


@router.post("/{project_pub_id}/variants/confirm", response_model=ConfirmResponse)
def confirm(
    project_pub_id: str,
    body: ConfirmRequest,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ConfirmResponse:
    """批量确认/拒绝（INV-25：确认后变体才允许进 config draft）。"""
    principal.require("change:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    canonical = json.dumps(
        {"decision": body.decision, "ids": sorted(body.variant_pub_ids)},
        ensure_ascii=False,
        sort_keys=True,
    )
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    action = (
        "query_variant.confirmed:"
        + hashlib.sha256(f"{project.pub_id}:{idempotency_key}".encode()).hexdigest()
    )
    replay = _replay_or_none(
        session, tenant_id=repository.tenant.id, action=action, payload_hash=payload_hash
    )
    if replay is not None:
        return ConfirmResponse(**replay)
    outcome = service.confirm_variants(
        session,
        tenant_id=repository.tenant.id,
        project=project,
        variant_pub_ids=body.variant_pub_ids,
        decision=body.decision,
    )
    result = ConfirmResponse(**outcome)
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_pub_id=project.pub_id,
        payload_hash=payload_hash,
        result=result.model_dump(),
    )
    session.commit()
    return result


@router.get(
    "/{project_pub_id}/variants/official-metrics",
    response_model=None,
    operation_id="getVariantOfficialMetricsV2",
)
def official_metrics(
    project_pub_id: str,
    start: date,
    end: date,
    focal_entity_id: list[str] | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Goal/verification metrics for variants from the V2 official set only."""

    principal.require("project:read")
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
    return {**result, "schema_version": "variant-official-metrics-v2"}


@router.get("/{project_pub_id}/variants/coverage", response_model=CoverageResponse)
def coverage(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CoverageResponse:
    """矩阵覆盖率：格子总数/已覆盖/空格清单 + 现有池与变体的近义重复率。"""
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    axes = service.project_axes(session, repository.tenant.id, project)
    pool = service.existing_pool(session, repository.tenant.id, project)
    variant_texts = list(
        session.scalars(
            select(QueryVariant.text).where(
                QueryVariant.tenant_id == repository.tenant.id,
                QueryVariant.project_id == project.id,
                QueryVariant.status.in_(["pending", "confirmed"]),
            )
        ).all()
    )
    before = matrix.compute_coverage(pool, axes)
    after = matrix.compute_coverage(pool + variant_texts, axes)
    pool_clusters = textutil.cluster_texts([(item, 1) for item in pool])
    variant_clusters = textutil.cluster_texts([(item, 1) for item in variant_texts])
    return CoverageResponse(
        axes={
            "intents": list(textutil.INTENTS),
            "audiences": list(axes.audiences),
            "regions": list(axes.regions),
            "product_lines": list(axes.product_lines),
        },
        truncated=before.truncated,
        existing_pool=CoverageBucket(
            total_cells=before.total_cells,
            covered_cells=before.covered_cells,
            coverage_ratio=before.coverage_ratio,
            duplicate_rate=matrix.duplicate_rate(pool, len(pool_clusters)),
            unclassified_count=len(before.unclassified_queries),
        ),
        with_variants=CoverageBucket(
            total_cells=after.total_cells,
            covered_cells=after.covered_cells,
            coverage_ratio=after.coverage_ratio,
            duplicate_rate=matrix.duplicate_rate(variant_texts, len(variant_clusters)),
            unclassified_count=len(after.unclassified_queries),
        ),
        gaps=[cell.as_dict() for cell in before.gaps[:_MAX_GAP_LIST]],
    )


@router.get("/{project_pub_id}/variants/draft", response_model=DraftResponse)
def confirmed_config_draft(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> DraftResponse:
    """INV-25 出口：仅 confirmed 变体的 QueryGroupDraft 形状（可直接并入 ConfigDraft）。"""
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    draft = service.confirmed_draft(session, tenant_id=repository.tenant.id, project=project)
    return DraftResponse(
        name=str(draft["name"]),
        items=[DraftItem(**item) for item in draft["items"]],
    )
