# ruff: noqa: B008

import hashlib
import json
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.evidence.dlp import assert_secret_free

from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import (
    AssetConfirmationVersion,
    Brand,
    ClientProfileVersion,
    Competitor,
    Project,
)

router = APIRouter(prefix="/api/v2/projects", tags=["customer-confirmations"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientProfileWrite(StrictModel):
    company_name: str = Field(min_length=2, max_length=200)
    contact_role: str = Field(min_length=2, max_length=120)
    audience: str = Field(min_length=10, max_length=4000)
    public_statement: str = Field(min_length=10, max_length=4000)
    truth_confirmed: bool

    @field_validator("company_name", "contact_role", "audience", "public_statement")
    @classmethod
    def reject_secret_content(cls, value: str) -> str:
        assert_secret_free(value)
        return value

    @field_validator("truth_confirmed")
    @classmethod
    def require_truth_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("truth confirmation is required")
        return value


class ClientProfileView(StrictModel):
    pub_id: str
    project_pub_id: str
    revision: int
    company_name: str
    contact_role: str
    audience: str
    public_statement: str
    created_at: str


class ClientProfilePage(StrictModel):
    data: list[ClientProfileView]
    next_cursor: str | None


class AssetConfirmationWrite(StrictModel):
    brand_name: str = Field(min_length=2, max_length=200)
    website: HttpUrl
    product_name: str = Field(min_length=2, max_length=200)
    competitor_name: str = Field(min_length=2, max_length=200)
    prohibited_claim: str = Field(min_length=2, max_length=2000)
    truth_confirmed: bool

    @field_validator("brand_name", "product_name", "competitor_name", "prohibited_claim")
    @classmethod
    def reject_secret_content(cls, value: str) -> str:
        assert_secret_free(value)
        return value

    @field_validator("website")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("website must use HTTPS")
        assert_secret_free(str(value))
        return value

    @field_validator("truth_confirmed")
    @classmethod
    def require_truth_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("truth confirmation is required")
        return value


class AssetConfirmationView(StrictModel):
    pub_id: str
    project_pub_id: str
    revision: int
    brand_name: str
    website: str
    product_name: str
    competitor_name: str
    prohibited_claim: str
    created_at: str


class AssetConfirmationPage(StrictModel):
    data: list[AssetConfirmationView]
    next_cursor: str | None


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


def _payload_hash(body: BaseModel) -> str:
    canonical = body.model_dump_json(exclude={"truth_confirmed"})
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(
    session: Session,
    *,
    tenant_id: object,
    action: str,
    payload_hash: str,
    model: type[ClientProfileVersion] | type[AssetConfirmationVersion],
) -> ClientProfileVersion | AssetConfirmationVersion | None:
    prior = session.scalar(
        select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
    )
    if prior is None:
        return None
    receipt = json.loads(prior.receipt)
    if receipt.get("payload_hash") != payload_hash:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
    resource = cast(
        ClientProfileVersion | AssetConfirmationVersion | None,
        session.scalar(
            select(model).where(model.tenant_id == tenant_id, model.pub_id == prior.resource_pub_id)
        ),
    )
    if resource is None:
        raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
    return resource


def _audit(
    session: Session,
    *,
    tenant_id: Any,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_pub_id: str,
    project_pub_id: str,
    payload_hash: str,
) -> None:
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant_id,
            actor_pub_id=principal.actor_pub_id,
            action=action,
            resource_type=resource_type,
            resource_pub_id=resource_pub_id,
            receipt=json.dumps({"project_pub_id": project_pub_id, "payload_hash": payload_hash}),
        )
    )


def _profile_view(item: ClientProfileVersion, project: Project) -> ClientProfileView:
    return ClientProfileView(
        pub_id=item.pub_id,
        project_pub_id=project.pub_id,
        revision=item.revision,
        company_name=item.company_name,
        contact_role=item.contact_role,
        audience=item.audience,
        public_statement=item.public_statement,
        created_at=item.created_at.isoformat(),
    )


def _asset_view(item: AssetConfirmationVersion, project: Project) -> AssetConfirmationView:
    return AssetConfirmationView(
        pub_id=item.pub_id,
        project_pub_id=project.pub_id,
        revision=item.revision,
        brand_name=item.brand_name,
        website=item.website,
        product_name=item.product_name,
        competitor_name=item.competitor_name,
        prohibited_claim=item.prohibited_claim,
        created_at=item.created_at.isoformat(),
    )


@router.get("/{project_pub_id}/client-profile/versions", response_model=ClientProfilePage)
def list_client_profile_versions(
    project_pub_id: str,
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ClientProfilePage:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    statement = select(ClientProfileVersion).where(
        ClientProfileVersion.tenant_id == repository.tenant.id,
        ClientProfileVersion.project_id == project.id,
    )
    if cursor is not None:
        statement = statement.where(ClientProfileVersion.revision < cursor)
    rows = list(
        session.scalars(
            statement.order_by(ClientProfileVersion.revision.desc()).limit(limit + 1)
        ).all()
    )
    return ClientProfilePage(
        data=[_profile_view(row, project) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1].revision) if len(rows) > limit else None,
    )


@router.post(
    "/{project_pub_id}/client-profile/versions",
    response_model=ClientProfileView,
    status_code=201,
)
def create_client_profile_version(
    project_pub_id: str,
    body: ClientProfileWrite,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ClientProfileView:
    principal.require("change:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    payload_hash = _payload_hash(body)
    action = (
        "client_profile.created:"
        + hashlib.sha256(f"{project.pub_id}:{idempotency_key}".encode()).hexdigest()
    )
    replay = _replay(
        session,
        tenant_id=repository.tenant.id,
        action=action,
        payload_hash=payload_hash,
        model=ClientProfileVersion,
    )
    if isinstance(replay, ClientProfileVersion):
        return _profile_view(replay, project)
    revision = (
        session.scalar(
            select(func.max(ClientProfileVersion.revision)).where(
                ClientProfileVersion.tenant_id == repository.tenant.id,
                ClientProfileVersion.project_id == project.id,
            )
        )
        or 0
    ) + 1
    item = ClientProfileVersion(
        pub_id=new_pub_id("cpv"),
        tenant_id=repository.tenant.id,
        project_id=project.id,
        revision=revision,
        company_name=body.company_name,
        contact_role=body.contact_role,
        audience=body.audience,
        public_statement=body.public_statement,
        declared_by=principal.actor_pub_id,
    )
    session.add(item)
    session.flush()
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_type="client_profile_version",
        resource_pub_id=item.pub_id,
        project_pub_id=project.pub_id,
        payload_hash=payload_hash,
    )
    session.commit()
    return _profile_view(item, project)


@router.get("/{project_pub_id}/asset-confirmations", response_model=AssetConfirmationPage)
def list_asset_confirmations(
    project_pub_id: str,
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AssetConfirmationPage:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    statement = select(AssetConfirmationVersion).where(
        AssetConfirmationVersion.tenant_id == repository.tenant.id,
        AssetConfirmationVersion.project_id == project.id,
    )
    if cursor is not None:
        statement = statement.where(AssetConfirmationVersion.revision < cursor)
    rows = list(
        session.scalars(
            statement.order_by(AssetConfirmationVersion.revision.desc()).limit(limit + 1)
        ).all()
    )
    return AssetConfirmationPage(
        data=[_asset_view(row, project) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1].revision) if len(rows) > limit else None,
    )


@router.post(
    "/{project_pub_id}/asset-confirmations",
    response_model=AssetConfirmationView,
    status_code=201,
)
def create_asset_confirmation(
    project_pub_id: str,
    body: AssetConfirmationWrite,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AssetConfirmationView:
    principal.require("change:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    payload_hash = _payload_hash(body)
    action = (
        "asset_confirmation.created:"
        + hashlib.sha256(f"{project.pub_id}:{idempotency_key}".encode()).hexdigest()
    )
    replay = _replay(
        session,
        tenant_id=repository.tenant.id,
        action=action,
        payload_hash=payload_hash,
        model=AssetConfirmationVersion,
    )
    if isinstance(replay, AssetConfirmationVersion):
        return _asset_view(replay, project)
    revision = (
        session.scalar(
            select(func.max(AssetConfirmationVersion.revision)).where(
                AssetConfirmationVersion.tenant_id == repository.tenant.id,
                AssetConfirmationVersion.project_id == project.id,
            )
        )
        or 0
    ) + 1
    item = AssetConfirmationVersion(
        pub_id=new_pub_id("acv"),
        tenant_id=repository.tenant.id,
        project_id=project.id,
        revision=revision,
        brand_name=body.brand_name,
        website=str(body.website),
        product_name=body.product_name,
        competitor_name=body.competitor_name,
        prohibited_claim=body.prohibited_claim,
        declared_by=principal.actor_pub_id,
    )
    session.add(item)
    brand = session.scalar(
        select(Brand).where(
            Brand.tenant_id == repository.tenant.id,
            Brand.project_id == project.id,
            Brand.name == body.brand_name,
        )
    )
    if brand is None:
        session.add(
            Brand(
                pub_id=new_pub_id("brd"),
                tenant_id=repository.tenant.id,
                project_id=project.id,
                name=body.brand_name,
                website=str(body.website),
            )
        )
    competitor = session.scalar(
        select(Competitor).where(
            Competitor.tenant_id == repository.tenant.id,
            Competitor.project_id == project.id,
            Competitor.name == body.competitor_name,
        )
    )
    if competitor is None:
        session.add(
            Competitor(
                pub_id=new_pub_id("cmp"),
                tenant_id=repository.tenant.id,
                project_id=project.id,
                name=body.competitor_name,
                website=None,
            )
        )
    session.flush()
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_type="asset_confirmation_version",
        resource_pub_id=item.pub_id,
        project_pub_id=project.pub_id,
        payload_hash=payload_hash,
    )
    session.commit()
    return _asset_view(item, project)
