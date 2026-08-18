"""Official answer-sharing exports for browser-backed platform adapters."""

from __future__ import annotations

import base64
import io
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_SHARE_IMAGE_BYTES = 30 * 1024 * 1024
_MAX_SHARE_VERIFICATION_BYTES = 2 * 1024 * 1024
_MAX_SHARE_REDIRECTS = 5
SHARE_PROBE_VERSION = "official-share-http-v1"
_HTTP_URL_RE = re.compile(r"https://[^\s<>\]\[\"'，。；]+")


class OfficialShareExportError(RuntimeError):
    """The platform did not yield both an official share link and share image."""


@dataclass(frozen=True)
class OfficialShareArtifacts:
    image_path: Path
    share_url: str
    audit: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ShareLinkVerification:
    checked_at: datetime | None
    availability_status: str
    http_status: int | None
    final_url: str | None
    redirect_chain: tuple[dict[str, Any], ...]
    allowlist_valid: bool
    content_hash: str | None
    embed_status: str
    x_frame_options: str | None
    csp_frame_ancestors: str | None
    embed_reason: str | None
    failure_reason: str | None
    probe_version: str = SHARE_PROBE_VERSION

    def as_manifest(self) -> dict[str, Any]:
        return {
            "allowlist_valid": self.allowlist_valid,
            "availability_status": self.availability_status,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "content_hash": self.content_hash,
            "csp_frame_ancestors": self.csp_frame_ancestors,
            "embed_reason": self.embed_reason,
            "embed_status": self.embed_status,
            "failure_reason": self.failure_reason,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "probe_version": self.probe_version,
            "redirect_chain": list(self.redirect_chain),
            "x_frame_options": self.x_frame_options,
        }


def unchecked_share_verification(share_url: str) -> ShareLinkVerification:
    return ShareLinkVerification(
        checked_at=None,
        availability_status="unchecked",
        http_status=None,
        final_url=share_url,
        redirect_chain=(),
        allowlist_valid=False,
        content_hash=None,
        embed_status="unknown",
        x_frame_options=None,
        csp_frame_ancestors=None,
        embed_reason="not_checked",
        failure_reason=None,
    )


def _frame_policy(headers: httpx.Headers) -> tuple[str, str | None, str | None, str]:
    x_frame_options = headers.get("x-frame-options")
    if x_frame_options is not None:
        x_frame_options = x_frame_options.strip()[:500] or None
    csp = headers.get("content-security-policy", "")
    frame_ancestors: str | None = None
    for raw_directive in csp.split(";"):
        directive = raw_directive.strip()
        if directive.lower().startswith("frame-ancestors"):
            frame_ancestors = directive[:1_000]
            break
    normalized_xfo = (x_frame_options or "").lower()
    if any(token in normalized_xfo for token in ("deny", "sameorigin", "allow-from")):
        return "blocked", x_frame_options, frame_ancestors, "x_frame_options_restricts_embedding"
    if frame_ancestors is not None:
        sources = frame_ancestors.split()[1:]
        if "*" in sources:
            return "allowed", x_frame_options, frame_ancestors, "csp_frame_ancestors_wildcard"
        return (
            "blocked",
            x_frame_options,
            frame_ancestors,
            "csp_frame_ancestors_restricts_embedding",
        )
    if x_frame_options:
        return "unknown", x_frame_options, None, "unrecognized_x_frame_options"
    return "allowed", None, None, "no_restrictive_frame_policy"


def probe_official_share_url(
    share_url: str,
    *,
    allowed_hosts: set[str],
    client: httpx.Client | None = None,
) -> ShareLinkVerification:
    """Verify one official URL without following a redirect outside its allowlist."""

    checked_at = datetime.now(UTC)
    normalized_hosts = {host.casefold() for host in allowed_hosts}

    def allowed(value: str) -> str | None:
        return _validated_share_url(value, hosts=normalized_hosts)

    current_url = allowed(share_url)
    if current_url is None:
        return ShareLinkVerification(
            checked_at=checked_at,
            availability_status="unreachable",
            http_status=None,
            final_url=None,
            redirect_chain=(),
            allowlist_valid=False,
            content_hash=None,
            embed_status="unknown",
            x_frame_options=None,
            csp_frame_ancestors=None,
            embed_reason="url_allowlist_rejected",
            failure_reason="url_allowlist_rejected",
        )

    owned_client = client is None
    active_client = client or httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(8.0, connect=5.0),
        trust_env=False,
        headers={"User-Agent": "GEO-Official-Share-Verifier/1.0"},
    )
    redirects: list[dict[str, Any]] = []
    try:
        for redirect_index in range(_MAX_SHARE_REDIRECTS + 1):
            with active_client.stream("GET", current_url) as response:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return ShareLinkVerification(
                            checked_at=checked_at,
                            availability_status="unreachable",
                            http_status=status,
                            final_url=current_url,
                            redirect_chain=tuple(redirects),
                            allowlist_valid=True,
                            content_hash=None,
                            embed_status="unknown",
                            x_frame_options=None,
                            csp_frame_ancestors=None,
                            embed_reason="redirect_location_missing",
                            failure_reason="redirect_location_missing",
                        )
                    next_url = allowed(urljoin(current_url, location))
                    if next_url is None:
                        return ShareLinkVerification(
                            checked_at=checked_at,
                            availability_status="unreachable",
                            http_status=status,
                            final_url=current_url,
                            redirect_chain=tuple(redirects),
                            allowlist_valid=False,
                            content_hash=None,
                            embed_status="unknown",
                            x_frame_options=None,
                            csp_frame_ancestors=None,
                            embed_reason="redirect_allowlist_rejected",
                            failure_reason="redirect_allowlist_rejected",
                        )
                    redirects.append(
                        {"from_url": current_url, "http_status": status, "to_url": next_url}
                    )
                    current_url = next_url
                    if redirect_index == _MAX_SHARE_REDIRECTS:
                        return ShareLinkVerification(
                            checked_at=checked_at,
                            availability_status="unreachable",
                            http_status=status,
                            final_url=current_url,
                            redirect_chain=tuple(redirects),
                            allowlist_valid=True,
                            content_hash=None,
                            embed_status="unknown",
                            x_frame_options=None,
                            csp_frame_ancestors=None,
                            embed_reason="redirect_limit_exceeded",
                            failure_reason="redirect_limit_exceeded",
                        )
                    continue

                availability = (
                    "redirected"
                    if 200 <= status < 300 and redirects
                    else "reachable"
                    if 200 <= status < 300
                    else "blocked"
                    if status in {401, 403, 407, 429}
                    else "unreachable"
                )
                if availability in {"reachable", "redirected"}:
                    embed_status, xfo, frame_ancestors, embed_reason = _frame_policy(
                        response.headers
                    )
                    digest = sha256()
                    byte_count = 0
                    complete = True
                    for chunk in response.iter_bytes():
                        byte_count += len(chunk)
                        if byte_count > _MAX_SHARE_VERIFICATION_BYTES:
                            complete = False
                            break
                        digest.update(chunk)
                    content_hash = digest.hexdigest() if complete else None
                else:
                    embed_status, xfo, frame_ancestors, embed_reason = (
                        "unknown",
                        None,
                        None,
                        "share_page_not_reachable",
                    )
                    content_hash = None
                return ShareLinkVerification(
                    checked_at=checked_at,
                    availability_status=availability,
                    http_status=status,
                    final_url=current_url,
                    redirect_chain=tuple(redirects),
                    allowlist_valid=True,
                    content_hash=content_hash,
                    embed_status=embed_status,
                    x_frame_options=xfo,
                    csp_frame_ancestors=frame_ancestors,
                    embed_reason=embed_reason,
                    failure_reason=(
                        None if availability in {"reachable", "redirected"} else f"http_{status}"
                    ),
                )
    except httpx.HTTPError as exc:
        return ShareLinkVerification(
            checked_at=checked_at,
            availability_status="unreachable",
            http_status=None,
            final_url=current_url,
            redirect_chain=tuple(redirects),
            allowlist_valid=True,
            content_hash=None,
            embed_status="unknown",
            x_frame_options=None,
            csp_frame_ancestors=None,
            embed_reason="http_probe_failed",
            failure_reason=type(exc).__name__,
        )
    finally:
        if owned_client:
            active_client.close()

    raise AssertionError("official share probe exhausted without a result")


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
    verification: ShareLinkVerification | None = None,
) -> None:
    verification = verification or unchecked_share_verification(share_url)
    path.write_text(
        json.dumps(
            {
                "channel": channel,
                "platform": platform,
                "schema_version": "official-share-link-v2",
                "url": share_url,
                "verification": verification.as_manifest(),
            },
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
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
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
                box = {key: float(raw_box[key]) for key in ("x", "y", "width", "height")}
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
                    if tooltip.is_visible(timeout=200) and tooltip.inner_text().strip() == "分享":
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
        candidate = validated_deepseek_share_url(f"https://chat.deepseek.com/share/{share_id}")
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


_YIYAN_SHARE_UPLOAD_URL_RE = re.compile(r"https://[^/]*\.bcebos\.com/.*fileManager/")


def _download_yiyan_share_image(
    page: Any,
    out_path: Path,
    *,
    click: Callable[[Any], None] | None = None,
    timeout_ms: int = 60_000,
    settle_ms: int = 3_000,
) -> dict[str, Any]:
    """Take Wenxin's official share image from the「下载图片」button's own payload.

    The preview dialog's download button renders the full share card client-side
    and multipart-uploads the PNG to ``aisearch.bj.bcebos.com`` (BOS) before
    offering it.  The collection relay resets request bodies beyond ~128 KiB
    (live-verified 2026-08-13: every PUT part dies with ERR_CONNECTION_RESET and
    the platform toasts 网络异常), so the upload can never complete through it.
    The PUT payloads are nevertheless the platform's own rendered image, so this
    capture intercepts the parts, reassembles them in ``partNumber`` order and
    aborts the doomed requests.  Fail-closed: no payload, non-contiguous parts or
    an invalid reassembled PNG raise OfficialShareExportError — never fall back
    to a preview-window screenshot.
    """

    parts: dict[int, bytes] = {}

    def _handle(route: Any) -> None:
        request = route.request
        if str(request.method).upper() != "PUT":
            route.continue_()
            return
        query = parse_qs(urlsplit(str(request.url)).query)
        try:
            part_number = int((query.get("partNumber") or [""])[0])
        except (TypeError, ValueError):
            part_number = -1
        try:
            body = request.post_data_buffer
        except Exception:
            body = None
        if part_number >= 1 and body:
            parts[part_number] = bytes(body)
        route.abort()

    click_control = click or (lambda locator: locator.click(timeout=8_000))
    page.route(_YIYAN_SHARE_UPLOAD_URL_RE, _handle)
    try:
        download = _first_enabled(
            page,
            (
                '.cos-dialog button:has-text("下载图片")',
                '.cos-dialog [role="button"]:has-text("下载图片")',
            ),
            timeout_ms=10_000,
        )
        click_control(download)
        deadline = time.monotonic() + timeout_ms / 1_000
        last_count = 0
        stable_since: float | None = None
        while time.monotonic() < deadline:
            page.wait_for_timeout(500)
            count = len(parts)
            if count == 0:
                continue
            if count != last_count:
                last_count = count
                stable_since = time.monotonic()
            elif (
                stable_since is not None and (time.monotonic() - stable_since) * 1_000 >= settle_ms
            ):
                break
        if not parts:
            raise OfficialShareExportError(
                "Wenxin share download produced no upload payload (the 下载图片 "
                "click did not reach the platform's BOS multipart upload)"
            )
        ordered_numbers = sorted(parts)
        if ordered_numbers != list(range(1, len(parts) + 1)):
            raise OfficialShareExportError(
                f"Wenxin share upload parts were not contiguous: {ordered_numbers}"
            )
        data = b"".join(parts[number] for number in ordered_numbers)
        if len(data) > _MAX_SHARE_IMAGE_BYTES:
            raise OfficialShareExportError("Wenxin share download exceeded size limit")
        out_path.write_bytes(data)
        if not valid_png(out_path):
            raise OfficialShareExportError("Wenxin share download payload is not a valid PNG")
        return {
            "image_source": "share_download_button",
            "upload_transport": "bos_upload_intercept",
            "part_count": len(ordered_numbers),
            "image_bytes": len(data),
        }
    finally:
        try:
            page.unroute(_YIYAN_SHARE_UPLOAD_URL_RE, _handle)
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
    """Capture Wenxin's official share download (下载图片) and copied public link."""

    click_control = click or (lambda locator: locator.click(timeout=8_000))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _grant_clipboard(page)
    audit: dict[str, Any] = {"platform": "yiyan", "image_source": "share_download_button"}
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
        capture_audit = _download_yiyan_share_image(page, out_path, click=click_control)
        audit["share_url"] = share_url
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
