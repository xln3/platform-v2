from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any


class MetricState(StrEnum):
    READY = "ready"
    EXPERIMENTAL = "experimental"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AnswerFact:
    answer_pub_id: str
    mentioned: bool
    rank: int | None
    sentiment: str | None
    recommended: bool | None
    competitor_ranks: Mapping[str, int]
    citation_count: int
    dimensions: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class KpiCell:
    metric: str
    value: Decimal | None
    numerator: int | None
    denominator: int
    state: MetricState
    metric_version: str
    scorer_version: str
    filter_hash: str
    trace_token: str
    contributing_answer_pub_ids: tuple[str, ...]
    advisory: bool = False
    reason: str | None = None


MetricComputer = Callable[[tuple[AnswerFact, ...]], tuple[Decimal | None, int | None]]


class MetricRegistry:
    def __init__(self, *, metric_version: str, scorer_version: str) -> None:
        self.metric_version = metric_version
        self.scorer_version = scorer_version
        self._computers: dict[str, MetricComputer] = {}
        self.register("mention_rate", _mention_rate)
        self.register("average_rank", _average_rank)
        for top_n in (1, 3, 10):
            self.register(f"top{top_n}_rate", _top_rate(top_n))
        self.register("citation_coverage", _citation_coverage)

    def register(self, name: str, computer: MetricComputer) -> None:
        if name in self._computers:
            raise ValueError(f"metric already registered: {name}")
        self._computers[name] = computer

    def compute(
        self,
        name: str,
        facts: Iterable[AnswerFact],
        *,
        filters: Mapping[str, Any],
        recommendation_calibrated: bool = False,
    ) -> KpiCell:
        selected = tuple(fact for fact in facts if _matches(fact, filters))
        canonical = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        filter_hash = sha256(canonical.encode()).hexdigest()
        ids = tuple(sorted(fact.answer_pub_id for fact in selected))
        trace_token = sha256(f"{name}:{filter_hash}:{','.join(ids)}".encode()).hexdigest()
        value: Decimal | None
        numerator: int | None
        if name == "recommendation_rate":
            if not recommendation_calibrated:
                return KpiCell(
                    metric=name,
                    value=None,
                    numerator=None,
                    denominator=len(selected),
                    state=MetricState.EXPERIMENTAL,
                    metric_version=self.metric_version,
                    scorer_version=self.scorer_version,
                    filter_hash=filter_hash,
                    trace_token=trace_token,
                    contributing_answer_pub_ids=ids,
                    advisory=True,
                    reason="recommendation classifier is not calibrated",
                )
            value, numerator = _recommendation_rate(selected)
        else:
            try:
                value, numerator = self._computers[name](selected)
            except KeyError as exc:
                raise ValueError(f"unknown metric: {name}") from exc
        return KpiCell(
            metric=name,
            value=value,
            numerator=numerator,
            denominator=len(selected),
            state=MetricState.READY if selected else MetricState.INSUFFICIENT,
            metric_version=self.metric_version,
            scorer_version=self.scorer_version,
            filter_hash=filter_hash,
            trace_token=trace_token,
            contributing_answer_pub_ids=ids,
        )


def _matches(fact: AnswerFact, filters: Mapping[str, Any]) -> bool:
    return all(fact.dimensions.get(key) == str(value) for key, value in filters.items())


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return (Decimal(numerator) / Decimal(denominator)) if denominator else None


def _mention_rate(facts: tuple[AnswerFact, ...]) -> tuple[Decimal | None, int]:
    numerator = sum(fact.mentioned for fact in facts)
    return _ratio(numerator, len(facts)), numerator


def _average_rank(facts: tuple[AnswerFact, ...]) -> tuple[Decimal | None, None]:
    ranks = [fact.rank for fact in facts if fact.rank is not None]
    return (
        (sum(Decimal(rank) for rank in ranks) / Decimal(len(ranks))) if ranks else None,
        None,
    )


def _top_rate(top_n: int) -> MetricComputer:
    def compute(facts: tuple[AnswerFact, ...]) -> tuple[Decimal | None, int]:
        numerator = sum(fact.rank is not None and fact.rank <= top_n for fact in facts)
        return _ratio(numerator, len(facts)), numerator

    return compute


def _citation_coverage(facts: tuple[AnswerFact, ...]) -> tuple[Decimal | None, int]:
    numerator = sum(fact.citation_count > 0 for fact in facts)
    return _ratio(numerator, len(facts)), numerator


def _recommendation_rate(facts: tuple[AnswerFact, ...]) -> tuple[Decimal | None, int]:
    classified = [fact for fact in facts if fact.recommended is not None]
    numerator = sum(fact.recommended is True for fact in classified)
    return _ratio(numerator, len(classified)), numerator
