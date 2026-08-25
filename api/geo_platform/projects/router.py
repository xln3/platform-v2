# ruff: noqa: B008
# mypy: disable-error-code="arg-type"

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from domain.brandrank.rules import load_domain

from ..contracts import PageMeta, ProjectPage, ProjectSummary
from ..identity.policy import Principal, get_principal
from ..pagination import decode_keyset_cursor, encode_keyset_cursor
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import Customer, MonitoringConfig, MonitoringConfigVersion, Project

router = APIRouter(prefix="/api/v2/projects", tags=["projects"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_brandrank_domain(value: str | None) -> str | None:
    """项目级 brandrank domain 真源校验：None/空白 → None（清除）；非法值 → 400。

    词表单源 = domain/brandrank/rules.py（rules_data/ 已落盘规则包集合）；
    校验先于我方任何 DB 写入（fail-fast，非法请求不留审计垃圾）。
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        load_domain(cleaned)
    except ValueError as exc:
        # projects router 本地惯例：plain HTTPException（全局 handler 丢 details，
        # 故只带 code；可用词表见 brand-visibility 端点 unknown_domain 的 details）
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_brandrank_domain"},
        ) from exc
    return cleaned


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    customer_name: str = Field(min_length=1, max_length=200)
    brandrank_domain: str | None = Field(default=None, max_length=40)


class ProjectPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: str | None = Field(default=None, pattern="^(draft|active|paused|archived)$")
    # 显式 null = 清除真源设置；缺省 = 不动（model_fields_set 区分）
    brandrank_domain: str | None = Field(default=None, max_length=40)
    expected_version: int = Field(ge=1)


class QueryItemDraft(StrictModel):
    text: str = Field(min_length=1, max_length=2_000)
    priority: int = Field(default=100, ge=1, le=1_000)


class QueryGroupDraft(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    items: list[QueryItemDraft] = Field(min_length=1, max_length=500)


class ConfigDraft(StrictModel):
    query_groups: list[QueryGroupDraft] = Field(min_length=1, max_length=50)
    regions: list[str] = Field(min_length=1, max_length=20)
    models: list[str] = Field(min_length=1, max_length=10)
    modes: list[str] = Field(min_length=1, max_length=10)
    frequency: str = Field(min_length=1, max_length=80)
    effective_at: datetime


class FrozenConfigView(StrictModel):
    pub_id: str
    revision: int
    effective_at: datetime
    frozen_at: datetime
    snapshot_hash: str
    snapshot: dict[str, Any]
    question_groups: list[QueryGroupDraft] = Field(default_factory=list)


class CurrentConfigView(StrictModel):
    """Read-only v1 projection with explicit effective/pending semantics."""

    effective: FrozenConfigView | None
    next_pending: FrozenConfigView | None


def _frozen_config_view(version: MonitoringConfigVersion) -> FrozenConfigView:
    if version.frozen_at is None:
        raise ValueError("config_version_not_frozen")
    snapshot = json.loads(version.snapshot_json)
    raw_groups = snapshot.get("query_groups") if isinstance(snapshot, dict) else None
    question_groups = (
        [QueryGroupDraft.model_validate(group) for group in raw_groups]
        if isinstance(raw_groups, list)
        else []
    )
    return FrozenConfigView(
        pub_id=version.pub_id,
        revision=version.revision,
        effective_at=version.effective_at,
        frozen_at=version.frozen_at,
        snapshot_hash=version.snapshot_hash,
        snapshot=snapshot,
        question_groups=question_groups,
    )


def as_summary(project: Project, tenant_pub_id: str) -> ProjectSummary:
    return ProjectSummary(
        pub_id=project.pub_id,
        tenant_pub_id=tenant_pub_id,
        name=project.name,
        state=project.state,
        brandrank_domain=project.brandrank_domain,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=ProjectPage, operation_id="listProjects")
def list_projects(
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProjectPage:
    principal.require("project:read")
    try:
        repository = TenantRepository(session, principal.tenant_pub_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "tenant_not_found"}) from exc
    filters: dict[str, str | None] = {}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="projects",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    statement = select(Project).where(Project.tenant_id == repository.tenant.id)
    if anchor is not None:
        statement = statement.where(
            or_(
                Project.created_at < anchor.created_at,
                and_(Project.created_at == anchor.created_at, Project.pub_id < anchor.pub_id),
            )
        )
    rows = session.scalars(
        statement.order_by(Project.created_at.desc(), Project.pub_id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    data = rows[:limit]
    next_cursor = None
    if has_more and data:
        last = data[-1]
        next_cursor = encode_keyset_cursor(
            kind="projects",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last.created_at,
            pub_id=last.pub_id,
        )
    return ProjectPage(
        data=[as_summary(item, principal.tenant_pub_id) for item in data],
        page=PageMeta(next_cursor=next_cursor, has_more=has_more),
    )


@router.post("", response_model=ProjectSummary, status_code=201)
def create_project(
    body: ProjectCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProjectSummary:
    principal.require("project:write")
    brandrank_domain = _validate_brandrank_domain(body.brandrank_domain)
    repository = TenantRepository(session, principal.tenant_pub_id)
    receipt_action = f"project.created:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id,
            AuditLog.action == receipt_action,
        )
    )
    if prior:
        receipt = json.loads(prior.receipt)
        project = session.scalar(
            select(Project).where(
                Project.tenant_id == repository.tenant.id,
                Project.pub_id == receipt["project_pub_id"],
            )
        )
        assert project is not None
        return as_summary(project, principal.tenant_pub_id)
    customer = Customer(
        pub_id=new_pub_id("cst"), tenant_id=repository.tenant.id, name=body.customer_name
    )
    session.add(customer)
    session.flush()
    project = Project(
        pub_id=new_pub_id("prj"),
        tenant_id=repository.tenant.id,
        customer_id=customer.id,
        name=body.name,
        brandrank_domain=brandrank_domain,
    )
    session.add(project)
    session.flush()
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.actor_pub_id,
            action=receipt_action,
            resource_type="project",
            resource_pub_id=project.pub_id,
            receipt=json.dumps({"project_pub_id": project.pub_id}),
        )
    )
    session.commit()
    return as_summary(project, principal.tenant_pub_id)


@router.patch("/{project_pub_id}", response_model=ProjectSummary)
def update_project(
    project_pub_id: str,
    body: ProjectPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProjectSummary:
    principal.require("project:write")
    brandrank_domain_marker = (
        _validate_brandrank_domain(body.brandrank_domain)
        if "brandrank_domain" in body.model_fields_set
        else None
    )
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id, Project.pub_id == project_pub_id
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    if project.version != body.expected_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict"})
    if body.name is not None:
        project.name = body.name
    if body.state is not None:
        project.state = body.state
    if "brandrank_domain" in body.model_fields_set:
        # 显式传值（含 null/空白=清除）才动真源列；缺省不动
        project.brandrank_domain = brandrank_domain_marker
    project.version += 1
    session.commit()
    return as_summary(project, principal.tenant_pub_id)


@router.post("/{project_pub_id}/config/freeze", response_model=FrozenConfigView, status_code=201)
def freeze_config(
    project_pub_id: str,
    body: ConfigDraft,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FrozenConfigView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id, Project.pub_id == project_pub_id
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    receipt_action = f"project.config.frozen:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id,
            AuditLog.action == receipt_action,
        )
    )
    if prior is not None:
        receipt = json.loads(prior.receipt)
        version = session.scalar(
            select(MonitoringConfigVersion).where(
                MonitoringConfigVersion.tenant_id == repository.tenant.id,
                MonitoringConfigVersion.pub_id == receipt.get("config_version_pub_id"),
            )
        )
        if version is None:
            raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
        return FrozenConfigView(
            pub_id=version.pub_id,
            revision=version.revision,
            effective_at=version.effective_at,
            frozen_at=version.frozen_at,
            snapshot_hash=version.snapshot_hash,
            snapshot=json.loads(version.snapshot_json),
        )
    # INV-25 硬门（W5）：命中本项目 pending 变体的文本必须先经 /variants/confirm，
    # 防止绕过确认门把同一文本手打进配置。归一化口径与 variants 域一致。
    draft_texts = [item.text for group in body.query_groups for item in group.items]
    if draft_texts:
        from ..variants.models import QueryVariant
        from ..variants.textutil import normalize_query

        pending_rows = session.scalars(
            select(QueryVariant.normalized).where(
                QueryVariant.tenant_id == repository.tenant.id,
                QueryVariant.project_id == project.id,
                QueryVariant.status == "pending",
            )
        ).all()
        pending_normalized = set(pending_rows)
        blocked = sorted(
            {text for text in draft_texts if normalize_query(text) in pending_normalized}
        )
        if blocked:
            raise HTTPException(
                status_code=409,
                detail={"code": "variants_pending_confirmation", "texts": blocked},
            )
    config = session.scalar(
        select(MonitoringConfig).where(
            MonitoringConfig.tenant_id == repository.tenant.id,
            MonitoringConfig.project_id == project.id,
        )
    )
    if config is None:
        config = MonitoringConfig(
            pub_id=new_pub_id("cfg"), tenant_id=repository.tenant.id, project_id=project.id
        )
        session.add(config)
        session.flush()
    snapshot = body.model_dump(mode="json")
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision = config.current_version + 1
    frozen_at = datetime.now(UTC)
    version = MonitoringConfigVersion(
        pub_id=new_pub_id("cfv"),
        tenant_id=repository.tenant.id,
        config_id=config.id,
        revision=revision,
        effective_at=body.effective_at,
        frozen_at=frozen_at,
        snapshot_json=canonical,
        snapshot_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    config.current_version = revision
    config.state = "frozen"
    session.add(version)
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.actor_pub_id,
            action=receipt_action,
            resource_type="monitoring_config_version",
            resource_pub_id=version.pub_id,
            receipt=json.dumps({"config_version_pub_id": version.pub_id}),
        )
    )
    session.commit()
    return FrozenConfigView(
        pub_id=version.pub_id,
        revision=revision,
        effective_at=version.effective_at,
        frozen_at=frozen_at,
        snapshot_hash=version.snapshot_hash,
        snapshot=snapshot,
    )


@router.get("/{project_pub_id}/config/versions", response_model=list[FrozenConfigView])
def list_config_versions(
    project_pub_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[FrozenConfigView]:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id,
            Project.pub_id == project_pub_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    rows = session.scalars(
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == repository.tenant.id,
            MonitoringConfig.project_id == project.id,
        )
        .order_by(MonitoringConfigVersion.revision.desc())
        .limit(limit)
    ).all()
    return [_frozen_config_view(row) for row in rows]


@router.get("/{project_pub_id}/config/current", response_model=CurrentConfigView)
def current_config(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CurrentConfigView:
    """Resolve the effective revision and nearest future revision server-side.

    This endpoint is intentionally read-only.  It preserves v1 snapshot bytes
    and hash while giving the service workspace deterministic time semantics.
    Session 04 can extend the same projection to the canonical v2 store once
    activation and commercial admission are wired.
    """

    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id,
            Project.pub_id == project_pub_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    now = datetime.now(UTC)
    base = (
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == repository.tenant.id,
            MonitoringConfigVersion.frozen_at.is_not(None),
            MonitoringConfig.project_id == project.id,
        )
    )
    effective = session.scalar(
        base.where(MonitoringConfigVersion.effective_at <= now)
        .order_by(
            MonitoringConfigVersion.effective_at.desc(),
            MonitoringConfigVersion.revision.desc(),
        )
        .limit(1)
    )
    pending = session.scalar(
        base.where(MonitoringConfigVersion.effective_at > now)
        .order_by(
            MonitoringConfigVersion.effective_at.asc(),
            MonitoringConfigVersion.revision.asc(),
        )
        .limit(1)
    )
    return CurrentConfigView(
        effective=_frozen_config_view(effective) if effective is not None else None,
        next_pending=_frozen_config_view(pending) if pending is not None else None,
    )
