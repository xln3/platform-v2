from __future__ import annotations

import hmac
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.collection import workflow_outbox
from geo_platform.evidence.object_store import StoredObject
from geo_platform.evidence.service import EvidenceService
from geo_platform.main import app
from geo_platform.reports import formal_production
from geo_platform.reports.formal_production import (
    FormalProductionConflict,
    FormalProductionIncomplete,
    FormalReportProductionService,
)

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


class MemoryObjectStore:
    """Small verified CAS seam; PostgreSQL remains real in these tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_redacted(
        self,
        payload: bytes,
        *,
        mime_type: str,
        namespace: str = "sha256",
    ) -> StoredObject:
        digest = sha256(payload).hexdigest()
        key = f"{namespace}/{digest[:2]}/{digest}"
        self.objects[key] = payload
        return StoredObject(key, digest, len(payload), mime_type, ())

    def get_verified(self, key: str, expected_sha256: str) -> bytes:
        payload = self.objects[key]
        actual = sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise ValueError("object integrity verification failed")
        return payload


class FailAfterSecondCapture(EvidenceService):
    def __init__(self, *, dsn: str, store: MemoryObjectStore) -> None:
        super().__init__(dsn=dsn, store=store)  # type: ignore[arg-type]
        self.capture_count = 0

    def capture(self, **kwargs: Any) -> StoredObject:
        captured = super().capture(**kwargs)
        self.capture_count += 1
        if self.capture_count == 2:
            raise RuntimeError("injected artifact persistence failure")
        return captured


def _bootstrap_project(client: TestClient, *, marker: str) -> tuple[str, str, dict[str, str]]:
    subject = f"formal-integration-{marker}"
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant_pub_id = str(response.json()["tenant_pub_id"])
    headers = {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "project-" + secrets.token_hex(16),
    }
    project = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Formal report integration", "customer_name": "Formal customer"},
    )
    assert project.status_code == 201, project.text
    return tenant_pub_id, str(project.json()["pub_id"]), headers


def _create_body(project_pub_id: str) -> dict[str, object]:
    return {
        "project_pub_id": project_pub_id,
        "services": [1],
        "window_start": "2026-07-01",
        "window_end": "2026-07-31",
        "document_status": "pre_formal",
        "candidate_group_strategy": "evidence_completeness_v1",
    }


def _tenant_counts(tenant_pub_id: str, production_pub_id: str) -> dict[str, int]:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        report_operation = f"formal:{production_pub_id}:service:1"
        row = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM reporting.formal_report_output
               WHERE tenant_pub_id=%s AND production_pub_id=%s) AS outputs,
              (SELECT count(*) FROM reporting.report
               WHERE tenant_pub_id=%s AND workflow_operation_id=%s) AS reports,
              (SELECT count(*) FROM reporting.report_version version
               JOIN reporting.report report ON report.pub_id=version.report_pub_id
               WHERE version.tenant_pub_id=%s AND report.workflow_operation_id=%s) AS versions,
              (SELECT count(*) FROM reporting.report_frozen_fact fact
               JOIN reporting.report_version version ON version.pub_id=fact.report_version_pub_id
               JOIN reporting.report report ON report.pub_id=version.report_pub_id
               WHERE fact.tenant_pub_id=%s AND report.workflow_operation_id=%s) AS facts,
              (SELECT count(*) FROM reporting.report_evidence_reference reference
               JOIN reporting.report_version version
                 ON version.pub_id=reference.report_version_pub_id
               JOIN reporting.report report ON report.pub_id=version.report_pub_id
               WHERE reference.tenant_pub_id=%s AND report.workflow_operation_id=%s)
                AS evidence_references,
              (SELECT count(*) FROM reporting.report_artifact artifact
               JOIN reporting.report_version version
                 ON version.pub_id=artifact.report_version_pub_id
               JOIN reporting.report report ON report.pub_id=version.report_pub_id
               WHERE artifact.tenant_pub_id=%s AND report.workflow_operation_id=%s) AS artifacts,
              (SELECT count(*) FROM evidence.evidence_asset
               WHERE tenant_pub_id=%s AND project_pub_id IS NOT NULL
                 AND kind LIKE 'formal_report_service_1_%%') AS artifact_assets
            """,
            (
                tenant_pub_id,
                production_pub_id,
                tenant_pub_id,
                report_operation,
                tenant_pub_id,
                report_operation,
                tenant_pub_id,
                report_operation,
                tenant_pub_id,
                report_operation,
                tenant_pub_id,
                report_operation,
                tenant_pub_id,
            ),
        ).fetchone()
    assert row is not None
    return dict(
        zip(
            (
                "outputs",
                "reports",
                "versions",
                "facts",
                "evidence_references",
                "artifacts",
                "artifact_assets",
            ),
            (int(value) for value in row),
            strict=True,
        )
    )


def test_formal_enqueue_and_outbox_are_atomic_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    headers["Idempotency-Key"] = "formal-" + secrets.token_hex(16)
    body = _create_body(project_pub_id)

    created = client.post("/api/v2/reports/formal-productions", headers=headers, json=body)
    assert created.status_code == 201, created.text
    production_pub_id = str(created.json()["pub_id"])
    assert created.json()["status"] == "queued"

    replayed = client.post("/api/v2/reports/formal-productions", headers=headers, json=body)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["pub_id"] == production_pub_id

    drifted = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=body | {"window_end": "2026-08-01"},
    )
    assert drifted.status_code == 409, drifted.text

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        persisted = connection.execute(
            """
            SELECT production.pub_id,production.workflow_id,production.status,
                   count(command.id)
            FROM reporting.formal_report_production production
            LEFT JOIN integration.workflow_start_command command
              ON command.tenant_pub_id=production.tenant_pub_id
             AND command.workflow_id=production.workflow_id
            WHERE production.tenant_pub_id=%s AND production.pub_id=%s
            GROUP BY production.pub_id,production.workflow_id,production.status
            """,
            (tenant_pub_id, production_pub_id),
        ).fetchone()
    assert persisted is not None
    assert persisted[0] == production_pub_id
    assert persisted[2] == "queued"
    assert persisted[3] == 1

    # This executes the real worker-side UPDATE SQL and catches malformed query
    # regressions before a Temporal activity ever starts rendering.
    service = FormalReportProductionService(
        dsn=POSTGRES_DSN,
        evidence=EvidenceService(
            dsn=POSTGRES_DSN,
            store=MemoryObjectStore(),  # type: ignore[arg-type]
        ),
    )
    request = service._request(tenant_pub_id, production_pub_id)
    assert request.pub_id == production_pub_id
    assert (
        service.get(tenant_pub_id=tenant_pub_id, production_pub_id=production_pub_id)["status"]
        == "running"
    )

    failed_tenant, failed_project, failed_headers = _bootstrap_project(
        client, marker=secrets.token_hex(8)
    )
    failed_headers["Idempotency-Key"] = "formal-rollback-" + secrets.token_hex(16)

    def fail_outbox(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(workflow_outbox, "enqueue_workflow_start", fail_outbox)
    failing_client = TestClient(app, raise_server_exceptions=False)
    failed = failing_client.post(
        "/api/v2/reports/formal-productions",
        headers=failed_headers,
        json=_create_body(failed_project),
    )
    assert failed.status_code == 500, failed.text
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (failed_tenant,))
        production_count = connection.execute(
            "SELECT count(*) FROM reporting.formal_report_production WHERE tenant_pub_id=%s",
            (failed_tenant,),
        ).fetchone()
        outbox_count = connection.execute(
            "SELECT count(*) FROM integration.workflow_start_command "
            "WHERE tenant_pub_id=%s AND workflow_type='formal_report_production'",
            (failed_tenant,),
        ).fetchone()
    assert production_count == (0,)
    assert outbox_count == (0,)


def test_formal_hardening_schema_and_role_acl_contracts() -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        constraints = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conname IN (
                  'formal_report_output_production_fk',
                  'formal_report_output_report_fk',
                  'formal_report_output_report_version_fk',
                  'uq_formal_production_tenant_pub_id',
                  'uq_report_tenant_pub_id',
                  'uq_report_version_tenant_report_pub_id',
                  'workflow_signal_tenant_start_fk',
                  'uq_workflow_start_tenant_workflow',
                  'formal_review_request_hash_ck'
                )
                """
            ).fetchall()
        }
        active_index = connection.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname='reporting'
              AND indexname='uq_formal_report_production_tenant_active'
            """
        ).fetchone()
        roles = {
            str(row[0])
            for row in connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('geo_api','geo_worker')"
            ).fetchall()
        }
        for role in roles:
            production_acl = connection.execute(
                """
                SELECT has_table_privilege(%s,'reporting.formal_report_production','SELECT'),
                       has_table_privilege(%s,'reporting.formal_report_production','INSERT'),
                       has_table_privilege(%s,'reporting.formal_report_production','UPDATE'),
                       has_table_privilege(%s,'reporting.formal_report_production','DELETE')
                """,
                (role, role, role, role),
            ).fetchone()
            output_acl = connection.execute(
                """
                SELECT has_table_privilege(%s,'reporting.formal_report_output','SELECT'),
                       has_table_privilege(%s,'reporting.formal_report_output','INSERT'),
                       has_table_privilege(%s,'reporting.formal_report_output','UPDATE'),
                       has_table_privilege(%s,'reporting.formal_report_output','DELETE')
                """,
                (role, role, role, role),
            ).fetchone()
            outbox_grants = {
                str(row[0]): tuple(row[1])
                for row in connection.execute(
                    """
                    SELECT table_name,array_agg(privilege_type ORDER BY privilege_type)
                    FROM information_schema.role_table_grants
                    WHERE grantee=%s AND table_schema='integration'
                      AND table_name IN (
                        'workflow_start_command','workflow_signal_command'
                      )
                    GROUP BY table_name
                    """,
                    (role,),
                ).fetchall()
            }
            if role == "geo_api":
                assert production_acl == (True, True, True, False)
                assert output_acl == (True, False, False, False)
                assert outbox_grants == {
                    "workflow_start_command": ("INSERT", "SELECT"),
                    "workflow_signal_command": ("INSERT", "SELECT"),
                }
            else:
                assert production_acl == (True, False, True, False)
                assert output_acl == (True, True, False, False)
                assert outbox_grants == {
                    "workflow_start_command": ("SELECT", "UPDATE"),
                    "workflow_signal_command": ("SELECT", "UPDATE"),
                }
    assert constraints == {
        "formal_report_output_production_fk",
        "formal_report_output_report_fk",
        "formal_report_output_report_version_fk",
        "uq_formal_production_tenant_pub_id",
        "uq_report_tenant_pub_id",
        "uq_report_version_tenant_report_pub_id",
        "workflow_signal_tenant_start_fk",
        "uq_workflow_start_tenant_workflow",
        "formal_review_request_hash_ck",
    }
    assert active_index == (1,)


def test_formal_enqueue_allows_only_one_active_production_per_tenant() -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    body = _create_body(project_pub_id)
    request_headers = [
        headers | {"Idempotency-Key": "formal-concurrent-a-" + secrets.token_hex(16)},
        headers | {"Idempotency-Key": "formal-concurrent-b-" + secrets.token_hex(16)},
    ]

    def enqueue(index: int) -> tuple[int, dict[str, object]]:
        with TestClient(app) as concurrent_client:
            response = concurrent_client.post(
                "/api/v2/reports/formal-productions",
                headers=request_headers[index],
                json=body,
            )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(enqueue, range(2)))
    assert sorted(status for status, _ in responses) == [201, 409]
    conflict = next(payload for status, payload in responses if status == 409)
    assert "formal_production_in_progress" in str(conflict)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        active = connection.execute(
            """
            SELECT count(*) FROM reporting.formal_report_production
            WHERE tenant_pub_id=%s AND status IN ('queued','running')
            """,
            (tenant_pub_id,),
        ).fetchone()
        starts = connection.execute(
            """
            SELECT count(*) FROM integration.workflow_start_command
            WHERE tenant_pub_id=%s AND workflow_type='formal_report_production'
            """,
            (tenant_pub_id,),
        ).fetchone()
    assert active == (1,)
    assert starts == (1,)

    winner_headers = next(
        request_headers[index] for index, (status, _) in enumerate(responses) if status == 201
    )
    replay = client.post("/api/v2/reports/formal-productions", headers=winner_headers, json=body)
    assert replay.status_code == 200, replay.text


def test_formal_signal_outbox_rejects_cross_tenant_workflow_binding() -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    headers["Idempotency-Key"] = "formal-signal-binding-" + secrets.token_hex(16)
    created = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=_create_body(project_pub_id),
    )
    assert created.status_code == 201, created.text
    workflow_id = str(created.json()["workflow_id"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                """
                INSERT INTO integration.workflow_signal_command (
                  command_id,tenant_pub_id,workflow_id,signal_name,args,trace_context,
                  idempotency_key_hash,contract_hash
                ) VALUES (%s,%s,%s,'review','[]'::jsonb,'{}'::jsonb,%s,%s)
                """,
                (
                    uuid4(),
                    "tnt_wrong_" + secrets.token_hex(12),
                    workflow_id,
                    "a" * 64,
                    "b" * 64,
                ),
            )


def test_formal_review_claim_is_atomic_and_exactly_replayable() -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    headers["Idempotency-Key"] = "formal-review-claim-" + secrets.token_hex(16)
    created = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=_create_body(project_pub_id) | {"document_status": "formal"},
    )
    assert created.status_code == 201, created.text
    production_pub_id = str(created.json()["pub_id"])
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute(
            """
            UPDATE reporting.formal_report_production
            SET status='awaiting_review'
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant_pub_id, production_pub_id),
        )

    # Bootstrap creates an authenticated admin membership, which is also
    # authorized to review.  Reusing that identity keeps each concurrent
    # request on the real membership-validation path.
    review_headers = {
        key: value
        for key, value in headers.items()
        if key in {"X-Tenant-Id", "X-Actor-Id", "X-Actor-Role"}
    }
    requests = [
        (
            review_headers | {"Idempotency-Key": "review-approve-" + secrets.token_hex(16)},
            {"decision": "approved", "rationale": "Approve this formal report."},
        ),
        (
            review_headers | {"Idempotency-Key": "review-change-" + secrets.token_hex(16)},
            {"decision": "changes_requested", "rationale": "Request report changes."},
        ),
    ]
    auth_cookies = dict(client.cookies)

    def review(index: int) -> tuple[int, dict[str, object]]:
        with TestClient(app) as concurrent_client:
            concurrent_client.cookies.update(auth_cookies)
            response = concurrent_client.post(
                f"/api/v2/reports/formal-productions/{production_pub_id}/review",
                headers=requests[index][0],
                json=requests[index][1],
            )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(review, range(2)))
    assert sorted(status for status, _ in responses) == [202, 409]
    conflict = next(payload for status, payload in responses if status == 409)
    assert "formal_review_conflict" in str(conflict)
    winner = next(index for index, (status, _) in enumerate(responses) if status == 202)
    replay = client.post(
        f"/api/v2/reports/formal-productions/{production_pub_id}/review",
        headers=requests[winner][0],
        json=requests[winner][1],
    )
    assert replay.status_code == 202, replay.text
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        claimed = connection.execute(
            """
            SELECT review_request_hash FROM reporting.formal_report_production
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant_pub_id, production_pub_id),
        ).fetchone()
        signals = connection.execute(
            """
            SELECT count(*) FROM integration.workflow_signal_command
            WHERE tenant_pub_id=%s AND workflow_id=%s AND signal_name='review'
            """,
            (tenant_pub_id, created.json()["workflow_id"]),
        ).fetchone()
    assert claimed is not None and len(str(claimed[0])) == 64
    assert signals == (1,)


def test_formal_output_artifacts_persist_all_or_none_and_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    headers["Idempotency-Key"] = "formal-persist-" + secrets.token_hex(16)
    created = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=_create_body(project_pub_id),
    )
    assert created.status_code == 201, created.text
    production_pub_id = str(created.json()["pub_id"])

    store = MemoryObjectStore()
    evidence = EvidenceService(
        dsn=POSTGRES_DSN,
        store=store,  # type: ignore[arg-type]
    )
    input_evidence = evidence.capture(
        evidence_pub_id="evd_" + secrets.token_hex(16),
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        kind="formal_integration_input",
        payload=("frozen-input-" + secrets.token_hex(12)).encode(),
        mime_type="text/plain",
        source_url=None,
        provenance=RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("report:write",),
            adapter_version="formal-integration-v1",
            capture_time=datetime.now(UTC),
            access_class=AccessClass.CUSTOMER_PRIVATE,
        ),
    )
    assert input_evidence.metadata_pub_id is not None
    facts = {
        1: {
            "document_status": "pre_formal",
            "summary": "Customer-visible frozen facts",
            "_input_evidence": {
                "pub_id": input_evidence.metadata_pub_id,
                "object_key": input_evidence.key,
                "sha256": input_evidence.sha256,
                "mime_type": input_evidence.mime_type,
            },
        }
    }
    service = FormalReportProductionService(dsn=POSTGRES_DSN, evidence=evidence)
    request = service._request(tenant_pub_id, production_pub_id)
    fact_snapshot_hash = formal_production._freeze_service_fact(
        request, 1, facts[1]
    ).fact_snapshot_hash
    docx = b"integration-docx-content"
    pdf = b"%PDF-1.7 integration-pdf-content"
    manifest = json.dumps(
        {
            "schema_version": "formal-report-manifest-v1",
            "service_number": 1,
            "document_status": "pre_formal",
            "window": {"start": "2026-07-01", "end": "2026-07-31"},
            "fact_snapshot_hash": fact_snapshot_hash,
            "artifacts": {
                "docx": {"sha256": sha256(docx).hexdigest(), "byte_size": len(docx)},
                "pdf": {"sha256": sha256(pdf).hexdigest(), "byte_size": len(pdf)},
            },
        },
        sort_keys=True,
    ).encode()
    artifacts = {1: {"docx": docx, "pdf": pdf, "manifest": manifest}}

    monkeypatch.setattr(formal_production, "report_runtime_preflight", lambda: None)
    monkeypatch.setattr(
        FormalReportProductionService,
        "_freeze_facts",
        lambda self, request: (facts, "0" * 64),
    )
    monkeypatch.setattr(
        FormalReportProductionService,
        "_freeze_rendered_artifacts",
        lambda self, request, frozen: artifacts,
    )

    failing = FormalReportProductionService(
        dsn=POSTGRES_DSN,
        evidence=FailAfterSecondCapture(dsn=POSTGRES_DSN, store=store),
    )
    with pytest.raises(RuntimeError, match="injected artifact persistence failure"):
        failing.produce(
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
        )
    assert _tenant_counts(tenant_pub_id, production_pub_id) == {
        "outputs": 0,
        "reports": 0,
        "versions": 0,
        "facts": 0,
        "evidence_references": 0,
        "artifacts": 0,
        "artifact_assets": 0,
    }

    completed = service.produce(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=production_pub_id,
    )
    assert completed["status"] == "awaiting_review"
    assert [output["service_number"] for output in completed["outputs"]] == [1]
    assert {artifact["format"] for artifact in completed["outputs"][0]["artifacts"]} == {
        "docx",
        "pdf",
        "manifest",
    }
    persisted_manifest, manifest_mime, _ = service.artifact(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=production_pub_id,
        service_number=1,
        format_name="manifest",
    )
    assert manifest_mime == "application/json"
    manifest_fact_hash = str(json.loads(persisted_manifest)["fact_snapshot_hash"])
    assert manifest_fact_hash == completed["outputs"][0]["fact_snapshot_hash"]
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        persisted_hashes = connection.execute(
            """
            SELECT output.fact_snapshot_hash,version.fact_snapshot_hash
            FROM reporting.formal_report_output output
            JOIN reporting.report_version version
              ON version.tenant_pub_id=output.tenant_pub_id
             AND version.pub_id=output.report_version_pub_id
            WHERE output.tenant_pub_id=%s AND output.production_pub_id=%s
            """,
            (tenant_pub_id, production_pub_id),
        ).fetchone()
    assert persisted_hashes == (manifest_fact_hash, manifest_fact_hash)

    wrong_tenant_pub_id = "tnt_wrong_" + secrets.token_hex(12)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_pub_id', %s, true)",
                (wrong_tenant_pub_id,),
            )
            connection.execute(
                """
                INSERT INTO reporting.formal_report_output (
                  pub_id,tenant_pub_id,production_pub_id,service_number,
                  report_pub_id,report_version_pub_id,fact_snapshot_hash
                ) VALUES (%s,%s,%s,1,%s,%s,%s)
                """,
                (
                    "fout_wrong_" + secrets.token_hex(12),
                    wrong_tenant_pub_id,
                    production_pub_id,
                    completed["outputs"][0]["report_pub_id"],
                    completed["outputs"][0]["report_version_pub_id"],
                    manifest_fact_hash,
                ),
            )

    mismatched_report_pub_id = "rpt_mismatch_" + secrets.token_hex(12)
    mismatched_version_pub_id = "rptv_mismatch_" + secrets.token_hex(12)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
            connection.execute(
                """
                INSERT INTO reporting.report (
                  pub_id,tenant_pub_id,project_pub_id,title,state
                ) VALUES (%s,%s,%s,'Mismatched report','review')
                """,
                (mismatched_report_pub_id, tenant_pub_id, project_pub_id),
            )
            connection.execute(
                """
                INSERT INTO reporting.report_version (
                  pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                  filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
                  status,created_by_pub_id
                ) VALUES (
                  %s,%s,%s,1,%s,%s,'{}'::jsonb,%s,'unit','unit',%s,'review',%s
                )
                """,
                (
                    mismatched_version_pub_id,
                    tenant_pub_id,
                    mismatched_report_pub_id,
                    datetime(2026, 7, 1, tzinfo=UTC),
                    datetime(2026, 8, 1, tzinfo=UTC),
                    "c" * 64,
                    "d" * 64,
                    "usr_formal_integration",
                ),
            )
            connection.execute(
                """
                UPDATE reporting.formal_report_output
                SET report_version_pub_id=%s
                WHERE tenant_pub_id=%s AND production_pub_id=%s AND service_number=1
                """,
                (mismatched_version_pub_id, tenant_pub_id, production_pub_id),
            )
    assert _tenant_counts(tenant_pub_id, production_pub_id) == {
        "outputs": 1,
        "reports": 1,
        "versions": 1,
        "facts": 1,
        "evidence_references": 1,
        "artifacts": 3,
        "artifact_assets": 3,
    }

    # A lost Temporal activity response must replay from persisted output without
    # running preflight, rebuilding facts, rendering, or touching CAS metadata.
    def should_not_run(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("completed production must replay without rebuilding")

    monkeypatch.setattr(formal_production, "report_runtime_preflight", should_not_run)
    monkeypatch.setattr(FormalReportProductionService, "_freeze_facts", should_not_run)
    replayed = service.produce(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=production_pub_id,
    )
    assert replayed == completed
    assert _tenant_counts(tenant_pub_id, production_pub_id)["artifacts"] == 3

    rejected = service.finalize(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=production_pub_id,
        reviewer_pub_id="usr_formal_integration_reviewer",
        approved=False,
        rationale="Integration evidence needs changes.",
        workflow_operation_id=f"formal-review/{production_pub_id}",
    )
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "changes_requested"
    assert (
        service.finalize(
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
            reviewer_pub_id="usr_formal_integration_reviewer",
            approved=False,
            rationale="Integration evidence needs changes.",
            workflow_operation_id=f"formal-review/{production_pub_id}",
        )
        == rejected
    )
    with pytest.raises(FormalProductionConflict, match="formal_review_replay_drift"):
        service.finalize(
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
            reviewer_pub_id="usr_formal_integration_reviewer",
            approved=False,
            rationale="A drifted replay must not be accepted.",
            workflow_operation_id=f"formal-review/{production_pub_id}",
        )

    # A timed-out activity must not resurrect a production after the workflow has
    # already recorded failure, even if the old process reaches persistence later.
    headers["Idempotency-Key"] = "formal-terminal-race-" + secrets.token_hex(16)
    terminal = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=_create_body(project_pub_id),
    )
    assert terminal.status_code == 201, terminal.text
    terminal_pub_id = str(terminal.json()["pub_id"])
    terminal_request = service._request(tenant_pub_id, terminal_pub_id)
    service.mark_failed(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=terminal_pub_id,
        error_code="workflow_interrupted",
    )
    object_count = len(store.objects)
    with pytest.raises(FormalProductionConflict, match="formal_production_not_persistable"):
        service._persist_bundle(terminal_request, facts, artifacts)
    terminal_counts = _tenant_counts(tenant_pub_id, terminal_pub_id)
    assert all(
        terminal_counts[name] == 0
        for name in (
            "outputs",
            "reports",
            "versions",
            "facts",
            "evidence_references",
            "artifacts",
        )
    )
    assert len(store.objects) == object_count
    terminal_row = service.get(
        tenant_pub_id=tenant_pub_id,
        production_pub_id=terminal_pub_id,
    )
    assert terminal_row["status"] == "failed"
    assert terminal_row["error_code"] == "workflow_interrupted"


def test_formal_persistence_rejects_cross_project_evidence() -> None:
    client = TestClient(app)
    tenant_pub_id, project_pub_id, headers = _bootstrap_project(client, marker=secrets.token_hex(8))
    headers["Idempotency-Key"] = "foreign-project-" + secrets.token_hex(16)
    foreign_project = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Foreign evidence project", "customer_name": "Other customer"},
    )
    assert foreign_project.status_code == 201, foreign_project.text
    headers["Idempotency-Key"] = "formal-scope-" + secrets.token_hex(16)
    created = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json=_create_body(project_pub_id),
    )
    assert created.status_code == 201, created.text
    production_pub_id = str(created.json()["pub_id"])

    store = MemoryObjectStore()
    evidence = EvidenceService(
        dsn=POSTGRES_DSN,
        store=store,  # type: ignore[arg-type]
    )
    foreign = evidence.capture(
        evidence_pub_id="evd_" + secrets.token_hex(16),
        tenant_pub_id=tenant_pub_id,
        project_pub_id=str(foreign_project.json()["pub_id"]),
        kind="formal_foreign_project_input",
        payload=b"foreign-project-evidence",
        mime_type="text/plain",
        source_url=None,
        provenance=RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("report:write",),
            adapter_version="formal-integration-v1",
            capture_time=datetime.now(UTC),
            access_class=AccessClass.CUSTOMER_PRIVATE,
        ),
    )
    assert foreign.metadata_pub_id is not None
    service = FormalReportProductionService(dsn=POSTGRES_DSN, evidence=evidence)
    request = service._request(tenant_pub_id, production_pub_id)
    facts = {
        1: {
            "document_status": "pre_formal",
            "_input_evidence": {
                "pub_id": foreign.metadata_pub_id,
                "object_key": foreign.key,
                "sha256": foreign.sha256,
                "mime_type": foreign.mime_type,
            },
        }
    }
    artifacts = {
        1: {
            "docx": b"scoped-docx",
            "pdf": b"scoped-pdf",
            "manifest": b"scoped-manifest",
        }
    }
    with pytest.raises(FormalProductionIncomplete, match="frozen_evidence_drifted"):
        service._persist_bundle(request, facts, artifacts)
    counts = _tenant_counts(tenant_pub_id, production_pub_id)
    assert all(
        counts[name] == 0
        for name in (
            "outputs",
            "reports",
            "versions",
            "facts",
            "evidence_references",
            "artifacts",
            "artifact_assets",
        )
    )
