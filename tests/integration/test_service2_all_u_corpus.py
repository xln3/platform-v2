from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from fastapi import Response
from geo_platform.identity.policy import Principal, Role
from geo_platform.service2_corpus.router import list_corpus_items
from geo_platform.service2_corpus.schemas import BatchCreate, FindingCreate
from geo_platform.service2_corpus.service import Conflict, NotFound, Service2CorpusService
from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.repository import TenantRepository
from psycopg.rows import dict_row
from sqlalchemy import text

from workflows.activities.service2_source_corpus import (
    Service2BatchInput,
    Service2CorpusPageInput,
    finish_service2_corpus_batch,
    prepare_service2_corpus_batch,
    process_service2_corpus_page,
)

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


@pytest.fixture()
def seeded_all_u_scope() -> Any:
    suffix = uuid.uuid4().hex[:10]
    tenant_id = uuid.uuid4()
    tenant_pub_id = f"tnt_s2u_{suffix}"
    customer_id = uuid.uuid4()
    project_id = uuid.uuid4()
    project_pub_id = f"prj_s2u_{suffix}"
    config_id = uuid.uuid4()
    config_version_id = uuid.uuid4()
    boundary = datetime.now(UTC) - timedelta(seconds=2)
    captured_at = boundary - timedelta(minutes=2)
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    run_pub_ids = [f"run_s2u_a_{suffix}", f"run_s2u_b_{suffix}"]
    task_ids = [uuid.uuid4() for _ in range(5)]
    task_pub_ids = [new_pub_id("ans") for _ in range(5)]
    site_id = uuid.uuid4()
    source_urls = [
        (uuid.uuid4(), new_pub_id("url"), "https://shared.example.com/article"),
        (uuid.uuid4(), new_pub_id("url"), "https://facts.example.com/article"),
        (uuid.uuid4(), new_pub_id("url"), "https://blocked.example.com/article"),
    ]
    occurrence_source_indexes = [0, 0, 1, 0, 2]
    occurrence_run_indexes = [0, 0, 0, 1, 1]
    matrix = json.dumps(
        {
            "query": "品牌比较",
            "adapter": "doubao",
            "model": "fixed-model",
            "region": "CN-SH",
        },
        ensure_ascii=False,
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at) "
            "VALUES (%s,%s,'Service2 all U','active',now(),now())",
            (tenant_id, tenant_pub_id),
        )
        connection.execute(
            "INSERT INTO platform.customer "
            "(id,pub_id,tenant_id,version,created_at,updated_at,name) "
            "VALUES (%s,%s,%s,1,now(),now(),'Service2 customer')",
            (customer_id, new_pub_id("cus"), tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.project (id,pub_id,tenant_id,version,created_at,updated_at,"
            "customer_id,name,state) VALUES (%s,%s,%s,1,now(),now(),%s,"
            "'Service2 project','active')",
            (project_id, project_pub_id, tenant_id, customer_id),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,state,current_version) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,'frozen',1)",
            (config_id, new_pub_id("mcg"), tenant_id, project_id),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config_version (id,pub_id,tenant_id,version,"
            "created_at,updated_at,config_id,revision,effective_at,snapshot_json,snapshot_hash) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,1,%s,'{}',%s)",
            (
                config_version_id,
                new_pub_id("mcv"),
                tenant_id,
                config_id,
                captured_at,
                "1" * 64,
            ),
        )
        for index, (run_id, run_pub_id) in enumerate(zip(run_ids, run_pub_ids, strict=True)):
            task_count = occurrence_run_indexes.count(index)
            connection.execute(
                "INSERT INTO platform.collection_run (id,pub_id,tenant_id,version,created_at,"
                "updated_at,project_id,config_version_id,idempotency_key,workflow_id,state,"
                "total_tasks,completed_tasks,failed_tasks,paused) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'completed',%s,%s,0,false)",
                (
                    run_id,
                    run_pub_id,
                    tenant_id,
                    captured_at,
                    captured_at,
                    project_id,
                    config_version_id,
                    f"s2u-run-{index}-{suffix}",
                    f"s2u/workflow/{index}/{suffix}",
                    task_count,
                    task_count,
                ),
            )
        for index, (task_id, task_pub_id) in enumerate(zip(task_ids, task_pub_ids, strict=True)):
            run_id = run_ids[occurrence_run_indexes[index]]
            connection.execute(
                "INSERT INTO platform.collection_task (id,pub_id,tenant_id,version,created_at,"
                "updated_at,run_id,business_key,matrix_json,state,attempt_count,answer_text) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,'done',1,'answer')",
                (
                    task_id,
                    task_pub_id,
                    tenant_id,
                    captured_at,
                    captured_at,
                    run_id,
                    f"s2u-task-{index}-{suffix}",
                    matrix,
                ),
            )
        connection.execute(
            "INSERT INTO platform.source_site "
            "(id,pub_id,tenant_id,host,created_at,updated_at) "
            "VALUES (%s,%s,%s,'example.com',now(),now())",
            (site_id, new_pub_id("sit"), tenant_id),
        )
        for source_id, source_pub_id, url in source_urls:
            connection.execute(
                "INSERT INTO platform.source_url "
                "(id,pub_id,tenant_id,site_id,canonical_url,canonical_url_hash,"
                "normalization_version,first_raw_url,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'url-normalization-v1',%s,now(),now())",
                (
                    source_id,
                    source_pub_id,
                    tenant_id,
                    site_id,
                    url,
                    sha256(url.encode()).hexdigest(),
                    url,
                ),
            )
        for index in range(5):
            source_id, _source_pub_id, url = source_urls[occurrence_source_indexes[index]]
            run_id = run_ids[occurrence_run_indexes[index]]
            connection.execute(
                "INSERT INTO platform.answer_source_occurrence "
                "(id,pub_id,tenant_id,project_id,run_id,answer_task_id,retrieval_event_id,"
                "source_url_id,occurrence_ordinal,query_text,raw_url,u_state,u_rank,v_state,"
                "v_open_order,final_reference_state,final_reference_ordinal,w_state,title,"
                "summary,evidence_pub_id,captured_at,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,1,'品牌比较',%s,'observed',1,"
                "'not_entered',NULL,'not_referenced',NULL,'no_evidence','title',NULL,NULL,%s,%s)",
                (
                    uuid.uuid4(),
                    new_pub_id("aso"),
                    tenant_id,
                    project_id,
                    run_id,
                    task_ids[index],
                    source_id,
                    url,
                    captured_at,
                    captured_at,
                ),
            )
        for index, (source_id, _source_pub_id, url) in enumerate(source_urls):
            state = "blocked" if index == 2 else "succeeded"
            digest = f"{index + 2:x}" * 64
            connection.execute(
                "INSERT INTO platform.source_page_snapshot "
                "(id,pub_id,tenant_id,project_id,source_url_id,source_document_id,"
                "fetch_attempt_id,snapshot_state,final_url,http_status,title,site_name,author,"
                "account_name,published_at,metadata,body_object_key,body_sha256,text_sha256,"
                "extractor_version,captured_at,created_at) "
                "VALUES (%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,'title','example',NULL,NULL,NULL,"
                "'{}'::jsonb,%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    new_pub_id("snp"),
                    tenant_id,
                    project_id,
                    source_id,
                    state,
                    url,
                    403 if state == "blocked" else 200,
                    None if state == "blocked" else f"cas/{digest}",
                    None if state == "blocked" else digest,
                    None if state == "blocked" else digest,
                    None if state == "blocked" else "density-extract-v1",
                    captured_at + timedelta(seconds=30),
                    captured_at + timedelta(seconds=30),
                ),
            )
        connection.execute(
            "INSERT INTO platform.project_service_entitlement "
            "(id,pub_id,tenant_id,project_id,service_code,catalog_version,state,"
            "authorized_from,authorized_until,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,'outbound_disparagement_audit','quotation_services_v2',"
            "'active',NULL,NULL,now(),now())",
            (uuid.uuid4(), new_pub_id("ent"), tenant_id, project_id),
        )

    yield SimpleNamespace(
        tenant_id=tenant_id,
        tenant_pub_id=tenant_pub_id,
        project_id=project_id,
        project_pub_id=project_pub_id,
        run_pub_ids=run_pub_ids,
        boundary=boundary,
    )

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SET session_replication_role='replica'")
        for table in (
            "service2_fact_manifest",
            "service2_batch_event",
            "service2_finding_review",
            "service2_relation_finding",
            "service2_analysis_attempt",
            "service2_corpus_item",
            "service2_corpus_batch_query",
            "service2_corpus_batch_run",
            "service2_corpus_batch",
            "source_page_snapshot",
            "answer_source_occurrence",
            "project_service_entitlement",
            "collection_task",
            "collection_run",
            "monitoring_config_version",
            "monitoring_config",
            "source_url",
            "source_site",
            "project",
            "customer",
        ):
            connection.execute(f"DELETE FROM platform.{table} WHERE tenant_id=%s", (tenant_id,))
        connection.execute("DELETE FROM platform.tenant WHERE id=%s", (tenant_id,))
        connection.execute("SET session_replication_role='origin'")


def test_materialization_pagination_and_fetch_shards_keep_the_all_u_denominator(
    seeded_all_u_scope: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = seeded_all_u_scope
    body = BatchCreate(
        run_pub_ids=scope.run_pub_ids,
        window_start=scope.boundary - timedelta(days=1),
        window_end=scope.boundary,
        source_snapshot_boundary=scope.boundary,
    )
    service = Service2CorpusService()
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        receipt = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-create-{uuid.uuid4().hex}",
            body=body,
        )
        row = service.batch_row(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            batch_pub_id=receipt.batch_pub_id,
        )
        coverage = service.coverage(session, tenant_id=scope.tenant_id, batch_id=row["id"])
        session.commit()

    assert coverage["expected_occurrences"] == 5
    assert coverage["materialized_items"] == 5
    assert coverage["distinct_urls"] == 3
    assert coverage["processing_states"] == {"blocked": 1, "queued": 4}
    assert coverage["selected_queries"] == 5
    assert coverage["successful_queries"] == 5
    assert coverage["failed_queries"] == 0
    assert coverage["query_outcomes_complete"] is True
    assert coverage["query_coverage_complete"] is True

    principal = Principal(
        subject="service2-reviewer",
        role=Role.REVIEWER,
        tenant_pub_id=scope.tenant_pub_id,
        user_pub_id="usr_service2_reviewer",
    )
    with WorkerSessionLocal() as session:
        first_response = Response()
        first = list_corpus_items(
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            response=first_response,
            cursor=None,
            page_size=4,
            processing_state=None,
            fetch_state=None,
            review_state=None,
            attribution_confidence=None,
            principal=principal,
            session=session,
        )
        second = list_corpus_items(
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            response=Response(),
            cursor=first.next_cursor,
            page_size=4,
            processing_state=None,
            fetch_state=None,
            review_state=None,
            attribution_confidence=None,
            principal=principal,
            session=session,
        )
        unknown = list_corpus_items(
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            response=Response(),
            cursor=None,
            page_size=100,
            processing_state=None,
            fetch_state=None,
            review_state=None,
            attribution_confidence="unknown",
            principal=principal,
            session=session,
        )

    assert len(first.data) == 4 and first.has_more
    assert len(second.data) == 1 and not second.has_more
    assert {item.item_pub_id for item in first.data}.isdisjoint(
        {item.item_pub_id for item in second.data}
    )
    assert first.all_u_total == second.all_u_total == 5
    assert first_response.headers["X-All-U-Total"] == "5"
    assert unknown.filtered_count == unknown.all_u_total == 5

    preparation = prepare_service2_corpus_batch(
        Service2BatchInput(
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
        )
    )
    shard_urls = [
        source_url for shard in preparation.fetch_shards for source_url in shard.source_url_pub_ids
    ]
    assert len(shard_urls) == len(set(shard_urls)) == 3

    monkeypatch.setattr(
        "workflows.activities.service2_source_corpus.activity.heartbeat",
        lambda *_args: None,
    )
    first_work_page = process_service2_corpus_page(
        Service2CorpusPageInput(
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            page_size=4,
        )
    )
    second_work_page = process_service2_corpus_page(
        Service2CorpusPageInput(
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            cursor=first_work_page.next_cursor,
            page_size=4,
        )
    )
    assert first_work_page.processed == 4 and first_work_page.has_more
    assert second_work_page.processed == 1 and not second_work_page.has_more
    assert (
        finish_service2_corpus_batch(
            Service2BatchInput(
                tenant_pub_id=scope.tenant_pub_id,
                project_pub_id=scope.project_pub_id,
                batch_pub_id=receipt.batch_pub_id,
            )
        )
        == "review"
    )

    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        freeze_key = f"service2-freeze-{uuid.uuid4().hex}"
        manifest, replayed = service.freeze(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            tenant_pub_id=scope.tenant_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            actor_pub_id="usr_service2_reviewer",
            idempotency_key=freeze_key,
        )
        replay_manifest, replayed_again = service.freeze(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            tenant_pub_id=scope.tenant_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            actor_pub_id="usr_service2_reviewer",
            idempotency_key=freeze_key,
        )
        second = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-create-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=scope.run_pub_ids,
                window_start=scope.boundary - timedelta(days=1) + timedelta(seconds=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        with pytest.raises(Conflict, match="idempotency_key_payload_conflict"):
            service.freeze(
                session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                tenant_pub_id=scope.tenant_pub_id,
                batch_pub_id=second.batch_pub_id,
                actor_pub_id="usr_service2_reviewer",
                idempotency_key=freeze_key,
            )
        session.commit()
    assert not replayed
    assert replayed_again
    assert replay_manifest["manifest_hash"] == manifest["manifest_hash"]
    assert manifest["facts"]["coverage"]["expected_occurrences"] == 5
    assert manifest["facts"]["coverage"]["distinct_urls"] == 3
    assert manifest["facts"]["coverage"]["processing_states"] == {
        "blocked": 1,
        "manual_evidence_required": 4,
    }
    assert manifest["facts"]["cases"] == []


def test_draft_cancel_is_terminal_idempotent_and_cannot_freeze(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    action_key = f"service2-cancel-{uuid.uuid4().hex}"
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        receipt = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-create-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=scope.run_pub_ids,
                window_start=scope.boundary - timedelta(days=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        session.execute(
            text(
                """
                UPDATE platform.project_service_entitlement
                SET state='suspended',updated_at=now()
                WHERE tenant_id=:tenant_id AND project_id=:project_id
                  AND service_code='outbound_disparagement_audit'
                """
            ),
            {"tenant_id": scope.tenant_id, "project_id": scope.project_id},
        )
        with pytest.raises(Conflict, match="service2_entitlement_inactive"):
            service.transition(
                session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                tenant_pub_id=scope.tenant_pub_id,
                project_pub_id=scope.project_pub_id,
                batch_pub_id=receipt.batch_pub_id,
                actor_pub_id="usr_service2_test",
                idempotency_key=f"service2-start-{uuid.uuid4().hex}",
                action="start",
                task_queue="unused-analysis-queue",
                source_task_queue="unused-source-queue",
            )
        session.execute(
            text(
                """
                UPDATE platform.project_service_entitlement
                SET state='active',updated_at=now()
                WHERE tenant_id=:tenant_id AND project_id=:project_id
                  AND service_code='outbound_disparagement_audit'
                """
            ),
            {"tenant_id": scope.tenant_id, "project_id": scope.project_id},
        )
        status, version, replayed = service.transition(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=action_key,
            action="cancel",
            task_queue="unused-analysis-queue",
            source_task_queue="unused-source-queue",
        )
        replay_status, replay_version, was_replayed = service.transition(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            batch_pub_id=receipt.batch_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=action_key,
            action="cancel",
            task_queue="unused-analysis-queue",
            source_task_queue="unused-source-queue",
        )
        row = service.batch_row(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            batch_pub_id=receipt.batch_pub_id,
        )
        processing = dict(
            session.execute(
                text(
                    """
                    SELECT processing_state,count(*)::int
                    FROM platform.service2_corpus_item
                    WHERE batch_id=:batch_id GROUP BY processing_state
                    """
                ),
                {"batch_id": row["id"]},
            ).all()
        )
        with pytest.raises(Conflict, match="service2_freeze_not_allowed_from_cancelled"):
            service.freeze(
                session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                tenant_pub_id=scope.tenant_pub_id,
                batch_pub_id=receipt.batch_pub_id,
                actor_pub_id="usr_service2_reviewer",
                idempotency_key=f"service2-freeze-{uuid.uuid4().hex}",
            )
        session.commit()

    assert status == replay_status == "cancelled"
    assert replayed is False and was_replayed is True
    assert version == replay_version
    assert processing == {"blocked": 1, "cancelled": 4}


def test_batch_scope_rejects_a_run_that_is_not_terminal(seeded_all_u_scope: Any) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    running_run_id = uuid.uuid4()
    running_run_pub_id = f"run_s2u_running_{uuid.uuid4().hex[:10]}"
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        session.execute(
            text(
                """
                INSERT INTO platform.collection_run
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,
                   config_version_id,idempotency_key,workflow_id,state,total_tasks,
                   completed_tasks,failed_tasks,paused)
                SELECT :run_id,:run_pub_id,:tenant_id,1,:created_at,:created_at,:project_id,
                       version.id,:idempotency_key,:workflow_id,'running',0,0,0,false
                FROM platform.monitoring_config_version version
                JOIN platform.monitoring_config config ON config.id=version.config_id
                WHERE version.tenant_id=:tenant_id AND config.project_id=:project_id
                ORDER BY version.created_at DESC LIMIT 1
                """
            ),
            {
                "run_id": running_run_id,
                "run_pub_id": running_run_pub_id,
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "created_at": scope.boundary - timedelta(minutes=1),
                "idempotency_key": f"service2-running-{uuid.uuid4().hex}",
                "workflow_id": f"service2/running/{uuid.uuid4().hex}",
            },
        )
        with pytest.raises(Conflict, match="service2_runs_must_be_terminal"):
            service.create_batch(
                session,
                tenant_id=scope.tenant_id,
                tenant_pub_id=scope.tenant_pub_id,
                project_id=scope.project_id,
                project_pub_id=scope.project_pub_id,
                actor_pub_id="usr_service2_test",
                idempotency_key=f"service2-create-{uuid.uuid4().hex}",
                body=BatchCreate(
                    run_pub_ids=[running_run_pub_id],
                    window_start=scope.boundary - timedelta(days=1),
                    window_end=scope.boundary,
                    source_snapshot_boundary=scope.boundary,
                ),
            )
        session.commit()


def test_terminal_partial_run_keeps_successful_query_u_and_ledgers_failed_queries(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    partial_run_id = uuid.uuid4()
    partial_run_pub_id = f"run_s2u_partial_{uuid.uuid4().hex[:10]}"
    task_ids = [uuid.uuid4() for _ in range(4)]
    task_pub_ids = [new_pub_id("ans") for _ in range(4)]
    captured_at = scope.boundary - timedelta(minutes=2)
    matrix_json = json.dumps(
        {
            "query": "独立查询",
            "adapter": "doubao",
            "model": "fixed-model",
            "region": "CN-SH",
        },
        ensure_ascii=False,
    )
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        session.execute(
            text(
                """
                INSERT INTO platform.collection_run
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,
                   config_version_id,idempotency_key,workflow_id,state,total_tasks,
                   completed_tasks,failed_tasks,paused,error_code)
                SELECT :run_id,:run_pub_id,:tenant_id,1,:created_at,:created_at,:project_id,
                       version.id,:idempotency_key,:workflow_id,'completed_with_failures',
                       4,1,3,false,'partial_failure'
                FROM platform.monitoring_config_version version
                JOIN platform.monitoring_config config ON config.id=version.config_id
                WHERE version.tenant_id=:tenant_id AND config.project_id=:project_id
                ORDER BY version.created_at DESC LIMIT 1
                """
            ),
            {
                "run_id": partial_run_id,
                "run_pub_id": partial_run_pub_id,
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "created_at": captured_at,
                "idempotency_key": f"service2-partial-run-{uuid.uuid4().hex}",
                "workflow_id": f"service2/partial/{uuid.uuid4().hex}",
            },
        )
        session.execute(
            text(
                """
                INSERT INTO platform.collection_task
                  (id,pub_id,tenant_id,version,created_at,updated_at,run_id,business_key,
                   matrix_json,state,attempt_count,answer_text)
                VALUES
                  (:id,:pub_id,:tenant_id,1,:created_at,:created_at,:run_id,:business_key,
                   :matrix_json,:state,1,:answer_text)
                """
            ),
            [
                {
                    "id": task_id,
                    "pub_id": task_pub_id,
                    "tenant_id": scope.tenant_id,
                    "created_at": captured_at + timedelta(seconds=index),
                    "run_id": partial_run_id,
                    "business_key": f"service2-partial-task-{index}-{uuid.uuid4().hex}",
                    "matrix_json": matrix_json,
                    "state": "done" if index == 0 else "failed",
                    "answer_text": "成功查询回答" if index == 0 else None,
                }
                for index, (task_id, task_pub_id) in enumerate(
                    zip(task_ids, task_pub_ids, strict=True)
                )
            ],
        )
        source = (
            session.execute(
                text(
                    """
                    SELECT id,canonical_url FROM platform.source_url
                    WHERE tenant_id=:tenant_id ORDER BY created_at,pub_id LIMIT 1
                    """
                ),
                {"tenant_id": scope.tenant_id},
            )
            .mappings()
            .one()
        )
        session.execute(
            text(
                """
                INSERT INTO platform.answer_source_occurrence
                  (id,pub_id,tenant_id,project_id,run_id,answer_task_id,retrieval_event_id,
                   source_url_id,occurrence_ordinal,query_text,raw_url,u_state,u_rank,v_state,
                   v_open_order,final_reference_state,final_reference_ordinal,w_state,title,
                   summary,evidence_pub_id,captured_at,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:run_id,:answer_task_id,NULL,
                   :source_url_id,1,'成功查询',:raw_url,'observed',1,'not_entered',
                   NULL,'not_referenced',NULL,'no_evidence','title',NULL,NULL,
                   :captured_at,:captured_at)
                """
            ),
            {
                "id": uuid.uuid4(),
                "pub_id": new_pub_id("aso"),
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "run_id": partial_run_id,
                "answer_task_id": task_ids[0],
                "source_url_id": source["id"],
                "raw_url": source["canonical_url"],
                "captured_at": captured_at,
            },
        )

        receipt = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-partial-create-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=[partial_run_pub_id],
                window_start=scope.boundary - timedelta(days=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        row = service.batch_row(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            batch_pub_id=receipt.batch_pub_id,
        )
        coverage = service.coverage(session, tenant_id=scope.tenant_id, batch_id=row["id"])
        admitted_task_states = list(
            session.execute(
                text(
                    """
                    SELECT task.state FROM platform.service2_corpus_item item
                    JOIN platform.collection_task task ON task.id=item.answer_task_id
                    WHERE item.batch_id=:batch_id ORDER BY item.pub_id
                    """
                ),
                {"batch_id": row["id"]},
            ).scalars()
        )
        query_rows = session.execute(
            text(
                """
                SELECT outcome,count(*)::int
                FROM platform.service2_corpus_batch_query
                WHERE batch_id=:batch_id GROUP BY outcome ORDER BY outcome
                """
            ),
            {"batch_id": row["id"]},
        ).all()
        session.commit()

    assert coverage["selected_queries"] == 4
    assert coverage["successful_queries"] == 1
    assert coverage["failed_queries"] == 3
    assert coverage["successful_queries_with_u"] == 1
    assert coverage["successful_queries_without_u"] == 0
    assert coverage["query_failure_codes"] == {"query_failed": 3}
    assert coverage["query_outcomes_complete"] is True
    assert coverage["query_coverage_complete"] is False
    assert coverage["expected_occurrences"] == 1
    assert coverage["materialized_items"] == 1
    assert admitted_task_states == ["done"]
    assert dict(query_rows) == {"failed": 3, "succeeded": 1}


def test_finding_item_must_belong_to_the_requested_batch_before_audit_write(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        first = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-create-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=scope.run_pub_ids,
                window_start=scope.boundary - timedelta(days=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        second = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-create-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=scope.run_pub_ids,
                window_start=scope.boundary - timedelta(days=1) + timedelta(seconds=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        first_item = (
            session.execute(
                text(
                    """
                SELECT item.pub_id,snapshot.pub_id AS snapshot_pub_id
                FROM platform.service2_corpus_item item
                JOIN platform.service2_corpus_batch batch ON batch.id=item.batch_id
                LEFT JOIN platform.source_page_snapshot snapshot ON snapshot.id=item.snapshot_id
                WHERE batch.pub_id=:batch_pub_id AND snapshot.id IS NOT NULL
                ORDER BY item.pub_id LIMIT 1
                """
                ),
                {"batch_pub_id": first.batch_pub_id},
            )
            .mappings()
            .one()
        )
        with pytest.raises(NotFound, match="service2_corpus_item_not_found"):
            service.create_finding(
                session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                batch_pub_id=second.batch_pub_id,
                body=FindingCreate(
                    corpus_item_pub_id=str(first_item["pub_id"]),
                    snapshot_pub_id=str(first_item["snapshot_pub_id"]),
                    ledger="statement",
                    level="L1",
                    relation_direction="target_negative",
                    textual_speaker="页面作者",
                    target_entity="目标品牌",
                    is_disparagement=False,
                    fact_anchor_state="present",
                    evidence_quote="目标品牌",
                    quote_start=0,
                    quote_end=4,
                    context_text="目标品牌",
                    context_start=0,
                    context_end=4,
                    snapshot_text_sha256="a" * 64,
                    flags={"direct_target_negative": True},
                    method="human",
                    model="human-review",
                    prompt_version="human-v1",
                    confidence=1,
                ),
            )
        attempts = session.execute(
            text(
                """
                SELECT count(*) FROM platform.service2_analysis_attempt attempt
                JOIN platform.service2_corpus_batch batch ON batch.id=attempt.batch_id
                WHERE batch.pub_id IN (:first_batch,:second_batch)
                """
            ),
            {"first_batch": first.batch_pub_id, "second_batch": second.batch_pub_id},
        ).scalar_one()
        session.commit()
    assert attempts == 0


def test_service2_tables_force_rls_in_the_running_database() -> None:
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='platform' AND c.relkind='r'
              AND c.relname LIKE 'service2_%'
            ORDER BY c.relname
            """
        ).fetchall()
    assert len(rows) == 9
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)
