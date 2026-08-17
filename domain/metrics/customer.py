"""Customer-facing GEO metric projections.

This module is deliberately separated from collection control-plane models.  Its
inputs contain only eligible answer, citation, source-audit, and brand-risk facts;
task totals, task failures, browser instances, platform accounts, retries, and
other operational details cannot enter the customer projection by construction.

The projection is deterministic and suitable for rebuilding from persisted raw
facts.  All ratios use the eligible facts supplied by the caller.  Missing data
stays missing and dates without observations are never synthesized as zeroes.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from statistics import median, pstdev
from typing import Any, Literal

MetricFormat = Literal["percentage", "score", "rank", "count", "decimal"]
MetricDirection = Literal["higher", "lower", "neutral"]


@dataclass(frozen=True, slots=True)
class CustomerCitationFact:
    canonical_url: str
    host: str
    own_source: bool
    cited_text: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class CustomerAnswerFact:
    answer_pub_id: str
    capture_time: datetime
    model: str
    region: str
    mode: str
    query_pub_id: str | None
    query_text: str | None
    query_group: str | None
    response_text: str
    mentioned: bool
    rank: int | None
    sentiment: str | None
    recommended: bool | None
    competitor_mentions: tuple[str, ...]
    competitor_ranks: Mapping[str, int]
    citations: tuple[CustomerCitationFact, ...]


@dataclass(frozen=True, slots=True)
class CustomerSourceAuditFact:
    host: str
    dimension: str
    verdict: str | None
    audit_status: str
    own_source: bool


@dataclass(frozen=True, slots=True)
class CustomerRiskFact:
    model: str
    subject_brand: str
    target_brand: str
    attitude: str
    disparagement: bool
    confidence: Decimal | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MetricSpec:
    code: str
    label: str
    group: str
    format: MetricFormat
    direction: MetricDirection
    description: str
    version: str = "customer-metrics-v1"


@dataclass(frozen=True, slots=True)
class MetricValue:
    code: str
    value: Decimal | None
    state: Literal["ready", "not_ready"]

    def json_value(self) -> float | int | None:
        if self.value is None:
            return None
        if self.value == self.value.to_integral_value():
            return int(self.value)
        return float(self.value)


_METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "geo_visibility_index",
        "GEO 可见度指数",
        "composite",
        "score",
        "higher",
        "品牌在 AI 回答中的综合可见程度。",
    ),
    MetricSpec(
        "competitive_power_index",
        "竞争力指数",
        "composite",
        "score",
        "higher",
        "品牌相对于已配置竞品的综合竞争优势。",
    ),
    MetricSpec(
        "source_authority_index",
        "信源权威指数",
        "composite",
        "score",
        "higher",
        "引用覆盖、官网引用和信源结构的综合表现。",
    ),
    MetricSpec(
        "content_readiness_index",
        "内容准备度指数",
        "composite",
        "score",
        "higher",
        "官网内容被发现、引用和准确转述的准备程度。",
    ),
    MetricSpec(
        "reputation_index",
        "AI 口碑指数",
        "composite",
        "score",
        "higher",
        "AI 回答对品牌的综合态度，并扣除明确风险。",
    ),
    MetricSpec(
        "cognition_consistency_index",
        "AI 认知一致性指数",
        "composite",
        "score",
        "higher",
        "不同模型对品牌认知的稳定程度。",
    ),
    MetricSpec(
        "answer_count",
        "已分析回答",
        "visibility",
        "count",
        "neutral",
        "进入客户事实快照的有效回答数量。",
    ),
    MetricSpec(
        "mention_count",
        "品牌提及回答",
        "visibility",
        "count",
        "higher",
        "明确提到目标品牌的回答数量。",
    ),
    MetricSpec(
        "query_count",
        "覆盖问题数",
        "visibility",
        "count",
        "higher",
        "观察窗口内覆盖的独立问题数量。",
    ),
    MetricSpec(
        "model_count",
        "覆盖模型数",
        "visibility",
        "count",
        "higher",
        "观察窗口内产生有效回答的 AI 模型数量。",
    ),
    MetricSpec(
        "region_count",
        "覆盖地区数",
        "visibility",
        "count",
        "neutral",
        "观察窗口内产生有效回答的地区数量。",
    ),
    MetricSpec(
        "mode_count",
        "覆盖回答模式",
        "visibility",
        "count",
        "neutral",
        "观察窗口内产生有效回答的模式数量。",
    ),
    MetricSpec(
        "observation_day_count",
        "有效观察日",
        "visibility",
        "count",
        "higher",
        "至少包含一条有效回答的自然日数量，不补齐缺失日期。",
    ),
    MetricSpec(
        "mention_rate",
        "品牌提及率",
        "visibility",
        "percentage",
        "higher",
        "有效回答中提到目标品牌的比例。",
    ),
    MetricSpec(
        "no_mention_rate",
        "品牌未提及率",
        "visibility",
        "percentage",
        "lower",
        "有效回答中没有提到目标品牌的比例。",
    ),
    MetricSpec(
        "recommendation_rate",
        "品牌推荐率",
        "visibility",
        "percentage",
        "higher",
        "推荐型问题或明确推荐语境中，目标品牌进入候选结果且未被明确否定的比例。",
    ),
    MetricSpec(
        "recommendation_classification_rate",
        "推荐分类覆盖率",
        "visibility",
        "percentage",
        "higher",
        "有效回答中可依据问题意图、明确推荐语境或既有分类事实判定推荐结果的比例。",
    ),
    MetricSpec(
        "average_rank", "平均排名", "ranking", "rank", "lower", "存在明确品牌排名时的算术平均名次。"
    ),
    MetricSpec(
        "median_rank", "排名中位数", "ranking", "rank", "lower", "存在明确品牌排名时的中位名次。"
    ),
    MetricSpec("best_rank", "最佳排名", "ranking", "rank", "lower", "观察窗口中的最佳明确名次。"),
    MetricSpec("worst_rank", "最差排名", "ranking", "rank", "lower", "观察窗口中的最差明确名次。"),
    MetricSpec(
        "rank_score", "排名得分", "ranking", "score", "higher", "将 Top10 内排名线性归一到 0–100。"
    ),
    MetricSpec(
        "ranked_answer_rate",
        "明确排名覆盖率",
        "ranking",
        "percentage",
        "higher",
        "有效回答中能够识别目标品牌明确名次的比例。",
    ),
    MetricSpec(
        "rank_stddev",
        "排名波动",
        "ranking",
        "decimal",
        "lower",
        "目标品牌明确排名的总体标准差，越低表示排名越稳定。",
    ),
    MetricSpec(
        "top1_rate", "Top1 率", "ranking", "percentage", "higher", "有效回答中品牌排名第一的比例。"
    ),
    MetricSpec(
        "top3_rate", "Top3 率", "ranking", "percentage", "higher", "有效回答中品牌进入前三的比例。"
    ),
    MetricSpec(
        "top5_rate", "Top5 率", "ranking", "percentage", "higher", "有效回答中品牌进入前五的比例。"
    ),
    MetricSpec(
        "top10_rate",
        "Top10 率",
        "ranking",
        "percentage",
        "higher",
        "有效回答中品牌进入前十的比例。",
    ),
    MetricSpec(
        "share_of_voice",
        "竞争声量份额",
        "competition",
        "percentage",
        "higher",
        "目标品牌提及次数占目标品牌与竞品总提及次数的比例。",
    ),
    MetricSpec(
        "exclusive_mention_rate",
        "品牌独占提及率",
        "competition",
        "percentage",
        "higher",
        "仅提到目标品牌、未提到配置竞品的回答比例。",
    ),
    MetricSpec(
        "co_mention_rate",
        "品牌竞品共现率",
        "competition",
        "percentage",
        "neutral",
        "目标品牌与至少一个竞品共同出现的回答比例。",
    ),
    MetricSpec(
        "first_mention_rate",
        "品牌优先出现率",
        "competition",
        "percentage",
        "higher",
        "品牌与竞品共同出现时，目标品牌最先出现的比例。",
    ),
    MetricSpec(
        "head_to_head_win_rate",
        "同题对决胜率",
        "competition",
        "percentage",
        "higher",
        "目标品牌和竞品均有明确名次时，目标品牌排名更高的比例。",
    ),
    MetricSpec(
        "head_to_head_tie_rate",
        "同题对决平局率",
        "competition",
        "percentage",
        "neutral",
        "目标品牌和竞品均有明确名次时，名次相同的比例。",
    ),
    MetricSpec(
        "head_to_head_loss_rate",
        "同题对决失利率",
        "competition",
        "percentage",
        "lower",
        "目标品牌和竞品均有明确名次时，目标品牌排名更低的比例。",
    ),
    MetricSpec(
        "configured_competitor_count",
        "配置竞品数",
        "competition",
        "count",
        "neutral",
        "当前项目进入对标计算的独立竞品数量。",
    ),
    MetricSpec(
        "mention_frequency",
        "平均提及次数",
        "visibility",
        "decimal",
        "higher",
        "每个有效回答中目标品牌平均出现次数。",
    ),
    MetricSpec(
        "citation_coverage",
        "引用覆盖率",
        "source",
        "percentage",
        "higher",
        "带有至少一个可解析引用的回答比例。",
    ),
    MetricSpec(
        "uncited_answer_rate",
        "无引用回答率",
        "source",
        "percentage",
        "lower",
        "没有任何可解析引用的有效回答比例。",
    ),
    MetricSpec(
        "mentioned_answer_citation_rate",
        "提及回答引用率",
        "source",
        "percentage",
        "higher",
        "提到目标品牌的回答中同时带有引用的比例。",
    ),
    MetricSpec(
        "average_citations",
        "平均引用数",
        "source",
        "decimal",
        "higher",
        "每个有效回答平均包含的引用数量。",
    ),
    MetricSpec(
        "citation_references", "引用总次数", "source", "count", "higher", "观察窗口中的引用总次数。"
    ),
    MetricSpec(
        "unique_source_hosts", "独立信源网站", "source", "count", "higher", "被引用的独立域名数量。"
    ),
    MetricSpec(
        "source_diversity_index",
        "信源多样性指数",
        "source",
        "score",
        "higher",
        "基于信源份额集中度计算的 0–100 多样性得分。",
    ),
    MetricSpec(
        "source_concentration_hhi",
        "信源集中度",
        "source",
        "score",
        "lower",
        "各信源份额平方和，越高表示依赖少数网站。",
    ),
    MetricSpec(
        "top_source_share",
        "头部信源份额",
        "source",
        "percentage",
        "lower",
        "引用次数最多的网站占全部引用次数的比例。",
    ),
    MetricSpec(
        "own_source_answer_rate",
        "官网引用回答率",
        "source",
        "percentage",
        "higher",
        "含有至少一个官网引用的回答比例。",
    ),
    MetricSpec(
        "own_source_reference_share",
        "官网引用份额",
        "source",
        "percentage",
        "higher",
        "官网引用次数占全部引用次数的比例。",
    ),
    MetricSpec(
        "own_source_share_of_cited_answers",
        "官网覆盖已引用回答",
        "source",
        "percentage",
        "higher",
        "已有引用的回答中至少引用一次官网的比例。",
    ),
    MetricSpec(
        "third_party_source_answer_rate",
        "第三方信源回答率",
        "source",
        "percentage",
        "neutral",
        "含有至少一个第三方网站引用的有效回答比例。",
    ),
    MetricSpec(
        "cited_text_visibility_rate",
        "引用原文可见率",
        "source",
        "percentage",
        "higher",
        "引用记录中包含可核对原文的比例。",
    ),
    MetricSpec(
        "citation_title_visibility_rate",
        "引用标题可见率",
        "source",
        "percentage",
        "higher",
        "引用记录中包含可识别页面标题的比例。",
    ),
    MetricSpec(
        "sentiment_classification_rate",
        "情感分类覆盖率",
        "reputation",
        "percentage",
        "higher",
        "有效回答中已得到正面、中性或负面分类的比例。",
    ),
    MetricSpec(
        "unknown_sentiment_rate",
        "未分类情感占比",
        "reputation",
        "percentage",
        "lower",
        "有效回答中尚无可用情感分类的比例。",
    ),
    MetricSpec(
        "positive_rate",
        "正面回答率",
        "reputation",
        "percentage",
        "higher",
        "有效回答中对目标品牌持正面态度的比例。",
    ),
    MetricSpec(
        "neutral_rate",
        "中性回答率",
        "reputation",
        "percentage",
        "neutral",
        "有效回答中对目标品牌持中性态度的比例。",
    ),
    MetricSpec(
        "negative_rate",
        "负面回答率",
        "reputation",
        "percentage",
        "lower",
        "有效回答中对目标品牌持负面态度的比例。",
    ),
    MetricSpec(
        "net_sentiment",
        "净情感指数",
        "reputation",
        "score",
        "higher",
        "正面率减负面率后映射到 0–100。",
    ),
    MetricSpec(
        "disparagement_rate",
        "品牌贬损率",
        "risk",
        "percentage",
        "lower",
        "已完成风险判断的事实中构成明确贬损的比例。",
    ),
    MetricSpec(
        "risk_judgment_count",
        "风险判断数",
        "risk",
        "count",
        "neutral",
        "进入快照的有效品牌风险判断数量。",
    ),
    MetricSpec(
        "disparagement_count",
        "明确贬损数",
        "risk",
        "count",
        "lower",
        "被判定构成明确品牌贬损的事实数量。",
    ),
    MetricSpec(
        "support_count", "明确支持数", "risk", "count", "higher", "被判定为支持目标品牌的事实数量。"
    ),
    MetricSpec(
        "support_rate",
        "品牌支持率",
        "risk",
        "percentage",
        "higher",
        "已完成风险判断的事实中支持目标品牌的比例。",
    ),
    MetricSpec(
        "source_accuracy_rate",
        "信源准确率",
        "content",
        "percentage",
        "higher",
        "已完成信源事实审计的记录中判定准确的比例。",
    ),
    MetricSpec(
        "source_audit_count",
        "已完成信源审计",
        "content",
        "count",
        "higher",
        "已产生有效判定的信源审计记录数量。",
    ),
    MetricSpec(
        "source_unsupported_rate",
        "无依据信源率",
        "content",
        "percentage",
        "lower",
        "信源审计中被判定为无依据的比例。",
    ),
    MetricSpec(
        "source_unverifiable_rate",
        "无法核实率",
        "content",
        "percentage",
        "lower",
        "信源审计中无法核实的比例。",
    ),
)

METRIC_CATALOG: Mapping[str, MetricSpec] = {item.code: item for item in _METRIC_SPECS}


def metric_catalog() -> tuple[MetricSpec, ...]:
    """Stable ordered catalog for API/UI metadata generation."""

    return _METRIC_SPECS


def build_customer_metric_bundle(
    *,
    project_pub_id: str,
    brand_name: str,
    competitor_names: Sequence[str],
    answers: Iterable[CustomerAnswerFact],
    source_audits: Iterable[CustomerSourceAuditFact] = (),
    risks: Iterable[CustomerRiskFact] = (),
    generated_at: datetime | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    filters: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the complete deterministic customer projection.

    The returned document intentionally has no collection-control-plane fields.
    It is safe to serialize directly through the customer dashboard API after
    tenant/project authorization.
    """

    materialized = tuple(sorted(answers, key=lambda item: (item.capture_time, item.answer_pub_id)))
    audits = tuple(source_audits)
    risk_facts = tuple(risks)
    now = generated_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    overall = _aggregate_answers(materialized, brand_name=brand_name)
    coverage_metrics = _aggregate_snapshot_coverage(
        materialized,
        competitor_names=competitor_names,
    )
    source_metrics, source_rows = _aggregate_sources(materialized)
    audit_metrics = _aggregate_source_audits(audits)
    risk_metrics = _aggregate_risks(risk_facts)
    base_metrics = overall | coverage_metrics | source_metrics | audit_metrics | risk_metrics

    models = _dimension_rows(
        materialized,
        key=lambda item: item.model or "未知模型",
        brand_name=brand_name,
    )
    regions = _dimension_rows(
        materialized,
        key=lambda item: item.region or "未知地区",
        brand_name=brand_name,
    )
    modes = _dimension_rows(
        materialized,
        key=lambda item: item.mode or "未知模式",
        brand_name=brand_name,
    )
    questions = _question_rows(materialized, brand_name=brand_name)
    competitors = _competitor_rows(
        materialized,
        brand_name=brand_name,
        competitor_names=competitor_names,
    )
    trends = _trend_rows(materialized, brand_name=brand_name)
    composites = _composite_metrics(
        base_metrics,
        models=models,
        competitors=competitors,
    )
    metrics = _serialize_metrics(composites | base_metrics)
    as_of = max((item.capture_time for item in materialized), default=None)
    observed_start = min(
        (item.capture_time.astimezone(UTC).date() for item in materialized), default=None
    )
    observed_end = max(
        (item.capture_time.astimezone(UTC).date() for item in materialized), default=None
    )
    effective_start = window_start or observed_start
    effective_end = window_end or observed_end

    document: dict[str, Any] = {
        "schema_version": "customer-dashboard-v1",
        "metric_version": "customer-metrics-v1",
        "project_pub_id": project_pub_id,
        "brand_name": brand_name,
        "state": "ready" if materialized else "building",
        "generated_at": _iso(now),
        "as_of": _iso(as_of) if as_of else None,
        "window": {
            "start": effective_start.isoformat() if effective_start else None,
            "end": effective_end.isoformat() if effective_end else None,
            "filters": dict(sorted((filters or {}).items())),
        },
        "metrics": metrics,
        "models": models,
        "competitors": competitors,
        "questions": questions,
        "sources": source_rows,
        "regions": regions,
        "modes": modes,
        "trends": trends,
        "risk": {
            "metrics": _serialize_metrics(risk_metrics),
            "by_model": _risk_dimension_rows(risk_facts),
        },
        "source_audit": {
            "metrics": _serialize_metrics(audit_metrics),
            "verdicts": dict(sorted(Counter(item.verdict or "unknown" for item in audits).items())),
        },
    }
    snapshot_material = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    document["snapshot_hash"] = sha256(snapshot_material.encode()).hexdigest()
    return document


def _aggregate_answers(
    facts: Sequence[CustomerAnswerFact], *, brand_name: str
) -> dict[str, Decimal | None]:
    total = len(facts)
    mentions = sum(item.mentioned for item in facts)
    ranks = [item.rank for item in facts if item.rank is not None]
    classified_recommendations = [
        item.recommended for item in facts if item.recommended is not None
    ]
    sentiments = [(item.sentiment or "").strip().lower() for item in facts]
    classified_sentiments = [
        sentiment for sentiment in sentiments if sentiment in {"positive", "neutral", "negative"}
    ]
    positive = sum(sentiment == "positive" for sentiment in classified_sentiments)
    negative = sum(sentiment == "negative" for sentiment in classified_sentiments)
    neutral = sum(sentiment == "neutral" for sentiment in classified_sentiments)
    exclusive = sum(item.mentioned and not item.competitor_mentions for item in facts)
    co_mentions = sum(item.mentioned and bool(item.competitor_mentions) for item in facts)
    first_mentions = 0
    first_mention_denominator = 0
    h2h_wins = 0
    h2h_ties = 0
    h2h_losses = 0
    h2h_total = 0
    target_occurrences = 0
    competitor_mention_events = 0
    normalized_brand = brand_name.casefold()
    for item in facts:
        folded = item.response_text.casefold()
        target_occurrences += folded.count(normalized_brand) if normalized_brand else 0
        competitor_mention_events += len(set(item.competitor_mentions))
        if item.mentioned and item.competitor_mentions:
            target_position = folded.find(normalized_brand)
            competitor_positions = [
                position
                for name in item.competitor_mentions
                if (position := folded.find(name.casefold())) >= 0
            ]
            if target_position >= 0 and competitor_positions:
                first_mention_denominator += 1
                first_mentions += target_position < min(competitor_positions)
        if item.rank is not None:
            for competitor_rank in item.competitor_ranks.values():
                h2h_total += 1
                h2h_wins += item.rank < competitor_rank
                h2h_ties += item.rank == competitor_rank
                h2h_losses += item.rank > competitor_rank

    return {
        "answer_count": Decimal(total),
        "mention_count": Decimal(mentions),
        "mention_rate": _ratio(mentions, total),
        "no_mention_rate": _ratio(total - mentions, total),
        "recommendation_rate": _ratio(
            sum(value is True for value in classified_recommendations),
            len(classified_recommendations),
        ),
        "recommendation_classification_rate": _ratio(len(classified_recommendations), total),
        "average_rank": _mean(ranks),
        "median_rank": _decimal(median(ranks)) if ranks else None,
        "best_rank": Decimal(min(ranks)) if ranks else None,
        "worst_rank": Decimal(max(ranks)) if ranks else None,
        "rank_score": _mean([max(0, 11 - rank) * 10 for rank in ranks]),
        "ranked_answer_rate": _ratio(len(ranks), total),
        "rank_stddev": _decimal(pstdev(ranks)) if ranks else None,
        "top1_rate": _ratio(sum(rank <= 1 for rank in ranks), total),
        "top3_rate": _ratio(sum(rank <= 3 for rank in ranks), total),
        "top5_rate": _ratio(sum(rank <= 5 for rank in ranks), total),
        "top10_rate": _ratio(sum(rank <= 10 for rank in ranks), total),
        "share_of_voice": _ratio(mentions, mentions + competitor_mention_events),
        "exclusive_mention_rate": _ratio(exclusive, total),
        "co_mention_rate": _ratio(co_mentions, total),
        "first_mention_rate": _ratio(first_mentions, first_mention_denominator),
        "head_to_head_win_rate": _ratio(h2h_wins, h2h_total),
        "head_to_head_tie_rate": _ratio(h2h_ties, h2h_total),
        "head_to_head_loss_rate": _ratio(h2h_losses, h2h_total),
        "mention_frequency": _ratio(target_occurrences, total),
        "sentiment_classification_rate": _ratio(len(classified_sentiments), total),
        "unknown_sentiment_rate": _ratio(total - len(classified_sentiments), total),
        "positive_rate": _ratio(positive, total),
        "neutral_rate": _ratio(neutral, total),
        "negative_rate": _ratio(negative, total),
        "net_sentiment": (
            _score((Decimal(positive - negative) / Decimal(total) + Decimal(1)) * Decimal(50))
            if total
            else None
        ),
    }


def _aggregate_snapshot_coverage(
    facts: Sequence[CustomerAnswerFact], *, competitor_names: Sequence[str]
) -> dict[str, Decimal | None]:
    query_keys = {
        ("pub_id", item.query_pub_id)
        if item.query_pub_id
        else ("text", (item.query_text or "").strip().casefold())
        for item in facts
        if item.query_pub_id or (item.query_text and item.query_text.strip())
    }
    configured_competitors = {
        name.strip().casefold() for name in competitor_names if name and name.strip()
    }
    return {
        "query_count": Decimal(len(query_keys)),
        "model_count": Decimal(len({item.model for item in facts if item.model})),
        "region_count": Decimal(len({item.region for item in facts if item.region})),
        "mode_count": Decimal(len({item.mode for item in facts if item.mode})),
        "observation_day_count": Decimal(
            len({item.capture_time.astimezone(UTC).date() for item in facts})
        ),
        "configured_competitor_count": Decimal(len(configured_competitors)),
    }


def _aggregate_sources(
    facts: Sequence[CustomerAnswerFact],
    *,
    include_rows: bool = True,
) -> tuple[dict[str, Decimal | None], list[dict[str, Any]]]:
    citations = [
        citation for answer in facts for citation in answer.citations if citation.host.strip()
    ]
    total_answers = len(facts)
    answers_with_citations = sum(
        any(citation.host.strip() for citation in item.citations) for item in facts
    )
    mentioned_answers = sum(item.mentioned for item in facts)
    mentioned_answers_with_citations = sum(
        item.mentioned and any(citation.host.strip() for citation in item.citations)
        for item in facts
    )
    answers_with_own = sum(
        any(citation.own_source for citation in item.citations) for item in facts
    )
    answers_with_third_party = sum(
        any(not citation.own_source for citation in item.citations if citation.host.strip())
        for item in facts
    )
    own_references = sum(citation.own_source for citation in citations)
    cited_text = sum(bool((citation.cited_text or "").strip()) for citation in citations)
    citation_titles = sum(bool((citation.title or "").strip()) for citation in citations)
    host_counts: Counter[str] = Counter()
    host_answer_ids: dict[str, set[str]] = defaultdict(set)
    own_hosts: set[str] = set()
    for answer in facts:
        for citation in answer.citations:
            host = citation.host.strip().lower()
            if not host:
                continue
            host_counts[host] += 1
            host_answer_ids[host].add(answer.answer_pub_id)
            if citation.own_source:
                own_hosts.add(host)
    total_references = sum(host_counts.values())
    hhi = (
        sum((Decimal(count) / Decimal(total_references)) ** 2 for count in host_counts.values())
        if total_references
        else None
    )
    rows = (
        [
            {
                "host": host,
                "references": count,
                "share": _json_decimal(_ratio(count, total_references)),
                "own_source": host in own_hosts,
                "answers": len(host_answer_ids[host]),
            }
            for host, count in host_counts.most_common()
        ]
        if include_rows
        else []
    )
    return (
        {
            "citation_coverage": _ratio(answers_with_citations, total_answers),
            "uncited_answer_rate": _ratio(total_answers - answers_with_citations, total_answers),
            "mentioned_answer_citation_rate": _ratio(
                mentioned_answers_with_citations, mentioned_answers
            ),
            "average_citations": _ratio(len(citations), total_answers),
            "citation_references": Decimal(len(citations)),
            "unique_source_hosts": Decimal(len(host_counts)),
            "source_concentration_hhi": _score(hhi * Decimal(100)) if hhi is not None else None,
            "source_diversity_index": _score((Decimal(1) - hhi) * Decimal(100))
            if hhi is not None
            else None,
            "top_source_share": _ratio(max(host_counts.values()), total_references)
            if host_counts
            else None,
            "own_source_answer_rate": _ratio(answers_with_own, total_answers),
            "own_source_reference_share": _ratio(own_references, len(citations)),
            "own_source_share_of_cited_answers": _ratio(answers_with_own, answers_with_citations),
            "third_party_source_answer_rate": _ratio(answers_with_third_party, total_answers),
            "cited_text_visibility_rate": _ratio(cited_text, len(citations)),
            "citation_title_visibility_rate": _ratio(citation_titles, len(citations)),
        },
        rows,
    )


def _aggregate_source_audits(
    facts: Sequence[CustomerSourceAuditFact],
) -> dict[str, Decimal | None]:
    completed = [
        item for item in facts if item.audit_status in {"ok", "completed"} and item.verdict
    ]
    return {
        "source_audit_count": Decimal(len(completed)),
        "source_accuracy_rate": _ratio(
            sum(item.verdict == "accurate" for item in completed), len(completed)
        ),
        "source_unsupported_rate": _ratio(
            sum(item.verdict == "unsupported" for item in completed), len(completed)
        ),
        "source_unverifiable_rate": _ratio(
            sum(item.verdict == "unverifiable" for item in completed), len(completed)
        ),
    }


def _aggregate_risks(facts: Sequence[CustomerRiskFact]) -> dict[str, Decimal | None]:
    return {
        "risk_judgment_count": Decimal(len(facts)),
        "disparagement_count": Decimal(sum(item.disparagement for item in facts)),
        "support_count": Decimal(sum(item.attitude == "support" for item in facts)),
        "disparagement_rate": _ratio(sum(item.disparagement for item in facts), len(facts)),
        "support_rate": _ratio(sum(item.attitude == "support" for item in facts), len(facts)),
    }


def _dimension_rows(
    facts: Sequence[CustomerAnswerFact],
    *,
    key: Any,
    brand_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[CustomerAnswerFact]] = defaultdict(list)
    for item in facts:
        grouped[str(key(item))].append(item)
    rows = []
    for label, children in sorted(grouped.items()):
        metrics = (
            _aggregate_answers(children, brand_name=brand_name)
            | _aggregate_sources(children, include_rows=False)[0]
        )
        rows.append({"key": label, "label": label, "metrics": _serialize_metrics(metrics)})
    return rows


def _question_rows(facts: Sequence[CustomerAnswerFact], *, brand_name: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[CustomerAnswerFact]] = defaultdict(list)
    for item in facts:
        key = item.query_pub_id or _stable_query_key(item.query_text or "未知问题")
        grouped[(key, item.query_text or "未知问题")].append(item)
    rows = []
    for (key, text), children in grouped.items():
        rows.append(
            {
                "query_pub_id": key,
                "query_text": text,
                "query_group": next(
                    (item.query_group for item in children if item.query_group), None
                ),
                "metrics": _serialize_metrics(
                    _aggregate_answers(children, brand_name=brand_name)
                    | _aggregate_sources(children, include_rows=False)[0]
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -_sortable_metric_value(row["metrics"], "mention_rate"),
            str(row["query_text"]),
        ),
    )


def _competitor_rows(
    facts: Sequence[CustomerAnswerFact],
    *,
    brand_name: str,
    competitor_names: Sequence[str],
) -> list[dict[str, Any]]:
    del brand_name  # Answer analysis is the authoritative target-brand mention signal.
    target_mentions = sum(item.mentioned for item in facts)
    competitor_mentions = {
        competitor: sum(competitor in item.competitor_mentions for item in facts)
        for competitor in competitor_names
    }
    market_mentions = target_mentions + sum(competitor_mentions.values())
    rows: list[dict[str, Any]] = []
    for competitor in competitor_names:
        answer_mentions = competitor_mentions[competitor]
        ranks = [
            item.competitor_ranks[competitor]
            for item in facts
            if competitor in item.competitor_ranks
        ]
        h2h = [
            (item.rank, item.competitor_ranks[competitor])
            for item in facts
            if item.rank is not None and competitor in item.competitor_ranks
        ]
        h2h_wins = sum(target_rank < competitor_rank for target_rank, competitor_rank in h2h)
        h2h_ties = sum(target_rank == competitor_rank for target_rank, competitor_rank in h2h)
        h2h_losses = sum(target_rank > competitor_rank for target_rank, competitor_rank in h2h)
        rows.append(
            {
                "name": competitor,
                "metrics": _serialize_metrics(
                    {
                        "mention_rate": _ratio(answer_mentions, len(facts)),
                        "average_rank": _mean(ranks),
                        "ranked_answer_rate": _ratio(len(ranks), len(facts)),
                        "rank_stddev": _decimal(pstdev(ranks)) if ranks else None,
                        "top1_rate": _ratio(sum(rank <= 1 for rank in ranks), len(facts)),
                        "top3_rate": _ratio(sum(rank <= 3 for rank in ranks), len(facts)),
                        "share_of_voice": _ratio(competitor_mentions[competitor], market_mentions),
                        "head_to_head_win_rate": _ratio(h2h_wins, len(h2h)),
                        "head_to_head_tie_rate": _ratio(h2h_ties, len(h2h)),
                        "head_to_head_loss_rate": _ratio(h2h_losses, len(h2h)),
                    }
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: -_sortable_metric_value(row["metrics"], "mention_rate"),
    )


def _trend_rows(facts: Sequence[CustomerAnswerFact], *, brand_name: str) -> list[dict[str, Any]]:
    grouped: dict[date, list[CustomerAnswerFact]] = defaultdict(list)
    for item in facts:
        grouped[item.capture_time.astimezone(UTC).date()].append(item)
    return [
        {
            "date": day.isoformat(),
            "metrics": _serialize_metrics(
                _aggregate_answers(grouped[day], brand_name=brand_name)
                | _aggregate_sources(grouped[day], include_rows=False)[0]
            ),
        }
        for day in sorted(grouped)
    ]


def _risk_dimension_rows(facts: Sequence[CustomerRiskFact]) -> list[dict[str, Any]]:
    grouped: dict[str, list[CustomerRiskFact]] = defaultdict(list)
    for item in facts:
        grouped[item.model or "未知模型"].append(item)
    return [
        {"key": key, "label": key, "metrics": _serialize_metrics(_aggregate_risks(children))}
        for key, children in sorted(grouped.items())
    ]


def _composite_metrics(
    metrics: Mapping[str, Decimal | None],
    *,
    models: Sequence[Mapping[str, Any]],
    competitors: Sequence[Mapping[str, Any]],
) -> dict[str, Decimal | None]:
    visibility = _weighted_score(
        (
            (_percentage_score(metrics.get("mention_rate")), Decimal("0.40")),
            (_percentage_score(metrics.get("top3_rate")), Decimal("0.25")),
            (metrics.get("rank_score"), Decimal("0.20")),
            (_percentage_score(metrics.get("share_of_voice")), Decimal("0.15")),
        )
    )
    competitor_mentions = [
        _metric_json_value(row.get("metrics", []), "mention_rate") for row in competitors
    ]
    strongest_competitor = max(
        (value for value in competitor_mentions if value is not None), default=0
    )
    mention_advantage = _score(
        Decimal("50")
        + Decimal(str((_json_decimal(metrics.get("mention_rate")) or 0) - strongest_competitor))
        * Decimal("50")
    )
    competitive = _weighted_score(
        (
            (_percentage_score(metrics.get("share_of_voice")), Decimal("0.45")),
            (_percentage_score(metrics.get("head_to_head_win_rate")), Decimal("0.30")),
            (mention_advantage, Decimal("0.25")),
        ),
        allow_missing=True,
    )
    authority = _weighted_score(
        (
            (_percentage_score(metrics.get("citation_coverage")), Decimal("0.35")),
            (_percentage_score(metrics.get("own_source_answer_rate")), Decimal("0.30")),
            (metrics.get("source_diversity_index"), Decimal("0.20")),
            (_percentage_score(metrics.get("cited_text_visibility_rate")), Decimal("0.15")),
        )
    )
    content = _weighted_score(
        (
            (_percentage_score(metrics.get("own_source_answer_rate")), Decimal("0.30")),
            (_percentage_score(metrics.get("cited_text_visibility_rate")), Decimal("0.20")),
            (_percentage_score(metrics.get("source_accuracy_rate")), Decimal("0.30")),
            (metrics.get("source_diversity_index"), Decimal("0.20")),
        ),
        allow_missing=True,
    )
    reputation = metrics.get("net_sentiment")
    disparagement = metrics.get("disparagement_rate")
    if reputation is not None and disparagement is not None:
        reputation = _score(reputation - disparagement * Decimal(30))
    model_mentions = [
        value
        for row in models
        if (value := _metric_json_value(row.get("metrics", []), "mention_rate")) is not None
    ]
    consistency = None
    if model_mentions:
        variation = pstdev(model_mentions) if len(model_mentions) > 1 else 0.0
        consistency = _score(Decimal(100) - Decimal(str(min(1.0, variation))) * Decimal(100))
    return {
        "geo_visibility_index": visibility,
        "competitive_power_index": competitive,
        "source_authority_index": authority,
        "content_readiness_index": content,
        "reputation_index": reputation,
        "cognition_consistency_index": consistency,
    }


def _serialize_metrics(values: Mapping[str, Decimal | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, value in values.items():
        spec = METRIC_CATALOG.get(code)
        if spec is None:
            continue
        rows.append(
            {
                "code": code,
                "label": spec.label,
                "group": spec.group,
                "format": spec.format,
                "direction": spec.direction,
                "value": _json_decimal(value),
                "state": "ready" if value is not None else "not_ready",
                "version": spec.version,
            }
        )
    order = {spec.code: index for index, spec in enumerate(_METRIC_SPECS)}
    return sorted(rows, key=lambda item: order[item["code"]])


def _weighted_score(
    values: Sequence[tuple[Decimal | None, Decimal]], *, allow_missing: bool = False
) -> Decimal | None:
    if not values:
        return None
    available = [(value, weight) for value, weight in values if value is not None]
    if not available or (not allow_missing and len(available) != len(values)):
        return None
    total_weight = sum((weight for _, weight in available), Decimal(0))
    if not total_weight:
        return None
    return _score(sum((value * weight for value, weight in available), Decimal(0)) / total_weight)


def _percentage_score(value: Decimal | None) -> Decimal | None:
    return _score(value * Decimal(100)) if value is not None else None


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return Decimal(numerator) / Decimal(denominator) if denominator else None


def _mean(values: Sequence[int | float | Decimal]) -> Decimal | None:
    return (
        sum((_decimal(value) for value in values), Decimal(0)) / Decimal(len(values))
        if values
        else None
    )


def _decimal(value: int | float | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _score(value: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(100), value)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _json_decimal(value: Decimal | None) -> float | int | None:
    if value is None:
        return None
    rounded = value.quantize(Decimal("0.0001"), ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def _metric_json_value(metrics: object, code: str) -> float | None:
    if not isinstance(metrics, Sequence) or isinstance(metrics, str | bytes):
        return None
    for row in metrics:
        if isinstance(row, Mapping) and row.get("code") == code:
            value = row.get("value")
            return float(value) if isinstance(value, int | float) else None
    return None


def _sortable_metric_value(metrics: object, code: str) -> float:
    value = _metric_json_value(metrics, code)
    return value if value is not None else -1.0


def _stable_query_key(value: str) -> str:
    return f"qry_hash_{sha256(value.encode()).hexdigest()[:20]}"


def _iso(value: datetime) -> str:
    candidate = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return candidate.astimezone(UTC).isoformat().replace("+00:00", "Z")


_RECOMMENDATION_QUERY_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"推荐",
        r"排行|排名|榜单",
        r"(?:哪家|哪个).{0,12}(?:好|强|可靠|领先|首选|值得)",
        r"(?:品牌|厂商|公司|服务商|供应商|产品|方案).{0,12}(?:有哪些|哪家|哪个好|选谁)",
        r"(?:有哪些|哪家|哪个好|选谁).{0,12}(?:品牌|厂商|公司|服务商|供应商|产品|方案)",
        r"(?:首选|优选|值得选择|值得购买|值得考虑)",
        r"top\s*\d+",
        r"十大.{0,12}(?:品牌|厂商|公司|服务商|供应商|产品|方案)",
    )
)
_EXPLICIT_RECOMMENDATION_MARKERS = (
    "推荐",
    "建议选择",
    "优先考虑",
    "值得选择",
    "值得购买",
    "值得考虑",
    "首选",
    "优选",
    "表现突出",
    "可以考虑",
)
_EXPLICIT_REJECTION_MARKERS = (
    "不推荐",
    "不建议",
    "避免选择",
    "应避免",
    "谨慎选择",
    "慎选",
    "不值得",
    "不适合",
    "不宜选择",
)


def infer_recommendation(
    *,
    query_text: str | None,
    response_text: str,
    brand_name: str,
    mentioned: bool,
    rank: int | None,
) -> bool | None:
    """Derive an auditable recommendation fact from saved answer text.

    A recommendation query supplies a trustworthy denominator: inclusion of the
    target brand is positive unless the nearby answer text explicitly rejects it;
    absence is negative.  Outside that scope, only an explicit rank or local
    recommendation/rejection phrase is classified.  Ambiguous prose remains
    unclassified instead of being silently converted to ``False``.
    """

    query = " ".join((query_text or "").casefold().split())
    response = " ".join(response_text.casefold().split())
    brand = " ".join(brand_name.casefold().split())
    recommendation_query = bool(query) and any(
        pattern.search(query) for pattern in _RECOMMENDATION_QUERY_PATTERNS
    )

    contexts: list[str] = []
    if brand:
        start = 0
        while (position := response.find(brand, start)) >= 0:
            contexts.append(response[max(0, position - 36) : position + len(brand) + 36])
            start = position + len(brand)
    local_rejection = any(
        marker in context for marker in _EXPLICIT_REJECTION_MARKERS for context in contexts
    )
    local_recommendation = any(
        marker in context for marker in _EXPLICIT_RECOMMENDATION_MARKERS for context in contexts
    )

    if mentioned and local_rejection:
        return False
    if mentioned and (recommendation_query or rank is not None or local_recommendation):
        return True
    if recommendation_query:
        return False
    return None


def infer_competitor_mentions(
    response_text: str, competitor_names: Sequence[str]
) -> tuple[str, ...]:
    """Case-insensitive literal competitor mentions for raw-fact backfills."""

    folded = response_text.casefold()
    return tuple(name for name in competitor_names if name and name.casefold() in folded)


def own_site_host(website: str | None) -> str | None:
    """Normalize an official website to its lower-case host without importing API code."""

    if not website:
        return None
    candidate = website.strip()
    match = re.match(r"^(?:https?://)?([^/:?#]+)", candidate, flags=re.IGNORECASE)
    return match.group(1).lower().rstrip(".") if match else None


def is_own_site(host: str, official_host: str | None) -> bool:
    if not host or not official_host:
        return False
    candidate = host.lower().rstrip(".")
    apex = official_host[4:] if official_host.startswith("www.") else official_host
    return candidate in {official_host, apex, f"www.{apex}"} or candidate.endswith(f".{apex}")


def assert_customer_projection_safe(value: object, path: str = "") -> None:
    """Fail closed if an operational collection field ever reaches a customer document."""

    forbidden = {
        "total_tasks",
        "completed_tasks",
        "failed_tasks",
        "success_rate",
        "attempt_count",
        "workflow_id",
        "temporal_run_id",
        "browser_instance",
        "platform_account",
        "account_pub_id",
        "error_code",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in forbidden:
                raise ValueError(
                    f"operational field rejected from customer projection: {path}{key}"
                )
            assert_customer_projection_safe(child, f"{path}{key}.")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, child in enumerate(value):
            assert_customer_projection_safe(child, f"{path}{index}.")


__all__ = [
    "CustomerAnswerFact",
    "CustomerCitationFact",
    "CustomerRiskFact",
    "CustomerSourceAuditFact",
    "METRIC_CATALOG",
    "MetricSpec",
    "assert_customer_projection_safe",
    "build_customer_metric_bundle",
    "infer_competitor_mentions",
    "infer_recommendation",
    "is_own_site",
    "metric_catalog",
    "own_site_host",
]
