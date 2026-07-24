import secrets

from fastapi.testclient import TestClient
from geo_platform.main import app


def bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def create_resource(
    client: TestClient,
    project: str,
    kind: str,
    request_headers: dict[str, str],
    body: dict[str, object],
) -> dict[str, object]:
    request_headers["Idempotency-Key"] = "idem-" + secrets.token_hex(16)
    response = client.post(
        f"/api/v2/projects/{project}/resources/{kind}",
        headers=request_headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_complete_project_catalog_crud_cas_idempotency_and_tenant_scope() -> None:
    client = TestClient(app)
    subject = "catalog-" + secrets.token_hex(5)
    tenant, request_headers = bootstrap(client, subject)
    project_response = client.post(
        "/api/v2/projects",
        headers=request_headers,
        json={"name": "Catalog Project", "customer_name": "Catalog Customer"},
    )
    assert project_response.status_code == 201
    project = project_response.json()["pub_id"]

    brand = create_resource(
        client,
        project,
        "brands",
        request_headers,
        {"name": "Acme", "website": "https://acme.example"},
    )
    alias = create_resource(
        client,
        project,
        "aliases",
        request_headers,
        {"parent_pub_id": brand["pub_id"], "value": "ACME China"},
    )
    create_resource(
        client,
        project,
        "assets",
        request_headers,
        {
            "parent_pub_id": brand["pub_id"],
            "kind": "website",
            "uri": "https://acme.example/about",
        },
    )
    create_resource(
        client,
        project,
        "competitors",
        request_headers,
        {"name": "Competitor", "website": "https://competitor.example"},
    )
    group = create_resource(
        client, project, "query-groups", request_headers, {"name": "Core Questions"}
    )
    create_resource(
        client,
        project,
        "query-items",
        request_headers,
        {"parent_pub_id": group["pub_id"], "text": "Which product is best?", "priority": 10},
    )
    create_resource(
        client,
        project,
        "goals",
        request_headers,
        {"metric": "mention_rate", "payload": {"target": 0.8}, "state": "draft"},
    )
    create_resource(
        client,
        project,
        "change-requests",
        request_headers,
        {"kind": "pause", "payload": {"reason": "customer requested"}, "state": "pending"},
    )
    for kind in (
        "brands",
        "aliases",
        "assets",
        "competitors",
        "query-groups",
        "query-items",
        "goals",
        "change-requests",
    ):
        response = client.get(
            f"/api/v2/projects/{project}/resources/{kind}", headers=request_headers
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    conflict = client.patch(
        f"/api/v2/projects/{project}/resources/aliases/{alias['pub_id']}",
        headers=request_headers,
        json={"value": "New Alias", "expected_version": 99},
    )
    assert conflict.status_code == 409
    updated = client.patch(
        f"/api/v2/projects/{project}/resources/aliases/{alias['pub_id']}",
        headers=request_headers,
        json={"value": "New Alias", "expected_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert (
        client.delete(
            f"/api/v2/projects/{project}/resources/aliases/{alias['pub_id']}?expected_version=2",
            headers=request_headers,
        ).status_code
        == 204
    )

    other_subject = "catalog-other-" + secrets.token_hex(5)
    _, other_headers = bootstrap(client, other_subject)
    assert (
        client.get(
            f"/api/v2/projects/{project}/resources/brands", headers=other_headers
        ).status_code
        == 404
    )


def test_customer_cursor_idempotency_and_cas() -> None:
    client = TestClient(app)
    subject = "customer-catalog-" + secrets.token_hex(5)
    _, request_headers = bootstrap(client, subject)
    key = "customer-idem-" + secrets.token_hex(16)
    request_headers["Idempotency-Key"] = key
    body = {"name": "Customer One", "external_ref": "crm-42"}
    first = client.post("/api/v2/customers", headers=request_headers, json=body)
    second = client.post("/api/v2/customers", headers=request_headers, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["pub_id"] == second.json()["pub_id"]
    page = client.get("/api/v2/customers?limit=1", headers=request_headers)
    assert page.status_code == 200
    assert any(item["pub_id"] == first.json()["pub_id"] for item in page.json()["data"])
    conflict = client.patch(
        f"/api/v2/customers/{first.json()['pub_id']}",
        headers=request_headers,
        json={"name": "Customer Updated", "expected_version": 99},
    )
    assert conflict.status_code == 409
    updated = client.patch(
        f"/api/v2/customers/{first.json()['pub_id']}",
        headers=request_headers,
        json={"name": "Customer Updated", "expected_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
