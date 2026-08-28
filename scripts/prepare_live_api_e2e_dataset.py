from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

PLATFORMS = ("prfabu", "toumeiw", "mtpfw", "meititejia", "meijiehezi", "pinda")
DEFAULT_ROWS = 20_001


def build_dataset(row_count: int) -> dict[str, Any]:
    if not 1 <= row_count <= 200_000:
        raise ValueError("row_count must be between 1 and 200000")
    rows = [
        {
            # Five hexadecimal characters stay unique up to the 200k row cap
            # without looking like a standalone six-digit OTP to the browser DLP gate.
            "name": f"CI媒体-{index:05x}",
            "prices": {"prfabu": 100 + index % 17},
            "best": 100 + index % 17,
            "best_plat": "prfabu",
            "spread": None,
            "n_src": 1,
            "geo": ["b"],
            "geo_n": 1,
            "portal": "CI 合同样本",
            "channel": "新闻",
            "include": "收录",
        }
        for index in range(row_count)
    ]
    zero_counts = {platform: 0 for platform in PLATFORMS}
    return {
        "generated_at": "2026-08-27 00:00",
        "sources": {platform: f"CI {platform}" for platform in PLATFORMS},
        "partial": {platform: False for platform in PLATFORMS},
        "stats": {
            "counts": {**zero_counts, "prfabu": row_count},
            "geo_counts": {**zero_counts, "prfabu": row_count},
            "unique_media": row_count,
            "matched_2plus": 0,
            "matched_3": 0,
            "geo_union": row_count,
            "geo_multi_src": 0,
        },
        "rows": rows,
    }


def write_dataset(output_dir: Path, row_count: int = DEFAULT_ROWS) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        build_dataset(row_count), ensure_ascii=False, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    dataset_path = output_dir / "media-prices.json"
    dataset_path.write_bytes(payload)
    (output_dir / "media-prices.sha256").write_text(
        f"{digest}  media-prices.json\n", encoding="utf-8"
    )
    return dataset_path, digest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a deterministic large dataset for the live-API browser lane."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    arguments = parser.parse_args()
    dataset_path, digest = write_dataset(arguments.output_dir, arguments.rows)
    print(
        json.dumps(
            {"dataset": str(dataset_path), "rows": arguments.rows, "sha256": digest},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
