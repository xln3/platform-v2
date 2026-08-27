from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from domain.knowledge_evolution.release import KnowledgeReleaseStore
from tools.migrate_brand_knowledge_release import (
    _entity_evidence_claims,
    _record_database_lineage_only,
    _replay_baseline_release_id,
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


def test_idempotent_retry_uses_the_immutable_candidates_original_parent(tmp_path: Path) -> None:
    store = KnowledgeReleaseStore(tmp_path)
    store.publish(
        release_id="knowledge-baseline",
        schema_version="knowledge-release-v1",
        documents={"fixture": {"value": "baseline"}},
        parent_release_id=None,
        quality_report={"quality_gate": "passed"},
        activate=True,
    )
    store.publish(
        release_id="knowledge-candidate",
        schema_version="knowledge-release-v1",
        documents={"fixture": {"value": "candidate"}},
        parent_release_id="knowledge-baseline",
        quality_report={"quality_gate": "passed"},
        activate=True,
    )

    assert (
        _replay_baseline_release_id(store, release_id="knowledge-candidate") == "knowledge-baseline"
    )
    assert _replay_baseline_release_id(store, release_id="knowledge-next") == "knowledge-candidate"


def test_idempotent_database_retry_returns_before_requiring_a_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExistingReleaseSession:
        def __enter__(self) -> ExistingReleaseSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def scalar(self, _statement: object) -> SimpleNamespace:
            return SimpleNamespace(content_hash="sha256:same", pub_id="krl_existing")

    monkeypatch.setattr(
        "tools.migrate_brand_knowledge_release.SessionLocal",
        lambda: ExistingReleaseSession(),
    )
    monkeypatch.setattr(
        "tools.migrate_brand_knowledge_release.TenantRepository",
        lambda *_args, **_kwargs: None,
    )

    result = _record_database_lineage_only(
        tenant_pub_id="tnt_fixture",
        release_id="knowledge-candidate",
        manifest={"content_hash": "sha256:same", "parent_release_id": None},
        projection={"source_release_id": "2026-08-27.6"},
        artifact_uri="/tmp/immutable-artifact",
    )

    assert result == {"database": "already_recorded", "release_pub_id": "krl_existing"}
