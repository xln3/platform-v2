import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.s02_routers import router

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_s02_router_bundle_exposes_tenant_scoped_safe_analytics_and_evidence() -> None:
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    project = f"prj_{suffix}"
    captured = datetime.now(UTC)
    provenance = RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="api-test-v1",
        capture_time=captured,
        access_class=AccessClass.PUBLIC,
    )
    AnalyticsService(dsn=POSTGRES_DSN).analyze_and_persist(
        tenant_pub_id=tenant,
        project_pub_id=project,
        answer_pub_id=f"ans_{suffix}",
        answer_text="Acme 排第一。",
        brand="Acme",
        competitors=(),
        citations=(),
        dimensions={"model": "api-test"},
        own_domains=(),
        provenance=provenance,
        scorer_version="scorer-v2",
        metric_version="metrics-v2",
        model_version="rules-v1",
    )
    store = ContentAddressedObjectStore(
        endpoint="http://127.0.0.1:19000",
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    EvidenceService(dsn=POSTGRES_DSN, store=store).capture(
        evidence_pub_id=f"evd_{suffix}",
        tenant_pub_id=tenant,
        project_pub_id=project,
        kind="answer_text",
        payload=b"safe evidence",
        mime_type="text/plain",
        source_url="https://example.com",
        provenance=provenance,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="test", role=Role.ADMIN, tenant_pub_id=tenant
    )
    client = TestClient(app)
    overview = client.get(
        "/api/v2/analytics/overview",
        params={
            "project_pub_id": project,
            "start": captured.date().isoformat(),
            "end": captured.date().isoformat(),
        },
    )
    assert overview.status_code == 200
    body = overview.json()
    mention = next(item for item in body if item["metric"] == "mention_rate")
    assert mention["value"] == 1
    assert mention["trace_tokens"]
    assert mention["state"] == "ready"
    assert len(mention["filter_hash"]) == 64
    answers = client.get(
        "/api/v2/analytics/answers",
        params={"project_pub_id": project, "model": "api-test"},
    )
    assert answers.status_code == 200
    answer_projection = answers.json()["data"][0]
    assert answer_projection["response_text"] == "Acme 排第一。"
    assert answer_projection["mentioned"] is True
    assert answer_projection["model"] == "api-test"
    assert answers.json()["page"] == {"next_cursor": None, "has_more": False}
    evidence = client.get("/api/v2/evidence/assets")
    assert evidence.status_code == 200
    projection = evidence.json()["data"][0]
    forbidden = {
        "platform_account_pub_id",
        "browser_profile_version_pub_id",
        "session_event_pub_id",
        "cookie",
        "token",
        "otp",
        "profile_path",
    }
    assert forbidden.isdisjoint(projection)
    package_id = f"pkg_{suffix}"
    package = client.post(
        "/api/v2/evidence/packages",
        json={
            "package_pub_id": package_id,
            "evidence_pub_ids": [f"evd_{suffix}"],
            "public": True,
        },
    )
    assert package.status_code == 201
    grant = client.post(
        f"/api/v2/evidence/packages/{package_id}/grants",
        params={"grant_pub_id": f"grant_{suffix}"},
    )
    assert grant.status_code == 201
    access = client.post(
        "/api/v2/evidence/package-access",
        json={"grant_token": grant.json()["grant_token"], "request_id": f"req_{suffix}"},
    )
    assert access.status_code == 200
    assert "X-Amz-Expires=300" in access.json()["download_url"]
    openapi = app.openapi()
    assert "/api/v2/analytics/overview" in openapi["paths"]
    assert "/api/v2/evidence/packages" in openapi["paths"]
    assert (
        "/api/v2/intelligence/investigations/{investigation_pub_id}/conclusion"
        in (openapi["paths"])
    )
    exported = client.post(
        "/api/v2/exports/metrics",
        json={
            "project_pub_id": project,
            "start": captured.date().isoformat(),
            "end": captured.date().isoformat(),
            "dimensions": {"model": "api-test"},
        },
    )
    assert exported.status_code == 201
    assert exported.json()["format"] == "xlsx"
    assert exported.json()["row_count"] == 7
    assert "/api/v2/exports/metrics" in openapi["paths"]
    report = client.post(
        "/api/v2/reports",
        json={
            "project_pub_id": project,
            "title": "API 正式报告",
            "window_start": captured.isoformat(),
            "window_end": (captured.replace(microsecond=0) + timedelta(seconds=1)).isoformat(),
            "filters": {"model": "api-test"},
            "metric_version": "metrics-v2",
            "scorer_version": "scorer-v2",
            "fact_rows": [{"metric": "mention_rate", "value": 1}],
            "components": [{"component_type": "section", "title": "摘要", "body": "已复核。"}],
            "workflow_operation_id": f"api-report/{suffix}",
        },
    )
    assert report.status_code == 201
    report_body = report.json()
    review = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/"
        f"{report_body['report_version_pub_id']}/reviews",
        json={"decision": "approved", "rationale": "API 审核通过"},
    )
    assert review.status_code == 201
    publish = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/"
        f"{report_body['report_version_pub_id']}/publish"
    )
    assert publish.status_code == 204

    investigation = client.post(
        "/api/v2/intelligence/investigations",
        json={"title": "API 调查", "access_class": "customer_private"},
    )
    assert investigation.status_code == 201
    investigation_id = investigation.json()["investigation_pub_id"]
    content = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/contents",
        json={
            "canonical_url": f"https://example.com/{suffix}",
            "title": "待核验帖子",
            "body_text": "Acme 声称市场第一。该结论必须经多源核验。",
            "embedding": [0.9, 0.1],
            "access_class": "public",
            "captured_at": captured.isoformat(),
        },
    )
    assert content.status_code == 201
    score = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/score",
        json={
            "content_feature_score": "0.8",
            "propagation_feature_score": "0.6",
            "circular_citation_risk": "0.1",
            "workflow_operation_id": f"api-investigation/{suffix}/score",
        },
    )
    assert score.status_code == 201
    assert float(score.json()["probability"]) <= 0.49
    verdict = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/verdicts",
        json={
            "verdict": "insufficient",
            "rationale": "独立来源不足",
            "workflow_operation_id": f"api-investigation/{suffix}/verdict",
        },
    )
    assert verdict.status_code == 201
    conclusion = client.get(f"/api/v2/intelligence/investigations/{investigation_id}/conclusion")
    assert conclusion.status_code == 200
    assert conclusion.json()["human_verdict"]["verdict"] == "insufficient"
