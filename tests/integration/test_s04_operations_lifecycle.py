from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from geo_platform.main import app


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant_pub_id = response.json()["tenant_pub_id"]
    return tenant_pub_id, {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def test_operations_lifecycle_is_live_bounded_and_tenant_scoped() -> None:
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    tenant_pub_id, admin_headers = _bootstrap(client, f"operations-admin-{suffix}")
    _foreign_tenant_pub_id, foreign_headers = _bootstrap(client, f"operations-foreign-{suffix}")
    customer_subject = f"operations-customer-{suffix}"
    responsible_subject = f"operations-responsible-{suffix}"
    customer = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": customer_subject,
            "display_name": "Customer Owner",
            "role": "customer",
        },
    )
    responsible = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": responsible_subject,
            "display_name": "Operations Responsible",
            "role": "operator",
        },
    )
    assert customer.status_code == responsible.status_code == 201
    customer_headers = {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": customer_subject,
        "X-Actor-Role": "customer",
    }
    account = client.post(
        "/api/v2/customer/platform-accounts",
        headers=customer_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "operations-***21",
            "custody_mode": "customer_device",
            "region": "CN-SH",
            "responsible_member_pub_id": responsible.json()["user_pub_id"],
        },
    )
    assert account.status_code == 201
    authorized = client.post(
        f"/api/v2/customer/platform-accounts/{account.json()['pub_id']}/authorizations",
        headers=customer_headers,
        json={
            "scopes": ["read", "query"],
            "regions": ["CN-SH"],
            "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "responsible_member_pub_id": responsible.json()["user_pub_id"],
        },
    )
    assert authorized.status_code == 200

    response = client.get("/api/v2/operations/lifecycle?limit=25", headers=admin_headers)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["metrics"]["project_count"] >= 0
    assert snapshot["metrics"]["running_runs"] >= 0
    assert [item["pub_id"] for item in snapshot["accounts"]] == [account.json()["pub_id"]]
    assert snapshot["accounts"][0]["account_mask"] == "operations-***21"
    assert set(snapshot["accounts"][0]["scopes"]) == {"read", "query"}
    assert snapshot["projection"]["accounts"] == {
        "total": 1,
        "shown": 1,
        "truncated": False,
    }
    assert all(
        len(snapshot[key]) <= 25 for key in ("activity", "accounts", "interventions", "events")
    )
    assert customer_subject not in response.text
    assert responsible_subject not in response.text

    forbidden = client.get("/api/v2/operations/lifecycle", headers=customer_headers)
    assert forbidden.status_code == 403

    foreign = client.get("/api/v2/operations/lifecycle", headers=foreign_headers)
    assert foreign.status_code == 200
    assert foreign.json()["accounts"] == []
    assert foreign.json()["projection"]["accounts"]["total"] == 0
