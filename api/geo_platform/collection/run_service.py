from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from workflows.activities.collection import PLATFORM_MODE_CAPABILITIES, CollectionTaskInput

from ..config import get_settings
from ..projects.models import MonitoringConfig, MonitoringConfigVersion, Project
from ..tenancy.ids import new_pub_id
from .models import CollectionRun, CollectionTask
from .retry_queue import (
    RetryTrigger,
    attach_retry_run,
    ensure_retry_intents,
    existing_retry_run,
    runnable_retry_plans,
)
from .workflow_outbox import enqueue_workflow_start

log = structlog.get_logger()

RunSource = Literal["manual", "schedule", "retry", "training"]


def _task_matrix(config: MonitoringConfigVersion) -> list[CollectionTaskInput]:
    snapshot = json.loads(config.snapshot_json)
    tasks: list[CollectionTaskInput] = []
    queries = [
        item["text"]
        for group in snapshot.get("query_groups", [])
        for item in group.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
    ]
    dropped: set[tuple[str, str]] = set()
    for query in queries:
        for model in snapshot.get("models", []):
            capabilities = PLATFORM_MODE_CAPABILITIES.get(model)
            for region in snapshot.get("regions", []):
                for mode in snapshot.get("modes", []):
                    if capabilities is not None and mode not in capabilities:
                        # 平台不支持该 mode（如 yiyan×deep_think）→ 矩阵剔除，
                        # 不产出必败任务；剔除对全量记录在案（非静默）。
                        dropped.add((model, mode))
                        continue
                    business_key = hashlib.sha256(
                        f"{config.snapshot_hash}|{query}|{model}|{region}|{mode}".encode()
                    ).hexdigest()
                    tasks.append(
                        CollectionTaskInput(
                            business_key=business_key,
                            query=query,
                            model=model,
                            region=region,
                            mode=mode,
                            adapter=model,
                        )
                    )
    if dropped:
        log.warning(
            "collection_matrix_mode_filtered",
            dropped=sorted(f"{model}:{mode}" for model, mode in dropped),
            config_version_pub_id=config.pub_id,
        )
    if not tasks:
        raise ValueError("collection_matrix_empty")
    if len(tasks) > 10_000:
        raise ValueError("collection_matrix_too_large")
    return tasks


def stage_collection_run(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    tenant_pub_id: str,
    project_pub_id: str,
    config_version_pub_id: str,
    idempotency_key: str,
    initiated_by_pub_id: str,
    source: RunSource,
    requires_intervention: bool = False,
    account_pub_id: str | None = None,
    schedule_pub_id: str | None = None,
    retry_of_run_pub_id: str | None = None,
    retry_trigger: RetryTrigger = "manual",
    retry_task_pub_ids: list[str] | None = None,
) -> CollectionRun:
    project = session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.pub_id == project_pub_id)
    )
    config = session.scalar(
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == tenant_id,
            MonitoringConfigVersion.pub_id == config_version_pub_id,
            MonitoringConfigVersion.frozen_at.is_not(None),
            MonitoringConfig.project_id == (project.id if project is not None else None),
        )
    )
    if project is None or config is None:
        raise LookupError("project_or_config_not_found")
    retry_source: CollectionRun | None = None
    retry_plans = []
    if retry_of_run_pub_id is not None:
        if source != "retry":
            raise ValueError("retry_source_requires_retry_run")
        retry_source = session.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.tenant_id == tenant_id,
                CollectionRun.project_id == project.id,
                CollectionRun.pub_id == retry_of_run_pub_id,
                CollectionRun.config_version_id == config.id,
            )
            .with_for_update()
        )
        if retry_source is None:
            raise LookupError("retry_source_run_not_found")
        retry_plans = ensure_retry_intents(
            session,
            run=retry_source,
            trigger=retry_trigger,
            created_by_pub_id=initiated_by_pub_id,
            task_pub_ids=retry_task_pub_ids,
        )
        if not retry_plans:
            raise ValueError("retry_has_no_failed_queries")
        already_enqueued = existing_retry_run(session, run=retry_source, plans=retry_plans)
        if already_enqueued is not None:
            return already_enqueued
        runnable_plans = runnable_retry_plans(
            session,
            run=retry_source,
            plans=retry_plans,
        )
        # An operator-supplied task set is one exact idempotent command. Never
        # silently create a retry child for only the still-runnable subset: a
        # replay would otherwise observe a different selection than the command
        # that originally claimed the idempotency key. Automatic reconciliation
        # deliberately remains subset-based so it can skip queries already
        # claimed by an earlier manual command.
        if (
            retry_trigger == "manual"
            and retry_task_pub_ids is not None
            and len(runnable_plans) != len(retry_plans)
        ):
            raise ValueError("retry_task_selection_not_dispatchable")
        if (
            retry_trigger == "automatic"
            and not runnable_plans
            and all(plan.retry_depth > 2 for plan in retry_plans)
        ):
            raise ValueError("retry_auto_exhausted")
        retry_plans = [
            plan for plan in runnable_plans if retry_trigger == "manual" or plan.retry_depth <= 2
        ]
        if not retry_plans:
            raise ValueError("retry_intent_not_dispatchable")
        tasks = [
            CollectionTaskInput(
                business_key=plan.business_key,
                query=plan.query,
                model=plan.model,
                region=plan.region,
                mode=plan.mode,
                adapter=plan.adapter,
            )
            for plan in retry_plans
        ]
    else:
        tasks = _task_matrix(config)
    run_pub_id = new_pub_id("run")
    workflow_id = f"geo-collection/{tenant_pub_id}/{project.pub_id}/{run_pub_id}"
    run = CollectionRun(
        pub_id=run_pub_id,
        tenant_id=tenant_id,
        project_id=project.id,
        config_version_id=config.id,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id,
        state="starting",
        total_tasks=len(tasks),
        source=source,
        schedule_pub_id=schedule_pub_id,
        retry_of_run_pub_id=retry_of_run_pub_id,
        initiated_by_pub_id=initiated_by_pub_id,
    )
    session.add(run)
    session.flush()
    # Plan every query before Temporal starts.  A workflow/process failure can
    # therefore mark the unexecuted remainder failed and retry exactly those
    # business keys; no query disappears merely because no result was persisted.
    for task in tasks:
        session.add(
            CollectionTask(
                pub_id=new_pub_id("ans"),
                tenant_id=tenant_id,
                run_id=run.id,
                business_key=task.business_key,
                matrix_json=json.dumps(
                    {
                        "query": task.query,
                        "model": task.model,
                        "region": task.region,
                        "mode": task.mode,
                        "adapter": task.adapter,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                state="pending",
                attempt_count=0,
                answer_text=None,
                citations_json="[]",
                evidence_json="[]",
                search_queries_json="[]",
            )
        )
    session.flush()
    if retry_source is not None:
        attach_retry_run(
            session,
            source_run=retry_source,
            retry_run=run,
            plans=retry_plans,
            trigger=retry_trigger,
        )
    retry_not_before = (
        max(plan.not_before for plan in retry_plans).isoformat() if retry_plans else None
    )
    enqueue_workflow_start(
        session,
        tenant_pub_id=tenant_pub_id,
        workflow_type="geo_collection",
        workflow_id=workflow_id,
        task_queue=get_settings().temporal_task_queue,
        payload={
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project.pub_id,
            "run_pub_id": run_pub_id,
            "config_version_pub_id": config.pub_id,
            "tasks": [
                {
                    "business_key": task.business_key,
                    "query": task.query,
                    "model": task.model,
                    "region": task.region,
                    "mode": task.mode,
                    "adapter": task.adapter,
                    "fail_until_attempt": task.fail_until_attempt,
                }
                for task in tasks
            ],
            "requires_intervention": requires_intervention,
            "account_pub_id": account_pub_id,
            "source": source,
            "schedule_pub_id": schedule_pub_id,
            "retry_of_run_pub_id": retry_of_run_pub_id,
            "retry_trigger": retry_trigger if retry_of_run_pub_id else None,
            "retry_not_before": retry_not_before,
            "retry_depth": retry_plans[0].retry_depth if retry_plans else 0,
            "retry_capability_keys": sorted({plan.capability_key for plan in retry_plans}),
            "activity_timeout_minutes": get_settings().collection_activity_timeout_minutes,
            "inter_task_delay_min_s": get_settings().collection_inter_task_delay_min_s,
            "inter_task_delay_max_s": get_settings().collection_inter_task_delay_max_s,
        },
    )
    return run
