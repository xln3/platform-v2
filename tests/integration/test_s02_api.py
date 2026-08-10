import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geo_platform.analytics.service import AnalyticsService
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.reports import narrative
from geo_platform.s02_routers import router

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.analyzer import CitationInput

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_s02_router_bundle_exposes_tenant_scoped_safe_analytics_and_evidence() -> None:
    suffix = uuid4().hex
    tenant = f"tnt_{suffix[:20]}"
    project = f"prj_{suffix}"
    captured = datetime.now(UTC)
    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    admin_user_id = uuid4()
    customer_user_id = uuid4()
    other_customer_user_id = uuid4()
    analyst_user_id = uuid4()
    reviewer_user_id = uuid4()
    second_reviewer_user_id = uuid4()
    revoked_customer_user_id = uuid4()
    foreign_customer_user_id = uuid4()
    admin_user_pub_id = f"usr_admin_{suffix[:12]}"
    customer_user_pub_id = f"usr_customer_{suffix[:12]}"
    other_customer_user_pub_id = f"usr_other_{suffix[:12]}"
    analyst_user_pub_id = f"usr_analyst_{suffix[:12]}"
    reviewer_user_pub_id = f"usr_reviewer_{suffix[:12]}"
    second_reviewer_user_pub_id = f"usr_reviewer2_{suffix[:12]}"
    revoked_customer_user_pub_id = f"usr_revoked_{suffix[:12]}"
    foreign_customer_user_pub_id = f"usr_foreign_{suffix[:12]}"
    admin_subject = f"admin-subject-{suffix}"
    customer_subject = f"customer-subject-{suffix}"
    other_customer_subject = f"other-customer-subject-{suffix}"
    analyst_subject = f"analyst-subject-{suffix}"
    reviewer_subject = f"reviewer-subject-{suffix}"
    second_reviewer_subject = f"second-reviewer-subject-{suffix}"
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,'S02 API tenant','active',%s,%s),
                   (%s,%s,'S02 foreign tenant','active',%s,%s)
            """,
            (
                tenant_id,
                tenant,
                captured,
                captured,
                foreign_tenant_id,
                f"tnt_foreign_{suffix[:12]}",
                captured,
                captured,
            ),
        )
        connection.cursor().executemany(
            """
            INSERT INTO platform.app_user
              (id,pub_id,subject,display_name,is_service_account,created_at)
            VALUES (%s,%s,%s,%s,false,%s)
            """,
            (
                (admin_user_id, admin_user_pub_id, admin_subject, "API admin", captured),
                (
                    customer_user_id,
                    customer_user_pub_id,
                    customer_subject,
                    "API customer",
                    captured,
                ),
                (
                    other_customer_user_id,
                    other_customer_user_pub_id,
                    other_customer_subject,
                    "API other customer",
                    captured,
                ),
                (
                    analyst_user_id,
                    analyst_user_pub_id,
                    analyst_subject,
                    "API analyst",
                    captured,
                ),
                (
                    reviewer_user_id,
                    reviewer_user_pub_id,
                    reviewer_subject,
                    "API reviewer",
                    captured,
                ),
                (
                    second_reviewer_user_id,
                    second_reviewer_user_pub_id,
                    second_reviewer_subject,
                    "API second reviewer",
                    captured,
                ),
                (
                    revoked_customer_user_id,
                    revoked_customer_user_pub_id,
                    f"revoked-customer-subject-{suffix}",
                    "API revoked customer",
                    captured,
                ),
                (
                    foreign_customer_user_id,
                    foreign_customer_user_pub_id,
                    f"foreign-customer-subject-{suffix}",
                    "API foreign customer",
                    captured,
                ),
            ),
        )
        connection.cursor().executemany(
            """
            INSERT INTO platform.membership
              (id,pub_id,tenant_id,user_id,role,state,revoked_at,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                (
                    uuid4(),
                    f"mbr_admin_{suffix[:12]}",
                    tenant_id,
                    admin_user_id,
                    "admin",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_customer_{suffix[:12]}",
                    tenant_id,
                    customer_user_id,
                    "customer",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_other_{suffix[:12]}",
                    tenant_id,
                    other_customer_user_id,
                    "customer",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_analyst_{suffix[:12]}",
                    tenant_id,
                    analyst_user_id,
                    "analyst",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_reviewer_{suffix[:12]}",
                    tenant_id,
                    reviewer_user_id,
                    "reviewer",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_reviewer2_{suffix[:12]}",
                    tenant_id,
                    second_reviewer_user_id,
                    "reviewer",
                    "active",
                    None,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_revoked_{suffix[:12]}",
                    tenant_id,
                    revoked_customer_user_id,
                    "customer",
                    "revoked",
                    captured,
                    captured,
                ),
                (
                    uuid4(),
                    f"mbr_foreign_{suffix[:12]}",
                    foreign_tenant_id,
                    foreign_customer_user_id,
                    "customer",
                    "active",
                    None,
                    captured,
                ),
            ),
        )
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
        citations=(
            CitationInput(
                "https://example.com/source?utm_source=api-test",
                title="独立来源",
                cited_text="Acme 排第一",
            ),
        ),
        dimensions={
            "model": "api-test",
            "region": "east",
            "mode": "deep",
            "question_pub_id": f"qry_{suffix}",
            "query_text": "企业如何选择可信知识服务？",
        },
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
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO evidence.evidence_relation
              (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
            VALUES (%s,%s,%s,'visualizes')
            """,
            (tenant, f"ans_{suffix}", f"evd_{suffix}"),
        )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=admin_subject,
        role=Role.ADMIN,
        tenant_pub_id=tenant,
        user_pub_id=admin_user_pub_id,
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
    assert answer_projection["citation_count"] == 1
    assert answers.json()["page"] == {"next_cursor": None, "has_more": False}
    exact_answer = client.get(
        "/api/v2/analytics/answers",
        params={"project_pub_id": project, "answer_pub_id": f"ans_{suffix}"},
    )
    assert exact_answer.status_code == 200
    assert [item["pub_id"] for item in exact_answer.json()["data"]] == [f"ans_{suffix}"]
    missing_answer = client.get(
        "/api/v2/analytics/answers",
        params={"project_pub_id": project, "answer_pub_id": "ans_missing"},
    )
    assert missing_answer.status_code == 200
    assert missing_answer.json()["data"] == []
    relations = client.get(f"/api/v2/analytics/answers/ans_{suffix}/relations")
    assert relations.status_code == 200
    assert relations.json()["citations"][0]["canonical_url"] == "https://example.com/source"
    assert relations.json()["citations"][0]["content_hash"]
    assert relations.json()["evidence"][0]["pub_id"] == f"evd_{suffix}"
    assert relations.json()["evidence"][0]["relation_type"] == "visualizes"
    assert relations.json()["evidence"][0]["anchors"] == []
    assert relations.json()["history"] == []
    for group_by in ("day", "model", "region_mode", "question"):
        breakdown = client.get(
            "/api/v2/analytics/breakdown",
            params={
                "project_pub_id": project,
                "start": captured.date().isoformat(),
                "end": captured.date().isoformat(),
                "group_by": group_by,
            },
        )
        assert breakdown.status_code == 200, breakdown.text
        assert breakdown.json()[0]["answer_count"] == 1
        assert breakdown.json()[0]["mention_rate"] == 1
    model_breakdown = client.get(
        "/api/v2/analytics/breakdown",
        params={
            "project_pub_id": project,
            "start": captured.date().isoformat(),
            "end": captured.date().isoformat(),
            "group_by": "model",
        },
    ).json()[0]
    assert model_breakdown["model"] == "api-test"
    question_breakdown = client.get(
        "/api/v2/analytics/breakdown",
        params={
            "project_pub_id": project,
            "start": captured.date().isoformat(),
            "end": captured.date().isoformat(),
            "group_by": "question",
        },
    ).json()[0]
    assert question_breakdown["question_pub_id"] == f"qry_{suffix}"
    assert question_breakdown["question_text"] == "企业如何选择可信知识服务？"
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
    assert "/api/v2/analytics/breakdown" in openapi["paths"]
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
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=analyst_subject,
        role=Role.ANALYST,
        tenant_pub_id=tenant,
        user_pub_id=analyst_user_pub_id,
    )
    report_payload = {
        "project_pub_id": project,
        "title": "API 正式报告",
        "window_start": captured.isoformat(),
        "window_end": (captured.replace(microsecond=0) + timedelta(seconds=1)).isoformat(),
        "filters": {"model": "api-test"},
        "metric_version": "metrics-v2",
        "scorer_version": "scorer-v2",
        "fact_rows": [
            {
                "metric": "mention_rate",
                "value": 1,
                "evidence_pub_id": f"evd_{suffix}",
            }
        ],
        "components": [{"component_type": "section", "title": "摘要", "body": "已复核。"}],
        "workflow_operation_id": f"api-report/{suffix}",
    }
    report = client.post(
        "/api/v2/reports",
        json=report_payload,
    )
    assert report.status_code == 201
    report_body = report.json()
    revision_payload = {
        "components": [
            {
                "component_type": "section",
                "source": "human",
                "title": "摘要",
                "body": "已由分析师修订并绑定证据。",
                "evidence_pub_ids": [f"evd_{suffix}"],
            }
        ]
    }
    revision_key = f"report-revision-{suffix}"
    revision = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": revision_key},
        json=revision_payload,
    )
    assert revision.status_code == 201, revision.text
    revision_body = revision.json()
    assert revision_body["version_number"] == 2
    assert revision_body["fact_snapshot_hash"] == report_body["fact_snapshot_hash"]
    replayed_revision = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": revision_key},
        json=revision_payload,
    )
    assert replayed_revision.status_code == 201
    assert (
        replayed_revision.json()["report_version_pub_id"] == revision_body["report_version_pub_id"]
    )
    revision_conflict = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": revision_key},
        json={
            "components": [
                {
                    "component_type": "section",
                    "source": "human",
                    "title": "摘要",
                    "body": "同一幂等键不得改写内容。",
                }
            ]
        },
    )
    assert revision_conflict.status_code == 409
    assert revision_conflict.json()["detail"]["code"] == "idempotency_conflict"
    missing_evidence_revision = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": f"missing-evidence-{suffix}"},
        json={
            "components": [
                {
                    "component_type": "section",
                    "source": "human",
                    "title": "摘要",
                    "body": "跨租户或不存在的证据必须失败。",
                    "evidence_pub_ids": [f"evd_missing_{suffix[:12]}"],
                }
            ]
        },
    )
    assert missing_evidence_revision.status_code == 404
    assert missing_evidence_revision.json()["detail"]["code"] == "report_or_evidence_not_found"
    active_version_id = revision_body["report_version_pub_id"]
    comment = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/comments",
        json={"body": "请保留冻结口径。"},
    )
    assert comment.status_code == 201
    action = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/actions",
        json={
            "description": "复测品牌提及率",
            "baseline": {"mention_rate": 1},
        },
    )
    assert action.status_code == 201
    action_id = action.json()["action_pub_id"]
    action_update = client.patch(
        f"/api/v2/reports/{report_body['report_pub_id']}/actions/{action_id}",
        json={"state": "in_progress"},
    )
    assert action_update.status_code == 204
    retest = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/actions/{action_id}/effect-retests",
        json={"measured_at": captured.isoformat(), "result": {"mention_rate": 1}},
    )
    assert retest.status_code == 201
    detail = client.get(f"/api/v2/reports/{report_body['report_pub_id']}")
    assert detail.status_code == 200
    assert len(detail.json()["versions"]) == 2
    version = detail.json()["versions"][1]
    assert version["frozen_facts"][0]["payload"] == {
        "metric": "mention_rate",
        "value": 1,
        "evidence_pub_id": f"evd_{suffix}",
    }
    assert len(version["frozen_facts"][0]["payload_hash"]) == 64
    assert version["components"][0]["payload"] == {
        "title": "摘要",
        "body": "已由分析师修订并绑定证据。",
        "evidence_pub_ids": [f"evd_{suffix}"],
    }
    assert version["comments"][0]["body"] == "请保留冻结口径。"
    assert {artifact["format"] for artifact in version["artifacts"]} == {
        "docx",
        "html",
        "pdf",
        "xlsx",
    }
    assert version["evidence_bindings"] == [
        {
            "pub_id": version["evidence_bindings"][0]["pub_id"],
            "report_version_pub_id": active_version_id,
            "evidence_pub_id": f"evd_{suffix}",
            "purpose": "frozen_fact_or_component",
            "kind": "answer_text",
            "access_class": "public",
            "mime_type": "text/plain",
            "byte_size": 13,
            "sha256": version["evidence_bindings"][0]["sha256"],
            "anchor_count": 0,
            "capture_time": captured.isoformat().replace("+00:00", "Z"),
            "created_at": version["evidence_bindings"][0]["created_at"],
        }
    ]
    assert detail.json()["optimization_actions"][0]["effect_retests"][0]["result"] == {
        "mention_rate": 1
    }
    preview = client.get(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/artifacts/pdf"
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.content.startswith(b"%PDF")
    analyst_review = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/reviews",
        json={"decision": "approved", "rationale": "分析师不能自审"},
    )
    assert analyst_review.status_code == 403
    assert analyst_review.json()["detail"]["code"] == "permission_denied"
    assert (
        client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/publish"
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/deliveries",
            json={"recipient_pub_id": customer_user_pub_id},
        ).status_code
        == 403
    )
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=reviewer_subject,
        role=Role.REVIEWER,
        tenant_pub_id=tenant,
        user_pub_id=reviewer_user_pub_id,
    )
    assert client.post("/api/v2/reports", json=report_payload).status_code == 403
    assert (
        client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/versions",
            headers={"Idempotency-Key": f"reviewer-revision-{suffix}"},
            json=revision_payload,
        ).status_code
        == 403
    )
    review = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/reviews",
        json={"decision": "approved", "rationale": "API 审核通过"},
    )
    assert review.status_code == 201
    publish = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/publish"
    )
    assert publish.status_code == 204
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=analyst_subject,
        role=Role.ANALYST,
        tenant_pub_id=tenant,
        user_pub_id=analyst_user_pub_id,
    )
    published_replay = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": revision_key},
        json=revision_payload,
    )
    assert published_replay.status_code == 201
    assert published_replay.json()["report_version_pub_id"] == active_version_id
    new_revision_after_publish = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions",
        headers={"Idempotency-Key": f"post-publish-revision-{suffix}"},
        json=revision_payload,
    )
    assert new_revision_after_publish.status_code == 409
    assert new_revision_after_publish.json()["detail"]["code"] == "published_report_immutable"
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=customer_subject,
        role=Role.CUSTOMER,
        tenant_pub_id=tenant,
        user_pub_id=customer_user_pub_id,
    )
    assert client.get("/api/v2/reports").json()["data"] == []
    assert client.get(f"/api/v2/reports/{report_body['report_pub_id']}").status_code == 404
    assert (
        client.get(
            f"/api/v2/reports/{report_body['report_pub_id']}/versions/"
            f"{active_version_id}/artifacts/pdf"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/comments",
            json={"body": "未交付报告不可提问"},
        ).status_code
        == 404
    )
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=reviewer_subject,
        role=Role.REVIEWER,
        tenant_pub_id=tenant,
        user_pub_id=reviewer_user_pub_id,
    )
    for invalid_recipient in (
        analyst_user_pub_id,
        revoked_customer_user_pub_id,
        foreign_customer_user_pub_id,
        f"usr_missing_{suffix[:12]}",
    ):
        rejected_delivery = client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/deliveries",
            json={"recipient_pub_id": invalid_recipient},
        )
        assert rejected_delivery.status_code == 404
        assert rejected_delivery.json()["detail"]["code"] == "delivery_recipient_not_found"
    delivery = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries",
        json={"recipient_pub_id": customer_user_pub_id},
    )
    assert delivery.status_code == 201
    delivery_id = delivery.json()["delivery_pub_id"]
    replayed_delivery = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries",
        json={"recipient_pub_id": customer_user_pub_id},
    )
    assert replayed_delivery.json()["delivery_pub_id"] == delivery_id
    reviewer_confirmation = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries/{delivery_id}/confirm",
        json={"confirmation_comment": "审核人不能代替客户确认"},
    )
    assert reviewer_confirmation.status_code == 403
    assert (
        reviewer_confirmation.json()["detail"]["code"] == "delivery_confirmation_customer_required"
    )
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=other_customer_subject,
        role=Role.CUSTOMER,
        tenant_pub_id=tenant,
        user_pub_id=other_customer_user_pub_id,
    )
    mismatch = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries/{delivery_id}/confirm",
        json={"confirmation_comment": "已收到"},
    )
    assert mismatch.status_code == 403
    assert client.get(f"/api/v2/reports/{report_body['report_pub_id']}/deliveries").json() == []
    assert client.get(f"/api/v2/reports/{report_body['report_pub_id']}").status_code == 404
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=customer_subject,
        role=Role.CUSTOMER,
        tenant_pub_id=tenant,
        user_pub_id=customer_user_pub_id,
    )
    visible_delivery = client.get(f"/api/v2/reports/{report_body['report_pub_id']}/deliveries")
    assert visible_delivery.status_code == 200
    assert [item["pub_id"] for item in visible_delivery.json()] == [delivery_id]
    customer_reports = client.get("/api/v2/reports")
    assert customer_reports.status_code == 200
    assert [item["pub_id"] for item in customer_reports.json()["data"]] == [
        report_body["report_pub_id"]
    ]
    customer_detail = client.get(f"/api/v2/reports/{report_body['report_pub_id']}")
    assert customer_detail.status_code == 200
    assert [item["status"] for item in customer_detail.json()["versions"]] == ["published"]
    assert customer_detail.json()["versions"][0]["reviews"] == []
    assert customer_detail.json()["versions"][0]["comments"] == []
    assert customer_detail.json()["versions"][0]["events"] == []
    assert customer_detail.json()["optimization_actions"] == []
    assert (
        client.get(
            f"/api/v2/reports/{report_body['report_pub_id']}/versions/"
            f"{active_version_id}/artifacts/pdf"
        ).status_code
        == 200
    )
    customer_comment = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/versions/{active_version_id}/comments",
        json={"body": "客户仅能查看自己的报告提问"},
    )
    assert customer_comment.status_code == 201
    customer_detail = client.get(f"/api/v2/reports/{report_body['report_pub_id']}")
    customer_comment_authors = [
        item["author_pub_id"] for item in customer_detail.json()["versions"][0]["comments"]
    ]
    assert customer_comment_authors == [customer_user_pub_id]
    confirmed = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries/{delivery_id}/confirm",
        json={"confirmation_comment": "已收到"},
    )
    assert confirmed.status_code == 200
    assert (
        client.post(
            f"/api/v2/reports/{report_body['report_pub_id']}/deliveries/{delivery_id}/confirm",
            json={"confirmation_comment": "已收到"},
        ).status_code
        == 200
    )
    conflict = client.post(
        f"/api/v2/reports/{report_body['report_pub_id']}/deliveries/{delivery_id}/confirm",
        json={"confirmation_comment": "更改确认内容"},
    )
    assert conflict.status_code == 409
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=admin_subject,
        role=Role.ADMIN,
        tenant_pub_id=tenant,
        user_pub_id=admin_user_pub_id,
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        persisted_version = connection.execute(
            """
            SELECT created_by_pub_id,authoring_operation_hash,authoring_contract_hash
            FROM reporting.report_version
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, active_version_id),
        ).fetchone()
        persisted_review = connection.execute(
            """
            SELECT reviewer_pub_id FROM reporting.report_review
            WHERE tenant_pub_id=%s AND report_version_pub_id=%s
            """,
            (tenant, active_version_id),
        ).fetchone()
        persisted_delivery = connection.execute(
            """
            SELECT recipient_pub_id FROM reporting.report_delivery
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, delivery_id),
        ).fetchone()
        delivery_events = connection.execute(
            """
            SELECT event_type,actor_pub_id
            FROM reporting.report_event
            WHERE tenant_pub_id=%s AND report_pub_id=%s
              AND event_type IN ('delivered','delivery_confirmed')
            ORDER BY event_type
            """,
            (tenant, report_body["report_pub_id"]),
        ).fetchall()
        revision_events = connection.execute(
            """
            SELECT actor_pub_id FROM reporting.report_event
            WHERE tenant_pub_id=%s AND report_pub_id=%s
              AND report_version_pub_id=%s AND event_type='revision_created'
            """,
            (tenant, report_body["report_pub_id"], active_version_id),
        ).fetchall()
    assert persisted_version is not None
    assert persisted_version[0] == analyst_user_pub_id
    assert len(persisted_version[1]) == 64
    assert len(persisted_version[2]) == 64
    assert revision_key not in persisted_version
    assert persisted_review == (reviewer_user_pub_id,)
    assert persisted_delivery == (customer_user_pub_id,)
    assert delivery_events == [
        ("delivered", reviewer_user_pub_id),
        ("delivery_confirmed", customer_user_pub_id),
    ]
    assert revision_events == [(analyst_user_pub_id,)]

    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=customer_subject,
        role=Role.CUSTOMER,
        tenant_pub_id=tenant,
        user_pub_id=customer_user_pub_id,
    )
    assert client.get("/api/v2/intelligence/investigations").status_code == 403
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=analyst_subject,
        role=Role.ANALYST,
        tenant_pub_id=tenant,
        user_pub_id=analyst_user_pub_id,
    )
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
            "evidence_pub_id": f"evd_{suffix}",
        },
    )
    assert content.status_code == 201
    history = client.get(f"/api/v2/intelligence/investigations/{investigation_id}/page-history")
    assert history.status_code == 200
    assert history.json()[0]["snapshot_number"] == 1
    assert history.json()[0]["evidence_pub_id"] == f"evd_{suffix}"
    visual_diffs = client.get(
        f"/api/v2/intelligence/investigations/{investigation_id}/visual-diffs"
    )
    assert visual_diffs.status_code == 200
    assert visual_diffs.json() == []
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
    analyst_verdict = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/verdicts",
        json={
            "verdict": "insufficient",
            "rationale": "分析师不能自裁决",
            "workflow_operation_id": f"api-investigation/{suffix}/analyst-verdict",
        },
    )
    assert analyst_verdict.status_code == 403
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=reviewer_subject,
        role=Role.REVIEWER,
        tenant_pub_id=tenant,
        user_pub_id=reviewer_user_pub_id,
    )
    assert (
        client.post(
            f"/api/v2/intelligence/investigations/{investigation_id}/score",
            json={
                "content_feature_score": "0.8",
                "propagation_feature_score": "0.6",
                "circular_citation_risk": "0.1",
                "workflow_operation_id": f"api-investigation/{suffix}/reviewer-score",
            },
        ).status_code
        == 403
    )
    verdict = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/verdicts",
        json={
            "verdict": "insufficient",
            "rationale": "独立来源不足",
            "workflow_operation_id": f"api-investigation/{suffix}/verdict",
        },
    )
    assert verdict.status_code == 201
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=analyst_subject,
        role=Role.ANALYST,
        tenant_pub_id=tenant,
        user_pub_id=analyst_user_pub_id,
    )
    appeal = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/appeals",
        json={"reason": "补充新的独立来源，请求独立复核"},
    )
    assert appeal.status_code == 201
    appeal_id = appeal.json()["appeal_pub_id"]
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=reviewer_subject,
        role=Role.REVIEWER,
        tenant_pub_id=tenant,
        user_pub_id=reviewer_user_pub_id,
    )
    same_reviewer_resolution = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/appeals/{appeal_id}/resolve",
        json={
            "resolution": "upheld",
            "corrected_verdict": None,
            "rationale": "原裁决人不能复核自己的裁决",
        },
    )
    assert same_reviewer_resolution.status_code == 403
    assert (
        same_reviewer_resolution.json()["detail"]["code"] == "appeal_independent_reviewer_required"
    )
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=second_reviewer_subject,
        role=Role.REVIEWER,
        tenant_pub_id=tenant,
        user_pub_id=second_reviewer_user_pub_id,
    )
    resolved = client.post(
        f"/api/v2/intelligence/investigations/{investigation_id}/appeals/{appeal_id}/resolve",
        json={
            "resolution": "upheld",
            "corrected_verdict": None,
            "rationale": "独立复核未发现足以改写原裁决的新证据",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["replacement_verdict_pub_id"] is None
    conclusion = client.get(f"/api/v2/intelligence/investigations/{investigation_id}/conclusion")
    assert conclusion.status_code == 200
    assert conclusion.json()["human_verdict"]["verdict"] == "insufficient"
    with psycopg.connect(POSTGRES_DSN) as connection:
        persisted_verdict = connection.execute(
            """
            SELECT reviewer_pub_id FROM intelligence.human_verdict
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, verdict.json()["verdict_pub_id"]),
        ).fetchone()
        persisted_appeal = connection.execute(
            """
            SELECT submitted_by_pub_id,resolved_by_pub_id,resolution,
                   resolution_rationale,resolved_at IS NOT NULL
            FROM intelligence.appeal
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, appeal_id),
        ).fetchone()
    assert persisted_verdict == (reviewer_user_pub_id,)
    assert persisted_appeal == (
        analyst_user_pub_id,
        second_reviewer_user_pub_id,
        "upheld",
        "独立复核未发现足以改写原裁决的新证据",
        True,
    )


def test_s02_report_ai_draft_model_selection_and_output_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI 起草端点：报告独立模型清单、temperature 规则、不发 max token 字段、
    输出不过滤（反编造为提示词层纪律+人工确认门）、503/400/404 诚实降级。"""
    suffix = uuid4().hex
    tenant = f"tnt_{suffix[:20]}"
    tenant_id = uuid4()
    user_id = uuid4()
    user_pub_id = f"usr_analyst_{suffix[:12]}"
    subject = f"analyst-subject-{suffix}"
    captured = datetime.now(UTC)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,'AI draft tenant','active',%s,%s)
            """,
            (tenant_id, tenant, captured, captured),
        )
        connection.execute(
            """
            INSERT INTO platform.app_user (id,pub_id,subject,display_name,
                is_service_account,created_at)
            VALUES (%s,%s,%s,'AI draft analyst',false,%s)
            """,
            (user_id, user_pub_id, subject, captured),
        )
        connection.execute(
            """
            INSERT INTO platform.membership (id,pub_id,tenant_id,user_id,role,state,
                revoked_at,created_at)
            VALUES (%s,%s,%s,%s,'analyst','active',NULL,%s)
            """,
            (uuid4(), f"mbr_{suffix[:12]}", tenant_id, user_id, captured),
        )
        connection.commit()

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject=subject,
        role=Role.ANALYST,
        tenant_pub_id=tenant,
        user_pub_id=user_pub_id,
    )

    canned_text = "品牌提及数为 1 次，样本边界内保持保守判断。具体份额以完整报告数据为准。"
    seen_requests: list[dict] = []

    def fake_factory(config: object, base_url: str) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": canned_text}}]},
            )

        return httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://llm.test/v1"
        )

    monkeypatch.setenv("GEO_RESEARCH_LLM_API_KEY", "test-key")
    monkeypatch.setattr(narrative, "_default_client_factory", fake_factory)
    get_settings.cache_clear()
    try:
        report = client.post(
            "/api/v2/reports",
            json={
                "project_pub_id": f"prj_{suffix}",
                "title": "AI 起草测试报告",
                "window_start": captured.isoformat(),
                "window_end": (captured + timedelta(seconds=1)).isoformat(),
                "filters": {},
                "metric_version": "metrics-v2",
                "scorer_version": "scorer-v2",
                "fact_rows": [{"metric": "mention_count", "value": 1}],
                "components": [
                    {"component_type": "section", "title": "执行摘要", "body": "占位。"}
                ],
            },
        )
        assert report.status_code == 201, report.text
        report_pub_id = report.json()["report_pub_id"]

        # 模型清单 = 报告独立真源（GEO_REPORT_LLM_MODELS 缺省七项，首项缺省）
        models = client.get("/api/v2/reports/ai-draft-models")
        assert models.status_code == 200
        assert models.json() == {
            "models": [
                "deep-deepseek-v4-flash",
                "deep-deepseek-v4-pro",
                "claude-opus-5",
                "gpt-5.6-sol",
                "gemini-3.6-flash",
                "baidu-glm-5.2",
                "moonshot-kimi-k3",
            ]
        }

        # 显式选 Kimi K3：请求只带 model/messages——不发 temperature、不发 max token 字段
        drafted = client.post(
            f"/api/v2/reports/{report_pub_id}/ai-draft",
            json={"title": "执行摘要", "model": "moonshot-kimi-k3"},
        )
        assert drafted.status_code == 200, drafted.text
        assert drafted.json()["model"] == "moonshot-kimi-k3"
        assert seen_requests[-1]["model"] == "moonshot-kimi-k3"
        assert "temperature" not in seen_requests[-1]
        assert "max_tokens" not in seen_requests[-1]
        assert "max_completion_tokens" not in seen_requests[-1]
        # 提示词纪律钉死：客户语言 + 禁内部术语 + 只依据冻结指标
        system_msg = seen_requests[-1]["messages"][0]["content"]
        assert "客户语言" in system_msg
        assert "不要臆造" in system_msg
        # 输出原样透传（不过滤；反编造=提示词纪律+人工确认门）
        assert drafted.json()["body"] == canned_text

        # 缺省模型 = 清单首项
        defaulted = client.post(
            f"/api/v2/reports/{report_pub_id}/ai-draft", json={"title": "执行摘要"}
        )
        assert defaulted.status_code == 200
        assert defaulted.json()["model"] == "deep-deepseek-v4-flash"
        assert "temperature" not in seen_requests[-1]

        rejected = client.post(
            f"/api/v2/reports/{report_pub_id}/ai-draft",
            json={"title": "执行摘要", "model": "gpt-5.6-luna"},
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["code"] == "model_not_allowed"

        missing = client.post(
            "/api/v2/reports/rpt_nonexistent/ai-draft", json={"title": "执行摘要"}
        )
        assert missing.status_code == 404
    finally:
        get_settings.cache_clear()
        app.dependency_overrides.clear()
