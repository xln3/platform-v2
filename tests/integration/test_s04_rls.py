import os
from uuid import uuid4

import psycopg

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_all_tenant_business_tables_force_rls_and_filter_s02_rows() -> None:
    suffix = uuid4().hex
    role = f"s04_rls_probe_{suffix}"
    tenant_a = f"tnt_rls_a_{suffix}"
    tenant_b = f"tnt_rls_b_{suffix}"
    with psycopg.connect(POSTGRES_DSN) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT n.nspname, c.relname
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
                      AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
                    """
                )
                assert cursor.fetchall() == []
                cursor.execute(
                    """
                    INSERT INTO analytics.metric_daily
                      (tenant_pub_id,project_pub_id,metric_date,metric_name,dimensions,
                       dimensions_hash,value,numerator,denominator,state,metric_version,
                       scorer_version,trace_token)
                    VALUES
                      (%s,'prj_rls','2026-07-25','mention_rate','{}','a',1,1,1,'ready','v1','v1','a'),
                      (%s,'prj_rls','2026-07-25','mention_rate','{}','b',1,1,1,'ready','v1','v1','b')
                    """,
                    (tenant_a, tenant_b),
                )
                cursor.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                cursor.execute(f'GRANT USAGE ON SCHEMA analytics TO "{role}"')
                cursor.execute(f'GRANT SELECT ON analytics.metric_daily TO "{role}"')
                cursor.execute(f'SET LOCAL ROLE "{role}"')
                cursor.execute("SELECT count(*) FROM analytics.metric_daily")
                assert cursor.fetchone() == (0,)
                cursor.execute(
                    "SELECT set_config('app.tenant_pub_id', %s, true)",
                    (tenant_a,),
                )
                cursor.execute(
                    "SELECT tenant_pub_id FROM analytics.metric_daily ORDER BY tenant_pub_id"
                )
                assert cursor.fetchall() == [(tenant_a,)]
