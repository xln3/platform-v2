"""Persist append-only raw answer truth for API/report/evidence traceability.

Revision ID: s04_0004
Revises: s04_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s04_0004"
down_revision: str = "s04_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column("project_pub_id", sa.Text(), nullable=False),
        sa.Column("query_pub_id", sa.Text()),
        sa.Column("query_text", sa.Text()),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("adapter_version", sa.Text(), nullable=False),
        sa.Column("capture_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_pub_id", "pub_id", name="uq_raw_answer_tenant_pub"),
        schema="analytics",
    )
    op.create_index(
        "ix_raw_answer_project_capture",
        "answer",
        ["tenant_pub_id", "project_pub_id", "capture_time", "pub_id"],
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_index("ix_raw_answer_project_capture", table_name="answer", schema="analytics")
    op.drop_table("answer", schema="analytics")
