# ruff: noqa: B008

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import Membership, ServiceCredential, Tenant, User
from ..tenancy.repository import TenantRepository
from .models import (
    AccountAuthorization,
    CapabilityLease,
    PlatformAccount,
    SessionEvent,
)

router = APIRouter(prefix="/api/v2/collection/capability-leases", tags=["capability-leases"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityLeaseCreate(StrictModel):
    platform_account_pub_id: str
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    allowed_actions: list[str] = Field(min_length=1, max_length=20)
    authorization_scope: list[Literal["read", "query", "draft", "publish"]] = Field(min_length=1)
    subject_workflow_id: str = Field(min_length=8, max_length=500)
    ttl_seconds: int = Field(default=600, ge=30, le=1800)
    max_uses: int = Field(default=10, ge=1, le=1000)

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or "/" in domain or ":" in domain or domain.startswith("."):
                raise ValueError("allowed_domains must contain hostnames only")
            normalized.append(domain)
        return sorted(set(normalized))


class CapabilityLeaseValidate(StrictModel):
    tenant_pub_id: str
    platform_account_pub_id: str
    target_url: str = Field(min_length=8, max_length=2000)
    action: str = Field(min_length=1, max_length=80)
    required_scopes: list[str] = Field(min_length=1, max_length=10)
    workflow_id: str = Field(min_length=8, max_length=500)


class CapabilityLeaseView(StrictModel):
    lease_pub_id: str
    tenant_pub_id: str
    platform_account_pub_id: str
    allowed_domains: list[str]
    allowed_actions: list[str]
    authorization_scope: list[str]
    subject_workflow_id: str
    expires_at: datetime
    revoked_at: datetime | None
    max_uses: int
    use_count: int
    issuer: Literal["s01-session-gateway"] = "s01-session-gateway"


def view(lease: CapabilityLease, tenant: Tenant, account: PlatformAccount) -> CapabilityLeaseView:
    return CapabilityLeaseView(
        lease_pub_id=lease.pub_id,
        tenant_pub_id=tenant.pub_id,
        platform_account_pub_id=account.pub_id,
        allowed_domains=json.loads(lease.allowed_domains_json),
        allowed_actions=json.loads(lease.allowed_actions_json),
        authorization_scope=json.loads(lease.authorization_scope_json),
        subject_workflow_id=lease.subject_workflow_id,
        expires_at=lease.expires_at,
        revoked_at=lease.revoked_at,
        max_uses=lease.max_uses,
        use_count=lease.use_count,
    )


def active_service_tenant(
    session: Session, tenant_pub_id: str, service_token: str | None
) -> Tenant:
    if not service_token:
        raise HTTPException(status_code=401, detail={"code": "service_token_required"})
    now = datetime.now(UTC)
    row = session.execute(
        select(Tenant, ServiceCredential)
        .join(ServiceCredential, ServiceCredential.tenant_id == Tenant.id)
        .join(User, User.id == ServiceCredential.user_id)
        .join(
            Membership,
            (Membership.user_id == User.id) & (Membership.tenant_id == Tenant.id),
        )
        .where(
            Tenant.pub_id == tenant_pub_id,
            Tenant.state == "active",
            User.is_service_account.is_(True),
            User.disabled_at.is_(None),
            Membership.role == "worker",
            Membership.state == "active",
            Membership.revoked_at.is_(None),
            ServiceCredential.secret_hash == hashlib.sha256(service_token.encode()).hexdigest(),
            ServiceCredential.revoked_at.is_(None),
            ServiceCredential.expires_at > now,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail={"code": "service_token_invalid"})
    tenant, _credential = row
    assert isinstance(tenant, Tenant)
    return tenant


def audit(
    session: Session,
    tenant_id: object,
    account_id: object,
    event_type: str,
    lease_pub_id: str,
    result: str,
) -> None:
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=tenant_id,
            account_id=account_id,
            event_type=event_type,
            summary_json=json.dumps({"lease_pub_id": lease_pub_id, "result": result}),
        )
    )


@router.post("", response_model=CapabilityLeaseView, status_code=201)
def issue_capability_lease(
    body: CapabilityLeaseCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CapabilityLeaseView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.tenant_id == repository.tenant.id,
            PlatformAccount.pub_id == body.platform_account_pub_id,
            PlatformAccount.state == "active",
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"code": "account_not_active"})
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    requested_scopes = set(body.authorization_scope)
    if authorization is None or not requested_scopes.issubset(
        set(json.loads(authorization.scopes_json))
    ):
        raise HTTPException(status_code=403, detail={"code": "scope_not_authorized"})
    forbidden = set(json.loads(authorization.forbidden_actions_json))
    if forbidden.intersection(body.allowed_actions):
        raise HTTPException(status_code=403, detail={"code": "action_forbidden"})
    lease = CapabilityLease(
        pub_id=new_pub_id("cpl"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        issued_by=principal.subject,
        subject_workflow_id=body.subject_workflow_id,
        allowed_domains_json=json.dumps(body.allowed_domains),
        allowed_actions_json=json.dumps(sorted(set(body.allowed_actions))),
        authorization_scope_json=json.dumps(sorted(requested_scopes)),
        expires_at=min(now + timedelta(seconds=body.ttl_seconds), authorization.valid_until),
        max_uses=body.max_uses,
    )
    session.add(lease)
    session.flush()
    audit(
        session,
        repository.tenant.id,
        account.id,
        "capability_lease.issued",
        lease.pub_id,
        "issued",
    )
    session.commit()
    return view(lease, repository.tenant, account)


@router.post("/{lease_pub_id}/revoke", response_model=CapabilityLeaseView)
def revoke_capability_lease(
    lease_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CapabilityLeaseView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(CapabilityLease, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == CapabilityLease.account_id)
        .where(
            CapabilityLease.tenant_id == repository.tenant.id,
            CapabilityLease.pub_id == lease_pub_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "capability_lease_not_found"})
    lease, account = row
    lease.revoked_at = lease.revoked_at or datetime.now(UTC)
    audit(
        session,
        repository.tenant.id,
        account.id,
        "capability_lease.revoked",
        lease.pub_id,
        "revoked",
    )
    session.commit()
    return view(lease, repository.tenant, account)


@router.post("/{lease_pub_id}/validate", response_model=CapabilityLeaseView)
def validate_capability_lease(
    lease_pub_id: str,
    body: CapabilityLeaseValidate,
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    session: Session = Depends(get_db),
) -> CapabilityLeaseView:
    tenant = active_service_tenant(session, body.tenant_pub_id, x_service_token)
    row = session.execute(
        select(CapabilityLease, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == CapabilityLease.account_id)
        .where(
            CapabilityLease.tenant_id == tenant.id,
            CapabilityLease.pub_id == lease_pub_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "capability_lease_not_found"})
    lease, account = row
    now = datetime.now(UTC)
    domains = set(json.loads(lease.allowed_domains_json))
    actions = set(json.loads(lease.allowed_actions_json))
    scopes = set(json.loads(lease.authorization_scope_json))
    hostname = (urlsplit(body.target_url).hostname or "").lower()
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    denial = None
    if account.pub_id != body.platform_account_pub_id:
        denial = "wrong_account"
    elif account.state != "active":
        denial = "account_inactive"
    elif lease.revoked_at is not None:
        denial = "revoked"
    elif now >= lease.expires_at:
        denial = "expired"
    elif lease.subject_workflow_id != body.workflow_id:
        denial = "wrong_workflow"
    elif body.action not in actions:
        denial = "wrong_action"
    elif not set(body.required_scopes).issubset(scopes):
        denial = "wrong_scope"
    elif authorization is None or not scopes.issubset(set(json.loads(authorization.scopes_json))):
        denial = "authorization_invalid"
    elif not hostname or not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    ):
        denial = "wrong_domain"
    elif lease.use_count >= lease.max_uses:
        denial = "use_limit"
    if denial:
        audit(
            session,
            tenant.id,
            account.id,
            "capability_lease.validation_denied",
            lease.pub_id,
            denial,
        )
        session.commit()
        raise HTTPException(status_code=403, detail={"code": f"capability_lease_{denial}"})
    lease.use_count += 1
    audit(
        session,
        tenant.id,
        account.id,
        "capability_lease.validated",
        lease.pub_id,
        "validated",
    )
    session.commit()
    return view(lease, tenant, account)
