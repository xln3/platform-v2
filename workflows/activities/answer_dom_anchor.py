"""Capture answer-scoped evidence with DOM anchors and a real OCR fallback.

The ordinary run screenshot is an audit asset and can contain navigation, question
history, and other unrelated UI.  Report evidence instead uses the last visible
assistant node as a clean, answer-scoped bitmap.  Text rectangles are tied to exact
intervals in the persisted answer text.

DOM text blocks are the primary path.  When the answer is canvas-backed (or DOM text
extraction otherwise produces no or materially incomplete verifiable coverage), the
same clean bitmap is sent to the local RapidOCR/ONNX Runtime engine.  OCR output is
admitted only when an entire recognized line can be matched exactly after whitespace
normalization.  If neither path succeeds, this module returns ``None`` and never
invents a rectangle.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from domain.evidence.diff import BoundingBox, OcrEngine, OcrSpan, validate_ocr_spans

_MAX_DOM_BLOCKS = 240
_MIN_OCR_CONFIDENCE = 0.60
_MIN_DOM_COVERAGE_FOR_PRIMARY = 0.80
_OCR_ANCHOR_METHOD = "ocr_rapidocr_ppocrv6_v1"
_LOGGER = logging.getLogger(__name__)
_OCR_ENGINE_INIT_LOCK = threading.Lock()
_OCR_ENGINE_INITIALIZED = False
_OCR_ENGINE: OcrEngine | None = None

_DOM_TEXT_BLOCKS_JS = r"""
(root, payload) => {
  const excluded = Array.isArray(payload.excludedSelectors)
    ? payload.excludedSelectors.filter((value) => typeof value === "string" && value)
    : [];
  const isExcluded = (node) => excluded.some((selector) => {
    try { return Boolean(node.closest(selector)); } catch (_) { return false; }
  });
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity || "1") > 0 && rect.width > 1 && rect.height > 1;
  };
  const blockSelector = [
    "p", "li", "blockquote", "pre", "code", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd",
    "tr", "th", "td", "article", "section"
  ].join(",");
  let nodes = Array.from(root.querySelectorAll(blockSelector));
  if (!nodes.length) nodes = [root];
  const rootRect = root.getBoundingClientRect();
  const output = [];
  for (const node of nodes) {
    if (output.length >= payload.maxBlocks) break;
    if (isExcluded(node) || !visible(node)) continue;
    const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    if (node.tagName === "TR" && node.querySelector("th,td")) continue;
    if (node.tagName === "PRE" && node.querySelector("code")) continue;
    // Generic containers are retained only when they do not duplicate semantic
    // descendants.  Table cells remain individual, report-addressable blocks.
    if ((node.tagName === "SECTION" || node.tagName === "ARTICLE") &&
        node.querySelector(blockSelector)) continue;
    const rect = node.getBoundingClientRect();
    output.push({
      text,
      x: rect.left - rootRect.left,
      y: rect.top - rootRect.top,
      width: rect.width,
      height: rect.height,
    });
  }
  return {
    rootWidth: rootRect.width,
    rootHeight: rootRect.height,
    blocks: output,
  };
}
"""


@dataclass(frozen=True, slots=True)
class AnswerEvidenceCapture:
    path: Path
    anchors: list[dict[str, Any]]


# Kept as a compatibility name for code/tests created while this module was DOM-only.
AnswerDomCapture = AnswerEvidenceCapture


class RapidOcrEngine:
    """Offline Chinese/English OCR implementation of the existing evidence protocol."""

    def __init__(
        self,
        backend: Callable[[bytes], Any] | None = None,
        *,
        engine_version: str | None = None,
    ) -> None:
        if backend is None:
            # Lazy import keeps the normal DOM path free of OCR model startup cost.
            from rapidocr import RapidOCR

            backend = RapidOCR()
        self._backend = backend
        self._lock = threading.Lock()
        self._version = engine_version or _installed_ocr_version()

    @property
    def version(self) -> str:
        return self._version

    def recognize(self, image: bytes) -> Sequence[OcrSpan]:
        # ONNX Runtime sessions are reused because model initialization is relatively
        # expensive.  A lock avoids relying on undocumented concurrent-call behavior.
        with self._lock:
            result = self._backend(image)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        boxes = getattr(result, "boxes", None)
        if texts is None or scores is None or boxes is None:
            return ()

        spans: list[OcrSpan] = []
        cursor = 0
        for raw_text, raw_score, raw_box in zip(texts, scores, boxes, strict=False):
            text = str(raw_text or "").strip()
            try:
                confidence = float(raw_score)
                points = [(float(point[0]), float(point[1])) for point in raw_box]
            except (IndexError, TypeError, ValueError):
                continue
            if (
                not text
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
                or len(points) < 3
                or not all(math.isfinite(value) for point in points for value in point)
            ):
                continue
            x0 = min(point[0] for point in points)
            y0 = min(point[1] for point in points)
            x1 = max(point[0] for point in points)
            y1 = max(point[1] for point in points)
            if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
                continue
            end = cursor + len(text)
            spans.append(
                OcrSpan(
                    text=text,
                    start=cursor,
                    end=end,
                    bbox=BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
                    confidence=confidence,
                )
            )
            cursor = end
        return tuple(spans)


def _installed_ocr_version() -> str:
    versions: list[str] = []
    for distribution in ("rapidocr", "onnxruntime"):
        try:
            versions.append(f"{distribution}-{package_version(distribution)}")
        except PackageNotFoundError:
            versions.append(f"{distribution}-unknown")
    return "+".join(versions)


def _default_ocr_engine() -> OcrEngine | None:
    global _OCR_ENGINE, _OCR_ENGINE_INITIALIZED

    if _OCR_ENGINE_INITIALIZED:
        return _OCR_ENGINE
    with _OCR_ENGINE_INIT_LOCK:
        if not _OCR_ENGINE_INITIALIZED:
            try:
                _OCR_ENGINE = RapidOcrEngine()
            except Exception:
                # Missing/broken models are an evidence gap, never permission to guess pixels.
                _LOGGER.warning("answer evidence OCR engine is unavailable", exc_info=True)
                _OCR_ENGINE = None
            _OCR_ENGINE_INITIALIZED = True
    return _OCR_ENGINE


def _controlled_ocr_preflight_png() -> bytes:
    """Build a dependency-local probe image without filesystem or browser input."""

    image = Image.new("RGB", (480, 150), "white")
    font = ImageFont.load_default(size=72)
    ImageDraw.Draw(image).text((20, 25), "GEO2026", fill="black", font=font)
    payload = BytesIO()
    image.save(payload, format="PNG")
    return payload.getvalue()


def preflight_answer_evidence_ocr(ocr_engine: OcrEngine | None = None) -> str:
    """Initialize OCR and execute inference before a collection worker registers.

    A successful import or model construction is insufficient: this probe sends a
    controlled in-memory PNG through the exact production ``recognize`` protocol and
    requires the known text plus valid geometry. Any failure is raised so the caller
    cannot proceed to Temporal task polling with a broken OCR fallback.
    """

    engine = ocr_engine if ocr_engine is not None else _default_ocr_engine()
    if engine is None:
        raise RuntimeError("answer evidence OCR engine is unavailable")
    try:
        engine_version = str(engine.version or "").strip()
        spans = tuple(engine.recognize(_controlled_ocr_preflight_png()))
    except Exception as exc:
        raise RuntimeError("answer evidence OCR inference preflight failed") from exc
    if not engine_version or not _validate_protocol_spans(spans):
        raise RuntimeError("answer evidence OCR protocol preflight failed")
    recognized, _ = _compact_with_offsets("".join(span.text for span in spans))
    if "geo2026" not in recognized:
        raise RuntimeError("answer evidence OCR controlled text was not recognized")
    if not any(span.confidence >= _MIN_OCR_CONFIDENCE for span in spans):
        raise RuntimeError("answer evidence OCR confidence preflight failed")
    if any(
        span.bbox.x < 0
        or span.bbox.y < 0
        or span.bbox.width <= 0
        or span.bbox.height <= 0
        or span.bbox.x + span.bbox.width > 480
        or span.bbox.y + span.bbox.height > 150
        for span in spans
    ):
        raise RuntimeError("answer evidence OCR geometry preflight failed")
    return engine_version


def _compact_with_offsets(text: str) -> tuple[str, list[int]]:
    compact: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace() or character in {"\u200b", "\ufeff"}:
            continue
        # Some case-folds expand to more than one code point (for example, ss from
        # sharp-s).  Repeating the source offset keeps compact indexes reversible.
        for folded in character.casefold():
            compact.append(folded)
            offsets.append(index)
    return "".join(compact), offsets


def _match_text_span(
    answer_text: str,
    block_text: str,
    *,
    compact_answer: str,
    answer_offsets: list[int],
    cursor: int,
) -> tuple[int, int, int] | None:
    block = " ".join(block_text.split()).strip()
    if not block:
        return None
    compact_block, _ = _compact_with_offsets(block)
    if len(compact_block) < 2:
        return None
    compact_start = compact_answer.find(compact_block, cursor)
    if compact_start < 0:
        compact_start = compact_answer.find(compact_block)
    if compact_start < 0:
        return None
    compact_end = compact_start + len(compact_block)
    if compact_end > len(answer_offsets):
        return None
    start = answer_offsets[compact_start]
    end = answer_offsets[compact_end - 1] + 1
    if not answer_text[start:end].strip():
        return None
    return start, end, compact_end


def _last_visible_answer(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        try:
            elements = page.locator(selector).all()
        except Exception:
            continue
        for element in reversed(elements):
            try:
                if element.is_visible(timeout=300):
                    return element
            except Exception:
                continue
    return None


def _clean_screenshot_style(excluded_selectors: tuple[str, ...]) -> str | None:
    selectors = [selector.strip() for selector in excluded_selectors if selector.strip()]
    if not selectors:
        return None
    # visibility preserves answer layout while removing thinking/tool chrome pixels.
    return ",".join(selectors) + "{visibility:hidden!important;}"


def _screenshot_answer(
    root: Any,
    output_path: Path,
    *,
    excluded_selectors: tuple[str, ...],
) -> tuple[int, int] | None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        kwargs: dict[str, Any] = {
            "path": str(output_path),
            "animations": "disabled",
            "caret": "hide",
            "timeout": 20_000,
        }
        style = _clean_screenshot_style(excluded_selectors)
        if style is not None:
            kwargs["style"] = style
        root.screenshot(**kwargs)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            return None
        with Image.open(output_path) as image:
            image.verify()
        with Image.open(output_path) as image:
            image_width, image_height = image.size
        if (
            image_width <= 0
            or image_height <= 0
            or image_width > 100_000
            or image_height > 100_000
            or image_width * image_height > 100_000_000
        ):
            return None
        return image_width, image_height
    except Exception:
        _LOGGER.warning("answer evidence clean screenshot failed", exc_info=True)
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _clipped_bbox(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0 or x >= image_width or y >= image_height:
        return None
    if x + width <= 0 or y + height <= 0:
        return None
    left = max(0, min(image_width - 1, round(x)))
    top = max(0, min(image_height - 1, round(y)))
    right = max(left + 1, min(image_width, round(x + width)))
    bottom = max(top + 1, min(image_height, round(y + height)))
    return left, top, right - left, bottom - top


def _dom_anchors(
    root: Any,
    *,
    answer_text: str,
    image_width: int,
    image_height: int,
    excluded_selectors: tuple[str, ...],
) -> list[dict[str, Any]]:
    try:
        raw = root.evaluate(
            _DOM_TEXT_BLOCKS_JS,
            {
                "excludedSelectors": list(excluded_selectors),
                "maxBlocks": _MAX_DOM_BLOCKS,
            },
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
            return []
        root_width = float(raw.get("rootWidth") or 0)
        root_height = float(raw.get("rootHeight") or 0)
        if (
            not math.isfinite(root_width)
            or not math.isfinite(root_height)
            or root_width <= 0
            or root_height <= 0
        ):
            return []
        scale_x = image_width / root_width
        scale_y = image_height / root_height
        compact_answer, answer_offsets = _compact_with_offsets(answer_text)
        if not compact_answer or not answer_offsets:
            return []

        cursor = 0
        previous_end = 0
        anchors: list[dict[str, Any]] = []
        for block in raw["blocks"][:_MAX_DOM_BLOCKS]:
            if not isinstance(block, dict):
                continue
            match = _match_text_span(
                answer_text,
                str(block.get("text") or ""),
                compact_answer=compact_answer,
                answer_offsets=answer_offsets,
                cursor=cursor,
            )
            if match is None:
                continue
            start, end, compact_end = match
            if start < previous_end:
                continue
            try:
                clipped = _clipped_bbox(
                    x=float(block.get("x") or 0) * scale_x,
                    y=float(block.get("y") or 0) * scale_y,
                    width=float(block.get("width") or 0) * scale_x,
                    height=float(block.get("height") or 0) * scale_y,
                    image_width=image_width,
                    image_height=image_height,
                )
            except (TypeError, ValueError):
                continue
            if clipped is None:
                continue
            x, y, width, height = clipped
            cursor = max(cursor, compact_end)
            previous_end = end
            anchors.append(
                {
                    "text_start": start,
                    "text_end": end,
                    "text": answer_text[start:end],
                    "bbox": {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "image_width": image_width,
                        "image_height": image_height,
                        "confidence": 1.0,
                        "anchor_method": "dom_text_block_v1",
                    },
                }
            )
        return anchors
    except Exception:
        # DOM extraction failure intentionally falls through to OCR on the bitmap.
        _LOGGER.info("answer evidence DOM localization failed; trying OCR", exc_info=True)
        return []


def _validate_protocol_spans(spans: Sequence[OcrSpan]) -> bool:
    if not spans:
        return False
    try:
        cursor = 0
        pieces: list[str] = []
        for span in spans:
            if span.start < cursor:
                return False
            pieces.append(" " * (span.start - cursor))
            pieces.append(span.text)
            cursor = span.end
        validate_ocr_spans("".join(pieces), spans)
        return True
    except Exception:
        return False


def _ocr_anchors(
    image_path: Path,
    *,
    answer_text: str,
    image_width: int,
    image_height: int,
    ocr_engine: OcrEngine,
) -> list[dict[str, Any]]:
    try:
        spans = tuple(ocr_engine.recognize(image_path.read_bytes()))
    except Exception:
        _LOGGER.warning("answer evidence OCR inference failed", exc_info=True)
        return []
    if not _validate_protocol_spans(spans):
        return []

    compact_answer, answer_offsets = _compact_with_offsets(answer_text)
    if not compact_answer or not answer_offsets:
        return []
    try:
        engine_version = str(ocr_engine.version or "").strip()[:160]
    except Exception:
        _LOGGER.warning("answer evidence OCR version lookup failed", exc_info=True)
        return []
    if not engine_version:
        return []
    cursor = 0
    previous_end = 0
    anchors: list[dict[str, Any]] = []
    for span in spans:
        if span.confidence < _MIN_OCR_CONFIDENCE:
            continue
        match = _match_text_span(
            answer_text,
            span.text,
            compact_answer=compact_answer,
            answer_offsets=answer_offsets,
            cursor=cursor,
        )
        if match is None:
            continue
        start, end, compact_end = match
        if start < previous_end:
            continue
        clipped = _clipped_bbox(
            x=span.bbox.x,
            y=span.bbox.y,
            width=span.bbox.width,
            height=span.bbox.height,
            image_width=image_width,
            image_height=image_height,
        )
        if clipped is None:
            continue
        x, y, width, height = clipped
        cursor = max(cursor, compact_end)
        previous_end = end
        anchors.append(
            {
                "text_start": start,
                "text_end": end,
                "text": answer_text[start:end],
                "bbox": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "image_width": image_width,
                    "image_height": image_height,
                    "confidence": float(span.confidence),
                    "anchor_method": _OCR_ANCHOR_METHOD,
                    "ocr_version": engine_version,
                },
            }
        )
    return anchors


def _anchor_text_coverage(answer_text: str, anchors: list[dict[str, Any]]) -> float:
    compact_answer, _ = _compact_with_offsets(answer_text)
    if not compact_answer:
        return 0.0
    covered = 0
    for anchor in anchors:
        text = anchor.get("text")
        if isinstance(text, str):
            compact_text, _ = _compact_with_offsets(text)
            covered += len(compact_text)
    return min(1.0, covered / len(compact_answer))


def capture_answer_evidence(
    page: Any,
    *,
    assistant_selectors: tuple[str, ...],
    answer_text: str,
    output_path: Path,
    excluded_selectors: tuple[str, ...] = (),
    ocr_engine: OcrEngine | None = None,
) -> AnswerEvidenceCapture | None:
    """Capture a clean answer bitmap and exact DOM/OCR text rectangles.

    ``ocr_engine`` is injectable for deterministic tests.  Production callers omit
    it and lazily use the bundled local RapidOCR engine only when DOM anchoring yields
    no verified interval.
    """

    if not answer_text.strip():
        return None
    root = _last_visible_answer(page, assistant_selectors)
    if root is None:
        return None
    dimensions = _screenshot_answer(
        root,
        output_path,
        excluded_selectors=excluded_selectors,
    )
    if dimensions is None:
        return None
    image_width, image_height = dimensions
    anchors = _dom_anchors(
        root,
        answer_text=answer_text,
        image_width=image_width,
        image_height=image_height,
        excluded_selectors=excluded_selectors,
    )
    dom_coverage = _anchor_text_coverage(answer_text, anchors)
    if anchors and dom_coverage >= _MIN_DOM_COVERAGE_FOR_PRIMARY:
        return AnswerEvidenceCapture(path=output_path, anchors=anchors)

    engine = ocr_engine if ocr_engine is not None else _default_ocr_engine()
    ocr_anchors: list[dict[str, Any]] = []
    if engine is not None:
        ocr_anchors = _ocr_anchors(
            output_path,
            answer_text=answer_text,
            image_width=image_width,
            image_height=image_height,
            ocr_engine=engine,
        )
    if _anchor_text_coverage(answer_text, ocr_anchors) > dom_coverage:
        anchors = ocr_anchors
    if anchors:
        return AnswerEvidenceCapture(path=output_path, anchors=anchors)

    # An unanchored image must never enter the report evidence relation.  Remove the
    # local candidate as well so operators cannot mistake it for an admitted asset.
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        _LOGGER.warning("failed to remove unanchored answer evidence candidate", exc_info=True)
    return None


def capture_answer_dom_evidence(
    page: Any,
    *,
    assistant_selectors: tuple[str, ...],
    answer_text: str,
    output_path: Path,
    excluded_selectors: tuple[str, ...] = (),
    ocr_engine: OcrEngine | None = None,
) -> AnswerEvidenceCapture | None:
    """Backward-compatible name; now includes the production OCR fallback."""

    return capture_answer_evidence(
        page,
        assistant_selectors=assistant_selectors,
        answer_text=answer_text,
        output_path=output_path,
        excluded_selectors=excluded_selectors,
        ocr_engine=ocr_engine,
    )


__all__ = [
    "AnswerDomCapture",
    "AnswerEvidenceCapture",
    "RapidOcrEngine",
    "capture_answer_dom_evidence",
    "capture_answer_evidence",
    "preflight_answer_evidence_ocr",
]
