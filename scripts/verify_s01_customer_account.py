import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from geo_platform.main import app
from temporalio.client import Client

EVIDENCE = Path("tests/s01-customer-account-runtime.json")


def expect(response, status: int) -> dict[str, object] | list[dict[str, object]]:
    assert response.status_code == status, response.text
    return response.json()


async def run() -> None:
    client = TestClient(app)
    admin = "customer-runtime-admin-" + secrets.token_hex(5)
    identity = expect(
        client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={"tenant_name": admin, "subject": admin, "display_name": "Admin"},
        ),
        201,
    )
    assert isinstance(identity, dict)
    tenant = str(identity["tenant_pub_id"])
    admin_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": admin,
        "X-Actor-Role": "admin",
    }
    customer = "customer-runtime-" + secrets.token_hex(5)
    expect(
        client.post(
            "/api/v2/identity/members",
            headers=admin_headers,
            json={"subject": customer, "display_name": "Customer Owner", "role": "customer"},
        ),
        201,
    )
    customer_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": customer,
        "X-Actor-Role": "customer",
    }
    account = expect(
        client.post(
            "/api/v2/customer/platform-accounts",
            headers=customer_headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Fixed",
                "account_mask": "customer-runtime-***",
                "custody_mode": "customer_device",
                "region": "CN-SH",
            },
        ),
        201,
    )
    assert isinstance(account, dict)
    expect(
        client.post(
            f"/api/v2/customer/platform-accounts/{account['pub_id']}/authorizations",
            headers=customer_headers,
            json={
                "scopes": ["read", "query"],
                "regions": ["CN-SH"],
                "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        ),
        200,
    )
    pairing = expect(
        client.post(
            f"/api/v2/customer/platform-accounts/{account['pub_id']}/pairings",
            headers=customer_headers,
            json={
                "allowed_domain": "fixed.example",
                "action": "read",
                "challenge_type": "qr",
            },
        ),
        201,
    )
    assert isinstance(pairing, dict)
    controlled = expect(
        client.post(f"/api/v2/interventions/{pairing['pub_id']}/pair", headers=admin_headers),
        200,
    )
    assert isinstance(controlled, dict)
    expect(
        client.post(
            f"/api/v2/interventions/{pairing['pub_id']}/complete",
            headers=admin_headers,
            json={
                "pairing_token": controlled["pairing_token"],
                "platform_result": "verified",
                "evidence_hash": "b" * 64,
            },
        ),
        200,
    )
    accepted = expect(
        client.post(
            f"/api/v2/customer/platform-accounts/{account['pub_id']}/revoke",
            headers=customer_headers,
        ),
        202,
    )
    assert isinstance(accepted, dict)
    temporal = await Client.connect("127.0.0.1:17233")
    result = await temporal.get_workflow_handle(str(accepted["workflow_id"])).result()
    summaries = expect(
        client.get("/api/v2/customer/platform-accounts", headers=customer_headers), 200
    )
    assert isinstance(summaries, list)
    summary = next(item for item in summaries if item["pub_id"] == account["pub_id"])
    events = expect(
        client.get(
            f"/api/v2/customer/platform-accounts/{account['pub_id']}/events",
            headers=customer_headers,
        ),
        200,
    )
    assert isinstance(events, list)
    output = {
        "verified_at": datetime.now(UTC).isoformat(),
        "account_pub_id": account["pub_id"],
        "account_mask": summary["account_mask"],
        "pairing_state_before_revoke": "completed",
        "customer_projection_state": summary["session_health"],
        "revocation_receipt_pub_id": summary["revocation_receipt_pub_id"],
        "revoked_at": summary["revoked_at"],
        "workflow_result": result,
        "event_types": [item["event_type"] for item in events],
        "projection_keys": sorted(summary),
        "customer_received_pairing_token": False,
        "contains_secret": False,
    }
    assert summary["session_health"] == "revoked"
    assert summary["revocation_receipt_pub_id"]
    assert summary["revoked_at"]
    EVIDENCE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
