"""Consume one durable media-price refresh request outside the API cgroup.

The API only creates ``media-prices.refresh.request.json``.  A systemd path
unit starts this worker in its own resource-controlled service.  The request is
renamed before execution, so a host or service restart can resume an unclaimed
or interrupted request without keeping queue state in an API process.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
REQUEST_NAME = "media-prices.refresh.request.json"
RUNNING_REQUEST_NAME = "media-prices.refresh.request.running.json"
WORKER_MUTEX_NAME = "media-prices.refresh.worker.lock"
REFRESH_STATUS_NAME = "media-prices.refresh.json"
REFRESH_LOG_NAME = "media-prices.refresh.log"


def _configured_datasets_dir() -> Path:
    configured = os.environ.get("GEO_DATASETS_DIR", "")
    return Path(configured) if configured else ROOT / ".datasets"


def _configured_refresh_script() -> Path:
    configured = os.environ.get("MEDIA_PRICES_REFRESH_SCRIPT", "")
    return Path(configured) if configured else Path(__file__).with_name("media_prices_refresh.py")


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_launch_failure(base: Path, message: str) -> None:
    status_path = base / REFRESH_STATUS_NAME
    sources: dict[str, object] = {}
    started_at = _now()
    try:
        current = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if isinstance(current, dict):
        current_sources = current.get("sources")
        if isinstance(current_sources, dict):
            sources = current_sources
        current_started_at = current.get("started_at")
        if isinstance(current_started_at, str):
            started_at = current_started_at
    document = {
        "state": "failed",
        "started_at": started_at,
        "updated_at": _now(),
        "message": message[:200],
        "sources": sources,
    }
    _atomic_write(
        status_path,
        json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8"),
    )


@contextmanager
def _worker_mutex(base: Path) -> Iterator[BinaryIO | None]:
    handle = (base / WORKER_MUTEX_NAME).open("a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        yield handle
    finally:
        handle.close()


def _claim_request(base: Path) -> Path | None:
    queued = base / REQUEST_NAME
    running = base / RUNNING_REQUEST_NAME
    if running.is_file():
        return running
    try:
        os.replace(queued, running)
    except FileNotFoundError:
        return None
    _fsync_directory(base)
    return running


def run_once(*, datasets_dir: Path | None = None, refresh_script: Path | None = None) -> int:
    base = datasets_dir or _configured_datasets_dir()
    script = refresh_script or _configured_refresh_script()
    base.mkdir(parents=True, exist_ok=True)
    with _worker_mutex(base) as mutex:
        if mutex is None:
            return 0
        claim = _claim_request(base)
        if claim is None:
            return 0
        environment = os.environ.copy()
        environment["GEO_DATASETS_DIR"] = str(base)
        try:
            log_path = base / REFRESH_LOG_NAME
            with log_path.open("ab") as log:
                completed = subprocess.run(
                    [sys.executable, str(script)],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
        except OSError as exc:
            _write_launch_failure(base, f"refresh_worker_launch_failed: {exc}")
            return 1
        # The refresh script owns business-level done/failed status.  Reaching
        # this point means the durable request was consumed, even when a source
        # refresh itself reported a terminal failure.
        if completed.returncode in {0, 1, 2}:
            claim.unlink(missing_ok=True)
            _fsync_directory(base)
            return 0
        _write_launch_failure(
            base,
            f"refresh_worker_unexpected_exit: {completed.returncode}",
        )
        return completed.returncode


def main() -> int:
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
