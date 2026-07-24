# ruff: noqa: B008

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import Membership, ServiceCredential, Tenant, User
from .policy import Principal, Role, get_principal

router = APIRouter(prefix="/api/v2/identity", tags=["identity"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapRequest(StrictModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=200)


class SessionView(StrictModel):
    tenant_pub_id: str
    user_pub_id: str
    role: Role
    permissions: list[str]


class MemberCreate(StrictModel):
    subject: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=200)
    role: Role


class MemberView(StrictModel):
    pub_id: str
    user_pub_id: str
    subject: str
    display_name: str
    role: Role
    state: str
    service_account: bool


class ServiceAccountCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    expires_in_hours: int = Field(default=24 * 30, ge=1, le=24 * 365)


class ServiceAccountCredential(MemberView):
    token: str
    expires_at: datetime


@router.post("/bootstrap", response_model=SessionView, status_code=201)
def bootstrap(
    body: BootstrapRequest,
    bootstrap_secret: str = Header(alias="X-Bootstrap-Secret"),
    session: Session = Depends(get_db),
) -> SessionView:
    settings = get_settings()
    if settings.env not in {"development", "test"} or not hmac.compare_digest(
        bootstrap_secret, settings.bootstrap_secret
    ):
        raise HTTPException(status_code=403, detail={"code": "bootstrap_forbidden"})
    tenant = Tenant(pub_id=new_pub_id("tnt"), name=body.tenant_name)
    user = User(
        pub_id=new_pub_id("usr"),
        subject=body.subject,
        display_name=body.display_name,
        is_service_account=False,
    )
    session.add_all([tenant, user])
    session.flush()
    session.add(
        Membership(
            pub_id=new_pub_id("mbr"),
            tenant_id=tenant.id,
            user_id=user.id,
            role=Role.ADMIN.value,
        )
    )
    session.commit()
    return SessionView(
        tenant_pub_id=tenant.pub_id, user_pub_id=user.pub_id, role=Role.ADMIN, permissions=["*"]
    )


@router.get("/session", response_model=SessionView)
def session_view(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> SessionView:
    membership = session.scalar(
        select(Membership)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Tenant.pub_id == principal.tenant_pub_id,
            User.subject == principal.subject,
            Membership.state == "active",
            Membership.revoked_at.is_(None),
        )
    )
    if membership is None or membership.role != principal.role.value:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    user = session.get(User, membership.user_id)
    assert user is not None
    from .policy import ROLE_PERMISSIONS

    return SessionView(
        tenant_pub_id=principal.tenant_pub_id,
        user_pub_id=user.pub_id,
        role=principal.role,
        permissions=sorted(ROLE_PERMISSIONS[principal.role]),
    )


def require_admin(principal: Principal) -> None:
    if principal.role is not Role.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "admin_required"})


@router.get("/members", response_model=list[MemberView])
def list_members(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[MemberView]:
    require_admin(principal)
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(Tenant.pub_id == principal.tenant_pub_id)
        .order_by(Membership.created_at)
    ).all()
    return [
        MemberView(
            pub_id=membership.pub_id,
            user_pub_id=user.pub_id,
            subject=user.subject,
            display_name=user.display_name,
            role=Role(membership.role),
            state=membership.state,
            service_account=user.is_service_account,
        )
        for membership, user in rows
    ]


@router.post("/members", response_model=MemberView, status_code=201)
def create_member(
    body: MemberCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> MemberView:
    require_admin(principal)
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == principal.tenant_pub_id))
    assert tenant is not None
    user = session.scalar(select(User).where(User.subject == body.subject))
    if user is None:
        user = User(
            pub_id=new_pub_id("usr"),
            subject=body.subject,
            display_name=body.display_name,
            is_service_account=False,
        )
        session.add(user)
        session.flush()
    existing = session.scalar(
        select(Membership).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "membership_exists"})
    membership = Membership(
        pub_id=new_pub_id("mbr"),
        tenant_id=tenant.id,
        user_id=user.id,
        role=body.role.value,
    )
    session.add(membership)
    session.commit()
    return MemberView(
        pub_id=membership.pub_id,
        user_pub_id=user.pub_id,
        subject=user.subject,
        display_name=user.display_name,
        role=body.role,
        state=membership.state,
        service_account=False,
    )


@router.post("/service-accounts", response_model=ServiceAccountCredential, status_code=201)
def create_service_account(
    body: ServiceAccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ServiceAccountCredential:
    require_admin(principal)
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == principal.tenant_pub_id))
    assert tenant is not None
    subject = f"service:{new_pub_id('svc')}"
    user = User(
        pub_id=new_pub_id("usr"),
        subject=subject,
        display_name=body.name,
        is_service_account=True,
    )
    session.add(user)
    session.flush()
    membership = Membership(
        pub_id=new_pub_id("mbr"),
        tenant_id=tenant.id,
        user_id=user.id,
        role=Role.WORKER.value,
    )
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(hours=body.expires_in_hours)
    session.add_all(
        [
            membership,
            ServiceCredential(
                pub_id=new_pub_id("scr"),
                tenant_id=tenant.id,
                user_id=user.id,
                secret_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=expires_at,
            ),
        ]
    )
    session.commit()
    return ServiceAccountCredential(
        pub_id=membership.pub_id,
        user_pub_id=user.pub_id,
        subject=user.subject,
        display_name=user.display_name,
        role=Role.WORKER,
        state=membership.state,
        service_account=True,
        token=token,
        expires_at=expires_at,
    )


@router.post("/members/{membership_pub_id}/revoke", response_model=MemberView)
def revoke_member(
    membership_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> MemberView:
    require_admin(principal)
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Tenant.pub_id == principal.tenant_pub_id,
            Membership.pub_id == membership_pub_id,
            Membership.revoked_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "membership_not_found"})
    membership, user = row
    if user.subject == principal.subject:
        active_admins = session.scalar(
            select(func.count())
            .select_from(Membership)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Tenant.pub_id == principal.tenant_pub_id,
                Membership.role == Role.ADMIN.value,
                Membership.revoked_at.is_(None),
            )
        )
        if active_admins == 1:
            raise HTTPException(status_code=409, detail={"code": "last_admin"})
    membership.state = "revoked"
    membership.revoked_at = datetime.now(UTC)
    if user.is_service_account:
        session.query(ServiceCredential).filter(
            ServiceCredential.user_id == user.id,
            ServiceCredential.revoked_at.is_(None),
        ).update({"revoked_at": datetime.now(UTC)})
    session.commit()
    return MemberView(
        pub_id=membership.pub_id,
        user_pub_id=user.pub_id,
        subject=user.subject,
        display_name=user.display_name,
        role=Role(membership.role),
        state=membership.state,
        service_account=user.is_service_account,
    )
