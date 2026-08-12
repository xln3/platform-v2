"""Browser capture for Service-2 fact-check pages.

This is the missing report-side bridge for the visual idea already implemented by
``post_analysis``.  It does not reuse unrelated historical post-analysis tasks.
Instead, it opens the actual fact-check URL, highlights a verified keyword when the
page contains it, and returns the visible browser screenshot plus an honest capture
status.  Absence of a keyword is never presented as proof that a claim is false.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

_MARK_JS = r"""
(options) => {
  const terms = options && Array.isArray(options.terms) ? options.terms : [];
  const badgeText = options && options.badgeText
    ? String(options.badgeText)
    : '公开网页 · 红框为本页可见核查锚点';
  document.querySelectorAll('[data-geo-source-capture-badge]').forEach(
    (element) => element.remove()
  );
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const output = [];
  let firstMark = null;
  const matchedElements = [];
  for (const rawTerm of terms) {
    const term = normalize(rawTerm);
    if (!term) continue;
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
      {acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || ['SCRIPT','STYLE','NOSCRIPT','TEXTAREA'].includes(parent.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        const style = getComputedStyle(parent);
        const rect = parent.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden'
            || rect.width < 2 || rect.height < 2) {
          return NodeFilter.FILTER_REJECT;
        }
        return normalize(node.nodeValue).includes(term)
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }}
    );
    const node = walker.nextNode();
    if (!node) {
      const candidates = Array.from(document.querySelectorAll('body *')).filter((element) => {
        if (['SCRIPT','STYLE','NOSCRIPT','TEXTAREA'].includes(element.tagName)) return false;
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && rect.width >= 2 && rect.height >= 2
          && normalize(element.innerText).includes(term);
      });
      candidates.sort((left, right) =>
        normalize(left.innerText).length - normalize(right.innerText).length
      );
      const element = candidates[0];
      if (!element) {
        output.push({term, matched: false});
        continue;
      }
      const rect = element.getBoundingClientRect();
      const usable = rect.width <= window.innerWidth * 0.65 && rect.height <= 240;
      if (usable) {
        element.setAttribute('data-s2-source-mark', term);
        element.setAttribute('data-geo-source-capture-usable', 'true');
        element.style.outline = '4px solid #dc2626';
        element.style.outlineOffset = '4px';
        element.style.backgroundColor = '#fff2a8';
        if (!firstMark) firstMark = element;
        matchedElements.push(element);
      }
      output.push({
        term,
        matched: true,
        usable,
        fallback: 'smallest_containing_element',
        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
      });
      continue;
    }
    const text = node.nodeValue || '';
    let index = text.indexOf(rawTerm);
    if (index < 0) index = normalize(text).indexOf(term);
    if (index < 0 || index + term.length > text.length) {
      output.push({term, matched: false});
      continue;
    }
    const range = document.createRange();
    range.setStart(node, index);
    range.setEnd(node, index + term.length);
    const mark = document.createElement('mark');
    mark.setAttribute('data-s2-source-mark', term);
    mark.style.cssText = [
      'background:#fff2a8', 'color:#111827', 'outline:4px solid #dc2626',
      'outline-offset:3px', 'border-radius:2px', 'padding:2px 4px'
    ].join(';');
    try {
      range.surroundContents(mark);
    } catch (_) {
      const fragment = range.extractContents();
      mark.appendChild(fragment);
      range.insertNode(mark);
    }
    const rect = mark.getBoundingClientRect();
    const usable = rect.width >= 2 && rect.height >= 2
      && rect.width <= window.innerWidth * 0.65 && rect.height <= 240;
    if (usable) {
      mark.setAttribute('data-geo-source-capture-usable', 'true');
      if (!firstMark) firstMark = mark;
      matchedElements.push(mark);
    }
    output.push({
      term,
      matched: true,
      usable,
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
    });
  }
  if (firstMark) {
    // Select a readable block around the matched sentence.  The customer report
    // needs the relevant paragraph and nearby context, not a viewport dominated by
    // navigation, hero art or unrelated page content.
    let common = matchedElements[0];
    while (common && !matchedElements.every((element) => common.contains(element))) {
      common = common.parentElement;
    }
    if (!common || common === document.body || common === document.documentElement) {
      common = firstMark.parentElement || firstMark;
    }
    let captureRoot = common;
    let current = common;
    while (current && current !== document.body && current !== document.documentElement) {
      const rect = current.getBoundingClientRect();
      const textLength = normalize(current.innerText).length;
      if (rect.width >= 420 && rect.width <= window.innerWidth * 0.96
          && rect.height >= 45 && rect.height <= 420 && textLength <= 1800) {
        captureRoot = current;
      }
      current = current.parentElement;
    }
    captureRoot.setAttribute('data-geo-source-capture-root', 'true');
  }
  if (firstMark) firstMark.scrollIntoView({block: 'center', inline: 'nearest'});
  const badge = document.createElement('div');
  badge.setAttribute('data-geo-source-capture-badge', 'true');
  badge.textContent = firstMark
    ? badgeText
    : '公开网页 · 本页未找到可逐字标注的核查锚点';
  badge.style.cssText = [
    'position:fixed', 'z-index:2147483647', 'left:18px', 'top:18px',
    'background:#ffffff', 'color:#991b1b', 'border:3px solid #dc2626',
    'border-radius:8px', 'padding:10px 14px', 'font:700 16px sans-serif',
    'box-shadow:0 4px 18px rgba(0,0,0,.22)'
  ].join(';');
  document.documentElement.appendChild(badge);
  return output;
}
"""

_BLOCK_MARKERS = (
    "访问过于频繁",
    "请完成验证",
    "access denied",
    "robot check",
)

_AMBIGUOUS_BLOCK_MARKERS = ("安全验证", "captcha")


def _safe_error(error: BaseException, *, limit: int = 300) -> str:
    value = re.sub(r"\s+", " ", f"{type(error).__name__}: {error}").strip()
    return value[:limit]


def _source_specs(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    specs: list[dict[str, Any]] = []
    for case in cases:
        for source in case.get("factcheck_sources") or []:
            url = str(source.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            specs.append(
                {
                    "url": url,
                    "highlight_terms": [
                        str(value).strip()
                        for value in source.get("highlight_terms") or []
                        if str(value).strip()
                    ],
                }
            )
    return specs


def _capture_anchored_viewport(page: Any) -> tuple[bytes, str]:
    """Crop the visible browser bitmap around real highlighted DOM anchors.

    A locator screenshot can unexpectedly expand an overflow container to several
    thousand pixels.  Cropping the immutable viewport bitmap keeps the evidence
    readable and bounded while retaining generous context above and below the match.
    """

    payload = page.screenshot(type="png", full_page=False)
    marks = page.locator('[data-geo-source-capture-usable="true"]')
    boxes: list[dict[str, float]] = []
    for index in range(marks.count()):
        box = marks.nth(index).bounding_box()
        if not isinstance(box, dict):
            continue
        try:
            boxes.append(
                {
                    "x": float(box["x"]),
                    "y": float(box["y"]),
                    "width": float(box["width"]),
                    "height": float(box["height"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not boxes:
        return payload, "viewport"

    with Image.open(BytesIO(payload)) as source:
        image = source.convert("RGB")
    width, height = image.size
    visible = [
        box
        for box in boxes
        if box["x"] < width
        and box["y"] < height
        and box["x"] + box["width"] > 0
        and box["y"] + box["height"] > 0
    ]
    if not visible:
        return payload, "viewport"

    left_hit = max(0.0, min(box["x"] for box in visible))
    right_hit = min(width, max(box["x"] + box["width"] for box in visible))
    top_hit = max(0.0, min(box["y"] for box in visible))
    bottom_hit = min(height, max(box["y"] + box["height"] for box in visible))
    desired_width = min(width, max(900, int(right_hit - left_hit) + 280))
    desired_height = min(height, max(300, min(680, int(bottom_hit - top_hit) + 340)))
    center_x = (left_hit + right_hit) / 2
    center_y = (top_hit + bottom_hit) / 2
    crop_left = max(0, min(width - desired_width, int(center_x - desired_width / 2)))
    crop_top = max(0, min(height - desired_height, int(center_y - desired_height / 2)))
    crop = image.crop(
        (
            crop_left,
            crop_top,
            crop_left + desired_width,
            crop_top + desired_height,
        )
    )
    stream = BytesIO()
    crop.save(stream, format="PNG", optimize=True)
    cropped_payload = stream.getvalue()
    if len(cropped_payload) < 15_000:
        # A lazy-loaded illustration may leave the tight crop almost white even
        # though the surrounding viewport is informative.  Preserve the marked
        # viewport in that case instead of presenting a context-free floating word.
        return payload, "anchored_viewport_low_information_fallback"
    return cropped_payload, "anchored_context_viewport"


def capture_service2_source_screenshots(
    cases: Iterable[dict[str, Any]],
    *,
    timeout_ms: int = 30_000,
    settle_ms: int = 1_500,
) -> dict[str, dict[str, Any]]:
    """Capture each distinct fact-check URL once.

    Return values contain ``payload`` only for a real browser screenshot.  Callers
    must render the accompanying ``content_status`` and ``error``; a blocked/error
    page is evidence of capture failure, not evidence for or against the claim.
    """

    return capture_source_page_specs(
        _source_specs(cases),
        badge_text="事实核查网页 · 红框为本页可见核查锚点",
        timeout_ms=timeout_ms,
        settle_ms=settle_ms,
    )


def capture_source_page_specs(
    specs: Iterable[dict[str, Any]],
    *,
    badge_text: str,
    timeout_ms: int = 30_000,
    settle_ms: int = 1_500,
) -> dict[str, dict[str, Any]]:
    """Capture explicitly described public pages at a readable text anchor.

    This generic entry point is shared by the risk fact-check report and the
    official-site adoption report.  The caller supplies only URL and exact terms;
    failed, blocked, HTTP-error and unanchored captures remain metadata rather than
    customer evidence.
    """

    from playwright.sync_api import sync_playwright

    normalized_specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in specs:
        url = str(raw.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized_specs.append(
            {
                "url": url,
                "highlight_terms": [
                    str(value).strip()
                    for value in raw.get("highlight_terms") or []
                    if str(value).strip()
                ],
            }
        )

    output: dict[str, dict[str, Any]] = {}
    if not normalized_specs:
        return output
    captured_at = datetime.now(UTC).isoformat()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
                locale="zh-CN",
            )
            for spec in normalized_specs:
                url = str(spec["url"])
                page = context.new_page()
                try:
                    transport_fallback = None
                    response = None
                    transient_markers = (
                        "ERR_CONNECTION_CLOSED",
                        "ERR_CONNECTION_RESET",
                        "ERR_HTTP2_PROTOCOL_ERROR",
                        "ERR_TIMED_OUT",
                    )
                    for attempt in range(3):
                        try:
                            response = page.goto(
                                url,
                                wait_until="domcontentloaded",
                                timeout=timeout_ms,
                            )
                            break
                        except Exception as first_error:
                            message = str(first_error)
                            if attempt < 2 and any(
                                marker in message for marker in transient_markers
                            ):
                                page.close()
                                page = context.new_page()
                                page.wait_for_timeout(700 * (attempt + 1))
                                continue
                            if url.startswith("https://") and "ERR_CONNECTION_REFUSED" in message:
                                fallback_url = "http://" + url.removeprefix("https://")
                                page.close()
                                page = context.new_page()
                                response = page.goto(
                                    fallback_url,
                                    wait_until="domcontentloaded",
                                    timeout=timeout_ms,
                                )
                                transport_fallback = "https_to_http_after_connection_refused"
                                break
                            raise
                    page.wait_for_timeout(settle_ms)
                    body_text = ""
                    try:
                        body_text = page.locator("body").inner_text(timeout=5_000)
                    except Exception:
                        body_text = ""
                    lowered = body_text.lower()
                    hard_block_markers = [marker for marker in _BLOCK_MARKERS if marker in lowered]
                    ambiguous_block_markers = [
                        marker for marker in _AMBIGUOUS_BLOCK_MARKERS if marker in lowered
                    ]
                    http_status = response.status if response is not None else None
                    if http_status is not None and http_status >= 400:
                        content_status = "http_error"
                    elif hard_block_markers or (
                        len(body_text.strip()) < 1_500 and ambiguous_block_markers
                    ):
                        content_status = "blocked"
                    else:
                        content_status = "low_content" if len(body_text.strip()) < 120 else "ok"
                    mark_options = {
                        "terms": spec["highlight_terms"],
                        "badgeText": badge_text,
                    }
                    marks = page.evaluate(_MARK_JS, mark_options)
                    for _retry in range(7 if content_status == "ok" else 0):
                        if isinstance(marks, list) and any(
                            isinstance(row, dict)
                            and row.get("matched")
                            and row.get("usable") is not False
                            for row in marks
                        ):
                            break
                        # Some product capability cards are hydrated after DOM ready.
                        # Retry the exact same phrase rather than accepting a blank or
                        # unbounded element as customer evidence.
                        page.wait_for_timeout(1_000)
                        marks = page.evaluate(_MARK_JS, mark_options)
                    # Give lazy background images and fonts time to settle after the
                    # anchor scroll; otherwise a technically matched crop can contain
                    # only a floating word on an unpainted white canvas.
                    page.wait_for_timeout(1_000)
                    if content_status == "ok" and any(
                        isinstance(row, dict)
                        and row.get("matched")
                        and row.get("usable") is not False
                        for row in marks
                    ):
                        try:
                            payload, capture_scope = _capture_anchored_viewport(page)
                        except Exception:
                            payload = page.screenshot(type="png", full_page=False)
                            capture_scope = "viewport"
                    else:
                        payload = page.screenshot(type="png", full_page=False)
                        capture_scope = "viewport"
                    output[url] = {
                        "payload": payload,
                        "capture_status": "captured",
                        "content_status": content_status,
                        "http_status": http_status,
                        "final_url": page.url,
                        "transport_fallback": transport_fallback,
                        "title": page.title(),
                        "block_markers": [
                            *hard_block_markers,
                            *ambiguous_block_markers,
                        ],
                        "marks": marks if isinstance(marks, list) else [],
                        "matched_terms": [
                            str(row.get("term"))
                            for row in marks
                            if isinstance(row, dict)
                            and row.get("matched")
                            and row.get("usable") is not False
                        ]
                        if isinstance(marks, list)
                        else [],
                        "capture_scope": capture_scope,
                        "captured_at": captured_at,
                        "error": None,
                    }
                except Exception as exc:
                    failure_payload: bytes | None = None
                    try:
                        failure_payload = page.screenshot(type="png", full_page=False)
                    except Exception:
                        pass
                    output[url] = {
                        "payload": failure_payload,
                        "capture_status": "failed",
                        "content_status": "unknown",
                        "http_status": None,
                        "final_url": page.url,
                        "transport_fallback": None,
                        "title": "",
                        "marks": [],
                        "matched_terms": [],
                        "captured_at": captured_at,
                        "error": _safe_error(exc),
                    }
                finally:
                    page.close()
            context.close()
        finally:
            browser.close()
    return output


def source_capture_manifest(captures: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return JSON-safe capture metadata (screenshot bytes deliberately omitted)."""

    return {
        url: {key: value for key, value in row.items() if key != "payload"}
        for url, row in captures.items()
    }


def persist_source_capture_images(
    captures: dict[str, dict[str, Any]], output_dir: Path
) -> dict[str, str]:
    """Persist real screenshot payloads under deterministic, URL-derived names."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for url, row in captures.items():
        payload = row.get("payload")
        if not isinstance(payload, bytes) or not payload:
            continue
        filename = f"source-{sha256(url.encode()).hexdigest()[:20]}.png"
        path = output_dir / filename
        path.write_bytes(payload)
        paths[url] = str(path)
    return paths


__all__ = [
    "capture_service2_source_screenshots",
    "capture_source_page_specs",
    "persist_source_capture_images",
    "source_capture_manifest",
]
