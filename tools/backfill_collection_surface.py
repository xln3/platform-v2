#!/usr/bin/env python3
"""Dry-run or explicitly apply the historical collection surface backfill."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.collection.legacy_surface_backfill import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    BackfillConnection,
    BackfillRequest,
    SurfaceBackfillError,
    run_collection_surface_backfill,
)
from geo_platform.config import get_settings  # noqa: E402

_SAFE_TENANT_PUB_ID = re.compile(r"^[A-Za-z0-9._:@/-]{1,30}$")


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic historical consumer_web assignment; defaults to read-only dry-run"
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="inspect only (default)")
    mode.add_argument("--apply", action="store_true", help="atomically write after confirmation")
    parser.add_argument("--tenant-pub-id", required=True, help="single tenant public identifier")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"1..{MAX_BATCH_SIZE}"
    )
    parser.add_argument("--selection-hash", help="exact hash emitted by the locked dry-run")
    parser.add_argument("--confirm-token", help="exact confirmation token emitted by dry-run")
    parser.add_argument("--requested-by-pub-id", help="non-sensitive operator public identifier")
    parser.add_argument("--batch-key", help="stable non-sensitive apply batch identifier")
    parser.add_argument("--output-json", action="store_true", help="emit compact JSON")
    arguments = parser.parse_args(argv)
    if not _SAFE_TENANT_PUB_ID.fullmatch(arguments.tenant_pub_id):
        parser.error("--tenant-pub-id must be a safe public identifier of at most 30 characters")
    apply_fields = (
        arguments.selection_hash,
        arguments.confirm_token,
        arguments.requested_by_pub_id,
        arguments.batch_key,
    )
    if arguments.apply and not all(apply_fields):
        parser.error(
            "--apply requires --selection-hash, --confirm-token, --requested-by-pub-id, "
            "and --batch-key"
        )
    if not arguments.apply and any(apply_fields):
        parser.error("apply confirmation arguments require --apply")
    if not 1 <= arguments.batch_size <= MAX_BATCH_SIZE:
        parser.error(f"--batch-size must be between 1 and {MAX_BATCH_SIZE}")
    return arguments


def _dsn() -> str:
    settings = get_settings()
    return settings.worker_postgres_dsn or settings.runtime_postgres_dsn or settings.postgres_dsn


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    request = BackfillRequest(
        tenant_pub_id=arguments.tenant_pub_id,
        apply=arguments.apply,
        batch_size=arguments.batch_size,
        expected_selection_hash=arguments.selection_hash,
        confirm_token=arguments.confirm_token,
        requested_by_pub_id=arguments.requested_by_pub_id,
        batch_key=arguments.batch_key,
    )
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            _psycopg_dsn(_dsn()),
            autocommit=False,
            row_factory=dict_row,
        ) as connection:
            result = run_collection_surface_backfill(cast(BackfillConnection, connection), request)
    except SurfaceBackfillError as exc:
        print(json.dumps(exc.as_dict(), sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2
    rendered = (
        json.dumps(result, sort_keys=True, separators=(",", ":"))
        if arguments.output_json
        else json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    )
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
