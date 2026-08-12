from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from opentelemetry import context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from workflows.activities.collection import CollectionTaskInput
from workflows.activities.own_content_disparagement import OwnContentDisparagementInput
from workflows.definitions.collection import GeoCollectionInput, GeoCollectionWorkflow
from workflows.definitions.own_content import OwnContentDisparagementWorkflow
from workflows.definitions.post_analysis import PostAnalysisInput, PostAnalysisWorkflow
from workflows.definitions.s02 import AnswerAnalysisWorkflow, ReportProductionWorkflow
from workflows.definitions.session import AccountRevocationWorkflow, RevocationInput

TRACE_PROPAGATOR = TraceContextTextMapPropagator()


class WorkflowSignalConflictError(RuntimeError):
    pass


def workflow_signal_hashes(
    *, workflow_id: str, signal_name: str, args: list[object], idempotency_key: str
) -> tuple[str, str]:
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    contract = json.dumps(
        {
            "workflow_id": workflow_id,
            "signal_name": signal_name,
            "args": args,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return key_hash, hashlib.sha256(contract.encode()).hexdigest()


def workflow_signal_replayed(
    session: Session,
    *,
    tenant_pub_id: str,
    workflow_id: str,
    signal_name: str,
    args: list[object],
    idempotency_key: str,
) -> bool:
    key_hash, contract_hash = workflow_signal_hashes(
        workflow_id=workflow_id,
        signal_name=signal_name,
        args=args,
        idempotency_key=idempotency_key,
    )
    persisted = session.execute(
        text(
            """
            SELECT contract_hash
            FROM integration.workflow_signal_command
            WHERE tenant_pub_id=:tenant_pub_id
              AND idempotency_key_hash=:key_hash
            """
        ),
        {"tenant_pub_id": tenant_pub_id, "key_hash": key_hash},
    ).scalar_one_or_none()
    if persisted is None:
        return False
    if persisted != contract_hash:
        raise WorkflowSignalConflictError("workflow_signal_idempotency_conflict")
    return True


def enqueue_workflow_start(
    session: Session,
    *,
    tenant_pub_id: str,
    workflow_type: str,
    workflow_id: str,
    task_queue: str,
    payload: dict[str, object],
) -> None:
    payload_tenant = payload.get("tenant_pub_id")
    if payload_tenant != tenant_pub_id:
        raise ValueError("workflow_start_tenant_mismatch")
    # Persist only W3C traceparent/tracestate. The global propagator may include
    # baggage, which is caller-controlled and can contain sensitive values.
    trace_context: dict[str, str] = {}
    TRACE_PROPAGATOR.inject(trace_context)
    session.execute(
        text(
            """
            INSERT INTO integration.workflow_start_command (
              command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
              trace_context
            ) VALUES (
              :command_id,:tenant_pub_id,:workflow_type,:workflow_id,
              :task_queue,CAST(:payload AS jsonb),CAST(:trace_context AS jsonb)
            )
            """
        ),
        {
            "command_id": uuid.uuid4(),
            "tenant_pub_id": tenant_pub_id,
            "workflow_type": workflow_type,
            "workflow_id": workflow_id,
            "task_queue": task_queue,
            "payload": json.dumps(payload, separators=(",", ":")),
            "trace_context": json.dumps(trace_context, separators=(",", ":")),
        },
    )


def enqueue_workflow_signal(
    session: Session,
    *,
    tenant_pub_id: str,
    workflow_id: str,
    signal_name: str,
    args: list[object],
    idempotency_key: str,
) -> None:
    trace_context: dict[str, str] = {}
    TRACE_PROPAGATOR.inject(trace_context)
    key_hash, contract_hash = workflow_signal_hashes(
        workflow_id=workflow_id,
        signal_name=signal_name,
        args=args,
        idempotency_key=idempotency_key,
    )
    persisted_contract = session.execute(
        text(
            """
            INSERT INTO integration.workflow_signal_command (
              command_id,tenant_pub_id,workflow_id,signal_name,args,trace_context,
              idempotency_key_hash,contract_hash
            ) VALUES (
              :command_id,:tenant_pub_id,:workflow_id,:signal_name,
              CAST(:args AS jsonb),CAST(:trace_context AS jsonb),
              :key_hash,:contract_hash
            )
            ON CONFLICT (tenant_pub_id,idempotency_key_hash)
            DO UPDATE SET updated_at=integration.workflow_signal_command.updated_at
            RETURNING integration.workflow_signal_command.contract_hash
            """
        ),
        {
            "command_id": uuid.uuid4(),
            "tenant_pub_id": tenant_pub_id,
            "workflow_id": workflow_id,
            "signal_name": signal_name,
            "args": json.dumps(args, separators=(",", ":")),
            "trace_context": json.dumps(trace_context, separators=(",", ":")),
            "key_hash": key_hash,
            "contract_hash": contract_hash,
        },
    ).scalar_one()
    if persisted_contract != contract_hash:
        raise WorkflowSignalConflictError("workflow_signal_idempotency_conflict")


@dataclass(frozen=True)
class WorkflowStartCommand:
    command_id: str
    tenant_pub_id: str
    workflow_type: str
    workflow_id: str
    task_queue: str
    payload: dict[str, Any]
    trace_context: dict[str, str]


@dataclass(frozen=True)
class WorkflowSignalCommand:
    command_id: str
    tenant_pub_id: str
    workflow_id: str
    signal_name: str
    args: list[Any]
    trace_context: dict[str, str]


class WorkflowStartOutbox:
    def __init__(self, *, dsn: str, temporal: Client) -> None:
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self.temporal = temporal

    def claim(self, workflow_id: str | None = None) -> WorkflowStartCommand | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE integration.workflow_start_command command
                SET state='dispatching',
                    attempts=attempts+1,
                    claimed_at=now(),
                    updated_at=now()
                WHERE command.id = (
                  SELECT candidate.id
                  FROM integration.workflow_start_command candidate
                  WHERE (
                    candidate.state='pending'
                    OR (
                       candidate.state='dispatching'
                       AND candidate.claimed_at < now() - interval '30 seconds'
                     )
                  )
                  AND (%s::text IS NULL OR candidate.workflow_id=%s)
                  ORDER BY candidate.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                RETURNING command_id::text,tenant_pub_id,workflow_type,
                          workflow_id,task_queue,payload,trace_context
                """,
                (workflow_id, workflow_id),
            ).fetchone()
        if row is None:
            return None
        command = WorkflowStartCommand(**row)
        self._assert_start_tenant(command)
        return command

    @staticmethod
    def _assert_start_tenant(command: WorkflowStartCommand) -> None:
        if command.payload.get("tenant_pub_id") != command.tenant_pub_id:
            raise RuntimeError("workflow_start_tenant_mismatch")

    def started(self, command: WorkflowStartCommand, temporal_run_id: str | None) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_pub_id', %s, true)",
                (command.tenant_pub_id,),
            )
            tenant = connection.execute(
                "SELECT id::text FROM platform.tenant WHERE pub_id=%s",
                (command.tenant_pub_id,),
            ).fetchone()
            if tenant is None:
                raise RuntimeError("workflow_start_tenant_not_found")
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (tenant[0],),
            )
            if command.workflow_type == "geo_collection":
                connection.execute(
                    """
                    UPDATE platform.collection_run
                    SET state=CASE WHEN state IN ('starting','start_failed')
                                   THEN 'running' ELSE state END,
                        temporal_run_id=COALESCE(temporal_run_id, %s),
                        updated_at=now()
                    WHERE workflow_id=%s
                    """,
                    (temporal_run_id, command.workflow_id),
                )
            elif command.workflow_type == "account_revocation":
                connection.execute(
                    """
                    UPDATE platform.revocation_request
                    SET state=CASE WHEN state='starting' THEN 'running' ELSE state END,
                        updated_at=now()
                    WHERE workflow_id=%s
                    """,
                    (command.workflow_id,),
                )
            elif command.workflow_type == "formal_report_production":
                connection.execute(
                    """
                    UPDATE reporting.formal_report_production
                    SET status=CASE WHEN status='queued' THEN 'running' ELSE status END,
                        updated_at=now()
                    WHERE tenant_pub_id=%s AND workflow_id=%s
                    """,
                    (command.tenant_pub_id, command.workflow_id),
                )
            connection.execute(
                """
                UPDATE integration.workflow_start_command
                SET state='started', temporal_run_id=%s, started_at=now(),
                    claimed_at=NULL, last_error_code=NULL, updated_at=now()
                WHERE command_id=%s::uuid
                """,
                (temporal_run_id, command.command_id),
            )

    def failed(self, command: WorkflowStartCommand, error: BaseException) -> None:
        # Exception messages may contain addresses or credentials. Persist only
        # the bounded class name and retain dispatching for delayed reclaim.
        error_code = type(error).__name__[:120]
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                UPDATE integration.workflow_start_command
                SET last_error_code=%s, updated_at=now()
                WHERE command_id=%s::uuid
                """,
                (error_code, command.command_id),
            )

    def claim_signal(self, workflow_id: str | None = None) -> WorkflowSignalCommand | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE integration.workflow_signal_command command
                SET state='dispatching',attempts=attempts+1,
                    claimed_at=now(),updated_at=now()
                WHERE command.id = (
                  SELECT candidate.id
                  FROM integration.workflow_signal_command candidate
                  WHERE (
                    candidate.state='pending'
                    OR (
                      candidate.state='dispatching'
                      AND candidate.claimed_at < now()-interval '30 seconds'
                    )
                  )
                  AND (%s::text IS NULL OR candidate.workflow_id=%s)
                  AND NOT EXISTS (
                    SELECT 1 FROM integration.workflow_start_command starter
                    WHERE starter.workflow_id=candidate.workflow_id
                      AND starter.state<>'started'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM integration.workflow_signal_command prior
                    WHERE prior.workflow_id=candidate.workflow_id
                      AND prior.id<candidate.id
                      AND prior.state IN ('pending','dispatching')
                  )
                  ORDER BY candidate.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                RETURNING command_id::text,tenant_pub_id,workflow_id,
                          signal_name,args,trace_context
                """,
                (workflow_id, workflow_id),
            ).fetchone()
        if row is None:
            return None
        return WorkflowSignalCommand(**row)

    def signal_delivered(
        self, command: WorkflowSignalCommand, *, workflow_not_found: bool = False
    ) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                UPDATE integration.workflow_signal_command
                SET state=%s,delivered_at=now(),claimed_at=NULL,
                    last_error_code=NULL,updated_at=now()
                WHERE command_id=%s::uuid
                """,
                (
                    "workflow_not_found" if workflow_not_found else "delivered",
                    command.command_id,
                ),
            )

    def signal_failed(self, command: WorkflowSignalCommand, error: BaseException) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                UPDATE integration.workflow_signal_command
                SET last_error_code=%s,updated_at=now()
                WHERE command_id=%s::uuid
                """,
                (type(error).__name__[:120], command.command_id),
            )

    async def dispatch_signal_one(self, workflow_id: str | None = None) -> bool:
        command = self.claim_signal(workflow_id)
        if command is None:
            return False
        parent_context = TRACE_PROPAGATOR.extract(carrier=command.trace_context)
        context_token = context.attach(parent_context)
        try:
            try:
                await self.temporal.get_workflow_handle(command.workflow_id).signal(
                    command.signal_name, args=command.args
                )
            except RPCError as error:
                if error.status == RPCStatusCode.NOT_FOUND:
                    self.signal_delivered(command, workflow_not_found=True)
                    return True
                raise
            self.signal_delivered(command)
            return True
        except BaseException as error:
            self.signal_failed(command, error)
            raise
        finally:
            context.detach(context_token)

    def claim_reconciliation(self, workflow_id: str | None = None) -> WorkflowStartCommand | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE integration.workflow_start_command command
                SET last_reconciled_at=now(),updated_at=now()
                WHERE command.id = (
                  SELECT candidate.id
                  FROM integration.workflow_start_command candidate
                  WHERE candidate.state='started'
                    AND candidate.workflow_type IN (
                      'geo_collection','geo_collection_observation','account_revocation',
                      'answer_analysis','post_analysis','formal_report_production'
                    )
                    AND candidate.terminal_status IS NULL
                    AND (%s::text IS NULL OR candidate.workflow_id=%s)
                    AND (
                      candidate.last_reconciled_at IS NULL
                      OR candidate.last_reconciled_at < now()-interval '30 seconds'
                    )
                  ORDER BY candidate.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                RETURNING command_id::text,tenant_pub_id,workflow_type,
                          workflow_id,task_queue,payload,trace_context
                """,
                (workflow_id, workflow_id),
            ).fetchone()
        if row is None:
            return None
        return WorkflowStartCommand(**row)

    def reconciled_terminal(self, command: WorkflowStartCommand, temporal_status: str) -> None:
        run_state, error_code = {
            "COMPLETED": ("completed", None),
            "FAILED": ("failed", "temporal_failed"),
            "CANCELED": ("cancelled", "temporal_cancelled"),
            "TERMINATED": ("failed", "temporal_terminated"),
            "TIMED_OUT": ("failed", "temporal_timed_out"),
            "NOT_FOUND": ("failed", "temporal_history_missing"),
        }[temporal_status]
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_pub_id', %s, true)",
                (command.tenant_pub_id,),
            )
            tenant = connection.execute(
                "SELECT id::text FROM platform.tenant WHERE pub_id=%s",
                (command.tenant_pub_id,),
            ).fetchone()
            if tenant is None:
                raise RuntimeError("workflow_reconciliation_tenant_not_found")
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (tenant[0],),
            )
            if command.workflow_type in {
                "geo_collection",
                "geo_collection_observation",
            }:
                connection.execute(
                    """
                    UPDATE platform.collection_run
                    SET state=%s,error_code=%s,updated_at=now()
                    WHERE workflow_id=%s
                      AND state NOT IN ('completed','completed_with_failures',
                                        'cancelled','failed','skipped')
                    """,
                    (run_state, error_code, command.workflow_id),
                )
            elif command.workflow_type == "account_revocation":
                connection.execute(
                    """
                    UPDATE platform.revocation_request
                    SET state=%s,error_code=%s,updated_at=now()
                    WHERE workflow_id=%s
                      AND state NOT IN ('completed','cancelled','failed')
                    """,
                    (run_state, error_code, command.workflow_id),
                )
            elif command.workflow_type == "post_analysis":
                # 终态回写：workflow 内 finalize 已落 completed/partial 时不覆盖；
                # 只对未完成（queued/running）任务兜底收敛
                connection.execute(
                    """
                    UPDATE platform.post_analysis_task
                    SET status=%s,error=%s,updated_at=now()
                    WHERE workflow_id=%s
                      AND status NOT IN ('completed','partial','failed')
                    """,
                    (run_state, error_code, command.workflow_id),
                )
                if temporal_status != "COMPLETED":
                    # 非自然终态（FAILED/CANCELED/TERMINATED/TIMED_OUT/NOT_FOUND）：
                    # finalize 可能从未运行，清扫仍卡中间态的 item —— 按阶段如实落
                    # 既有失败词表（不新增枚举）；终态 item 绝不覆盖；pending=从未
                    # 开跑，保持原样（如实反映"未处理"，不伪造成失败）。
                    connection.execute(
                        """
                        UPDATE platform.post_analysis_item
                        SET status=CASE WHEN status='fetching' THEN 'fetch_failed'
                                        ELSE 'analysis_failed' END,
                            error='workflow_interrupted',updated_at=now()
                        WHERE status IN ('fetching','analyzing','annotating')
                          AND task_id IN (
                            SELECT id FROM platform.post_analysis_task WHERE workflow_id=%s
                          )
                        """,
                        (command.workflow_id,),
                    )
            elif command.workflow_type == "formal_report_production":
                if temporal_status != "COMPLETED":
                    connection.execute(
                        """
                        UPDATE reporting.formal_report_production
                        SET status='failed',error_code='workflow_interrupted',updated_at=now()
                        WHERE tenant_pub_id=%s AND workflow_id=%s
                          AND status NOT IN ('signed','failed')
                        """,
                        (command.tenant_pub_id, command.workflow_id),
                    )
            connection.execute(
                """
                UPDATE integration.workflow_start_command
                SET terminal_status=%s,last_reconciled_at=now(),updated_at=now()
                WHERE command_id=%s::uuid
                """,
                (temporal_status, command.command_id),
            )

    async def reconcile_one(self, workflow_id: str | None = None) -> bool:
        command = self.claim_reconciliation(workflow_id)
        if command is None:
            return False
        try:
            description = await self.temporal.get_workflow_handle(command.workflow_id).describe()
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
            # A started command proves prior acceptance. If Temporal no longer
            # has the execution, retention or administrative deletion removed
            # the only runtime authority; fail closed instead of reporting an
            # indefinitely running collection.
            self.reconciled_terminal(command, "NOT_FOUND")
            return True
        if description.status is None:
            raise RuntimeError("temporal_workflow_status_unavailable")
        status = description.status.name
        if status != "RUNNING":
            self.reconciled_terminal(command, status)
        return True

    async def dispatch_one(self, workflow_id: str | None = None) -> bool:
        command = self.claim(workflow_id)
        if command is None:
            return False
        self._assert_start_tenant(command)
        parent_context = TRACE_PROPAGATOR.extract(carrier=command.trace_context)
        context_token = context.attach(parent_context)
        try:
            payload = command.payload
            temporal_run_id = None
            handle: Any
            try:
                if command.workflow_type == "geo_collection":
                    workflow_input = GeoCollectionInput(
                        tenant_pub_id=str(payload["tenant_pub_id"]),
                        project_pub_id=str(payload["project_pub_id"]),
                        run_pub_id=str(payload["run_pub_id"]),
                        config_version_pub_id=str(payload["config_version_pub_id"]),
                        tasks=[CollectionTaskInput(**item) for item in payload["tasks"]],
                        requires_intervention=bool(payload["requires_intervention"]),
                        account_pub_id=(
                            str(payload["account_pub_id"])
                            if payload.get("account_pub_id") is not None
                            else None
                        ),
                    )
                    handle = await self.temporal.start_workflow(
                        GeoCollectionWorkflow.run,
                        workflow_input,
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                elif command.workflow_type == "account_revocation":
                    handle = await self.temporal.start_workflow(
                        AccountRevocationWorkflow.run,
                        RevocationInput(
                            tenant_pub_id=str(payload["tenant_pub_id"]),
                            account_pub_id=str(payload["account_pub_id"]),
                            profile_versions=[int(item) for item in payload["profile_versions"]],
                        ),
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                elif command.workflow_type == "answer_analysis":
                    handle = await self.temporal.start_workflow(
                        AnswerAnalysisWorkflow.run,
                        payload,
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                elif command.workflow_type == "post_analysis":
                    handle = await self.temporal.start_workflow(
                        PostAnalysisWorkflow.run,
                        PostAnalysisInput(
                            tenant_pub_id=str(payload["tenant_pub_id"]),
                            task_pub_id=str(payload["task_pub_id"]),
                        ),
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                elif command.workflow_type == "formal_report_production":
                    handle = await self.temporal.start_workflow(
                        ReportProductionWorkflow.run,
                        payload,
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                elif command.workflow_type == "own_content_disparagement":
                    handle = await self.temporal.start_workflow(
                        OwnContentDisparagementWorkflow.run,
                        OwnContentDisparagementInput(
                            tenant_pub_id=str(payload["tenant_pub_id"]),
                            article_version_pub_id=str(payload["article_version_pub_id"]),
                        ),
                        id=command.workflow_id,
                        task_queue=command.task_queue,
                    )
                else:
                    raise RuntimeError("unsupported_workflow_start_type")
                temporal_run_id = handle.result_run_id
            except WorkflowAlreadyStartedError as error:
                # Accepted-before-ack crashes converge on the same durable ID.
                temporal_run_id = error.run_id
                if not temporal_run_id:
                    description = await self.temporal.get_workflow_handle(
                        command.workflow_id
                    ).describe()
                    temporal_run_id = description.run_id
                if not temporal_run_id:
                    raise RuntimeError("already_started_run_id_unavailable") from error
            self.started(command, temporal_run_id)
            return True
        except BaseException as error:
            self.failed(command, error)
            raise
        finally:
            context.detach(context_token)
