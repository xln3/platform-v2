"""Add frozen data-export records.

Revision ID: s02_0007
Revises: s02_0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0007"
down_revision: str | None = "s02_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reporting.data_export (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          export_type TEXT NOT NULL CHECK (export_type IN ('metric_xlsx')),
          window_start DATE NOT NULL,
          window_end DATE NOT NULL,
          filters JSONB NOT NULL,
          filter_hash TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          scorer_version TEXT NOT NULL,
          fact_snapshot_hash TEXT NOT NULL,
          evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX data_export_tenant_project_idx
          ON reporting.data_export(tenant_pub_id,project_pub_id,created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reporting.data_export;")
