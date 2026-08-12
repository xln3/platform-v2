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
