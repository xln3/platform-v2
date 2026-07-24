import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from geo_platform.main import app


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
    customer_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": customer,
        "X-Actor-Role": "customer",
    }
    registered = client.post(
        "/api/v2/customer/platform-accounts",
        headers=customer_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "customer-***21",
            "custody_mode": "customer_device",
            "region": "CN-SH",
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
        },
    )
    assert authorized.status_code == 200
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
    completed = client.post(
        f"/api/v2/interventions/{pairing.json()['pub_id']}/complete",
        headers=admin_headers,
        json={
            "pairing_token": operations_pairing.json()["pairing_token"],
            "platform_result": "verified",
            "evidence_hash": "a" * 64,
        },
    )
    assert completed.status_code == 200

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
