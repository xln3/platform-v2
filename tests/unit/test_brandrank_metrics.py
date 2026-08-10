"""domain.brandrank.metrics：五个公式与她的实现数值对拍 + 双分母口径 + 专项/信源分析。

移植自旧库 server/tests/test_brandrank_metrics.py（import 路径换 V2，断言零变化）。
"""

import random

import pytest
from brandrank_her_ref import load_her_impl

from domain.brandrank import metrics
from domain.brandrank.rules import load_domain


@pytest.fixture(scope="module")
def her():
    return load_her_impl()


@pytest.fixture(scope="module")
def rules():
    return load_domain("insurance")


def _cases(seed=20260719, n=60):
    """确定性样例数据（含空列表/重复/单元素），覆盖她的公式的各分支。"""
    rng = random.Random(seed)
    pool = ["中意人寿", "中国平安", "中国人寿", "君龙人寿", "中国太保", "友邦人寿", "泰康人寿"]
    return [
        [[rng.choice(pool) for _ in range(rng.randint(0, 6))] for _ in range(rng.randint(0, 20))]
        for _ in range(n)
    ]


# ── 与她的函数逐案对拍（源码不在场时 skip）─────────────────────────────
def test_calculate_brand_ranking_matches_her_impl(her):
    for case in _cases():
        assert metrics.calculate_brand_ranking(case) == her["calculate_brand_ranking"](case)


def test_collect_and_merge_rank_maps_match_her_impl(her):
    for case in _cases(n=20):
        assert metrics.collect_brand_ranks(case) == her["collect_brand_ranks"](case)
    rng = random.Random(7)
    pool = ["A", "B", "C"]
    maps = [her["collect_brand_ranks"]([[rng.choice(pool)] for _ in range(3)]) for _ in range(3)]
    assert metrics.merge_rank_maps(*maps) == her["merge_rank_maps"](*maps)


def test_rates_match_her_impl(her):
    rng = random.Random(11)
    for _ in range(200):
        ranks = [rng.randint(1, 12) for _ in range(rng.randint(0, 8))]
        total = rng.randint(0, 20)
        n = rng.choice([1, 3, 5, 10])
        assert metrics.calculate_appearance_rate(ranks, total) == her["calculate_appearance_rate"](
            ranks, total
        )
        assert metrics.calculate_top_rate(ranks, n) == her["calculate_top_rate"](ranks, n)
        assert metrics.calculate_top_rate(ranks, n, total) == her["calculate_top_rate"](
            ranks, n, total
        )


# ── 双分母口径（她的 compare 版两处不一致的修正：分母=真实条数）─────────
def test_top_rate_double_denominator():
    ranks = [1, 2, 7]  # top3 命中 2 次
    assert metrics.calculate_top_rate(ranks, 3) == pytest.approx(2 / 3 * 100)  # 占出现条数
    assert metrics.calculate_top_rate(ranks, 3, 10) == pytest.approx(2 / 10 * 100)  # 占总条数
    assert metrics.calculate_top_rate([], 3) == 0
    assert metrics.calculate_top_rate(ranks, 3, 0) == 0


def test_ranking_table_rates_and_real_denominator():
    """appearance_rate/top_rates 分母=传入的真实条数（绝不硬编码 12/140）。"""
    lists = [["中意人寿", "中国平安"], ["中国平安", "中意人寿"], ["中意人寿"]]
    table = metrics.ranking_table(lists, total_count=4, top_ns=(1, 3))
    by_brand = {r["brand"]: r for r in table}
    # 中意人寿: ranks=[1,2,1] → occ=3, avg=4/3≈1.33, score=3/1.333=2.25
    zy = by_brand["中意人寿"]
    assert zy["occurrences"] == 3 and zy["avg_rank"] == 1.33 and zy["score"] == 2.25
    assert zy["appearance_rate"] == round(3 / 4 * 100, 2)  # 分母=4（真实条数）
    assert zy["top_rates"]["1"]["of_mentions"] == round(2 / 3 * 100, 2)
    assert zy["top_rates"]["1"]["of_total"] == round(2 / 4 * 100, 2)
    assert zy["top_rates"]["3"]["of_mentions"] == 100.0
    # score 降序：中意人寿(2.25) > 中国平安(3/1.67≈1.8→1.8)
    assert table[0]["brand"] == "中意人寿" and table[0]["rank"] == 1
    # 换分母结果随之变化（证明分母真的参数化）
    table2 = metrics.ranking_table(lists, total_count=140, top_ns=(1,))
    assert {r["brand"]: r["appearance_rate"] for r in table2} != {
        r["brand"]: r["appearance_rate"] for r in table
    }


# ── 目标品牌/竞品专项（同口径；normalize 后匹配）───────────────────────
def _records():
    return [
        {
            "brands": ["中意人寿保险", "中国平安"],
            "query": "保险公司推荐",
            "thinking_mode": "快速",
            "ip": "北京",
            "rec_type": "公司",
        },
        {
            "brands": ["中国平安", "超级玛丽15号"],
            "query": "保险产品推荐",
            "thinking_mode": "思考",
            "ip": "上海",
            "rec_type": "产品",
        },
        {
            "brands": ["擎天柱11号", "中国平安"],
            "query": "保险公司推荐",
            "thinking_mode": "快速",
            "ip": "北京",
            "rec_type": "公司",
        },
        {
            "brands": [],
            "query": "保险公司推荐",
            "thinking_mode": "快速",
            "ip": "北京",
            "rec_type": "公司",
        },  # 空 brands：入分母不入排名
    ]


def test_target_brand_special(rules):
    recs = _records()
    res = metrics.analyze(
        recs, [], rules=rules, target_brand="中意人寿", competitors=["中国平安"], top_ns=(1, 3)
    )
    t = res["target_brand"]
    # 合并后口径：'中意人寿保险'→中意人寿(rank1)、'擎天柱11号'→中意人寿(rank1) → mentions=2
    assert t["brand"] == "中意人寿" and t["mentions"] == 2
    assert t["ranks"] == [1, 1] and t["avg_rank"] == 1.0 and t["best_rank"] == 1
    assert t["appearance_rate"] == round(2 / 4 * 100, 2)  # 分母=4（含空 brands 条）
    assert t["top_rates"]["1"]["of_mentions"] == 100.0
    assert t["overall_rank"] == 1  # 合并榜第一
    # 每 query 排名明细（她的 query_analysis 同形）
    qa = {(x["query"], x["rank"]) for x in t["query_analysis"]}
    assert qa == {("保险公司推荐", 1)}
    bq = {x["query"]: x for x in t["by_query"]}
    assert bq["保险公司推荐"]["mentions"] == 2 and bq["保险公司推荐"]["ranks"] == [1, 1]
    # 竞品同口径：中国平安 3 次（ranks 2,1,2）
    comp = res["competitors"][0]
    assert comp["brand"] == "中国平安" and comp["mentions"] == 3
    assert comp["ranks"] == [2, 1, 2] and comp["avg_rank"] == 1.67
    assert comp["appearance_rate"] == round(3 / 4 * 100, 2)


def test_target_brand_alias_input_normalized(rules):
    """入参是别名也命中（'擎天柱11号'→中意人寿）；未上榜 overall_rank=None（她的 999→None）。"""
    t = metrics.analyze(_records(), [], rules=rules, target_brand="擎天柱11号")["target_brand"]
    assert t["brand"] == "中意人寿" and t["brand_input"] == "擎天柱11号"
    t2 = metrics.analyze(_records(), [], rules=rules, target_brand="不存在的品牌")["target_brand"]
    assert t2["mentions"] == 0 and t2["avg_rank"] is None and t2["overall_rank"] is None


def test_analyze_structure_and_denominators(rules):
    res = metrics.analyze(_records(), [], rules=rules, top_ns=(3,))
    assert res["denominators"]["n_answers"] == 4
    assert res["denominators"]["n_with_brands"] == 3  # 空 brands 条不计（她 if brands:）
    assert set(res["by_mode"]) == {"快速", "思考"}
    assert res["by_mode"]["快速"]["denominators"]["n_answers"] == 3  # 分模式真实条数
    assert res["by_ip"]["上海"]["denominators"]["n_answers"] == 1
    assert set(res["by_type"]) == {"公司", "产品"}
    assert res["overall"]["merged"] and res["overall"]["raw"]
    # 合并榜：君龙人寿（超级玛丽15号合并而来）应在 merged 而非 raw
    merged_brands = {r["brand"] for r in res["overall"]["merged"]}
    raw_brands = {r["brand"] for r in res["overall"]["raw"]}
    assert "君龙人寿" in merged_brands and "君龙人寿" not in raw_brands
    assert "超级玛丽15号" in raw_brands


# ── 信源分析（analyze_source.py L43-96 口径）──────────────────────────
def test_source_metrics_hand_computed():
    recs = [
        {"sitename": "知乎", "url": "u1", "index": 1},
        {"sitename": "知乎", "url": "u2", "index": 2},
        {"sitename": "百家号", "url": "u3", "index": 1},
        {"sitename": "百家号", "url": "", "index": 4},  # 空 url 不计 unique_urls
    ]
    s = metrics.source_metrics(recs)
    assert s["total"] == 4 and s["unique_urls"] == 3
    assert s["sitename_counts"] == {"知乎": 2, "百家号": 2}
    # 权重：知乎=1/1+1/2=1.5 > 百家号=1/1+1/4=1.25 → 知乎排前
    assert s["sources"][0]["sitename"] == "知乎" and s["sources"][0]["weight"] == 1.5
    assert s["sources"][1]["weight"] == 1.25
    assert s["sources"][0]["percent"] == 50.0
    assert s["top3_concentration"] == 100.0  # top2 即全部
    assert metrics.source_metrics([])["total"] == 0  # 空范围零形（她的 None→零 dict）


def test_analyze_sources_grouped_by_dims(rules):
    srcs = [
        {"sitename": "知乎", "url": "u1", "index": 1, "thinking_mode": "快速", "ip": "北京"},
        {"sitename": "知乎", "url": "u2", "index": 1, "thinking_mode": "思考", "ip": "上海"},
    ]
    res = metrics.analyze(_records(), srcs, rules=rules)
    assert res["sources"]["overall"]["total"] == 2
    assert res["sources"]["by_mode"]["快速"]["total"] == 1
    assert res["sources"]["by_ip"]["上海"]["unique_urls"] == 1


# ── by_engine：V2 附加维（纯增量分组，既有分组零变化）────────────────────
def test_by_engine_additive_grouping(rules):
    """engine 维分组（adapter 附加的 provenance 字段）；无 engine 的记录落 '' 桶
    （与 by_ip 的 '' 桶同口径）；overall/by_mode/by_ip/by_type 数值与键集合零变化。"""
    without_engine = metrics.analyze(_records(), [], rules=rules)
    assert set(without_engine["by_engine"]) == {""}  # 她的记录无 engine
    assert without_engine["by_engine"][""]["denominators"]["n_answers"] == 4

    records = [
        dict(r, engine=engine)
        for r, engine in zip(_records(), ["doubao", "deepseek", "doubao", "doubao"], strict=True)
    ]
    res = metrics.analyze(records, [], rules=rules)
    assert set(res["by_engine"]) == {"doubao", "deepseek"}
    assert res["by_engine"]["doubao"]["denominators"]["n_answers"] == 3
    assert res["by_engine"]["deepseek"]["denominators"]["n_answers"] == 1
    # 零漂移：既有分组与 overall 与无 engine 时逐键一致
    for key in ("overall", "by_mode", "by_ip", "by_type"):
        assert res[key] == without_engine[key]
