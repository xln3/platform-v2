from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.knowledge_release_backup import (
    KnowledgeBackupError,
    backup,
    restore,
    verify,
)


def test_backup_verify_restore_and_nonempty_guard(tmp_path: Path) -> None:
    source = tmp_path / "source"
    release = source / "release-1"
    release.mkdir(parents=True)
    (source / "CURRENT").write_text("release-1\n", encoding="utf-8")
    (release / "manifest.json").write_text('{"release_id":"release-1"}\n', encoding="utf-8")
    (release / "domain.json").write_text('{"value":1}\n', encoding="utf-8")

    manifest = backup(source, tmp_path / "backups")
    verified = verify(manifest)
    assert verified["status"] == "verified"
    assert verified["file_count"] == 3

    target = tmp_path / "restored"
    restored = restore(manifest, target)
    assert restored["status"] == "restored_and_verified"
    assert (target / "CURRENT").read_text(encoding="utf-8") == "release-1\n"

    with pytest.raises(KnowledgeBackupError, match="restore_target_must_be_empty"):
        restore(manifest, target)


def test_tampered_archive_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "CURRENT").write_text("release-1\n", encoding="utf-8")
    manifest_path = backup(source, tmp_path / "backups")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = manifest_path.parent / manifest["archive"]
    with archive.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(KnowledgeBackupError, match="backup_archive_hash_mismatch"):
        verify(manifest_path)
