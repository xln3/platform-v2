"""UVW capture facts shared by browser adapters and the persistence boundary.

The module deliberately separates a normalized URL *identity* from every raw
candidate occurrence.  Callers may aggregate identities later, but they must
never use that aggregation to discard a platform-observed search result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from domain.scoring.analyzer import canonicalize_url

Observation = Literal["observed", "partial", "unobserved"]

OBSERVATIONS = frozenset({"observed", "partial", "unobserved"})
URL_NORMALIZATION_VERSION = "citation-normalizer-v1"


@dataclass(frozen=True, slots=True)
class UvwOccurrence:
    occurrence_ordinal: int
    retrieval_event_ordinal: int | None
    query: str | None
    raw_url: str
    canonical_url: str
    host: str
    u_state: str
    u_rank: int | None
    v_state: str
    v_open_order: int | None
    final_reference_state: str
    final_reference_ordinal: int | None
    w_state: str
    title: str | None
    summary: str | None


def _http_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("UVW URL must be a string")
    url = value.strip()
    if not url or len(url) > 8_192:
        raise ValueError("UVW URL length is invalid")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("UVW URL must use HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("UVW URL must not contain credentials")
    return url


def _observation(value: Any, *, field: str) -> Observation:
    if value not in OBSERVATIONS:
        raise ValueError(f"{field} observation is invalid")
    return cast(Observation, value)


def _positive_ordinal(value: Any, *, fallback: int, field: str) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} ordinal is invalid")
    return int(value)


def _text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("UVW text field is invalid")
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _url_rows(items: Any, *, rank_field: str) -> list[dict[str, Any]]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError("UVW stage rows must be an array")
    rows: list[dict[str, Any]] = []
    # There is intentionally no collection-size cap here.  Backpressure belongs
    # at the queue/transaction batching boundary, not at the fact boundary.
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("UVW stage row must be an object")
        try:
            url = _http_url(item.get("url"))
        except ValueError:
            # Some platform cards expose only a title/document id.  Keep those
            # in raw trace evidence, but do not invent a URL identity for them.
            continue
        rank = item.get(rank_field, item.get("rank", item.get("ordinal")))
        rows.append(
            {
                "url": url,
                rank_field: _positive_ordinal(rank, fallback=index, field=rank_field),
                "title": _text(item.get("title"), limit=500),
                "summary": _text(item.get("summary", item.get("cited_text")), limit=20_000),
            }
        )
    return rows


def normalize_retrieval_events(items: Any) -> list[dict[str, Any]]:
    """Validate retrieval events without deduplicating events or URL rows."""

    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError("retrieval_events must be an array")
    normalized: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("retrieval event must be an object")
        ordinal = _positive_ordinal(item.get("ordinal"), fallback=index, field="event")
        if ordinal in seen_ordinals:
            raise ValueError("retrieval event ordinal is duplicated")
        seen_ordinals.add(ordinal)
        raw_queries = item.get("queries")
        if raw_queries is None:
            query = item.get("query")
            raw_queries = [] if query is None else [query]
        if not isinstance(raw_queries, list):
            raise ValueError("retrieval event queries must be an array")
        queries: list[str] = []
        for raw_query in raw_queries:
            query = _text(raw_query, limit=2_000)
            if query is not None:
                # Repeated queries are capture facts too; do not set-deduplicate.
                queries.append(query)
        normalized.append(
            {
                "ordinal": ordinal,
                "queries": queries,
                "u_observation": _observation(item.get("u_observation", "unobserved"), field="U"),
                "v_observation": _observation(item.get("v_observation", "unobserved"), field="V"),
                "final_reference_observation": _observation(
                    item.get("final_reference_observation", "unobserved"),
                    field="final reference",
                ),
                "candidates": _url_rows(item.get("candidates"), rank_field="u_rank"),
                "opened_pages": _url_rows(item.get("opened_pages"), rank_field="v_open_order"),
                "final_references": _url_rows(
                    item.get("final_references"), rank_field="final_reference_ordinal"
                ),
                "evidence_relation": _text(item.get("evidence_relation"), limit=80),
            }
        )
    return normalized


def retrieval_events_from_trace(trace: Any) -> list[dict[str, Any]]:
    """Project the five adapters' common trace vocabulary into UV facts.

    Trace evidence may itself be presentation-truncated.  Live adapters should
    pass their untruncated structured events directly; this helper is also the
    honest compatibility path for DOM adapters and historical fixtures.
    """

    if not isinstance(trace, dict):
        return []
    blocks = trace.get("search_blocks")
    blocks = blocks if isinstance(blocks, list) else []
    opened_present = "opened_pages_observed" in trace
    opened_observed = trace.get("opened_pages_observed") is True
    opened = trace.get("opened_pages") if isinstance(trace.get("opened_pages"), list) else []
    references_present = "answer_reference_pages" in trace
    references = (
        trace.get("answer_reference_pages")
        if isinstance(trace.get("answer_reference_pages"), list)
        else []
    )
    events: list[dict[str, Any]] = []
    if blocks:
        for index, block in enumerate(blocks, 1):
            if not isinstance(block, dict):
                continue
            events.append(
                {
                    "ordinal": index,
                    "queries": block.get("queries")
                    if isinstance(block.get("queries"), list)
                    else [],
                    "u_observation": "observed",
                    "v_observation": "observed" if opened_observed else "unobserved",
                    "final_reference_observation": (
                        "observed" if references_present else "unobserved"
                    ),
                    "candidates": block.get("results")
                    if isinstance(block.get("results"), list)
                    else [],
                    # Open/final stages describe the answer rather than a search
                    # block.  Attach them once; occurrence matching spans events.
                    "opened_pages": opened if index == 1 else [],
                    "final_references": references if index == 1 else [],
                    "evidence_relation": "answer_sse_trace",
                }
            )
    else:
        events.append(
            {
                "ordinal": 1,
                "queries": [],
                "u_observation": "unobserved",
                "v_observation": (
                    "observed" if opened_present and opened_observed else "unobserved"
                ),
                "final_reference_observation": ("observed" if references_present else "unobserved"),
                "candidates": [],
                "opened_pages": opened,
                "final_references": references,
                "evidence_relation": "answer_sse_trace",
            }
        )
    return normalize_retrieval_events(events)


def retrieval_events_from_trace_path(path: str | Path | None) -> list[dict[str, Any]]:
    """Best-effort compatibility reader for an adapter's already-written trace."""

    if path is None:
        return []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return []
    return retrieval_events_from_trace(payload)


def legacy_reference_event(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Represent legacy final references without fabricating historical U or V."""

    return normalize_retrieval_events(
        [
            {
                "ordinal": 1,
                "queries": [],
                "u_observation": "unobserved",
                "v_observation": "unobserved",
                "final_reference_observation": "observed",
                "candidates": [],
                "opened_pages": [],
                "final_references": [
                    {
                        "url": row.get("url"),
                        "title": row.get("title"),
                        "summary": row.get("cited_text"),
                        "final_reference_ordinal": row.get("ordinal", index),
                    }
                    for index, row in enumerate(citations, 1)
                    if isinstance(row, dict)
                ],
            }
        ]
    )


def citation_text_for_reference(
    citations: Any, *, canonical_url: str, final_reference_ordinal: int | None
) -> str | None:
    """Resolve an exact platform citation quote without using search summaries.

    URL plus displayed ordinal is authoritative.  A URL-only fallback is safe
    only when it identifies one citation row; repeated URL references remain
    ambiguous rather than choosing an arbitrary quote.
    """

    if not isinstance(citations, list):
        return None
    matches: list[str] = []
    for fallback_ordinal, citation in enumerate(citations, 1):
        if not isinstance(citation, dict):
            continue
        quote = citation.get("cited_text")
        url = citation.get("url")
        if not isinstance(quote, str) or not quote.strip() or not isinstance(url, str):
            continue
        try:
            same_url = canonicalize_url(url) == canonical_url
        except (TypeError, ValueError):
            same_url = False
        if not same_url:
            continue
        ordinal = citation.get("ordinal", fallback_ordinal)
        if (
            isinstance(ordinal, int)
            and not isinstance(ordinal, bool)
            and ordinal == final_reference_ordinal
        ):
            return quote.strip()
        matches.append(quote.strip())
    return matches[0] if len(matches) == 1 else None


def occurrence_rows(events: list[dict[str, Any]]) -> list[UvwOccurrence]:
    """Expand events into lossless U occurrences plus honest later-stage rows."""

    events = normalize_retrieval_events(events)
    occurrences: list[dict[str, Any]] = []
    opened: list[tuple[int, dict[str, Any]]] = []
    references: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        query = event["queries"][0] if event["queries"] else None
        for candidate in event["candidates"]:
            occurrences.append(
                {
                    "event": event,
                    "query": query,
                    "raw_url": candidate["url"],
                    "canonical_url": canonicalize_url(candidate["url"]),
                    "u_state": "observed",
                    "u_rank": candidate["u_rank"],
                    "v_state": (
                        "not_entered" if event["v_observation"] == "observed" else "unobserved"
                    ),
                    "v_open_order": None,
                    "final_reference_state": (
                        "not_referenced"
                        if event["final_reference_observation"] == "observed"
                        else "unobserved"
                    ),
                    "final_reference_ordinal": None,
                    "w_state": (
                        "no_evidence" if event["v_observation"] == "observed" else "unobserved"
                    ),
                    "title": candidate["title"],
                    "summary": candidate["summary"],
                }
            )
        opened.extend((event["ordinal"], row) for row in event["opened_pages"])
        references.extend((event["ordinal"], row) for row in event["final_references"])

    def _match_or_append(
        event_ordinal: int, row: dict[str, Any], *, stage: Literal["v", "final"]
    ) -> dict[str, Any]:
        canonical = canonicalize_url(row["url"])
        stage_key = "v_open_order" if stage == "v" else "final_reference_ordinal"
        # Structured events own their V rows.  Prefer the matching event so a
        # URL repeated by earlier/later queries cannot receive the wrong stage
        # state.  The cross-event fallback remains for compatibility traces
        # whose answer-level reference list has no event linkage.
        for occurrence in occurrences:
            if (
                occurrence["event"]["ordinal"] == event_ordinal
                and occurrence["canonical_url"] == canonical
                and occurrence[stage_key] is None
            ):
                return occurrence
        for occurrence in occurrences:
            if occurrence["canonical_url"] == canonical and occurrence[stage_key] is None:
                return occurrence
        event = next((value for value in events if value["ordinal"] == event_ordinal), events[0])
        created = {
            "event": event,
            "query": event["queries"][0] if event["queries"] else None,
            "raw_url": row["url"],
            "canonical_url": canonical,
            "u_state": "unobserved",
            "u_rank": None,
            "v_state": "unobserved",
            "v_open_order": None,
            "final_reference_state": "unobserved",
            "final_reference_ordinal": None,
            "w_state": "unobserved",
            "title": row["title"],
            "summary": row["summary"],
        }
        occurrences.append(created)
        return created

    for event_ordinal, row in opened:
        occurrence = _match_or_append(event_ordinal, row, stage="v")
        occurrence["v_state"] = "entered"
        occurrence["v_open_order"] = row["v_open_order"]
        occurrence["w_state"] = "pending"
        occurrence["title"] = occurrence["title"] or row["title"]
        occurrence["summary"] = occurrence["summary"] or row["summary"]
    for event_ordinal, row in references:
        occurrence = _match_or_append(event_ordinal, row, stage="final")
        occurrence["final_reference_state"] = "referenced"
        occurrence["final_reference_ordinal"] = row["final_reference_ordinal"]
        occurrence["title"] = occurrence["title"] or row["title"]
        occurrence["summary"] = occurrence["summary"] or row["summary"]

    result: list[UvwOccurrence] = []
    for index, row in enumerate(occurrences, 1):
        host = (urlsplit(row["canonical_url"]).hostname or "").lower().rstrip(".")
        result.append(
            UvwOccurrence(
                occurrence_ordinal=index,
                retrieval_event_ordinal=row["event"]["ordinal"],
                query=row["query"],
                raw_url=row["raw_url"],
                canonical_url=row["canonical_url"],
                host=host,
                u_state=row["u_state"],
                u_rank=row["u_rank"],
                v_state=row["v_state"],
                v_open_order=row["v_open_order"],
                final_reference_state=row["final_reference_state"],
                final_reference_ordinal=row["final_reference_ordinal"],
                # W is not a capture fact.  A later, versioned exact-span job
                # may promote this state; a final citation alone never does.
                w_state=row["w_state"],
                title=row["title"],
                summary=row["summary"],
            )
        )
    return result


__all__ = [
    "OBSERVATIONS",
    "URL_NORMALIZATION_VERSION",
    "UvwOccurrence",
    "citation_text_for_reference",
    "legacy_reference_event",
    "normalize_retrieval_events",
    "occurrence_rows",
    "retrieval_events_from_trace",
    "retrieval_events_from_trace_path",
]
