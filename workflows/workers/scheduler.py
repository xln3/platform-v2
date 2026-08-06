from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import structlog
from geo_platform.collection.run_service import stage_collection_run
from geo_platform.config import get_settings
from geo_platform.logging import configure_logging
from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import Tenant
from geo_platform.tenancy.repository import set_tenant_context
from sqlalchemy import select, text


def _next_due(current: datetime, interval_minutes: int, now: datetime) -> datetime:
    candidate = current + timedelta(minutes=interval_minutes)
    if candidate > now:
        return candidate
    elapsed = int((now - current).total_seconds() // 60)
    steps = elapsed // interval_minutes + 1
    return current + timedelta(minutes=interval_minutes * steps)


def _tick_tenant(tenant_id: object, tenant_pub_id: str, *, batch_size: int = 25) -> int:
    processed = 0
    while processed < batch_size:
        with WorkerSessionLocal() as session:
            set_tenant_context(session, tenant_id=tenant_id, tenant_pub_id=tenant_pub_id)  # type: ignore[arg-type]
            now = datetime.now(UTC)
            row = (
                session.execute(
                    text(
                        """
                    SELECT schedule.id,schedule.pub_id,schedule.interval_minutes,
                           schedule.next_run_at,schedule.responsible_pub_id,
                           project.pub_id AS project_pub_id,
                           config.pub_id AS config_version_pub_id
                    FROM platform.monitoring_schedule schedule
                    JOIN platform.project project ON project.id=schedule.project_id
                    JOIN platform.monitoring_config_version config
                      ON config.id=schedule.config_version_id
                    WHERE schedule.tenant_id=:tenant_id AND schedule.state='active'
                      AND schedule.next_run_at <= :now
                    ORDER BY schedule.next_run_at,schedule.pub_id
                    FOR UPDATE OF schedule SKIP LOCKED
                    LIMIT 1
                    """
                    ),
                    {"tenant_id": tenant_id, "now": now},
                )
                .mappings()
                .first()
            )
            if row is None:
                return processed
            due_at = row["next_run_at"]
            digest = hashlib.sha256(
                f"{tenant_pub_id}:{row['pub_id']}:{due_at.isoformat()}".encode()
            ).hexdigest()
            idempotency_key = f"scheduled-{digest}"
            existing = (
                session.execute(
                    text(
                        """
                    SELECT pub_id,workflow_id FROM platform.collection_run
                    WHERE tenant_id=:tenant_id AND idempotency_key=:idempotency_key
                    """
                    ),
                    {"tenant_id": tenant_id, "idempotency_key": idempotency_key},
                )
                .mappings()
                .first()
            )
            run_pub_id: str
            if existing is None:
                run = stage_collection_run(
                    session,
                    tenant_id=tenant_id,  # type: ignore[arg-type]
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=str(row["project_pub_id"]),
                    config_version_pub_id=str(row["config_version_pub_id"]),
                    idempotency_key=idempotency_key,
                    initiated_by_pub_id=str(row["responsible_pub_id"]),
                    source="schedule",
                    schedule_pub_id=str(row["pub_id"]),
                )
                run_pub_id = run.pub_id
            else:
                run_pub_id = str(existing["pub_id"])
            next_run_at = _next_due(due_at, int(row["interval_minutes"]), now)
            session.execute(
                text(
                    """
                    UPDATE platform.monitoring_schedule
                    SET last_run_at=:due_at,last_run_pub_id=:run_pub_id,
                        next_run_at=:next_run_at,updated_at=now(),version=version+1
                    WHERE id=:schedule_id
                    """
                ),
                {
                    "due_at": due_at,
                    "run_pub_id": run_pub_id,
                    "next_run_at": next_run_at,
                    "schedule_id": row["id"],
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO platform.monitoring_schedule_event
                      (id,pub_id,tenant_id,schedule_id,event_type,actor_pub_id,data_json)
                    VALUES (
                      gen_random_uuid(),:pub_id,:tenant_id,:schedule_id,
                      'schedule.run_staged','system:scheduler',
                      jsonb_build_object('run_pub_id',:run_pub_id,'due_at',:due_at)::text
                    )
                    """
                ),
                {
                    "pub_id": new_pub_id("sce"),
                    "tenant_id": tenant_id,
                    "schedule_id": row["id"],
                    "run_pub_id": run_pub_id,
                    "due_at": due_at,
                },
            )
            session.commit()
            processed += 1
    return processed


def tick() -> int:
    with WorkerSessionLocal() as session:
        tenants = list(
            session.execute(select(Tenant.id, Tenant.pub_id).where(Tenant.state == "active")).all()
        )
    return sum(_tick_tenant(tenant_id, tenant_pub_id) for tenant_id, tenant_pub_id in tenants)


async def run_scheduler() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    log.info("schedule_worker_started")
    while True:
        try:
            processed = await asyncio.to_thread(tick)
            if processed:
                log.info("scheduled_runs_staged", count=processed)
        except Exception as exc:
            log.error("schedule_tick_failed", exception_type=type(exc).__name__)
        await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(run_scheduler())
    except KeyboardInterrupt:
        pass
