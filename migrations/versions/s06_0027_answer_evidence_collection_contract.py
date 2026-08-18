"""Add customer answer-content and cited-source evidence contracts.

Revision ID: s06_0027
Revises: s06_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0027"
down_revision: str | Sequence[str] | None = "s06_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Durable collector projection. ``answer_text`` remains the immutable raw
    # platform response used by analysis and internal audit.
    for column in (
        sa.Column("response_markdown_normalized", sa.Text()),
        sa.Column("response_ast_json", sa.Text()),
        sa.Column("response_html_sanitized", sa.Text()),
        sa.Column("response_plain_text", sa.Text()),
        sa.Column("response_hash", sa.String(length=64)),
        sa.Column("render_parser_version", sa.String(length=80)),
    ):
        op.add_column("collection_task", column, schema="platform")

    op.execute(
        """
        UPDATE platform.collection_task
        SET response_markdown_normalized=answer_text,
            response_ast_json=CASE WHEN answer_text IS NULL THEN NULL ELSE '[]' END,
            response_html_sanitized=CASE
              WHEN answer_text IS NULL THEN NULL
              ELSE '<pre>' ||
                replace(replace(replace(answer_text,'&','&amp;'),'<','&lt;'),'>','&gt;') ||
                '</pre>'
            END,
            response_plain_text=answer_text,
            response_hash=CASE
              WHEN answer_text IS NULL THEN NULL
              ELSE encode(digest(answer_text,'sha256'),'hex')
            END,
            render_parser_version=CASE
              WHEN answer_text IS NULL THEN NULL ELSE 'legacy-backfill-v1'
            END
        WHERE response_markdown_normalized IS NULL
        """
    )

    op.add_column("answer", sa.Column("response_raw", sa.Text()), schema="analytics")
    op.add_column(
        "answer", sa.Column("response_markdown_normalized", sa.Text()), schema="analytics"
    )
    op.add_column(
        "answer",
        sa.Column(
            "response_ast",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="analytics",
    )
    op.add_column("answer", sa.Column("response_html_sanitized", sa.Text()), schema="analytics")
    op.add_column("answer", sa.Column("response_plain_text", sa.Text()), schema="analytics")
    op.add_column("answer", sa.Column("response_hash", sa.String(length=64)), schema="analytics")
    op.add_column(
        "answer", sa.Column("render_parser_version", sa.String(length=80)), schema="analytics"
    )
    op.execute(
        """
        UPDATE analytics.answer
        SET response_raw=response_text,
            response_markdown_normalized=response_text,
            response_ast=jsonb_build_array(
              jsonb_build_object('type','paragraph','text',response_text)
            ),
            response_html_sanitized='<pre>' ||
              replace(replace(replace(response_text,'&','&amp;'),'<','&lt;'),'>','&gt;') ||
              '</pre>',
            response_plain_text=response_text,
            response_hash=encode(digest(response_text,'sha256'),'hex'),
            render_parser_version='legacy-backfill-v1'
        WHERE response_raw IS NULL
        """
    )
    for column_name in (
        "response_raw",
        "response_markdown_normalized",
        "response_html_sanitized",
        "response_plain_text",
        "response_hash",
        "render_parser_version",
    ):
        op.alter_column("answer", column_name, nullable=False, schema="analytics")

    # Preserve the platform numbering scheme independently from the customer
    # one-based ordinal and carry selected source-publication provenance.
    op.add_column("citation_fact", sa.Column("platform_ordinal", sa.Integer()), schema="analytics")
    op.add_column("citation_fact", sa.Column("ordinal_base", sa.SmallInteger()), schema="analytics")
    op.add_column(
        "citation_fact",
        sa.Column(
            "source_document_pub_id",
            sa.String(length=30),
            sa.ForeignKey("platform.source_document.pub_id", ondelete="SET NULL"),
        ),
        schema="analytics",
    )
    op.add_column("citation_fact", sa.Column("published_at_raw", sa.Text()), schema="analytics")
    op.add_column(
        "citation_fact", sa.Column("published_at", sa.DateTime(timezone=True)), schema="analytics"
    )
    op.add_column(
        "citation_fact",
        sa.Column("published_at_timezone", sa.String(length=80)),
        schema="analytics",
    )
    op.add_column(
        "citation_fact",
        sa.Column("published_at_precision", sa.String(length=20)),
        schema="analytics",
    )
    op.add_column(
        "citation_fact", sa.Column("published_at_source", sa.String(length=120)), schema="analytics"
    )
    op.add_column(
        "citation_fact",
        sa.Column("published_at_confidence", sa.String(length=30)),
        schema="analytics",
    )
    op.add_column(
        "citation_fact",
        sa.Column(
            "published_at_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="analytics",
    )
    op.execute(
        """
        UPDATE analytics.citation_fact
        SET platform_ordinal=ordinal, ordinal_base=1,
            published_at_confidence='unknown'
        WHERE platform_ordinal IS NULL
        """
    )
    op.alter_column("citation_fact", "platform_ordinal", nullable=False, schema="analytics")
    op.alter_column("citation_fact", "ordinal_base", nullable=False, schema="analytics")
    op.alter_column("citation_fact", "published_at_confidence", nullable=False, schema="analytics")
    op.create_check_constraint(
        "ordinal_base",
        "citation_fact",
        "ordinal_base IN (0,1)",
        schema="analytics",
    )
    op.create_check_constraint(
        "ordinal_mapping",
        "citation_fact",
        "ordinal >= 1 AND platform_ordinal >= ordinal_base "
        "AND ordinal = platform_ordinal + (1 - ordinal_base)",
        schema="analytics",
    )
    op.create_check_constraint(
        "published_at_confidence",
        "citation_fact",
        "published_at_confidence IN "
        "('verified_structured','structured_only','visible_only','inferred_low','unknown')",
        schema="analytics",
    )
    op.create_index(
        "ix_analytics_citation_source_document",
        "citation_fact",
        ["tenant_pub_id", "source_document_pub_id"],
        schema="analytics",
    )

    # The W2 source fetch now keeps every candidate and why one was selected.
    source_columns = (
        sa.Column("canonical_url", sa.Text()),
        sa.Column(
            "redirect_chain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("page_title", sa.Text()),
        sa.Column("site_name", sa.String(length=300)),
        sa.Column("publisher", sa.String(length=300)),
        sa.Column(
            "authors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("language", sa.String(length=40)),
        sa.Column("content_format", sa.String(length=40), nullable=False, server_default="html"),
        sa.Column("published_at_raw", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_at_timezone", sa.String(length=80)),
        sa.Column("published_at_precision", sa.String(length=20)),
        sa.Column("published_at_source", sa.String(length=120)),
        sa.Column(
            "published_at_confidence",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "published_at_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_parser_version", sa.String(length=80)),
    )
    for column in source_columns:
        op.add_column("source_document", column, schema="platform")
    op.execute(
        """
        UPDATE platform.source_document
        SET canonical_url=COALESCE(final_url,url),
            first_seen_at=fetched_at,
            last_verified_at=fetched_at,
            metadata_parser_version='legacy-backfill-v1'
        WHERE canonical_url IS NULL
        """
    )
    for column_name in (
        "canonical_url",
        "first_seen_at",
        "last_verified_at",
        "metadata_parser_version",
    ):
        op.alter_column("source_document", column_name, nullable=False, schema="platform")
    op.create_check_constraint(
        "published_at_confidence",
        "source_document",
        "published_at_confidence IN "
        "('verified_structured','structured_only','visible_only','inferred_low','unknown')",
        schema="platform",
    )

    # Image geometry is part of the evidence manifest; it prevents huge share
    # images from being interpreted as unconstrained layout content.
    op.add_column("evidence_asset", sa.Column("image_width", sa.Integer()), schema="evidence")
    op.add_column("evidence_asset", sa.Column("image_height", sa.Integer()), schema="evidence")
    op.create_check_constraint(
        "image_geometry",
        "evidence_asset",
        "(image_width IS NULL AND image_height IS NULL) OR (image_width > 0 AND image_height > 0)",
        schema="evidence",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_asset_image_geometry", "evidence_asset", schema="evidence")
    op.drop_column("evidence_asset", "image_height", schema="evidence")
    op.drop_column("evidence_asset", "image_width", schema="evidence")

    op.drop_constraint(
        "ck_source_document_published_at_confidence", "source_document", schema="platform"
    )
    for column_name in (
        "metadata_parser_version",
        "last_verified_at",
        "first_seen_at",
        "modified_at",
        "published_at_candidates",
        "published_at_confidence",
        "published_at_source",
        "published_at_precision",
        "published_at_timezone",
        "published_at",
        "published_at_raw",
        "content_format",
        "language",
        "authors",
        "publisher",
        "site_name",
        "page_title",
        "redirect_chain",
        "canonical_url",
    ):
        op.drop_column("source_document", column_name, schema="platform")

    op.drop_index(
        "ix_analytics_citation_source_document", table_name="citation_fact", schema="analytics"
    )
    op.drop_constraint(
        "ck_citation_fact_published_at_confidence", "citation_fact", schema="analytics"
    )
    op.drop_constraint("ck_citation_fact_ordinal_mapping", "citation_fact", schema="analytics")
    op.drop_constraint("ck_citation_fact_ordinal_base", "citation_fact", schema="analytics")
    for column_name in (
        "published_at_candidates",
        "published_at_confidence",
        "published_at_source",
        "published_at_precision",
        "published_at_timezone",
        "published_at",
        "published_at_raw",
        "source_document_pub_id",
        "ordinal_base",
        "platform_ordinal",
    ):
        op.drop_column("citation_fact", column_name, schema="analytics")

    for column_name in (
        "render_parser_version",
        "response_hash",
        "response_plain_text",
        "response_html_sanitized",
        "response_ast",
        "response_markdown_normalized",
        "response_raw",
    ):
        op.drop_column("answer", column_name, schema="analytics")

    for column_name in (
        "render_parser_version",
        "response_hash",
        "response_plain_text",
        "response_html_sanitized",
        "response_ast_json",
        "response_markdown_normalized",
    ):
        op.drop_column("collection_task", column_name, schema="platform")
