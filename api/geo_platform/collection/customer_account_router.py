# ruff: noqa: B008

import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from temporalio.client import Client

from workflows.definitions.session import AccountRevocationWorkflow, RevocationInput

from ..config import get_settings
from ..contracts import WorkflowAccepted
from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import User
from ..tenancy.repository import TenantRepository
from .dlp import BEARER, SECRET_KEYS
from .models import (
    AccountAuthorization,
    BrowserProfile,
    InterventionRequest,
    PlatformAccount,
    PlatformAdapter,
    RevocationRequest,
    SessionEvent,
    SessionHealthCheck,
    SessionLease,
)

router = APIRouter(prefix="/api/v2/customer/platform-accounts", tags=["customer-accounts"])
settings = get_settings()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerAccountCreate(StrictModel):
    platform_slug: str = Field(min_length=1, max_length=80)
    platform_name: str = Field(min_length=1, max_length=160)
    account_mask: str = Field(min_length=3, max_length=120)
    custody_mode: Literal["server", "customer_device", "hybrid"]
    region: str = Field(min_length=2, max_length=80)

    @field_validator("account_mask")
    @classmethod
    def reject_secret_mask(cls, value: str) -> str:
        if SECRET_KEYS.search(value) or BEARER.search(value):
            raise ValueError("account_mask contains a secret marker")
        return value


class CustomerAuthorizationCreate(StrictModel):
    scopes: list[Literal["read", "query", "draft", "publish"]] = Field(min_length=1)
    forbidden_actions: list[str] = Field(default_factory=list)
    regions: list[str] = Field(min_length=1)
    valid_until: datetime


class CustomerPairingCreate(StrictModel):
    allowed_domain: str = Field(min_length=3, max_length=255)
    action: Literal["read", "query", "draft", "publish"]
    challenge_type: Literal["otp", "qr", "push", "passkey", "face", "graphical"] = "qr"

    @field_validator("allowed_domain")
    @classmethod
    def hostname_only(cls, value: str) -> str:
        domain = value.strip().lower().rstrip(".")
        if "/" in domain or ":" in domain or domain.startswith("."):
            raise ValueError("allowed_domain must be a hostname")
        return domain


class CustomerAccountView(StrictModel):
    pub_id: str
    account_mask: str
    platform_label: str
    owner_label: str
    custody_mode: Literal["server", "customer_device", "hybrid"]
    admission_level: str
    scopes: list[str]
    authorization_expires_at: datetime | None
    region_label: str
    session_health: Literal["healthy", "degraded", "challenge_required", "revoked"]
    last_verified_at: datetime | None
    intervention_status: str
    revocation_receipt_pub_id: str | None
    revoked_at: datetime | None


class CustomerPairingView(StrictModel):
    pub_id: str
    account_pub_id: str
    account_mask: str
    allowed_domain: str
    action: str
    challenge_type: str
    state: str
    expires_at: datetime | None


class CustomerEventView(StrictModel):
    pub_id: str
    event_type: str
    occurred_at: datetime


def current_user(session: Session, principal: Principal) -> User:
    user = session.scalar(
        select(User).where(User.subject == principal.subject, User.disabled_at.is_(None))
    )
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "membership_invalid"})
    return user


def owned_account(
    session: Session, tenant_id: object, owner_pub_id: str, account_pub_id: str
) -> PlatformAccount:
    account = session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.tenant_id == tenant_id,
            PlatformAccount.owner_pub_id == owner_pub_id,
            PlatformAccount.pub_id == account_pub_id,
        )
    )
    if account is None:
        # Do not let customer roles distinguish another customer's account from a missing one.
        raise HTTPException(status_code=404, detail={"code": "account_not_found"})
    return account


def safe_view(
    session: Session, account: PlatformAccount, adapter: PlatformAdapter
) -> CustomerAccountView:
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    intervention = session.scalar(
        select(InterventionRequest)
        .where(InterventionRequest.account_id == account.id)
        .order_by(InterventionRequest.created_at.desc())
    )
    health = session.scalar(
        select(SessionHealthCheck)
        .where(SessionHealthCheck.account_id == account.id)
        .order_by(SessionHealthCheck.checked_at.desc())
    )
    revocation = session.scalar(
        select(RevocationRequest)
        .where(RevocationRequest.account_id == account.id)
        .order_by(RevocationRequest.created_at.desc())
    )
    if account.state == "revoked":
        session_health = "revoked"
    elif account.state == "challenge_required":
        session_health = "challenge_required"
    elif health and health.result == "passed":
        session_health = "healthy"
    else:
        session_health = "degraded"
    return CustomerAccountView(
        pub_id=account.pub_id,
        account_mask=account.account_mask,
        platform_label=adapter.display_name,
        owner_label="当前客户",
        custody_mode=cast(Literal["server", "customer_device", "hybrid"], account.custody_mode),
        admission_level=account.admission_level,
        scopes=json.loads(authorization.scopes_json) if authorization else [],
        authorization_expires_at=authorization.valid_until if authorization else None,
        region_label=account.region,
        session_health=cast(
            Literal["healthy", "degraded", "challenge_required", "revoked"],
            session_health,
        ),
        last_verified_at=adapter.last_passed_at,
        intervention_status=intervention.state if intervention else "none",
        revocation_receipt_pub_id=revocation.pub_id if revocation else None,
        revoked_at=revocation.deletion_verified_at if revocation else None,
    )


@router.get("", response_model=list[CustomerAccountView])
def list_customer_accounts(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[CustomerAccountView]:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    rows = session.execute(
        select(PlatformAccount, PlatformAdapter)
        .join(PlatformAdapter, PlatformAdapter.id == PlatformAccount.adapter_id)
        .where(
            PlatformAccount.tenant_id == repository.tenant.id,
            PlatformAccount.owner_pub_id == user.pub_id,
        )
        .order_by(PlatformAccount.created_at.desc())
    ).all()
    return [safe_view(session, account, adapter) for account, adapter in rows]


@router.post("", response_model=CustomerAccountView, status_code=201)
def register_customer_account(
    body: CustomerAccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerAccountView:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    adapter = session.scalar(
        select(PlatformAdapter).where(PlatformAdapter.slug == body.platform_slug)
    )
    if adapter is None:
        adapter = PlatformAdapter(
            pub_id=new_pub_id("pad"),
            slug=body.platform_slug,
            display_name=body.platform_name,
            admission_level="catalogued",
            capabilities_json="[]",
            adapter_version="unimplemented",
        )
        session.add(adapter)
        session.flush()
    account = PlatformAccount(
        pub_id=new_pub_id("pac"),
        tenant_id=repository.tenant.id,
        adapter_id=adapter.id,
        owner_pub_id=user.pub_id,
        account_mask=body.account_mask,
        purpose="customer-authorized",
        responsible_pub_id=user.pub_id,
        custody_mode=body.custody_mode,
        region=body.region,
        admission_level=adapter.admission_level,
    )
    session.add(account)
    session.commit()
    return safe_view(session, account, adapter)


@router.post("/{account_pub_id}/authorizations", response_model=CustomerAccountView)
def authorize_customer_account(
    account_pub_id: str,
    body: CustomerAuthorizationCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerAccountView:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    account = owned_account(session, repository.tenant.id, user.pub_id, account_pub_id)
    now = datetime.now(UTC)
    if body.valid_until <= now or body.valid_until > now + timedelta(days=366):
        raise HTTPException(status_code=422, detail={"code": "invalid_authorization_window"})
    if "publish" in body.scopes and account.custody_mode == "server":
        raise HTTPException(
            status_code=422, detail={"code": "publish_requires_customer_device_or_hybrid"}
        )
    authorization = AccountAuthorization(
        pub_id=new_pub_id("aut"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        scopes_json=json.dumps(sorted(set(body.scopes))),
        forbidden_actions_json=json.dumps(sorted(set(body.forbidden_actions))),
        regions_json=json.dumps(sorted(set(body.regions))),
        valid_from=now,
        valid_until=body.valid_until,
    )
    session.add(authorization)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="customer_authorization.updated",
            summary_json=json.dumps({"scopes": sorted(set(body.scopes))}),
        )
    )
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    session.commit()
    return safe_view(session, account, adapter)


@router.post("/{account_pub_id}/pairings", response_model=CustomerPairingView, status_code=201)
def create_customer_pairing(
    account_pub_id: str,
    body: CustomerPairingCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerPairingView:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    account = owned_account(session, repository.tenant.id, user.pub_id, account_pub_id)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_until > datetime.now(UTC),
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    if authorization is None or body.action not in json.loads(authorization.scopes_json):
        raise HTTPException(status_code=403, detail={"code": "scope_not_authorized"})
    if body.action in json.loads(authorization.forbidden_actions_json):
        raise HTTPException(status_code=403, detail={"code": "action_forbidden"})
    item = InterventionRequest(
        pub_id=new_pub_id("int"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        challenge_type=body.challenge_type,
        allowed_domain=body.allowed_domain.lower(),
        action=body.action,
        state="pending",
    )
    # Operations/controlled-terminal pairing creates the one-time token.
    # It never enters Customer Web.
    account.state = "challenge_required"
    session.add(item)
    session.flush()
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="customer_pairing.requested",
            summary_json=json.dumps({"intervention_pub_id": item.pub_id}),
        )
    )
    session.commit()
    return CustomerPairingView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        allowed_domain=item.allowed_domain,
        action=item.action,
        challenge_type=item.challenge_type,
        state=item.state,
        expires_at=None,
    )


@router.get("/{account_pub_id}/pairings", response_model=list[CustomerPairingView])
def list_customer_pairings(
    account_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CustomerPairingView]:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    account = owned_account(session, repository.tenant.id, user.pub_id, account_pub_id)
    rows = session.scalars(
        select(InterventionRequest)
        .where(InterventionRequest.account_id == account.id)
        .order_by(InterventionRequest.created_at.desc())
    ).all()
    return [
        CustomerPairingView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            allowed_domain=item.allowed_domain,
            action=item.action,
            challenge_type=item.challenge_type,
            state=item.state,
            expires_at=item.pairing_expires_at,
        )
        for item in rows
    ]


@router.get("/{account_pub_id}/events", response_model=list[CustomerEventView])
def list_customer_account_events(
    account_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CustomerEventView]:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    account = owned_account(session, repository.tenant.id, user.pub_id, account_pub_id)
    rows = session.scalars(
        select(SessionEvent)
        .where(SessionEvent.account_id == account.id)
        .order_by(SessionEvent.occurred_at.desc())
        .limit(100)
    ).all()
    return [
        CustomerEventView(
            pub_id=item.pub_id, event_type=item.event_type, occurred_at=item.occurred_at
        )
        for item in rows
    ]


@router.post("/{account_pub_id}/revoke", response_model=WorkflowAccepted, status_code=202)
async def revoke_customer_account(
    account_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("account:authorize")
    repository = TenantRepository(session, principal.tenant_pub_id)
    user = current_user(session, principal)
    account = owned_account(session, repository.tenant.id, user.pub_id, account_pub_id)
    account.state = "revoked"
    now = datetime.now(UTC)
    session.execute(
        update(SessionLease)
        .where(SessionLease.account_id == account.id, SessionLease.released_at.is_(None))
        .values(released_at=now)
    )
    profiles = session.scalars(
        select(BrowserProfile).where(BrowserProfile.account_id == account.id)
    ).all()
    for profile in profiles:
        profile.state = "REVOKED"
        profile.wrapped_dek = None
        profile.purged_at = now
    workflow_id = (
        f"account-revocation/{principal.tenant_pub_id}/{account.pub_id}/{new_pub_id('rev')}"
    )
    request = RevocationRequest(
        pub_id=new_pub_id("rev"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        reason="customer-owner-requested",
        workflow_id=workflow_id,
        state="running",
    )
    session.add(request)
    session.commit()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        AccountRevocationWorkflow.run,
        RevocationInput(
            account_pub_id=account.pub_id,
            profile_versions=[profile.profile_version for profile in profiles],
            request_pub_id=request.pub_id,
        ),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return WorkflowAccepted(workflow_id=workflow_id, run_id=handle.result_run_id)
