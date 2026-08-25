# ruff: noqa: B008

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..identity.policy import Principal, get_principal
from ..projects.models import Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from .customer_account_router import CustomerAccountView
from .models import (
    AccountAuthorization,
    CollectionRun,
    InterventionRequest,
    PlatformAccount,
    PlatformAdapter,
    RevocationRequest,
    SessionEvent,
    SessionHealthCheck,
    SessionLease,
)
from .operations_constants import RUN_DELAY_THRESHOLD, TERMINAL_RUN_STATES

router = APIRouter(prefix="/api/v2/operations", tags=["operations"])

PENDING_INTERVENTION_STATES = frozenset(
    {"pending", "paired", "task_issued", "awaiting_platform_probe"}
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationsLifecycleMetricsView(StrictModel):
    running_runs: int
    project_count: int
    pending_interventions: int
    healthy_sessions: int
    total_sessions: int
    delayed_runs: int
    p95_delay_seconds: int | None


class OperationsLifecycleActivityView(StrictModel):
    pub_id: str
    occurred_at: datetime
    event_type: str
    object_mask: str
    result: str
    tone: Literal["positive", "warning", "danger", "neutral"]


class OperationsLifecycleInterventionView(StrictModel):
    pub_id: str
    account_pub_id: str
    account_mask: str
    challenge_type: Literal["otp", "qr", "push", "passkey", "face", "graphical"]
    state: str
    lease_expires_at: datetime | None
    pairing_expires_at: datetime | None


class OperationsLifecycleEventView(StrictModel):
    pub_id: str
    account_pub_id: str
    account_mask: str
    event_type: str
    occurred_at: datetime


class OperationsLifecycleProjectionView(StrictModel):
    total: int
    shown: int
    truncated: bool


class OperationsLifecycleView(StrictModel):
    metrics: OperationsLifecycleMetricsView
    activity: list[OperationsLifecycleActivityView]
    accounts: list[CustomerAccountView]
    interventions: list[OperationsLifecycleInterventionView]
    events: list[OperationsLifecycleEventView]
    projection: dict[str, OperationsLifecycleProjectionView]


class PlatformSlaPolicyUpdate(StrictModel):
    owner_pub_id: str = Field(min_length=5, max_length=30)
    session_ttl_minutes: int = Field(ge=15, le=525_600)
    intervention_sla_minutes: int = Field(ge=1, le=10_080)
    success_target_bps: int = Field(ge=0, le=10_000)


class PlatformSlaView(StrictModel):
    platform: str
    display_name: str
    owner_pub_id: str
    session_ttl_minutes: int
    intervention_sla_minutes: int
    success_target_bps: int
    total_tasks_30d: int
    completed_tasks_30d: int
    failed_tasks_30d: int
    interventions_30d: int
    overdue_interventions: int
    success_rate: float | None
    manual_takeover_rate: float | None
    active_accounts: int
    session_expires_at: datetime | None
    state: Literal["healthy", "warning", "breached", "unmeasured"]


AccountScopedRow = (
    AccountAuthorization
    | InterventionRequest
    | RevocationRequest
    | SessionHealthCheck
    | SessionLease
)


def _latest_by_account(rows: Sequence[AccountScopedRow]) -> dict[object, AccountScopedRow]:
    latest: dict[object, AccountScopedRow] = {}
    for row in rows:
        account_id = row.account_id
        if account_id not in latest:
            latest[account_id] = row
    return latest


def _tone(value: str) -> Literal["positive", "warning", "danger", "neutral"]:
    if value in {"completed", "passed", "verified", "healthy"}:
        return "positive"
    if value in {"failed", "cancelled", "quarantined", "revoked"}:
        return "danger"
    if value in {
        "pending",
        "running",
        "paused",
        "paired",
        "challenge_required",
        "partial",
    }:
        return "warning"
    return "neutral"


def _session_health(
    account: PlatformAccount, health: SessionHealthCheck | None
) -> Literal["healthy", "degraded", "challenge_required", "revoked"]:
    if account.state == "revoked":
        return "revoked"
    if account.state == "challenge_required":
        return "challenge_required"
    return "healthy" if health is not None and health.result == "passed" else "degraded"


@router.get("/lifecycle", response_model=OperationsLifecycleView)
def get_operations_lifecycle(
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> OperationsLifecycleView:
    """Return one bounded, secret-free tenant lifecycle snapshot for Operations Web."""

    principal.require("project:read")
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    tenant_id = repository.tenant.id
    now = datetime.now(UTC)

    project_count = int(
        session.scalar(
            select(func.count()).select_from(Project).where(Project.tenant_id == tenant_id)
        )
        or 0
    )
    run_total = int(
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(CollectionRun.tenant_id == tenant_id)
        )
        or 0
    )
    runs = list(
        session.scalars(
            select(CollectionRun)
            .where(CollectionRun.tenant_id == tenant_id)
            .order_by(CollectionRun.updated_at.desc(), CollectionRun.pub_id.desc())
            .limit(limit)
        ).all()
    )
    active_run_filter = (
        CollectionRun.tenant_id == tenant_id,
        CollectionRun.state.not_in(TERMINAL_RUN_STATES),
    )
    running_run_count = int(
        session.scalar(select(func.count()).select_from(CollectionRun).where(*active_run_filter))
        or 0
    )
    delayed_run_count = int(
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(
                *active_run_filter,
                CollectionRun.updated_at <= now - RUN_DELAY_THRESHOLD,
            )
        )
        or 0
    )
    raw_p95_delay = session.scalar(
        select(
            func.percentile_cont(0.95).within_group(
                func.extract("epoch", func.now() - CollectionRun.updated_at)
            )
        ).where(*active_run_filter)
    )
    p95_delay_seconds = (
        max(0, int(math.ceil(float(raw_p95_delay)))) if raw_p95_delay is not None else None
    )

    account_total = int(
        session.scalar(
            select(func.count())
            .select_from(PlatformAccount)
            .where(PlatformAccount.tenant_id == tenant_id)
        )
        or 0
    )
    account_rows = list(
        session.execute(
            select(PlatformAccount, PlatformAdapter)
            .join(PlatformAdapter, PlatformAdapter.id == PlatformAccount.adapter_id)
            .where(PlatformAccount.tenant_id == tenant_id)
            .order_by(PlatformAccount.created_at.desc(), PlatformAccount.pub_id.desc())
            .limit(limit)
        ).all()
    )
    account_ids = [account.id for account, _adapter in account_rows]
    authorizations = (
        list(
            session.scalars(
                select(AccountAuthorization)
                .where(
                    AccountAuthorization.account_id.in_(account_ids),
                    AccountAuthorization.revoked_at.is_(None),
                    AccountAuthorization.valid_from <= now,
                    AccountAuthorization.valid_until > now,
                )
                .order_by(
                    AccountAuthorization.account_id,
                    AccountAuthorization.valid_until.desc(),
                )
            ).all()
        )
        if account_ids
        else []
    )
    health_checks = (
        list(
            session.scalars(
                select(SessionHealthCheck)
                .where(SessionHealthCheck.account_id.in_(account_ids))
                .order_by(
                    SessionHealthCheck.account_id,
                    SessionHealthCheck.checked_at.desc(),
                )
            ).all()
        )
        if account_ids
        else []
    )
    account_interventions = (
        list(
            session.scalars(
                select(InterventionRequest)
                .where(InterventionRequest.account_id.in_(account_ids))
                .order_by(
                    InterventionRequest.account_id,
                    InterventionRequest.created_at.desc(),
                )
            ).all()
        )
        if account_ids
        else []
    )
    revocations = (
        list(
            session.scalars(
                select(RevocationRequest)
                .where(RevocationRequest.account_id.in_(account_ids))
                .order_by(RevocationRequest.account_id, RevocationRequest.created_at.desc())
            ).all()
        )
        if account_ids
        else []
    )
    latest_authorization = cast(
        dict[object, AccountAuthorization], _latest_by_account(list(authorizations))
    )
    latest_health = cast(dict[object, SessionHealthCheck], _latest_by_account(list(health_checks)))
    latest_intervention = cast(
        dict[object, InterventionRequest], _latest_by_account(list(account_interventions))
    )
    latest_revocation = cast(dict[object, RevocationRequest], _latest_by_account(list(revocations)))
    accounts = []
    for account, adapter in account_rows:
        authorization = latest_authorization.get(account.id)
        health = latest_health.get(account.id)
        intervention = latest_intervention.get(account.id)
        revocation = latest_revocation.get(account.id)
        accounts.append(
            CustomerAccountView(
                pub_id=account.pub_id,
                account_mask=account.account_mask,
                platform_label=adapter.display_name,
                owner_label=f"成员 · {account.owner_pub_id[-8:]}",
                custody_mode=cast(
                    Literal["server", "customer_device", "hybrid"],
                    account.custody_mode,
                ),
                admission_level=account.admission_level,
                scopes=json.loads(authorization.scopes_json) if authorization else [],
                authorization_expires_at=(authorization.valid_until if authorization else None),
                region_label=account.region,
                session_health=_session_health(account, health),
                last_verified_at=adapter.last_passed_at,
                intervention_status=intervention.state if intervention else "none",
                revocation_receipt_pub_id=revocation.pub_id if revocation else None,
                revoked_at=revocation.deletion_verified_at if revocation else None,
            )
        )

    intervention_total = int(
        session.scalar(
            select(func.count())
            .select_from(InterventionRequest)
            .where(InterventionRequest.tenant_id == tenant_id)
        )
        or 0
    )
    intervention_rows = list(
        session.execute(
            select(InterventionRequest, PlatformAccount)
            .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
            .where(InterventionRequest.tenant_id == tenant_id)
            .order_by(
                InterventionRequest.created_at.desc(),
                InterventionRequest.pub_id.desc(),
            )
            .limit(limit)
        ).all()
    )
    active_lease_filter = (
        SessionLease.tenant_id == tenant_id,
        SessionLease.released_at.is_(None),
        SessionLease.expires_at > now,
    )
    active_lease_total = int(
        session.scalar(select(func.count()).select_from(SessionLease).where(*active_lease_filter))
        or 0
    )
    healthy_cutoff = now - timedelta(minutes=2)
    healthy_sessions = int(
        session.scalar(
            select(func.count())
            .select_from(SessionLease)
            .where(*active_lease_filter, SessionLease.heartbeat_at >= healthy_cutoff)
        )
        or 0
    )
    relevant_account_ids = {
        *account_ids,
        *(account.id for _item, account in intervention_rows),
    }
    active_leases = (
        list(
            session.scalars(
                select(SessionLease)
                .where(
                    *active_lease_filter,
                    SessionLease.account_id.in_(relevant_account_ids),
                )
                .order_by(SessionLease.account_id, SessionLease.expires_at.desc())
            ).all()
        )
        if relevant_account_ids
        else []
    )
    active_lease_by_account = cast(
        dict[object, SessionLease], _latest_by_account(list(active_leases))
    )
    interventions = [
        OperationsLifecycleInterventionView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            challenge_type=cast(
                Literal["otp", "qr", "push", "passkey", "face", "graphical"],
                item.challenge_type,
            ),
            state=item.state,
            lease_expires_at=(
                active_lease_by_account[account.id].expires_at
                if account.id in active_lease_by_account
                else None
            ),
            pairing_expires_at=item.pairing_expires_at,
        )
        for item, account in intervention_rows
    ]

    event_total = int(
        session.scalar(
            select(func.count())
            .select_from(SessionEvent)
            .where(SessionEvent.tenant_id == tenant_id)
        )
        or 0
    )
    event_rows = list(
        session.execute(
            select(SessionEvent, PlatformAccount)
            .join(PlatformAccount, PlatformAccount.id == SessionEvent.account_id)
            .where(SessionEvent.tenant_id == tenant_id)
            .order_by(SessionEvent.occurred_at.desc(), SessionEvent.pub_id.desc())
            .limit(limit)
        ).all()
    )
    events = [
        OperationsLifecycleEventView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            event_type=item.event_type,
            occurred_at=item.occurred_at,
        )
        for item, account in event_rows
    ]

    project_ids = {
        item.id: item.pub_id
        for item in session.scalars(select(Project).where(Project.tenant_id == tenant_id)).all()
    }
    activity_candidates = [
        OperationsLifecycleActivityView(
            pub_id=item.pub_id,
            occurred_at=item.updated_at,
            event_type=f"collection.run.{item.state}",
            object_mask=project_ids.get(item.project_id, "project_unavailable"),
            result=item.state,
            tone=_tone(item.state),
        )
        for item in runs
    ]
    activity_candidates.extend(
        OperationsLifecycleActivityView(
            pub_id=item.pub_id,
            occurred_at=item.occurred_at,
            event_type=item.event_type,
            object_mask=account.account_mask,
            result=item.event_type.rsplit(".", 1)[-1],
            tone=_tone(item.event_type.rsplit(".", 1)[-1]),
        )
        for item, account in event_rows
    )
    activity = sorted(
        activity_candidates,
        key=lambda item: (item.occurred_at, item.pub_id),
        reverse=True,
    )[:limit]
    pending_intervention_count = int(
        session.scalar(
            select(func.count())
            .select_from(InterventionRequest)
            .where(
                InterventionRequest.tenant_id == tenant_id,
                InterventionRequest.state.in_(PENDING_INTERVENTION_STATES),
            )
        )
        or 0
    )
    projection_totals = {
        "activity": run_total + event_total,
        "accounts": account_total,
        "interventions": intervention_total,
        "events": event_total,
    }
    projection_shown = {
        "activity": len(activity),
        "accounts": len(accounts),
        "interventions": len(interventions),
        "events": len(events),
    }
    return OperationsLifecycleView(
        metrics=OperationsLifecycleMetricsView(
            running_runs=running_run_count,
            project_count=project_count,
            pending_interventions=pending_intervention_count,
            healthy_sessions=healthy_sessions,
            total_sessions=active_lease_total,
            delayed_runs=delayed_run_count,
            p95_delay_seconds=p95_delay_seconds,
        ),
        activity=activity,
        accounts=accounts,
        interventions=interventions,
        events=events,
        projection={
            key: OperationsLifecycleProjectionView(
                total=projection_totals[key],
                shown=projection_shown[key],
                truncated=projection_shown[key] < projection_totals[key],
            )
            for key in projection_totals
        },
    )


def _sla_state(row: dict[str, object]) -> Literal["healthy", "warning", "breached", "unmeasured"]:
    total = cast(int, row["total_tasks_30d"])
    if total == 0:
        return "unmeasured"
    success_rate = cast(int, row["completed_tasks_30d"]) / total
    if cast(int, row["overdue_interventions"]) > 0 or success_rate * 10_000 < cast(
        int, row["success_target_bps"]
    ):
        return "breached"
    if cast(int, row["interventions_30d"]) / total >= 0.2:
        return "warning"
    expires_at = row["session_expires_at"]
    if isinstance(expires_at, datetime) and expires_at <= datetime.now(UTC) + timedelta(hours=24):
        return "warning"
    return "healthy"


def _sla_view(row: dict[str, object]) -> PlatformSlaView:
    total = cast(int, row["total_tasks_30d"])
    completed = cast(int, row["completed_tasks_30d"])
    interventions = cast(int, row["interventions_30d"])
    return PlatformSlaView(
        platform=str(row["platform"]),
        display_name=str(row["display_name"]),
        owner_pub_id=str(row["owner_pub_id"]),
        session_ttl_minutes=cast(int, row["session_ttl_minutes"]),
        intervention_sla_minutes=cast(int, row["intervention_sla_minutes"]),
        success_target_bps=cast(int, row["success_target_bps"]),
        total_tasks_30d=total,
        completed_tasks_30d=completed,
        failed_tasks_30d=cast(int, row["failed_tasks_30d"]),
        interventions_30d=interventions,
        overdue_interventions=cast(int, row["overdue_interventions"]),
        success_rate=completed / total if total else None,
        manual_takeover_rate=interventions / total if total else None,
        active_accounts=cast(int, row["active_accounts"]),
        session_expires_at=cast(datetime | None, row["session_expires_at"]),
        state=_sla_state(row),
    )


@router.get("/platform-sla", response_model=list[PlatformSlaView])
def list_platform_sla(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[PlatformSlaView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = (
        session.execute(
            text(
                """
            WITH task_stats AS (
              SELECT task.matrix_json::jsonb->>'adapter' AS platform,
                     count(*)::integer AS total_tasks_30d,
                     count(*) FILTER (WHERE task.state IN ('completed','done'))::integer
                       AS completed_tasks_30d,
                     count(*) FILTER (WHERE task.state='failed')::integer AS failed_tasks_30d
              FROM platform.collection_task task
              WHERE task.tenant_id=:tenant_id
                AND task.created_at >= now() - interval '30 days'
              GROUP BY task.matrix_json::jsonb->>'adapter'
            ), intervention_stats AS (
              SELECT adapter.id AS adapter_id,
                     count(intervention.id) FILTER (
                       WHERE intervention.created_at >= now() - interval '30 days'
                     )::integer AS interventions_30d,
                     count(intervention.id) FILTER (
                       WHERE intervention.state IN (
                         'pending','paired','task_issued','awaiting_platform_probe'
                       ) AND intervention.due_at < now()
                     )::integer AS overdue_interventions
              FROM platform.platform_adapter adapter
              LEFT JOIN platform.platform_account account
                ON account.adapter_id=adapter.id AND account.tenant_id=:tenant_id
              LEFT JOIN platform.intervention_request intervention
                ON intervention.account_id=account.id AND intervention.tenant_id=:tenant_id
              GROUP BY adapter.id
            ), account_stats AS (
              SELECT adapter.id AS adapter_id,
                     count(account.id) FILTER (WHERE account.state='active')::integer
                       AS active_accounts,
                     max(profile.expires_at) FILTER (WHERE profile.state='ACTIVE')
                       AS session_expires_at,
                     min(account.responsible_pub_id) AS default_owner
              FROM platform.platform_adapter adapter
              LEFT JOIN platform.platform_account account
                ON account.adapter_id=adapter.id AND account.tenant_id=:tenant_id
              LEFT JOIN platform.browser_profile profile
                ON profile.account_id=account.id AND profile.tenant_id=:tenant_id
              GROUP BY adapter.id
            )
            SELECT adapter.slug AS platform,adapter.display_name,
                   COALESCE(policy.owner_pub_id,account_stats.default_owner,'unassigned')
                     AS owner_pub_id,
                   COALESCE(policy.session_ttl_minutes,10080)::integer AS session_ttl_minutes,
                   COALESCE(policy.intervention_sla_minutes,30)::integer
                     AS intervention_sla_minutes,
                   COALESCE(policy.success_target_bps,9500)::integer AS success_target_bps,
                   COALESCE(task_stats.total_tasks_30d,0)::integer AS total_tasks_30d,
                   COALESCE(task_stats.completed_tasks_30d,0)::integer AS completed_tasks_30d,
                   COALESCE(task_stats.failed_tasks_30d,0)::integer AS failed_tasks_30d,
                   COALESCE(intervention_stats.interventions_30d,0)::integer
                     AS interventions_30d,
                   COALESCE(intervention_stats.overdue_interventions,0)::integer
                     AS overdue_interventions,
                   COALESCE(account_stats.active_accounts,0)::integer AS active_accounts,
                   account_stats.session_expires_at
            FROM platform.platform_adapter adapter
            LEFT JOIN platform.account_sla_policy policy
              ON policy.adapter_id=adapter.id AND policy.tenant_id=:tenant_id
            LEFT JOIN task_stats ON task_stats.platform=adapter.slug
            LEFT JOIN intervention_stats ON intervention_stats.adapter_id=adapter.id
            LEFT JOIN account_stats ON account_stats.adapter_id=adapter.id
            WHERE adapter.slug IN ('doubao','deepseek','yiyan','tongyi','yuanbao')
            ORDER BY adapter.slug
            """
            ),
            {"tenant_id": repository.tenant.id},
        )
        .mappings()
        .all()
    )
    return [_sla_view(dict(row)) for row in rows]


@router.put("/platform-sla/{platform_slug}", response_model=PlatformSlaView)
def upsert_platform_sla(
    platform_slug: str,
    body: PlatformSlaPolicyUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PlatformSlaView:
    principal.require("sla:manage")
    repository = TenantRepository(session, principal.tenant_pub_id)
    adapter = session.scalar(select(PlatformAdapter).where(PlatformAdapter.slug == platform_slug))
    if adapter is None or platform_slug not in {"doubao", "deepseek", "yiyan", "tongyi", "yuanbao"}:
        raise HTTPException(status_code=404, detail={"code": "platform_not_found"})
    session.execute(
        text(
            """
            INSERT INTO platform.account_sla_policy
              (id,pub_id,tenant_id,adapter_id,owner_pub_id,session_ttl_minutes,
               intervention_sla_minutes,success_target_bps)
            VALUES (gen_random_uuid(),:pub_id,:tenant_id,:adapter_id,:owner_pub_id,
                    :session_ttl_minutes,:intervention_sla_minutes,:success_target_bps)
            ON CONFLICT (tenant_id,adapter_id) DO UPDATE
            SET owner_pub_id=EXCLUDED.owner_pub_id,
                session_ttl_minutes=EXCLUDED.session_ttl_minutes,
                intervention_sla_minutes=EXCLUDED.intervention_sla_minutes,
                success_target_bps=EXCLUDED.success_target_bps,updated_at=now()
            """
        ),
        {
            "pub_id": new_pub_id("sla"),
            "tenant_id": repository.tenant.id,
            "adapter_id": adapter.id,
            **body.model_dump(),
        },
    )
    session.commit()
    matches = [
        item
        for item in list_platform_sla(principal=principal, session=session)
        if item.platform == platform_slug
    ]
    if not matches:
        raise HTTPException(status_code=404, detail={"code": "platform_not_found"})
    return matches[0]


# Keep the business portfolio projection behind the existing Operations router mount while
# isolating its contract and query from the account/session lifecycle implementation above.
from .business_overview_router import router as business_overview_router  # noqa: E402

router.include_router(business_overview_router)
