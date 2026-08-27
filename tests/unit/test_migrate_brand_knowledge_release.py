from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.migrate_brand_knowledge_release import (
    _entity_evidence_claims,
    _validated_historical_replay,
    _verify_lineage_only_successor,
)


def test_claim_specific_identity_evidence_is_preserved_and_deduplicated() -> None:
    claims = _entity_evidence_claims(
        {
            "entity_id": "BR-1",
            "evidence_urls": ["https://example.test/brand"],
            "alias_identities": {
                "Legal Name": {
                    "entity_id": "OBJ-LEGAL",
                    "evidence_urls": ["https://example.test/legal"],
                },
                "Legal Abbreviation": {
                    "entity_id": "OBJ-LEGAL",
                    "evidence_urls": ["https://example.test/legal"],
                },
            },
        }
    )
    assert claims == (
        ("https://example.test/brand", "Supports reviewed object(s): BR-1."),
        ("https://example.test/legal", "Supports reviewed object(s): OBJ-LEGAL."),
    )

    with pytest.raises(SystemExit, match="reviewed_identity_without_evidence"):
        _entity_evidence_claims(
            {
                "entity_id": "BR-1",
                "evidence_urls": ["https://example.test/brand"],
                "alias_identities": {"Alias": {"entity_id": "OBJ-UNPROVEN"}},
            }
        )


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


def test_database_import_replay_receipt_is_bound_to_candidate_and_baseline(
    tmp_path: Path,
) -> None:
    projection = _projection()
    report = {
        "historical_replay": {
            "schema_version": "historical-replay-v1",
            "evaluation_set_hash": "sha256:" + "a" * 64,
            "time_cutoff": "2026-08-26T23:59:59+08:00",
            "evaluated_request_count": 12,
            "baseline_error_count": 2,
            "candidate_error_count": 0,
            "corrected_error_count": 2,
            "new_error_count": 0,
            "allowed_new_error_count": 0,
            "passed": True,
            "baseline_release_id": "knowledge-baseline",
            "candidate_release_id": projection["source_release_id"],
            "candidate_content_hash": projection["source_content_hash"],
        }
    }
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    replay, gate = _validated_historical_replay(
        path,
        projection=projection,
        baseline_release_id="knowledge-baseline",
    )
    assert replay["candidate_release_id"] == projection["source_release_id"]
    assert gate["passed"] is True

    report["historical_replay"]["candidate_content_hash"] = "sha256:wrong"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(SystemExit, match="historical_replay_candidate_hash_mismatch"):
        _validated_historical_replay(
            path,
            projection=projection,
            baseline_release_id="knowledge-baseline",
        )
