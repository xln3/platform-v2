"""Force tenant RLS across platform and S02 business schemas.

Revision ID: s04_0007
Revises: s04_0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0007"
down_revision: str | Sequence[str] | None = "s04_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLATFORM_RLS = r"""
DO $$
DECLARE
  target record;
BEGIN
  FOR target IN
    SELECT c.table_name
    FROM information_schema.columns c
    WHERE c.table_schema = 'platform' AND c.column_name = 'tenant_id'
  LOOP
    EXECUTE format('ALTER TABLE platform.%I ENABLE ROW LEVEL SECURITY', target.table_name);
    EXECUTE format('ALTER TABLE platform.%I FORCE ROW LEVEL SECURITY', target.table_name);
    IF NOT EXISTS (
      SELECT 1
      FROM pg_policies
      WHERE schemaname = 'platform'
        AND tablename = target.table_name
        AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON platform.%I '
        'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
        'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
        target.table_name
      );
    END IF;
  END LOOP;
END $$;
"""

_S02_RLS = r"""
DO $$
DECLARE
  target record;
BEGIN
  FOR target IN
    SELECT c.table_schema, c.table_name
    FROM information_schema.columns c
    WHERE c.table_schema IN ('analytics', 'evidence', 'reporting', 'intelligence')
      AND c.column_name = 'tenant_pub_id'
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
      target.table_schema,
      target.table_name
    );
    EXECUTE format(
      'ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
      target.table_schema,
      target.table_name
    );
    IF NOT EXISTS (
      SELECT 1
      FROM pg_policies
      WHERE schemaname = target.table_schema
        AND tablename = target.table_name
        AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I.%I '
        'USING (tenant_pub_id = NULLIF(current_setting(''app.tenant_pub_id'', true), '''')) '
        'WITH CHECK (tenant_pub_id = NULLIF(current_setting(''app.tenant_pub_id'', true), ''''))',
        target.table_schema,
        target.table_name
      );
    END IF;
  END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(_PLATFORM_RLS)
    op.execute(_S02_RLS)


def downgrade() -> None:
    op.execute(
        r"""
        DO $$
        DECLARE
          target record;
        BEGIN
          FOR target IN
            SELECT c.table_schema, c.table_name
            FROM information_schema.columns c
            WHERE (
              c.table_schema = 'platform' AND c.column_name = 'tenant_id'
            ) OR (
              c.table_schema IN ('analytics', 'evidence', 'reporting', 'intelligence')
              AND c.column_name = 'tenant_pub_id'
            )
          LOOP
            EXECUTE format(
              'ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
              target.table_schema,
              target.table_name
            );
            IF target.table_schema <> 'platform' THEN
              EXECUTE format(
                'DROP POLICY IF EXISTS tenant_isolation ON %I.%I',
                target.table_schema,
                target.table_name
              );
              EXECUTE format(
                'ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                target.table_schema,
                target.table_name
              );
            END IF;
          END LOOP;
        END $$;
        """
    )
