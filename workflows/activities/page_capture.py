"""Browser-page capture helpers that always restore temporary layout changes.

Chat products commonly render the answer inside an overflow container.  A normal
``full_page=True`` screenshot only expands the document, not that inner scroller.
Flattening the scroller fixes completeness, but leaving the injected inline styles
behind corrupts the resident tab and every later task.  This module brackets the
temporary flattening with a complete inline-style snapshot and a ``finally`` restore.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

SNAPSHOT_PAGE_STYLES_JS = r"""() => {
  const token = `geo-capture-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const records = Array.from(document.querySelectorAll('*')).map((el) => [
    el,
    el.hasAttribute('style'),
    el.getAttribute('style') || ''
  ]);
  window.__geoCaptureStyleSnapshots = window.__geoCaptureStyleSnapshots || new Map();
  window.__geoCaptureStyleSnapshots.set(token, {
    records,
    scrollX: window.scrollX,
    scrollY: window.scrollY
  });
  return token;
}"""


RESTORE_PAGE_STYLES_JS = r"""(token) => {
  const store = window.__geoCaptureStyleSnapshots;
  const snapshot = store && store.get(token);
  if (!snapshot) return false;
  for (const [el, hadStyle, value] of snapshot.records) {
    if (!el || !el.isConnected) continue;
    if (hadStyle) el.setAttribute('style', value);
    else el.removeAttribute('style');
  }
  window.scrollTo(snapshot.scrollX, snapshot.scrollY);
  store.delete(token);
  return true;
}"""


# Generic chat-layout flattener.  Platform adapters may pass a more targeted script,
# but all scripts are protected by the snapshot/restore transaction above.
FLATTEN_CHAT_SCROLLER_JS = r"""() => {
  const body = document.body;
  const doc = document.documentElement;
  const beforeBodyClientH = body ? body.clientHeight : 0;
  const beforeBodyScrollH = body ? body.scrollHeight : 0;
  const cands = [];
  for (const el of document.querySelectorAll('div, main, section, article, aside, nav, form')) {
    const cs = getComputedStyle(el);
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 100) {
      cands.push(el);
    }
  }
  let main = null;
  let fullHeight = 0;
  if (cands.length) {
    cands.sort((a, b) => b.scrollHeight - a.scrollHeight);
    main = cands[0];
    fullHeight = main.scrollHeight;
    let cur = main;
    while (cur) {
      if (cur === main) cur.style.setProperty('height', fullHeight + 'px', 'important');
      else cur.style.setProperty('height', 'auto', 'important');
      cur.style.setProperty('max-height', 'none', 'important');
      cur.style.setProperty('min-height', '0', 'important');
      cur.style.setProperty('overflow', 'visible', 'important');
      cur.style.setProperty('flex', '0 0 auto', 'important');
      cur.style.setProperty('position', 'static', 'important');
      cur.style.setProperty('transform', 'none', 'important');
      cur.style.setProperty('contain', 'none', 'important');
      if (cur === doc) break;
      cur = cur.parentElement;
    }
  }
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.transform && cs.transform !== 'none') {
      el.style.setProperty('transform', 'none', 'important');
    }
    if (cs.position === 'fixed') {
      el.style.setProperty('position', 'absolute', 'important');
    }
  }
  const targetH = Math.max(fullHeight, beforeBodyScrollH, beforeBodyClientH);
  if (body) {
    body.style.setProperty('height', 'auto', 'important');
    body.style.setProperty('min-height', targetH + 'px', 'important');
    body.style.setProperty('overflow', 'visible', 'important');
    body.style.setProperty('transform', 'none', 'important');
  }
  if (doc) {
    doc.style.setProperty('height', 'auto', 'important');
    doc.style.setProperty('min-height', targetH + 'px', 'important');
    doc.style.setProperty('overflow', 'visible', 'important');
    doc.style.setProperty('transform', 'none', 'important');
  }
  if (body) void body.offsetHeight;
  return {
    ok: !!main,
    scroller_full_height: fullHeight,
    body_scroll_height_after: body ? body.scrollHeight : 0,
    doc_scroll_height_after: doc ? doc.scrollHeight : 0,
    viewport_height: window.innerHeight
  };
}"""


class PageStyleRestoreError(RuntimeError):
    """Temporary capture styles could not be proven restored in a resident tab."""


def capture_full_page_safely(
    page: Any,
    out_path: Path,
    *,
    flatten_script: str = FLATTEN_CHAT_SCROLLER_JS,
) -> dict[str, Any]:
    """Capture the complete chat page and restore every temporary inline style.

    CDP ``captureBeyondViewport`` is preferred after flattening the largest inner
    scroller.  Playwright's full-page screenshot remains the compatibility fallback.
    The restore runs even when flattening or screenshot capture raises.
    """

    out_path.parent.mkdir(parents=True, exist_ok=True)
    token: str | None = None
    metrics: dict[str, Any] = {}
    capture_method = "playwright_full_page"
    restore_error: BaseException | None = None
    capture_error: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        try:
            raw_token = page.evaluate(SNAPSHOT_PAGE_STYLES_JS)
            if isinstance(raw_token, str) and raw_token:
                token = raw_token
        except Exception:
            token = None

        # Fail safe: without a valid snapshot token there is no way to undo
        # flattening, so never run the mutating script. A normal full-page capture
        # is incomplete on some inner-scroller layouts but cannot poison the next
        # resident-browser task.
        if token is not None:
            try:
                raw_metrics = page.evaluate(flatten_script)
                page.wait_for_timeout(300)
                if isinstance(raw_metrics, dict):
                    metrics = raw_metrics
            except Exception:
                metrics = {}
        else:
            page.screenshot(path=str(out_path), full_page=True)
            result = {"method": "playwright_full_page_no_mutation", "metrics": {}}
            return result

        target_height = max(
            int(metrics.get("body_scroll_height_after") or 0),
            int(metrics.get("doc_scroll_height_after") or 0),
            int(metrics.get("scroller_full_height") or 0),
        )
        viewport_h = int(metrics.get("viewport_height") or 0)
        if target_height and target_height > viewport_h + 50:
            try:
                cdp = page.context.new_cdp_session(page)
                layout = cdp.send("Page.getLayoutMetrics")
                css_size = layout.get("cssContentSize") or layout.get("contentSize") or {}
                width = int(css_size.get("width") or 0) or 1280
                height = max(target_height, int(css_size.get("height") or 0))
                result = cdp.send(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "captureBeyondViewport": True,
                        "fromSurface": True,
                        "clip": {
                            "x": 0,
                            "y": 0,
                            "width": width,
                            "height": height,
                            "scale": 1,
                        },
                    },
                )
                png_b64 = result.get("data")
                if png_b64:
                    out_path.write_bytes(base64.b64decode(png_b64, validate=True))
                    capture_method = "cdp_capture_beyond_viewport"
                    result = {"method": capture_method, "metrics": metrics}
                    return result
            except Exception:
                pass

        page.screenshot(path=str(out_path), full_page=True)
        result = {"method": capture_method, "metrics": metrics}
        return result
    except BaseException as exc:
        capture_error = exc
        raise
    finally:
        if token is not None:
            try:
                restored = page.evaluate(RESTORE_PAGE_STYLES_JS, token)
                if restored is not True:
                    restore_error = PageStyleRestoreError(
                        "temporary screenshot styles were not restored"
                    )
            except BaseException as exc:
                restore_error = exc
            if restore_error is not None:
                # Best-effort recovery removes any mutated SPA state. The adapter
                # still fails this item because reload success cannot prove that the
                # screenshot transaction itself restored cleanly.
                try:
                    page.reload(wait_until="domcontentloaded", timeout=25_000)
                except Exception:
                    pass
                if capture_error is None:
                    raise PageStyleRestoreError(
                        f"temporary screenshot styles could not be restored: {restore_error}"
                    ) from restore_error
