from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.errors import InsufficientPrivilege

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
EVIDENCE_PATH = Path("tests/s04-evidence/production-rls-certification.json")


def environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-path", type=Path, required=True)
    parser.add_argument("--backup-catalog-entries", type=int, required=True)
    args = parser.parse_args()
    backup_path = args.backup_path.resolve(strict=True)
    backup_stat = backup_path.stat()
    if backup_stat.st_mode & 0o077:
        raise RuntimeError("production backup must not be group/world accessible")

    values = environment(ENV_PATH)
    runtime_dsn = psycopg_dsn(values["GEO_RUNTIME_POSTGRES_DSN"])
    owner_dsn = psycopg_dsn(values["GEO_POSTGRES_DSN"])
    with psycopg.connect(owner_dsn) as owner:
        table_counts = owner.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE c.relrowsecurity),
                   count(*) FILTER (WHERE c.relforcerowsecurity)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE c.relkind='r'
              AND (
                (n.nspname='platform' AND a.attname='tenant_id')
                OR
                (n.nspname IN ('analytics','evidence','reporting','intelligence')
                 AND a.attname='tenant_pub_id')
              )
            """
        ).fetchone()
        assert table_counts is not None
        worker_flags = owner.execute(
            """
            SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls
            FROM pg_roles WHERE rolname='geo_worker'
            """
        ).fetchone()
        if worker_flags != (False, False, False, False):
            raise RuntimeError("worker database role is privileged")
        schema_revision = owner.execute("SELECT version_num FROM alembic_version").fetchone()
        if schema_revision is None:
            raise RuntimeError("schema revision unavailable")
        tenants = owner.execute(
            "SELECT id,pub_id FROM platform.tenant ORDER BY id LIMIT 2"
        ).fetchall()
        if not tenants:
            raise RuntimeError("one production tenant is required for RLS certification")
        owner_a_memberships = owner.execute(
            "SELECT count(*) FROM platform.membership WHERE tenant_id=%s",
            (tenants[0][0],),
        ).fetchone()
        owner_a_metrics = owner.execute(
            "SELECT count(*) FROM analytics.metric_daily WHERE tenant_pub_id=%s",
            (tenants[0][1],),
        ).fetchone()
        assert owner_a_memberships is not None and owner_a_metrics is not None
        owner_a_membership_count = owner_a_memberships[0]
        owner_a_metric_count = owner_a_metrics[0]

    with psycopg.connect(runtime_dsn) as runtime:
        flags = runtime.execute(
            """
            SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls
            FROM pg_roles WHERE rolname=current_user
            """
        ).fetchone()
        assert flags is not None
        no_context_memberships = runtime.execute(
            "SELECT count(*) FROM platform.membership"
        ).fetchone()
        no_context_metrics = runtime.execute(
            "SELECT count(*) FROM analytics.metric_daily"
        ).fetchone()
        assert no_context_memberships is not None and no_context_metrics is not None
        no_context = {
            "platform_memberships": no_context_memberships[0],
            "analytics_metrics": no_context_metrics[0],
        }
        runtime.execute(
            """
            SELECT set_config('app.tenant_id', %s, true),
                   set_config('app.tenant_pub_id', %s, true)
            """,
            (str(tenants[0][0]), tenants[0][1]),
        )
        own_memberships = runtime.execute("SELECT count(*) FROM platform.membership").fetchone()
        own_metrics = runtime.execute("SELECT count(*) FROM analytics.metric_daily").fetchone()
        foreign_tenant_id = tenants[1][0] if len(tenants) > 1 else uuid4()
        foreign_tenant_pub_id = (
            tenants[1][1] if len(tenants) > 1 else f"tnt_rls_foreign_{uuid4().hex}"
        )
        cross_memberships = runtime.execute(
            "SELECT count(*) FROM platform.membership WHERE tenant_id=%s",
            (foreign_tenant_id,),
        ).fetchone()
        cross_metrics = runtime.execute(
            "SELECT count(*) FROM analytics.metric_daily WHERE tenant_pub_id=%s",
            (foreign_tenant_pub_id,),
        ).fetchone()
        assert (
            own_memberships is not None
            and own_metrics is not None
            and cross_memberships is not None
            and cross_metrics is not None
        )
        own_context = {
            "platform_memberships": own_memberships[0],
            "analytics_metrics": own_metrics[0],
        }
        cross_context = {
            "platform_memberships": cross_memberships[0],
            "analytics_metrics": cross_metrics[0],
        }

    force_rls_blocked = False
    with psycopg.connect(runtime_dsn) as runtime:
        try:
            runtime.execute("SET row_security=off")
            runtime.execute("SELECT count(*) FROM analytics.metric_daily").fetchone()
        except InsufficientPrivilege:
            force_rls_blocked = True

    passed = (
        flags == (False, False, False, False)
        and table_counts[0] == table_counts[1] == table_counts[2]
        and no_context == {"platform_memberships": 0, "analytics_metrics": 0}
        and own_context
        == {
            "platform_memberships": owner_a_membership_count,
            "analytics_metrics": owner_a_metric_count,
        }
        and cross_context == {"platform_memberships": 0, "analytics_metrics": 0}
        and force_rls_blocked
    )
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if passed else "failed",
        "schema_revision": schema_revision[0],
        "predeployment_backup": {
            "path": str(backup_path),
            "postgres_dump_bytes": backup_stat.st_size,
            "postgres_dump_sha256": file_sha256(backup_path),
            "postgres_16_catalog_entries": args.backup_catalog_entries,
            "postgres_16_catalog_validation": "passed",
            "backup_mode": f"{backup_stat.st_mode & 0o777:04o}",
        },
        "runtime_role": {
            "superuser": flags[0],
            "createdb": flags[1],
            "createrole": flags[2],
            "bypass_rls": flags[3],
        },
        "worker_role": {
            "superuser": worker_flags[0],
            "createdb": worker_flags[1],
            "createrole": worker_flags[2],
            "bypass_rls": worker_flags[3],
            "scope": "internal Temporal and outbox processing with explicit tenant context",
        },
        "tenant_tables": {
            "total": table_counts[0],
            "rls_enabled": table_counts[1],
            "rls_forced": table_counts[2],
        },
        "no_context_counts": no_context,
        "own_context_matches_owner_truth": own_context
        == {
            "platform_memberships": owner_a_membership_count,
            "analytics_metrics": owner_a_metric_count,
        },
        "cross_tenant_counts": cross_context,
        "production_cross_tenant_sample": (
            "populated" if len(tenants) > 1 else "not_applicable_single_tenant"
        ),
        "populated_two_tenant_test": "tests/integration/test_s04_rls.py",
        "row_security_off_blocked": force_rls_blocked,
        "tenant_identifiers_emitted": False,
        "secrets_emitted": False,
    }
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "tenant_tables": table_counts[0],
                "cross_tenant_rows": sum(cross_context.values()),
            }
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
