import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from geo_platform.main import app


def bootstrap(client: TestClient, subject: str) -> str:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    return str(response.json()["tenant_pub_id"])


def headers(tenant: str, subject: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


@pytest.mark.parametrize("challenge_type", ["otp", "qr", "push", "passkey", "face", "graphical"])
def test_each_intervention_type_pairs_once_and_records_only_platform_result(
    challenge_type: str,
) -> None:
    client = TestClient(app)
    subject = f"intervention-{challenge_type}-" + secrets.token_hex(4)
    tenant = bootstrap(client, subject)
    request_headers = headers(tenant, subject)
    account = client.post(
        "/api/v2/platform-accounts",
        headers=request_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": f"fixture-{challenge_type}-***",
            "owner_pub_id": "own_test",
            "purpose": "measure",
            "responsible_pub_id": "usr_test",
            "custody_mode": (
                "customer_device" if challenge_type in {"passkey", "face"} else "server"
            ),
            "region": "CN-BJ",
        },
    ).json()
    intervention = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/interventions",
        headers=request_headers,
        json={
            "challenge_type": challenge_type,
            "allowed_domain": "platform.example",
            "action": "query",
        },
    )
    assert intervention.status_code == 201
    intervention_id = intervention.json()["pub_id"]
    paired = client.post(f"/api/v2/interventions/{intervention_id}/pair", headers=request_headers)
    assert paired.status_code == 200
    token = paired.json()["pairing_token"]
    completed = client.post(
        f"/api/v2/interventions/{intervention_id}/complete",
        headers=request_headers,
        json={
            "pairing_token": token,
            "platform_result": "verified",
            "evidence_hash": hashlib.sha256(challenge_type.encode()).hexdigest(),
        },
    )
    assert completed.status_code == 200
    serialized = completed.text.lower()
    assert token.lower() not in serialized
    assert "human_verified_token" not in serialized
    assert (
        client.post(
            f"/api/v2/interventions/{intervention_id}/complete",
            headers=request_headers,
            json={
                "pairing_token": token,
                "platform_result": "verified",
                "evidence_hash": hashlib.sha256(challenge_type.encode()).hexdigest(),
            },
        ).status_code
        == 410
    )
