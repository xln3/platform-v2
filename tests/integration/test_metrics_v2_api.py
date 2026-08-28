from __future__ import annotations

import importlib
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.metrics_v2.repository import MetricsV2Repository
from geo_platform.metrics_v2.service import MetricsV2Service

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_snapshot_request_api_is_private_idempotent_and_hides_missing_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    router_module = importlib.import_module("geo_platform.metrics_v2.router")
    service = MetricsV2Service(repository=MetricsV2Repository(POSTGRES_DSN))
    monkeypatch.setattr(router_module, "_service", lambda: service)

    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=f"subject-{token}",
        role=Role.CUSTOMER,
        tenant_pub_id=tenant,
        user_pub_id=f"usr_{token}",
    )
    client = TestClient(app)
    payload = {
        "window": {"start": "2026-08-01", "end": "2026-08-02"},
        "filters": {"model": [], "region": [], "mode": []},
        "focal_entity_ids": [f"entity-{token}"],
        "aggregation_method": "query_macro",
        "publication_channel": "shadow",
        "idempotency_key": f"api-snapshot-request-{token}",
    }

    first = client.post(f"/api/v2/metrics/projects/{project}/snapshot-requests", json=payload)
    replay = client.post(f"/api/v2/metrics/projects/{project}/snapshot-requests", json=payload)

    assert first.status_code == 202
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.json()["status"] == "pending"
    assert replay.status_code == 202
    assert replay.json()["job_pub_id"] == first.json()["job_pub_id"]
    assert replay.json()["reused"] is True

    missing = client.get(f"/api/v2/metrics/snapshot-sets/mss_missing_{token}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "metrics_v2_resource_not_found"}}
