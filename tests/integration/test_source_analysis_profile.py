from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.collection.workflow_outbox import WorkflowStartOutbox
from geo_platform.main import app
from geo_platform.source_analysis.service import SourceAnalysisService
from temporalio.exceptions import ApplicationError

from workflows.activities.page_inspection import (
    PageCandidateBatch,
    PageInspectionInput,
    _PostgresPageInspectionLoader,
    _PostgresPageInspectionSink,
    execute_page_inspection,
)
from workflows.activities.source_audit import AuditLlmConfig

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)
_SOURCE_TEXT = "作者账号：安全观察。文章称盛邦安全能力很差，但没有给出测试方法。"
_SOURCE_SHA256 = hashlib.sha256(_SOURCE_TEXT.encode()).hexdigest()


def _bootstrap(client: TestClient) -> tuple[str, dict[str, str]]:
    subject = "source-profile-" + secrets.token_hex(8)
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant_pub_id = str(response.json()["tenant_pub_id"])
    return tenant_pub_id, {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "source-profile-" + secrets.token_hex(16),
    }


def _profile() -> dict[str, object]:
    return {
        "object_name": "盛邦安全",
        "object_kind": "brand",
        "categories": ["WAF", "网络空间测绘"],
        "aliases": [
            {
                "value": "盛邦",
                "evidence_url": "https://example.com/observed-alias",
            }
        ],
        "own_domains": ["webray.com.cn"],
        "peers": ["绿盟科技", "阿里云"],
        "anchor_sources": [
            {
                "name": "IDC",
                "publisher": "IDC",
                "url": "https://www.idc.com/",
                "categories": ["WAF"],
            }
        ],
        "linked_entities": [
            {"name": "恒安嘉新", "relation": "parent"},
        ],
        "hard_anchor_available": True,
        "decision_mode": "selection",
    }


def test_profile_is_versioned_and_alias_provenance_is_required() -> None:
    with TestClient(app) as client:
        _tenant, headers = _bootstrap(client)
        project = client.post(
            "/api/v2/projects",
            headers=headers,
            json={"name": "Source analysis", "customer_name": "Customer"},
        )
        assert project.status_code == 201, project.text
        project_pub_id = project.json()["pub_id"]
        path = f"/api/v2/source-analysis/projects/{project_pub_id}/profile"

        invalid = _profile()
        invalid["aliases"] = [{"value": "无出处别名"}]
        rejected = client.put(path, headers=headers, json=invalid)
        assert rejected.status_code == 422

        unproven_capture = _profile()
        unproven_capture["aliases"] = [{"value": "盛邦", "capture_pub_id": "ans_missing_capture"}]
        rejected_capture = client.put(path, headers=headers, json=unproven_capture)
        assert rejected_capture.status_code == 400
        assert rejected_capture.json()["error"]["code"] == "invalid_profile"

        created = client.put(path, headers=headers, json=_profile())
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["revision"] == 1
        assert first["profile_type"] == "I"
        assert first["state"] == "active"

        replay = client.put(path, headers=headers, json=_profile())
        assert replay.status_code == 200, replay.text
        assert replay.json()["pub_id"] == first["pub_id"]

        changed = _profile()
        changed["decision_mode"] = "reputation"
        second_response = client.put(path, headers=headers, json=changed)
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()
        assert second["revision"] == 2
        assert second["profile_type"] == "III"

        active = client.get(path, headers=headers)
        assert active.status_code == 200, active.text
        assert active.json()["pub_id"] == second["pub_id"]

        history = client.get(
            f"/api/v2/source-analysis/projects/{project_pub_id}/profiles",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        assert [item["revision"] for item in history.json()] == [2, 1]
        assert [item["state"] for item in history.json()] == ["active", "retired"]


class _StartedHandle:
    result_run_id = "page-inspection-test-run"


class _Temporal:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def start_workflow(self, *args: object, **kwargs: object) -> _StartedHandle:
        self.calls.append((args, kwargs))
        return _StartedHandle()


class _TextStore:
    def get_text(self, _object_key: str, _expected_sha256: str) -> str:
        return _SOURCE_TEXT


class _Judge:
    def analyze(self, **_kwargs: object) -> PageCandidateBatch:
        return PageCandidateBatch(
            findings=(
                {
                    "code": "A1",
                    "ledger": "statement",
                    "variant": "",
                    "summary": "页面对对象作出无依据的能力贬评",
                    "action": "review_content",
                    "evidence_chain": [
                        {
                            "connector": "because",
                            "fact_type": "source_quote",
                            "quote": "盛邦安全能力很差",
                            "occurrence": 1,
                            "explanation": "该句逐字指向对象且没有给出测试依据。",
                        }
                    ],
                    "self_check": {
                        "passed": True,
                        "reasoning": "若同一句用于同位对手，仍采用同一判据。",
                    },
                },
            ),
            attributions=(
                {
                    "kind": "publisher_account",
                    "value": "安全观察",
                    "quote": "作者账号：安全观察",
                    "occurrence": 1,
                    "confidence": 0.95,
                },
            ),
        )


@pytest.mark.asyncio
async def test_existing_run_can_enqueue_profile_versioned_inspection() -> None:
    with TestClient(app) as client:
        tenant, headers = _bootstrap(client)
        project = client.post(
            "/api/v2/projects",
            headers=headers,
            json={"name": "Replay source analysis", "customer_name": "Customer"},
        )
        assert project.status_code == 201, project.text
        project_pub_id = project.json()["pub_id"]
        profile = client.put(
            f"/api/v2/source-analysis/projects/{project_pub_id}/profile",
            headers=headers,
            json=_profile(),
        )
        assert profile.status_code == 201, profile.text

        headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
        frozen = client.post(
            f"/api/v2/projects/{project_pub_id}/config/freeze",
            headers=headers,
            json={
                "query_groups": [{"name": "Core", "items": [{"text": "WAF厂商有哪些"}]}],
                "regions": ["CN-BJ"],
                "models": ["fixed"],
                "modes": ["fast"],
                "frequency": "manual",
                "effective_at": datetime.now(UTC).isoformat(),
            },
        )
        assert frozen.status_code == 201, frozen.text
        headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
        run = client.post(
            "/api/v2/collection/runs",
            headers=headers,
            json={
                "project_pub_id": project_pub_id,
                "config_version_pub_id": frozen.json()["pub_id"],
                "requires_intervention": False,
            },
        )
        assert run.status_code == 202, run.text
        run_pub_id = str(run.json()["workflow_id"]).rsplit("/", 1)[-1]

        source_pub_id = "src_" + secrets.token_hex(12)
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                """
                INSERT INTO platform.source_document
                  (id,pub_id,tenant_id,project_id,run_id,url,url_hash,host,fetched_at,
                   extract_status,extractor,bytes,text_cas_key,text_sha256,canonical_url,
                   first_seen_at,last_verified_at,metadata_parser_version)
                SELECT %s,%s,r.tenant_id,r.project_id,r.id,%s,%s,%s,now(),
                       'ok','fixture-v1',10,%s,%s,%s,now(),now(),'fixture-v1'
                FROM platform.collection_run r WHERE r.pub_id=%s
                """,
                (
                    uuid.uuid4(),
                    source_pub_id,
                    "https://example.com/source",
                    "b" * 64,
                    "example.com",
                    "cas/fixture/source",
                    _SOURCE_SHA256,
                    "https://example.com/source",
                    run_pub_id,
                ),
            )

        enqueue_path = (
            f"/api/v2/source-analysis/projects/{project_pub_id}/runs/{run_pub_id}/inspect"
        )
        queued = client.post(enqueue_path, headers=headers, json={})
        assert queued.status_code == 201, queued.text
        job = queued.json()
        assert job["state"] == "queued"
        assert job["profile_pub_id"] == profile.json()["pub_id"]
        assert job["policy_version"].startswith("page-inspection-v1-r1-")

        replay = client.post(enqueue_path, headers=headers, json={})
        assert replay.status_code == 200, replay.text
        assert replay.json()["pub_id"] == job["pub_id"]

        with psycopg.connect(POSTGRES_DSN) as connection:
            command = connection.execute(
                """
                SELECT workflow_type,payload FROM integration.workflow_start_command
                WHERE workflow_id=%s
                """,
                (job["workflow_id"],),
            ).fetchone()
        assert command is not None
        assert command[0] == "page_inspection"
        assert command[1]["profile_hash"] == profile.json()["profile_hash"]
        assert command[1]["analysis_job_pub_id"] == job["pub_id"]
        assert command[1]["tenant_pub_id"] == tenant

        # A model/prompt change is a new immutable interpretation version.  It
        # must not collide with, reset, or overwrite the first queued job.
        service = SourceAnalysisService(dsn=POSTGRES_DSN)
        next_job, next_created = service.enqueue_run_inspection(
            tenant_pub_id=tenant,
            project_pub_id=project_pub_id,
            run_pub_id=run_pub_id,
            profile_pub_id=profile.json()["pub_id"],
            task_queue="geo-analysis-test",
            model="fixture-next-model",
        )
        assert next_created
        assert next_job["pub_id"] != job["pub_id"]
        assert next_job["policy_version"] != job["policy_version"]
        assert next_job["workflow_id"] != job["workflow_id"]

        with pytest.raises(ApplicationError, match="differs from frozen job input"):
            execute_page_inspection(
                PageInspectionInput(
                    tenant_pub_id=tenant,
                    project_pub_id=project_pub_id,
                    run_pub_id=run_pub_id,
                    profile_pub_id=profile.json()["pub_id"],
                    profile_hash="0" * 64,
                    policy_version=job["policy_version"],
                    model=command[1]["model"],
                    prompt_version=command[1]["prompt_version"],
                ),
                enabled=True,
                llm=AuditLlmConfig("fixture-key", command[1]["model"], "https://example.com"),
                loader=_PostgresPageInspectionLoader(POSTGRES_DSN),
                text_store=_TextStore(),
                sink=_PostgresPageInspectionSink(POSTGRES_DSN),
                judge=_Judge(),
                max_documents=10,
                max_chars=120_000,
            )

        inspection_result = execute_page_inspection(
            PageInspectionInput(
                tenant_pub_id=tenant,
                project_pub_id=project_pub_id,
                run_pub_id=run_pub_id,
                profile_pub_id=profile.json()["pub_id"],
                profile_hash=profile.json()["profile_hash"],
                policy_version=job["policy_version"],
                model=command[1]["model"],
                prompt_version=command[1]["prompt_version"],
            ),
            enabled=True,
            llm=AuditLlmConfig("fixture-key", command[1]["model"], "https://example.com"),
            loader=_PostgresPageInspectionLoader(POSTGRES_DSN),
            text_store=_TextStore(),
            sink=_PostgresPageInspectionSink(POSTGRES_DSN),
            judge=_Judge(),
            max_documents=10,
            max_chars=120_000,
        )
        assert len(inspection_result.inspected) == 1
        inspection_pub_id = inspection_result.inspected[0].inspection_pub_id

        inspection_list = client.get(
            f"/api/v2/source-analysis/projects/{project_pub_id}/inspections",
            headers=headers,
            params={"run_pub_id": run_pub_id},
        )
        assert inspection_list.status_code == 200, inspection_list.text
        assert inspection_list.json()["data"][0]["statement_count"] == 1
        assert inspection_list.json()["data"][0]["exposure_count"] == 0

        detail = client.get(
            f"/api/v2/source-analysis/projects/{project_pub_id}/inspections/{inspection_pub_id}",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text
        finding = detail.json()["findings"][0]
        assert finding["code"] == "A1"
        assert finding["finding_status"] == "confirmed"
        span = finding["spans"][0]
        assert _SOURCE_TEXT[span["text_start"] : span["text_end"]] == span["quote"]
        assert detail.json()["attribution"]["publisher_identity"]["account"] == "安全观察"

        temporal = _Temporal()
        dispatcher = WorkflowStartOutbox(
            dsn=POSTGRES_DSN,
            temporal=temporal,  # type: ignore[arg-type]
        )
        assert await dispatcher.dispatch_one(job["workflow_id"])
        assert len(temporal.calls) == 1
        workflow_input = temporal.calls[0][0][1]
        assert workflow_input.run_pub_id == run_pub_id
        assert workflow_input.profile_pub_id == profile.json()["pub_id"]
        assert workflow_input.profile_hash == profile.json()["profile_hash"]
