import secrets

from fastapi.testclient import TestClient
from geo_platform.main import app


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def _create_member(
    client: TestClient,
    admin_headers: dict[str, str],
    tenant: str,
    role: str,
) -> dict[str, str]:
    subject = f"{role}-" + secrets.token_hex(8)
    response = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": subject, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
    }


def _create_project(client: TestClient, headers: dict[str, str]) -> str:
    headers["Idempotency-Key"] = "project-" + secrets.token_hex(16)
    response = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Confirmation Project", "customer_name": "Confirmation Customer"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def test_versioned_customer_profile_idempotency_permissions_and_isolation() -> None:
    client = TestClient(app)
    tenant, admin_headers = _bootstrap(client, "confirm-admin-" + secrets.token_hex(6))
    project = _create_project(client, admin_headers)
    customer_headers = _create_member(client, admin_headers, tenant, "customer")
    analyst_headers = _create_member(client, admin_headers, tenant, "analyst")
    body = {
        "company_name": "Example Industries",
        "contact_role": "Brand manager",
        "audience": "Enterprise procurement teams in regulated industries.",
        "public_statement": "We provide independently verifiable workflow software.",
        "truth_confirmed": True,
    }
    key = "profile-" + secrets.token_hex(16)
    write_headers = {**customer_headers, "Idempotency-Key": key}
    first = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers=write_headers,
        json=body,
    )
    replay = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers=write_headers,
        json=body,
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["pub_id"] == replay.json()["pub_id"]
    assert first.json()["revision"] == 1
    assert "declared_by" not in first.json()

    conflict = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers=write_headers,
        json={**body, "company_name": "Different Industries"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    second = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers={**customer_headers, "Idempotency-Key": "profile-" + secrets.token_hex(16)},
        json={**body, "contact_role": "Communications director"},
    )
    assert second.status_code == 201
    page = client.get(
        f"/api/v2/projects/{project}/client-profile/versions?limit=1",
        headers=customer_headers,
    )
    assert page.status_code == 200
    assert [item["revision"] for item in page.json()["data"]] == [2]
    assert page.json()["next_cursor"] == "2"
    next_page = client.get(
        f"/api/v2/projects/{project}/client-profile/versions?limit=1&cursor=2",
        headers=customer_headers,
    )
    assert [item["revision"] for item in next_page.json()["data"]] == [1]

    denied = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers={**analyst_headers, "Idempotency-Key": "profile-" + secrets.token_hex(16)},
        json=body,
    )
    assert denied.status_code == 403
    secret = "Authorization: Bearer " + "x" * 32
    rejected = client.post(
        f"/api/v2/projects/{project}/client-profile/versions",
        headers={**customer_headers, "Idempotency-Key": "profile-" + secrets.token_hex(16)},
        json={**body, "public_statement": f"Never persist {secret} in this field."},
    )
    assert rejected.status_code == 422
    assert secret not in rejected.text

    _, other_headers = _bootstrap(client, "confirm-other-" + secrets.token_hex(6))
    hidden = client.get(
        f"/api/v2/projects/{project}/client-profile/versions", headers=other_headers
    )
    assert hidden.status_code == 404


def test_asset_confirmation_atomically_projects_catalog_and_replays_once() -> None:
    client = TestClient(app)
    tenant, admin_headers = _bootstrap(client, "asset-admin-" + secrets.token_hex(6))
    project = _create_project(client, admin_headers)
    customer_headers = _create_member(client, admin_headers, tenant, "customer")
    body = {
        "brand_name": "Example Brand",
        "website": "https://brand.example",
        "product_name": "Evidence Cloud",
        "competitor_name": "Example Rival",
        "prohibited_claim": "Do not claim guaranteed rankings.",
        "truth_confirmed": True,
    }
    headers = {
        **customer_headers,
        "Idempotency-Key": "assets-" + secrets.token_hex(16),
    }
    first = client.post(
        f"/api/v2/projects/{project}/asset-confirmations", headers=headers, json=body
    )
    replay = client.post(
        f"/api/v2/projects/{project}/asset-confirmations", headers=headers, json=body
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["pub_id"] == replay.json()["pub_id"]
    assert "declared_by" not in first.json()

    confirmations = client.get(
        f"/api/v2/projects/{project}/asset-confirmations", headers=customer_headers
    )
    assert confirmations.status_code == 200
    assert len(confirmations.json()["data"]) == 1
    brands = client.get(f"/api/v2/projects/{project}/resources/brands", headers=customer_headers)
    competitors = client.get(
        f"/api/v2/projects/{project}/resources/competitors", headers=customer_headers
    )
    assert [item["data"]["name"] for item in brands.json()] == ["Example Brand"]
    assert [item["data"]["name"] for item in competitors.json()] == ["Example Rival"]

    invalid = client.post(
        f"/api/v2/projects/{project}/asset-confirmations",
        headers={
            **customer_headers,
            "Idempotency-Key": "assets-" + secrets.token_hex(16),
        },
        json={**body, "brand_name": "Rejected Brand", "website": "http://unsafe.example"},
    )
    assert invalid.status_code == 422
    brands_after = client.get(
        f"/api/v2/projects/{project}/resources/brands", headers=customer_headers
    )
    assert [item["data"]["name"] for item in brands_after.json()] == ["Example Brand"]
