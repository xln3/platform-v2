#!/usr/bin/env python3
"""Daily production backup: PostgreSQL custom dump + ClickHouse table exports + MinIO volume tar.

Design choices (see deploy/production/):
- PostgreSQL: `docker exec <pg> pg_dump --format=custom` streams to stdout. The container's
  local socket is trust-authenticated, so no credential ever touches argv, env, or disk.
- ClickHouse: `BACKUP TO Disk` needs a pre-configured backup disk that the stock image lacks,
  so every table in geo_analytics is exported as self-describing Parquet via clickhouse-client
  inside the container (password stays in the container's own env, expanded by its shell).
  Restore path: `INSERT INTO <t> SELECT * FROM file('x.parquet')`.
- MinIO: the evidence bucket is immutable CAS objects, so a plain `tar czf` of the /data
  volume (read via --volumes-from with GNU tar from the PostgreSQL image — the MinIO
  image ships no tar) is sufficient; no S3 credentials needed.
- Any component failure is logged and makes the overall exit code non-zero (fail-loud);
  the remaining components still run so one failure never leaves everything unbacked.
- Retention keeps the newest N (default 14) snapshot directories created by this script
  (manifest.json marker match); older script-created dirs are listed-then-deleted with logs.
  Pre-existing manual backup dirs without the marker are never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = PLATFORM_ROOT / ".production-backups"
DEFAULT_KEEP = 14
TOOL_MARKER = "production_backup.py"

POSTGRES_CONTAINER = "geo-platform-v2-production-postgres-1"
POSTGRES_DB = "geo_platform"
POSTGRES_USER = "geo"
CLICKHOUSE_CONTAINER = "geo-platform-v2-production-clickhouse-1"
CLICKHOUSE_DB = "geo_analytics"
MINIO_CONTAINER = "geo-platform-v2-production-minio-1"
MINIO_DATA_DIR = "/data"
# The MinIO image ships no tar; borrow GNU tar from an image that is always present
# (the production PostgreSQL image) and read the data dir via --volumes-from.
TAR_IMAGE = "pgvector/pgvector:pg16"

_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class BackupError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)


def log_err(message: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"{stamp} ERROR {message}", file=sys.stderr, flush=True)


def stream_to_file(cmd: list[str], dest: Path) -> tuple[str, int]:
    """Run cmd, streaming stdout into dest while hashing; raise on non-zero exit."""
    log(f"run: {' '.join(cmd[:3])} ... -> {dest.name}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    hasher = hashlib.sha256()
    size = 0
    assert proc.stdout is not None
    with dest.open("wb") as fh:
        while chunk := proc.stdout.read(4 * 1024 * 1024):
            fh.write(chunk)
            hasher.update(chunk)
            size += len(chunk)
    stderr = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
    if proc.wait() != 0:
        dest.unlink(missing_ok=True)
        raise BackupError(f"{cmd[0]} {cmd[1]} exited {proc.returncode}: {stderr[:500]}")
    if stderr:
        log(f"stderr: {stderr[:300]}")
    return hasher.hexdigest(), size


def capture(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:500]
        raise BackupError(f"{cmd[0]} {cmd[1]} exited {proc.returncode}: {detail}")
    return proc.stdout.strip()


def backup_postgres(dest_dir: Path, artifacts: list[dict]) -> None:
    dest = dest_dir / "postgres.dump"
    sha256, size = stream_to_file(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "pg_dump",
            "--format=custom",
            f"--username={POSTGRES_USER}",
            f"--dbname={POSTGRES_DB}",
        ],
        dest,
    )
    artifacts.append(
        {
            "file": dest.name,
            "component": "postgres",
            "bytes": size,
            "sha256": sha256,
            "detail": f"pg_dump custom format of {POSTGRES_DB}",
        }
    )


_CH_CLIENT_SH = (
    'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "$1"'
)


def clickhouse_client(query: str) -> list[str]:
    # Password stays in the container's own environment; the container shell expands it.
    return [
        "docker",
        "exec",
        CLICKHOUSE_CONTAINER,
        "sh",
        "-c",
        _CH_CLIENT_SH,
        "sh",
        query,
    ]


def backup_clickhouse(dest_dir: Path, artifacts: list[dict]) -> None:
    raw = capture(clickhouse_client(f"SHOW TABLES FROM {CLICKHOUSE_DB}"))
    tables = [line.strip() for line in raw.splitlines() if line.strip()]
    if not tables:
        raise BackupError(f"no tables found in ClickHouse database {CLICKHOUSE_DB}")
    for table in tables:
        if not _TABLE_NAME_RE.match(table):
            raise BackupError(f"unexpected ClickHouse table name: {table!r}")
    for table in tables:
        dest = dest_dir / f"clickhouse-{CLICKHOUSE_DB}-{table}.parquet"
        sha256, size = stream_to_file(
            clickhouse_client(f"SELECT * FROM {CLICKHOUSE_DB}.{table} FORMAT Parquet"),
            dest,
        )
        rows = capture(clickhouse_client(f"SELECT count() FROM {CLICKHOUSE_DB}.{table}"))
        artifacts.append(
            {
                "file": dest.name,
                "component": "clickhouse",
                "bytes": size,
                "sha256": sha256,
                "detail": f"{CLICKHOUSE_DB}.{table} rows={rows} (Parquet)",
            }
        )


def backup_minio(dest_dir: Path, artifacts: list[dict]) -> None:
    dest = dest_dir / "minio-data.tar.gz"
    sha256, size = stream_to_file(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "tar",
            f"--volumes-from={MINIO_CONTAINER}",
            TAR_IMAGE,
            "czf",
            "-",
            "-C",
            MINIO_DATA_DIR,
            ".",
        ],
        dest,
    )
    artifacts.append(
        {
            "file": dest.name,
            "component": "minio",
            "bytes": size,
            "sha256": sha256,
            "detail": f"tar.gz of MinIO {MINIO_DATA_DIR} volume (immutable CAS evidence)",
        }
    )


def apply_retention(backup_root: Path, keep: int) -> None:
    snapshots: list[Path] = []
    for entry in backup_root.iterdir():
        if not entry.is_dir() or not re.fullmatch(r"\d{8}T\d{6}Z", entry.name):
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            marker = json.loads(manifest_path.read_text(encoding="utf-8")).get("tool")
        except (OSError, json.JSONDecodeError):
            continue
        if marker == TOOL_MARKER:
            snapshots.append(entry)
    snapshots.sort(key=lambda p: p.name, reverse=True)
    for stale in snapshots[keep:]:
        log(f"retention: removing {stale.name} (keeping newest {keep})")
        shutil.rmtree(stale)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="snapshots to retain")
    args = parser.parse_args()

    os.umask(0o077)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = args.backup_root / stamp
    dest_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    log(f"backup destination: {dest_dir}")

    artifacts: list[dict] = []
    failures: list[str] = []
    components = {
        "postgres": backup_postgres,
        "clickhouse": backup_clickhouse,
        "minio": backup_minio,
    }
    status: dict[str, str] = {}
    for name, fn in components.items():
        try:
            fn(dest_dir, artifacts)
            status[name] = "ok"
            log(f"{name}: ok")
        except (BackupError, OSError) as exc:
            status[name] = "failed"
            failures.append(f"{name}: {exc}")
            log_err(f"{name}: FAILED: {exc}")

    manifest = {
        "tool": TOOL_MARKER,
        "started_at": stamp,
        "finished_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "hostname": os.uname().nodename,
        "status": status,
        "artifacts": artifacts,
    }
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    apply_retention(args.backup_root, args.keep)

    if failures:
        log_err(f"backup finished with {len(failures)} failed component(s): {'; '.join(failures)}")
        return 1
    total = sum(item["bytes"] for item in artifacts)
    log(f"backup complete: {len(artifacts)} artifact(s), {total} bytes -> {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
