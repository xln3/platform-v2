"""Bounded automatic paid acquisition, outside the collection/retry activity.

The local journal reserves a daily count BEFORE provider I/O. Unknown outcomes
remain reserved across restarts and dates. Provider reconciliation and duplicate
order prevention are still enforced by WukongLeasePool's canonical cache/lock.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from workflows.activities.region_proxy_router import RegionProxyError, RegionProxyRouter

HARD_DAILY_LIMIT = 3
PURCHASE_TIMEZONE = ZoneInfo("Asia/Tokyo")


class ProxyPurchaseBudget:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS purchase_attempt (
            region TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL,
            attempted_at REAL NOT NULL,
            PRIMARY KEY(region, day))""")
        self.connection.commit()

    def reserve(self, region: str, *, daily_limit: int, now: datetime) -> bool:
        if not re.fullmatch(r"\d{6}", region) or not 1 <= daily_limit <= HARD_DAILY_LIMIT:
            raise ValueError("explicit positive daily purchase limit and GB region required")
        day = now.astimezone(PURCHASE_TIMEZONE).date().isoformat()
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            unresolved = conn.execute(
                "SELECT 1 FROM purchase_attempt WHERE region=? AND state='pending'",
                (region,),
            ).fetchone()
            previous = conn.execute(
                "SELECT state,attempted_at FROM purchase_attempt WHERE region=? AND day=?",
                (region, day),
            ).fetchone()
            count = conn.execute(
                "SELECT count(*) FROM purchase_attempt WHERE day=? AND state <> 'rejected'",
                (day,),
            ).fetchone()[0]
            cooldown = previous and (
                previous[0] != "rejected" or now.timestamp() - previous[1] < 900
            )
            if unresolved or cooldown or count >= daily_limit:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO purchase_attempt VALUES (?, ?, 'pending', ?) "
                "ON CONFLICT(region,day) DO UPDATE SET state='pending',"
                "attempted_at=excluded.attempted_at",
                (region, day, now.timestamp()),
            )
            conn.commit()
            return True
        except BaseException:
            conn.rollback()
            raise

    def complete(self, region: str, *, now: datetime, state: str) -> None:
        if state not in {"fulfilled", "rejected"}:
            raise ValueError("unknown purchase outcome")
        self.connection.execute(
            "UPDATE purchase_attempt SET state=? WHERE region=? AND day=? AND state='pending'",
            (state, region, now.astimezone(PURCHASE_TIMEZONE).date().isoformat()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def reconcile_available(self, region: str) -> None:
        self.connection.execute(
            "UPDATE purchase_attempt SET state='fulfilled' WHERE region=? AND state='pending'",
            (region,),
        )
        self.connection.commit()


def ensure_region_capacity(
    region: str,
    *,
    router: RegionProxyRouter,
    budget: ProxyPurchaseBudget,
    daily_limit: int,
    apply: bool,
    now: datetime,
    notify_needed: Callable[[str, datetime], bool] | None = None,
) -> dict[str, Any]:
    try:
        resolved = router.resolve("doubao", region)
    except RegionProxyError as exc:
        if exc.code != "proxy_lease_unavailable":
            return {"region": region, "state": "blocked", "reason": exc.code}
    else:
        budget.reconcile_available(region)
        return {"region": region, "state": "available", "source": resolved.source}
    if not apply:
        return {"region": region, "state": "purchase_planned"}
    # A durable enqueue alone is not proof of remote notification. The callback
    # must verify the provider message receipt before authorizing paid I/O.
    if notify_needed is None or not notify_needed(region, now):
        return {"region": region, "state": "blocked", "reason": "notification_pending"}
    if not budget.reserve(region, daily_limit=daily_limit, now=now):
        return {"region": region, "state": "blocked", "reason": "budget_or_reconciliation"}
    try:
        result = router.acquire_paid(region, confirm_spend=True)
    except RegionProxyError as exc:
        # Do not clear uncertain intent or refund the reserved count on errors.
        # A provider POST may already have succeeded; reconciliation is required.
        if exc.provider_action in {
            "no_balance",
            "no_stock",
            "existing_order_expiring",
            "existing_order_validate_failed",
        } or (exc.provider_action and exc.provider_action.startswith("buy_failed:")):
            budget.complete(region, now=now, state="rejected")
        return {
            "region": region,
            "state": "blocked",
            "reason": exc.code,
            "provider_action": exc.provider_action,
        }
    budget.complete(region, now=now, state="fulfilled")
    return {
        "region": region,
        "state": "acquired",
        "action": result.provider_action,
        "observed_gb": result.observed_gb,
        "activation": "relay_sync_required",
    }
