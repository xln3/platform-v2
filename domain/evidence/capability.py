from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class CaptureCapabilityLease:
    lease_pub_id: str
    tenant_pub_id: str
    platform_account_pub_id: str
    allowed_domains: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    authorization_scope: tuple[str, ...]
    expires_at: datetime
    revoked_at: datetime | None
    subject_workflow_id: str
    signature_verified: bool

    def authorize(
        self,
        *,
        tenant_pub_id: str,
        platform_account_pub_id: str,
        target_url: str,
        action: str,
        workflow_id: str,
        now: datetime,
    ) -> None:
        if not self.signature_verified:
            raise PermissionError("capability lease signature is invalid")
        if self.revoked_at is not None:
            raise PermissionError("capability lease is revoked")
        if now >= self.expires_at:
            raise PermissionError("capability lease is expired")
        if tenant_pub_id != self.tenant_pub_id:
            raise PermissionError("capability lease belongs to another tenant")
        if platform_account_pub_id != self.platform_account_pub_id:
            raise PermissionError("capability lease belongs to another account")
        if workflow_id != self.subject_workflow_id:
            raise PermissionError("capability lease is bound to another workflow")
        if action not in self.allowed_actions:
            raise PermissionError("action is outside capability lease")
        hostname = (urlsplit(target_url).hostname or "").lower()
        if not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_domains
        ):
            raise PermissionError("domain is outside capability lease")
