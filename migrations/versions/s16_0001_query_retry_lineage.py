"""Freeze query terminal time and retry lineage in Service 2 batches.

Revision ID: s16_0001_query_retry_lineage
Revises: s15_0001_integrity_retry_queue
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geo_platform.tenancy.runtime_acl import (
    API_ROLE,
    WORKER_ROLE,
    migration_reconcile_sql,
)

revision: str = "s16_0001_query_retry_lineage"
down_revision: str | Sequence[str] | None = "s15_0001_integrity_retry_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pre-release environments may already be stamped at s15 from an earlier
    # draft of that additive migration. Keep upgrade idempotent for those
    # databases while a clean chain remains governed by s15's exact schema.
    op.execute(
        """
        ALTER TABLE platform.service2_model_call
          ADD COLUMN IF NOT EXISTS catalog_revision varchar(80)
            NOT NULL DEFAULT 'unknown',
          ADD COLUMN IF NOT EXISTS catalog_provider varchar(80)
            NOT NULL DEFAULT 'unknown',
          ADD COLUMN IF NOT EXISTS resolved_provider varchar(80),
          ADD COLUMN IF NOT EXISTS provider_resolution_source varchar(40)
            NOT NULL DEFAULT 'not_observed',
          ADD COLUMN IF NOT EXISTS protocol_route varchar(80),
          ADD COLUMN IF NOT EXISTS gateway_host varchar(255),
          ADD COLUMN IF NOT EXISTS provider_response_id varchar(255),
          ADD COLUMN IF NOT EXISTS pricing_currency varchar(3)
            NOT NULL DEFAULT 'USD',
          ADD COLUMN IF NOT EXISTS input_usd_per_million_tokens numeric(18,6),
          ADD COLUMN IF NOT EXISTS output_usd_per_million_tokens numeric(18,6),
          ADD COLUMN IF NOT EXISTS web_search_usd_per_call numeric(18,6),
          ADD COLUMN IF NOT EXISTS web_search_pricing_status varchar(48)
            NOT NULL DEFAULT 'unknown',
          ADD COLUMN IF NOT EXISTS estimated_token_cost_usd numeric(20,10),
          ADD COLUMN IF NOT EXISTS estimated_search_cost_usd numeric(20,10),
          ADD COLUMN IF NOT EXISTS estimated_total_cost_usd numeric(20,10),
          ADD COLUMN IF NOT EXISTS cost_completeness varchar(48)
            NOT NULL DEFAULT 'not_computable',
          ADD COLUMN IF NOT EXISTS audit_completeness varchar(64)
            NOT NULL DEFAULT 'legacy_schema_repair'
        """
    )

    # Planning time remains CollectionTask.created_at. This nullable additive
    # field records terminal time for new writes; legacy rows deliberately use
    # the read-side COALESCE(updated_at) fallback instead of rewriting history.
    op.add_column(
        "collection_task",
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.create_index(
        "ix_collection_task_terminal_resolution",
        "collection_task",
        ["run_id", "business_key", "terminal_at"],
        schema="platform",
    )
    # Mixed ordering matches the future dispatcher: eligible time first,
    # operator-directed priority descending, then stable FIFO. Tenant remains
    # in the index so a fair scheduler can rotate without table scans.
    op.drop_index(
        "ix_collection_query_retry_dispatch",
        table_name="collection_query_retry_intent",
        schema="platform",
    )
    op.execute(
        "CREATE INDEX ix_collection_query_retry_dispatch "
        "ON platform.collection_query_retry_intent "
        "(state,not_before,priority DESC,created_at,tenant_id)"
    )

    # These columns are nullable only for immutable pre-s16 batch ledgers. New
    # code always writes the complete group, enforced by the all-or-none check.
    op.add_column(
        "service2_corpus_batch_query",
        sa.Column("root_run_id", sa.Uuid(), nullable=True),
        schema="platform",
    )
    op.add_column(
        "service2_corpus_batch_query",
        sa.Column("root_run_pub_id", sa.String(length=30), nullable=True),
        schema="platform",
    )
    op.add_column(
        "service2_corpus_batch_query",
        sa.Column("business_key", sa.String(length=255), nullable=True),
        schema="platform",
    )
    op.add_column(
        "service2_corpus_batch_query",
        sa.Column("retry_depth", sa.Integer(), nullable=True),
        schema="platform",
    )
    op.add_column(
        "service2_corpus_batch_query",
        sa.Column("resolved_task_terminal_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.create_foreign_key(
        op.f("fk_service2_batch_query_root_run_scope"),
        "service2_corpus_batch_query",
        "collection_run",
        ["root_run_id", "tenant_id", "project_id"],
        ["id", "tenant_id", "project_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_check_constraint(
        op.f("ck_service2_batch_query_retry_resolution_group"),
        "service2_corpus_batch_query",
        "(root_run_id IS NULL AND root_run_pub_id IS NULL AND business_key IS NULL "
        " AND retry_depth IS NULL AND resolved_task_terminal_at IS NULL) OR "
        "(root_run_id IS NOT NULL AND root_run_pub_id IS NOT NULL "
        " AND business_key IS NOT NULL AND retry_depth IS NOT NULL "
        " AND retry_depth >= 0 AND resolved_task_terminal_at IS NOT NULL)",
        schema="platform",
    )
    op.create_index(
        "uq_service2_batch_query_logical_query",
        "service2_corpus_batch_query",
        ["batch_id", "root_run_id", "business_key"],
        unique=True,
        postgresql_where=sa.text("root_run_id IS NOT NULL"),
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_query_retry_lineage",
        "service2_corpus_batch_query",
        ["batch_id", "root_run_id", "retry_depth", "ordinal"],
        postgresql_where=sa.text("root_run_id IS NOT NULL"),
        schema="platform",
    )

    # ``completed`` is the production CollectionTask success state. ``done``
    # remains accepted only so immutable legacy batch rows stay readable.
    op.drop_constraint(
        op.f("ck_service2_batch_query_terminal_state"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_service2_batch_query_truth"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_service2_batch_query_terminal_state"),
        "service2_corpus_batch_query",
        "task_state IN ('done','completed','failed')",
        schema="platform",
    )

    op.create_check_constraint(
        op.f("ck_service2_batch_query_truth"),
        "service2_corpus_batch_query",
        "(outcome='succeeded' AND task_state IN ('done','completed') AND answer_present) OR "
        "(outcome='failed' AND task_state='failed')",
        schema="platform",
    )

    # This head migration is the database-side reconciliation point.  It uses
    # the same closed-world manifest as role provisioning and verification so
    # rerunning either path cannot silently broaden a runtime role.
    op.execute(migration_reconcile_sql(API_ROLE))
    op.execute(migration_reconcile_sql(WORKER_ROLE))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM platform.service2_corpus_batch_query
            WHERE root_run_id IS NOT NULL
          ) OR EXISTS (
            SELECT 1 FROM platform.collection_task WHERE terminal_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'query_retry_lineage_history_present_downgrade_refused';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        op.f("ck_service2_batch_query_truth"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_service2_batch_query_terminal_state"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_service2_batch_query_terminal_state"),
        "service2_corpus_batch_query",
        "task_state IN ('done','failed')",
        schema="platform",
    )
    op.create_check_constraint(
        op.f("ck_service2_batch_query_truth"),
        "service2_corpus_batch_query",
        "(outcome='succeeded' AND task_state='done' AND answer_present) OR "
        "(outcome='failed' AND task_state='failed')",
        schema="platform",
    )
    op.drop_index(
        "ix_service2_batch_query_retry_lineage",
        table_name="service2_corpus_batch_query",
        schema="platform",
    )
    op.drop_index(
        "uq_service2_batch_query_logical_query",
        table_name="service2_corpus_batch_query",
        schema="platform",
    )
    op.drop_constraint(
        op.f("ck_service2_batch_query_retry_resolution_group"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_service2_batch_query_root_run_scope"),
        "service2_corpus_batch_query",
        schema="platform",
        type_="foreignkey",
    )
    for column in (
        "resolved_task_terminal_at",
        "retry_depth",
        "business_key",
        "root_run_pub_id",
        "root_run_id",
    ):
        op.drop_column("service2_corpus_batch_query", column, schema="platform")
    op.drop_index(
        "ix_collection_task_terminal_resolution",
        table_name="collection_task",
        schema="platform",
    )
    op.drop_column("collection_task", "terminal_at", schema="platform")
    op.drop_index(
        "ix_collection_query_retry_dispatch",
        table_name="collection_query_retry_intent",
        schema="platform",
    )
    op.create_index(
        "ix_collection_query_retry_dispatch",
        "collection_query_retry_intent",
        ["state", "not_before", "priority", "created_at"],
        schema="platform",
    )


__all__ = ["downgrade", "upgrade"]
