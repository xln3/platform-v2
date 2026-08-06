"""W5 query variants: data-driven query variant seeds and candidates.

Revision ID: s06_0009
Revises: s06_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0009"
down_revision: str | Sequence[str] | None = "s06_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_table(name: str, columns: list[sa.Column[object]]) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("platform.tenant.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("platform.project.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *columns,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "normalized"),
        schema="platform",
    )
    op.create_index(f"ix_platform_{name}_pub_id", name, ["pub_id"], unique=True, schema="platform")
    op.create_index(f"ix_platform_{name}_tenant_id", name, ["tenant_id"], schema="platform")
    op.create_index(f"ix_platform_{name}_project_id", name, ["project_id"], schema="platform")
    op.execute(f'ALTER TABLE platform."{name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{name}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    _tenant_table(
        "variant_seed",
        [
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("normalized", sa.Text(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_ref", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("usage_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        ],
    )
    _tenant_table(
        "query_variant",
        [
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("normalized", sa.Text(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_ref", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("intent", sa.String(length=20), nullable=False, server_default="未分类"),
            sa.Column("audience", sa.String(length=80), nullable=False, server_default="通用"),
            sa.Column("region", sa.String(length=80), nullable=False, server_default="通用"),
            sa.Column("product_line", sa.String(length=200), nullable=False, server_default="通用"),
            sa.Column("marginal_coverage_cell", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("cluster_id", sa.String(length=30), nullable=True),
            sa.Column("cluster_size", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("model", sa.String(length=120), nullable=True),
            sa.Column("prompt_version", sa.String(length=40), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        ],
    )
    op.create_index(
        "ix_platform_query_variant_status",
        "query_variant",
        ["status"],
        schema="platform",
    )


def downgrade() -> None:
    op.drop_index("ix_platform_query_variant_status", table_name="query_variant", schema="platform")
    op.drop_table("query_variant", schema="platform")
    op.drop_table("variant_seed", schema="platform")
