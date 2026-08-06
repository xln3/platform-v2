"""Persist collection run and config lineage on raw answers.

Revision ID: s04_0023
Revises: s04_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0023"
down_revision: str | Sequence[str] | None = "s04_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE analytics.answer
          ADD COLUMN run_pub_id TEXT,
          ADD COLUMN config_version_pub_id TEXT;
        CREATE INDEX ix_answer_collection_lineage
          ON analytics.answer (tenant_pub_id,run_pub_id,config_version_pub_id)
          WHERE run_pub_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS analytics.ix_answer_collection_lineage;
        ALTER TABLE analytics.answer
          DROP COLUMN config_version_pub_id,
          DROP COLUMN run_pub_id;
        """
    )
