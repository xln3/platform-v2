"""Persist frozen report facts and append-only effect retests.

Revision ID: s04_0009
Revises: s04_0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0009"
down_revision: str | Sequence[str] | None = "s04_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _force_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE reporting.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE reporting.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON reporting.{table}
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reporting.report_frozen_fact (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
          ordinal INTEGER NOT NULL,
          payload JSONB NOT NULL,
          payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,report_version_pub_id,ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE reporting.effect_retest (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          action_pub_id TEXT NOT NULL REFERENCES reporting.optimization_action(pub_id),
          measured_at TIMESTAMPTZ NOT NULL,
          result JSONB NOT NULL,
          recorded_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX report_frozen_fact_version_idx "
        "ON reporting.report_frozen_fact(tenant_pub_id,report_version_pub_id,ordinal)"
    )
    op.execute(
        "CREATE INDEX effect_retest_action_idx "
        "ON reporting.effect_retest(tenant_pub_id,action_pub_id,measured_at,pub_id)"
    )
    _force_tenant_rls("report_frozen_fact")
    _force_tenant_rls("effect_retest")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reporting.effect_retest")
    op.execute("DROP TABLE IF EXISTS reporting.report_frozen_fact")
