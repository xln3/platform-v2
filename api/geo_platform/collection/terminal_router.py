# ruff: noqa: B008

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from .models import (
    AccountAuthorization,
    DeviceBinding,
    InterventionRequest,
    PlatformAccount,
    SessionEvent,
    TerminalTask,
)
from .terminal_protocol import (
    b64url_encode,
    canonical_json,
    fingerprint,
    public_key_bytes,
    public_key_from_text,
    task_signing_key,
    verify_signature,
)

router = APIRouter(prefix="/api/v2/terminal", tags=["customer-terminal"])
settings = get_settings()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TerminalBind(StrictModel):
    pairing_token: str = Field(min_length=32, max_length=128)
    device_label: str = Field(min_length=1, max_length=80)
    device_public_key: str = Field(min_length=40, max_length=50)
    proof_signature: str = Field(min_length=80, max_length=90)


class TerminalTaskView(StrictModel):
    task_pub_id: str
    device_binding_pub_id: str
    payload: dict[str, object]
    server_signature: str
    server_public_key: str
    expires_at: datetime


class TerminalResult(StrictModel):
    result: Literal["challenge_completed", "failed", "expired", "rejected"]
    evidence_hash: str = Field(pattern="^[a-f0-9]{64}$")
    terminal_signature: str = Field(min_length=80, max_length=90)


class TerminalResultView(StrictModel):
    task_pub_id: str
    intervention_pub_id: str
    state: str
    platform_result: str
    completed_at: datetime


def _repository(session: Session, tenant_pub_id: str | None) -> TenantRepository:
    if tenant_pub_id is None or not tenant_pub_id:
        raise HTTPException(status_code=401, detail={"code": "terminal_channel_invalid"})
    try:
        return TenantRepository(session, tenant_pub_id)
    except LookupError as exc:
        raise HTTPException(status_code=401, detail={"code": "terminal_channel_invalid"}) from exc


def _signing_key() -> Ed25519PrivateKey:
    try:
        return task_signing_key(settings)
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail={"code": "terminal_signing_unavailable"}
        ) from exc


def _record_expiry(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    account: PlatformAccount,
    intervention: InterventionRequest,
    task: TerminalTask | None = None,
) -> None:
    intervention.state = "expired"
    intervention.platform_result = "expired"
    intervention.pairing_token_hash = None
    intervention.completed_at = datetime.now(UTC)
    account.state = "challenge_required"
    if task is not None:
        task.state = "expired"
        task.result = "expired"
        task.consumed_at = datetime.now(UTC)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=tenant_id,
            account_id=account.id,
            event_type="customer_terminal.task_expired"
            if task
            else "customer_terminal.pairing_expired",
            summary_json=json.dumps(
                {
                    "intervention_pub_id": intervention.pub_id,
                    **({"task_pub_id": task.pub_id} if task else {}),
                },
                sort_keys=True,
            ),
        )
    )
    session.commit()


@router.post(
    "/interventions/{intervention_pub_id}/bind",
    response_model=TerminalTaskView,
    status_code=201,
)
def bind_terminal_and_issue_task(
    intervention_pub_id: str,
    body: TerminalBind,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    session: Session = Depends(get_db),
) -> TerminalTaskView:
    """Consume a pairing capability, prove device-key possession and issue one signed task."""
    repository = _repository(session, x_tenant_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=410, detail={"code": "terminal_channel_invalid"})
    intervention, account = row
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
    token_hash = hashlib.sha256(body.pairing_token.encode()).hexdigest()
    if intervention.pairing_expires_at is not None and intervention.pairing_expires_at <= now:
        _record_expiry(
            session,
            tenant_id=repository.tenant.id,
            account=account,
            intervention=intervention,
        )
        raise HTTPException(status_code=410, detail={"code": "terminal_channel_invalid"})
    if (
        account.state == "revoked"
        or authorization is None
        or intervention.action not in json.loads(authorization.scopes_json)
        or intervention.action in json.loads(authorization.forbidden_actions_json)
        or account.custody_mode not in {"customer_device", "hybrid"}
        or intervention.state != "paired"
        or intervention.pairing_token_hash is None
        or not secrets.compare_digest(intervention.pairing_token_hash, token_hash)
        or intervention.pairing_expires_at is None
    ):
        raise HTTPException(status_code=410, detail={"code": "terminal_channel_invalid"})
    try:
        device_public_key = public_key_from_text(body.device_public_key)
        proof = canonical_json(
            {
                "intervention_pub_id": intervention.pub_id,
                "pairing_token_sha256": token_hash,
                "purpose": "geo-terminal-bind",
                "tenant_pub_id": repository.tenant.pub_id,
                "version": 1,
            }
        )
        verify_signature(device_public_key, body.proof_signature, proof)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"code": "device_proof_invalid"}) from exc

    raw_public_key = public_key_bytes(device_public_key)
    key_fingerprint = fingerprint(raw_public_key)
    device = session.scalar(
        select(DeviceBinding).where(
            DeviceBinding.account_id == account.id,
            DeviceBinding.public_key_sha256 == key_fingerprint,
        )
    )
    if device is not None and (device.state != "active" or device.revoked_at is not None):
        raise HTTPException(status_code=410, detail={"code": "device_binding_revoked"})
    if device is None:
        device = DeviceBinding(
            pub_id=new_pub_id("dev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            public_key=raw_public_key,
            public_key_sha256=key_fingerprint,
            label=body.device_label,
            state="active",
        )
        session.add(device)
        session.flush()
    nonce = secrets.token_urlsafe(32)
    expires_at = min(intervention.pairing_expires_at, now + timedelta(minutes=5))
    task_pub_id = new_pub_id("ttk")
    payload: dict[str, object] = {
        "account_pub_id": account.pub_id,
        "action": intervention.action,
        "allowed_domain": intervention.allowed_domain,
        "challenge_type": intervention.challenge_type,
        "device_binding_pub_id": device.pub_id,
        "expires_at": expires_at.isoformat(),
        "intervention_pub_id": intervention.pub_id,
        "nonce": nonce,
        "task_pub_id": task_pub_id,
        "version": 1,
    }
    payload_bytes = canonical_json(payload)
    signing_key = _signing_key()
    server_signature = signing_key.sign(payload_bytes)
    task = TerminalTask(
        pub_id=task_pub_id,
        tenant_id=repository.tenant.id,
        intervention_id=intervention.id,
        device_binding_id=device.id,
        nonce_sha256=hashlib.sha256(nonce.encode()).hexdigest(),
        payload_json=payload_bytes.decode(),
        server_signature=server_signature,
        expires_at=expires_at,
        state="issued",
    )
    intervention.pairing_token_hash = None
    intervention.state = "task_issued"
    session.add(task)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="customer_terminal.task_issued",
            summary_json=json.dumps(
                {
                    "device_binding_pub_id": device.pub_id,
                    "intervention_pub_id": intervention.pub_id,
                    "task_pub_id": task.pub_id,
                },
                sort_keys=True,
            ),
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "terminal_binding_conflict"}) from exc
    return TerminalTaskView(
        task_pub_id=task.pub_id,
        device_binding_pub_id=device.pub_id,
        payload=payload,
        server_signature=b64url_encode(server_signature),
        server_public_key=b64url_encode(public_key_bytes(signing_key.public_key())),
        expires_at=expires_at,
    )


@router.post("/tasks/{task_pub_id}/complete", response_model=TerminalResultView)
def complete_terminal_task(
    task_pub_id: str,
    body: TerminalResult,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    session: Session = Depends(get_db),
) -> TerminalResultView:
    repository = _repository(session, x_tenant_id)
    row = session.execute(
        select(TerminalTask, DeviceBinding, InterventionRequest, PlatformAccount)
        .join(DeviceBinding, DeviceBinding.id == TerminalTask.device_binding_id)
        .join(InterventionRequest, InterventionRequest.id == TerminalTask.intervention_id)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            TerminalTask.tenant_id == repository.tenant.id,
            TerminalTask.pub_id == task_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=410, detail={"code": "terminal_task_invalid"})
    task, device, intervention, account = row
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
    if task.state == "issued" and task.expires_at <= now:
        _record_expiry(
            session,
            tenant_id=repository.tenant.id,
            account=account,
            intervention=intervention,
            task=task,
        )
        raise HTTPException(status_code=410, detail={"code": "terminal_task_invalid"})
    if (
        task.state != "issued"
        or device.state != "active"
        or device.revoked_at is not None
        or account.state == "revoked"
        or intervention.state != "task_issued"
        or authorization is None
        or intervention.action not in json.loads(authorization.scopes_json)
        or intervention.action in json.loads(authorization.forbidden_actions_json)
    ):
        raise HTTPException(status_code=410, detail={"code": "terminal_task_invalid"})
    result_payload = canonical_json(
        {
            "evidence_hash": body.evidence_hash,
            "result": body.result,
            "task_payload_sha256": hashlib.sha256(task.payload_json.encode()).hexdigest(),
            "task_pub_id": task.pub_id,
            "version": 1,
        }
    )
    try:
        verify_signature(
            public_key_from_text(b64url_encode(device.public_key)),
            body.terminal_signature,
            result_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"code": "terminal_result_invalid"}) from exc

    task.state = "completed"
    task.consumed_at = now
    task.result = body.result
    task.evidence_hash = body.evidence_hash
    device.last_used_at = now
    intervention.state = (
        "awaiting_platform_probe" if body.result == "challenge_completed" else body.result
    )
    intervention.platform_result = None if body.result == "challenge_completed" else body.result
    intervention.evidence_hash = body.evidence_hash
    intervention.completed_at = now
    account.state = "challenge_required"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="customer_terminal.task_completed",
            summary_json=json.dumps(
                {
                    "challenge_type": intervention.challenge_type,
                    "intervention_pub_id": intervention.pub_id,
                    "platform_result": body.result,
                    "task_pub_id": task.pub_id,
                },
                sort_keys=True,
            ),
        )
    )
    session.commit()
    return TerminalResultView(
        task_pub_id=task.pub_id,
        intervention_pub_id=intervention.pub_id,
        state=task.state,
        platform_result=body.result,
        completed_at=now,
    )
