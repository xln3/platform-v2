from __future__ import annotations

import hashlib
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from domain.brandrank.entities import load_entity_master
from domain.siliconindex import SiliconIndexSyncError, SiliconIndexSynchronizer
from domain.siliconindex.snapshot import CORE_FILES, validate_snapshot


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _publish_fixture(root: Path, release_id: str, *, aliases: list[str]) -> Path:
    data_dir = root / "data" / "v1"
    datasets: dict[str, Any] = {
        "brands": [
            {
                "brand_id": "CYB-BR-TENCENT",
                "canonical_name": "腾讯",
                "status": "active",
                "review_status": "reviewed",
                "entity_type": "company",
                "brand_level": "brand_family",
                "category_ids": ["CYB-CAT-CYBERSECURITY"],
                "primary_category_id": "CYB-CAT-CYBERSECURITY",
                "compliance_rule_ids": [],
                "comparison_profiles": [
                    {
                        "domain": "cybersecurity",
                        "industry_fit": "adjacent_platform_security",
                        "competitor_scopes": ["cloud_security"],
                        "competitor_eligible": True,
                        "evidence_urls": ["https://www.tencent.com/"],
                    }
                ],
            }
        ],
        "mentions": [
            {
                "mention_id": f"CYB-MEN-TENCENT-{index:03d}",
                "brand_id": "CYB-BR-TENCENT",
                "text": alias,
                "mention_type": "product_line",
                "relationship_to_brand": "business_unit_of",
                "match_mode": "exact",
                "status": "reviewed",
            }
            for index, alias in enumerate(aliases, start=1)
        ],
        "categories": [{"category_id": "CYB-CAT-CYBERSECURITY"}],
        "cognition-profiles": [],
        "compliance-rules": [],
        "competitor-relations": [],
        "query-templates": [],
        "search-index": [],
        "graph": {},
    }
    for name, value in datasets.items():
        _write_json(data_dir / f"{name}.json", value)

    digest = hashlib.sha256()
    for name in CORE_FILES:
        digest.update((data_dir / f"{name}.json").read_bytes())
    endpoints = {name.replace("-", "_"): f"/data/v1/{name}.json" for name in datasets}
    _write_json(
        data_dir / "manifest.json",
        {
            "release_id": release_id,
            "data_version": release_id,
            "schema_version": "1.1.0",
            "content_hash": f"sha256:{digest.hexdigest()}",
            "endpoints": endpoints,
        },
    )
    return data_dir


def test_sync_promotes_one_global_release_and_cache_tracks_current(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    snapshot_root = tmp_path / "snapshots"
    _publish_fixture(remote, "2026-08-26.1", aliases=["腾讯云"])
    handler = partial(_QuietHandler, directory=str(remote))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/data/v1"

    try:
        synchronizer = SiliconIndexSynchronizer(snapshot_root, base_url)
        first = synchronizer.sync()
        assert first["status"] == "success"
        assert (snapshot_root / "CURRENT").read_text(encoding="utf-8") == "2026-08-26.1"

        master_v1 = load_entity_master("cybersecurity", str(snapshot_root))
        assert master_v1.source_mode == "synced_siliconindex_snapshot"
        assert master_v1.source_release_id == "2026-08-26.1"
        assert master_v1.alias_index["腾讯云"].entity_id == "CYB-BR-TENCENT"

        _publish_fixture(remote, "2026-08-26.2", aliases=["腾讯云", "腾讯安全"])
        second = synchronizer.sync()
        assert second["previous"] == "2026-08-26.1"

        # Same process and same configured directory: changing CURRENT must
        # invalidate the read cache without restarting the API.
        master_v2 = load_entity_master("cybersecurity", str(snapshot_root))
        assert master_v2.source_release_id == "2026-08-26.2"
        assert master_v2.alias_index["腾讯安全"].entity_id == "CYB-BR-TENCENT"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_refresh_keeps_previous_valid_release_and_recovers(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    snapshot_root = tmp_path / "snapshots"
    data_dir = _publish_fixture(remote, "2026-08-26.1", aliases=["腾讯云"])
    handler = partial(_QuietHandler, directory=str(remote))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        synchronizer = SiliconIndexSynchronizer(
            snapshot_root,
            f"http://127.0.0.1:{server.server_port}/data/v1",
        )
        synchronizer.sync()
        manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["release_id"] = "2026-08-26.2"
        manifest["data_version"] = "2026-08-26.2"
        manifest["content_hash"] = "sha256:" + "0" * 64
        _write_json(data_dir / "manifest.json", manifest)

        with pytest.raises(SiliconIndexSyncError, match="content_hash_mismatch"):
            synchronizer.sync()

        assert synchronizer.current_release() == "2026-08-26.1"
        assert synchronizer.status()["status"] == "failed"
        assert synchronizer.status()["current"] == "2026-08-26.1"

        # One failed attempt per simulated outage day must never disturb LKG.
        for _ in range(7):
            with pytest.raises(SiliconIndexSyncError, match="content_hash_mismatch"):
                synchronizer.sync()
            assert synchronizer.current_release() == "2026-08-26.1"

        _publish_fixture(remote, "2026-08-25.9", aliases=["腾讯云"])
        with pytest.raises(SiliconIndexSyncError, match="release_rollback_rejected"):
            synchronizer.sync()
        assert synchronizer.current_release() == "2026-08-26.1"

        _publish_fixture(remote, "2026-08-26.2", aliases=["腾讯云", "腾讯安全"])
        recovered = synchronizer.sync()
        assert recovered["status"] == "success"
        assert recovered["previous"] == "2026-08-26.1"
        assert synchronizer.current_release() == "2026-08-26.2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_validation_rejects_duplicate_ids_and_broken_references(tmp_path: Path) -> None:
    duplicate = _publish_fixture(tmp_path / "duplicate", "2026-08-26.1", aliases=["腾讯云"])
    mentions = json.loads((duplicate / "mentions.json").read_text(encoding="utf-8"))
    mentions.append(dict(mentions[0]))
    _write_json(duplicate / "mentions.json", mentions)
    with pytest.raises(SiliconIndexSyncError, match="duplicate_mention_id"):
        validate_snapshot(duplicate)

    broken = _publish_fixture(tmp_path / "broken", "2026-08-26.1", aliases=["腾讯云"])
    brands = json.loads((broken / "brands.json").read_text(encoding="utf-8"))
    brands[0]["category_ids"] = ["CYB-CAT-MISSING"]
    _write_json(broken / "brands.json", brands)
    with pytest.raises(SiliconIndexSyncError, match="invalid_category_ref"):
        validate_snapshot(broken)
