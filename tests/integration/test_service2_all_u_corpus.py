from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi import Response
from geo_platform.identity.policy import Principal, Role
from geo_platform.service2_corpus.analysis_models import model_snapshot
from geo_platform.service2_corpus.router import list_corpus_items, list_manifests
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
            "service2_model_call",
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
    work_pages = []
    work_cursor = None
    while True:
        page = process_service2_corpus_page(
            Service2CorpusPageInput(
                tenant_pub_id=scope.tenant_pub_id,
                project_pub_id=scope.project_pub_id,
                batch_pub_id=receipt.batch_pub_id,
                cursor=work_cursor,
                page_size=1,
            )
        )
        work_pages.append(page)
        if not page.has_more:
            break
        work_cursor = page.next_cursor
    assert [page.processed for page in work_pages] == [1, 1, 1, 1, 1]
    assert all(page.has_more for page in work_pages[:-1])
    assert not work_pages[-1].has_more
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
        with pytest.raises(Conflict, match="service2_processing_incomplete"):
            service.freeze(
                session,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                tenant_pub_id=scope.tenant_pub_id,
                batch_pub_id=receipt.batch_pub_id,
                actor_pub_id="usr_service2_reviewer",
                idempotency_key=freeze_key,
            )
        # Fixture-only projection of completed manual evidence review. The
        # first freeze proves blocked/partial items cannot be frozen; after an
        # operator resolves every item as no-entity/not-applicable, the same
        # batch can become an honest complete manifest.
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_item
                SET processing_state='processed',entity_state='no_entities',
                    judgment_state='not_applicable',review_state='not_applicable',
                    failure_code=NULL,manual_evidence_state='provided',
                    version=version+1,updated_at=now()
                WHERE batch_id=:batch_id AND processing_state<>'processed'
                """
            ),
            {"batch_id": row["id"]},
        )
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
    assert manifest["facts"]["coverage"]["processing_states"] == {"processed": 5}
    assert manifest["facts"]["cases"] == []

    shanghai = ZoneInfo("Asia/Shanghai")
    with WorkerSessionLocal() as session:
        exact_options = list_manifests(
            project_pub_id=scope.project_pub_id,
            window_start=(scope.boundary - timedelta(days=1)).astimezone(shanghai).date(),
            window_end=scope.boundary.astimezone(shanghai).date(),
            limit=50,
            principal=principal,
            session=session,
        )
        no_match = list_manifests(
            project_pub_id=scope.project_pub_id,
            window_start=(scope.boundary - timedelta(days=3)).astimezone(shanghai).date(),
            window_end=scope.boundary.astimezone(shanghai).date(),
            limit=50,
            principal=principal,
            session=session,
        )
    assert len(exact_options) == 1
    assert exact_options[0].manifest_pub_id == manifest["pub_id"]
    assert exact_options[0].manifest_hash == manifest["manifest_hash"]
    assert exact_options[0].batch_pub_id == receipt.batch_pub_id
    assert no_match == []


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


def test_entitlement_must_cover_the_closed_window_for_create_start_resume_and_retry(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    window_start = scope.boundary - timedelta(days=1)
    window_end = scope.boundary
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        session.execute(
            text(
                """
                UPDATE platform.project_service_entitlement
                SET authorized_from=:window_start,authorized_until=:window_end,updated_at=now()
                WHERE tenant_id=:tenant_id AND project_id=:project_id
                  AND service_code='outbound_disparagement_audit'
                """
            ),
            {
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "window_start": window_start,
                "window_end": window_end,
            },
        )
        receipt = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-entitlement-exact-{uuid.uuid4().hex}",
            body=BatchCreate(
                run_pub_ids=scope.run_pub_ids,
                window_start=window_start,
                window_end=window_end,
                source_snapshot_boundary=scope.boundary,
            ),
        )

        with pytest.raises(Conflict, match="service2_entitlement_required"):
            service.create_batch(
                session,
                tenant_id=scope.tenant_id,
                tenant_pub_id=scope.tenant_pub_id,
                project_id=scope.project_id,
                project_pub_id=scope.project_pub_id,
                actor_pub_id="usr_service2_test",
                idempotency_key=f"service2-entitlement-partial-{uuid.uuid4().hex}",
                body=BatchCreate(
                    run_pub_ids=scope.run_pub_ids,
                    window_start=window_start - timedelta(microseconds=1),
                    window_end=window_end,
                    source_snapshot_boundary=scope.boundary,
                ),
            )

        session.execute(
            text(
                """
                UPDATE platform.project_service_entitlement
                SET authorized_from=:partial_start,updated_at=now()
                WHERE tenant_id=:tenant_id AND project_id=:project_id
                  AND service_code='outbound_disparagement_audit'
                """
            ),
            {
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "partial_start": window_start + timedelta(microseconds=1),
            },
        )
        for action, state in (("start", "draft"), ("resume", "paused"), ("retry", "failed")):
            session.execute(
                text(
                    """
                    UPDATE platform.service2_corpus_batch SET status=:state,updated_at=now()
                    WHERE tenant_id=:tenant_id AND pub_id=:batch_pub_id
                    """
                ),
                {
                    "tenant_id": scope.tenant_id,
                    "batch_pub_id": receipt.batch_pub_id,
                    "state": state,
                },
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
                    idempotency_key=f"service2-entitlement-{action}-{uuid.uuid4().hex}",
                    action=action,
                    task_queue="unused-analysis-queue",
                    source_task_queue="unused-source-queue",
                )
        session.commit()


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


def test_retry_child_success_replaces_only_its_root_query_failure(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    root_run_id = uuid.uuid4()
    root_run_pub_id = f"run_s2u_retry_root_{uuid.uuid4().hex[:8]}"
    retry_run_id = uuid.uuid4()
    retry_run_pub_id = f"run_s2u_retry_child_{uuid.uuid4().hex[:8]}"
    success_business_key = f"retry-success-{uuid.uuid4().hex}"
    failed_business_key = f"retry-failed-{uuid.uuid4().hex}"
    root_success_task_id = uuid.uuid4()
    root_failed_task_id = uuid.uuid4()
    retry_success_task_id = uuid.uuid4()
    captured_at = scope.boundary - timedelta(minutes=2)
    matrix_json = json.dumps(
        {
            "query": "重试链查询",
            "adapter": "doubao",
            "model": "fixed-model",
            "region": "CN-SH",
            "mode": "normal",
        },
        ensure_ascii=False,
    )
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        config_version_id = session.execute(
            text(
                """
                SELECT version.id
                FROM platform.monitoring_config_version version
                JOIN platform.monitoring_config config ON config.id=version.config_id
                WHERE version.tenant_id=:tenant_id AND config.project_id=:project_id
                ORDER BY version.created_at DESC LIMIT 1
                """
            ),
            {"tenant_id": scope.tenant_id, "project_id": scope.project_id},
        ).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO platform.collection_run
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,
                   config_version_id,idempotency_key,workflow_id,state,total_tasks,
                   completed_tasks,failed_tasks,paused,retry_of_run_pub_id,source)
                VALUES
                  (:root_id,:root_pub_id,:tenant_id,1,:captured_at,:captured_at,:project_id,
                   :config_id,:root_idem,:root_workflow,'completed_with_failures',2,1,1,
                   false,NULL,'manual'),
                  (:retry_id,:retry_pub_id,:tenant_id,1,:captured_at,:captured_at,:project_id,
                   :config_id,:retry_idem,:retry_workflow,'completed',1,1,0,
                   false,:root_pub_id,'retry')
                """
            ),
            {
                "root_id": root_run_id,
                "root_pub_id": root_run_pub_id,
                "retry_id": retry_run_id,
                "retry_pub_id": retry_run_pub_id,
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "config_id": config_version_id,
                "captured_at": captured_at,
                "root_idem": f"service2-retry-root-{uuid.uuid4().hex}",
                "retry_idem": f"service2-retry-child-{uuid.uuid4().hex}",
                "root_workflow": f"service2/retry/root/{uuid.uuid4().hex}",
                "retry_workflow": f"service2/retry/child/{uuid.uuid4().hex}",
            },
        )
        session.execute(
            text(
                """
                INSERT INTO platform.collection_task
                  (id,pub_id,tenant_id,version,created_at,updated_at,terminal_at,run_id,
                   business_key,matrix_json,state,attempt_count,answer_text,quality_state)
                VALUES
                  (:root_success_id,:root_success_pub,:tenant_id,1,:captured_at,:captured_at,
                   :captured_at,:root_run_id,:success_key,:matrix,'completed',1,
                   'root success',NULL),
                  (:root_failed_id,:root_failed_pub,:tenant_id,1,:captured_at,:captured_at,
                   :captured_at,:root_run_id,:failed_key,:matrix,'failed',1,NULL,
                   'provider_timeout'),
                  (:retry_success_id,:retry_success_pub,:tenant_id,1,:captured_at,:captured_at,
                   :captured_at,:retry_run_id,:failed_key,:matrix,'completed',1,
                   'retry success',NULL)
                """
            ),
            {
                "root_success_id": root_success_task_id,
                "root_success_pub": new_pub_id("ans"),
                "root_failed_id": root_failed_task_id,
                "root_failed_pub": new_pub_id("ans"),
                "retry_success_id": retry_success_task_id,
                "retry_success_pub": new_pub_id("ans"),
                "tenant_id": scope.tenant_id,
                "captured_at": captured_at,
                "root_run_id": root_run_id,
                "retry_run_id": retry_run_id,
                "success_key": success_business_key,
                "failed_key": failed_business_key,
                "matrix": matrix_json,
            },
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
                  (:first_id,:first_pub,:tenant_id,:project_id,:root_run_id,
                   :root_success_task_id,NULL,:source_id,1,'首轮成功',:raw_url,'observed',1,
                   'not_entered',NULL,'not_referenced',NULL,'no_evidence','title',NULL,NULL,
                   :captured_at,:captured_at),
                  (:second_id,:second_pub,:tenant_id,:project_id,:retry_run_id,
                   :retry_success_task_id,NULL,:source_id,1,'重试成功',:raw_url,'observed',1,
                   'not_entered',NULL,'not_referenced',NULL,'no_evidence','title',NULL,NULL,
                   :captured_at,:captured_at)
                """
            ),
            {
                "first_id": uuid.uuid4(),
                "first_pub": new_pub_id("aso"),
                "second_id": uuid.uuid4(),
                "second_pub": new_pub_id("aso"),
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "root_run_id": root_run_id,
                "retry_run_id": retry_run_id,
                "root_success_task_id": root_success_task_id,
                "retry_success_task_id": retry_success_task_id,
                "source_id": source["id"],
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
            idempotency_key=f"service2-retry-lineage-{uuid.uuid4().hex}",
            body=BatchCreate(
                # Selecting root and child together must still mean one logical
                # run scope, not three query rows or a duplicated occurrence.
                run_pub_ids=[root_run_pub_id, retry_run_pub_id],
                window_start=scope.boundary - timedelta(days=1),
                window_end=scope.boundary,
                source_snapshot_boundary=scope.boundary,
            ),
        )
        batch = service.batch_row(
            session,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            batch_pub_id=receipt.batch_pub_id,
        )
        coverage = service.coverage(session, tenant_id=scope.tenant_id, batch_id=batch["id"])
        ledger = (
            session.execute(
                text(
                    """
                SELECT root_run_pub_id,run_pub_id,business_key,retry_depth,outcome
                FROM platform.service2_corpus_batch_query
                WHERE batch_id=:batch_id ORDER BY business_key
                """
                ),
                {"batch_id": batch["id"]},
            )
            .mappings()
            .all()
        )
        linked_runs = list(
            session.execute(
                text(
                    """
                    SELECT run_pub_id FROM platform.service2_corpus_batch_run
                    WHERE batch_id=:batch_id ORDER BY ordinal
                    """
                ),
                {"batch_id": batch["id"]},
            ).scalars()
        )
        session.commit()

    assert coverage["selected_queries"] == 2
    assert coverage["successful_queries"] == 2
    assert coverage["failed_queries"] == 0
    assert coverage["expected_occurrences"] == 2
    assert linked_runs == [root_run_pub_id]
    assert {row["root_run_pub_id"] for row in ledger} == {root_run_pub_id}
    assert {
        (row["business_key"], row["run_pub_id"], row["retry_depth"], row["outcome"])
        for row in ledger
    } == {
        (success_business_key, root_run_pub_id, 0, "succeeded"),
        (failed_business_key, retry_run_pub_id, 1, "succeeded"),
    }


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


def test_paid_model_call_claim_is_single_and_replays_stored_response(
    seeded_all_u_scope: Any,
) -> None:
    scope = seeded_all_u_scope
    service = Service2CorpusService()
    body = BatchCreate(
        run_pub_ids=scope.run_pub_ids,
        window_start=scope.boundary - timedelta(days=1),
        window_end=scope.boundary,
        source_snapshot_boundary=scope.boundary,
    )
    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        receipt = service.create_batch(
            session,
            tenant_id=scope.tenant_id,
            tenant_pub_id=scope.tenant_pub_id,
            project_id=scope.project_id,
            project_pub_id=scope.project_pub_id,
            actor_pub_id="usr_service2_test",
            idempotency_key=f"service2-paid-call-{uuid.uuid4().hex}",
            body=body,
        )
        item = (
            session.execute(
                text(
                    """
                    SELECT item.* FROM platform.service2_corpus_item item
                    JOIN platform.service2_corpus_batch batch ON batch.id=item.batch_id
                    WHERE batch.pub_id=:batch_pub_id AND item.snapshot_id IS NOT NULL
                    ORDER BY item.pub_id LIMIT 1
                    """
                ),
                {"batch_pub_id": receipt.batch_pub_id},
            )
            .mappings()
            .one()
        )
        frozen_catalog = model_snapshot("gpt-5.6-luna")
        call, claimed = service.claim_model_call(
            session,
            item=item,
            snapshot_id=item["snapshot_id"],
            input_hash="a" * 64,
            model="gpt-5.6-luna",
            prompt_version="service2-relation-web-search-v2",
            policy_version="service2-entity-relation-v1",
            catalog_snapshot=frozen_catalog,
        )
        call_pub_id = str(call["pub_id"])
        session.commit()
        frozen_catalog["provider"] = "mutated-after-claim"
    assert claimed

    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        item = (
            session.execute(
                text(
                    """
                    SELECT item.* FROM platform.service2_corpus_item item
                    JOIN platform.service2_corpus_batch batch ON batch.id=item.batch_id
                    WHERE batch.pub_id=:batch_pub_id AND item.snapshot_id IS NOT NULL
                    ORDER BY item.pub_id LIMIT 1
                    """
                ),
                {"batch_pub_id": receipt.batch_pub_id},
            )
            .mappings()
            .one()
        )
        replay_before_response, claimed_again = service.claim_model_call(
            session,
            item=item,
            snapshot_id=item["snapshot_id"],
            input_hash="a" * 64,
            model="gpt-5.6-luna",
            prompt_version="service2-relation-web-search-v2",
            policy_version="service2-entity-relation-v1",
            catalog_snapshot=model_snapshot("gpt-5.6-luna"),
        )
        assert not claimed_again
        assert replay_before_response["state"] == "claimed"
        service.complete_model_call(
            session,
            call_pub_id=call_pub_id,
            data={"findings": []},
            sources=[{"url": "https://docs.python.org/", "title": "Python docs"}],
            usage={"input_tokens": 100, "output_tokens": 20},
            transport="responses",
            resolved_model="gpt-5.6-luna",
            provider_request_id="req_fixture",
            provider_response_id="resp_fixture",
            resolved_provider="openai",
            provider_resolution_source="provider_response",
            gateway_host="api.inferera.com",
            protocol_route="/v1/responses",
            web_search_observed=True,
            search_event_count=1,
            provider_citation_count=1,
            source_origin="provider_citation",
        )
        session.commit()

    with WorkerSessionLocal() as session:
        TenantRepository(session, scope.tenant_pub_id)
        item = (
            session.execute(
                text(
                    """
                    SELECT item.* FROM platform.service2_corpus_item item
                    JOIN platform.service2_corpus_batch batch ON batch.id=item.batch_id
                    WHERE batch.pub_id=:batch_pub_id AND item.snapshot_id IS NOT NULL
                    ORDER BY item.pub_id LIMIT 1
                    """
                ),
                {"batch_pub_id": receipt.batch_pub_id},
            )
            .mappings()
            .one()
        )
        replay, third_claim = service.claim_model_call(
            session,
            item=item,
            snapshot_id=item["snapshot_id"],
            input_hash="a" * 64,
            model="gpt-5.6-luna",
            prompt_version="service2-relation-web-search-v2",
            policy_version="service2-entity-relation-v1",
            catalog_snapshot=model_snapshot("gpt-5.6-luna"),
        )
        call_count = session.execute(
            text(
                """
                SELECT count(*) FROM platform.service2_model_call
                WHERE corpus_item_id=:item_id AND input_hash=:input_hash
                """
            ),
            {"item_id": item["id"], "input_hash": "a" * 64},
        ).scalar_one()
        session.commit()
    assert not third_claim
    assert replay["state"] == "succeeded"
    assert replay["response_data"] == {"findings": []}
    assert replay["response_sources"] == [
        {"url": "https://docs.python.org/", "title": "Python docs"}
    ]
    assert replay["catalog_snapshot"]["provider"] == "gpt"
    assert replay["catalog_revision"] == model_snapshot("gpt-5.6-luna")["catalog_revision"]
    assert replay["catalog_provider"] == "gpt"
    assert replay["resolved_provider"] == "openai"
    assert replay["resolved_model"] == "gpt-5.6-luna"
    assert replay["transport"] == "responses"
    assert replay["protocol_route"] == "/v1/responses"
    assert replay["gateway_host"] == "api.inferera.com"
    assert replay["provider_request_id"] == "req_fixture"
    assert replay["provider_response_id"] == "resp_fixture"
    assert replay["input_tokens"] == 100
    assert replay["output_tokens"] == 20
    assert replay["web_search_observed"] is True
    assert replay["search_event_count"] == 1
    assert replay["provider_citation_count"] == 1
    assert replay["source_origin"] == "provider_citation"
    assert str(replay["estimated_token_cost_usd"]) == "0.0000440000"
    assert replay["estimated_search_cost_usd"] is None
    assert replay["estimated_total_cost_usd"] is None
    assert replay["cost_completeness"] == "token_only_search_price_unknown"
    assert replay["audit_completeness"] == "complete"
    assert call_count == 1


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
    assert "service2_model_call" in {row["relname"] for row in rows}
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)
