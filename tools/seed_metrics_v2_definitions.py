#!/usr/bin/env python3
"""Validate and explicitly seed GEO metrics V2 definition artifacts.

The default mode is read-only.  ``--apply`` is required for database writes,
and every repository artifact is persisted as ``experimental``.  Publication
is a separate calibrated, authorized operation; this command cannot activate
an official definition or publication pointer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geo_platform.config import get_settings  # noqa: E402

from domain.analysis.v2 import (  # noqa: E402
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.metrics.v2 import load_definitions, validate_metric_definition  # noqa: E402


@dataclass(frozen=True, slots=True)
class SeedArtifact:
    kind: str
    name: str
    version: str
    content_hash: str
    document: dict[str, Any]


def _dsn(value: str | None) -> str:
    configured = value or get_settings().postgres_dsn
    return configured.replace("postgresql+psycopg://", "postgresql://", 1)


def build_seed_bundle() -> tuple[SeedArtifact, ...]:
    """Load all artifacts and coerce only their lifecycle to experimental."""

    task_registry = load_builtin_task_definitions()
    policies = load_builtin_judge_policies(tasks=task_registry)
    metrics = load_definitions()
    artifacts: list[SeedArtifact] = []

    for task in task_registry.definitions:
        document = task.model_dump(mode="json")
        document["status"] = "experimental"
        document["published_at"] = None
        artifacts.append(
            SeedArtifact(
                kind="decision_task",
                name=task.name,
                version=task.version,
                content_hash=task.definition_hash,
                document=document,
            )
        )

    for policy in policies:
        document = policy.model_dump(mode="json")
        document["status"] = "experimental"
        document["published_at"] = None
        artifacts.append(
            SeedArtifact(
                kind="judge_policy",
                name=policy.name,
                version=policy.version,
                content_hash=policy.policy_hash,
                document=document,
            )
        )

    for definition in metrics.all():
        source = dict(definition.raw_definition)
        source["status"] = "experimental"
        source.pop("definition_hash", None)
        staged = validate_metric_definition(source)
        document = dict(staged.raw_definition)
        document["status"] = "experimental"
        document["definition_hash"] = staged.definition_hash
        artifacts.append(
            SeedArtifact(
                kind="metric_definition",
                name=staged.name,
                version=staged.version,
                content_hash=staged.definition_hash,
                document=document,
            )
        )

    artifacts.sort(key=lambda item: (item.kind, item.name, item.version))
    return tuple(artifacts)


def _seed_task(connection: Connection[dict[str, Any]], artifact: SeedArtifact) -> bool:
    item = artifact.document
    row = connection.execute(
        """
        INSERT INTO analytics.semantic_decision_task_definition_v2
          (name,version,subject_type,subject_ref_schema,business_question,input_schema,
           output_schema,dependency_task_refs,candidate_policy,decision_method_policy,
           rubric_ref,rubric_hash,prompt_template_ref,prompt_template_hash,
           evidence_requirements,abstention_policy,adjudication_policy,calibration_gate,
           definition_hash,status,published_at,created_at)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
           'experimental',NULL,%s)
        ON CONFLICT (name,version) DO NOTHING
        RETURNING definition_hash
        """,
        (
            item["name"],
            item["version"],
            item["subject_type"],
            Jsonb(item["subject_ref_schema"]),
            item["business_question"],
            Jsonb(item["input_schema"]),
            Jsonb(item["output_schema"]),
            Jsonb(item["dependency_task_refs"]),
            Jsonb(item["candidate_policy"]),
            item["decision_method_policy"],
            item["rubric_ref"],
            item["rubric_hash"],
            item["prompt_template_ref"],
            item["prompt_template_hash"],
            Jsonb(item["evidence_requirements"]),
            Jsonb(item["abstention_policy"]),
            Jsonb(item["adjudication_policy"]),
            Jsonb(item["calibration_gate"]),
            artifact.content_hash,
            item["created_at"],
        ),
    ).fetchone()
    return row is not None


def _seed_policy(connection: Connection[dict[str, Any]], artifact: SeedArtifact) -> bool:
    item = artifact.document
    row = connection.execute(
        """
        INSERT INTO analytics.semantic_judge_policy_v2
          (name,version,compatible_task_refs,method_pipeline,model_routes,
           inference_configs,timeout_retry_policy,acceptance_thresholds,
           disagreement_policy,evidence_budget,cost_budget,fallback_policy,
           calibration_artifact_hash,policy_hash,status,published_at,created_at)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'experimental',NULL,%s)
        ON CONFLICT (name,version) DO NOTHING
        RETURNING policy_hash
        """,
        (
            item["name"],
            item["version"],
            Jsonb(item["compatible_task_refs"]),
            Jsonb(item["method_pipeline"]),
            Jsonb(item["model_routes"]),
            Jsonb(item["inference_configs"]),
            Jsonb(item["timeout_retry_policy"]),
            Jsonb(item["acceptance_thresholds"]),
            item["disagreement_policy"],
            Jsonb(item["evidence_budget"]),
            Jsonb(item["cost_budget"]),
            Jsonb(item["fallback_policy"]),
            item.get("calibration_artifact_hash"),
            artifact.content_hash,
            item["created_at"],
        ),
    ).fetchone()
    return row is not None


def _seed_metric(connection: Connection[dict[str, Any]], artifact: SeedArtifact) -> bool:
    item = validate_metric_definition(artifact.document)
    row = connection.execute(
        """
        INSERT INTO analytics.metric_definition
          (name,version,experimental,definition,definition_schema_version,
           definition_hash,status,unit_type,required_event_types,
           required_semantic_capabilities,decision_task_refs,outcome_source,
           semantic_rubric_ref,adjudication_uncertainty_policy,
           allowed_aggregation_methods,default_aggregation_method,
           publication_gate,published_at)
        VALUES
          (%s,%s,true,%s,%s,%s,'experimental',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
        ON CONFLICT (name,version) DO NOTHING
        RETURNING definition_hash
        """,
        (
            item.name,
            item.version,
            Jsonb(artifact.document),
            item.definition_schema_version,
            item.definition_hash,
            item.unit_type.value,
            list(item.required_event_types),
            Jsonb(
                [
                    {
                        "name": capability.name,
                        "task_ref": capability.task_ref,
                        "accepted_status": capability.accepted_status,
                    }
                    for capability in item.required_semantic_capabilities
                ]
            ),
            Jsonb(list(item.decision_task_refs)),
            item.outcome_source.value,
            item.semantic_rubric_ref,
            Jsonb(dict(item.adjudication_uncertainty_policy)),
            [method.value for method in item.allowed_aggregation_methods],
            item.default_aggregation_method.value,
            Jsonb(dict(item.publication_gate)),
        ),
    ).fetchone()
    return row is not None


def _verify_existing(
    connection: Connection[dict[str, Any]], artifact: SeedArtifact
) -> str:
    table, hash_column = {
        "decision_task": (
            "analytics.semantic_decision_task_definition_v2",
            "definition_hash",
        ),
        "judge_policy": ("analytics.semantic_judge_policy_v2", "policy_hash"),
        "metric_definition": ("analytics.metric_definition", "definition_hash"),
    }[artifact.kind]
    # Table/column identifiers are closed constants above; values stay bound.
    row = connection.execute(
        f"SELECT {hash_column},status FROM {table} WHERE name=%s AND version=%s",
        (artifact.name, artifact.version),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"metrics_v2_seed_missing:{artifact.kind}:{artifact.name}")
    if row[hash_column] != artifact.content_hash:
        raise RuntimeError(f"metrics_v2_seed_hash_conflict:{artifact.kind}:{artifact.name}")
    return str(row["status"])


def seed(dsn: str, artifacts: tuple[SeedArtifact, ...]) -> dict[str, Any]:
    inserted = 0
    reused = 0
    statuses: dict[str, int] = {}
    with Connection.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("geo-metrics-v2-definition-seed",),
        )
        missing = connection.execute(
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
        if missing is None or missing["names"]:
            raise RuntimeError("metrics_v2_migration_required")
        for artifact in artifacts:
            created = {
                "decision_task": _seed_task,
                "judge_policy": _seed_policy,
                "metric_definition": _seed_metric,
            }[artifact.kind](connection, artifact)
            inserted += int(created)
            reused += int(not created)
            status = _verify_existing(connection, artifact)
            statuses[status] = statuses.get(status, 0) + 1
    return {
        "mode": "applied",
        "inserted": inserted,
        "reused": reused,
        "statuses": dict(sorted(statuses.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="owner/migration PostgreSQL DSN")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write experimental artifacts; omitted means validation-only dry-run",
    )
    args = parser.parse_args()
    artifacts = build_seed_bundle()
    counts: dict[str, int] = {}
    for item in artifacts:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    report: dict[str, Any] = {
        "mode": "dry_run",
        "artifact_count": len(artifacts),
        "counts": dict(sorted(counts.items())),
        "target_status": "experimental",
        "official_activation": False,
    }
    if args.apply:
        report.update(seed(_dsn(args.dsn), artifacts))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
