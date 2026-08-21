"""Independent A/B/C page inspection with exact evidence chains and T propagation.

This analyzer runs after public source acquisition.  It never mutates collection
rows and is deliberately separate from source-accuracy audit and binary
disparagement detection.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol

import httpx
import psycopg
import structlog
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.source_analysis.page_inspection import (
    PAGE_INSPECTION_POLICY_VERSION,
    PAGE_INSPECTION_PROMPT_VERSION,
    SourceAnalysisProfile,
    ValidatedFinding,
    ValidatedSpan,
    profile_fingerprint,
    validate_finding,
)
from workflows.activities.source_audit import (
    AuditLlmConfig,
    SourceTextStore,
    _MinioSourceTextStore,
    _normalize_base_url,
    audit_llm_config_from_settings,
)

log = structlog.get_logger()

ENV_ENABLED = "GEO_PAGE_INSPECTION_ENABLED"
ENV_MAX_DOCUMENTS = "GEO_PAGE_INSPECTION_MAX_DOCUMENTS"
ENV_MAX_CHARS = "GEO_PAGE_INSPECTION_MAX_CHARS"

PROMPT_VERSION = PAGE_INSPECTION_PROMPT_VERSION
_LLM_TIMEOUT_S = 120.0
_HEARTBEAT_INTERVAL_S = 10.0
_WINDOW_CHARS = 12_000
_WINDOW_OVERLAP = 500


@dataclass(frozen=True)
class PageInspectionInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    profile_pub_id: str | None
    profile_hash: str = ""
    policy_version: str = PAGE_INSPECTION_POLICY_VERSION
    model: str = ""
    prompt_version: str = PAGE_INSPECTION_PROMPT_VERSION


@dataclass(frozen=True)
class LinkedAnswer:
    pub_id: str
    text: str
    query: str
    model: str
    source_quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InspectionDocument:
    pub_id: str
    url: str
    host: str
    extract_status: str
    text_cas_key: str | None
    text_sha256: str | None
    page_title: str | None
    site_name: str | None
    publisher: str | None
    authors: tuple[str, ...]
    published_at: datetime | None
    published_at_confidence: str
    linked_answers: tuple[LinkedAnswer, ...]
    repost_members: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RunPageInspectionContext:
    tenant_pub_id: str
    tenant_id: str
    project_id: str
    run_id: str
    run_pub_id: str
    project_pub_id: str
    profile_id: str | None
    profile: SourceAnalysisProfile | None
    documents: tuple[InspectionDocument, ...]
    existing_keys: frozenset[tuple[str, str, str, str, str]]


@dataclass(frozen=True)
class AnalysisWindow:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PageCandidateBatch:
    findings: tuple[Mapping[str, Any], ...]
    attributions: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PageInspectionRecord:
    source_document_pub_id: str
    content_sha256: str
    status: str
    page_summary: Mapping[str, Any]
    transmission: Mapping[str, Any]
    attribution: Mapping[str, Any]
    quality: Mapping[str, Any]
    findings: tuple[ValidatedFinding, ...]
    model: str
    prompt_version: str
    policy_version: str


@dataclass(frozen=True)
class InspectedPage:
    source_document_pub_id: str
    inspection_pub_id: str
    status: str
    finding_count: int


@dataclass(frozen=True)
class PageInspectionFailure:
    source_document_pub_id: str
    error: str


@dataclass
class PageInspectionResult:
    inspected: list[InspectedPage] = field(default_factory=list)
    failures: list[PageInspectionFailure] = field(default_factory=list)
    skipped_documents: int = 0
    invalid_candidates: int = 0
    candidate_quotes: int = 0
    verified_quotes: int = 0
    truncated: int = 0
    skipped: str | None = None
    disabled: bool = False
    llm_unavailable: bool = False


class PageInspectionJudge(Protocol):
    def analyze(
        self,
        *,
        url: str,
        window: AnalysisWindow,
        profile: SourceAnalysisProfile,
        page_stats: Mapping[str, Any],
    ) -> PageCandidateBatch: ...


class PageInspectionLoader(Protocol):
    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
        profile_pub_id: str | None,
        profile_hash: str,
        *,
        policy_version: str,
        model: str,
        prompt_version: str,
    ) -> RunPageInspectionContext | None: ...


class PageInspectionSink(Protocol):
    def persist(
        self, *, context: RunPageInspectionContext, record: PageInspectionRecord
    ) -> tuple[str, bool]: ...


_CHAIN_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "connector": {
            "type": "string",
            "enum": ["because", "and", "but", "compared_with", "therefore"],
        },
        "fact_type": {
            "type": "string",
            "enum": ["source_quote", "authority_fact", "recomputable", "absence"],
        },
        "quote": {"type": "string"},
        "occurrence": {"type": "integer", "minimum": 0},
        "explanation": {"type": "string"},
        "authority_source": {"type": "string"},
        "authority_url": {"type": "string"},
        "publisher": {"type": "string"},
        "published_at": {"type": "string"},
        "authority_category": {"type": "string"},
        "algorithm": {"type": "string"},
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                "required": ["name", "value"],
            },
        },
        "result": {"type": "string"},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "search_scope": {"type": "string"},
        "operator": {"type": "string", "enum": ["any", "all"]},
        "match_count": {"type": "integer", "minimum": 0},
    },
    "required": [
        "connector",
        "fact_type",
        "quote",
        "occurrence",
        "explanation",
        "authority_source",
        "authority_url",
        "publisher",
        "published_at",
        "authority_category",
        "algorithm",
        "inputs",
        "result",
        "search_terms",
        "search_scope",
        "operator",
        "match_count",
    ],
}

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [
                            "A0",
                            "A1",
                            "A2",
                            "A3",
                            "A4",
                            "A5",
                            "B1",
                            "B2",
                            "B3",
                            "C1",
                            "C2",
                            "C3",
                            "C4",
                        ],
                    },
                    "ledger": {"type": "string", "enum": ["statement", "exposure"]},
                    "variant": {"type": "string"},
                    "summary": {"type": "string"},
                    "action": {"type": "string"},
                    "evidence_chain": {"type": "array", "items": _CHAIN_LINK_SCHEMA},
                    "self_check": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "passed": {"type": "boolean"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["passed", "reasoning"],
                    },
                },
                "required": [
                    "code",
                    "ledger",
                    "variant",
                    "summary",
                    "action",
                    "evidence_chain",
                    "self_check",
                ],
            },
        },
        "attributions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "publisher_account",
                            "content_source",
                            "correction_channel",
                            "beneficiary",
                        ],
                    },
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["kind", "value", "quote", "occurrence", "confidence"],
            },
        },
    },
    "required": ["findings", "attributions"],
}

_INSTRUCTIONS_V1 = """你是页面危害体检编码员。
对象档案是唯一参数来源，不得自动生成别名、同位对手或权威锚。
只提出本窗口有证据支撑的候选：A0-A5/B1-B3 属 statement 言论账，C1-C4 属 exposure 暴露账。
每条结论必须给 evidence_chain；source_quote.quote 必须逐字连续复制窗口正文。
不得改写、拼接或加省略号；source_quote.occurrence 按当前窗口内同一 quote 的第几次出现填写，
从 1 开始。程序会把它换算成全页字符区间，不要猜窗口之外的出现次数。
absence 必须写 search_terms、search_scope=source_document_body、operator 与全页复算 match_count。
authority_fact 只能引用档案 anchor_sources，并完整给来源、URL、发布方、时间、所属品类。
recomputable 必须给算法、输入和结果。说明只能解释该事实为何支撑本环节，不得塞入新事实。
C 组不得使用“拉踩、抹黑、诋毁、打压”；任何字段不得使用“刻意、恶意、故意、雇佣、水军”。
自校必须把同一判据反向用于对象自身；不通过则不要输出。A0/A5 只作为待外部核验候选。
受益方不等于发布/加害主体；没有逐字归属证据就不要输出归属候选。
只输出符合 JSON schema 的对象。"""

_PROMPTS = {PAGE_INSPECTION_PROMPT_VERSION: _INSTRUCTIONS_V1}


class PageJudgeError(RuntimeError):
    pass


class _ResponsesPageInspectionJudge:
    def __init__(
        self,
        config: AuditLlmConfig,
        *,
        instructions: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._instructions = instructions
        self._client = client

    def analyze(
        self,
        *,
        url: str,
        window: AnalysisWindow,
        profile: SourceAnalysisProfile,
        page_stats: Mapping[str, Any],
    ) -> PageCandidateBatch:
        profile_data = {
            "object": profile.object_name,
            "object_kind": profile.object_kind,
            "categories": profile.categories,
            "aliases": profile.aliases,
            "peers": profile.peers,
            "anchor_sources": list(profile.anchor_sources),
            "linked_entities": list(profile.linked_entities),
            "axes": {
                "hard_anchor_available": profile.hard_anchor_available,
                "decision_mode": profile.decision_mode,
            },
            "type": profile.profile_type,
        }
        body: dict[str, Any] = {
            "model": self._config.model,
            "instructions": self._instructions,
            "input": (
                f"【URL】{url}\n"
                f"【对象档案】{json.dumps(profile_data, ensure_ascii=False)}\n"
                f"【全页可复算统计】{json.dumps(page_stats, ensure_ascii=False)}\n"
                f"【本窗口字符范围】[{window.start},{window.end})\n"
                f"【窗口正文】\n{window.text}"
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "source_page_inspection",
                    "strict": True,
                    "schema": _JSON_SCHEMA,
                }
            },
        }
        return _parse_batch(self._post(body))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return self._post_with(self._client, body)
        bases = [self._config.base_url]
        if self._config.base_url_fallback.strip():
            bases.append(self._config.base_url_fallback)
        final_error: PageJudgeError | None = None
        for base in bases:
            try:
                with httpx.Client(
                    base_url=_normalize_base_url(base),
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    timeout=_LLM_TIMEOUT_S,
                    trust_env=False,
                ) as client:
                    return self._post_with(client, body)
            except PageJudgeError as exc:
                final_error = exc
        assert final_error is not None
        raise final_error

    @staticmethod
    def _post_with(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.post("/responses", json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PageJudgeError(f"LLM upstream: {type(exc).__name__}") from exc
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise PageJudgeError("LLM response is not JSON") from exc
        return payload


def _parse_batch(payload: Mapping[str, Any]) -> PageCandidateBatch:
    text_parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, Mapping) and part.get("type") == "output_text":
                    text_parts.append(str(part.get("text") or ""))
    raw = "\n".join(text_parts).strip()
    if not raw:
        raise PageJudgeError("LLM returned no output text")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PageJudgeError("LLM output JSON parse failed") from exc
    if not isinstance(data, Mapping):
        raise PageJudgeError("LLM output is not an object")
    findings = data.get("findings")
    attributions = data.get("attributions")
    if not isinstance(findings, list) or not isinstance(attributions, list):
        raise PageJudgeError("LLM output arrays missing")
    return PageCandidateBatch(
        findings=tuple(item for item in findings if isinstance(item, Mapping)),
        attributions=tuple(item for item in attributions if isinstance(item, Mapping)),
    )


def _quote_starts(text: str, quote: str) -> list[int]:
    starts: list[int] = []
    cursor = 0
    while quote and (position := text.find(quote, cursor)) >= 0:
        starts.append(position)
        cursor = position + max(1, len(quote))
    return starts


def _global_occurrence(
    *, source_text: str, window: AnalysisWindow, quote: str, local_occurrence: object
) -> int:
    """Translate a model-visible window occurrence into a full-page occurrence.

    The model cannot know how many equal strings occurred before its window.
    Returning zero deliberately makes the hard validator reject malformed or
    out-of-window evidence.
    """

    if (
        not quote
        or isinstance(local_occurrence, bool)
        or not isinstance(local_occurrence, int)
        or local_occurrence < 1
    ):
        return 0
    local_starts = _quote_starts(window.text, quote)
    if local_occurrence > len(local_starts):
        return 0
    global_start = window.start + local_starts[local_occurrence - 1]
    full_starts = _quote_starts(source_text, quote)
    try:
        return full_starts.index(global_start) + 1
    except ValueError:
        return 0


def bind_finding_candidate_to_window(
    candidate: Mapping[str, Any], *, source_text: str, window: AnalysisWindow
) -> dict[str, Any]:
    """Bind every candidate quote to the exact window that produced it."""

    bound = dict(candidate)
    chain = candidate.get("evidence_chain")
    if not isinstance(chain, list):
        return bound
    bound_chain: list[Any] = []
    for raw_link in chain:
        if not isinstance(raw_link, Mapping):
            bound_chain.append(raw_link)
            continue
        link = dict(raw_link)
        if link.get("fact_type") == "source_quote":
            quote = str(link.get("quote") or "")
            link["occurrence"] = _global_occurrence(
                source_text=source_text,
                window=window,
                quote=quote,
                local_occurrence=link.get("occurrence"),
            )
        bound_chain.append(link)
    bound["evidence_chain"] = bound_chain
    return bound


def bind_attribution_candidate_to_window(
    candidate: Mapping[str, Any], *, source_text: str, window: AnalysisWindow
) -> dict[str, Any]:
    """Bind an attribution quote to the exact window that produced it."""

    bound = dict(candidate)
    quote = str(bound.get("quote") or "")
    bound["occurrence"] = _global_occurrence(
        source_text=source_text,
        window=window,
        quote=quote,
        local_occurrence=bound.get("occurrence"),
    )
    return bound


def clamp_max_documents(value: str | None) -> int:
    try:
        parsed = int(value or "500")
    except ValueError:
        parsed = 500
    return max(1, min(parsed, 10_000))


def clamp_max_chars(value: str | None) -> int:
    try:
        parsed = int(value or "120000")
    except ValueError:
        parsed = 120_000
    return max(_WINDOW_CHARS, min(parsed, 500_000))


def build_analysis_windows(text: str, *, max_chars: int) -> tuple[list[AnalysisWindow], int]:
    allowed = min(len(text), max_chars)
    windows: list[AnalysisWindow] = []
    start = 0
    while start < allowed:
        end = min(allowed, start + _WINDOW_CHARS)
        windows.append(AnalysisWindow(start=start, end=end, text=text[start:end]))
        if end >= allowed:
            break
        start = end - _WINDOW_OVERLAP
    return windows, max(0, len(text) - allowed)


def page_stats(text: str, profile: SourceAnalysisProfile) -> dict[str, Any]:
    return {
        "body_characters": len(text),
        "object_term_matches": {term: text.count(term) for term in profile.object_terms},
        "peer_matches": {term: text.count(term) for term in profile.peers},
        "anchor_name_matches": {
            str(item.get("name") or ""): text.casefold().count(
                str(item.get("name") or "").casefold()
            )
            for item in profile.anchor_sources
            if str(item.get("name") or "").strip()
        },
    }


def compute_transmission(
    text: str, profile: SourceAnalysisProfile, answers: tuple[LinkedAnswer, ...]
) -> dict[str, Any]:
    unique_answers = {answer.pub_id: answer for answer in answers}
    rows = tuple(unique_answers.values())
    page_has_object = any(term in text for term in profile.object_terms)
    retained = sum(
        1 for answer in rows if any(term in answer.text for term in profile.object_terms)
    )
    exact_reuse = sum(
        1
        for answer in rows
        if any(quote and quote in answer.text for quote in answer.source_quotes)
    )
    denominator = len(rows)
    return {
        "T1": {
            "answer_count": denominator,
            "question_count": len({answer.query for answer in rows if answer.query}),
            "model_count": len({answer.model for answer in rows if answer.model}),
        },
        "T2": {
            "eligible": page_has_object,
            "retained_answers": retained if page_has_object else None,
            "linked_answers": denominator if page_has_object else None,
            "rate": (retained / denominator if page_has_object and denominator else None),
        },
        "T3": {
            "rate": None,
            "reason": "page/answer champion requires a validated ranking structure",
        },
        "T4": {
            "exact_wording_reuse_answers": exact_reuse,
            "linked_answers": denominator,
            "rate": exact_reuse / denominator if denominator else None,
        },
        "page_has_object": page_has_object,
    }


def _exact_span_for_term(text: str, terms: tuple[str, ...]) -> ValidatedSpan | None:
    candidates = [(text.find(term), term) for term in terms if text.find(term) >= 0]
    if not candidates:
        return None
    start, term = min(candidates, key=lambda item: item[0])
    left = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n")) + 1
    rights = [
        position for mark in ("。", "！", "？", "\n") if (position := text.find(mark, start)) >= 0
    ]
    end = min(rights) + 1 if rights else min(len(text), start + max(len(term), 160))
    if end - left > 400:
        left, end = start, start + len(term)
    quote = text[left:end]
    return ValidatedSpan(
        chain_ordinal=1,
        quote=quote,
        text_start=left,
        text_end=end,
        quote_hash=sha256(quote.encode()).hexdigest(),
    )


def build_transmission_finding(
    *, text: str, profile: SourceAnalysisProfile, transmission: Mapping[str, Any]
) -> ValidatedFinding | None:
    t1 = transmission.get("T1")
    t2 = transmission.get("T2")
    if not isinstance(t1, Mapping) or not isinstance(t2, Mapping):
        return None
    answer_count = t1.get("answer_count")
    retained = t2.get("retained_answers")
    if not isinstance(answer_count, int) or answer_count < 3 or retained != 0:
        return None
    span = _exact_span_for_term(text, profile.object_terms)
    if span is None:
        return None
    terms = list(profile.object_terms)
    chain = (
        {
            "connector": "because",
            "fact_type": "source_quote",
            "quote": span.quote,
            "text_start": span.text_start,
            "text_end": span.text_end,
            "quote_hash": span.quote_hash,
            "explanation": "页面正文逐字提及对象。",
        },
        {
            "connector": "but",
            "fact_type": "recomputable",
            "algorithm": "取引用本页的全部回答，按对象档案名称与实采别名逐字检索。",
            "inputs": {
                "linked_answers": answer_count,
                "search_terms": terms,
            },
            "result": {"retained_answers": 0, "retention_rate": 0.0},
            "explanation": "页面中的对象没有在引用它的回答中存续。",
        },
        {
            "connector": "therefore",
            "fact_type": "absence",
            "search_scope": "linked_answer_bodies",
            "search_terms": terms,
            "operator": "any",
            "match_count": 0,
            "explanation": "缺席发生在回答传导层，不据此评价页面作者。",
        },
    )
    return ValidatedFinding(
        code="C1",
        ledger="exposure",
        variant="transmission",
        summary="页面层未形成言论结论，但引用回答没有保留对象",
        action="build_content_coverage",
        evidence_chain=chain,
        self_check={
            "passed": True,
            "reasoning": "若同样的承接损失发生在同位对手，也只记传导缺席。",
        },
        spans=(span,),
        finding_status="confirmed",
    )


def _resolve_attributions(
    candidates: list[Mapping[str, Any]],
    *,
    source_text: str,
    windows: list[AnalysisWindow],
) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    rejected = 0
    allowed = {"publisher_account", "content_source", "correction_channel", "beneficiary"}
    seen: set[tuple[str, str, int]] = set()
    for candidate in candidates:
        kind = str(candidate.get("kind") or "")
        value = str(candidate.get("value") or "").strip()
        quote = str(candidate.get("quote") or "")
        occurrence = candidate.get("occurrence")
        confidence = candidate.get("confidence")
        if (
            kind not in allowed
            or not value
            or not quote
            or isinstance(occurrence, bool)
            or not isinstance(occurrence, int)
            or isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or re.search(r"刻意|恶意|故意|雇佣|水军", _all_candidate_text(candidate))
        ):
            rejected += 1
            continue
        positions = [match.start() for match in re.finditer(re.escape(quote), source_text)]
        if occurrence < 1 or occurrence > len(positions):
            rejected += 1
            continue
        start = positions[occurrence - 1]
        if not any(window.start <= start < window.end for window in windows):
            rejected += 1
            continue
        key = (kind, value.casefold(), start)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {
                "kind": kind,
                "value": value,
                "quote": quote,
                "text_start": start,
                "text_end": start + len(quote),
                "quote_hash": sha256(quote.encode()).hexdigest(),
                "confidence": float(confidence),
                "verification": "exact",
            }
        )
    return accepted, rejected


def _all_candidate_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_all_candidate_text(item) for item in value.values())
    if isinstance(value, list | tuple):
        return "\n".join(_all_candidate_text(item) for item in value)
    return ""


def build_attribution(
    document: InspectionDocument,
    *,
    candidates: list[Mapping[str, Any]],
    source_text: str,
    windows: list[AnalysisWindow],
    findings: tuple[ValidatedFinding, ...],
) -> tuple[dict[str, Any], int]:
    resolved, rejected = _resolve_attributions(candidates, source_text=source_text, windows=windows)
    members = [dict(member) for member in document.repost_members]
    earliest = None
    dated = [member for member in members if member.get("published_at") is not None]
    if dated:
        earliest = min(dated, key=lambda member: str(member["published_at"]))
    beneficiaries = sorted(
        {
            peer
            for finding in findings
            if finding.code.startswith("B")
            for peer in _peers_from_chain(finding, resolved)
        }
    )
    return (
        {
            "publisher_identity": {
                "domain": document.host,
                "site_name": document.site_name,
                "publisher": document.publisher,
                "authors": list(document.authors),
                "account": next(
                    (item["value"] for item in resolved if item["kind"] == "publisher_account"),
                    None,
                ),
                "granularity": (
                    "domain_and_account"
                    if any(item["kind"] == "publisher_account" for item in resolved)
                    else "domain_metadata_only"
                ),
            },
            "content_origin": {
                "exact_content_cluster_size": len(members),
                "members": members,
                "earliest_published_candidate": earliest,
                "method": "text_sha256",
                "scope": "project_history_canonical_urls",
                "claim_boundary": "earliest candidate is not proof of original publication",
            },
            "beneficiaries": beneficiaries,
            "beneficiary_is_not_actor": True,
            "correction_channels": [
                item["value"] for item in resolved if item["kind"] == "correction_channel"
            ],
            "declared_content_sources": [
                item["value"] for item in resolved if item["kind"] == "content_source"
            ],
            "evidence": resolved,
        },
        rejected,
    )


def _peers_from_chain(
    finding: ValidatedFinding, attributions: Sequence[Mapping[str, Any]]
) -> set[str]:
    del finding
    return {
        str(item.get("value") or "")
        for item in attributions
        if item.get("kind") == "beneficiary" and str(item.get("value") or "")
    }


def _dedupe_findings(findings: list[ValidatedFinding]) -> tuple[ValidatedFinding, ...]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ValidatedFinding] = []
    for finding in findings:
        anchor = finding.spans[0].quote_hash if finding.spans else ""
        key = (finding.code, finding.variant, anchor or finding.summary.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return tuple(out)


def _postgres_dsn() -> str:
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


def _profile_from_row(row: Mapping[str, Any]) -> SourceAnalysisProfile:
    aliases_raw = row.get("aliases")
    aliases = (
        tuple(
            str(item.get("value") or "").strip()
            for item in aliases_raw
            if isinstance(item, Mapping) and str(item.get("value") or "").strip()
        )
        if isinstance(aliases_raw, list)
        else ()
    )
    categories = tuple(
        str(item).strip() for item in (row.get("categories") or []) if str(item).strip()
    )
    own_domains = tuple(
        str(item).strip() for item in (row.get("own_domains") or []) if str(item).strip()
    )
    peers = tuple(str(item).strip() for item in (row.get("peers") or []) if str(item).strip())
    anchors_raw = row.get("anchor_sources")
    anchors = (
        tuple(item for item in anchors_raw if isinstance(item, Mapping))
        if isinstance(anchors_raw, list)
        else ()
    )
    entities_raw = row.get("linked_entities")
    entities = (
        tuple(item for item in entities_raw if isinstance(item, Mapping))
        if isinstance(entities_raw, list)
        else ()
    )
    decision_mode = str(row["decision_mode"])
    profile_type = str(row["profile_type"])
    if decision_mode not in {"selection", "reputation"} or profile_type not in {
        "I",
        "II",
        "III",
        "IV",
    }:
        raise ApplicationError(
            "source analysis profile vocabulary invalid",
            type="source_profile_invalid",
            non_retryable=True,
        )
    profile = SourceAnalysisProfile(
        pub_id=str(row["pub_id"]),
        object_name=str(row["object_name"]),
        object_kind=str(row["object_kind"]),  # type: ignore[arg-type]
        categories=categories,
        aliases=aliases,
        own_domains=own_domains,
        peers=peers,
        anchor_sources=anchors,
        linked_entities=entities,
        hard_anchor_available=bool(row["hard_anchor_available"]),
        decision_mode=decision_mode,  # type: ignore[arg-type]
        profile_type=profile_type,  # type: ignore[arg-type]
        profile_hash=str(row["profile_hash"]),
    )
    canonical = {
        "object_name": profile.object_name,
        "object_kind": profile.object_kind,
        "categories": list(row.get("categories") or []),
        "aliases": list(row.get("aliases") or []),
        "own_domains": list(row.get("own_domains") or []),
        "peers": list(row.get("peers") or []),
        "anchor_sources": list(row.get("anchor_sources") or []),
        "linked_entities": list(row.get("linked_entities") or []),
        "hard_anchor_available": profile.hard_anchor_available,
        "decision_mode": profile.decision_mode,
        "profile_type": profile.profile_type,
    }
    if profile_fingerprint(canonical) != profile.profile_hash:
        raise ApplicationError(
            "source analysis profile hash mismatch",
            type="source_profile_hash_mismatch",
            non_retryable=True,
        )
    return profile


class _PostgresPageInspectionLoader:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
        profile_pub_id: str | None,
        profile_hash: str,
        *,
        policy_version: str,
        model: str,
        prompt_version: str,
    ) -> RunPageInspectionContext | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            tenant = connection.execute(
                "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
            ).fetchone()
            if tenant is None:
                raise ApplicationError(
                    "tenant not found", type="tenant_not_found", non_retryable=True
                )
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.tenant_pub_id', %s, true)",
                (str(tenant["id"]), tenant_pub_id),
            )
            run = connection.execute(
                """
                SELECT r.id,r.project_id,p.pub_id AS project_pub_id
                FROM platform.collection_run r
                JOIN platform.project p ON p.id=r.project_id
                WHERE r.pub_id=%s
                """,
                (run_pub_id,),
            ).fetchone()
            if run is None:
                return None
            if str(run["project_pub_id"]) != project_pub_id:
                raise ApplicationError(
                    "collection run does not belong to project",
                    type="project_mismatch",
                    non_retryable=True,
                )
            profile_row = None
            if profile_pub_id:
                profile_row = connection.execute(
                    """
                    SELECT * FROM platform.source_analysis_profile
                    WHERE pub_id=%s AND project_id=%s
                    """,
                    (profile_pub_id, run["project_id"]),
                ).fetchone()
                if profile_row is None:
                    raise ApplicationError(
                        "source analysis profile not found",
                        type="source_profile_not_found",
                        non_retryable=True,
                    )
                if profile_hash and str(profile_row["profile_hash"]) != profile_hash:
                    raise ApplicationError(
                        "source analysis profile differs from frozen job input",
                        type="source_profile_frozen_hash_mismatch",
                        non_retryable=True,
                    )
            document_rows = connection.execute(
                """
                SELECT id,pub_id,url,host,extract_status,text_cas_key,text_sha256,
                       page_title,site_name,publisher,authors,published_at,
                       published_at_confidence
                FROM platform.source_document
                WHERE run_id=%s ORDER BY created_at,pub_id
                """,
                (run["id"],),
            ).fetchall()
            cluster_rows = connection.execute(
                """
                SELECT d.text_sha256,d.pub_id,d.url,d.canonical_url,d.publisher,
                       d.published_at,d.published_at_confidence,r.pub_id AS run_pub_id
                FROM platform.source_document d
                JOIN platform.collection_run r ON r.id=d.run_id
                WHERE d.project_id=%s AND d.extract_status='ok'
                  AND d.text_sha256 IS NOT NULL
                  AND d.text_sha256 IN (
                    SELECT text_sha256 FROM platform.source_document
                    WHERE run_id=%s AND text_sha256 IS NOT NULL
                  )
                ORDER BY d.first_seen_at,d.pub_id
                """,
                (run["project_id"], run["id"]),
            ).fetchall()
            links = connection.execute(
                """
                SELECT er.to_pub_id AS source_document_pub_id,t.pub_id AS answer_pub_id,
                       t.answer_text,t.matrix_json,acr.source_quote
                FROM evidence.evidence_relation er
                JOIN platform.collection_task t ON t.pub_id=er.from_pub_id AND t.run_id=%s
                LEFT JOIN analytics.answer_citation_relation acr
                  ON acr.tenant_pub_id=er.tenant_pub_id
                 AND acr.answer_pub_id=t.pub_id
                 AND acr.source_document_pub_id=er.to_pub_id
                WHERE er.tenant_pub_id=%s AND er.relation_type='cited_source_document'
                ORDER BY er.to_pub_id,t.pub_id,acr.ordinal
                """,
                (run["id"], tenant_pub_id),
            ).fetchall()
            existing_rows = connection.execute(
                """
                SELECT d.pub_id AS document_pub_id,p.pub_id AS profile_pub_id,
                       i.policy_version,i.model,i.prompt_version
                FROM platform.page_inspection i
                JOIN platform.source_document d ON d.id=i.source_document_id
                JOIN platform.source_analysis_profile p ON p.id=i.profile_id
                WHERE i.run_id=%s
                """,
                (run["id"],),
            ).fetchall()

        link_data: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in links:
            doc_key = str(row["source_document_pub_id"])
            answer_key = str(row["answer_pub_id"])
            current = link_data[doc_key].setdefault(
                answer_key,
                {"text": str(row["answer_text"] or ""), "query": "", "model": "", "quotes": []},
            )
            try:
                matrix = json.loads(row["matrix_json"] or "{}")
            except (TypeError, ValueError):
                matrix = {}
            if isinstance(matrix, Mapping):
                current["query"] = str(matrix.get("query") or "")
                current["model"] = str(matrix.get("model") or matrix.get("adapter") or "")
            quote = row["source_quote"]
            if isinstance(quote, str) and quote and quote not in current["quotes"]:
                current["quotes"].append(quote)

        clusters_by_url: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in cluster_rows:
            digest = str(row["text_sha256"] or "")
            if digest:
                canonical_url = str(row["canonical_url"] or row["url"])
                member = {
                    "source_document_pub_id": str(row["pub_id"]),
                    "run_pub_id": str(row["run_pub_id"]),
                    "url": str(row["url"]),
                    "canonical_url": canonical_url,
                    "publisher": row["publisher"],
                    "published_at": (
                        row["published_at"].isoformat()
                        if isinstance(row["published_at"], datetime)
                        else None
                    ),
                    "published_at_confidence": str(row["published_at_confidence"] or "unknown"),
                }
                existing = clusters_by_url[digest].get(canonical_url)
                # Repeated snapshots of one URL are one propagation member.
                # Prefer the snapshot with a usable publication timestamp.
                if existing is None or (
                    existing["published_at"] is None and member["published_at"] is not None
                ):
                    clusters_by_url[digest][canonical_url] = member
        clusters = {
            digest: sorted(members.values(), key=lambda member: str(member["canonical_url"]))
            for digest, members in clusters_by_url.items()
        }
        documents: list[InspectionDocument] = []
        for row in document_rows:
            answer_rows = link_data.get(str(row["pub_id"]), {})
            authors_raw = row["authors"]
            authors = (
                tuple(str(item) for item in authors_raw if str(item).strip())
                if isinstance(authors_raw, list)
                else ()
            )
            digest = str(row["text_sha256"] or "")
            documents.append(
                InspectionDocument(
                    pub_id=str(row["pub_id"]),
                    url=str(row["url"]),
                    host=str(row["host"]),
                    extract_status=str(row["extract_status"]),
                    text_cas_key=(str(row["text_cas_key"]) if row["text_cas_key"] else None),
                    text_sha256=digest or None,
                    page_title=(str(row["page_title"]) if row["page_title"] else None),
                    site_name=(str(row["site_name"]) if row["site_name"] else None),
                    publisher=(str(row["publisher"]) if row["publisher"] else None),
                    authors=authors,
                    published_at=row["published_at"],
                    published_at_confidence=str(row["published_at_confidence"] or "unknown"),
                    linked_answers=tuple(
                        LinkedAnswer(
                            pub_id=answer_pub_id,
                            text=str(data["text"]),
                            query=str(data["query"]),
                            model=str(data["model"]),
                            source_quotes=tuple(str(item) for item in data["quotes"]),
                        )
                        for answer_pub_id, data in answer_rows.items()
                    ),
                    repost_members=tuple(clusters.get(digest, [])),
                )
            )
        return RunPageInspectionContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant["id"]),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            run_pub_id=run_pub_id,
            project_pub_id=project_pub_id,
            profile_id=(str(profile_row["id"]) if profile_row is not None else None),
            profile=(_profile_from_row(profile_row) if profile_row is not None else None),
            documents=tuple(documents),
            existing_keys=frozenset(
                (
                    str(row["document_pub_id"]),
                    str(row["profile_pub_id"]),
                    str(row["policy_version"]),
                    str(row["model"]),
                    str(row["prompt_version"]),
                )
                for row in existing_rows
            ),
        )


@contextmanager
def _platform_connection(
    dsn: str, context: RunPageInspectionContext
) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (context.tenant_id, context.tenant_pub_id),
        )
        yield connection


def derive_inspection_pub_id(
    context: RunPageInspectionContext, record: PageInspectionRecord
) -> str:
    assert context.profile is not None
    stable = "|".join(
        (
            context.tenant_pub_id,
            record.source_document_pub_id,
            context.profile.pub_id,
            record.policy_version,
            record.model,
            record.prompt_version,
        )
    )
    return f"pgi_{sha256(stable.encode()).hexdigest()[:26]}"


class _PostgresPageInspectionSink:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def persist(
        self, *, context: RunPageInspectionContext, record: PageInspectionRecord
    ) -> tuple[str, bool]:
        if context.profile is None or context.profile_id is None:
            raise ApplicationError(
                "source analysis profile missing",
                type="source_profile_missing",
                non_retryable=True,
            )
        pub_id = derive_inspection_pub_id(context, record)
        with _platform_connection(self._dsn, context) as connection:
            inserted = connection.execute(
                """
                INSERT INTO platform.page_inspection
                  (id,pub_id,tenant_id,project_id,run_id,source_document_id,profile_id,
                   policy_version,prompt_version,model,content_sha256,status,page_summary,
                   transmission,attribution,quality)
                SELECT %s,%s,%s,%s,%s,d.id,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb
                FROM platform.source_document d
                WHERE d.pub_id=%s AND d.run_id=%s
                ON CONFLICT (source_document_id,profile_id,policy_version,model,prompt_version)
                DO NOTHING
                RETURNING id
                """,
                (
                    uuid.uuid4(),
                    pub_id,
                    context.tenant_id,
                    context.project_id,
                    context.run_id,
                    context.profile_id,
                    record.policy_version,
                    record.prompt_version,
                    record.model,
                    record.content_sha256,
                    record.status,
                    json.dumps(record.page_summary, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.transmission, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.attribution, ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(record.quality, ensure_ascii=False, sort_keys=True),
                    record.source_document_pub_id,
                    context.run_id,
                ),
            ).fetchone()
            if inserted is None:
                current = connection.execute(
                    "SELECT pub_id,content_sha256 FROM platform.page_inspection WHERE pub_id=%s",
                    (pub_id,),
                ).fetchone()
                if current is None or str(current["content_sha256"]) != record.content_sha256:
                    raise ApplicationError(
                        "page inspection replay payload drifted",
                        type="page_inspection_payload_drift",
                        non_retryable=True,
                    )
                return pub_id, False
            inspection_id = inserted["id"]
            source_document_row = connection.execute(
                "SELECT id FROM platform.source_document WHERE pub_id=%s",
                (record.source_document_pub_id,),
            ).fetchone()
            assert source_document_row is not None
            source_document_id = source_document_row["id"]
            for ordinal, finding in enumerate(record.findings, 1):
                finding_key = f"{pub_id}|{ordinal}|{finding.code}|{finding.variant}"
                finding_pub_id = f"pgf_{sha256(finding_key.encode()).hexdigest()[:26]}"
                finding_id = uuid.uuid4()
                connection.execute(
                    """
                    INSERT INTO platform.page_inspection_finding
                      (id,pub_id,tenant_id,inspection_id,ordinal,code,ledger,variant,
                       finding_status,summary,action,evidence_chain,self_check,validation)
                    VALUES
                      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                    """,
                    (
                        finding_id,
                        finding_pub_id,
                        context.tenant_id,
                        inspection_id,
                        ordinal,
                        finding.code,
                        finding.ledger,
                        finding.variant,
                        finding.finding_status,
                        finding.summary,
                        finding.action,
                        json.dumps(finding.evidence_chain, ensure_ascii=False, sort_keys=True),
                        json.dumps(finding.self_check, ensure_ascii=False, sort_keys=True),
                        json.dumps(
                            {
                                "quote_count": len(finding.spans),
                                "all_quotes_exact": True,
                                "validator": "page-evidence-chain-v1",
                            },
                            sort_keys=True,
                        ),
                    ),
                )
                for span in finding.spans:
                    span_key = f"{finding_pub_id}|{span.chain_ordinal}|{span.quote_hash}"
                    connection.execute(
                        """
                        INSERT INTO platform.page_evidence_span
                          (id,pub_id,tenant_id,finding_id,source_document_id,chain_ordinal,
                           quote,text_start,text_end,quote_hash,verification)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'exact')
                        """,
                        (
                            uuid.uuid4(),
                            f"pgs_{sha256(span_key.encode()).hexdigest()[:26]}",
                            context.tenant_id,
                            finding_id,
                            source_document_id,
                            span.chain_ordinal,
                            span.quote,
                            span.text_start,
                            span.text_end,
                            span.quote_hash,
                        ),
                    )
            connection.commit()
        return pub_id, True


def execute_page_inspection(
    item: PageInspectionInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    loader: PageInspectionLoader,
    text_store: SourceTextStore,
    sink: PageInspectionSink,
    judge: PageInspectionJudge | None,
    max_documents: int,
    max_chars: int,
    on_progress: Callable[[str, str], None] | None = None,
) -> PageInspectionResult:
    result = PageInspectionResult(disabled=not enabled)
    if not enabled:
        return result
    instructions = _PROMPTS.get(item.prompt_version)
    if instructions is None:
        raise ApplicationError(
            "page inspection prompt version is unsupported",
            type="page_inspection_prompt_version_unsupported",
            non_retryable=True,
        )
    progress = on_progress or (lambda _stage, _subject: None)
    # The model name is frozen in the durable handoff payload.  A deployment
    # changing settings later must not silently change an already queued job.
    model = item.model.strip()
    llm = replace(llm, model=model)
    context = loader.load(
        item.tenant_pub_id,
        item.run_pub_id,
        item.project_pub_id,
        item.profile_pub_id,
        item.profile_hash,
        policy_version=item.policy_version,
        model=model,
        prompt_version=item.prompt_version,
    )
    if context is None:
        result.skipped = "run_not_found"
        return result
    if context.profile is None:
        result.skipped = "profile_missing"
        return result
    profile = context.profile
    effective_judge = judge
    if effective_judge is None and llm.api_key and model:
        effective_judge = _ResponsesPageInspectionJudge(llm, instructions=instructions)
    if effective_judge is None:
        result.llm_unavailable = True

    documents = context.documents[:max_documents]
    result.truncated += max(0, len(context.documents) - len(documents))
    for document in documents:
        if document.extract_status != "ok" or not document.text_cas_key or not document.text_sha256:
            result.skipped_documents += 1
            continue
        key = (
            document.pub_id,
            profile.pub_id,
            item.policy_version,
            model,
            item.prompt_version,
        )
        if key in context.existing_keys:
            result.skipped_documents += 1
            continue
        progress("read_text", document.pub_id)
        try:
            source_text = text_store.get_text(document.text_cas_key, document.text_sha256)
        except Exception as exc:
            result.failures.append(
                PageInspectionFailure(document.pub_id, f"cas_read:{type(exc).__name__}")
            )
            continue
        if sha256(source_text.encode()).hexdigest() != document.text_sha256:
            raise ApplicationError(
                "source document content hash mismatch",
                type="source_document_hash_mismatch",
                non_retryable=True,
            )
        windows, truncated_chars = build_analysis_windows(source_text, max_chars=max_chars)
        if truncated_chars:
            result.truncated += 1
        stats = page_stats(source_text, profile)
        transmission = compute_transmission(source_text, profile, document.linked_answers)
        candidates: list[Mapping[str, Any]] = []
        attribution_candidates: list[Mapping[str, Any]] = []
        window_errors: list[str] = []
        if effective_judge is not None:
            for index, window in enumerate(windows, 1):
                progress(f"judge_{index}_{len(windows)}", document.pub_id)
                try:
                    batch = effective_judge.analyze(
                        url=document.url,
                        window=window,
                        profile=profile,
                        page_stats=stats,
                    )
                except Exception as exc:
                    window_errors.append(type(exc).__name__)
                    continue
                candidates.extend(
                    bind_finding_candidate_to_window(
                        candidate, source_text=source_text, window=window
                    )
                    for candidate in batch.findings
                )
                attribution_candidates.extend(
                    bind_attribution_candidate_to_window(
                        candidate, source_text=source_text, window=window
                    )
                    for candidate in batch.attributions
                )

        valid: list[ValidatedFinding] = []
        invalid_details: list[dict[str, Any]] = []
        document_candidate_quotes = 0
        document_verified_quotes = 0
        for candidate in candidates:
            validation = validate_finding(candidate, source_text=source_text, profile=profile)
            document_candidate_quotes += validation.candidate_quote_count
            document_verified_quotes += validation.verified_quote_count
            result.candidate_quotes += validation.candidate_quote_count
            result.verified_quotes += validation.verified_quote_count
            if validation.finding is None:
                result.invalid_candidates += 1
                invalid_details.append(
                    {
                        "code": str(candidate.get("code") or "")[:3],
                        "errors": list(validation.errors)[:12],
                    }
                )
                continue
            valid.append(validation.finding)
        findings = _dedupe_findings(valid)
        fully_scanned = effective_judge is not None and not window_errors and truncated_chars == 0
        if fully_scanned and not findings:
            transmission_finding = build_transmission_finding(
                text=source_text, profile=profile, transmission=transmission
            )
            if transmission_finding is not None:
                findings = (transmission_finding,)
                document_candidate_quotes += 1
                document_verified_quotes += 1
                result.candidate_quotes += 1
                result.verified_quotes += 1
        attribution, attribution_rejected = build_attribution(
            document,
            candidates=attribution_candidates,
            source_text=source_text,
            windows=windows,
            findings=findings,
        )
        result.invalid_candidates += attribution_rejected
        status = (
            "unverifiable"
            if effective_judge is None
            else "partial"
            if window_errors or truncated_chars
            else "completed"
        )
        page_summary = {
            "statement_codes": sorted(
                {finding.code for finding in findings if finding.ledger == "statement"}
            ),
            "exposure_codes": sorted(
                {finding.code for finding in findings if finding.ledger == "exposure"}
            ),
            "confirmed_count": sum(finding.finding_status == "confirmed" for finding in findings),
            "needs_review_count": sum(
                finding.finding_status == "needs_review" for finding in findings
            ),
            "profile_type": profile.profile_type,
            "page_stats": stats,
            "statement_and_exposure_are_not_additive": True,
        }
        quality = {
            "candidate_findings": len(candidates),
            "accepted_findings": len(findings),
            "invalid_candidates": len(invalid_details) + attribution_rejected,
            "candidate_quotes": document_candidate_quotes,
            "verified_quotes": document_verified_quotes,
            "quote_hit_rate": (
                document_verified_quotes / document_candidate_quotes
                if document_candidate_quotes
                else None
            ),
            "window_count": len(windows),
            "window_errors": window_errors,
            "truncated_characters": truncated_chars,
            "rejections": invalid_details[:50],
            "single_reviewer_confidence_not_calibrated": True,
        }
        progress("persist", document.pub_id)
        inspection_pub_id, created = sink.persist(
            context=context,
            record=PageInspectionRecord(
                source_document_pub_id=document.pub_id,
                content_sha256=document.text_sha256,
                status=status,
                page_summary=page_summary,
                transmission=transmission,
                attribution=attribution,
                quality=quality,
                findings=findings,
                model=model,
                prompt_version=item.prompt_version,
                policy_version=item.policy_version,
            ),
        )
        if created:
            result.inspected.append(
                InspectedPage(
                    source_document_pub_id=document.pub_id,
                    inspection_pub_id=inspection_pub_id,
                    status=status,
                    finding_count=len(findings),
                )
            )
        else:
            result.skipped_documents += 1
    if not context.documents:
        result.skipped = "no_source_documents"
    return result


async def run_page_inspection(
    item: PageInspectionInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    loader: PageInspectionLoader,
    text_store: SourceTextStore,
    sink: PageInspectionSink,
    judge: PageInspectionJudge | None = None,
    max_documents: int = 500,
    max_chars: int = 120_000,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> PageInspectionResult:
    progress: dict[str, str] = {"stage": "start", "subject": ""}

    def on_progress(stage: str, subject: str) -> None:
        progress["stage"] = stage
        progress["subject"] = subject

    def blocking() -> PageInspectionResult:
        return execute_page_inspection(
            item,
            enabled=enabled,
            llm=llm,
            loader=loader,
            text_store=text_store,
            sink=sink,
            judge=judge,
            max_documents=max_documents,
            max_chars=max_chars,
            on_progress=on_progress,
        )

    if judge is not None:
        if heartbeat:
            heartbeat({"run_pub_id": item.run_pub_id, **progress})
        return blocking()
    thread = asyncio.ensure_future(asyncio.to_thread(blocking))
    while True:
        if heartbeat:
            heartbeat({"run_pub_id": item.run_pub_id, **progress})
        done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
        if done:
            return thread.result()


@activity.defn(name="inspect_run_source_pages")
async def inspect_run_source_pages(item: PageInspectionInput) -> PageInspectionResult:
    raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
    enabled = raw_enabled not in {"0", "false", "no", "off"}
    if not enabled:
        return PageInspectionResult(disabled=True)
    settings = get_settings()
    llm = audit_llm_config_from_settings(settings)
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    dsn = _postgres_dsn()
    return await run_page_inspection(
        item,
        enabled=True,
        llm=llm,
        loader=_PostgresPageInspectionLoader(dsn),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresPageInspectionSink(dsn),
        max_documents=clamp_max_documents(os.getenv(ENV_MAX_DOCUMENTS)),
        max_chars=clamp_max_chars(os.getenv(ENV_MAX_CHARS)),
        heartbeat=activity.heartbeat,
    )


__all__ = [
    "AnalysisWindow",
    "InspectionDocument",
    "InspectedPage",
    "LinkedAnswer",
    "PAGE_INSPECTION_POLICY_VERSION",
    "PROMPT_VERSION",
    "PageCandidateBatch",
    "PageInspectionInput",
    "PageInspectionJudge",
    "PageInspectionRecord",
    "PageInspectionResult",
    "RunPageInspectionContext",
    "bind_attribution_candidate_to_window",
    "bind_finding_candidate_to_window",
    "build_analysis_windows",
    "build_attribution",
    "build_transmission_finding",
    "compute_transmission",
    "execute_page_inspection",
    "inspect_run_source_pages",
    "page_stats",
    "run_page_inspection",
]
