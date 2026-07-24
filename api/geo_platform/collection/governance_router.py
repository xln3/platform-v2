# ruff: noqa: B008
# mypy: disable-error-code="arg-type"

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..identity.policy import Principal, Role, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from .leases import LeaseBusyError, acquire_session_lease, heartbeat_lease
from .models import (
    AccountAuthorization,
    BrowserProfile,
    CredentialAccessApproval,
    CredentialAccessRequest,
    PlatformAccount,
    ResourceRegistration,
    SessionEvent,
    SessionLease,
)

router = APIRouter(prefix="/api/v2", tags=["resource-governance"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceCreate(StrictModel):
    kind: Literal["account", "proxy", "device", "region"]
    display_mask: str = Field(min_length=1, max_length=160)
    capabilities: list[str]
    region: str
    concurrency_limit: int = Field(default=1, ge=1, le=100)


class ResourceView(StrictModel):
    pub_id: str
    kind: str
    display_mask: str
    capabilities: list[str]
    region: str
    concurrency_limit: int
    state: str
    last_heartbeat_at: datetime | None


class LeaseCreate(StrictModel):
    profile_pub_id: str
    holder: str
    capability: Literal["read", "query", "draft", "publish"]
    ttl_seconds: int = Field(default=1200, ge=30, le=3600)


class LeaseView(StrictModel):
    pub_id: str
    account_pub_id: str
    holder: str
    capability: str
    fencing_token: int
    heartbeat_at: datetime
    expires_at: datetime
    released_at: datetime | None


class HeartbeatRequest(StrictModel):
    fencing_token: int
    ttl_seconds: int = Field(default=1200, ge=30, le=3600)


class BreakGlassCreate(StrictModel):
    reason: str = Field(min_length=20, max_length=1000)
    ttl_seconds: int = Field(default=600, ge=60, le=900)


class BreakGlassView(StrictModel):
    pub_id: str
    account_pub_id: str
    requested_by: str
    reason: str
    state: str
    approvals: int
    expires_at: datetime
    capability_token: str | None = None


def get_account(session: Session, tenant_id: object, pub_id: str) -> PlatformAccount:
    account = session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.tenant_id == tenant_id, PlatformAccount.pub_id == pub_id
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"code": "account_not_found"})
    return account


@router.post("/resources", response_model=ResourceView, status_code=201)
def register_resource(
    body: ResourceCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ResourceView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    item = ResourceRegistration(
        pub_id=new_pub_id("res"),
        tenant_id=repository.tenant.id,
        resource_kind=body.kind,
        display_mask=body.display_mask,
        capabilities_json=json.dumps(body.capabilities),
        region=body.region,
        concurrency_limit=body.concurrency_limit,
        last_heartbeat_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    return ResourceView(
        pub_id=item.pub_id,
        kind=item.resource_kind,
        display_mask=item.display_mask,
        capabilities=body.capabilities,
        region=item.region,
        concurrency_limit=item.concurrency_limit,
        state=item.state,
        last_heartbeat_at=item.last_heartbeat_at,
    )


@router.get("/resources", response_model=list[ResourceView])
def list_resources(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[ResourceView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.scalars(
        select(ResourceRegistration)
        .where(ResourceRegistration.tenant_id == repository.tenant.id)
        .order_by(ResourceRegistration.created_at.desc())
    ).all()
    return [
        ResourceView(
            pub_id=item.pub_id,
            kind=item.resource_kind,
            display_mask=item.display_mask,
            capabilities=json.loads(item.capabilities_json),
            region=item.region,
            concurrency_limit=item.concurrency_limit,
            state=item.state,
            last_heartbeat_at=item.last_heartbeat_at,
        )
        for item in rows
    ]


@router.post(
    "/platform-accounts/{account_pub_id}/leases",
    response_model=LeaseView,
    status_code=201,
)
def acquire_lease(
    account_pub_id: str,
    body: LeaseCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> LeaseView:
    principal.require("lease:acquire")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = get_account(session, repository.tenant.id, account_pub_id)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= datetime.now(UTC),
            AccountAuthorization.valid_until > datetime.now(UTC),
        )
        .order_by(AccountAuthorization.created_at.desc())
    )
    if (
        authorization is None
        or body.capability not in json.loads(authorization.scopes_json)
        or account.region not in json.loads(authorization.regions_json)
    ):
        raise HTTPException(status_code=403, detail={"code": "capability_not_authorized"})
    profile = session.scalar(
        select(BrowserProfile).where(
            BrowserProfile.tenant_id == repository.tenant.id,
            BrowserProfile.account_id == account.id,
            BrowserProfile.pub_id == body.profile_pub_id,
            BrowserProfile.state == "ACTIVE",
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "active_profile_not_found"})
    try:
        lease = acquire_session_lease(
            session,
            account,
            profile,
            body.holder,
            body.capability,
            timedelta(seconds=body.ttl_seconds),
        )
        session.commit()
    except LeaseBusyError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "account_lease_busy"}) from exc
    return LeaseView(
        pub_id=lease.pub_id,
        account_pub_id=account.pub_id,
        holder=lease.holder,
        capability=lease.capability,
        fencing_token=lease.fencing_token,
        heartbeat_at=lease.heartbeat_at,
        expires_at=lease.expires_at,
        released_at=lease.released_at,
    )


@router.post("/leases/{lease_pub_id}/heartbeat", response_model=LeaseView)
def heartbeat(
    lease_pub_id: str,
    body: HeartbeatRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> LeaseView:
    principal.require("lease:acquire")
    repository = TenantRepository(session, principal.tenant_pub_id)
    lease = session.scalar(
        select(SessionLease)
        .where(
            SessionLease.tenant_id == repository.tenant.id,
            SessionLease.pub_id == lease_pub_id,
        )
        .with_for_update()
    )
    if lease is None:
        raise HTTPException(status_code=404, detail={"code": "lease_not_found"})
    try:
        heartbeat_lease(session, lease, body.fencing_token, timedelta(seconds=body.ttl_seconds))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "fence_violation"}) from exc
    account = session.get(PlatformAccount, lease.account_id)
    assert account is not None
    session.commit()
    return LeaseView(
        pub_id=lease.pub_id,
        account_pub_id=account.pub_id,
        holder=lease.holder,
        capability=lease.capability,
        fencing_token=lease.fencing_token,
        heartbeat_at=lease.heartbeat_at,
        expires_at=lease.expires_at,
        released_at=lease.released_at,
    )


@router.post("/leases/reap")
def reap_expired_leases(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> dict[str, int]:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    now = datetime.now(UTC)
    result = session.execute(
        update(SessionLease)
        .where(
            SessionLease.tenant_id == repository.tenant.id,
            SessionLease.released_at.is_(None),
            SessionLease.expires_at <= now,
        )
        .values(released_at=now)
    )
    session.commit()
    return {"reaped": result.rowcount}


@router.post(
    "/platform-accounts/{account_pub_id}/break-glass",
    response_model=BreakGlassView,
    status_code=201,
)
def request_break_glass(
    account_pub_id: str,
    body: BreakGlassCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BreakGlassView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = get_account(session, repository.tenant.id, account_pub_id)
    item = CredentialAccessRequest(
        pub_id=new_pub_id("bgr"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        requested_by=principal.subject,
        reason=body.reason,
        expires_at=datetime.now(UTC) + timedelta(seconds=body.ttl_seconds),
    )
    session.add(item)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="break_glass.requested",
            summary_json=json.dumps({"request_pub_id": item.pub_id}),
        )
    )
    session.commit()
    return BreakGlassView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        requested_by=item.requested_by,
        reason=item.reason,
        state=item.state,
        approvals=0,
        expires_at=item.expires_at,
    )


@router.get("/break-glass", response_model=list[BreakGlassView])
def list_break_glass_requests(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[BreakGlassView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.execute(
        select(CredentialAccessRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == CredentialAccessRequest.account_id)
        .where(CredentialAccessRequest.tenant_id == repository.tenant.id)
        .order_by(CredentialAccessRequest.created_at.desc())
        .limit(100)
    ).all()
    return [
        BreakGlassView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            requested_by=item.requested_by,
            reason=item.reason,
            state=item.state,
            approvals=int(
                session.scalar(
                    select(func.count())
                    .select_from(CredentialAccessApproval)
                    .where(
                        CredentialAccessApproval.request_id == item.id,
                        CredentialAccessApproval.decision == "approved",
                    )
                )
                or 0
            ),
            expires_at=item.expires_at,
        )
        for item, account in rows
    ]


@router.post("/break-glass/{request_pub_id}/approve", response_model=BreakGlassView)
def approve_break_glass(
    request_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BreakGlassView:
    if principal.role not in {Role.REVIEWER, Role.ADMIN}:
        raise HTTPException(status_code=403, detail={"code": "reviewer_required"})
    repository = TenantRepository(session, principal.tenant_pub_id)
    item = session.scalar(
        select(CredentialAccessRequest)
        .where(
            CredentialAccessRequest.tenant_id == repository.tenant.id,
            CredentialAccessRequest.pub_id == request_pub_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "break_glass_not_found"})
    if item.expires_at <= datetime.now(UTC) or item.state in {"expired", "used", "rejected"}:
        raise HTTPException(status_code=410, detail={"code": "break_glass_expired"})
    if principal.subject == item.requested_by:
        raise HTTPException(status_code=403, detail={"code": "self_approval_forbidden"})
    session.add(
        CredentialAccessApproval(
            pub_id=new_pub_id("bga"),
            tenant_id=repository.tenant.id,
            request_id=item.id,
            approver_pub_id=principal.subject,
            decision="approved",
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "duplicate_approval"}) from exc
    approvals = session.scalar(
        select(func.count())
        .select_from(CredentialAccessApproval)
        .where(
            CredentialAccessApproval.request_id == item.id,
            CredentialAccessApproval.decision == "approved",
        )
    )
    token: str | None = None
    if int(approvals or 0) >= 2:
        token = secrets.token_urlsafe(32)
        item.capability_token_hash = hashlib.sha256(token.encode()).hexdigest()
        item.state = "approved"
    account = session.get(PlatformAccount, item.account_id)
    assert account is not None
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="break_glass.approved",
            summary_json=json.dumps(
                {"request_pub_id": item.pub_id, "approval_count": int(approvals or 0)}
            ),
        )
    )
    session.commit()
    return BreakGlassView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        requested_by=item.requested_by,
        reason=item.reason,
        state=item.state,
        approvals=int(approvals or 0),
        expires_at=item.expires_at,
        capability_token=token,
    )
