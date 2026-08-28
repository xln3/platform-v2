from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.prepare_live_api_e2e_dataset import PLATFORMS, build_dataset, write_dataset


def test_build_dataset_is_self_consistent() -> None:
    dataset = build_dataset(4)

    assert dataset["stats"] == {
        "counts": {platform: 4 if platform == "prfabu" else 0 for platform in PLATFORMS},
        "geo_counts": {platform: 4 if platform == "prfabu" else 0 for platform in PLATFORMS},
        "unique_media": 4,
        "matched_2plus": 0,
        "matched_3": 0,
        "geo_union": 4,
        "geo_multi_src": 0,
    }
    assert len({row["name"] for row in dataset["rows"]}) == 4
    assert all(row["best"] == min(row["prices"].values()) for row in dataset["rows"])
    assert all(re.search(r"(?<!\w)\d{6}(?!\w)", row["name"]) is None for row in dataset["rows"])


def test_write_dataset_publishes_matching_digest(tmp_path: Path) -> None:
    dataset_path, digest = write_dataset(tmp_path, 3)

    payload = dataset_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == digest
    assert json.loads(payload)["stats"]["unique_media"] == 3
    assert (tmp_path / "media-prices.sha256").read_text(encoding="utf-8") == (
        f"{digest}  media-prices.json\n"
    )
