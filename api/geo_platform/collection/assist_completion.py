"""One application service for mobile-page and Feishu assist completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..notifications.service import NotificationService
from ..tenancy.models import Tenant
from ..tenancy.repository import set_tenant_context
from .assist_registry import session_kind
from .models import CollectionRun
from .workflow_outbox import (
    WorkflowSignalConflictError,
    enqueue_workflow_signal,
    workflow_signal_replayed,
)


class AssistCompletionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistCompletion:
    session_kind: str
    ticket_sha256: str


def prepare_assist_completion(
    session: Session,
    *,
    registry: dict[str, Any],
    ticket_sha256: str,
    actor_hash: str = "system",
    actor_mask: str | None = None,
) -> AssistCompletion:
    """Persist the appropriate completion effect without committing.

    Workflow captcha sessions enqueue the existing idempotent Temporal signal.
    OTP CLI sessions intentionally do not query CollectionRun or emit a signal;
    the authenticated completion acknowledgement is the CLI's registry signal.
    The caller commits once, then marks the registry file solved.
    """

    kind = session_kind(registry)
    session_id = registry.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise AssistCompletionError("assist_session_invalid")

    if kind == "workflow_captcha":
        run_pub_id = registry.get("run_pub_id")
        if not isinstance(run_pub_id, str) or not run_pub_id:
            raise AssistCompletionError("assist_run_invalid")
        tenant_pub_id = registry.get("tenant_pub_id")
        if isinstance(tenant_pub_id, str) and tenant_pub_id:
            tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
            if tenant is None:
                raise AssistCompletionError("assist_run_not_found")
            set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
            run = session.scalar(
                select(CollectionRun).where(
                    CollectionRun.pub_id == run_pub_id,
                    CollectionRun.tenant_id == tenant.id,
                )
            )
        else:
            # Compatibility for pre-session_kind registry files. Production
            # activation requires no live legacy assists before using a
            # tenant-restricted bot database role.
            run = session.scalar(select(CollectionRun).where(CollectionRun.pub_id == run_pub_id))
            tenant = session.get(Tenant, run.tenant_id) if run is not None else None
            if tenant is not None:
                set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
        if run is None or tenant is None:
            raise AssistCompletionError("assist_run_not_found")
        if not workflow_signal_replayed(
            session,
            tenant_pub_id=tenant.pub_id,
            workflow_id=run.workflow_id,
            signal_name="captcha_solved",
            args=[session_id],
            idempotency_key=f"captcha-solved:{session_id}",
        ):
            enqueue_workflow_signal(
                session,
                tenant_pub_id=tenant.pub_id,
                workflow_id=run.workflow_id,
                signal_name="captcha_solved",
                args=[session_id],
                idempotency_key=f"captcha-solved:{session_id}",
            )

    NotificationService(session).mark_assist_state_by_ticket(
        ticket_sha256=ticket_sha256,
        state="solved",
        actor_hash=actor_hash,
        actor_mask=actor_mask,
    )
    return AssistCompletion(session_kind=kind, ticket_sha256=ticket_sha256)


__all__ = [
    "AssistCompletion",
    "AssistCompletionError",
    "WorkflowSignalConflictError",
    "prepare_assist_completion",
]
