from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from .models import BrowserFence, BrowserProfile, PlatformAccount, SessionLease

log = structlog.get_logger()


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


# ---------------------------------------------------------------------------
# 常驻浏览器跨 worker fencing（platform.browser_fence，机器资源无租户）
# ---------------------------------------------------------------------------


def acquire_browser_fence(
    session: Session,
    *,
    platform: str,
    holder: str,
    ttl: timedelta,
) -> BrowserFence:
    """获取平台浏览器 fence。被他人持有且未过期 → LeaseBusyError；
    过期未释放 → 抢占（如实记 log），fencing_token 无论新建/续租/抢占都 +1。"""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"browser_fence:{platform}"))))
    now = datetime.now(UTC)
    row = session.scalar(
        select(BrowserFence).where(BrowserFence.platform == platform).with_for_update()
    )
    if row is not None and row.released_at is None and row.expires_at > now:
        raise LeaseBusyError(platform)
    token = int(row.fencing_token if row is not None else 0) + 1
    if row is not None and row.released_at is None:
        log.warning(
            "browser_fence_preempted",
            platform=platform,
            previous_holder=row.holder,
            previous_fencing_token=row.fencing_token,
            expired_at=row.expires_at.isoformat(),
        )
    if row is None:
        row = BrowserFence(
            platform=platform,
            holder=holder,
            fencing_token=token,
            expires_at=now + ttl,
        )
        session.add(row)
    else:
        row.holder = holder
        row.fencing_token = token
        row.acquired_at = now
        row.heartbeat_at = now
        row.expires_at = now + ttl
        row.released_at = None
    session.flush()
    return row


def release_browser_fence(
    session: Session,
    *,
    platform: str,
    holder: str,
    fencing_token: int,
) -> bool:
    """归还 fence。holder/token 对不上（已被抢占后重发）或本就已释放 → False，
    调用方如实 warning——stale token 释放绝不炸、绝不误删他人租约。"""
    row = session.scalar(
        select(BrowserFence).where(BrowserFence.platform == platform).with_for_update()
    )
    if row is None or row.released_at is not None:
        return False
    if row.holder != holder or row.fencing_token != fencing_token:
        return False
    row.released_at = datetime.now(UTC)
    session.flush()
    return True


def heartbeat_browser_fence(
    session: Session,
    *,
    platform: str,
    holder: str,
    fencing_token: int,
    ttl: timedelta,
) -> bool:
    """续期 fence（heartbeat_at/expires_at 前推 ttl）。holder/token 失配、
    已释放或已过期 → False（fencing 已丢失，调用方如实记 log）。"""
    now = datetime.now(UTC)
    row = session.scalar(
        select(BrowserFence).where(BrowserFence.platform == platform).with_for_update()
    )
    if (
        row is None
        or row.released_at is not None
        or row.holder != holder
        or row.fencing_token != fencing_token
        or row.expires_at <= now
    ):
        return False
    row.heartbeat_at = now
    row.expires_at = now + ttl
    session.flush()
    return True
