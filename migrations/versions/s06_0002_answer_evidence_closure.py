"""Persist collection citations and evidence manifests for downstream analysis.

Revision ID: s06_0002
Revises: s06_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0002"
down_revision: str | Sequence[str] | None = "s06_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_task",
        sa.Column("citations_json", sa.Text(), nullable=False, server_default="[]"),
        schema="platform",
    )
    op.add_column(
        "collection_task",
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("collection_task", "evidence_json", schema="platform")
    op.drop_column("collection_task", "citations_json", schema="platform")
