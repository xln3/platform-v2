"""Align persisted audit fields with the frozen actor/action contract.

Revision ID: s04_0002
Revises: s04_merge_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s04_0002"
down_revision: str = "s04_merge_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "audit_log",
        "actor_pub_id",
        schema="platform",
        existing_type=sa.String(length=30),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_log",
        "action",
        schema="platform",
        existing_type=sa.String(length=30),
        type_=sa.String(length=120),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_log",
        "action",
        schema="platform",
        existing_type=sa.String(length=120),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_log",
        "actor_pub_id",
        schema="platform",
        existing_type=sa.String(length=255),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
