"""Server-verified fact sources and exact visual anchors for Service 2 findings.

This stage runs after relation analysis on the public-source queue.  It never calls
an LLM.  Every fact-check URL is redirect/peer checked, bounded, frozen in CAS and
tenant/project resolved.  Visual evidence is rendered from that safely fetched HTML
with all active content and subrequests disabled, then bound to the exact immutable
snapshot offsets of the selected quote occurrence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any

import httpx
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.psycopg import tenant_connection
from lxml import etree, html
from PIL import Image, ImageDraw
from psycopg.rows import dict_row
from temporalio import activity

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.security.public_http import (
    PublicHttpDocument,
    PublicHttpRejected,
    fetch_public_http,
)
from domain.security.redaction import safe_exception_summary
from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.source_fetch import extract_text_from_html

_MAX_FACT_SOURCES = 20
_MAX_DOCUMENT_BYTES = 5_242_880
_FACTCHECK_ADAPTER = "service2-factcheck-public-http-v1"
_VISUAL_ADAPTER = "service2-exact-quote-offline-render-v2"

_LOCATE_QUOTE_JS = r"""
async ({context, contextOccurrence, quote, quoteOffset}) => {
  const root = document.body;
  if (!root || !context || !quote || quoteOffset < 0) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || ['SCRIPT','STYLE','NOSCRIPT','TEXTAREA'].includes(parent.tagName)) {
        return NodeFilter.FILTER_REJECT;
      }
      const style = getComputedStyle(parent);
      const rect = parent.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden'
        && rect.width > 0 && rect.height > 0
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  const nodes = [];
  let combined = '';
  let node;
  while ((node = walker.nextNode())) {
    const value = node.nodeValue || '';
    nodes.push({node, start: combined.length, end: combined.length + value.length});
    combined += value;
  }
  const starts = [];
  let cursor = 0;
  while ((cursor = combined.indexOf(context, cursor)) >= 0) {
    starts.push(cursor);
    cursor += Math.max(1, context.length);
  }
  if (contextOccurrence < 0 || contextOccurrence >= starts.length) return null;
  const contextStart = starts[contextOccurrence];
  if (context.slice(quoteOffset, quoteOffset + quote.length) !== quote) return null;
  const start = contextStart + quoteOffset;
  const end = start + quote.length;
  if (combined.slice(start, end) !== quote) return null;
  const first = nodes.find((entry) => entry.start <= start && start < entry.end);
  const last = [...nodes].reverse().find((entry) => entry.start < end && end <= entry.end);
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, start - first.start);
  range.setEnd(last.node, end - last.start);
  const parent = first.node.parentElement;
  if (!parent) return null;
  parent.scrollIntoView({block: 'center', inline: 'nearest'});
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const rect = range.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  return {
    x: rect.x, y: rect.y, width: rect.width, height: rect.height,
    viewport_width: window.innerWidth, viewport_height: window.innerHeight,
    dom_context_occurrences: starts.length
  };
}
"""


@dataclass(frozen=True, slots=True)
class Service2EvidencePageInput:
    tenant_pub_id: str
    project_pub_id: str
    batch_pub_id: str
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class Service2EvidencePageResult:
    processed: int
    next_cursor: str | None
    has_more: bool
    verified_fact_sources: int = 0
    visual_verified: int = 0
    failure_codes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class QuoteCapture:
    png_bytes: bytes
    bbox: dict[str, float]
    image_width: int
    image_height: int


def _dsn() -> str:
    settings = get_settings()
    value = os.getenv("S02_POSTGRES_DSN") or (settings.worker_postgres_dsn or settings.postgres_dsn)
    return value.replace("postgresql+psycopg://", "postgresql://")


def _stable_pub_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode()).hexdigest()[:26]}"


def _exact_occurrence(source_text: str, value: str, start: int, *, code: str) -> int:
    starts: list[int] = []
    cursor = 0
    while (cursor := source_text.find(value, cursor)) >= 0:
        starts.append(cursor)
        cursor += max(1, len(value))
    try:
        return starts.index(start)
    except ValueError as exc:
        raise ValueError(code) from exc


def _decode_html(payload: bytes, preferred_text: str = "") -> str:
    decoded: str | None = None
    for encoding in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            candidate = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        if preferred_text in candidate or decoded is None:
            decoded = candidate
        if preferred_text and preferred_text in candidate:
            break
    if decoded is None:
        raise ValueError("visual_html_decode_failed")
    return decoded


def sanitize_html_for_offline_render(payload: bytes, quote: str) -> str:
    """Remove executable/embedded content while retaining real source text/layout."""

    decoded = _decode_html(payload, quote)
    document = html.document_fromstring(decoded)
    for element in document.xpath("//script|//iframe|//object|//embed|//form|//base|//meta"):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    for element in document.iter():
        for name in tuple(element.attrib):
            lowered = name.lower()
            if lowered.startswith("on") or lowered in {
                "src",
                "srcset",
                "href",
                "action",
                "formaction",
                "poster",
            }:
                del element.attrib[name]
    return str(etree.tostring(document, encoding="unicode", method="html"))


def render_exact_quote_capture(
    *,
    html_text: str,
    context_text: str,
    context_occurrence: int,
    quote: str,
    quote_offset: int,
) -> QuoteCapture:
    """Render inert fetched HTML and draw a box around the chosen real DOM range."""

    _driver, sync_playwright, _timeout_error = load_sync_browser_driver()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--lang=zh-CN"])
        try:
            context = browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(html_text, wait_until="domcontentloaded", timeout=20_000)
            projection = page.evaluate(
                _LOCATE_QUOTE_JS,
                {
                    "context": context_text,
                    "contextOccurrence": context_occurrence,
                    "quote": quote,
                    "quoteOffset": quote_offset,
                },
            )
            if not isinstance(projection, dict):
                raise ValueError("visual_quote_not_found_in_safe_dom")
            payload = page.screenshot(type="png", full_page=False)
        finally:
            browser.close()
    with Image.open(BytesIO(payload)) as source:
        source.load()
        width, height = source.size
        x = float(projection["x"])
        y = float(projection["y"])
        box_width = float(projection["width"])
        box_height = float(projection["height"])
        left = max(0, int(x - 100))
        top = max(0, int(y - 140))
        right = min(width, int(x + box_width + 100))
        bottom = min(height, int(y + box_height + 180))
        if right <= left or bottom <= top:
            raise ValueError("visual_quote_bbox_invalid")
        cropped = source.convert("RGB").crop((left, top, right, bottom))
        draw = ImageDraw.Draw(cropped)
        relative = {
            "x": max(0.0, x - left),
            "y": max(0.0, y - top),
            "width": min(box_width, float(right - left)),
            "height": min(box_height, float(bottom - top)),
        }
        draw.rectangle(
            (
                relative["x"],
                relative["y"],
                relative["x"] + relative["width"],
                relative["y"] + relative["height"],
            ),
            outline=(220, 38, 38),
            width=4,
        )
        output = BytesIO()
        cropped.save(output, format="PNG", optimize=True)
    return QuoteCapture(
        png_bytes=output.getvalue(),
        bbox=relative,
        image_width=right - left,
        image_height=bottom - top,
    )


def _load_page(item: Service2EvidencePageInput) -> tuple[dict[str, Any] | None, bool]:
    with tenant_connection(_dsn(), item.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT finding.*,batch.pub_id AS batch_pub_id,project.pub_id AS project_pub_id,
                   snapshot.body_object_key,snapshot.body_sha256,snapshot.text_sha256 AS
                     stored_snapshot_text_sha256,
                   item.canonical_url,document.pub_id AS source_document_pub_id
            FROM platform.service2_relation_finding finding
            JOIN platform.service2_corpus_batch batch ON batch.id=finding.batch_id
            JOIN platform.project project ON project.id=finding.project_id
            JOIN platform.service2_corpus_item item ON item.id=finding.corpus_item_id
            JOIN platform.source_page_snapshot snapshot ON snapshot.id=finding.snapshot_id
            LEFT JOIN platform.source_document document ON document.id=snapshot.source_document_id
            WHERE batch.pub_id=%s AND project.pub_id=%s
              AND (%s IS NULL OR finding.pub_id>%s)
            ORDER BY finding.pub_id LIMIT 2
            """,
            (item.batch_pub_id, item.project_pub_id, item.cursor, item.cursor),
        ).fetchall()
    return (dict(rows[0]) if rows else None, len(rows) > 1)


def _fetch_document(url: str) -> PublicHttpDocument:
    with httpx.Client(
        timeout=httpx.Timeout(20.0),
        trust_env=False,
        headers={"User-Agent": "GEO-Service2-Evidence/1.0", "Accept": "text/html,*/*;q=0.5"},
    ) as client:
        return fetch_public_http(url, client=client, max_redirects=5, max_bytes=_MAX_DOCUMENT_BYTES)


def _provenance(*, adapter: str, capture_time: datetime, public: bool) -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.WEB,
        authorization_scope=(),
        adapter_version=adapter,
        capture_time=capture_time,
        access_class=AccessClass.PUBLIC if public else AccessClass.CUSTOMER_PRIVATE,
    )


@activity.defn
def enrich_service2_evidence_page(
    item: Service2EvidencePageInput,
) -> Service2EvidencePageResult:
    row, has_more = _load_page(item)
    if row is None:
        return Service2EvidencePageResult(0, item.cursor, False)
    finding_pub_id = str(row["pub_id"])
    activity.heartbeat(finding_pub_id, "evidence_enrichment")
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    evidence_service = EvidenceService(dsn=_dsn(), store=store)
    now = datetime.now(UTC)
    failures: list[str] = []
    verified_rows: list[dict[str, Any]] = []
    fact_documents: list[tuple[PublicHttpDocument, dict[str, Any]]] = []
    raw_fact_evidence = row["factcheck_evidence"]
    if row["factcheck_verdict"] in {"supported", "refuted", "mixed"}:
        candidates = raw_fact_evidence if isinstance(raw_fact_evidence, list) else []
        for source in candidates[:_MAX_FACT_SOURCES]:
            if not isinstance(source, dict) or not isinstance(source.get("url"), str):
                continue
            if source.get("verification_status") == "verified":
                verified_rows.append(dict(source))
                continue
            try:
                document = _fetch_document(str(source["url"]))
                fact_documents.append((document, source))
            except (PublicHttpRejected, httpx.HTTPError, ValueError) as exc:
                failures.append(safe_exception_summary(exc) or "factcheck_source_fetch_failed")

    visual_document: PublicHttpDocument | None = None
    visual_capture: QuoteCapture | None = None
    if row["visual_validation_status"] != "verified":
        try:
            visual_document = _fetch_document(str(row["canonical_url"]))
            if visual_document.mime_type not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("visual_source_not_html")
            source_text = store.get_verified(
                str(row["body_object_key"]), str(row["body_sha256"])
            ).decode("utf-8")
            expected_text_hash = str(row["snapshot_text_sha256"])
            if (
                expected_text_hash != str(row["stored_snapshot_text_sha256"])
                or sha256(source_text.encode()).hexdigest() != expected_text_hash
            ):
                raise ValueError("visual_frozen_snapshot_text_hash_mismatch")
            decoded_html = _decode_html(visual_document.payload, str(row["evidence_quote"]))
            fetched_text = extract_text_from_html(decoded_html)
            if fetched_text != source_text:
                raise ValueError("visual_refetch_does_not_match_frozen_snapshot")
            context_occurrence = _exact_occurrence(
                source_text,
                str(row["context_text"]),
                int(row["context_start"]),
                code="visual_context_offset_not_in_snapshot",
            )
            inert_html = sanitize_html_for_offline_render(
                visual_document.payload, str(row["evidence_quote"])
            )
            visual_capture = render_exact_quote_capture(
                html_text=inert_html,
                context_text=str(row["context_text"]),
                context_occurrence=context_occurrence,
                quote=str(row["evidence_quote"]),
                quote_offset=int(row["quote_start"]) - int(row["context_start"]),
            )
        except (PublicHttpRejected, httpx.HTTPError, UnicodeDecodeError, ValueError) as exc:
            failures.append(safe_exception_summary(exc) or "visual_capture_failed")

    with tenant_connection(_dsn(), item.tenant_pub_id, row_factory=dict_row) as connection:
        for document, source in fact_documents:
            evidence_pub_id = _stable_pub_id(
                "evd",
                f"factcheck|{finding_pub_id}|{document.final_url}|"
                f"{sha256(document.payload).hexdigest()}",
            )
            stored = evidence_service.capture(
                evidence_pub_id=evidence_pub_id,
                tenant_pub_id=item.tenant_pub_id,
                project_pub_id=item.project_pub_id,
                kind="service2_factcheck_source",
                payload=document.payload,
                mime_type=document.mime_type,
                source_url=document.final_url,
                provenance=_provenance(adapter=_FACTCHECK_ADAPTER, capture_time=now, public=True),
                db_connection=connection,
            )
            store.get_verified(stored.key, stored.sha256)
            connection.execute(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                VALUES (%s,%s,%s,'service2_factcheck_source')
                ON CONFLICT DO NOTHING
                """,
                (item.tenant_pub_id, finding_pub_id, evidence_pub_id),
            )
            verified_rows.append(
                {
                    "title": str(source.get("title") or "")[:200],
                    "url": document.requested_url,
                    "source_url": document.final_url,
                    "evidence_pub_id": evidence_pub_id,
                    "evidence_type": "service2_factcheck_source",
                    "verification_status": "verified",
                    "content_sha256": stored.sha256,
                    "retrieved_at": now.isoformat(),
                }
            )

        visual_status = str(row["visual_validation_status"])
        visual_anchor = row["visual_anchor"] if isinstance(row["visual_anchor"], dict) else {}
        if visual_document is not None and visual_capture is not None:
            page_evidence_pub_id = _stable_pub_id(
                "evd",
                f"visual-page|{finding_pub_id}|{sha256(visual_document.payload).hexdigest()}",
            )
            page_stored = evidence_service.capture(
                evidence_pub_id=page_evidence_pub_id,
                tenant_pub_id=item.tenant_pub_id,
                project_pub_id=item.project_pub_id,
                kind="service2_visual_page_snapshot",
                payload=visual_document.payload,
                mime_type=visual_document.mime_type,
                source_url=visual_document.final_url,
                provenance=_provenance(
                    adapter=_VISUAL_ADAPTER,
                    # This payload is the independently re-fetched HTML that
                    # matched the frozen text, not the historical snapshot
                    # payload itself. Preserve the real acquisition time.
                    capture_time=now,
                    public=True,
                ),
                db_connection=connection,
            )
            store.get_verified(page_stored.key, page_stored.sha256)
            evidence_pub_id = _stable_pub_id(
                "evd",
                f"visual|{finding_pub_id}|{row['snapshot_text_sha256']}|"
                f"{row['quote_start']}|{row['quote_end']}",
            )
            stored = evidence_service.capture(
                evidence_pub_id=evidence_pub_id,
                tenant_pub_id=item.tenant_pub_id,
                project_pub_id=item.project_pub_id,
                kind="service2_exact_quote_screenshot",
                payload=visual_capture.png_bytes,
                mime_type="image/png",
                source_url=visual_document.final_url,
                provenance=_provenance(
                    adapter=_VISUAL_ADAPTER,
                    # The screenshot is rendered during this enrichment run;
                    # its anchor still carries the original frozen text hash.
                    capture_time=now,
                    public=False,
                ),
                db_connection=connection,
            )
            store.get_verified(stored.key, stored.sha256)
            anchor_pub_id = _stable_pub_id(
                "anc", f"{evidence_pub_id}|{row['quote_start']}|{row['quote_end']}"
            )
            bbox = {
                **visual_capture.bbox,
                "confidence": 1.0,
                "image_width": float(visual_capture.image_width),
                "image_height": float(visual_capture.image_height),
            }
            connection.execute(
                """
                INSERT INTO evidence.evidence_anchor
                  (pub_id,tenant_pub_id,evidence_pub_id,text_start,text_end,bbox,quote_hash)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    anchor_pub_id,
                    item.tenant_pub_id,
                    evidence_pub_id,
                    row["quote_start"],
                    row["quote_end"],
                    json.dumps(bbox, sort_keys=True, separators=(",", ":")),
                    row["evidence_quote_hash"],
                ),
            )
            for from_pub_id in (row["source_document_pub_id"], finding_pub_id):
                if from_pub_id:
                    for to_pub_id, relation_type in (
                        (page_evidence_pub_id, "service2_visual_page_snapshot"),
                        (evidence_pub_id, "service2_exact_quote_snapshot"),
                    ):
                        connection.execute(
                            """
                            INSERT INTO evidence.evidence_relation
                              (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING
                            """,
                            (item.tenant_pub_id, from_pub_id, to_pub_id, relation_type),
                        )
            visual_status = "verified"
            visual_anchor = {
                "anchor_pub_id": anchor_pub_id,
                "evidence_pub_id": evidence_pub_id,
                "text_start": int(row["quote_start"]),
                "text_end": int(row["quote_end"]),
                "page_number": None,
                "bbox": visual_capture.bbox,
                "content_sha256": stored.sha256,
                "page_snapshot_evidence_pub_id": page_evidence_pub_id,
                "page_snapshot_sha256": page_stored.sha256,
                "snapshot_text_sha256": str(row["snapshot_text_sha256"]),
                "source_url": visual_document.final_url,
                "capture_adapter": _VISUAL_ADAPTER,
            }
        validation_failures = list(row["validation_failures"] or [])
        validation_failures.extend(failures)
        connection.execute(
            """
            UPDATE platform.service2_relation_finding
            SET factcheck_evidence=%s::jsonb,visual_anchor=%s::jsonb,
                visual_validation_status=%s,validation_failures=%s::jsonb,
                version=version+1,updated_at=now()
            WHERE pub_id=%s AND current_review_state='unreviewed'
            """,
            (
                json.dumps(verified_rows, ensure_ascii=False, sort_keys=True),
                json.dumps(visual_anchor, ensure_ascii=False, sort_keys=True),
                visual_status,
                json.dumps(list(dict.fromkeys(validation_failures)), ensure_ascii=False),
                finding_pub_id,
            ),
        )
        connection.commit()
    return Service2EvidencePageResult(
        processed=1,
        next_cursor=finding_pub_id,
        has_more=has_more,
        verified_fact_sources=len(verified_rows),
        visual_verified=int(visual_status == "verified"),
        failure_codes=tuple(dict.fromkeys(failures)),
    )


__all__ = [
    "QuoteCapture",
    "Service2EvidencePageInput",
    "Service2EvidencePageResult",
    "enrich_service2_evidence_page",
    "render_exact_quote_capture",
    "sanitize_html_for_offline_render",
]
