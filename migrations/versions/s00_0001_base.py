"""Create the S00 migration anchor.

Revision ID: s00_0001
Revises:
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s00_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("s00",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for schema in ("platform", "analytics", "evidence", "reporting", "intelligence", "integration"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    # Domain revisions own their tables. The anchor does not drop shared
    # schemas because extensions or operator-managed objects may coexist.
    pass
