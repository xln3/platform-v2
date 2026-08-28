from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from geo_platform.metrics_v2.repository import MetricsV2Repository

from domain.analysis.v2 import load_builtin_task_definitions
from domain.analysis.v2.decision_models import SemanticDecisionRecord, subject_key_for
from domain.metrics.v2 import validate_metric_definition
from tools.seed_metrics_v2_definitions import SeedArtifact, build_seed_bundle, seed

from .metrics_v2_fixtures import digest, snapshot_row, snapshot_set_row

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_repository_persists_reads_publishes_and_reuses_an_atomic_set() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    set_row = snapshot_set_row(token)
    persisted = repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=set_row,
        snapshots=[snapshot_row(token)],
    )
    assert persisted == {
        "snapshot_set_pub_id": f"mss_{token}",
        "snapshot_set_hash": digest(f"set:{token}"),
        "reused": False,
    }
    reused = repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=set_row,
        snapshots=[snapshot_row(token)],
    )
    assert reused["reused"] is True
    document = repository.get_snapshot_set(tenant_pub_id=tenant, set_pub_id=f"mss_{token}")
    assert document["snapshot_set_hash"] == digest(f"set:{token}")
    assert document["metrics"][0]["value"] == 1
    publication = repository.publish_snapshot_set_cas(
        tenant_pub_id=tenant,
        set_pub_id=f"mss_{token}",
        publication_channel="shadow",
        expected_generation=0,
        expected_snapshot_set_hash=digest(f"set:{token}"),
        published_by="actor_test",
    )
    assert publication["generation"] == 1
    current = repository.current_snapshot_set(
        tenant_pub_id=tenant,
        project_pub_id=project,
        publication_channel="shadow",
    )
    assert current["snapshot_set_pub_id"] == f"mss_{token}"
    with pytest.raises(LookupError):
        repository.get_snapshot_set(tenant_pub_id=f"tnt_other_{token}", set_pub_id=f"mss_{token}")


def test_metric_definition_lifecycle_preserves_hash_and_loader_contract() -> None:
    token = uuid4().hex[:16]
    source = next(item for item in build_seed_bundle() if item.kind == "metric_definition")
    document = deepcopy(source.document)
    document["name"] = f"lifecycle_probe_{token}"
    document["status"] = "experimental"
    document.pop("definition_hash", None)
    staged = validate_metric_definition(document)
    document = dict(staged.raw_definition)
    document["status"] = "experimental"
    document["definition_hash"] = staged.definition_hash
    artifact = SeedArtifact(
        kind="metric_definition",
        name=staged.name,
        version=staged.version,
        content_hash=staged.definition_hash,
        document=document,
    )

    report = seed(POSTGRES_DSN, (artifact,))
    assert report["inserted"] == 1
    with psycopg.connect(POSTGRES_DSN) as connection:
        before = connection.execute(
            """
            SELECT definition,definition_hash,status
            FROM analytics.metric_definition WHERE name=%s AND version=%s
            """,
            (artifact.name, artifact.version),
        ).fetchone()
        assert before is not None
        assert before[0]["status"] == "experimental"
        connection.execute(
            """
            UPDATE analytics.metric_definition
            SET status='published',published_at=%s,experimental=false
            WHERE name=%s AND version=%s AND definition_hash=%s
            """,
            (
                datetime(2026, 8, 27, tzinfo=UTC),
                artifact.name,
                artifact.version,
                artifact.content_hash,
            ),
        )
        after = connection.execute(
            """
            SELECT definition,definition_hash,status
            FROM analytics.metric_definition WHERE name=%s AND version=%s
            """,
            (artifact.name, artifact.version),
        ).fetchone()
    assert after is not None
    assert after[0] == before[0]
    assert after[1] == before[1] == artifact.content_hash
    assert after[2] == "published"

    frozen = MetricsV2Repository(POSTGRES_DSN).load_snapshot_build_inputs(
        tenant_pub_id=f"tnt_{token}",
        project_pub_id=f"prj_{token}",
        scope={
            "window": {"start": "2026-08-01", "end": "2026-08-02"},
            "filters": {"model": [], "region": [], "mode": []},
            "focal_entity_ids": [f"entity-{token}"],
        },
        as_of="2026-08-03T00:00:00+00:00",
        definition_refs=(
            {
                "name": artifact.name,
                "version": artifact.version,
                "definition_hash": artifact.content_hash,
            },
        ),
    )
    assert len(frozen["definition_documents"]) == 1
    loaded = frozen["definition_documents"][0]
    assert loaded["status"] == "published"
    assert validate_metric_definition(loaded).definition_hash == artifact.content_hash
    assert frozen["definition_refs"] == [
        {
            "name": artifact.name,
            "version": artifact.version,
            "definition_hash": artifact.content_hash,
        }
    ]
    assert len(frozen["metric_definition_set_hash"]) == 64


def test_official_publication_rejects_a_missing_supporting_decision_reference() -> None:
    token = uuid4().hex[:16]
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    entity = f"entity-{token}"
    answer_pub_id = f"ans_{token}"
    query_pub_id = f"qry_{token}"
    context_pub_id = f"qcf_{token}"
    manifest_pub_id = f"asm_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    artifacts = build_seed_bundle()
    seed(POSTGRES_DSN, artifacts)
    source = next(item for item in artifacts if item.kind == "metric_definition")
    document = deepcopy(source.document)
    document["name"] = f"official_dependency_probe_{token}"
    document["status"] = "experimental"
    document.pop("definition_hash", None)
    staged = validate_metric_definition(document)
    document = dict(staged.raw_definition)
    document["status"] = "experimental"
    document["definition_hash"] = staged.definition_hash
    seed(
        POSTGRES_DSN,
        (
            SeedArtifact(
                kind="metric_definition",
                name=staged.name,
                version=staged.version,
                content_hash=staged.definition_hash,
                document=document,
            ),
        ),
    )
    task_refs = {
        str(item["task_ref"]) for item in document.get("required_semantic_capabilities", [])
    } | set(map(str, document.get("decision_task_refs", [])))
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            UPDATE analytics.semantic_decision_task_definition_v2
            SET status='published',published_at=%s
            WHERE name || '@' || version=ANY(%s::text[]) AND status='experimental'
            """,
            (datetime(2026, 8, 27, tzinfo=UTC), sorted(task_refs)),
        )
        connection.execute(
            """
            UPDATE analytics.metric_definition
            SET status='published',published_at=%s,experimental=false
            WHERE name=%s AND version=%s AND definition_hash=%s
            """,
            (
                datetime(2026, 8, 27, tzinfo=UTC),
                staged.name,
                staged.version,
                staged.definition_hash,
            ),
        )
        captured_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
        connection.execute(
            """
            INSERT INTO analytics.answer
              (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,
               response_text,model,region,mode,eligible,degraded,channel,
               adapter_version,capture_time,response_raw,
               response_markdown_normalized,response_ast,response_html_sanitized,
               response_plain_text,response_hash,render_parser_version)
            VALUES
              (%s,%s,%s,%s,%s,%s,'fixture-model','cn','api',true,false,'api',
               'fixture-v1',%s,'{}',%s,'[]'::jsonb,%s,%s,%s,'fixture-parser-v1')
            """,
            (
                answer_pub_id,
                tenant,
                project,
                query_pub_id,
                f"official dependency query {token}",
                f"official dependency answer {token}",
                captured_at,
                f"official dependency answer {token}",
                f"official dependency answer {token}",
                f"official dependency answer {token}",
                digest(f"answer:{token}"),
            ),
        )
    repository.persist_query_context_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        fact={
            "pub_id": context_pub_id,
            "query_key": digest(f"query-key:{token}"),
            "query_pub_id": query_pub_id,
            "query_text_hash": digest(f"query-text:{token}"),
            "primary_lens": "ai_recommendation",
            "analysis_lenses": ["ai_recommendation"],
            "requested_operations": ["recommend"],
            "query_subtypes": [],
            "detected_entity_ids": [entity],
            "brand_structure_type": "single_brand_named",
            "classification_state": "ready",
            "classifier_version": "fixture-v2",
            "decision_task_bundle_hash": digest(f"task-bundle:{token}"),
            "entity_dictionary_hash": digest(f"entity-dictionary:{token}"),
            "classification_source": "historical_backfill",
            "derivation_method": "deterministic",
            "decision_record_pub_ids": [],
            "review_status": "approved",
            "fact_hash": digest(f"context:{token}"),
        },
        exposures=[],
    )
    repository.persist_semantic_manifest_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        manifest={
            "pub_id": manifest_pub_id,
            "answer_pub_id": answer_pub_id,
            "analysis_run_pub_id": f"arun_{token}",
            "query_context_fact_pub_id": context_pub_id,
            "answer_text_hash": digest(f"answer:{token}"),
            "input_hash": digest(f"manifest-input:{token}"),
            "extractor_bundle": {},
            "decision_task_bundle": {},
            "extractor_bundle_hash": digest(f"extractor-bundle:{token}"),
            "decision_task_bundle_hash": digest(f"task-bundle:{token}"),
            "entity_dictionary_hash": digest(f"entity-dictionary:{token}"),
            "status": "ready",
            "capability_statuses": {},
            "decision_record_pub_ids": [],
            "decision_set_hash": digest(f"decision-set:{token}"),
            "completed_at": datetime(2026, 8, 1, 13, tzinfo=UTC),
        },
        events=[],
    )
    set_row = snapshot_set_row(token)
    metric_row = snapshot_row(token)
    metric_row.update(
        {
            "metric_name": staged.name,
            "metric_version": staged.version,
            "metric_definition_hash": staged.definition_hash,
        }
    )
    repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=set_row,
        snapshots=[metric_row],
        contributions=[
            {
                "pub_id": f"mct_{token}",
                "snapshot_pub_id": f"msn_{token}",
                "answer_pub_id": answer_pub_id,
                "query_key": digest(f"query-key:{token}"),
                "focal_entity_id": entity,
                "metric_name": staged.name,
                "metric_version": staged.version,
                "model": "fixture-model",
                "region": "cn",
                "mode": "api",
                "capture_time": datetime(2026, 8, 1, 12, tzinfo=UTC),
                "eligibility_status": "included_hit",
                "reason_codes": ["fixture_hit"],
                "outcome_value": {"hit": True},
                "numerator_contribution": 1,
                "denominator_contribution": 1,
                "query_weight": 1,
                "design_cell_weight": 1,
                "repeat_weight": 1,
                "final_weight": 1,
                "weighted_numerator": 1,
                "weighted_denominator": 1,
                "query_context_fact_pub_id": context_pub_id,
                "semantic_manifest_pub_id": manifest_pub_id,
                "supporting_event_pub_ids": [],
                "supporting_decision_pub_ids": [f"sdr_missing_{token}"],
                "semantic_decision_set_hash": digest(f"decision-set:{token}"),
                "dimension_snapshot": {},
                "answer_detail_ref": f"/answers/{answer_pub_id}",
                "contribution_hash": digest(f"contribution:{token}"),
            }
        ],
    )

    with pytest.raises(RuntimeError, match="metrics_v2_official_dependency_not_published"):
        repository.publish_snapshot_set_cas(
            tenant_pub_id=tenant,
            set_pub_id=f"mss_{token}",
            publication_channel="official",
            expected_generation=0,
            expected_snapshot_set_hash=digest(f"set:{token}"),
            published_by=f"usr_{token}",
        )
    with psycopg.connect(POSTGRES_DSN) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM analytics.metric_publication_v2
            WHERE tenant_pub_id=%s AND publication_channel='official'
            """,
            (tenant,),
        ).fetchone() == (0,)


def test_snapshot_request_is_idempotent_and_enqueues_one_job() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    scope = {"window": {"start": "2026-08-01", "end": "2026-08-02"}}
    repository = MetricsV2Repository(POSTGRES_DSN)
    first = repository.request_snapshot(
        tenant_pub_id=tenant,
        project_pub_id=project,
        scope=scope,
        scope_hash=digest(f"scope:{token}"),
        idempotency_key=f"client-idempotency-{token}",
        requested_by="actor_test",
    )
    second = repository.request_snapshot(
        tenant_pub_id=tenant,
        project_pub_id=project,
        scope=scope,
        scope_hash=digest(f"scope:{token}"),
        idempotency_key=f"client-idempotency-{token}",
        requested_by="actor_test",
    )
    assert first["status"] == "pending"
    assert second["job_pub_id"] == first["job_pub_id"]
    assert second["reused"] is True
    with psycopg.connect(POSTGRES_DSN) as connection:
        outbox_count = connection.execute(
            """
            SELECT count(*) FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
              AND event_type='metric.snapshot_set.requested.v2'
            """,
            (tenant, first["job_pub_id"]),
        ).fetchone()
        command = connection.execute(
            """
            SELECT workflow_type,workflow_id,task_queue,payload
            FROM integration.workflow_start_command
            WHERE tenant_pub_id=%s AND workflow_id=%s
            """,
            (tenant, f"metrics-v2:{first['job_pub_id']}"),
        ).fetchone()
    assert outbox_count == (1,)
    assert command is not None
    assert command[:3] == (
        "metric_snapshot_set_v2",
        f"metrics-v2:{first['job_pub_id']}",
        "geo-platform-v2-metrics",
    )
    assert command[3]["job_pub_id"] == first["job_pub_id"]


def test_decision_request_and_completion_are_atomic_and_idempotent() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    task = load_builtin_task_definitions().get("substantive-entity-mention@2.1.0")
    artifacts = build_seed_bundle()
    seed(POSTGRES_DSN, artifacts)
    policy_hash = next(
        item.content_hash
        for item in artifacts
        if item.kind == "judge_policy" and task.task_ref in item.document["compatible_task_refs"]
    )
    subject_ref = {"answer_pub_id": f"ans_{token}", "entity_id": f"entity-{token}"}
    request = repository.create_decision_request(
        tenant_pub_id=tenant,
        project_pub_id=project,
        task_ref=task.task_ref,
        subject_ref=subject_ref,
        input_snapshot_ref=f"answer:{token}",
        input_hash=digest(f"input:{token}"),
        context_hash=digest(f"context:{token}"),
        judge_policy_hash=policy_hash,
        idempotency_key=f"decision-request:{token}",
    )
    replayed_request = repository.create_decision_request(
        tenant_pub_id=tenant,
        project_pub_id=project,
        task_ref=task.task_ref,
        subject_ref=subject_ref,
        input_snapshot_ref=f"answer:{token}",
        input_hash=digest(f"input:{token}"),
        context_hash=digest(f"context:{token}"),
        judge_policy_hash=policy_hash,
        idempotency_key=f"different-client-token:{token}",
    )
    assert replayed_request["decision_job_pub_id"] == request["decision_job_pub_id"]
    assert replayed_request["reused"] is True

    now = datetime.now(UTC)
    attempt_pub_id = f"sda_{token}"
    decision_pub_id = f"sdr_{token}"
    accepted_result = {
        "entity_id": f"entity-{token}",
        "surface": "fixture entity",
        "substantive": True,
        "mention_role": "asserted_body",
        "start": 0,
        "end": 1,
        "excerpt_hash": digest(f"excerpt:{token}"),
        "reason_codes": [],
    }
    attempt = {
        "pub_id": attempt_pub_id,
        "attempt_index": 0,
        "role": "proposer",
        "method": "deterministic",
        "inference_config": {},
        "prompt_template_ref": task.prompt_template_ref,
        "prompt_template_hash": task.prompt_template_hash,
        "rubric_hash": task.rubric_hash,
        "output_schema_hash": digest(f"schema:{token}"),
        "request_payload_hash": digest(f"request:{token}"),
        "response_payload_hash": digest(f"response:{token}"),
        "validated_output": accepted_result,
        "validation_status": "valid",
        "reason_codes": ("fixture_attempt",),
        "created_at": now,
    }
    decision = {
        "decision_pub_id": decision_pub_id,
        "task_name": task.name,
        "task_version": task.version,
        "task_definition_hash": task.definition_hash,
        "subject_type": task.subject_type.value,
        "subject_key": subject_key_for(subject_ref),
        "subject_ref": subject_ref,
        "input_snapshot_ref": f"answer:{token}",
        "input_hash": digest(f"input:{token}"),
        "context_hash": digest(f"context:{token}"),
        "method": "deterministic",
        "status": "accepted",
        "result": accepted_result,
        "rationale_summary": "bounded evidence-backed reason",
        "calibrated_confidence": "0.99",
        "calibration_bucket": "high",
        "reason_codes": ("fixture_decision",),
        "evidence_refs": [],
        "evidence_spans": [],
        "selected_attempt_pub_ids": (attempt_pub_id,),
        "judge_policy_hash": policy_hash,
        "rubric_ref": task.rubric_ref,
        "rubric_hash": task.rubric_hash,
        "output_schema_hash": digest(f"schema:{token}"),
        "decision_hash": "",
        "created_at": now,
    }
    decision["decision_hash"] = SemanticDecisionRecord.model_validate(
        decision
        | {
            "tenant_pub_id": tenant,
            "project_pub_id": project,
            "decision_job_pub_id": request["decision_job_pub_id"],
        }
    ).decision_hash
    completed = repository.persist_decision_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        decision_job_pub_id=request["decision_job_pub_id"],
        attempts=[attempt],
        decision=decision,
        workflow_id=f"workflow-{token}",
        run_id=f"run-{token}",
    )
    replayed = repository.persist_decision_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        decision_job_pub_id=request["decision_job_pub_id"],
        attempts=[attempt],
        decision=decision,
        workflow_id=f"workflow-{token}",
        run_id=f"run-{token}",
    )
    assert completed["status"] == "succeeded"
    assert completed["decision_pub_id"] == decision_pub_id
    assert replayed["reused"] is True
    with psycopg.connect(POSTGRES_DSN) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM analytics.semantic_decision_attempt_v2
               WHERE tenant_pub_id=%s AND decision_job_pub_id=%s),
              (SELECT count(*) FROM analytics.semantic_decision_record_v2
               WHERE tenant_pub_id=%s AND decision_job_pub_id=%s),
              (SELECT count(*) FROM integration.outbox_event
               WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
                 AND event_type='semantic.decision.completed.v2'),
              (SELECT count(*) FROM integration.workflow_start_command
               WHERE tenant_pub_id=%s AND workflow_id=%s)
            """,
            (
                tenant,
                request["decision_job_pub_id"],
                tenant,
                request["decision_job_pub_id"],
                tenant,
                decision_pub_id,
                tenant,
                f"decision-v2:{request['decision_job_pub_id']}",
            ),
        ).fetchone()
    assert counts == (1, 1, 1, 1)
    with pytest.raises(LookupError):
        repository.get_semantic_decision(
            tenant_pub_id=f"tnt_other_{token}", decision_pub_id=decision_pub_id
        )
    entity = f"entity-{token}"
    answer_pub_id = f"ans_{token}"
    query_pub_id = f"qry_{token}"
    context_pub_id = f"qcf_{token}"
    manifest_pub_id = f"asm_{token}"
    query_key = digest(f"query-key:{token}")
    answer_hash = digest(f"answer:{token}")
    decision_set_hash = digest(f"decision-set:{token}")
    captured_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO analytics.answer
              (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,
               response_text,model,region,mode,eligible,degraded,channel,
               adapter_version,capture_time,response_raw,
               response_markdown_normalized,response_ast,response_html_sanitized,
               response_plain_text,response_hash,render_parser_version)
            VALUES
              (%s,%s,%s,%s,%s,%s,'fixture-model','cn','api',true,false,'api',
               'fixture-v1',%s,'{}',%s,'[]'::jsonb,%s,%s,%s,'fixture-parser-v1')
            """,
            (
                answer_pub_id,
                tenant,
                project,
                query_pub_id,
                f"fixture query {token}",
                "fixture entity is recommended",
                captured_at,
                "fixture entity is recommended",
                "fixture entity is recommended",
                "fixture entity is recommended",
                answer_hash,
            ),
        )
    repository.persist_query_context_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        fact={
            "pub_id": context_pub_id,
            "query_key": query_key,
            "query_pub_id": query_pub_id,
            "query_text_hash": digest(f"query-text:{token}"),
            "primary_lens": "ai_recommendation",
            "analysis_lenses": ["ai_recommendation"],
            "requested_operations": ["recommend"],
            "query_subtypes": [],
            "detected_entity_ids": [entity],
            "brand_structure_type": "single_brand_named",
            "classification_state": "ready",
            "classifier_version": "fixture-v2",
            "decision_task_bundle_hash": digest(f"task-bundle:{token}"),
            "entity_dictionary_hash": digest(f"entity-dictionary:{token}"),
            "classification_source": "historical_backfill",
            "derivation_method": "deterministic",
            "decision_record_pub_ids": [],
            "review_status": "approved",
            "fact_hash": digest(f"query-context:{token}"),
            "created_at": captured_at,
        },
        exposures=[],
    )
    repository.persist_semantic_manifest_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        manifest={
            "pub_id": manifest_pub_id,
            "answer_pub_id": answer_pub_id,
            "analysis_run_pub_id": f"arun_{token}",
            "query_context_fact_pub_id": context_pub_id,
            "answer_text_hash": answer_hash,
            "input_hash": digest(f"manifest-input:{token}"),
            "extractor_bundle": {},
            "decision_task_bundle": {},
            "extractor_bundle_hash": digest(f"extractor-bundle:{token}"),
            "decision_task_bundle_hash": digest(f"task-bundle:{token}"),
            "entity_dictionary_hash": digest(f"entity-dictionary:{token}"),
            "status": "ready",
            "capability_statuses": {
                "substantive_entity_mention": {
                    "status": "ready",
                    "decision_record_pub_ids": [decision_pub_id],
                    "reason_codes": [],
                }
            },
            "decision_record_pub_ids": [decision_pub_id],
            "decision_set_hash": decision_set_hash,
            "completed_at": now,
        },
        events=[],
    )
    set_row = snapshot_set_row(token)
    metric_row = snapshot_row(token)
    repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=set_row,
        snapshots=[metric_row],
        contributions=[
            {
                "pub_id": f"mct_{token}",
                "snapshot_pub_id": metric_row["pub_id"],
                "answer_pub_id": answer_pub_id,
                "query_key": query_key,
                "focal_entity_id": entity,
                "metric_name": metric_row["metric_name"],
                "metric_version": metric_row["metric_version"],
                "model": "fixture-model",
                "region": "cn",
                "mode": "api",
                "capture_time": captured_at,
                "eligibility_status": "included_hit",
                "reason_codes": ["fixture_hit"],
                "outcome_value": {"hit": True},
                "numerator_contribution": 1,
                "denominator_contribution": 1,
                "query_weight": 1,
                "design_cell_weight": 1,
                "repeat_weight": 1,
                "final_weight": 1,
                "weighted_numerator": 1,
                "weighted_denominator": 1,
                "query_context_fact_pub_id": context_pub_id,
                "semantic_manifest_pub_id": manifest_pub_id,
                "supporting_event_pub_ids": [],
                "supporting_decision_pub_ids": [decision_pub_id],
                "semantic_decision_set_hash": decision_set_hash,
                "dimension_snapshot": {},
                "answer_detail_ref": f"/answers/{answer_pub_id}",
                "contribution_hash": digest(f"contribution:{token}"),
            }
        ],
    )
    correction = {
        **accepted_result,
        "substantive": False,
        "mention_role": "prompt_echo",
        "start": None,
        "end": None,
        "excerpt_hash": None,
        "reason_codes": ["customer_fact_correction"],
    }
    with pytest.raises(LookupError):
        repository.create_override(
            tenant_pub_id=tenant,
            project_pub_id=f"prj_other_{token}",
            decision_pub_id=decision_pub_id,
            result=correction,
            rationale_summary="wrong project must remain invisible",
            reason_codes=("customer_fact_correction",),
            expected_decision_hash=decision["decision_hash"],
            actor_pub_id=f"usr_{token}",
        )
    with pytest.raises(LookupError):
        repository.create_override(
            tenant_pub_id=f"tnt_other_{token}",
            project_pub_id=project,
            decision_pub_id=decision_pub_id,
            result=correction,
            rationale_summary="cross tenant must remain invisible",
            reason_codes=("customer_fact_correction",),
            expected_decision_hash=decision["decision_hash"],
            actor_pub_id=f"usr_{token}",
        )
    override = repository.create_override(
        tenant_pub_id=tenant,
        project_pub_id=project,
        decision_pub_id=decision_pub_id,
        result=correction,
        rationale_summary="The matched surface is only a prompt echo.",
        reason_codes=("customer_fact_correction",),
        expected_decision_hash=decision["decision_hash"],
        actor_pub_id=f"usr_{token}",
    )
    with pytest.raises(RuntimeError, match="metrics_v2_decision_already_superseded"):
        repository.create_override(
            tenant_pub_id=tenant,
            project_pub_id=project,
            decision_pub_id=decision_pub_id,
            result=correction,
            rationale_summary="stale retry",
            reason_codes=("customer_fact_correction",),
            expected_decision_hash=decision["decision_hash"],
            actor_pub_id=f"usr_{token}",
        )
    with psycopg.connect(POSTGRES_DSN) as connection:
        successor = connection.execute(
            """
            SELECT method,status,result,supersedes_pub_id
            FROM analytics.semantic_decision_record_v2
            WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
            """,
            (tenant, project, override["decision_pub_id"]),
        ).fetchone()
        recompute = connection.execute(
            """
            SELECT status,scope,requested_by
            FROM analytics.metric_recompute_job_v2
            WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
            """,
            (tenant, project, override["recompute_job_pub_id"]),
        ).fetchone()
        workflow = connection.execute(
            """
            SELECT workflow_type,task_queue,payload
            FROM integration.workflow_start_command
            WHERE tenant_pub_id=%s AND workflow_id=%s
            """,
            (tenant, f"metrics-v2:{override['recompute_job_pub_id']}"),
        ).fetchone()
        refreshed_manifest = connection.execute(
            """
            SELECT pub_id,decision_record_pub_ids,decision_set_hash,supersedes_pub_id,event_count
            FROM analytics.answer_semantic_manifest_v2
            WHERE tenant_pub_id=%s AND project_pub_id=%s AND supersedes_pub_id=%s
            """,
            (tenant, project, manifest_pub_id),
        ).fetchone()
    assert successor == ("human", "accepted", correction, decision_pub_id)
    assert recompute is not None
    assert recompute[0] == "pending"
    assert recompute[1] == {
        "window": {"start": "2026-08-01", "end": "2026-08-02"},
        "filters": {"model": [], "region": [], "mode": []},
        "focal_entity_ids": [entity],
        "aggregation_method": "query_macro",
        "design_basis": "planned_cells",
    }
    assert recompute[2] == f"usr_{token}"
    assert workflow is not None
    assert workflow[:2] == ("metric_snapshot_set_v2", "geo-platform-v2-metrics")
    assert workflow[2]["project_pub_id"] == project
    assert workflow[2]["scope"] == recompute[1]
    assert refreshed_manifest is not None
    assert refreshed_manifest[1] == [override["decision_pub_id"]]
    assert refreshed_manifest[2] != decision_set_hash
    assert refreshed_manifest[3:] == (manifest_pub_id, 0)

    loaded = repository.load_snapshot_build_inputs(
        tenant_pub_id=tenant,
        project_pub_id=project,
        scope=recompute[1],
        as_of=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    )
    assert len(loaded["subjects"]) == 1
    loaded_subject = loaded["subjects"][0]
    loaded_decision = loaded_subject["decisions"][task.task_ref]
    assert loaded_subject["semantic_manifest_pub_id"] == refreshed_manifest[0]
    assert loaded_subject["semantic_decision_set_hash"] == refreshed_manifest[2]
    assert loaded_subject["events"] == []
    assert loaded_decision["decision_pub_id"] == override["decision_pub_id"]
    assert loaded_decision["method"] == "human"
    assert loaded_decision["value"] == correction


def test_recompute_claim_and_finish_are_compare_and_swap_safe() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    requested = repository.request_recompute(
        tenant_pub_id=tenant,
        project_pub_id=project,
        window={"start": "2026-08-01", "end": "2026-08-02"},
        focal_entity_ids=[f"entity-{token}"],
        trigger_reason="integration_test",
        idempotency_key=f"recompute-request:{token}",
        requested_by="actor_test",
    )
    claimed = repository.claim_recompute_job(
        tenant_pub_id=tenant,
        job_pub_id=requested["job_pub_id"],
        workflow_id=f"workflow-{token}",
        run_id=f"run-{token}",
    )
    assert claimed["status"] == "running"
    repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=snapshot_set_row(token),
        snapshots=[snapshot_row(token)],
    )
    finished = repository.finish_recompute_job(
        tenant_pub_id=tenant,
        job_pub_id=requested["job_pub_id"],
        status="succeeded",
        snapshot_set_pub_id=f"mss_{token}",
        input_count=1,
        output_count=1,
    )
    replayed = repository.finish_recompute_job(
        tenant_pub_id=tenant,
        job_pub_id=requested["job_pub_id"],
        status="succeeded",
        snapshot_set_pub_id=f"mss_{token}",
        input_count=1,
        output_count=1,
    )
    assert finished["status"] == "succeeded"
    assert replayed["reused"] is True


def test_decision_backfill_builds_reference_only_work_and_filters_unknowns() -> None:
    token = uuid4().hex[:12]
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    ready_answer = f"ans_a_{token}"
    unknown_answer = f"ans_b_{token}"
    analysis_run = f"arun_{token}"
    other_analysis_run = f"arun_other_{token}"
    exact_citation = f"cit_exact_{token}"
    wrong_run_citation = f"cit_wrong_run_{token}"
    late_citation = f"cit_late_{token}"
    query_text = f"which managed brand is suitable {token}"
    response_text = f"bounded response body {token}"
    captured_at = datetime.now(UTC)
    tenant_id = uuid4()
    customer_id = uuid4()
    project_id = uuid4()
    brand_id = uuid4()
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant
              (id,pub_id,name,state,environment,created_at,updated_at)
            VALUES (%s,%s,%s,'active','production',%s,%s)
            """,
            (tenant_id, tenant, f"tenant-{token}", captured_at, captured_at),
        )
        connection.execute(
            """
            INSERT INTO platform.customer
              (id,pub_id,tenant_id,name,version,created_at,updated_at)
            VALUES (%s,%s,%s,%s,1,%s,%s)
            """,
            (
                customer_id,
                f"cus_{token}",
                tenant_id,
                f"customer-{token}",
                captured_at,
                captured_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.project
              (id,pub_id,tenant_id,customer_id,name,state,version,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,'active',1,%s,%s)
            """,
            (
                project_id,
                project,
                tenant_id,
                customer_id,
                f"project-{token}",
                captured_at,
                captured_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.brand
              (id,pub_id,tenant_id,project_id,name,version,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,1,%s,%s)
            """,
            (
                brand_id,
                f"brd_{token}",
                tenant_id,
                project_id,
                f"managed-{token}",
                captured_at,
                captured_at,
            ),
        )
        for answer_pub_id in (ready_answer, unknown_answer):
            answer_hash = digest(f"response:{answer_pub_id}")
            connection.execute(
                """
                INSERT INTO analytics.answer
                  (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,
                   response_text,model,region,mode,eligible,degraded,channel,
                   adapter_version,capture_time,response_raw,
                   response_markdown_normalized,response_ast,response_html_sanitized,
                   response_plain_text,response_hash,render_parser_version)
                VALUES
                  (%s,%s,%s,%s,%s,%s,'fixture-model','cn','api',true,false,'api',
                   'fixture-v1',%s,'{}',%s,'[]'::jsonb,%s,%s,%s,'fixture-parser-v1')
                """,
                (
                    answer_pub_id,
                    tenant,
                    project,
                    f"qry_{token}",
                    query_text,
                    response_text,
                    captured_at,
                    response_text,
                    response_text,
                    response_text,
                    answer_hash,
                ),
            )
        connection.execute(
            """
            INSERT INTO analytics.analysis_run
              (pub_id,tenant_pub_id,input_hash,scorer_version,metric_version,
               model_version,status)
            VALUES (%s,%s,%s,'fixture-scorer','v2','fixture-model','ready')
            """,
            (analysis_run, tenant, digest(f"analysis:{token}")),
        )
        connection.execute(
            """
            INSERT INTO analytics.analysis_run
              (pub_id,tenant_pub_id,input_hash,scorer_version,metric_version,
               model_version,status)
            VALUES (%s,%s,%s,'fixture-scorer','v2','fixture-model','ready')
            """,
            (other_analysis_run, tenant, digest(f"other-analysis:{token}")),
        )
        connection.execute(
            """
            INSERT INTO analytics.answer_analysis
              (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,mentioned,
               recommended,channel,adapter_version,capture_time)
            VALUES (%s,%s,%s,%s,true,false,'api','fixture-v1',%s)
            """,
            (f"ana_{token}", tenant, ready_answer, analysis_run, captured_at),
        )
        for pub_id, run_pub_id, ordinal, created_at in (
            (exact_citation, analysis_run, 1, captured_at),
            (wrong_run_citation, other_analysis_run, 1, captured_at),
            (late_citation, analysis_run, 2, captured_at + timedelta(days=1)),
        ):
            connection.execute(
                """
                INSERT INTO analytics.citation_fact
                  (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,ordinal,
                   platform_ordinal,ordinal_base,original_url,canonical_url,host,title,
                   cited_text,content_hash,published_at_confidence,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,'example.test','fixture',%s,%s,
                        'unknown',%s)
                """,
                (
                    pub_id,
                    tenant,
                    ready_answer,
                    run_pub_id,
                    ordinal,
                    ordinal,
                    f"https://example.test/{pub_id}",
                    f"https://example.test/{pub_id}",
                    f"citation body {pub_id}",
                    digest(f"citation:{pub_id}"),
                    created_at,
                ),
            )

    page = MetricsV2Repository(POSTGRES_DSN).load_decision_backfill_batch(
        tenant_pub_id=tenant,
        project_pub_id=project,
        cursor=None,
        limit=20,
        as_of=(captured_at + timedelta(seconds=5)).isoformat(),
    )
    assert page["page_count"] == 2
    assert page["executable_count"] == 1
    assert page["preparation_unknown_count"] == 1
    assert len(page["items"]) == 1
    ready = page["items"][0]
    assert ready["answer_pub_id"] == ready_answer
    assert ready["preparation_state"] == "ready"
    assert ready["workflow_payload"]["manifest"]["analysis_run_pub_id"] == analysis_run
    assert (
        ready["workflow_payload"]["query_context_request"]["classification_source"]
        == "historical_backfill"
    )
    assert ready["workflow_payload"]["query_context_request"]["query_pub_id"] == (f"qry_{token}")
    assert ready["workflow_payload"]["dynamic_inputs"]["citation_pub_ids"] == [exact_citation]
    assert "citation_claim_support" in ready["workflow_payload"]["required_capabilities"]
    assert wrong_run_citation not in repr(ready["workflow_payload"])
    assert late_citation not in repr(ready["workflow_payload"])
    assert query_text not in str(ready["workflow_payload"])
    assert response_text not in str(ready["workflow_payload"])
    unknown = page["preparation_unknowns"][0]
    assert unknown["answer_pub_id"] == unknown_answer
    assert unknown["preparation_state"] == "unknown"
    assert unknown["reason_codes"] == ["semantic_v2_ready_analysis_missing"]
    assert "workflow_payload" not in unknown

    # Metrics replay must follow the manifest's immutable answer→context
    # binding. query_pub_id/query_key matching is only metadata and may be
    # absent on historical captures.
    repository = MetricsV2Repository(POSTGRES_DSN)
    artifacts = build_seed_bundle()
    seed(POSTGRES_DSN, artifacts)
    query_request = ready["workflow_payload"]["query_context_request"]
    context_pub_id = f"qcf_{token}"
    context_hash = digest(f"backfill-context:{token}")
    focal_entity_id = next(
        str(item["candidate_id"])
        for item in query_request["candidate_input"]["candidates"]
        if item["candidate_type"] == "brand"
    )
    repository.persist_query_context_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        fact={
            "pub_id": context_pub_id,
            "query_key": query_request["query_key"],
            "query_pub_id": None,
            "query_text_hash": query_request["query_text_hash"],
            "primary_lens": None,
            "analysis_lenses": (),
            "requested_operations": (),
            "query_subtypes": (),
            "detected_entity_ids": (),
            "brand_structure_type": "unknown",
            "classification_state": "review_required",
            "classifier_version": query_request["classifier_version"],
            "decision_task_bundle_hash": query_request["decision_task_bundle_hash"],
            "entity_dictionary_hash": query_request["entity_dictionary_hash"],
            "classification_source": "historical_backfill",
            "derivation_method": "hybrid",
            "decision_record_pub_ids": (),
            "review_status": "unreviewed",
            "fact_hash": context_hash,
            "created_at": captured_at,
        },
        exposures=(
            {
                "pub_id": f"qef_{token}",
                "query_key": query_request["query_key"],
                "focal_entity_id": focal_entity_id,
                "exposure_role": "unknown",
                "matched_entity_ids": (),
                "fact_hash": digest(f"backfill-exposure:{token}"),
                "created_at": captured_at,
            },
        ),
    )
    manifest = dict(ready["workflow_payload"]["manifest"])
    manifest.update(
        {
            "query_context_fact_pub_id": context_pub_id,
            "status": "partial",
            "capability_statuses": {
                "query_context": {
                    "status": "abstained",
                    "decision_record_pub_ids": [],
                    "reason_codes": ["model_budget_exhausted"],
                }
            },
            "decision_record_pub_ids": (),
            "decision_set_hash": digest(f"backfill-decisions:{token}"),
            "completed_at": captured_at + timedelta(seconds=1),
        }
    )
    repository.persist_semantic_manifest_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        manifest=manifest,
        events=(),
    )
    metrics_page = repository.load_metrics_backfill_batch(
        tenant_pub_id=tenant,
        project_pub_id=project,
        cursor=None,
        limit=20,
        as_of=(captured_at + timedelta(seconds=5)).isoformat(),
        dry_run=False,
    )
    assert [item["answer_pub_id"] for item in metrics_page["subjects"]] == [ready_answer]
    assert metrics_page["unknown_count"] == 2
    metric = next(item for item in artifacts if item.kind == "metric_definition")
    persisted_unknown = repository.persist_metric_evaluations(
        tenant_pub_id=tenant,
        project_pub_id=project,
        evaluations=(
            {
                "answer_pub_id": ready_answer,
                "query_key": query_request["query_key"],
                "focal_entity_id": focal_entity_id,
                "metric_name": metric.name,
                "metric_version": metric.version,
                "metric_definition_hash": metric.content_hash,
                "query_context_fact_pub_id": context_pub_id,
                "semantic_manifest_pub_id": manifest["pub_id"],
                "semantic_decision_pub_ids": (),
                "semantic_decision_set_hash": manifest["decision_set_hash"],
                "eligibility_status": "analysis_unknown",
                "reason_codes": ("model_budget_exhausted",),
                "outcome_value": None,
                "numerator_contribution": None,
                "denominator_contribution": None,
                "supporting_event_pub_ids": (),
                "evaluation_hash": digest(f"unknown-evaluation:{token}"),
                "created_at": captured_at,
            },
        ),
    )
    assert persisted_unknown["inserted_count"] == 1
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,false)", (tenant,))
        stored_outcome = connection.execute(
            """
            SELECT outcome_value::text FROM analytics.metric_evaluation_v2
            WHERE tenant_pub_id=%s AND evaluation_hash=%s
            """,
            (tenant, digest(f"unknown-evaluation:{token}")),
        ).fetchone()
    assert stored_outcome == ("null",)
