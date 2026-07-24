"""Verify the mounted S01+S02 API against a running local V2 process."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def verify(base_url: str) -> dict[str, object]:
    suffix = uuid4().hex
    subject = f"s04-admin-{suffix}"
    with httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client:
        schema = client.get("/openapi.json")
        schema.raise_for_status()
        paths = schema.json()["paths"]

        bootstrap = client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={
                "tenant_name": f"S04 runtime {suffix}",
                "subject": subject,
                "display_name": "S04 runtime admin",
            },
        )
        bootstrap.raise_for_status()
        tenant_pub_id = bootstrap.json()["tenant_pub_id"]
        admin_headers = {
            "X-Tenant-Id": tenant_pub_id,
            "X-Actor-Id": subject,
            "X-Actor-Role": "admin",
        }
        project = client.post(
            "/api/v2/projects",
            headers={**admin_headers, "Idempotency-Key": f"s04-project-{suffix}"},
            json={"name": "S04 integrated project", "customer_name": "S04 customer"},
        )
        project.raise_for_status()
        project_pub_id = project.json()["pub_id"]

        today = date.today().isoformat()
        analytics = client.get(
            "/api/v2/analytics/overview",
            headers=admin_headers,
            params={"project_pub_id": project_pub_id, "start": today, "end": today},
        )
        analytics.raise_for_status()
        reports = client.get("/api/v2/reports", headers=admin_headers)
        reports.raise_for_status()
        evidence = client.get("/api/v2/evidence/assets", headers=admin_headers)
        evidence.raise_for_status()

        forbidden = client.get(
            "/api/v2/platform-accounts",
            headers={**admin_headers, "X-Actor-Role": "customer"},
        )
        validation = client.get(
            "/api/v2/analytics/overview",
            params={"project_pub_id": "must-not-be-reflected"},
            headers={"X-Request-Id": f"req-{suffix}"},
        )
        validation_body = validation.json()

    required_paths = {
        "/api/v2/analytics/overview",
        "/api/v2/evidence/assets",
        "/api/v2/exports/metrics",
        "/api/v2/reports",
        "/api/v2/intelligence/search",
        "/api/v2/customer/platform-accounts",
    }
    assert required_paths <= paths.keys()
    assert analytics.json() == []
    assert reports.json()["data"] == []
    assert evidence.json()["data"] == []
    assert forbidden.status_code == 401
    assert forbidden.json()["error"]["code"] == "membership_invalid"
    assert validation.status_code == 422
    assert validation_body["error"]["code"] == "validation_error"
    assert "must-not-be-reflected" not in validation.text
    return {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "openapi_path_count": len(paths),
        "required_paths_present": sorted(required_paths),
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project_pub_id,
        "analytics_empty_state": True,
        "reports_empty_state": True,
        "evidence_empty_state": True,
        "role_spoof_rejected": True,
        "stable_error_envelope": True,
        "request_value_reflected": False,
        "contains_secret": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:45201")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
