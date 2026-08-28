from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from domain.metrics.v2 import load_definitions
from tools.activate_metrics_v2_definitions import (
    PUBLISHED_AT,
    ActivationArtifact,
    StoredArtifact,
    _partition_activation,
    activation_bundle_hash,
    build_activation_bundle,
    confirmation_token,
    plan_activation,
    verify_database_state,
)

ROOT = Path(__file__).resolve().parents[2]
PREEXISTING_PUBLISHED_AT = datetime(2026, 8, 28, 8, 3, 59, 763346, tzinfo=UTC)


def _stored(
    artifact: ActivationArtifact, *, status: str = "experimental"
) -> StoredArtifact:
    return StoredArtifact(
        kind=artifact.kind,
        name=artifact.name,
        version=artifact.version,
        content_hash=artifact.content_hash,
        status=status,
        published_at=PUBLISHED_AT if status == "published" else None,
        experimental=(status != "published" if artifact.kind == "metric_definition" else None),
    )


def test_v21_activation_bundle_is_exact_complete_and_stable() -> None:
    artifacts = build_activation_bundle()
    counts = {
        kind: sum(item.kind == kind for item in artifacts)
        for kind in ("decision_task", "judge_policy", "metric_definition")
    }

    assert len(artifacts) == 50
    assert counts == {"decision_task": 14, "judge_policy": 2, "metric_definition": 34}
    assert len({(item.kind, item.name, item.version) for item in artifacts}) == 50
    assert all(item.version == "2.1.0" for item in artifacts)
    assert all(item.published_at == PUBLISHED_AT for item in artifacts)
    bundle_hash = activation_bundle_hash(artifacts)
    assert bundle_hash == "af0cd33e1584b857e6f93365d66e5a9fa41fb27fb4a172ce5c98638a125d2af7"
    assert confirmation_token(bundle_hash) == (
        "d0f9af4ac49dbeb395f5b92424fcc197ca4aa1fdab4396fc81b7b115cce93cac"
    )


def test_generated_v21_metric_files_and_task_refs_are_frozen() -> None:
    expected_file_hashes = {
        "core_exposed_and_impression_v2_1.json": (
            "7cdd637f0032c5f7065e49686e43302152861e50d547afbeb407cb1e04f05cb4"
        ),
        "core_recommendation_v2_1.json": (
            "4d6fa1ef37a09b813fe16a916f22a3eb07ec73b973d37bf65cfd44e2e85db22f"
        ),
        "core_remaining_v2_1.json": (
            "3ff358a78ae64abe7c60793360f8350c27f2c6716639868fb4df853c137c9a55"
        ),
    }
    directory = ROOT / "domain/metrics/v2/definitions"
    for name, expected_hash in expected_file_hashes.items():
        assert sha256((directory / name).read_bytes()).hexdigest() == expected_hash

    definitions = tuple(item for item in load_definitions().all() if item.version == "2.1.0")
    assert len(definitions) == 34
    for definition in definitions:
        task_refs = set(definition.decision_task_refs) | {
            capability.task_ref for capability in definition.required_semantic_capabilities
        }
        assert all(ref.endswith("@2.1.0") for ref in task_refs)
    by_name = {item.name: item for item in definitions}
    assert by_name["claim_accuracy_rate_v2"].reason_codes["unknown"] == (
        "evidence_retrieval_failed"
    )
    assert by_name["unsupported_claim_rate_v2"].reason_codes["unknown"] == (
        "evidence_retrieval_failed"
    )


def test_dry_run_plan_accepts_only_the_exact_experimental_set() -> None:
    artifacts = build_activation_bundle()
    rows = tuple(_stored(item) for item in artifacts)

    report = plan_activation(artifacts, rows)

    assert report["mode"] == "dry_run"
    assert report["database_state"] == "activatable"
    assert report["artifact_count"] == 50
    assert report["counts"] == {
        "decision_task": 14,
        "judge_policy": 2,
        "metric_definition": 34,
    }
    assert report["reused"] == 0
    assert report["updated"] == 0
    assert report["pending"] == 50
    assert report["partial"] is False
    assert report["official_snapshot_activation"] is False


def test_exact_published_bundle_is_an_idempotent_terminal_state() -> None:
    artifacts = build_activation_bundle()
    rows = tuple(
        replace(
            _stored(item, status="published"),
            published_at=PREEXISTING_PUBLISHED_AT,
        )
        for item in artifacts
    )

    assert verify_database_state(artifacts, rows) == "already_published"
    report = plan_activation(artifacts, rows)
    assert report["reused"] == 50
    assert report["pending"] == 0
    assert report["partial"] is False


def test_database_hash_drift_fails_closed() -> None:
    artifacts = build_activation_bundle()
    rows = [_stored(item) for item in artifacts]
    rows[0] = replace(rows[0], content_hash="f" * 64)

    with pytest.raises(RuntimeError, match="metrics_v2_activation_hash_drift"):
        verify_database_state(artifacts, rows)


def test_database_count_drift_fails_closed() -> None:
    artifacts = build_activation_bundle()
    rows = tuple(_stored(item) for item in artifacts[:-1])

    with pytest.raises(RuntimeError, match="metrics_v2_activation_database_count_drift"):
        verify_database_state(artifacts, rows)


def test_exact_partial_activation_reuses_published_rows_and_plans_remaining_cas() -> None:
    artifacts = build_activation_bundle()
    rows = [_stored(item) for item in artifacts]
    production_published = {
        ("decision_task", "substantive-entity-mention"),
        ("judge_policy", "semantic-v2-primary-hybrid"),
        ("metric_definition", "ai_recommendation_organic_mention_rate_v2"),
    }
    published_keys: set[tuple[str, str, str]] = set()
    for index, artifact in enumerate(artifacts):
        if (artifact.kind, artifact.name) not in production_published:
            continue
        published = replace(
            _stored(artifact, status="published"),
            published_at=PREEXISTING_PUBLISHED_AT,
        )
        rows[index] = published
        published_keys.add((published.kind, published.name, published.version))
    assert len(published_keys) == 3

    state, pending, reused = _partition_activation(artifacts, rows)
    report = plan_activation(artifacts, rows)

    assert state == "partially_published"
    assert reused == 3
    assert len(pending) == 47
    assert published_keys.isdisjoint(
        (item.kind, item.name, item.version) for item in pending
    )
    assert report["database_state"] == "partially_published"
    assert report["bundle_hash"] == activation_bundle_hash(artifacts)
    assert report["reused"] == 3
    assert report["updated"] == 0
    assert report["pending"] == 47
    assert report["partial"] is True


def test_published_row_without_timestamp_fails_closed() -> None:
    artifacts = build_activation_bundle()
    rows = [_stored(item) for item in artifacts]
    rows[0] = replace(
        _stored(artifacts[0], status="published"),
        published_at=None,
    )

    with pytest.raises(RuntimeError, match="metrics_v2_activation_published_at_drift"):
        verify_database_state(artifacts, rows)


def test_non_activation_status_fails_closed() -> None:
    artifacts = build_activation_bundle()
    rows = [_stored(item) for item in artifacts]
    rows[0] = replace(rows[0], status="retired")

    with pytest.raises(RuntimeError, match="metrics_v2_activation_status_drift"):
        verify_database_state(artifacts, rows)
