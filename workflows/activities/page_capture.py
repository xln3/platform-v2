"""Browser-page capture helpers that always restore temporary layout changes.

Chat products commonly render the answer inside an overflow container.  A normal
``full_page=True`` screenshot only expands the document, not that inner scroller.
Flattening the scroller fixes completeness, but leaving the injected inline styles
behind corrupts the resident tab and every later task.  This module brackets the
temporary flattening with a complete inline-style snapshot and a ``finally`` restore.
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

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


class ScopedChatCaptureError(RuntimeError):
    """A semantic chat-only screenshot could not be proven complete and stable."""


def _finite_capture_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ScopedChatCaptureError(f"capture metric {name} was not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ScopedChatCaptureError(f"capture metric {name} was not finite")
    return number


def _read_scoped_capture_state(
    page: Any,
    *,
    probe_script: str,
    expected_question: str,
    expected_roles: tuple[str, ...],
    scroll_top: float | None = None,
) -> dict[str, Any]:
    raw = page.evaluate(
        probe_script,
        {"expectedQuestion": expected_question, "scrollTop": scroll_top},
    )
    if not isinstance(raw, dict):
        raise ScopedChatCaptureError("scoped capture probe returned no state")
    if raw.get("ok") is not True:
        raise ScopedChatCaptureError(
            f"scoped capture unavailable: {str(raw.get('error') or 'unknown DOM shape')}"
        )
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != len(expected_roles):
        raise ScopedChatCaptureError(
            f"scoped capture requires {len(expected_roles)} semantic message blocks"
        )
    blocks: list[dict[str, Any]] = []
    for index, (raw_block, expected_role) in enumerate(
        zip(raw_blocks, expected_roles, strict=True)
    ):
        if not isinstance(raw_block, dict) or raw_block.get("role") != expected_role:
            raise ScopedChatCaptureError(
                f"scoped capture message order changed at index {index} ({expected_role})"
            )
        fingerprint = raw_block.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ScopedChatCaptureError(f"scoped capture {expected_role} fingerprint was missing")
        block: dict[str, Any] = {
            "role": expected_role,
            "top": _finite_capture_number(raw_block.get("top"), f"{expected_role}.top"),
            "bottom": _finite_capture_number(raw_block.get("bottom"), f"{expected_role}.bottom"),
            "left": _finite_capture_number(raw_block.get("left"), f"{expected_role}.left"),
            "right": _finite_capture_number(raw_block.get("right"), f"{expected_role}.right"),
            "fingerprint": fingerprint,
        }
        if block["bottom"] <= block["top"]:
            raise ScopedChatCaptureError(f"scoped capture {expected_role} height was not positive")
        blocks.append(block)
    state: dict[str, Any] = {
        "scroll_top": _finite_capture_number(raw.get("scroll_top"), "scroll_top"),
        "scroll_height": _finite_capture_number(raw.get("scroll_height"), "scroll_height"),
        "max_scroll": _finite_capture_number(raw.get("max_scroll"), "max_scroll"),
        "capture_x": _finite_capture_number(raw.get("capture_x"), "capture_x"),
        "capture_y": _finite_capture_number(raw.get("capture_y"), "capture_y"),
        "capture_width": _finite_capture_number(raw.get("capture_width"), "capture_width"),
        "capture_top_inset": _finite_capture_number(
            raw.get("capture_top_inset", 0), "capture_top_inset"
        ),
        "capture_height": _finite_capture_number(raw.get("capture_height"), "capture_height"),
        "terminal_capture_height": _finite_capture_number(
            raw.get("terminal_capture_height", raw.get("capture_height")),
            "terminal_capture_height",
        ),
        "blocks": blocks,
    }
    if state["capture_x"] < 0 or state["capture_y"] < 0:
        raise ScopedChatCaptureError("scoped capture clip started outside the viewport")
    if state["capture_width"] <= 0 or state["capture_width"] > 5_000:
        raise ScopedChatCaptureError("scoped capture width was unsafe")
    if state["capture_height"] <= 0 or state["capture_height"] > 5_000:
        raise ScopedChatCaptureError("scoped capture safe band was unsafe")
    if (
        state["terminal_capture_height"] < state["capture_height"]
        or state["terminal_capture_height"] > 5_000
    ):
        raise ScopedChatCaptureError("scoped capture terminal band was unsafe")
    if state["capture_top_inset"] < 0 or state["capture_top_inset"] >= state["capture_height"] - 1:
        raise ScopedChatCaptureError("scoped capture top inset was unsafe")
    if state["max_scroll"] < 0 or state["scroll_height"] < state["capture_height"]:
        raise ScopedChatCaptureError("scoped capture scroll extent was invalid")
    output_height = sum(block["bottom"] - block["top"] for block in blocks)
    if output_height <= 0 or output_height > 50_000:
        raise ScopedChatCaptureError("scoped capture output height was unsafe")
    return state


def _assert_scoped_capture_stable(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    requested_scroll_top: float,
) -> None:
    if abs(actual["scroll_top"] - requested_scroll_top) > 1:
        raise ScopedChatCaptureError("chat scroller did not settle at the requested tile")
    for key in (
        "scroll_height",
        "max_scroll",
        "capture_x",
        "capture_y",
        "capture_width",
        "capture_top_inset",
        "capture_height",
        "terminal_capture_height",
    ):
        if abs(actual[key] - expected[key]) > 1:
            raise ScopedChatCaptureError(f"chat layout changed during capture ({key})")
    for expected_block, actual_block in zip(expected["blocks"], actual["blocks"], strict=True):
        role = expected_block["role"]
        if actual_block["fingerprint"] != expected_block["fingerprint"]:
            raise ScopedChatCaptureError(f"{role} text changed during screenshot capture")
        for key in ("top", "bottom", "left", "right"):
            if abs(actual_block[key] - expected_block[key]) > 1:
                raise ScopedChatCaptureError(f"{role} bounds changed during screenshot capture")


def _decode_scoped_tile(raw_png: Any) -> Image.Image:
    if not isinstance(raw_png, bytes | bytearray):
        raise ScopedChatCaptureError("screenshot API returned no PNG bytes")
    try:
        with Image.open(io.BytesIO(bytes(raw_png))) as opened:
            opened.load()
            return opened.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ScopedChatCaptureError("screenshot tile was not a valid image") from exc


def _capture_scoped_block_tiles(
    page: Any,
    *,
    probe_script: str,
    expected_question: str,
    expected_roles: tuple[str, ...],
    expected: dict[str, Any],
    block: dict[str, Any],
    repeat_top_inset_css_px: float,
    overlap_css_px: float,
    settle_ms: int,
    max_tiles: int,
) -> tuple[Image.Image, int]:
    capture_height = expected["capture_height"]
    base_top_inset = expected["capture_top_inset"]
    if repeat_top_inset_css_px < 0:
        raise ScopedChatCaptureError("repeat-tile top inset was unsafe")
    effective_repeat_height = capture_height - base_top_inset - repeat_top_inset_css_px
    if effective_repeat_height <= 1:
        raise ScopedChatCaptureError("repeat-tile top inset was unsafe")
    if overlap_css_px < 0 or overlap_css_px >= effective_repeat_height - 1:
        raise ScopedChatCaptureError("tile overlap was unsafe")

    position = min(max(block["top"] - base_top_inset, 0.0), expected["max_scroll"])
    painted_until = block["top"]
    canvas: Image.Image | None = None
    scale_x: float | None = None
    scale_y: float | None = None
    tile_count = 0
    first_tile = True
    try:
        while painted_until < block["bottom"] - 0.5:
            if tile_count >= max_tiles:
                raise ScopedChatCaptureError("answer required too many screenshot tiles")
            state = _read_scoped_capture_state(
                page,
                probe_script=probe_script,
                expected_question=expected_question,
                expected_roles=expected_roles,
                scroll_top=position,
            )
            _assert_scoped_capture_stable(expected, state, requested_scroll_top=position)
            page.wait_for_timeout(settle_ms)
            stable_state = _read_scoped_capture_state(
                page,
                probe_script=probe_script,
                expected_question=expected_question,
                expected_roles=expected_roles,
            )
            _assert_scoped_capture_stable(
                expected,
                stable_state,
                requested_scroll_top=state["scroll_top"],
            )

            active_capture_height = (
                expected["terminal_capture_height"]
                if state["scroll_top"] >= expected["max_scroll"] - 1
                else capture_height
            )
            inset = base_top_inset + (0.0 if first_tile else repeat_top_inset_css_px)
            tile_css_height = active_capture_height - inset
            raw_png = page.screenshot(
                clip={
                    "x": expected["capture_x"],
                    "y": expected["capture_y"] + inset,
                    "width": expected["capture_width"],
                    "height": tile_css_height,
                },
                timeout=15_000,
            )
            tile = _decode_scoped_tile(raw_png)
            try:
                current_scale_x = tile.width / expected["capture_width"]
                current_scale_y = tile.height / tile_css_height
                if current_scale_x <= 0 or current_scale_y <= 0:
                    raise ScopedChatCaptureError("screenshot tile scale was invalid")
                if scale_x is None or scale_y is None:
                    scale_x = current_scale_x
                    scale_y = current_scale_y
                    if abs(scale_x - scale_y) > max(scale_x, scale_y) * 0.02:
                        raise ScopedChatCaptureError(
                            "screenshot tile used inconsistent pixel scaling"
                        )
                    canvas = Image.new(
                        "RGB",
                        (
                            tile.width,
                            max(1, round((block["bottom"] - block["top"]) * scale_y)),
                        ),
                        "white",
                    )
                elif (
                    abs(current_scale_x - scale_x) > 0.01
                    or abs(current_scale_y - scale_y) > 0.01
                    or canvas is None
                    or tile.width != canvas.width
                ):
                    raise ScopedChatCaptureError(
                        "screenshot tile dimensions changed during capture"
                    )

                visible_start = max(state["scroll_top"] + inset, block["top"])
                visible_end = min(
                    state["scroll_top"] + active_capture_height,
                    block["bottom"],
                )
                segment_start = max(visible_start, painted_until)
                if segment_start > painted_until + 1:
                    raise ScopedChatCaptureError("screenshot tiles contained a gap")
                if visible_end <= segment_start + 0.01:
                    raise ScopedChatCaptureError("screenshot tile made no forward progress")
                assert scale_y is not None and canvas is not None
                source_top = round((segment_start - state["scroll_top"] - inset) * scale_y)
                source_bottom = round((visible_end - state["scroll_top"] - inset) * scale_y)
                destination_top = round((segment_start - block["top"]) * scale_y)
                source_top = min(max(source_top, 0), tile.height)
                source_bottom = min(max(source_bottom, source_top), tile.height)
                segment = tile.crop((0, source_top, tile.width, source_bottom))
                try:
                    remaining = canvas.height - destination_top
                    if segment.height > remaining:
                        clipped = segment.crop((0, 0, segment.width, max(0, remaining)))
                        segment.close()
                        segment = clipped
                    if segment.height > 0:
                        canvas.paste(segment, (0, destination_top))
                finally:
                    segment.close()
                painted_until = visible_end
            finally:
                tile.close()
            tile_count += 1
            first_tile = False
            if painted_until >= block["bottom"] - 0.5:
                break
            next_position = min(
                max(
                    painted_until - base_top_inset - repeat_top_inset_css_px - overlap_css_px,
                    0.0,
                ),
                expected["max_scroll"],
            )
            if next_position <= position + 0.5:
                raise ScopedChatCaptureError(
                    "screenshot tiles could not reach the end of the message"
                )
            position = next_position
        if canvas is None or painted_until < block["bottom"] - 1:
            raise ScopedChatCaptureError(f"{block['role']} screenshot tiles were incomplete")
        return canvas, tile_count
    except BaseException:
        if canvas is not None:
            canvas.close()
        raise


def capture_scoped_chat_tiles(
    page: Any,
    out_path: Path,
    *,
    probe_script: str,
    restore_script: str,
    expected_question: str,
    expected_roles: tuple[str, ...] = ("question", "answer"),
    method: str = "scoped_chat_tiles",
    repeat_top_inset_css_px: float = 0.0,
    overlap_css_px: float = 32.0,
    settle_ms: int = 75,
    max_tiles: int = 200,
) -> dict[str, Any]:
    """Capture semantic question/answer blocks by scrolling only their chat pane.

    The platform probe must prove the current question, exact content blocks,
    scroll extent, and an overlay-free screenshot band.  The helper never changes
    element styles or the document scroll position.  Repeated tiles may reserve a
    top inset for sticky in-message controls; coordinate-based cropping removes the
    overlap rather than repeating pixels.  The original inner ``scrollTop`` is
    restored on every exit path, and an unproven restore fails the item.
    """

    if not expected_question.strip():
        raise ScopedChatCaptureError("expected question was empty")
    initial = _read_scoped_capture_state(
        page,
        probe_script=probe_script,
        expected_question=expected_question,
        expected_roles=expected_roles,
    )
    original_scroll_top = initial["scroll_top"]
    block_images: list[Image.Image] = []
    final_image: Image.Image | None = None
    capture_error: BaseException | None = None
    restore_error: BaseException | None = None
    tile_count = 0
    try:
        for block in initial["blocks"]:
            block_image, count = _capture_scoped_block_tiles(
                page,
                probe_script=probe_script,
                expected_question=expected_question,
                expected_roles=expected_roles,
                expected=initial,
                block=block,
                repeat_top_inset_css_px=repeat_top_inset_css_px,
                overlap_css_px=overlap_css_px,
                settle_ms=settle_ms,
                max_tiles=max_tiles,
            )
            block_images.append(block_image)
            tile_count += count
        widths = {image.width for image in block_images}
        if len(widths) != 1:
            raise ScopedChatCaptureError("question and answer screenshot widths differed")
        final_image = Image.new(
            "RGB",
            (block_images[0].width, sum(image.height for image in block_images)),
            "white",
        )
        paste_y = 0
        for image in block_images:
            final_image.paste(image, (0, paste_y))
            paste_y += image.height
    except BaseException as exc:
        capture_error = exc
    finally:
        try:
            restored = page.evaluate(restore_script, original_scroll_top)
            if not isinstance(restored, dict) or restored.get("ok") is not True:
                reason = (
                    str(restored.get("error") or restored.get("actual_scroll_top"))
                    if isinstance(restored, dict)
                    else "no restore result"
                )
                raise ScopedChatCaptureError(
                    f"chat scroll position could not be restored: {reason}"
                )
        except BaseException as exc:
            restore_error = exc

    for image in block_images:
        image.close()
    if capture_error is not None:
        if final_image is not None:
            final_image.close()
        if restore_error is not None:
            raise ScopedChatCaptureError(
                f"scoped screenshot failed and scroll restore also failed: {restore_error}"
            ) from capture_error
        raise capture_error
    if restore_error is not None:
        if final_image is not None:
            final_image.close()
        raise restore_error
    if final_image is None:
        raise ScopedChatCaptureError("scoped screenshot produced no image")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        final_image.save(out_path, format="PNG")
    finally:
        final_image.close()
    return {
        "method": method,
        "tile_count": tile_count,
        "block_count": len(initial["blocks"]),
        "restored_scroll_top": original_scroll_top,
    }


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
