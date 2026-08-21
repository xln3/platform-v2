"""Hard validation for page-hazard findings and their evidence chains.

The LLM is only a candidate generator.  This module owns the delivery boundary:
quotes must resolve to exact character spans, absence claims must carry a
recomputable search expression, statement/exposure ledgers stay separate, and
self-check/motive-language failures invalidate the whole finding.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

HAZARD_CODES = frozenset(
    {"A0", "A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "C1", "C2", "C3", "C4"}
)
PAGE_INSPECTION_POLICY_VERSION = "page-inspection-v1"
PAGE_INSPECTION_PROMPT_VERSION = "page-hazard-evidence-v1"
STATEMENT_CODES = frozenset({"A0", "A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3"})
EXPOSURE_CODES = frozenset({"C1", "C2", "C3", "C4"})
FACT_TYPES = frozenset({"source_quote", "authority_fact", "recomputable", "absence"})
CONNECTORS = frozenset({"because", "and", "but", "compared_with", "therefore"})

_MOTIVE_RE = re.compile(r"刻意|恶意|故意|雇佣|水军")
_EXPOSURE_LEGAL_RE = re.compile(r"拉踩|抹黑|诋毁|打压")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceAnalysisProfile:
    """Immutable profile snapshot used by one inspection version."""

    pub_id: str
    object_name: str
    object_kind: Literal["brand", "product"]
    categories: tuple[str, ...]
    aliases: tuple[str, ...]
    own_domains: tuple[str, ...]
    peers: tuple[str, ...]
    anchor_sources: tuple[Mapping[str, Any], ...]
    linked_entities: tuple[Mapping[str, Any], ...]
    hard_anchor_available: bool
    decision_mode: Literal["selection", "reputation"]
    profile_type: Literal["I", "II", "III", "IV"]
    profile_hash: str

    @property
    def object_terms(self) -> tuple[str, ...]:
        return _unique_terms((self.object_name, *self.aliases))

    @property
    def anchor_names(self) -> frozenset[str]:
        return frozenset(
            str(item.get("name") or "").strip().casefold()
            for item in self.anchor_sources
            if str(item.get("name") or "").strip()
        )


@dataclass(frozen=True)
class ValidatedSpan:
    chain_ordinal: int
    quote: str
    text_start: int
    text_end: int
    quote_hash: str


@dataclass(frozen=True)
class ValidatedFinding:
    code: str
    ledger: Literal["statement", "exposure"]
    variant: str
    summary: str
    action: str
    evidence_chain: tuple[dict[str, Any], ...]
    self_check: dict[str, Any]
    spans: tuple[ValidatedSpan, ...]
    finding_status: Literal["confirmed", "needs_review"]


@dataclass(frozen=True)
class FindingValidation:
    finding: ValidatedFinding | None
    errors: tuple[str, ...]
    candidate_quote_count: int
    verified_quote_count: int


def _unique_terms(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = value.strip()
        folded = cleaned.casefold()
        if cleaned and folded not in seen:
            seen.add(folded)
            out.append(cleaned)
    return tuple(out)


def derive_profile_type(
    *, hard_anchor_available: bool, decision_mode: Literal["selection", "reputation"]
) -> Literal["I", "II", "III", "IV"]:
    if decision_mode == "selection":
        return "I" if hard_anchor_available else "II"
    return "III" if hard_anchor_available else "IV"


def profile_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(canonical).hexdigest()


def derive_page_inspection_version(
    *, profile_revision: int, model: str, prompt_version: str
) -> str:
    """Derive one immutable analyzer version from every interpretation input."""

    spec = f"{model.strip()}|{prompt_version.strip()}"
    spec_hash = sha256(spec.encode()).hexdigest()[:12]
    return f"{PAGE_INSPECTION_POLICY_VERSION}-r{profile_revision}-{spec_hash}"


def _all_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_all_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return "\n".join(_all_text(item) for item in value)
    return ""


def _exact_occurrences(text: str, quote: str) -> list[int]:
    starts: list[int] = []
    cursor = 0
    while quote and (position := text.find(quote, cursor)) >= 0:
        starts.append(position)
        cursor = position + max(1, len(quote))
    return starts


def _resolve_quote(
    *, source_text: str, quote: str, occurrence: object, chain_ordinal: int
) -> tuple[ValidatedSpan | None, str | None]:
    if not quote:
        return None, "source_quote.quote 为空"
    positions = _exact_occurrences(source_text, quote)
    if not positions:
        return None, "source_quote.quote 不是正文逐字子串"
    if isinstance(occurrence, bool) or not isinstance(occurrence, int):
        if len(positions) != 1:
            return None, "source_quote.quote 在正文重复且未给 occurrence"
        occurrence_index = 1
    else:
        occurrence_index = occurrence
    if occurrence_index < 1 or occurrence_index > len(positions):
        return None, "source_quote.occurrence 越界"
    start = positions[occurrence_index - 1]
    end = start + len(quote)
    if source_text[start:end] != quote:
        return None, "source_quote 字符区间回校验失败"
    return (
        ValidatedSpan(
            chain_ordinal=chain_ordinal,
            quote=quote,
            text_start=start,
            text_end=end,
            quote_hash=sha256(quote.encode()).hexdigest(),
        ),
        None,
    )


def validate_exact_interval(
    *, text: str, quote: str, start: int, end: int, quote_hash: str
) -> bool:
    """Shared persona-scan-compatible exact-span gate.

    Page findings and W contributions both use this one byte-for-byte rule.  A
    normalized/fuzzy match is never silently promoted to exact evidence.
    """

    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start >= 0
        and end > start
        and end <= len(text)
        and text[start:end] == quote
        and sha256(quote.encode()).hexdigest() == quote_hash
    )


def _validate_authority_fact(link: Mapping[str, Any], profile: SourceAnalysisProfile) -> str | None:
    required = ("authority_source", "authority_url", "publisher", "published_at")
    if any(not str(link.get(key) or "").strip() for key in required):
        return "authority_fact 缺 authority_source/url/publisher/published_at"
    source = str(link["authority_source"]).strip().casefold()
    if source not in profile.anchor_names:
        return "authority_fact 不在对象档案 anchor_sources 中"
    return None


def _validate_recomputable(link: Mapping[str, Any]) -> str | None:
    if not str(link.get("algorithm") or "").strip():
        return "recomputable 缺 algorithm"
    inputs = link.get("inputs")
    if not isinstance(inputs, Mapping | list) or not inputs:
        return "recomputable 缺可复算 inputs"
    if "result" not in link:
        return "recomputable 缺 result"
    return None


def _validate_absence(link: Mapping[str, Any], source_text: str) -> str | None:
    if link.get("search_scope") != "source_document_body":
        return "absence.search_scope 仅允许 source_document_body"
    terms = link.get("search_terms")
    if (
        not isinstance(terms, list)
        or not terms
        or any(not isinstance(term, str) or not term.strip() for term in terms)
    ):
        return "absence 缺明确 search_terms"
    operator = link.get("operator", "any")
    if operator not in {"any", "all"}:
        return "absence.operator 非法"
    counts = {term: source_text.count(term) for term in terms}
    match_count = sum(counts.values())
    claimed = link.get("match_count")
    if isinstance(claimed, bool) or not isinstance(claimed, int):
        return "absence 缺整数 match_count"
    if claimed != match_count:
        return "absence.match_count 与程序复算不一致"
    if operator == "any" and match_count != 0:
        return "absence(any) 的正文命中不为 0"
    if operator == "all" and all(counts[term] > 0 for term in terms):
        return "absence(all) 的全部检索词均已命中"
    return None


def validate_finding(
    candidate: Mapping[str, Any],
    *,
    source_text: str,
    profile: SourceAnalysisProfile,
) -> FindingValidation:
    """Validate one LLM candidate; any hard-rule failure voids the whole chain."""

    errors: list[str] = []
    code = str(candidate.get("code") or "").strip().upper()
    if code not in HAZARD_CODES:
        errors.append("分型码非法")
    ledger: Literal["statement", "exposure"] = (
        "statement" if code in STATEMENT_CODES else "exposure"
    )
    supplied_ledger = str(candidate.get("ledger") or "").strip()
    if supplied_ledger != ledger:
        errors.append("分型码与账本不一致")

    candidate_text = _all_text(candidate)
    if _MOTIVE_RE.search(candidate_text):
        errors.append("证据链含不可证明的动机词")
    if ledger == "exposure" and _EXPOSURE_LEGAL_RE.search(candidate_text):
        errors.append("暴露账使用了言论/法律主张词")

    summary = str(candidate.get("summary") or "").strip()
    action = str(candidate.get("action") or "").strip()
    variant = str(candidate.get("variant") or "").strip()
    if not summary:
        errors.append("summary 为空")
    if not action:
        errors.append("action 为空")

    self_check_raw = candidate.get("self_check")
    self_check = dict(self_check_raw) if isinstance(self_check_raw, Mapping) else {}
    if self_check.get("passed") is not True:
        errors.append("自校未通过")
    if not str(self_check.get("reasoning") or "").strip():
        errors.append("自校 reasoning 为空")

    chain_raw = candidate.get("evidence_chain")
    if not isinstance(chain_raw, list) or not chain_raw:
        errors.append("证据链为空")
        chain_items: list[Mapping[str, Any]] = []
    else:
        chain_items = [item for item in chain_raw if isinstance(item, Mapping)]
        if len(chain_items) != len(chain_raw):
            errors.append("证据链含非对象环节")

    normalized_chain: list[dict[str, Any]] = []
    spans: list[ValidatedSpan] = []
    candidate_quotes = 0
    has_quote = False
    has_computable = False
    quote_texts: list[str] = []
    absence_terms: list[str] = []
    authority_categories: list[str] = []
    for ordinal, link in enumerate(chain_items, 1):
        connector = str(link.get("connector") or "").strip()
        fact_type = str(link.get("fact_type") or "").strip()
        explanation = str(link.get("explanation") or "").strip()
        if connector not in CONNECTORS:
            errors.append(f"第 {ordinal} 环节 connector 非法")
        if fact_type not in FACT_TYPES:
            errors.append(f"第 {ordinal} 环节 fact_type 非法")
            continue
        if not explanation:
            errors.append(f"第 {ordinal} 环节 explanation 为空")

        clean_link = dict(link)
        if fact_type == "source_quote":
            candidate_quotes += 1
            quote = str(link.get("quote") or "")
            span, error = _resolve_quote(
                source_text=source_text,
                quote=quote,
                occurrence=link.get("occurrence"),
                chain_ordinal=ordinal,
            )
            if error is not None:
                errors.append(f"第 {ordinal} 环节 {error}")
            elif span is not None:
                has_quote = True
                spans.append(span)
                quote_texts.append(span.quote)
                clean_link.update(
                    {
                        "quote": span.quote,
                        "text_start": span.text_start,
                        "text_end": span.text_end,
                        "quote_hash": span.quote_hash,
                    }
                )
        elif fact_type == "authority_fact":
            if error := _validate_authority_fact(link, profile):
                errors.append(f"第 {ordinal} 环节 {error}")
            category = str(link.get("authority_category") or "").strip()
            if category:
                authority_categories.append(category)
        elif fact_type == "recomputable":
            has_computable = True
            if error := _validate_recomputable(link):
                errors.append(f"第 {ordinal} 环节 {error}")
        else:
            has_computable = True
            terms = link.get("search_terms")
            if isinstance(terms, list):
                absence_terms.extend(str(term).strip() for term in terms if str(term).strip())
            if error := _validate_absence(link, source_text):
                errors.append(f"第 {ordinal} 环节 {error}")
        normalized_chain.append(clean_link)

    if code in STATEMENT_CODES and not has_quote:
        errors.append("言论账结论没有正文逐字证据")
    if code in {"C1", "C2", "C3"} and not has_computable:
        errors.append("暴露分型没有可复算数或缺席检索式")
    if code == "C4" and not has_quote:
        errors.append("C4 没有素材重合原文")

    object_terms = profile.object_terms
    object_present = any(term in source_text for term in object_terms)
    peer_present = any(peer in source_text for peer in profile.peers)
    if code.startswith("A") and not any(
        term in quote for quote in quote_texts for term in object_terms
    ):
        errors.append("A 组原文没有逐字指向对象")
    if code.startswith("B") and not any(
        peer in quote for quote in quote_texts for peer in profile.peers
    ):
        errors.append("B 组原文没有逐字指向同位对手")
    if code == "B2":
        if not authority_categories:
            errors.append("B2 缺锚所属品类")
        elif any(category in profile.categories for category in authority_categories):
            errors.append("B2 锚所属品类与本题品类未形成跨品类")
    if code == "C1":
        if object_present:
            errors.append("C1 页面正文实际已提及对象")
        if not peer_present:
            errors.append("C1 页面正文没有同位对手在场")
        if not any(term in object_terms for term in absence_terms):
            errors.append("C1 缺席检索式没有使用档案中的对象名/别名")
    if code in {"C2", "C3"} and not object_present:
        errors.append(f"{code} 页面正文未提及对象")

    if errors:
        return FindingValidation(
            finding=None,
            errors=tuple(errors),
            candidate_quote_count=candidate_quotes,
            verified_quote_count=len(spans),
        )

    finding_status: Literal["confirmed", "needs_review"] = (
        "needs_review" if code in {"A0", "A5", "C4"} else "confirmed"
    )
    return FindingValidation(
        finding=ValidatedFinding(
            code=code,
            ledger=ledger,
            variant=variant,
            summary=summary,
            action=action,
            evidence_chain=tuple(normalized_chain),
            self_check=self_check,
            spans=tuple(spans),
            finding_status=finding_status,
        ),
        errors=(),
        candidate_quote_count=candidate_quotes,
        verified_quote_count=len(spans),
    )


def validate_sha256(value: str) -> bool:
    """Small shared guard for persistence adapters."""

    return bool(_HASH_RE.fullmatch(value))


__all__ = [
    "CONNECTORS",
    "EXPOSURE_CODES",
    "FACT_TYPES",
    "FindingValidation",
    "HAZARD_CODES",
    "PAGE_INSPECTION_POLICY_VERSION",
    "PAGE_INSPECTION_PROMPT_VERSION",
    "STATEMENT_CODES",
    "SourceAnalysisProfile",
    "ValidatedFinding",
    "ValidatedSpan",
    "validate_exact_interval",
    "derive_profile_type",
    "derive_page_inspection_version",
    "profile_fingerprint",
    "validate_finding",
    "validate_sha256",
]
