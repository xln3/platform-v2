from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.sop.router import router


def test_s05_sop_workflow_full_loop_and_tenant_rbac_boundaries() -> None:
    suffix = uuid4().hex
    tenant_pub_id = f"tnt_s05_{suffix[:16]}"
    actor_pub_id = f"usr_s05_{suffix[:16]}"
    current_principal = {
        "value": Principal(
            subject=f"s05-{suffix}",
            role=Role.ADMIN,
            tenant_pub_id=tenant_pub_id,
            user_pub_id=actor_pub_id,
        )
    }
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: current_principal["value"]
    client = TestClient(app)
    idempotency = {"Idempotency-Key": f"sop-{suffix}"}
    now = datetime.now(UTC).isoformat()

    invalid_key = client.post(
        "/api/v2/sop/projects",
        json={"name": "无效幂等键", "brand_standard_name": "Acme"},
        headers={"Idempotency-Key": "short"},
    )
    assert invalid_key.status_code == 422

    project_response = client.post(
        "/api/v2/sop/projects",
        json={
            "name": "S05 全闭环",
            "brand_standard_name": "Acme",
            "brand_profile": {"aliases": ["ACME"], "official_site": "https://example.com"},
            "target_platforms": [{"platform": "doubao", "mode": "search"}],
            "success_definition": [{"metric": "citation_rate", "target": 0.5}],
        },
        headers=idempotency,
    )
    assert project_response.status_code == 201, project_response.text
    assert project_response.headers["Idempotency-Key"] == idempotency["Idempotency-Key"]
    project = project_response.json()
    project_pub_id = project["pub_id"]

    second_project = client.post(
        "/api/v2/sop/projects",
        json={"name": "分页项目", "brand_standard_name": "Acme"},
        headers={"Idempotency-Key": f"sop-page-{suffix}"},
    )
    assert second_project.status_code == 201
    second_project_pub_id = second_project.json()["pub_id"]
    first_page = client.get("/api/v2/sop/projects?limit=1")
    assert first_page.status_code == 200
    assert len(first_page.json()["data"]) == 1
    assert first_page.json()["page"]["has_more"] is True
    assert first_page.json()["page"]["next_cursor"]
    second_page = client.get(
        "/api/v2/sop/projects",
        params={"limit": 1, "cursor": first_page.json()["page"]["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["data"]) == 1

    query_set_one = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/query-sets",
        json={"note": "v1"},
        headers={"Idempotency-Key": f"sop-query-set-1-{suffix}"},
    )
    assert query_set_one.status_code == 201, query_set_one.text
    query_set_one_pub_id = query_set_one.json()["pub_id"]
    query_items = client.post(
        f"/api/v2/sop/query-sets/{query_set_one_pub_id}/items",
        json={
            "items": [
                {
                    "query_text": "企业如何选择可信的知识服务？",
                    "layer": "A",
                    "priority": "P0",
                    "expected_facts": "可核验来源",
                },
                {
                    "query_text": "Acme 的公开资料可信吗？",
                    "layer": "G",
                    "priority": "P1",
                    "contains_brand": True,
                },
            ]
        },
        headers={"Idempotency-Key": f"sop-query-items-{suffix}"},
    )
    assert query_items.status_code == 201, query_items.text
    query_item_pub_id = query_items.json()[0]["pub_id"]
    freeze_one = client.post(
        f"/api/v2/sop/query-sets/{query_set_one_pub_id}/freeze",
        headers={"Idempotency-Key": f"sop-freeze-1-{suffix}"},
    )
    assert freeze_one.status_code == 200
    assert freeze_one.json()["status"] == "frozen"
    repeat_freeze = client.post(
        f"/api/v2/sop/query-sets/{query_set_one_pub_id}/freeze",
        headers={"Idempotency-Key": f"sop-freeze-repeat-{suffix}"},
    )
    assert repeat_freeze.status_code == 200
    frozen_write = client.post(
        f"/api/v2/sop/query-sets/{query_set_one_pub_id}/items",
        json={"items": [{"query_text": "禁止追加", "layer": "A"}]},
        headers={"Idempotency-Key": f"sop-frozen-write-{suffix}"},
    )
    assert frozen_write.status_code == 409

    query_set_two = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/query-sets",
        json={"note": "v2"},
        headers={"Idempotency-Key": f"sop-query-set-2-{suffix}"},
    )
    query_set_two_pub_id = query_set_two.json()["pub_id"]
    query_item_two = client.post(
        f"/api/v2/sop/query-sets/{query_set_two_pub_id}/items",
        json={
            "items": [
                {
                    "query_text": "可信知识服务有哪些判断标准？",
                    "layer": "C",
                    "priority": "P0",
                }
            ]
        },
        headers={"Idempotency-Key": f"sop-query-items-2-{suffix}"},
    )
    assert query_item_two.status_code == 201
    frozen_query_item_pub_id = query_item_two.json()[0]["pub_id"]
    freeze_two = client.post(
        f"/api/v2/sop/query-sets/{query_set_two_pub_id}/freeze",
        headers={"Idempotency-Key": f"sop-freeze-2-{suffix}"},
    )
    assert freeze_two.status_code == 200
    query_sets = client.get(f"/api/v2/sop/projects/{project_pub_id}/query-sets")
    statuses = {row["pub_id"]: row["status"] for row in query_sets.json()["data"]}
    assert statuses[query_set_one_pub_id] == "superseded"
    assert statuses[query_set_two_pub_id] == "frozen"

    baseline_payload = {
        "query_item_pub_id": frozen_query_item_pub_id,
        "sample_index": 1,
        "platform": "doubao",
        "region": "CN",
        "mode": "search",
        "asked_at": now,
        "capture_status": "success",
        "answer_text": "当前回答会提到 Acme。",
        "search_terms": ["可信知识服务"],
        "citations": [{"url": "https://example.org/baseline"}],
        "brand_mentioned": True,
        "key_facts": ["基线事实"],
    }
    baseline = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/baseline-answers",
        json=baseline_payload,
        headers={"Idempotency-Key": f"sop-baseline-{suffix}"},
    )
    assert baseline.status_code == 201, baseline.text
    baseline_pub_id = baseline.json()["pub_id"]
    duplicate_baseline = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/baseline-answers",
        json=baseline_payload,
        headers={"Idempotency-Key": f"sop-baseline-duplicate-{suffix}"},
    )
    assert duplicate_baseline.status_code == 409
    failed_baseline = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/baseline-answers",
        json={
            **baseline_payload,
            "query_item_pub_id": query_item_pub_id,
            "sample_index": 2,
            "capture_status": "captcha",
            "answer_text": "",
        },
        headers={"Idempotency-Key": f"sop-baseline-failed-{suffix}"},
    )
    assert failed_baseline.status_code == 201
    failed_list = client.get(
        f"/api/v2/sop/projects/{project_pub_id}/baseline-answers",
        params={"capture_status": "captcha"},
    )
    assert [row["capture_status"] for row in failed_list.json()["data"]] == ["captcha"]

    insight = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/insights",
        json={"insight_type": "source_selection", "payload": {"preferred": ["official"]}},
        headers={"Idempotency-Key": f"sop-insight-{suffix}"},
    )
    assert insight.status_code == 201
    evidence = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/evidence",
        json={
            "claim_text": "Acme 发布了可核验的方法说明。",
            "source_name": "Acme 官网",
            "source_url": "https://example.com/method",
            "source_level": "official",
            "allowed_public": True,
        },
        headers={"Idempotency-Key": f"sop-evidence-{suffix}"},
    )
    assert evidence.status_code == 201, evidence.text
    evidence_patch = client.patch(
        f"/api/v2/sop/evidence/{evidence.json()['pub_id']}",
        json={"can_prove": "方法说明存在且公开"},
    )
    assert evidence_patch.status_code == 200
    assert evidence_patch.json()["can_prove"] == "方法说明存在且公开"

    opportunity = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/opportunities",
        json={
            "target_query": "可信知识服务有哪些判断标准？",
            "current_gap": "缺少一手方法说明",
            "current_sources": ["https://example.org/baseline"],
            "recommended_platform": "baijiahao",
        },
        headers={"Idempotency-Key": f"sop-opportunity-{suffix}"},
    )
    assert opportunity.status_code == 201
    opportunity_pub_id = opportunity.json()["pub_id"]
    selected = client.patch(
        f"/api/v2/sop/opportunities/{opportunity_pub_id}",
        json={"status": "selected", "expected_change": "新增一手来源引用"},
    )
    assert selected.status_code == 200
    assert selected.json()["status"] == "selected"

    article = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/articles",
        json={"title": "可信知识服务判断标准", "opportunity_pub_id": opportunity_pub_id},
        headers={"Idempotency-Key": f"sop-article-{suffix}"},
    )
    assert article.status_code == 201
    article_pub_id = article.json()["pub_id"]
    article_body = "# 可信知识服务判断标准\n\n这是可核验的文章正文。"
    version = client.post(
        f"/api/v2/sop/articles/{article_pub_id}/versions",
        json={"title": "可信知识服务判断标准", "body": article_body, "change_note": "初稿"},
        headers={"Idempotency-Key": f"sop-version-1-{suffix}"},
    )
    assert version.status_code == 201, version.text
    version_pub_id = version.json()["pub_id"]
    assert version.json()["version_no"] == 1
    assert version.json()["body_sha256"] == hashlib.sha256(article_body.encode()).hexdigest()
    version_two = client.post(
        f"/api/v2/sop/articles/{article_pub_id}/versions",
        json={
            "title": "可信知识服务判断标准（核验版）",
            "body": f"{article_body}\n\n补充一手来源。",
            "change_note": "补证",
        },
        headers={"Idempotency-Key": f"sop-version-2-{suffix}"},
    )
    assert version_two.status_code == 201
    assert version_two.json()["version_no"] == 2
    article_detail = client.get(f"/api/v2/sop/articles/{article_pub_id}")
    assert article_detail.status_code == 200
    assert article_detail.json()["status"] == "in_review"
    assert len(article_detail.json()["versions"]) == 2

    check = client.post(
        f"/api/v2/sop/article-versions/{version_pub_id}/checks",
        json={
            "check_type": "fact_verification",
            "result": "pass",
            "findings": "证据完整",
            "checked_by": "reviewer",
            "checked_at": now,
        },
        headers={"Idempotency-Key": f"sop-check-{suffix}"},
    )
    assert check.status_code == 201
    blocked_publication = client.post(
        f"/api/v2/sop/article-versions/{version_pub_id}/publications",
        json={"platform": "baijiahao", "submitted_at": now},
        headers={"Idempotency-Key": f"sop-publish-blocked-{suffix}"},
    )
    assert blocked_publication.status_code == 409
    ready = client.patch(
        f"/api/v2/sop/article-versions/{version_pub_id}",
        json={
            "readiness_checklist": {
                "fact_verification": True,
                "entity_disambiguation": True,
            },
            "publication_ready": True,
        },
    )
    assert ready.status_code == 200
    assert ready.json()["publication_ready"] is True
    publication = client.post(
        f"/api/v2/sop/article-versions/{version_pub_id}/publications",
        json={"platform": "baijiahao", "account_label": "官方号", "submitted_at": now},
        headers={"Idempotency-Key": f"sop-publication-{suffix}"},
    )
    assert publication.status_code == 201, publication.text
    publication_pub_id = publication.json()["pub_id"]
    assert client.get(f"/api/v2/sop/articles/{article_pub_id}").json()["status"] == "published"

    illegal_transition = client.patch(
        f"/api/v2/sop/publications/{publication_pub_id}",
        json={"status": "public"},
    )
    assert illegal_transition.status_code == 409
    for state, extra in (
        ("reviewing", {}),
        ("published", {"published_at": now}),
        (
            "public",
            {
                "public_url": "https://baijiahao.baidu.com/s?id=1",
                "public_checked_at": now,
                "public_http_status": 200,
            },
        ),
    ):
        transition = client.patch(
            f"/api/v2/sop/publications/{publication_pub_id}",
            json={"status": state, **extra},
        )
        assert transition.status_code == 200, transition.text
    terminal_transition = client.patch(
        f"/api/v2/sop/publications/{publication_pub_id}",
        json={"status": "withdrawn"},
    )
    assert terminal_transition.status_code == 409
    terminal_note = client.patch(
        f"/api/v2/sop/publications/{publication_pub_id}",
        json={"note": "公开页已复核"},
    )
    assert terminal_note.status_code == 200

    for checkpoint in ("immediate", "h24"):
        observation = client.post(
            f"/api/v2/sop/publications/{publication_pub_id}/observations",
            json={
                "checkpoint": checkpoint,
                "observed_at": now,
                "page_accessible": True,
                "search_engine_indexed": checkpoint == "h24",
                "ai_retrieved": checkpoint == "h24",
            },
            headers={"Idempotency-Key": f"sop-observation-{checkpoint}-{suffix}"},
        )
        assert observation.status_code == 201
    duplicate_observation = client.post(
        f"/api/v2/sop/publications/{publication_pub_id}/observations",
        json={"checkpoint": "h24", "observed_at": now},
        headers={"Idempotency-Key": f"sop-observation-duplicate-{suffix}"},
    )
    assert duplicate_observation.status_code == 409

    retest = client.post(
        f"/api/v2/sop/publications/{publication_pub_id}/retest-answers",
        json={
            "query_item_pub_id": frozen_query_item_pub_id,
            "sample_index": 1,
            "platform": "doubao",
            "asked_at": now,
            "capture_status": "success",
            "answer_text": "回答引用了 Acme 的方法说明。",
            "brand_mentioned": True,
            "article_appeared": True,
            "article_position": 2,
            "article_cited": True,
            "citation_position": 1,
            "brand_attribution_correct": True,
            "new_facts": ["一手方法说明"],
        },
        headers={"Idempotency-Key": f"sop-retest-{suffix}"},
    )
    assert retest.status_code == 201, retest.text
    retest_pub_id = retest.json()["pub_id"]
    comparison = client.post(
        f"/api/v2/sop/publications/{publication_pub_id}/comparisons",
        json={
            "query_item_pub_id": frozen_query_item_pub_id,
            "baseline_answer_pub_id": baseline_pub_id,
            "retest_answer_pub_id": retest_pub_id,
            "metrics": {"citation_delta": 1},
            "new_info_location": "答案第二段",
            "from_article_confidence": "medium",
            "attribution_correct": True,
            "conclusion": "文章被检索、引用并正确归属",
            "next_actions": ["保持标题稳定"],
        },
        headers={"Idempotency-Key": f"sop-comparison-{suffix}"},
    )
    assert comparison.status_code == 201, comparison.text
    comparison_pub_id = comparison.json()["pub_id"]
    comparison_update = client.post(
        f"/api/v2/sop/publications/{publication_pub_id}/comparisons",
        json={
            "query_item_pub_id": frozen_query_item_pub_id,
            "baseline_answer_pub_id": baseline_pub_id,
            "retest_answer_pub_id": retest_pub_id,
            "metrics": {"citation_delta": 1, "rank": 1},
            "from_article_confidence": "high",
            "attribution_correct": True,
            "conclusion": "高置信归因",
        },
        headers={"Idempotency-Key": f"sop-comparison-upsert-{suffix}"},
    )
    assert comparison_update.status_code == 201
    assert comparison_update.json()["pub_id"] == comparison_pub_id
    assert comparison_update.json()["conclusion"] == "高置信归因"

    foreign_query_set = client.post(
        f"/api/v2/sop/projects/{second_project_pub_id}/query-sets",
        json={"note": "另一个项目的问题集"},
        headers={"Idempotency-Key": f"sop-foreign-query-set-{suffix}"},
    )
    assert foreign_query_set.status_code == 201
    foreign_query_set_pub_id = foreign_query_set.json()["pub_id"]
    foreign_query_items = client.post(
        f"/api/v2/sop/query-sets/{foreign_query_set_pub_id}/items",
        json={"items": [{"query_text": "另一个项目的问题", "layer": "A"}]},
        headers={"Idempotency-Key": f"sop-foreign-query-item-{suffix}"},
    )
    assert foreign_query_items.status_code == 201
    foreign_query_item_pub_id = foreign_query_items.json()[0]["pub_id"]
    foreign_baseline = client.post(
        f"/api/v2/sop/projects/{second_project_pub_id}/baseline-answers",
        json={
            **baseline_payload,
            "query_item_pub_id": foreign_query_item_pub_id,
            "sample_index": 2,
        },
        headers={"Idempotency-Key": f"sop-foreign-baseline-{suffix}"},
    )
    assert foreign_baseline.status_code == 201

    other_publication = client.post(
        f"/api/v2/sop/article-versions/{version_pub_id}/publications",
        json={"platform": "zhihu", "account_label": "官方号", "submitted_at": now},
        headers={"Idempotency-Key": f"sop-other-publication-{suffix}"},
    )
    assert other_publication.status_code == 201
    other_publication_pub_id = other_publication.json()["pub_id"]
    other_retest = client.post(
        f"/api/v2/sop/publications/{other_publication_pub_id}/retest-answers",
        json={
            "query_item_pub_id": frozen_query_item_pub_id,
            "sample_index": 1,
            "platform": "doubao",
            "asked_at": now,
            "capture_status": "success",
            "answer_text": "另一发布记录下的复测。",
            "brand_mentioned": True,
            "article_appeared": True,
            "article_cited": True,
            "brand_attribution_correct": True,
        },
        headers={"Idempotency-Key": f"sop-other-retest-{suffix}"},
    )
    assert other_retest.status_code == 201

    invalid_comparisons = (
        {
            "query_item_pub_id": foreign_query_item_pub_id,
        },
        {
            "query_item_pub_id": frozen_query_item_pub_id,
            "baseline_answer_pub_id": foreign_baseline.json()["pub_id"],
        },
        {
            "query_item_pub_id": frozen_query_item_pub_id,
            "retest_answer_pub_id": other_retest.json()["pub_id"],
        },
    )
    for index, invalid_comparison in enumerate(invalid_comparisons):
        rejected = client.post(
            f"/api/v2/sop/publications/{publication_pub_id}/comparisons",
            json=invalid_comparison,
            headers={"Idempotency-Key": f"sop-invalid-comparison-{index}-{suffix}"},
        )
        assert rejected.status_code == 404, rejected.text

    summary = client.get(f"/api/v2/sop/projects/{project_pub_id}/comparison-summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["retrieval"]["article_recall_rate"] == 1.0
    assert summary.json()["citation"]["citation_rate"] == 1.0
    assert summary.json()["brand"]["baseline_mention_rate"] == 1.0
    assert summary.json()["brand"]["retest_mention_rate"] == 1.0
    assert summary.json()["brand"]["attribution_correct_rate"] == 1.0
    assert summary.json()["answer"]["from_article_medium_or_high"] == 1

    experiment = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/experiments",
        json={
            "hypothesis": "补充一手证据可提高引用率",
            "controlled_conditions": {"query_set": "frozen"},
            "query_set_pub_id": query_set_two_pub_id,
            "observation_window": "7d",
        },
        headers={"Idempotency-Key": f"sop-experiment-{suffix}"},
    )
    assert experiment.status_code == 201
    experiment_patch = client.patch(
        f"/api/v2/sop/experiments/{experiment.json()['pub_id']}",
        json={"status": "done", "result": "引用率提高", "next_step": "扩展问题集"},
    )
    assert experiment_patch.status_code == 200
    rejected_query_set_patch = client.patch(
        f"/api/v2/sop/experiments/{experiment.json()['pub_id']}",
        json={"query_set_pub_id": foreign_query_set_pub_id},
    )
    assert rejected_query_set_patch.status_code == 404
    experiments_after_rejection = client.get(f"/api/v2/sop/projects/{project_pub_id}/experiments")
    assert experiments_after_rejection.status_code == 200
    persisted_experiment = next(
        row
        for row in experiments_after_rejection.json()["data"]
        if row["pub_id"] == experiment.json()["pub_id"]
    )
    assert persisted_experiment["query_set_pub_id"] == query_set_two_pub_id
    work_log = client.post(
        f"/api/v2/sop/projects/{project_pub_id}/work-logs",
        json={"entry_type": "progress", "content": "完成首轮发布后验证"},
        headers={"Idempotency-Key": f"sop-work-log-{suffix}"},
    )
    assert work_log.status_code == 201
    assert work_log.json()["actor_pub_id"] == actor_pub_id
    assert client.patch(
        f"/api/v2/sop/projects/{project_pub_id}/work-logs",
        json={"content": "禁止修改"},
    ).status_code in {404, 405}
    assert client.delete(f"/api/v2/sop/projects/{project_pub_id}/work-logs").status_code in {
        404,
        405,
    }

    dashboard = client.get(f"/api/v2/sop/projects/{project_pub_id}/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    steps = dashboard.json()["steps"]
    assert [step["key"] for step in steps] == [
        "project-definition",
        "query-set",
        "baseline",
        "retrieval-review",
        "evidence-ledger",
        "opportunities",
        "writing",
        "pre-publish",
        "publishing",
        "index-watch",
        "retest",
        "comparison",
        "experiments",
        "archive-log",
    ]
    assert all(step["status"] == "done" for step in steps)
    dashboard_article = next(
        row for row in dashboard.json()["articles"] if row["article_pub_id"] == article_pub_id
    )
    assert dashboard_article["maturity_level"] == "L4"

    current_principal["value"] = Principal(
        subject="foreign",
        role=Role.ADMIN,
        tenant_pub_id=f"tnt_foreign_{suffix[:16]}",
        user_pub_id=f"usr_foreign_{suffix[:16]}",
    )
    assert client.get(f"/api/v2/sop/projects/{project_pub_id}").status_code == 404

    current_principal["value"] = Principal(
        subject="customer",
        role=Role.CUSTOMER,
        tenant_pub_id=tenant_pub_id,
        user_pub_id=f"usr_customer_{suffix[:16]}",
    )
    assert client.get("/api/v2/sop/projects").status_code == 403
    assert (
        client.post(
            "/api/v2/sop/projects",
            json={"name": "无权限", "brand_standard_name": "Acme"},
            headers={"Idempotency-Key": f"sop-customer-{suffix}"},
        ).status_code
        == 403
    )
