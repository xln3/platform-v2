"""Deterministic publication metadata extraction for cited web sources."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

SOURCE_METADATA_PARSER_VERSION = "source-metadata-v1"

_PUBLISHED_META_KEYS = (
    "article:published_time",
    "og:published_time",
    "datepublished",
    "date_published",
    "publishdate",
    "pubdate",
    "publish_time",
    "published_time",
)
_MODIFIED_META_KEYS = (
    "article:modified_time",
    "og:updated_time",
    "datemodified",
    "last-modified",
)
_DATE_ONLY_RE = re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?$")
_DATE_TIME_RE = re.compile(
    r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?"
    r"(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
    r"(?:\s*(Z|[+-]\d{2}:?\d{2}))?$",
    re.IGNORECASE,
)
_URL_DATE_RE = re.compile(r"(?:^|/)(20\d{2})[/-](0?[1-9]|1[0-2])[/-]([0-2]?\d|3[01])(?:/|$)")


@dataclass(frozen=True, slots=True)
class DateCandidate:
    kind: str
    raw: str
    parsed_at: datetime | None
    source: str
    precision: str | None
    timezone: str | None
    confidence: str
    context: str | None = None

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["parsed_at"] = self.parsed_at.isoformat() if self.parsed_at is not None else None
        return value


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    canonical_url: str | None = None
    title: str | None = None
    site_name: str | None = None
    publisher: str | None = None
    authors: tuple[str, ...] = ()
    language: str | None = None
    published_at_raw: str | None = None
    published_at: datetime | None = None
    published_at_timezone: str | None = None
    published_at_precision: str | None = None
    published_at_source: str | None = None
    published_at_confidence: str = "unknown"
    modified_at: datetime | None = None
    candidates: tuple[DateCandidate, ...] = ()
    parser_version: str = SOURCE_METADATA_PARSER_VERSION

    def candidates_json(self) -> list[dict[str, Any]]:
        return [candidate.to_json() for candidate in self.candidates]


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.in_json_ld = False
        self.json_ld_parts: list[str] = []
        self.json_ld_documents: list[str] = []
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.times: list[dict[str, str]] = []
        self._time_stack: list[int] = []
        self.language: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): str(value or "").strip() for key, value in attrs}
        if tag == "html" and values.get("lang"):
            self.language = values["lang"][:40]
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            self.meta.append(values)
        elif tag == "link":
            self.links.append(values)
        elif tag == "time":
            self.times.append({**values, "text": ""})
            self._time_stack.append(len(self.times) - 1)
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.in_json_ld = True
            self.json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "time" and self._time_stack:
            self._time_stack.pop()
        elif tag == "script" and self.in_json_ld:
            self.in_json_ld = False
            document = "".join(self.json_ld_parts).strip()
            if document:
                self.json_ld_documents.append(document)
            self.json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_json_ld:
            self.json_ld_parts.append(data)
        if self._time_stack:
            self.times[self._time_stack[-1]]["text"] += data


def extract_source_metadata(
    html: str,
    *,
    final_url: str,
    response_headers: dict[str, str] | None = None,
    observed_at: datetime | None = None,
) -> SourceMetadata:
    """Extract all date candidates and select one with an auditable precedence."""

    parser = _MetadataParser()
    try:
        parser.feed(html[:5_000_000])
        parser.close()
    except Exception:
        # Broken HTML may still leave useful metadata collected before the error.
        pass
    observed = observed_at or datetime.now(UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    headers = {key.lower(): value for key, value in (response_headers or {}).items()}
    candidates: list[DateCandidate] = []
    json_ld_values: dict[str, list[str]] = {"published": [], "modified": []}
    json_ld_names: list[str] = []
    json_ld_authors: list[str] = []
    json_ld_publishers: list[str] = []
    for document in parser.json_ld_documents:
        try:
            payload = json.loads(document)
        except (TypeError, ValueError):
            continue
        _collect_json_ld(
            payload,
            values=json_ld_values,
            names=json_ld_names,
            authors=json_ld_authors,
            publishers=json_ld_publishers,
        )
    for raw in json_ld_values["published"]:
        candidates.append(_candidate("published", raw, "jsonld.datePublished", "structured_only"))
    for raw in json_ld_values["modified"]:
        candidates.append(_candidate("modified", raw, "jsonld.dateModified", "structured_only"))

    title: str | None = _clean_text(" ".join(parser.title_parts), 500)
    site_name: str | None = None
    publisher: str | None = json_ld_publishers[0] if json_ld_publishers else None
    authors = list(json_ld_authors)
    canonical_url: str | None = None
    for item in parser.meta:
        key = (item.get("property") or item.get("name") or item.get("itemprop") or "").lower()
        raw = item.get("content", "").strip()
        if not raw:
            continue
        if key in _PUBLISHED_META_KEYS:
            candidates.append(_candidate("published", raw, f"meta.{key}", "structured_only"))
        elif key in _MODIFIED_META_KEYS:
            candidates.append(_candidate("modified", raw, f"meta.{key}", "structured_only"))
        elif key in {"og:title", "twitter:title"} and not title:
            title = _clean_text(raw, 500)
        elif key == "og:site_name" and not site_name:
            site_name = _clean_text(raw, 300)
        elif key in {"author", "article:author", "byl"}:
            cleaned = _clean_text(raw, 300)
            if cleaned and cleaned not in authors:
                authors.append(cleaned)
        elif key in {"publisher", "article:publisher"} and not publisher:
            publisher = _clean_text(raw, 300)
        elif key == "og:url" and canonical_url is None:
            canonical_url = _resolve_http_url(raw, final_url)

    for item in parser.links:
        rel = {part.lower() for part in item.get("rel", "").split()}
        if "canonical" in rel and canonical_url is None:
            canonical_url = _resolve_http_url(item.get("href", ""), final_url)

    for item in parser.times:
        raw = item.get("datetime", "").strip()
        visible = _clean_text(item.get("text", ""), 200)
        semantic = " ".join(
            item.get(key, "") for key in ("itemprop", "class", "id", "data-testid", "aria-label")
        ).lower()
        kind = (
            "modified"
            if any(token in semantic for token in ("modified", "updated", "更新"))
            else "published"
        )
        if raw:
            candidates.append(
                _candidate(kind, raw, "time.datetime", "visible_only", context=visible)
            )
        elif visible:
            candidates.append(
                _candidate(
                    kind, visible, "time.visible_text", "visible_only", context=semantic[:200]
                )
            )

    url_match = _URL_DATE_RE.search(urlsplit(final_url).path)
    if url_match:
        raw = "-".join(url_match.groups())
        candidates.append(_candidate("published", raw, "url.path", "inferred_low"))
    if last_modified := headers.get("last-modified"):
        candidates.append(
            _candidate("modified", last_modified, "http.last_modified", "structured_only")
        )

    candidates = _deduplicate_candidates(candidates)
    published = _select_published(candidates, observed)
    modified = _select_modified(candidates, observed)
    if published is not None and published.source.startswith(("jsonld.", "meta.")):
        visible_dates = {
            candidate.parsed_at.date()
            for candidate in candidates
            if candidate.kind == "published"
            and candidate.source.startswith("time.")
            and candidate.parsed_at is not None
        }
        if published.parsed_at is not None and published.parsed_at.date() in visible_dates:
            published = DateCandidate(**{**asdict(published), "confidence": "verified_structured"})

    return SourceMetadata(
        canonical_url=canonical_url or _safe_http_url(final_url),
        title=title or (json_ld_names[0] if json_ld_names else None),
        site_name=site_name,
        publisher=publisher,
        authors=tuple(authors[:20]),
        language=parser.language,
        published_at_raw=published.raw if published else None,
        published_at=published.parsed_at if published else None,
        published_at_timezone=published.timezone if published else None,
        published_at_precision=published.precision if published else None,
        published_at_source=published.source if published else None,
        published_at_confidence=published.confidence if published else "unknown",
        modified_at=modified.parsed_at if modified else None,
        candidates=tuple(candidates),
    )


def _collect_json_ld(
    value: Any,
    *,
    values: dict[str, list[str]],
    names: list[str],
    authors: list[str],
    publishers: list[str],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_json_ld(
                item, values=values, names=names, authors=authors, publishers=publishers
            )
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        lowered = str(key).lower()
        if lowered == "datepublished" and isinstance(child, str):
            values["published"].append(child)
        elif lowered == "datemodified" and isinstance(child, str):
            values["modified"].append(child)
        elif lowered == "headline" and isinstance(child, str):
            cleaned = _clean_text(child, 500)
            if cleaned and cleaned not in names:
                names.append(cleaned)
        elif lowered == "author":
            _collect_people(child, authors)
        elif lowered == "publisher":
            _collect_people(child, publishers)
        _collect_json_ld(child, values=values, names=names, authors=authors, publishers=publishers)


def _collect_people(value: Any, output: list[str]) -> None:
    values = value if isinstance(value, list) else [value]
    for item in values:
        raw = item.get("name") if isinstance(item, dict) else item
        cleaned = _clean_text(raw, 300) if isinstance(raw, str) else None
        if cleaned and cleaned not in output:
            output.append(cleaned)


def _candidate(
    kind: str,
    raw: str,
    source: str,
    confidence: str,
    *,
    context: str | None = None,
) -> DateCandidate:
    parsed, precision, timezone = _parse_date(raw)
    return DateCandidate(
        kind=kind,
        raw=raw.strip()[:500],
        parsed_at=parsed,
        source=source,
        precision=precision,
        timezone=timezone,
        confidence=confidence if parsed is not None else "unknown",
        context=context,
    )


def _parse_date(value: str) -> tuple[datetime | None, str | None, str | None]:
    raw = value.strip()
    if not raw:
        return None, None, None
    date_only = _DATE_ONLY_RE.fullmatch(raw)
    if date_only:
        try:
            date_year, date_month, date_day = (int(part) for part in date_only.groups())
            return datetime(date_year, date_month, date_day, tzinfo=UTC), "date", "unknown"
        except ValueError:
            return None, None, None
    matched = _DATE_TIME_RE.fullmatch(raw)
    if matched:
        year, month, day, hour, minute, second, zone = matched.groups()
        precision = "second" if second is not None else "minute" if minute is not None else "date"
        normalized_zone = zone
        if zone and zone != "Z" and len(zone) == 5:
            normalized_zone = f"{zone[:3]}:{zone[3:]}"
        iso_value = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if hour is not None:
            iso_value += f"T{int(hour):02d}:{int(minute or 0):02d}:{int(second or 0):02d}"
        if normalized_zone:
            iso_value += "+00:00" if normalized_zone.upper() == "Z" else normalized_zone
        try:
            parsed = datetime.fromisoformat(iso_value)
        except ValueError:
            return None, None, None
        timezone = normalized_zone or "unknown"
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)), precision, timezone
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None, None, None
    timezone = str(parsed.tzinfo) if parsed.tzinfo is not None else "unknown"
    precision = "second" if re.search(r"\d{1,2}:\d{2}:\d{2}", raw) else "minute"
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)), precision, timezone


def _select_published(
    candidates: list[DateCandidate], observed_at: datetime
) -> DateCandidate | None:
    def rank(candidate: DateCandidate) -> int:
        if candidate.source == "jsonld.datePublished":
            return 1
        if candidate.source.startswith("meta."):
            return 2
        if candidate.source == "time.datetime":
            return 3
        if candidate.source == "time.visible_text":
            return 5
        if candidate.source == "url.path":
            return 6
        return 4

    eligible = [
        candidate
        for candidate in candidates
        if candidate.kind == "published"
        and candidate.parsed_at is not None
        and candidate.parsed_at <= observed_at + timedelta(days=1)
    ]
    return (
        min(eligible, key=lambda candidate: (rank(candidate), candidate.source))
        if eligible
        else None
    )


def _select_modified(
    candidates: list[DateCandidate], observed_at: datetime
) -> DateCandidate | None:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.kind == "modified"
        and candidate.parsed_at is not None
        and candidate.parsed_at <= observed_at + timedelta(days=1)
    ]

    def rank(candidate: DateCandidate) -> int:
        if candidate.source == "jsonld.dateModified":
            return 1
        if candidate.source.startswith("meta."):
            return 2
        if candidate.source.startswith("time."):
            return 3
        if candidate.source == "http.last_modified":
            return 4
        return 5

    return (
        min(eligible, key=lambda candidate: (rank(candidate), candidate.source))
        if eligible
        else None
    )


def _deduplicate_candidates(candidates: list[DateCandidate]) -> list[DateCandidate]:
    output: list[DateCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = (candidate.kind, candidate.raw, candidate.source)
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output[:100]


def _safe_http_url(value: str) -> str | None:
    if not value or len(value) > 2_048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _resolve_http_url(value: str, base_url: str) -> str | None:
    try:
        resolved = urljoin(base_url, value)
    except ValueError:
        return None
    return _safe_http_url(resolved)


def _clean_text(value: str, limit: int) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:limit] if cleaned else None
