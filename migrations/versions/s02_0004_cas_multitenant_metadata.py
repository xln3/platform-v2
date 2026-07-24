"""Allow one immutable CAS object to have tenant-scoped metadata rows.

Revision ID: s02_0004
Revises: s02_0003
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0004"
down_revision: str | None = "s02_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence.evidence_asset
          DROP CONSTRAINT IF EXISTS evidence_asset_object_key_key;
        CREATE INDEX evidence_asset_object_key_idx
          ON evidence.evidence_asset(object_key);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS evidence.evidence_asset_object_key_idx;
        ALTER TABLE evidence.evidence_asset
          ADD CONSTRAINT evidence_asset_object_key_key UNIQUE(object_key);
        """
    )
