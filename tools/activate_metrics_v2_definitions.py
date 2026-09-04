#!/usr/bin/env python3
"""Plan or atomically activate the reviewed Metrics V2.1 definition bundle.

The default mode is a database-backed dry run. Applying requires the exact
bundle hash and confirmation token emitted by a fresh dry run. The transaction
locks and compares every V2.1 definition row before changing lifecycle fields;
missing, extra, or hash-drifted rows fail closed. Exact-hash rows already
published by an earlier partial run are reused without rewriting them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from psycopg import Connection
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geo_platform.config import get_settings  # noqa: E402

from domain.analysis.v2 import (  # noqa: E402
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.analysis.v2._canonical import canonical_hash  # noqa: E402
from domain.metrics.v2 import load_definitions  # noqa: E402

TARGET_VERSION = "2.1.0"
PUBLISHED_AT = datetime(2026, 8, 28, tzinfo=UTC)
_EXPECTED_COUNTS = {
    "decision_task": 14,
    "judge_policy": 2,
    "metric_definition": 34,
}
_ACTIVATION_LOCK = "geo-metrics-v2-definition-activation:2.1.0"
_CONFIRMATION_DOMAIN = "geo-metrics-v2-definition-activation"

ArtifactKind = Literal["decision_task", "judge_policy", "metric_definition"]


@dataclass(frozen=True, slots=True)
class ActivationArtifact:
    kind: ArtifactKind
    name: str
    version: str
    content_hash: str
    published_at: datetime

    def canonical_document(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "content_hash": self.content_hash,
            "published_at": _wire_datetime(self.published_at),
        }


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    kind: ArtifactKind
    name: str
    version: str
    content_hash: str
    status: str
    published_at: datetime | None
    experimental: bool | None


_TABLES: dict[ArtifactKind, tuple[str, str]] = {
    "decision_task": (
        "analytics.semantic_decision_task_definition_v2",
        "definition_hash",
    ),
    "judge_policy": ("analytics.semantic_judge_policy_v2", "policy_hash"),
    "metric_definition": ("analytics.metric_definition", "definition_hash"),
}


def _wire_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dsn(value: str | None) -> str:
    configured = value or get_settings().postgres_dsn
    return configured.replace("postgresql+psycopg://", "postgresql://", 1)


def build_activation_bundle() -> tuple[ActivationArtifact, ...]:
    """Load the exact repository-owned V2.1 lifecycle activation set."""

    tasks = load_builtin_task_definitions()
    policies = load_builtin_judge_policies(tasks=tasks)
    metrics = load_definitions()
    artifacts: list[ActivationArtifact] = []

    for task in tasks.definitions:
        if task.version != TARGET_VERSION:
            continue
        if task.status.value != "published" or task.published_at != PUBLISHED_AT:
            raise RuntimeError(f"metrics_v2_activation_task_not_published:{task.task_ref}")
        artifacts.append(
            ActivationArtifact(
                kind="decision_task",
                name=task.name,
                version=task.version,
                content_hash=task.definition_hash,
                published_at=task.published_at,
            )
        )

    for policy in policies:
        if policy.version != TARGET_VERSION:
            continue
        if policy.status.value != "published" or policy.published_at != PUBLISHED_AT:
            raise RuntimeError(f"metrics_v2_activation_policy_not_published:{policy.policy_ref}")
        artifacts.append(
            ActivationArtifact(
                kind="judge_policy",
                name=policy.name,
                version=policy.version,
                content_hash=policy.policy_hash,
                published_at=policy.published_at,
            )
        )

    for definition in metrics.all():
        if definition.version != TARGET_VERSION:
            continue
        if definition.status.value != "published":
            raise RuntimeError(
                "metrics_v2_activation_metric_not_published:"
                f"{definition.name}@{definition.version}"
            )
        artifacts.append(
            ActivationArtifact(
                kind="metric_definition",
                name=definition.name,
                version=definition.version,
                content_hash=definition.definition_hash,
                published_at=PUBLISHED_AT,
            )
        )

    counts = _counts(artifacts)
    if counts != _EXPECTED_COUNTS:
        raise RuntimeError(
            "metrics_v2_activation_repository_count_drift:"
            f"expected={_EXPECTED_COUNTS}:actual={counts}"
        )
    ordered = tuple(sorted(artifacts, key=lambda item: (item.kind, item.name, item.version)))
    identities = [(item.kind, item.name, item.version) for item in ordered]
    if len(identities) != len(set(identities)):
        raise RuntimeError("metrics_v2_activation_repository_identity_duplicate")
    return ordered


def activation_bundle_hash(artifacts: Sequence[ActivationArtifact]) -> str:
    return canonical_hash(
        {
            "schema_version": "metrics-v2-definition-activation-plan-v1",
            "target_version": TARGET_VERSION,
            "published_at": _wire_datetime(PUBLISHED_AT),
            "artifacts": [item.canonical_document() for item in artifacts],
        }
    )


def confirmation_token(bundle_hash: str) -> str:
    return sha256(f"{_CONFIRMATION_DOMAIN}:{bundle_hash}".encode()).hexdigest()


def _counts(artifacts: Sequence[ActivationArtifact | StoredArtifact]) -> dict[str, int]:
    counts = {kind: 0 for kind in _EXPECTED_COUNTS}
    for item in artifacts:
        counts[item.kind] += 1
    return counts


def verify_database_state(
    expected: Sequence[ActivationArtifact],
    stored: Sequence[StoredArtifact],
) -> Literal["activatable", "partially_published", "already_published"]:
    """Compare the complete DB V2.1 set with the reviewed repository bundle."""

    expected_by_key = {(item.kind, item.name, item.version): item for item in expected}
    stored_by_key = {(item.kind, item.name, item.version): item for item in stored}
    if len(stored_by_key) != len(stored):
        raise RuntimeError("metrics_v2_activation_database_identity_duplicate")
    if _counts(stored) != _counts(expected):
        raise RuntimeError(
            "metrics_v2_activation_database_count_drift:"
            f"expected={_counts(expected)}:actual={_counts(stored)}"
        )
    if stored_by_key.keys() != expected_by_key.keys():
        missing = sorted(expected_by_key.keys() - stored_by_key.keys())
        extra = sorted(stored_by_key.keys() - expected_by_key.keys())
        raise RuntimeError(f"metrics_v2_activation_database_identity_drift:{missing}:{extra}")

    lifecycle_states: set[str] = set()
    for key, artifact in expected_by_key.items():
        row = stored_by_key[key]
        if row.content_hash != artifact.content_hash:
            raise RuntimeError(
                "metrics_v2_activation_hash_drift:"
                f"{artifact.kind}:{artifact.name}@{artifact.version}"
            )
        if row.status == "experimental":
            if row.published_at is not None:
                raise RuntimeError(
                    "metrics_v2_activation_lifecycle_drift:"
                    f"{artifact.kind}:{artifact.name}@{artifact.version}"
                )
            if artifact.kind == "metric_definition" and row.experimental is not True:
                raise RuntimeError(
                    "metrics_v2_activation_metric_flag_drift:"
                    f"{artifact.name}@{artifact.version}"
                )
            lifecycle_states.add("activatable")
        elif row.status == "published":
            # A prior exact-hash activation may have committed with its own
            # transaction timestamp.  It is immutable and safe to reuse, but
            # a published row without provenance is not.
            if row.published_at is None:
                raise RuntimeError(
                    "metrics_v2_activation_published_at_drift:"
                    f"{artifact.kind}:{artifact.name}@{artifact.version}"
                )
            if artifact.kind == "metric_definition" and row.experimental is not False:
                raise RuntimeError(
                    "metrics_v2_activation_metric_flag_drift:"
                    f"{artifact.name}@{artifact.version}"
                )
            lifecycle_states.add("already_published")
        else:
            raise RuntimeError(
                "metrics_v2_activation_status_drift:"
                f"{artifact.kind}:{artifact.name}@{artifact.version}:{row.status}"
            )
    if lifecycle_states == {"activatable"}:
        return "activatable"
    if lifecycle_states == {"already_published"}:
        return "already_published"
    if lifecycle_states == {"activatable", "already_published"}:
        return "partially_published"
    raise RuntimeError(f"metrics_v2_activation_lifecycle_drift:{sorted(lifecycle_states)}")


def _partition_activation(
    expected: Sequence[ActivationArtifact],
    stored: Sequence[StoredArtifact],
) -> tuple[
    Literal["activatable", "partially_published", "already_published"],
    tuple[ActivationArtifact, ...],
    int,
]:
    state = verify_database_state(expected, stored)
    stored_by_key = {(item.kind, item.name, item.version): item for item in stored}
    pending = tuple(
        artifact
        for artifact in expected
        if stored_by_key[(artifact.kind, artifact.name, artifact.version)].status
        == "experimental"
    )
    return state, pending, len(expected) - len(pending)


def _load_database_rows(
    connection: Connection[dict[str, Any]], *, for_update: bool
) -> tuple[StoredArtifact, ...]:
    rows: list[StoredArtifact] = []
    suffix = " FOR UPDATE" if for_update else ""
    for kind, (table, hash_column) in _TABLES.items():
        metric_flag = "experimental" if kind == "metric_definition" else "NULL::boolean"
        # Identifiers are selected only from the closed constants above.
        result = connection.execute(
            f"""
            SELECT name,version,{hash_column} AS content_hash,status,published_at,
                   {metric_flag} AS experimental
            FROM {table}
            WHERE version=%s
            ORDER BY name,version{suffix}
            """,
            (TARGET_VERSION,),
        ).fetchall()
        rows.extend(
            StoredArtifact(
                kind=kind,
                name=str(row["name"]),
                version=str(row["version"]),
                content_hash=str(row["content_hash"]),
                status=str(row["status"]),
                published_at=(
                    row["published_at"].astimezone(UTC)
                    if row.get("published_at") is not None
                    else None
                ),
                experimental=(
                    bool(row["experimental"])
                    if row.get("experimental") is not None
                    else None
                ),
            )
            for row in result
        )
    return tuple(rows)


def _require_schema(connection: Connection[dict[str, Any]]) -> None:
    row = connection.execute(
        """
        SELECT ARRAY_REMOVE(ARRAY[
          CASE WHEN to_regclass('analytics.semantic_decision_task_definition_v2')
               IS NULL THEN 'semantic_decision_task_definition_v2' END,
          CASE WHEN to_regclass('analytics.semantic_judge_policy_v2')
               IS NULL THEN 'semantic_judge_policy_v2' END,
          CASE WHEN to_regclass('analytics.metric_definition')
               IS NULL THEN 'metric_definition' END
        ],NULL) AS names
        """
    ).fetchone()
    if row is None or row["names"]:
        raise RuntimeError("metrics_v2_activation_migration_required")


def inspect_database(dsn: str) -> tuple[StoredArtifact, ...]:
    with Connection.connect(dsn, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        _require_schema(connection)
        return _load_database_rows(connection, for_update=False)


def activate(dsn: str, artifacts: Sequence[ActivationArtifact]) -> dict[str, Any]:
    with Connection.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (_ACTIVATION_LOCK,),
        )
        _require_schema(connection)
        stored = _load_database_rows(connection, for_update=True)
        state, pending, reused = _partition_activation(artifacts, stored)
        if state == "already_published":
            return {
                "activation_state": state,
                "initial_database_state": state,
                "updated": 0,
                "reused": reused,
                "pending": 0,
                "partial": False,
            }

        updated = 0
        for artifact in pending:
            table, hash_column = _TABLES[artifact.kind]
            metric_assignment = (
                ",experimental=false" if artifact.kind == "metric_definition" else ""
            )
            # Lifecycle columns are the only updated values; the DB trigger
            # independently rejects any semantic mutation.
            row = connection.execute(
                f"""
                UPDATE {table}
                SET status='published',published_at=%s{metric_assignment}
                WHERE name=%s AND version=%s AND {hash_column}=%s
                  AND status='experimental' AND published_at IS NULL
                RETURNING {hash_column} AS content_hash,status,published_at
                """,
                (
                    artifact.published_at,
                    artifact.name,
                    artifact.version,
                    artifact.content_hash,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "metrics_v2_activation_cas_conflict:"
                    f"{artifact.kind}:{artifact.name}@{artifact.version}"
                )
            updated += 1

        final_state = verify_database_state(
            artifacts, _load_database_rows(connection, for_update=False)
        )
        if final_state != "already_published":
            raise RuntimeError("metrics_v2_activation_postcondition_failed")
    return {
        "activation_state": final_state,
        "initial_database_state": state,
        "updated": updated,
        "reused": reused,
        "pending": 0,
        "partial": state == "partially_published",
    }


def plan_activation(
    artifacts: Sequence[ActivationArtifact], stored: Sequence[StoredArtifact]
) -> dict[str, Any]:
    database_state, pending_artifacts, reused = _partition_activation(artifacts, stored)
    bundle_hash = activation_bundle_hash(artifacts)
    return {
        "schema_version": "metrics-v2-definition-activation-plan-v1",
        "mode": "dry_run",
        "target_version": TARGET_VERSION,
        "target_status": "published",
        "published_at": _wire_datetime(PUBLISHED_AT),
        "artifact_count": len(artifacts),
        "counts": _counts(artifacts),
        "bundle_hash": bundle_hash,
        "confirm_token": confirmation_token(bundle_hash),
        "database_state": database_state,
        "reused": reused,
        "updated": 0,
        "pending": len(pending_artifacts),
        "partial": database_state == "partially_published",
        "official_snapshot_activation": False,
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="owner/migration PostgreSQL DSN")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bundle-hash")
    parser.add_argument("--confirm-token")
    arguments = parser.parse_args(argv)
    confirmations = (arguments.bundle_hash, arguments.confirm_token)
    if arguments.apply and not all(confirmations):
        parser.error("--apply requires --bundle-hash and --confirm-token")
    if not arguments.apply and any(confirmations):
        parser.error("confirmation arguments are only valid with --apply")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    artifacts = build_activation_bundle()
    dsn = _dsn(arguments.dsn)
    report = plan_activation(artifacts, inspect_database(dsn))
    if not arguments.apply:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.bundle_hash != report["bundle_hash"]:
        raise SystemExit("definition activation bundle changed; run dry-run again")
    if arguments.confirm_token != report["confirm_token"]:
        raise SystemExit("definition activation confirmation token mismatch")
    result = activate(dsn, artifacts)
    report.update(result)
    report["mode"] = "applied"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
