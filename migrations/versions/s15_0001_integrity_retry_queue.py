"""Close Service 2 integrity gaps and make query retries first-class queue data.

Revision ID: s15_0001_integrity_retry_queue
Revises: s14_0001_sop_runtime_acl
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s15_0001_integrity_retry_queue"
down_revision: str | Sequence[str] | None = "s14_0001_sop_runtime_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str, *, schema: str = "platform") -> None:
    op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {schema}.{table}
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(f"REVOKE ALL ON {schema}.{table} FROM PUBLIC")


def _runtime_grants(table: str, privileges: str = "SELECT,INSERT,UPDATE") -> None:
    op.execute(
        f"""
        DO $acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker','geo_api'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format('GRANT {privileges} ON platform.{table} TO %I', role_name);
            END IF;
          END LOOP;
        END
        $acl$
        """
    )


def upgrade() -> None:
    # Make the customer/project tenant invariant impossible to violate.  Fail
    # before any other migration writes if historical corruption needs repair.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM platform.project project
            JOIN platform.customer customer ON customer.id=project.customer_id
            WHERE customer.tenant_id<>project.tenant_id
          ) THEN
            RAISE EXCEPTION 'cross_tenant_project_customer_association_present';
          END IF;
        END $$
        """
    )
    op.create_unique_constraint(
        "uq_customer_id_tenant_id",
        "customer",
        ["id", "tenant_id"],
        schema="platform",
    )
    op.create_foreign_key(
        "fk_project_customer_tenant",
        "project",
        "customer",
        ["customer_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )

    # Durable pre-claim + replay record around exactly one paid provider request.
    op.create_table(
        "service2_model_call",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("corpus_item_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("call_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("requested_model", sa.String(length=120), nullable=False),
        sa.Column("resolved_model", sa.String(length=120), nullable=False),
        sa.Column("catalog_snapshot", JSONB(), nullable=False),
        sa.Column("catalog_revision", sa.String(length=80), nullable=False),
        sa.Column("catalog_provider", sa.String(length=80), nullable=False),
        sa.Column("resolved_provider", sa.String(length=80), nullable=True),
        sa.Column(
            "provider_resolution_source",
            sa.String(length=40),
            nullable=False,
            server_default="not_observed",
        ),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=40), nullable=True),
        sa.Column("protocol_route", sa.String(length=80), nullable=True),
        sa.Column("gateway_host", sa.String(length=255), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("web_search_observed", sa.Boolean(), nullable=True),
        sa.Column("search_event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_origin", sa.String(length=40), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pricing_currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("input_usd_per_million_tokens", sa.Numeric(18, 6), nullable=True),
        sa.Column("output_usd_per_million_tokens", sa.Numeric(18, 6), nullable=True),
        sa.Column("web_search_usd_per_call", sa.Numeric(18, 6), nullable=True),
        sa.Column("web_search_pricing_status", sa.String(length=48), nullable=False),
        sa.Column("estimated_token_cost_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column("estimated_search_cost_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column("estimated_total_cost_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column("cost_completeness", sa.String(length=48), nullable=False),
        sa.Column("audit_completeness", sa.String(length=64), nullable=False),
        sa.Column("response_data", JSONB(), nullable=True),
        sa.Column("response_sources", JSONB(), nullable=False, server_default="[]"),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_service2_model_call_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_item_id", "tenant_id", "project_id", "batch_id"],
            [
                "platform.service2_corpus_item.id",
                "platform.service2_corpus_item.tenant_id",
                "platform.service2_corpus_item.project_id",
                "platform.service2_corpus_item.batch_id",
            ],
            name="fk_service2_model_call_item_scope",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "project_id"],
            [
                "platform.source_page_snapshot.id",
                "platform.source_page_snapshot.tenant_id",
                "platform.source_page_snapshot.project_id",
            ],
            name="fk_service2_model_call_snapshot_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service2_model_call"),
        sa.UniqueConstraint("pub_id", name="uq_service2_model_call_pub_id"),
        sa.UniqueConstraint("call_key", name="uq_service2_model_call_key"),
        sa.CheckConstraint("call_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_service2_model_call_key")),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_service2_model_input_hash")
        ),
        sa.CheckConstraint(
            "response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_service2_model_response_hash"),
        ),
        sa.CheckConstraint(
            "state IN ('claimed','succeeded','failed','ambiguous')",
            name=op.f("ck_service2_model_call_state"),
        ),
        sa.CheckConstraint(
            "search_event_count >= 0 AND provider_citation_count >= 0 "
            "AND input_tokens >= 0 AND output_tokens >= 0",
            name=op.f("ck_service2_model_call_counts"),
        ),
        sa.CheckConstraint(
            "(input_usd_per_million_tokens IS NULL OR input_usd_per_million_tokens >= 0) "
            "AND (output_usd_per_million_tokens IS NULL "
            "OR output_usd_per_million_tokens >= 0) "
            "AND (web_search_usd_per_call IS NULL OR web_search_usd_per_call >= 0) "
            "AND (estimated_token_cost_usd IS NULL OR estimated_token_cost_usd >= 0) "
            "AND (estimated_search_cost_usd IS NULL OR estimated_search_cost_usd >= 0) "
            "AND (estimated_total_cost_usd IS NULL OR estimated_total_cost_usd >= 0)",
            name=op.f("ck_service2_model_call_nonnegative_costs"),
        ),
        sa.CheckConstraint(
            "(state='succeeded' AND response_data IS NOT NULL AND response_hash IS NOT NULL "
            " AND completed_at IS NOT NULL) OR state<>'succeeded'",
            name=op.f("ck_service2_model_call_success_payload"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_model_call_item",
        "service2_model_call",
        ["corpus_item_id", "created_at"],
        schema="platform",
    )
    _rls("service2_model_call")
    _runtime_grants("service2_model_call")
    op.execute(
        """
        CREATE TRIGGER trg_service2_model_call_frozen_guard
          BEFORE INSERT OR UPDATE OR DELETE ON platform.service2_model_call
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch()
        """
    )

    # Exact Service 2 fact-set binding at report request time.
    op.add_column(
        "formal_report_production",
        sa.Column("service2_manifest_pub_id", sa.String(length=30), nullable=True),
        schema="reporting",
    )
    op.add_column(
        "formal_report_production",
        sa.Column("service2_manifest_hash", sa.String(length=64), nullable=True),
        schema="reporting",
    )
    op.create_check_constraint(
        op.f("ck_formal_production_service2_manifest_pair"),
        "formal_report_production",
        "(service2_manifest_pub_id IS NULL) = (service2_manifest_hash IS NULL) "
        "AND (service2_manifest_hash IS NULL OR service2_manifest_hash ~ '^[0-9a-f]{64}$')",
        schema="reporting",
    )

    # Query retry intent is the mutable queue projection. Execution attempts and
    # failure knowledge remain append-only audit/learning ledgers.
    op.create_table(
        "collection_query_retry_intent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_task_id", sa.Uuid(), nullable=False),
        sa.Column("retry_run_id", sa.Uuid(), nullable=True),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("trigger_mode", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("retry_depth", sa.Integer(), nullable=False),
        sa.Column("max_auto_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=False),
        sa.Column("created_by_pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_collection_retry_source_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["source_task_id", "tenant_id", "source_run_id"],
            [
                "platform.collection_task.id",
                "platform.collection_task.tenant_id",
                "platform.collection_task.run_id",
            ],
            name="fk_collection_retry_source_task_scope",
        ),
        sa.ForeignKeyConstraint(
            ["retry_run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_collection_retry_run_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_query_retry_intent"),
        sa.UniqueConstraint("pub_id", name="uq_collection_query_retry_intent_pub_id"),
        sa.UniqueConstraint("source_task_id", name="uq_collection_query_retry_source_task"),
        sa.CheckConstraint(
            "state IN ('pending','leased','enqueued','succeeded','failed','exhausted','cancelled')",
            name=op.f("ck_collection_query_retry_state"),
        ),
        sa.CheckConstraint(
            "trigger_mode IN ('automatic','manual')",
            name=op.f("ck_collection_query_retry_trigger"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 1000 AND retry_depth >= 1 AND max_auto_retries >= 0",
            name=op.f("ck_collection_query_retry_bounds"),
        ),
        sa.CheckConstraint(
            "(state='leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            " AND lease_expires_at IS NOT NULL) OR "
            "(state<>'leased' AND lease_owner IS NULL AND lease_token IS NULL "
            " AND lease_expires_at IS NULL)",
            name=op.f("ck_collection_query_retry_lease"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_query_retry_dispatch",
        "collection_query_retry_intent",
        ["state", "not_before", "priority", "created_at"],
        schema="platform",
    )
    op.create_index(
        "ix_collection_query_retry_fairness",
        "collection_query_retry_intent",
        ["capability_key", "tenant_id", "state", "not_before"],
        schema="platform",
    )
    _rls("collection_query_retry_intent")
    _runtime_grants("collection_query_retry_intent")

    op.create_table(
        "collection_query_execution_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("retry_depth", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("execution_context", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_collection_query_attempt_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id", "run_id"],
            [
                "platform.collection_task.id",
                "platform.collection_task.tenant_id",
                "platform.collection_task.run_id",
            ],
            name="fk_collection_query_attempt_task_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_query_execution_attempt"),
        sa.UniqueConstraint("pub_id", name="uq_collection_query_execution_attempt_pub_id"),
        sa.UniqueConstraint(
            "task_id", "attempt_ordinal", name="uq_collection_query_execution_attempt_ordinal"
        ),
        sa.CheckConstraint(
            "attempt_ordinal >= 1 AND retry_depth >= 0",
            name=op.f("ck_query_attempt_ord"),
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded','failed')",
            name=op.f("ck_collection_query_attempt_outcome"),
        ),
        sa.CheckConstraint(
            "outcome <> 'failed' OR error_code IS NOT NULL",
            name=op.f("ck_collection_query_attempt_failure_code"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_query_attempt_business",
        "collection_query_execution_attempt",
        ["business_key", "created_at"],
        schema="platform",
    )
    _rls("collection_query_execution_attempt")
    _runtime_grants("collection_query_execution_attempt", "SELECT,INSERT")

    op.create_table(
        "collection_failure_knowledge",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("redacted_context", JSONB(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_collection_failure_knowledge_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id", "run_id"],
            [
                "platform.collection_task.id",
                "platform.collection_task.tenant_id",
                "platform.collection_task.run_id",
            ],
            name="fk_collection_failure_knowledge_task_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_failure_knowledge"),
        sa.UniqueConstraint("pub_id", name="uq_collection_failure_knowledge_pub_id"),
        sa.UniqueConstraint(
            "run_id", "task_id", "fingerprint", name="uq_collection_failure_knowledge_event"
        ),
        sa.CheckConstraint("scope IN ('query','run')", name=op.f("ck_collection_failure_scope")),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_failure_fingerprint")),
        sa.CheckConstraint("occurrence_count >= 1", name=op.f("ck_failure_occurrence_count")),
        schema="platform",
    )
    op.create_index(
        "ix_collection_failure_learning",
        "collection_failure_knowledge",
        ["fingerprint", "created_at"],
        schema="platform",
    )
    _rls("collection_failure_knowledge")
    _runtime_grants("collection_failure_knowledge", "SELECT,INSERT")

    op.execute(
        """
        CREATE TRIGGER trg_collection_query_attempt_append_only
          BEFORE UPDATE OR DELETE ON platform.collection_query_execution_attempt
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        CREATE TRIGGER trg_collection_failure_knowledge_append_only
          BEFORE UPDATE OR DELETE ON platform.collection_failure_knowledge
          FOR EACH ROW EXECUTE FUNCTION platform.service2_reject_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM platform.service2_model_call LIMIT 1)
             OR EXISTS (SELECT 1 FROM platform.collection_query_retry_intent LIMIT 1)
             OR EXISTS (SELECT 1 FROM platform.collection_query_execution_attempt LIMIT 1)
             OR EXISTS (SELECT 1 FROM platform.collection_failure_knowledge LIMIT 1)
             OR EXISTS (
               SELECT 1 FROM reporting.formal_report_production
               WHERE service2_manifest_pub_id IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'integrity_retry_history_present_downgrade_refused';
          END IF;
        END $$
        """
    )
    op.drop_table("collection_failure_knowledge", schema="platform")
    op.drop_table("collection_query_execution_attempt", schema="platform")
    op.drop_table("collection_query_retry_intent", schema="platform")
    op.drop_constraint(
        op.f("ck_formal_production_service2_manifest_pair"),
        "formal_report_production",
        schema="reporting",
        type_="check",
    )
    op.drop_column("formal_report_production", "service2_manifest_hash", schema="reporting")
    op.drop_column("formal_report_production", "service2_manifest_pub_id", schema="reporting")
    op.drop_table("service2_model_call", schema="platform")
    op.drop_constraint(
        "fk_project_customer_tenant", "project", schema="platform", type_="foreignkey"
    )
    op.drop_constraint("uq_customer_id_tenant_id", "customer", schema="platform", type_="unique")


__all__ = ["downgrade", "upgrade"]
