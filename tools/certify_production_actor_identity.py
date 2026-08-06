from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
DEFAULT_OUTPUT = Path("tests/s04-evidence/production-actor-identity.json")
SOURCE_ROOT = Path("api/geo_platform")


@dataclass(frozen=True)
class ActorColumn:
    schema: str
    table: str
    column: str
    tenant_column: str
    tenant_key: Literal["id", "pub_id"]

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


ACTOR_COLUMNS = (
    ActorColumn("platform", "audit_log", "actor_pub_id", "tenant_id", "id"),
    ActorColumn("platform", "client_profile_version", "declared_by", "tenant_id", "id"),
    ActorColumn("platform", "asset_confirmation_version", "declared_by", "tenant_id", "id"),
    ActorColumn("platform", "capability_lease", "issued_by", "tenant_id", "id"),
    ActorColumn("platform", "credential_access_request", "requested_by", "tenant_id", "id"),
    ActorColumn("platform", "credential_access_approval", "approver_pub_id", "tenant_id", "id"),
    ActorColumn("platform", "session_health_check", "checked_by", "tenant_id", "id"),
    ActorColumn("evidence", "evidence_access_audit", "actor_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "report_version", "created_by_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "report_review", "reviewer_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "report_comment", "author_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "report_event", "actor_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "effect_retest", "recorded_by_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "data_export", "created_by_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("reporting", "report_delivery", "recipient_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("intelligence", "human_verdict", "reviewer_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("intelligence", "appeal", "submitted_by_pub_id", "tenant_pub_id", "pub_id"),
    ActorColumn("intelligence", "appeal", "resolved_by_pub_id", "tenant_pub_id", "pub_id"),
)

ALLOWED_SUBJECT_REFERENCES = {
    "identity/router.py": {242, 270, 568},
    "collection/customer_account_router.py": {123},
    "collection/governance_router.py": {391},
}


def environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://")


def source_subject_references() -> dict[str, list[int]]:
    references: dict[str, list[int]] = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        hits = [
            line_number
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if "principal.subject" in line
        ]
        if hits:
            references[str(path.relative_to(SOURCE_ROOT))] = hits
    return references


def actor_counts(connection: psycopg.Connection[Any], item: ActorColumn) -> dict[str, int]:
    tenant_join = (
        sql.SQL("tenant.id = target.{tenant_column}")
        if item.tenant_key == "id"
        else sql.SQL("tenant.pub_id = target.{tenant_column}")
    ).format(tenant_column=sql.Identifier(item.tenant_column))
    query = sql.SQL(
        """
        SELECT
          count(*) FILTER (WHERE target.{actor_column} IS NOT NULL),
          count(*) FILTER (
            WHERE target.{actor_column} IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM platform.membership membership
                JOIN platform.app_user app_user ON app_user.id=membership.user_id
                WHERE membership.tenant_id=tenant.id
                  AND app_user.subject=target.{actor_column}
                  AND app_user.subject<>app_user.pub_id
              )
          ),
          count(*) FILTER (
            WHERE target.{actor_column} IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM platform.membership membership
                JOIN platform.app_user app_user ON app_user.id=membership.user_id
                WHERE membership.tenant_id=tenant.id
                  AND app_user.pub_id=target.{actor_column}
              )
          )
        FROM {schema}.{table} target
        JOIN platform.tenant tenant ON {tenant_join}
        """
    ).format(
        actor_column=sql.Identifier(item.column),
        schema=sql.Identifier(item.schema),
        table=sql.Identifier(item.table),
        tenant_join=tenant_join,
    )
    row = connection.execute(query).fetchone()
    assert row is not None
    return {
        "non_null": int(row[0]),
        "legacy_subject_residue": int(row[1]),
        "known_platform_user_ids": int(row[2]),
        "system_or_historical_ids": int(row[0]) - int(row[2]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    values = environment(ENV_PATH)
    owner_dsn = psycopg_dsn(values["GEO_POSTGRES_DSN"])
    with psycopg.connect(owner_dsn) as connection:
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision_row is None:
            raise RuntimeError("schema revision unavailable")
        revision = str(revision_row[0])
        appeal_columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='intelligence'
                  AND table_name='appeal'
                  AND column_name IN (
                    'resolved_by_pub_id',
                    'resolution_rationale',
                    'resolved_at'
                  )
                """
            ).fetchall()
        }
        counts = {item.label: actor_counts(connection, item) for item in ACTOR_COLUMNS}

    source_references = source_subject_references()
    expected_references = {
        path: sorted(lines) for path, lines in ALLOWED_SUBJECT_REFERENCES.items()
    }
    residue_total = sum(item["legacy_subject_residue"] for item in counts.values())
    passed = (
        revision == "s04_0029"
        and residue_total == 0
        and appeal_columns == {"resolved_by_pub_id", "resolution_rationale", "resolved_at"}
        and source_references == expected_references
    )
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if passed else "failed",
        "schema_revision": revision,
        "actor_contract": "same-tenant platform app_user.pub_id",
        "legacy_external_subject_residue": residue_total,
        "columns": counts,
        "appeal_resolution_audit_columns": sorted(appeal_columns),
        "source_boundary": {
            "result": "passed" if source_references == expected_references else "failed",
            "principal_subject_references": source_references,
            "allowed_purposes": [
                "identity lookup and projection",
                "legacy-session self-approval compatibility",
            ],
        },
        "identifiers_emitted": False,
        "secrets_emitted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "schema_revision": revision,
                "legacy_external_subject_residue": residue_total,
                "actor_columns": len(counts),
                "secrets_emitted": False,
            }
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
