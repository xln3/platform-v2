from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from .models import (
    BrowserProfile,
    DeviceBinding,
    InterventionRequest,
    PlatformAccount,
    RevocationRequest,
    SessionLease,
    TerminalTask,
)


def stage_account_revocation(
    session: Session,
    *,
    account: PlatformAccount,
    reason: str,
    workflow_id: str,
) -> tuple[RevocationRequest, list[int]]:
    """Stage the complete fail-closed revocation boundary in one transaction.

    The caller must durably start the deterministic Temporal workflow before
    committing. Until commit, none of these state changes are externally
    visible, so a Temporal connection/start failure can roll back cleanly.
    """
    now = datetime.now(UTC)
    account.state = "revoked"
    session.execute(
        update(SessionLease)
        .where(SessionLease.account_id == account.id, SessionLease.released_at.is_(None))
        .values(released_at=now)
    )
    session.execute(
        update(DeviceBinding)
        .where(DeviceBinding.account_id == account.id, DeviceBinding.revoked_at.is_(None))
        .values(state="revoked", revoked_at=now)
    )
    intervention_ids = select(InterventionRequest.id).where(
        InterventionRequest.account_id == account.id
    )
    session.execute(
        update(TerminalTask)
        .where(TerminalTask.intervention_id.in_(intervention_ids), TerminalTask.state == "issued")
        .values(state="revoked")
    )
    session.execute(
        update(InterventionRequest)
        .where(
            InterventionRequest.account_id == account.id,
            InterventionRequest.state.in_(["pending", "paired", "task_issued"]),
        )
        .values(state="revoked", pairing_token_hash=None)
    )
    profiles = session.scalars(
        select(BrowserProfile).where(BrowserProfile.account_id == account.id)
    ).all()
    for profile in profiles:
        profile.state = "REVOKED"
        # Remove database authority immediately. The deletion Worker retains
        # the independent Vault authority needed to destroy the account key.
        profile.wrapped_dek = None
        profile.purged_at = profile.purged_at or now
    request = RevocationRequest(
        pub_id=new_pub_id("rev"),
        tenant_id=account.tenant_id,
        account_id=account.id,
        reason=reason,
        workflow_id=workflow_id,
        state="starting",
    )
    session.add(request)
    session.flush()
    return request, [profile.profile_version for profile in profiles]
