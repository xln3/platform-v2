from fastapi.testclient import TestClient
from geo_platform.main import app


def test_missing_identity_uses_stable_secret_free_envelope() -> None:
    response = TestClient(app).get(
        "/api/v2/analytics/overview",
        params={"project_pub_id": "secret-value"},
        headers={"X-Request-Id": "req-contract-error"},
    )

    assert response.status_code == 401
    assert response.headers["X-Request-Id"] == "req-contract-error"
    assert response.json() == {
        "error": {
            "code": "identity_headers_missing",
            "message": "identity headers missing",
            "request_id": "req-contract-error",
            "details": {},
        }
    }
    assert "secret-value" not in response.text


def test_internal_error_hides_exception_and_query_values() -> None:
    @app.get("/_test/internal-error", include_in_schema=False)
    def fail() -> None:
        raise RuntimeError("Cookie: session=must-not-leak")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/_test/internal-error?access_token=must-not-leak",
        headers={"X-Request-Id": "req-internal-error"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "internal server error",
            "request_id": "req-internal-error",
            "details": {},
        }
    }
    assert "must-not-leak" not in response.text
