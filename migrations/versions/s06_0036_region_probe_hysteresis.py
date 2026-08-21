"""Persist relay probe hysteresis state on collection regions.

Revision ID: s06_0036_region_probe
Revises: s06_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0036_region_probe"
down_revision: str | Sequence[str] | None = "s06_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One resident browser profile is one authenticated platform session.  The partial
    # unique index closes the create/patch race while still allowing unbound accounts.
    op.create_index(
        "uq_collection_platform_account_browser_instance_key",
        "collection_platform_account",
        ["browser_instance_key"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("browser_instance_key IS NOT NULL"),
    )
    op.add_column(
        "collection_region",
        sa.Column("probe_success_streak", sa.Integer(), nullable=False, server_default="0"),
        schema="platform",
    )
    op.add_column(
        "collection_region",
        sa.Column("probe_failure_streak", sa.Integer(), nullable=False, server_default="0"),
        schema="platform",
    )
    op.add_column(
        "collection_region",
        sa.Column("last_probe_ok", sa.Boolean(), nullable=True),
        schema="platform",
    )
    op.add_column(
        "collection_region",
        sa.Column("last_probe_note", sa.Text(), nullable=True),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("collection_region", "last_probe_note", schema="platform")
    op.drop_column("collection_region", "last_probe_ok", schema="platform")
    op.drop_column("collection_region", "probe_failure_streak", schema="platform")
    op.drop_column("collection_region", "probe_success_streak", schema="platform")
    op.drop_index(
        "uq_collection_platform_account_browser_instance_key",
        table_name="collection_platform_account",
        schema="platform",
    )


__all__ = ["downgrade", "upgrade"]
