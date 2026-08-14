"""api/geo_platform/reports/fact_suggestions：报告事实建议端点单元测试。

全 fake：不起服务（TestClient 进程内）、不连 PG（monkeypatch brandrank.service 的
fetch_project/fetch_answers/fetch_brand_extracts 三接缝——本服务复用其语义与
monkeypatch 点）、**绝不烧 LLM**（报告路径不调 LLM：extract.default_client 一旦被
触达即判失败）。身份走 dependency_overrides[get_principal]（照 test_brandrank_api 模式）。

四指标映射（报价单项目1）：品牌提及率 / 推荐排名分布 / Top1·Top3·Top5 出现率 /
竞品对比——逐组（平台×地域×query）对拍 domain.brandrank.metrics.brand_special 输出。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.brandrank import service as brandrank_service
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app

from domain.brandrank import extract, metrics
from domain.brandrank.rules import load_domain


def _dev_pg_reachable() -> bool:
    """本文件用例经 TestClient 走 settings 默认 DSN（dev PG 127.0.0.1:55433）。

    2026-08-13 起开发栈已下线（生产只跑 production compose），dev PG 不可达时
    整组 skip——恢复方式见 backups/dev-stack-shutdown-20260813T163118/README.md。
    """
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 55433), timeout=1):
            return True
    except OSError:
        return False


if not _dev_pg_reachable():
    pytest.skip("dev PG(127.0.0.1:55433) 不在线（开发栈已下线）", allow_module_level=True)

TENANT = "tnt_facts"
PROJECT = "prj_facts"
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

client = TestClient(app)


def _answer(
    pub_id: str, *, model: str = "doubao", region: str = "北京", query: str = "保险公司推荐"
) -> dict:
    return {
        "pub_id": pub_id,
        "query_text": query,
        "response_text": f"文本{pub_id}",
        "model": model,
        "region": region,
        "mode": "normal",
        "capture_time": NOW,
    }


def _ok_row(brands: list[str]) -> dict:
    return {
        "brands": brands,
        "status": "ok",
        "model": "m-fanout",
        "error": None,
        "domain": "insurance",
        "extracted_at": NOW.isoformat(),
    }


def _project(
    brandrank_domain: str | None = "insurance",
    brand_names: list[str] | None = None,
    competitor_names: list[str] | None = None,
) -> dict:
    return {
        "pub_id": PROJECT,
        "name": "测试项目",
        "brandrank_domain": brandrank_domain,
        "brand_names": ["中意人寿"] if brand_names is None else brand_names,
        "competitor_names": ["中国平安"] if competitor_names is None else competitor_names,
    }


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每用例隔离：抽取缓存指 tmp；报告路径绝不触 LLM——default_client 触达即失败。"""
    monkeypatch.setenv("GEO_BRANDRANK_EXTRACT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("报告事实路径严禁调用 LLM"))
    yield
    app.dependency_overrides.pop(get_principal, None)


def _override_principal(role: Role = Role.OPERATOR, tenant: str = TENANT) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-facts", role=role, tenant_pub_id=tenant
    )


def _patch_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: list[dict],
    project: dict | None,
    table_rows: dict[str, dict] | None = None,
) -> dict:
    """monkeypatch brandrank.service 的三个 DB 接缝；返回记录调用参数的盒子。"""
    seen: dict[str, object] = {}

    def fake_fetch_project(dsn: str, tenant_pub_id: str, project_pub_id: str):
        seen["tenant_pub_id"] = tenant_pub_id
        seen["project_pub_id"] = project_pub_id
        return project

    def fake_fetch_answers(dsn: str, tenant_pub_id: str, project_pub_id: str, since: datetime):
        seen["since"] = since
        return list(answers), False

    def fake_fetch_brand_extracts(
        dsn: str, tenant_pub_id: str, answer_pub_ids: list[str], domain: str
    ):
        seen["extract_domain"] = domain
        seen["extract_answer_pub_ids"] = list(answer_pub_ids)
        return dict(table_rows or {})

    monkeypatch.setattr(brandrank_service, "fetch_project", fake_fetch_project)
    monkeypatch.setattr(brandrank_service, "fetch_answers", fake_fetch_answers)
    monkeypatch.setattr(brandrank_service, "fetch_brand_extracts", fake_fetch_brand_extracts)
    return seen


def _get(query: str = "", project: str = PROJECT):
    return client.get(f"/api/v2/projects/{project}/report-fact-suggestions{query}")


def _row(body: dict, *, platform: str, region: str, query: str, metric: str) -> dict[str, Any]:
    for row in body["fact_rows"]:
        dims = row["dimensions"]
        if (dims["platform"], dims["region"], dims["query"], row["metric"]) == (
            platform,
            region,
            query,
            metric,
        ):
            return row
    raise AssertionError(f"fact row not found: {platform}/{region}/{query}/{metric}")


# ── 权限门 ─────────────────────────────────────────────────────────────────
def test_requires_authentication_401() -> None:
    assert _get().status_code == 401


def test_permission_denied_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """worker 角色无 project:read → 403（门在 DB 访问之前）。"""
    _override_principal(Role.WORKER)
    monkeypatch.setattr(
        brandrank_service, "fetch_project", lambda *a, **k: pytest.fail("越权访问不应触达 DB")
    )
    resp = _get()
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


# ── 项目/domain 真源解析 ─────────────────────────────────────────────────────
def test_project_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    seen = _patch_fetch(monkeypatch, answers=[], project=None)
    resp = _get()
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
    assert seen["tenant_pub_id"] == TENANT and seen["project_pub_id"] == PROJECT


def test_domain_unset_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """项目未设 brandrank_domain → 400 domain_unset（不回退缺省包；空白同口径）。"""
    _override_principal()
    for unset in (None, "", "   "):
        _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain=unset))
        resp = _get()
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "domain_unset"
        assert "brandrank_domain" in body["error"]["details"]["why"]


def test_unknown_domain_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """真源列值非法（绕过 API 词表校验的直写）→ 400 unknown_domain，附可选包清单。"""
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project(brandrank_domain="不存在"))
    resp = _get()
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "unknown_domain"
    # 可选包清单对齐真源（rules 注册表），不硬编码——新增规则包不需改测试
    from domain.brandrank.rules import available_domains

    assert sorted(body["error"]["details"]["available"]) == sorted(available_domains())


def test_window_days_validation_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    assert _get("?window_days=0").status_code == 422
    assert _get("?window_days=367").status_code == 422


# ── 诚实空结构（不编造）──────────────────────────────────────────────────────
def test_empty_window_insufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    _patch_fetch(monkeypatch, answers=[], project=_project())
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient"] is True
    assert body["insufficient_reasons"] == ["no_answers"]
    assert body["fact_rows"] == []
    assert body["coverage"]["n_answers"] == 0
    assert body["domain"] == "insurance" and body["window_days"] == 30


def test_no_extraction_coverage_insufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗内有答案但表内无 ok 行（failed 行/缺行/形状坏）→ insufficient，零合成。"""
    _override_principal()
    answers = [_answer("ans_1"), _answer("ans_2"), _answer("ans_3")]
    table_rows = {
        "ans_1": {**_ok_row([]), "status": "failed", "error": "llm_disabled"},
        "ans_2": {**_ok_row([]), "brands": "not-a-list"},  # ok 但形状坏=未覆盖
    }
    _patch_fetch(monkeypatch, answers=answers, project=_project(), table_rows=table_rows)
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient"] is True
    assert body["insufficient_reasons"] == ["no_extraction_coverage"]
    assert body["fact_rows"] == []
    assert body["coverage"] == {
        "n_answers": 3,
        "n_with_extract": 0,
        "n_groups": 0,
        "n_fact_rows": 0,
    }


def test_target_brand_unset_insufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    """项目未配置品牌 → 四指标失去主体，insufficient=target_brand_unset，不编造。"""
    _override_principal()
    _patch_fetch(
        monkeypatch,
        answers=[_answer("ans_1")],
        project=_project(brand_names=[]),
        table_rows={"ans_1": _ok_row(["中国平安"])},
    )
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient"] is True
    assert body["insufficient_reasons"] == ["target_brand_unset"]
    assert body["fact_rows"] == [] and body["target_brand"] is None


# ── 四指标映射正确性（对拍 metrics.brand_special 输出）────────────────────────
def _happy_path_body(monkeypatch: pytest.MonkeyPatch) -> dict:
    _override_principal()
    answers = [
        # 组A：doubao/北京/q1 —— 目标两条 rank1，竞品两条 rank2
        _answer("ans_1", model="doubao", region="北京", query="q1"),
        _answer("ans_2", model="doubao", region="北京", query="q1"),
        # 组B：deepseek/上海/q2 —— 目标零提及（诚实 0 行）
        _answer("ans_3", model="deepseek", region="上海", query="q2"),
        # 组C：doubao/北京/q2 —— ranks=[1]，n=2 → 双分母可区分（50/100）
        _answer("ans_4", model="doubao", region="北京", query="q2"),
        _answer("ans_5", model="doubao", region="北京", query="q2"),
    ]
    table_rows = {
        "ans_1": _ok_row(["中意人寿保险", "中国平安"]),
        "ans_2": _ok_row(["擎天柱11号", "中国平安"]),
        "ans_3": _ok_row(["中国人寿"]),
        "ans_4": _ok_row(["中意人寿"]),
        "ans_5": _ok_row([]),  # 合法空抽取=未提及，仍入分母
    }
    _patch_fetch(monkeypatch, answers=answers, project=_project(), table_rows=table_rows)
    resp = _get("?window_days=14")
    assert resp.status_code == 200
    return resp.json()


def test_four_metrics_mapping_against_metrics_module(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _happy_path_body(monkeypatch)
    assert body["insufficient"] is False and body["insufficient_reasons"] == []
    assert body["domain"] == "insurance" and body["window_days"] == 14
    assert body["target_brand"] == "中意人寿" and body["competitors"] == ["中国平安"]
    assert body["coverage"] == {
        "n_answers": 5,
        "n_with_extract": 5,
        "n_groups": 3,
        "n_fact_rows": 18,
    }

    # 对拍：每组独立重算 brand_special（端点数值必须逐字段等于 metrics 纯函数输出）
    rules = load_domain("insurance")
    group_records = {
        ("doubao", "北京", "q1"): [
            {"brands": ["中意人寿保险", "中国平安"], "query": "q1"},
            {"brands": ["擎天柱11号", "中国平安"], "query": "q1"},
        ],
        ("deepseek", "上海", "q2"): [{"brands": ["中国人寿"], "query": "q2"}],
        ("doubao", "北京", "q2"): [
            {"brands": ["中意人寿"], "query": "q2"},
            {"brands": [], "query": "q2"},
        ],
    }
    expected = {
        key: metrics.brand_special(
            records, "中意人寿", rules=rules, total_count=len(records), top_ns=(1, 3, 5)
        )
        for key, records in group_records.items()
    }

    for (platform, region, query), special in expected.items():
        dims = {"platform": platform, "region": region, "query": query}
        n = len(group_records[(platform, region, query)])
        # ① 品牌提及率
        row = _row(
            body, platform=platform, region=region, query=query, metric="brand_appearance_rate"
        )
        assert row["value"] == special["appearance_rate"]
        assert row["unit"] == "percent"
        assert row["numerator"] == special["mentions"] and row["denominator"] == n
        # ② 推荐排名分布（avg_rank 汇总 + ranks 全量分布）
        row = _row(body, platform=platform, region=region, query=query, metric="rank_distribution")
        assert row["value"] == special["avg_rank"] and row["unit"] == "rank"
        assert row["extra"]["best_rank"] == special["best_rank"]
        assert row["extra"]["ranks"] == special["ranks"]
        # ③ Top1/Top3/Top5（value=of_total，extra.of_mentions 双分母成对）
        for top_n in (1, 3, 5):
            row = _row(
                body,
                platform=platform,
                region=region,
                query=query,
                metric=f"top{top_n}_appearance_rate",
            )
            rates = special["top_rates"][str(top_n)]
            assert row["value"] == rates["of_total"]
            assert row["extra"]["of_mentions"] == rates["of_mentions"]
            assert row["numerator"] == sum(1 for r in special["ranks"] if r <= top_n)
            assert row["denominator"] == n
        # ④ 竞品对比（归并后竞品名进 extra.competitor）
        row = _row(
            body, platform=platform, region=region, query=query, metric="competitor_appearance_rate"
        )
        comp = metrics.brand_special(
            group_records[(platform, region, query)],
            "中国平安",
            rules=rules,
            total_count=n,
            top_ns=(1, 3, 5),
        )
        assert row["value"] == comp["appearance_rate"]
        assert row["numerator"] == comp["mentions"] and row["denominator"] == n
        assert row["extra"]["competitor"] == "中国平安"
        # 行公共形状：source/method/domain/window 逐行标清
        for row in [r for r in body["fact_rows"] if r["dimensions"] == dims]:
            assert row["source"] == "system_computed"
            assert row["method"] == "brandrank-llm-v1"
            assert row["domain"] == "insurance"
            assert set(row["window"]) == {"start", "end"}


def test_dual_denominator_and_zero_mention_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """双分母可区分样本 + 零提及诚实 0 值（分母真实）的具体数值断言。"""
    body = _happy_path_body(monkeypatch)
    # 组C（ranks=[1]，n=2）：提及率 50%；top1 of_total=50 / of_mentions=100
    row = _row(body, platform="doubao", region="北京", query="q2", metric="brand_appearance_rate")
    assert (row["value"], row["numerator"], row["denominator"]) == (50.0, 1, 2)
    row = _row(body, platform="doubao", region="北京", query="q2", metric="top1_appearance_rate")
    assert row["value"] == 50.0 and row["extra"]["of_mentions"] == 100.0
    row = _row(body, platform="doubao", region="北京", query="q2", metric="rank_distribution")
    assert row["value"] == 1.0 and row["extra"]["ranks"] == [1]
    # 组A（ranks=[1,1]，n=2）：全中 → top1/3/5 全 100/100
    for top_n in (1, 3, 5):
        row = _row(
            body, platform="doubao", region="北京", query="q1", metric=f"top{top_n}_appearance_rate"
        )
        assert row["value"] == 100.0 and row["extra"]["of_mentions"] == 100.0
    # 组B（零提及）：appearance=0.0、avg_rank=None、top 全 0——分母仍真实（1）
    row = _row(body, platform="deepseek", region="上海", query="q2", metric="brand_appearance_rate")
    assert (row["value"], row["numerator"], row["denominator"]) == (0.0, 0, 1)
    row = _row(body, platform="deepseek", region="上海", query="q2", metric="rank_distribution")
    assert row["value"] is None and row["extra"]["ranks"] == []
    row = _row(body, platform="deepseek", region="上海", query="q2", metric="top3_appearance_rate")
    assert row["value"] == 0 and row["extra"]["of_mentions"] == 0
    row = _row(
        body, platform="deepseek", region="上海", query="q2", metric="competitor_appearance_rate"
    )
    assert row["value"] == 0.0 and row["numerator"] == 0


def test_uncovered_answers_excluded_from_denominator(monkeypatch: pytest.MonkeyPatch) -> None:
    """同组内未覆盖答案（无表行/failed 行）不进分母；coverage 如实披露。"""
    _override_principal()
    answers = [_answer("ans_1"), _answer("ans_2"), _answer("ans_3")]
    table_rows = {
        "ans_1": _ok_row(["中意人寿"]),
        "ans_2": {**_ok_row([]), "status": "failed", "error": "api_error: timeout"},
        # ans_3 无表行（fanout 未覆盖）
    }
    _patch_fetch(monkeypatch, answers=answers, project=_project(), table_rows=table_rows)
    resp = _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient"] is False
    assert body["coverage"]["n_answers"] == 3
    assert body["coverage"]["n_with_extract"] == 1
    row = _row(
        body, platform="doubao", region="北京", query="保险公司推荐", metric="brand_appearance_rate"
    )
    assert row["denominator"] == 1 and row["value"] == 100.0


# ── 响应投影安全：不泄露账号/profile/会话维、不带 answer 原文 ────────────────
def test_response_has_no_account_or_profile_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _happy_path_body(monkeypatch)
    payload = __import__("json").dumps(body, ensure_ascii=False)
    for forbidden in (
        "platform_account",
        "browser_profile",
        "session_event",
        "authorization_scope",
        "response_text",
        "文本ans_",
    ):
        assert forbidden not in payload
    for row in body["fact_rows"]:
        assert set(row["dimensions"]) == {"platform", "region", "query"}
