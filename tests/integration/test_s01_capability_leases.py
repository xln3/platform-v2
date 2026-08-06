import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from geo_platform.collection.models import (
    AccountAuthorization,
    CapabilityLease,
    PlatformAccount,
    SessionEvent,
)
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import select


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


def provision() -> tuple[TestClient, str, dict[str, str], str, str]:
    client = TestClient(app)
    subject = "capability-" + secrets.token_hex(5)
    tenant, admin_headers = bootstrap(client, subject)
    account = client.post(
        "/api/v2/platform-accounts",
        headers=admin_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "fixture-capability-***",
            "owner_pub_id": "owner_capability",
            "purpose": "authorized-evidence",
            "responsible_pub_id": subject,
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    )
    assert account.status_code == 201
    now = datetime.now(UTC)
    authorization = client.post(
        f"/api/v2/platform-accounts/{account.json()['pub_id']}/authorizations",
        headers=admin_headers,
        json={
            "scopes": ["read", "query"],
            "forbidden_actions": ["publish"],
            "regions": ["CN-BJ"],
            "valid_from": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert authorization.status_code == 201
    profile = client.post(
        f"/api/v2/platform-accounts/{account.json()['pub_id']}/profiles/enroll",
        headers=admin_headers,
        json={
            "profile_payload": '{"fixture":"authorized-session"}',
            "custody_mode": "server",
            "constraints": ["READ_ONLY"],
        },
    )
    assert profile.status_code == 201
    worker = client.post(
        "/api/v2/identity/service-accounts",
        headers=admin_headers,
        json={"name": "Evidence Worker", "expires_in_hours": 1},
    )
    assert worker.status_code == 201
    return client, tenant, admin_headers, account.json()["pub_id"], worker.json()["token"]


def issue(
    client: TestClient, admin_headers: dict[str, str], account_pub_id: str
) -> dict[str, object]:
    response = client.post(
        "/api/v2/collection/capability-leases",
        headers=admin_headers,
        json={
            "platform_account_pub_id": account_pub_id,
            "allowed_domains": ["secure.example"],
            "allowed_actions": ["capture"],
            "authorization_scope": ["read"],
            "subject_workflow_id": "evidence-capture/workflow-1",
            "ttl_seconds": 600,
            "max_uses": 10,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def validation_body(tenant: str, account_pub_id: str) -> dict[str, object]:
    return {
        "tenant_pub_id": tenant,
        "platform_account_pub_id": account_pub_id,
        "target_url": "https://docs.secure.example/article",
        "action": "capture",
        "required_scopes": ["read"],
        "workflow_id": "evidence-capture/workflow-1",
    }


def test_capability_lease_validate_then_revoke_is_secret_free() -> None:
    client, tenant, admin_headers, account_pub_id, service_token = provision()
    lease = issue(client, admin_headers, account_pub_id)
    validated = client.post(
        f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/validate",
        headers={"X-Service-Token": service_token},
        json=validation_body(tenant, account_pub_id),
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["issuer"] == "s01-session-gateway"
    assert validated.json()["use_count"] == 1
    forbidden = {
        "cookie",
        "authorization",
        "token",
        "profile_path",
        "profile_object_key",
        "device_key",
        "proxy_password",
        "otp",
    }
    assert forbidden.isdisjoint({key.lower() for key in validated.json()})

    revoked = client.post(
        f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/revoke",
        headers=admin_headers,
    )
    assert revoked.status_code == 200
    rejected = client.post(
        f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/validate",
        headers={"X-Service-Token": service_token},
        json=validation_body(tenant, account_pub_id),
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "capability_lease_revoked"


def test_capability_lease_rejects_wrong_bindings_and_expiry_with_audit() -> None:
    client, tenant, admin_headers, account_pub_id, service_token = provision()
    cases = [
        ("platform_account_pub_id", "pac_wrong", "wrong_account"),
        ("target_url", "https://evil.example/article", "wrong_domain"),
        ("action", "publish", "wrong_action"),
        ("required_scopes", ["query"], "wrong_scope"),
        ("workflow_id", "evidence-capture/other-workflow", "wrong_workflow"),
    ]
    for field, value, code in cases:
        lease = issue(client, admin_headers, account_pub_id)
        body = validation_body(tenant, account_pub_id)
        body[field] = value
        response = client.post(
            f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/validate",
            headers={"X-Service-Token": service_token},
            json=body,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == f"capability_lease_{code}"

    lease = issue(client, admin_headers, account_pub_id)
    with SessionLocal() as session:
        row = session.scalar(
            select(CapabilityLease).where(CapabilityLease.pub_id == lease["lease_pub_id"])
        )
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired = client.post(
        f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/validate",
        headers={"X-Service-Token": service_token},
        json=validation_body(tenant, account_pub_id),
    )
    assert expired.status_code == 403
    assert expired.json()["error"]["code"] == "capability_lease_expired"
    with SessionLocal() as session:
        denials = session.scalars(
            select(SessionEvent).where(
                SessionEvent.event_type == "capability_lease.validation_denied"
            )
        ).all()
        assert len(denials) >= 6
        for event in denials[-6:]:
            summary = json.loads(event.summary_json)
            assert set(summary) == {"lease_pub_id", "result"}


def test_narrower_authorization_atomically_replaces_old_grant_and_revokes_lease() -> None:
    client, tenant, admin_headers, account_pub_id, service_token = provision()
    lease = issue(client, admin_headers, account_pub_id)
    now = datetime.now(UTC)
    narrowed = client.post(
        f"/api/v2/platform-accounts/{account_pub_id}/authorizations",
        headers=admin_headers,
        json={
            "scopes": ["query"],
            "forbidden_actions": ["capture", "publish"],
            "regions": ["CN-BJ"],
            "valid_from": now.isoformat(),
            # Deliberately shorter than the old broad grant. Selection by the
            # farthest valid_until must never resurrect that old authority.
            "valid_until": (now + timedelta(minutes=10)).isoformat(),
        },
    )
    assert narrowed.status_code == 201
    rejected = client.post(
        f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/validate",
        headers={"X-Service-Token": service_token},
        json=validation_body(tenant, account_pub_id),
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "capability_lease_revoked"
    with SessionLocal() as session:
        account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account_pub_id)
        )
        assert account is not None
        authorizations = session.scalars(
            select(AccountAuthorization)
            .where(AccountAuthorization.account_id == account.id)
            .order_by(AccountAuthorization.created_at)
        ).all()
        assert len(authorizations) == 2
        assert authorizations[0].revoked_at is not None
        assert authorizations[1].revoked_at is None
        assert json.loads(authorizations[1].scopes_json) == ["query"]
        persisted_lease = session.scalar(
            select(CapabilityLease).where(CapabilityLease.pub_id == lease["lease_pub_id"])
        )
        assert persisted_lease is not None
        assert persisted_lease.revoked_at is not None
