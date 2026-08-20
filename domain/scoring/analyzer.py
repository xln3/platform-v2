from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from domain.metrics.core import AnswerFact

_POSITIVE = frozenset({"推荐", "优秀", "领先", "可靠", "best", "recommend", "excellent"})
_NEGATIVE = frozenset({"不推荐", "风险", "较差", "投诉", "avoid", "poor", "risk"})
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "spm", "from", "source", "ref"})


@dataclass(frozen=True, slots=True)
class CitationInput:
    url: str
    title: str | None = None
    cited_text: str | None = None
    # Customer ordinals are always one-based. ``platform_ordinal`` preserves
    # what the platform emitted, including zero-based schemes.
    ordinal: int | None = None
    platform_ordinal: int | None = None
    ordinal_base: int | None = None


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    fact: AnswerFact
    citations: tuple[dict[str, object], ...]
    input_hash: str


def analyze_answer(
    *,
    answer_pub_id: str,
    text: str,
    brand: str,
    competitors: tuple[str, ...],
    citations: tuple[CitationInput, ...],
    dimensions: dict[str, str],
    own_domains: tuple[str, ...] = (),
) -> AnalysisResult:
    normalized = _normalize(text)
    brand_position = _first_position(normalized, brand)
    rank = _extract_rank(text, brand) if brand_position is not None else None
    competitor_ranks = {
        competitor: competitor_rank
        for competitor in competitors
        if (competitor_rank := _extract_rank(text, competitor)) is not None
    }
    # Sentiment is a fact about the requested brand.  A response which never
    # mentions that brand cannot truthfully carry brand sentiment.
    sentiment = _sentiment(normalized) if brand_position is not None else "neutral"
    citation_rows_list: list[dict[str, object]] = []
    for index, citation in enumerate(citations, 1):
        ordinal, platform_ordinal, ordinal_base = _citation_ordinals(citation, index)
        citation_rows_list.append(
            {
                "ordinal": ordinal,
                "platform_ordinal": platform_ordinal,
                "ordinal_base": ordinal_base,
                "original_url": citation.url,
                "canonical_url": canonicalize_url(citation.url),
                "host": (urlsplit(citation.url).hostname or "").lower(),
                "title": citation.title,
                "cited_text": citation.cited_text,
                "own_source": any(
                    (urlsplit(citation.url).hostname or "").lower() == domain
                    or (urlsplit(citation.url).hostname or "").lower().endswith(f".{domain}")
                    for domain in own_domains
                ),
            }
        )
    citation_rows = tuple(citation_rows_list)
    canonical_urls = [str(row["canonical_url"]) for row in citation_rows]
    digest_input = "\n".join([text, brand, *competitors, *canonical_urls])
    return AnalysisResult(
        fact=AnswerFact(
            answer_pub_id=answer_pub_id,
            mentioned=brand_position is not None,
            rank=rank if brand_position is not None else None,
            sentiment=sentiment,
            # Kept unavailable until a calibrated recommendation classifier is supplied.
            recommended=None,
            competitor_ranks=competitor_ranks,
            citation_count=len(citations),
            dimensions=dimensions,
        ),
        citations=citation_rows,
        input_hash=sha256(digest_input.encode()).hexdigest(),
    )


def canonicalize_url(url: str) -> str:
    split = urlsplit(url.strip())
    # Keep the established citation-normalizer-v1 contract used by legacy
    # persisted facts: hostname only (no port), original path and query order.
    host = (split.hostname or "").lower().rstrip(".")
    netloc = host
    path = split.path or "/"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((split.scheme.lower() or "https", netloc, path, query, ""))


def _citation_ordinals(citation: CitationInput, fallback_ordinal: int) -> tuple[int, int, int]:
    if citation.ordinal is not None and (
        not isinstance(citation.ordinal, int) or isinstance(citation.ordinal, bool)
    ):
        raise ValueError("citation ordinal mapping is invalid")
    if citation.platform_ordinal is not None and (
        not isinstance(citation.platform_ordinal, int)
        or isinstance(citation.platform_ordinal, bool)
    ):
        raise ValueError("citation ordinal mapping is invalid")
    base = (
        citation.ordinal_base
        if isinstance(citation.ordinal_base, int)
        and not isinstance(citation.ordinal_base, bool)
        and citation.ordinal_base in {0, 1}
        else 1
    )
    if citation.platform_ordinal is not None:
        platform_ordinal = citation.platform_ordinal
    elif citation.ordinal is not None:
        platform_ordinal = citation.ordinal - (1 - base)
    else:
        platform_ordinal = fallback_ordinal - (1 - base)
    ordinal = citation.ordinal if citation.ordinal is not None else platform_ordinal + (1 - base)
    if (
        not isinstance(ordinal, int)
        or isinstance(ordinal, bool)
        or not isinstance(platform_ordinal, int)
        or isinstance(platform_ordinal, bool)
        or ordinal < 1
        or platform_ordinal < base
        or ordinal != platform_ordinal + (1 - base)
    ):
        raise ValueError("citation ordinal mapping is invalid")
    return ordinal, platform_ordinal, base


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def _first_position(text: str, term: str) -> int | None:
    position = text.find(term.casefold())
    return position if position >= 0 else None


def _sentiment(text: str) -> str:
    positive = sum(word in text for word in _POSITIVE)
    negative = sum(word in text for word in _NEGATIVE)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _extract_rank(text: str, brand: str) -> int | None:
    for match in re.finditer(re.escape(brand), text, flags=re.IGNORECASE):
        suffix = text[match.end() : match.end() + 14]
        explicit = re.match(
            r"^[\s，,、:：()（）]*(?:排名?|排)?\s*第"
            r"\s*([一二三四五六七八九十百\d]{1,4})\s*[名位]?",
            suffix,
        )
        if explicit:
            rank = _parse_rank(explicit.group(1))
            if rank is not None and 1 <= rank <= 999:
                return rank
    for line in text.splitlines():
        if brand.casefold() not in line.casefold():
            continue
        marker = re.match(r"^\s*\|?\s*(\d{1,3})\s*(?:[.、)）:：|\t])", line)
        if marker:
            return int(marker.group(1))
    return None


def _parse_rank(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value == "百":
        return 100
    if "百" in value:
        hundreds, remainder = value.split("百", 1)
        head = digits.get(hundreds)
        if head is None:
            return None
        tail = _parse_rank(remainder) if remainder else 0
        return head * 100 + tail if tail is not None else None
    if "十" in value:
        tens, ones = value.split("十", 1)
        head = digits.get(tens, 1) if tens else 1
        tail = digits.get(ones, 0) if ones else 0
        return head * 10 + tail
    return digits.get(value)
