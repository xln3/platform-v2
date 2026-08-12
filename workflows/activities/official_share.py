"""Official answer-sharing exports for browser-backed platform adapters."""

from __future__ import annotations

import base64
import io
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_SHARE_IMAGE_BYTES = 30 * 1024 * 1024
_HTTP_URL_RE = re.compile(r"https://[^\s<>\]\[\"'，。；]+")


class OfficialShareExportError(RuntimeError):
    """The platform did not yield both an official share link and share image."""


@dataclass(frozen=True)
class OfficialShareArtifacts:
    image_path: Path
    share_url: str
    audit: dict[str, Any] = field(default_factory=dict)


def valid_png(path: Path) -> bool:
    """Require a non-empty PNG with a real IHDR and positive dimensions."""

    try:
        with path.open("rb") as stream:
            head = stream.read(24)
        size = path.stat().st_size
    except OSError:
        return False
    if size < 24 or size > _MAX_SHARE_IMAGE_BYTES:
        return False
    if head[:8] != _PNG_SIGNATURE or head[12:16] != b"IHDR":
        return False
    return int.from_bytes(head[16:20], "big") > 0 and int.from_bytes(head[20:24], "big") > 0


def recover_png_from_export_audit(path: Path, audit: dict[str, Any]) -> bool:
    """Repair the CDP-download edge case where ``save_as`` writes a zero-byte file.

    Chromium still exposes the official rendered image as ``download.url`` (a data
    URL).  The legacy Doubao exporter returns that URL in its audit record, so it can
    be decoded without synthesising or re-rendering any platform content.
    """

    if valid_png(path):
        return True
    value = audit.get("url")
    if not isinstance(value, str) or not value.startswith("data:image/"):
        return False
    try:
        header, payload = value.split(",", 1)
        if ";base64" not in header.lower():
            return False
        data = base64.b64decode(payload, validate=True)
        if len(data) > _MAX_SHARE_IMAGE_BYTES:
            return False
        path.write_bytes(data)
    except (OSError, ValueError):
        return False
    return valid_png(path)


def write_share_link_manifest(
    path: Path,
    *,
    share_url: str,
    platform: str,
    channel: str | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {"channel": channel, "platform": platform, "url": share_url},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def validated_yiyan_share_url(value: object) -> str | None:
    return _validated_share_url(value, hosts={"mr.baidu.com", "wenxin.baidu.com"})


def validated_deepseek_share_url(value: object) -> str | None:
    url = _validated_share_url(value, hosts={"chat.deepseek.com"})
    if url is None or not urlsplit(url).path.startswith("/share/"):
        return None
    return url


def _validated_share_url(value: object, *, hosts: set[str]) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    candidate = value.strip().rstrip(".,;，。；")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in hosts:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _clipboard_url(page: Any, validator: Callable[[object], str | None]) -> str | None:
    try:
        raw = page.evaluate(
            """async () => await Promise.race([
              navigator.clipboard.readText(),
              new Promise((resolve) => setTimeout(() => resolve(''), 2000))
            ])"""
        )
    except Exception:
        raw = ""
    for candidate in _HTTP_URL_RE.findall(str(raw or "")):
        if url := validator(candidate):
            return url
    return None


def _wait_clipboard_url(
    page: Any,
    validator: Callable[[object], str | None],
    *,
    timeout_ms: int = 10_000,
) -> str | None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        if url := _clipboard_url(page, validator):
            return url
        page.wait_for_timeout(250)
    return None


def _grant_clipboard(page: Any) -> None:
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        origin = f"{urlsplit(str(page.url)).scheme}://{urlsplit(str(page.url)).netloc}"
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=origin
        )
    except Exception:
        pass
    try:
        page.evaluate("async () => await navigator.clipboard.writeText('')")
    except Exception:
        pass


def _first_visible(page: Any, selectors: tuple[str, ...], *, timeout_ms: int = 8_000) -> Any:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                locators = page.locator(selector)
                # React often retains a hidden prior copy after transitions.  A
                # blind ``.last`` can therefore wait forever while an earlier
                # matching control is already visible.
                for index in range(locators.count() - 1, -1, -1):
                    locator = locators.nth(index)
                    if locator.is_visible(timeout=200):
                        return locator
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise OfficialShareExportError(f"official share control not found: {selectors[0]}")


def _first_enabled(page: Any, selectors: tuple[str, ...], *, timeout_ms: int = 30_000) -> Any:
    """Wait for a visible control whose platform loading/disabled state has cleared."""

    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                locators = page.locator(selector)
                for index in range(locators.count() - 1, -1, -1):
                    locator = locators.nth(index)
                    classes = locator.get_attribute("class") or ""
                    if (
                        locator.is_visible(timeout=200)
                        and "cos-disabled" not in classes.split()
                        and locator.get_attribute("disabled") is None
                        and locator.locator(".cos-loading").count() == 0
                    ):
                        return locator
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise OfficialShareExportError(f"official share control stayed disabled: {selectors[0]}")


_PREPARE_YIYAN_SHARE_JS = r"""() => {
  const scroller = document.querySelector('#conversation-flow-container');
  if (scroller) scroller.scrollTop = scroller.scrollHeight;
  return {found: !!document.querySelector('[data-testid="menu-btn-share"]')};
}"""


def _prepare_yiyan_share_button(page: Any, *, timeout_ms: int = 15_000) -> Any:
    """Reveal the latest completed answer's share control in Wenxin's inner scroller."""

    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        try:
            page.evaluate(_PREPARE_YIYAN_SHARE_JS)
        except Exception:
            pass
        try:
            buttons = page.locator('[data-testid="menu-btn-share"]')
            for index in range(buttons.count() - 1, -1, -1):
                locator = buttons.nth(index)
                if locator.is_visible(timeout=200):
                    return locator
        except Exception:
            pass
        page.wait_for_timeout(250)
    raise OfficialShareExportError(
        "Wenxin latest completed answer share control was not visible after scrolling"
    )


_DISCOVER_DEEPSEEK_SHARE_BOXES_JS = r"""() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0
      && rect.top < window.innerHeight && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const boxes = [];
  for (const header of Array.from(document.querySelectorAll('.the-header')).filter(visible)) {
    const parent = header.parentElement;
    if (!parent) continue;
    for (const child of Array.from(parent.children)) {
      if (child === header || child.getAttribute('role') !== 'button'
          || !child.querySelector('svg') || !visible(child)) continue;
      const rect = child.getBoundingClientRect();
      boxes.push({x: rect.x, y: rect.y, width: rect.width, height: rect.height});
    }
  }
  return boxes;
}"""


@dataclass(frozen=True)
class _CoordinateControl:
    """Minimal locator contract for an unlabeled control verified by hover semantics."""

    page: Any
    box: dict[str, float]

    def scroll_into_view_if_needed(self, **_kwargs: Any) -> None:
        return None

    def bounding_box(self, **_kwargs: Any) -> dict[str, float]:
        return dict(self.box)

    def click(self, **_kwargs: Any) -> None:
        self.page.mouse.click(
            self.box["x"] + self.box["width"] / 2,
            self.box["y"] + self.box["height"] / 2,
        )


def _find_deepseek_share_button(page: Any, *, timeout_ms: int = 15_000) -> Any:
    """Rediscover and semantically verify DeepSeek's live share affordance.

    React replaces header nodes after long-page screenshot restoration, so a marker
    cannot be tagged once and awaited.  Each poll starts from the visible header,
    hovers its direct visible icon candidates and accepts only tooltip text ``分享``.
    """

    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        try:
            # Move away first so hovering the same icon after a prior attempt
            # retriggers the transient tooltip.
            page.mouse.move(50, 100)
            page.wait_for_timeout(150)
            raw_boxes = page.evaluate(_DISCOVER_DEEPSEEK_SHARE_BOXES_JS)
            boxes = raw_boxes if isinstance(raw_boxes, list) else []
        except Exception:
            boxes = []
        for raw_box in boxes:
            try:
                if not isinstance(raw_box, dict):
                    continue
                box = {
                    key: float(raw_box[key])
                    for key in ("x", "y", "width", "height")
                }
                if box["width"] <= 0 or box["height"] <= 0:
                    continue
                page.mouse.move(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
                page.wait_for_timeout(500)
                tooltips = page.locator(".ds-tooltip")
                for tooltip_index in range(tooltips.count()):
                    tooltip = tooltips.nth(tooltip_index)
                    if (
                        tooltip.is_visible(timeout=200)
                        and tooltip.inner_text().strip() == "分享"
                    ):
                        return _CoordinateControl(page, box)
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise OfficialShareExportError(
        "DeepSeek visible share button with verified 分享 tooltip was not found"
    )


def _urls_from_text(value: object) -> list[str]:
    return _HTTP_URL_RE.findall(str(value or ""))


def _resolve_deepseek_share_url(
    api_payload: object,
    dom_text: object,
    clipboard_text: object,
) -> str | None:
    """Resolve the public URL from authoritative API data, then visible fallbacks."""

    share_id: object = None
    if isinstance(api_payload, dict):
        data = api_payload.get("data")
        if isinstance(data, dict):
            biz_data = data.get("biz_data")
            if isinstance(biz_data, dict):
                share_id = biz_data.get("share_id")
    if isinstance(share_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,128}", share_id):
        candidate = validated_deepseek_share_url(
            f"https://chat.deepseek.com/share/{share_id}"
        )
        if candidate:
            return candidate
    for source in (dom_text, clipboard_text):
        for candidate in _urls_from_text(source):
            if url := validated_deepseek_share_url(candidate):
                return url
    return None


_FLATTEN_DEEPSEEK_PUBLIC_SHARE_JS = r"""() => {
  const list = document.querySelector('.ds-virtual-list');
  const items = document.querySelector('.ds-virtual-list-items');
  if (!list || !items) return null;

  // The authenticated public-share page keeps a sticky "continue chatting"
  // composer above the shared answer.  It is not part of the shared content and
  // otherwise covers the middle of a capture that extends beyond the viewport.
  const textarea = document.querySelector('textarea[placeholder*="DeepSeek"]');
  let sticky = textarea;
  while (sticky && sticky !== items && getComputedStyle(sticky).position !== 'sticky') {
    sticky = sticky.parentElement;
  }
  if (sticky && sticky !== items) {
    sticky.style.setProperty('display', 'none', 'important');
  }

  const contentHeight = Math.ceil(items.getBoundingClientRect().height);
  list.scrollTop = 0;
  list.scrollLeft = 0;
  for (const descendant of items.querySelectorAll('*')) {
    if (descendant.scrollLeft) descendant.scrollLeft = 0;
  }
  list.style.setProperty('height', contentHeight + 'px', 'important');
  list.style.setProperty('max-height', 'none', 'important');
  list.style.setProperty('overflow', 'visible', 'important');
  let ancestor = list.parentElement;
  while (ancestor) {
    ancestor.style.setProperty('height', 'auto', 'important');
    ancestor.style.setProperty('max-height', 'none', 'important');
    ancestor.style.setProperty('overflow', 'visible', 'important');
    if (ancestor === document.documentElement) break;
    ancestor = ancestor.parentElement;
  }
  document.body.style.setProperty('min-height', (contentHeight + 80) + 'px', 'important');
  document.documentElement.style.setProperty(
    'min-height', (contentHeight + 80) + 'px', 'important');
  void document.body.offsetHeight;
  const rect = items.getBoundingClientRect();
  return {x: rect.x, y: rect.y, width: rect.width, height: rect.height, scale: 1};
}"""


def _capture_deepseek_public_share_card(page: Any, out_path: Path) -> None:
    """Capture the complete public-share content outside its clipped inner scroller."""

    raw_clip = page.evaluate(_FLATTEN_DEEPSEEK_PUBLIC_SHARE_JS)
    if not isinstance(raw_clip, dict):
        raise OfficialShareExportError("DeepSeek public share content was not found")
    page.wait_for_timeout(300)
    try:
        final_clip = page.evaluate(
            """() => {
              const list = document.querySelector('.ds-virtual-list');
              const items = document.querySelector('.ds-virtual-list-items');
              if (!list || !items) return null;
              list.scrollTop = 0;
              list.scrollLeft = 0;
              for (const descendant of items.querySelectorAll('*')) {
                if (descendant.scrollLeft) descendant.scrollLeft = 0;
              }
              const rect = items.getBoundingClientRect();
              return {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight
              };
            }"""
        )
        if isinstance(final_clip, dict):
            raw_clip = final_clip
    except Exception:
        pass
    try:
        clip = {
            "x": float(raw_clip["x"]),
            "y": float(raw_clip["y"]),
            "width": float(raw_clip["width"]),
            "height": float(raw_clip["height"]),
        }
        viewport_width = float(raw_clip["viewportWidth"])
        viewport_height = float(raw_clip["viewportHeight"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialShareExportError("DeepSeek public share bounds were invalid") from exc
    if (
        clip["width"] <= 0
        or clip["height"] <= 0
        or clip["width"] > 5_000
        or clip["height"] > 50_000
        or viewport_width <= 0
        or viewport_width > 5_000
    ):
        raise OfficialShareExportError("DeepSeek public share bounds were unsafe")

    # Capturing only ``clip`` makes Chromium temporarily treat its width as the
    # viewport width.  That trips DeepSeek's responsive CSS and can shift the
    # answer underneath the crop.  Capture at the unchanged real viewport width,
    # then crop the immutable bitmap to the measured share-content rectangle.
    full_clip = {
        "x": 0,
        "y": 0,
        "width": viewport_width,
        "height": max(viewport_height, clip["y"] + clip["height"]),
        "scale": 1,
    }
    cdp = page.context.new_cdp_session(page)
    try:
        result = cdp.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "fromSurface": True,
                "clip": full_clip,
            },
        )
    finally:
        try:
            cdp.detach()
        except Exception:
            pass
    payload = result.get("data") if isinstance(result, dict) else None
    if not isinstance(payload, str) or not payload:
        raise OfficialShareExportError("DeepSeek public share screenshot was empty")
    try:
        full_bytes = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(full_bytes)) as image:
            image.load()
            scale_x = image.width / full_clip["width"]
            scale_y = image.height / full_clip["height"]
            crop_box = (
                round(clip["x"] * scale_x),
                round(clip["y"] * scale_y),
                round((clip["x"] + clip["width"]) * scale_x),
                round((clip["y"] + clip["height"]) * scale_y),
            )
            if (
                crop_box[0] < 0
                or crop_box[1] < 0
                or crop_box[2] > image.width
                or crop_box[3] > image.height
                or crop_box[2] <= crop_box[0]
                or crop_box[3] <= crop_box[1]
            ):
                raise ValueError("crop is outside captured page")
            image.crop(crop_box).save(out_path, format="PNG")
        if out_path.stat().st_size > _MAX_SHARE_IMAGE_BYTES:
            raise ValueError("image exceeds maximum size")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise OfficialShareExportError("DeepSeek public share screenshot was invalid") from exc


def _dismiss_deepseek_share_ui(page: Any) -> None:
    """Leave a resident DeepSeek tab outside dialog and selection modes."""

    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        except Exception:
            break
    try:
        controls = page.locator('[role="button"]')
        for index in range(controls.count() - 1, -1, -1):
            control = controls.nth(index)
            if (
                control.is_visible(timeout=100)
                and control.inner_text(timeout=200).strip() == "取消"
            ):
                control.click(timeout=1_000)
                break
    except Exception:
        pass


def _largest_visible(page: Any, selector: str) -> Any:
    try:
        candidates = page.locator(selector).all()
    except Exception as exc:
        raise OfficialShareExportError(f"official share card not found: {selector}") from exc
    best: tuple[float, Any] | None = None
    for candidate in candidates:
        try:
            if not candidate.is_visible(timeout=500):
                continue
            box = candidate.bounding_box() or {}
            area = float(box.get("width") or 0) * float(box.get("height") or 0)
        except Exception:
            continue
        if area > 0 and (best is None or area > best[0]):
            best = (area, candidate)
    if best is None:
        raise OfficialShareExportError(f"official share card not visible: {selector}")
    return best[1]


def _capture_yiyan_share_card_tiled(page: Any, out_path: Path) -> dict[str, Any]:
    """Stitch Wenxin's official share preview from its real scroll viewport.

    The preview card is roughly 6k pixels tall, while Wenxin paints only the
    currently visible 452px dialog-body slice.  A locator/full-page screenshot
    therefore produces a deceptive long PNG whose lower 90% is blank.  Scrolling
    the platform's own preview viewport and stitching those painted slices keeps
    every pixel sourced from the official preview without inventing content.
    """

    metrics = page.evaluate(
        """() => {
          const body = document.querySelector('.cos-dialog-body');
          const root = document.querySelector('div[class^="_share-wrapper_"] > div');
          if (!body || !root) return null;
          const control = Array.from(body.children).find(
            (el) => String(el.className).startsWith('_footer_'));
          const record = {
            bodyScrollTop: body.scrollTop,
            control: control,
            controlHadStyle: !!control && control.hasAttribute('style'),
            controlStyle: control ? (control.getAttribute('style') || '') : ''
          };
          window.__geoYiyanShareCapture = record;
          if (control) control.style.setProperty('display', 'none', 'important');
          body.scrollTop = 0;
          const rect = root.getBoundingClientRect();
          return {
            width: rect.width,
            height: rect.height,
            step: body.clientHeight,
            maxScroll: body.scrollHeight - body.clientHeight
          };
        }"""
    )
    if not isinstance(metrics, dict):
        raise OfficialShareExportError("Wenxin official share preview was not found")
    try:
        width = float(metrics["width"])
        height = float(metrics["height"])
        step = int(metrics["step"])
        max_scroll = int(metrics["maxScroll"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialShareExportError("Wenxin share preview bounds were invalid") from exc
    if width <= 0 or height <= 0 or width > 5_000 or height > 50_000 or step <= 0:
        raise OfficialShareExportError("Wenxin share preview bounds were unsafe")

    positions = list(range(0, max_scroll + 1, step))
    if not positions or positions[-1] != max_scroll:
        positions.append(max_scroll)
    if len(positions) > 200:
        raise OfficialShareExportError("Wenxin share preview required too many tiles")

    canvas: Image.Image | None = None
    scale_x = 1.0
    scale_y = 1.0
    painted_bottom = 0
    try:
        for position in positions:
            raw_tile = page.evaluate(
                """(position) => {
                  const body = document.querySelector('.cos-dialog-body');
                  const root = document.querySelector(
                    'div[class^="_share-wrapper_"] > div');
                  if (!body || !root) return null;
                  body.scrollTop = position;
                  const bodyRect = body.getBoundingClientRect();
                  const rootRect = root.getBoundingClientRect();
                  const top = Math.max(bodyRect.top, rootRect.top);
                  const bottom = Math.min(bodyRect.bottom, rootRect.bottom);
                  return {
                    x: rootRect.x,
                    y: top,
                    width: rootRect.width,
                    height: Math.max(0, bottom - top),
                    pasteY: top - rootRect.top
                  };
                }""",
                position,
            )
            if not isinstance(raw_tile, dict) or float(raw_tile.get("height") or 0) <= 0:
                continue
            page.wait_for_timeout(100)
            clip = {
                key: float(raw_tile[key])
                for key in ("x", "y", "width", "height")
            }
            tile_bytes = page.screenshot(clip=clip, animations="disabled")
            with Image.open(io.BytesIO(tile_bytes)) as tile_image:
                tile_image.load()
                tile = tile_image.convert("RGB")
            if canvas is None:
                scale_x = tile.width / clip["width"]
                scale_y = tile.height / clip["height"]
                canvas = Image.new(
                    "RGB",
                    (round(width * scale_x), round(height * scale_y)),
                    "white",
                )
            paste_y = round(float(raw_tile["pasteY"]) * scale_y)
            canvas.paste(tile, (0, paste_y))
            painted_bottom = max(painted_bottom, paste_y + tile.height)

        if canvas is None or painted_bottom < canvas.height - 2:
            raise OfficialShareExportError("Wenxin share preview tiles were incomplete")
        canvas.save(out_path, format="PNG")
        if out_path.stat().st_size > _MAX_SHARE_IMAGE_BYTES:
            raise OfficialShareExportError("Wenxin official share preview exceeded size limit")
        return {
            "tile_count": len(positions),
            "width": canvas.width,
            "height": canvas.height,
        }
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise OfficialShareExportError("Wenxin official share preview was invalid") from exc
    finally:
        try:
            page.evaluate(
                """() => {
                  const body = document.querySelector('.cos-dialog-body');
                  const record = window.__geoYiyanShareCapture;
                  if (!record) return false;
                  if (body) body.scrollTop = record.bodyScrollTop;
                  const control = record.control;
                  if (control && control.isConnected) {
                    if (record.controlHadStyle) {
                      control.setAttribute('style', record.controlStyle);
                    } else {
                      control.removeAttribute('style');
                    }
                  }
                  delete window.__geoYiyanShareCapture;
                  return true;
                }"""
            )
        except Exception:
            pass


def _dismiss_yiyan_share_ui(page: Any) -> None:
    """Close Wenxin's image preview and share-selection mode in a resident tab."""

    try:
        dialog_closes = page.locator(".cos-dialog-close")
        for index in range(dialog_closes.count() - 1, -1, -1):
            close = dialog_closes.nth(index)
            if close.is_visible(timeout=100):
                close.click(timeout=1_000)
                page.wait_for_timeout(150)
                break
    except Exception:
        pass
    try:
        close_controls = page.locator(".share-footer .cos-icon-close")
        for index in range(close_controls.count() - 1, -1, -1):
            close = close_controls.nth(index)
            if close.is_visible(timeout=100):
                close.click(timeout=1_000)
                break
    except Exception:
        pass


def capture_yiyan_official_share(
    page: Any,
    out_path: Path,
    *,
    click: Callable[[Any], None] | None = None,
) -> OfficialShareArtifacts:
    """Capture Wenxin's official share-card preview and copied public link."""

    click_control = click or (lambda locator: locator.click(timeout=8_000))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _grant_clipboard(page)
    audit: dict[str, Any] = {"platform": "yiyan", "image_source": "share_preview_card"}
    try:
        share = _prepare_yiyan_share_button(page)
        click_control(share)
        copy_link = _first_enabled(
            page,
            (
                '.share-footer button:has-text("复制链接")',
                '.share-footer [role="button"]:has-text("复制链接")',
            ),
        )
        click_control(copy_link)
        page.wait_for_timeout(700)
        share_url = _wait_clipboard_url(page, validated_yiyan_share_url)
        if share_url is None:
            raise OfficialShareExportError("Wenxin official share link was not copied")

        image = _first_enabled(
            page,
            (
                '.share-footer button:has-text("分享图片")',
                '.share-footer [role="button"]:has-text("分享图片")',
            ),
        )
        click_control(image)
        _first_visible(
            page,
            ('div[class^="_share-wrapper_"] > div',),
            timeout_ms=30_000,
        )
        page.wait_for_timeout(1_000)  # preview card fonts/images finish hydrating
        capture_audit = _capture_yiyan_share_card_tiled(page, out_path)
        if not valid_png(out_path):
            raise OfficialShareExportError("Wenxin official share preview is not a valid PNG")
        audit["share_url"] = share_url
        audit["image_bytes"] = out_path.stat().st_size
        audit.update(capture_audit)
        return OfficialShareArtifacts(out_path, share_url, audit)
    finally:
        # Selection mode and preview are overlays in a resident tab.  Always close
        # them so the following batch item starts from an interactable page.
        _dismiss_yiyan_share_ui(page)


def capture_deepseek_official_share(
    page: Any,
    out_path: Path,
    *,
    click: Callable[[Any], None] | None = None,
) -> OfficialShareArtifacts:
    """Create DeepSeek's public share and screenshot that official share page.

    DeepSeek currently has no native image-download action.  The image is therefore
    captured from the clean, public ``/share/...`` page, never from the authenticated
    runtime chat window.
    """

    click_control = click or (lambda locator: locator.click(timeout=8_000))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _grant_clipboard(page)
    public_page: Any | None = None
    try:
        click_control(_find_deepseek_share_button(page))
        create_link = _first_visible(
            page,
            (
                'button:has-text("创建分享链接")',
                '[role="button"]:has-text("创建分享链接")',
            ),
        )
        selection_text = ""
        try:
            selection_text = page.locator("body").inner_text(timeout=1_000)
        except Exception:
            pass
        if "已选择 1 组对话" not in selection_text:
            raise OfficialShareExportError(
                "DeepSeek share selection did not contain exactly one fresh-chat conversation"
            )
        click_control(create_link)
        confirm = _first_visible(
            page,
            ('button:has-text("创建并复制")', '[role="button"]:has-text("创建并复制")'),
        )
        create_response: Any | None = None
        confirm_clicked = False
        try:
            with page.expect_response(
                lambda response: (
                    "/api/v0/share/create" in str(response.url)
                    and str(response.request.method).upper() == "POST"
                ),
                timeout=15_000,
            ) as response_info:
                click_control(confirm)
                confirm_clicked = True
            create_response = response_info.value
        except Exception:
            # DOM and clipboard remain independently validated official sources.
            if not confirm_clicked:
                click_control(confirm)
        page.wait_for_timeout(500)
        api_payload: object = None
        if create_response is not None:
            try:
                api_payload = create_response.json()
            except Exception:
                api_payload = None
        try:
            dom_text: object = page.locator('[role="dialog"]').last.inner_text(timeout=1_000)
        except Exception:
            dom_text = ""
        try:
            clipboard_text: object = page.evaluate(
                "async () => await navigator.clipboard.readText()"
            )
        except Exception:
            clipboard_text = ""
        share_url = _resolve_deepseek_share_url(api_payload, dom_text, clipboard_text)
        if share_url is None:
            raise OfficialShareExportError(
                "DeepSeek official share URL was absent from API, dialog and clipboard"
            )

        public_page = page.context.new_page()
        public_page.goto(share_url, wait_until="domcontentloaded", timeout=25_000)
        _first_visible(public_page, ('text="来自分享的对话"',), timeout_ms=15_000)
        public_page.wait_for_timeout(1_500)  # public answer hydrates after the header
        _capture_deepseek_public_share_card(public_page, out_path)
        if not valid_png(out_path):
            raise OfficialShareExportError("DeepSeek public share page is not a valid PNG")
        return OfficialShareArtifacts(
            out_path,
            share_url,
            {
                "platform": "deepseek",
                "image_source": "official_public_share_page",
                "image_bytes": out_path.stat().st_size,
            },
        )
    finally:
        if public_page is not None:
            try:
                public_page.close()
            except Exception:
                pass
        _dismiss_deepseek_share_ui(page)
