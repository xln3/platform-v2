"""Browser capacity policy; account reservations remain the dispatch authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class BrowserCapacity:
    key: str
    active: bool
    demanded: bool
    protected: bool
    last_used: datetime


@dataclass(frozen=True)
class BrowserAction:
    key: str
    action: str
    reason: str


def plan_browser_capacity(
    resources: list[BrowserCapacity],
    *,
    now: datetime,
    idle_seconds: int = 900,
    max_active: int = 8,
) -> list[BrowserAction]:
    if idle_seconds < 60 or max_active < 1:
        raise ValueError("elastic capacity limits must be positive and idle_seconds >= 60")
    if len({r.key for r in resources}) != len(resources):
        raise ValueError("duplicate browser instance")
    actions = []
    active = sum(r.active for r in resources)
    # Idle reclamation precedes scale-up so a full host can serve another lane.
    for resource in sorted(resources, key=lambda r: (r.last_used, r.key)):
        if (
            resource.active
            and not resource.demanded
            and not resource.protected
            and now - resource.last_used >= timedelta(seconds=idle_seconds)
        ):
            actions.append(BrowserAction(resource.key, "stop", "idle_timeout"))
            active -= 1
    for resource in sorted(resources, key=lambda r: (r.last_used, r.key)):
        if resource.demanded and not resource.active:
            if active >= max_active:
                actions.append(BrowserAction(resource.key, "wait", "host_capacity"))
            else:
                actions.append(BrowserAction(resource.key, "start", "account_reserved"))
                active += 1
    return actions
