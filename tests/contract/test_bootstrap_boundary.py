from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.main import app


def test_bootstrap_is_disabled_outside_development(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GEO_ENV", "production")
    monkeypatch.setenv("GEO_BOOTSTRAP_SECRET", "test-only-bootstrap")
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "test-only-bootstrap"},
            json={
                "tenant_name": "must-not-exist",
                "subject": "must-not-exist",
                "display_name": "must-not-exist",
            },
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bootstrap_forbidden"
