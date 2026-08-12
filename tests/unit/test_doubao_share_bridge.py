from __future__ import annotations

from pathlib import Path

import pytest

from workflows.activities import doubao_share_bridge


def test_configured_share_exporter_path_survives_detached_release_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = tmp_path / "share_export.py"
    exporter.write_text(
        "def capture_share_image(page, out_path, timeout_s):\n"
        "    return {'ok': True, 'timeout_s': timeout_s}\n"
        "def capture_share_link(page, timeout_s):\n"
        "    return {'ok': True, 'url': 'https://www.doubao.com/thread/abc', "
        "'timeout_s': timeout_s}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(doubao_share_bridge.ENV_SHARE_EXPORTER_PATH, str(exporter))
    doubao_share_bridge._share_export_module.cache_clear()

    assert doubao_share_bridge.capture_share_image(object(), tmp_path / "share.png") == {
        "ok": True,
        "timeout_s": 45.0,
        "attempts": 1,
    }
    assert doubao_share_bridge.capture_share_link(object()) == {
        "ok": True,
        "url": "https://www.doubao.com/thread/abc",
        "timeout_s": 25.0,
    }

    doubao_share_bridge._share_export_module.cache_clear()


def test_configured_share_exporter_path_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(doubao_share_bridge.ENV_SHARE_EXPORTER_PATH, "share_export.py")

    with pytest.raises(RuntimeError, match="must be an absolute path"):
        doubao_share_bridge._share_export_path()


def test_share_image_retries_one_slow_initial_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []

    class _Exporter:
        @staticmethod
        def capture_share_image(_page: object, _out_path: Path, *, timeout_s: float) -> dict:
            calls.append(timeout_s)
            if len(calls) == 1:
                return {
                    "ok": False,
                    "error": "no_share_image_captured",
                    "channel": None,
                    "timings_ms": {"total": 45_000},
                }
            return {"ok": True, "channel": "download", "path": str(_out_path)}

    monkeypatch.setattr(doubao_share_bridge, "_share_export_module", lambda: _Exporter())

    result = doubao_share_bridge.capture_share_image(object(), tmp_path / "share.png")

    assert calls == [45.0, 60.0]
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["first_attempt"] == {
        "error": "no_share_image_captured",
        "channel": None,
        "timings_ms": {"total": 45_000},
    }


def test_share_image_does_not_repeat_a_successful_first_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []

    class _Exporter:
        @staticmethod
        def capture_share_image(_page: object, _out_path: Path, *, timeout_s: float) -> dict:
            calls.append(timeout_s)
            return {"ok": True, "channel": "download", "path": str(_out_path)}

    monkeypatch.setattr(doubao_share_bridge, "_share_export_module", lambda: _Exporter())

    result = doubao_share_bridge.capture_share_image(object(), tmp_path / "share.png")

    assert calls == [45.0]
    assert result["attempts"] == 1
