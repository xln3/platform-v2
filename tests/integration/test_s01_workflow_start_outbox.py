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
from workflows.activities.content_contribution import (
    ContentContributionInput,
    execute_content_contribution,
)
from workflows.activities.content_strategy import ContentStrategyInput, execute_content_strategy
from workflows.activities.s02 import _resolve_answer_capture

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
        self.task_queues: list[str] = []

    async def start_workflow(self, *args: object, **kwargs: object) -> StartedHandle:
        del args
        self.calls += 1
        self.task_queues.append(str(kwargs["task_queue"]))
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
async def test_legacy_answer_command_is_routed_to_detached_analysis_queue() -> None:
    with TestClient(app) as client:
        tenant, _ = bootstrap(client, "analysis-dispatch-" + secrets.token_hex(8))
    workflow_id = f"answer-analysis/{tenant}/run_probe/tsk_probe"
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO integration.workflow_start_command
              (command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
               trace_context)
            VALUES (
              %s,%s,'answer_analysis',%s,'geo-platform-v2-s02',
              jsonb_build_object('tenant_pub_id',%s::text),'{}'
            )
            """,
            (str(uuid.uuid4()), tenant, workflow_id, tenant),
        )
    temporal = SuccessfulTemporal()
    dispatcher = WorkflowStartOutbox(dsn=POSTGRES_DSN, temporal=temporal)  # type: ignore[arg-type]
    assert await dispatcher.dispatch_one(workflow_id)
    assert temporal.calls == 1
    assert temporal.task_queues == ["geo-platform-v2-analysis"]
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
        workflow_id, _, run_request = create_run(client, headers)

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
        CollectionTaskResult(
            "business-a",
            "answer-a",
            "screen-a",
            "accepted",
            citations=[
                {
                    "url": "https://example.com/captured-source",
                    "title": "Captured source",
                    "cited_text": "captured evidence",
                    "ordinal": 1,
                }
            ],
        ),
        CollectionTaskResult("business-b", "answer-b  \r\n", "screen-b", "accepted"),
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
        capture_events = connection.execute(
            """
            SELECT payload
            FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND event_type='answer.capture.completed'
              AND payload->>'run_pub_id'=%s
            ORDER BY payload->>'business_key'
            """,
            (tenant, run_pub_id),
        ).fetchall()
        admitted_jobs = connection.execute(
            """
            SELECT job.state,job.analyzer_kind,job.policy_version,
                   task.business_key,command.task_queue,command.payload
            FROM platform.analysis_job job
            JOIN platform.collection_task task ON task.id=job.answer_task_id
            LEFT JOIN integration.workflow_start_command command
              ON command.workflow_id=job.workflow_id
            WHERE job.run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND job.subject_type='answer'
            ORDER BY task.business_key
            """,
            (run_pub_id,),
        ).fetchall()
    assert (state, completed_tasks, task_count) == ("completed", 2, 2)
    assert {row[0]["query"] for row in matrices} == {"query-a", "query-b"}
    # Capture, evidence, capture event and analysis admission commit together,
    # before the run-completion activity is called.
    assert [row[0]["business_key"] for row in capture_events] == [
        "business-a",
        "business-b",
    ]
    assert [(row[0], row[1], row[2], row[3]) for row in admitted_jobs] == [
        ("queued", "answer_basic", "answer-basic-v1", "business-a"),
        ("queued", "answer_basic", "answer-basic-v1", "business-b"),
    ]
    assert all(row[4] == "geo-platform-v2-analysis" for row in admitted_jobs)
    assert all("text" not in row[5] and "capture_ref" in row[5] for row in admitted_jobs)

    # No semantic projection exists yet, but the captured answers are already
    # queryable and their pending state is explicit.
    with TestClient(app) as client:
        captured_page = client.get(
            "/api/v2/analytics/answers",
            headers=headers,
            params={"project_pub_id": run_request["project_pub_id"]},
        )
    assert captured_page.status_code == 200, captured_page.text
    captured_answers = captured_page.json()["data"]
    assert {item["response_text"] for item in captured_answers} == {
        "answer-a",
        "answer-b",
    }
    assert all(item["capture_state"] == "completed" for item in captured_answers)
    assert all(item["answer_analysis_state"] == "queued" for item in captured_answers)
    assert all(item["source_analysis_state"] == "queued" for item in captured_answers)
    assert all(item["risk_analysis_state"] == "queued" for item in captured_answers)
    assert all(item["eligible"] is None for item in captured_answers)
    captured_with_citation = next(item for item in captured_answers if item["citation_count"] == 1)
    with TestClient(app) as client:
        captured_relations = client.get(
            f"/api/v2/analytics/answers/{captured_with_citation['pub_id']}/relations",
            headers=headers,
            params={"project_pub_id": run_request["project_pub_id"]},
        )
    assert captured_relations.status_code == 200, captured_relations.text
    citation = captured_relations.json()["citations"][0]
    assert citation["canonical_url"] == "https://example.com/captured-source"
    assert citation["title"] == "Captured source"
    assert citation["cited_text"] == "captured evidence"
    assert citation["support"]["mapping_status"] == "unmapped"
    assert citation["support"]["relation"] == "unverified"
    assert citation["support"]["source_match_status"] == "not_checked"

    publish_downstream_event(run_pub_id, tenant, [task_inputs[0]], True)
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
    # Per-answer handoff already happened in each persist transaction. Passing
    # only one final-generation input can no longer lose the earlier answer.
    assert partial[0]["analysis_admission"] == "enqueued"
    assert partial[0]["analysis_commands"] == 2
    assert partial[0]["analysis_expected"] == 2
    assert partial[0]["post_analysis_admission"] == "enqueued"
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
        run_jobs = connection.execute(
            """
            SELECT analyzer_kind,state,policy_version
            FROM platform.analysis_job
            WHERE run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND subject_type='run'
            ORDER BY analyzer_kind
            """,
            (run_pub_id,),
        ).fetchall()
        post_command = connection.execute(
            """
            SELECT task_queue,payload
            FROM integration.workflow_start_command
            WHERE tenant_pub_id=%s AND workflow_type='post_collection_analysis'
              AND workflow_id=%s
            """,
            (tenant, f"post-collection-analysis/{tenant}/{run_pub_id}"),
        ).fetchone()
    assert len(events) == 1
    assert events[0][1]["completed_tasks"] == 2
    assert events[0][1]["analysis_admission"] == "enqueued"
    assert events[0][1]["analysis_commands"] == 2
    assert events[0][1]["analysis_expected"] == 2
    assert events[0][1]["post_analysis_admission"] == "enqueued"
    # Projection publication belongs to the analytics outbox consumer, not the
    # analysis-admission producer.
    assert events[0][2] is False
    assert len(analysis_commands) == 2
    assert all("text" not in item[0] for item in analysis_commands)
    assert {_resolve_answer_capture(item[0])["text"] for item in analysis_commands} == {
        "answer-a",
        "answer-b  \r\n",
    }
    assert {item[0]["capture_ref"]["business_key"] for item in analysis_commands} == {
        "business-a",
        "business-b",
    }
    assert {
        item[0]["analysis_context"]["dimensions"]["query_text"] for item in analysis_commands
    } == {
        "query-a",
        "query-b",
    }
    assert {
        item[0]["analysis_context"]["dimensions"]["run_pub_id"] for item in analysis_commands
    } == {run_pub_id}
    assert all(
        item[0]["analysis_context"]["dimensions"]["config_version_pub_id"]
        for item in analysis_commands
    )
    assert {row[0] for row in run_jobs} == {
        "content_contribution",
        "content_strategy",
        "own_site_snapshot",
        "risk_disparagement",
        "risk_factcheck",
        "page_inspection",
        "site_suggestions",
        "source_audit",
        "source_fetch",
    }
    assert {row[0]: (row[1], row[2]) for row in run_jobs} == {
        "content_contribution": ("queued", "post-collection-v2"),
        "content_strategy": ("queued", "post-collection-v2"),
        "own_site_snapshot": ("queued", "post-collection-v2"),
        "page_inspection": ("not_requested", "post-collection-v2"),
        "risk_disparagement": ("queued", "post-collection-v2"),
        "risk_factcheck": ("queued", "post-collection-v2"),
        "site_suggestions": ("queued", "post-collection-v2"),
        "source_audit": ("queued", "post-collection-v2"),
        "source_fetch": ("queued", "post-collection-v2"),
    }
    assert post_command is not None
    assert post_command[0] == "geo-platform-v2-analysis"
    assert post_command[1]["source_task_queue"] == "geo-platform-v2-source"
    assert post_command[1]["source_analysis_profile_pub_id"] is None
    assert post_command[1]["source_analysis_profile_hash"] is None
    assert post_command[1]["page_inspection_policy_version"] == "page-inspection-v1"

    # Completing every requested source/risk analyzer must not hide that page
    # inspection was never requested because the profile was absent.
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            UPDATE platform.analysis_job
            SET state='completed'
            WHERE run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND subject_type='run' AND state='queued'
            """,
            (run_pub_id,),
        )
    with TestClient(app) as client:
        settled_page = client.get(
            "/api/v2/analytics/answers",
            headers=headers,
            params={"project_pub_id": run_request["project_pub_id"], "run_pub_id": run_pub_id},
        )
    assert settled_page.status_code == 200, settled_page.text
    assert all(item["source_analysis_state"] == "partial" for item in settled_page.json()["data"])
    assert all(item["risk_analysis_state"] == "completed" for item in settled_page.json()["data"])
    assert first_event.endswith(events[0][0])


def test_retrieval_events_persist_one_url_identity_and_every_occurrence() -> None:
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "uvw-occurrences-" + secrets.token_hex(8))
        workflow_id, _, run_request = create_run(client, headers)
    run_pub_id = workflow_id.rsplit("/", 1)[-1]
    raw_url = "https://example.com/repeated#first"
    canonical_url = "https://example.com/repeated"
    task_input = CollectionTaskInput("uvw-business", "UVW question", "deepseek", "CN-BJ", "normal")
    persist_collection_result(
        tenant,
        run_pub_id,
        CollectionTaskResult(
            "uvw-business",
            "captured answer",
            "screen",
            "accepted",
            retrieval_events=[
                {
                    "ordinal": 1,
                    "queries": ["first query"],
                    "u_observation": "observed",
                    "v_observation": "observed",
                    "final_reference_observation": "observed",
                    "candidates": [{"url": raw_url, "u_rank": 1}],
                    "opened_pages": [{"url": canonical_url, "v_open_order": 1}],
                    "final_references": [],
                },
                {
                    "ordinal": 2,
                    "queries": ["second query"],
                    "u_observation": "observed",
                    "v_observation": "unobserved",
                    "final_reference_observation": "unobserved",
                    "candidates": [{"url": canonical_url, "u_rank": 2}],
                    "opened_pages": [],
                    "final_references": [],
                },
            ],
        ),
        task_input,
    )

    with psycopg.connect(POSTGRES_DSN) as connection:
        facts = connection.execute(
            """
            SELECT
              count(DISTINCT url.id),count(occurrence.id),count(DISTINCT event.id),
              array_agg(occurrence.query_text ORDER BY occurrence.occurrence_ordinal),
              array_agg(occurrence.u_rank ORDER BY occurrence.occurrence_ordinal),
              array_agg(occurrence.v_state ORDER BY occurrence.occurrence_ordinal),
              min(url.canonical_url)
            FROM platform.collection_run run
            JOIN platform.answer_source_occurrence occurrence ON occurrence.run_id=run.id
            JOIN platform.source_url url ON url.id=occurrence.source_url_id
            JOIN platform.answer_retrieval_event event ON event.id=occurrence.retrieval_event_id
            WHERE run.pub_id=%s
            """,
            (run_pub_id,),
        ).fetchone()
        capture_event = connection.execute(
            """
            SELECT count(*) FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=(
              SELECT task.pub_id FROM platform.collection_task task
              JOIN platform.collection_run run ON run.id=task.run_id
              WHERE run.pub_id=%s AND task.business_key='uvw-business'
            ) AND event_type='answer.capture.completed'
            """,
            (tenant, run_pub_id),
        ).fetchone()[0]

    assert facts == (
        1,
        2,
        2,
        ["first query", "second query"],
        [1, 2],
        ["entered", "unobserved"],
        canonical_url,
    )
    assert capture_event == 1
    assert run_request["project_pub_id"]


class _FixtureTextStore:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, key: str, expected_sha256: str) -> str:
        assert key == "cas/uvw-content"
        assert hashlib.sha256(self.text.encode()).hexdigest() == expected_sha256
        return self.text


def test_w_and_content_strategy_recalculations_are_immutable_versions() -> None:
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "uvw-w-version-" + secrets.token_hex(8))
        workflow_id, _, run_request = create_run(client, headers)
    run_pub_id = workflow_id.rsplit("/", 1)[-1]
    project_pub_id = str(run_request["project_pub_id"])
    quote = "该页面提供可以逐字回校验的公开产品事实与完整来源说明。"
    source_text = f"页面标题\n{quote}\n补充信息。"
    url = "https://example.com/versioned-w"
    persist_collection_result(
        tenant,
        run_pub_id,
        CollectionTaskResult(
            "uvw-w-business",
            f"根据公开页面，{quote}",
            "screen",
            "accepted",
            citations=[
                {
                    "url": url,
                    "title": "版本化 W 来源",
                    "cited_text": quote,
                    "ordinal": 1,
                }
            ],
            retrieval_events=[
                {
                    "ordinal": 1,
                    "queries": ["版本化 W"],
                    "u_observation": "observed",
                    "v_observation": "observed",
                    "final_reference_observation": "observed",
                    "candidates": [
                        {
                            "url": url,
                            "u_rank": 1,
                            "summary": "这只是搜索摘要，不能作为逐字引用。",
                        }
                    ],
                    "opened_pages": [{"url": url, "v_open_order": 1}],
                    "final_references": [
                        {"url": url, "final_reference_ordinal": 1, "summary": quote}
                    ],
                }
            ],
        ),
        CollectionTaskInput("uvw-w-business", "UVW W question", "deepseek", "CN-BJ", "normal"),
    )
    text_hash = hashlib.sha256(source_text.encode()).hexdigest()
    with psycopg.connect(POSTGRES_DSN) as connection:
        ids = connection.execute(
            """
            SELECT tenant.id,project.id,occurrence.source_url_id
            FROM platform.tenant tenant
            JOIN platform.project project ON project.tenant_id=tenant.id
            JOIN platform.collection_run run ON run.project_id=project.id
            JOIN platform.answer_source_occurrence occurrence ON occurrence.run_id=run.id
            WHERE tenant.pub_id=%s AND project.pub_id=%s AND run.pub_id=%s
            """,
            (tenant, project_pub_id, run_pub_id),
        ).fetchone()
        assert ids is not None
        connection.execute(
            "SELECT set_config('app.tenant_id',%s,true),set_config('app.tenant_pub_id',%s,true)",
            (str(ids[0]), tenant),
        )
        connection.execute(
            """
            INSERT INTO platform.source_page_snapshot
              (id,pub_id,tenant_id,project_id,source_url_id,snapshot_state,
               final_url,http_status,metadata,body_object_key,body_sha256,text_sha256,
               extractor_version,captured_at,created_at)
            VALUES (%s,%s,%s,%s,%s,'succeeded',%s,200,'{}','cas/uvw-content',
                    %s,%s,'integration-exact-v1',now(),now())
            """,
            (
                uuid.uuid4(),
                "snp_" + secrets.token_hex(10),
                ids[0],
                ids[1],
                ids[2],
                url,
                text_hash,
                text_hash,
            ),
        )

    text_store = _FixtureTextStore(source_text)
    for policy_version in ("content-contribution-exact-v1", "content-contribution-exact-v2"):
        contribution = execute_content_contribution(
            ContentContributionInput(
                tenant_pub_id=tenant,
                project_pub_id=project_pub_id,
                run_pub_id=run_pub_id,
                policy_version=policy_version,
            ),
            dsn=POSTGRES_DSN,
            text_store=text_store,  # type: ignore[arg-type]
        )
        assert contribution.analyzed == 1
        assert contribution.confirmed_occurrences == 1
        assert contribution.chunks >= 1
        assert contribution.failures == []
        strategy = execute_content_strategy(
            ContentStrategyInput(
                tenant_pub_id=tenant,
                project_pub_id=project_pub_id,
                run_pub_id=run_pub_id,
                content_contribution_policy_version=policy_version,
            ),
            dsn=POSTGRES_DSN,
            text_store=text_store,  # type: ignore[arg-type]
        )
        assert strategy.u_occurrences == 1
        assert strategy.snapshot_available == 1

    # Same snapshot and occurrence are retained as two W policy versions; the
    # service-5 strategy also retains both frozen-input analyses instead of
    # treating a changed W policy as replay drift.
    with psycopg.connect(POSTGRES_DSN) as connection:
        versions = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM platform.content_contribution_analysis analysis
               WHERE analysis.occurrence_id=occurrence.id),
              (SELECT count(*) FROM platform.weighted_content_chunk chunk
               WHERE chunk.occurrence_id=occurrence.id),
              (SELECT count(*) FROM platform.content_strategy_analysis strategy
               WHERE strategy.run_id=run.id)
            FROM platform.collection_run run
            JOIN platform.answer_source_occurrence occurrence ON occurrence.run_id=run.id
            WHERE run.pub_id=%s
            """,
            (run_pub_id,),
        ).fetchone()
    assert versions is not None
    assert versions[0] == 2
    assert versions[1] >= 2
    assert versions[2] == 2

    # Exercise the real internal drill-down queries against the versioned W
    # facts. This catches project-scope and SQL-parameter regressions that the
    # response-schema unit tests cannot observe.
    base = f"/api/v2/internal/source-intelligence/projects/{project_pub_id}"
    with TestClient(app) as client:
        sites_response = client.get(f"{base}/sites", headers=headers)
        assert sites_response.status_code == 200, sites_response.text
        site = next(item for item in sites_response.json()["data"] if item["host"] == "example.com")
        assert site["u_occurrence_count"] == 1
        assert site["v_count"] == 1
        assert site["w_count"] == 1

        urls_response = client.get(
            f"{base}/sites/{site['site_pub_id']}/urls",
            headers=headers,
        )
        assert urls_response.status_code == 200, urls_response.text
        source_url = next(
            item for item in urls_response.json()["data"] if item["canonical_url"] == url
        )
        url_pub_id = source_url["url_pub_id"]

        detail_response = client.get(f"{base}/urls/{url_pub_id}", headers=headers)
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["latest_snapshot"]["text_sha256"] == text_hash
        assert detail["w_count"] == 1

        snapshots_response = client.get(
            f"{base}/urls/{url_pub_id}/snapshots",
            headers=headers,
        )
        assert snapshots_response.status_code == 200, snapshots_response.text
        assert snapshots_response.json()["data"][0]["text_sha256"] == text_hash

        occurrences_response = client.get(
            f"{base}/urls/{url_pub_id}/occurrences",
            headers=headers,
        )
        assert occurrences_response.status_code == 200, occurrences_response.text
        occurrence = occurrences_response.json()["data"][0]
        assert occurrence["w_state"] == "confirmed"
        assert occurrence["w_weight"] is not None

        answer_response = client.get(
            f"{base}/answers/{occurrence['answer_pub_id']}/uvw",
            headers=headers,
        )
        assert answer_response.status_code == 200, answer_response.text
        answer = answer_response.json()
        assert answer["u_observation"] == "observed"
        assert answer["v_observation"] == "observed"
        assert answer["final_reference_observation"] == "observed"
        assert answer["occurrences"][0]["url_pub_id"] == url_pub_id


class ReconciliationHandle:
    async def describe(self) -> object:
        return SimpleNamespace(status=SimpleNamespace(name="FAILED"))


class ReconciliationTemporal:
    def get_workflow_handle(self, workflow_id: str) -> ReconciliationHandle:
        del workflow_id
        return ReconciliationHandle()


@pytest.mark.asyncio
async def test_post_analysis_workflow_failure_only_fails_analysis_jobs() -> None:
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "analysis-isolation-" + secrets.token_hex(8))
        collection_workflow_id, _, _ = create_run(client, headers)
    run_pub_id = collection_workflow_id.rsplit("/", 1)[-1]
    task_input = CollectionTaskInput(
        "isolated-business", "isolated-query", "fixed", "CN-BJ", "fast"
    )
    persist_collection_result(
        tenant,
        run_pub_id,
        CollectionTaskResult("isolated-business", "captured answer survives", "screen", "accepted"),
        task_input,
    )
    publish_downstream_event(run_pub_id, tenant, [task_input], True)
    post_workflow_id = f"post-collection-analysis/{tenant}/{run_pub_id}"
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            UPDATE integration.workflow_start_command
            SET state='started',temporal_run_id='fixture-run',started_at=now()
            WHERE workflow_id=%s
            """,
            (post_workflow_id,),
        )

    reconciler = WorkflowStartOutbox(
        dsn=POSTGRES_DSN,
        temporal=ReconciliationTemporal(),  # type: ignore[arg-type]
    )
    assert await reconciler.reconcile_one(post_workflow_id)

    with psycopg.connect(POSTGRES_DSN) as connection:
        collection_state = connection.execute(
            "SELECT state FROM platform.collection_run WHERE pub_id=%s",
            (run_pub_id,),
        ).fetchone()
        task_state = connection.execute(
            """
            SELECT task.state
            FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            WHERE run.pub_id=%s
            """,
            (run_pub_id,),
        ).fetchone()
        analysis_states = connection.execute(
            """
            SELECT DISTINCT state,error_code
            FROM platform.analysis_job
            WHERE run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND subject_type='run'
            """,
            (run_pub_id,),
        ).fetchall()
    assert collection_state == ("completed",)
    assert task_state == ("completed",)
    assert set(analysis_states) == {
        ("failed", "workflow_interrupted"),
        ("not_requested", "profile_missing"),
    }


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
