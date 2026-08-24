"""Deterministic, fail-closed historical ``consumer_web`` assignment backfill.

The module owns the complete SQL plan and execution order.  It never creates newer
campaign/target/slot identities and never derives a surface from legacy provenance
fields.  Callers provide an already-open PostgreSQL connection; dry-run performs
only reads, while apply commits exactly once after every target and audit row succeeds.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

COLLECTION_SURFACE = "consumer_web"
SURFACE_ASSIGNMENT_BASIS = "authoritative_historical_collection_policy_20260824"
LEGACY_CONTRACT_VERSION = "collection-v1-consumer-web-overlay-20260824"
SELECTOR_VERSION = "collection-surface-selector-20260824-v2"
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1_000
SAMPLE_LIMIT_PER_TARGET = 5
AUDIT_SAMPLE_LIMIT = 25

_SAFE_AUDIT_VALUE = re.compile(r"^[A-Za-z0-9._:@/-]+$")

STRICT_SELECTOR_PREDICATE = """
(
  (
    r.workflow_id LIKE 'geo-collection/%'
    AND r.source IN ('manual','schedule','retry')
  )
  OR
  (
    r.workflow_id LIKE 'legacy-history/%'
    AND EXISTS (
      SELECT 1
      FROM integration.legacy_id_map legacy_map
      JOIN integration.migration_run migration
        ON migration.id=legacy_map.run_id
      WHERE legacy_map.source_system='legacy-geosys-sqlite'
        AND legacy_map.entity_type='collection_run'
        AND legacy_map.target_pub_id=r.pub_id
        AND legacy_map.state='migrated'
        AND migration.source_system='legacy-geosys-sqlite'
        AND migration.state='completed'
    )
  )
)
""".strip()

_SELECTED_RUNS_SELECT = f"""
SELECT r.id AS run_id,
       r.pub_id AS run_pub_id,
       r.tenant_id,
       tenant.pub_id AS tenant_pub_id,
       r.project_id,
       project.pub_id AS project_pub_id
FROM platform.collection_run r
JOIN platform.tenant tenant ON tenant.id=r.tenant_id
JOIN platform.project project
  ON project.id=r.project_id AND project.tenant_id=r.tenant_id
WHERE r.tenant_id=%(tenant_id)s
  AND {STRICT_SELECTOR_PREDICATE}
""".strip()

SELECTED_RUNS_CTE = f"WITH selected_runs AS ({_SELECTED_RUNS_SELECT})"
BATCH_SELECTED_RUNS_CTE = (
    f"WITH selected_runs AS ({_SELECTED_RUNS_SELECT} AND r.pub_id=ANY(%(batch_run_pub_ids)s))"
)
SELECTOR_DEFINITION_HASH = hashlib.sha256(
    f"{SELECTOR_VERSION}\n{_SELECTED_RUNS_SELECT}".encode()
).hexdigest()

SCHEMA_CHECK_SQL = """
/* collection-surface-backfill:schema-check */
SELECT table_schema,table_name,column_name
FROM information_schema.columns
WHERE table_schema IN ('platform','analytics','evidence','integration')
ORDER BY table_schema,table_name,ordinal_position
""".strip()

DRY_RUN_ISOLATION_SQL = """
/* collection-surface-backfill:isolation:dry-run */
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY
""".strip()

APPLY_ISOLATION_SQL = """
/* collection-surface-backfill:isolation:apply */
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE
""".strip()

TENANT_ID_SQL = """
/* collection-surface-backfill:tenant-id */
SELECT id FROM platform.tenant WHERE pub_id=%(tenant_pub_id)s
""".strip()

RLS_CONTEXT_SQL = """
/* collection-surface-backfill:rls-context */
SELECT set_config('app.tenant_id',%(tenant_id)s,true),
       set_config('app.tenant_pub_id',%(tenant_pub_id)s,true)
""".strip()

ADVISORY_LOCK_SQL = """
/* collection-surface-backfill:advisory-lock */
SELECT pg_advisory_xact_lock(%(advisory_lock_key)s)
""".strip()

SELECTED_RUNS_SQL = f"""
/* collection-surface-backfill:selected-runs */
{SELECTED_RUNS_CTE}
SELECT run_id,run_pub_id,tenant_id,tenant_pub_id,project_id,project_pub_id
FROM selected_runs
ORDER BY tenant_pub_id,project_pub_id,run_pub_id
""".strip()

EXCLUDED_COUNTS_SQL = f"""
/* collection-surface-backfill:excluded-counts */
{SELECTED_RUNS_CTE},
selected_projects AS (
  SELECT DISTINCT tenant_id,project_id FROM selected_runs
)
SELECT candidate.tenant_id,candidate.project_id,count(*) AS excluded_count
FROM platform.collection_run candidate
JOIN selected_projects selected_project
  ON selected_project.tenant_id=candidate.tenant_id
 AND selected_project.project_id=candidate.project_id
LEFT JOIN selected_runs selected ON selected.run_id=candidate.id
WHERE selected.run_id IS NULL
GROUP BY candidate.tenant_id,candidate.project_id
ORDER BY candidate.tenant_id,candidate.project_id
""".strip()

ORPHAN_COUNTS_SQL = f"""
/* collection-surface-backfill:orphan-counts */
{SELECTED_RUNS_CTE},
selected_answers AS (
  SELECT answer.pub_id AS answer_pub_id,
         selected.tenant_id,selected.tenant_pub_id,
         selected.project_id,selected.project_pub_id
  FROM selected_runs selected
  JOIN analytics.answer answer
    ON answer.tenant_pub_id=selected.tenant_pub_id
   AND answer.run_pub_id=selected.run_pub_id
   AND answer.project_pub_id=selected.project_pub_id
),
orphan_facts AS (
  SELECT DISTINCT 'collection_task_tenant_mismatch' AS category,
         selected.tenant_id,selected.project_id,task.pub_id AS fact_pub_id
  FROM selected_runs selected
  JOIN platform.collection_task task ON task.run_id=selected.run_id
  WHERE task.tenant_id<>selected.tenant_id
  UNION ALL
  SELECT DISTINCT 'analysis_job_tenant_mismatch',
         selected.tenant_id,selected.project_id,job.pub_id
  FROM selected_runs selected
  JOIN platform.analysis_job job ON job.run_id=selected.run_id
  WHERE job.tenant_id<>selected.tenant_id
  UNION ALL
  SELECT DISTINCT 'answer_tenant_mismatch',
         selected.tenant_id,selected.project_id,answer.pub_id
  FROM selected_runs selected
  JOIN analytics.answer answer ON answer.run_pub_id=selected.run_pub_id
  WHERE answer.tenant_pub_id<>selected.tenant_pub_id
  UNION ALL
  SELECT DISTINCT 'answer_project_mismatch',
         selected.tenant_id,selected.project_id,answer.pub_id
  FROM selected_runs selected
  JOIN analytics.answer answer
    ON answer.tenant_pub_id=selected.tenant_pub_id
   AND answer.run_pub_id=selected.run_pub_id
  WHERE answer.project_pub_id IS DISTINCT FROM selected.project_pub_id
  UNION ALL
  SELECT DISTINCT 'answer_analysis_tenant_mismatch',
         selected_answer.tenant_id,selected_answer.project_id,analysis.pub_id
  FROM selected_answers selected_answer
  JOIN analytics.answer_analysis analysis
    ON analysis.answer_pub_id=selected_answer.answer_pub_id
  WHERE analysis.tenant_pub_id<>selected_answer.tenant_pub_id
  UNION ALL
  SELECT DISTINCT 'evidence_relation_tenant_mismatch',
         selected_answer.tenant_id,selected_answer.project_id,asset.pub_id
  FROM selected_answers selected_answer
  JOIN evidence.evidence_relation relation
    ON relation.from_pub_id=selected_answer.answer_pub_id
   AND relation.tenant_pub_id<>selected_answer.tenant_pub_id
  JOIN evidence.evidence_asset asset
    ON asset.tenant_pub_id=relation.tenant_pub_id
   AND asset.pub_id=relation.to_pub_id
  UNION ALL
  SELECT DISTINCT 'evidence_asset_tenant_mismatch',
         selected_answer.tenant_id,selected_answer.project_id,asset.pub_id
  FROM selected_answers selected_answer
  JOIN evidence.evidence_relation relation
    ON relation.tenant_pub_id=selected_answer.tenant_pub_id
   AND relation.from_pub_id=selected_answer.answer_pub_id
  JOIN evidence.evidence_asset asset
    ON asset.pub_id=relation.to_pub_id
   AND asset.tenant_pub_id<>relation.tenant_pub_id
  UNION ALL
  SELECT DISTINCT 'evidence_asset_project_mismatch',
         selected_answer.tenant_id,selected_answer.project_id,asset.pub_id
  FROM selected_answers selected_answer
  JOIN evidence.evidence_relation relation
    ON relation.tenant_pub_id=selected_answer.tenant_pub_id
   AND relation.from_pub_id=selected_answer.answer_pub_id
  JOIN evidence.evidence_asset asset
    ON asset.tenant_pub_id=relation.tenant_pub_id
   AND asset.pub_id=relation.to_pub_id
  WHERE asset.project_pub_id IS DISTINCT FROM selected_answer.project_pub_id
)
SELECT category,tenant_id,project_id,count(*) AS orphan_count,
       (array_agg(fact_pub_id ORDER BY fact_pub_id))[1:%(sample_limit)s] AS sample_pub_ids
FROM orphan_facts
GROUP BY category,tenant_id,project_id
ORDER BY tenant_id,project_id,category
""".strip()


@dataclass(frozen=True)
class TargetPlan:
    name: str
    table_name: str
    alias: str
    target_cte: str
    snapshot_sql: str
    lock_sql: str
    update_sql: str


def _target_plan(name: str, table_name: str, alias: str, target_cte: str) -> TargetPlan:
    snapshot_sql = f"""
/* collection-surface-backfill:snapshot:{name} */
{SELECTED_RUNS_CTE},
selected_targets AS ({target_cte})
SELECT tenant_id,project_id,target_pub_id,
       collection_surface,surface_assignment_basis,legacy_contract_version
FROM selected_targets
ORDER BY tenant_id,project_id,target_pub_id
""".strip()
    lock_sql = f"""
/* collection-surface-backfill:lock:{name} */
{SELECTED_RUNS_CTE},
selected_targets AS ({target_cte})
SELECT target.id
FROM {table_name} target
JOIN selected_targets selected ON selected.target_id=target.id
ORDER BY target.id
FOR UPDATE OF target
""".strip()
    update_sql = f"""
/* collection-surface-backfill:update:{name} */
{BATCH_SELECTED_RUNS_CTE},
selected_targets AS ({target_cte})
UPDATE {table_name} target
SET collection_surface=%(collection_surface)s,
    surface_assignment_basis=%(surface_assignment_basis)s,
    legacy_contract_version=%(legacy_contract_version)s
FROM selected_targets selected
WHERE selected.target_id=target.id
  AND (target.collection_surface IS NULL
       OR target.collection_surface=%(collection_surface)s)
  AND (target.surface_assignment_basis IS NULL
       OR target.surface_assignment_basis=%(surface_assignment_basis)s)
  AND (target.legacy_contract_version IS NULL
       OR target.legacy_contract_version=%(legacy_contract_version)s)
  AND NOT (
    target.collection_surface=%(collection_surface)s
    AND target.surface_assignment_basis=%(surface_assignment_basis)s
    AND target.legacy_contract_version=%(legacy_contract_version)s
  )
""".strip()
    return TargetPlan(
        name=name,
        table_name=table_name,
        alias=alias,
        target_cte=target_cte,
        snapshot_sql=snapshot_sql,
        lock_sql=lock_sql,
        update_sql=update_sql,
    )


_RUN_TARGETS = """
SELECT DISTINCT ON (run.id)
       run.id AS target_id,run.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       run.collection_surface,run.surface_assignment_basis,run.legacy_contract_version
FROM selected_runs selected
JOIN platform.collection_run run ON run.id=selected.run_id
ORDER BY run.id,selected.tenant_id,selected.project_id
""".strip()

_TASK_TARGETS = """
SELECT DISTINCT ON (task.id)
       task.id AS target_id,task.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       task.collection_surface,task.surface_assignment_basis,task.legacy_contract_version
FROM selected_runs selected
JOIN platform.collection_task task
  ON task.run_id=selected.run_id AND task.tenant_id=selected.tenant_id
ORDER BY task.id,selected.tenant_id,selected.project_id
""".strip()

_ANSWER_TARGETS = """
SELECT DISTINCT ON (answer.id)
       answer.id AS target_id,answer.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       answer.collection_surface,answer.surface_assignment_basis,answer.legacy_contract_version
FROM selected_runs selected
JOIN analytics.answer answer
  ON answer.tenant_pub_id=selected.tenant_pub_id
 AND answer.run_pub_id=selected.run_pub_id
 AND answer.project_pub_id=selected.project_pub_id
ORDER BY answer.id,selected.tenant_id,selected.project_id
""".strip()

_ANSWER_ANALYSIS_TARGETS = """
SELECT DISTINCT ON (analysis.id)
       analysis.id AS target_id,analysis.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       analysis.collection_surface,analysis.surface_assignment_basis,
       analysis.legacy_contract_version
FROM selected_runs selected
JOIN analytics.answer answer
  ON answer.tenant_pub_id=selected.tenant_pub_id
 AND answer.run_pub_id=selected.run_pub_id
 AND answer.project_pub_id=selected.project_pub_id
JOIN analytics.answer_analysis analysis
  ON analysis.tenant_pub_id=answer.tenant_pub_id
 AND analysis.answer_pub_id=answer.pub_id
ORDER BY analysis.id,selected.tenant_id,selected.project_id
""".strip()

_ANALYSIS_JOB_TARGETS = """
SELECT DISTINCT ON (job.id)
       job.id AS target_id,job.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       job.collection_surface,job.surface_assignment_basis,job.legacy_contract_version
FROM selected_runs selected
JOIN platform.analysis_job job
  ON job.run_id=selected.run_id AND job.tenant_id=selected.tenant_id
ORDER BY job.id,selected.tenant_id,selected.project_id
""".strip()

_EVIDENCE_ASSET_TARGETS = """
SELECT DISTINCT ON (asset.id)
       asset.id AS target_id,asset.pub_id AS target_pub_id,
       selected.tenant_id,selected.project_id,
       asset.collection_surface,asset.surface_assignment_basis,asset.legacy_contract_version
FROM selected_runs selected
JOIN analytics.answer answer
  ON answer.tenant_pub_id=selected.tenant_pub_id
 AND answer.run_pub_id=selected.run_pub_id
 AND answer.project_pub_id=selected.project_pub_id
JOIN evidence.evidence_relation relation
  ON relation.tenant_pub_id=answer.tenant_pub_id
 AND relation.from_pub_id=answer.pub_id
JOIN evidence.evidence_asset asset
  ON asset.tenant_pub_id=relation.tenant_pub_id
 AND asset.pub_id=relation.to_pub_id
 AND asset.project_pub_id=selected.project_pub_id
ORDER BY asset.id,selected.tenant_id,selected.project_id
""".strip()

TARGET_PLANS: tuple[TargetPlan, ...] = (
    _target_plan("collection_run", "platform.collection_run", "run", _RUN_TARGETS),
    _target_plan("collection_task", "platform.collection_task", "task", _TASK_TARGETS),
    _target_plan("answer", "analytics.answer", "answer", _ANSWER_TARGETS),
    _target_plan(
        "answer_analysis",
        "analytics.answer_analysis",
        "analysis",
        _ANSWER_ANALYSIS_TARGETS,
    ),
    _target_plan("analysis_job", "platform.analysis_job", "job", _ANALYSIS_JOB_TARGETS),
    _target_plan(
        "evidence_asset",
        "evidence.evidence_asset",
        "asset",
        _EVIDENCE_ASSET_TARGETS,
    ),
)

AUDIT_INSERT_SQL = """
/* collection-surface-backfill:audit-insert */
INSERT INTO platform.collection_surface_backfill_run
  (id,pub_id,tenant_id,project_id,execution_mode,state,
   selector_version,selector_hash,batch_key,idempotency_key,batch_size,
   checkpoint_run_pub_id,collection_surface,surface_assignment_basis,
   legacy_contract_version,candidate_count,assigned_count,
   already_consistent_count,conflict_count,orphan_count,excluded_count,
   sample_fact_pub_ids_json,requested_by_pub_id,started_at,completed_at,error_code)
VALUES
  (%(id)s,%(pub_id)s,%(tenant_id)s,%(project_id)s,'apply','completed',
   %(selector_version)s,%(selector_hash)s,%(batch_key)s,%(idempotency_key)s,
   %(batch_size)s,%(checkpoint_run_pub_id)s,%(collection_surface)s,
   %(surface_assignment_basis)s,%(legacy_contract_version)s,%(candidate_count)s,
   %(assigned_count)s,%(already_consistent_count)s,%(conflict_count)s,
   %(orphan_count)s,%(excluded_count)s,%(sample_fact_pub_ids_json)s::jsonb,
   %(requested_by_pub_id)s,now(),now(),NULL)
ON CONFLICT (tenant_id,project_id,idempotency_key) DO NOTHING
""".strip()

_REQUIRED_COLUMNS: Mapping[tuple[str, str], frozenset[str]] = {
    ("platform", "tenant"): frozenset({"id", "pub_id"}),
    ("platform", "project"): frozenset({"id", "pub_id", "tenant_id"}),
    ("platform", "collection_run"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_id",
            "project_id",
            "workflow_id",
            "source",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("platform", "collection_task"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_id",
            "run_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("analytics", "answer"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_pub_id",
            "project_pub_id",
            "run_pub_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("analytics", "answer_analysis"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_pub_id",
            "answer_pub_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("platform", "analysis_job"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_id",
            "run_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("evidence", "evidence_relation"): frozenset({"tenant_pub_id", "from_pub_id", "to_pub_id"}),
    ("evidence", "evidence_asset"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_pub_id",
            "project_pub_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
        }
    ),
    ("integration", "legacy_id_map"): frozenset(
        {"run_id", "source_system", "entity_type", "target_pub_id", "state"}
    ),
    ("integration", "migration_run"): frozenset({"id", "source_system", "state"}),
    ("platform", "collection_surface_backfill_run"): frozenset(
        {
            "id",
            "pub_id",
            "tenant_id",
            "project_id",
            "execution_mode",
            "state",
            "selector_version",
            "selector_hash",
            "batch_key",
            "idempotency_key",
            "batch_size",
            "checkpoint_run_pub_id",
            "collection_surface",
            "surface_assignment_basis",
            "legacy_contract_version",
            "candidate_count",
            "assigned_count",
            "already_consistent_count",
            "conflict_count",
            "orphan_count",
            "excluded_count",
            "sample_fact_pub_ids_json",
            "requested_by_pub_id",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
            "error_code",
        }
    ),
}


class QueryResult(Protocol):
    rowcount: int

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class BackfillConnection(Protocol):
    def execute(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
    ) -> QueryResult: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class SurfaceBackfillError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error_code": self.code, "details": self.details}


@dataclass(frozen=True)
class BackfillRequest:
    tenant_pub_id: str = ""
    apply: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE
    expected_selection_hash: str | None = None
    confirm_token: str | None = None
    requested_by_pub_id: str | None = None
    batch_key: str | None = None

    @property
    def mode(self) -> str:
        return "apply" if self.apply else "dry_run"


def confirmation_token(selection_hash: str) -> str:
    material = (
        f"apply|{SELECTOR_VERSION}|{selection_hash}|{COLLECTION_SURFACE}|"
        f"{SURFACE_ASSIGNMENT_BASIS}|{LEGACY_CONTRACT_VERSION}"
    )
    return f"APPLY-{hashlib.sha256(material.encode()).hexdigest()}"


def _advisory_lock_key() -> int:
    digest = hashlib.sha256(SELECTOR_VERSION.encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


def _rows(result: QueryResult) -> list[Mapping[str, Any]]:
    return list(result.fetchall())


def _ensure_schema(connection: BackfillConnection) -> None:
    rows = _rows(connection.execute(SCHEMA_CHECK_SQL))
    actual = {
        (str(row["table_schema"]), str(row["table_name"]), str(row["column_name"])) for row in rows
    }
    missing = sorted(
        f"{schema}.{table}.{column}"
        for (schema, table), columns in _REQUIRED_COLUMNS.items()
        for column in columns
        if (schema, table, column) not in actual
    )
    if missing:
        raise SurfaceBackfillError(
            "surface_backfill_schema_not_ready",
            "required surface backfill schema has not been migrated",
            missing_columns=missing,
        )


def _project_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row["tenant_id"]), str(row["project_id"])


def _classification(row: Mapping[str, Any]) -> str:
    values = (
        row.get("collection_surface"),
        row.get("surface_assignment_basis"),
        row.get("legacy_contract_version"),
    )
    desired = (COLLECTION_SURFACE, SURFACE_ASSIGNMENT_BASIS, LEGACY_CONTRACT_VERSION)
    if values == desired:
        return "consistent"
    if any(
        value is not None and value != expected
        for value, expected in zip(values, desired, strict=True)
    ):
        return "conflict"
    return "pending"


def _selection_hash(
    selected_runs: Sequence[Mapping[str, Any]],
    *,
    tenant_pub_id: str,
) -> str:
    payload = {
        "selector_version": SELECTOR_VERSION,
        "selector_definition_hash": SELECTOR_DEFINITION_HASH,
        "tenant_pub_id": tenant_pub_id,
        "assignment": {
            "collection_surface": COLLECTION_SURFACE,
            "surface_assignment_basis": SURFACE_ASSIGNMENT_BASIS,
            "legacy_contract_version": LEGACY_CONTRACT_VERSION,
        },
        "runs": [
            [str(row["tenant_pub_id"]), str(row["project_pub_id"]), str(row["run_pub_id"])]
            for row in selected_runs
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _new_project_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": run["tenant_id"],
        "tenant_pub_id": str(run["tenant_pub_id"]),
        "project_id": run["project_id"],
        "project_pub_id": str(run["project_pub_id"]),
        "run_pub_ids": [],
        "table_counts": {},
        "candidate_count": 0,
        "pending_count": 0,
        "consistent_count": 0,
        "conflict_count": 0,
        "orphan_count": 0,
        "excluded_count": 0,
        "samples": {},
    }


def _collect_snapshot(
    connection: BackfillConnection,
    *,
    tenant_id: Any,
    tenant_pub_id: str,
) -> dict[str, Any]:
    scope_params = {"tenant_id": tenant_id}
    selected_runs = _rows(connection.execute(SELECTED_RUNS_SQL, scope_params))
    projects: dict[tuple[str, str], dict[str, Any]] = {}
    for run in selected_runs:
        key = _project_key(run)
        project = projects.setdefault(key, _new_project_summary(run))
        project["run_pub_ids"].append(str(run["run_pub_id"]))

    global_tables: dict[str, dict[str, int]] = {}
    fingerprint_rows: list[list[str]] = []
    for plan in TARGET_PLANS:
        target_rows = _rows(connection.execute(plan.snapshot_sql, scope_params))
        counts = {"candidate": 0, "pending": 0, "consistent": 0, "conflict": 0}
        for row in target_rows:
            key = _project_key(row)
            if key not in projects:
                raise SurfaceBackfillError(
                    "surface_backfill_unattributed_fact",
                    "selected fact has no selected project",
                    target=plan.name,
                    fact_pub_id=str(row["target_pub_id"]),
                )
            classification = _classification(row)
            counts["candidate"] += 1
            counts[classification] += 1
            project = projects[key]
            per_table = project["table_counts"].setdefault(
                plan.name,
                {"candidate": 0, "pending": 0, "consistent": 0, "conflict": 0},
            )
            per_table["candidate"] += 1
            per_table[classification] += 1
            project["candidate_count"] += 1
            project[f"{classification}_count"] += 1
            samples = project["samples"].setdefault(plan.name, [])
            if len(samples) < SAMPLE_LIMIT_PER_TARGET:
                samples.append(str(row["target_pub_id"]))
            fingerprint_rows.append(
                [
                    plan.name,
                    str(row["target_pub_id"]),
                    str(row.get("collection_surface") or ""),
                    str(row.get("surface_assignment_basis") or ""),
                    str(row.get("legacy_contract_version") or ""),
                ]
            )
        global_tables[plan.name] = counts

    orphan_rows = _rows(
        connection.execute(
            ORPHAN_COUNTS_SQL,
            {"tenant_id": tenant_id, "sample_limit": SAMPLE_LIMIT_PER_TARGET},
        )
    )
    for row in orphan_rows:
        key = _project_key(row)
        if key not in projects:
            continue
        count = int(row["orphan_count"])
        projects[key]["orphan_count"] += count
        samples = [str(value) for value in (row.get("sample_pub_ids") or [])]
        projects[key]["samples"][f"orphan:{row['category']}"] = samples[:SAMPLE_LIMIT_PER_TARGET]
        fingerprint_rows.append(["orphan", str(row["category"]), str(count)])

    for row in _rows(connection.execute(EXCLUDED_COUNTS_SQL, scope_params)):
        key = _project_key(row)
        if key in projects:
            projects[key]["excluded_count"] = int(row["excluded_count"])

    selection_hash = _selection_hash(selected_runs, tenant_pub_id=tenant_pub_id)
    fingerprint_payload = {
        "selection_hash": selection_hash,
        "facts": sorted(fingerprint_rows),
        "orphans": sum(project["orphan_count"] for project in projects.values()),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "selected_runs": selected_runs,
        "selection_hash": selection_hash,
        "fingerprint": fingerprint,
        "projects": projects,
        "table_counts": global_tables,
    }


def _validate_request(request: BackfillRequest) -> None:
    if (
        not request.tenant_pub_id
        or len(request.tenant_pub_id) > 30
        or not _SAFE_AUDIT_VALUE.fullmatch(request.tenant_pub_id)
    ):
        raise SurfaceBackfillError(
            "surface_backfill_tenant_required",
            "a non-sensitive tenant public identifier is required",
        )
    if not 1 <= request.batch_size <= MAX_BATCH_SIZE:
        raise SurfaceBackfillError(
            "surface_backfill_invalid_batch_size",
            f"batch size must be between 1 and {MAX_BATCH_SIZE}",
        )
    if not request.apply:
        if any(
            value is not None
            for value in (
                request.expected_selection_hash,
                request.confirm_token,
                request.requested_by_pub_id,
                request.batch_key,
            )
        ):
            raise SurfaceBackfillError(
                "surface_backfill_dry_run_apply_arguments",
                "apply confirmation arguments are forbidden in dry-run mode",
            )
        return
    required = {
        "expected_selection_hash": request.expected_selection_hash,
        "confirm_token": request.confirm_token,
        "requested_by_pub_id": request.requested_by_pub_id,
        "batch_key": request.batch_key,
    }
    missing = sorted(key for key, value in required.items() if not value)
    if missing:
        raise SurfaceBackfillError(
            "surface_backfill_apply_confirmation_required",
            "apply requires selection hash, confirmation token, requester, and batch key",
            missing=missing,
        )
    assert request.requested_by_pub_id is not None
    assert request.batch_key is not None
    if len(request.requested_by_pub_id) > 255 or not _SAFE_AUDIT_VALUE.fullmatch(
        request.requested_by_pub_id
    ):
        raise SurfaceBackfillError(
            "surface_backfill_invalid_requester",
            "requested-by must be a non-sensitive public identifier",
        )
    if len(request.batch_key) > 120 or not _SAFE_AUDIT_VALUE.fullmatch(request.batch_key):
        raise SurfaceBackfillError(
            "surface_backfill_invalid_batch_key",
            "batch key must be a stable non-sensitive identifier",
        )


def _public_summary(
    snapshot: Mapping[str, Any],
    *,
    mode: str,
    assigned_by_project: Mapping[tuple[str, str], int] | None = None,
    checkpoints: Mapping[tuple[str, str], str | None] | None = None,
) -> dict[str, Any]:
    assigned_by_project = assigned_by_project or {}
    checkpoints = checkpoints or {}
    projects_out: list[dict[str, Any]] = []
    for key in sorted(snapshot["projects"]):
        project = snapshot["projects"][key]
        projects_out.append(
            {
                "tenant_pub_id": project["tenant_pub_id"],
                "project_pub_id": project["project_pub_id"],
                "selected_run_count": len(project["run_pub_ids"]),
                "candidate_count": project["candidate_count"],
                "assigned_count": assigned_by_project.get(key, 0),
                "pending_count": project["pending_count"],
                "already_consistent_count": project["consistent_count"],
                "conflict_count": project["conflict_count"],
                "orphan_count": project["orphan_count"],
                "excluded_count": project["excluded_count"],
                "checkpoint_run_pub_id": checkpoints.get(key),
                "table_counts": project["table_counts"],
                "sample_fact_pub_ids": project["samples"],
            }
        )
    return {
        "ok": True,
        "mode": mode,
        "selector_version": SELECTOR_VERSION,
        "selector_definition_hash": SELECTOR_DEFINITION_HASH,
        "selection_hash": snapshot["selection_hash"],
        "confirmation_token": confirmation_token(str(snapshot["selection_hash"])),
        "collection_surface": COLLECTION_SURFACE,
        "surface_assignment_basis": SURFACE_ASSIGNMENT_BASIS,
        "legacy_contract_version": LEGACY_CONTRACT_VERSION,
        "selected_run_count": len(snapshot["selected_runs"]),
        "candidate_count": sum(
            project["candidate_count"] for project in snapshot["projects"].values()
        ),
        "assigned_count": sum(assigned_by_project.values()),
        "already_consistent_count": sum(
            project["consistent_count"] for project in snapshot["projects"].values()
        ),
        "conflict_count": sum(
            project["conflict_count"] for project in snapshot["projects"].values()
        ),
        "orphan_count": sum(project["orphan_count"] for project in snapshot["projects"].values()),
        "excluded_count": sum(
            project["excluded_count"] for project in snapshot["projects"].values()
        ),
        "table_counts": snapshot["table_counts"],
        "projects": projects_out,
    }


def _chunks(values: Sequence[str], size: int) -> Sequence[Sequence[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _audit_identifiers(idempotency_key: str) -> tuple[uuid.UUID, str]:
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"geo:{idempotency_key}")
    public_id = f"sfb_{hashlib.sha256(idempotency_key.encode()).hexdigest()[:26]}"
    return identifier, public_id


def _tenant_id(connection: BackfillConnection, tenant_pub_id: str) -> Any:
    rows = _rows(connection.execute(TENANT_ID_SQL, {"tenant_pub_id": tenant_pub_id}))
    if len(rows) != 1:
        raise SurfaceBackfillError(
            "surface_backfill_tenant_not_found",
            "tenant public identifier did not resolve uniquely",
            tenant_pub_id=tenant_pub_id,
        )
    return rows[0]["id"]


def _set_rls_context(
    connection: BackfillConnection,
    *,
    tenant_id: Any,
    tenant_pub_id: str,
) -> None:
    connection.execute(
        RLS_CONTEXT_SQL,
        {"tenant_id": str(tenant_id), "tenant_pub_id": tenant_pub_id},
    )


def _audit_sample_pub_ids(project: Mapping[str, Any]) -> list[str]:
    public_ids = {
        str(public_id) for samples in project["samples"].values() for public_id in samples
    }
    return sorted(public_ids)[:AUDIT_SAMPLE_LIMIT]


def run_collection_surface_backfill(
    connection: BackfillConnection,
    request: BackfillRequest | None = None,
) -> dict[str, Any]:
    """Inspect or atomically apply the historical surface assignment plan."""

    request = request or BackfillRequest()
    _validate_request(request)
    if not request.apply:
        try:
            connection.execute(DRY_RUN_ISOLATION_SQL)
            _ensure_schema(connection)
            tenant_id = _tenant_id(connection, request.tenant_pub_id)
            _set_rls_context(
                connection,
                tenant_id=tenant_id,
                tenant_pub_id=request.tenant_pub_id,
            )
            snapshot = _collect_snapshot(
                connection,
                tenant_id=tenant_id,
                tenant_pub_id=request.tenant_pub_id,
            )
            return _public_summary(snapshot, mode="dry_run")
        finally:
            connection.rollback()

    try:
        connection.execute(APPLY_ISOLATION_SQL)
        _ensure_schema(connection)
        tenant_id = _tenant_id(connection, request.tenant_pub_id)
        _set_rls_context(
            connection,
            tenant_id=tenant_id,
            tenant_pub_id=request.tenant_pub_id,
        )
        connection.execute(ADVISORY_LOCK_SQL, {"advisory_lock_key": _advisory_lock_key()})
        scope_params = {"tenant_id": tenant_id}
        before_lock = _collect_snapshot(
            connection,
            tenant_id=tenant_id,
            tenant_pub_id=request.tenant_pub_id,
        )
        for plan in TARGET_PLANS:
            connection.execute(plan.lock_sql, scope_params)
        locked = _collect_snapshot(
            connection,
            tenant_id=tenant_id,
            tenant_pub_id=request.tenant_pub_id,
        )
        if before_lock["fingerprint"] != locked["fingerprint"]:
            raise SurfaceBackfillError(
                "surface_backfill_snapshot_changed",
                "candidate facts changed while the apply lock set was acquired",
            )
        if not locked["selected_runs"]:
            raise SurfaceBackfillError(
                "surface_backfill_no_candidates",
                "selector returned no historical collection runs",
            )
        selection_hash = str(locked["selection_hash"])
        if request.expected_selection_hash != selection_hash:
            raise SurfaceBackfillError(
                "surface_backfill_selection_hash_mismatch",
                "apply selection hash does not match the locked snapshot",
                actual_selection_hash=selection_hash,
            )
        if request.confirm_token != confirmation_token(selection_hash):
            raise SurfaceBackfillError(
                "surface_backfill_confirmation_token_mismatch",
                "apply confirmation token does not match the locked selection",
            )
        conflict_count = sum(project["conflict_count"] for project in locked["projects"].values())
        orphan_count = sum(project["orphan_count"] for project in locked["projects"].values())
        if conflict_count or orphan_count:
            raise SurfaceBackfillError(
                "surface_backfill_conflict_or_orphan",
                "apply refused because conflicts or orphaned lineage were detected",
                conflict_count=conflict_count,
                orphan_count=orphan_count,
            )

        assert request.batch_key is not None
        assert request.requested_by_pub_id is not None
        assigned_by_project: dict[tuple[str, str], int] = {}
        checkpoints: dict[tuple[str, str], str | None] = {}
        for key in sorted(locked["projects"]):
            project = locked["projects"][key]
            run_pub_ids = sorted(str(value) for value in project["run_pub_ids"])
            assigned = 0
            checkpoint: str | None = None
            for batch in _chunks(run_pub_ids, request.batch_size):
                params: dict[str, Any] = {
                    "tenant_id": tenant_id,
                    "batch_run_pub_ids": list(batch),
                    "collection_surface": COLLECTION_SURFACE,
                    "surface_assignment_basis": SURFACE_ASSIGNMENT_BASIS,
                    "legacy_contract_version": LEGACY_CONTRACT_VERSION,
                }
                for plan in TARGET_PLANS:
                    assigned += connection.execute(plan.update_sql, params).rowcount
                checkpoint = batch[-1]
            assigned_by_project[key] = assigned
            checkpoints[key] = checkpoint
            if assigned != project["pending_count"]:
                raise SurfaceBackfillError(
                    "surface_backfill_update_count_mismatch",
                    "updated facts differ from the locked pending count",
                    tenant_pub_id=project["tenant_pub_id"],
                    project_pub_id=project["project_pub_id"],
                    expected=project["pending_count"],
                    actual=assigned,
                )

        for key in sorted(locked["projects"]):
            project = locked["projects"][key]
            idempotency_material = (
                f"{request.batch_key}|{key[0]}|{key[1]}|{selection_hash}|"
                f"{SELECTOR_VERSION}|{COLLECTION_SURFACE}"
            )
            idempotency_key = hashlib.sha256(idempotency_material.encode()).hexdigest()
            audit_id, audit_pub_id = _audit_identifiers(idempotency_key)
            connection.execute(
                AUDIT_INSERT_SQL,
                {
                    "id": audit_id,
                    "pub_id": audit_pub_id,
                    "tenant_id": project["tenant_id"],
                    "project_id": project["project_id"],
                    "selector_version": SELECTOR_VERSION,
                    "selector_hash": selection_hash,
                    "batch_key": request.batch_key,
                    "idempotency_key": idempotency_key,
                    "batch_size": request.batch_size,
                    "checkpoint_run_pub_id": checkpoints[key],
                    "collection_surface": COLLECTION_SURFACE,
                    "surface_assignment_basis": SURFACE_ASSIGNMENT_BASIS,
                    "legacy_contract_version": LEGACY_CONTRACT_VERSION,
                    "candidate_count": project["candidate_count"],
                    "assigned_count": assigned_by_project[key],
                    "already_consistent_count": project["consistent_count"],
                    "conflict_count": project["conflict_count"],
                    "orphan_count": project["orphan_count"],
                    "excluded_count": project["excluded_count"],
                    "sample_fact_pub_ids_json": json.dumps(
                        _audit_sample_pub_ids(project),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "requested_by_pub_id": request.requested_by_pub_id,
                },
            )
        connection.commit()
        return _public_summary(
            locked,
            mode="apply",
            assigned_by_project=assigned_by_project,
            checkpoints=checkpoints,
        )
    except Exception:
        connection.rollback()
        raise


SQL_PLAN: tuple[str, ...] = (
    SCHEMA_CHECK_SQL,
    DRY_RUN_ISOLATION_SQL,
    APPLY_ISOLATION_SQL,
    TENANT_ID_SQL,
    RLS_CONTEXT_SQL,
    ADVISORY_LOCK_SQL,
    SELECTED_RUNS_SQL,
    EXCLUDED_COUNTS_SQL,
    ORPHAN_COUNTS_SQL,
    *(plan.snapshot_sql for plan in TARGET_PLANS),
    *(plan.lock_sql for plan in TARGET_PLANS),
    *(plan.update_sql for plan in TARGET_PLANS),
    AUDIT_INSERT_SQL,
)
