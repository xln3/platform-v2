"""Service 3 V2 facts: answer-level website citation and adoption evidence chains.

This module is intentionally service-specific so the shared formal-review generator can
integrate it without coupling services 1/2 to MinIO.  It never infers hidden reasoning:
only the platform-surfaced trace stored as ``answer_sse_trace`` is reported.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from difflib import SequenceMatcher
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageStat
from psycopg.rows import dict_row

from geo_platform.analytics.service import AnalyticsService, _platform_tenant_connection
from geo_platform.brandrank import service as brandrank_service
from geo_platform.tenancy.psycopg import tenant_connection
from workflows.activities.own_site_snapshot import url_dedupe_key

MODEL_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}

_REFERENCE_SECTION_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:参考来源|参考资料|参考文献|sources?|references?)\s*[:：]?"
)
_NORMALIZED_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def _host(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text if "://" in text else f"//{text}")
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_own_host(value: object, own_site_host: object) -> bool:
    candidate = _host(value)
    official = _host(own_site_host)
    return bool(
        candidate and official and (candidate == official or candidate.endswith(f".{official}"))
    )


def answer_narrative(text: str) -> str:
    """Remove the rendered reference list before testing content reuse.

    Source titles copied into a final ``参考来源`` block prove citation, not that the
    source content influenced the narrative.  Counting those titles as adoption would inflate
    the quotation metric.
    """

    match = _REFERENCE_SECTION_RE.search(text)
    return text[: match.start()].rstrip() if match else text.rstrip()


def _normalized_with_positions(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if _NORMALIZED_CHAR_RE.fullmatch(char):
            chars.append(char.lower())
            positions.append(index)
    return "".join(chars), positions


def longest_common_evidence(left: str, right: str, *, context: int = 100) -> dict[str, Any]:
    """Return the longest normalized common span with readable original excerpts."""

    left_normalized, left_positions = _normalized_with_positions(left)
    right_normalized, right_positions = _normalized_with_positions(right)
    if not left_normalized or not right_normalized:
        return {"length": 0, "normalized_phrase": "", "left_excerpt": "", "right_excerpt": ""}
    match = SequenceMatcher(
        None, left_normalized, right_normalized, autojunk=False
    ).find_longest_match()
    if match.size <= 0:
        return {"length": 0, "normalized_phrase": "", "left_excerpt": "", "right_excerpt": ""}

    def excerpt(source: str, positions: list[int], start: int, size: int) -> str:
        raw_start = positions[start]
        raw_end = positions[start + size - 1] + 1
        clipped = source[max(0, raw_start - context) : min(len(source), raw_end + context)]
        return " ".join(clipped.split())

    return {
        "length": match.size,
        "normalized_phrase": left_normalized[match.a : match.a + match.size],
        "left_excerpt": excerpt(left, left_positions, match.a, match.size),
        "right_excerpt": excerpt(right, right_positions, match.b, match.size),
    }


def adoption_status(match_length: int, *, snapshot_available: bool) -> str:
    """Conservative deterministic V2 classification.

    20+ normalized consecutive characters are treated as confirmed direct reuse; 10--19
    characters are only weak evidence because product names and common security phrases can
    coincide independently.  No snapshot means not evaluated, regardless of cited_text.
    """

    if not snapshot_available:
        return "not_evaluated"
    if match_length >= 20:
        return "confirmed"
    if match_length >= 10:
        return "weak"
    return "no_direct_evidence"


def _asset_descriptor(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "pub_id": str(row["pub_id"]),
        "object_key": str(row["object_key"]),
        "sha256": str(row["sha256"]),
        "mime_type": str(row["mime_type"]),
        "source_url": row.get("source_url"),
        "capture_time": row.get("capture_time"),
    }


def _safe_json(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _trace_summary(trace: dict[str, Any]) -> str:
    parts: list[str] = []
    for step in trace.get("thinking_chain") or []:
        if not isinstance(step, dict):
            continue
        if step.get("kind") == "reasoning" and step.get("text"):
            parts.append(str(step["text"]))
        elif step.get("kind") == "search":
            queries = "、".join(str(value) for value in step.get("queries") or [])
            summary = str(step.get("summary") or "")
            parts.append(f"检索 {queries}{f'：{summary}' if summary else ''}")
    return " | ".join(" ".join(part.split()) for part in parts)[:1800]


def _load_answers(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    start_at = datetime.combine(start, time.min, tzinfo=UTC)
    end_at = datetime.combine(end, time.max, tzinfo=UTC)
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id,query_text,response_text,model,region,mode,capture_time,run_pub_id
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time BETWEEN %s AND %s
            ORDER BY capture_time,pub_id
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_evidence_assets(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    answer_pub_ids: list[str],
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        site_assets = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ea.pub_id,ea.object_key,ea.sha256,ea.mime_type,ea.source_url,
                       ea.capture_time
                FROM evidence.evidence_asset ea
                WHERE ea.tenant_pub_id=%s AND ea.project_pub_id=%s
                  AND ea.kind='own_site_snapshot'
                  AND ea.mime_type IN ('application/json','image/png')
                  AND ea.capture_time::date BETWEEN %s AND %s
                  AND ea.deleted_at IS NULL
                ORDER BY ea.capture_time DESC,ea.pub_id DESC
                """,
                (tenant_pub_id, project_pub_id, start, end),
            ).fetchall()
        ]
        answer_assets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if answer_pub_ids:
            rows = connection.execute(
                """
                SELECT er.from_pub_id,er.relation_type,ea.pub_id,ea.object_key,ea.sha256,
                       ea.mime_type,ea.source_url,ea.capture_time,ea.kind
                FROM evidence.evidence_relation er
                JOIN evidence.evidence_asset ea
                  ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
                WHERE er.tenant_pub_id=%s AND er.from_pub_id=ANY(%s::text[])
                  AND er.relation_type IN (
                    'answer_evidence_excerpt','answer_page','answer_sse_trace','own_site_snapshot'
                  )
                  AND ea.deleted_at IS NULL
                ORDER BY er.from_pub_id,ea.capture_time DESC,ea.pub_id DESC
                """,
                (tenant_pub_id, answer_pub_ids),
            ).fetchall()
            for row in rows:
                answer_assets[str(row["from_pub_id"])].append(dict(row))
    return site_assets, answer_assets


def _snapshot_index(
    site_assets: list[dict[str, Any]],
    blob_loader: Callable[[str, str], bytes],
    own_site_host: str | None,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in site_assets:
        if not _is_own_host(row.get("source_url"), own_site_host):
            continue
        key = url_dedupe_key(str(row.get("source_url") or ""))
        if key is None:
            continue
        entry = grouped.setdefault(key, {"url": row.get("source_url"), "text": None, "png": None})
        if row["mime_type"] == "image/png" and entry["png"] is None:
            entry["png"] = _asset_descriptor(row)
        elif row["mime_type"] == "application/json" and entry["text"] is None:
            try:
                payload = _safe_json(
                    json.loads(blob_loader(str(row["object_key"]), str(row["sha256"])))
                )
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            entry["text"] = {
                **(_asset_descriptor(row) or {}),
                "title": str(payload.get("title") or ""),
                "final_url": str(payload.get("final_url") or row.get("source_url") or ""),
                "body": str(payload.get("text") or ""),
                "fetched_at": payload.get("fetched_at"),
            }
    return grouped


def _screenshot_status(
    snapshot: dict[str, Any] | None,
    blob_loader: Callable[[str, str], bytes],
    cache: dict[str, str],
) -> str:
    descriptor = (snapshot or {}).get("png")
    if not isinstance(descriptor, dict):
        return "missing"
    digest = str(descriptor.get("sha256") or "")
    if digest in cache:
        return cache[digest]
    try:
        payload = blob_loader(str(descriptor["object_key"]), digest)
        with Image.open(BytesIO(payload)) as source:
            preview = source.convert("L")
            preview.thumbnail((192, 192))
            stats = ImageStat.Stat(preview)
        status = (
            "blank_or_low_information"
            if stats.mean[0] >= 250 and stats.stddev[0] <= 5
            else "usable"
        )
    except (KeyError, OSError, ValueError):
        status = "unavailable"
    cache[digest] = status
    return status


def _answer_asset(
    rows: list[dict[str, Any]], *, relation_type: str, mime_type: str
) -> dict[str, Any] | None:
    return next(
        (
            _asset_descriptor(row)
            for row in rows
            if row.get("relation_type") == relation_type and row.get("mime_type") == mime_type
        ),
        None,
    )


def _trace(rows: list[dict[str, Any]], blob_loader: Callable[[str, str], bytes]) -> dict[str, Any]:
    row = next(
        (
            item
            for item in rows
            if item.get("relation_type") == "answer_sse_trace"
            and item.get("mime_type") == "application/json"
        ),
        None,
    )
    if row is None:
        return {}
    try:
        return _safe_json(json.loads(blob_loader(str(row["object_key"]), str(row["sha256"]))))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _trace_urls(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    output: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            output.append(url)
    return output


def _retrieval_observability(
    *,
    model: str,
    trace: dict[str, Any],
    citations: list[dict[str, Any]],
    own_host: str | None,
) -> dict[str, Any]:
    """Describe only the retrieval stages that the platform exposed to the client.

    DeepSeek exposes candidate, opened-page and final-reference stages.  Doubao exposes
    candidate blocks and final citations but no normalized opened-page event.  Wenxin's
    DOM trace contains final reference rows only, so treating its ``search_blocks`` as a
    complete candidate list would be false precision.
    """

    candidate_urls = [
        url
        for block in trace.get("search_blocks") or []
        if isinstance(block, dict)
        for url in _trace_urls(block.get("results"))
    ]
    final_urls = [
        str(row.get("canonical_url") or row.get("original_url") or "")
        for row in citations
        if row.get("canonical_url") or row.get("original_url")
    ]
    candidate_stage_observed = bool(trace) and model in {"doubao", "deepseek"}
    opened_stage_observed = trace.get("opened_pages_observed") is True
    opened_urls = _trace_urls(trace.get("opened_pages")) if opened_stage_observed else []
    return {
        "trace_available": bool(trace),
        "search_queries_observed": any(
            isinstance(step, dict) and step.get("kind") == "search" and bool(step.get("queries"))
            for step in trace.get("thinking_chain") or []
        ),
        "candidate_stage_observed": candidate_stage_observed,
        "candidate_urls": len(candidate_urls),
        "official_candidate_observed": any(_is_own_host(url, own_host) for url in candidate_urls),
        "opened_stage_observed": opened_stage_observed,
        "opened_urls": len(opened_urls),
        "official_opened_observed": any(_is_own_host(url, own_host) for url in opened_urls),
        "final_citations": len(final_urls),
        "official_final_citation": any(_is_own_host(url, own_host) for url in final_urls),
        "platform_boundary": (
            "候选、打开、最终引用三阶段可观测"
            if model == "deepseek" and candidate_stage_observed and opened_stage_observed
            else "候选与最终引用可观测；页面打开阶段不可观测"
            if model == "doubao" and candidate_stage_observed
            else "只可观测最终引用；完整候选与页面打开阶段不可观测"
            if model == "yiyan"
            else "当前证据未形成完整检索阶段记录"
        ),
    }


def _direct_snapshot_urls(rows: list[dict[str, Any]]) -> set[str]:
    return {
        key
        for row in rows
        if row.get("relation_type") == "own_site_snapshot"
        and (key := url_dedupe_key(str(row.get("source_url") or ""))) is not None
    }


def _host_distribution(
    answers: list[dict[str, Any]], citations: dict[str, list[dict[str, Any]]], own_host: str | None
) -> list[dict[str, Any]]:
    answer_ids: dict[str, set[str]] = defaultdict(set)
    references: dict[str, int] = defaultdict(int)
    for answer in answers:
        answer_id = str(answer["pub_id"])
        for citation in citations.get(answer_id, []):
            host = str(citation.get("host") or "").strip().lower()
            if not host:
                continue
            answer_ids[host].add(answer_id)
            references[host] += 1
    rows = [
        {
            "host": host,
            "answers": len(ids),
            "references": references[host],
            "is_own_site": _is_own_host(host, own_host),
        }
        for host, ids in answer_ids.items()
    ]
    return sorted(rows, key=lambda row: (-row["answers"], -row["references"], row["host"]))


def _platform_region_breakdown(
    answers: list[dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    own_host: str | None,
    retrieval_by_answer: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for answer in answers:
        answer_id = str(answer["pub_id"])
        key = (str(answer["model"]), str(answer["mode"]), str(answer["region"]))
        bucket = buckets.setdefault(
            key,
            {
                "model": key[0],
                "model_label": MODEL_LABELS.get(key[0], key[0]),
                "mode": key[1],
                "region": key[2],
                "answers": 0,
                "answers_with_citation": 0,
                "answers_with_own_site_citation": 0,
                "answers_with_candidate_stage": 0,
                "answers_with_official_candidate": 0,
                "answers_with_opened_stage": 0,
                "answers_with_official_opened": 0,
            },
        )
        bucket["answers"] += 1
        answer_citations = citations.get(answer_id, [])
        if answer_citations:
            bucket["answers_with_citation"] += 1
        if any(_is_own_host(row.get("host"), own_host) for row in answer_citations):
            bucket["answers_with_own_site_citation"] += 1
        retrieval = retrieval_by_answer.get(answer_id, {})
        bucket["answers_with_candidate_stage"] += int(
            bool(retrieval.get("candidate_stage_observed"))
        )
        bucket["answers_with_official_candidate"] += int(
            bool(retrieval.get("official_candidate_observed"))
        )
        bucket["answers_with_opened_stage"] += int(bool(retrieval.get("opened_stage_observed")))
        bucket["answers_with_official_opened"] += int(
            bool(retrieval.get("official_opened_observed"))
        )
    for row in buckets.values():
        row["citation_coverage_rate"] = round(row["answers_with_citation"] / row["answers"], 4)
        row["own_site_answer_citation_rate"] = round(
            row["answers_with_own_site_citation"] / row["answers"], 4
        )
    return sorted(buckets.values(), key=lambda row: (row["model"], row["mode"], row["region"]))


def build_service3_review_v2_facts(
    *,
    dsn: str,
    blob_loader: Callable[[str, str], bytes],
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build client-readable Service 3 facts from current answer and CAS evidence."""

    generated_at = generated_at or datetime.now(UTC)
    project = brandrank_service.fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise LookupError("project_not_found")
    answers = _load_answers(dsn, tenant_pub_id, project_pub_id, start, end)
    answer_by_id = {str(row["pub_id"]): row for row in answers}
    answer_ids = list(answer_by_id)
    citations = brandrank_service.fetch_citations(dsn, tenant_pub_id, answer_ids)
    overview = AnalyticsService(dsn=dsn).source_audit_overview(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
    )
    own_host = overview.get("own_site_host")
    site_assets, assets_by_answer = _load_evidence_assets(
        dsn, tenant_pub_id, project_pub_id, answer_ids, start, end
    )
    snapshots = _snapshot_index(site_assets, blob_loader, own_host)
    screenshot_status_cache: dict[str, str] = {}
    trace_by_answer = {
        answer_id: _trace(assets_by_answer.get(answer_id, []), blob_loader)
        for answer_id in answer_ids
    }
    retrieval_by_answer = {
        answer_id: _retrieval_observability(
            model=str(answer_by_id[answer_id]["model"]),
            trace=trace_by_answer[answer_id],
            citations=citations.get(answer_id, []),
            own_host=own_host,
        )
        for answer_id in answer_ids
    }

    evaluations: list[dict[str, Any]] = []
    official_reference_count = 0
    own_source_flag_false = 0
    for answer in answers:
        answer_id = str(answer["pub_id"])
        all_sources = citations.get(answer_id, [])
        official_sources = [row for row in all_sources if _is_own_host(row.get("host"), own_host)]
        if not official_sources:
            continue
        official_reference_count += len(official_sources)
        own_source_flag_false += sum(not bool(row.get("own_source")) for row in official_sources)
        narrative = answer_narrative(str(answer.get("response_text") or ""))
        direct_urls = _direct_snapshot_urls(assets_by_answer.get(answer_id, []))
        candidates: list[dict[str, Any]] = []
        for source in official_sources:
            source_url = str(source.get("canonical_url") or source.get("original_url") or "")
            key = url_dedupe_key(source_url)
            snapshot = snapshots.get(key or "")
            body = str(((snapshot or {}).get("text") or {}).get("body") or "")
            narrative_match = (
                longest_common_evidence(narrative, body)
                if body
                else {
                    "length": 0,
                    "normalized_phrase": "",
                    "left_excerpt": "",
                    "right_excerpt": "",
                }
            )
            cited_text = str(source.get("cited_text") or "")
            cited_match = (
                longest_common_evidence(cited_text, body)
                if cited_text and body
                else {
                    "length": 0,
                    "normalized_phrase": "",
                    "left_excerpt": "",
                    "right_excerpt": "",
                }
            )
            screenshot_status = _screenshot_status(snapshot, blob_loader, screenshot_status_cache)
            candidates.append(
                {
                    "ordinal": int(source.get("ordinal") or 0),
                    "url": source_url,
                    "title": str(source.get("title") or ""),
                    "cited_text": cited_text,
                    "snapshot": snapshot,
                    "screenshot_status": screenshot_status,
                    "direct_answer_relation": bool(key and key in direct_urls),
                    "narrative_match": narrative_match,
                    "cited_text_source_match": cited_match,
                }
            )
        best = max(
            candidates,
            key=lambda row: (
                int(row["narrative_match"]["length"]),
                int(row["cited_text_source_match"]["length"]),
                -int(row["ordinal"]),
            ),
        )
        snapshot_available = any(
            row["snapshot"] and row["snapshot"].get("text") for row in candidates
        )
        best_snapshot_candidate = max(
            (row for row in candidates if row["snapshot"] and row["snapshot"].get("text")),
            key=lambda row: (
                int(row["narrative_match"]["length"]),
                int(row["cited_text_source_match"]["length"]),
            ),
            default=best,
        )
        best = best_snapshot_candidate
        status = adoption_status(
            int(best["narrative_match"]["length"]), snapshot_available=snapshot_available
        )
        trace = trace_by_answer.get(answer_id, {})
        answer_excerpt_asset = _answer_asset(
            assets_by_answer.get(answer_id, []),
            relation_type="answer_evidence_excerpt",
            mime_type="image/png",
        )
        answer_page_asset = _answer_asset(
            assets_by_answer.get(answer_id, []),
            relation_type="answer_page",
            mime_type="image/png",
        )
        evaluations.append(
            {
                "answer_pub_id": answer_id,
                "query": str(answer["query_text"]),
                "model": str(answer["model"]),
                "model_label": MODEL_LABELS.get(str(answer["model"]), str(answer["model"])),
                "region": str(answer["region"]),
                "mode": str(answer["mode"]),
                "capture_time": answer["capture_time"],
                "status": status,
                "status_basis": {
                    "confirmed": "回答主文与当前窗口官网正文有不少于 20 个连续归一化字符的独特重合",
                    "weak": "有 10–19 个连续归一化字符重合，可能只是产品名或通用术语，待人工复核",
                    "no_direct_evidence": (
                        "已有官网正文快照，但回答主文未找到长度达 10 的连续直接重合"
                    ),
                    "not_evaluated": "该官网 URL 在当前窗口没有可用正文快照，不进入采纳率分母",
                }[status],
                "all_source_count": len(all_sources),
                "all_sources": [
                    {
                        "ordinal": int(row.get("ordinal") or 0),
                        "host": str(row.get("host") or ""),
                        "url": str(row.get("canonical_url") or row.get("original_url") or ""),
                        "is_own_site": _is_own_host(row.get("host"), own_host),
                    }
                    for row in all_sources
                ],
                "official_sources": [
                    {
                        "ordinal": row["ordinal"],
                        "url": row["url"],
                        "title": row["title"],
                        "has_cited_text": bool(row["cited_text"].strip()),
                        "has_current_text_snapshot": bool(
                            row["snapshot"] and row["snapshot"].get("text")
                        ),
                        "has_current_screenshot": bool(row["screenshot_status"] == "usable"),
                        "screenshot_status": row["screenshot_status"],
                        "direct_answer_relation": row["direct_answer_relation"],
                    }
                    for row in candidates
                ],
                "best_official_url": best["url"],
                "best_official_title": str(
                    ((best.get("snapshot") or {}).get("text") or {}).get("title")
                    or best.get("title")
                    or ""
                ),
                "answer_excerpt": best["narrative_match"]["left_excerpt"]
                or " ".join(narrative.split())[:500],
                "source_excerpt": best["narrative_match"]["right_excerpt"]
                or str(best.get("cited_text") or "")[:500],
                "matched_phrase": best["narrative_match"]["normalized_phrase"],
                "match_length": int(best["narrative_match"]["length"]),
                "cited_text_source_match_length": int(best["cited_text_source_match"]["length"]),
                "surface_reasoning": _trace_summary(trace),
                "trace_available": bool(trace),
                "retrieval_observability": retrieval_by_answer.get(answer_id, {}),
                "answer_screenshot": answer_excerpt_asset or answer_page_asset,
                "answer_screenshot_kind": (
                    "answer_excerpt_screenshot" if answer_excerpt_asset else "answer_screenshot"
                ),
                "official_screenshot": (
                    ((best.get("snapshot") or {}).get("png"))
                    if best["screenshot_status"] == "usable"
                    else None
                ),
                "official_screenshot_status": best["screenshot_status"],
                "snapshot_relation": (
                    "direct"
                    if best.get("direct_answer_relation")
                    else "same_url_current_window_reuse"
                    if snapshot_available
                    else "missing"
                ),
            }
        )

    counts = {status: 0 for status in ("confirmed", "weak", "no_direct_evidence", "not_evaluated")}
    for row in evaluations:
        counts[row["status"]] += 1
    evaluated = len(evaluations) - counts["not_evaluated"]
    verified = counts["confirmed"]
    direct_bound_answers = sum(
        any(source["direct_answer_relation"] for source in row["official_sources"])
        for row in evaluations
    )
    text_snapshot_covered_answers = sum(
        any(source["has_current_text_snapshot"] for source in row["official_sources"])
        for row in evaluations
    )
    usable_screenshot_covered_answers = sum(
        any(source["has_current_screenshot"] for source in row["official_sources"])
        for row in evaluations
    )

    selected_cases: list[dict[str, Any]] = []

    def evidence_readability_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
        path = urlsplit(str(row.get("best_official_url") or "")).path.lower()
        # Long-form content pages normally yield a sentence-level, readable DOM
        # anchor.  A homepage carousel or product hero often leaves only a giant
        # container outline, which must not outrank a precise article paragraph.
        long_form_page = int(any(marker in path for marker in ("/news-", "/article", "/detail")))
        return (
            long_form_page,
            int(row["cited_text_source_match_length"]),
            int(row["match_length"]),
            int(row["all_source_count"]),
        )

    for status in ("confirmed", "weak", "no_direct_evidence"):
        candidates = [row for row in evaluations if row["status"] == status]
        if candidates:
            selected_cases.append(max(candidates, key=evidence_readability_score))
    if len(selected_cases) < 3:
        for row in sorted(
            evaluations,
            key=lambda value: (value["status"] == "not_evaluated", -value["match_length"]),
        ):
            if row not in selected_cases:
                selected_cases.append(row)
            if len(selected_cases) == 3:
                break

    breakdown = _platform_region_breakdown(answers, citations, own_host, retrieval_by_answer)

    def zero_group_diagnosis(row: dict[str, Any]) -> list[str]:
        notes = [
            (
                f"本单元 {row['answers']} 条回答中有 {row['answers_with_citation']} 条返回"
                "可解析引用，但最终官网引用为 0。"
            )
        ]
        if row["answers_with_candidate_stage"] == row["answers"]:
            if row["answers_with_official_candidate"]:
                notes.append(
                    f"候选结果阶段完整可见，其中 {row['answers_with_official_candidate']} 条"
                    "回答曾返回官网候选，但最终没有引用官网；可归为“候选出现、最终未引用”。"
                )
            else:
                notes.append("候选结果阶段完整可见，当前可见候选中没有官网 URL。")
        elif row["answers_with_candidate_stage"]:
            notes.append(
                f"仅 {row['answers_with_candidate_stage']}/{row['answers']} 条回答保存了候选"
                "结果阶段，其余回答不能判断官网是否曾进入候选。"
            )
        else:
            notes.append(
                "该平台当前只保存最终引用，没有完整候选 URL 清单；因此不能判断官网是"
                "未进入候选，还是进入候选后未被最终引用。"
            )
        if row["answers_with_opened_stage"] != row["answers"]:
            notes.append("页面打开/读取阶段并非本单元全部可观测；报告不推断平台隐藏的页面选择。")
        return notes

    zero_groups = [
        {**row, "diagnosis_items": zero_group_diagnosis(row)}
        for row in breakdown
        if row["answers"] and row["answers_with_own_site_citation"] == 0
    ]

    observability_by_platform: list[dict[str, Any]] = []
    for model in sorted({str(answer["model"]) for answer in answers}):
        states = [
            retrieval_by_answer[str(answer["pub_id"])]
            for answer in answers
            if str(answer["model"]) == model
        ]
        answer_count = len(states)
        candidate_count = sum(bool(row.get("candidate_stage_observed")) for row in states)
        opened_count = sum(bool(row.get("opened_stage_observed")) for row in states)
        if model == "deepseek":
            if opened_count == answer_count:
                boundary = "本窗口全部回答均可观察候选、打开与最终引用三阶段"
            elif opened_count:
                boundary = (
                    f"本窗口混合新旧采集：{opened_count}/{answer_count} 条保存了页面"
                    "打开阶段；其余旧回答不能倒推当时打开了哪些页面"
                )
            else:
                boundary = "本窗口旧回答未保存页面打开阶段；新适配器需以新采集验证"
        elif model == "doubao":
            boundary = "候选与最终引用可观测；平台未暴露稳定的页面打开事件"
        elif model == "yiyan":
            boundary = "只可观测最终引用；完整候选与页面打开阶段不可观测"
        else:
            boundary = "当前证据未形成完整检索阶段记录"
        observability_by_platform.append(
            {
                "model": model,
                "model_label": MODEL_LABELS.get(model, model),
                "answers": len(states),
                "trace_available": sum(bool(row.get("trace_available")) for row in states),
                "candidate_stage_observed": candidate_count,
                "opened_stage_observed": opened_count,
                "final_citation_stage_observed": len(states),
                "boundary": boundary,
            }
        )

    latest_by_model: dict[str, dict[str, Any]] = {}
    for answer in answers:
        model = str(answer["model"])
        previous = latest_by_model.get(model)
        if previous is None or answer["capture_time"] > previous["capture_time"]:
            latest_by_model[model] = answer
    probe_models = ("doubao", "deepseek", "yiyan")
    probe_answers = [latest_by_model.get(model) for model in probe_models]
    latest_cross_platform_probe: dict[str, Any] | None = None
    if all(answer is not None for answer in probe_answers):
        complete_probe_answers = [answer for answer in probe_answers if answer is not None]
        queries = {str(answer["query_text"]) for answer in complete_probe_answers}
        capture_times = [answer["capture_time"] for answer in complete_probe_answers]
        if len(queries) == 1 and max(capture_times) - min(capture_times) <= timedelta(hours=2):
            probe_rows: list[dict[str, Any]] = []
            for answer in complete_probe_answers:
                answer_id = str(answer["pub_id"])
                state = retrieval_by_answer[answer_id]
                probe_rows.append(
                    {
                        "model": str(answer["model"]),
                        "model_label": MODEL_LABELS.get(str(answer["model"]), str(answer["model"])),
                        "answer_pub_id": answer_id,
                        "capture_time": answer["capture_time"],
                        "candidate_stage_observed": bool(state.get("candidate_stage_observed")),
                        "candidate_urls": int(state.get("candidate_urls") or 0),
                        "official_candidate_observed": bool(
                            state.get("official_candidate_observed")
                        ),
                        "opened_stage_observed": bool(state.get("opened_stage_observed")),
                        "opened_urls": int(state.get("opened_urls") or 0),
                        "official_opened_observed": bool(state.get("official_opened_observed")),
                        "final_citations": int(state.get("final_citations") or 0),
                        "official_final_citation": bool(state.get("official_final_citation")),
                    }
                )
            latest_cross_platform_probe = {
                "query": next(iter(queries)),
                "capture_start": min(capture_times),
                "capture_end": max(capture_times),
                "rows": probe_rows,
                "scope": "同一问题、同一地域、三个平台各 1 次的新部署试点",
            }

    return {
        "schema_version": "service3-review-v2-facts-v1",
        "document_status": "pre_formal_review_nonproduction_data",
        "project_pub_id": project_pub_id,
        "project_name": str(project.get("name") or ""),
        "target_brand": str((project.get("brand_names") or [""])[0]),
        "domain": str(project.get("brandrank_domain") or ""),
        "own_site_host": own_host,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": generated_at,
        "metrics": {
            "answers_total": int(overview["answers_total"]),
            "answers_with_citation": int(overview["answers_with_citation"]),
            "citation_coverage_rate": overview["citation_coverage_rate"],
            "answers_with_own_site_citation": int(overview["answers_with_own_site_citation"]),
            "own_site_answer_citation_rate": overview["own_site_answer_citation_rate"],
            "own_site_share_of_cited_answers": overview["own_site_share_of_cited_answers"],
            "citation_references_total": int(overview["citation_references_total"]),
            "own_site_citation_references": int(overview["own_site_citation_references"]),
            "own_site_reference_share": overview["own_site_reference_share"],
            "own_site_cited_text_answers": int(overview["own_site_cited_text_answers"]),
            "own_site_cited_text_evidence_rate": overview["own_site_cited_text_evidence_rate"],
            "adoption_evaluated_answers": evaluated,
            "adoption_verified_answers": verified,
            "conservative_adoption_rate": round(verified / evaluated, 4) if evaluated else None,
            "weak_evidence_answers": counts["weak"],
            "no_direct_evidence_answers": counts["no_direct_evidence"],
            "not_evaluated_answers": counts["not_evaluated"],
            "adoption_evaluation_coverage_rate": (
                round(evaluated / len(evaluations), 4) if evaluations else None
            ),
            "direct_snapshot_bound_answers": direct_bound_answers,
            "same_url_snapshot_covered_answers": text_snapshot_covered_answers,
            "usable_screenshot_covered_answers": usable_screenshot_covered_answers,
            "own_source_flag_false_references": own_source_flag_false,
            "official_reference_count_recomputed": official_reference_count,
        },
        "adoption_method": {
            "denominator": "仅统计同时具有官网 URL 引用与当前窗口官网正文快照的回答",
            "reference_list_excluded": True,
            "confirmed_rule": (
                "剔除回答参考来源列表后，回答主文与官网正文有至少 20 个连续归一化字符的直接重合"
            ),
            "weak_rule": "10–19 个连续归一化字符重合，不计入已确认采纳",
            "boundary": (
                "本判定只证明可见回答与官网正文的直接内容关联，不推断平台隐藏的内部取舍过程。"
            ),
        },
        "platform_region_breakdown": breakdown,
        "zero_citation_groups": zero_groups,
        "retrieval_observability_by_platform": observability_by_platform,
        "latest_cross_platform_probe": latest_cross_platform_probe,
        "answer_source_domains": _host_distribution(answers, citations, own_host)[:20],
        "evaluations": evaluations,
        "selected_evidence_cases": selected_cases,
        "client_actions": [
            {
                "priority": "P0",
                "fact": f"当前仅 {evaluated}/{len(evaluations)} 条官网引用回答可完成正文级评价。",
                "action": "先补齐报告中列出的缺失官网 URL 快照，再对采纳率签发结论。",
                "owner": "评测方",
            },
            {
                "priority": "P1",
                "fact": (
                    f"保守标准下可确认采纳 {verified}/{evaluated}；弱证据 {counts['weak']} 条。"
                ),
                "action": (
                    "保留已被直接采用的可引用表述；对弱证据页补充独立、完整、"
                    "可核验的产品能力句和数字事实。"
                ),
                "owner": "官网内容",
            },
            {
                "priority": "P1",
                "fact": f"观测到 {len(zero_groups)} 个平台/模式/地域单元的官网引用为 0。",
                "action": (
                    "重采时按平台披露可观测检索漏斗：DeepSeek 可记录候选、打开与最终引用，"
                    "豆包可记录候选与最终引用，文心当前只暴露最终引用；平台不返回的阶段"
                    "不得强行归因。"
                ),
                "owner": "评测与官网运营",
            },
        ],
        "limitations": [
            "本报告为内部审核稿；网页快照覆盖补齐并经人工复核、批准前，不作为正式签发结论。",
            "“公开思考/检索摘要”仅来自平台明确返回并已存证的检索记录；不包含、也不推断隐藏推理。",
            "本报告按项目确认的官网域名识别官网链接，不依赖平台侧的历史标记。",
            "直接文本重合是保守的可见证据；无直接重合不等于证明 AI 没有语义改写或受其影响。",
            "重新采集可以改善已暴露阶段的证据，但不能凭空补出平台未向浏览器暴露的完整"
            "候选或页面打开事件。",
        ],
    }
