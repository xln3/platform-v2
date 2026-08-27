#!/usr/bin/env python3
"""Backup, verify, and safely restore immutable knowledge release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL = "geo-knowledge-release-backup-v1"
_STAMP = re.compile(r"^\d{8}T\d{6}Z$")


class KnowledgeBackupError(RuntimeError):
    pass


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _source_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise KnowledgeBackupError("knowledge_release_root_missing")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise KnowledgeBackupError("knowledge_release_symlink_forbidden")
        if path.is_file():
            files.append(path)
    if not files:
        raise KnowledgeBackupError("knowledge_release_root_empty")
    return files


def backup(source: Path, backup_root: Path) -> Path:
    source = source.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not _STAMP.fullmatch(stamp):
        raise AssertionError("invalid_generated_timestamp")
    destination = backup_root.resolve() / stamp
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    archive = destination / "knowledge-releases.tar.gz"
    entries: list[dict[str, Any]] = []
    files = _source_files(source)
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for path in files:
            relative = path.relative_to(source)
            bundle.add(path, arcname=relative.as_posix(), recursive=False)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _hash_file(path),
                }
            )
    manifest = {
        "tool": TOOL,
        "created_at": datetime.now(UTC).isoformat(),
        "archive": archive.name,
        "archive_sha256": _hash_file(archive),
        "file_count": len(entries),
        "files": entries,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(archive, 0o600)
    os.chmod(manifest_path, 0o600)
    verify(manifest_path)
    return manifest_path


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("tool") != TOOL:
        raise KnowledgeBackupError("backup_manifest_tool_invalid")
    archive = manifest_path.parent / str(manifest.get("archive") or "")
    if not archive.is_file() or _hash_file(archive) != manifest.get("archive_sha256"):
        raise KnowledgeBackupError("backup_archive_hash_mismatch")
    expected = {row["path"]: row for row in manifest.get("files") or []}
    observed: dict[str, dict[str, Any]] = {}
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            invalid_member = (
                not member.isfile()
                or member.name.startswith("/")
                or ".." in Path(member.name).parts
            )
            if invalid_member:
                raise KnowledgeBackupError("backup_archive_member_invalid")
            stream = bundle.extractfile(member)
            if stream is None:
                raise KnowledgeBackupError("backup_archive_member_unreadable")
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            observed[member.name] = {
                "bytes": size,
                "sha256": "sha256:" + digest.hexdigest(),
            }
    if set(observed) != set(expected):
        raise KnowledgeBackupError("backup_file_set_mismatch")
    for name, value in observed.items():
        if value["bytes"] != expected[name]["bytes"] or value["sha256"] != expected[name]["sha256"]:
            raise KnowledgeBackupError("backup_file_hash_mismatch")
    return {
        "status": "verified",
        "manifest": str(manifest_path),
        "archive_sha256": manifest["archive_sha256"],
        "file_count": len(observed),
    }


def restore(manifest_path: Path, target: Path) -> dict[str, Any]:
    verification = verify(manifest_path)
    if target.exists() and any(target.iterdir()):
        raise KnowledgeBackupError("restore_target_must_be_empty")
    target.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = manifest_path.parent / manifest["archive"]
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(target, filter="data")
    for row in manifest["files"]:
        restored = target / row["path"]
        if not restored.is_file() or _hash_file(restored) != row["sha256"]:
            raise KnowledgeBackupError("restored_file_hash_mismatch")
    return {
        **verification,
        "status": "restored_and_verified",
        "target": str(target),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--source", type=Path, required=True)
    backup_parser.add_argument("--backup-root", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--manifest", type=Path, required=True)
    restore_parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        manifest = backup(args.source, args.backup_root)
        result = verify(manifest)
    elif args.command == "verify":
        result = verify(args.manifest)
    else:
        result = restore(args.manifest, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
