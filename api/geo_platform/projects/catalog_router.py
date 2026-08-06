# ruff: noqa: B008
# mypy: disable-error-code="arg-type,attr-defined"

import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import (
    Brand,
    BrandAlias,
    BrandAsset,
    ChangeRequest,
    ClientGoal,
    Competitor,
    Project,
    QueryGroup,
    QueryItem,
)

router = APIRouter(prefix="/api/v2/projects", tags=["project-catalog"])
ResourceKind = Literal[
    "brands",
    "aliases",
    "assets",
    "competitors",
    "query-groups",
    "query-items",
    "goals",
    "change-requests",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceWrite(StrictModel):
    parent_pub_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=500)
    text: str | None = None
    website: str | None = Field(default=None, max_length=500)
    kind: str | None = Field(default=None, max_length=80)
    uri: str | None = Field(default=None, max_length=500)
    sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    priority: int | None = Field(default=None, ge=0, le=10000)
    metric: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    state: str | None = Field(default=None, max_length=30)
    expected_version: int | None = Field(default=None, ge=1)


class ResourceView(StrictModel):
    pub_id: str
    project_pub_id: str
    resource_kind: str
    version: int
    data: dict[str, Any]


MODEL_BY_KIND = {
    "brands": Brand,
    "aliases": BrandAlias,
    "assets": BrandAsset,
    "competitors": Competitor,
    "query-groups": QueryGroup,
    "query-items": QueryItem,
    "goals": ClientGoal,
    "change-requests": ChangeRequest,
}


def require_project(session: Session, tenant_id: object, project_pub_id: str) -> Project:
    project = session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.pub_id == project_pub_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return project


def project_for_resource(session: Session, kind: ResourceKind, resource: Any) -> Project:
    if kind in {"brands", "competitors", "query-groups", "goals", "change-requests"}:
        project = session.get(Project, resource.project_id)
    elif kind in {"aliases", "assets"}:
        brand = session.get(Brand, resource.brand_id)
        project = session.get(Project, brand.project_id) if brand else None
    else:
        group = session.get(QueryGroup, resource.group_id)
        project = session.get(Project, group.project_id) if group else None
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "resource_parent_not_found"})
    return project


def serialize(kind: ResourceKind, item: Any, project: Project) -> ResourceView:
    if kind == "brands":
        data = {"name": item.name, "website": item.website}
    elif kind == "aliases":
        data = {"parent_pub_id": session_pub(item, Brand, "brand_id"), "value": item.value}
    elif kind == "assets":
        data = {
            "parent_pub_id": session_pub(item, Brand, "brand_id"),
            "kind": item.kind,
            "uri": item.uri,
            "sha256": item.sha256,
        }
    elif kind == "competitors":
        data = {"name": item.name, "website": item.website}
    elif kind == "query-groups":
        data = {"name": item.name}
    elif kind == "query-items":
        data = {
            "parent_pub_id": session_pub(item, QueryGroup, "group_id"),
            "text": item.text,
            "priority": item.priority,
        }
    elif kind == "goals":
        data = {
            "metric": item.metric,
            "payload": json.loads(item.target_json),
            "state": item.state,
        }
    else:
        data = {
            "kind": item.kind,
            "payload": json.loads(item.requested_json),
            "state": item.state,
            "reviewed_by": item.reviewed_by,
        }
    return ResourceView(
        pub_id=item.pub_id,
        project_pub_id=project.pub_id,
        resource_kind=kind,
        version=item.version,
        data=data,
    )


def session_pub(item: Any, model: Any, field: str) -> str:
    session = __import__("sqlalchemy").inspect(item).session
    parent = session.get(model, getattr(item, field)) if session else None
    return parent.pub_id if parent else ""


def build_resource(
    session: Session,
    tenant_id: object,
    project: Project,
    kind: ResourceKind,
    body: ResourceWrite,
) -> Any:
    common = {"pub_id": new_pub_id("ent"), "tenant_id": tenant_id}
    if kind == "brands":
        if not body.name:
            raise HTTPException(status_code=422, detail={"code": "name_required"})
        return Brand(**common, project_id=project.id, name=body.name, website=body.website)
    if kind in {"aliases", "assets"}:
        brand = session.scalar(
            select(Brand).where(
                Brand.tenant_id == tenant_id,
                Brand.project_id == project.id,
                Brand.pub_id == body.parent_pub_id,
            )
        )
        if brand is None:
            raise HTTPException(status_code=404, detail={"code": "brand_not_found"})
        if kind == "aliases":
            if not body.value:
                raise HTTPException(status_code=422, detail={"code": "value_required"})
            return BrandAlias(**common, brand_id=brand.id, value=body.value)
        if not body.kind or not body.uri:
            raise HTTPException(status_code=422, detail={"code": "asset_fields_required"})
        return BrandAsset(
            **common, brand_id=brand.id, kind=body.kind, uri=body.uri, sha256=body.sha256
        )
    if kind == "competitors":
        if not body.name:
            raise HTTPException(status_code=422, detail={"code": "name_required"})
        return Competitor(**common, project_id=project.id, name=body.name, website=body.website)
    if kind == "query-groups":
        if not body.name:
            raise HTTPException(status_code=422, detail={"code": "name_required"})
        return QueryGroup(**common, project_id=project.id, name=body.name)
    if kind == "query-items":
        group = session.scalar(
            select(QueryGroup).where(
                QueryGroup.tenant_id == tenant_id,
                QueryGroup.project_id == project.id,
                QueryGroup.pub_id == body.parent_pub_id,
            )
        )
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "query_group_not_found"})
        if not body.text:
            raise HTTPException(status_code=422, detail={"code": "text_required"})
        return QueryItem(
            **common,
            group_id=group.id,
            text=body.text,
            priority=body.priority if body.priority is not None else 100,
        )
    if kind == "goals":
        if not body.metric:
            raise HTTPException(status_code=422, detail={"code": "metric_required"})
        return ClientGoal(
            **common,
            project_id=project.id,
            metric=body.metric,
            target_json=json.dumps(body.payload),
            state=body.state or "draft",
        )
    if not body.kind:
        raise HTTPException(status_code=422, detail={"code": "change_kind_required"})
    return ChangeRequest(
        **common,
        project_id=project.id,
        kind=body.kind,
        requested_json=json.dumps(body.payload),
        state=body.state or "pending",
    )


def scoped_resources(
    session: Session, tenant_id: object, project: Project, kind: ResourceKind
) -> list[Any]:
    model = MODEL_BY_KIND[kind]
    statement = select(model).where(model.tenant_id == tenant_id)
    if kind in {"brands", "competitors", "query-groups", "goals", "change-requests"}:
        statement = statement.where(model.project_id == project.id)
    elif kind in {"aliases", "assets"}:
        statement = statement.join(Brand, model.brand_id == Brand.id).where(
            Brand.project_id == project.id
        )
    else:
        statement = statement.join(QueryGroup, model.group_id == QueryGroup.id).where(
            QueryGroup.project_id == project.id
        )
    return list(session.scalars(statement.order_by(model.created_at, model.id)).all())


@router.get("/{project_pub_id}/resources/{kind}", response_model=list[ResourceView])
def list_project_resources(
    project_pub_id: str,
    kind: ResourceKind,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ResourceView]:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = require_project(session, repository.tenant.id, project_pub_id)
    rows = scoped_resources(session, repository.tenant.id, project, kind)
    if cursor:
        rows = [item for item in rows if item.pub_id > cursor]
    return [serialize(kind, item, project) for item in rows[:limit]]


@router.post("/{project_pub_id}/resources/{kind}", response_model=ResourceView, status_code=201)
def create_project_resource(
    project_pub_id: str,
    kind: ResourceKind,
    body: ResourceWrite,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ResourceView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = require_project(session, repository.tenant.id, project_pub_id)
    digest = hashlib.sha256(f"{kind}:{idempotency_key}".encode()).hexdigest()
    action = f"project_resource.created:{digest}"
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id,
            AuditLog.action == action,
        )
    )
    if prior:
        model = MODEL_BY_KIND[kind]
        item = session.scalar(
            select(model).where(
                model.tenant_id == repository.tenant.id,
                model.pub_id == prior.resource_pub_id,
            )
        )
        if item is not None:
            return serialize(kind, item, project)
    item = build_resource(session, repository.tenant.id, project, kind, body)
    session.add(item)
    session.flush()
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.actor_pub_id,
            action=action,
            resource_type=kind,
            resource_pub_id=item.pub_id,
            receipt=json.dumps({"project_pub_id": project.pub_id}),
        )
    )
    session.commit()
    return serialize(kind, item, project)


@router.patch("/{project_pub_id}/resources/{kind}/{resource_pub_id}", response_model=ResourceView)
def update_project_resource(
    project_pub_id: str,
    kind: ResourceKind,
    resource_pub_id: str,
    body: ResourceWrite,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ResourceView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = require_project(session, repository.tenant.id, project_pub_id)
    model = MODEL_BY_KIND[kind]
    item = session.scalar(
        select(model).where(
            model.tenant_id == repository.tenant.id, model.pub_id == resource_pub_id
        )
    )
    if item is None or project_for_resource(session, kind, item).id != project.id:
        raise HTTPException(status_code=404, detail={"code": "resource_not_found"})
    if body.expected_version is None or item.version != body.expected_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict"})
    fields = (
        "name",
        "value",
        "text",
        "website",
        "uri",
        "sha256",
        "priority",
        "metric",
        "state",
    )
    for field in fields:
        value = getattr(body, field)
        if value is not None and hasattr(item, field):
            setattr(item, field, value)
    if kind == "goals" and body.payload:
        item.target_json = json.dumps(body.payload)
    if kind == "change-requests" and body.payload:
        item.requested_json = json.dumps(body.payload)
    item.version += 1
    session.commit()
    return serialize(kind, item, project)


@router.delete("/{project_pub_id}/resources/{kind}/{resource_pub_id}", status_code=204)
def delete_project_resource(
    project_pub_id: str,
    kind: ResourceKind,
    resource_pub_id: str,
    expected_version: int = Query(ge=1),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> None:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = require_project(session, repository.tenant.id, project_pub_id)
    model = MODEL_BY_KIND[kind]
    item = session.scalar(
        select(model).where(
            model.tenant_id == repository.tenant.id, model.pub_id == resource_pub_id
        )
    )
    if item is None or project_for_resource(session, kind, item).id != project.id:
        raise HTTPException(status_code=404, detail={"code": "resource_not_found"})
    if item.version != expected_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict"})
    session.delete(item)
    session.commit()
