"""Create and verify a consistent read-only SQLite migration snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def backup(source: Path, target: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {target}")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
            integrity = target_db.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise RuntimeError("SQLite backup integrity check failed")
    os.chmod(target, 0o400)
    metadata = target.stat()
    return {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "source_path_digest": hashlib.sha256(str(source).encode()).hexdigest(),
        "backup_path_digest": hashlib.sha256(str(target.resolve()).encode()).hexdigest(),
        "backup_sha256": sha256_file(target),
        "size_bytes": metadata.st_size,
        "mode": oct(metadata.st_mode & 0o777),
        "sqlite_integrity": "ok",
        "source_modified_at": datetime.fromtimestamp(source.stat().st_mtime, UTC).isoformat(),
        "contains_secret_values": False,
        "raw_paths_included": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = backup(args.source, args.target)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
