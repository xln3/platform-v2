import asyncio
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from geo_platform.collection.models import (
    CollectionRun,
    CollectionTask,
    PlatformAccount,
    SessionLease,
)
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import select
from temporalio.client import Client

EVIDENCE = Path(__file__).parents[1] / "tests" / "s01-e2e-runtime.json"


def expect(response: object, status: int) -> dict[str, object]:
    assert hasattr(response, "status_code") and response.status_code == status, response.text
    return response.json()


async def run() -> None:
    client = TestClient(app)
    subject = "s01-e2e-" + secrets.token_hex(5)
    identity = expect(
        client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={"tenant_name": "S01 E2E", "subject": subject, "display_name": "E2E Admin"},
        ),
        201,
    )
    tenant = str(identity["tenant_pub_id"])
    headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "project-" + secrets.token_hex(16),
    }
    project = expect(
        client.post(
            "/api/v2/projects",
            headers=headers,
            json={"name": "S01 E2E Project", "customer_name": "S01 E2E Customer"},
        ),
        201,
    )
    headers["Idempotency-Key"] = "config-" + secrets.token_hex(16)
    frozen = expect(
        client.post(
            f"/api/v2/projects/{project['pub_id']}/config/freeze",
            headers=headers,
            json={
                "query_groups": [
                    {"name": "core", "items": [{"text": "Which GEO platform is reliable?"}]}
                ],
                "regions": ["CN-BJ"],
                "models": ["fixed"],
                "modes": ["fast"],
                "frequency": "manual",
                "effective_at": datetime.now(UTC).isoformat(),
            },
        ),
        201,
    )
    account = expect(
        client.post(
            "/api/v2/platform-accounts",
            headers=headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Auditable Fixed Adapter",
                "account_mask": "fixture-***42",
                "owner_pub_id": "own_e2e",
                "purpose": "measure",
                "responsible_pub_id": str(identity["user_pub_id"]),
                "custody_mode": "server",
                "region": "CN-BJ",
            },
        ),
        201,
    )
    expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
            headers=headers,
            json={
                "scopes": ["read", "query"],
                "forbidden_actions": ["publish", "payment", "security_settings"],
                "regions": ["CN-BJ"],
                "valid_from": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                "valid_until": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            },
        ),
        201,
    )
    profile = expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
            headers=headers,
            json={
                "profile_payload": json.dumps({"fixture": True, "session": "not-a-live-secret"}),
                "custody_mode": "server",
                "constraints": ["READ_ONLY"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        ),
        201,
    )
    headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
    accepted = expect(
        client.post(
            "/api/v2/collection/runs",
            headers=headers,
            json={
                "project_pub_id": project["pub_id"],
                "config_version_pub_id": frozen["pub_id"],
                "requires_intervention": True,
                "account_pub_id": account["pub_id"],
            },
        ),
        202,
    )
    with SessionLocal() as session:
        run_row = session.scalar(
            select(CollectionRun).where(CollectionRun.workflow_id == accepted["workflow_id"])
        )
        assert run_row is not None
        run_pub_id = run_row.pub_id
    intervention = expect(
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/interventions",
            headers=headers,
            json={
                "challenge_type": "otp",
                "allowed_domain": "fixed.example",
                "action": "query",
                "run_pub_id": run_pub_id,
            },
        ),
        201,
    )
    pairing = expect(
        client.post(f"/api/v2/interventions/{intervention['pub_id']}/pair", headers=headers),
        200,
    )
    restart_exercised = os.environ.get("GEO_S01_RESTART_CHECK") == "1"
    if restart_exercised:
        print(
            json.dumps(
                {
                    "phase": "waiting_for_worker_restart",
                    "workflow_id": accepted["workflow_id"],
                    "intervention_pub_id": intervention["pub_id"],
                }
            ),
            flush=True,
        )
        await asyncio.to_thread(input)
    evidence_hash = hashlib.sha256(b"fixed-platform-native-verification").hexdigest()
    expect(
        client.post(
            f"/api/v2/interventions/{intervention['pub_id']}/complete",
            headers=headers,
            json={
                "pairing_token": pairing["pairing_token"],
                "platform_result": "verified",
                "evidence_hash": evidence_hash,
            },
        ),
        200,
    )
    temporal = await Client.connect("127.0.0.1:17233")
    result = await temporal.get_workflow_handle(str(accepted["workflow_id"])).result()
    with SessionLocal() as session:
        run_row = session.scalar(
            select(CollectionRun).where(CollectionRun.workflow_id == accepted["workflow_id"])
        )
        assert run_row is not None
        tasks = session.scalars(
            select(CollectionTask).where(CollectionTask.run_id == run_row.id)
        ).all()
        account_row = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert account_row is not None
        leases = session.scalars(
            select(SessionLease).where(
                SessionLease.account_id == account_row.id,
                SessionLease.released_at.is_not(None),
            )
        ).all()
        persisted = {
            "run_state": run_row.state,
            "completed_tasks": run_row.completed_tasks,
            "task_count": len(tasks),
            "quality_states": [item.quality_state for item in tasks],
            "released_lease_observed": bool(leases),
        }
    output = {
        "verified_at": datetime.now(UTC).isoformat(),
        "workflow_id": accepted["workflow_id"],
        "temporal_run_id": accepted["run_id"],
        "project_pub_id": project["pub_id"],
        "config_version_pub_id": frozen["pub_id"],
        "account_pub_id": account["pub_id"],
        "account_mask": account["account_mask"],
        "admission_level": account["admission_level"],
        "profile_version": profile["profile_version"],
        "intervention_type": intervention["challenge_type"],
        "intervention_platform_result": "verified",
        "workflow_result": result,
        "postgres": persisted,
        "contains_live_claim": False,
        "contains_secret": False,
        "worker_restart_exercised": restart_exercised,
    }
    EVIDENCE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
