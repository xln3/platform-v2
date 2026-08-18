"""Separate internal collection evidence from customer-visible assets.

Revision ID: s06_0029
Revises: s06_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0029"
down_revision: str | Sequence[str] | None = "s06_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_asset",
        sa.Column(
            "customer_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema="evidence",
    )
    op.execute(
        """
        UPDATE evidence.evidence_asset asset
        SET customer_visible=false
        WHERE EXISTS (
          SELECT 1
          FROM evidence.evidence_relation relation
          WHERE relation.tenant_pub_id=asset.tenant_pub_id
            AND relation.to_pub_id=asset.pub_id
            AND relation.relation_type IN (
              'answer_page','answer_evidence_excerpt','official_share_image',
              'official_share_link','cited_source_snapshot','ai_opened_source_preview',
              'answer_sse_trace','answer_sse_raw','answer_har'
            )
        )
        """
    )
    op.create_index(
        "ix_evidence_asset_customer_visible",
        "evidence_asset",
        ["tenant_pub_id", "customer_visible", "pub_id"],
        schema="evidence",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_asset_customer_visible",
        table_name="evidence_asset",
        schema="evidence",
    )
    op.drop_column("evidence_asset", "customer_visible", schema="evidence")
