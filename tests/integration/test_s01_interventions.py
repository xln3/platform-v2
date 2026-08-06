import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from geo_platform.collection.terminal_protocol import b64url_encode
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
    authorized = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
        headers=request_headers,
        json={
            "scopes": ["query"],
            "forbidden_actions": [],
            "regions": ["CN-BJ"],
            "valid_from": datetime.now(UTC).isoformat(),
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert authorized.status_code == 201
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
    evidence_hash = hashlib.sha256(challenge_type.encode()).hexdigest()
    if challenge_type in {"passkey", "face"}:
        key = Ed25519PrivateKey.generate()
        proof = json.dumps(
            {
                "intervention_pub_id": intervention_id,
                "pairing_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "purpose": "geo-terminal-bind",
                "tenant_pub_id": tenant,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        task_response = client.post(
            f"/api/v2/terminal/interventions/{intervention_id}/bind",
            headers={"X-Tenant-Id": tenant},
            json={
                "pairing_token": token,
                "device_label": "Test device",
                "device_public_key": b64url_encode(key.public_key().public_bytes_raw()),
                "proof_signature": b64url_encode(key.sign(proof)),
            },
        )
        assert task_response.status_code == 201
        task = task_response.json()
        result = json.dumps(
            {
                "evidence_hash": evidence_hash,
                "result": "challenge_completed",
                "task_payload_sha256": hashlib.sha256(
                    json.dumps(task["payload"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "task_pub_id": task["task_pub_id"],
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        completion_url = f"/api/v2/terminal/tasks/{task['task_pub_id']}/complete"
        completion_headers = {"X-Tenant-Id": tenant}
        completion_body = {
            "result": "challenge_completed",
            "evidence_hash": evidence_hash,
            "terminal_signature": b64url_encode(key.sign(result)),
        }
    else:
        completion_url = f"/api/v2/interventions/{intervention_id}/complete"
        completion_headers = request_headers
        completion_body = {
            "pairing_token": token,
            "platform_result": "verified",
            "evidence_hash": evidence_hash,
        }
    completed = client.post(
        completion_url,
        headers=completion_headers,
        json=completion_body,
    )
    assert completed.status_code == 200
    if challenge_type in {"passkey", "face"}:
        completed = client.post(
            f"/api/v2/interventions/{intervention_id}/attest",
            headers=request_headers,
            json={
                "proof_source": "identity_probe",
                "platform_result": "verified",
                "evidence_hash": evidence_hash,
            },
        )
        assert completed.status_code == 200
    serialized = completed.text.lower()
    assert token.lower() not in serialized
    assert "human_verified_token" not in serialized
    assert (
        client.post(completion_url, headers=completion_headers, json=completion_body).status_code
        == 410
    )
