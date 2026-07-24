import hashlib

# ruff: noqa: B008
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.database import get_db
from ..tenancy.models import Membership, ServiceCredential, Tenant, User


class Role(StrEnum):
    CUSTOMER = "customer"
    OPERATOR = "operator"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    WORKER = "worker"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.CUSTOMER: frozenset({"project:read", "change:write", "account:authorize"}),
    Role.OPERATOR: frozenset(
        {
            "project:read",
            "project:write",
            "collection:control",
            "account:read",
            "account:operate",
            "intervention:operate",
        }
    ),
    Role.ANALYST: frozenset({"project:read", "collection:read"}),
    Role.REVIEWER: frozenset(
        {"project:read", "collection:read", "account:read", "break_glass:approve"}
    ),
    Role.ADMIN: frozenset({"*"}),
    Role.WORKER: frozenset({"collection:execute", "lease:acquire", "profile:use"}),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    tenant_pub_id: str

    def require(self, permission: str) -> None:
        allowed = ROLE_PERMISSIONS[self.role]
        if "*" not in allowed and permission not in allowed:
            raise HTTPException(status_code=403, detail={"code": "permission_denied"})


class SessionAdapter:
    """Replaceable development boundary for an OIDC-validated session."""

    def authenticate(self, subject: str, role: str, tenant_pub_id: str) -> Principal:
        try:
            parsed_role = Role(role)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": "invalid_role"}) from exc
        return Principal(subject=subject, role=parsed_role, tenant_pub_id=tenant_pub_id)


session_adapter = SessionAdapter()


def get_principal(
    x_tenant_id: str = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id"),
    x_actor_role: str = Header(alias="X-Actor-Role"),
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    session: Session = Depends(get_db),
) -> Principal:
    principal = session_adapter.authenticate(x_actor_id, x_actor_role, x_tenant_id)
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Tenant.pub_id == x_tenant_id,
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
    return principal
