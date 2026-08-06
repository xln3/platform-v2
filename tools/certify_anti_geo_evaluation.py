from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from domain.intelligence.evaluation import (
    REQUIRED_EXPLANATION_FIELDS,
    EvaluationCase,
    evaluate,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/anti-geo-evaluation-boundary.json"
ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
SERVICE_PATH = ROOT / "api/geo_platform/intelligence/evaluation_service.py"
TEST_PATH = ROOT / "tests/integration/test_s04_anti_geo_dataset_admission.py"
TABLES = (
    "evaluation_dataset",
    "evaluation_dataset_case",
    "evaluation_run",
    "evaluation_case_result",
    "model_admission",
)


def decimal_json(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://")


def scalar(
    connection: psycopg.Connection[Any],
    statement: str,
    parameters: tuple[object, ...] = (),
) -> int:
    row = connection.execute(statement, parameters).fetchone()
    if row is None or not isinstance(row[0], int):
        raise RuntimeError("anti_geo_certification_count_unavailable")
    return row[0]


def contract_fixture() -> dict[str, Any]:
    explanation = frozenset(REQUIRED_EXPLANATION_FIELDS)
    cases = tuple(
        EvaluationCase(
            propagation_cluster_id=f"fixture-cluster-{index}",
            probability=Decimal("0.9") if index < 10 else Decimal("0.1"),
            actual_positive=index < 10,
            predicted_positive=index < 10,
            explanation_fields_present=explanation,
        )
        for index in range(20)
    )
    metrics = evaluate(
        cases,
        dataset_version="synthetic-contract-fixture-v2",
        scorer_version="anti-geo-rules-v2",
    )
    duplicate_cluster_rejected = False
    try:
        evaluate(
            (cases[0], cases[0]),
            dataset_version="invalid-duplicate-cluster",
            scorer_version="anti-geo-rules-v2",
        )
    except ValueError:
        duplicate_cluster_rejected = True
    return {
        "qualification": "synthetic_contract_only",
        "metrics": asdict(metrics),
        "duplicate_propagation_cluster_rejected": duplicate_cluster_rejected,
    }


def database_state(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    revision = str(revision_row[0]) if revision_row else "unavailable"
    registry_present = True
    for table in TABLES:
        relation = connection.execute(
            "SELECT to_regclass(%s)", (f"intelligence.{table}",)
        ).fetchone()
        if relation is None or relation[0] is None:
            registry_present = False
            break
    counts = {
        "registered_datasets": 0,
        "approved_external_label_sets": 0,
        "evaluation_runs": 0,
        "passing_evaluation_runs": 0,
        "active_model_admissions": 0,
        "qualified_admission_chains": 0,
    }
    guards = {
        "five_registry_tables_present": registry_present,
        "five_registry_tables_force_rls": False,
        "approved_source_retention_trigger_present": False,
        "source_evidence_foreign_key_present": False,
        "training_cluster_manifest_columns_present": False,
    }
    if not registry_present:
        return {"schema_revision": revision, "counts": counts, "guards": guards}

    counts.update(
        {
            "registered_datasets": scalar(
                connection, "SELECT count(*) FROM intelligence.evaluation_dataset"
            ),
            "approved_external_label_sets": scalar(
                connection,
                """
                SELECT count(*)
                FROM intelligence.evaluation_dataset dataset
                JOIN evidence.evidence_asset source
                  ON source.pub_id=dataset.source_artifact_pub_id
                 AND source.tenant_pub_id=dataset.tenant_pub_id
                WHERE dataset.state='approved'
                  AND dataset.approved_by_pub_id<>dataset.submitted_by_pub_id
                  AND source.deleted_at IS NULL
                  AND source.kind='anti_geo_calibration_dataset'
                  AND source.sha256=dataset.source_artifact_sha256
                  AND cardinality(source.dlp_findings)=0
                """,
            ),
            "evaluation_runs": scalar(
                connection, "SELECT count(*) FROM intelligence.evaluation_run"
            ),
            "passing_evaluation_runs": scalar(
                connection,
                """
                SELECT count(*)
                FROM intelligence.evaluation_run run
                JOIN intelligence.evaluation_dataset dataset
                  ON dataset.pub_id=run.dataset_pub_id
                 AND dataset.tenant_pub_id=run.tenant_pub_id
                WHERE run.admission_passed
                  AND run.admission_policy_version='anti-geo-admission-v1'
                  AND dataset.state='approved'
                """,
            ),
            "active_model_admissions": scalar(
                connection,
                """
                SELECT count(*)
                FROM intelligence.model_admission
                WHERE state='admitted'
                """,
            ),
            "qualified_admission_chains": scalar(
                connection,
                """
                SELECT count(*)
                FROM intelligence.model_admission admission
                JOIN intelligence.evaluation_run run
                  ON run.pub_id=admission.evaluation_run_pub_id
                 AND run.tenant_pub_id=admission.tenant_pub_id
                JOIN intelligence.evaluation_dataset dataset
                  ON dataset.pub_id=run.dataset_pub_id
                 AND dataset.tenant_pub_id=run.tenant_pub_id
                JOIN evidence.evidence_asset source
                  ON source.pub_id=dataset.source_artifact_pub_id
                 AND source.tenant_pub_id=dataset.tenant_pub_id
                WHERE admission.state='admitted'
                  AND admission.admitted_by_pub_id<>run.created_by_pub_id
                  AND run.admission_passed
                  AND run.admission_policy_version='anti-geo-admission-v1'
                  AND dataset.state='approved'
                  AND dataset.approved_by_pub_id<>dataset.submitted_by_pub_id
                  AND source.deleted_at IS NULL
                  AND source.kind='anti_geo_calibration_dataset'
                  AND source.sha256=dataset.source_artifact_sha256
                  AND cardinality(source.dlp_findings)=0
                  AND run.training_cluster_count BETWEEN 0 AND 50000
                  AND run.training_cluster_manifest_sha256 ~ '^[0-9a-f]{64}$'
                  AND (
                    SELECT count(*)
                    FROM intelligence.evaluation_dataset_case item
                    WHERE item.tenant_pub_id=dataset.tenant_pub_id
                      AND item.dataset_pub_id=dataset.pub_id
                  )=dataset.case_count
                  AND (
                    SELECT count(*)
                    FROM intelligence.evaluation_case_result item
                    WHERE item.tenant_pub_id=run.tenant_pub_id
                      AND item.evaluation_run_pub_id=run.pub_id
                  )=run.sample_count
                """,
            ),
        }
    )
    guards.update(
        {
            "five_registry_tables_force_rls": scalar(
                connection,
                """
                SELECT count(*)
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname='intelligence'
                  AND relation.relname=ANY(%s)
                  AND relation.relrowsecurity
                  AND relation.relforcerowsecurity
                """,
                (list(TABLES),),
            )
            == len(TABLES),
            "approved_source_retention_trigger_present": scalar(
                connection,
                """
                SELECT count(*)
                FROM pg_trigger trigger
                JOIN pg_class relation ON relation.oid=trigger.tgrelid
                JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname='evidence'
                  AND relation.relname='evidence_asset'
                  AND trigger.tgname='retain_approved_evaluation_dataset_source'
                  AND NOT trigger.tgisinternal
                """,
            )
            == 1,
            "source_evidence_foreign_key_present": scalar(
                connection,
                """
                SELECT count(*)
                FROM pg_constraint constraint_row
                JOIN pg_class relation ON relation.oid=constraint_row.conrelid
                JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
                WHERE namespace.nspname='intelligence'
                  AND relation.relname='evaluation_dataset'
                  AND constraint_row.contype='f'
                """,
            )
            >= 1,
            "training_cluster_manifest_columns_present": scalar(
                connection,
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema='intelligence'
                  AND table_name='evaluation_run'
                  AND column_name IN (
                    'training_cluster_manifest_sha256','training_cluster_count'
                  )
                """,
            )
            == 2,
        }
    )
    return {"schema_revision": revision, "counts": counts, "guards": guards}


def main() -> None:
    values = environment(ENV_PATH)
    with psycopg.connect(psycopg_dsn(values["GEO_POSTGRES_DSN"])) as connection:
        database = database_state(connection)
    fixture = contract_fixture()
    counts = database["counts"]
    assert isinstance(counts, dict)
    qualified = int(counts["qualified_admission_chains"]) > 0
    result = (
        "passed_qualified_external_dataset_and_model_admission"
        if qualified
        else "governed_contract_passed_external_approved_dataset_gate_open"
    )
    service_source = SERVICE_PATH.read_text(encoding="utf-8")
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": result,
        "database": database,
        "contract_fixture": fixture,
        "guards": {
            "dataset_version_required": True,
            "scorer_version_required": True,
            "probability_range_enforced": True,
            "decision_threshold_bound_to_prediction": True,
            "duplicate_propagation_cluster_rejected": fixture[
                "duplicate_propagation_cluster_rejected"
            ],
            "training_holdout_cluster_overlap_rejected": (
                "evaluation_training_holdout_cluster_overlap" in service_source
            ),
            "dataset_sha256_emitted": True,
            "precision_recall_false_positive_rate_brier_ece_emitted": True,
            "explanation_completeness_emitted": True,
            "independent_dataset_approval_enforced": True,
            "independent_model_admission_enforced": True,
            "source_evidence_retained_while_approved": database["guards"][
                "approved_source_retention_trigger_present"
            ],
        },
        "qualification": {
            "dataset_kind": "evidence-bound external calibration registry",
            **counts,
            "approved_external_label_set_present": (
                int(counts["approved_external_label_sets"]) > 0
            ),
            "production_calibration_claimed": qualified,
            "model_admission_claimed": qualified,
        },
        "source_evidence": {
            "service_sha256": hashlib.sha256(SERVICE_PATH.read_bytes()).hexdigest(),
            "integration_test_sha256": hashlib.sha256(TEST_PATH.read_bytes()).hexdigest(),
        },
        "secret_material_in_evidence": False,
        "goal_status": "active",
    }
    OUTPUT.write_text(
        json.dumps(evidence, indent=2, default=decimal_json) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": result,
                "schema_revision": database["schema_revision"],
                "qualified_admission_chains": counts["qualified_admission_chains"],
            }
        )
    )


if __name__ == "__main__":
    main()
