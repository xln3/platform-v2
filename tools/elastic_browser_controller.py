"""Root-side browser reconciler. Dry-run by default; no platform questions sent.

Starts only registered local browser units with a live account reservation.
Stops idle units only after locking account rows and acquiring the same database
fence used by workers and captcha-assist. No force-release or health reset.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geo_platform.collection.account_models import CollectionBrowser, CollectionPlatformAccount
from geo_platform.collection.elastic_resources import BrowserCapacity, plan_browser_capacity
from geo_platform.collection.leases import (
    LeaseBusyError,
    acquire_browser_fence,
    release_browser_fence,
)
from geo_platform.collection.models import CollectionRun
from geo_platform.config import get_settings
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_TERMINAL = {"completed", "completed_with_failures", "failed", "cancelled", "skipped"}
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")


def unit_for(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ValueError("invalid browser instance key")
    return f"geo-platform-v2-browser@{key}.service"


def unit_active(key: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", unit_for(key)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode not in (0, 3):
        raise RuntimeError("browser unit state unavailable")
    return result.stdout.strip() in {"active", "activating", "reloading"}


def capacity(session: Session, browser: CollectionBrowser, now: datetime) -> BrowserCapacity:
    accounts = list(
        session.scalars(
            select(CollectionPlatformAccount)
            .where(
                CollectionPlatformAccount.browser_instance_key == browser.instance_key,
            )
            .with_for_update()
        )
    )
    demanded = False
    protected = browser.activity not in {"idle", "error"}
    # Health probes refresh updated_at; they are not collection demand.
    latest = browser.created_at or now
    for account in accounts:
        latest = max(latest, account.state_updated_at or account.created_at or now)
        if account.runtime_state == "captcha":
            protected = True
        if account.current_run_pub_id:
            run = session.scalar(
                select(CollectionRun).where(
                    CollectionRun.pub_id == account.current_run_pub_id,
                )
            )
            if run is not None and run.state not in _TERMINAL:
                protected = True
                demanded = account.runtime_state in {"running", "captcha"}
    return BrowserCapacity(
        browser.instance_key, unit_active(browser.instance_key), demanded, protected, latest
    )


def reconcile(*, apply: bool, idle_seconds: int, max_active: int) -> list[dict[str, str]]:
    engine = create_engine(get_settings().postgres_dsn, pool_pre_ping=True)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            browsers = list(session.scalars(select(CollectionBrowser)))
            local = [
                b
                for b in browsers
                if _KEY.fullmatch(b.instance_key)
                and Path(f"/etc/geo-platform-v2/browser-{b.instance_key}.env").is_file()
            ]
            resources = [capacity(session, browser, now) for browser in local]
            plan = plan_browser_capacity(
                resources, now=now, idle_seconds=idle_seconds, max_active=max_active
            )
            session.rollback()
        records = []
        for action in plan:
            record = {
                "instance": action.key,
                "action": action.action,
                "reason": action.reason,
                "state": "planned",
            }
            if apply and action.action != "wait":
                with Session(engine) as session:
                    browser = session.scalar(
                        select(CollectionBrowser).where(
                            CollectionBrowser.instance_key == action.key,
                        )
                    )
                    if browser is None:
                        continue
                    fresh = capacity(session, browser, datetime.now(UTC))
                    if action.action == "start":
                        if not fresh.demanded or fresh.active:
                            continue
                        if (
                            sum(unit_active(candidate.instance_key) for candidate in local)
                            >= max_active
                        ):
                            record["state"] = "capacity_wait"
                            records.append(record)
                            continue
                        # Starting a stopped supervisor is idempotent and must work
                        # even while a worker holds its fence waiting for CDP.
                        subprocess.run(
                            ["systemctl", "start", unit_for(action.key)],
                            check=True,
                            timeout=45,
                            capture_output=True,
                        )
                    else:
                        if fresh.demanded or fresh.protected or not fresh.active:
                            continue
                        if datetime.now(UTC) - fresh.last_used < timedelta(seconds=idle_seconds):
                            continue
                        try:
                            fence = acquire_browser_fence(
                                session,
                                platform=action.key,
                                holder="elastic-browser-controller",
                                ttl=timedelta(seconds=60),
                            )
                        except LeaseBusyError:
                            session.rollback()
                            record["state"] = "fenced_busy"
                            records.append(record)
                            continue
                        subprocess.run(
                            ["systemctl", "stop", unit_for(action.key)],
                            check=True,
                            timeout=45,
                            capture_output=True,
                        )
                        release_browser_fence(
                            session,
                            platform=action.key,
                            holder="elastic-browser-controller",
                            fencing_token=fence.fencing_token,
                        )
                    session.commit()
                    record["state"] = "applied"
            records.append(record)
        return records
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=900)
    parser.add_argument("--max-active", type=int, default=8)
    args = parser.parse_args()
    with Path("runtime/elastic-browser-controller.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for record in reconcile(
            apply=args.apply, idle_seconds=args.idle_seconds, max_active=args.max_active
        ):
            print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
