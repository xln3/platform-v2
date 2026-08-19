"""Add an explicit customer answer-library catalog boundary.

Revision ID: s06_catalog_0001
Revises: s06_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_catalog_0001"
down_revision: str | Sequence[str] | None = "s06_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_library_catalog",
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
        sa.Column(
            "catalog_config_version_id",
            sa.Uuid(),
            sa.ForeignKey("platform.monitoring_config_version.id"),
            nullable=False,
        ),
        sa.Column("campaign_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "campaign_started_at <= activated_at",
            name="ck_answer_library_catalog_campaign_before_activation",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR retired_at > activated_at",
            name="ck_answer_library_catalog_retired_after_activation",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_answer_library_catalog_pub_id",
        "answer_library_catalog",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_answer_library_catalog_project_activation",
        "answer_library_catalog",
        ["project_id", "activated_at"],
        schema="platform",
    )
    op.create_index(
        "uq_platform_answer_library_catalog_active_project",
        "answer_library_catalog",
        ["project_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.execute("ALTER TABLE platform.answer_library_catalog ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.answer_library_catalog FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.answer_library_catalog
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT,INSERT,UPDATE ON platform.answer_library_catalog TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE ALL PRIVILEGES ON platform.answer_library_catalog FROM geo_api;
            GRANT SELECT ON platform.answer_library_catalog TO geo_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            REVOKE ALL PRIVILEGES ON platform.answer_library_catalog FROM geo_worker;
            GRANT SELECT ON platform.answer_library_catalog TO geo_worker;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_platform_answer_library_catalog_active_project",
        table_name="answer_library_catalog",
        schema="platform",
    )
    op.drop_index(
        "ix_platform_answer_library_catalog_project_activation",
        table_name="answer_library_catalog",
        schema="platform",
    )
    op.drop_index(
        "ix_platform_answer_library_catalog_pub_id",
        table_name="answer_library_catalog",
        schema="platform",
    )
    op.drop_table("answer_library_catalog", schema="platform")
