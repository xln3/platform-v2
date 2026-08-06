# ruff: noqa: B008

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Form, Header, HTTPException, Path, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import (
    AuditLog,
    Membership,
    OidcIdentityBinding,
    ServiceCredential,
    Tenant,
    User,
)
from .browser_oidc import (
    BrowserOidcConfig,
    BrowserOidcError,
    BrowserOidcFlow,
    BrowserOidcUnavailableError,
)
from .native_session import create_native_session, revoke_native_session, set_native_password
from .oidc import OidcUnavailableError, normalize_oidc_issuer
from .policy import (
    Principal,
    Role,
    _oidc_session_principal,
    _oidc_verifier,
    get_principal,
)

router = APIRouter(prefix="/api/v2/identity", tags=["identity"])
OIDC_ACCESS_COOKIE = "__Host-geo_oidc"
OIDC_TRANSACTION_COOKIE = "__Host-geo_oidc_tx"


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


class OidcBindingCreate(StrictModel):
    subject: str = Field(min_length=1, max_length=512)


class OidcBindingView(StrictModel):
    user_pub_id: str
    active: bool
    created_at: datetime
    revoked_at: datetime | None


class NativeLoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(max_length=256)


class NativePasswordSet(StrictModel):
    password: str = Field(max_length=256)


def _browser_oidc_flow() -> BrowserOidcFlow:
    settings = get_settings()
    return BrowserOidcFlow(
        BrowserOidcConfig(
            issuer=settings.oidc_issuer,
            authorization_endpoint=settings.oidc_authorization_endpoint,
            token_endpoint=settings.oidc_token_endpoint,
            client_id=settings.oidc_client_id,
            redirect_uri=settings.oidc_redirect_uri,
            post_login_uri=settings.oidc_post_login_uri,
            cookie_key_file=settings.oidc_browser_cookie_key_file,
        )
    )


def _require_oidc_mode() -> None:
    if get_settings().identity_mode != "oidc":
        raise HTTPException(status_code=503, detail={"code": "identity_provider_unavailable"})


@router.get("/login", include_in_schema=False)
def oidc_login() -> RedirectResponse:
    _require_oidc_mode()
    try:
        request = _browser_oidc_flow().authorization_request()
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "identity_provider_unavailable"}
        ) from exc
    response = RedirectResponse(request.url, status_code=302)
    response.set_cookie(
        OIDC_TRANSACTION_COOKIE,
        request.transaction_cookie,
        max_age=300,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/callback", include_in_schema=False)
async def oidc_callback(
    code: str | None = Form(default=None),
    state: str | None = Form(default=None),
    transaction_cookie: str | None = Cookie(default=None, alias=OIDC_TRANSACTION_COOKIE),
    session: Session = Depends(get_db),
) -> RedirectResponse:
    _require_oidc_mode()
    if code is None or state is None or transaction_cookie is None:
        raise HTTPException(status_code=401, detail={"code": "session_invalid"})
    try:
        flow = _browser_oidc_flow()
        verifier = flow.consume_transaction(transaction_cookie, state)
        exchange = await flow.exchange(code, verifier)
        identity = _oidc_verifier().verify(exchange.access_token)
        _oidc_session_principal(session, identity)
    except (BrowserOidcError, OidcUnavailableError) as exc:
        raise HTTPException(status_code=401, detail={"code": "session_invalid"}) from exc
    except (BrowserOidcUnavailableError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail={"code": "identity_provider_unavailable"}
        ) from exc
    response = RedirectResponse(flow.config.post_login_uri, status_code=303)
    response.set_cookie(
        OIDC_ACCESS_COOKIE,
        exchange.access_token,
        max_age=exchange.max_age_seconds,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        OIDC_TRANSACTION_COOKIE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/login", response_model=SessionView)
def native_login(
    body: NativeLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> SessionView:
    if get_settings().identity_mode != "native_session":
        raise HTTPException(status_code=503, detail={"code": "identity_provider_unavailable"})
    try:
        token, identity, expires_at = create_native_session(
            session,
            email=body.email,
            password=body.password,
            network_label=request.client.host if request.client else "unknown",
        )
        role = Role(identity.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_credentials"}) from exc
    except PermissionError as exc:
        code = str(exc)
        status = 429 if code == "login_rate_limited" else 401
        raise HTTPException(status_code=status, detail={"code": code}) from exc
    production = get_settings().env in {"production", "prod"}
    response.set_cookie(
        "__Host-geo_session" if production else "geo_session",
        token,
        max_age=max(1, int((expires_at - datetime.now(UTC)).total_seconds())),
        secure=production,
        httponly=True,
        samesite="strict",
        path="/",
    )
    from .policy import ROLE_PERMISSIONS

    return SessionView(
        tenant_pub_id=identity.tenant_pub_id,
        user_pub_id=identity.user_pub_id,
        role=role,
        permissions=sorted(ROLE_PERMISSIONS[role]),
    )


@router.post("/logout", status_code=204, include_in_schema=False)
def identity_logout(
    session: Session = Depends(get_db),
    native_token: str | None = Cookie(default=None, alias="__Host-geo_session"),
    development_native_token: str | None = Cookie(default=None, alias="geo_session"),
) -> Response:
    revoke_native_session(session, native_token or development_native_token)
    response = Response(status_code=204)
    for cookie_name in (
        OIDC_ACCESS_COOKIE,
        OIDC_TRANSACTION_COOKIE,
        "__Host-geo_session",
        "geo_session",
    ):
        response.delete_cookie(
            cookie_name,
            secure=cookie_name.startswith("__Host-"),
            httponly=True,
            samesite="strict" if "geo_session" in cookie_name else "lax",
            path="/",
        )
    return response


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


def _identity_tenant_and_actor(session: Session, principal: Principal) -> tuple[Tenant, User]:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == principal.tenant_pub_id))
    actor = session.scalar(
        select(User).where(
            User.subject == principal.subject,
            User.disabled_at.is_(None),
        )
    )
    if tenant is None or actor is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    return tenant, actor


def _binding_target(session: Session, tenant: Tenant, user_pub_id: str) -> tuple[Membership, User]:
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.tenant_id == tenant.id,
            User.pub_id == user_pub_id,
            User.disabled_at.is_(None),
            User.is_service_account.is_(False),
            Membership.state == "active",
            Membership.revoked_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "member_not_found"})
    return row[0], row[1]


@router.get("/oidc-bindings", response_model=list[OidcBindingView])
def list_oidc_bindings(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[OidcBindingView]:
    require_admin(principal)
    tenant, _ = _identity_tenant_and_actor(session, principal)
    rows = session.execute(
        select(OidcIdentityBinding, User)
        .join(User, User.id == OidcIdentityBinding.user_id)
        .where(OidcIdentityBinding.tenant_id == tenant.id)
        .order_by(OidcIdentityBinding.created_at, User.pub_id)
    ).all()
    return [
        OidcBindingView(
            user_pub_id=user.pub_id,
            active=binding.revoked_at is None,
            created_at=binding.created_at,
            revoked_at=binding.revoked_at,
        )
        for binding, user in rows
    ]


@router.put("/members/{user_pub_id}/oidc-binding", response_model=OidcBindingView)
def bind_oidc_identity(
    body: OidcBindingCreate,
    user_pub_id: str = Path(min_length=1, max_length=30),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> OidcBindingView:
    require_admin(principal)
    if body.subject != body.subject.strip() or any(
        ord(character) < 0x20 for character in body.subject
    ):
        raise HTTPException(status_code=422, detail={"code": "oidc_subject_invalid"})
    try:
        issuer = normalize_oidc_issuer(get_settings().oidc_issuer)
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "identity_provider_unavailable"}
        ) from exc
    tenant, actor = _identity_tenant_and_actor(session, principal)
    _, user = _binding_target(session, tenant, user_pub_id)
    binding = session.scalar(
        select(OidcIdentityBinding).where(
            OidcIdentityBinding.tenant_id == tenant.id,
            OidcIdentityBinding.user_id == user.id,
        )
    )
    if binding is not None and binding.revoked_at is None:
        raise HTTPException(status_code=409, detail={"code": "oidc_binding_exists"})
    now = datetime.now(UTC)
    if binding is None:
        binding = OidcIdentityBinding(
            tenant_id=tenant.id,
            user_id=user.id,
            issuer_sha256=hashlib.sha256(issuer.encode()).hexdigest(),
            subject_sha256=hashlib.sha256(body.subject.encode()).hexdigest(),
            created_at=now,
        )
        session.add(binding)
    else:
        binding.issuer_sha256 = hashlib.sha256(issuer.encode()).hexdigest()
        binding.subject_sha256 = hashlib.sha256(body.subject.encode()).hexdigest()
        binding.created_at = now
        binding.revoked_at = None
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant.id,
            actor_pub_id=actor.pub_id,
            action="identity.oidc_binding.created",
            resource_type="app_user",
            resource_pub_id=user.pub_id,
            receipt='{"raw_subject_persisted":false}',
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "oidc_subject_already_bound"}) from exc
    return OidcBindingView(
        user_pub_id=user.pub_id,
        active=True,
        created_at=binding.created_at,
        revoked_at=None,
    )


@router.delete("/members/{user_pub_id}/oidc-binding", response_model=OidcBindingView)
def revoke_oidc_identity(
    user_pub_id: str = Path(min_length=1, max_length=30),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> OidcBindingView:
    require_admin(principal)
    tenant, actor = _identity_tenant_and_actor(session, principal)
    _, user = _binding_target(session, tenant, user_pub_id)
    binding = session.scalar(
        select(OidcIdentityBinding).where(
            OidcIdentityBinding.tenant_id == tenant.id,
            OidcIdentityBinding.user_id == user.id,
        )
    )
    if binding is None:
        raise HTTPException(status_code=404, detail={"code": "oidc_binding_not_found"})
    if binding.revoked_at is None:
        binding.revoked_at = datetime.now(UTC)
        session.add(
            AuditLog(
                pub_id=new_pub_id("aud"),
                tenant_id=tenant.id,
                actor_pub_id=actor.pub_id,
                action="identity.oidc_binding.revoked",
                resource_type="app_user",
                resource_pub_id=user.pub_id,
                receipt='{"raw_subject_persisted":false}',
            )
        )
        session.commit()
    return OidcBindingView(
        user_pub_id=user.pub_id,
        active=False,
        created_at=binding.created_at,
        revoked_at=binding.revoked_at,
    )


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


@router.put("/members/{user_pub_id}/password", status_code=204)
def set_member_native_password(
    body: NativePasswordSet,
    user_pub_id: str = Path(min_length=1, max_length=30),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    require_admin(principal)
    tenant, actor = _identity_tenant_and_actor(session, principal)
    _, user = _binding_target(session, tenant, user_pub_id)
    try:
        set_native_password(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "password_policy_failed"}) from exc
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant.id,
            actor_pub_id=actor.pub_id,
            action="identity.native_password.rotated",
            resource_type="app_user",
            resource_pub_id=user.pub_id,
            receipt='{"all_browser_sessions_revoked":true}',
        )
    )
    session.commit()
    return Response(status_code=204)


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
