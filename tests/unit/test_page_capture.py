from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from workflows.activities.page_capture import (
    FLATTEN_CHAT_SCROLLER_JS,
    RESTORE_PAGE_STYLES_JS,
    SNAPSHOT_PAGE_STYLES_JS,
    PageStyleRestoreError,
    ScopedChatCaptureError,
    capture_full_page_safely,
    capture_scoped_chat_tiles,
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


class _ScopedPage:
    def __init__(
        self,
        *,
        screenshot_error_at: int | None = None,
        drift_after_probe: int | None = None,
    ) -> None:
        self.scroll_top = 33.0
        self.screenshot_error_at = screenshot_error_at
        self.drift_after_probe = drift_after_probe
        self.probe_count = 0
        self.screenshot_count = 0
        self.calls: list[tuple[Any, ...]] = []

    def evaluate(self, script: str, arg: Any = None) -> object:
        self.calls.append(("evaluate", script, arg))
        if script == "probe":
            self.probe_count += 1
            if isinstance(arg, dict) and arg.get("scrollTop") is not None:
                self.scroll_top = float(arg["scrollTop"])
            answer_fingerprint = (
                "answer-drifted"
                if self.drift_after_probe is not None and self.probe_count >= self.drift_after_probe
                else "answer-stable"
            )
            return {
                "ok": True,
                "scroll_top": self.scroll_top,
                "scroll_height": 600,
                "max_scroll": 400,
                "capture_x": 10,
                "capture_y": 5,
                "capture_width": 100,
                "capture_top_inset": 20,
                "capture_height": 200,
                "blocks": [
                    {
                        "role": "question",
                        "top": 20,
                        "bottom": 70,
                        "left": 10,
                        "right": 110,
                        "fingerprint": "question-stable",
                    },
                    {
                        "role": "answer",
                        "top": 80,
                        "bottom": 500,
                        "left": 10,
                        "right": 110,
                        "fingerprint": answer_fingerprint,
                    },
                ],
            }
        if script == "restore":
            self.scroll_top = float(arg)
            return {"ok": True, "actual_scroll_top": self.scroll_top}
        raise AssertionError("unexpected script")

    def wait_for_timeout(self, value: int) -> None:
        self.calls.append(("wait", value))

    def screenshot(self, *, clip: dict[str, float], timeout: int) -> bytes:
        self.screenshot_count += 1
        self.calls.append(("screenshot", clip, timeout))
        if self.screenshot_error_at == self.screenshot_count:
            raise RuntimeError("tile capture failed")
        image = Image.new(
            "RGB",
            (round(clip["width"]), round(clip["height"])),
            (self.screenshot_count, 2, 3),
        )
        try:
            payload = io.BytesIO()
            image.save(payload, format="PNG")
            return payload.getvalue()
        finally:
            image.close()


def test_scoped_capture_tiles_only_safe_band_and_restores_scroll(tmp_path: Path) -> None:
    page = _ScopedPage()
    path = tmp_path / "scoped.png"

    result = capture_scoped_chat_tiles(
        page,
        path,
        probe_script="probe",
        restore_script="restore",
        expected_question="current question",
        method="semantic-test",
    )

    assert result == {
        "method": "semantic-test",
        "tile_count": 4,
        "block_count": 2,
        "restored_scroll_top": 33.0,
    }
    assert page.scroll_top == 33.0
    with Image.open(path) as image:
        assert image.size == (100, 470)
    clips = [call[1] for call in page.calls if call[0] == "screenshot"]
    assert clips
    assert all(clip["y"] == 25 for clip in clips)
    assert all(clip["height"] == 180 for clip in clips)


def test_scoped_capture_screenshot_failure_still_restores_scroll(tmp_path: Path) -> None:
    page = _ScopedPage(screenshot_error_at=2)

    with pytest.raises(RuntimeError, match="tile capture failed"):
        capture_scoped_chat_tiles(
            page,
            tmp_path / "broken.png",
            probe_script="probe",
            restore_script="restore",
            expected_question="current question",
        )

    assert page.scroll_top == 33.0
    assert ("evaluate", "restore", 33.0) in page.calls


def test_scoped_capture_fails_closed_when_answer_text_changes(tmp_path: Path) -> None:
    page = _ScopedPage(drift_after_probe=2)

    with pytest.raises(ScopedChatCaptureError, match="answer text changed"):
        capture_scoped_chat_tiles(
            page,
            tmp_path / "drift.png",
            probe_script="probe",
            restore_script="restore",
            expected_question="current question",
        )

    assert page.screenshot_count == 0
    assert page.scroll_top == 33.0
