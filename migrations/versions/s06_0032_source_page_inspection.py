"""Versioned source profiles, page inspections, findings and exact evidence spans.

Revision ID: s06_0032
Revises: s06_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0032"
down_revision: str | Sequence[str] | None = "s06_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{table}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "source_analysis_profile",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("platform.tenant.id"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("object_name", sa.String(length=200), nullable=False),
        sa.Column("object_kind", sa.String(length=20), nullable=False),
        sa.Column(
            "categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # [{value,evidence_url?,capture_pub_id?}].  API requires at least one
        # provenance field for every alias; automatic alias generation is forbidden.
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "own_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "peers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "anchor_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "linked_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("hard_anchor_available", sa.Boolean(), nullable=False),
        sa.Column("decision_mode", sa.String(length=20), nullable=False),
        sa.Column("profile_type", sa.String(length=4), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=40), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_source_analysis_profile_pub_id"),
        sa.UniqueConstraint(
            "project_id", "revision", name="uq_source_analysis_profile_project_revision"
        ),
        sa.CheckConstraint("revision >= 1", name="revision"),
        sa.CheckConstraint("state IN ('active','retired')", name="state"),
        sa.CheckConstraint("object_kind IN ('brand','product')", name="object_kind"),
        sa.CheckConstraint("decision_mode IN ('selection','reputation')", name="decision_mode"),
        sa.CheckConstraint("profile_type IN ('I','II','III','IV')", name="profile_type"),
        sa.CheckConstraint("profile_hash ~ '^[0-9a-f]{64}$'", name="profile_hash"),
        schema="platform",
    )
    op.create_index(
        "uq_source_analysis_profile_active_project",
        "source_analysis_profile",
        ["project_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("state='active'"),
    )
    op.create_index(
        "ix_source_analysis_profile_tenant_project",
        "source_analysis_profile",
        ["tenant_id", "project_id", "revision"],
        schema="platform",
    )
    _enable_rls("source_analysis_profile")

    op.create_table(
        "page_inspection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("platform.tenant.id"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("platform.collection_run.id"), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_document.id"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_analysis_profile.id"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "page_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "transmission",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "attribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "quality",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_page_inspection_pub_id"),
        sa.UniqueConstraint(
            "source_document_id",
            "profile_id",
            "policy_version",
            "model",
            "prompt_version",
            name="uq_page_inspection_version",
        ),
        sa.CheckConstraint("status IN ('completed','partial','unverifiable')", name="status"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
        schema="platform",
    )
    op.create_index(
        "ix_page_inspection_project_run",
        "page_inspection",
        ["tenant_id", "project_id", "run_id", "created_at"],
        schema="platform",
    )
    op.create_index(
        "ix_page_inspection_document",
        "page_inspection",
        ["source_document_id", "created_at"],
        schema="platform",
    )
    _enable_rls("page_inspection")

    op.create_table(
        "page_inspection_finding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("platform.tenant.id"), nullable=False),
        sa.Column(
            "inspection_id",
            sa.Uuid(),
            sa.ForeignKey("platform.page_inspection.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("ledger", sa.String(length=20), nullable=False),
        sa.Column("variant", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("finding_status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("evidence_chain", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("self_check", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "validation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_page_inspection_finding_pub_id"),
        sa.UniqueConstraint("inspection_id", "ordinal", name="uq_page_inspection_finding_order"),
        sa.CheckConstraint(
            "code IN ('A0','A1','A2','A3','A4','A5','B1','B2','B3','C1','C2','C3','C4')",
            name="code",
        ),
        sa.CheckConstraint("ledger IN ('statement','exposure')", name="ledger"),
        sa.CheckConstraint("finding_status IN ('confirmed','needs_review')", name="finding_status"),
        sa.CheckConstraint("ordinal >= 1", name="ordinal"),
        schema="platform",
    )
    op.create_index(
        "ix_page_inspection_finding_inspection",
        "page_inspection_finding",
        ["inspection_id", "ledger", "code"],
        schema="platform",
    )
    _enable_rls("page_inspection_finding")

    op.create_table(
        "page_evidence_span",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("platform.tenant.id"), nullable=False),
        sa.Column(
            "finding_id",
            sa.Uuid(),
            sa.ForeignKey("platform.page_inspection_finding.id"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_document.id"),
            nullable=False,
        ),
        sa.Column("chain_ordinal", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("text_start", sa.Integer(), nullable=False),
        sa.Column("text_end", sa.Integer(), nullable=False),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("verification", sa.String(length=20), nullable=False, server_default="exact"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_page_evidence_span_pub_id"),
        sa.UniqueConstraint("finding_id", "chain_ordinal", name="uq_page_evidence_span_chain"),
        sa.CheckConstraint("text_start >= 0 AND text_end > text_start", name="text_interval"),
        sa.CheckConstraint("quote_hash ~ '^[0-9a-f]{64}$'", name="quote_hash"),
        sa.CheckConstraint("verification='exact'", name="verification"),
        schema="platform",
    )
    op.create_index(
        "ix_page_evidence_span_document",
        "page_evidence_span",
        ["source_document_id", "text_start"],
        schema="platform",
    )
    _enable_rls("page_evidence_span")

    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format(
                'GRANT SELECT,INSERT,UPDATE ON platform.source_analysis_profile, '
                'platform.page_inspection,platform.page_inspection_finding, '
                'platform.page_evidence_span TO %I', role_name
              );
            END IF;
          END LOOP;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT,INSERT,UPDATE ON platform.source_analysis_profile TO geo_api;
            GRANT SELECT ON platform.page_inspection,platform.page_inspection_finding,
              platform.page_evidence_span TO geo_api;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.drop_table("page_evidence_span", schema="platform")
    op.drop_table("page_inspection_finding", schema="platform")
    op.drop_table("page_inspection", schema="platform")
    op.drop_table("source_analysis_profile", schema="platform")
