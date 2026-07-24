# ruff: noqa: B008
# mypy: disable-error-code="arg-type"

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.service import RPCError

from workflows.activities.collection import CollectionTaskInput
from workflows.definitions.collection import GeoCollectionInput, GeoCollectionWorkflow
from workflows.definitions.session import AccountRevocationWorkflow, RevocationInput

from ..config import get_settings
from ..contracts import WorkflowAccepted
from ..identity.policy import Principal, get_principal
from ..projects.models import MonitoringConfigVersion, Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from .leases import FenceViolationError, assert_fenced_write
from .models import (
    AccountAuthorization,
    BrowserProfile,
    CollectionRun,
    InterventionRequest,
    PlatformAccount,
    PlatformAdapter,
    RevocationRequest,
    SessionEvent,
    SessionHealthCheck,
    SessionLease,
)
from .vault import LocalKms, ProfileVault, SealedProfile, profile_aad

router = APIRouter(prefix="/api/v2", tags=["collection"])
settings = get_settings()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(StrictModel):
    project_pub_id: str
    config_version_pub_id: str
    requires_intervention: bool = False
    account_pub_id: str | None = None


class RunView(StrictModel):
    pub_id: str
    project_pub_id: str
    config_version_pub_id: str
    workflow_id: str
    temporal_run_id: str | None
    state: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    paused: bool
    error_code: str | None
    updated_at: datetime


class AccountCreate(StrictModel):
    platform_slug: str
    platform_name: str
    account_mask: str
    owner_pub_id: str
    purpose: str
    responsible_pub_id: str
    custody_mode: Literal["server", "customer_device", "hybrid"]
    region: str


class AdapterView(StrictModel):
    pub_id: str
    slug: str
    display_name: str
    admission_level: str
    capabilities: list[str]
    adapter_version: str
    last_passed_at: datetime | None


class AccountView(StrictModel):
    pub_id: str
    platform: str
    account_mask: str
    owner_pub_id: str
    purpose: str
    responsible_pub_id: str
    custody_mode: str
    region: str
    state: str
    admission_level: str
    last_passed_at: datetime | None
    scopes: list[str]
    authorization_expires_at: datetime | None
    profile_state: str | None
    profile_version: int | None
    profile_constraints: list[str]
    lease_expires_at: datetime | None


class AuthorizationCreate(StrictModel):
    scopes: list[Literal["read", "query", "draft", "publish"]]
    forbidden_actions: list[str] = Field(default_factory=list)
    regions: list[str]
    valid_from: datetime
    valid_until: datetime


class ProfileEnroll(StrictModel):
    profile_payload: str | None = None
    custody_mode: Literal["server", "customer_device", "hybrid"]
    constraints: list[Literal["DEVICE_BOUND", "READ_ONLY"]] = Field(default_factory=list)
    expires_at: datetime | None = None


class ProfileView(StrictModel):
    pub_id: str
    profile_version: int
    custody_mode: str
    state: str
    constraints: list[str]
    ciphertext_sha256: str | None
    expires_at: datetime | None


class ProfileSeal(StrictModel):
    lease_pub_id: str
    fencing_token: int
    expected_profile_version: int = Field(ge=1)
    profile_payload: str
    expires_at: datetime | None = None


class InterventionCreate(StrictModel):
    challenge_type: Literal["otp", "qr", "push", "passkey", "face", "graphical"]
    allowed_domain: str
    action: str
    run_pub_id: str | None = None


class InterventionView(StrictModel):
    pub_id: str
    account_pub_id: str
    account_mask: str
    challenge_type: str
    allowed_domain: str
    action: str
    state: str
    pairing_expires_at: datetime | None
    platform_result: str | None


class PairingView(StrictModel):
    intervention_pub_id: str
    pairing_token: str
    expires_at: datetime


class CompleteIntervention(StrictModel):
    pairing_token: str
    platform_result: Literal["verified", "failed", "expired", "rejected"]
    evidence_hash: str = Field(pattern="^[a-f0-9]{64}$")


class EventView(StrictModel):
    pub_id: str
    event_type: str
    summary: dict[str, Any]
    occurred_at: datetime


def account_view(
    account: PlatformAccount, adapter: PlatformAdapter, session: Session
) -> AccountView:
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
    profile = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
    )
    lease = session.scalar(
        select(SessionLease)
        .where(
            SessionLease.account_id == account.id,
            SessionLease.released_at.is_(None),
            SessionLease.expires_at > now,
        )
        .order_by(SessionLease.expires_at.desc())
    )
    return AccountView(
        pub_id=account.pub_id,
        platform=adapter.slug,
        account_mask=account.account_mask,
        owner_pub_id=account.owner_pub_id,
        purpose=account.purpose,
        responsible_pub_id=account.responsible_pub_id,
        custody_mode=account.custody_mode,
        region=account.region,
        state=account.state,
        admission_level=account.admission_level,
        last_passed_at=adapter.last_passed_at,
        scopes=json.loads(authorization.scopes_json) if authorization else [],
        authorization_expires_at=authorization.valid_until if authorization else None,
        profile_state=profile.state if profile else None,
        profile_version=profile.profile_version if profile else None,
        profile_constraints=json.loads(profile.constraints_json) if profile else [],
        lease_expires_at=lease.expires_at if lease else None,
    )


@router.post(
    "/collection/runs",
    response_model=WorkflowAccepted,
    status_code=202,
    operation_id="startCollectionRun",
)
async def start_collection_run(
    body: RunCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("collection:control")
    repository = TenantRepository(session, principal.tenant_pub_id)
    existing = session.scalar(
        select(CollectionRun).where(
            CollectionRun.tenant_id == repository.tenant.id,
            CollectionRun.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return WorkflowAccepted(workflow_id=existing.workflow_id, run_id=existing.temporal_run_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id, Project.pub_id == body.project_pub_id
        )
    )
    config = session.scalar(
        select(MonitoringConfigVersion).where(
            MonitoringConfigVersion.tenant_id == repository.tenant.id,
            MonitoringConfigVersion.pub_id == body.config_version_pub_id,
            MonitoringConfigVersion.frozen_at.is_not(None),
        )
    )
    if project is None or config is None:
        raise HTTPException(status_code=404, detail={"code": "project_or_config_not_found"})
    snapshot = json.loads(config.snapshot_json)
    tasks: list[CollectionTaskInput] = []
    queries = [
        item["text"]
        for group in snapshot["query_groups"]
        for item in group.get("items", [])
        if item.get("text")
    ]
    for query in queries:
        for model in snapshot["models"]:
            for region in snapshot["regions"]:
                for mode in snapshot["modes"]:
                    business_key = hashlib.sha256(
                        f"{config.snapshot_hash}|{query}|{model}|{region}|{mode}".encode()
                    ).hexdigest()
                    tasks.append(
                        CollectionTaskInput(
                            business_key=business_key,
                            query=query,
                            model=model,
                            region=region,
                            mode=mode,
                        )
                    )
    run_pub_id = new_pub_id("run")
    workflow_id = f"geo-collection/{principal.tenant_pub_id}/{project.pub_id}/{run_pub_id}"
    run = CollectionRun(
        pub_id=run_pub_id,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        config_version_id=config.id,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id,
        state="starting",
        total_tasks=len(tasks),
    )
    session.add(run)
    session.commit()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    try:
        handle = await client.start_workflow(
            GeoCollectionWorkflow.run,
            GeoCollectionInput(
                tenant_pub_id=principal.tenant_pub_id,
                project_pub_id=project.pub_id,
                run_pub_id=run_pub_id,
                config_version_pub_id=config.pub_id,
                tasks=tasks,
                requires_intervention=body.requires_intervention,
                account_pub_id=body.account_pub_id,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except Exception:
        run.state = "start_failed"
        session.commit()
        raise
    run.temporal_run_id = handle.result_run_id
    run.state = "running"
    session.commit()
    return WorkflowAccepted(workflow_id=workflow_id, run_id=handle.result_run_id)


@router.post("/platform-accounts/{account_pub_id}/quarantine", response_model=AccountView)
def quarantine_account(
    account_pub_id: str,
    reason: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AccountView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    account.state = "quarantined"
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
        profile.state = "QUARANTINED"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="account.quarantined",
            summary_json=json.dumps({"reason": reason}),
        )
    )
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    session.commit()
    return account_view(account, adapter, session)


@router.get("/collection/runs", response_model=list[RunView])
def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[RunView]:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.tenant_id == repository.tenant.id)
        .order_by(CollectionRun.created_at.desc())
        .limit(limit)
    ).all()
    projects = {item.id: item.pub_id for item in session.scalars(select(Project)).all()}
    config_versions = {
        item.id: item.pub_id for item in session.scalars(select(MonitoringConfigVersion)).all()
    }
    return [
        RunView(
            pub_id=item.pub_id,
            project_pub_id=projects[item.project_id],
            config_version_pub_id=config_versions[item.config_version_id],
            workflow_id=item.workflow_id,
            temporal_run_id=item.temporal_run_id,
            state=item.state,
            total_tasks=item.total_tasks,
            completed_tasks=item.completed_tasks,
            failed_tasks=item.failed_tasks,
            paused=item.paused,
            error_code=item.error_code,
            updated_at=item.updated_at,
        )
        for item in rows
    ]


@router.post("/collection/runs/{run_pub_id}/{action}", response_model=WorkflowAccepted)
async def control_run(
    run_pub_id: str,
    action: Literal["pause", "resume", "cancel", "retry"],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("collection:control")
    repository = TenantRepository(session, principal.tenant_pub_id)
    run = session.scalar(
        select(CollectionRun).where(
            CollectionRun.tenant_id == repository.tenant.id, CollectionRun.pub_id == run_pub_id
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(run.workflow_id)
    if action in {"pause", "resume", "cancel"}:
        await handle.signal(action)
        run.paused = action == "pause"
        if action == "cancel":
            run.state = "cancelling"
    else:
        raise HTTPException(status_code=409, detail={"code": "retry_requires_new_run"})
    session.commit()
    return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)


@router.get("/platform-accounts", response_model=list[AccountView])
def list_accounts(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[AccountView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.execute(
        select(PlatformAccount, PlatformAdapter)
        .join(PlatformAdapter, PlatformAdapter.id == PlatformAccount.adapter_id)
        .where(PlatformAccount.tenant_id == repository.tenant.id)
        .order_by(PlatformAccount.created_at.desc())
    ).all()
    return [account_view(account, adapter, session) for account, adapter in rows]


@router.get("/platform-adapters", response_model=list[AdapterView])
def list_platform_adapters(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[AdapterView]:
    principal.require("account:read")
    # Global code capabilities; this projection has no tenant account/profile data.
    adapters = session.scalars(select(PlatformAdapter).order_by(PlatformAdapter.slug)).all()
    return [
        AdapterView(
            pub_id=item.pub_id,
            slug=item.slug,
            display_name=item.display_name,
            admission_level=item.admission_level,
            capabilities=json.loads(item.capabilities_json),
            adapter_version=item.adapter_version,
            last_passed_at=item.last_passed_at,
        )
        for item in adapters
    ]


@router.post("/platform-accounts", response_model=AccountView, status_code=201)
def create_account(
    body: AccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AccountView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    adapter = session.scalar(
        select(PlatformAdapter).where(PlatformAdapter.slug == body.platform_slug)
    )
    if adapter is None:
        adapter = PlatformAdapter(
            pub_id=new_pub_id("pad"),
            slug=body.platform_slug,
            display_name=body.platform_name,
            admission_level="adapter_ready" if body.platform_slug == "fixed" else "catalogued",
            capabilities_json=json.dumps(
                ["read", "query"] if body.platform_slug == "fixed" else []
            ),
            adapter_version="fixed-v1" if body.platform_slug == "fixed" else "unimplemented",
        )
        session.add(adapter)
        session.flush()
    account = PlatformAccount(
        pub_id=new_pub_id("pac"),
        tenant_id=repository.tenant.id,
        adapter_id=adapter.id,
        owner_pub_id=body.owner_pub_id,
        account_mask=body.account_mask,
        purpose=body.purpose,
        responsible_pub_id=body.responsible_pub_id,
        custody_mode=body.custody_mode,
        region=body.region,
        admission_level=adapter.admission_level,
    )
    session.add(account)
    session.commit()
    return account_view(account, adapter, session)


def find_account(session: Session, tenant_id: Any, pub_id: str) -> PlatformAccount:
    account = session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.tenant_id == tenant_id, PlatformAccount.pub_id == pub_id
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"code": "account_not_found"})
    return account


@router.post("/platform-accounts/{account_pub_id}/authorizations", status_code=201)
def authorize_account(
    account_pub_id: str,
    body: AuthorizationCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    if body.valid_until <= body.valid_from:
        raise HTTPException(status_code=422, detail={"code": "invalid_authorization_window"})
    authorization = AccountAuthorization(
        pub_id=new_pub_id("atz"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        scopes_json=json.dumps(body.scopes),
        forbidden_actions_json=json.dumps(body.forbidden_actions),
        regions_json=json.dumps(body.regions),
        valid_from=body.valid_from,
        valid_until=body.valid_until,
    )
    account.state = "owner_authorizing"
    session.add(authorization)
    session.commit()
    return {"pub_id": authorization.pub_id, "state": "active"}


@router.post(
    "/platform-accounts/{account_pub_id}/profiles/enroll",
    response_model=ProfileView,
    status_code=201,
)
def enroll_profile(
    account_pub_id: str,
    body: ProfileEnroll,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProfileView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    previous = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
        .limit(1)
    )
    version = (previous.profile_version if previous else 0) + 1
    ciphertext = nonce = wrapped_dek = None
    ciphertext_sha256 = None
    if body.custody_mode != "customer_device":
        if body.profile_payload is None:
            raise HTTPException(status_code=422, detail={"code": "profile_payload_required"})
        aad = profile_aad(
            principal.tenant_pub_id, account.owner_pub_id, adapter.slug, account.pub_id, version
        )
        sealed = ProfileVault(LocalKms(settings.kms_master_key)).seal(
            body.profile_payload.encode(), aad
        )
        ciphertext, nonce, wrapped_dek = sealed.ciphertext, sealed.nonce, sealed.wrapped_dek
        ciphertext_sha256 = sealed.sha256
    profile = BrowserProfile(
        pub_id=new_pub_id("prf"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        profile_version=version,
        custody_mode=body.custody_mode,
        state="ACTIVE",
        constraints_json=json.dumps(body.constraints),
        ciphertext=ciphertext,
        nonce=nonce,
        wrapped_dek=wrapped_dek,
        ciphertext_sha256=ciphertext_sha256,
        expires_at=body.expires_at,
    )
    account.state = "active"
    session.add(profile)
    session.commit()
    return ProfileView(
        pub_id=profile.pub_id,
        profile_version=version,
        custody_mode=profile.custody_mode,
        state=profile.state,
        constraints=list(body.constraints),
        ciphertext_sha256=ciphertext_sha256,
        expires_at=profile.expires_at,
    )


@router.post(
    "/platform-accounts/{account_pub_id}/profiles/seal",
    response_model=ProfileView,
    status_code=201,
)
def seal_profile_version(
    account_pub_id: str,
    body: ProfileSeal,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProfileView:
    principal.require("profile:use")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    current = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
        .limit(1)
        .with_for_update()
    )
    if current is None or current.profile_version != body.expected_profile_version:
        raise HTTPException(status_code=409, detail={"code": "profile_version_conflict"})
    lease = session.scalar(
        select(SessionLease).where(
            SessionLease.tenant_id == repository.tenant.id,
            SessionLease.account_id == account.id,
            SessionLease.pub_id == body.lease_pub_id,
        )
    )
    if lease is None:
        raise HTTPException(status_code=404, detail={"code": "lease_not_found"})
    try:
        assert_fenced_write(lease, body.fencing_token)
    except FenceViolationError as exc:
        raise HTTPException(status_code=409, detail={"code": "fence_violation"}) from exc
    next_version = current.profile_version + 1
    aad = profile_aad(
        principal.tenant_pub_id,
        account.owner_pub_id,
        adapter.slug,
        account.pub_id,
        next_version,
    )
    sealed = ProfileVault(LocalKms(settings.kms_master_key)).seal(
        body.profile_payload.encode(), aad
    )
    next_profile = BrowserProfile(
        pub_id=new_pub_id("prf"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        profile_version=next_version,
        custody_mode=current.custody_mode,
        state="ACTIVE",
        constraints_json=current.constraints_json,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        wrapped_dek=sealed.wrapped_dek,
        ciphertext_sha256=sealed.sha256,
        expires_at=body.expires_at,
    )
    session.add(next_profile)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="profile.version_sealed",
            summary_json=json.dumps(
                {"profile_version": next_version, "fencing_token": lease.fencing_token}
            ),
        )
    )
    session.commit()
    return ProfileView(
        pub_id=next_profile.pub_id,
        profile_version=next_version,
        custody_mode=next_profile.custody_mode,
        state=next_profile.state,
        constraints=json.loads(next_profile.constraints_json),
        ciphertext_sha256=next_profile.ciphertext_sha256,
        expires_at=next_profile.expires_at,
    )


@router.post("/platform-accounts/{account_pub_id}/health-checks", status_code=202)
def health_check(
    account_pub_id: str,
    live_canary: bool = False,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require("account:operate")
    if live_canary and principal.role.value not in {"admin", "reviewer"}:
        raise HTTPException(status_code=403, detail={"code": "live_canary_confirmation_required"})
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    profile = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
    )
    l0 = "not_applicable_customer_device"
    if profile and profile.ciphertext is not None:
        try:
            aad = profile_aad(
                principal.tenant_pub_id,
                account.owner_pub_id,
                adapter.slug,
                account.pub_id,
                profile.profile_version,
            )
            ProfileVault(LocalKms(settings.kms_master_key)).open(
                SealedProfile(
                    ciphertext=profile.ciphertext,
                    nonce=profile.nonce or b"",
                    wrapped_dek=profile.wrapped_dek or b"",
                    sha256=profile.ciphertext_sha256 or "",
                ),
                aad,
            )
            l0 = "passed"
        except Exception:
            l0 = "failed_quarantined"
            account.state = "quarantined"
            profile.state = "QUARANTINED"
    levels = {
        "L0": l0,
        # Network/account/capability probes require the real adapter and an
        # explicitly authorized live canary. Never promote a fixture to a
        # health result.
        "L1": "not_run",
        "L2": "not_run",
        "L3": "not_run",
        "L4": "adapter_not_live" if live_canary else "not_run",
    }
    event = SessionEvent(
        pub_id=new_pub_id("sev"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        event_type="health_check.completed",
        summary_json=json.dumps({"levels": levels, "live": False, "canary_requested": live_canary}),
    )
    session.add_all(
        [
            event,
            SessionHealthCheck(
                pub_id=new_pub_id("shc"),
                tenant_id=repository.tenant.id,
                account_id=account.id,
                probe_levels_json=json.dumps(levels),
                result=(
                    "failed"
                    if l0 == "failed_quarantined"
                    else ("partial" if l0 == "passed" else "not_verified")
                ),
                live_canary=live_canary,
                checked_by=principal.subject,
            ),
        ]
    )
    session.commit()
    return {"account_pub_id": account.pub_id, "levels": levels, "live_verified": False}


@router.post(
    "/platform-accounts/{account_pub_id}/interventions",
    response_model=InterventionView,
    status_code=201,
)
def create_intervention(
    account_pub_id: str,
    body: InterventionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    run_id = None
    if body.run_pub_id:
        run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.tenant_id == repository.tenant.id,
                CollectionRun.pub_id == body.run_pub_id,
            )
        )
        if run is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        run_id = run.id
    request = InterventionRequest(
        pub_id=new_pub_id("int"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        run_id=run_id,
        challenge_type=body.challenge_type,
        allowed_domain=body.allowed_domain,
        action=body.action,
        state="pending",
    )
    account.state = "challenge_required"
    session.add(request)
    session.commit()
    return InterventionView(
        pub_id=request.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=request.challenge_type,
        allowed_domain=request.allowed_domain,
        action=request.action,
        state=request.state,
        pairing_expires_at=None,
        platform_result=None,
    )


@router.get("/interventions", response_model=list[InterventionView])
def list_interventions(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[InterventionView]:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(InterventionRequest.tenant_id == repository.tenant.id)
        .order_by(InterventionRequest.created_at.desc())
    ).all()
    return [
        InterventionView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            challenge_type=item.challenge_type,
            allowed_domain=item.allowed_domain,
            action=item.action,
            state=item.state,
            pairing_expires_at=item.pairing_expires_at,
            platform_result=item.platform_result,
        )
        for item, account in rows
    ]


@router.post("/interventions/{intervention_pub_id}/pair", response_model=PairingView)
def pair_intervention(
    intervention_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PairingView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    item = session.scalar(
        select(InterventionRequest).where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(minutes=10)
    item.pairing_token_hash = hashlib.sha256(token.encode()).hexdigest()
    item.pairing_expires_at = expires
    item.paired_at = datetime.now(UTC)
    item.state = "paired"
    session.commit()
    return PairingView(intervention_pub_id=item.pub_id, pairing_token=token, expires_at=expires)


@router.post("/interventions/{intervention_pub_id}/complete", response_model=InterventionView)
async def complete_intervention(
    intervention_pub_id: str,
    body: CompleteIntervention,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account = row
    if (
        item.pairing_token_hash != hashlib.sha256(body.pairing_token.encode()).hexdigest()
        or item.pairing_expires_at is None
        or item.pairing_expires_at < datetime.now(UTC)
    ):
        raise HTTPException(status_code=410, detail={"code": "pairing_token_invalid"})
    item.pairing_token_hash = None
    item.state = "completed" if body.platform_result == "verified" else body.platform_result
    item.platform_result = body.platform_result
    item.evidence_hash = body.evidence_hash
    item.completed_at = datetime.now(UTC)
    account.state = "active" if body.platform_result == "verified" else "challenge_required"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="intervention.completed",
            summary_json=json.dumps(
                {"challenge_type": item.challenge_type, "platform_result": body.platform_result}
            ),
        )
    )
    session.commit()
    if item.run_id is not None and body.platform_result == "verified":
        run = session.get(CollectionRun, item.run_id)
        if run is not None:
            client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
            try:
                await client.get_workflow_handle(run.workflow_id).signal(
                    "complete_intervention", body.evidence_hash
                )
            except RPCError:
                # The durable platform result remains authoritative even if the linked
                # workflow already reached a terminal state.
                pass
    return InterventionView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=item.challenge_type,
        allowed_domain=item.allowed_domain,
        action=item.action,
        state=item.state,
        pairing_expires_at=item.pairing_expires_at,
        platform_result=item.platform_result,
    )


@router.post(
    "/platform-accounts/{account_pub_id}/revoke",
    response_model=WorkflowAccepted,
    status_code=202,
)
async def revoke_account(
    account_pub_id: str,
    reason: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    account.state = "revoked"
    session.execute(
        update(SessionLease)
        .where(SessionLease.account_id == account.id, SessionLease.released_at.is_(None))
        .values(released_at=datetime.now(UTC))
    )
    profiles = session.scalars(
        select(BrowserProfile).where(BrowserProfile.account_id == account.id)
    ).all()
    for profile in profiles:
        profile.state = "REVOKED"
        profile.wrapped_dek = None
        profile.purged_at = datetime.now(UTC)
    workflow_id = (
        f"account-revocation/{principal.tenant_pub_id}/{account.pub_id}/{new_pub_id('rev')}"
    )
    request = RevocationRequest(
        pub_id=new_pub_id("rev"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        reason=reason,
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


@router.get("/platform-accounts/{account_pub_id}/events", response_model=list[EventView])
def account_events(
    account_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[EventView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    rows = session.scalars(
        select(SessionEvent)
        .where(SessionEvent.account_id == account.id)
        .order_by(SessionEvent.occurred_at.desc())
    ).all()
    return [
        EventView(
            pub_id=item.pub_id,
            event_type=item.event_type,
            summary=json.loads(item.summary_json),
            occurred_at=item.occurred_at,
        )
        for item in rows
    ]
