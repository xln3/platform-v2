from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from .models import BrowserProfile, PlatformAccount, SessionLease


class LeaseBusyError(RuntimeError):
    pass


class FenceViolationError(RuntimeError):
    pass


def acquire_session_lease(
    session: Session,
    account: PlatformAccount,
    profile: BrowserProfile,
    holder: str,
    capability: str,
    ttl: timedelta = timedelta(minutes=20),
) -> SessionLease:
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(account.pub_id))))
    now = datetime.now(UTC)
    current = session.scalar(
        select(SessionLease)
        .where(
            SessionLease.account_id == account.id,
            SessionLease.released_at.is_(None),
            SessionLease.expires_at > now,
        )
        .with_for_update()
    )
    if current is not None:
        raise LeaseBusyError(account.pub_id)
    previous_fence = session.scalar(
        select(func.coalesce(func.max(SessionLease.fencing_token), 0)).where(
            SessionLease.account_id == account.id
        )
    )
    lease = SessionLease(
        pub_id=new_pub_id("sle"),
        tenant_id=account.tenant_id,
        account_id=account.id,
        profile_id=profile.id,
        holder=holder,
        capability=capability,
        fencing_token=int(previous_fence or 0) + 1,
        expires_at=now + ttl,
    )
    session.add(lease)
    session.flush()
    return lease


def heartbeat_lease(
    session: Session, lease: SessionLease, fencing_token: int, ttl: timedelta
) -> None:
    if lease.fencing_token != fencing_token or lease.released_at is not None:
        raise FenceViolationError(lease.pub_id)
    now = datetime.now(UTC)
    if lease.expires_at <= now:
        raise FenceViolationError(lease.pub_id)
    lease.heartbeat_at = now
    lease.expires_at = now + ttl


def assert_fenced_write(lease: SessionLease, fencing_token: int) -> None:
    if (
        lease.fencing_token != fencing_token
        or lease.released_at is not None
        or lease.expires_at <= datetime.now(UTC)
    ):
        raise FenceViolationError(lease.pub_id)
