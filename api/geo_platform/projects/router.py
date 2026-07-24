# ruff: noqa: B008
# mypy: disable-error-code="arg-type"

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contracts import PageMeta, ProjectPage, ProjectSummary
from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import Customer, MonitoringConfig, MonitoringConfigVersion, Project

router = APIRouter(prefix="/api/v2/projects", tags=["projects"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    customer_name: str = Field(min_length=1, max_length=200)


class ProjectPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: str | None = Field(default=None, pattern="^(draft|active|paused|archived)$")
    expected_version: int = Field(ge=1)


class ConfigDraft(StrictModel):
    query_groups: list[dict[str, Any]]
    regions: list[str]
    models: list[str]
    modes: list[str]
    frequency: str
    effective_at: datetime


class FrozenConfigView(StrictModel):
    pub_id: str
    revision: int
    effective_at: datetime
    frozen_at: datetime
    snapshot_hash: str
    snapshot: dict[str, Any]


def as_summary(project: Project, tenant_pub_id: str) -> ProjectSummary:
    return ProjectSummary(
        pub_id=project.pub_id,
        tenant_pub_id=tenant_pub_id,
        name=project.name,
        state=project.state,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=ProjectPage, operation_id="listProjects")
def list_projects(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProjectPage:
    principal.require("project:read")
    try:
        repository = TenantRepository(session, principal.tenant_pub_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "tenant_not_found"}) from exc
    statement = select(Project).where(Project.tenant_id == repository.tenant.id)
    if cursor:
        statement = statement.where(Project.pub_id > cursor)
    rows = session.scalars(statement.order_by(Project.pub_id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    data = rows[:limit]
    return ProjectPage(
        data=[as_summary(item, principal.tenant_pub_id) for item in data],
        page=PageMeta(next_cursor=data[-1].pub_id if has_more else None, has_more=has_more),
    )


@router.post("", response_model=ProjectSummary, status_code=201)
def create_project(
    body: ProjectCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProjectSummary:
    principal.require("project:write")
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
    )
    session.add(project)
    session.flush()
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.subject,
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
    del idempotency_key
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id, Project.pub_id == project_pub_id
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
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
    session.commit()
    return FrozenConfigView(
        pub_id=version.pub_id,
        revision=revision,
        effective_at=version.effective_at,
        frozen_at=frozen_at,
        snapshot_hash=version.snapshot_hash,
        snapshot=snapshot,
    )
