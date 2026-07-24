from fastapi.testclient import TestClient
from geo_platform.main import app


def test_health_contract() -> None:
    response = TestClient(app).get("/api/v2/health")
    assert response.status_code == 200
    assert response.json()["service"] == "geo-platform-v2"


def test_project_mock_requires_tenant() -> None:
    response = TestClient(app).get("/api/v2/projects")
    assert response.status_code == 422


def test_project_route_does_not_fall_back_to_contract_mock() -> None:
    response = TestClient(app).get(
        "/api/v2/projects",
        headers={"X-Tenant-Id": "tnt_01K10D5Z70X5T9V9C8ZJS1R0AB"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
