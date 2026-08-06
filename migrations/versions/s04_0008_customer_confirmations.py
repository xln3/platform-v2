"""Add versioned customer profile and atomic asset confirmations.

Revision ID: s04_0008
Revises: s04_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s04_0008"
down_revision: str | Sequence[str] | None = "s04_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _version_table(name: str, columns: list[sa.Column[object]]) -> None:
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
        sa.Column("revision", sa.Integer(), nullable=False),
        *columns,
        sa.Column("declared_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision"),
        schema="platform",
    )
    op.create_index(
        f"ix_platform_{name}_pub_id",
        name,
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        f"ix_platform_{name}_tenant_id",
        name,
        ["tenant_id"],
        schema="platform",
    )
    op.create_index(
        f"ix_platform_{name}_project_id",
        name,
        ["project_id"],
        schema="platform",
    )
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
    _version_table(
        "client_profile_version",
        [
            sa.Column("company_name", sa.String(length=200), nullable=False),
            sa.Column("contact_role", sa.String(length=120), nullable=False),
            sa.Column("audience", sa.Text(), nullable=False),
            sa.Column("public_statement", sa.Text(), nullable=False),
        ],
    )
    _version_table(
        "asset_confirmation_version",
        [
            sa.Column("brand_name", sa.String(length=200), nullable=False),
            sa.Column("website", sa.String(length=500), nullable=False),
            sa.Column("product_name", sa.String(length=200), nullable=False),
            sa.Column("competitor_name", sa.String(length=200), nullable=False),
            sa.Column("prohibited_claim", sa.Text(), nullable=False),
        ],
    )


def downgrade() -> None:
    op.drop_table("asset_confirmation_version", schema="platform")
    op.drop_table("client_profile_version", schema="platform")
