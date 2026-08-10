import json
import secrets
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.intake import models as intake_models
from geo_platform.intake import research
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.repository import TenantRepository
from sqlalchemy import select

_SECRET = "Authorization: Bearer " + "x" * 32
_LICENSE_CODE = "91310000MA1FL0000A"  # 18 位 [0-9A-Z]


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def _create_member(
    client: TestClient,
    admin_headers: dict[str, str],
    tenant: str,
    role: str,
) -> dict[str, str]:
    subject = f"{role}-" + secrets.token_hex(8)
    response = client.post(
        "/api/v2/identity/members",
        headers={**admin_headers, "Idempotency-Key": "member-" + secrets.token_hex(16)},
        json={"subject": subject, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return {"X-Tenant-Id": tenant, "X-Actor-Id": subject, "X-Actor-Role": role}


def _create_project(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v2/projects",
        headers={**headers, "Idempotency-Key": "project-" + secrets.token_hex(16)},
        json={"name": "Intake Project", "customer_name": "Intake Customer"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _idem() -> str:
    return "idem-" + secrets.token_hex(16)


@pytest.fixture()
def intake_env() -> Iterator[tuple[TestClient, str, dict[str, str], dict[str, str], str]]:
    client = TestClient(app)
    tenant, admin_headers = _bootstrap(client, "intake-admin-" + secrets.token_hex(6))
    customer_headers = _create_member(client, admin_headers, tenant, "customer")
    analyst_headers = _create_member(client, admin_headers, tenant, "analyst")
    project = _create_project(client, admin_headers)
    yield client, tenant, customer_headers, analyst_headers, project


def test_form_schema_is_public_and_vocab_backed() -> None:
    client = TestClient(app)
    response = client.get("/api/v2/intake/form-schema")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "GEO 客户信息收集表（通用版）"
    assert [s["id"] for s in body["sections"]] == ["promo", "qualification"]
    review = next(f for f in body["sections"][0]["fields"] if f["key"] == "review_category")
    assert [o["value"] for o in review["options"]] == ["A", "B", "C", "D", "none"]


def test_profile_upsert_roundtrip_and_fail_closed(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
) -> None:
    client, _, customer, _, project = intake_env
    url = f"/api/v2/projects/{project}/intake/profile"

    empty = client.get(url, headers=customer)
    assert empty.status_code == 200
    assert empty.json()["exists"] is False
    assert empty.json()["goals"] == []

    first = client.put(
        url,
        headers=customer,
        json={
            "contact_person": "张三",
            "website": "https://brand.example",
            "goals": ["提升AI搜索曝光", "获取销售线索"],
            "platforms": ["豆包", "Kimi"],
            "review_category": "C",
            "pre_review_required": False,
            "truth_confirmed": True,
            "business_license_code": _LICENSE_CODE,
            "licenses": [{"name": "食品经营许可证", "number": "JY1", "expiry": "2027-01-01"}],
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["exists"] is True
    assert body["contact_person"] == "张三"
    assert body["goals"] == ["提升AI搜索曝光", "获取销售线索"]
    assert body["truth_confirmed"] is True
    assert body["licenses"][0]["number"] == "JY1"

    # 部分更新：只写出现的字段，其余保留
    second = client.put(url, headers=customer, json={"website": "https://new.example"})
    assert second.status_code == 200
    assert second.json()["website"] == "https://new.example"
    assert second.json()["contact_person"] == "张三"
    assert second.json()["goals"] == ["提升AI搜索曝光", "获取销售线索"]

    # 未知字段 422
    assert client.put(url, headers=customer, json={"nickname": "x"}).status_code == 422
    # 词表 fail-closed
    assert client.put(url, headers=customer, json={"review_category": "Z"}).status_code == 422
    bad_goals = client.put(url, headers=customer, json={"goals": ["不存在的目标"]})
    assert bad_goals.status_code == 422
    # 信用代码正则 fail-closed
    assert (
        client.put(url, headers=customer, json={"business_license_code": "abc"}).status_code == 422
    )
    # DLP 拒密钥
    rejected = client.put(url, headers=customer, json={"selling_points": f"拒绝 {_SECRET}"})
    assert rejected.status_code == 422
    assert _SECRET not in rejected.text


def test_profile_permissions_and_tenant_isolation(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
) -> None:
    client, _, customer, analyst, project = intake_env
    url = f"/api/v2/projects/{project}/intake/profile"
    assert client.put(url, headers=customer, json={"contact_person": "李四"}).status_code == 200
    # analyst 只读：GET 200 / PUT 403
    assert client.get(url, headers=analyst).status_code == 200
    assert client.put(url, headers=analyst, json={"contact_person": "王五"}).status_code == 403
    # 越租户 404
    _, other = _bootstrap(client, "intake-other-" + secrets.token_hex(6))
    assert client.get(url, headers=other).status_code == 404
    assert client.put(url, headers=other, json={"contact_person": "x"}).status_code == 404


def test_promo_crud_shape_validation_and_idempotency(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
) -> None:
    client, _, customer, analyst, project = intake_env
    base = f"/api/v2/projects/{project}/intake/promos"
    payload = {
        "name": "扫地机器人",
        "category": "家电",
        "features": ["价格优势", "品质领先"],
        "desc": "一句话介绍",
        "price": "1000-2000",
    }
    key = _idem()
    created = client.post(
        base,
        headers={**customer, "Idempotency-Key": key},
        json={"kind": "product", "payload": payload},
    )
    assert created.status_code == 201, created.text
    pub_id = created.json()["pub_id"]
    # 幂等重放：同 key 同 body → 同 pub_id
    replay = client.post(
        base,
        headers={**customer, "Idempotency-Key": key},
        json={"kind": "product", "payload": payload},
    )
    assert replay.status_code == 201
    assert replay.json()["pub_id"] == pub_id
    # 同 key 不同 body → 409
    conflict = client.post(
        base,
        headers={**customer, "Idempotency-Key": key},
        json={"kind": "product", "payload": {**payload, "price": "3000"}},
    )
    assert conflict.status_code == 409

    # 形状 fail-closed：未知键 / 词表外 features / 缺 name / 未知 kind
    assert (
        client.post(
            base,
            headers={**customer, "Idempotency-Key": _idem()},
            json={"kind": "product", "payload": {**payload, "bogus": "x"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            base,
            headers={**customer, "Idempotency-Key": _idem()},
            json={"kind": "product", "payload": {**payload, "features": ["宇宙第一"]}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            base,
            headers={**customer, "Idempotency-Key": _idem()},
            json={"kind": "product", "payload": {"category": "家电"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            base,
            headers={**customer, "Idempotency-Key": _idem()},
            json={"kind": "alien", "payload": payload},
        ).status_code
        == 422
    )
    # analyst 403
    assert (
        client.post(
            base,
            headers={**analyst, "Idempotency-Key": _idem()},
            json={"kind": "product", "payload": payload},
        ).status_code
        == 403
    )

    # PATCH 合并（部分字段）
    patched = client.patch(
        f"{base}/{pub_id}", headers=customer, json={"payload": {"price": "2500"}}
    )
    assert patched.status_code == 200
    assert patched.json()["payload"]["price"] == "2500"
    assert patched.json()["payload"]["name"] == "扫地机器人"

    listing = client.get(base, headers=customer)
    assert [item["pub_id"] for item in listing.json()["items"]] == [pub_id]

    deleted = client.delete(f"{base}/{pub_id}", headers=customer)
    assert deleted.status_code == 200
    assert client.get(base, headers=customer).json()["items"] == []
    assert client.delete(f"{base}/{pub_id}", headers=customer).status_code == 404


def test_trigger_questions_dedup_and_draft_freeze(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
) -> None:
    client, tenant, customer, _, project = intake_env
    base = f"/api/v2/projects/{project}/intake/trigger-questions"

    created = client.post(
        base,
        headers={**customer, "Idempotency-Key": _idem()},
        json={"text": "扫地机器人怎么选\n预算三千买哪个"},
    )
    assert created.status_code == 201, created.text
    assert len(created.json()["items"]) == 2
    assert created.json()["skipped_duplicates"] == []

    # 去重跳过：同文本不再建行
    again = client.post(
        base,
        headers={**customer, "Idempotency-Key": _idem()},
        json={"text": "扫地机器人怎么选\n除螨仪推荐"},
    )
    assert again.status_code == 201
    assert [i["text"] for i in again.json()["items"]] == ["除螨仪推荐"]
    assert again.json()["skipped_duplicates"] == ["扫地机器人怎么选"]

    first_pub = created.json()["items"][0]["pub_id"]
    patched = client.patch(
        f"{base}/{first_pub}", headers=customer, json={"text": "扫地机器人如何挑选"}
    )
    assert patched.status_code == 200
    assert patched.json()["text"] == "扫地机器人如何挑选"
    # 改成与既有问法重复 → 409
    dup = client.patch(f"{base}/{first_pub}", headers=customer, json={"text": "预算三千买哪个"})
    assert dup.status_code == 409

    # 文本含密钥 → 422
    assert (
        client.post(
            base, headers={**customer, "Idempotency-Key": _idem()}, json={"text": _SECRET}
        ).status_code
        == 422
    )

    # claim_created 后冻结：PATCH/DELETE → 409（直连 DB 翻状态，RLS 走 tenant context）
    with SessionLocal() as session:
        repository = TenantRepository(session, tenant)
        row = session.scalar(
            select(intake_models.IntakeTriggerQuestion).where(
                intake_models.IntakeTriggerQuestion.tenant_id == repository.tenant.id,
                intake_models.IntakeTriggerQuestion.pub_id == first_pub,
            )
        )
        assert row is not None
        row.status = "claim_created"
        session.commit()
    frozen = client.patch(f"{base}/{first_pub}", headers=customer, json={"text": "改不动"})
    assert frozen.status_code == 409
    assert client.delete(f"{base}/{first_pub}", headers=customer).status_code == 409

    # draft 可删
    second_pub = created.json()["items"][1]["pub_id"]
    assert client.delete(f"{base}/{second_pub}", headers=customer).status_code == 200
    assert client.delete(f"{base}/{second_pub}", headers=customer).status_code == 404


def _mock_llm_data() -> dict[str, object]:
    return {
        "company_name": "示例科技",
        "industry": "互联网 / 软件",
        "description": "一句话简介",
        "website": "https://ai.example",
        "wechat": "example-mp",
        "douyin": "example-dy",
        "social_media": "小红书 example",
        "business_license_code": _LICENSE_CODE,
        "review_category": "C",
        "ad_review_no": None,
        "ad_review_authority": None,
        "ad_review_expiry": None,
        "ad_review_doc_types": ["不适用（非A类行业）"],
        "selling_points": "技术领先，有公开出处",
        "evidence_links": ["https://example.com/report"],
        "products": [
            {
                "name": "主打产品",
                "category": "软件",
                "features": ["技术领先", "吹牛"],
                "desc": "d",
                "price": "p",
            }
        ],
        "company_brief": {
            "name": "示例科技",
            "strength": ["拥有专利", "假的实力"],
            "advantage": "a",
            "cases": "c",
            "data": "d",
        },
        "goals": ["提升AI搜索曝光", "词表外目标"],
        "audience_type": ["B2B企业客户"],
        "audience_desc": "CTO 画像",
        "trigger_questions": "示例科技怎么样\n示例产品值得买吗",
        "regions": ["全国"],
        "platforms": ["豆包", "文心一言"],
        "unavailable": ["ad_review_no", "ad_review_authority", "ad_review_expiry"],
        "confidence": {"high": ["website"], "medium": [], "low": []},
        "sources": [{"title": "官网", "url": "https://ai.example"}],
        "summary": "调研小结",
    }


def _mock_llm_payload() -> dict[str, object]:
    inner = _mock_llm_data()
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(inner, ensure_ascii=False),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def test_ai_research_disabled_without_key(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, customer, _, project = intake_env
    monkeypatch.setenv("GEO_RESEARCH_LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        response = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_disabled"
    finally:
        get_settings.cache_clear()


def test_ai_research_prefills_only_empty_fields(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, customer, _, project = intake_env
    profile_url = f"/api/v2/projects/{project}/intake/profile"

    # 用户先填 website（调研绝不覆盖用户值）
    assert (
        client.put(
            profile_url, headers=customer, json={"website": "https://user.example"}
        ).status_code
        == 200
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_mock_llm_payload())

    def fake_build_client(config: research.LlmConfig, base_url: str) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://llm.test/v1",
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    monkeypatch.setenv("GEO_RESEARCH_LLM_API_KEY", "test-key")
    monkeypatch.setattr(research, "_build_client", fake_build_client)
    get_settings.cache_clear()
    try:
        response = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "website": "https://ai.example"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rounds"] == 1
        assert body["usage"] == {"input_tokens": 100, "output_tokens": 50}
        # 词表 fail-closed 披露：goals/features/strength 各丢 1 个词表外值
        assert body["dropped"]["goals"] == 1
        assert body["dropped"]["features"] == 1
        assert body["dropped"]["strength"] == 1
        assert body["data"]["goals"] == ["提升AI搜索曝光"]
        assert body["unavailable"] == ["ad_review_authority", "ad_review_expiry", "ad_review_no"]

        profile = client.get(profile_url, headers=customer).json()
        # 用户值不被覆盖；空字段被预填 + provenance
        assert profile["website"] == "https://user.example"
        assert "website" not in profile["prefilled"]
        assert profile["wechat"] == "example-mp"
        assert profile["prefilled"]["wechat"] == "research:ai-live"
        assert profile["goals"] == ["提升AI搜索曝光"]
        assert profile["business_license_code"] == _LICENSE_CODE
        assert profile["audience_desc"] == "CTO 画像"
        # promo 草稿：product ≤3 + company ≤1，词表外 features/strength 已滤除
        promos = client.get(f"/api/v2/projects/{project}/intake/promos", headers=customer).json()[
            "items"
        ]
        assert [p["kind"] for p in promos] == ["product", "company"]
        assert promos[0]["payload"]["features"] == ["技术领先"]
        assert promos[1]["payload"]["strength"] == ["拥有专利"]
        assert profile["prefilled"]["promos"] == "research:ai-live"
        # 问法收录 draft
        triggers = client.get(
            f"/api/v2/projects/{project}/intake/trigger-questions", headers=customer
        ).json()["items"]
        assert [t["text"] for t in triggers] == ["示例科技怎么样", "示例产品值得买吗"]
        assert all(t["status"] == "draft" for t in triggers)
        assert profile["prefilled"]["trigger_questions"] == "research:ai-live"

        # 再跑一次：已填字段（含 AI 预填的）一律不覆盖，promo 已有 → 不再建
        second = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技"},
        )
        assert second.status_code == 200
        assert second.json()["prefilled"] == []
        assert second.json()["promos_created"] == []
        assert second.json()["triggers_created"] == []
        assert second.json()["triggers_skipped"] == ["示例科技怎么样", "示例产品值得买吗"]
        profile2 = client.get(profile_url, headers=customer).json()
        assert profile2["website"] == "https://user.example"
        assert profile2["wechat"] == "example-mp"
    finally:
        get_settings.cache_clear()


def test_ai_research_model_selection(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, customer, _, project = intake_env
    seen: list[tuple[str, dict]] = []  # (path, request_body)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append((request.url.path, body))
        if request.url.path.endswith("/messages"):
            # claude 系传输：Anthropic 原生形状
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "text", "text": json.dumps(_mock_llm_data(), ensure_ascii=False)}
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            )
        if request.url.path.endswith("/chat/completions"):
            # qwen/gemini-search 系传输：chat/completions 形状
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(_mock_llm_data(), ensure_ascii=False)}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                },
            )
        return httpx.Response(200, json=_mock_llm_payload())

    def fake_build_client(config: research.LlmConfig, base_url: str) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://llm.test/v1",
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    monkeypatch.setenv("GEO_RESEARCH_LLM_API_KEY", "test-key")
    monkeypatch.setenv(
        "GEO_RESEARCH_LLM_MODELS",
        "gpt-5.5, claude-opus-5, qwen3.7-max, gemini-2.5-flash-search",
    )
    monkeypatch.setattr(research, "_build_client", fake_build_client)
    get_settings.cache_clear()
    try:
        # 模型清单端点：缺省模型恒在首位 + 按 provider 级联分组
        listed = client.get(f"/api/v2/projects/{project}/intake/research-models", headers=customer)
        assert listed.status_code == 200
        assert listed.json() == {
            "models": [
                "gpt-5.6-luna",
                "gpt-5.5",
                "claude-opus-5",
                "qwen3.7-max",
                "gemini-2.5-flash-search",
            ],
            "groups": [
                {"provider": "gpt", "models": ["gpt-5.6-luna", "gpt-5.5"]},
                {"provider": "claude", "models": ["claude-opus-5"]},
                {"provider": "qwen", "models": ["qwen3.7-max"]},
                {"provider": "gemini", "models": ["gemini-2.5-flash-search"]},
            ],
        }

        # gpt 系 → Responses + web_search 工具
        ok = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "model": "gpt-5.5"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["model"] == "gpt-5.5"
        assert seen[0][0] == "/v1/responses"
        assert seen[0][1]["model"] == "gpt-5.5"
        assert seen[0][1]["tools"][0]["type"] == "web_search"

        # claude 系 → Anthropic /v1/messages + web_search_20250305 server 工具
        claude = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "model": "claude-opus-5"},
        )
        assert claude.status_code == 200, claude.text
        assert seen[-1][0] == "/v1/messages"
        assert seen[-1][1]["tools"] == [{"type": "web_search_20250305", "name": "web_search"}]
        assert seen[-1][1]["max_tokens"] >= 16384  # API 强制字段，取输出上限级

        # qwen 系 → /chat/completions + enable_search:true
        qwen = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "model": "qwen3.7-max"},
        )
        assert qwen.status_code == 200, qwen.text
        assert seen[-1][0] == "/v1/chat/completions"
        assert seen[-1][1]["enable_search"] is True

        # gemini *-search 系 → /chat/completions（搜索内建，不带 tools/enable_search）
        gemini = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "model": "gemini-2.5-flash-search"},
        )
        assert gemini.status_code == 200, gemini.text
        assert seen[-1][0] == "/v1/chat/completions"
        assert "tools" not in seen[-1][1]
        assert "enable_search" not in seen[-1][1]

        # 白名单外模型 → 400 model_not_allowed（不发 LLM 请求）
        rejected = client.post(
            f"/api/v2/projects/{project}/intake/ai-research",
            headers=customer,
            json={"brand": "示例科技", "model": "doubao-seed-2-1-pro"},
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "model_not_allowed"
        assert len(seen) == 4
    finally:
        get_settings.cache_clear()


def test_profile_docx_export(
    intake_env: tuple[TestClient, str, dict[str, str], dict[str, str], str],
) -> None:
    client, _, customer, analyst, project = intake_env
    profile_url = f"/api/v2/projects/{project}/intake/profile"
    client.put(
        profile_url,
        headers=customer,
        json={
            "contact_person": "张三",
            "goals": ["提升AI搜索曝光"],
            "review_category": "none",
            "truth_confirmed": True,
        },
    )
    client.post(
        f"/api/v2/projects/{project}/intake/promos",
        headers={**customer, "Idempotency-Key": _idem()},
        json={"kind": "product", "payload": {"name": "主打产品", "features": ["服务好"]}},
    )
    response = client.get(f"/api/v2/projects/{project}/intake/profile.docx", headers=customer)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content[:2] == b"PK"
    assert len(response.content) > 5000
    # analyst 只读也可导出
    assert (
        client.get(f"/api/v2/projects/{project}/intake/profile.docx", headers=analyst).status_code
        == 200
    )
