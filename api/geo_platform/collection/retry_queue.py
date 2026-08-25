"""Durable query-level retry queue and failure-learning helpers.

The database is the queue authority.  Temporal executions are disposable
consumers of an immutable task plan; a workflow/run boundary never decides
whether an individual query result is usable.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from domain.security.redaction import redact_value

from .models import CollectionRun, CollectionTask

RetryTrigger = Literal["automatic", "manual"]

_MAX_AUTO_RETRIES = 2
_PUB_HEX_LENGTH = 26


@dataclass(frozen=True)
class RetryTaskPlan:
    source_task_id: uuid.UUID
    source_task_pub_id: str
    business_key: str
    query: str
    model: str
    region: str
    mode: str
    adapter: str
    capability_key: str
    not_before: datetime
    retry_depth: int


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _query_identity(query: str) -> dict[str, int | str]:
    """Identify a query for learning without copying customer text or secrets."""

    encoded = query.encode()
    return {
        "query_sha256": hashlib.sha256(encoded).hexdigest(),
        "query_length": len(query),
    }


def _redacted_json(value: Any) -> str:
    return _canonical_json(redact_value(value))


def _stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-collection-retry:{kind}:{key}")


def _stable_pub_id(prefix: str, key: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{key}".encode()).hexdigest()
    return f"{prefix}_{digest[:_PUB_HEX_LENGTH]}"


def _matrix(task: CollectionTask, *, require_complete: bool = True) -> dict[str, str]:
    try:
        raw = json.loads(task.matrix_json or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("retry_source_matrix_invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("retry_source_matrix_invalid")
    required = ("query", "model", "region", "mode", "adapter")
    normalized: dict[str, str] = {}
    for key in required:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            if require_complete:
                raise ValueError(f"retry_source_matrix_missing_{key}")
            normalized[key] = "[legacy-unavailable]"
            continue
        normalized[key] = value.strip()
    return normalized


def capability_key(matrix: dict[str, str]) -> str:
    """Stable routing key for future capability-aware worker pools."""

    value = "|".join(
        (
            f"adapter={matrix['adapter']}",
            f"model={matrix['model']}",
            f"region={matrix['region']}",
            f"mode={matrix['mode']}",
        )
    )
    if len(value) > 255:
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f"capability-sha256={digest}"
    return value


def retry_depth(session: Session, run: CollectionRun) -> int:
    """Return the number of retry edges between ``run`` and its root run."""

    depth = 0
    cursor = run
    visited = {run.pub_id}
    while getattr(cursor, "retry_of_run_pub_id", None):
        parent = session.scalar(
            select(CollectionRun).where(
                CollectionRun.tenant_id == run.tenant_id,
                CollectionRun.pub_id == cursor.retry_of_run_pub_id,
            )
        )
        if parent is None:
            raise ValueError("retry_lineage_broken")
        if parent.project_id != run.project_id or parent.pub_id in visited:
            raise ValueError("retry_lineage_invalid")
        visited.add(parent.pub_id)
        cursor = parent
        depth += 1
        if depth > 100:
            raise ValueError("retry_lineage_too_deep")
    return depth


def _automatic_not_before(*, business_key: str, depth: int, now: datetime) -> datetime:
    # Exponential backoff is capped at 15 minutes.  Stable jitter prevents a
    # failed capability pool from stampeding when many tenants become eligible.
    base_seconds = min(900, 30 * (2 ** min(max(depth - 1, 0), 5)))
    jitter_seconds = int(hashlib.sha256(business_key.encode()).hexdigest()[:4], 16) % 31
    return now + timedelta(seconds=base_seconds + jitter_seconds)


def failed_task_plans(
    session: Session,
    *,
    run: CollectionRun,
    trigger: RetryTrigger,
    task_pub_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[RetryTaskPlan]:
    current = now or datetime.now(UTC)
    depth = retry_depth(session, run) + 1
    requested = (
        frozenset(value.strip() for value in task_pub_ids if value.strip())
        if task_pub_ids is not None
        else None
    )
    conditions = [CollectionTask.run_id == run.id, CollectionTask.state == "failed"]
    if requested is not None:
        if not requested:
            raise ValueError("retry_task_selection_empty")
        conditions.append(CollectionTask.pub_id.in_(requested))
    tasks = list(
        session.scalars(
            select(CollectionTask)
            .where(*conditions)
            .order_by(CollectionTask.created_at, CollectionTask.pub_id)
        )
    )
    if requested is not None and {task.pub_id for task in tasks} != requested:
        raise ValueError("retry_tasks_not_failed_or_not_found")
    plans: list[RetryTaskPlan] = []
    for task in tasks:
        matrix = _matrix(task)
        plans.append(
            RetryTaskPlan(
                source_task_id=task.id,
                source_task_pub_id=task.pub_id,
                business_key=task.business_key,
                query=matrix["query"],
                model=matrix["model"],
                region=matrix["region"],
                mode=matrix["mode"],
                adapter=matrix["adapter"],
                capability_key=capability_key(matrix),
                not_before=(
                    current
                    if trigger == "manual"
                    else _automatic_not_before(
                        business_key=task.business_key,
                        depth=depth,
                        now=current,
                    )
                ),
                retry_depth=depth,
            )
        )
    return plans


def ensure_retry_intents(
    session: Session,
    *,
    run: CollectionRun,
    trigger: RetryTrigger,
    created_by_pub_id: str,
    task_pub_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[RetryTaskPlan]:
    """Create one retry intent per failed query and return runnable plans.

    Automatic retries stop after the configured depth.  A manual trigger may
    revive a pending/failed/exhausted intent, but can never duplicate an intent
    that is already enqueued or succeeded.
    """

    current = now or datetime.now(UTC)
    plans = failed_task_plans(
        session,
        run=run,
        trigger=trigger,
        task_pub_ids=task_pub_ids,
        now=current,
    )
    for plan in plans:
        initial_state = (
            "pending"
            if trigger == "manual" or plan.retry_depth <= _MAX_AUTO_RETRIES
            else "exhausted"
        )
        values = {
            "id": _stable_uuid("intent", str(plan.source_task_id)),
            "pub_id": _stable_pub_id("qri", str(plan.source_task_id)),
            "tenant_id": run.tenant_id,
            "project_id": run.project_id,
            "source_run_id": run.id,
            "source_task_id": plan.source_task_id,
            "business_key": plan.business_key,
            "capability_key": plan.capability_key,
            "state": initial_state,
            "trigger_mode": trigger,
            # Manual recovery is operator-directed. Automatic retries remain
            # below initial work so a provider outage cannot starve new runs.
            "priority": 200 if trigger == "manual" else 50,
            "retry_depth": plan.retry_depth,
            "max_auto_retries": _MAX_AUTO_RETRIES,
            "not_before": plan.not_before,
            "last_error_code": "query_failed",
            "created_by_pub_id": created_by_pub_id[:30],
            "now": current,
        }
        if trigger == "manual":
            session.execute(
                text(
                    """
                    INSERT INTO platform.collection_query_retry_intent
                      (id,pub_id,tenant_id,project_id,source_run_id,source_task_id,
                       business_key,capability_key,state,trigger_mode,priority,retry_depth,
                       max_auto_retries,not_before,last_error_code,created_by_pub_id,
                       created_at,updated_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:project_id,:source_run_id,:source_task_id,
                       :business_key,:capability_key,:state,:trigger_mode,:priority,:retry_depth,
                       :max_auto_retries,:not_before,:last_error_code,:created_by_pub_id,
                       :now,:now)
                    ON CONFLICT (source_task_id) DO UPDATE
                    SET state=CASE
                          WHEN platform.collection_query_retry_intent.state
                               IN ('pending','failed','exhausted')
                           AND platform.collection_query_retry_intent.retry_run_id IS NULL
                          THEN 'pending'
                          ELSE platform.collection_query_retry_intent.state
                        END,
                        trigger_mode=CASE
                          WHEN platform.collection_query_retry_intent.state
                               IN ('pending','failed','exhausted')
                           AND platform.collection_query_retry_intent.retry_run_id IS NULL
                          THEN 'manual'
                          ELSE platform.collection_query_retry_intent.trigger_mode
                        END,
                        not_before=CASE
                          WHEN platform.collection_query_retry_intent.state
                               IN ('pending','failed','exhausted')
                           AND platform.collection_query_retry_intent.retry_run_id IS NULL
                          THEN :not_before
                          ELSE platform.collection_query_retry_intent.not_before
                        END,
                        priority=CASE
                          WHEN platform.collection_query_retry_intent.state
                               IN ('pending','failed','exhausted')
                           AND platform.collection_query_retry_intent.retry_run_id IS NULL
                          THEN :priority
                          ELSE platform.collection_query_retry_intent.priority
                        END,
                        updated_at=:now
                    """
                ),
                values,
            )
        else:
            session.execute(
                text(
                    """
                    INSERT INTO platform.collection_query_retry_intent
                      (id,pub_id,tenant_id,project_id,source_run_id,source_task_id,
                       business_key,capability_key,state,trigger_mode,priority,retry_depth,
                       max_auto_retries,not_before,last_error_code,created_by_pub_id,
                       created_at,updated_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:project_id,:source_run_id,:source_task_id,
                       :business_key,:capability_key,:state,:trigger_mode,:priority,:retry_depth,
                       :max_auto_retries,:not_before,:last_error_code,:created_by_pub_id,
                       :now,:now)
                    ON CONFLICT (source_task_id) DO NOTHING
                    """
                ),
                values,
            )
    return plans


def existing_retry_run(
    session: Session, *, run: CollectionRun, plans: list[RetryTaskPlan]
) -> CollectionRun | None:
    if not plans:
        return None
    rows = session.execute(
        text(
            """
            SELECT retry_run_id,count(*)::int AS linked_count
            FROM platform.collection_query_retry_intent
            WHERE source_run_id=:source_run_id
              AND source_task_id=ANY(:source_task_ids)
              AND state IN ('enqueued','succeeded')
              AND retry_run_id IS NOT NULL
            GROUP BY retry_run_id
            """
        ),
        {
            "source_run_id": run.id,
            "source_task_ids": [plan.source_task_id for plan in plans],
        },
    ).all()
    if len(rows) != 1 or int(rows[0][1]) != len(plans):
        return None
    return session.scalar(
        select(CollectionRun).where(
            CollectionRun.id == rows[0][0],
            CollectionRun.tenant_id == run.tenant_id,
            CollectionRun.project_id == run.project_id,
        )
    )


def runnable_retry_plans(
    session: Session,
    *,
    run: CollectionRun,
    plans: list[RetryTaskPlan],
) -> list[RetryTaskPlan]:
    """Return queue intents that still need workflow/outbox dispatch."""

    if not plans:
        return []
    runnable_ids = set(
        session.execute(
            text(
                """
                SELECT source_task_id
                FROM platform.collection_query_retry_intent
                WHERE source_run_id=:source_run_id
                  AND source_task_id=ANY(:source_task_ids)
                  AND state='pending' AND retry_run_id IS NULL
                """
            ),
            {
                "source_run_id": run.id,
                "source_task_ids": [plan.source_task_id for plan in plans],
            },
        ).scalars()
    )
    return [plan for plan in plans if plan.source_task_id in runnable_ids]


def attach_retry_run(
    session: Session,
    *,
    source_run: CollectionRun,
    retry_run: CollectionRun,
    plans: list[RetryTaskPlan],
    trigger: RetryTrigger,
    now: datetime | None = None,
) -> None:
    if not plans:
        raise ValueError("retry_has_no_failed_queries")
    current = now or datetime.now(UTC)
    attached_ids = set(
        session.execute(
            text(
                """
            UPDATE platform.collection_query_retry_intent
            SET retry_run_id=:retry_run_id,
                state='enqueued',
                trigger_mode=:trigger_mode,
                lease_owner=NULL,
                lease_token=NULL,
                lease_expires_at=NULL,
                updated_at=:now
            WHERE source_run_id=:source_run_id
              AND source_task_id=ANY(:source_task_ids)
              AND state='pending'
              AND retry_run_id IS NULL
            RETURNING source_task_id
            """
            ),
            {
                "retry_run_id": retry_run.id,
                "trigger_mode": trigger,
                "now": current,
                "source_run_id": source_run.id,
                "source_task_ids": [plan.source_task_id for plan in plans],
            },
        ).scalars()
    )
    if attached_ids != {plan.source_task_id for plan in plans}:
        raise ValueError("retry_intent_claim_conflict")


def mark_source_retry_outcome(
    session: Session,
    *,
    run: CollectionRun,
    business_key: str,
    succeeded: bool,
    error_code: str | None = None,
    now: datetime | None = None,
) -> None:
    if not getattr(run, "retry_of_run_pub_id", None):
        return
    current = now or datetime.now(UTC)
    session.execute(
        text(
            """
            UPDATE platform.collection_query_retry_intent
            SET state=:state,
                last_error_code=:last_error_code,
                updated_at=:now
            WHERE retry_run_id=:retry_run_id
              AND business_key=:business_key
              AND state='enqueued'
            """
        ),
        {
            "state": "succeeded" if succeeded else "failed",
            "last_error_code": "none" if succeeded else (error_code or "query_failed")[:120],
            "now": current,
            "retry_run_id": run.id,
            "business_key": business_key,
        },
    )


def record_query_attempt(
    session: Session,
    *,
    run: CollectionRun,
    task: CollectionTask,
    outcome: Literal["succeeded", "failed"],
    error_code: str | None,
    execution_status: str,
) -> None:
    depth = retry_depth(session, run)
    matrix = _matrix(task, require_complete=False)
    context = {
        "run_pub_id": run.pub_id,
        "run_source": getattr(run, "source", "legacy"),
        "retry_of_run_pub_id": getattr(run, "retry_of_run_pub_id", None),
        "business_key": task.business_key,
        **_query_identity(matrix["query"]),
        "model": matrix["model"],
        "region": matrix["region"],
        "mode": matrix["mode"],
        "adapter": matrix["adapter"],
        "capability_key": capability_key(matrix),
        "execution_status": execution_status,
    }
    stable_key = f"{task.id}:1"
    session.execute(
        text(
            """
            INSERT INTO platform.collection_query_execution_attempt
              (id,pub_id,tenant_id,project_id,run_id,task_id,business_key,
               attempt_ordinal,retry_depth,outcome,error_code,execution_context,created_at)
            VALUES
              (:id,:pub_id,:tenant_id,:project_id,:run_id,:task_id,:business_key,
               1,:retry_depth,:outcome,:error_code,CAST(:execution_context AS jsonb),:created_at)
            ON CONFLICT (task_id,attempt_ordinal) DO NOTHING
            """
        ),
        {
            "id": _stable_uuid("attempt", stable_key),
            "pub_id": _stable_pub_id("qea", stable_key),
            "tenant_id": run.tenant_id,
            "project_id": run.project_id,
            "run_id": run.id,
            "task_id": task.id,
            "business_key": task.business_key,
            "retry_depth": depth,
            "outcome": outcome,
            "error_code": error_code[:120] if error_code else None,
            "execution_context": _redacted_json(context),
            "created_at": datetime.now(UTC),
        },
    )


def record_query_failure_knowledge(
    session: Session,
    *,
    run: CollectionRun,
    task: CollectionTask,
    error_code: str,
    execution_status: str,
) -> None:
    matrix = _matrix(task, require_complete=False)
    context = {
        "run_pub_id": run.pub_id,
        "task_pub_id": task.pub_id,
        "business_key": task.business_key,
        **_query_identity(matrix["query"]),
        "model": matrix["model"],
        "region": matrix["region"],
        "mode": matrix["mode"],
        "adapter": matrix["adapter"],
        "capability_key": capability_key(matrix),
        "execution_status": execution_status,
        "error_code": error_code,
    }
    fingerprint = hashlib.sha256(
        _canonical_json(
            {
                "scope": "query",
                "error_code": error_code,
                "capability_key": context["capability_key"],
            }
        ).encode()
    ).hexdigest()
    stable_key = f"{run.id}:{task.id}:{fingerprint}"
    session.execute(
        text(
            """
            INSERT INTO platform.collection_failure_knowledge
              (id,pub_id,tenant_id,project_id,run_id,task_id,scope,error_code,
               fingerprint,redacted_context,occurrence_count,created_at)
            VALUES
              (:id,:pub_id,:tenant_id,:project_id,:run_id,:task_id,'query',:error_code,
               :fingerprint,CAST(:context AS jsonb),1,:created_at)
            ON CONFLICT (pub_id) DO NOTHING
            """
        ),
        {
            "id": _stable_uuid("query-failure", stable_key),
            "pub_id": _stable_pub_id("qfk", stable_key),
            "tenant_id": run.tenant_id,
            "project_id": run.project_id,
            "run_id": run.id,
            "task_id": task.id,
            "error_code": error_code[:120],
            "fingerprint": fingerprint,
            "context": _redacted_json(context),
            "created_at": datetime.now(UTC),
        },
    )


def record_run_failure_knowledge(
    session: Session,
    *,
    run: CollectionRun,
    error_code: str,
) -> None:
    tasks = list(
        session.scalars(
            select(CollectionTask)
            .where(CollectionTask.run_id == run.id)
            .order_by(CollectionTask.created_at, CollectionTask.pub_id)
        )
    )
    successful: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for task in tasks:
        matrix = _matrix(task, require_complete=False)
        item = {
            "task_pub_id": task.pub_id,
            "business_key": task.business_key,
            **_query_identity(matrix["query"]),
            "model": matrix["model"],
            "region": matrix["region"],
            "mode": matrix["mode"],
            "adapter": matrix["adapter"],
        }
        if task.state == "completed":
            successful.append(item)
        else:
            failed.append(
                {
                    **item,
                    "state": task.state,
                    "error_code": task.quality_state or "query_failed",
                }
            )
    context = {
        "run_pub_id": run.pub_id,
        "run_state": run.state,
        "run_error_code": error_code,
        "retry_of_run_pub_id": getattr(run, "retry_of_run_pub_id", None),
        "successful_queries": successful,
        "failed_queries": failed,
        "successful_query_count": len(successful),
        "failed_query_count": len(failed),
        "total_query_count": len(tasks),
    }
    fingerprint = hashlib.sha256(
        _canonical_json(
            {
                "scope": "run",
                "error_code": error_code,
                "failed": [
                    {
                        "business_key": item["business_key"],
                        "error_code": item["error_code"],
                    }
                    for item in failed
                ],
            }
        ).encode()
    ).hexdigest()
    stable_key = f"{run.id}:{fingerprint}"
    session.execute(
        text(
            """
            INSERT INTO platform.collection_failure_knowledge
              (id,pub_id,tenant_id,project_id,run_id,task_id,scope,error_code,
               fingerprint,redacted_context,occurrence_count,created_at)
            VALUES
              (:id,:pub_id,:tenant_id,:project_id,:run_id,NULL,'run',:error_code,
               :fingerprint,CAST(:context AS jsonb),1,:created_at)
            ON CONFLICT (pub_id) DO NOTHING
            """
        ),
        {
            "id": _stable_uuid("run-failure", stable_key),
            "pub_id": _stable_pub_id("rfk", stable_key),
            "tenant_id": run.tenant_id,
            "project_id": run.project_id,
            "run_id": run.id,
            "error_code": error_code[:120],
            "fingerprint": fingerprint,
            "context": _redacted_json(context),
            "created_at": datetime.now(UTC),
        },
    )


__all__ = [
    "RetryTaskPlan",
    "RetryTrigger",
    "attach_retry_run",
    "capability_key",
    "ensure_retry_intents",
    "existing_retry_run",
    "failed_task_plans",
    "mark_source_retry_outcome",
    "record_query_attempt",
    "record_query_failure_knowledge",
    "record_run_failure_knowledge",
    "retry_depth",
    "runnable_retry_plans",
]
