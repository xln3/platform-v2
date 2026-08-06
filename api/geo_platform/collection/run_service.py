from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from workflows.activities.collection import CollectionTaskInput

from ..config import get_settings
from ..projects.models import MonitoringConfig, MonitoringConfigVersion, Project
from ..tenancy.ids import new_pub_id
from .models import CollectionRun
from .workflow_outbox import enqueue_workflow_start

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
    for query in queries:
        for model in snapshot.get("models", []):
            for region in snapshot.get("regions", []):
                for mode in snapshot.get("modes", []):
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
            "activity_timeout_minutes": get_settings().collection_activity_timeout_minutes,
            "inter_task_delay_min_s": get_settings().collection_inter_task_delay_min_s,
            "inter_task_delay_max_s": get_settings().collection_inter_task_delay_max_s,
        },
    )
    return run
