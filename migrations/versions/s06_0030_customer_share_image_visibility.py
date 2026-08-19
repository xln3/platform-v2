"""Expose official share images while retaining ordinary screenshots internally.

Revision ID: s06_0030
Revises: s06_catalog_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0030"
down_revision: str | Sequence[str] | None = "s06_catalog_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The relation and kind must both identify an official share image.  This
    # deliberately leaves answer/source screenshots and all trace assets private.
    op.execute(
        """
        UPDATE evidence.evidence_asset asset
        SET customer_visible=true
        WHERE asset.kind='share_image'
          AND asset.customer_visible=false
          AND EXISTS (
            SELECT 1
            FROM evidence.evidence_relation relation
            WHERE relation.tenant_pub_id=asset.tenant_pub_id
              AND relation.to_pub_id=asset.pub_id
              AND relation.relation_type='official_share_image'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE evidence.evidence_asset asset
        SET customer_visible=false
        WHERE asset.kind='share_image'
          AND asset.customer_visible=true
          AND EXISTS (
            SELECT 1
            FROM evidence.evidence_relation relation
            WHERE relation.tenant_pub_id=asset.tenant_pub_id
              AND relation.to_pub_id=asset.pub_id
              AND relation.relation_type='official_share_image'
          )
        """
    )
