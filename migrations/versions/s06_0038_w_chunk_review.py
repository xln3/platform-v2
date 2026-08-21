"""Add append-only human review facts for weighted content chunks.

Revision ID: s06_0038_w_review
Revises: s06_0037_uvw
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0038_w_review"
down_revision: str | Sequence[str] | None = "s06_0037_uvw"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The composite candidate key lets the review fact enforce tenant and
    # project lineage in PostgreSQL instead of trusting the API join alone.
    op.create_unique_constraint(
        "uq_weighted_content_chunk_tenant_project",
        "weighted_content_chunk",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_table(
        "weighted_content_chunk_review",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reviewer_pub_id", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "tenant_id", "project_id"],
            [
                "platform.weighted_content_chunk.id",
                "platform.weighted_content_chunk.tenant_id",
                "platform.weighted_content_chunk.project_id",
            ],
            name="fk_w_chunk_review_chunk_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_weighted_content_chunk_review_pub_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_w_chunk_review_idempotency",
        ),
        sa.CheckConstraint("decision IN ('accepted','rejected')", name="decision"),
        sa.CheckConstraint("btrim(rationale) <> ''", name="rationale_nonempty"),
        sa.CheckConstraint("btrim(reviewer_pub_id) <> ''", name="reviewer_nonempty"),
        schema="platform",
    )
    op.create_index(
        "ix_w_chunk_review_history",
        "weighted_content_chunk_review",
        ["chunk_id", "reviewed_at", "pub_id"],
        schema="platform",
    )
    op.execute("ALTER TABLE platform.weighted_content_chunk_review ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.weighted_content_chunk_review FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.weighted_content_chunk_review
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format(
                'GRANT SELECT,INSERT ON platform.weighted_content_chunk_review TO %I',
                role_name
              );
            END IF;
          END LOOP;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT,INSERT ON platform.weighted_content_chunk_review TO geo_api;
            GRANT UPDATE(review_state) ON platform.weighted_content_chunk TO geo_api;
            GRANT UPDATE(w_state) ON platform.answer_source_occurrence TO geo_api;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("weighted_content_chunk_review", schema="platform")
    op.drop_constraint(
        "uq_weighted_content_chunk_tenant_project",
        "weighted_content_chunk",
        type_="unique",
        schema="platform",
    )


__all__ = ["downgrade", "upgrade"]
