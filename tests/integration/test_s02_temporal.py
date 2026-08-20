import base64
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import psycopg
import pytest
from geo_platform.evidence.session_gateway import SessionGatewayClient
from geo_platform.intelligence.service import IntelligenceService
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker

from workflows.definitions.s02 import (
    AnswerAnalysisWorkflow,
    AntiGeoInvestigationWorkflow,
    EvidenceCaptureWorkflow,
    ReportProductionWorkflow,
)
from workflows.workers.s02 import S02_ACTIVITIES, S02_WORKFLOWS


@pytest.fixture
def gateway_server(monkeypatch: pytest.MonkeyPatch) -> str:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            lease_id = self.path.split("/")[-2]
            if lease_id not in {
                "lease_valid",
                "lease_expired",
                "lease_revoked",
                "lease_wrong_account",
                "lease_wrong_domain",
                "lease_wrong_scope",
            }:
                self.send_response(403)
                self.end_headers()
                return
            response = {
                "lease_pub_id": lease_id,
                "tenant_pub_id": body["tenant_pub_id"],
                "platform_account_pub_id": (
                    "acct_01ZZZZZZZZZZZZZZZZZZZZZZ"
                    if lease_id == "lease_wrong_account"
                    else body["platform_account_pub_id"]
                ),
                "allowed_domains": (
                    ["other.example.com"]
                    if lease_id == "lease_wrong_domain"
                    else ["private.example.com"]
                ),
                "allowed_actions": ["capture_evidence"],
                "authorization_scope": (
                    ["unrelated"] if lease_id == "lease_wrong_scope" else ["read"]
                ),
                "expires_at": (
                    datetime.now(UTC)
                    + (
                        timedelta(minutes=-5)
                        if lease_id == "lease_expired"
                        else timedelta(minutes=5)
                    )
                ).isoformat(),
                "revoked_at": (
                    datetime.now(UTC).isoformat() if lease_id == "lease_revoked" else None
                ),
                "subject_workflow_id": body["workflow_id"],
                "issuer": "s01-session-gateway",
            }
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("GEO_SESSION_GATEWAY_URL", endpoint)
    yield endpoint
    server.shutdown()
    thread.join()


@pytest.fixture
async def temporal(gateway_server: str) -> tuple[Client, str]:
    client = await Client.connect("127.0.0.1:17233")
    queue = f"s02-test-{uuid4().hex}"
    async with Worker(
        client,
        task_queue=queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
    ):
        yield client, queue


def _seed_platform_tenant(tenant_pub_id: str) -> None:
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,'S02 Temporal integration','active',now(),now())
            """,
            (uuid4(), tenant_pub_id),
        )


@pytest.mark.asyncio
async def test_answer_analysis_workflow_runs_on_real_temporal(
    temporal: tuple[Client, str],
) -> None:
    client, queue = temporal
    suffix = uuid4().hex
    # platform.tenant.pub_id is VARCHAR(30): keep the standard tnt_ prefix and
    # retain 104 random bits without relying on database-side truncation.
    tenant_pub_id = f"tnt_{suffix[:26]}"
    _seed_platform_tenant(tenant_pub_id)
    answer_pub_id = f"ans_{suffix}"
    result = await client.execute_workflow(
        AnswerAnalysisWorkflow.run,
        {
            "answer_pub_id": answer_pub_id,
            "text": "推荐 Acme，Beta 排第二。",
            "brand": "Acme",
            "competitors": ["Beta"],
            "citations": [{"url": "https://example.com/source"}],
            "dimensions": {"model": "test"},
            "filters": {"model": "test"},
            "metric_version": "metrics-v2",
            "scorer_version": "scorer-v2",
            "model_version": "rules-v1",
            "fail_until_attempt": 1,
            "persist": True,
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": f"prj_{suffix}",
            "adapter_version": "temporal-test-v1",
            "capture_time": datetime.now(UTC).isoformat(),
            "channel": "api",
            "access_class": "public",
        },
        id=f"answer-analysis/tnt_test/{answer_pub_id}/{uuid4().hex}",
        task_queue=queue,
    )
    assert result["metrics"]["mention_rate"]["value"] == "1"
    assert result["metrics"]["recommendation_rate"]["state"] == "experimental"
    assert result["persistence"]["outbox_event_id"]
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        persisted = connection.execute(
            "SELECT rank FROM analytics.answer_analysis WHERE answer_pub_id=%s",
            (answer_pub_id,),
        ).fetchone()
    assert persisted == (None,)


@pytest.mark.asyncio
async def test_evidence_workflow_waits_for_lease_and_revocation_stops_capture(
    temporal: tuple[Client, str],
) -> None:
    client, queue = temporal
    suffix = uuid4().hex
    evidence_pub_id = f"evd_{suffix}"
    payload = {
        "tenant_pub_id": f"tnt_{suffix}",
        "project_pub_id": f"prj_{suffix}",
        "evidence_pub_id": evidence_pub_id,
        "source_url": "https://private.example.com",
        "kind": "html_snapshot",
        "mime_type": "text/html",
        "capture_time": datetime.now(UTC).isoformat(),
        "adapter_version": "adapter-v1",
        "access_class": "customer_private",
        "requires_authenticated_session": True,
        "platform_account_pub_id": "acct_01ABCDEFGHIJKLMNOPQRSTUV",
        "browser_profile_version_pub_id": "bpv_01ABCDEFGHIJKLMNOPQRSTUV",
        "session_event_pub_id": "sevt_01ABCDEFGHIJKLMNOPQRSTUV",
        "capture_payload_b64": base64.b64encode(
            b"private page Authorization: Bearer secret-value"
        ).decode(),
    }
    handle = await client.start_workflow(
        EvidenceCaptureWorkflow.run,
        payload,
        id=f"evidence-capture/tnt_test/evd_test/{uuid4().hex}",
        task_queue=queue,
    )
    await handle.signal(EvidenceCaptureWorkflow.authorize_capture, "lease_valid")
    result = await handle.result()
    assert result["authorized_session_capture"] is True
    assert result["captured"] is True
    assert "authorization" in result["dlp_findings"]
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        stored = connection.execute(
            """
            SELECT authorized_session_capture,platform_account_pub_id
            FROM evidence.evidence_asset WHERE pub_id=%s
            """,
            (evidence_pub_id,),
        ).fetchone()
    assert stored == (True, "acct_01ABCDEFGHIJKLMNOPQRSTUV")

    revoked = await client.start_workflow(
        EvidenceCaptureWorkflow.run,
        payload,
        id=f"evidence-capture/tnt_test/evd_revoked/{uuid4().hex}",
        task_queue=queue,
    )
    await revoked.signal(EvidenceCaptureWorkflow.revoke)
    assert await revoked.result() == {"state": "revoked", "captured": False}


@pytest.mark.asyncio
async def test_report_and_investigation_resume_from_human_signals(
    temporal: tuple[Client, str],
) -> None:
    client, queue = temporal
    now = datetime.now(UTC)
    suffix = uuid4().hex
    tenant_pub_id = f"tnt_{suffix}"
    report = await client.start_workflow(
        ReportProductionWorkflow.run,
        {
            "window_start": (now - timedelta(days=7)).isoformat(),
            "window_end": now.isoformat(),
            "filters": {},
            "metric_version": "metrics-v2",
            "scorer_version": "scorer-v2",
            "fact_rows": [{"answer_pub_id": "ans_1", "mentioned": True}],
            "sections": [{"title": "摘要", "body": "冻结事实已由人工复核。"}],
            "persist": True,
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": f"prj_{suffix}",
            "title": "Temporal 正式报告",
            "created_by_pub_id": "usr_analyst",
            "adapter_version": "temporal-test-v1",
            "capture_time": now.isoformat(),
            "channel": "api",
            "access_class": "customer_private",
        },
        id=f"report-production/tnt_test/rpt_test/{uuid4().hex}",
        task_queue=queue,
    )
    await report.signal(
        ReportProductionWorkflow.review,
        {"approved": True, "reviewer_pub_id": "usr_reviewer"},
    )
    report_result = await report.result()
    assert report_result["state"] == "published"
    assert set(report_result["artifacts"]) == {"docx", "html", "pdf", "xlsx"}
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        persisted_report = connection.execute(
            "SELECT state FROM reporting.report WHERE pub_id=%s",
            (report_result["report_pub_id"],),
        ).fetchone()
    assert persisted_report == ("published",)

    investigation_pub_id = IntelligenceService(
        dsn=os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ).create_investigation(tenant_pub_id=tenant_pub_id, title="Temporal 调查")
    investigation = await client.start_workflow(
        AntiGeoInvestigationWorkflow.run,
        {
            "assessments": [
                {
                    "source_pub_id": "src_1",
                    "source_cluster": "cluster_1",
                    "relation": "supports",
                    "independence_weight": "1",
                }
            ],
            "content_feature_score": "1",
            "propagation_feature_score": "1",
            "circular_citation_risk": "0",
            "persist": True,
            "tenant_pub_id": tenant_pub_id,
            "investigation_pub_id": investigation_pub_id,
        },
        id=f"anti-geo-investigation/tnt_test/inv_test/{uuid4().hex}",
        task_queue=queue,
    )
    await investigation.signal(
        AntiGeoInvestigationWorkflow.human_verdict,
        {"verdict": "uncertain", "reviewer_pub_id": "usr_reviewer"},
    )
    result = await investigation.result()
    assert result["score"]["probability"] <= "0.490000"
    assert result["human_verdict"]["verdict"] == "uncertain"
    assert result["persistence"]["verdict_pub_id"]
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        persisted_verdict = connection.execute(
            """
            SELECT verdict FROM intelligence.human_verdict
            WHERE investigation_pub_id=%s ORDER BY id DESC LIMIT 1
            """,
            (investigation_pub_id,),
        ).fetchone()
    assert persisted_verdict == ("uncertain",)


@pytest.mark.asyncio
async def test_report_workflow_survives_worker_interruption_and_duplicate_signal() -> None:
    client = await Client.connect("127.0.0.1:17233")
    queue = f"s02-recovery-{uuid4().hex}"
    workflow_id = f"report-production/tnt_test/rpt_recovery/{uuid4().hex}"
    now = datetime.now(UTC)
    async with Worker(
        client,
        task_queue=queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
    ):
        handle = await client.start_workflow(
            ReportProductionWorkflow.run,
            {
                "window_start": (now - timedelta(days=1)).isoformat(),
                "window_end": now.isoformat(),
                "filters": {},
                "metric_version": "metrics-v2",
                "scorer_version": "scorer-v2",
                "fact_rows": [{"answer_pub_id": "ans_recovery"}],
            },
            id=workflow_id,
            task_queue=queue,
        )
        # Let the first worker persist the frozen state and enter its durable review wait.
        for _ in range(50):
            if await handle.query(ReportProductionWorkflow.state) == "awaiting_review":
                break
    # The first worker is gone. A new worker replays history, accepts an idempotent Signal and
    # completes without re-freezing different facts.
    async with Worker(
        client,
        task_queue=queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
    ):
        decision = {"approved": True, "reviewer_pub_id": "usr_recovery"}
        await handle.signal(ReportProductionWorkflow.review, decision)
        await handle.signal(ReportProductionWorkflow.review, decision)
        result = await handle.result()
    assert result["state"] == "approved"
    assert result["review"] == decision


@pytest.mark.asyncio
async def test_analysis_evidence_and_investigation_resume_after_worker_restart(
    gateway_server: str,
) -> None:
    client = await Client.connect("127.0.0.1:17233")
    queue = f"s02-multi-recovery-{uuid4().hex}"
    suffix = uuid4().hex
    now = datetime.now(UTC)
    async with Worker(
        client,
        task_queue=queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
    ):
        answer = await client.start_workflow(
            AnswerAnalysisWorkflow.run,
            {
                "answer_pub_id": f"ans_{suffix}",
                "text": "Acme 排第一。",
                "brand": "Acme",
                "competitors": [],
                "citations": [],
                "dimensions": {},
                "filters": {},
                "metric_version": "metrics-v2",
                "scorer_version": "scorer-v2",
                "fail_until_attempt": 2,
            },
            id=f"answer-analysis/recovery/{suffix}",
            task_queue=queue,
        )
        evidence = await client.start_workflow(
            EvidenceCaptureWorkflow.run,
            {
                "tenant_pub_id": f"tnt_{suffix}",
                "source_url": "https://private.example.com/page",
                "kind": "html_snapshot",
                "mime_type": "text/html",
                "capture_time": now.isoformat(),
                "adapter_version": "adapter-v1",
                "access_class": "customer_private",
                "requires_authenticated_session": True,
                "platform_account_pub_id": "acct_01ABCDEFGHIJKLMNOPQRSTUV",
            },
            id=f"evidence-capture/recovery/{suffix}",
            task_queue=queue,
        )
        investigation = await client.start_workflow(
            AntiGeoInvestigationWorkflow.run,
            {
                "assessments": [
                    {
                        "source_pub_id": "src_recovery",
                        "source_cluster": "cluster_recovery",
                        "relation": "supports",
                        "independence_weight": "1",
                    }
                ],
                "content_feature_score": "0.8",
                "propagation_feature_score": "0.7",
                "circular_citation_risk": "0.1",
            },
            id=f"anti-geo-investigation/recovery/{suffix}",
            task_queue=queue,
        )
        for _ in range(50):
            evidence_state = await evidence.query(EvidenceCaptureWorkflow.state)
            investigation_state = await investigation.query(AntiGeoInvestigationWorkflow.state)
            if evidence_state == "awaiting_capability" and investigation_state == (
                "awaiting_human_verdict"
            ):
                break
    await evidence.signal(EvidenceCaptureWorkflow.authorize_capture, "lease_valid")
    await investigation.signal(
        AntiGeoInvestigationWorkflow.human_verdict,
        {"verdict": "uncertain", "reviewer_pub_id": "usr_recovery"},
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=list(S02_WORKFLOWS),
        activities=list(S02_ACTIVITIES),
    ):
        answer_result = await answer.result()
        evidence_result = await evidence.result()
        investigation_result = await investigation.result()
    assert answer_result["metrics"]["average_rank"]["value"] == "1"
    assert evidence_result["prepared"] is True
    assert investigation_result["human_verdict"]["verdict"] == "uncertain"


def test_gateway_rejects_expired_revoked_wrong_account_and_unknown_leases(
    gateway_server: str,
) -> None:
    client = SessionGatewayClient(endpoint=gateway_server)
    for lease_id in (
        "lease_expired",
        "lease_revoked",
        "lease_wrong_account",
        "lease_wrong_domain",
        "lease_wrong_scope",
        "lease_unknown",
    ):
        with pytest.raises(PermissionError):
            client.validate_capture_lease(
                lease_pub_id=lease_id,
                tenant_pub_id="tnt_01ABCDEFGHIJKLMNOPQRSTUV",
                platform_account_pub_id="acct_01ABCDEFGHIJKLMNOPQRSTUV",
                target_url="https://private.example.com/page",
                action="capture_evidence",
                workflow_id="evidence-capture/tnt/account/op",
                now=datetime.now(UTC),
            )


@pytest.mark.asyncio
async def test_evidence_workflow_rejects_invalid_leases_with_secret_free_audit(
    temporal: tuple[Client, str],
) -> None:
    client, queue = temporal
    suffix = uuid4().hex
    tenant_pub_id = f"tnt_{suffix}"
    base_payload = {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": f"prj_{suffix}",
        "source_url": "https://private.example.com/page",
        "kind": "html_snapshot",
        "mime_type": "text/html",
        "capture_time": datetime.now(UTC).isoformat(),
        "adapter_version": "adapter-v1",
        "access_class": "customer_private",
        "requires_authenticated_session": True,
        "platform_account_pub_id": "acct_01ABCDEFGHIJKLMNOPQRSTUV",
        "browser_profile_version_pub_id": "bpv_01ABCDEFGHIJKLMNOPQRSTUV",
        "session_event_pub_id": "sevt_01ABCDEFGHIJKLMNOPQRSTUV",
    }
    rejected = (
        "lease_expired",
        "lease_revoked",
        "lease_wrong_account",
        "lease_wrong_domain",
        "lease_wrong_scope",
        "lease_unknown",
    )
    for lease_id in rejected:
        handle = await client.start_workflow(
            EvidenceCaptureWorkflow.run,
            base_payload | {"evidence_pub_id": f"evd_{lease_id}_{suffix}"},
            id=f"evidence-capture/{tenant_pub_id}/{lease_id}/{uuid4().hex}",
            task_queue=queue,
        )
        await handle.signal(EvidenceCaptureWorkflow.authorize_capture, lease_id)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        audit = connection.execute(
            """
            SELECT count(*),string_agg(data::text,' ')
            FROM evidence.evidence_access_audit
            WHERE tenant_pub_id=%s AND action='capability_validate' AND outcome='denied'
            """,
            (tenant_pub_id,),
        ).fetchone()
        captured = connection.execute(
            "SELECT count(*) FROM evidence.evidence_asset WHERE tenant_pub_id=%s",
            (tenant_pub_id,),
        ).fetchone()
    assert audit is not None and audit[0] == len(rejected)
    assert captured == (0,)
    audit_text = audit[1].lower()
    assert all(secret not in audit_text for secret in ("cookie", "bearer", "otp", "password"))


@pytest.mark.asyncio
async def test_evidence_workflow_binary_secret_marker_fails_closed_and_audits(
    temporal: tuple[Client, str],
) -> None:
    client, queue = temporal
    suffix = uuid4().hex
    tenant_pub_id = f"tnt_{suffix}"
    evidence_pub_id = f"evd_{suffix}"
    handle = await client.start_workflow(
        EvidenceCaptureWorkflow.run,
        {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": f"prj_{suffix}",
            "evidence_pub_id": evidence_pub_id,
            "source_url": "https://private.example.com/page",
            "kind": "source_screenshot",
            "mime_type": "image/png",
            "capture_time": datetime.now(UTC).isoformat(),
            "adapter_version": "adapter-v1",
            "access_class": "customer_private",
            "requires_authenticated_session": True,
            "platform_account_pub_id": "acct_01ABCDEFGHIJKLMNOPQRSTUV",
            "capture_payload_b64": base64.b64encode(
                b"\x89PNG\r\nAuthorization: Bearer never-store-this"
            ).decode(),
        },
        id=f"evidence-capture/{tenant_pub_id}/dlp/{uuid4().hex}",
        task_queue=queue,
    )
    await handle.signal(EvidenceCaptureWorkflow.authorize_capture, "lease_valid")
    with pytest.raises(WorkflowFailureError):
        await handle.result()
    with psycopg.connect(
        os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        )
    ) as connection:
        asset_count = connection.execute(
            "SELECT count(*) FROM evidence.evidence_asset WHERE pub_id=%s",
            (evidence_pub_id,),
        ).fetchone()
        denied = connection.execute(
            """
            SELECT data::text FROM evidence.evidence_access_audit
            WHERE tenant_pub_id=%s AND action='capture' AND outcome='denied'
            """,
            (tenant_pub_id,),
        ).fetchone()
    assert asset_count == (0,)
    assert denied is not None and "never-store-this" not in denied[0]
