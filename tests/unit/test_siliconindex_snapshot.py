from __future__ import annotations

import hashlib
import json
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from domain.brandrank.entities import load_entity_master
from domain.knowledge_evolution.release import KnowledgeReleaseStore
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
        simulated_now = [1_787_764_800.0]
        synchronizer = SiliconIndexSynchronizer(
            snapshot_root,
            f"http://127.0.0.1:{server.server_port}/data/v1",
            clock=lambda: simulated_now[0],
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
        local_store = KnowledgeReleaseStore(tmp_path / "local-knowledge")
        local_store.publish(
            release_id="local-before-outage",
            schema_version="1",
            documents={"source/type-fixture": {"entries": []}},
            parent_release_id=None,
            quality_report={},
            activate=True,
        )
        for day in range(1, 8):
            simulated_now[0] += 86_400
            with pytest.raises(SiliconIndexSyncError, match="content_hash_mismatch"):
                synchronizer.sync()
            assert synchronizer.current_release() == "2026-08-26.1"
            if day == 4:
                local_store.publish(
                    release_id="local-during-outage",
                    schema_version="1",
                    documents={"source/type-fixture": {"entries": [{"key": "forum"}]}},
                    parent_release_id="local-before-outage",
                    quality_report={"offline_review_and_publish": "passed"},
                    activate=True,
                )
                assert local_store.current_release_id() == "local-during-outage"

        history = [
            json.loads(line)
            for line in (snapshot_root / "sync-history.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        failed_days = [row for row in history if row["status"] == "failed"][-7:]
        assert len(failed_days) == 7
        assert failed_days[-1]["started_at"] - failed_days[0]["started_at"] == 6 * 86_400
        assert all(row["record_hash"].startswith("sha256:") for row in failed_days)
        assert all(
            row["previous_record_hash"] == history[index - 1]["record_hash"]
            for index, row in enumerate(history)
            if index > 0
        )
        assert history[0]["previous_record_hash"] is None

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


def test_validation_executes_downloaded_official_schema_bundle(tmp_path: Path) -> None:
    data_dir = _publish_fixture(tmp_path / "schema", "2026-08-26.1", aliases=["腾讯云"])
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = "1.2.0"
    _write_json(data_dir / "manifest.json", manifest)
    schema_dir = data_dir / "schemas" / "v1"
    schema_names = (
        "brand",
        "mention",
        "category",
        "cognition-profile",
        "compliance-rule",
        "competitor-relation",
        "query-template",
    )
    for name in schema_names:
        _write_json(
            schema_dir / f"{name}.schema.json",
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
        )
    assert validate_snapshot(data_dir)["schema_version"] == "1.2.0"

    _write_json(
        schema_dir / "brand.schema.json",
        {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["publisher_schema_marker"],
        },
    )
    with pytest.raises(SiliconIndexSyncError, match="official_schema_validation_failed:brands"):
        validate_snapshot(data_dir)


def test_same_release_sync_adds_shared_schema_validation_without_mutating_release(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote"
    data_dir = _publish_fixture(remote, "2026-08-26.1", aliases=["腾讯云"])
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = "1.2.0"
    manifest["endpoints"]["schemas"] = "/schemas/v1"
    _write_json(data_dir / "manifest.json", manifest)
    for name in (
        "brand",
        "mention",
        "category",
        "cognition-profile",
        "compliance-rule",
        "competitor-relation",
        "query-template",
    ):
        _write_json(
            remote / "schemas" / "v1" / f"{name}.schema.json",
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
        )

    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    # This represents a previously downloaded immutable 1.2 release from the
    # older synchronizer, before it retained the publisher schema bundle.
    shutil.copytree(data_dir, snapshot_root / "2026-08-26.1")
    (snapshot_root / "CURRENT").write_text("2026-08-26.1", encoding="utf-8")
    release_files_before = sorted(
        path.relative_to(snapshot_root / "2026-08-26.1")
        for path in (snapshot_root / "2026-08-26.1").rglob("*")
        if path.is_file()
    )

    handler = partial(_QuietHandler, directory=str(remote))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = SiliconIndexSynchronizer(
            snapshot_root,
            f"http://127.0.0.1:{server.server_port}/data/v1",
        ).sync()
        assert result["status"] == "success"
        assert validate_snapshot(snapshot_root / "2026-08-26.1")["release_id"] == ("2026-08-26.1")
        release_files_after = sorted(
            path.relative_to(snapshot_root / "2026-08-26.1")
            for path in (snapshot_root / "2026-08-26.1").rglob("*")
            if path.is_file()
        )
        assert release_files_after == release_files_before
        assert (snapshot_root / "schema-bundles" / "1.2.0" / "CURRENT").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_validation_rejects_safe_but_unorderable_release_id(tmp_path: Path) -> None:
    data_dir = _publish_fixture(tmp_path / "unorderable", "2026-08-26.1", aliases=["腾讯云"])
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["release_id"] = "rollback-x"
    manifest["data_version"] = "rollback-x"
    _write_json(data_dir / "manifest.json", manifest)
    with pytest.raises(SiliconIndexSyncError, match="unorderable_release_id"):
        validate_snapshot(data_dir)
