"""Evidence-bounded W (Weighted Content Chunk) candidate generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from domain.source_analysis.page_inspection import validate_exact_interval

POLICY_VERSION = "content-contribution-exact-v1"
PROMPT_VERSION = "none-deterministic-v1"
ALGORITHM_VERSION = "exact-sentence-and-citation-v1"

_ANSWER_SEGMENT_RE = re.compile(r"[^\n。！？!?]+[。！？!?]?", re.UNICODE)
_LEADING_MARKUP_RE = re.compile(r"^(?:\s*(?:[-*+] |\d+[.)、]\s*|#{1,6}\s*))+")


@dataclass(frozen=True, slots=True)
class ContributionChunk:
    source_text_start: int
    source_text_end: int
    source_quote: str
    source_quote_hash: str
    answer_text_start: int | None
    answer_text_end: int | None
    answer_quote: str | None
    answer_quote_hash: str | None
    basis: str
    contribution_score: float
    confidence: float


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def exact_answer_chunks(
    *, source_text: str, answer_text: str, minimum_characters: int = 12
) -> tuple[ContributionChunk, ...]:
    """Find exact answer sentences in a source snapshot, preserving offsets."""

    chunks: list[ContributionChunk] = []
    seen: set[tuple[int, int, int, int]] = set()
    for match in _ANSWER_SEGMENT_RE.finditer(answer_text):
        raw = match.group(0)
        left_trim = len(raw) - len(raw.lstrip())
        cleaned = raw.strip()
        markup = _LEADING_MARKUP_RE.match(cleaned)
        markup_length = len(markup.group(0)) if markup else 0
        quote = cleaned[markup_length:].strip()
        if len(quote) < minimum_characters:
            continue
        answer_start = match.start() + left_trim + markup_length
        # Account for whitespace after a markdown prefix.
        while answer_start < len(answer_text) and answer_text[answer_start].isspace():
            answer_start += 1
        answer_end = answer_start + len(quote)
        if answer_text[answer_start:answer_end] != quote:
            continue
        source_start = source_text.find(quote)
        if source_start < 0:
            continue
        source_end = source_start + len(quote)
        identity = (source_start, source_end, answer_start, answer_end)
        if identity in seen:
            continue
        seen.add(identity)
        source_hash = _hash(quote)
        answer_hash = _hash(quote)
        if not validate_exact_interval(
            text=source_text,
            quote=quote,
            start=source_start,
            end=source_end,
            quote_hash=source_hash,
        ) or not validate_exact_interval(
            text=answer_text,
            quote=quote,
            start=answer_start,
            end=answer_end,
            quote_hash=answer_hash,
        ):
            continue
        chunks.append(
            ContributionChunk(
                source_text_start=source_start,
                source_text_end=source_end,
                source_quote=quote,
                source_quote_hash=source_hash,
                answer_text_start=answer_start,
                answer_text_end=answer_end,
                answer_quote=quote,
                answer_quote_hash=answer_hash,
                basis="verbatim",
                contribution_score=min(0.95, 0.5 + len(quote) / 500),
                confidence=0.99,
            )
        )
    return tuple(chunks)


def explicit_citation_chunk(
    *, source_text: str, cited_text: str | None
) -> ContributionChunk | None:
    """Build W only when a platform citation carries an exact source fragment."""

    quote = (cited_text or "").strip()
    if len(quote) < 4:
        return None
    start = source_text.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    quote_hash = _hash(quote)
    if not validate_exact_interval(
        text=source_text, quote=quote, start=start, end=end, quote_hash=quote_hash
    ):
        return None
    return ContributionChunk(
        source_text_start=start,
        source_text_end=end,
        source_quote=quote,
        source_quote_hash=quote_hash,
        answer_text_start=None,
        answer_text_end=None,
        answer_quote=None,
        answer_quote_hash=None,
        basis="explicit_citation",
        contribution_score=0.65,
        confidence=0.95,
    )


def validate_chunk(chunk: ContributionChunk, *, source_text: str, answer_text: str) -> bool:
    if not validate_exact_interval(
        text=source_text,
        quote=chunk.source_quote,
        start=chunk.source_text_start,
        end=chunk.source_text_end,
        quote_hash=chunk.source_quote_hash,
    ):
        return False
    if chunk.answer_quote is None:
        return (
            chunk.answer_text_start is None
            and chunk.answer_text_end is None
            and chunk.answer_quote_hash is None
        )
    if (
        chunk.answer_text_start is None
        or chunk.answer_text_end is None
        or chunk.answer_quote_hash is None
    ):
        return False
    return validate_exact_interval(
        text=answer_text,
        quote=chunk.answer_quote,
        start=chunk.answer_text_start,
        end=chunk.answer_text_end,
        quote_hash=chunk.answer_quote_hash,
    )


__all__ = [
    "ALGORITHM_VERSION",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "ContributionChunk",
    "exact_answer_chunks",
    "explicit_citation_chunk",
    "validate_chunk",
]
