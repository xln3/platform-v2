import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from geo_platform.collection.models import (
    BrowserProfile,
    CapabilityLease,
    PlatformAccount,
    RevocationRequest,
    SessionLease,
)
from geo_platform.evidence.session_gateway import SessionGatewayClient
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import select
from temporalio.client import Client

EVIDENCE = Path(__file__).parents[1] / "tests" / "s01-revocation-runtime.json"


def expect(response: object, status: int) -> dict[str, object]:
    assert hasattr(response, "status_code") and response.status_code == status, response.text
    return response.json()


async def run() -> None:
    client = TestClient(app)
    subject = "s01-revoke-" + secrets.token_hex(5)
    identity = expect(
        client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={"tenant_name": "S01 Revoke", "subject": subject, "display_name": "Admin"},
        ),
        201,
    )
    headers = {
        "X-Tenant-Id": str(identity["tenant_pub_id"]),
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "revoke-" + secrets.token_hex(16),
    }
    account = expect(
        client.post(
            "/api/v2/platform-accounts",
            headers=headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Fixed",
                "account_mask": "fixture-revoke-***",
                "owner_pub_id": "own_revoke",
                "purpose": "measure",
                "responsible_pub_id": str(identity["user_pub_id"]),
                "custody_mode": "server",
                "region": "CN-BJ",
            },
        ),
        201,
    )
    now = datetime.now(UTC)
    expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
            headers=headers,
            json={
                "scopes": ["read", "query"],
                "regions": ["CN-BJ"],
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
            },
        ),
        201,
    )
    profile = expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
            headers=headers,
            json={
                "profile_payload": '{"fixture":"encrypted-before-revoke"}',
                "custody_mode": "server",
                "constraints": ["READ_ONLY"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        ),
        201,
    )
    worker = expect(
        client.post(
            "/api/v2/identity/service-accounts",
            headers=headers,
            json={"name": "Revocation Evidence Worker", "expires_in_hours": 1},
        ),
        201,
    )
    capability = expect(
        client.post(
            "/api/v2/collection/capability-leases",
            headers=headers,
            json={
                "platform_account_pub_id": account["pub_id"],
                "allowed_domains": ["secure.example"],
                "allowed_actions": ["capture"],
                "authorization_scope": ["read"],
                "subject_workflow_id": "evidence-capture/revocation-proof",
                "ttl_seconds": 600,
            },
        ),
        201,
    )
    accepted = expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/revoke"
            "?reason=owner-requested-runtime-verification",
            headers=headers,
        ),
        202,
    )
    temporal = await Client.connect("127.0.0.1:17233")
    workflow_result = await temporal.get_workflow_handle(str(accepted["workflow_id"])).result()
    gateway = SessionGatewayClient(
        endpoint="http://127.0.0.1:45200", service_token=str(worker["token"])
    )
    capability_rejected = False
    try:
        gateway.validate_capture_lease(
            lease_pub_id=str(capability["lease_pub_id"]),
            tenant_pub_id=str(identity["tenant_pub_id"]),
            platform_account_pub_id=str(account["pub_id"]),
            target_url="https://docs.secure.example/article",
            action="capture",
            workflow_id="evidence-capture/revocation-proof",
            now=datetime.now(UTC),
        )
    except PermissionError:
        capability_rejected = True
    with SessionLocal() as session:
        account_row = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert account_row is not None
        profile_row = session.scalar(
            select(BrowserProfile).where(BrowserProfile.pub_id == profile["pub_id"])
        )
        request = session.scalar(
            select(RevocationRequest).where(
                RevocationRequest.workflow_id == accepted["workflow_id"]
            )
        )
        active_leases = session.scalars(
            select(SessionLease).where(
                SessionLease.account_id == account_row.id,
                SessionLease.released_at.is_(None),
            )
        ).all()
        capability_row = session.scalar(
            select(CapabilityLease).where(CapabilityLease.pub_id == capability["lease_pub_id"])
        )
        assert profile_row is not None and request is not None and capability_row is not None
        postgres = {
            "account_state": account_row.state,
            "profile_state": profile_row.state,
            "ciphertext_present": profile_row.ciphertext is not None,
            "nonce_present": profile_row.nonce is not None,
            "wrapped_dek_present": profile_row.wrapped_dek is not None,
            "active_lease_count": len(active_leases),
            "capability_lease_revoked": capability_row.revoked_at is not None,
            "request_state": request.state,
            "deletion_verified_at": request.deletion_verified_at.isoformat()
            if request.deletion_verified_at
            else None,
        }
    output = {
        "verified_at": datetime.now(UTC).isoformat(),
        "workflow_id": accepted["workflow_id"],
        "workflow_result": workflow_result,
        "postgres": postgres,
        "contains_secret": False,
        "revoked_capability_validation_rejected": capability_rejected,
    }
    assert capability_rejected
    EVIDENCE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
