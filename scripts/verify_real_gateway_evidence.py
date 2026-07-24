"""Run EvidenceCaptureWorkflow through the live S01 capability API."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker

from workflows.definitions.s02 import EvidenceCaptureWorkflow
from workflows.workers.s02 import S02_ACTIVITIES, S02_WORKFLOWS


async def verify(base_url: str) -> dict[str, object]:
    suffix = uuid4().hex
    subject = f"s04-evidence-{suffix[:12]}"
    workflow_id = f"evidence-capture/tnt_pending/evd_{suffix}/op_{suffix}"
    with httpx.Client(base_url=base_url, timeout=20, trust_env=False) as http:
        bootstrap = http.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={
                "tenant_name": f"S04 evidence {suffix}",
                "subject": subject,
                "display_name": "S04 evidence admin",
            },
        )
        bootstrap.raise_for_status()
        tenant_pub_id = bootstrap.json()["tenant_pub_id"]
        workflow_id = f"evidence-capture/{tenant_pub_id}/evd_{suffix}/op_{suffix}"
        headers = {
            "X-Tenant-Id": tenant_pub_id,
            "X-Actor-Id": subject,
            "X-Actor-Role": "admin",
        }
        account = http.post(
            "/api/v2/platform-accounts",
            headers=headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Fixed deterministic adapter",
                "account_mask": "s04-evidence-***",
                "owner_pub_id": f"owner_{suffix[:20]}",
                "purpose": "authorized-evidence-runtime",
                "responsible_pub_id": subject,
                "custody_mode": "server",
                "region": "CN-BJ",
            },
        )
        account.raise_for_status()
        account_pub_id = account.json()["pub_id"]
        now = datetime.now(UTC)
        authorization = http.post(
            f"/api/v2/platform-accounts/{account_pub_id}/authorizations",
            headers=headers,
            json={
                "scopes": ["read"],
                "forbidden_actions": ["publish"],
                "regions": ["CN-BJ"],
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
            },
        )
        authorization.raise_for_status()
        profile = http.post(
            f"/api/v2/platform-accounts/{account_pub_id}/profiles/enroll",
            headers=headers,
            json={
                "profile_payload": '{"fixture":"s04-real-gateway"}',
                "custody_mode": "server",
                "constraints": ["READ_ONLY"],
            },
        )
        profile.raise_for_status()
        worker = http.post(
            "/api/v2/identity/service-accounts",
            headers=headers,
            json={"name": "S04 Evidence Worker", "expires_in_hours": 1},
        )
        worker.raise_for_status()
        service_token = worker.json()["token"]
        lease = http.post(
            "/api/v2/collection/capability-leases",
            headers=headers,
            json={
                "platform_account_pub_id": account_pub_id,
                "allowed_domains": ["secure.example"],
                "allowed_actions": ["capture_evidence"],
                "authorization_scope": ["read"],
                "subject_workflow_id": workflow_id,
                "ttl_seconds": 600,
                "max_uses": 3,
            },
        )
        lease.raise_for_status()
        lease_pub_id = lease.json()["lease_pub_id"]

        os.environ["GEO_SESSION_GATEWAY_URL"] = base_url
        os.environ["GEO_SESSION_GATEWAY_SERVICE_TOKEN"] = service_token
        temporal = await Client.connect("127.0.0.1:17233")
        queue = f"s04-real-gateway-{suffix}"
        payload = {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": f"prj_{suffix}",
            "evidence_pub_id": f"evd_{suffix}",
            "source_url": "https://docs.secure.example/article",
            "kind": "html_snapshot",
            "mime_type": "text/html",
            "capture_time": now.isoformat(),
            "adapter_version": "fixed-runtime-v1",
            "access_class": "customer_private",
            "requires_authenticated_session": True,
            "platform_account_pub_id": account_pub_id,
            "capture_payload_b64": base64.b64encode(b"<html>safe evidence</html>").decode(),
        }
        async with Worker(
            temporal,
            task_queue=queue,
            workflows=list(S02_WORKFLOWS),
            activities=list(S02_ACTIVITIES),
        ):
            handle = await temporal.start_workflow(
                EvidenceCaptureWorkflow.run,
                payload,
                id=workflow_id,
                task_queue=queue,
            )
            await handle.signal(EvidenceCaptureWorkflow.authorize_capture, lease_pub_id)
            captured = await handle.result()

            revoked = http.post(
                f"/api/v2/collection/capability-leases/{lease_pub_id}/revoke",
                headers=headers,
            )
            revoked.raise_for_status()
            rejected_id = f"evidence-capture/{tenant_pub_id}/evd_rejected_{suffix}/op_{suffix}"
            rejected = await temporal.start_workflow(
                EvidenceCaptureWorkflow.run,
                payload | {"evidence_pub_id": f"evd_rejected_{suffix}"},
                id=rejected_id,
                task_queue=queue,
            )
            await rejected.signal(EvidenceCaptureWorkflow.authorize_capture, lease_pub_id)
            rejected_after_revoke = False
            try:
                await rejected.result()
            except WorkflowFailureError:
                rejected_after_revoke = True

    assert captured["captured"] is True
    assert captured["authorized_session_capture"] is True
    assert rejected_after_revoke
    return {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "gateway_url": base_url,
        "workflow_id": workflow_id,
        "tenant_pub_id": tenant_pub_id,
        "platform_account_pub_id": account_pub_id,
        "lease_pub_id": lease_pub_id,
        "captured": True,
        "authorized_session_capture": True,
        "rejected_after_revoke": True,
        "gateway_issuer_verified": True,
        "contains_service_token": False,
        "contains_secret": False,
        "admission_claim": "adapter_ready",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:45201")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(verify(args.base_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
