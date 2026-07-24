"""Build a secret-free inventory of legacy browser/session artifacts.

The report deliberately omits raw paths, file names and content. It is safe to
attach to migration evidence while the underlying files remain quarantined.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".mypy_cache"})


@dataclass(frozen=True)
class InventoryEntry:
    path_digest: str
    kind: str
    size_bytes: int
    modified_at: str
    mode: str
    owner_uid: int
    content_sha256: str
    owner_resolution: str = "unresolved"
    platform_resolution: str = "unresolved"
    migration_disposition: str = "quarantine_pending_review"


def classify(path: Path) -> str | None:
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    if name.endswith(".har"):
        return "har"
    if "storage_state" in name or "storage-state" in name:
        return "storage_state"
    if "cookie" in name:
        return "cookie_material"
    if "user_data_dir" in parts or "browser_profile" in parts:
        return "browser_profile_file"
    if ".bak" in name:
        return "backup"
    if name.endswith((".db", ".sqlite", ".sqlite3")):
        return "legacy_database"
    if any(token in name for token in ("profile", "session")):
        return "session_named_file"
    return None


def _files(root: Path) -> Iterator[Path]:
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in _SKIP_DIRS]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            if path.is_symlink() or classify(path) is None:
                continue
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def build_inventory(root: Path) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    entries: list[InventoryEntry] = []
    for path in _files(resolved_root):
        metadata = path.stat()
        relative = path.relative_to(resolved_root).as_posix()
        entries.append(
            InventoryEntry(
                path_digest=hashlib.sha256(relative.encode()).hexdigest(),
                kind=classify(path) or "unknown",
                size_bytes=metadata.st_size,
                modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
                mode=stat.filemode(metadata.st_mode),
                owner_uid=metadata.st_uid,
                content_sha256=_sha256(path),
            )
        )
    entries.sort(key=lambda item: item.path_digest)
    kinds = Counter(entry.kind for entry in entries)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "root_digest": hashlib.sha256(str(resolved_root).encode()).hexdigest(),
        "secret_values_included": False,
        "raw_paths_included": False,
        "entries_total": len(entries),
        "bytes_total": sum(entry.size_bytes for entry in entries),
        "kinds": dict(sorted(kinds.items())),
        "entries": [asdict(entry) for entry in entries],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
