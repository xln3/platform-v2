"""Persist lossless UVW occurrences, page versions and five-service entitlement facts.

Revision ID: s06_0037_uvw
Revises: s06_0036_region_probe
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0037_uvw"
down_revision: str | Sequence[str] | None = "s06_0036_region_probe"
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


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("platform.tenant.id"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "source_site",
        *_identity_columns(),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("registrable_domain", sa.String(length=253), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_source_site_pub_id"),
        sa.UniqueConstraint("tenant_id", "host", name="uq_source_site_tenant_host"),
        sa.CheckConstraint("host = lower(host) AND btrim(host) <> ''", name="host_normalized"),
        schema="platform",
    )
    op.create_index(
        "ix_source_site_tenant_host", "source_site", ["tenant_id", "host"], schema="platform"
    )
    _enable_rls("source_site")

    op.create_table(
        "source_url",
        *_identity_columns(),
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("platform.source_site.id"), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("canonical_url_hash", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=80), nullable=False),
        sa.Column("first_raw_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_source_url_pub_id"),
        sa.UniqueConstraint(
            "tenant_id", "canonical_url_hash", "canonical_url", name="uq_source_url_identity"
        ),
        sa.CheckConstraint("canonical_url_hash ~ '^[0-9a-f]{64}$'", name="canonical_hash"),
        schema="platform",
    )
    op.create_index(
        "ix_source_url_site", "source_url", ["site_id", "created_at"], schema="platform"
    )
    _enable_rls("source_url")

    op.create_table(
        "answer_retrieval_event",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("platform.collection_run.id"), nullable=False),
        sa.Column(
            "answer_task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_task.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "queries",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("u_observation", sa.String(length=16), nullable=False),
        sa.Column("v_observation", sa.String(length=16), nullable=False),
        sa.Column("final_reference_observation", sa.String(length=16), nullable=False),
        sa.Column("evidence_pub_id", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_answer_retrieval_event_pub_id"),
        sa.UniqueConstraint(
            "answer_task_id", "ordinal", name="uq_answer_retrieval_event_answer_order"
        ),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        sa.CheckConstraint(
            "u_observation IN ('observed','partial','unobserved')", name="u_observation"
        ),
        sa.CheckConstraint(
            "v_observation IN ('observed','partial','unobserved')", name="v_observation"
        ),
        sa.CheckConstraint(
            "final_reference_observation IN ('observed','partial','unobserved')",
            name="final_observation",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_answer_retrieval_event_project_run",
        "answer_retrieval_event",
        ["tenant_id", "project_id", "run_id", "created_at"],
        schema="platform",
    )
    _enable_rls("answer_retrieval_event")

    op.create_table(
        "answer_source_occurrence",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("platform.collection_run.id"), nullable=False),
        sa.Column(
            "answer_task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_task.id"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_event_id",
            sa.Uuid(),
            sa.ForeignKey("platform.answer_retrieval_event.id"),
            nullable=True,
        ),
        sa.Column(
            "source_url_id", sa.Uuid(), sa.ForeignKey("platform.source_url.id"), nullable=False
        ),
        sa.Column("occurrence_ordinal", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("raw_url", sa.Text(), nullable=False),
        sa.Column("u_state", sa.String(length=16), nullable=False),
        sa.Column("u_rank", sa.Integer(), nullable=True),
        sa.Column("v_state", sa.String(length=16), nullable=False),
        sa.Column("v_open_order", sa.Integer(), nullable=True),
        sa.Column("final_reference_state", sa.String(length=20), nullable=False),
        sa.Column("final_reference_ordinal", sa.Integer(), nullable=True),
        sa.Column("w_state", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("evidence_pub_id", sa.String(length=30), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_answer_source_occurrence_pub_id"),
        sa.UniqueConstraint(
            "answer_task_id", "occurrence_ordinal", name="uq_answer_source_occurrence_order"
        ),
        sa.CheckConstraint("occurrence_ordinal >= 1", name="occurrence_ordinal_positive"),
        sa.CheckConstraint("u_state IN ('observed','unobserved')", name="u_state"),
        sa.CheckConstraint("u_rank IS NULL OR u_rank >= 1", name="u_rank"),
        sa.CheckConstraint("(u_state='observed') = (u_rank IS NOT NULL)", name="u_rank_state"),
        sa.CheckConstraint("v_state IN ('entered','not_entered','unobserved')", name="v_state"),
        sa.CheckConstraint("v_open_order IS NULL OR v_open_order >= 1", name="v_order"),
        sa.CheckConstraint(
            "(v_state='entered') = (v_open_order IS NOT NULL)", name="v_order_state"
        ),
        sa.CheckConstraint(
            "final_reference_state IN ('referenced','not_referenced','unobserved')",
            name="final_reference_state",
        ),
        sa.CheckConstraint(
            "final_reference_ordinal IS NULL OR final_reference_ordinal >= 1",
            name="final_reference_order",
        ),
        sa.CheckConstraint(
            "(final_reference_state='referenced') = (final_reference_ordinal IS NOT NULL)",
            name="final_reference_order_state",
        ),
        sa.CheckConstraint(
            "w_state IN ('pending','confirmed','no_evidence','unobserved')", name="w_state"
        ),
        sa.CheckConstraint("w_state<>'pending' OR v_state='entered'", name="w_pending_requires_v"),
        schema="platform",
    )
    op.create_index(
        "ix_answer_source_occurrence_project_url_u",
        "answer_source_occurrence",
        ["tenant_id", "project_id", "source_url_id", "u_state", "captured_at"],
        schema="platform",
    )
    op.create_index(
        "ix_answer_source_occurrence_answer",
        "answer_source_occurrence",
        ["answer_task_id", "occurrence_ordinal"],
        schema="platform",
    )
    _enable_rls("answer_source_occurrence")

    op.add_column(
        "source_document", sa.Column("source_url_id", sa.Uuid(), nullable=True), schema="platform"
    )
    op.create_foreign_key(
        "fk_source_document_source_url",
        "source_document",
        "source_url",
        ["source_url_id"],
        ["id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_index(
        "ix_source_document_source_url",
        "source_document",
        ["source_url_id", "fetched_at"],
        schema="platform",
    )

    op.create_table(
        "source_fetch_attempt",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column(
            "source_url_id", sa.Uuid(), sa.ForeignKey("platform.source_url.id"), nullable=False
        ),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("platform.collection_run.id"), nullable=True),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("fetcher", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column(
            "redirect_chain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_source_fetch_attempt_pub_id"),
        sa.UniqueConstraint(
            "source_url_id", "attempt_ordinal", name="uq_source_fetch_attempt_order"
        ),
        sa.CheckConstraint("attempt_ordinal >= 1", name="attempt_ordinal_positive"),
        sa.CheckConstraint(
            "state IN ('queued','fetching','succeeded','partial','blocked','gone',"
            "'retry_wait','failed')",
            name="state",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_source_fetch_attempt_queue",
        "source_fetch_attempt",
        ["tenant_id", "state", "next_retry_at", "source_url_id"],
        schema="platform",
    )
    _enable_rls("source_fetch_attempt")

    op.create_table(
        "source_page_snapshot",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column(
            "source_url_id", sa.Uuid(), sa.ForeignKey("platform.source_url.id"), nullable=False
        ),
        sa.Column(
            "source_document_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_document.id"),
            nullable=True,
        ),
        sa.Column(
            "fetch_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_fetch_attempt.id"),
            nullable=True,
        ),
        sa.Column("snapshot_state", sa.String(length=24), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("site_name", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("body_object_key", sa.Text(), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
        sa.Column("text_sha256", sa.String(length=64), nullable=True),
        sa.Column("extractor_version", sa.String(length=80), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_source_page_snapshot_pub_id"),
        sa.CheckConstraint(
            "snapshot_state IN ('succeeded','partial','blocked','gone','failed')",
            name="snapshot_state",
        ),
        sa.CheckConstraint(
            "body_sha256 IS NULL OR body_sha256 ~ '^[0-9a-f]{64}$'", name="body_hash"
        ),
        sa.CheckConstraint(
            "text_sha256 IS NULL OR text_sha256 ~ '^[0-9a-f]{64}$'", name="text_hash"
        ),
        sa.CheckConstraint(
            "snapshot_state<>'succeeded' OR "
            "(body_object_key IS NOT NULL AND text_sha256 IS NOT NULL "
            "AND extractor_version IS NOT NULL)",
            name="succeeded_body",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_source_page_snapshot_url_time",
        "source_page_snapshot",
        ["source_url_id", "captured_at"],
        schema="platform",
    )
    op.create_index(
        "uq_source_page_snapshot_content",
        "source_page_snapshot",
        ["source_url_id", "text_sha256", "extractor_version"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("text_sha256 IS NOT NULL"),
    )
    _enable_rls("source_page_snapshot")

    op.create_table(
        "content_contribution_analysis",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column(
            "answer_task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_task.id"),
            nullable=False,
        ),
        sa.Column(
            "occurrence_id",
            sa.Uuid(),
            sa.ForeignKey("platform.answer_source_occurrence.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_page_snapshot.id"),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_state", sa.String(length=20), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_content_contribution_analysis_pub_id"),
        sa.UniqueConstraint(
            "occurrence_id",
            "snapshot_id",
            "policy_version",
            name="uq_content_contribution_analysis_version",
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash"),
        sa.CheckConstraint("result_state IN ('confirmed','no_evidence')", name="result_state"),
        sa.CheckConstraint("chunk_count >= 0", name="chunk_count_nonnegative"),
        sa.CheckConstraint(
            "(result_state='confirmed') = (chunk_count > 0)", name="result_matches_chunks"
        ),
        schema="platform",
    )
    op.create_index(
        "ix_content_contribution_analysis_occurrence",
        "content_contribution_analysis",
        ["occurrence_id", "policy_version", "created_at"],
        schema="platform",
    )
    _enable_rls("content_contribution_analysis")

    op.create_table(
        "weighted_content_chunk",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column(
            "answer_task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_task.id"),
            nullable=False,
        ),
        sa.Column(
            "occurrence_id",
            sa.Uuid(),
            sa.ForeignKey("platform.answer_source_occurrence.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("platform.source_page_snapshot.id"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            sa.Uuid(),
            sa.ForeignKey("platform.content_contribution_analysis.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_text_start", sa.Integer(), nullable=False),
        sa.Column("source_text_end", sa.Integer(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("source_quote_hash", sa.String(length=64), nullable=False),
        sa.Column("answer_text_start", sa.Integer(), nullable=True),
        sa.Column("answer_text_end", sa.Integer(), nullable=True),
        sa.Column("answer_quote", sa.Text(), nullable=True),
        sa.Column("answer_quote_hash", sa.String(length=64), nullable=True),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("contribution_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False),
        sa.Column("verification_state", sa.String(length=24), nullable=False),
        sa.Column("review_state", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_weighted_content_chunk_pub_id"),
        sa.UniqueConstraint(
            "analysis_id",
            "ordinal",
            name="uq_weighted_chunk_version_order",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        sa.CheckConstraint(
            "source_text_start >= 0 AND source_text_end > source_text_start", name="source_interval"
        ),
        sa.CheckConstraint(
            "(answer_text_start IS NULL AND answer_text_end IS NULL "
            "AND answer_quote IS NULL AND answer_quote_hash IS NULL) OR "
            "(answer_text_start >= 0 AND answer_text_end > answer_text_start "
            "AND answer_quote IS NOT NULL AND answer_quote_hash IS NOT NULL)",
            name="answer_interval",
        ),
        sa.CheckConstraint("source_quote_hash ~ '^[0-9a-f]{64}$'", name="source_quote_hash"),
        sa.CheckConstraint(
            "answer_quote_hash IS NULL OR answer_quote_hash ~ '^[0-9a-f]{64}$'",
            name="answer_quote_hash",
        ),
        sa.CheckConstraint(
            "basis IN ('explicit_citation','answer_anchor','verbatim',"
            "'structured_fact','semantic')",
            name="basis",
        ),
        sa.CheckConstraint(
            "contribution_score >= 0 AND contribution_score <= 1", name="contribution_score"
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        sa.CheckConstraint(
            "verification_state IN ('exact','needs_review','rejected')", name="verification_state"
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed','accepted','rejected')", name="review_state"
        ),
        schema="platform",
    )
    op.create_index(
        "ix_weighted_content_chunk_answer",
        "weighted_content_chunk",
        ["answer_task_id", "policy_version", "occurrence_id"],
        schema="platform",
    )
    _enable_rls("weighted_content_chunk")

    op.create_table(
        "content_strategy_analysis",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("platform.collection_run.id"), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "cohort_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "feature_comparison",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "recommendations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_content_strategy_analysis_pub_id"),
        sa.UniqueConstraint(
            "run_id",
            "policy_version",
            "input_hash",
            name="uq_content_strategy_analysis_version",
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash"),
        sa.CheckConstraint("status IN ('ready','partial','insufficient')", name="status"),
        schema="platform",
    )
    op.create_index(
        "ix_content_strategy_analysis_project_time",
        "content_strategy_analysis",
        ["tenant_id", "project_id", "created_at"],
        schema="platform",
    )
    _enable_rls("content_strategy_analysis")

    op.create_table(
        "project_service_entitlement",
        *_identity_columns(),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("platform.project.id"), nullable=False),
        sa.Column("service_code", sa.String(length=48), nullable=False),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("authorized_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorized_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_project_service_entitlement_pub_id"),
        sa.UniqueConstraint(
            "project_id", "service_code", "catalog_version", name="uq_project_service_entitlement"
        ),
        sa.CheckConstraint(
            "service_code IN ('ranking_test','outbound_disparagement_audit',"
            "'inbound_disparagement_audit','official_site_audit',"
            "'content_publishing_pilot')",
            name="service_code",
        ),
        sa.CheckConstraint("state IN ('inactive','active','suspended','expired')", name="state"),
        sa.CheckConstraint(
            "authorized_until IS NULL OR authorized_from IS NULL "
            "OR authorized_until > authorized_from",
            name="authorization_window",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_project_service_entitlement_project",
        "project_service_entitlement",
        ["tenant_id", "project_id", "state", "service_code"],
        schema="platform",
    )
    _enable_rls("project_service_entitlement")

    # Historical citations prove only the final-reference stage.  They are
    # deliberately backfilled with U/V/W=unobserved instead of being promoted
    # into stages the old capture did not expose.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pg_temp.uvw_safe_jsonb(value text)
        RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
          RETURN value::jsonb;
        EXCEPTION WHEN others THEN
          RETURN '[]'::jsonb;
        END $$;

        INSERT INTO platform.answer_retrieval_event
          (id,pub_id,tenant_id,project_id,run_id,answer_task_id,ordinal,queries,
           u_observation,v_observation,final_reference_observation,created_at)
        SELECT gen_random_uuid(),
               'ret_'||substr(encode(digest(task.pub_id||'|retrieval|1','sha256'),'hex'),1,26),
               task.tenant_id,run.project_id,task.run_id,task.id,1,'[]'::jsonb,
               'unobserved','unobserved','observed',task.created_at
        FROM platform.collection_task task
        JOIN platform.collection_run run ON run.id=task.run_id
        WHERE task.state='completed'
        ON CONFLICT (answer_task_id,ordinal) DO NOTHING;

        WITH citation AS (
          SELECT task.tenant_id,task.id AS answer_task_id,task.run_id,run.project_id,
                 task.created_at,item.value,item.ordinality::int AS fallback_ordinal,
                 btrim(item.value->>'url') AS raw_url,
                 lower(substring(btrim(item.value->>'url') from '^[a-zA-Z]+://([^/:?#]+)')) AS host,
                 regexp_replace(btrim(item.value->>'url'),'#.*$','') AS canonical_url
          FROM platform.collection_task task
          JOIN platform.collection_run run ON run.id=task.run_id
          CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(pg_temp.uvw_safe_jsonb(task.citations_json))='array'
                 THEN pg_temp.uvw_safe_jsonb(task.citations_json) ELSE '[]'::jsonb END
          ) WITH ORDINALITY AS item(value,ordinality)
          WHERE task.state='completed'
            AND btrim(item.value->>'url') ~* '^https?://[^/[:space:]]+'
        )
        INSERT INTO platform.source_site
          (id,pub_id,tenant_id,host,registrable_domain,created_at,updated_at)
        SELECT gen_random_uuid(),'sit_'||substr(md5(tenant_id::text||'|'||host),1,26),
               tenant_id,host,NULL,min(created_at),max(created_at)
        FROM citation WHERE host IS NOT NULL AND host<>''
        GROUP BY tenant_id,host
        ON CONFLICT (tenant_id,host) DO NOTHING;

        WITH citation AS (
          SELECT task.tenant_id,task.created_at,btrim(item.value->>'url') AS raw_url,
                 lower(substring(btrim(item.value->>'url') from '^[a-zA-Z]+://([^/:?#]+)')) AS host,
                 regexp_replace(btrim(item.value->>'url'),'#.*$','') AS canonical_url
          FROM platform.collection_task task
          CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(pg_temp.uvw_safe_jsonb(task.citations_json))='array'
                 THEN pg_temp.uvw_safe_jsonb(task.citations_json) ELSE '[]'::jsonb END
          ) AS item(value)
          WHERE task.state='completed'
            AND btrim(item.value->>'url') ~* '^https?://[^/[:space:]]+'
        )
        INSERT INTO platform.source_url
          (id,pub_id,tenant_id,site_id,canonical_url,canonical_url_hash,
           normalization_version,first_raw_url,created_at,updated_at)
        SELECT gen_random_uuid(),
               'url_'||substr(md5(citation.tenant_id::text||'|'||citation.canonical_url),1,26),
               citation.tenant_id,site.id,citation.canonical_url,
               encode(digest(citation.canonical_url,'sha256'),'hex'),'legacy-raw-v1',
               min(citation.raw_url),min(citation.created_at),max(citation.created_at)
        FROM citation
        JOIN platform.source_site site
          ON site.tenant_id=citation.tenant_id AND site.host=citation.host
        WHERE citation.host IS NOT NULL AND citation.host<>''
        GROUP BY citation.tenant_id,site.id,citation.canonical_url
        ON CONFLICT (tenant_id,canonical_url_hash,canonical_url) DO NOTHING;

        WITH citation AS (
          SELECT task.tenant_id,task.id AS answer_task_id,task.run_id,run.project_id,
                 task.created_at,item.value,item.ordinality::int AS fallback_ordinal,
                 btrim(item.value->>'url') AS raw_url,
                 regexp_replace(btrim(item.value->>'url'),'#.*$','') AS canonical_url
          FROM platform.collection_task task
          JOIN platform.collection_run run ON run.id=task.run_id
          CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(pg_temp.uvw_safe_jsonb(task.citations_json))='array'
                 THEN pg_temp.uvw_safe_jsonb(task.citations_json) ELSE '[]'::jsonb END
          ) WITH ORDINALITY AS item(value,ordinality)
          WHERE task.state='completed'
            AND btrim(item.value->>'url') ~* '^https?://[^/[:space:]]+'
        )
        INSERT INTO platform.answer_source_occurrence
          (id,pub_id,tenant_id,project_id,run_id,answer_task_id,retrieval_event_id,
           source_url_id,occurrence_ordinal,query_text,raw_url,u_state,u_rank,v_state,
           v_open_order,final_reference_state,final_reference_ordinal,w_state,title,
           summary,evidence_pub_id,captured_at,created_at)
        SELECT gen_random_uuid(),
               'uoc_'||substr(encode(digest(
                 task.pub_id||'|occurrence|'||citation.fallback_ordinal::text,'sha256'
               ),'hex'),1,26),
               citation.tenant_id,citation.project_id,citation.run_id,citation.answer_task_id,
               event.id,url.id,citation.fallback_ordinal,NULL,citation.raw_url,
               'unobserved',NULL,'unobserved',NULL,'referenced',
               CASE WHEN citation.value->>'ordinal' ~ '^[1-9][0-9]*$'
                    THEN (citation.value->>'ordinal')::int ELSE citation.fallback_ordinal END,
               'unobserved',NULLIF(citation.value->>'title',''),
               NULLIF(citation.value->>'cited_text',''),NULL,
               citation.created_at,citation.created_at
        FROM citation
        JOIN platform.collection_task task ON task.id=citation.answer_task_id
        JOIN platform.answer_retrieval_event event
          ON event.answer_task_id=citation.answer_task_id AND event.ordinal=1
        JOIN platform.source_url url
          ON url.tenant_id=citation.tenant_id
         AND url.canonical_url=citation.canonical_url
        ON CONFLICT (answer_task_id,occurrence_ordinal) DO NOTHING;

        UPDATE platform.source_document document
        SET source_url_id=url.id
        FROM platform.source_url url
        WHERE document.tenant_id=url.tenant_id
          AND url.canonical_url IN (document.url,document.canonical_url,document.final_url)
          AND document.source_url_id IS NULL;

        INSERT INTO platform.source_page_snapshot
          (id,pub_id,tenant_id,project_id,source_url_id,source_document_id,
           fetch_attempt_id,snapshot_state,final_url,http_status,title,site_name,author,
           account_name,published_at,metadata,body_object_key,body_sha256,text_sha256,
           extractor_version,captured_at,created_at)
        SELECT gen_random_uuid(),
               'snp_'||substr(encode(digest(document.pub_id||'|legacy','sha256'),'hex'),1,26),
               document.tenant_id,document.project_id,document.source_url_id,document.id,NULL,
               CASE
                 WHEN document.extract_status='ok'
                  AND document.text_cas_key IS NOT NULL
                  AND document.text_sha256 IS NOT NULL
                  AND document.extractor IS NOT NULL THEN 'succeeded'
                 WHEN document.extract_status IN ('ok','extract_empty') THEN 'partial'
                 WHEN document.extract_status='blocked' THEN 'blocked'
                 WHEN document.http_status IN (404,410) THEN 'gone'
                 ELSE 'failed'
               END,
               document.final_url,document.http_status,document.page_title,
               document.site_name,NULL,NULL,document.published_at,
               jsonb_build_object('legacy_source_document',document.pub_id),
               document.text_cas_key,document.text_sha256,document.text_sha256,
               document.extractor,document.fetched_at,document.created_at
        FROM platform.source_document document
        WHERE document.source_url_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        INSERT INTO platform.project_service_entitlement
          (id,pub_id,tenant_id,project_id,service_code,catalog_version,state,
           authorized_from,authorized_until,created_at,updated_at)
        SELECT gen_random_uuid(),
               'ent_'||substr(encode(digest(
                 project.pub_id||'|'||service.service_number::text||'|quotation_services_v2',
                 'sha256'),'hex'),1,26),
               project.tenant_id,project.id,
               CASE service.service_number
                 WHEN 1 THEN 'ranking_test'
                 WHEN 2 THEN 'outbound_disparagement_audit'
                 WHEN 3 THEN 'inbound_disparagement_audit'
                 WHEN 4 THEN 'official_site_audit'
                 WHEN 5 THEN 'content_publishing_pilot'
               END,
               'quotation_services_v2','active',min(production.created_at),NULL,
               min(production.created_at),max(production.updated_at)
        FROM reporting.formal_report_production production
        JOIN platform.project project
          ON project.pub_id=production.project_pub_id
         AND production.tenant_pub_id=(
           SELECT tenant.pub_id FROM platform.tenant tenant WHERE tenant.id=project.tenant_id
         )
        CROSS JOIN LATERAL unnest(production.services) service(service_number)
        WHERE service.service_number BETWEEN 1 AND 5
        GROUP BY project.tenant_id,project.id,project.pub_id,service.service_number
        ON CONFLICT (project_id,service_code,catalog_version) DO NOTHING;
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
                'GRANT SELECT,INSERT,UPDATE ON platform.source_site,platform.source_url,'
                'platform.answer_retrieval_event,platform.answer_source_occurrence,'
                'platform.source_fetch_attempt,platform.source_page_snapshot,'
                'platform.content_contribution_analysis,platform.weighted_content_chunk,'
                'platform.content_strategy_analysis,'
                'platform.project_service_entitlement TO %I',
                role_name
              );
            END IF;
          END LOOP;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT ON platform.source_site,platform.source_url,
              platform.answer_retrieval_event,platform.answer_source_occurrence,
              platform.source_fetch_attempt,platform.source_page_snapshot,
              platform.content_contribution_analysis,platform.weighted_content_chunk,
              platform.content_strategy_analysis,
              platform.project_service_entitlement TO geo_api;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("project_service_entitlement", schema="platform")
    op.drop_table("content_strategy_analysis", schema="platform")
    op.drop_table("weighted_content_chunk", schema="platform")
    op.drop_table("content_contribution_analysis", schema="platform")
    op.drop_table("source_page_snapshot", schema="platform")
    op.drop_table("source_fetch_attempt", schema="platform")
    op.drop_index("ix_source_document_source_url", table_name="source_document", schema="platform")
    op.drop_constraint(
        "fk_source_document_source_url", "source_document", type_="foreignkey", schema="platform"
    )
    op.drop_column("source_document", "source_url_id", schema="platform")
    op.drop_table("answer_source_occurrence", schema="platform")
    op.drop_table("answer_retrieval_event", schema="platform")
    op.drop_table("source_url", schema="platform")
    op.drop_table("source_site", schema="platform")


__all__ = ["downgrade", "upgrade"]
