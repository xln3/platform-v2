"""Activate a verified lease only while all affected browser/account leases are idle."""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from pathlib import Path

from geo_platform.collection.account_models import (
    CollectionBrowser,
    CollectionPlatformAccount,
    CollectionRegion,
)
from geo_platform.collection.leases import (
    LeaseBusyError,
    acquire_browser_fence,
    release_browser_fence,
)
from geo_platform.collection.models import CollectionRun
from geo_platform.config import get_settings
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tools.elastic_browser_controller import unit_active, unit_for
from tools.sync_wukong_relay_env import _rewrite_env
from workflows.activities.region_proxy_router import ResolvedRegionProxy

_TERMINAL = {"completed", "completed_with_failures", "cancelled", "failed", "skipped"}


def activate_verified_proxy(resolved: ResolvedRegionProxy) -> str:
    if not resolved.proxy_url or not resolved.region_gb or not resolved.observed_gb:
        raise ValueError("verified proxy is required for activation")
    if resolved.observed_gb[:2] != resolved.region_gb[:2]:
        raise ValueError("proxy region mismatch")
    engine = create_engine(get_settings().postgres_dsn, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            region = session.scalar(
                select(CollectionRegion)
                .where(
                    CollectionRegion.region_gb == resolved.region_gb,
                )
                .with_for_update()
            )
            if (
                region is None
                or not region.relay_unit
                or not re.fullmatch(
                    r"geo-platform-v2-proxy-relay@[a-z0-9_]+\.service",
                    region.relay_unit,
                )
            ):
                return "relay_registration_required"
            prop = subprocess.run(
                ["systemctl", "show", region.relay_unit, "--property=EnvironmentFiles", "--value"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            matched = re.fullmatch(
                r"(/etc/geo-platform-v2/[A-Za-z0-9_.-]+\.env) \(ignore_errors=(?:yes|no)\)",
                prop,
            )
            if not matched:
                raise ValueError("relay environment must be one registered local file")
            env_path = Path(matched[1])
            old = [
                line.split("=", 1)[1]
                for line in env_path.read_text().splitlines()
                if line.startswith("UPSTREAM_PROXY_URL=")
            ]
            if len(old) != 1:
                raise ValueError("relay upstream is ambiguous")
            if old[0] == resolved.proxy_url:
                return "already_active"
            accounts = list(
                session.scalars(
                    select(CollectionPlatformAccount)
                    .where(
                        CollectionPlatformAccount.region_gb == resolved.region_gb,
                    )
                    .with_for_update()
                )
            )
            for account in accounts:
                if account.runtime_state == "captcha":
                    return "waiting_for_region_drain"
                if account.current_run_pub_id:
                    run = session.scalar(
                        select(CollectionRun).where(
                            CollectionRun.pub_id == account.current_run_pub_id,
                        )
                    )
                    if run is not None and run.state not in _TERMINAL:
                        return "waiting_for_region_drain"
            browsers = list(
                session.scalars(
                    select(CollectionBrowser)
                    .where(
                        CollectionBrowser.region_gb == resolved.region_gb,
                    )
                    .order_by(CollectionBrowser.instance_key)
                )
            )
            fences = []
            for browser in browsers:
                if browser.activity not in {"idle", "error"}:
                    return "waiting_for_region_drain"
                try:
                    fences.append(
                        acquire_browser_fence(
                            session,
                            platform=browser.instance_key,
                            holder="elastic-proxy-activation",
                            ttl=timedelta(minutes=5),
                        )
                    )
                except LeaseBusyError:
                    return "waiting_for_region_drain"
            active = [
                browser.instance_key for browser in browsers if unit_active(browser.instance_key)
            ]
            stopped = []
            changed = False
            try:
                for key in active:
                    subprocess.run(
                        ["systemctl", "stop", unit_for(key)],
                        check=True,
                        capture_output=True,
                        timeout=45,
                    )
                    stopped.append(key)
                _rewrite_env(env_path, resolved.proxy_url, apply=True)
                changed = True
                subprocess.run(
                    ["systemctl", "restart", region.relay_unit],
                    check=True,
                    capture_output=True,
                    timeout=45,
                )
            except Exception:
                if changed:
                    _rewrite_env(env_path, old[0], apply=True)
                    subprocess.run(
                        ["systemctl", "restart", region.relay_unit],
                        check=False,
                        capture_output=True,
                        timeout=45,
                    )
                raise
            finally:
                restart_error = None
                for key in stopped:
                    try:
                        subprocess.run(
                            ["systemctl", "start", unit_for(key)],
                            check=True,
                            capture_output=True,
                            timeout=45,
                        )
                    except Exception as exc:
                        restart_error = exc
                if restart_error is not None:
                    raise RuntimeError("failed to restore an affected browser") from restart_error
            for fence in fences:
                release_browser_fence(
                    session,
                    platform=fence.platform,
                    holder="elastic-proxy-activation",
                    fencing_token=fence.fencing_token,
                )
            session.commit()
            return "activated"
    finally:
        engine.dispose()
