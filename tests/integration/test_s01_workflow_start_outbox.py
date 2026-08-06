import hashlib
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.collection.workflow_outbox import WorkflowStartOutbox
from geo_platform.main import app
from opentelemetry import trace
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from workflows.activities.collection import (
    CollectionTaskInput,
    CollectionTaskResult,
    persist_collection_result,
    publish_downstream_event,
)

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def create_run(client: TestClient, headers: dict[str, str]) -> tuple[str, str, dict[str, object]]:
    project = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Outbox project", "customer_name": "Outbox customer"},
    )
    assert project.status_code == 201
    project_pub_id = project.json()["pub_id"]
    headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
    frozen = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers=headers,
        json={
            "query_groups": [{"name": "Core", "items": [{"text": "What is GEO?"}]}],
            "regions": ["CN-BJ"],
            "models": ["fixed"],
            "modes": ["fast"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201
    headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
    body = {
        "project_pub_id": project_pub_id,
        "config_version_pub_id": frozen.json()["pub_id"],
        "requires_intervention": False,
    }
    accepted = client.post("/api/v2/collection/runs", headers=headers, json=body)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["run_id"] is None
    return accepted.json()["workflow_id"], headers["Idempotency-Key"], body


class StartedHandle:
    result_run_id = "temporal-outbox-test-run"


class SuccessfulTemporal:
    def __init__(self) -> None:
        self.calls = 0

    async def start_workflow(self, *args: object, **kwargs: object) -> StartedHandle:
        del args, kwargs
        self.calls += 1
        return StartedHandle()


class AlreadyStartedHandle:
    async def describe(self) -> object:
        return SimpleNamespace(run_id="existing-run")


class AlreadyStartedTemporal:
    def __init__(self) -> None:
        self.observed_trace_id: int | None = None

    async def start_workflow(self, *args: object, **kwargs: object) -> StartedHandle:
        del args
        self.observed_trace_id = trace.get_current_span().get_span_context().trace_id
        raise WorkflowAlreadyStartedError(str(kwargs["id"]), "GeoCollectionWorkflow", run_id=None)

    def get_workflow_handle(self, workflow_id: str) -> AlreadyStartedHandle:
        del workflow_id
        return AlreadyStartedHandle()


class FailingTemporal:
    async def start_workflow(self, *args: object, **kwargs: object) -> StartedHandle:
        del args, kwargs
        raise ConnectionError("fixture temporal unavailable")


@pytest.mark.asyncio
async def test_answer_analysis_workflow_start_command_dispatches_to_s02() -> None:
    with TestClient(app) as client:
        tenant, _ = bootstrap(client, "analysis-dispatch-" + secrets.token_hex(8))
    workflow_id = f"answer-analysis/{tenant}/run_probe/tsk_probe"
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO integration.workflow_start_command
              (command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
               trace_context)
            VALUES (%s,%s,'answer_analysis',%s,'geo-platform-v2-s02','{}','{}')
            """,
            (str(uuid.uuid4()), tenant, workflow_id),
        )
    temporal = SuccessfulTemporal()
    dispatcher = WorkflowStartOutbox(dsn=POSTGRES_DSN, temporal=temporal)  # type: ignore[arg-type]
    assert await dispatcher.dispatch_one(workflow_id)
    assert temporal.calls == 1
    with psycopg.connect(POSTGRES_DSN) as connection:
        state = connection.execute(
            """
            SELECT state,temporal_run_id FROM integration.workflow_start_command
            WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
    assert state == ("started", StartedHandle.result_run_id)


def test_distinct_collection_results_serialize_run_completion() -> None:
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "activity-accounting-" + secrets.token_hex(8))
        workflow_id, _, _ = create_run(client, headers)

    run_pub_id = workflow_id.rsplit("/", 1)[-1]
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
                UPDATE platform.collection_run
                SET total_tasks=2
                WHERE pub_id=%s
                """,
            (run_pub_id,),
        )
        connection.execute(
            """
            INSERT INTO platform.brand
              (id,pub_id,tenant_id,project_id,version,created_at,updated_at,name)
            SELECT %s,%s,run.tenant_id,run.project_id,1,now(),now(),'Acme'
            FROM platform.collection_run run WHERE run.pub_id=%s
            """,
            (str(uuid.uuid4()), f"brd_{secrets.token_hex(10)}", run_pub_id),
        )

    task_inputs = (
        CollectionTaskInput("business-a", "query-a", "model-a", "region-a", "mode-a"),
        CollectionTaskInput("business-b", "query-b", "model-b", "region-b", "mode-b"),
    )
    results = (
        CollectionTaskResult("business-a", "answer-a", "screen-a", "accepted"),
        CollectionTaskResult("business-b", "answer-b", "screen-b", "accepted"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(persist_collection_result, tenant, run_pub_id, result, task_inputs[index])
            for index, result in enumerate(results)
        ]
        for future in futures:
            future.result()
    with pytest.raises(ApplicationError, match="collection result replay payload drifted"):
        persist_collection_result(
            tenant,
            run_pub_id,
            CollectionTaskResult("business-a", "changed", "screen-a", "accepted"),
            task_inputs[0],
        )
    with pytest.raises(ApplicationError, match="collection result rejected by DLP"):
        # 原始采集原则（2026-08-06 拍板）：answer_text 等公开平台输出原文存储
        # 零 DLP；DLP fail-closed 自检只守 screenshot_ref 等平台自产路径串。
        persist_collection_result(
            tenant,
            run_pub_id,
            CollectionTaskResult(
                "business-secret",
                "answer-secret",
                "Authorization: Bearer forbidden-secret",
                "accepted",
            ),
            CollectionTaskInput("business-secret", "query", "model", "region", "mode"),
        )

    with psycopg.connect(POSTGRES_DSN) as connection:
        state, completed_tasks = connection.execute(
            """
            SELECT state,completed_tasks
            FROM platform.collection_run
            WHERE pub_id=%s
            """,
            (run_pub_id,),
        ).fetchone()
        task_count = connection.execute(
            """
            SELECT count(*)
            FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            WHERE run.pub_id=%s
            """,
            (run_pub_id,),
        ).fetchone()[0]
        matrices = connection.execute(
            """
            SELECT matrix_json::jsonb
            FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            WHERE run.pub_id=%s ORDER BY task.business_key
            """,
            (run_pub_id,),
        ).fetchall()
    assert (state, completed_tasks, task_count) == ("completed", 2, 2)
    assert {row[0]["query"] for row in matrices} == {"query-a", "query-b"}
    publish_downstream_event(run_pub_id, tenant, [task_inputs[0]])
    with psycopg.connect(POSTGRES_DSN) as connection:
        partial = connection.execute(
            """
            SELECT payload,published_at
            FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
              AND event_type='collection.run.completed'
            """,
            (tenant, run_pub_id),
        ).fetchone()
    assert partial[0]["analysis_admission"] == "partial_fanout"
    assert partial[0]["analysis_commands"] == 1
    assert partial[0]["analysis_expected"] == 2
    assert partial[1] is None
    first_event = publish_downstream_event(run_pub_id, tenant, list(task_inputs))
    replayed_event = publish_downstream_event(run_pub_id, tenant, list(task_inputs))
    assert replayed_event == first_event
    with psycopg.connect(POSTGRES_DSN) as connection:
        events = connection.execute(
            """
            SELECT event_id,payload,published_at IS NOT NULL
            FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
              AND event_type='collection.run.completed'
            """,
            (tenant, run_pub_id),
        ).fetchall()
        analysis_commands = connection.execute(
            """
            SELECT payload
            FROM integration.workflow_start_command
            WHERE tenant_pub_id=%s AND workflow_type='answer_analysis'
              AND workflow_id LIKE %s
            ORDER BY workflow_id
            """,
            (tenant, f"answer-analysis/{tenant}/{run_pub_id}/%"),
        ).fetchall()
    assert len(events) == 1
    assert events[0][1]["completed_tasks"] == 2
    assert events[0][1]["analysis_admission"] == "enqueued"
    assert events[0][1]["analysis_commands"] == 2
    assert events[0][1]["analysis_expected"] == 2
    assert events[0][2] is True
    assert len(analysis_commands) == 2
    assert {item[0]["text"] for item in analysis_commands} == {"answer-a", "answer-b"}
    assert {item[0]["dimensions"]["query_text"] for item in analysis_commands} == {
        "query-a",
        "query-b",
    }
    assert {item[0]["dimensions"]["run_pub_id"] for item in analysis_commands} == {run_pub_id}
    assert all(item[0]["dimensions"]["config_version_pub_id"] for item in analysis_commands)
    assert first_event.endswith(events[0][0])


class ReconciliationHandle:
    async def describe(self) -> object:
        return SimpleNamespace(status=SimpleNamespace(name="FAILED"))


class ReconciliationTemporal:
    def get_workflow_handle(self, workflow_id: str) -> ReconciliationHandle:
        del workflow_id
        return ReconciliationHandle()


class MissingWorkflowHandle:
    async def describe(self) -> object:
        raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")


class MissingWorkflowTemporal:
    def get_workflow_handle(self, workflow_id: str) -> MissingWorkflowHandle:
        del workflow_id
        return MissingWorkflowHandle()


class SignalHandle:
    def __init__(self, signals: list[tuple[str, list[object]]]) -> None:
        self.signals = signals

    async def signal(self, signal_name: str, *, args: list[object]) -> None:
        self.signals.append((signal_name, args))


class SignallingTemporal:
    def __init__(self) -> None:
        self.signals: list[tuple[str, list[object]]] = []

    def get_workflow_handle(self, workflow_id: str) -> SignalHandle:
        del workflow_id
        return SignalHandle(self.signals)


class FailingSignalHandle:
    async def signal(self, signal_name: str, *, args: list[object]) -> None:
        del signal_name, args
        raise ConnectionError("fixture signal unavailable")


class FailingSignalTemporal:
    def get_workflow_handle(self, workflow_id: str) -> FailingSignalHandle:
        del workflow_id
        return FailingSignalHandle()


@pytest.mark.asyncio
async def test_collection_start_outbox_retries_and_converges_already_started() -> None:
    client = TestClient(app)
    tenant, headers = bootstrap(client, "workflow-outbox-" + secrets.token_hex(5))
    request_trace_id = secrets.token_hex(16)
    headers["traceparent"] = f"00-{request_trace_id}-{secrets.token_hex(8)}-01"
    workflow_id, idempotency_key, body = create_run(client, headers)
    replay = client.post(
        "/api/v2/collection/runs",
        headers=headers | {"Idempotency-Key": idempotency_key},
        json=body,
    )
    assert replay.status_code == 202
    assert replay.json()["workflow_id"] == workflow_id
    conflict = client.post(
        "/api/v2/collection/runs",
        headers=headers | {"Idempotency-Key": idempotency_key},
        json=body | {"requires_intervention": True},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    with psycopg.connect(POSTGRES_DSN) as connection:
        command = connection.execute(
            """
            SELECT state,attempts,trace_context FROM integration.workflow_start_command
            WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert command is not None
        assert command[:2] == ("pending", 0)
        assert command[2]["traceparent"].startswith(f"00-{request_trace_id}-")
        assert "baggage" not in command[2]

    already_started_temporal = AlreadyStartedTemporal()
    already_started = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=already_started_temporal,  # type: ignore[arg-type]
    )
    assert await already_started.dispatch_one(workflow_id)
    assert already_started_temporal.observed_trace_id == int(request_trace_id, 16)
    with psycopg.connect(POSTGRES_DSN) as connection:
        command = connection.execute(
            """
            SELECT state,attempts,last_error_code,temporal_run_id
            FROM integration.workflow_start_command WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert command == ("started", 1, None, "existing-run")
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        tenant_id = connection.execute(
            "SELECT id::text FROM platform.tenant WHERE pub_id=%s", (tenant,)
        ).fetchone()
        assert tenant_id is not None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id[0],))
        run = connection.execute(
            """
            SELECT state,temporal_run_id
            FROM platform.collection_run WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert run == ("running", "existing-run")
    reconciler = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=ReconciliationTemporal(),  # type: ignore[arg-type]
    )
    assert await reconciler.reconcile_one(workflow_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        terminal = connection.execute(
            """
            SELECT terminal_status FROM integration.workflow_start_command
            WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert terminal == ("FAILED",)
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        tenant_id = connection.execute(
            "SELECT id::text FROM platform.tenant WHERE pub_id=%s", (tenant,)
        ).fetchone()
        assert tenant_id is not None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id[0],))
        run = connection.execute(
            """
            SELECT state,error_code FROM platform.collection_run WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert run == ("failed", "temporal_failed")
        failed_run_pub_id = connection.execute(
            """
            SELECT pub_id FROM platform.collection_run WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert failed_run_pub_id is not None
    terminal_cancel = client.post(
        f"/api/v2/collection/runs/{failed_run_pub_id[0]}/cancel",
        headers=headers,
    )
    assert terminal_cancel.status_code == 409
    assert terminal_cancel.json()["error"]["code"] == "run_terminal"
    with psycopg.connect(POSTGRES_DSN) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as terminal_violation:
            connection.execute(
                """
                UPDATE platform.collection_run
                SET state='running' WHERE workflow_id=%s
                """,
                (workflow_id,),
            )
        assert terminal_violation.value.diag.constraint_name == "ck_collection_run_terminal_state"
    with psycopg.connect(POSTGRES_DSN) as connection:
        terminal_state = connection.execute(
            """
            SELECT state FROM platform.collection_run WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert terminal_state == ("failed",)
        terminal_signals = connection.execute(
            """
            SELECT count(*) FROM integration.workflow_signal_command
            WHERE workflow_id=%s
            """,
            (workflow_id,),
        ).fetchone()
        assert terminal_signals == (0,)

    second_workflow_id, _, _ = create_run(client, headers)
    failing = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=FailingTemporal(),  # type: ignore[arg-type]
    )
    with pytest.raises(ConnectionError):
        await failing.dispatch_one(second_workflow_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        failed = connection.execute(
            """
            SELECT state,attempts,last_error_code
            FROM integration.workflow_start_command WHERE workflow_id=%s
            """,
            (second_workflow_id,),
        ).fetchone()
        assert failed == ("dispatching", 1, "ConnectionError")
        connection.execute(
            """
            UPDATE integration.workflow_start_command
            SET claimed_at=now()-interval '31 seconds'
            WHERE workflow_id=%s
            """,
            (second_workflow_id,),
        )
    temporal = SuccessfulTemporal()
    retrying = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=temporal,  # type: ignore[arg-type]
    )
    assert await retrying.dispatch_one(second_workflow_id)
    assert temporal.calls == 1
    missing = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=MissingWorkflowTemporal(),  # type: ignore[arg-type]
    )
    assert await missing.reconcile_one(second_workflow_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        terminal = connection.execute(
            """
            SELECT terminal_status FROM integration.workflow_start_command
            WHERE workflow_id=%s
            """,
            (second_workflow_id,),
        ).fetchone()
        assert terminal == ("NOT_FOUND",)
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        tenant_id = connection.execute(
            "SELECT id::text FROM platform.tenant WHERE pub_id=%s", (tenant,)
        ).fetchone()
        assert tenant_id is not None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id[0],))
        run = connection.execute(
            """
            SELECT state,error_code FROM platform.collection_run WHERE workflow_id=%s
            """,
            (second_workflow_id,),
        ).fetchone()
        assert run == ("failed", "temporal_history_missing")

    third_workflow_id, _, _ = create_run(client, headers)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        tenant_id = connection.execute(
            "SELECT id::text FROM platform.tenant WHERE pub_id=%s", (tenant,)
        ).fetchone()
        assert tenant_id is not None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id[0],))
        run_pub_id = connection.execute(
            """
            SELECT pub_id FROM platform.collection_run WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        ).fetchone()
        assert run_pub_id is not None
    paused = client.post(
        f"/api/v2/collection/runs/{run_pub_id[0]}/pause",
        headers=headers,
    )
    assert paused.status_code == 200
    duplicate_pause = client.post(
        f"/api/v2/collection/runs/{run_pub_id[0]}/pause",
        headers=headers,
    )
    assert duplicate_pause.status_code == 200
    conflicting_resume = client.post(
        f"/api/v2/collection/runs/{run_pub_id[0]}/resume",
        headers=headers,
    )
    assert conflicting_resume.status_code == 409
    assert conflicting_resume.json()["error"]["code"] == "idempotency_conflict"
    with psycopg.connect(POSTGRES_DSN) as connection:
        signal_command = connection.execute(
            """
            SELECT state,attempts FROM integration.workflow_signal_command
            WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        ).fetchone()
        assert signal_command == ("pending", 0)

    blocked_by_start = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=SignallingTemporal(),  # type: ignore[arg-type]
    )
    assert not await blocked_by_start.dispatch_signal_one(third_workflow_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        unchanged_signal = connection.execute(
            """
            SELECT state,attempts FROM integration.workflow_signal_command
            WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        ).fetchone()
        assert unchanged_signal == ("pending", 0)
        connection.execute(
            """
            UPDATE integration.workflow_start_command
            SET state='started',started_at=now()
            WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        )

    signal_failure = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=FailingSignalTemporal(),  # type: ignore[arg-type]
    )
    with pytest.raises(ConnectionError):
        await signal_failure.dispatch_signal_one(third_workflow_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        failed_signal = connection.execute(
            """
            SELECT state,attempts,last_error_code
            FROM integration.workflow_signal_command WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        ).fetchone()
        assert failed_signal == ("dispatching", 1, "ConnectionError")
        connection.execute(
            """
            UPDATE integration.workflow_signal_command
            SET claimed_at=now()-interval '31 seconds'
            WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        )
    signalling_temporal = SignallingTemporal()
    signal_retry = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=signalling_temporal,  # type: ignore[arg-type]
    )
    assert await signal_retry.dispatch_signal_one(third_workflow_id)
    assert signalling_temporal.signals == [("pause", [])]
    with psycopg.connect(POSTGRES_DSN) as connection:
        delivered_signal = connection.execute(
            """
            SELECT state,attempts,last_error_code,delivered_at IS NOT NULL
            FROM integration.workflow_signal_command WHERE workflow_id=%s
            """,
            (third_workflow_id,),
        ).fetchone()
        assert delivered_signal == ("delivered", 2, None, True)

    concurrent_workflows = [create_run(client, headers)[0] for _ in range(2)]
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        tenant_id = connection.execute(
            "SELECT id::text FROM platform.tenant WHERE pub_id=%s", (tenant,)
        ).fetchone()
        assert tenant_id is not None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id[0],))
        concurrent_run_rows = [
            connection.execute(
                "SELECT pub_id FROM platform.collection_run WHERE workflow_id=%s",
                (candidate,),
            ).fetchone()
            for candidate in concurrent_workflows
        ]
        assert all(row is not None for row in concurrent_run_rows)
        concurrent_run_ids = [row[0] for row in concurrent_run_rows if row is not None]
    shared_key = "concurrent-control-" + secrets.token_hex(16)

    def pause_run(run_id: str) -> tuple[int, str | None]:
        response = TestClient(app).post(
            f"/api/v2/collection/runs/{run_id}/pause",
            headers=headers | {"Idempotency-Key": shared_key},
        )
        error_code = (
            response.json().get("error", {}).get("code") if response.status_code != 200 else None
        )
        return response.status_code, error_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(executor.map(pause_run, concurrent_run_ids))
    assert sorted(concurrent_results) == [
        (200, None),
        (409, "idempotency_conflict"),
    ]
    with psycopg.connect(POSTGRES_DSN) as connection:
        receipt_count = connection.execute(
            """
            SELECT count(*) FROM integration.workflow_signal_command
            WHERE idempotency_key_hash=%s
            """,
            (hashlib.sha256(shared_key.encode()).hexdigest(),),
        ).fetchone()
        assert receipt_count == (1,)
