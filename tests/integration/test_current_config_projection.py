"""Real-Postgres tests for the read-only effective configuration projection."""

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
    assert response.status_code == 201, response.text
    tenant_pub_id = str(response.json()["tenant_pub_id"])
    return tenant_pub_id, {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def _create_project(client: TestClient, headers: dict[str, str], marker: str) -> str:
    response = client.post(
        "/api/v2/projects",
        headers={**headers, "Idempotency-Key": f"config-project-{marker}-000000"},
        json={"name": f"Config {marker}", "customer_name": f"Customer {marker}"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _freeze(
    client: TestClient,
    headers: dict[str, str],
    project_pub_id: str,
    *,
    marker: str,
    effective_at: datetime,
) -> dict[str, object]:
    response = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers={**headers, "Idempotency-Key": f"config-freeze-{marker}-0000000"},
        json={
            "query_groups": [
                {
                    "name": f"Question group {marker}",
                    "items": [{"text": f"Question {marker}?", "priority": 7}],
                }
            ],
            "regions": ["北京"],
            "models": ["doubao"],
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": effective_at.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_current_config_resolves_time_semantics_preserves_snapshot_and_is_tenant_scoped() -> None:
    client = TestClient(app)
    marker = secrets.token_hex(5)
    _, headers_a = _bootstrap(client, f"current-config-a-{marker}")
    _, headers_b = _bootstrap(client, f"current-config-b-{marker}")
    project_pub_id = _create_project(client, headers_a, marker)

    empty = client.get(
        f"/api/v2/projects/{project_pub_id}/config/current",
        headers=headers_a,
    )
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"effective": None, "next_pending": None}

    now = datetime.now(UTC)
    distant_future = _freeze(
        client,
        headers_a,
        project_pub_id,
        marker=f"future-{marker}",
        effective_at=now + timedelta(days=10),
    )
    newest_effective = _freeze(
        client,
        headers_a,
        project_pub_id,
        marker=f"effective-{marker}",
        effective_at=now - timedelta(hours=1),
    )
    older_effective = _freeze(
        client,
        headers_a,
        project_pub_id,
        marker=f"older-{marker}",
        effective_at=now - timedelta(days=2),
    )
    nearest_future = _freeze(
        client,
        headers_a,
        project_pub_id,
        marker=f"next-{marker}",
        effective_at=now + timedelta(days=1),
    )

    response = client.get(
        f"/api/v2/projects/{project_pub_id}/config/current",
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"]["pub_id"] == newest_effective["pub_id"]
    assert body["effective"]["snapshot_hash"] == newest_effective["snapshot_hash"]
    assert body["effective"]["snapshot"] == newest_effective["snapshot"]
    assert body["effective"]["question_groups"] == newest_effective["snapshot"]["query_groups"]
    assert body["effective"]["pub_id"] != older_effective["pub_id"]
    assert body["next_pending"]["pub_id"] == nearest_future["pub_id"]
    assert body["next_pending"]["pub_id"] != distant_future["pub_id"]

    cross_tenant = client.get(
        f"/api/v2/projects/{project_pub_id}/config/current",
        headers=headers_b,
    )
    assert cross_tenant.status_code == 404
