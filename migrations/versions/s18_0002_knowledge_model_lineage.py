"""Separate requested and provider-resolved knowledge model lineage.

Revision ID: s18_0002_knowledge_model_lineage
Revises: s18_0001_geo_metrics_v2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s18_0002_knowledge_model_lineage"
down_revision: str | Sequence[str] | None = "s18_0001_geo_metrics_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inference_trace",
        sa.Column("requested_model_name", sa.String(length=120), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "inference_trace",
        sa.Column("model_identity_source", sa.String(length=30), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "inference_trace",
        sa.Column("model_catalog_revision", sa.String(length=160), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "inference_trace",
        sa.Column(
            "model_call_attempted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="knowledge",
    )
    op.create_index(
        "ix_inference_trace_tenant_requested_model",
        "inference_trace",
        ["tenant_pub_id", "requested_model_name"],
        schema="knowledge",
    )
    op.create_index(
        "ix_inference_trace_tenant_actual_model",
        "inference_trace",
        ["tenant_pub_id", "model_name"],
        schema="knowledge",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1
            FROM knowledge.inference_trace
            WHERE requested_model_name IS NOT NULL
               OR model_identity_source IS NOT NULL
               OR model_catalog_revision IS NOT NULL
               OR model_call_attempted IS TRUE
            LIMIT 1
          ) THEN
            RAISE EXCEPTION 'knowledge_model_lineage_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.drop_index(
        "ix_inference_trace_tenant_actual_model",
        table_name="inference_trace",
        schema="knowledge",
    )
    op.drop_index(
        "ix_inference_trace_tenant_requested_model",
        table_name="inference_trace",
        schema="knowledge",
    )
    op.drop_column("inference_trace", "model_catalog_revision", schema="knowledge")
    op.drop_column("inference_trace", "model_identity_source", schema="knowledge")
    op.drop_column("inference_trace", "requested_model_name", schema="knowledge")
    op.drop_column("inference_trace", "model_call_attempted", schema="knowledge")


__all__ = ["downgrade", "upgrade"]
