import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.intake import research
from geo_platform.intake_form import models as form_models
from geo_platform.main import app
from geo_platform.projects.models import Brand, Competitor
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.repository import TenantRepository
from sqlalchemy import select

_SECRET = "Authorization: Bearer " + "x" * 32


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


def _create_project(client: TestClient, headers: dict[str, str], name: str = "Form Project") -> str:
    response = client.post(
        "/api/v2/projects",
        headers={**headers, "Idempotency-Key": "project-" + secrets.token_hex(16)},
        json={"name": name, "customer_name": "Form Customer"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _idem() -> str:
    return "idem-" + secrets.token_hex(16)


def _create_invite(
    client: TestClient, headers: dict[str, str], project: str, **body: object
) -> dict:
    response = client.post(
        f"/api/v2/projects/{project}/intake/invites",
        headers={**headers, "Idempotency-Key": _idem()},
        json=body or {},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _token_headers(token: str) -> dict[str, str]:
    return {"X-Intake-Token": token}


@pytest.fixture()
def form_env() -> Iterator[tuple[TestClient, str, dict[str, str], str, dict]]:
    client = TestClient(app)
    tenant, admin_headers = _bootstrap(client, "form-admin-" + secrets.token_hex(6))
    project = _create_project(client, admin_headers)
    invite = _create_invite(client, admin_headers, project)
    yield client, tenant, admin_headers, project, invite


# ── 签发 / 列表 / 撤销 ───────────────────────────────────────────────────────
def test_invite_issue_list_revoke(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, _, admin, project, invite = form_env
    assert invite["token"]  # 原文只在签发响应出现
    assert invite["ai_quota"] == 3
    assert invite["submitted_at"] is None
    assert invite["revoked_at"] is None

    listing = client.get(f"/api/v2/projects/{project}/intake/invites", headers=admin)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [i["pub_id"] for i in items] == [invite["pub_id"]]
    assert "token" not in items[0]  # 列表绝不含原文

    # 幂等重放：同 Idempotency-Key → 同一邀请，不再发原文
    # （重放走 200 视图，token=None）
    # 撤销
    revoked = client.delete(
        f"/api/v2/projects/{project}/intake/invites/{invite['pub_id']}", headers=admin
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": invite["pub_id"], "already_revoked": False}
    again = client.delete(
        f"/api/v2/projects/{project}/intake/invites/{invite['pub_id']}", headers=admin
    )
    assert again.json()["already_revoked"] is True
    # 撤销后 token 域 403 revoked
    ctx = client.get("/api/v2/intake-form/context", headers=_token_headers(invite["token"]))
    assert ctx.status_code == 403
    assert ctx.json()["error"]["code"] == "invite_token_revoked"
    # 不存在 404
    missing = client.delete(f"/api/v2/projects/{project}/intake/invites/itv_missing", headers=admin)
    assert missing.status_code == 404


def test_invite_issue_idempotent_replay(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, _, admin, project, _ = form_env
    key = _idem()
    first = client.post(
        f"/api/v2/projects/{project}/intake/invites",
        headers={**admin, "Idempotency-Key": key},
        json={"ttl_hours": 24, "ai_quota": 1},
    )
    assert first.status_code == 201
    replay = client.post(
        f"/api/v2/projects/{project}/intake/invites",
        headers={**admin, "Idempotency-Key": key},
        json={"ttl_hours": 24, "ai_quota": 1},
    )
    assert replay.status_code == 200 or replay.status_code == 201
    assert replay.json()["pub_id"] == first.json()["pub_id"]
    assert replay.json()["replay"] is True
    assert replay.json()["token"] is None  # 重放不再发原文
    listing = client.get(f"/api/v2/projects/{project}/intake/invites", headers=admin).json()
    assert len(listing["items"]) == 2  # 固件 1 + 本次 1（重放未新建）


# ── token 失效态 ─────────────────────────────────────────────────────────────
def test_token_invalid_states(form_env: tuple[TestClient, str, dict[str, str], str, dict]) -> None:
    client, tenant, _, _, invite = form_env
    # 缺头 401
    assert client.get("/api/v2/intake-form/context").status_code == 401
    # 错 token 403 invalid
    bad = client.get(
        "/api/v2/intake-form/context", headers=_token_headers(secrets.token_urlsafe(32))
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "invite_token_invalid"

    # 过期 403 expired（直连 DB 把 expires_at 拨到过去）
    with SessionLocal() as session:
        repository = TenantRepository(session, tenant)
        row = session.scalar(
            select(form_models.IntakeInvite).where(
                form_models.IntakeInvite.tenant_id == repository.tenant.id,
                form_models.IntakeInvite.pub_id == invite["pub_id"],
            )
        )
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    expired = client.get("/api/v2/intake-form/context", headers=_token_headers(invite["token"]))
    assert expired.status_code == 403
    assert expired.json()["error"]["code"] == "invite_token_expired"


# ── 上下文 + 租户隔离 ────────────────────────────────────────────────────────
def test_context_and_tenant_isolation(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, _, admin, project, invite = form_env
    headers = _token_headers(invite["token"])
    ctx = client.get("/api/v2/intake-form/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    body = ctx.json()
    assert body["form"]["title"] == "GEO 客户信息收集表（通用版）"
    assert body["invite"]["ai_quota"] == 3
    assert body["invite"]["ai_remaining"] == 3
    assert body["invite"]["submitted"] is False
    assert body["profile"]["exists"] is False

    # 另一租户的 project：token 只摸得到自己 project（操作面 404，token 面绑死）
    _, other_admin = _bootstrap(client, "form-other-" + secrets.token_hex(6))
    other_project = _create_project(client, other_admin, "Other Project")
    assert (
        client.get(f"/api/v2/projects/{other_project}/intake/invites", headers=admin).status_code
        == 404
    )
    # 本 token 的 profile 写入只落在绑定 project
    put = client.put(
        "/api/v2/intake-form/profile", headers=headers, json={"contact_person": "张三"}
    )
    assert put.status_code == 200, put.text
    own = client.get(f"/api/v2/projects/{project}/intake/profile", headers=admin)
    assert own.json()["contact_person"] == "张三"
    other = client.get(f"/api/v2/projects/{other_project}/intake/profile", headers=other_admin)
    assert other.json()["exists"] is False


# ── 写端点：promo/trigger/词表/DLP ───────────────────────────────────────────
def test_token_domain_writes_and_fail_closed(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])

    promo = client.post(
        "/api/v2/intake-form/promos",
        headers=headers,
        json={"kind": "product", "payload": {"name": "扫地机器人", "features": ["价格优势"]}},
    )
    assert promo.status_code == 201, promo.text
    # 匿名端缺 Idempotency-Key 时按 body 自然幂等
    replay = client.post(
        "/api/v2/intake-form/promos",
        headers=headers,
        json={"kind": "product", "payload": {"name": "扫地机器人", "features": ["价格优势"]}},
    )
    assert replay.status_code == 201
    assert replay.json()["pub_id"] == promo.json()["pub_id"]

    triggers = client.post(
        "/api/v2/intake-form/trigger-questions",
        headers=headers,
        json={"text": "扫地机器人怎么选\n预算三千买哪个"},
    )
    assert triggers.status_code == 201, triggers.text
    assert len(triggers.json()["items"]) == 2

    # 词表 fail-closed 422
    assert (
        client.put(
            "/api/v2/intake-form/profile", headers=headers, json={"review_category": "Z"}
        ).status_code
        == 422
    )
    # DLP 422 且不回显
    rejected = client.put(
        "/api/v2/intake-form/profile", headers=headers, json={"selling_points": f"卖点 {_SECRET}"}
    )
    assert rejected.status_code == 422
    assert _SECRET not in rejected.text


# ── AI 配额 ──────────────────────────────────────────────────────────────────
def _mock_suggest_payload() -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "questions": [
                                    {
                                        "question": "扫地机器人怎么选",
                                        "core_word": "扫地机器人",
                                        "heat": 90,
                                    },
                                    {
                                        "question": "两千价位扫地机器人推荐",
                                        "core_word": "扫地机器人",
                                        "heat": 80,
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_query_suggestions_and_quota(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, admin, project, _ = form_env
    invite = _create_invite(client, admin, project, ai_quota=1)
    headers = _token_headers(invite["token"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        # 纯生成：不带 web_search 工具
        assert "tools" not in (request.content and json.loads(request.content) or {})
        return httpx.Response(200, json=_mock_suggest_payload())

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
        ok = client.post(
            "/api/v2/intake-form/query-suggestions",
            headers=headers,
            json={"core_words": ["扫地机器人"], "n": 5},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["candidate_only"] is True
        assert [q["question"] for q in body["questions"]] == [
            "扫地机器人怎么选",
            "两千价位扫地机器人推荐",
        ]
        assert body["ai_used"] == 1
        assert body["ai_remaining"] == 0
        # 配额用尽 → 429
        exhausted = client.post(
            "/api/v2/intake-form/query-suggestions",
            headers=headers,
            json={"core_words": ["扫地机器人"]},
        )
        assert exhausted.status_code == 429
        assert exhausted.json()["error"]["code"] == "quota_exhausted"
        # ai-research 同配额 → 也 429
        assert (
            client.post(
                "/api/v2/intake-form/ai-research", headers=headers, json={"brand": "示例"}
            ).status_code
            == 429
        )
    finally:
        get_settings.cache_clear()


def test_ai_research_mocked(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "company_name": "示例科技",
                                        "website": "https://ai.example",
                                        "wechat": "mp",
                                        "goals": ["提升AI搜索曝光"],
                                        "products": [{"name": "主打产品"}],
                                        "trigger_questions": "示例科技怎么样",
                                        "summary": "s",
                                    },
                                    ensure_ascii=False,
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

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
            "/api/v2/intake-form/ai-research", headers=headers, json={"brand": "示例科技"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ai_used"] == 1
        assert "website" in body["prefilled"]
        profile = client.get("/api/v2/intake-form/profile", headers=headers).json()
        assert profile["website"] == "https://ai.example"
        assert profile["prefilled"]["website"] == "research:ai-live"
    finally:
        get_settings.cache_clear()


def test_llm_disabled_without_key(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])
    monkeypatch.setenv("GEO_RESEARCH_LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert (
            client.post(
                "/api/v2/intake-form/query-suggestions",
                headers=headers,
                json={"core_words": ["x"]},
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/api/v2/intake-form/ai-research", headers=headers, json={"brand": "x"}
            ).status_code
            == 503
        )
    finally:
        get_settings.cache_clear()


# ── submit 门 + 幂等 + 提交后写 409 ─────────────────────────────────────────
def test_submit_gate_idempotent_and_write_lockout(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])

    # truth/filler 缺失 → 422 列出缺失
    early = client.post("/api/v2/intake-form/submit", headers=headers)
    assert early.status_code == 422
    assert early.json()["error"]["code"] == "submit_incomplete"

    client.put(
        "/api/v2/intake-form/profile",
        headers=headers,
        json={"truth_confirmed": True},
    )
    still = client.post("/api/v2/intake-form/submit", headers=headers)
    assert still.status_code == 422

    client.put("/api/v2/intake-form/profile", headers=headers, json={"filler_name": "张三"})
    done = client.post("/api/v2/intake-form/submit", headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["submitted"] is True
    assert done.json()["replay"] is False

    # 幂等：重复提交 200 replay
    replay = client.post("/api/v2/intake-form/submit", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["replay"] is True
    assert replay.json()["submitted_at"] == done.json()["submitted_at"]

    # 提交后全部写端点 409
    assert (
        client.put(
            "/api/v2/intake-form/profile", headers=headers, json={"contact_person": "x"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v2/intake-form/promos",
            headers=headers,
            json={"kind": "product", "payload": {"name": "x"}},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v2/intake-form/trigger-questions", headers=headers, json={"text": "x"}
        ).status_code
        == 409
    )
    assert (
        client.patch("/api/v2/intake-form/brand", headers=headers, json={"name": "x"}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v2/intake-form/competitors", headers=headers, json={"name": "x"}
        ).status_code
        == 409
    )
    # 读端点仍可用
    assert client.get("/api/v2/intake-form/context", headers=headers).status_code == 200
    assert client.get("/api/v2/intake-form/profile", headers=headers).status_code == 200


# ── brand / competitor ───────────────────────────────────────────────────────
def test_brand_and_competitor_editing(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
) -> None:
    client, tenant, _, _, invite = form_env
    headers = _token_headers(invite["token"])

    empty = client.get("/api/v2/intake-form/brand", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["exists"] is False

    patched = client.patch(
        "/api/v2/intake-form/brand",
        headers=headers,
        json={"name": "示例品牌", "website": "https://brand.example", "aliases": ["示例", "示例"]},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["exists"] is True
    assert body["name"] == "示例品牌"
    assert body["aliases"] == ["示例"]  # 去重

    again = client.get("/api/v2/intake-form/brand", headers=headers).json()
    assert again["website"] == "https://brand.example"

    created = client.post(
        "/api/v2/intake-form/competitors",
        headers=headers,
        json={"name": "竞品A", "website": "https://a.example"},
    )
    assert created.status_code == 201, created.text
    comp_id = created.json()["pub_id"]
    listing = client.get("/api/v2/intake-form/competitors", headers=headers).json()
    assert [c["pub_id"] for c in listing["items"]] == [comp_id]
    deleted = client.delete(f"/api/v2/intake-form/competitors/{comp_id}", headers=headers)
    assert deleted.status_code == 200
    missing = client.delete(f"/api/v2/intake-form/competitors/{comp_id}", headers=headers)
    assert missing.status_code == 404
    # DLP
    assert (
        client.post(
            "/api/v2/intake-form/competitors", headers=headers, json={"name": f"x {_SECRET}"}
        ).status_code
        == 422
    )
    # 运营端视角可见（同 project 数据面，直连 DB 走 tenant 上下文核对）
    with SessionLocal() as session:
        repository = TenantRepository(session, tenant)
        brand = session.scalar(
            select(Brand).where(Brand.tenant_id == repository.tenant.id, Brand.name == "示例品牌")
        )
        assert brand is not None and brand.website == "https://brand.example"
        remaining = session.scalars(
            select(Competitor).where(Competitor.tenant_id == repository.tenant.id)
        ).all()
        assert list(remaining) == []


# ── SiliconIndex ─────────────────────────────────────────────────────────────
def test_siliconindex_degrades_without_snapshot(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])
    monkeypatch.setenv("GEO_SILICONINDEX_SNAPSHOT_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    try:
        response = client.get("/api/v2/intake-form/siliconindex/candidates", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"available": False}
        templates = client.post(
            "/api/v2/intake-form/siliconindex/template-questions", headers=headers, json={}
        )
        assert templates.status_code == 200
        assert templates.json() == {"available": False}
    finally:
        get_settings.cache_clear()


def _write_snapshot(root: Path) -> None:
    snap = root / "2026-08-01"
    snap.mkdir(parents=True)
    (root / "CURRENT").write_text("2026-08-01", encoding="utf-8")
    (snap / "snapshot-meta.json").write_text(
        json.dumps({"release_id": "si-2026-08-01"}), encoding="utf-8"
    )
    (snap / "brands.json").write_text(
        json.dumps(
            [
                {
                    "brand_id": "b1",
                    "canonical_name": "示例品牌",
                    "display_name": "示例品牌",
                    "primary_category_id": "c1",
                    "category_ids": ["c1"],
                },
                {"brand_id": "b2", "canonical_name": "竞品A", "display_name": "竞品A"},
            ]
        ),
        encoding="utf-8",
    )
    (snap / "mentions.json").write_text(
        json.dumps(
            [
                {
                    "brand_id": "b1",
                    "text": "示例",
                    "mention_type": "alias",
                    "match_mode": "exact",
                    "status": "reviewed",
                }
            ]
        ),
        encoding="utf-8",
    )
    (snap / "competitor-relations.json").write_text(
        json.dumps(
            [
                {
                    "source_brand_id": "b1",
                    "target_brand_id": "b2",
                    "relation_type": "direct",
                    "strength": 0.9,
                    "status": "reviewed",
                }
            ]
        ),
        encoding="utf-8",
    )
    (snap / "categories.json").write_text(
        json.dumps(
            [
                {
                    "category_id": "c1",
                    "level_1": "家电",
                    "level_2": "清洁电器",
                    "level_3": "扫地机器人",
                }
            ]
        ),
        encoding="utf-8",
    )
    (snap / "query-templates.json").write_text(
        json.dumps(
            [
                {
                    "template_id": "t1",
                    "category_id": "c1",
                    "template": "{region}扫地机器人怎么选",
                    "variables": ["region"],
                    "intent": "选购",
                    "analysis_dimensions": ["推荐"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (snap / "compliance-rules.json").write_text(json.dumps([]), encoding="utf-8")


def test_siliconindex_with_snapshot(
    form_env: tuple[TestClient, str, dict[str, str], str, dict],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _, _, _, invite = form_env
    headers = _token_headers(invite["token"])
    _write_snapshot(tmp_path)
    monkeypatch.setenv("GEO_SILICONINDEX_SNAPSHOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        response = client.get(
            "/api/v2/intake-form/siliconindex/candidates",
            headers=headers,
            params={"name": "示例品牌"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["available"] is True
        assert body["matched"] is True
        assert body["brand"]["brand_id"] == "b1"
        assert body["category_path"] == ["家电", "清洁电器", "扫地机器人"]
        assert body["mention_rules"][0]["text"] == "示例"
        assert body["competitors"][0]["brand"]["brand_id"] == "b2"

        # mention 文本也能解析
        by_alias = client.get(
            "/api/v2/intake-form/siliconindex/candidates", headers=headers, params={"name": "示例"}
        )
        assert by_alias.json()["matched"] is True

        miss = client.get(
            "/api/v2/intake-form/siliconindex/candidates",
            headers=headers,
            params={"name": "不存在"},
        )
        assert miss.json()["matched"] is False
        assert miss.json()["compliance"]["disclaimer"] == "索引未命中，需人工复核"

        templates = client.post(
            "/api/v2/intake-form/siliconindex/template-questions",
            headers=headers,
            json={"region": "上海"},
        )
        # project 无 brand 行时回落 project.name（不在索引中）→ matched False
        assert templates.json()["available"] is True
        assert templates.json()["matched"] is False

        # 建好 brand 后模板按品牌渲染
        client.patch("/api/v2/intake-form/brand", headers=headers, json={"name": "示例品牌"})
        rendered = client.post(
            "/api/v2/intake-form/siliconindex/template-questions",
            headers=headers,
            json={"region": "上海"},
        )
        assert rendered.json()["matched"] is True
        assert rendered.json()["candidate_only"] is True
        assert rendered.json()["questions"][0]["text"] == "上海扫地机器人怎么选"
        assert rendered.json()["questions"][0]["index_version"] == "si-2026-08-01"
    finally:
        get_settings.cache_clear()
