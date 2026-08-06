"""Separate evidence occurrence identity from content-addressed object identity.

Revision ID: s04_0021
Revises: s04_0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0021"
down_revision: str | Sequence[str] | None = "s04_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence.evidence_asset
          DROP CONSTRAINT IF EXISTS evidence_asset_tenant_pub_id_sha256_kind_key;
        CREATE INDEX evidence_asset_tenant_content_idx
          ON evidence.evidence_asset (tenant_pub_id,sha256,kind);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS evidence.evidence_asset_tenant_content_idx;
        ALTER TABLE evidence.evidence_asset
          ADD CONSTRAINT evidence_asset_tenant_pub_id_sha256_kind_key
          UNIQUE (tenant_pub_id,sha256,kind);
        """
    )
