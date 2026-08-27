#!/usr/bin/env python3
"""Synchronize one globally shared SiliconIndex snapshot for platform-v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.siliconindex import SiliconIndexSynchronizer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot-dir",
        default=os.environ.get("GEO_SILICONINDEX_SNAPSHOT_DIR", "data/siliconindex-snapshots"),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "GEO_SILICONINDEX_BASE_URL",
            "https://siliconindex-consumer.onrender.com/data/v1",
        ),
    )
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    synchronizer = SiliconIndexSynchronizer(args.snapshot_dir, args.base_url)
    result = synchronizer.status() if args.status else synchronizer.sync()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
