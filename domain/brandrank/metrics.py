"""品牌排名指标核：公式逐行对齐同事版实现，分母全部真实参数化（对她的两处不一致的修正）。

逐行移植自已退役旧系统 server/geosys/brandrank/metrics.py（纯函数零依赖，语义零变化）：
- collect_brand_ranks      analyze_brand.py L853-863（{brand: [rank,...]}，1-based）
- merge_rank_maps          analyze_brand.py L866-872（多 rank map 按列表拼接）
- calculate_brand_ranking  analyze_brand.py L932-961（avg_rank=mean、occurrences=len、
                           score=occurrences/avg_rank、按 score 降序、round(3)/round(2)）
- calculate_appearance_rate compare_zhongyi_analysis.py L645-649（mentions/total_count×100）
- calculate_top_rate        compare_zhongyi_analysis.py L652-670（**双分母**：
                           给 total_count→占总条数，否则→占出现条数）
- source_metrics           analyze_source.py L43-96（sitename Counter、权重 Σ1/index、
                           Top3 集中度=top3 count/total×100、去重 URL 数）

★ 修正（与她口径的偏差，全部注释留痕）：
1. 分母参数化：她的 appearance/top_rate 分母硬编码 12（compare L695-699「2IP×2模式×3次」）
   与报告总数 140——换一批数据即错。本模块分母一律取**本范围真实 eligible answer 条数**
   （n_answers，由调用方按本窗实测传入），并在结果里逐项披露。
2. 空范围她返回 None（analyze_source L45-46）/除零风险：本模块返回零形 dict（诚实零值）。
3. 目标/竞品专项作用于**合并后**品牌列表（她的 compare 版作用于 raw jsonl——raw 里是
   "超级玛丽15号" 这类产品名，目标匹配会漏计；合并口径与本模块主排名表一致）。
   目标/竞品名本身也先过 normalize_brand（"中意人寿保险"→"中意人寿"）。
4. 她的"未上榜"哨兵 ranking=999（compare L252 等）→ 本模块用 None（JSON 诚实空值）。
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .rules import DomainRules, normalize_brand, normalize_brand_list

DEFAULT_TOP_NS = (3, 5, 10)


# ══ 逐行移植：她的五个公式（注释行号=analyze_brand.py / compare_zhongyi_analysis.py）══
def collect_brand_ranks(brand_lists: list[list[str]]) -> dict[str, list[int]]:
    """统计每个品牌在各条列表中出现时的排名位置（1-based）。（analyze_brand L853-863）"""
    ranks = defaultdict(list)
    for brand_list in brand_lists:
        for position, brand in enumerate(brand_list, start=1):
            ranks[brand].append(position)
    return ranks


def merge_rank_maps(*rank_maps: dict[str, list[int]]) -> dict[str, list[int]]:
    """合并多个 rank map（例如快速+思考），按列表拼接。（analyze_brand L866-872）"""
    merged = defaultdict(list)
    for rm in rank_maps:
        for brand, positions in rm.items():
            merged[brand].extend(positions)
    return merged


def calculate_brand_ranking(all_brand_lists: list[list[str]]) -> list[dict[str, Any]]:
    """计算品牌综合排名。（analyze_brand L932-961，逐行一致）"""
    brand_rankings = defaultdict(list)

    for brand_list in all_brand_lists:
        for rank, brand in enumerate(brand_list, start=1):
            brand_rankings[brand].append(rank)

    brand_scores = {}
    for brand, rankings in brand_rankings.items():
        avg_rank = statistics.mean(rankings)
        occurrences = len(rankings)
        score = occurrences / avg_rank if avg_rank > 0 else 0
        brand_scores[brand] = {
            'score': round(score, 3),
            'avg_rank': round(avg_rank, 2),
            'occurrences': occurrences
        }

    sorted_brands = sorted(brand_scores.items(), key=lambda x: x[1]['score'], reverse=True)

    result = []
    for rank, (brand, data) in enumerate(sorted_brands, start=1):
        result.append({
            'rank': rank,
            'brand': brand,
            **data
        })

    return result


def calculate_appearance_rate(ranks: Sequence[int], total_count: int) -> float:
    """计算出现率 - 分母为所有条数。（compare L645-649；total_count 由调用方给真实值）"""
    if total_count == 0:
        return 0
    return len(ranks) / total_count * 100


def calculate_top_rate(ranks: Sequence[int], top_n: int, total_count: int | None = None) -> float:
    """计算Top N出现率——**双分母**（compare L652-670，逐行一致）：

    给 total_count → 分母为总条数；否则 → 分母为出现条数。两个口径都合法、
    在结果里成对披露（of_total / of_mentions），绝不只报一个。"""
    if len(ranks) == 0:
        return 0

    count = sum(1 for r in ranks if r <= top_n)

    if total_count is not None:
        # 分母为所有条数
        return count / total_count * 100 if total_count > 0 else 0
    else:
        # 分母为出现条数
        return count / len(ranks) * 100


# ══ 组合层：排名表 + 双分母比率 + 专项 + 信源 ══
def _top_rates(ranks: Sequence[int], top_ns: Iterable[int],
               total_count: int) -> dict[str, dict[str, float]]:
    """每个 N 同时给两个分母口径（of_mentions=占出现条数 / of_total=占总条数）。"""
    return {str(n): {"of_mentions": round(calculate_top_rate(ranks, n), 2),
                     "of_total": round(calculate_top_rate(ranks, n, total_count), 2)}
            for n in top_ns}


def ranking_table(brand_lists: list[list[str]], *, total_count: int,
                  top_ns: Iterable[int] = DEFAULT_TOP_NS) -> list[dict[str, Any]]:
    """calculate_brand_ranking 的扩展表：每行追加 appearance_rate（分母=真实总条数）与 top_rates。

    排名/score/avg_rank/occurrences 由逐行移植的 calculate_brand_ranking 产出，数值与她一致；
    追加列是**展示层增量**，不改变她的排序口径。"""
    rank_map = collect_brand_ranks(brand_lists)
    rows = calculate_brand_ranking(brand_lists)
    for row in rows:
        ranks = rank_map[row["brand"]]
        row["appearance_rate"] = round(calculate_appearance_rate(ranks, total_count), 2)
        row["top_rates"] = _top_rates(ranks, top_ns, total_count)
    return rows


def _scope_stats(brand_lists_raw: list[list[str]], brand_lists_merged: list[list[str]],
                 *, total_count: int, top_ns: Iterable[int]) -> dict[str, Any]:
    """一个范围（overall/某 mode/某 ip/某 type）的双口径排名表 + 分母披露。"""
    return {
        "raw": ranking_table(brand_lists_raw, total_count=total_count, top_ns=top_ns),
        "merged": ranking_table(brand_lists_merged, total_count=total_count, top_ns=top_ns),
        "denominators": {
            "n_answers": total_count,                  # 本范围真实 eligible answer 条数（修正点①）
            "n_with_brands": len(brand_lists_raw),     # 非空品牌列表条数（她的 if brands: 过滤）
        },
    }


def brand_special(records: list[dict[str, Any]], brand: str, *, rules: DomainRules,
                  total_count: int, top_ns: Iterable[int] = DEFAULT_TOP_NS,
                  overall_merged_table: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """目标品牌/竞品专项（同口径；修正点③：作用于合并后列表，品牌名先 normalize）。

    records: 她的 brand_list 记录（raw brands 在记录内，本函数自行 normalize）。
    返回 mentions / appearance_rate / avg/best rank / top_rates / 每 query 排名明细。
    """
    std = normalize_brand(brand, rules)
    mentions = 0
    ranks: list[int] = []
    query_analysis: list[dict[str, Any]] = []           # compare L177-183 同形（每出现一行）
    by_query: dict[str, dict[str, Any]] = {}

    for rec in records:
        merged = normalize_brand_list(rec.get("brands") or [], rules)
        if std not in merged:
            continue
        rank = merged.index(std) + 1                    # 排名从1开始（compare L174）
        mentions += 1
        ranks.append(rank)
        query_analysis.append({
            "query": rec.get("query", ""), "ip": rec.get("ip", ""),
            "mode": rec.get("thinking_mode", ""), "rec_type": rec.get("rec_type"),
            "rank": rank,
        })
        q = rec.get("query", "")
        bucket = by_query.setdefault(q, {"query": q, "mentions": 0, "ranks": []})
        bucket["mentions"] += 1
        bucket["ranks"].append(rank)

    per_query = []
    for q in sorted(by_query):
        b = by_query[q]
        per_query.append({
            "query": q, "mentions": b["mentions"], "ranks": b["ranks"],
            "avg_rank": round(statistics.mean(b["ranks"]), 2),
            "best_rank": min(b["ranks"]),
        })

    overall_rank = None                                 # 修正点④：她的 999 哨兵 → None
    if overall_merged_table:
        overall_rank = next((r["rank"] for r in overall_merged_table if r["brand"] == std), None)

    return {
        "brand": std, "brand_input": brand,             # 入参原名留痕（normalize 前后）
        "mentions": mentions,
        "appearance_rate": round(calculate_appearance_rate(ranks, total_count), 2),
        "avg_rank": round(statistics.mean(ranks), 2) if ranks else None,
        "best_rank": min(ranks) if ranks else None,
        "ranks": ranks,
        "top_rates": _top_rates(ranks, top_ns, total_count),
        "overall_rank": overall_rank,                   # 在 overall 合并表的排名；未上榜=None
        "query_analysis": query_analysis,
        "by_query": per_query,
        "denominators": {"n_answers": total_count},
    }


def source_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """信源分析（analyze_source L43-96 口径；空范围 → 零形 dict，修正点②）。

    records: [{sitename, url, index}]（adapter 产出，index 1-based）。
    权重=Σ(1/index)，Top3 集中度=top3 count/total×100（analyze_source L93-96）。"""
    total = len(records)
    if total == 0:
        return {"total": 0, "unique_urls": 0, "sitename_counts": {},
                "top3_concentration": 0.0, "sources": []}

    sitename_counts = Counter([r["sitename"] for r in records])
    weights_all: dict[str, float] = defaultdict(float)
    for r in records:
        weights_all[r["sitename"]] += 1.0 / r["index"]

    sorted_sitenames = sorted(weights_all.items(), key=lambda x: x[1], reverse=True)
    top3_count = sum(sitename_counts[s[0]] for s in sorted_sitenames[:3])

    return {
        "total": total,
        "unique_urls": len(set(r["url"] for r in records if r["url"])),
        "sitename_counts": dict(sitename_counts),
        "top3_concentration": round((top3_count / total) * 100, 2),
        "sources": [
            {"rank": i, "sitename": sitename, "count": sitename_counts[sitename],
             "percent": round((sitename_counts[sitename] / total) * 100, 2),
             "weight": round(weight, 4)}
            for i, (sitename, weight) in enumerate(sorted_sitenames, start=1)
        ],
    }


def analyze(records: list[dict[str, Any]], source_records: list[dict[str, Any]], *,
            rules: DomainRules, target_brand: str | None = None,
            competitors: Iterable[str] = (),
            top_ns: Iterable[int] = DEFAULT_TOP_NS) -> dict[str, Any]:
    """一次运行的完整指标快照：overall/by_mode/by_ip/by_type 双口径排名表
    + 目标品牌/竞品专项 + 信源分析 + 全量分母披露。

    records: 她的 brand_list 记录（adapter 产出；**只含抽取成功的 answer**——failed 条
    不进 brands 分析，但其 references 仍进信源分析，由调用方分别组装）。
    source_records: [{sitename, url, index, thinking_mode, ip}]——信源条目自带分组维。
    她的 L1083 `if brands:` 过滤在此复刻：raw brands 为空的记录不进任何品牌列表，
    但仍计入 n_answers 分母（分母=真实条数，修正点①）。
    """
    top_ns = tuple(top_ns)

    def lists_of(scope_records: list[dict[str, Any]]) -> tuple[list[list[str]], list[list[str]]]:
        raw = [r["brands"] for r in scope_records if r.get("brands")]
        merged = [normalize_brand_list(b, rules) for b in raw]
        merged = [m for m in merged if m]               # 空列表对 ranks 无贡献（与她数值一致）
        return raw, merged

    def grouped(key_fn: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            buckets[key_fn(r)].append(r)
        out = {}
        for name in sorted(buckets):
            raw, merged = lists_of(buckets[name])
            out[name] = _scope_stats(raw, merged, total_count=len(buckets[name]), top_ns=top_ns)
        return out

    raw_all, merged_all = lists_of(records)
    overall = _scope_stats(raw_all, merged_all, total_count=len(records), top_ns=top_ns)
    overall_merged_table = overall["merged"]

    result: dict[str, Any] = {
        "domain": rules.domain,
        "category": rules.category,
        "top_ns": list(top_ns),
        "overall": overall,
        "by_mode": grouped(lambda r: r.get("thinking_mode") or ""),
        "by_ip": grouped(lambda r: r.get("ip") or ""),
        "by_type": grouped(lambda r: r.get("rec_type") or "其他"),   # None→'其他' 桶（她的词）
        "target_brand": None,
        "competitors": [],
        "sources": {
            "overall": source_metrics(source_records),
            "by_mode": _source_by(source_records, "thinking_mode"),
            "by_ip": _source_by(source_records, "ip"),
        },
        "denominators": {
            "n_answers": len(records),
            "n_with_brands": len(raw_all),
            "basis": "分母=本范围真实 eligible answer 条数（本窗实测）；"
                     "修正同事版硬编码 12/query（compare L695-699）与总数 140 的两处不一致",
        },
    }

    if target_brand:
        result["target_brand"] = brand_special(
            records, target_brand, rules=rules, total_count=len(records),
            top_ns=top_ns, overall_merged_table=overall_merged_table)
    for comp in competitors:
        result["competitors"].append(brand_special(
            records, comp, rules=rules, total_count=len(records),
            top_ns=top_ns, overall_merged_table=overall_merged_table))
    return result


def _source_by(source_records: list[dict[str, Any]], dim: str) -> dict[str, dict[str, Any]]:
    """信源按维分组。source_records 每条自带维字段（thinking_mode/ip，由调用方在
    adapter 产出的条目上附加）——与品牌记录无对齐耦合，
    因此抽取失败的 answer 的信源也能正确进信源分析（references 不依赖 LLM）。"""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in source_records:
        buckets[entry.get(dim) or ""].append(entry)
    return {name: source_metrics(buckets[name]) for name in sorted(buckets)}
