import hashlib

# ruff: noqa: B008
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tenancy.database import get_db
from ..tenancy.models import (
    Membership,
    OidcIdentityBinding,
    ServiceCredential,
    Tenant,
    User,
)
from ..tenancy.repository import set_tenant_context
from .native_session import authenticate_native_session
from .oidc import OidcIdentity, OidcUnavailableError, OidcVerifier


class Role(StrEnum):
    CUSTOMER = "customer"
    OPERATOR = "operator"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    WORKER = "worker"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.CUSTOMER: frozenset(
        {
            "project:read",
            "change:write",
            "account:authorize",
            "intake:read",
            "intake:write",
            "knowledge:resolve",
            "knowledge:observe",
        }
    ),
    Role.OPERATOR: frozenset(
        {
            "project:read",
            "project:write",
            "change:write",
            "collection:control",
            "schedule:read",
            "schedule:manage",
            "sla:manage",
            "account:read",
            "account:operate",
            "intervention:operate",
            "report:deliver",
            "formal_report:produce",
            "formal_report:read",
            "evidence:read",
            "intake:read",
            "intake:write",
            "operations:business:read",
            "sop:read",
            "sop:write",
            "knowledge:resolve",
            "knowledge:observe",
            "knowledge:read",
            "knowledge:propose",
            "knowledge:evidence",
            "metrics:publish",
            "metrics:recompute",
            "semantic:override",
        }
    ),
    Role.ANALYST: frozenset(
        {
            "project:read",
            "collection:read",
            "report:write",
            "formal_report:produce",
            "formal_report:read",
            "evidence:read",
            "schedule:read",
            "intelligence:read",
            "intelligence:write",
            "intake:read",
            "operations:business:read",
            "sop:read",
            "sop:write",
            "knowledge:resolve",
            "knowledge:observe",
            "knowledge:read",
            "knowledge:propose",
            "knowledge:evidence",
            "metrics:recompute",
        }
    ),
    Role.REVIEWER: frozenset(
        {
            "project:read",
            "collection:read",
            "account:read",
            "break_glass:approve",
            "report:review",
            "formal_report:read",
            "evidence:read",
            "report:publish",
            "report:deliver",
            "posting:approve",
            "schedule:read",
            "intelligence:read",
            "intelligence:review",
            "intake:read",
            "operations:business:read",
            "sop:read",
            "knowledge:resolve",
            "knowledge:observe",
            "knowledge:read",
            "knowledge:evidence",
            "knowledge:review",
            "knowledge:audit",
            "metrics:publish",
            "semantic:override",
        }
    ),
    Role.ADMIN: frozenset({"*"}),
    Role.WORKER: frozenset(
        {
            "collection:execute",
            "lease:acquire",
            "profile:use",
            "knowledge:resolve",
            "knowledge:observe",
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    tenant_pub_id: str
    user_pub_id: str | None = None

    def allows(self, permission: str) -> bool:
        allowed = ROLE_PERMISSIONS[self.role]
        return "*" in allowed or permission in allowed

    def require(self, permission: str) -> None:
        if not self.allows(permission):
            raise HTTPException(status_code=403, detail={"code": "permission_denied"})

    @property
    def actor_pub_id(self) -> str:
        if self.user_pub_id is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "identity_projection_incomplete"},
            )
        return self.user_pub_id


class SessionAdapter:
    """Replaceable development boundary for an OIDC-validated session."""

    def authenticate(self, subject: str, role: str, tenant_pub_id: str) -> Principal:
        try:
            parsed_role = Role(role)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": "invalid_role"}) from exc
        return Principal(subject=subject, role=parsed_role, tenant_pub_id=tenant_pub_id)


session_adapter = SessionAdapter()


@lru_cache(maxsize=1)
def _oidc_verifier() -> OidcVerifier:
    settings = get_settings()
    return OidcVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.oidc_jwks_url,
        algorithms=tuple(
            item.strip() for item in settings.oidc_algorithms.split(",") if item.strip()
        ),
        tenant_claim=settings.oidc_tenant_claim,
        max_token_lifetime_seconds=settings.oidc_max_token_lifetime_seconds,
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
    )


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=401, detail={"code": "session_invalid"})
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme != "Bearer"
        or not token
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        raise HTTPException(status_code=401, detail={"code": "session_invalid"})
    return token


def _oidc_access_token(authorization: str | None, browser_cookie: str | None) -> str:
    if authorization is not None and browser_cookie is not None:
        raise HTTPException(status_code=401, detail={"code": "session_invalid"})
    if browser_cookie is not None:
        if (
            not browser_cookie
            or len(browser_cookie) > 3800
            or any(character.isspace() for character in browser_cookie)
            or browser_cookie.count(".") != 2
        ):
            raise HTTPException(status_code=401, detail={"code": "session_invalid"})
        return browser_cookie
    return _bearer_token(authorization)


def _oidc_session_principal(
    session: Session, identity: OidcIdentity
) -> tuple[Principal, Membership, User]:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == identity.tenant_pub_id))
    if tenant is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    issuer_sha256 = hashlib.sha256(identity.issuer.encode()).hexdigest()
    subject_sha256 = hashlib.sha256(identity.subject.encode()).hexdigest()
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(
            OidcIdentityBinding,
            OidcIdentityBinding.user_id == User.id,
        )
        .where(
            Membership.tenant_id == tenant.id,
            OidcIdentityBinding.tenant_id == tenant.id,
            OidcIdentityBinding.issuer_sha256 == issuer_sha256,
            OidcIdentityBinding.subject_sha256 == subject_sha256,
            OidcIdentityBinding.revoked_at.is_(None),
            User.disabled_at.is_(None),
            User.is_service_account.is_(False),
            Membership.state == "active",
            Membership.revoked_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    membership, user = row
    try:
        role = Role(membership.role)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"code": "invalid_role"}) from exc
    return Principal(user.subject, role, tenant.pub_id, user.pub_id), membership, user


def get_principal(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    x_actor_role: str | None = Header(default=None, alias="X-Actor-Role"),
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    native_token: str | None = Cookie(default=None, alias="__Host-geo_session"),
    development_native_token: str | None = Cookie(default=None, alias="geo_session"),
    oidc_browser_token: str | None = Cookie(default=None, alias="__Host-geo_oidc"),
    session: Session = Depends(get_db),
) -> Principal:
    settings = get_settings()
    if x_service_token is None and settings.identity_mode == "native_session":
        identity = authenticate_native_session(
            session,
            native_token if settings.env in {"production", "prod"} else development_native_token,
        )
        if identity is None:
            raise HTTPException(status_code=401, detail={"code": "session_invalid"})
        try:
            role = Role(identity.role)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": "invalid_role"}) from exc
        return Principal(identity.subject, role, identity.tenant_pub_id, identity.user_pub_id)
    if x_service_token is None and settings.identity_mode == "oidc":
        try:
            oidc_identity = _oidc_verifier().verify(
                _oidc_access_token(authorization, oidc_browser_token)
            )
        except OidcUnavailableError as exc:
            raise HTTPException(status_code=401, detail={"code": "session_invalid"}) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=503, detail={"code": "identity_provider_unavailable"}
            ) from exc
        principal, _, _ = _oidc_session_principal(session, oidc_identity)
        return principal
    if settings.env in {"production", "prod"} and settings.identity_mode == "trusted_headers":
        raise HTTPException(status_code=503, detail={"code": "identity_provider_unavailable"})
    if settings.identity_mode not in {
        "trusted_headers",
        "native_session",
        "oidc",
    }:
        raise HTTPException(status_code=503, detail={"code": "identity_provider_unavailable"})
    if x_tenant_id is None or x_actor_id is None or x_actor_role is None:
        raise HTTPException(status_code=401, detail={"code": "identity_headers_missing"})
    principal = session_adapter.authenticate(x_actor_id, x_actor_role, x_tenant_id)
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == x_tenant_id))
    if tenant is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    set_tenant_context(
        session,
        tenant_id=tenant.id,
        tenant_pub_id=tenant.pub_id,
    )
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Membership.tenant_id == tenant.id,
            User.subject == x_actor_id,
            User.disabled_at.is_(None),
            Membership.role == principal.role.value,
            Membership.state == "active",
            Membership.revoked_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    membership, user = row
    if settings.env in {"production", "prod"} and not user.is_service_account:
        # 生产环境身份头路径仅放行 service-account（下方强验 token）；其他主体
        # 一律拒绝——伪造 X-Tenant-Id/X-Actor-Id/X-Actor-Role 不得绕过 cookie 认证。
        raise HTTPException(status_code=401, detail={"code": "identity_headers_not_allowed"})
    if user.is_service_account:
        if not x_service_token:
            raise HTTPException(status_code=401, detail={"code": "service_token_required"})
        credential = session.scalar(
            select(ServiceCredential).where(
                ServiceCredential.tenant_id == membership.tenant_id,
                ServiceCredential.user_id == user.id,
                ServiceCredential.secret_hash
                == hashlib.sha256(x_service_token.encode()).hexdigest(),
                ServiceCredential.revoked_at.is_(None),
                ServiceCredential.expires_at > datetime.now(UTC),
            )
        )
        if credential is None:
            raise HTTPException(status_code=401, detail={"code": "service_token_invalid"})
    return Principal(user.subject, principal.role, tenant.pub_id, user.pub_id)
