"""report-fact-suggestions 优化前后对比组（报价单服务 4）单元测试。

全 fake：不连 PG——monkeypatch fetch_answers_window（按窗起止分双臂假数据）+
brandrank_service.fetch_brand_extracts（fanout 落账表假命中）。

口径断言：双臂各自 metrics.analyze 同一把规则/同一目标品牌；value=after−before；
topN 双分母（of_total 为行值、of_mentions 成对进 extra）；臂内零答案/零覆盖 →
insufficient 诚实占位（绝不伪零/伪差）；四参不齐/畸形/倒置 → before_after=None。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.brandrank import service as brandrank_service
from geo_platform.reports import fact_suggestions

TENANT = "tnt_ba"
PROJECT = "prj_ba"
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

_PROJECT = {
    "pub_id": PROJECT,
    "name": "盛邦验证",
    "brandrank_domain": "cybersecurity",
    "brand_names": ["盛邦安全"],
    "competitor_names": ["奇安信"],
}

BEFORE = {"before_start": "2026-07-01", "before_end": "2026-07-07",
          "after_start": "2026-08-01", "after_end": "2026-08-07"}


def _answer(pub_id: str) -> dict[str, Any]:
    return {"pub_id": pub_id, "query_text": "网络安全厂商推荐", "response_text": "r",
            "model": "doubao", "region": "北京", "mode": "normal", "capture_time": NOW}


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省：主草稿组/W2/W3 全部垫底为空，只留 before/after 两臂按窗给数。"""
    monkeypatch.setattr(brandrank_service, "fetch_project",
                        lambda dsn, tenant, project: dict(_PROJECT))
    monkeypatch.setattr(brandrank_service, "fetch_answers",
                        lambda dsn, tenant, project, since: ([], False))
    monkeypatch.setattr(fact_suggestions, "fetch_disparagement_judgments",
                        lambda dsn, tenant, project, since, until: ([], False))
    monkeypatch.setattr(fact_suggestions, "fetch_source_audit_overview",
                        lambda dsn, tenant, project, start, end: {
                            "own_site_host": None, "documents_total": 0,
                            "own_site_documents": 0, "own_site_share": None})
    monkeypatch.setattr(fact_suggestions, "fetch_site_audit_suggestions",
                        lambda dsn, tenant, project: {"rows": [], "batch_pub_id": None,
                                                      "truncated": False})

    def fake_window(dsn: str, tenant: str, project: str,
                    start: datetime, end: datetime) -> tuple[list[dict[str, Any]], bool]:
        if start.month == 7:                        # before 臂：两条答案
            return [_answer("ans_b1"), _answer("ans_b2")], False
        return [_answer("ans_a1"), _answer("ans_a2")], False

    def fake_extracts(dsn: str, tenant: str, ids: list[str],
                      domain: str) -> dict[str, dict[str, Any]]:
        table = {
            # before：目标 1/2 提及、rank 2
            "ans_b1": ["奇安信"],
            "ans_b2": ["奇安信", "盛邦安全"],
            # after：目标 2/2 提及、均 rank 1
            "ans_a1": ["盛邦安全", "奇安信"],
            "ans_a2": ["盛邦安全"],
        }
        return {i: {"status": "ok", "brands": table[i]} for i in ids}

    monkeypatch.setattr(fact_suggestions, "fetch_answers_window", fake_window)
    monkeypatch.setattr(brandrank_service, "fetch_brand_extracts", fake_extracts)


def _compute(**params: str) -> dict[str, Any]:
    return fact_suggestions.compute_report_fact_suggestions(
        dsn="postgresql://fake", tenant_pub_id=TENANT, project_pub_id=PROJECT,
        window_days=7, now=NOW, **params)


def test_before_after_none_when_params_absent() -> None:
    assert _compute()["before_after"] is None


def test_before_after_none_when_params_partial() -> None:
    assert _compute(before_start="2026-07-01", before_end="2026-07-07",
                    after_start="2026-08-01")["before_after"] is None


def test_before_after_none_when_params_malformed_or_reversed() -> None:
    assert _compute(before_start="2026/07/01", before_end="2026-07-07",
                    after_start="2026-08-01", after_end="2026-08-07")["before_after"] is None
    assert _compute(before_start="2026-07-07", before_end="2026-07-01",
                    after_start="2026-08-01", after_end="2026-08-07")["before_after"] is None


def test_before_after_happy_path_diffs() -> None:
    section = _compute(**BEFORE)["before_after"]
    assert section["status"] == "ok"
    assert section["window"] == {"start": "2026-07-01", "end": "2026-08-07"}
    assert section["coverage"]["before_answers"] == 2
    assert section["coverage"]["before_with_extract"] == 2
    assert section["coverage"]["after_with_extract"] == 2

    rows = {r["extra"]["metric_name"]: r for r in section["fact_rows"]}
    assert set(rows) == {"mention_rate", "avg_rank", "top1", "top3", "top5"}

    mention = rows["mention_rate"]
    assert mention["metric"] == "before_after_metric"
    assert mention["extra"]["before"] == 50.0 and mention["extra"]["after"] == 100.0
    assert mention["value"] == 50.0                  # diff = after − before
    assert mention["unit"] == "percent"
    assert mention["numerator"] == 2 and mention["denominator"] == 2   # after 臂
    assert mention["extra"]["before_numerator"] == 1
    assert mention["extra"]["denominators"] == {"before_n": 2, "after_n": 2}
    assert mention["method"] == "brandrank-llm-v1"

    rank = rows["avg_rank"]
    assert rank["extra"]["before"] == 2.0 and rank["extra"]["after"] == 1.0
    assert rank["value"] == -1.0 and rank["unit"] == "rank"

    top1 = rows["top1"]
    assert top1["extra"]["before"] == 0.0 and top1["extra"]["after"] == 100.0
    assert top1["value"] == 100.0
    # 双分母成对：before 臂 1 次提及 rank2 → of_mentions top1=0；after 臂全 rank1 → 100
    assert top1["extra"]["before_of_mentions"] == 0.0
    assert top1["extra"]["after_of_mentions"] == 100.0

    top3 = rows["top3"]
    assert top3["extra"]["before"] == 50.0 and top3["extra"]["after"] == 100.0
    assert top3["value"] == 50.0
    assert top3["extra"]["before_of_mentions"] == 100.0   # 1 次提及 rank2 ≤ 3
    assert top3["value"] == top3["extra"]["after"] - top3["extra"]["before"]


def test_before_after_insufficient_when_arm_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_window(dsn: str, tenant: str, project: str,
                    start: datetime, end: datetime) -> tuple[list[dict[str, Any]], bool]:
        if start.month == 7:
            return [], False                          # before 臂零答案
        return [_answer("ans_a1")], False

    monkeypatch.setattr(fact_suggestions, "fetch_answers_window", fake_window)
    section = _compute(**BEFORE)["before_after"]
    assert section["status"] == "insufficient"
    assert "before_no_answers" in section["insufficient_reasons"]
    assert section["fact_rows"] == []                 # 绝不伪零/伪差


def test_before_after_insufficient_when_arm_no_coverage(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # after 臂答案在、但抽取表零覆盖（failed/缺失）→ 诚实占位
    monkeypatch.setattr(brandrank_service, "fetch_brand_extracts",
                        lambda dsn, tenant, ids, domain: {
                            i: {"status": "failed", "brands": []} for i in ids})
    section = _compute(**BEFORE)["before_after"]
    assert section["status"] == "insufficient"
    assert "before_no_extraction_coverage" in section["insufficient_reasons"]
    assert "after_no_extraction_coverage" in section["insufficient_reasons"]
    assert section["fact_rows"] == []
