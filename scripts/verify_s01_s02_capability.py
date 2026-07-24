import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from geo_platform.evidence.session_gateway import SessionGatewayClient
from geo_platform.main import app

EVIDENCE = Path("tests/s01-s02-capability-runtime.json")


def expect(response, status: int) -> dict[str, object]:
    assert response.status_code == status, response.text
    return response.json()


def run() -> None:
    client = TestClient(app)
    subject = "capability-runtime-" + secrets.token_hex(5)
    bootstrap = expect(
        client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
        ),
        201,
    )
    tenant = str(bootstrap["tenant_pub_id"])
    headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }
    account = expect(
        client.post(
            "/api/v2/platform-accounts",
            headers=headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Fixed",
                "account_mask": "fixture-capability-***",
                "owner_pub_id": "owner_runtime",
                "purpose": "authorized-evidence",
                "responsible_pub_id": subject,
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
    expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
            headers=headers,
            json={
                "profile_payload": '{"fixture":"runtime"}',
                "custody_mode": "server",
                "constraints": ["READ_ONLY"],
            },
        ),
        201,
    )
    worker = expect(
        client.post(
            "/api/v2/identity/service-accounts",
            headers=headers,
            json={"name": "S02 Evidence Worker", "expires_in_hours": 1},
        ),
        201,
    )
    lease = expect(
        client.post(
            "/api/v2/collection/capability-leases",
            headers=headers,
            json={
                "platform_account_pub_id": account["pub_id"],
                "allowed_domains": ["secure.example"],
                "allowed_actions": ["capture"],
                "authorization_scope": ["read"],
                "subject_workflow_id": "evidence-capture/runtime-proof",
                "ttl_seconds": 600,
                "max_uses": 3,
            },
        ),
        201,
    )
    gateway = SessionGatewayClient(
        endpoint="http://127.0.0.1:45200", service_token=str(worker["token"])
    )
    validated = gateway.validate_capture_lease(
        lease_pub_id=str(lease["lease_pub_id"]),
        tenant_pub_id=tenant,
        platform_account_pub_id=str(account["pub_id"]),
        target_url="https://docs.secure.example/article",
        action="capture",
        workflow_id="evidence-capture/runtime-proof",
        now=datetime.now(UTC),
        required_scopes=("read",),
    )
    expect(
        client.post(
            f"/api/v2/collection/capability-leases/{lease['lease_pub_id']}/revoke",
            headers=headers,
        ),
        200,
    )
    revoked_rejected = False
    try:
        gateway.validate_capture_lease(
            lease_pub_id=str(lease["lease_pub_id"]),
            tenant_pub_id=tenant,
            platform_account_pub_id=str(account["pub_id"]),
            target_url="https://docs.secure.example/article",
            action="capture",
            workflow_id="evidence-capture/runtime-proof",
            now=datetime.now(UTC),
            required_scopes=("read",),
        )
    except PermissionError:
        revoked_rejected = True
    output = {
        "verified_at": datetime.now(UTC).isoformat(),
        "lease_pub_id": validated.lease_pub_id,
        "tenant_pub_id": validated.tenant_pub_id,
        "platform_account_pub_id": validated.platform_account_pub_id,
        "allowed_domains": list(validated.allowed_domains),
        "allowed_actions": list(validated.allowed_actions),
        "authorization_scope": list(validated.authorization_scope),
        "subject_workflow_id": validated.subject_workflow_id,
        "signature_verified": validated.signature_verified,
        "revoked_validation_rejected": revoked_rejected,
        "contains_secret": False,
    }
    assert validated.signature_verified
    assert revoked_rejected
    EVIDENCE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    run()
