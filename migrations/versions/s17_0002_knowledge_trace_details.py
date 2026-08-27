"""Add explicit adoption policy and tool summary to inference traces.

Revision ID: s17_0002_knowledge_trace_details
Revises: s17_0001_knowledge_evolution
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s17_0002_knowledge_trace_details"
down_revision: str | Sequence[str] | None = "s17_0001_knowledge_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inference_trace",
        sa.Column(
            "adopt_model_inferred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="knowledge",
    )
    op.add_column(
        "inference_trace",
        sa.Column(
            "tool_summary",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="knowledge",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM knowledge.inference_trace LIMIT 1) THEN
            RAISE EXCEPTION 'knowledge_trace_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.drop_column("inference_trace", "tool_summary", schema="knowledge")
    op.drop_column("inference_trace", "adopt_model_inferred", schema="knowledge")
