"""提及指标层（mention-metrics-v1）：客户报告口径的版本化纯函数实现。

口径真源=clients/client-zjgy/recompute_zjgy_report_data.py（咱家果源会话即兴
脚本），逐条移植、不做「改进」：
- 真源查询组（波次×组→场景→items[text,priority]）的 query text 逐字匹配答案；
- 按（波次×平台×问题）cell 分组，按 capture_time 取最新 N 条（N=该平台采样数），
  多余的 canary/retry 样本丢弃并计数 dropped_extra_answers；
- 名称命中=精确子串（``name in resp``），occ=``str.count`` 原文总次数；
- 提及明细上下文窗口 = 命中点前 150 / 后 200 字符。

本模块零 IO：输入=answer 行 + citation 行 + spec（frozen dataclass），输出=指标
dict（顶层带 metric_version 与 spec_hash）。DB 抓取是 api 层薄适配
（api/geo_platform/analytics/mention_metrics.py），便于单测与金标对拍。
v1 输出 schema 保留源脚本的历史键名（w1_*/w2_*、w2_q001_q002 等），保证与
既有报告数据逐键可比。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, TypedDict

MENTION_METRICS_VERSION = "mention-metrics-v1"

# 口径常数（源脚本逐字移植，改任何一个都必须升 MENTION_METRICS_VERSION）
_MENTION_CONTEXT_BEFORE = 150
_MENTION_CONTEXT_AFTER = 200
_EXCERPT_CHARS = 400
_TOP_HOSTS_LIMIT = 35
_TOP_HOSTS_BY_PLATFORM_LIMIT = 10
# W2 词频的固定补充键：答「无法核实/资料不足/未找到」的答案数（源脚本硬编码口径）
_INSUFFICIENT_TERM_KEY = "无法核实/资料不足"
_INSUFFICIENT_MARKERS = ("无法核实", "资料不足", "未找到")

_WAVE_W1 = "w1"
_WAVE_W2 = "w2"


class AnswerRow(TypedDict):
    """纯函数输入的答案行（api 适配层由 analytics.answer 组装）。

    ``cap`` 是 capture_time 的字符串投影（源脚本 ``str(capture_time)`` 同款），
    只用于 cell 内排序；同值时保持稳定序（输入序），与 SQL ORDER BY capture_time
    的既有抓取顺序零漂移。
    """

    pub: str
    run: str | None
    model: str
    q: str
    resp: str
    cap: str


class CitationRow(TypedDict):
    """纯函数输入的信源行（api 适配层由 analytics.citation_fact 组装）。"""

    answer_pub_id: str
    host: str | None
    canonical_url: str | None
    title: str | None
    cited_text: str | None


@dataclass(frozen=True)
class QueryItem:
    text: str
    priority: int


@dataclass(frozen=True)
class QueryGroup:
    """真源查询组；name 形如「ZJ-Q003｜品类发现｜NFC果汁候选集」：

    组 id=「｜」首段、场景=末段（源脚本 ``parts[0]``/``parts[-1]`` 口径）。
    """

    name: str
    items: tuple[QueryItem, ...]


@dataclass(frozen=True)
class WaveSpec:
    wave: str  # "w1" | "w2"
    query_groups: tuple[QueryGroup, ...]


@dataclass(frozen=True)
class MentionFlag:
    """提及明细里的附加布尔标记（如源脚本 has_huiyuan=答案含「汇源」）。

    ``key`` 是输出键名（历史键名兼容），``name`` 是检测子串。
    """

    name: str
    key: str


@dataclass(frozen=True)
class MentionMetricsSpec:
    platforms: tuple[str, ...]
    samples_per_query: dict[str, int]
    names: tuple[str, ...]
    brands: tuple[str, ...]
    terms: tuple[str, ...]
    primary_name: str
    waves: tuple[WaveSpec, ...]
    mention_flags: tuple[MentionFlag, ...] = ()
    excerpt_groups: tuple[str, ...] = ()


def wave_spec_from_truth(wave: str, payload: dict[str, Any]) -> WaveSpec:
    """真源查询组 JSON（{"query_groups":[{"name",items[{"text","priority"]}]}]}）→ WaveSpec。"""
    groups = []
    for group in payload["query_groups"]:
        items = tuple(
            QueryItem(text=str(item["text"]), priority=int(item["priority"]))
            for item in group["items"]
        )
        groups.append(QueryGroup(name=str(group["name"]), items=items))
    return WaveSpec(wave=wave, query_groups=tuple(groups))


def spec_payload(spec: MentionMetricsSpec) -> dict[str, Any]:
    """spec 的 canonical JSON 投影（spec_hash 的输入；手工构造，不依赖 asdict 的容器转换语义）。"""
    return {
        "platforms": list(spec.platforms),
        "samples_per_query": dict(spec.samples_per_query),
        "names": list(spec.names),
        "brands": list(spec.brands),
        "terms": list(spec.terms),
        "primary_name": spec.primary_name,
        "waves": [
            {
                "wave": wave.wave,
                "query_groups": [
                    {
                        "name": group.name,
                        "items": [
                            {"text": item.text, "priority": item.priority} for item in group.items
                        ],
                    }
                    for group in wave.query_groups
                ],
            }
            for wave in spec.waves
        ],
        "mention_flags": [{"name": flag.name, "key": flag.key} for flag in spec.mention_flags],
        "excerpt_groups": list(spec.excerpt_groups),
    }


def spec_hash(spec: MentionMetricsSpec) -> str:
    """spec 口径指纹：sha256(canonical json)。同口径同 hash，供输出审计。"""
    canonical = json.dumps(
        spec_payload(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(canonical.encode()).hexdigest()


def compute_mention_metrics(
    *,
    spec: MentionMetricsSpec,
    answers: list[AnswerRow],
    citations: list[CitationRow],
) -> dict[str, Any]:
    """按 spec 口径计算提及指标（纯函数）。

    ``citations`` 只取 answer_pub_id 属于保留 W1 答案的行（api 层可按项目全量
    传，函数内过滤等价于源脚本的 ``WHERE answer_pub_id = ANY(w1_pubs)``）。
    零覆盖（无匹配答案）时返回全零/空结构，不抛错。
    """
    # 真源映射：query text → (组 id, 场景, priority)，W1/W2 各自独立
    w1_map: dict[str, tuple[str, str, int]] = {}
    w2_map: dict[str, tuple[str, str, int]] = {}
    for wave_spec in spec.waves:
        target = w1_map if wave_spec.wave == _WAVE_W1 else w2_map
        for group in wave_spec.query_groups:
            parts = group.name.split("｜")
            for item in group.items:
                target[item.text] = (parts[0], parts[-1], item.priority)
    w2_groups = sorted({value[0] for value in w2_map.values()})

    # cell 分组（波次×平台×问题）；同一 text 同现两波次时按源脚本判 w1 优先
    by_cell: dict[tuple[str, str, str], list[AnswerRow]] = {}
    for row in sorted(answers, key=lambda item: item["cap"]):
        if row["q"] in w1_map:
            wave = _WAVE_W1
        elif row["q"] in w2_map:
            wave = _WAVE_W2
        else:
            continue
        by_cell.setdefault((wave, row["model"], row["q"]), []).append(row)

    # R1 口径：每 cell 取最新 samples_per_query[model] 条，多余 canary/retry 丢弃
    kept: list[AnswerRow] = []
    wave_of: dict[str, str] = {}  # 保留答案的波次归属（cell 键带出，维持 AnswerRow 形状）
    dropped = 0
    for (wave, model, _q), recs in by_cell.items():
        samples = spec.samples_per_query.get(model)
        if samples is None:
            raise ValueError(f"samples_per_query 缺平台 {model!r} 的采样数")
        keep = recs[-samples:]
        dropped += len(recs) - len(keep)
        kept.extend(keep)
        for row in keep:
            wave_of[row["pub"]] = wave
    w1_ans = [row for row in kept if wave_of[row["pub"]] == _WAVE_W1]
    w2_ans = [row for row in kept if wave_of[row["pub"]] == _WAVE_W2]

    out: dict[str, Any] = {
        "metric_version": MENTION_METRICS_VERSION,
        "spec_hash": spec_hash(spec),
        "samples_per_query": dict(spec.samples_per_query),
        "dropped_extra_answers": dropped,
        "w1_total": len(w1_ans),
        "w2_total": len(w2_ans),
    }

    # 每平台题数覆盖（distinct query）与答案数
    coverage: dict[str, dict[str, int]] = {}
    for platform in spec.platforms:
        sub1 = [a for a in w1_ans if a["model"] == platform]
        sub2 = [a for a in w2_ans if a["model"] == platform]
        coverage[platform] = {
            "w1_answers": len(sub1),
            "w1_queries": len({a["q"] for a in sub1}),
            "w2_answers": len(sub2),
            "w2_queries": len({a["q"] for a in sub2}),
        }
    out["platform_coverage"] = coverage

    # W1 平台×名称（命中=答案含精确名称；occ=原文总次数）
    platform_names: dict[str, dict[str, Any]] = {}
    for platform in spec.platforms:
        sub = [a for a in w1_ans if a["model"] == platform]
        entry: dict[str, Any] = {"n": len(sub), "uq": len({a["q"] for a in sub})}
        for name in spec.names:
            hits = [a for a in sub if name in a["resp"]]
            entry[name] = {
                "answers": len(hits),
                "occ": sum(a["resp"].count(name) for a in hits),
            }
        platform_names[platform] = entry
    out["w1_platform"] = platform_names
    out["w1_name_totals"] = {
        name: sum(platform_names[p][name]["answers"] for p in spec.platforms) for name in spec.names
    }

    # W1 场景×名称
    group_hits: dict[tuple[str, str], dict[str, int]] = {}
    for answer in w1_ans:
        gid, scene, _priority = w1_map[answer["q"]]
        cell = group_hits.setdefault((gid, scene), {})
        cell["_n"] = cell.get("_n", 0) + 1
        for name in spec.names:
            if name in answer["resp"]:
                cell[name] = cell.get(name, 0) + 1
    out["w1_group"] = {
        f"{key[0]}|{key[1]}": dict(value) for key, value in sorted(group_hits.items())
    }

    # 品牌榜
    out["w1_brands"] = {
        brand: sum(1 for a in w1_ans if brand in a["resp"]) for brand in spec.brands
    }
    out["w1_brands_by_platform"] = {
        brand: {
            platform: sum(1 for a in w1_ans if a["model"] == platform and brand in a["resp"])
            for platform in spec.platforms
        }
        for brand in spec.brands
    }

    # 提及明细（含上下文，供表达形态/情绪人工判读）
    mentions: list[dict[str, Any]] = []
    for answer in w1_ans:
        if spec.primary_name not in answer["resp"]:
            continue
        gid, scene, priority = w1_map[answer["q"]]
        index = answer["resp"].find(spec.primary_name)
        mention: dict[str, Any] = {
            "model": answer["model"],
            "group": gid,
            "scene": scene,
            "variant": priority,
            "pub": answer["pub"],
            "context": answer["resp"][
                max(0, index - _MENTION_CONTEXT_BEFORE) : index + _MENTION_CONTEXT_AFTER
            ],
        }
        for flag in spec.mention_flags:
            mention[flag.key] = flag.name in answer["resp"]
        mentions.append(mention)
    out["w1_mentions"] = mentions

    # 信源统计（W1 保留答案的 citation 行）
    w1_pubs = {a["pub"] for a in w1_ans}
    pub2model = {a["pub"]: a["model"] for a in w1_ans}
    resp_by_pub = {a["pub"]: a["resp"] for a in w1_ans}
    crows = [c for c in citations if c["answer_pub_id"] in w1_pubs]
    per_platform: dict[str, dict[str, Any]] = {}
    for citation in crows:
        model = pub2model[citation["answer_pub_id"]]
        entry = per_platform.setdefault(model, {"records": 0, "hosts": set(), "urls": set()})
        entry["records"] += 1
        if citation["host"]:
            entry["hosts"].add(citation["host"])
        if citation["canonical_url"]:
            entry["urls"].add(citation["canonical_url"])
    out["w1_citations_by_platform"] = {
        platform: {
            "records": value["records"],
            "domains": len(value["hosts"]),
            "urls": len(value["urls"]),
        }
        for platform, value in per_platform.items()
    }
    out["w1_citations_total"] = {
        "records": len(crows),
        "domains": len({c["host"] for c in crows if c["host"]}),
        "urls": len({c["canonical_url"] for c in crows if c["canonical_url"]}),
    }
    out["w1_top_hosts"] = [
        [host, count]
        for host, count in Counter(c["host"] for c in crows if c["host"]).most_common(
            _TOP_HOSTS_LIMIT
        )
    ]
    out["w1_top_hosts_by_platform"] = {
        platform: [
            [host, count]
            for host, count in Counter(
                c["host"] for c in crows if c["host"] and pub2model[c["answer_pub_id"]] == platform
            ).most_common(_TOP_HOSTS_BY_PLATFORM_LIMIT)
        ]
        for platform in spec.platforms
    }

    # 品牌相关页面（W1；title/cited_text 含品牌名）
    pages: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for citation in crows:
        title_hit = spec.primary_name in (citation["title"] or "")
        text_hit = spec.primary_name in (citation["cited_text"] or "")
        if not (title_hit or text_hit):
            continue
        key = (citation["host"], citation["canonical_url"])
        entry = pages.setdefault(
            key, {"models": set(), "answers": set(), "mentioned": 0, "title": None}
        )
        answer_pub_id = citation["answer_pub_id"]
        entry["answers"].add(answer_pub_id)
        entry["models"].add(pub2model[answer_pub_id])
        entry["title"] = citation["title"]
        if spec.primary_name in resp_by_pub.get(answer_pub_id, ""):
            entry["mentioned"] += 1
    out["w1_brand_pages"] = [
        {
            "host": key[0],
            "url": key[1],
            "title": value["title"],
            "n_answers": len(value["answers"]),
            "n_mentioned": value["mentioned"],
            "models": sorted(value["models"]),
        }
        for key, value in sorted(pages.items(), key=lambda kv: -len(kv[1]["answers"]))
    ]
    out["w1_brand_pages_distinct_answers"] = len(
        {answer for value in pages.values() for answer in value["answers"]}
    )
    out["w1_brand_pages_mentioned_answers"] = len(
        {
            answer
            for value in pages.values()
            for answer in value["answers"]
            if spec.primary_name in resp_by_pub.get(answer, "")
        }
    )

    # W2 覆盖（组×平台答案数）
    w2_coverage: dict[str, dict[str, int]] = {}
    for answer in w2_ans:
        gid = w2_map[answer["q"]][0]
        cell = w2_coverage.setdefault(gid, {})
        cell[answer["model"]] = cell.get(answer["model"], 0) + 1
    out["w2_coverage"] = {group: dict(w2_coverage.get(group, {})) for group in w2_groups}

    # W2 词频（含固定「无法核实/资料不足」补充键）
    terms = {term: sum(1 for a in w2_ans if term in a["resp"]) for term in spec.terms}
    terms[_INSUFFICIENT_TERM_KEY] = sum(
        1 for a in w2_ans if any(marker in a["resp"] for marker in _INSUFFICIENT_MARKERS)
    )
    out["w2_terms"] = terms

    # W2 指定组各平台摘要（供人工判读）
    excerpts = []
    for answer in w2_ans:
        gid = w2_map[answer["q"]][0]
        if gid in spec.excerpt_groups:
            excerpts.append(
                {
                    "model": answer["model"],
                    "group": gid,
                    "excerpt": answer["resp"][:_EXCERPT_CHARS],
                }
            )
    out["w2_q001_q002"] = sorted(excerpts, key=lambda item: (item["group"], item["model"]))

    return out
