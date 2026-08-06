from fastapi.testclient import TestClient
from geo_platform.main import app


def test_health_contract() -> None:
    response = TestClient(app).get("/api/v2/health")
    assert response.status_code == 200
    assert response.json()["service"] == "geo-platform-v2"


def test_project_route_requires_identity() -> None:
    response = TestClient(app).get("/api/v2/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_headers_missing"


def test_tenant_header_alone_cannot_authenticate_project_route() -> None:
    response = TestClient(app).get(
        "/api/v2/projects",
        headers={"X-Tenant-Id": "tnt_01K10D5Z70X5T9V9C8ZJS1R0AB"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_headers_missing"
