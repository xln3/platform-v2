import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from geo_platform.collection.models import (
    AccountAuthorization,
    DeviceBinding,
    InterventionRequest,
    PlatformAccount,
    RevocationRequest,
    TerminalTask,
)
from geo_platform.collection.workflow_outbox import (
    WorkflowStartCommand,
    WorkflowStartOutbox,
)
from geo_platform.config import get_settings
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from workflows.activities.collection import finalize_account_revocation


def bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant = response.json()["tenant_pub_id"]
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_customer_safe_account_authorization_pairing_and_projection() -> None:
    client = TestClient(app)
    admin = "customer-safe-admin-" + secrets.token_hex(5)
    tenant, admin_headers = bootstrap(client, admin)
    customer = "customer-safe-" + secrets.token_hex(5)
    member = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": customer, "display_name": "Customer Owner", "role": "customer"},
    )
    assert member.status_code == 201
    responsible = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": "responsible-" + secrets.token_hex(5),
            "display_name": "Operations Responsible",
            "role": "operator",
        },
    )
    assert responsible.status_code == 201
    customer_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": customer,
        "X-Actor-Role": "customer",
    }
    responsible_members = client.get(
        "/api/v2/customer/platform-accounts/responsible-members",
        headers=customer_headers,
    )
    assert responsible_members.status_code == 200
    projected_responsible = next(
        item
        for item in responsible_members.json()
        if item["user_pub_id"] == responsible.json()["user_pub_id"]
    )
    assert projected_responsible == {
        "user_pub_id": responsible.json()["user_pub_id"],
        "label": f"成员 · {responsible.json()['user_pub_id'][-8:]}",
        "role": "operator",
    }
    assert "Operations Responsible" not in responsible_members.text
    registered = client.post(
        "/api/v2/customer/platform-accounts",
        headers=customer_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "customer-***21",
            "custody_mode": "customer_device",
            "region": "CN-SH",
            "responsible_member_pub_id": responsible.json()["user_pub_id"],
        },
    )
    assert registered.status_code == 201, registered.text
    account = registered.json()
    authorized = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["read", "query"],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "responsible_member_pub_id": responsible.json()["user_pub_id"],
        },
    )
    assert authorized.status_code == 200
    _, foreign_admin_headers = bootstrap(
        client, "foreign-responsible-admin-" + secrets.token_hex(5)
    )
    foreign_member = client.post(
        "/api/v2/identity/members",
        headers=foreign_admin_headers,
        json={
            "subject": "foreign-responsible-" + secrets.token_hex(5),
            "display_name": "Foreign Responsible",
            "role": "operator",
        },
    )
    assert foreign_member.status_code == 201
    foreign_assignment = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["read"],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "responsible_member_pub_id": foreign_member.json()["user_pub_id"],
        },
    )
    assert foreign_assignment.status_code == 404
    assert foreign_assignment.json()["error"]["code"] == "responsible_member_not_found"
    assert foreign_member.json()["user_pub_id"] not in responsible_members.text
    pairing = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
        json={
            "allowed_domain": "fixed.example",
            "action": "read",
            "challenge_type": "qr",
        },
    )
    assert pairing.status_code == 201
    assert "pairing_token" not in pairing.json()

    operations_pairing = client.post(
        f"/api/v2/interventions/{pairing.json()['pub_id']}/pair",
        headers=admin_headers,
    )
    assert operations_pairing.status_code == 200
    token = operations_pairing.json()["pairing_token"]
    direct_completion = client.post(
        f"/api/v2/interventions/{pairing.json()['pub_id']}/complete",
        headers=admin_headers,
        json={
            "pairing_token": token,
            "platform_result": "verified",
            "evidence_hash": "a" * 64,
        },
    )
    assert direct_completion.status_code == 409
    assert direct_completion.json()["error"]["code"] == "terminal_proof_required"

    device_key = Ed25519PrivateKey.generate()
    device_public_key = device_key.public_key().public_bytes_raw()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    proof = canonical(
        {
            "intervention_pub_id": pairing.json()["pub_id"],
            "pairing_token_sha256": token_hash,
            "purpose": "geo-terminal-bind",
            "tenant_pub_id": tenant,
            "version": 1,
        }
    )
    rejected_proof = client.post(
        f"/api/v2/terminal/interventions/{pairing.json()['pub_id']}/bind",
        headers={"X-Tenant-Id": tenant},
        json={
            "pairing_token": token,
            "device_label": "Customer device",
            "device_public_key": b64url(device_public_key),
            "proof_signature": b64url(b"\x00" * 64),
        },
    )
    assert rejected_proof.status_code == 401
    task_response = client.post(
        f"/api/v2/terminal/interventions/{pairing.json()['pub_id']}/bind",
        headers={"X-Tenant-Id": tenant},
        json={
            "pairing_token": token,
            "device_label": "Customer device",
            "device_public_key": b64url(device_public_key),
            "proof_signature": b64url(device_key.sign(proof)),
        },
    )
    assert task_response.status_code == 201, task_response.text
    assert (
        client.post(
            f"/api/v2/terminal/interventions/{pairing.json()['pub_id']}/bind",
            headers={"X-Tenant-Id": tenant},
            json={
                "pairing_token": token,
                "device_label": "Customer device",
                "device_public_key": b64url(device_public_key),
                "proof_signature": b64url(device_key.sign(proof)),
            },
        ).status_code
        == 410
    )
    task = task_response.json()
    Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(
            b"geo-development-terminal-task-key\x00" + b"development-only-kms-master-key-change-me"
        ).digest()
    ).public_key().verify(
        base64.urlsafe_b64decode(task["server_signature"] + "=="),
        canonical(task["payload"]),
    )
    result_payload = canonical(
        {
            "evidence_hash": "a" * 64,
            "result": "challenge_completed",
            "task_payload_sha256": hashlib.sha256(canonical(task["payload"])).hexdigest(),
            "task_pub_id": task["task_pub_id"],
            "version": 1,
        }
    )
    completed = client.post(
        f"/api/v2/terminal/tasks/{task['task_pub_id']}/complete",
        headers={"X-Tenant-Id": tenant},
        json={
            "result": "challenge_completed",
            "evidence_hash": "a" * 64,
            "terminal_signature": b64url(device_key.sign(result_payload)),
        },
    )
    assert completed.status_code == 200
    assert completed.json()["platform_result"] == "challenge_completed"
    attested = client.post(
        f"/api/v2/interventions/{pairing.json()['pub_id']}/attest",
        headers=admin_headers,
        json={
            "proof_source": "identity_probe",
            "platform_result": "verified",
            "evidence_hash": "c" * 64,
        },
    )
    assert attested.status_code == 200
    assert attested.json()["platform_result"] == "verified"
    replay = client.post(
        f"/api/v2/terminal/tasks/{task['task_pub_id']}/complete",
        headers={"X-Tenant-Id": tenant},
        json={
            "result": "challenge_completed",
            "evidence_hash": "a" * 64,
            "terminal_signature": b64url(device_key.sign(result_payload)),
        },
    )
    assert replay.status_code == 410

    listing = client.get("/api/v2/customer/platform-accounts", headers=customer_headers)
    assert listing.status_code == 200
    projected = next(item for item in listing.json() if item["pub_id"] == account["pub_id"])
    assert projected["account_mask"] == "customer-***21"
    assert projected["scopes"] == ["query", "read"]
    assert projected["intervention_status"] == "completed"
    allowed_keys = {
        "pub_id",
        "account_mask",
        "platform_label",
        "owner_label",
        "custody_mode",
        "admission_level",
        "scopes",
        "authorization_expires_at",
        "region_label",
        "session_health",
        "last_verified_at",
        "intervention_status",
        "revocation_receipt_pub_id",
        "revoked_at",
    }
    assert set(projected) == allowed_keys
    rendered = json.dumps(projected).lower()
    for canary in ("cookie=", "bearer ", "profile_path", "proxy_password", "device_key", "otp="):
        assert canary not in rendered
    events = client.get(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/events",
        headers=customer_headers,
    )
    assert events.status_code == 200
    assert all(set(item) == {"pub_id", "event_type", "occurred_at"} for item in events.json())

    expiry_pairing = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
        json={
            "allowed_domain": "fixed.example",
            "action": "read",
            "challenge_type": "passkey",
        },
    ).json()
    expiry_token = client.post(
        f"/api/v2/interventions/{expiry_pairing['pub_id']}/pair",
        headers=admin_headers,
    ).json()["pairing_token"]
    expiry_proof = canonical(
        {
            "intervention_pub_id": expiry_pairing["pub_id"],
            "pairing_token_sha256": hashlib.sha256(expiry_token.encode()).hexdigest(),
            "purpose": "geo-terminal-bind",
            "tenant_pub_id": tenant,
            "version": 1,
        }
    )
    with SessionLocal() as session:
        account_row = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert account_row is not None
        authorization_row = session.scalar(
            select(AccountAuthorization)
            .where(AccountAuthorization.account_id == account_row.id)
            .order_by(AccountAuthorization.created_at.desc())
        )
        assert authorization_row is not None
        authorization_row.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired_authorization_bind = client.post(
        f"/api/v2/terminal/interventions/{expiry_pairing['pub_id']}/bind",
        headers={"X-Tenant-Id": tenant},
        json={
            "pairing_token": expiry_token,
            "device_label": "Customer device",
            "device_public_key": b64url(device_public_key),
            "proof_signature": b64url(device_key.sign(expiry_proof)),
        },
    )
    assert expired_authorization_bind.status_code == 410
    with SessionLocal() as session:
        authorization_row = session.scalar(
            select(AccountAuthorization)
            .join(PlatformAccount, PlatformAccount.id == AccountAuthorization.account_id)
            .where(PlatformAccount.pub_id == account["pub_id"])
            .order_by(AccountAuthorization.created_at.desc())
        )
        assert authorization_row is not None
        authorization_row.valid_until = datetime.now(UTC) + timedelta(days=30)
        session.commit()

    pending_pairing = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
        json={
            "allowed_domain": "fixed.example",
            "action": "read",
            "challenge_type": "passkey",
        },
    )
    pending_token = client.post(
        f"/api/v2/interventions/{pending_pairing.json()['pub_id']}/pair",
        headers=admin_headers,
    ).json()["pairing_token"]
    pending_proof = canonical(
        {
            "intervention_pub_id": pending_pairing.json()["pub_id"],
            "pairing_token_sha256": hashlib.sha256(pending_token.encode()).hexdigest(),
            "purpose": "geo-terminal-bind",
            "tenant_pub_id": tenant,
            "version": 1,
        }
    )
    pending_task = client.post(
        f"/api/v2/terminal/interventions/{pending_pairing.json()['pub_id']}/bind",
        headers={"X-Tenant-Id": tenant},
        json={
            "pairing_token": pending_token,
            "device_label": "Customer device",
            "device_public_key": b64url(device_public_key),
            "proof_signature": b64url(device_key.sign(pending_proof)),
        },
    ).json()
    with SessionLocal() as session:
        expiring_task = session.scalar(
            select(TerminalTask).where(TerminalTask.pub_id == pending_task["task_pub_id"])
        )
        assert expiring_task is not None
        expiring_task.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired_task = client.post(
        f"/api/v2/terminal/tasks/{pending_task['task_pub_id']}/complete",
        headers={"X-Tenant-Id": tenant},
        json={
            "result": "challenge_completed",
            "evidence_hash": "b" * 64,
            "terminal_signature": b64url(b"\x00" * 64),
        },
    )
    assert expired_task.status_code == 410
    pairing_states = client.get(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
    ).json()
    assert (
        next(
            item["state"]
            for item in pairing_states
            if item["pub_id"] == pending_pairing.json()["pub_id"]
        )
        == "expired"
    )

    revocation_pairing = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
        json={
            "allowed_domain": "fixed.example",
            "action": "read",
            "challenge_type": "passkey",
        },
    ).json()
    revocation_token = client.post(
        f"/api/v2/interventions/{revocation_pairing['pub_id']}/pair",
        headers=admin_headers,
    ).json()["pairing_token"]
    revocation_proof = canonical(
        {
            "intervention_pub_id": revocation_pairing["pub_id"],
            "pairing_token_sha256": hashlib.sha256(revocation_token.encode()).hexdigest(),
            "purpose": "geo-terminal-bind",
            "tenant_pub_id": tenant,
            "version": 1,
        }
    )
    revocation_task = client.post(
        f"/api/v2/terminal/interventions/{revocation_pairing['pub_id']}/bind",
        headers={"X-Tenant-Id": tenant},
        json={
            "pairing_token": revocation_token,
            "device_label": "Customer device",
            "device_public_key": b64url(device_public_key),
            "proof_signature": b64url(device_key.sign(revocation_proof)),
        },
    ).json()
    downgraded = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["query"],
            "forbidden_actions": ["read"],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert downgraded.status_code == 200
    with SessionLocal() as session:
        downgraded_task = session.scalar(
            select(TerminalTask).where(TerminalTask.pub_id == revocation_task["task_pub_id"])
        )
        assert downgraded_task is not None
        assert downgraded_task.state == "revoked"
        downgraded_intervention = session.get(InterventionRequest, downgraded_task.intervention_id)
        assert downgraded_intervention is not None
        assert downgraded_intervention.state == "revoked"
        assert downgraded_intervention.pairing_token_hash is None
    restored = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["read", "query"],
            "forbidden_actions": [],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert restored.status_code == 200

    revoked = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/revoke",
        headers=customer_headers,
    )
    assert revoked.status_code == 202
    replayed_revocation = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/revoke",
        headers=customer_headers,
    )
    assert replayed_revocation.status_code == 202
    assert replayed_revocation.json()["workflow_id"] == revoked.json()["workflow_id"]
    reauthorize_revoked = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["read"],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert reauthorize_revoked.status_code == 409
    pairing_revoked = client.post(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
        headers=customer_headers,
        json={
            "allowed_domain": "fixed.example",
            "action": "read",
            "challenge_type": "passkey",
        },
    )
    assert pairing_revoked.status_code == 409
    quarantine_revoked = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/quarantine",
        headers=admin_headers,
        params={"reason": "must-remain-terminal"},
    )
    assert quarantine_revoked.status_code == 409
    revoked_task = client.post(
        f"/api/v2/terminal/tasks/{revocation_task['task_pub_id']}/complete",
        headers={"X-Tenant-Id": tenant},
        json={
            "result": "challenge_completed",
            "evidence_hash": "b" * 64,
            "terminal_signature": b64url(b"\x00" * 64),
        },
    )
    assert revoked_task.status_code == 410
    with SessionLocal() as session:
        revoked_account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert revoked_account is not None
        revocation_request = session.scalar(
            select(RevocationRequest).where(RevocationRequest.account_id == revoked_account.id)
        )
        assert revocation_request is not None
        revocation_request_pub_id = revocation_request.pub_id
    first_propagation = finalize_account_revocation(tenant, account["pub_id"])
    second_propagation = finalize_account_revocation(tenant, account["pub_id"])
    assert first_propagation.deletion_verified is True
    assert second_propagation.deletion_verified is True
    assert first_propagation.revoked_device_bindings >= 1
    assert first_propagation.revoked_terminal_tasks >= 1
    assert first_propagation.revoked_interventions >= 1
    with SessionLocal() as session:
        completed_request = session.scalar(
            select(RevocationRequest).where(RevocationRequest.pub_id == revocation_request_pub_id)
        )
        assert completed_request is not None
        assert completed_request.state == "completed"
        assert completed_request.deletion_verified_at is not None
        session.execute(
            text(
                """
                UPDATE integration.workflow_start_command
                SET state='started',started_at=now()
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": completed_request.workflow_id},
        )
        session.commit()
        assert all(
            item.state == "revoked" and item.revoked_at is not None
            for item in session.scalars(
                select(DeviceBinding).where(
                    DeviceBinding.account_id == completed_request.account_id
                )
            )
        )
        interventions = session.scalars(
            select(InterventionRequest).where(
                InterventionRequest.account_id == completed_request.account_id
            )
        ).all()
        assert all(item.pairing_token_hash is None for item in interventions)
        assert all(
            item.state != "issued"
            for item in session.scalars(
                select(TerminalTask).where(
                    TerminalTask.intervention_id.in_([item.id for item in interventions])
                )
            )
        )
    with SessionLocal() as session:
        terminal_account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert terminal_account is not None
        terminal_account.state = "active"
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with SessionLocal() as session:
        terminal_account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert terminal_account is not None
        assert terminal_account.state == "revoked"


def test_revocation_commits_fail_closed_state_with_durable_start_command() -> None:
    client = TestClient(app)
    subject = "revocation-rollback-admin-" + secrets.token_hex(5)
    tenant, admin_headers = bootstrap(client, subject)
    customer = "revocation-rollback-customer-" + secrets.token_hex(5)
    member = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": customer, "display_name": "Customer", "role": "customer"},
    )
    assert member.status_code == 201
    customer_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": customer,
        "X-Actor-Role": "customer",
    }
    registered = client.post(
        "/api/v2/customer/platform-accounts",
        headers=customer_headers,
        json={
            "platform_slug": "fixed-rollback",
            "platform_name": "Fixed rollback",
            "account_mask": "rollback-***",
            "custody_mode": "customer_device",
            "region": "CN-SH",
        },
    )
    assert registered.status_code == 201
    account_pub_id = registered.json()["pub_id"]

    response = client.post(
        f"/api/v2/customer/platform-accounts/{account_pub_id}/revoke",
        headers=customer_headers,
    )
    assert response.status_code == 202

    with SessionLocal() as session:
        account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account_pub_id)
        )
        assert account is not None
        assert account.state == "revoked"
        request = session.scalar(
            select(RevocationRequest).where(RevocationRequest.account_id == account.id)
        )
        assert request is not None
        assert request.state == "starting"
        command = session.execute(
            text(
                """
                SELECT command_id::text,tenant_pub_id,workflow_type,workflow_id,
                       task_queue,payload,trace_context,state
                FROM integration.workflow_start_command
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": request.workflow_id},
        ).one()
        assert command[7] == "pending"
        assert command[2] == "account_revocation"
        session.execute(
            text(
                """
                UPDATE integration.workflow_start_command
                SET state='started',started_at=now()
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": request.workflow_id},
        )
        request.state = "running"
        session.commit()
        workflow_command = WorkflowStartCommand(*command[:7])

    settings = get_settings()
    WorkflowStartOutbox(
        dsn=settings.worker_postgres_dsn or settings.postgres_dsn,
        temporal=object(),  # type: ignore[arg-type]
    ).reconciled_terminal(workflow_command, "FAILED")
    with SessionLocal() as session:
        request = session.scalar(
            select(RevocationRequest).where(
                RevocationRequest.workflow_id == workflow_command.workflow_id
            )
        )
        assert request is not None
        assert request.state == "failed"
        assert request.error_code == "temporal_failed"
        terminal_status = session.execute(
            text(
                """
                SELECT terminal_status FROM integration.workflow_start_command
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": workflow_command.workflow_id},
        ).scalar_one()
        assert terminal_status == "FAILED"
        session.execute(
            text(
                """
                DELETE FROM integration.workflow_start_command
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": workflow_command.workflow_id},
        )
        session.commit()


def test_customer_cannot_infer_another_customer_account() -> None:
    client = TestClient(app)
    admin = "customer-isolation-admin-" + secrets.token_hex(5)
    tenant, admin_headers = bootstrap(client, admin)
    customer_headers: list[dict[str, str]] = []
    for index in range(2):
        subject = f"customer-isolation-{index}-" + secrets.token_hex(4)
        assert (
            client.post(
                "/api/v2/identity/members",
                headers=admin_headers,
                json={"subject": subject, "display_name": subject, "role": "customer"},
            ).status_code
            == 201
        )
        customer_headers.append(
            {
                "X-Tenant-Id": tenant,
                "X-Actor-Id": subject,
                "X-Actor-Role": "customer",
            }
        )
    account = client.post(
        "/api/v2/customer/platform-accounts",
        headers=customer_headers[0],
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "isolated-***",
            "custody_mode": "customer_device",
            "region": "CN-BJ",
        },
    ).json()
    missing = client.get(
        "/api/v2/customer/platform-accounts/pac_DOESNOTEXIST/events",
        headers=customer_headers[1],
    )
    foreign = client.get(
        f"/api/v2/customer/platform-accounts/{account['pub_id']}/events",
        headers=customer_headers[1],
    )
    assert missing.status_code == foreign.status_code == 404
    assert missing.json()["error"]["code"] == foreign.json()["error"]["code"] == "account_not_found"
    assert missing.json()["error"]["details"] == foreign.json()["error"]["details"] == {}


def test_customer_safe_registration_rejects_secret_bearing_mask() -> None:
    client = TestClient(app)
    admin = "customer-dlp-admin-" + secrets.token_hex(5)
    tenant, admin_headers = bootstrap(client, admin)
    customer = "customer-dlp-" + secrets.token_hex(5)
    assert (
        client.post(
            "/api/v2/identity/members",
            headers=admin_headers,
            json={"subject": customer, "display_name": customer, "role": "customer"},
        ).status_code
        == 201
    )
    response = client.post(
        "/api/v2/customer/platform-accounts",
        headers={
            "X-Tenant-Id": tenant,
            "X-Actor-Id": customer,
            "X-Actor-Role": "customer",
        },
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "Cookie=session-secret",
            "custody_mode": "customer_device",
            "region": "CN-BJ",
        },
    )
    assert response.status_code == 422
