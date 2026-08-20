from __future__ import annotations

import base64
import json
import time
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from workflows.activities import official_share
from workflows.activities.official_share import (
    OfficialShareExportError,
    probe_official_share_url,
    recover_png_from_export_audit,
    valid_png,
    validated_deepseek_share_url,
    validated_yiyan_share_url,
)

_ONE_PIXEL_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"


def test_share_probe_records_redirect_hash_and_blocking_frame_policy() -> None:
    payload = b"official shared answer"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/share/start":
            return httpx.Response(302, headers={"Location": "/share/final"})
        return httpx.Response(200, headers={"X-Frame-Options": "DENY"}, content=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_official_share_url(
            "https://chat.deepseek.com/share/start",
            allowed_hosts={"chat.deepseek.com"},
            client=client,
        )

    assert result.availability_status == "redirected"
    assert result.http_status == 200
    assert result.final_url == "https://chat.deepseek.com/share/final"
    assert len(result.redirect_chain) == 1
    assert result.content_hash == sha256(payload).hexdigest()
    assert result.embed_status == "blocked"
    assert result.embed_reason == "x_frame_options_restricts_embedding"


def test_share_probe_rejects_redirect_outside_platform_allowlist() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example/share"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_official_share_url(
            "https://www.doubao.com/thread/test",
            allowed_hosts={"doubao.com", "www.doubao.com"},
            client=client,
        )

    assert result.availability_status == "unreachable"
    assert not result.allowlist_valid
    assert result.failure_reason == "redirect_allowlist_rejected"


def test_share_manifest_always_carries_normalized_verification(tmp_path: Path) -> None:
    path = tmp_path / "share-link.json"
    official_share.write_share_link_manifest(
        path,
        share_url="https://chat.deepseek.com/share/test",
        platform="deepseek",
        channel="create-and-copy",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "official-share-link-v2"
    assert payload["verification"]["availability_status"] == "unchecked"
    assert payload["verification"]["embed_status"] == "unknown"


class _YiyanShareLocator:
    def __init__(self, page: _YiyanSharePage, selector: str) -> None:
        self._page = page
        self.selector = selector

    @property
    def last(self) -> _YiyanShareLocator:
        return self

    def count(self) -> int:
        return 1

    def nth(self, index: int) -> _YiyanShareLocator:
        assert index == 0
        return self

    def evaluate(self, _script: str, *_args: Any) -> None:
        if self.selector == "#conversation-flow-container":
            self._page.scroll_to_bottom()

    def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "visible"
        self._page.events.append(("wait_for", self.selector, timeout))
        if "share" in self.selector and not self._page.at_bottom:
            raise TimeoutError("share control is outside the conversation viewport")

    def is_visible(self, *, timeout: int | None = None) -> bool:
        del timeout
        return "share" not in self.selector or self._page.at_bottom


class _YiyanSharePage:
    """DOM model where Wenxin's share control only becomes visible at scroll end."""

    def __init__(self) -> None:
        self.scroll_height = 5_451
        self.client_height = 556
        self.scroll_top = 1_757
        self.events: list[tuple[Any, ...]] = []
        self.share_lookup_scroll_tops: list[int] = []
        self.share = _YiyanShareLocator(self, '[data-testid="menu-btn-share"]')

    @property
    def at_bottom(self) -> bool:
        return self.scroll_top == self.scroll_height - self.client_height

    def scroll_to_bottom(self) -> None:
        self.events.append(("scroll_to_bottom", "#conversation-flow-container"))
        self.scroll_top = self.scroll_height - self.client_height

    def evaluate(self, script: str, *_args: Any) -> dict[str, bool]:
        self.events.append(("evaluate",))
        # Emulate the browser effect, while leaving the assertion focused on the
        # resulting order/state rather than an exact JavaScript implementation.
        if "conversation-flow-container" in script and "scroll" in script.lower():
            self.scroll_to_bottom()
            self.events.append(("discover_share", '[data-testid="menu-btn-share"]'))
            self.share_lookup_scroll_tops.append(self.scroll_top)
            return {"found": True, "visible": self.at_bottom}
        return {"found": False, "visible": False}

    def locator(self, selector: str) -> _YiyanShareLocator:
        self.events.append(("locator", selector))
        if selector == '[data-testid="menu-btn-share"]':
            self.share_lookup_scroll_tops.append(self.scroll_top)
            return self.share
        return _YiyanShareLocator(self, selector)

    def wait_for_timeout(self, timeout: float) -> None:
        self.events.append(("wait", timeout))


class _DeepSeekButton:
    def __init__(
        self,
        page: _DeepSeekSharePage,
        name: str,
        *,
        tooltip: str,
        visible: bool = True,
    ) -> None:
        self._page = page
        self.name = name
        self.tooltip = tooltip
        self.visible = visible

    @property
    def last(self) -> _DeepSeekButton:
        return self

    def is_visible(self, *, timeout: int | None = None) -> bool:
        del timeout
        return self.visible

    def wait_for(self, *, state: str, timeout: int) -> None:
        del timeout
        if state != "visible" or not self.visible:
            raise TimeoutError(f"button is not visible: {self.name}")

    def hover(self, *, timeout: int | None = None) -> None:
        del timeout
        if not self.visible:
            raise TimeoutError(f"button is not visible: {self.name}")
        self._page.hovered.append(self.name)
        self._page.current_tooltip = self.tooltip


class _DeepSeekMouse:
    def __init__(self, page: _DeepSeekSharePage) -> None:
        self._page = page

    def move(self, x: float, y: float) -> None:
        self._page.current_tooltip = None
        for button, box in self._page._candidate_boxes:
            if (
                box["x"] <= x <= box["x"] + box["width"]
                and box["y"] <= y <= box["y"] + box["height"]
            ):
                self._page.hovered.append(button.name)
                self._page.current_tooltip = button.tooltip
                break

    def click(self, _x: float, _y: float) -> None:
        pass


class _DeepSeekLocatorList:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    @property
    def last(self) -> Any:
        if not self._items:
            raise TimeoutError("no matching locator")
        return self._items[-1]

    def all(self) -> list[Any]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)

    def nth(self, index: int) -> Any:
        return self._items[index]


class _DeepSeekButtonParent:
    def __init__(
        self,
        direct_buttons: list[_DeepSeekButton],
        nested_buttons: list[_DeepSeekButton],
    ) -> None:
        self._direct_buttons = direct_buttons
        self._nested_buttons = nested_buttons

    def locator(self, selector: str) -> _DeepSeekLocatorList:
        buttons = list(self._direct_buttons)
        if ":scope >" not in selector:
            # A descendant query sees a deliberately attractive nested trap. The
            # production contract requires direct children of the header parent.
            buttons.extend(self._nested_buttons)
        if ":visible" in selector:
            buttons = [button for button in buttons if button.visible]
        return _DeepSeekLocatorList(buttons)


class _DeepSeekHeader:
    def __init__(
        self,
        *,
        visible: bool,
        parent: _DeepSeekButtonParent,
    ) -> None:
        self.visible = visible
        self._parent = parent

    def is_visible(self, *, timeout: int | None = None) -> bool:
        del timeout
        return self.visible

    def locator(self, selector: str) -> Any:
        if selector in {"..", "xpath=.."}:
            return self._parent
        return self._parent.locator(selector)


class _DeepSeekTooltip:
    def __init__(self, page: _DeepSeekSharePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def last(self) -> _DeepSeekTooltip:
        return self

    def _matches(self) -> bool:
        tooltip = self._page.current_tooltip
        if tooltip is None:
            return False
        if 'text-is("\u5206\u4eab")' in self._selector:
            return tooltip == "\u5206\u4eab"
        if 'has-text("\u5206\u4eab")' in self._selector:
            return "\u5206\u4eab" in tooltip
        return True

    def wait_for(self, *, state: str, timeout: int) -> None:
        del timeout
        if state != "visible" or not self._matches():
            raise TimeoutError("expected tooltip is not visible")

    def is_visible(self, *, timeout: int | None = None) -> bool:
        del timeout
        return self._matches()

    def inner_text(self, *, timeout: int | None = None) -> str:
        del timeout
        return self._page.current_tooltip or ""

    def text_content(self, *, timeout: int | None = None) -> str:
        return self.inner_text(timeout=timeout)


class _DeepSeekSharePage:
    """Two-render model: the first visible header has no exact share control."""

    def __init__(self) -> None:
        self.header_discoveries = 0
        self.hovered: list[str] = []
        self.current_tooltip: str | None = None
        self.waits: list[float] = []
        self._current_candidates: list[_DeepSeekButton] = []
        self._candidate_boxes: list[tuple[_DeepSeekButton, dict[str, float]]] = []
        self.mouse = _DeepSeekMouse(self)

        hidden_share = _DeepSeekButton(
            self,
            "hidden-share",
            tooltip="\u5206\u4eab",
            visible=False,
        )
        wrong_direct = _DeepSeekButton(self, "wrong-direct", tooltip="\u5206\u4eab\u8bbe\u7f6e")
        nested_trap = _DeepSeekButton(self, "nested-share-trap", tooltip="\u5206\u4eab")
        self.expected = _DeepSeekButton(self, "fresh-direct-share", tooltip="\u5206\u4eab")

        self._hidden = _DeepSeekHeader(
            visible=False,
            parent=_DeepSeekButtonParent([hidden_share], []),
        )
        self._first = _DeepSeekHeader(
            visible=True,
            parent=_DeepSeekButtonParent([wrong_direct], [nested_trap]),
        )
        self._second = _DeepSeekHeader(
            visible=True,
            parent=_DeepSeekButtonParent([self.expected], []),
        )

    def _discover_headers(self, selector: str) -> _DeepSeekLocatorList:
        self.header_discoveries += 1
        current = self._first if self.header_discoveries == 1 else self._second
        headers = [self._hidden, current]
        if ":visible" in selector:
            headers = [header for header in headers if header.visible]
        return _DeepSeekLocatorList(headers)

    def locator(self, selector: str) -> Any:
        if "the-header" in selector:
            return self._discover_headers(selector)
        if selector.startswith('[data-geo-deepseek-share-candidate="'):
            index = int(selector.rsplit('"', 2)[1])
            return _DeepSeekLocatorList([self._current_candidates[index]])
        if selector == ".ds-tooltip":
            return _DeepSeekLocatorList([_DeepSeekTooltip(self, selector)])
        if "tooltip" in selector:
            return _DeepSeekTooltip(self, selector)
        raise AssertionError(f"unexpected page-level selector: {selector}")

    def evaluate(self, _script: str, *_args: Any) -> list[dict[str, float]]:
        headers = self._discover_headers(".the-header:visible").all()
        self._current_candidates = []
        for header in headers:
            parent = header.locator("..")
            self._current_candidates.extend(
                parent.locator(':scope > [role="button"]:visible').all()
            )
        self._candidate_boxes = [
            (
                button,
                {"x": 200.0 + index * 50, "y": 20.0, "width": 30.0, "height": 30.0},
            )
            for index, button in enumerate(self._current_candidates)
        ]
        return [dict(box) for _, box in self._candidate_boxes]

    def get_by_text(self, text: str, *, exact: bool = False) -> _DeepSeekTooltip:
        selector = f'.ds-tooltip:visible:text-is("{text}")' if exact else ".ds-tooltip:visible"
        return _DeepSeekTooltip(self, selector)

    def wait_for_timeout(self, timeout: float) -> None:
        self.waits.append(timeout)


def test_zero_byte_doubao_download_is_recovered_from_official_data_url(tmp_path: Path) -> None:
    path = tmp_path / "share.png"
    path.write_bytes(b"")
    audit = {
        "url": "data:image/octet-stream;base64,"
        + base64.b64encode(_ONE_PIXEL_PNG_HEADER).decode("ascii")
    }

    assert recover_png_from_export_audit(path, audit) is True
    assert valid_png(path) is True


def test_zero_byte_share_without_platform_bytes_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "share.png"
    path.write_bytes(b"")

    assert recover_png_from_export_audit(path, {"ok": True}) is False


def test_share_url_validators_are_platform_scoped() -> None:
    assert (
        validated_deepseek_share_url("https://chat.deepseek.com/share/abc")
        == "https://chat.deepseek.com/share/abc"
    )
    assert validated_deepseek_share_url("https://chat.deepseek.com/a/chat/s/abc") is None
    assert validated_yiyan_share_url("https://mr.baidu.com/r/abc") == "https://mr.baidu.com/r/abc"
    assert validated_yiyan_share_url("https://evil.example/r/abc") is None


def test_yiyan_scrolls_conversation_to_bottom_before_discovering_share_button() -> None:
    page = _YiyanSharePage()

    button = official_share._prepare_yiyan_share_button(page, timeout_ms=1_200)

    assert button is page.share
    assert page.at_bottom is True
    assert page.share_lookup_scroll_tops
    assert all(
        scroll_top == page.scroll_height - page.client_height
        for scroll_top in page.share_lookup_scroll_tops
    )
    scroll_index = next(i for i, event in enumerate(page.events) if event[0] == "scroll_to_bottom")
    share_index = next(
        i
        for i, event in enumerate(page.events)
        if event[:2] == ("discover_share", '[data-testid="menu-btn-share"]')
    )
    assert scroll_index < share_index


def test_deepseek_rediscovers_visible_header_and_requires_exact_share_tooltip() -> None:
    page = _DeepSeekSharePage()

    button = official_share._find_deepseek_share_button(page, timeout_ms=2_000)

    assert button.page is page
    assert button.bounding_box() == {
        "x": 200.0,
        "y": 20.0,
        "width": 30.0,
        "height": 30.0,
    }
    assert page.header_discoveries >= 2
    assert page.hovered == ["wrong-direct", "fresh-direct-share"]
    assert "hidden-share" not in page.hovered
    assert "nested-share-trap" not in page.hovered


@pytest.mark.parametrize(
    ("api_payload", "dom_text", "clipboard_text", "expected"),
    [
        pytest.param(
            {
                "code": 0,
                "data": {
                    "biz_code": 0,
                    "biz_data": {"share_id": "api_ABC-123"},
                },
            },
            "链接：https://chat.deepseek.com/share/dom-choice",
            "https://chat.deepseek.com/share/clipboard-choice",
            "https://chat.deepseek.com/share/api_ABC-123",
            id="api-share-id-wins",
        ),
        pytest.param(
            {"code": 0, "data": {"biz_code": 1, "biz_data": {}}},
            "已创建 https://chat.deepseek.com/share/dom-choice。",
            "https://chat.deepseek.com/share/clipboard-choice",
            "https://chat.deepseek.com/share/dom-choice",
            id="dom-url-follows-unusable-api",
        ),
        pytest.param(
            None,
            "分享链接尚未显示",
            "已复制 https://chat.deepseek.com/share/clipboard-choice",
            "https://chat.deepseek.com/share/clipboard-choice",
            id="clipboard-is-last-resort",
        ),
        pytest.param(
            {"code": 0, "data": {"biz_code": 0, "biz_data": {"share_id": ""}}},
            "https://evil.example/share/not-deepseek",
            "https://chat.deepseek.com/a/chat/s/not-public",
            None,
            id="all-candidates-invalid",
        ),
    ],
)
def test_deepseek_share_url_resolution_prefers_api_then_dom_then_clipboard(
    api_payload: object,
    dom_text: str,
    clipboard_text: str,
    expected: str | None,
) -> None:
    assert (
        official_share._resolve_deepseek_share_url(api_payload, dom_text, clipboard_text)
        == expected
    )


class _YiyanDownloadRoute:
    def __init__(self, page: _YiyanDownloadPage, method: str, url: str, body: bytes | None) -> None:
        self._page = page
        self.request = SimpleNamespace(method=method, url=url, post_data_buffer=body)

    def abort(self) -> None:
        self._page.aborted.append(self.request.url)

    def continue_(self) -> None:
        self._page.continued.append(self.request.url)


class _YiyanDownloadButton:
    def __init__(self, page: _YiyanDownloadPage) -> None:
        self._page = page

    def is_visible(self, *, timeout: int | None = None) -> bool:
        del timeout
        return True

    def get_attribute(self, name: str) -> str | None:
        if name == "class":
            return "cos-button cos-md cos-button-primary"
        return None

    def locator(self, selector: str) -> _YiyanDownloadLocatorList:
        assert selector == ".cos-loading"
        return _YiyanDownloadLocatorList([])

    def click(self, *, timeout: int | None = None) -> None:
        del timeout
        self._page.simulate_upload()


class _YiyanDownloadLocatorList:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def count(self) -> int:
        return len(self._items)

    def nth(self, index: int) -> Any:
        return self._items[index]


class _YiyanDownloadPage:
    """Route-interception model: clicking 下载图片 fires BOS multipart PUT parts."""

    def __init__(self, parts: dict[int, bytes]) -> None:
        self._parts = parts
        self._route: tuple[Any, Any] | None = None
        self.aborted: list[str] = []
        self.continued: list[str] = []
        self.unrouted = False
        self.download = _YiyanDownloadButton(self)

    def route(self, pattern: Any, handler: Any) -> None:
        self._route = (pattern, handler)

    def unroute(self, pattern: Any, handler: Any) -> None:
        assert self._route == (pattern, handler)
        self.unrouted = True

    def locator(self, selector: str) -> _YiyanDownloadLocatorList:
        if "下载图片" in selector:
            return _YiyanDownloadLocatorList([self.download])
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_timeout(self, _ms: float) -> None:
        time.sleep(0.001)

    def simulate_upload(self) -> None:
        assert self._route is not None
        pattern, handler = self._route
        sample = "https://aisearch.bj.bcebos.com/fileManager/K/share.png?uploads"
        assert pattern.search(sample)
        # initiate POST（空体）必须放行继续走真实 BOS
        handler(_YiyanDownloadRoute(self, "POST", sample, None))
        for part_no, body in self._parts.items():
            url = (
                "https://aisearch.bj.bcebos.com/fileManager/K/share.png"
                f"?partNumber={part_no}&uploadId=u1"
            )
            handler(_YiyanDownloadRoute(self, "PUT", url, body))


def test_yiyan_share_download_reassembles_bos_upload_parts(tmp_path: Path) -> None:
    head, tail = _ONE_PIXEL_PNG_HEADER[:12], _ONE_PIXEL_PNG_HEADER[12:]
    page = _YiyanDownloadPage({2: tail, 1: head})  # 乱序到达

    audit = official_share._download_yiyan_share_image(
        page, tmp_path / "share.png", timeout_ms=2_000, settle_ms=20
    )

    out = tmp_path / "share.png"
    assert out.read_bytes() == _ONE_PIXEL_PNG_HEADER
    assert valid_png(out) is True
    assert audit["image_source"] == "share_download_button"
    assert audit["upload_transport"] == "bos_upload_intercept"
    assert audit["part_count"] == 2
    assert audit["image_bytes"] == len(_ONE_PIXEL_PNG_HEADER)
    assert page.unrouted is True
    assert len(page.aborted) == 2
    assert all("partNumber=" in url for url in page.aborted)
    assert len(page.continued) == 1  # 仅 initiate POST 放行


def test_yiyan_share_download_fail_closed_without_payload(tmp_path: Path) -> None:
    page = _YiyanDownloadPage({})

    with pytest.raises(OfficialShareExportError, match="no upload payload"):
        official_share._download_yiyan_share_image(
            page, tmp_path / "share.png", timeout_ms=50, settle_ms=10
        )
    assert page.unrouted is True
    assert not (tmp_path / "share.png").exists()


def test_yiyan_share_download_rejects_non_contiguous_parts(tmp_path: Path) -> None:
    page = _YiyanDownloadPage({2: b"second", 3: b"third"})

    with pytest.raises(OfficialShareExportError, match="not contiguous"):
        official_share._download_yiyan_share_image(
            page, tmp_path / "share.png", timeout_ms=2_000, settle_ms=20
        )
    assert not (tmp_path / "share.png").exists()


def test_yiyan_share_download_rejects_non_png_payload(tmp_path: Path) -> None:
    page = _YiyanDownloadPage({1: b"definitely-not-a-png"})

    with pytest.raises(OfficialShareExportError, match="not a valid PNG"):
        official_share._download_yiyan_share_image(
            page, tmp_path / "share.png", timeout_ms=2_000, settle_ms=20
        )
    assert (tmp_path / "share.png").read_bytes() == b"definitely-not-a-png"  # 证据保留
