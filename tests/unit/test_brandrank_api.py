"""api/geo_platform/brandrank：只读端点 + service 编排的单元测试。

全 fake：不起服务（TestClient 进程内）、不连 PG（monkeypatch service.fetch_* 三接缝）、
不烧 LLM（monkeypatch extract.default_client 注入 SDK 形状假 client）、缓存指 tmp_path。
身份走 dependency_overrides[get_principal]（与 tests/integration 同款模式）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from geo_platform.brandrank import service
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app

from domain.brandrank import extract

TENANT = "tnt_brandrank"
PROJECT = "prj_brandrank"
NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)

client = TestClient(app)


# ── fake 接缝 ─────────────────────────────────────────────────────────────
class FakeClient:
    """OpenAI SDK 形状假 client：按答案文本内容决定品牌列表/抛错。"""

    def __init__(self, behavior):
        self.calls = 0
        self._behavior = behavior
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, temperature, response_format):
        self.calls += 1
        reply_text = messages[1]["content"].split("以下是AI回复文本：\n", 1)[1]
        outcome = self._behavior(reply_text)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"brands": outcome}, ensure_ascii=False)
                    )
                )
            ]
        )


def _answer(
    pub_id: str,
    text: str,
    *,
    mode: str = "normal",
    region: str = "北京",
    model: str = "doubao",
    query: str = "保险公司推荐",
) -> dict:
    return {
        "pub_id": pub_id,
        "query_text": query,
        "response_text": text,
        "model": model,
        "region": region,
        "mode": mode,
        "capture_time": NOW,
    }


def _citation(host: str, ordinal: int = 1) -> dict:
    return {
        "ordinal": ordinal,
        "host": host,
        "canonical_url": f"https://{host}/a",
        "original_url": f"https://{host}/a",
    }


def _project(brandrank_domain: str | None = "insurance") -> dict:
    return {
        "pub_id": PROJECT,
        "name": "测试项目",
        "brandrank_domain": brandrank_domain,
        "brand_names": ["中意人寿"],
        "competitor_names": ["中国平安"],
    }


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每用例隔离：缓存指 tmp、LLM env 清空（各用例按需自行 setenv）。"""
    monkeypatch.setenv("GEO_BRANDRANK_EXTRACT_CACHE_DIR", str(tmp_path))
    for key in (
        "GEO_BRANDRANK_LLM_API_KEY",
        "GEO_BRANDRANK_LLM_BASE_URL",
        "GEO_BRANDRANK_LLM_BASE_URL_FALLBACK",
        "GEO_BRANDRANK_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    app.dependency_overrides.pop(get_principal, None)


def _override_principal(role: Role = Role.OPERATOR, tenant: str = TENANT) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-brandrank", role=role, tenant_pub_id=tenant
    )


def _patch_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: list[dict],
    citations: dict[str, list[dict]] | None = None,
    project: dict | None = None,
    table_rows: dict[str, dict] | None = None,
) -> dict:
    """monkeypatch service 的四个 DB 接缝；返回记录调用参数的盒子。"""
    seen: dict[str, object] = {}

    def fake_fetch_project(dsn: str, tenant_pub_id: str, project_pub_id: str):
        seen["tenant_pub_id"] = tenant_pub_id
        seen["project_pub_id"] = project_pub_id
        return project

    def fake_fetch_answers(dsn: str, tenant_pub_id: str, project_pub_id: str, since: datetime):
        seen["since"] = since
        return list(answers), False

    def fake_fetch_citations(dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]):
        return dict(citations or {})

    def fake_fetch_brand_extracts(
        dsn: str, tenant_pub_id: str, answer_pub_ids: list[str], domain: str
    ):
        seen["extract_domain"] = domain
        return dict(table_rows or {})

    monkeypatch.setattr(service, "fetch_project", fake_fetch_project)
    monkeypatch.setattr(service, "fetch_answers", fake_fetch_answers)
    monkeypatch.setattr(service, "fetch_citations", fake_fetch_citations)
    monkeypatch.setattr(service, "fetch_brand_extracts", fake_fetch_brand_extracts)
    return seen


def _get(query: str = "", project: str = PROJECT):
    return client.get(f"/api/v2/projects/{project}/brand-visibility{query}")


# ── 权限门 ─────────────────────────────────────────────────────────────────
def test_requires_authentication_401() -> None:
    resp = _get()
    assert resp.status_code == 401


def test_permission_denied_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """worker 角色无 project:read → 403（门在 DB 访问之前）。"""
    _override_principal(Role.WORKER)
    monkeypatch.setattr(
        service, "fetch_project", lambda *a, **k: pytest.fail("越权访问不应触达 DB")
    )
    resp = _get()
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


# ── 项目/规则包解析 ─────────────────────────────────────────────────────────
def test_project_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    seen = _patch_fetch(monkeypatch, answers=[], project=None)
    resp = _get()
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
    # 租户谓词来自 principal（跨租户同 404）
    assert seen["tenant_pub_id"] == TENANT and seen["project_pub_id"] == PROJECT


def test_unmapped_industry_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """行业有值但未映射 → 400 fail-loud（绝不静默回退保险包）。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    resp = _get("?industry=餐饮")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unmapped_industry"
    assert "餐饮" in resp.json()["error"]["details"]["why"]


def test_industry_mapped_to_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """industry=法律 → legal 规则包（domain_source=industry 如实披露）。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    resp = _get("?industry=法律")
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == "legal" and body["domain_source"] == "industry"
    assert body["category"] == "律师事务所"


def test_unknown_domain_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    resp = _get("?domain=不存在的领域")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "unknown_domain"
    assert sorted(body["error"]["details"]["available"]) == ["cybersecurity", "insurance", "legal"]


# ── LLM 禁用态（诚实降级）──────────────────────────────────────────────────
def test_llm_disabled_503_when_extraction_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗内有答案且无缓存、GEO_BRANDRANK_LLM_API_KEY 未配 → 503 llm_disabled，绝不合成。"""
    _override_principal()
    _patch_fetch(
        monkeypatch, answers=[_answer("ans_1", "答案甲")], citations={}, project=_project()
    )
    resp = _get()
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "llm_disabled"
    assert body["error"]["details"]["llm"]["enabled"] is False


def test_zero_answers_insufficient_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗内零答案：无需 LLM，200 返回 insufficient=true 的诚实空分析。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    resp = _get()  # 故意不配 LLM key
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["insufficient"] is True
    assert body["result"]["extraction"]["n_answers"] == 0
    assert body["result"]["denominators"]["n_answers"] == 0
    assert body["result"]["overall"]["merged"] == []
    assert body["llm"]["enabled"] is False


# ── 完整链路：抽取→归并→指标→缓存命中 ───────────────────────────────────────
def _two_answers_behavior(text: str):
    if "答案甲" in text:
        return ["中意人寿保险", "中国平安"]
    if "答案乙" in text:
        return ["擎天柱11号", "中国平安"]
    return []


def test_happy_path_then_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    monkeypatch.setenv("GEO_BRANDRANK_LLM_MODEL", "m-test")
    seen = _patch_fetch(
        monkeypatch,
        answers=[
            _answer("ans_1", "答案甲", mode="normal", region="北京"),
            _answer("ans_2", "答案乙", mode="deep_think", region="上海"),
        ],
        citations={
            "ans_1": [_citation("www.zhihu.com")],
            "ans_2": [_citation("baijiahao.baidu.com")],
        },
        project=_project(),
    )
    fake = FakeClient(_two_answers_behavior)
    monkeypatch.setattr(extract, "default_client", lambda: fake)

    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    assert fake.calls == 2  # 两条各抽一次
    # 窗与项目上下文
    assert body["project_pub_id"] == PROJECT and body["window_days"] == 30
    assert seen["since"] is not None
    assert body["domain"] == "insurance" and body["domain_source"] == "project"
    # 合并榜：中意人寿（别名/产品名归并）occ=2 avg_rank=1.0 score=2.0 居首
    merged = body["result"]["overall"]["merged"]
    assert merged[0]["brand"] == "中意人寿"
    assert merged[0]["occurrences"] == 2 and merged[0]["avg_rank"] == 1.0
    assert merged[0]["score"] == 2.0 and merged[0]["rank"] == 1
    assert merged[1]["brand"] == "中国平安"
    # 双分母成对披露
    assert set(merged[0]["top_rates"]["3"]) == {"of_mentions", "of_total"}
    # raw 榜保留原始产品名（合并只作用 merged 口径）
    raw_brands = {row["brand"] for row in body["result"]["overall"]["raw"]}
    assert "擎天柱11号" in raw_brands
    # 目标品牌/竞品缺省取项目 brand/competitor
    assert body["target_brand"] == "中意人寿"
    assert body["result"]["target_brand"]["mentions"] == 2
    assert body["competitors"] == ["中国平安"]
    assert body["result"]["competitors"][0]["mentions"] == 2
    # 分组维与信源
    assert set(body["result"]["by_mode"]) == {"快速", "思考"}
    assert body["result"]["sources"]["overall"]["total"] == 2
    assert body["result"]["sources"]["overall"]["sources"][0]["sitename"] in {
        "zhihu.com",
        "baijiahao.baidu.com",
    }
    # 抽取账目
    ext = body["result"]["extraction"]
    assert ext["n_answers"] == 2 and ext["cached_ok"] == 0
    assert ext["extracted_new"] == 2 and ext["failed_total"] == 0
    assert ext["llm_model"] == "m-test"
    assert body["llm"] == {"enabled": True, "model": "m-test"}

    # 第二次：全部命中缓存——default_client 被调即失败
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("缓存命中不应再调 LLM"))
    resp2 = _get()
    assert resp2.status_code == 200
    ext2 = resp2.json()["result"]["extraction"]
    assert ext2["cached_ok"] == 2 and ext2["extracted_new"] == 0
    assert resp2.json()["result"]["overall"]["merged"] == merged  # 结果与首跑一致


def test_extraction_failure_honest_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    """单条抽取失败：不进品牌分析（零合成）但信源照算，failed 计数如实披露。"""
    _override_principal()
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")

    def behavior(text: str):
        if "答案乙" in text:
            return RuntimeError("api down")
        return ["中意人寿保险", "中国平安"]

    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1", "答案甲"), _answer("ans_2", "答案乙")],
        citations={"ans_2": [_citation("zhihu.com")]},
        project=_project(),
    )
    monkeypatch.setattr(extract, "default_client", lambda: FakeClient(behavior))
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    ext = body["result"]["extraction"]
    assert ext["extracted_new"] == 1 and ext["failed_new"] == 1 and ext["failed_total"] == 1
    # 失败条不进品牌榜（只有 ans_1 的品牌）；其引用仍进信源。
    # 分母口径照旧库：denominators.n_answers=入分析条数（仅抽取成功），
    # extraction.n_answers=窗内全部 eligible 条数（两处不一致由 extraction 账目披露）。
    assert body["result"]["denominators"]["n_answers"] == 1
    assert body["result"]["extraction"]["n_answers"] == 2
    assert {r["brand"] for r in body["result"]["overall"]["merged"]} == {"中意人寿", "中国平安"}
    assert body["result"]["sources"]["overall"]["total"] == 1


def test_explicit_params_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 competitors/top_ns/target_brand 覆盖项目缺省。"""
    _override_principal()
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    _patch_fetch(
        monkeypatch, answers=[_answer("ans_1", "答案甲")], citations={}, project=_project()
    )
    monkeypatch.setattr(extract, "default_client", lambda: FakeClient(_two_answers_behavior))
    resp = _get("?competitors=中国人寿&top_ns=1&top_ns=3&target_brand=中国平安")
    assert resp.status_code == 200
    body = resp.json()
    assert body["competitors"] == ["中国人寿"]
    assert body["target_brand"] == "中国平安"
    assert body["result"]["top_ns"] == [1, 3]
    assert set(body["result"]["overall"]["merged"][0]["top_rates"]) == {"1", "3"}


def test_window_days_validation_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    assert _get("?window_days=0").status_code == 422
    assert _get("?window_days=367").status_code == 422
    assert _get("?top_ns=abc").status_code == 422
    assert _get("?top_ns=0").status_code == 422


# ── 响应投影安全：不泄露账号/profile/会话维 ────────────────────────────────
def test_response_has_no_account_or_profile_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1", "答案甲")],
        citations={"ans_1": [_citation("zhihu.com")]},
        project=_project(),
    )
    monkeypatch.setattr(extract, "default_client", lambda: FakeClient(_two_answers_behavior))
    resp = _get()
    assert resp.status_code == 200
    payload = resp.text
    for forbidden in (
        "platform_account",
        "browser_profile",
        "session_event",
        "authorization_scope",
        "response_text",
    ):
        assert forbidden not in payload


# ── service 层：domain 解析优先级 ───────────────────────────────────────────
def test_resolve_rules_priority() -> None:
    rules, source = service.resolve_rules("legal", "保险")
    assert rules.domain == "legal" and source == "explicit"  # 显式 domain 最优先
    rules, source = service.resolve_rules(None, "法律")
    assert rules.domain == "legal" and source == "industry"
    rules, source = service.resolve_rules(None, None)
    assert rules.domain == "insurance" and source == "default"  # V2 无持久行业→缺省+披露
    rules, source = service.resolve_rules("  ", "  ")
    assert rules.domain == "insurance" and source == "default"  # 纯空白视同未给
    with pytest.raises(service.UnmappedIndustry):
        service.resolve_rules(None, "餐饮")
    with pytest.raises(service.UnknownDomain):
        service.resolve_rules("不存在", None)


def test_resolve_rules_allow_default_false_fail_loud() -> None:
    """allow_default=False（visibility 端点路径）：三者皆无 → DomainUnresolved，
    绝不静默回退缺省包；显式 domain/industry/项目真源不受影响。"""
    with pytest.raises(service.DomainUnresolved):
        service.resolve_rules(None, None, allow_default=False)
    with pytest.raises(service.DomainUnresolved):
        service.resolve_rules(None, None, "  ", allow_default=False)  # 空白真源视同未设
    rules, source = service.resolve_rules("insurance", None, allow_default=False)
    assert source == "explicit" and rules.domain == "insurance"
    rules, source = service.resolve_rules(None, "法律", allow_default=False)
    assert source == "industry" and rules.domain == "legal"
    rules, source = service.resolve_rules(None, None, "cybersecurity", allow_default=False)
    assert source == "project" and rules.domain == "cybersecurity"


def test_visibility_domain_unresolved_fail_loud_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """端点路径：无显式 domain/industry 且项目真源未设 → 400 brandrank_domain_unresolved；
    显式 domain/industry 参数仍可正常解析（不受 fail-loud 影响）。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain=None))
    resp = _get()
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "brandrank_domain_unresolved"
    assert sorted(body["error"]["details"]["available"]) == ["cybersecurity", "insurance", "legal"]
    resp2 = _get("?domain=insurance")
    assert resp2.status_code == 200
    assert resp2.json()["domain_source"] == "explicit"
    resp3 = _get("?industry=保险")
    assert resp3.status_code == 200
    assert resp3.json()["domain_source"] == "industry"


# ── s06_0014：表→文件→LLM 读取顺序 + 项目级 domain 真源 ─────────────────────
def _table_row(brands: list[str], *, status: str = "ok", model: str = "m-fanout") -> dict:
    return {
        "brands": brands,
        "status": status,
        "model": model,
        "error": None if status == "ok" else "api_error: upstream_500",
        "domain": "insurance",
        "extracted_at": NOW.isoformat(),
    }


def test_table_hit_short_circuits_cache_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """fanout 落账表 ok 行直接命中：不读文件缓存、不调 LLM，账目 table_ok 如实披露。"""
    _override_principal()
    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1", "答案甲"), _answer("ans_2", "答案乙")],
        citations={},
        project=_project(),
        table_rows={
            "ans_1": _table_row(["中意人寿", "中国平安"]),
            "ans_2": _table_row(["中国人寿"]),
        },
    )
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("表命中不应再调 LLM"))
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    ext = body["result"]["extraction"]
    assert ext["table_ok"] == 2 and ext["cached_ok"] == 0 and ext["extracted_new"] == 0
    assert {r["brand"] for r in body["result"]["overall"]["merged"]} == {
        "中意人寿",
        "中国平安",
        "中国人寿",
    }


def test_table_failed_row_falls_through_to_file_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """表内 failed 行=未命中（与文件缓存 failed 条目同口径）→ 落文件缓存兜底。"""
    from domain.brandrank import cache

    _override_principal()
    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1", "答案甲")],
        citations={},
        project=_project(),
        table_rows={"ans_1": _table_row([], status="failed")},
    )
    # 文件缓存预置同 domain 同文本的 ok 条目
    cache.store(
        cache.cache_key("insurance", "答案甲"),
        brands=["中意人寿"],
        model="m-cached",
        status="ok",
        domain="insurance",
    )
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("缓存兜底命中不应再调 LLM"))
    resp = _get()
    assert resp.status_code == 200
    ext = resp.json()["result"]["extraction"]
    assert ext["table_ok"] == 0 and ext["cached_ok"] == 1 and ext["extracted_new"] == 0
    assert resp.json()["result"]["overall"]["merged"][0]["brand"] == "中意人寿"


def test_table_failed_and_no_cache_falls_through_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """表 failed + 缓存未命中 → LLM 现抽兜底（端点对历史未覆盖 run 的旧行为保持）。"""
    _override_principal()
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    monkeypatch.setenv("GEO_BRANDRANK_LLM_MODEL", "m-test")
    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1", "答案甲")],
        citations={},
        project=_project(),
        table_rows={"ans_1": _table_row([], status="failed")},
    )
    fake = FakeClient(_two_answers_behavior)
    monkeypatch.setattr(extract, "default_client", lambda: fake)
    resp = _get()
    assert resp.status_code == 200
    ext = resp.json()["result"]["extraction"]
    assert fake.calls == 1
    assert ext["table_ok"] == 0 and ext["extracted_new"] == 1


def test_project_brandrank_domain_is_default_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """项目设了 brandrank_domain → 缺省用它（真源），domain_source=project 如实披露。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain="legal"))
    resp = _get()  # 无显式 domain/industry 参数
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == "legal" and body["domain_source"] == "project"
    assert body["category"] == "律师事务所"


def test_explicit_domain_and_industry_beat_project_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 domain/industry 参数优先于项目真源（端点行为保持）。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain="legal"))
    resp = _get("?domain=insurance")
    assert resp.status_code == 200
    assert resp.json()["domain"] == "insurance"
    assert resp.json()["domain_source"] == "explicit"
    resp2 = _get("?industry=保险")
    assert resp2.status_code == 200
    assert resp2.json()["domain"] == "insurance"
    assert resp2.json()["domain_source"] == "industry"


def test_project_domain_invalid_fail_loud_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """真源列值非法（绕过 API 词表校验的直写）→ 400 unknown_domain，绝不静默回退。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain="不存在的领域"))
    resp = _get()
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unknown_domain"


def test_resolve_rules_project_domain_priority() -> None:
    rules, source = service.resolve_rules(None, None, "legal")
    assert rules.domain == "legal" and source == "project"  # 项目真源>缺省包
    rules, source = service.resolve_rules("insurance", None, "legal")
    assert rules.domain == "insurance" and source == "explicit"  # 显式参数仍最优先
    rules, source = service.resolve_rules(None, "法律", "insurance")
    assert rules.domain == "legal" and source == "industry"  # 显式行业>项目真源
    rules, source = service.resolve_rules(None, None, "  ")
    assert rules.domain == "insurance" and source == "default"  # 空白真源视同未设
    with pytest.raises(service.UnknownDomain):
        service.resolve_rules(None, None, "不存在")  # 非法真源 fail-loud
