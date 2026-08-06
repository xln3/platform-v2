from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from .models import (
    AccountAuthorization,
    CapabilityLease,
    InterventionRequest,
    PlatformAccount,
    SessionLease,
    TerminalTask,
)


def replace_account_authorization(
    session: Session,
    *,
    account: PlatformAccount,
    scopes: set[str],
    forbidden_actions: set[str],
    regions: set[str],
    valid_from: datetime,
    valid_until: datetime,
    pub_id_prefix: str,
) -> tuple[AccountAuthorization, dict[str, int]]:
    """Atomically replace authority and revoke capabilities outside the new grant."""
    locked_account = session.scalar(
        select(PlatformAccount).where(PlatformAccount.id == account.id).with_for_update()
    )
    if locked_account is None:
        raise RuntimeError("platform_account_disappeared")
    if locked_account.state == "revoked":
        raise ValueError("account_revoked")

    now = datetime.now(UTC)
    prior_authorizations = session.scalars(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
        )
        .with_for_update()
    ).all()
    for prior in prior_authorizations:
        prior.revoked_at = now

    revoked_capabilities = 0
    capability_leases = session.scalars(
        select(CapabilityLease)
        .where(
            CapabilityLease.account_id == account.id,
            CapabilityLease.revoked_at.is_(None),
        )
        .with_for_update()
    ).all()
    for capability_lease in capability_leases:
        lease_scopes = set(json.loads(capability_lease.authorization_scope_json))
        lease_actions = set(json.loads(capability_lease.allowed_actions_json))
        if not lease_scopes.issubset(scopes) or lease_actions.intersection(forbidden_actions):
            capability_lease.revoked_at = now
            revoked_capabilities += 1

    released_sessions = 0
    session_leases = session.scalars(
        select(SessionLease)
        .where(SessionLease.account_id == account.id, SessionLease.released_at.is_(None))
        .with_for_update()
    ).all()
    for session_lease in session_leases:
        if session_lease.capability not in scopes or session_lease.capability in forbidden_actions:
            session_lease.released_at = now
            released_sessions += 1

    revoked_interventions = 0
    interventions = session.scalars(
        select(InterventionRequest)
        .where(
            InterventionRequest.account_id == account.id,
            InterventionRequest.state.in_(["pending", "paired", "task_issued"]),
        )
        .with_for_update()
    ).all()
    revoked_intervention_ids = []
    for intervention in interventions:
        if intervention.action not in scopes or intervention.action in forbidden_actions:
            intervention.state = "revoked"
            intervention.pairing_token_hash = None
            revoked_intervention_ids.append(intervention.id)
            revoked_interventions += 1
    revoked_terminal_tasks = 0
    if revoked_intervention_ids:
        terminal_tasks = session.scalars(
            select(TerminalTask)
            .where(
                TerminalTask.intervention_id.in_(revoked_intervention_ids),
                TerminalTask.state == "issued",
            )
            .with_for_update()
        ).all()
        for task in terminal_tasks:
            task.state = "revoked"
            revoked_terminal_tasks += 1

    authorization = AccountAuthorization(
        pub_id=new_pub_id(pub_id_prefix),
        tenant_id=account.tenant_id,
        account_id=account.id,
        scopes_json=json.dumps(sorted(scopes)),
        forbidden_actions_json=json.dumps(sorted(forbidden_actions)),
        regions_json=json.dumps(sorted(regions)),
        valid_from=valid_from,
        valid_until=valid_until,
    )
    session.add(authorization)
    return authorization, {
        "prior_authorizations_revoked": len(prior_authorizations),
        "capability_leases_revoked": revoked_capabilities,
        "session_leases_released": released_sessions,
        "interventions_revoked": revoked_interventions,
        "terminal_tasks_revoked": revoked_terminal_tasks,
    }
