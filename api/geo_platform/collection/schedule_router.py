# ruff: noqa: B008
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..identity.policy import Principal, get_principal
from ..pagination import decode_keyset_cursor, encode_keyset_cursor, set_cursor_headers
from ..projects.models import MonitoringConfig, MonitoringConfigVersion, Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from .run_service import stage_collection_run

router = APIRouter(prefix="/api/v2/schedules", tags=["schedules"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleCreate(StrictModel):
    project_pub_id: str = Field(min_length=5, max_length=30)
    config_version_pub_id: str = Field(min_length=5, max_length=30)
    interval_minutes: int = Field(ge=15, le=525_600)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    next_run_at: datetime
    responsible_pub_id: str = Field(min_length=5, max_length=30)

    @field_validator("next_run_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone_required")
        return value


class ScheduleStateChange(StrictModel):
    state: Literal["active", "paused", "archived"]
    expected_version: int = Field(ge=1)
    next_run_at: datetime | None = None


class ScheduleView(StrictModel):
    pub_id: str
    project_pub_id: str
    config_version_pub_id: str
    interval_minutes: int
    timezone: str
    state: Literal["active", "paused", "archived"]
    next_run_at: datetime
    last_run_at: datetime | None
    last_run_pub_id: str | None
    responsible_pub_id: str
    created_by_pub_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class ScheduleEventView(StrictModel):
    pub_id: str
    schedule_pub_id: str
    event_type: str
    actor_pub_id: str
    data: dict[str, object]
    occurred_at: datetime


def _schedule_event(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    actor_pub_id: str,
    event_type: str,
    data: dict[str, object] | None = None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO platform.monitoring_schedule_event
              (id,pub_id,tenant_id,schedule_id,event_type,actor_pub_id,data_json)
            VALUES (:id,:pub_id,:tenant_id,:schedule_id,:event_type,:actor_pub_id,:data_json)
            """
        ),
        {
            "id": uuid.uuid4(),
            "pub_id": new_pub_id("sce"),
            "tenant_id": tenant_id,
            "schedule_id": schedule_id,
            "event_type": event_type,
            "actor_pub_id": actor_pub_id,
            "data_json": json.dumps(data or {}, ensure_ascii=False, sort_keys=True),
        },
    )


def _view(row: dict[str, object]) -> ScheduleView:
    return ScheduleView.model_validate(row)


@router.get(
    "",
    response_model=list[ScheduleView],
    responses={
        200: {
            "headers": {
                "X-Next-Cursor": {"schema": {"type": "string"}},
                "X-Has-More": {"schema": {"type": "boolean"}},
            }
        }
    },
)
def list_schedules(
    response: Response,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ScheduleView]:
    principal.require("schedule:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    filters: dict[str, str | None] = {}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="schedules",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    cursor_clause = ""
    params: dict[str, object] = {"tenant_id": repository.tenant.id, "limit": limit + 1}
    if anchor is not None:
        cursor_clause = """
          AND (schedule.created_at < :cursor_created_at
               OR (schedule.created_at = :cursor_created_at
                   AND schedule.pub_id < :cursor_pub_id))
        """
        params.update(cursor_created_at=anchor.created_at, cursor_pub_id=anchor.pub_id)
    rows = (
        session.execute(
            text(
                f"""
            SELECT schedule.pub_id,project.pub_id AS project_pub_id,
                   config.pub_id AS config_version_pub_id,
                   schedule.interval_minutes,schedule.timezone,schedule.state,
                   schedule.next_run_at,schedule.last_run_at,schedule.last_run_pub_id,
                   schedule.responsible_pub_id,schedule.created_by_pub_id,
                   schedule.version,schedule.created_at,schedule.updated_at
            FROM platform.monitoring_schedule schedule
            JOIN platform.project project ON project.id=schedule.project_id
            JOIN platform.monitoring_config_version config
              ON config.id=schedule.config_version_id
            WHERE schedule.tenant_id=:tenant_id
            {cursor_clause}
            ORDER BY schedule.created_at DESC,schedule.pub_id DESC
            LIMIT :limit
            """
            ),
            params,
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="schedules",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last["created_at"],
            pub_id=str(last["pub_id"]),
        )
    set_cursor_headers(response, next_cursor=next_cursor, has_more=has_more)
    return [_view(dict(row)) for row in visible]


@router.post("", response_model=ScheduleView, status_code=201)
def create_schedule(
    body: ScheduleCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ScheduleView:
    principal.require("schedule:manage")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == repository.tenant.id,
            Project.pub_id == body.project_pub_id,
        )
    )
    config = session.scalar(
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == repository.tenant.id,
            MonitoringConfigVersion.pub_id == body.config_version_pub_id,
            MonitoringConfigVersion.frozen_at.is_not(None),
            MonitoringConfig.project_id == (project.id if project is not None else None),
        )
    )
    if project is None or config is None:
        raise HTTPException(status_code=404, detail={"code": "project_or_config_not_found"})
    schedule_id = uuid.uuid4()
    schedule_pub_id = new_pub_id("sch")
    session.execute(
        text(
            """
            INSERT INTO platform.monitoring_schedule
              (id,pub_id,tenant_id,project_id,config_version_id,interval_minutes,
               timezone,next_run_at,responsible_pub_id,created_by_pub_id)
            VALUES (:id,:pub_id,:tenant_id,:project_id,:config_version_id,:interval_minutes,
                    :timezone,:next_run_at,:responsible_pub_id,:created_by_pub_id)
            """
        ),
        {
            "id": schedule_id,
            "pub_id": schedule_pub_id,
            "tenant_id": repository.tenant.id,
            "project_id": project.id,
            "config_version_id": config.id,
            "interval_minutes": body.interval_minutes,
            "timezone": body.timezone,
            "next_run_at": body.next_run_at,
            "responsible_pub_id": body.responsible_pub_id,
            "created_by_pub_id": principal.actor_pub_id,
        },
    )
    _schedule_event(
        session,
        tenant_id=repository.tenant.id,
        schedule_id=schedule_id,
        actor_pub_id=principal.actor_pub_id,
        event_type="schedule.created",
        data={"next_run_at": body.next_run_at.isoformat()},
    )
    session.commit()
    return list_schedules(
        response=Response(), cursor=None, limit=100, principal=principal, session=session
    )[0]


@router.patch("/{schedule_pub_id}", response_model=ScheduleView)
def update_schedule_state(
    schedule_pub_id: str,
    body: ScheduleStateChange,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ScheduleView:
    principal.require("schedule:manage")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = (
        session.execute(
            text(
                """
            SELECT id,state,version,next_run_at FROM platform.monitoring_schedule
            WHERE tenant_id=:tenant_id AND pub_id=:pub_id FOR UPDATE
            """
            ),
            {"tenant_id": repository.tenant.id, "pub_id": schedule_pub_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "schedule_not_found"})
    if int(row["version"]) != body.expected_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict"})
    next_run_at = body.next_run_at or row["next_run_at"]
    if body.state == "active" and next_run_at <= datetime.now(UTC):
        next_run_at = datetime.now(UTC) + timedelta(minutes=1)
    session.execute(
        text(
            """
            UPDATE platform.monitoring_schedule
            SET state=:state,next_run_at=:next_run_at,version=version+1,updated_at=now()
            WHERE tenant_id=:tenant_id AND pub_id=:pub_id
            """
        ),
        {
            "state": body.state,
            "next_run_at": next_run_at,
            "tenant_id": repository.tenant.id,
            "pub_id": schedule_pub_id,
        },
    )
    _schedule_event(
        session,
        tenant_id=repository.tenant.id,
        schedule_id=row["id"],
        actor_pub_id=principal.actor_pub_id,
        event_type=f"schedule.{body.state}",
        data={"from_state": row["state"], "next_run_at": next_run_at.isoformat()},
    )
    session.commit()
    matches = [
        item
        for item in list_schedules(
            response=Response(), cursor=None, limit=100, principal=principal, session=session
        )
        if item.pub_id == schedule_pub_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail={"code": "schedule_not_found"})
    return matches[0]


@router.post("/{schedule_pub_id}/run-now", response_model=dict[str, str], status_code=202)
def run_schedule_now(
    schedule_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("schedule:manage")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = (
        session.execute(
            text(
                """
            SELECT schedule.id,schedule.state,project.pub_id AS project_pub_id,
                   config.pub_id AS config_version_pub_id
            FROM platform.monitoring_schedule schedule
            JOIN platform.project project ON project.id=schedule.project_id
            JOIN platform.monitoring_config_version config
              ON config.id=schedule.config_version_id
            WHERE schedule.tenant_id=:tenant_id AND schedule.pub_id=:pub_id
            FOR UPDATE OF schedule
            """
            ),
            {"tenant_id": repository.tenant.id, "pub_id": schedule_pub_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "schedule_not_found"})
    if row["state"] == "archived":
        raise HTTPException(status_code=409, detail={"code": "schedule_archived"})
    idempotency_key = f"schedule-now-{schedule_pub_id}-{uuid.uuid4().hex}"
    run = stage_collection_run(
        session,
        tenant_id=repository.tenant.id,
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=str(row["project_pub_id"]),
        config_version_pub_id=str(row["config_version_pub_id"]),
        idempotency_key=idempotency_key,
        initiated_by_pub_id=principal.actor_pub_id,
        source="schedule",
        schedule_pub_id=schedule_pub_id,
    )
    session.execute(
        text(
            """
            UPDATE platform.monitoring_schedule
            SET last_run_at=now(),last_run_pub_id=:run_pub_id,updated_at=now()
            WHERE id=:schedule_id
            """
        ),
        {"run_pub_id": run.pub_id, "schedule_id": row["id"]},
    )
    _schedule_event(
        session,
        tenant_id=repository.tenant.id,
        schedule_id=row["id"],
        actor_pub_id=principal.actor_pub_id,
        event_type="schedule.run_requested",
        data={"run_pub_id": run.pub_id},
    )
    session.commit()
    return {
        "schedule_pub_id": schedule_pub_id,
        "run_pub_id": run.pub_id,
        "workflow_id": run.workflow_id,
    }


@router.get(
    "/{schedule_pub_id}/events",
    response_model=list[ScheduleEventView],
    responses={
        200: {
            "headers": {
                "X-Next-Cursor": {"schema": {"type": "string"}},
                "X-Has-More": {"schema": {"type": "boolean"}},
            }
        }
    },
)
def list_schedule_events(
    schedule_pub_id: str,
    response: Response,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ScheduleEventView]:
    principal.require("schedule:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    filters = {"schedule_pub_id": schedule_pub_id}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="schedule-events",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    cursor_clause = ""
    params: dict[str, object] = {
        "tenant_id": repository.tenant.id,
        "schedule_pub_id": schedule_pub_id,
        "limit": limit + 1,
    }
    if anchor is not None:
        cursor_clause = """
          AND (event.occurred_at < :cursor_occurred_at
               OR (event.occurred_at = :cursor_occurred_at
                   AND event.pub_id < :cursor_pub_id))
        """
        params.update(cursor_occurred_at=anchor.created_at, cursor_pub_id=anchor.pub_id)
    rows = (
        session.execute(
            text(
                f"""
            SELECT event.pub_id,schedule.pub_id AS schedule_pub_id,event.event_type,
                   event.actor_pub_id,event.data_json,event.occurred_at
            FROM platform.monitoring_schedule_event event
            JOIN platform.monitoring_schedule schedule ON schedule.id=event.schedule_id
            WHERE event.tenant_id=:tenant_id AND schedule.pub_id=:schedule_pub_id
            {cursor_clause}
            ORDER BY event.occurred_at DESC,event.pub_id DESC LIMIT :limit
            """
            ),
            params,
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_keyset_cursor(
            kind="schedule-events",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last["occurred_at"],
            pub_id=str(last["pub_id"]),
        )
    set_cursor_headers(response, next_cursor=next_cursor, has_more=has_more)
    return [
        ScheduleEventView(
            pub_id=str(row["pub_id"]),
            schedule_pub_id=str(row["schedule_pub_id"]),
            event_type=str(row["event_type"]),
            actor_pub_id=str(row["actor_pub_id"]),
            data=json.loads(str(row["data_json"])),
            occurred_at=row["occurred_at"],
        )
        for row in visible
    ]
