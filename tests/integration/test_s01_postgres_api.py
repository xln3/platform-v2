# ruff: noqa: B008

import secrets

from fastapi.testclient import TestClient
from geo_platform.main import app


def bootstrap(client: TestClient, subject: str) -> str:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    return str(response.json()["tenant_pub_id"])


def headers(tenant: str, subject: str, role: str = "admin") -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def test_real_postgres_tenant_scope_idempotency_and_sensitive_projection() -> None:
    client = TestClient(app)
    subject_a = "admin-a-" + secrets.token_hex(5)
    subject_b = "admin-b-" + secrets.token_hex(5)
    tenant_a = bootstrap(client, subject_a)
    tenant_b = bootstrap(client, subject_b)
    request_headers = headers(tenant_a, subject_a)
    body = {"name": "Scoped A", "customer_name": "Customer A"}
    first = client.post("/api/v2/projects", headers=request_headers, json=body)
    second = client.post("/api/v2/projects", headers=request_headers, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["pub_id"] == second.json()["pub_id"]
    visible_a = client.get("/api/v2/projects", headers=headers(tenant_a, subject_a)).json()["data"]
    visible_b = client.get("/api/v2/projects", headers=headers(tenant_b, subject_b)).json()["data"]
    assert first.json()["pub_id"] in {item["pub_id"] for item in visible_a}
    assert first.json()["pub_id"] not in {item["pub_id"] for item in visible_b}

    account = client.post(
        "/api/v2/platform-accounts",
        headers=headers(tenant_a, subject_a),
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "fixture-***09",
            "owner_pub_id": "own_test",
            "purpose": "measure",
            "responsible_pub_id": "usr_test",
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    )
    assert account.status_code == 201
    serialized = account.text.lower()
    assert "cookie" not in serialized
    assert "token" not in serialized
    assert "profile_path" not in serialized
    # Header role spoofing fails membership authentication before authorization.
    assert (
        client.get(
            "/api/v2/platform-accounts", headers=headers(tenant_a, subject_a, "customer")
        ).status_code
        == 401
    )
