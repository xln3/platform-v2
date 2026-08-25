from __future__ import annotations

import os
import secrets
import uuid
from datetime import UTC, datetime

import psycopg
from fastapi.testclient import TestClient
from geo_platform.main import app

from workflows.activities.collection import (
    CollectionBatchItemResult,
    CollectionTaskInput,
    CollectionTaskResult,
    mark_collection_run_terminal,
    persist_collection_result,
)

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def _bootstrap(client: TestClient) -> tuple[str, dict[str, str]]:
    subject = "query-retry-" + secrets.token_hex(8)
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant_pub_id = str(response.json()["tenant_pub_id"])
    return tenant_pub_id, {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def _stage_four_query_run(client: TestClient, headers: dict[str, str]) -> tuple[str, str, str]:
    project_response = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Query retry project", "customer_name": "Query retry customer"},
    )
    assert project_response.status_code == 201
    project_pub_id = str(project_response.json()["pub_id"])
    headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
    frozen = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers=headers,
        json={
            "query_groups": [
                {
                    "name": "Core",
                    "items": [{"text": f"query-{index}"} for index in range(1, 5)],
                }
            ],
            "regions": ["CN-BJ"],
            "models": ["fixed"],
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201, frozen.text
    headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
    accepted = client.post(
        "/api/v2/collection/runs",
        headers=headers,
        json={
            "project_pub_id": project_pub_id,
            "config_version_pub_id": frozen.json()["pub_id"],
            "requires_intervention": False,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_pub_id = str(accepted.json()["workflow_id"]).rsplit("/", 1)[-1]
    return project_pub_id, str(frozen.json()["pub_id"]), run_pub_id


def test_partial_run_analyzes_success_and_requeues_only_failed_queries() -> None:
    with TestClient(app) as client:
        tenant_pub_id, headers = _bootstrap(client)
        project_pub_id, _config_pub_id, run_pub_id = _stage_four_query_run(client, headers)

        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                """
                INSERT INTO platform.brand
                  (id,pub_id,tenant_id,project_id,version,created_at,updated_at,name)
                SELECT %s,%s,run.tenant_id,run.project_id,1,now(),now(),'Acme'
                FROM platform.collection_run run WHERE run.pub_id=%s
                """,
                (str(uuid.uuid4()), f"brd_{secrets.token_hex(10)}", run_pub_id),
            )
            planned = connection.execute(
                """
                SELECT task.business_key,task.matrix_json::jsonb,task.state
                FROM platform.collection_task task
                JOIN platform.collection_run run ON run.id=task.run_id
                WHERE run.pub_id=%s ORDER BY task.matrix_json::jsonb->>'query'
                """,
                (run_pub_id,),
            ).fetchall()
        assert len(planned) == 4
        assert {row[2] for row in planned} == {"pending"}

        task_inputs = [
            CollectionTaskInput(
                business_key=str(business_key),
                query=str(matrix["query"]),
                model=str(matrix["model"]),
                region=str(matrix["region"]),
                mode=str(matrix["mode"]),
                adapter=str(matrix["adapter"]),
            )
            for business_key, matrix, _state in planned
        ]
        successful = task_inputs[0]
        failed = task_inputs[1:]
        persist_collection_result(
            tenant_pub_id,
            run_pub_id,
            CollectionTaskResult(
                successful.business_key,
                "successful answer",
                "screen-success",
                "accepted",
            ),
            successful,
        )
        for index, task in enumerate(failed, 1):
            persist_collection_result(
                tenant_pub_id,
                run_pub_id,
                CollectionBatchItemResult(
                    business_key=task.business_key,
                    status="incomplete",
                    error_type=f"fixture_failure_{index}",
                    error_message=f"failure context {index}",
                ),
                task,
            )

        with psycopg.connect(POSTGRES_DSN) as connection:
            failed_task_rows = connection.execute(
                """
                SELECT task.business_key,task.pub_id
                FROM platform.collection_task task
                JOIN platform.collection_run run ON run.id=task.run_id
                WHERE run.pub_id=%s AND task.state='failed'
                ORDER BY task.business_key
                """,
                (run_pub_id,),
            ).fetchall()
        failed_pub_by_key = {str(key): str(pub_id) for key, pub_id in failed_task_rows}
        manually_selected_key = sorted(failed_pub_by_key)[0]
        manual_idempotency_key = "manual-one-query-" + secrets.token_hex(16)
        headers["Idempotency-Key"] = manual_idempotency_key
        manual_precise = client.post(
            f"/api/v2/collection/runs/{run_pub_id}/retry",
            headers=headers,
            json={"task_pub_ids": [failed_pub_by_key[manually_selected_key]]},
        )
        assert manual_precise.status_code in {200, 202}, manual_precise.text
        manual_workflow_id = str(manual_precise.json()["workflow_id"])

        # Exact manual selection is atomic. q1 has already been claimed, so a
        # later q1+q2 command must roll back instead of silently creating a q2-
        # only child whose idempotency replay would no longer match its request.
        overlapping_key = next(key for key in failed_pub_by_key if key != manually_selected_key)
        headers["Idempotency-Key"] = "manual-overlap-" + secrets.token_hex(16)
        overlapping_selection = client.post(
            f"/api/v2/collection/runs/{run_pub_id}/retry",
            headers=headers,
            json={
                "task_pub_ids": [
                    failed_pub_by_key[manually_selected_key],
                    failed_pub_by_key[overlapping_key],
                ]
            },
        )
        assert overlapping_selection.status_code == 409
        assert "retry_task_selection_not_dispatchable" in overlapping_selection.text

        # Run reconciliation automatically queues only the two failures that
        # the operator did not already enqueue. The successful query and the
        # manual query must never be duplicated.
        mark_collection_run_terminal(tenant_pub_id, run_pub_id, "completed", None)

        with psycopg.connect(POSTGRES_DSN) as connection:
            run_row = connection.execute(
                """
                SELECT state,total_tasks,completed_tasks,failed_tasks,id
                FROM platform.collection_run WHERE pub_id=%s
                """,
                (run_pub_id,),
            ).fetchone()
            assert run_row is not None
            source_run_id = run_row[4]
            task_states = connection.execute(
                """
                SELECT business_key,state FROM platform.collection_task
                WHERE run_id=%s ORDER BY business_key
                """,
                (source_run_id,),
            ).fetchall()
            retry_runs = connection.execute(
                """
                SELECT id,pub_id,state,total_tasks,retry_of_run_pub_id,workflow_id
                FROM platform.collection_run
                WHERE retry_of_run_pub_id=%s
                ORDER BY created_at,pub_id
                """,
                (run_pub_id,),
            ).fetchall()
            retry_tasks = connection.execute(
                """
                SELECT run_id,business_key,state FROM platform.collection_task
                WHERE run_id=ANY(%s) ORDER BY run_id,business_key
                """,
                ([row[0] for row in retry_runs],),
            ).fetchall()
            intents = connection.execute(
                """
                SELECT business_key,state,trigger_mode,capability_key,not_before,retry_run_id,
                       priority
                FROM platform.collection_query_retry_intent
                WHERE source_run_id=%s ORDER BY business_key
                """,
                (source_run_id,),
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT business_key,outcome,error_code,execution_context
                FROM platform.collection_query_execution_attempt
                WHERE run_id=%s ORDER BY business_key
                """,
                (source_run_id,),
            ).fetchall()
            knowledge = connection.execute(
                """
                SELECT scope,redacted_context
                FROM platform.collection_failure_knowledge
                WHERE run_id=%s ORDER BY scope,created_at
                """,
                (source_run_id,),
            ).fetchall()
            retry_payloads = connection.execute(
                """
                SELECT workflow_id,payload FROM integration.workflow_start_command
                WHERE workflow_id=ANY(%s)
                """,
                ([row[5] for row in retry_runs],),
            ).fetchall()
            successful_analysis_jobs = connection.execute(
                """
                SELECT count(*) FROM platform.analysis_job job
                JOIN platform.collection_task task ON task.id=job.answer_task_id
                WHERE job.run_id=%s AND task.business_key=%s
                  AND job.subject_type='answer' AND job.state='queued'
                """,
                (source_run_id, successful.business_key),
            ).fetchone()[0]

        failed_keys = {task.business_key for task in failed}
        assert run_row[:4] == ("completed_with_failures", 4, 1, 3)
        assert dict(task_states)[successful.business_key] == "completed"
        assert {key for key, state in task_states if state == "failed"} == failed_keys
        assert len(retry_runs) == 2
        assert sorted(row[3] for row in retry_runs) == [1, 2]
        assert all(row[2] == "starting" and row[4] == run_pub_id for row in retry_runs)
        assert {key for _run_id, key, state in retry_tasks if state == "pending"} == failed_keys
        assert successful.business_key not in {key for _run_id, key, _state in retry_tasks}
        tasks_by_retry_run = {
            row[0]: {task[1] for task in retry_tasks if task[0] == row[0]} for row in retry_runs
        }
        manual_retry_run = next(row for row in retry_runs if row[5] == manual_workflow_id)
        assert tasks_by_retry_run[manual_retry_run[0]] == {manually_selected_key}
        automatic_retry_run = next(row for row in retry_runs if row[5] != manual_workflow_id)
        assert tasks_by_retry_run[automatic_retry_run[0]] == failed_keys - {manually_selected_key}
        assert len(intents) == 3
        assert all(row[1] == "enqueued" for row in intents)
        assert all(row[3].startswith("adapter=fixed|") for row in intents)
        intent_by_key = {row[0]: row for row in intents}
        assert intent_by_key[manually_selected_key][2] == "manual"
        assert intent_by_key[manually_selected_key][5] == manual_retry_run[0]
        assert intent_by_key[manually_selected_key][6] == 200
        assert all(
            intent_by_key[key][2] == "automatic"
            and intent_by_key[key][5] == automatic_retry_run[0]
            and intent_by_key[key][6] == 50
            for key in failed_keys - {manually_selected_key}
        )
        assert {row[0] for row in attempts} == {task.business_key for task in task_inputs}
        assert sorted(row[1] for row in attempts) == ["failed", "failed", "failed", "succeeded"]
        assert all(
            "query" not in context
            and len(context["query_sha256"]) == 64
            and context["query_length"] > 0
            for _business_key, _outcome, _error_code, context in attempts
        )
        assert len([row for row in knowledge if row[0] == "query"]) == 3
        assert all(
            "query" not in context
            and len(context["query_sha256"]) == 64
            and context["query_length"] > 0
            for scope, context in knowledge
            if scope == "query"
        )
        run_knowledge = next(row[1] for row in knowledge if row[0] == "run")
        assert run_knowledge["successful_query_count"] == 1
        assert run_knowledge["failed_query_count"] == 3
        assert {row["business_key"] for row in run_knowledge["failed_queries"]} == failed_keys
        assert all(
            "query" not in query and len(query["query_sha256"]) == 64 and query["query_length"] > 0
            for query in (run_knowledge["successful_queries"] + run_knowledge["failed_queries"])
        )
        payload_by_workflow = {workflow_id: payload for workflow_id, payload in retry_payloads}
        assert {
            str(row["business_key"]) for row in payload_by_workflow[manual_workflow_id]["tasks"]
        } == {manually_selected_key}
        assert {
            str(row["business_key"]) for row in payload_by_workflow[automatic_retry_run[5]]["tasks"]
        } == failed_keys - {manually_selected_key}
        assert all(payload["retry_capability_keys"] for payload in payload_by_workflow.values())
        assert all(payload["retry_not_before"] for payload in payload_by_workflow.values())
        assert successful_analysis_jobs == 1

        headers["Idempotency-Key"] = "manual-replay-" + secrets.token_hex(16)
        manual_replay = client.post(
            f"/api/v2/collection/runs/{run_pub_id}/retry",
            headers=headers,
            json={"task_pub_ids": [failed_pub_by_key[manually_selected_key]]},
        )
        assert manual_replay.status_code in {200, 202}, manual_replay.text
        assert manual_replay.json()["workflow_id"] == manual_workflow_id

        headers["Idempotency-Key"] = manual_idempotency_key
        conflicting_replay = client.post(
            f"/api/v2/collection/runs/{run_pub_id}/retry",
            headers=headers,
            json={
                "task_pub_ids": [
                    failed_pub_by_key[
                        next(key for key in failed_keys if key != manually_selected_key)
                    ]
                ]
            },
        )
        assert conflicting_replay.status_code == 409
        assert "idempotency_conflict" in conflicting_replay.text
        assert project_pub_id
