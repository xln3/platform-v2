"""Add the all-U Service 2 corpus, relation, review and frozen-fact plane.

This is expand-only.  Legacy ``disparagement_judgment`` and
``formal-outbound-disparagement-v1`` facts keep their original meaning.

Revision ID: s08_0001_service2_all_u
Revises: s07_0002_execution_governance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s08_0001_service2_all_u"
down_revision: str | Sequence[str] | None = "s07_0002_execution_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
    ]


def _rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{table}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(f'REVOKE ALL ON platform."{table}" FROM PUBLIC')


def upgrade() -> None:
    # Candidate keys let every new business pointer prove tenant/project/run
    # lineage at the database boundary.  All include the already-unique id, so
    # adding them cannot collapse or reinterpret existing facts.
    op.create_unique_constraint(
        "uq_project_id_tenant_s08", "project", ["id", "tenant_id"], schema="platform"
    )
    op.create_unique_constraint(
        "uq_collection_run_scope_s08",
        "collection_run",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_collection_task_scope_s08",
        "collection_task",
        ["id", "tenant_id", "run_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_source_url_scope_s08", "source_url", ["id", "tenant_id"], schema="platform"
    )
    op.create_unique_constraint(
        "uq_source_occurrence_scope_s08",
        "answer_source_occurrence",
        ["id", "tenant_id", "project_id", "run_id", "answer_task_id", "source_url_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_source_snapshot_scope_s08",
        "source_page_snapshot",
        ["id", "tenant_id", "project_id", "source_url_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_source_snapshot_tenant_project_s08",
        "source_page_snapshot",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_source_document_scope_s08",
        "source_document",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_source_fetch_attempt_scope_s08",
        "source_fetch_attempt",
        ["id", "tenant_id", "project_id", "source_url_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_service_entitlement_scope_s08",
        "project_service_entitlement",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )

    op.create_table(
        "service2_corpus_batch",
        *_identity(),
        sa.Column("service_entitlement_id", sa.Uuid(), nullable=False),
        sa.Column("service_entitlement_pub_id", sa.String(length=30), nullable=False),
        sa.Column("service_entitlement_revision", sa.String(length=80), nullable=False),
        sa.Column("scope_revision", sa.Integer(), nullable=False),
        sa.Column("scope_selector", JSONB(), nullable=False),
        sa.Column("scope_selector_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_snapshot_boundary", sa.DateTime(timezone=True), nullable=False),
        sa.Column("corpus_policy_version", sa.String(length=80), nullable=False),
        sa.Column("judgment_policy_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("expected_occurrence_count", sa.Integer(), nullable=False),
        sa.Column("distinct_url_count", sa.Integer(), nullable=False),
        sa.Column("materialized_item_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("workflow_id", sa.String(length=500), nullable=True),
        sa.Column("coverage_cursor", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_pub_id", sa.String(length=30), nullable=False),
        sa.Column("frozen_by_pub_id", sa.String(length=30), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_service2_batch_project_scope",
        ),
        sa.ForeignKeyConstraint(
            ["service_entitlement_id", "tenant_id", "project_id"],
            [
                "platform.project_service_entitlement.id",
                "platform.project_service_entitlement.tenant_id",
                "platform.project_service_entitlement.project_id",
            ],
            name="fk_service2_batch_entitlement_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_corpus_batch_pub_id"),
        sa.UniqueConstraint(
            "tenant_id", "scope_selector_hash", name="uq_service2_corpus_batch_scope"
        ),
        sa.UniqueConstraint(
            "id", "tenant_id", "project_id", name="uq_service2_corpus_batch_scope_key"
        ),
        sa.CheckConstraint("scope_revision >= 1", name="scope_revision_positive"),
        sa.CheckConstraint("scope_selector_hash ~ '^[0-9a-f]{64}$'", name="scope_hash"),
        sa.CheckConstraint("window_start <= window_end", name="window_order"),
        sa.CheckConstraint(
            "source_snapshot_boundary >= window_end", name="snapshot_boundary_after_window"
        ),
        sa.CheckConstraint(
            "expected_occurrence_count >= 0 AND distinct_url_count >= 0 "
            "AND materialized_item_count >= 0 "
            "AND distinct_url_count <= expected_occurrence_count "
            "AND materialized_item_count <= expected_occurrence_count",
            name="coverage_counts",
        ),
        sa.CheckConstraint(
            "status IN ('draft','queued','running','paused','cancel_requested','cancelled',"
            "'review','frozen','failed')",
            name="status",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "manifest_hash IS NULL OR manifest_hash ~ '^[0-9a-f]{64}$'", name="manifest_hash"
        ),
        sa.CheckConstraint(
            "status <> 'frozen' OR (frozen_at IS NOT NULL AND frozen_by_pub_id IS NOT NULL "
            "AND manifest_hash IS NOT NULL AND materialized_item_count=expected_occurrence_count)",
            name="frozen_complete",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_project_status",
        "service2_corpus_batch",
        ["tenant_id", "project_id", "status", sa.text("created_at DESC"), "pub_id"],
        schema="platform",
    )
    _rls("service2_corpus_batch")

    op.create_table(
        "service2_corpus_batch_run",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_pub_id", sa.String(length=30), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_batch_run_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_service2_batch_run_run_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_corpus_batch_run_pub_id"),
        sa.UniqueConstraint("batch_id", "run_id", name="uq_service2_corpus_batch_run"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_service2_corpus_batch_run_order"),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_run_scope",
        "service2_corpus_batch_run",
        ["batch_id", "ordinal"],
        schema="platform",
    )
    _rls("service2_corpus_batch_run")

    op.create_table(
        "service2_corpus_item",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_pub_id", sa.String(length=30), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_pub_id", sa.String(length=30), nullable=False),
        sa.Column("answer_task_id", sa.Uuid(), nullable=False),
        sa.Column("answer_task_pub_id", sa.String(length=30), nullable=False),
        sa.Column("source_url_id", sa.Uuid(), nullable=False),
        sa.Column("source_url_pub_id", sa.String(length=30), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_pub_id", sa.String(length=30), nullable=True),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("source_document_pub_id", sa.String(length=30), nullable=True),
        sa.Column("fetch_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("fetch_attempt_pub_id", sa.String(length=30), nullable=True),
        sa.Column("raw_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("site_host", sa.String(length=253), nullable=False),
        sa.Column("occurrence_ordinal", sa.Integer(), nullable=False),
        sa.Column("u_rank", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("retrieval_query", sa.Text(), nullable=True),
        sa.Column("u_state", sa.String(length=16), nullable=False),
        sa.Column("fetch_state", sa.String(length=24), nullable=False),
        sa.Column("processing_state", sa.String(length=32), nullable=False),
        sa.Column("entity_state", sa.String(length=24), nullable=False),
        sa.Column("judgment_state", sa.String(length=24), nullable=False),
        sa.Column("review_state", sa.String(length=24), nullable=False),
        sa.Column("entered_judgment", sa.Boolean(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("manual_evidence_state", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_item_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "occurrence_id",
                "tenant_id",
                "project_id",
                "run_id",
                "answer_task_id",
                "source_url_id",
            ],
            [
                "platform.answer_source_occurrence.id",
                "platform.answer_source_occurrence.tenant_id",
                "platform.answer_source_occurrence.project_id",
                "platform.answer_source_occurrence.run_id",
                "platform.answer_source_occurrence.answer_task_id",
                "platform.answer_source_occurrence.source_url_id",
            ],
            name="fk_service2_item_occurrence_scope",
        ),
        sa.ForeignKeyConstraint(
            ["source_url_id", "tenant_id"],
            ["platform.source_url.id", "platform.source_url.tenant_id"],
            name="fk_service2_item_url_scope",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "project_id", "source_url_id"],
            [
                "platform.source_page_snapshot.id",
                "platform.source_page_snapshot.tenant_id",
                "platform.source_page_snapshot.project_id",
                "platform.source_page_snapshot.source_url_id",
            ],
            name="fk_service2_item_snapshot_scope",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "tenant_id", "project_id"],
            [
                "platform.source_document.id",
                "platform.source_document.tenant_id",
                "platform.source_document.project_id",
            ],
            name="fk_service2_item_document_scope",
        ),
        sa.ForeignKeyConstraint(
            ["fetch_attempt_id", "tenant_id", "project_id", "source_url_id"],
            [
                "platform.source_fetch_attempt.id",
                "platform.source_fetch_attempt.tenant_id",
                "platform.source_fetch_attempt.project_id",
                "platform.source_fetch_attempt.source_url_id",
            ],
            name="fk_service2_item_attempt_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_corpus_item_pub_id"),
        sa.UniqueConstraint("batch_id", "occurrence_id", name="uq_service2_corpus_item_occurrence"),
        sa.UniqueConstraint(
            "id", "tenant_id", "project_id", "batch_id", name="uq_service2_corpus_item_scope_key"
        ),
        sa.CheckConstraint("occurrence_ordinal >= 1", name="occurrence_ordinal_positive"),
        sa.CheckConstraint("u_rank IS NULL OR u_rank >= 1", name="u_rank_positive"),
        sa.CheckConstraint(
            "collection_surface IS NULL OR collection_surface IN "
            "('provider_api','consumer_web','consumer_app')",
            name="collection_surface",
        ),
        sa.CheckConstraint("u_state IN ('observed','unobserved')", name="u_state"),
        sa.CheckConstraint(
            "fetch_state IN ('queued','fetching','succeeded','partial','blocked','gone',"
            "'retry_wait','failed','unobserved')",
            name="fetch_state",
        ),
        sa.CheckConstraint(
            "processing_state IN ('queued','fetching','processed','partial','blocked','gone',"
            "'retry_wait','manual_evidence_required','unobservable','failed','cancelled')",
            name="processing_state",
        ),
        sa.CheckConstraint(
            "entity_state IN ('pending','no_entities','candidates','validated',"
            "'validation_failure','error')",
            name="entity_state",
        ),
        sa.CheckConstraint(
            "judgment_state IN ('pending','not_applicable','completed',"
            "'validation_failure','error')",
            name="judgment_state",
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed','not_applicable','in_review','accepted','rejected')",
            name="review_state",
        ),
        sa.CheckConstraint("finding_count >= 0 AND retry_count >= 0", name="counts_nonnegative"),
        sa.CheckConstraint(
            "(snapshot_id IS NULL) = (snapshot_pub_id IS NULL) "
            "AND (source_document_id IS NULL) = (source_document_pub_id IS NULL) "
            "AND (fetch_attempt_id IS NULL) = (fetch_attempt_pub_id IS NULL)",
            name="public_pointer_pairs",
        ),
        sa.CheckConstraint(
            "manual_evidence_state IN ('not_required','pending','provided','rejected')",
            name="manual_evidence_state",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        schema="platform",
    )
    op.create_index(
        "ix_service2_item_queue",
        "service2_corpus_item",
        ["batch_id", "processing_state", "captured_at", "pub_id"],
        schema="platform",
    )
    op.create_index(
        "ix_service2_item_url_context",
        "service2_corpus_item",
        ["batch_id", "source_url_id", "answer_task_id", "occurrence_ordinal"],
        schema="platform",
    )
    _rls("service2_corpus_item")

    op.create_table(
        "service2_analysis_attempt",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("corpus_item_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("result_state", sa.String(length=24), nullable=False),
        sa.Column("failure_codes", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_attempt_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_item_id", "tenant_id", "project_id", "batch_id"],
            [
                "platform.service2_corpus_item.id",
                "platform.service2_corpus_item.tenant_id",
                "platform.service2_corpus_item.project_id",
                "platform.service2_corpus_item.batch_id",
            ],
            name="fk_service2_attempt_item_scope",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "project_id"],
            [
                "platform.source_page_snapshot.id",
                "platform.source_page_snapshot.tenant_id",
                "platform.source_page_snapshot.project_id",
            ],
            name="fk_service2_attempt_snapshot_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_analysis_attempt_pub_id"),
        sa.UniqueConstraint(
            "corpus_item_id",
            "snapshot_id",
            "policy_version",
            "model",
            "input_hash",
            name="uq_service2_analysis_attempt_input",
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash"),
        sa.CheckConstraint(
            "method IN ('llm','human','dictionary_experimental','system')", name="method"
        ),
        sa.CheckConstraint(
            "result_state IN ('accepted','schema_invalid','evidence_invalid','llm_unavailable',"
            "'llm_error','no_entities','cancelled')",
            name="result_state",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_analysis_attempt_item",
        "service2_analysis_attempt",
        ["corpus_item_id", sa.text("created_at DESC"), "pub_id"],
        schema="platform",
    )
    _rls("service2_analysis_attempt")

    op.create_table(
        "service2_relation_finding",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("corpus_item_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("relation_hash", sa.String(length=64), nullable=False),
        sa.Column("ledger", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("relation_direction", sa.String(length=32), nullable=False),
        sa.Column("textual_speaker", sa.Text(), nullable=False),
        sa.Column("target_entity", sa.Text(), nullable=False),
        sa.Column("beneficiary_entity", sa.Text(), nullable=True),
        sa.Column("is_disparagement", sa.Boolean(), nullable=False),
        sa.Column("fact_anchor_state", sa.String(length=20), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
        sa.Column("evidence_quote_hash", sa.String(length=64), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("context_start", sa.Integer(), nullable=False),
        sa.Column("context_end", sa.Integer(), nullable=False),
        sa.Column("snapshot_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("visual_anchor", JSONB(), nullable=False),
        sa.Column("visual_validation_status", sa.String(length=20), nullable=False),
        sa.Column("comparison_present", sa.Boolean(), nullable=False),
        sa.Column("peer_elevated", sa.Boolean(), nullable=False),
        sa.Column("scope_narrowed", sa.Boolean(), nullable=False),
        sa.Column("industry_wide", sa.Boolean(), nullable=False),
        sa.Column("direct_target_negative", sa.Boolean(), nullable=False),
        sa.Column("secondary_position", sa.Boolean(), nullable=False),
        sa.Column("comparison_manipulated", sa.Boolean(), nullable=False),
        sa.Column("key_fact_omitted", sa.Boolean(), nullable=False),
        sa.Column("comparison_dimensions", JSONB(), nullable=False),
        sa.Column("omitted_facts", JSONB(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_status", sa.String(length=24), nullable=False),
        sa.Column("validation_failures", JSONB(), nullable=False),
        sa.Column("publisher_party", sa.Text(), nullable=True),
        sa.Column("publisher_confidence", sa.String(length=16), nullable=False),
        sa.Column("publisher_evidence", JSONB(), nullable=False),
        sa.Column("commissioner_party", sa.Text(), nullable=True),
        sa.Column("commissioner_confidence", sa.String(length=16), nullable=False),
        sa.Column("commissioner_evidence", JSONB(), nullable=False),
        sa.Column("factcheck_claim", sa.Text(), nullable=True),
        sa.Column("factcheck_verdict", sa.String(length=20), nullable=True),
        sa.Column("factcheck_evidence", JSONB(), nullable=False),
        sa.Column("factcheck_boundary", sa.Text(), nullable=True),
        sa.Column("current_review_state", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_finding_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_item_id", "tenant_id", "project_id", "batch_id"],
            [
                "platform.service2_corpus_item.id",
                "platform.service2_corpus_item.tenant_id",
                "platform.service2_corpus_item.project_id",
                "platform.service2_corpus_item.batch_id",
            ],
            name="fk_service2_finding_item_scope",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "project_id"],
            [
                "platform.source_page_snapshot.id",
                "platform.source_page_snapshot.tenant_id",
                "platform.source_page_snapshot.project_id",
            ],
            name="fk_service2_finding_snapshot_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_relation_finding_pub_id"),
        sa.UniqueConstraint(
            "corpus_item_id",
            "snapshot_id",
            "policy_version",
            "model",
            "relation_hash",
            name="uq_service2_relation_finding_version",
        ),
        sa.UniqueConstraint(
            "id", "tenant_id", "project_id", "batch_id", name="uq_service2_finding_scope_key"
        ),
        sa.CheckConstraint("relation_hash ~ '^[0-9a-f]{64}$'", name="relation_hash"),
        sa.CheckConstraint("ledger IN ('statement','exposure')", name="ledger"),
        sa.CheckConstraint("level IN ('L0','L1','L2a','L2b','L3a','L3b','L4')", name="level"),
        sa.CheckConstraint(
            "relation_direction IN ('target_negative','target_degraded','target_compared',"
            "'target_omitted','context_only')",
            name="relation_direction",
        ),
        sa.CheckConstraint(
            "fact_anchor_state IN ('present','absent','disputed','not_applicable')",
            name="fact_anchor_state",
        ),
        sa.CheckConstraint("evidence_quote_hash ~ '^[0-9a-f]{64}$'", name="quote_hash"),
        sa.CheckConstraint("snapshot_text_sha256 ~ '^[0-9a-f]{64}$'", name="snapshot_hash"),
        sa.CheckConstraint(
            "quote_start >= 0 AND quote_end > quote_start "
            "AND context_start >= 0 AND context_end > context_start "
            "AND context_start <= quote_start AND quote_end <= context_end",
            name="evidence_intervals",
        ),
        sa.CheckConstraint(
            "visual_validation_status IN ('verified','unavailable','mismatch','needs_review')",
            name="visual_status",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        sa.CheckConstraint(
            "validation_status IN ('exact','needs_review','rejected','experimental')",
            name="validation_status",
        ),
        sa.CheckConstraint(
            "publisher_confidence IN ('verified','probable','weak','unknown') "
            "AND commissioner_confidence IN ('verified','probable','weak','unknown')",
            name="attribution_confidence",
        ),
        sa.CheckConstraint(
            "(publisher_confidence <> 'unknown' OR publisher_party IS NULL) "
            "AND (commissioner_confidence <> 'unknown' OR commissioner_party IS NULL)",
            name="unknown_attribution_has_no_party",
        ),
        sa.CheckConstraint(
            "factcheck_verdict IS NULL OR factcheck_verdict IN "
            "('supported','refuted','mixed','unverifiable')",
            name="factcheck_verdict",
        ),
        sa.CheckConstraint(
            "current_review_state IN ('unreviewed','accepted','rejected','needs_changes')",
            name="review_state",
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "(ledger='exposure' AND level='L0' AND is_disparagement IS FALSE "
            "AND relation_direction='context_only') OR "
            "(ledger='statement' AND level IN ('L0','L1') AND is_disparagement IS FALSE) OR "
            "(ledger='statement' AND level IN ('L2a','L2b','L3a','L3b','L4') "
            "AND is_disparagement IS TRUE)",
            name="ledger_level_semantics",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_finding_review_queue",
        "service2_relation_finding",
        ["batch_id", "current_review_state", "level", sa.text("created_at DESC"), "pub_id"],
        schema="platform",
    )
    op.create_index(
        "ix_service2_finding_relation",
        "service2_relation_finding",
        ["batch_id", "target_entity", "textual_speaker", "level"],
        schema="platform",
    )
    _rls("service2_relation_finding")

    op.create_table(
        "service2_finding_review",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reviewer_pub_id", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("based_on_version", sa.Integer(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_review_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id", "tenant_id", "project_id", "batch_id"],
            [
                "platform.service2_relation_finding.id",
                "platform.service2_relation_finding.tenant_id",
                "platform.service2_relation_finding.project_id",
                "platform.service2_relation_finding.batch_id",
            ],
            name="fk_service2_review_finding_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_finding_review_pub_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_service2_finding_review_idem"),
        sa.UniqueConstraint("finding_id", "resulting_version", name="uq_service2_review_version"),
        sa.CheckConstraint("decision IN ('accepted','rejected','needs_changes')", name="decision"),
        sa.CheckConstraint("btrim(reason_code) <> ''", name="reason_code"),
        sa.CheckConstraint("btrim(rationale) <> ''", name="rationale"),
        sa.CheckConstraint("btrim(reviewer_pub_id) <> ''", name="reviewer"),
        sa.CheckConstraint(
            "based_on_version >= 1 AND resulting_version=based_on_version+1",
            name="version_progression",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_review_history",
        "service2_finding_review",
        ["finding_id", "resulting_version", "created_at"],
        schema="platform",
    )
    _rls("service2_finding_review")

    op.create_table(
        "service2_batch_event",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_pub_id", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_event_batch_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_batch_event_pub_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_service2_batch_event_idem"),
        sa.CheckConstraint(
            "event_type IN ('created','started','paused','resumed','retry_requested',"
            "'cancel_requested','cancelled','processing_completed','failed','frozen')",
            name="event_type",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_event_history",
        "service2_batch_event",
        ["batch_id", "created_at", "pub_id"],
        schema="platform",
    )
    _rls("service2_batch_event")

    op.create_table(
        "service2_fact_manifest",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("evidence_reference_count", sa.Integer(), nullable=False),
        sa.Column("frozen_by_pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_manifest_batch_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_service2_fact_manifest_pub_id"),
        sa.UniqueConstraint("batch_id", "revision", name="uq_service2_fact_manifest_revision"),
        sa.UniqueConstraint("batch_id", "manifest_hash", name="uq_service2_fact_manifest_hash"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="manifest_hash"),
        sa.CheckConstraint("case_count >= 0 AND evidence_reference_count >= 0", name="counts"),
        schema="platform",
    )
    op.create_index(
        "ix_service2_manifest_batch",
        "service2_fact_manifest",
        ["batch_id", sa.text("revision DESC")],
        schema="platform",
    )
    _rls("service2_fact_manifest")

    # Once frozen, neither the scope nor any child fact may be changed.  The
    # manifest is inserted before the batch transition to frozen in one
    # transaction; later report rendering is read-only.
    op.execute(
        """
        CREATE FUNCTION platform.service2_guard_frozen_batch()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_batch uuid;
        BEGIN
          IF TG_TABLE_NAME = 'service2_corpus_batch' THEN
            IF OLD.status = 'frozen' THEN
              RAISE EXCEPTION 'service2_frozen_batch_immutable';
            END IF;
            RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
          END IF;
          target_batch := CASE WHEN TG_OP='DELETE' THEN OLD.batch_id ELSE NEW.batch_id END;
          IF EXISTS (
            SELECT 1 FROM platform.service2_corpus_batch batch
            WHERE batch.id=target_batch AND batch.status='frozen'
          ) THEN
            RAISE EXCEPTION 'service2_frozen_batch_immutable';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;

        CREATE FUNCTION platform.service2_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'service2_append_only_fact_immutable';
        END $$;

        CREATE TRIGGER trg_service2_batch_frozen_guard
          BEFORE UPDATE OR DELETE ON platform.service2_corpus_batch
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_batch_run_frozen_guard
          BEFORE INSERT OR UPDATE OR DELETE ON platform.service2_corpus_batch_run
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_item_frozen_guard
          BEFORE INSERT OR UPDATE OR DELETE ON platform.service2_corpus_item
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_finding_frozen_guard
          BEFORE INSERT OR UPDATE OR DELETE ON platform.service2_relation_finding
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_analysis_attempt_frozen_guard
          BEFORE INSERT ON platform.service2_analysis_attempt
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_analysis_attempt_append_only
          BEFORE UPDATE OR DELETE ON platform.service2_analysis_attempt
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        CREATE TRIGGER trg_service2_review_frozen_guard
          BEFORE INSERT ON platform.service2_finding_review
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_review_append_only
          BEFORE UPDATE OR DELETE ON platform.service2_finding_review
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        CREATE TRIGGER trg_service2_event_frozen_guard
          BEFORE INSERT ON platform.service2_batch_event
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_event_append_only
          BEFORE UPDATE OR DELETE ON platform.service2_batch_event
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        CREATE TRIGGER trg_service2_manifest_frozen_guard
          BEFORE INSERT ON platform.service2_fact_manifest
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch();
        CREATE TRIGGER trg_service2_manifest_append_only
          BEFORE UPDATE OR DELETE ON platform.service2_fact_manifest
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        """
    )

    tables = (
        "service2_corpus_batch",
        "service2_corpus_batch_run",
        "service2_corpus_item",
        "service2_analysis_attempt",
        "service2_relation_finding",
        "service2_finding_review",
        "service2_batch_event",
        "service2_fact_manifest",
    )
    table_list = ",".join(f"platform.{table}" for table in tables)
    op.execute(
        f"""
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker','geo_api'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format('GRANT SELECT,INSERT,UPDATE ON {table_list} TO %I',role_name);
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # A populated Service 2 plane may contain frozen report facts.  Refuse a
    # destructive downgrade until an operator has explicitly archived/removed
    # those facts; legacy UVW, judgments and reports are never touched here.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM platform.service2_corpus_batch LIMIT 1) THEN
            RAISE EXCEPTION 'service2_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION platform.service2_guard_frozen_batch() CASCADE")
    op.execute("DROP FUNCTION platform.service2_reject_mutation() CASCADE")
    for table in (
        "service2_fact_manifest",
        "service2_batch_event",
        "service2_finding_review",
        "service2_relation_finding",
        "service2_analysis_attempt",
        "service2_corpus_item",
        "service2_corpus_batch_run",
        "service2_corpus_batch",
    ):
        op.drop_table(table, schema="platform")
    for constraint, table in (
        ("uq_service_entitlement_scope_s08", "project_service_entitlement"),
        ("uq_source_snapshot_tenant_project_s08", "source_page_snapshot"),
        ("uq_source_snapshot_scope_s08", "source_page_snapshot"),
        ("uq_source_document_scope_s08", "source_document"),
        ("uq_source_fetch_attempt_scope_s08", "source_fetch_attempt"),
        ("uq_source_occurrence_scope_s08", "answer_source_occurrence"),
        ("uq_source_url_scope_s08", "source_url"),
        ("uq_collection_task_scope_s08", "collection_task"),
        ("uq_collection_run_scope_s08", "collection_run"),
        ("uq_project_id_tenant_s08", "project"),
    ):
        op.drop_constraint(constraint, table, type_="unique", schema="platform")


__all__ = ["downgrade", "upgrade"]
