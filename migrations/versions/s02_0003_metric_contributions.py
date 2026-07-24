"""Make incremental metric aggregation exactly replayable.

Revision ID: s02_0003
Revises: s02_0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0003"
down_revision: str | None = "s02_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE analytics.metric_trace
          ADD COLUMN project_pub_id TEXT,
          ADD COLUMN metric_date DATE,
          ADD COLUMN dimensions JSONB,
          ADD COLUMN dimensions_hash TEXT,
          ADD COLUMN numerator BIGINT,
          ADD COLUMN denominator BIGINT,
          ADD COLUMN value_sum NUMERIC,
          ADD COLUMN state TEXT,
          ADD COLUMN metric_version TEXT,
          ADD COLUMN scorer_version TEXT;
        CREATE INDEX metric_trace_rollup_idx ON analytics.metric_trace
          (tenant_pub_id,project_pub_id,metric_date,metric_name,dimensions_hash,
           metric_version,scorer_version);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS analytics.metric_trace_rollup_idx;
        ALTER TABLE analytics.metric_trace
          DROP COLUMN scorer_version,
          DROP COLUMN metric_version,
          DROP COLUMN state,
          DROP COLUMN value_sum,
          DROP COLUMN denominator,
          DROP COLUMN numerator,
          DROP COLUMN dimensions_hash,
          DROP COLUMN dimensions,
          DROP COLUMN metric_date,
          DROP COLUMN project_pub_id;
        """
    )
