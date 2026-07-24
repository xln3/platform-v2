import json
from dataclasses import dataclass

from geo_platform.collection.leases import acquire_session_lease
from geo_platform.collection.models import (
    AccountAuthorization,
    BrowserProfile,
    CapabilityLease,
    CollectionRun,
    CollectionTask,
    PlatformAccount,
    RevocationRequest,
    SessionEvent,
    SessionLease,
)
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError


@dataclass
class CollectionTaskInput:
    business_key: str
    query: str
    model: str
    region: str
    mode: str
    adapter: str = "fixed"
    fail_until_attempt: int = 0


@dataclass
class CollectionTaskResult:
    business_key: str
    answer_text: str
    screenshot_ref: str
    quality_state: str


@dataclass
class SessionPreparation:
    lease_pub_id: str
    fencing_token: int
    profile_version: int


@dataclass
class RevocationResult:
    account_pub_id: str
    released_leases: int
    purged_profile_versions: list[int]
    deletion_verified: bool


@activity.defn
async def collect_with_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """Fail-closed production adapter boundary.

    A worker deployment must replace this activity registration with a live,
    capability-gated platform adapter. Contract fixtures belong in tests only.
    """
    activity.heartbeat({"business_key": item.business_key, "stage": "adapter_started"})
    raise ApplicationError(
        "no live collection adapter is registered",
        type="adapter_not_configured",
        non_retryable=True,
    )


@activity.defn
async def publish_downstream_event(run_pub_id: str) -> str:
    activity.heartbeat({"run_pub_id": run_pub_id, "stage": "outbox"})
    return f"collection.completed:{run_pub_id}"


@activity.defn
def persist_collection_result(run_pub_id: str, result: CollectionTaskResult) -> None:
    """Transactional, business-key idempotent activity."""
    with SessionLocal() as session:
        run = session.scalar(select(CollectionRun).where(CollectionRun.pub_id == run_pub_id))
        if run is None:
            raise ValueError("run_not_found")
        prior = session.scalar(
            select(CollectionTask).where(
                CollectionTask.run_id == run.id,
                CollectionTask.business_key == result.business_key,
            )
        )
        if prior is None:
            session.add(
                CollectionTask(
                    pub_id=new_pub_id("tsk"),
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    business_key=result.business_key,
                    matrix_json="{}",
                    state="completed",
                    attempt_count=1,
                    answer_text=result.answer_text,
                    screenshot_ref=result.screenshot_ref,
                    quality_state=result.quality_state,
                )
            )
            run.completed_tasks += 1
        run.state = "completed" if run.completed_tasks >= run.total_tasks else "running"
        session.commit()


@activity.defn
def finalize_account_revocation(request_pub_id: str) -> RevocationResult:
    """Idempotently propagates revocation through leases and encrypted profile versions."""
    from datetime import UTC, datetime

    with SessionLocal() as session:
        request = session.scalar(
            select(RevocationRequest).where(RevocationRequest.pub_id == request_pub_id)
        )
        if request is None:
            raise ValueError("revocation_request_not_found")
        account = session.get(PlatformAccount, request.account_id)
        assert account is not None
        now = datetime.now(UTC)
        leases = session.scalars(
            select(SessionLease).where(
                SessionLease.account_id == account.id,
                SessionLease.released_at.is_(None),
            )
        ).all()
        for lease in leases:
            lease.released_at = now
        profiles = session.scalars(
            select(BrowserProfile).where(BrowserProfile.account_id == account.id)
        ).all()
        for profile in profiles:
            profile.state = "PURGED"
            profile.ciphertext = None
            profile.nonce = None
            profile.wrapped_dek = None
            profile.purged_at = profile.purged_at or now
        capability_leases = session.scalars(
            select(CapabilityLease).where(
                CapabilityLease.account_id == account.id,
                CapabilityLease.revoked_at.is_(None),
            )
        ).all()
        for capability_lease in capability_leases:
            capability_lease.revoked_at = now
        account.state = "revoked"
        request.state = "completed"
        request.deletion_verified_at = now
        prior_event = session.scalar(
            select(SessionEvent).where(
                SessionEvent.account_id == account.id,
                SessionEvent.event_type == "account.revocation.completed",
            )
        )
        if prior_event is None:
            session.add(
                SessionEvent(
                    pub_id=new_pub_id("sev"),
                    tenant_id=account.tenant_id,
                    account_id=account.id,
                    event_type="account.revocation.completed",
                    summary_json=json.dumps({"request_pub_id": request.pub_id}),
                )
            )
        session.commit()
        return RevocationResult(
            account_pub_id=account.pub_id,
            released_leases=len(leases),
            purged_profile_versions=[item.profile_version for item in profiles],
            deletion_verified=True,
        )


@activity.defn
def prepare_collection_session(
    account_pub_id: str, holder: str, required_scope: str
) -> SessionPreparation:
    from datetime import UTC, datetime, timedelta

    with SessionLocal() as session:
        account = session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.pub_id == account_pub_id,
                PlatformAccount.state.in_(["active", "challenge_required"]),
            )
        )
        if account is None:
            raise ValueError("account_not_active")
        authorization = session.scalar(
            select(AccountAuthorization)
            .where(
                AccountAuthorization.account_id == account.id,
                AccountAuthorization.revoked_at.is_(None),
                AccountAuthorization.valid_from <= datetime.now(UTC),
                AccountAuthorization.valid_until > datetime.now(UTC),
            )
            .order_by(AccountAuthorization.created_at.desc())
        )
        if authorization is None or required_scope not in json.loads(authorization.scopes_json):
            raise ValueError("scope_not_authorized")
        profile = session.scalar(
            select(BrowserProfile)
            .where(
                BrowserProfile.account_id == account.id,
                BrowserProfile.state == "ACTIVE",
            )
            .order_by(BrowserProfile.profile_version.desc())
        )
        if profile is None:
            raise ValueError("active_profile_not_found")
        lease = acquire_session_lease(
            session,
            account,
            profile,
            holder,
            required_scope,
            timedelta(minutes=20),
        )
        session.commit()
        return SessionPreparation(
            lease_pub_id=lease.pub_id,
            fencing_token=lease.fencing_token,
            profile_version=profile.profile_version,
        )


@activity.defn
def release_collection_session(lease_pub_id: str, fencing_token: int) -> None:
    from datetime import UTC, datetime

    with SessionLocal() as session:
        lease = session.scalar(
            select(SessionLease).where(SessionLease.pub_id == lease_pub_id).with_for_update()
        )
        if lease is None:
            return
        if lease.fencing_token != fencing_token:
            raise ValueError("fence_violation")
        lease.released_at = lease.released_at or datetime.now(UTC)
        session.commit()
