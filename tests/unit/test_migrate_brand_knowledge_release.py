from __future__ import annotations

from typing import Any

import pytest

from tools.migrate_brand_knowledge_release import _verify_lineage_only_successor


def _projection(*, canonical_name: str = "腾讯") -> dict[str, Any]:
    return {
        "domain": "cybersecurity",
        "source_release_id": "2026-08-27.2",
        "source_content_hash": "sha256:new-global-snapshot",
        "entities": [
            {
                "entity_id": "CYB-BR-TENCENT",
                "canonical_name": canonical_name,
                "aliases": ["腾讯云"],
                "review_status": "reviewed",
            }
        ],
    }


def _current_objects() -> dict[str, dict[str, Any]]:
    entity = _projection()["entities"][0]
    return {
        "CYB-BR-TENCENT": {"analysis_domain": "cybersecurity", **entity},
    }


def test_lineage_only_accepts_new_upstream_hash_when_governed_objects_match() -> None:
    verification = _verify_lineage_only_successor(
        parent_quality_report={
            "source_release_id": "2026-08-27.1",
            "source_content_hash": "sha256:old-global-snapshot",
        },
        projection=_projection(),
        current_reviewed_objects=_current_objects(),
    )

    assert verification == {
        "reviewed_objects_verified": 1,
        "previous_source_release_id": "2026-08-27.1",
        "source_release_id": "2026-08-27.2",
        "source_release_changed": True,
        "previous_source_content_hash": "sha256:old-global-snapshot",
        "source_content_hash": "sha256:new-global-snapshot",
        "source_content_hash_changed": True,
    }


def test_lineage_only_rejects_changed_governed_object_content() -> None:
    with pytest.raises(RuntimeError, match="lineage_only_governed_object_content_changed"):
        _verify_lineage_only_successor(
            parent_quality_report={
                "source_release_id": "2026-08-27.1",
                "source_content_hash": "sha256:old-global-snapshot",
            },
            projection=_projection(canonical_name="腾讯安全"),
            current_reviewed_objects=_current_objects(),
        )
