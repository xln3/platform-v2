"""Wait for the resource controller to wake a reserved resident browser."""

import os
import time

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError


def wait_for_resident_browser(cdp_url: str, *, instance: str) -> None:
    if os.getenv("GEO_BROWSER_ELASTIC_ENABLED", "0") != "1":
        return
    deadline = time.monotonic() + 90.0
    with httpx.Client(trust_env=False, timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(cdp_url.rstrip("/") + "/json/version")
                if response.is_success and response.json().get("webSocketDebuggerUrl"):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            try:
                activity.heartbeat({"stage": "browser_capacity_wait", "instance": instance})
            except RuntimeError:
                pass
            time.sleep(2)
    raise ApplicationError(
        f"reserved browser {instance} did not become ready within 90s",
        type="account_contention_timeout",
        non_retryable=True,
    )
