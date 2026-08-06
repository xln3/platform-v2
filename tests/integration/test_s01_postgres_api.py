# ruff: noqa: B008

import secrets
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import text


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


def test_collection_start_routes_each_model_to_same_adapter_slug() -> None:
    client = TestClient(app)
    subject = "multi-adapter-" + secrets.token_hex(5)
    tenant = bootstrap(client, subject)
    request_headers = headers(tenant, subject)
    project = client.post(
        "/api/v2/projects",
        headers=request_headers,
        json={"name": "Multi adapter", "customer_name": "Multi adapter"},
    )
    assert project.status_code == 201

    supported = ["doubao", "deepseek", "yiyan", "tongyi", "yuanbao"]
    request_headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
    frozen = client.post(
        f"/api/v2/projects/{project.json()['pub_id']}/config/freeze",
        headers=request_headers,
        json={
            "query_groups": [{"name": "Core", "items": [{"text": "What is GEO?"}]}],
            "regions": ["CN-BJ"],
            "models": supported,
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201

    request_headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
    accepted = client.post(
        "/api/v2/collection/runs",
        headers=request_headers,
        json={
            "project_pub_id": project.json()["pub_id"],
            "config_version_pub_id": frozen.json()["pub_id"],
            "requires_intervention": False,
        },
    )
    assert accepted.status_code == 202
    with SessionLocal() as session:
        payload = session.execute(
            text(
                """
                SELECT payload
                FROM integration.workflow_start_command
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": accepted.json()["workflow_id"]},
        ).scalar_one()

    assert [task["model"] for task in payload["tasks"]] == supported
    assert [task["adapter"] for task in payload["tasks"]] == supported
