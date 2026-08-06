"""Add intake profile, promo and trigger question tables.

Revision ID: s06_0004
Revises: s06_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0004"
down_revision: str | Sequence[str] | None = "s06_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _intake_table(
    name: str,
    columns: list[sa.Column[object]],
    constraints: list[sa.Constraint] | None = None,
    extra_indexes: list[tuple[str, list[str], bool]] | None = None,
) -> None:
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
        *(constraints or []),
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
    for index_name, index_columns, unique in extra_indexes or []:
        op.create_index(index_name, name, index_columns, unique=unique, schema="platform")
    op.execute(f'ALTER TABLE platform."{name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{name}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def _jsonb_list(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def upgrade() -> None:
    _intake_table(
        "intake_profile",
        [
            sa.Column("contact_person", sa.String(length=200)),
            sa.Column("contact_info", sa.String(length=500)),
            sa.Column("website", sa.String(length=500)),
            sa.Column("wechat", sa.String(length=200)),
            sa.Column("douyin", sa.String(length=200)),
            sa.Column("social_media", sa.Text()),
            sa.Column("audience_desc", sa.Text()),
            sa.Column("business_license_code", sa.String(length=18)),
            sa.Column("selling_points", sa.Text()),
            sa.Column("filler_name", sa.String(length=200)),
            sa.Column("ad_review_no", sa.String(length=200)),
            sa.Column("ad_review_authority", sa.String(length=200)),
            sa.Column("ad_review_expiry", sa.String(length=40)),
            sa.Column("review_category", sa.String(length=10)),
            sa.Column("pre_review_required", sa.Boolean()),
            sa.Column("truth_confirmed", sa.Boolean()),
            _jsonb_list("goals"),
            _jsonb_list("audience_type"),
            _jsonb_list("platforms"),
            _jsonb_list("regions"),
            _jsonb_list("trademarks"),
            _jsonb_list("ad_review_doc_types"),
            _jsonb_list("evidence_links"),
            _jsonb_list("licenses"),
            sa.Column(
                "prefilled",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        ],
        constraints=[sa.UniqueConstraint("project_id")],
    )
    _intake_table(
        "intake_promo",
        [
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        ],
    )
    _intake_table(
        "intake_trigger_question",
        [
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="draft",
            ),
        ],
        constraints=[sa.UniqueConstraint("tenant_id", "project_id", "text")],
    )


def downgrade() -> None:
    op.drop_table("intake_trigger_question", schema="platform")
    op.drop_table("intake_promo", schema="platform")
    op.drop_table("intake_profile", schema="platform")
