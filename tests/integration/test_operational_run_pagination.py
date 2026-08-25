"""Real-Postgres contract tests for the operational run keyset page."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from geo_platform.collection.models import CollectionRun
from geo_platform.main import app
from geo_platform.projects.models import MonitoringConfigVersion, Project
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.models import Tenant
from sqlalchemy import select


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


def _project_and_config(
    client: TestClient, request_headers: dict[str, str], suffix: str
) -> tuple[str, str]:
    project = client.post(
        "/api/v2/projects",
        headers={**request_headers, "Idempotency-Key": f"project-{suffix}-0000000000000000"},
        json={"name": f"Pagination {suffix}", "customer_name": f"Customer {suffix}"},
    )
    assert project.status_code == 201, project.text
    project_pub_id = str(project.json()["pub_id"])
    frozen = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers={**request_headers, "Idempotency-Key": f"freeze-{suffix}-00000000000000000"},
        json={
            "query_groups": [{"name": "Core", "items": [{"text": "What is GEO?"}]}],
            "regions": ["CN-BJ"],
            "models": ["fixed"],
            "modes": ["fast"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201, frozen.text
    return project_pub_id, str(frozen.json()["pub_id"])


def _seed_runs(
    tenant_pub_id: str,
    project_pub_id: str,
    config_pub_id: str,
    *,
    count: int,
    token: str,
) -> list[str]:
    created_at = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    pub_ids = [f"run_{token}_{index:02d}" for index in range(count)]
    with SessionLocal() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
        assert tenant is not None
        project = session.scalar(
            select(Project).where(
                Project.tenant_id == tenant.id,
                Project.pub_id == project_pub_id,
            )
        )
        config = session.scalar(
            select(MonitoringConfigVersion).where(
                MonitoringConfigVersion.tenant_id == tenant.id,
                MonitoringConfigVersion.pub_id == config_pub_id,
            )
        )
        assert project is not None and config is not None
        for index, pub_id in enumerate(pub_ids):
            session.add(
                CollectionRun(
                    pub_id=pub_id,
                    tenant_id=tenant.id,
                    project_id=project.id,
                    config_version_id=config.id,
                    idempotency_key=f"pagination-{token}-{index}",
                    workflow_id=f"pagination/{token}/{index}",
                    state="running",
                    total_tasks=index + 1,
                    completed_tasks=index,
                    failed_tasks=0,
                    paused=False,
                    source="manual",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        session.commit()
    return pub_ids


def _page(
    client: TestClient,
    request_headers: dict[str, str],
    project_pub_id: str,
    cursor: str | None = None,
):
    params = {"project_pub_id": project_pub_id, "limit": 4}
    if cursor is not None:
        params["cursor"] = cursor
    return client.get("/api/v2/collection/runs", headers=request_headers, params=params)


def _numbered_page(
    client: TestClient,
    request_headers: dict[str, str],
    project_pub_id: str,
    page: int,
):
    return client.get(
        "/api/v2/collection/runs",
        headers=request_headers,
        params={"project_pub_id": project_pub_id, "page": page, "limit": 4},
    )


def test_run_cursor_is_stable_scoped_opaque_and_summary_is_full_cohort() -> None:
    client = TestClient(app)
    token = secrets.token_hex(5)
    tenant_a, headers_a = _bootstrap(client, f"run-page-a-{token}")
    tenant_b, headers_b = _bootstrap(client, f"run-page-b-{token}")
    project_a, config_a = _project_and_config(client, headers_a, f"a{token}")
    project_other, config_other = _project_and_config(client, headers_a, f"b{token}")
    project_exact, config_exact = _project_and_config(client, headers_a, f"e{token}")
    project_b, config_b = _project_and_config(client, headers_b, f"t{token}")

    expected_a = sorted(
        _seed_runs(tenant_a, project_a, config_a, count=9, token=f"a{token}"), reverse=True
    )
    _seed_runs(tenant_a, project_other, config_other, count=5, token=f"o{token}")
    expected_exact = sorted(
        _seed_runs(tenant_a, project_exact, config_exact, count=4, token=f"e{token}"), reverse=True
    )
    _seed_runs(tenant_b, project_b, config_b, count=1, token=f"t{token}")

    first = _page(client, headers_a, project_a)
    assert first.status_code == 200, first.text
    assert [row["pub_id"] for row in first.json()] == expected_a[:4]
    assert all("created_at" in row for row in first.json())
    assert first.headers["X-Has-More"] == "true"
    first_cursor = first.headers["X-Next-Cursor"]
    assert project_a not in first_cursor and tenant_a not in first_cursor

    second = _page(client, headers_a, project_a, first_cursor)
    assert second.status_code == 200, second.text
    assert [row["pub_id"] for row in second.json()] == expected_a[4:8]
    assert second.headers["X-Has-More"] == "true"
    third = _page(client, headers_a, project_a, second.headers["X-Next-Cursor"])
    assert [row["pub_id"] for row in third.json()] == expected_a[8:]
    assert third.headers["X-Has-More"] == "false"
    assert "X-Next-Cursor" not in third.headers

    numbered_first = _numbered_page(client, headers_a, project_a, 1)
    assert numbered_first.status_code == 200, numbered_first.text
    assert [row["pub_id"] for row in numbered_first.json()] == expected_a[:4]
    assert numbered_first.headers["X-Page"] == "1"
    assert numbered_first.headers["X-Page-Size"] == "4"
    assert numbered_first.headers["X-Total-Count"] == "9"
    assert numbered_first.headers["X-Page-Count"] == "3"
    assert numbered_first.headers["X-Has-More"] == "true"

    numbered_third = _numbered_page(client, headers_a, project_a, 3)
    assert [row["pub_id"] for row in numbered_third.json()] == expected_a[8:]
    assert numbered_third.headers["X-Page"] == "3"
    assert numbered_third.headers["X-Has-More"] == "false"

    clamped = _numbered_page(client, headers_a, project_a, 999)
    assert [row["pub_id"] for row in clamped.json()] == expected_a[8:]
    assert clamped.headers["X-Page"] == "3"

    conflict = client.get(
        "/api/v2/collection/runs",
        headers=headers_a,
        params={
            "project_pub_id": project_a,
            "page": 1,
            "limit": 4,
            "cursor": first_cursor,
        },
    )
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "pagination_mode_conflict"

    exact = _page(client, headers_a, project_exact)
    assert [row["pub_id"] for row in exact.json()] == expected_exact
    assert exact.headers["X-Has-More"] == "false"
    assert "X-Next-Cursor" not in exact.headers

    summary = client.get(
        "/api/v2/collection/runs/summary",
        headers=headers_a,
        params={"project_pub_id": project_a},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "project_pub_id": project_a,
        "run_count": 9,
        "active_run_count": 9,
        "total_tasks": 45,
        "completed_tasks": 36,
        "failed_tasks": 0,
    }

    assert _page(client, headers_a, project_other, first_cursor).status_code == 422
    assert _page(client, headers_b, project_b, first_cursor).status_code == 422
    tampered = first_cursor[:-1] + ("A" if first_cursor[-1] != "A" else "B")
    assert _page(client, headers_a, project_a, tampered).status_code == 422

    tenant_b_page = _page(client, headers_b, project_b)
    assert len(tenant_b_page.json()) == 1
    assert all(row["pub_id"] not in expected_a for row in tenant_b_page.json())
