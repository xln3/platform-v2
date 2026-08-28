from __future__ import annotations

import os
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_every_tenant_metrics_v2_table_forces_exact_pub_id_rls() -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        missing = connection.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_pub_id'
            WHERE n.nspname='analytics' AND c.relname LIKE '%_v2'
              AND c.relkind='r' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
            """
        ).fetchall()
        assert missing == []
        policies = connection.execute(
            """
            SELECT tablename,qual,with_check
            FROM pg_policies
            WHERE schemaname='analytics' AND tablename LIKE '%_v2'
              AND policyname='tenant_isolation'
            """
        ).fetchall()
        assert policies
        for _table, using, check in policies:
            assert "app.tenant_pub_id" in str(using)
            assert using == check


def test_metrics_v2_rls_hides_same_project_and_object_names_across_tenants() -> None:
    suffix = uuid4().hex
    role = f"metrics_v2_rls_{suffix}"
    tenant_a = f"tnt_a_{suffix}"
    tenant_b = f"tnt_b_{suffix}"
    with psycopg.connect(POSTGRES_DSN) as connection, connection.transaction():
        connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        connection.execute(f'GRANT USAGE ON SCHEMA analytics TO "{role}"')
        connection.execute(f'GRANT SELECT ON analytics.metric_recompute_job_v2 TO "{role}"')
        for tenant in (tenant_a, tenant_b):
            connection.execute(
                """
                INSERT INTO analytics.metric_recompute_job_v2
                  (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,status,
                   idempotency_key,requested_by)
                VALUES (%s,%s,'prj_same','{}',%s,'pending',%s,'actor')
                """,
                (
                    f"mrj_{tenant}",
                    tenant,
                    ("a" if tenant == tenant_a else "b") * 64,
                    sha256(f"{tenant}:idempotency".encode()).hexdigest(),
                ),
            )
        connection.execute(f'SET LOCAL ROLE "{role}"')
        assert connection.execute(
            "SELECT count(*) FROM analytics.metric_recompute_job_v2"
        ).fetchone() == (0,)
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant_a,))
        assert connection.execute(
            "SELECT tenant_pub_id FROM analytics.metric_recompute_job_v2"
        ).fetchall() == [(tenant_a,)]


def test_api_manual_override_uses_only_the_constrained_command_view() -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        privileges = connection.execute(
            """
            SELECT
              has_table_privilege(
                'geo_api','analytics.semantic_decision_override_command_v2','SELECT'
              ),
              has_table_privilege(
                'geo_api','analytics.semantic_decision_override_command_v2','INSERT'
              ),
              has_table_privilege(
                'geo_api','analytics.semantic_decision_attempt_v2','INSERT'
              ),
              has_table_privilege(
                'geo_api','analytics.semantic_decision_record_v2','INSERT'
              ),
              has_table_privilege(
                'geo_api','analytics.semantic_decision_job_v2','UPDATE'
              ),
              has_function_privilege(
                'geo_api','analytics.metrics_v2_create_override_command()','EXECUTE'
              )
            """
        ).fetchone()
    assert privileges == (True, True, False, False, False, False)
