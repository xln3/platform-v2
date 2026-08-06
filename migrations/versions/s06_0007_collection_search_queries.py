"""Persist platform-issued search queries captured from SSE traces (W1).

Revision ID: s06_0007
Revises: s06_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0007"
down_revision: str | Sequence[str] | None = "s06_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_task",
        sa.Column("search_queries_json", sa.Text(), nullable=False, server_default="[]"),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("collection_task", "search_queries_json", schema="platform")
