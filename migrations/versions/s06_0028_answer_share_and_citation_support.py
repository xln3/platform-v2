"""Add safe official-share and answer-citation relation contracts.

Revision ID: s06_0028
Revises: s06_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0028"
down_revision: str | Sequence[str] | None = "s06_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_share_artifact",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("pub_id", sa.Text(), nullable=False, unique=True),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column("project_pub_id", sa.Text(), nullable=False),
        # Collection persists before analytics.answer is created, so this identity
        # intentionally has no FK.  The answer route always binds it through the
        # already tenant/project-scoped analytics.answer row.
        sa.Column("answer_pub_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("share_url", sa.Text()),
        sa.Column("final_url", sa.Text()),
        sa.Column(
            "redirect_chain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("allowlist_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("share_created_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("availability_status", sa.String(length=20), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_accessible_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("embed_status", sa.String(length=20), nullable=False),
        sa.Column("x_frame_options", sa.Text()),
        sa.Column("csp_frame_ancestors", sa.Text()),
        sa.Column("embed_reason", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("probe_version", sa.String(length=80), nullable=False),
        sa.Column(
            "share_link_evidence_pub_id",
            sa.Text(),
            sa.ForeignKey("evidence.evidence_asset.pub_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "share_image_evidence_pub_id",
            sa.Text(),
            sa.ForeignKey("evidence.evidence_asset.pub_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_pub_id", "answer_pub_id", name="tenant_answer"),
        sa.CheckConstraint(
            "status IN ('available','missing','unsupported','invalid')", name="status"
        ),
        sa.CheckConstraint(
            "availability_status IN ('reachable','redirected','blocked','unreachable','unchecked')",
            name="availability_status",
        ),
        sa.CheckConstraint("embed_status IN ('allowed','blocked','unknown')", name="embed_status"),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="http_status",
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"
        ),
        schema="evidence",
    )
    op.create_index(
        "ix_evidence_answer_share_project",
        "answer_share_artifact",
        ["tenant_pub_id", "project_pub_id", "answer_pub_id"],
        schema="evidence",
    )
    op.create_index(
        "ix_evidence_answer_share_reverify",
        "answer_share_artifact",
        ["availability_status", "checked_at"],
        schema="evidence",
    )

    op.create_table(
        "answer_share_verification_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("pub_id", sa.Text(), nullable=False, unique=True),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column(
            "artifact_pub_id",
            sa.Text(),
            sa.ForeignKey("evidence.answer_share_artifact.pub_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_status", sa.String(length=20), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("final_url", sa.Text()),
        sa.Column(
            "redirect_chain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("allowlist_valid", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("embed_status", sa.String(length=20), nullable=False),
        sa.Column("x_frame_options", sa.Text()),
        sa.Column("csp_frame_ancestors", sa.Text()),
        sa.Column("embed_reason", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("probe_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "availability_status IN ('reachable','redirected','blocked','unreachable','unchecked')",
            name="availability_status",
        ),
        sa.CheckConstraint("embed_status IN ('allowed','blocked','unknown')", name="embed_status"),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="http_status",
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"
        ),
        schema="evidence",
    )
    op.create_index(
        "ix_evidence_answer_share_event_artifact",
        "answer_share_verification_event",
        ["tenant_pub_id", "artifact_pub_id", "checked_at"],
        schema="evidence",
    )

    op.create_table(
        "answer_citation_relation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("pub_id", sa.Text(), nullable=False, unique=True),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column("answer_pub_id", sa.Text(), nullable=False),
        sa.Column("citation_pub_id", sa.Text()),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_document_pub_id", sa.Text()),
        sa.Column("mapping_status", sa.String(length=20), nullable=False),
        sa.Column("mapping_basis", sa.String(length=80)),
        sa.Column("answer_text_start", sa.Integer()),
        sa.Column("answer_text_end", sa.Integer()),
        sa.Column("answer_ast_path", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("answer_sentence", sa.Text()),
        sa.Column("source_quote", sa.Text()),
        sa.Column("source_text_start", sa.Integer()),
        sa.Column("source_text_end", sa.Integer()),
        sa.Column("source_quote_hash", sa.String(length=64)),
        sa.Column("source_match_status", sa.String(length=20), nullable=False),
        sa.Column("source_match_version", sa.String(length=80)),
        sa.Column("relation", sa.String(length=20), nullable=False),
        sa.Column("relevance_confidence", sa.Numeric(5, 4)),
        sa.Column("classifier_version", sa.String(length=80)),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("first_cited_at", sa.DateTime(timezone=True)),
        sa.Column("last_cited_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_pub_id", "answer_pub_id", "ordinal", name="answer_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name="ordinal"),
        sa.CheckConstraint(
            "mapping_status IN ('mapped','unmapped','ambiguous')", name="mapping_status"
        ),
        sa.CheckConstraint(
            "source_match_status IN ('exact','normalized','not_found','not_checked')",
            name="source_match_status",
        ),
        sa.CheckConstraint(
            "relation IN ('supports','contradicts','background','unverified')", name="relation"
        ),
        sa.CheckConstraint(
            "review_status IN ('unreviewed','approved','rejected','needs_review')",
            name="review_status",
        ),
        sa.CheckConstraint(
            "(answer_text_start IS NULL AND answer_text_end IS NULL) OR "
            "(answer_text_start >= 0 AND answer_text_end > answer_text_start)",
            name="answer_interval",
        ),
        sa.CheckConstraint(
            "(source_text_start IS NULL AND source_text_end IS NULL) OR "
            "(source_text_start >= 0 AND source_text_end > source_text_start)",
            name="source_interval",
        ),
        sa.CheckConstraint(
            "source_quote_hash IS NULL OR source_quote_hash ~ '^[0-9a-f]{64}$'",
            name="source_quote_hash",
        ),
        sa.CheckConstraint(
            "relevance_confidence IS NULL OR "
            "(relevance_confidence >= 0 AND relevance_confidence <= 1)",
            name="relevance_confidence",
        ),
        schema="analytics",
    )
    op.create_index(
        "ix_analytics_answer_citation_relation_source",
        "answer_citation_relation",
        ["tenant_pub_id", "source_document_pub_id"],
        schema="analytics",
    )

    # Historical rows remain explicit unknowns.  Never infer availability from
    # capture time and never promote platform-returned cited_text to a verified
    # source quote during backfill.
    op.execute(
        """
        WITH link AS (
          SELECT DISTINCT ON (er.tenant_pub_id,er.from_pub_id)
                 er.tenant_pub_id,er.from_pub_id,ea.pub_id,ea.source_url,ea.capture_time
          FROM evidence.evidence_relation er
          JOIN evidence.evidence_asset ea
            ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
          WHERE er.relation_type='official_share_link' AND ea.deleted_at IS NULL
          ORDER BY er.tenant_pub_id,er.from_pub_id,ea.capture_time DESC,ea.pub_id DESC
        ), image AS (
          SELECT DISTINCT ON (er.tenant_pub_id,er.from_pub_id)
                 er.tenant_pub_id,er.from_pub_id,ea.pub_id
          FROM evidence.evidence_relation er
          JOIN evidence.evidence_asset ea
            ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
          WHERE er.relation_type='official_share_image' AND ea.deleted_at IS NULL
          ORDER BY er.tenant_pub_id,er.from_pub_id,ea.capture_time DESC,ea.pub_id DESC
        )
        INSERT INTO evidence.answer_share_artifact
          (pub_id,tenant_pub_id,project_pub_id,answer_pub_id,platform,status,share_url,
           final_url,allowlist_valid,share_created_at,availability_status,embed_status,
           probe_version,share_link_evidence_pub_id,share_image_evidence_pub_id)
        SELECT
          'ash_' || substr(encode(digest(a.tenant_pub_id || '|' || a.pub_id,'sha256'),'hex'),1,26),
          a.tenant_pub_id,a.project_pub_id,a.pub_id,lower(a.model),
          CASE
            WHEN link.source_url IS NOT NULL AND (
              (lower(a.model)='deepseek' AND
               link.source_url ~ '^https://chat\\.deepseek\\.com/share/') OR
              (lower(a.model)='doubao' AND link.source_url ~ '^https://(www\\.)?doubao\\.com/') OR
              (lower(a.model)='yiyan' AND link.source_url ~ '^https://(mr\\.baidu\\.com|wenxin\\.baidu\\.com)/')
            ) THEN 'available'
            WHEN link.source_url IS NOT NULL THEN 'invalid'
            WHEN lower(a.model) IN ('tongyi','yuanbao') THEN 'unsupported'
            ELSE 'missing'
          END,
          link.source_url,link.source_url,
          COALESCE(
            (lower(a.model)='deepseek' AND
             link.source_url ~ '^https://chat\\.deepseek\\.com/share/') OR
            (lower(a.model)='doubao' AND link.source_url ~ '^https://(www\\.)?doubao\\.com/') OR
            (lower(a.model)='yiyan' AND link.source_url ~ '^https://(mr\\.baidu\\.com|wenxin\\.baidu\\.com)/'),
            false
          ),
          link.capture_time,'unchecked','unknown','legacy-backfill-v1',link.pub_id,image.pub_id
        FROM analytics.answer a
        LEFT JOIN link ON link.tenant_pub_id=a.tenant_pub_id AND link.from_pub_id=a.pub_id
        LEFT JOIN image ON image.tenant_pub_id=a.tenant_pub_id AND image.from_pub_id=a.pub_id
        ON CONFLICT (tenant_pub_id,answer_pub_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO analytics.answer_citation_relation
          (pub_id,tenant_pub_id,answer_pub_id,citation_pub_id,ordinal,
           source_document_pub_id,mapping_status,source_match_status,relation,
           classifier_version,review_status,first_cited_at,last_cited_at)
        SELECT
          'acr_' || substr(encode(digest(c.tenant_pub_id || '|' || c.answer_pub_id || '|' ||
            c.ordinal::text,'sha256'),'hex'),1,26),
          c.tenant_pub_id,c.answer_pub_id,c.pub_id,c.ordinal,c.source_document_pub_id,
          'unmapped','not_checked','unverified','legacy-backfill-v1','unreviewed',
          a.capture_time,a.capture_time
        FROM (
          SELECT DISTINCT ON (tenant_pub_id,answer_pub_id,ordinal) *
          FROM analytics.citation_fact
          ORDER BY tenant_pub_id,answer_pub_id,ordinal,created_at DESC,id DESC
        ) c
        JOIN analytics.answer a
          ON a.tenant_pub_id=c.tenant_pub_id AND a.pub_id=c.answer_pub_id
        ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal) DO NOTHING
        """
    )

    for schema, table in (
        ("evidence", "answer_share_artifact"),
        ("evidence", "answer_share_verification_event"),
        ("analytics", "answer_citation_relation"),
    ):
        op.execute(f'ALTER TABLE {schema}."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {schema}."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {schema}."{table}"
            USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
            WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
            """
        )

    op.execute(
        """
        REVOKE ALL ON evidence.answer_share_artifact FROM PUBLIC;
        REVOKE ALL ON evidence.answer_share_verification_event FROM PUBLIC;
        REVOKE ALL ON analytics.answer_citation_relation FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT,INSERT,UPDATE ON evidence.answer_share_artifact TO geo;
            GRANT SELECT,INSERT ON evidence.answer_share_verification_event TO geo;
            GRANT SELECT,INSERT,UPDATE ON analytics.answer_citation_relation TO geo;
            GRANT USAGE,SELECT ON SEQUENCE evidence.answer_share_artifact_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE evidence.answer_share_verification_event_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE analytics.answer_citation_relation_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT,INSERT,UPDATE ON evidence.answer_share_artifact TO geo_worker;
            GRANT SELECT,INSERT ON evidence.answer_share_verification_event TO geo_worker;
            GRANT SELECT,INSERT,UPDATE ON analytics.answer_citation_relation TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE evidence.answer_share_artifact_id_seq TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE
              evidence.answer_share_verification_event_id_seq TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE analytics.answer_citation_relation_id_seq TO geo_worker;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT ON evidence.answer_share_artifact TO geo_api;
            GRANT SELECT ON evidence.answer_share_verification_event TO geo_api;
            GRANT SELECT ON analytics.answer_citation_relation TO geo_api;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("answer_citation_relation", schema="analytics")
    op.drop_table("answer_share_verification_event", schema="evidence")
    op.drop_table("answer_share_artifact", schema="evidence")
