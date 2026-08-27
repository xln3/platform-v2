"""Create the domain-neutral knowledge evolution control plane.

Revision ID: s17_0001_knowledge_evolution
Revises: s16_0001_query_retry_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s17_0001_knowledge_evolution"
down_revision: str | Sequence[str] | None = "s16_0001_query_retry_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns(*, include_namespace: bool = True) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [sa.Column("tenant_pub_id", sa.String(30), nullable=False)]
    if include_namespace:
        columns.extend(
            [
                sa.Column("namespace", sa.String(120), nullable=False),
                sa.Column("domain", sa.String(160), nullable=False),
            ]
        )
    return columns


def _json(name: str, default: str) -> sa.Column[object]:
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text(f"'{default}'::jsonb"))


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        )
    ]


def _rls(table: str) -> None:
    op.execute(f'ALTER TABLE knowledge."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE knowledge."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY tenant_isolation ON knowledge."{table}"
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))'''
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")
    op.create_table(
        "observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("task", sa.String(120), nullable=False),
        sa.Column("surface_form", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_ref_hash", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("safe_context", sa.Text(), nullable=True),
        sa.Column("data_classification", sa.String(30), nullable=False),
        sa.Column("visibility", sa.String(30), nullable=False),
        _json("payload", "{}"),
        sa.Column("state", sa.String(30), nullable=False, server_default="observed"),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_observation"),
        sa.UniqueConstraint("pub_id", name="uq_observation_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id",
            "namespace",
            "domain",
            "idempotency_key",
            name="uq_observation_tenant_domain_idempotency",
        ),
        schema="knowledge",
    )
    op.create_index(
        "ix_observation_candidate",
        "observation",
        ["tenant_pub_id", "namespace", "domain", "normalized_key"],
        schema="knowledge",
    )

    op.create_table(
        "candidate",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("aggregation_key", sa.String(80), nullable=False),
        _json("surface_forms", "[]"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(40), nullable=False, server_default="aggregated"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(120), nullable=False, server_default="unknown"),
        sa.Column("evidence_version", sa.String(120), nullable=False, server_default="none"),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *_timestamps(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate"),
        sa.UniqueConstraint("pub_id", name="uq_candidate_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id",
            "namespace",
            "domain",
            "aggregation_key",
            name="uq_candidate_tenant_domain_key",
        ),
        schema="knowledge",
    )
    op.create_index(
        "ix_candidate_queue",
        "candidate",
        ["tenant_pub_id", "namespace", "domain", "state", "priority", "last_seen_at"],
        schema="knowledge",
    )

    op.create_table(
        "candidate_observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_pub_id", sa.String(30), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["knowledge.candidate.id"], name="fk_candidate_observation_candidate"
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["knowledge.observation.id"],
            name="fk_candidate_observation_observation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_observation"),
        sa.UniqueConstraint("candidate_id", "observation_id", name="uq_candidate_observation_pair"),
        schema="knowledge",
    )

    op.create_table(
        "knowledge_object",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("stable_id", sa.String(200), nullable=False),
        sa.Column("object_type", sa.String(80), nullable=False),
        _json("attributes", "{}"),
        sa.Column("origin", sa.String(80), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("visibility", sa.String(30), nullable=False),
        sa.Column("sync_status", sa.String(30), nullable=False, server_default="local_ahead"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_object"),
        sa.UniqueConstraint("pub_id", name="uq_knowledge_object_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id",
            "namespace",
            "domain",
            "stable_id",
            "version",
            name="uq_knowledge_object_stable_version",
        ),
        schema="knowledge",
    )

    op.create_table(
        "assertion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("subject_stable_id", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(120), nullable=False),
        sa.Column("object_stable_id", sa.String(200), nullable=True),
        _json("object_value", "{}"),
        _json("scope", "{}"),
        _json("evidence_refs", "[]"),
        sa.Column("epistemic_status", sa.String(30), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("confidence_ppm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_assertion"),
        sa.UniqueConstraint("pub_id", name="uq_assertion_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("target_stable_id", sa.String(200), nullable=True),
        _json("payload", "{}"),
        _json("alternatives", "[]"),
        _json("confidence", "{}"),
        sa.Column("model_provider", sa.String(80), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("model_version", sa.String(120), nullable=True),
        sa.Column("prompt_id", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(120), nullable=True),
        sa.Column("policy_version", sa.String(120), nullable=False),
        sa.Column("state", sa.String(40), nullable=False, server_default="proposed"),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["knowledge.candidate.id"], name="fk_proposal_candidate"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal"),
        sa.UniqueConstraint("pub_id", name="uq_proposal_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("publisher", sa.String(240), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("trust_tier", sa.String(30), nullable=False),
        sa.Column("visibility", sa.String(30), nullable=False),
        sa.Column("data_classification", sa.String(30), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["knowledge.candidate.id"], name="fk_evidence_candidate"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["knowledge.proposal.id"], name="fk_evidence_proposal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.UniqueConstraint("pub_id", name="uq_evidence_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "adjudication",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(120), nullable=False),
        _json("before_value", "{}"),
        _json("after_value", "{}"),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["knowledge.proposal.id"], name="fk_adjudication_proposal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_adjudication"),
        sa.UniqueConstraint("pub_id", name="uq_adjudication_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "change_set",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("base_release_id", sa.String(128), nullable=True),
        _json("changes", "[]"),
        _json("dependency_ids", "[]"),
        _json("conflicts", "[]"),
        sa.Column("visibility", sa.String(30), nullable=False),
        sa.Column("state", sa.String(40), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_change_set"),
        sa.UniqueConstraint("pub_id", name="uq_change_set_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "knowledge_release",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("parent_release_id", sa.String(128), nullable=True),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        _json("quality_report", "{}"),
        sa.Column("state", sa.String(30), nullable=False, server_default="published"),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_release"),
        sa.UniqueConstraint("pub_id", name="uq_knowledge_release_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id",
            "namespace",
            "domain",
            "release_id",
            name="uq_knowledge_release_domain_release",
        ),
        schema="knowledge",
    )

    op.create_table(
        "release_activation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("previous_release_id", sa.String(128), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_activation"),
        sa.UniqueConstraint("pub_id", name="uq_release_activation_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "connector_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("adapter", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("base_release_id", sa.String(128), nullable=True),
        sa.Column("upstream_release_id", sa.String(128), nullable=True),
        sa.Column("local_release_id", sa.String(128), nullable=True),
        _json("cursor", "{}"),
        _json("result", "{}"),
        sa.Column("error_code", sa.String(160), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_connector_run"),
        sa.UniqueConstraint("pub_id", name="uq_connector_run_pub_id"),
        schema="knowledge",
    )

    op.create_table(
        "inference_trace",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("task", sa.String(120), nullable=False),
        sa.Column("input_hash", sa.String(80), nullable=False),
        sa.Column("reasoning_policy", sa.String(40), nullable=False),
        sa.Column("policy_id", sa.String(120), nullable=False),
        sa.Column("policy_version", sa.String(120), nullable=False),
        sa.Column("knowledge_release_id", sa.String(128), nullable=False),
        sa.Column("knowledge_content_hash", sa.String(80), nullable=False),
        sa.Column("prompt_id", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(120), nullable=True),
        sa.Column("model_provider", sa.String(80), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("model_version", sa.String(120), nullable=True),
        sa.Column("tool_version", sa.String(120), nullable=False),
        sa.Column("adopted_model_decisions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("model_latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("cache_status", sa.String(30), nullable=False),
        _json("degradation", "[]"),
        sa.Column("data_classification", sa.String(30), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_inference_trace"),
        sa.UniqueConstraint("pub_id", name="uq_inference_trace_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id", "request_id", name="uq_inference_trace_tenant_request"
        ),
        schema="knowledge",
    )

    op.create_table(
        "semantic_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.Column("cache_key", sa.String(80), nullable=False),
        _json("value", "{}"),
        *_timestamps(),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_cache"),
        sa.UniqueConstraint("cache_key", name="uq_semantic_cache_key"),
        schema="knowledge",
    )

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(40), nullable=False),
        *_tenant_columns(),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_pub_id", sa.String(40), nullable=False),
        _json("receipt", "{}"),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
        sa.UniqueConstraint("pub_id", name="uq_audit_event_pub_id"),
        schema="knowledge",
    )

    tables = (
        "observation",
        "candidate",
        "candidate_observation",
        "knowledge_object",
        "assertion",
        "proposal",
        "evidence",
        "adjudication",
        "change_set",
        "knowledge_release",
        "release_activation",
        "connector_run",
        "inference_trace",
        "semantic_cache",
        "audit_event",
    )
    for table in tables:
        _rls(table)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION knowledge.reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append_only_table:%', TG_TABLE_NAME; END $$;
        """
    )
    for table in (
        "observation",
        "evidence",
        "adjudication",
        "knowledge_release",
        "release_activation",
        "inference_trace",
        "audit_event",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f'ON knowledge."{table}" '
            "FOR EACH ROW EXECUTE FUNCTION knowledge.reject_mutation()"
        )
    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format('GRANT USAGE ON SCHEMA knowledge TO %I', role_name);
              EXECUTE format(
                'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA knowledge TO %I',
                role_name
              );
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM knowledge.observation LIMIT 1)
             OR EXISTS (SELECT 1 FROM knowledge.knowledge_release LIMIT 1) THEN
            RAISE EXCEPTION 'knowledge_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    for table in (
        "audit_event",
        "semantic_cache",
        "inference_trace",
        "connector_run",
        "release_activation",
        "knowledge_release",
        "change_set",
        "adjudication",
        "evidence",
        "proposal",
        "assertion",
        "knowledge_object",
        "candidate_observation",
        "candidate",
        "observation",
    ):
        op.drop_table(table, schema="knowledge")
    op.execute("DROP FUNCTION IF EXISTS knowledge.reject_mutation()")
    op.execute("DROP SCHEMA IF EXISTS knowledge")
