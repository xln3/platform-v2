from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from domain.siliconindex import SiliconIndexAdapter, SiliconIndexSyncError, project_brand_domain


def _datasets(brand: Mapping[str, object]) -> dict[str, object]:
    return {
        "brands": [dict(brand)],
        "mentions": [],
        "categories": [],
        "cognition-profiles": [],
        "compliance-rules": [],
        "competitor-relations": [],
        "query-templates": [],
        "search-index": [],
        "graph": {},
    }


def test_adapter_three_way_merge_is_identity_aware_and_conflict_explicit() -> None:
    adapter = SiliconIndexAdapter()
    base_brand = {"brand_id": "BR-1", "name": "Base", "status": "active"}
    upstream_brand = {**base_brand, "name": "Upstream"}
    local_brand = {**base_brand, "status": "reviewed"}
    clean = adapter.reconcile(
        _datasets(base_brand),
        _datasets(upstream_brand),
        _datasets(local_brand),
    )
    assert clean.conflicts == ()
    assert clean.merged["brands"] == [
        {"brand_id": "BR-1", "name": "Upstream", "status": "reviewed"}
    ]

    conflict = adapter.reconcile(
        _datasets(base_brand),
        _datasets(upstream_brand),
        _datasets({**base_brand, "name": "Local"}),
    )
    assert len(conflict.conflicts) == 1
    assert conflict.conflicts[0].path == "/datasets/brands/BR-1/name"


def test_public_export_filters_private_and_requires_evidence() -> None:
    adapter = SiliconIndexAdapter()
    result = adapter.export_changes(
        (
            {
                "stable_id": "private",
                "visibility": "private",
                "review_status": "reviewed",
                "attributes": {"evidence_urls": ["https://example.test/private"]},
            },
            {
                "stable_id": "public",
                "visibility": "public",
                "review_status": "reviewed",
                "attributes": {"evidence_urls": ["https://example.test/public"]},
            },
        )
    )
    assert result.result["count"] == 1
    assert result.result["changes"][0]["stable_id"] == "public"
    assert str(result.result["content_hash"]).startswith("sha256:")

    with pytest.raises(SiliconIndexSyncError, match="private_field"):
        adapter.export_changes(
            (
                {
                    "visibility": "public",
                    "review_status": "reviewed",
                    "project_name": "must-not-leak",
                    "attributes": {"evidence_urls": ["https://example.test"]},
                },
            )
        )
    with pytest.raises(SiliconIndexSyncError, match="evidence_required"):
        adapter.export_changes(
            (
                {
                    "visibility": "public",
                    "review_status": "reviewed",
                    "attributes": {},
                },
            )
        )


def test_adapter_projector_matches_the_checked_in_generated_read_model() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    siliconindex = repository_root.parent / "GEO-auto-analysis" / "siliconindex-consumer"
    if not (siliconindex / "public" / "data" / "v1" / "manifest.json").is_file():
        pytest.skip("companion SiliconIndex checkout is not available")
    expected_path = (
        repository_root
        / "domain"
        / "brandrank"
        / "rules_data"
        / "siliconindex_projection_cybersecurity.json"
    )
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = project_brand_domain(siliconindex, analysis_domain="cybersecurity")
    assert actual == expected
