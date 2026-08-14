"""Service-1 delivery facts and evidence loading for the V2 review report.

The compact pre-formal report intentionally exposed only headline measurements.  A
client-facing delivery needs the complete chain behind those measurements: the test
matrix, question-level results, full brand ranking, source landscape, representative
answer evidence and a row-level sample register.  This module builds that richer
shape without changing the measurement population selected by ``formal_review``.

Representative screenshots are presentation examples only.  They are selected after
the three candidate groups have already been fixed, and never influence any metric.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from hashlib import sha256
from statistics import mean
from typing import Any

from psycopg.rows import dict_row

from domain.brandrank import adapter, metrics
from domain.brandrank.entities import load_entity_master, normalize_answer_entities
from domain.brandrank.rules import load_domain
from domain.reporting.service1_governance import assign_repeats, quotation_gate
from domain.reporting.service1_metrics import (
    comparable_competitors,
    entity_metric,
    ranked_entities,
    repeat_consistency,
    source_cooccurrence,
)
from geo_platform.brandrank import service as brandrank_service
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.tenancy.psycopg import tenant_connection

MODEL_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except ValueError:
            return []
        return loaded if isinstance(loaded, list) else []
    return []


def _load_answers(dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]) -> list[dict[str, Any]]:
    if not answer_pub_ids:
        return []
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id, query_text, response_text, model, region, mode, capture_time,
                   run_pub_id, config_version_pub_id
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[])
            ORDER BY capture_time, pub_id
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_detailed_citations(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            WITH latest_run AS (
              SELECT DISTINCT ON (answer_pub_id) answer_pub_id, analysis_run_pub_id
              FROM analytics.citation_fact
              WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s::text[])
              ORDER BY answer_pub_id, id DESC
            )
            SELECT c.answer_pub_id, c.ordinal, c.host, c.canonical_url, c.original_url,
                   c.title, c.cited_text, c.own_source
            FROM analytics.citation_fact c
            JOIN latest_run lr ON lr.answer_pub_id=c.answer_pub_id
                              AND lr.analysis_run_pub_id=c.analysis_run_pub_id
            WHERE c.tenant_pub_id=%s
            ORDER BY c.answer_pub_id, c.ordinal, c.pub_id
            """,
            (tenant_pub_id, answer_pub_ids, tenant_pub_id),
        ).fetchall()
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        output.setdefault(str(row["answer_pub_id"]), []).append(dict(row))
    return output


def _load_answer_auxiliary(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> tuple[
    dict[str, bool],
    dict[str, str],
    dict[str, bool],
    dict[str, list[dict[str, object]]],
]:
    """Return runtime screenshot/share-image availability and search queries."""

    if not answer_pub_ids:
        return {}, {}, {}, {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        image_rows = connection.execute(
            """
            SELECT DISTINCT er.from_pub_id, ea.kind
            FROM evidence.evidence_relation er
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
            WHERE er.tenant_pub_id=%s AND er.from_pub_id=ANY(%s::text[])
              AND ea.kind IN ('share_image','answer_excerpt_screenshot','answer_screenshot')
              AND ea.mime_type LIKE 'image/%%'
              AND ea.deleted_at IS NULL
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
        task_rows = connection.execute(
            """
            SELECT ct.pub_id, ct.search_queries_json
            FROM platform.collection_task ct
            JOIN platform.tenant t ON t.id=ct.tenant_id
            WHERE t.pub_id=%s AND ct.pub_id=ANY(%s::text[])
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    has_screenshot = {
        str(row["from_pub_id"]): True
        for row in image_rows
        if row["kind"] in {"answer_excerpt_screenshot", "answer_screenshot"}
    }
    screenshot_kind: dict[str, str] = {}
    for row in image_rows:
        answer_pub_id = str(row["from_pub_id"])
        kind = str(row["kind"])
        if kind == "answer_excerpt_screenshot" or (
            kind == "answer_screenshot" and answer_pub_id not in screenshot_kind
        ):
            screenshot_kind[answer_pub_id] = kind
    has_share_image = {
        str(row["from_pub_id"]): True for row in image_rows if row["kind"] == "share_image"
    }
    search_queries: dict[str, list[dict[str, object]]] = {}
    for row in task_rows:
        values = _json_list(row["search_queries_json"])
        search_queries[str(row["pub_id"])] = [
            dict(value) for value in values if isinstance(value, dict)
        ]
    return has_screenshot, screenshot_kind, has_share_image, search_queries


def _load_evidence_inventory(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Load every answer-linked customer evidence descriptor, not one preferred image."""

    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT relation.from_pub_id,relation.relation_type,asset.pub_id AS evidence_pub_id,
                   asset.kind,asset.capture_time,asset.mime_type,asset.byte_size,asset.sha256,
                   asset.object_key,asset.source_url
            FROM evidence.evidence_relation relation
            JOIN evidence.evidence_asset asset
              ON asset.tenant_pub_id=relation.tenant_pub_id
             AND asset.pub_id=relation.to_pub_id
            WHERE relation.tenant_pub_id=%s
              AND relation.from_pub_id=ANY(%s::text[])
              AND asset.deleted_at IS NULL
            ORDER BY relation.from_pub_id,asset.kind,asset.capture_time,asset.pub_id
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[str(row["from_pub_id"])].append(
            {
                "evidence_id": str(row["evidence_pub_id"]),
                "relation_type": str(row["relation_type"]),
                "kind": str(row["kind"]),
                "capture_time": row["capture_time"],
                "mime_type": str(row["mime_type"]),
                "byte_size": int(row["byte_size"]),
                "sha256": str(row["sha256"]),
                "object_key": str(row["object_key"]),
                "source_url": row["source_url"],
            }
        )
    return dict(output)


def _load_sampling_provenance(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Load immutable task-outcome provenance; never infer it from current state."""

    if not answer_pub_ids:
        return {}
    output: dict[str, dict[str, Any]] = {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        available = connection.execute(
            """
            SELECT to_regclass('platform.collection_account_event') AS event_table,
                   to_regclass('platform.collection_task') AS task_table
            """
        ).fetchone()
        if available and available["task_table"] is not None:
            task_rows = connection.execute(
                """
                SELECT task.pub_id,task.matrix_json
                FROM platform.collection_task task
                JOIN platform.tenant tenant ON tenant.id=task.tenant_id
                WHERE tenant.pub_id=%s AND task.pub_id=ANY(%s::text[])
                """,
                (tenant_pub_id, answer_pub_ids),
            ).fetchall()
            for row in task_rows:
                matrix = row["matrix_json"]
                if isinstance(matrix, str):
                    try:
                        matrix = json.loads(matrix)
                    except ValueError:
                        matrix = {}
                if isinstance(matrix, dict):
                    output[str(row["pub_id"])] = {
                        "browser_instance": matrix.get("browser_instance"),
                    }
        if available and available["event_table"] is not None:
            event_rows = connection.execute(
                """
                SELECT DISTINCT ON (event.new_value->>'task_pub_id')
                       event.new_value->>'task_pub_id' AS answer_pub_id,
                       event.new_value,event.created_at
                FROM platform.collection_account_event event
                WHERE event.event_type='task_outcome'
                  AND event.new_value->>'task_pub_id'=ANY(%s::text[])
                  AND event.new_value->>'outcome'='success'
                ORDER BY event.new_value->>'task_pub_id',event.created_at DESC,event.id DESC
                """,
                (answer_pub_ids,),
            ).fetchall()
            for row in event_rows:
                payload = row["new_value"] if isinstance(row["new_value"], dict) else {}
                output[str(row["answer_pub_id"])] = {
                    "account_id_masked": payload.get("account_id_masked"),
                    "browser_instance": payload.get("browser_instance"),
                    "egress_region_gb": payload.get("egress_region_gb"),
                    "egress_audit": {
                        "ip_sha256": payload.get("egress_ip_sha256"),
                        "probe_at": payload.get("egress_probe_at"),
                        "probe_state": payload.get("egress_probe_state"),
                        "provenance_recorded_at": row["created_at"],
                    }
                    if payload.get("egress_region_gb")
                    and payload.get("egress_ip_sha256")
                    and payload.get("egress_probe_at")
                    else None,
                }
    return output


def _load_citation_snapshots(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Load answer-linked URL snapshots and their optional screenshot descriptors."""

    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT answer_relation.from_pub_id AS answer_pub_id,document.pub_id AS document_id,
                   document.url,document.final_url,document.http_status,document.fetched_at,
                   document.extract_status,document.bytes,document.text_sha256,
                   document.text_cas_key,snapshot.pub_id AS snapshot_id,
                   snapshot.sha256 AS snapshot_sha256,snapshot.object_key AS snapshot_object_key,
                   snapshot.mime_type AS snapshot_mime_type,snapshot.byte_size AS snapshot_byte_size
            FROM evidence.evidence_relation answer_relation
            JOIN platform.source_document document
              ON document.pub_id=answer_relation.to_pub_id
            LEFT JOIN evidence.evidence_relation snapshot_relation
              ON snapshot_relation.tenant_pub_id=answer_relation.tenant_pub_id
             AND snapshot_relation.from_pub_id=document.pub_id
             AND snapshot_relation.relation_type='brand_mention_source_snapshot'
            LEFT JOIN evidence.evidence_asset snapshot
              ON snapshot.tenant_pub_id=snapshot_relation.tenant_pub_id
             AND snapshot.pub_id=snapshot_relation.to_pub_id AND snapshot.deleted_at IS NULL
            WHERE answer_relation.tenant_pub_id=%s
              AND answer_relation.from_pub_id=ANY(%s::text[])
              AND answer_relation.relation_type='cited_source_document'
            ORDER BY answer_relation.from_pub_id,document.url,snapshot.capture_time DESC
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row["answer_pub_id"]), str(row["document_id"]))
        if key in seen:
            continue
        seen.add(key)
        output[key[0]].append(
            {
                "document_id": key[1],
                "url": str(row["url"]),
                "final_url": row["final_url"],
                "http_status": row["http_status"],
                "visited_at": row["fetched_at"],
                "extract_status": str(row["extract_status"]),
                "byte_size": int(row["bytes"]),
                "text_sha256": row["text_sha256"],
                "text_object_key": row["text_cas_key"],
                "snapshot": (
                    {
                        "evidence_id": str(row["snapshot_id"]),
                        "sha256": str(row["snapshot_sha256"]),
                        "object_key": str(row["snapshot_object_key"]),
                        "mime_type": str(row["snapshot_mime_type"]),
                        "byte_size": int(row["snapshot_byte_size"]),
                    }
                    if row["snapshot_id"] is not None
                    else None
                ),
            }
        )
    return dict(output)


def _load_native_answer_anchors(
    dsn: str,
    tenant_pub_id: str,
    answers: list[dict[str, Any]],
    target_brand: str,
) -> dict[str, dict[str, Any]]:
    """Load brand rectangles from the newest clean answer image, fail closed.

    Every text interval is rechecked against the persisted answer and ``quote_hash``.
    Rectangles from different images are never combined, and a rectangle is admitted
    only when its verified text contains the target brand verbatim.
    """

    answer_text = {
        str(row["pub_id"]): str(row.get("response_text") or "")
        for row in answers
        if row.get("pub_id")
    }
    if not answer_text or not target_brand:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT relation.from_pub_id,asset.pub_id AS evidence_pub_id,
                   asset.capture_time,anchor.text_start,anchor.text_end,
                   anchor.bbox,anchor.quote_hash
            FROM evidence.evidence_relation relation
            JOIN evidence.evidence_asset asset
              ON asset.tenant_pub_id=relation.tenant_pub_id
             AND asset.pub_id=relation.to_pub_id
            JOIN evidence.evidence_anchor anchor
              ON anchor.tenant_pub_id=asset.tenant_pub_id
             AND anchor.evidence_pub_id=asset.pub_id
            WHERE relation.tenant_pub_id=%s
              AND relation.from_pub_id=ANY(%s::text[])
              AND relation.relation_type='answer_evidence_excerpt'
              AND asset.kind='answer_excerpt_screenshot'
              AND asset.mime_type LIKE 'image/%%' AND asset.deleted_at IS NULL
            ORDER BY relation.from_pub_id,asset.capture_time DESC,asset.pub_id DESC,
                     anchor.text_start,anchor.pub_id
            """,
            (tenant_pub_id, list(answer_text)),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_row in rows:
        grouped[str(raw_row["from_pub_id"])].append(dict(raw_row))

    output: dict[str, dict[str, Any]] = {}
    for answer_pub_id, candidates in grouped.items():
        latest_asset = str(candidates[0]["evidence_pub_id"])
        text = answer_text.get(answer_pub_id, "")
        boxes: list[list[int]] = []
        methods: list[str] = []
        for row in candidates:
            if str(row["evidence_pub_id"]) != latest_asset:
                break
            start = row.get("text_start")
            end = row.get("text_end")
            bbox = row.get("bbox")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(text)
                or not isinstance(bbox, dict)
            ):
                continue
            anchored_text = text[start:end]
            if target_brand not in anchored_text or sha256(
                anchored_text.encode()
            ).hexdigest() != str(row.get("quote_hash") or ""):
                continue
            try:
                x = float(bbox["x"])
                y = float(bbox["y"])
                width = float(bbox["width"])
                height = float(bbox["height"])
                image_width = float(bbox["image_width"])
                image_height = float(bbox["image_height"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                x < 0
                or y < 0
                or width <= 0
                or height <= 0
                or image_width <= 0
                or image_height <= 0
                or x + width > image_width
                or y + height > image_height
            ):
                continue
            boxes.append([round(x), round(y), max(1, round(width)), max(1, round(height))])
            methods.append(str(bbox.get("anchor_method") or ""))
        if boxes:
            output[answer_pub_id] = {
                "bboxes": boxes,
                "method": methods[0] if len(set(methods)) == 1 else "mixed_verified_anchors",
                "evidence_pub_id": latest_asset,
            }
    return output


def _scope_analysis(
    answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    domain: str,
    target_brand: str,
    competitors: Iterable[str],
) -> dict[str, Any]:
    rules = load_domain(domain)
    records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for answer in answers:
        answer_pub_id = str(answer["pub_id"])
        extract = extracts.get(answer_pub_id)
        if extract and extract.get("status") == "ok" and isinstance(extract.get("brands"), list):
            records.append(adapter.answer_to_brand_record(answer, list(extract["brands"])))
        for citation in citations.get(answer_pub_id, []):
            source = adapter.citation_to_source_entry(citation)
            source.update(
                {
                    "thinking_mode": adapter.mode_label(str(answer.get("mode") or "")),
                    "ip": str(answer.get("region") or ""),
                }
            )
            source_records.append(source)
    return metrics.analyze(
        records,
        source_records,
        rules=rules,
        target_brand=target_brand,
        competitors=competitors,
        top_ns=(1, 3, 5),
    )


def _target_summary(result: dict[str, Any], answer_count: int) -> dict[str, Any]:
    target = result.get("target_brand") or {}
    return {
        "answers": answer_count,
        "mentions": int(target.get("mentions") or 0),
        "appearance_rate": float(target.get("appearance_rate") or 0),
        "avg_rank": target.get("avg_rank"),
        "best_rank": target.get("best_rank"),
        "top_rates": target.get("top_rates") or {},
    }


def _answer_excerpt(text: str, target_brand: str, *, limit: int = 340) -> str:
    readable = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    readable = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", readable)
    readable = readable.replace("**", "").replace("__", "").replace("`", "")
    readable = readable.replace("|", "；")
    compact = " ".join(readable.split())
    if len(compact) <= limit:
        return compact
    position = compact.find(target_brand)
    if position < 0:
        return f"{compact[: limit - 1]}…"
    before = max(0, position - limit // 3)
    after = min(len(compact), before + limit)
    before = max(0, after - limit)
    prefix = "…" if before else ""
    suffix = "…" if after < len(compact) else ""
    return f"{prefix}{compact[before:after]}{suffix}"


def _representative_rows(
    selected_groups: list[dict[str, Any]],
    answer_rows: list[dict[str, Any]],
    *,
    row_details: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    has_screenshot: dict[str, bool],
    screenshot_kind: dict[str, str],
    has_share_image: dict[str, bool],
    search_queries: dict[str, list[dict[str, object]]],
    target_brand: str,
    answer_anchor_overrides: dict[str, dict[str, Any]],
    native_answer_anchors: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose one auditable display example per group.

    Selection prioritises an official share export, then the readability of a runtime
    screenshot fallback, citation availability, response completeness and platform
    diversity across the selected cards.
    Rank is deliberately not part of the score.
    """

    output: list[dict[str, Any]] = []
    platform_rotation = [model for model in ("doubao", "deepseek", "yiyan")]
    for group_index, group in enumerate(selected_groups):
        members = [
            row
            for row in answer_rows
            if row_details[str(row["pub_id"])]["candidate_group_id"] == group["id"]
        ]
        designated_model = platform_rotation[group_index % len(platform_rotation)]
        designated = [row for row in members if row.get("model") == designated_model]
        candidates = designated or members
        used_models = {str(row["platform"]) for row in output}

        def completeness(
            row: dict[str, Any], diversity_models: set[str] = used_models
        ) -> tuple[int, int, int, str]:
            answer_pub_id = str(row["pub_id"])
            cite_count = len(citations.get(answer_pub_id, []))
            model = str(row.get("model") or "")
            fallback_visual_quality = {"yiyan": 55, "deepseek": 48, "doubao": 18}.get(model, 25)
            score = (
                120 * int(has_share_image.get(answer_pub_id, False))
                + fallback_visual_quality * int(has_screenshot.get(answer_pub_id, False))
                + 25 * int(cite_count > 0)
                + min(35, len(str(row.get("response_text") or "")) // 250)
                + 16 * int(model not in diversity_models)
            )
            return score, cite_count, len(str(row.get("response_text") or "")), answer_pub_id

        if not candidates:
            continue
        chosen = max(candidates, key=completeness)
        answer_pub_id = str(chosen["pub_id"])
        detail = row_details[answer_pub_id]
        citation_rows = citations.get(answer_pub_id, [])
        preferred_image_kind = (
            "share_image"
            if has_share_image.get(answer_pub_id, False)
            else screenshot_kind.get(answer_pub_id, "missing")
        )
        answer_anchor = (
            native_answer_anchors.get(answer_pub_id)
            if preferred_image_kind == "answer_excerpt_screenshot"
            else answer_anchor_overrides.get(answer_pub_id)
            if preferred_image_kind == "answer_screenshot"
            else None
        )
        output.append(
            {
                "display_number": len(output) + 1,
                "candidate_group_id": group["id"],
                "group_title": group["title"],
                "question": str(chosen.get("query_text") or ""),
                "platform": str(chosen.get("model") or ""),
                "platform_label": MODEL_LABELS.get(
                    str(chosen.get("model") or ""), str(chosen.get("model") or "")
                ),
                "region": str(chosen.get("region") or ""),
                "mode": str(chosen.get("mode") or ""),
                "capture_time": chosen.get("capture_time"),
                "answer_pub_id": answer_pub_id,
                "target_rank": detail["target_rank"],
                "brand_sequence": detail["brands"],
                "response_chars": len(str(chosen.get("response_text") or "")),
                "answer_excerpt": _answer_excerpt(
                    str(chosen.get("response_text") or ""), target_brand
                ),
                "citation_count": len(citation_rows),
                # Keep the complete captured URL list in delivery facts.  The body
                # shows only a short preview, while Appendix C renders every URL for
                # the three selected representative answers.
                "citations": [
                    {
                        "ordinal": citation.get("ordinal"),
                        "host": citation.get("host") or "（未知）",
                        "title": citation.get("title"),
                        "url": citation.get("canonical_url") or citation.get("original_url") or "",
                        "cited_text": citation.get("cited_text"),
                    }
                    for citation in citation_rows
                ],
                "search_queries": search_queries.get(answer_pub_id, []),
                "has_answer_screenshot": has_screenshot.get(answer_pub_id, False),
                "has_share_image": has_share_image.get(answer_pub_id, False),
                "preferred_image_kind": preferred_image_kind,
                "answer_anchor": answer_anchor,
                "selection_note": (
                    "代表页只用于展示回答及其所列引用：三组按固定平台轮换，组内优先选择"
                    "官方分享图片；不使用品牌是否提及或位次，不参与任何统计指标。"
                ),
            }
        )
    return output


def enrich_service1_v2_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    facts: dict[str, Any],
    answer_anchor_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach complete delivery facts under ``service1.delivery_v2``."""

    answer_anchor_overrides = answer_anchor_overrides or {}
    service1 = facts["service1"]
    registry = list(service1.get("answer_registry") or [])
    balanced_ids = [str(row["answer_pub_id"]) for row in registry]
    selected_ids = [
        str(row["answer_pub_id"]) for row in registry if row.get("selected_for_main_report")
    ]
    answers = _load_answers(dsn, tenant_pub_id, balanced_ids)
    answers_by_id = {str(row["pub_id"]): row for row in answers}
    selected_answers = [answers_by_id[value] for value in selected_ids if value in answers_by_id]
    extracts = brandrank_service.fetch_brand_extracts(
        dsn, tenant_pub_id, balanced_ids, str(facts["domain"])
    )
    citations = _load_detailed_citations(dsn, tenant_pub_id, balanced_ids)
    has_screenshot, screenshot_kind, has_share_image, search_queries = _load_answer_auxiliary(
        dsn, tenant_pub_id, balanced_ids
    )
    evidence_inventory = _load_evidence_inventory(dsn, tenant_pub_id, balanced_ids)
    sampling_provenance = _load_sampling_provenance(dsn, tenant_pub_id, balanced_ids)
    citation_snapshots = _load_citation_snapshots(dsn, tenant_pub_id, balanced_ids)
    rules = load_domain(str(facts["domain"]))
    entity_master = load_entity_master(str(facts["domain"]))
    target_brand = str(facts["target_brand"])
    native_answer_anchors = _load_native_answer_anchors(dsn, tenant_pub_id, answers, target_brand)
    governed_target = normalize_answer_entities(
        [target_brand],
        rules=rules,
        master=entity_master,
        target_brand=target_brand,
        named_competitors=list(facts.get("competitors") or []),
    )
    normalized_target = (
        str(governed_target[0]["canonical_name"]) if governed_target else target_brand
    )
    candidate_groups = list(service1.get("candidate_groups") or [])
    question_group: dict[str, dict[str, Any]] = {
        str(question): group
        for group in candidate_groups
        for question in list(group.get("questions") or [])
    }

    row_details: dict[str, dict[str, Any]] = {}
    governed_extracts: dict[str, dict[str, Any]] = {}
    for answer in answers:
        answer_pub_id = str(answer["pub_id"])
        extract = extracts.get(answer_pub_id) or {}
        raw_brands = extract.get("brands") if extract.get("status") == "ok" else []
        entities = normalize_answer_entities(
            raw_brands if isinstance(raw_brands, list) else [],
            rules=rules,
            master=entity_master,
            target_brand=target_brand,
            named_competitors=list(facts.get("competitors") or []),
        )
        brands = [str(row["canonical_name"]) for row in entities]
        governed_extracts[answer_pub_id] = {
            "status": extract.get("status"),
            "brands": brands,
        }
        target_rank = brands.index(normalized_target) + 1 if normalized_target in brands else None
        group = question_group.get(str(answer.get("query_text") or ""), {})
        row_details[answer_pub_id] = {
            "candidate_group_id": group.get("id"),
            "group_title": group.get("title"),
            "brands": brands,
            "entities": entities,
            "target_rank": target_rank,
            "citation_count": len(citations.get(answer_pub_id, [])),
            "has_answer_screenshot": has_screenshot.get(answer_pub_id, False),
            "has_share_image": has_share_image.get(answer_pub_id, False),
        }

    overall_analysis = _scope_analysis(
        selected_answers,
        extracts=governed_extracts,
        citations=citations,
        domain=str(facts["domain"]),
        target_brand=target_brand,
        competitors=list(facts.get("competitors") or []),
    )
    by_region: dict[str, dict[str, Any]] = {}
    for region in service1.get("primary_regions") or []:
        scoped = [row for row in selected_answers if row.get("region") == region]
        result = _scope_analysis(
            scoped,
            extracts=governed_extracts,
            citations=citations,
            domain=str(facts["domain"]),
            target_brand=target_brand,
            competitors=list(facts.get("competitors") or []),
        )
        by_region[str(region)] = {
            **_target_summary(result, len(scoped)),
            "answers_with_citation": sum(1 for row in scoped if citations.get(str(row["pub_id"]))),
            "citation_references": sum(
                len(citations.get(str(row["pub_id"]), [])) for row in scoped
            ),
        }

    selected_groups = sorted(
        [group for group in candidate_groups if group.get("selected_for_main_report")],
        key=lambda group: int(group.get("index") or 0),
    )
    question_rows: list[dict[str, Any]] = []
    for group in selected_groups:
        for question_index, question in enumerate(group.get("questions") or [], 1):
            scoped = [row for row in selected_answers if row.get("query_text") == question]
            target_ranks = [
                row_details[str(row["pub_id"])]["target_rank"]
                for row in scoped
                if row_details[str(row["pub_id"])]["target_rank"] is not None
            ]
            question_rows.append(
                {
                    "group_id": group["id"],
                    "group_title": group["title"],
                    "group_index": group["index"],
                    "question_index": question_index,
                    "question": question,
                    "answers": len(scoped),
                    "mentions": len(target_ranks),
                    "appearance_rate": round(len(target_ranks) / len(scoped) * 100, 2)
                    if scoped
                    else 0,
                    "avg_rank": round(mean(target_ranks), 2) if target_ranks else None,
                    "best_rank": min(target_ranks) if target_ranks else None,
                    "top1": sum(rank <= 1 for rank in target_ranks),
                    "top3": sum(rank <= 3 for rank in target_ranks),
                    "top5": sum(rank <= 5 for rank in target_ranks),
                    "answers_with_citation": sum(
                        1 for row in scoped if citations.get(str(row["pub_id"]))
                    ),
                    "citation_references": sum(
                        len(citations.get(str(row["pub_id"]), [])) for row in scoped
                    ),
                }
            )

    ranks = [
        row_details[str(row["pub_id"])]["target_rank"]
        for row in selected_answers
        if row_details[str(row["pub_id"])]["target_rank"] is not None
    ]
    rank_distribution = [
        {"label": "第 1 位", "count": sum(rank == 1 for rank in ranks)},
        {"label": "第 2–3 位", "count": sum(2 <= rank <= 3 for rank in ranks)},
        {"label": "第 4–5 位", "count": sum(4 <= rank <= 5 for rank in ranks)},
        {"label": "第 6–10 位", "count": sum(6 <= rank <= 10 for rank in ranks)},
        {"label": "第 11 位以后", "count": sum(rank >= 11 for rank in ranks)},
        {"label": "未提及", "count": len(selected_answers) - len(ranks)},
    ]

    full_brand_ranking = []
    competitors = {str(value) for value in facts.get("competitors") or []}
    for row in list(overall_analysis["overall"]["merged"]):
        brand = str(row.get("brand") or "")
        full_brand_ranking.append(
            {
                **row,
                "is_target": brand == normalized_target,
                "is_named_competitor": brand in competitors,
            }
        )

    selected_group_ids = {str(group["id"]) for group in selected_groups}
    sample_registry = []
    repeat_numbers, repeat_independence_reasons = assign_repeats(selected_answers)
    cell_runs: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    cell_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in selected_answers:
        cell = (
            str(row.get("query_text") or ""),
            str(row.get("model") or ""),
            str(row.get("region") or ""),
        )
        cell_counts[cell] += 1
        if row.get("run_pub_id"):
            cell_runs[cell].add(str(row["run_pub_id"]))
    ordered_answers = sorted(
        selected_answers,
        key=lambda row: (
            int(question_group.get(str(row.get("query_text") or ""), {}).get("index") or 0),
            str(row.get("query_text") or ""),
            str(row.get("model") or ""),
            str(row.get("region") or ""),
        ),
    )
    for display_number, row in enumerate(ordered_answers, 1):
        answer_pub_id = str(row["pub_id"])
        detail = row_details[answer_pub_id]
        if detail["candidate_group_id"] not in selected_group_ids:
            continue
        response_text = str(row.get("response_text") or "")
        response_payload = response_text.encode()
        assets = evidence_inventory.get(answer_pub_id, [])
        screenshots = [
            asset
            for asset in assets
            if asset["kind"] in {"answer_screenshot", "answer_excerpt_screenshot"}
        ]
        share_images = [asset for asset in assets if asset["kind"] == "share_image"]
        provenance = sampling_provenance.get(answer_pub_id, {})
        cell = (
            str(row.get("query_text") or ""),
            str(row.get("model") or ""),
            str(row.get("region") or ""),
        )
        sample_registry.append(
            {
                "display_number": display_number,
                "sample_id": f"S1-{display_number:04d}",
                "answer_pub_id": answer_pub_id,
                "group_title": detail["group_title"],
                "question": str(row.get("query_text") or ""),
                "platform": str(row.get("model") or ""),
                "platform_label": MODEL_LABELS.get(
                    str(row.get("model") or ""), str(row.get("model") or "")
                ),
                "region": str(row.get("region") or ""),
                "mode": str(row.get("mode") or ""),
                "capture_time": row.get("capture_time"),
                "repeat_no": repeat_numbers.get(answer_pub_id),
                "run_id": str(row.get("run_pub_id") or ""),
                "independent_repeat": (cell_counts[cell] == 2 and len(cell_runs[cell]) == 2),
                "account_id_masked": provenance.get("account_id_masked"),
                "browser_instance": provenance.get("browser_instance"),
                "egress_region_gb": provenance.get("egress_region_gb"),
                "egress_audit": provenance.get("egress_audit"),
                "mentioned": detail["target_rank"] is not None,
                "target_rank": detail["target_rank"],
                "entities": detail["entities"],
                "citation_count": detail["citation_count"],
                "citations": [
                    {
                        "ordinal": citation.get("ordinal"),
                        "host": citation.get("host"),
                        "title": citation.get("title"),
                        "url": citation.get("canonical_url") or citation.get("original_url") or "",
                        "cited_text": citation.get("cited_text"),
                    }
                    for citation in citations.get(answer_pub_id, [])
                ],
                "citation_snapshots": citation_snapshots.get(answer_pub_id, []),
                "response_chars": len(response_text),
                "response_text": response_text,
                "answer_evidence": {
                    "path": f"answers/S1-{display_number:04d}.txt",
                    "sha256": sha256(response_payload).hexdigest(),
                    "byte_size": len(response_payload),
                    "mime_type": "text/plain; charset=utf-8",
                },
                "screenshot_evidence": screenshots,
                "share_image_evidence": share_images,
                "all_evidence": assets,
                "has_answer_screenshot": detail["has_answer_screenshot"],
                "has_share_image": detail["has_share_image"],
            }
        )

    source_overall = dict(overall_analysis["sources"]["overall"])
    source_positions: dict[str, list[int]] = defaultdict(list)
    for answer in selected_answers:
        for citation in citations.get(str(answer["pub_id"]), []):
            entry = adapter.citation_to_source_entry(citation)
            source_positions[str(entry["sitename"])].append(int(entry["index"]))
    source_rows = []
    for row in list(source_overall.get("sources") or []):
        positions = source_positions.get(str(row.get("sitename") or ""), [])
        source_rows.append(
            {
                **row,
                "avg_ordinal": round(mean(positions), 2) if positions else None,
                "first_position_count": sum(position == 1 for position in positions),
            }
        )
    source_rows.sort(
        key=lambda row: (
            -int(row.get("count") or 0),
            float(row.get("avg_ordinal") or 10**9),
            str(row.get("sitename") or ""),
        )
    )
    for rank, row in enumerate(source_rows, 1):
        row["rank"] = rank
    source_overall["sources"] = source_rows
    representative_answers = _representative_rows(
        selected_groups,
        selected_answers,
        row_details=row_details,
        citations=citations,
        has_screenshot=has_screenshot,
        screenshot_kind=screenshot_kind,
        has_share_image=has_share_image,
        search_queries=search_queries,
        target_brand=target_brand,
        answer_anchor_overrides=answer_anchor_overrides,
        native_answer_anchors=native_answer_anchors,
    )
    screenshot_count = sum(
        1 for row in selected_answers if has_screenshot.get(str(row["pub_id"]), False)
    )
    share_image_count = sum(
        1 for row in selected_answers if has_share_image.get(str(row["pub_id"]), False)
    )
    delivery_v2 = {
        "schema_version": "service1-delivery-v2",
        "scope": {
            "selected_groups": len(selected_groups),
            "questions": sum(len(group.get("questions") or []) for group in selected_groups),
            "platforms": len(service1.get("primary_models") or []),
            "regions": len(service1.get("primary_regions") or []),
            "current_repetitions": int(service1.get("current_repetitions_per_cell") or 0),
            "answers": len(selected_answers),
            "extract_ok": sum(
                1
                for row in selected_answers
                if (extracts.get(str(row["pub_id"])) or {}).get("status") == "ok"
            ),
            "answer_screenshots": screenshot_count,
            "share_images": share_image_count,
            "answers_with_citation": sum(
                1 for row in selected_answers if citations.get(str(row["pub_id"]))
            ),
            "citation_references": sum(
                len(citations.get(str(row["pub_id"]), [])) for row in selected_answers
            ),
            "brands_observed": len(full_brand_ranking),
        },
        "by_region": by_region,
        "question_rows": question_rows,
        "rank_distribution": rank_distribution,
        "full_brand_ranking": full_brand_ranking,
        "sources": source_overall,
        "representative_answers": representative_answers,
        "sample_registry": sample_registry,
        "evidence_policy": (
            f"主样本官方分享图片覆盖 {share_image_count}/{len(selected_answers)}，"
            f"回答图片覆盖 {screenshot_count}/{len(selected_answers)}；正文代表图优先"
            "使用官方分享图片，其后使用干净回答证据图，仅在两者缺失时使用历史运行页截图。"
            f"正文展示的 {len(representative_answers)} 张代表图只为便于阅读，"
            f"全量 {len(selected_answers)} 条主样本逐条列于附录 B；三条代表回答的"
            "全部信源 URL 列于附录 C。"
        ),
    }
    service1["delivery_v2"] = delivery_v2
    entity_ranking = ranked_entities(sample_registry)
    competitor_ranking = [row for row in entity_ranking if row["competitor_eligible"]]
    target_metric = entity_metric(sample_registry, normalized_target)
    competitor_comparison = comparable_competitors(
        sample_registry,
        target_brand=normalized_target,
        limit=5,
    )
    consistency = repeat_consistency(sample_registry, target_brand=normalized_target)
    quotation_ready, quotation_reasons = quotation_gate(
        selected_groups=selected_groups,
        sample_rows=sample_registry,
        scope_registration=service1.get("scope_registration") or {},
    )

    def scoped_metrics(field: str, values: Iterable[str]) -> dict[str, dict[str, Any]]:
        return {
            str(value): entity_metric(
                [row for row in sample_registry if str(row.get(field) or "") == str(value)],
                normalized_target,
            )
            for value in values
        }

    governed_question_rows = []
    for group in selected_groups:
        for question_index, question in enumerate(group.get("questions") or [], 1):
            scoped = [row for row in sample_registry if row["question"] == question]
            governed_question_rows.append(
                {
                    "group_title": group["title"],
                    "group_index": group["index"],
                    "question_index": question_index,
                    "question": question,
                    **entity_metric(scoped, normalized_target),
                    "answers_with_citation": sum(bool(row["citations"]) for row in scoped),
                }
            )

    representative_platforms = {str(row.get("platform")) for row in representative_answers}
    service1["delivery_v3"] = {
        "schema_version": "service1-delivery-v3",
        "scope": {
            **delivery_v2["scope"],
            "scope_label": (service1.get("scope_registration") or {}).get("scope_label"),
            "represents_overall_brand": bool(
                (service1.get("scope_registration") or {}).get("represents_overall_brand")
            ),
            "registration_status": (service1.get("scope_registration") or {}).get("status"),
            "entity_rows_audited": len(entity_ranking),
            "competitor_entities": len(competitor_ranking),
            "unclassified_entities": sum(row["entity_type"] == "unknown" for row in entity_ranking),
        },
        "selected_groups": selected_groups,
        "target": target_metric,
        "by_platform": scoped_metrics("platform", service1.get("primary_models") or []),
        "by_region": scoped_metrics("region", service1.get("primary_regions") or []),
        "by_group": scoped_metrics(
            "group_title", [str(group["title"]) for group in selected_groups]
        ),
        "question_rows": governed_question_rows,
        "entity_ranking": entity_ranking,
        "competitor_ranking": competitor_ranking,
        "competitor_comparison": competitor_comparison,
        "repeat_consistency": consistency,
        "repeat_independence_reasons": repeat_independence_reasons,
        "source_cooccurrence": source_cooccurrence(sample_registry, target_brand=normalized_target),
        "sources": source_overall,
        "representative_answers": representative_answers,
        "representative_platforms_complete": representative_platforms
        == {"doubao", "deepseek", "yiyan"},
        "sample_registry": sample_registry,
        "quotation_gate": {
            "status": "ready" if quotation_ready else "blocked",
            "reasons": list(quotation_reasons),
        },
        "metric_policy": {
            "answer_denominator": True,
            "within_answer_canonical_entity_deduplication": True,
            "competitor_eligibility_filter": True,
            "percent_display_digits": 1,
            "uncertainty": (
                "Wilson 95% interval for mention rate; repeat-pair agreement reported separately"
            ),
        },
    }
    return facts


def load_service1_screenshot_payloads(
    *,
    dsn: str,
    tenant_pub_id: str,
    answer_pub_ids: list[str],
    endpoint: str,
    access_key: str,
    secret_key: str,
) -> dict[str, bytes]:
    """Load preferred representative images: official share first, screenshot fallback."""

    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT ON (er.from_pub_id)
                   er.from_pub_id, ea.object_key, ea.sha256, ea.kind
            FROM evidence.evidence_relation er
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
            WHERE er.tenant_pub_id=%s AND er.from_pub_id=ANY(%s::text[])
              AND ea.kind IN ('share_image','answer_excerpt_screenshot','answer_screenshot')
              AND ea.mime_type LIKE 'image/%%'
              AND ea.deleted_at IS NULL
            ORDER BY er.from_pub_id,
                     CASE ea.kind
                       WHEN 'share_image' THEN 0
                       WHEN 'answer_excerpt_screenshot' THEN 1
                       ELSE 2
                     END,
                     ea.capture_time DESC, ea.pub_id DESC
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    store = ContentAddressedObjectStore(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
    )
    return {
        str(row["from_pub_id"]): store.get_verified(str(row["object_key"]), str(row["sha256"]))
        for row in rows
    }
