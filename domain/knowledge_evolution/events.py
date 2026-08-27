"""Versioned, domain-neutral event envelope for polling or broker adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeEvent:
    schema_version: str
    event_id: str
    event_type: str
    occurred_at: datetime
    tenant: str
    namespace: str
    domain: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    payload_hash: str


def event_envelope(
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    tenant: str,
    namespace: str,
    domain: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
) -> KnowledgeEvent:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return KnowledgeEvent(
        schema_version="knowledge-event-v1",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        tenant=tenant,
        namespace=namespace,
        domain=domain,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload,
        payload_hash="sha256:" + hashlib.sha256(rendered.encode()).hexdigest(),
    )


__all__ = ["KnowledgeEvent", "event_envelope"]
