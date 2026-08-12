"""report-fact-suggestions 扩展组（W3 拉踩核查 / W2 官网能效）单元测试。

全 fake：不连 PG——monkeypatch fact_suggestions 模块的读取接缝（fetch_disparagement_*/
fetch_source_audit_overview/fetch_site_audit_suggestions）与 brandrank_service 三接缝
（主草稿组必须一并垫底，口径照 tests/unit/test_brandrank_api.py）。

覆盖：W3 方向分组比率/典型案例排序与 T1 事实核查挂载/表未就绪降级；W2 官网引用率、
回答级引用率、回答级采纳率（有值/显式 None/缺键）与 T2 建议降级。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from geo_platform.brandrank import service as brandrank_service
from geo_platform.reports import fact_suggestions

TENANT = "tnt_w23"
PROJECT = "prj_w23"
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

_PROJECT = {
    "pub_id": PROJECT,
    "name": "盛邦验证",
    "brandrank_domain": "cybersecurity",
    "brand_names": ["盛邦安全"],
    "competitor_names": ["奇安信"],
}


def _judgment(
    pub_id: str,
    subject: str,
    target: str,
    *,
    disparagement: bool,
    confidence: float | None,
    platform: str = "doubao",
    subject_type: str = "answer",
    subject_pub_id: str = "col_x",
    source_url: str | None = None,
) -> dict[str, Any]:
    return {
        "judgment_pub_id": pub_id,
        "subject_type": subject_type,
        "subject_pub_id": subject_pub_id,
        "platform": platform,
        "subject_brand": subject,
        "target_brand": target,
        "attitude": "negative" if disparagement else "neutral",
        "disparagement": disparagement,
        "evidence_quote": f"引文{pub_id}",
        "confidence": Decimal(str(confidence)) if confidence is not None else None,
        "method": "llm_judge",
        "created_at": NOW,
        "source_url": source_url,
    }


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省接缝：项目存在、窗内零答案零判定零文档（各用例按需再 monkeypatch）。"""
    monkeypatch.setattr(
        brandrank_service, "fetch_project", lambda dsn, tenant, project: dict(_PROJECT)
    )
    monkeypatch.setattr(
        brandrank_service, "fetch_answers", lambda dsn, tenant, project, since: ([], False)
    )
    monkeypatch.setattr(
        brandrank_service, "fetch_brand_extracts", lambda dsn, tenant, ids, domain: {}
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_disparagement_judgments",
        lambda dsn, tenant, project, since, until: ([], False),
    )
    monkeypatch.setattr(
        fact_suggestions, "fetch_disparagement_factchecks", lambda dsn, tenant, project, ids: {}
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_source_audit_overview",
        lambda dsn, tenant, project, start, end: {
            "own_site_host": None,
            "answers_total": 0,
            "answers_with_own_site_citation": 0,
            "own_site_answer_citation_rate": None,
            "documents_total": 0,
            "own_site_documents": 0,
            "own_site_share": None,
        },
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_site_audit_suggestions",
        lambda dsn, tenant, project: {"rows": [], "batch_pub_id": None, "truncated": False},
    )


def _compute() -> dict[str, Any]:
    return fact_suggestions.compute_report_fact_suggestions(
        dsn="postgresql://fake",
        tenant_pub_id=TENANT,
        project_pub_id=PROJECT,
        window_days=7,
        now=NOW,
    )


# ── W3 拉踩核查 ──────────────────────────────────────────────────────────────
def test_w3_rate_by_direction_and_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    judgments = [
        # 竞品 → 己方：抹黑己方（disparagement）
        _judgment(
            "jdg_1",
            "奇安信",
            "盛邦安全",
            disparagement=True,
            confidence=0.9,
            source_url="https://src.example.com/p?q=1",
        ),
        # 第三方 → 己方：非拉踩（进分母不进分子）
        _judgment("jdg_2", "某自媒体", "盛邦安全", disparagement=False, confidence=0.3),
        # 己方 → 竞品：己方拉踩竞品
        _judgment("jdg_3", "盛邦安全", "奇安信", disparagement=True, confidence=0.8),
        # 两侧都非项目品牌：不计方向，但 disparagement=true 仍进案例（方向如实 null）
        _judgment("jdg_4", "甲厂商", "乙厂商", disparagement=True, confidence=0.7),
    ]
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_disparagement_judgments",
        lambda dsn, t, p, since, until: (judgments, False),
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_disparagement_factchecks",
        lambda dsn, t, p, ids: {
            "jdg_1": {
                "verdict": "supported",
                "summary": "官网公告可证实",
                "source_url": "https://www.example.com/a?utm=1",
            }
        },
    )

    w3 = _compute()["w3_disparagement"]
    assert w3["status"] == "ok"
    assert w3["n_judgments"] == 4 and w3["n_disparagement"] == 3
    assert w3["n_undirected"] == 1 and w3["fact_check_available"] is True

    rates = {
        r["extra"]["direction"]: r for r in w3["fact_rows"] if r["metric"] == "disparagement_rate"
    }
    own = rates["smear_on_own"]
    assert own["value"] == 50.0 and own["numerator"] == 1 and own["denominator"] == 2
    comp = rates["own_smear_on_competitor"]
    assert comp["value"] == 100.0 and comp["numerator"] == 1 and comp["denominator"] == 1
    assert own["method"] == "w3-disparagement-v1" and own["source"] == "system_computed"

    cases = [r for r in w3["fact_rows"] if r["metric"] == "disparagement_case"]
    assert [r["extra"]["judgment_pub_id"] for r in cases] == ["jdg_1", "jdg_3", "jdg_4"]
    first = cases[0]
    assert first["value"] is None and first["unit"] == "case"
    assert first["numerator"] == 1 and first["denominator"] == 3
    assert first["extra"]["evidence_quote"] == "引文jdg_1"
    assert first["extra"]["subject_brand"] == "奇安信"
    assert first["extra"]["target_brand"] == "盛邦安全"
    assert first["extra"]["direction"] == "smear_on_own"
    assert first["extra"]["confidence"] == pytest.approx(0.9)
    assert first["extra"]["answer_ref"] == "col_x"
    # URL 输出清洗：丢 query
    assert first["extra"]["source_url"] == "https://src.example.com/p"
    # T1 事实核查挂载（报价单「逐条事实核查与证据链」）
    assert first["extra"]["fact_check"] == {
        "verdict": "supported",
        "summary": "官网公告可证实",
        "source_url": "https://www.example.com/a",
    }
    assert cases[1]["extra"]["fact_check"] is None  # T1 无该行 → null（不编造）
    assert cases[2]["extra"]["direction"] is None  # 无方向如实 null


def test_w3_degrades_when_factcheck_table_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    judgments = [_judgment("jdg_1", "奇安信", "盛邦安全", disparagement=True, confidence=0.9)]
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_disparagement_judgments",
        lambda dsn, t, p, since, until: (judgments, False),
    )
    # 契约表 T1 未就绪
    monkeypatch.setattr(
        fact_suggestions, "fetch_disparagement_factchecks", lambda dsn, t, p, ids: None
    )
    w3 = _compute()["w3_disparagement"]
    assert w3["status"] == "ok" and w3["fact_check_available"] is False
    case = next(r for r in w3["fact_rows"] if r["metric"] == "disparagement_case")
    assert case["extra"]["fact_check"] is None  # 优雅降级，不炸不编造


def test_w3_insufficient_when_no_judgments() -> None:
    w3 = _compute()["w3_disparagement"]
    assert w3["status"] == "insufficient"
    assert w3["insufficient_reasons"] == ["no_judgments"]
    assert w3["fact_rows"] == [] and w3["n_judgments"] == 0


# ── W2 官网能效 ──────────────────────────────────────────────────────────────
def _overview(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "own_site_host": "www.webray.com.cn",
        "answers_total": 100,
        "answers_with_citation": 60,
        "citation_coverage_rate": 0.6,
        "answers_with_own_site_citation": 10,
        "own_site_answer_citation_rate": 0.1,
        "own_site_share_of_cited_answers": 1 / 6,
        "citation_references_total": 200,
        "own_site_citation_references": 12,
        "own_site_reference_share": 0.06,
        "own_site_cited_text_answers": 5,
        "own_site_cited_text_evidence_rate": 0.5,
        "documents_total": 40,
        "own_site_documents": 1,
        "own_site_share": 0.025,
    }
    base.update(overrides)
    return base


def test_w2_share_and_adoption_with_contract_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_source_audit_overview",
        lambda dsn, t, p, start, end: _overview(
            own_site_transcript_total=10,
            own_site_transcript_accurate=8,
            own_site_transcript_accuracy_rate=0.8,
            own_site_adoption_evaluated_answers=10,
            own_site_adoption_verified_answers=8,
            own_site_adoption_rate=0.8,
        ),
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_site_audit_suggestions",
        lambda dsn, t, p: {
            "batch_pub_id": "sab_1",
            "rows": [
                {
                    "pub_id": "sas_1",
                    "category": "citability",
                    "severity": "high",
                    "title": "缺少结构化数据",
                    "detail": "产品页未提供 JSON-LD",
                    "evidence_document_pub_id": "doc_1",
                    "model": "m1",
                },
                {
                    "pub_id": "sas_2",
                    "category": "content_coverage",
                    "severity": "low",
                    "title": "FAQ 覆盖不足",
                    "detail": "竞品对比场景无官网内容",
                    "evidence_document_pub_id": None,
                    "model": "m1",
                },
            ],
            "truncated": False,
        },
    )
    w2 = _compute()["w2_site_audit"]
    assert w2["status"] == "ok"
    assert w2["own_site_host"] == "www.webray.com.cn"
    assert w2["suggestions_available"] is True and w2["suggestion_batch_pub_id"] == "sab_1"

    share = next(r for r in w2["fact_rows"] if r["metric"] == "own_site_citation_share")
    assert share["value"] == 10.0
    assert share["numerator"] == 10 and share["denominator"] == 100
    assert share["method"] == "w2-site-audit-v2"

    adoption = next(r for r in w2["fact_rows"] if r["metric"] == "own_site_adoption_rate")
    assert adoption["value"] == 80.0
    assert adoption["numerator"] == 8 and adoption["denominator"] == 10
    assert "insufficient" not in (adoption["extra"] or {})

    suggestions = [r for r in w2["fact_rows"] if r["metric"] == "site_audit_suggestion"]
    assert len(suggestions) == 2
    assert suggestions[0]["value"] is None and suggestions[0]["unit"] == "suggestion"
    assert suggestions[0]["denominator"] == 2
    assert suggestions[0]["extra"]["severity"] == "high"
    assert suggestions[0]["extra"]["category"] == "citability"
    assert suggestions[0]["extra"]["title"] == "缺少结构化数据"
    assert suggestions[0]["extra"]["batch_pub_id"] == "sab_1"


def test_w2_adoption_degrades_when_contract_keys_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Worker B 未加键
    monkeypatch.setattr(
        fact_suggestions, "fetch_source_audit_overview", lambda dsn, t, p, start, end: _overview()
    )
    w2 = _compute()["w2_site_audit"]
    adoption = next(r for r in w2["fact_rows"] if r["metric"] == "own_site_adoption_rate")
    assert adoption["value"] is None
    assert adoption["extra"]["insufficient"] is True
    assert adoption["extra"]["note"] == "adoption_metrics_unavailable"


def test_w2_adoption_none_when_no_transcript_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    # 键在、显式 None
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_source_audit_overview",
        lambda dsn, t, p, start, end: _overview(
            own_site_transcript_total=0,
            own_site_transcript_accurate=0,
            own_site_adoption_evaluated_answers=0,
            own_site_adoption_verified_answers=0,
            own_site_adoption_rate=None,
        ),
    )
    w2 = _compute()["w2_site_audit"]
    adoption = next(r for r in w2["fact_rows"] if r["metric"] == "own_site_adoption_rate")
    assert adoption["value"] is None
    assert adoption["extra"]["note"] == "no_answer_level_adoption_evaluations"


def test_w2_no_documents_and_t2_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # 契约表 T2 未就绪
    monkeypatch.setattr(fact_suggestions, "fetch_site_audit_suggestions", lambda dsn, t, p: None)
    w2 = _compute()["w2_site_audit"]
    assert w2["suggestions_available"] is False
    assert w2["suggestion_batch_pub_id"] is None
    assert "no_eligible_answers" in w2["insufficient_reasons"]
    metrics_present = {r["metric"] for r in w2["fact_rows"]}
    assert "own_site_citation_share" not in metrics_present  # 零分母不出行
    assert "site_audit_suggestion" not in metrics_present  # 表未就绪不编造
