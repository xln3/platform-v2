from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from domain.evidence.capability import CaptureCapabilityLease


@dataclass(frozen=True, slots=True)
class SessionGatewayClient:
    """Narrow S01 boundary: validates a lease, never returns a profile or credential."""

    endpoint: str
    service_token: str | None = None

    def validate_capture_lease(
        self,
        *,
        lease_pub_id: str,
        tenant_pub_id: str,
        platform_account_pub_id: str,
        target_url: str,
        action: str,
        workflow_id: str,
        now: datetime,
        required_scopes: tuple[str, ...] = ("read",),
    ) -> CaptureCapabilityLease:
        headers = {"X-Service-Token": self.service_token} if self.service_token is not None else {}
        response = httpx.post(
            f"{self.endpoint.rstrip('/')}/api/v2/collection/capability-leases/"
            f"{lease_pub_id}/validate",
            json={
                "tenant_pub_id": tenant_pub_id,
                "platform_account_pub_id": platform_account_pub_id,
                "target_url": target_url,
                "action": action,
                "workflow_id": workflow_id,
                "required_scopes": list(required_scopes),
            },
            headers=headers,
            timeout=10,
            trust_env=False,
        )
        if response.status_code != 200:
            raise PermissionError(
                f"S01 session gateway rejected capability lease ({response.status_code})"
            )
        body: dict[str, Any] = response.json()
        forbidden = {
            "cookie",
            "authorization",
            "token",
            "profile_path",
            "profile_object_key",
            "device_key",
            "proxy_password",
            "otp",
        }
        if forbidden & {key.lower() for key in body}:
            raise ValueError("S01 gateway response contains a forbidden secret-bearing field")
        lease = CaptureCapabilityLease(
            lease_pub_id=body["lease_pub_id"],
            tenant_pub_id=body["tenant_pub_id"],
            platform_account_pub_id=body["platform_account_pub_id"],
            allowed_domains=tuple(body["allowed_domains"]),
            allowed_actions=tuple(body["allowed_actions"]),
            authorization_scope=tuple(body["authorization_scope"]),
            expires_at=datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")),
            revoked_at=(
                datetime.fromisoformat(body["revoked_at"].replace("Z", "+00:00"))
                if body.get("revoked_at")
                else None
            ),
            subject_workflow_id=body["subject_workflow_id"],
            signature_verified=body.get("issuer") == "s01-session-gateway",
        )
        lease.authorize(
            tenant_pub_id=tenant_pub_id,
            platform_account_pub_id=platform_account_pub_id,
            target_url=target_url,
            action=action,
            workflow_id=workflow_id,
            now=now,
        )
        if not set(required_scopes).issubset(lease.authorization_scope):
            raise PermissionError("required scope is outside capability lease")
        return lease
