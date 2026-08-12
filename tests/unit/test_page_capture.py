from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from workflows.activities.page_capture import (
    FLATTEN_CHAT_SCROLLER_JS,
    RESTORE_PAGE_STYLES_JS,
    SNAPSHOT_PAGE_STYLES_JS,
    PageStyleRestoreError,
    capture_full_page_safely,
)


class _Page:
    def __init__(
        self,
        *,
        snapshot: object = "snapshot-token",
        screenshot_error: BaseException | None = None,
        restored: object = True,
    ) -> None:
        self.snapshot = snapshot
        self.screenshot_error = screenshot_error
        self.restored = restored
        self.calls: list[tuple[Any, ...]] = []

    def evaluate(self, script: str, *args: Any) -> object:
        self.calls.append(("evaluate", script, *args))
        if script == SNAPSHOT_PAGE_STYLES_JS:
            if isinstance(self.snapshot, BaseException):
                raise self.snapshot
            return self.snapshot
        if script == FLATTEN_CHAT_SCROLLER_JS:
            return {}
        if script == RESTORE_PAGE_STYLES_JS:
            return self.restored
        raise AssertionError("unexpected script")

    def wait_for_timeout(self, value: int) -> None:
        self.calls.append(("wait", value))

    def screenshot(self, *, path: str, full_page: bool) -> None:
        self.calls.append(("screenshot", path, full_page))
        if self.screenshot_error is not None:
            raise self.screenshot_error
        Path(path).write_bytes(b"png")

    def reload(self, **kwargs: Any) -> None:
        self.calls.append(("reload", kwargs))


def test_snapshot_failure_never_runs_mutating_flatten(tmp_path: Path) -> None:
    page = _Page(snapshot=RuntimeError("snapshot failed"))

    result = capture_full_page_safely(page, tmp_path / "fallback.png")

    assert result["method"] == "playwright_full_page_no_mutation"
    assert not any(call[:2] == ("evaluate", FLATTEN_CHAT_SCROLLER_JS) for call in page.calls)
    assert (tmp_path / "fallback.png").is_file()


def test_screenshot_exception_still_restores_styles(tmp_path: Path) -> None:
    page = _Page(screenshot_error=RuntimeError("capture failed"))

    with pytest.raises(RuntimeError, match="capture failed"):
        capture_full_page_safely(page, tmp_path / "broken.png")

    assert ("evaluate", RESTORE_PAGE_STYLES_JS, "snapshot-token") in page.calls


def test_unconfirmed_restore_reloads_and_fails(tmp_path: Path) -> None:
    page = _Page(restored=False)

    with pytest.raises(PageStyleRestoreError, match="could not be restored"):
        capture_full_page_safely(page, tmp_path / "captured.png")

    assert any(call[0] == "reload" for call in page.calls)
