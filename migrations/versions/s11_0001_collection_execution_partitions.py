"""Add the compact collection execution-plan and partition persistence plane.

This revision persists deterministic ordinal partitions and constant-size
workflow-start commands.  It does not start Temporal workflows, perform
external I/O, select routes/resources, or implement a collection worker.

Revision ID: s11_0001_execution_partitions
Revises: s10_0001_submission_transactions
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s11_0001_execution_partitions"
down_revision: str | Sequence[str] | None = "s10_0001_submission_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256 = "{column} ~ '^[0-9a-f]{{64}}$'"
_OPAQUE_REF = "{column} ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$'"
_TABLES = (
    "collection_execution_plan_v2",
    "collection_execution_partition_v2",
    "collection_execution_start_outbox_v2",
)
_ENTRYPOINTS = (
    "create_collection_execution_plan_v2",
    "create_collection_execution_partition_v2",
    "finalize_collection_execution_plan_v2",
    "stage_collection_partition_workflow_start_v2",
    "claim_collection_execution_start_outbox_v2",
    "finalize_collection_execution_start_outbox_v2",
    "read_collection_execution_control_v2",
    "advance_collection_execution_partition_v2",
    "claim_collection_execution_reconciliation_v2",
    "cancel_collection_execution_partition_v2",
    "finalize_collection_execution_partition_v2",
)


def _identity_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _scope_constraints(table: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name=f"fk_{table}_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name=f"fk_{table}_project_scope",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("pub_id", name=f"uq_{table}_pub_id"),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name=f"uq_{table}_id_scope",
        ),
        sa.CheckConstraint("version > 0", name=f"ck_{table}_version"),
    ]


def _create_tables() -> None:
    op.create_table(
        "collection_execution_plan_v2",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("config_revision_pub_id", sa.String(length=30), nullable=False),
        sa.Column("config_revision_hash", sa.String(length=64), nullable=False),
        sa.Column("capability_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("comparison_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("campaign_binding_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("specification_hash", sa.String(length=64), nullable=False),
        sa.Column("slot_generator_version", sa.String(length=80), nullable=False),
        sa.Column("membership_digest_version", sa.String(length=80), nullable=False),
        sa.Column("membership_hash", sa.String(length=64), nullable=False),
        sa.Column("expected_slot_count", sa.BigInteger(), nullable=False),
        sa.Column("execution_partition_size", sa.BigInteger(), nullable=False),
        sa.Column("workflow_page_size", sa.Integer(), nullable=False),
        sa.Column("expected_partition_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "materialized_partition_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "materialization_cursor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "materialization_state",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("last_partition_digest", sa.String(length=64)),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="assembling"),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_execution_plan_v2"),
        sa.ForeignKeyConstraint(
            ["campaign_id", "tenant_id", "project_id"],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
            ],
            name="fk_collection_execution_plan_campaign_scope",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "tenant_id",
            "project_id",
            name="uq_collection_execution_plan_campaign",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "plan_digest",
            name="uq_collection_execution_plan_lineage",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-execution-plan-v1' "
            "AND slot_generator_version = 'collection-slot-generator-v1' "
            "AND membership_digest_version = 'collection-membership-chain-v1'",
            name=op.f("ck_collection_execution_plan_versions"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="config_revision_hash")
            + " AND "
            + _SHA256.format(column="specification_hash")
            + " AND "
            + _SHA256.format(column="membership_hash")
            + " AND "
            + _SHA256.format(column="plan_digest")
            + " AND (last_partition_digest IS NULL OR "
            + _SHA256.format(column="last_partition_digest")
            + ")",
            name=op.f("ck_collection_execution_plan_hashes"),
        ),
        sa.CheckConstraint(
            "expected_slot_count > 0 AND execution_partition_size > 0 "
            "AND workflow_page_size BETWEEN 1 AND 2048 "
            "AND expected_partition_count = "
            "((expected_slot_count - 1) / execution_partition_size) + 1 "
            "AND materialized_partition_count BETWEEN 0 AND expected_partition_count "
            "AND materialization_cursor = materialized_partition_count",
            name=op.f("ck_collection_execution_plan_counts"),
        ),
        sa.CheckConstraint(
            "(materialization_state='pending' AND materialization_cursor=0) OR "
            "(materialization_state='materializing' AND materialization_cursor>0 "
            " AND materialization_cursor<expected_partition_count) OR "
            "(materialization_state='complete' "
            " AND materialization_cursor=expected_partition_count)",
            name=op.f("ck_collection_execution_plan_materialization"),
        ),
        sa.CheckConstraint(
            "(state='assembling' AND frozen_at IS NULL "
            " AND last_partition_digest IS NULL) OR "
            "(state='frozen' AND frozen_at IS NOT NULL "
            " AND last_partition_digest IS NOT NULL "
            " AND materialization_state='complete')",
            name=op.f("ck_collection_execution_plan_state"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_execution_plan_campaign",
        "collection_execution_plan_v2",
        ["tenant_id", "project_id", "campaign_id", "state"],
        schema="platform",
    )

    op.create_table(
        "collection_execution_partition_v2",
        *_identity_columns(),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("partition_index", sa.BigInteger(), nullable=False),
        sa.Column("start_slot_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("end_slot_ordinal_exclusive", sa.BigInteger(), nullable=False),
        sa.Column("partition_digest", sa.String(length=64), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("cursor_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoint_digest", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_ref", sa.String(length=128), nullable=False),
        sa.Column(
            "reconciliation_checkpoint_ref",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("control_revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("last_page_start", sa.BigInteger()),
        sa.Column("last_page_end_exclusive", sa.BigInteger()),
        sa.Column("last_page_digest", sa.String(length=64)),
        sa.Column("workflow_command_id", sa.Uuid()),
        sa.Column("workflow_id", sa.String(length=30)),
        sa.Column("workflow_command_digest", sa.String(length=64)),
        sa.Column("control_policy_revision", sa.String(length=128)),
        sa.Column("workflow_staged_at", sa.DateTime(timezone=True)),
        sa.Column("reconcile_after", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_ref", sa.String(length=128)),
        sa.Column("reconciliation_reason", sa.String(length=128)),
        sa.Column("reconciliation_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("cancellation_ref", sa.String(length=128)),
        sa.Column("cancellation_reason", sa.String(length=128)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_ref", sa.String(length=128)),
        sa.Column("terminal_digest", sa.String(length=64)),
        sa.Column("terminal_state", sa.String(length=30)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="planned"),
        *_scope_constraints("collection_execution_partition_v2"),
        sa.ForeignKeyConstraint(
            ["plan_id", "tenant_id", "project_id", "campaign_id", "plan_digest"],
            [
                "platform.collection_execution_plan_v2.id",
                "platform.collection_execution_plan_v2.tenant_id",
                "platform.collection_execution_plan_v2.project_id",
                "platform.collection_execution_plan_v2.campaign_id",
                "platform.collection_execution_plan_v2.plan_digest",
            ],
            name="fk_collection_execution_partition_plan_lineage",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "plan_id",
            name="uq_collection_execution_partition_plan_scope",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "partition_index",
            name="uq_collection_execution_partition_index",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "start_slot_ordinal",
            "end_slot_ordinal_exclusive",
            name="uq_collection_execution_partition_range",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "partition_digest",
            name="uq_collection_execution_partition_digest",
        ),
        sa.UniqueConstraint(
            "workflow_command_id",
            name="uq_collection_execution_partition_command",
        ),
        sa.UniqueConstraint("workflow_id", name="uq_collection_execution_partition_workflow"),
        sa.CheckConstraint(
            _SHA256.format(column="plan_digest")
            + " AND "
            + _SHA256.format(column="partition_digest")
            + " AND "
            + _SHA256.format(column="checkpoint_digest")
            + " AND (last_page_digest IS NULL OR "
            + _SHA256.format(column="last_page_digest")
            + ") AND (workflow_command_digest IS NULL OR "
            + _SHA256.format(column="workflow_command_digest")
            + ") AND (terminal_digest IS NULL OR "
            + _SHA256.format(column="terminal_digest")
            + ")",
            name=op.f("ck_collection_execution_partition_hashes"),
        ),
        sa.CheckConstraint(
            "partition_index >= 0 AND start_slot_ordinal >= 0 "
            "AND end_slot_ordinal_exclusive > start_slot_ordinal "
            "AND cursor BETWEEN start_slot_ordinal AND end_slot_ordinal_exclusive "
            "AND cursor_version >= 0 AND control_revision > 0",
            name=op.f("ck_collection_execution_partition_range_cursor"),
        ),
        sa.CheckConstraint(
            "(last_page_start IS NULL AND last_page_end_exclusive IS NULL "
            " AND last_page_digest IS NULL) OR "
            "(last_page_start IS NOT NULL AND last_page_end_exclusive IS NOT NULL "
            " AND last_page_digest IS NOT NULL "
            " AND last_page_start >= start_slot_ordinal "
            " AND last_page_end_exclusive > last_page_start "
            " AND last_page_end_exclusive = cursor "
            " AND last_page_end_exclusive <= end_slot_ordinal_exclusive)",
            name=op.f("ck_collection_execution_partition_page_checkpoint"),
        ),
        sa.CheckConstraint(
            "state IN ('planned','start_staged','running','awaiting_terminal',"
            "'completed','reconciling','cancelled')",
            name=op.f("ck_collection_execution_partition_state"),
        ),
        sa.CheckConstraint(
            "((workflow_command_id IS NULL AND workflow_id IS NULL "
            "  AND workflow_command_digest IS NULL AND workflow_staged_at IS NULL "
            "  AND control_policy_revision IS NULL AND reconcile_after IS NULL) OR "
            " (workflow_command_id IS NOT NULL AND workflow_id IS NOT NULL "
            "  AND workflow_command_digest IS NOT NULL AND workflow_staged_at IS NOT NULL "
            "  AND control_policy_revision IS NOT NULL "
            "  AND reconcile_after IS NOT NULL)) "
            "AND (state='planned' OR workflow_command_id IS NOT NULL "
            "     OR (state='cancelled' AND workflow_command_id IS NULL))",
            name=op.f("ck_collection_execution_partition_workflow_identity"),
        ),
        sa.CheckConstraint(
            "(state IN ('awaiting_terminal','completed') "
            " AND cursor=end_slot_ordinal_exclusive) OR "
            "(state NOT IN ('awaiting_terminal','completed') "
            " AND cursor<end_slot_ordinal_exclusive)",
            name=op.f("ck_collection_execution_partition_completion"),
        ),
        sa.CheckConstraint(
            "(state='reconciling' AND reconciliation_ref IS NOT NULL "
            " AND reconciliation_reason IS NOT NULL "
            " AND reconciliation_claimed_at IS NOT NULL) OR "
            "(state<>'reconciling' AND reconciliation_ref IS NULL "
            " AND reconciliation_reason IS NULL "
            " AND reconciliation_claimed_at IS NULL)",
            name=op.f("ck_collection_execution_partition_reconciliation"),
        ),
        sa.CheckConstraint(
            "(state='cancelled' AND cancellation_ref IS NOT NULL "
            " AND cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL "
            " AND cursor<end_slot_ordinal_exclusive) OR "
            "(state<>'cancelled' AND cancellation_ref IS NULL "
            " AND cancellation_reason IS NULL AND cancelled_at IS NULL)",
            name=op.f("ck_collection_execution_partition_cancellation"),
        ),
        sa.CheckConstraint(
            "(state='completed' AND terminal_ref IS NOT NULL "
            " AND terminal_digest IS NOT NULL "
            " AND terminal_state IN ('completed','completed_with_reconciliation') "
            " AND terminal_at IS NOT NULL) OR "
            "(state<>'completed' AND terminal_ref IS NULL "
            " AND terminal_digest IS NULL AND terminal_state IS NULL "
            " AND terminal_at IS NULL)",
            name=op.f("ck_collection_execution_partition_terminal_proof"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_execution_partition_plan_cursor",
        "collection_execution_partition_v2",
        ["tenant_id", "project_id", "plan_id", "partition_index"],
        schema="platform",
    )
    op.create_index(
        "ix_collection_execution_partition_reconcile",
        "collection_execution_partition_v2",
        ["state", "reconcile_after", "partition_index"],
        schema="platform",
        postgresql_where=sa.text("state = 'start_staged'"),
    )

    command_keys = (
        "ARRAY['schema_version','outbox_type','workflow_type','task_queue',"
        "'payload_schema_version','workflow_id','command_digest','campaign_id',"
        "'campaign_pub_id','partition_pub_id','partition_digest','plan_digest',"
        "'cursor','campaign_reference','workflow_input']::text[]"
    )
    reference_keys = (
        "ARRAY['schema_version','tenant_id','project_id','campaign_pub_id',"
        "'config_revision_pub_id','config_revision_hash','specification_hash',"
        "'slot_generator_version','membership_digest_version','membership_hash',"
        "'partition_pub_id','start_slot_ordinal','end_slot_ordinal_exclusive',"
        "'cursor','page_size']::text[]"
    )
    input_keys = (
        "ARRAY['schema_version','tenant_pub_id','project_pub_id',"
        "'config_revision_pub_id','config_revision_hash','campaign_pub_id',"
        "'specification_hash','partition_pub_id','partition_digest',"
        "'membership_digest_version','membership_digest',"
        "'canonical_enumeration_version','slot_generator_version',"
        "'start_slot_ordinal','end_slot_ordinal_exclusive','cursor','page_size',"
        "'checkpoint_ref','checkpoint_digest','reconciliation_checkpoint_ref',"
        "'capability_policy_revision','control_policy_revision',"
        "'comparison_policy_revision','scheduling_window_start_utc',"
        "'scheduling_window_end_utc','idempotency_key','generation',"
        "'continue_as_new_after_pages']::text[]"
    )
    op.create_table(
        "collection_execution_start_outbox_v2",
        *_identity_columns(),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("partition_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("partition_digest", sa.String(length=64), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=30), nullable=False),
        sa.Column("outbox_type", sa.String(length=80), nullable=False),
        sa.Column("workflow_type", sa.String(length=80), nullable=False),
        sa.Column("payload_schema_version", sa.String(length=80), nullable=False),
        sa.Column("task_queue", sa.String(length=128), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("command_json", JSONB(), nullable=False),
        sa.Column("outbox_state", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_ref", sa.String(length=128)),
        sa.Column("claim_fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("temporal_run_id", sa.String(length=128)),
        sa.Column("last_error_code", sa.String(length=128)),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_execution_start_outbox_v2"),
        sa.ForeignKeyConstraint(
            ["partition_id", "tenant_id", "project_id", "plan_id"],
            [
                "platform.collection_execution_partition_v2.id",
                "platform.collection_execution_partition_v2.tenant_id",
                "platform.collection_execution_partition_v2.project_id",
                "platform.collection_execution_partition_v2.plan_id",
            ],
            name="fk_collection_execution_start_outbox_partition",
        ),
        sa.UniqueConstraint(
            "partition_id",
            name="uq_collection_execution_start_outbox_partition",
        ),
        sa.UniqueConstraint("command_id", name="uq_collection_execution_start_outbox_command"),
        sa.UniqueConstraint("workflow_id", name="uq_collection_execution_start_outbox_workflow"),
        sa.CheckConstraint(
            _SHA256.format(column="plan_digest")
            + " AND "
            + _SHA256.format(column="partition_digest")
            + " AND "
            + _SHA256.format(column="command_digest"),
            name=op.f("ck_collection_execution_start_outbox_hashes"),
        ),
        sa.CheckConstraint(
            "outbox_type='geo_collection_v2' "
            "AND workflow_type='GeoCollectionV2Workflow' "
            "AND payload_schema_version='collection-workflow-v2' "
            "AND task_queue='geo-platform-v2-collection-v2' "
            "AND outbox_state IN ('pending','claimed','published','failed','cancelled') "
            "AND attempt_count>=0 AND claim_fence>=0",
            name=op.f("ck_collection_execution_start_outbox_constants"),
        ),
        sa.CheckConstraint(
            "(outbox_state='pending' AND claim_ref IS NULL AND claimed_at IS NULL "
            " AND published_at IS NULL AND temporal_run_id IS NULL "
            " AND last_error_code IS NULL) OR "
            "(outbox_state='claimed' AND claim_ref IS NOT NULL AND claimed_at IS NOT NULL "
            " AND published_at IS NULL AND temporal_run_id IS NULL) OR "
            "(outbox_state='published' AND claim_ref IS NOT NULL "
            " AND claimed_at IS NOT NULL AND published_at IS NOT NULL "
            " AND temporal_run_id IS NOT NULL AND last_error_code IS NULL) OR "
            "(outbox_state='failed' AND claim_ref IS NOT NULL AND claimed_at IS NOT NULL "
            " AND published_at IS NULL AND temporal_run_id IS NULL "
            " AND last_error_code IS NOT NULL) OR "
            "(outbox_state='cancelled' AND published_at IS NULL "
            " AND temporal_run_id IS NULL)",
            name=op.f("ck_collection_execution_start_outbox_delivery"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(command_json)='object' "
            f"AND command_json ?& {command_keys} "
            f"AND command_json - {command_keys} = '{{}}'::jsonb "
            "AND jsonb_typeof(command_json->'workflow_input')='object' "
            "AND jsonb_typeof(command_json->'campaign_reference')='object' "
            f"AND (command_json->'campaign_reference') ?& {reference_keys} "
            f"AND (command_json->'campaign_reference') - {reference_keys} = '{{}}'::jsonb "
            f"AND (command_json->'workflow_input') ?& {input_keys} "
            f"AND (command_json->'workflow_input') - {input_keys} = '{{}}'::jsonb "
            "AND octet_length(command_json::text)<=8192",
            name=op.f("ck_collection_execution_start_outbox_payload"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_execution_start_outbox_pending",
        "collection_execution_start_outbox_v2",
        ["outbox_state", "staged_at", "pub_id"],
        schema="platform",
    )


def _enable_rls() -> None:
    for table in _TABLES:
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON platform."{table}"
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )


def _create_digest_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION platform.collection_canonical_json_s11(p_value jsonb)
        RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE result_value text;
        DECLARE item record;
        DECLARE separator text := '';
        BEGIN
          CASE jsonb_typeof(p_value)
            WHEN 'object' THEN
              result_value := '{';
              FOR item IN
                SELECT entry.key,entry.value
                  FROM jsonb_each(p_value) entry
                 ORDER BY entry.key COLLATE "C"
              LOOP
                result_value := result_value || separator ||
                  to_jsonb(item.key)::text || ':' ||
                  platform.collection_canonical_json_s11(item.value);
                separator := ',';
              END LOOP;
              RETURN result_value || '}';
            WHEN 'array' THEN
              result_value := '[';
              FOR item IN
                SELECT entry.value
                  FROM jsonb_array_elements(p_value) WITH ORDINALITY entry(value,ordinal)
                 ORDER BY entry.ordinal
              LOOP
                result_value := result_value || separator ||
                  platform.collection_canonical_json_s11(item.value);
                separator := ',';
              END LOOP;
              RETURN result_value || ']';
            ELSE
              RETURN p_value::text;
          END CASE;
        END
        $$;

        CREATE FUNCTION platform.assert_collection_execution_context_s11(p_tenant_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        BEGIN
          IF p_tenant_id IS NULL OR
             NULLIF(current_setting('app.tenant_id',true),'') IS DISTINCT FROM
               p_tenant_id::text THEN
            RAISE EXCEPTION 'collection_execution_tenant_context_required';
          END IF;
          IF current_setting('TimeZone') <> 'UTC' THEN
            RAISE EXCEPTION 'collection_execution_utc_required';
          END IF;
        END
        $$;

        CREATE FUNCTION platform.collection_execution_plan_digest_s11(
          p_campaign_id uuid,
          p_campaign_pub_id text,
          p_tenant_id uuid,
          p_project_id uuid,
          p_config_revision_pub_id text,
          p_config_revision_hash text,
          p_specification_hash text,
          p_slot_generator_version text,
          p_membership_digest_version text,
          p_membership_hash text,
          p_expected_slot_count bigint,
          p_execution_partition_size bigint,
          p_workflow_page_size integer
        ) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path = pg_catalog, public
        AS $$
        DECLARE canonical_value text;
        BEGIN
          canonical_value := format(
            '{"campaign_id":"%s","campaign_pub_id":"%s",'
            '"config_revision_hash":"%s","config_revision_pub_id":"%s",'
            '"execution_partition_size":%s,"expected_slot_count":%s,'
            '"membership_digest_version":"%s","membership_hash":"%s",'
            '"project_id":"%s","slot_generator_version":"%s",'
            '"specification_hash":"%s","tenant_id":"%s",'
            '"version":"collection-execution-plan-v1","workflow_page_size":%s}',
            p_campaign_id,p_campaign_pub_id,p_config_revision_hash,
            p_config_revision_pub_id,p_execution_partition_size,
            p_expected_slot_count,p_membership_digest_version,p_membership_hash,
            p_project_id,p_slot_generator_version,p_specification_hash,p_tenant_id,
            p_workflow_page_size
          );
          RETURN encode(digest(canonical_value,'sha256'),'hex');
        END
        $$;

        CREATE FUNCTION platform.collection_execution_partition_digest_s11(
          p_campaign_id uuid,
          p_campaign_pub_id text,
          p_specification_hash text,
          p_slot_generator_version text,
          p_membership_digest_version text,
          p_membership_hash text,
          p_partition_index bigint,
          p_start_slot_ordinal bigint,
          p_end_slot_ordinal_exclusive bigint
        ) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path = pg_catalog, public
        AS $$
        DECLARE canonical_value text;
        BEGIN
          canonical_value := format(
            '{"campaign_id":"%s","campaign_pub_id":"%s",'
            '"end_slot_ordinal_exclusive":%s,'
            '"membership_digest_version":"%s","membership_hash":"%s",'
            '"partition_index":%s,"slot_generator_version":"%s",'
            '"specification_hash":"%s","start_slot_ordinal":%s,'
            '"version":"collection-execution-partition-v1"}',
            p_campaign_id,p_campaign_pub_id,p_end_slot_ordinal_exclusive,
            p_membership_digest_version,p_membership_hash,p_partition_index,
            p_slot_generator_version,p_specification_hash,p_start_slot_ordinal
          );
          RETURN encode(digest(canonical_value,'sha256'),'hex');
        END
        $$;

        CREATE FUNCTION platform.collection_execution_workflow_id_s11(
          p_campaign_id uuid,
          p_partition_digest text
        ) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path = pg_catalog, public
        AS $$
        DECLARE canonical_value jsonb;
        BEGIN
          canonical_value := jsonb_build_object(
            'campaign_id',p_campaign_id::text,
            'partition_digest',p_partition_digest,
            'version','collection-workflow-start-command-v1'
          );
          RETURN 'cwf2_' || substr(
            encode(digest(
              platform.collection_canonical_json_s11(canonical_value),'sha256'
            ),'hex'),1,24
          );
        END
        $$;

        CREATE FUNCTION platform.collection_execution_workflow_command_digest_s11(
          p_digest_basis jsonb
        ) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT
        SET search_path = pg_catalog, platform, public
        AS $$
          SELECT encode(digest(
            platform.collection_canonical_json_s11(p_digest_basis),'sha256'
          ),'hex')
        $$;
        """
    )


def _create_row_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION platform.guard_collection_execution_plan_s11()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'collection_execution_plan_delete_forbidden';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.state<>'assembling' OR NEW.version<>1 OR
               NEW.materialized_partition_count<>0 OR
               NEW.materialization_cursor<>0 OR
               NEW.materialization_state<>'pending' OR
               NEW.frozen_at IS NOT NULL OR NEW.last_partition_digest IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_plan_insert_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state='frozen' THEN
            RAISE EXCEPTION 'collection_execution_plan_frozen_immutable';
          END IF;
          IF ROW(
            NEW.id,NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.campaign_id,
            NEW.schema_version,NEW.config_revision_pub_id,NEW.config_revision_hash,
            NEW.capability_policy_revision,NEW.comparison_policy_revision,
            NEW.campaign_binding_policy_revision,
            NEW.specification_hash,NEW.slot_generator_version,
            NEW.membership_digest_version,NEW.membership_hash,
            NEW.expected_slot_count,NEW.execution_partition_size,
            NEW.workflow_page_size,NEW.expected_partition_count,NEW.plan_digest,
            NEW.created_at
          ) IS DISTINCT FROM ROW(
            OLD.id,OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.campaign_id,
            OLD.schema_version,OLD.config_revision_pub_id,OLD.config_revision_hash,
            OLD.capability_policy_revision,OLD.comparison_policy_revision,
            OLD.campaign_binding_policy_revision,
            OLD.specification_hash,OLD.slot_generator_version,
            OLD.membership_digest_version,OLD.membership_hash,
            OLD.expected_slot_count,OLD.execution_partition_size,
            OLD.workflow_page_size,OLD.expected_partition_count,OLD.plan_digest,
            OLD.created_at
          ) OR NEW.version<>OLD.version+1 OR NEW.updated_at<OLD.updated_at THEN
            RAISE EXCEPTION 'collection_execution_plan_identity_immutable';
          END IF;
          IF OLD.state='assembling' AND NEW.state='assembling' THEN
            IF NEW.frozen_at IS NOT NULL OR NEW.last_partition_digest IS NOT NULL OR
               NEW.materialization_cursor<>OLD.materialization_cursor+1 OR
               NEW.materialized_partition_count<>OLD.materialized_partition_count+1 THEN
              RAISE EXCEPTION 'collection_execution_plan_progress_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state='assembling' AND NEW.state='frozen' THEN
            IF NEW.materialization_cursor<>OLD.materialization_cursor OR
               NEW.materialized_partition_count<>OLD.materialized_partition_count OR
               NEW.materialization_state<>OLD.materialization_state OR
               NEW.materialization_state<>'complete' OR NEW.frozen_at IS NULL OR
               NEW.last_partition_digest IS NULL THEN
              RAISE EXCEPTION 'collection_execution_plan_freeze_invalid';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'collection_execution_plan_transition_forbidden';
        END
        $$;

        CREATE FUNCTION platform.guard_collection_execution_partition_s11()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'collection_execution_partition_delete_forbidden';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.state<>'planned' OR NEW.version<>1 OR NEW.cursor_version<>0 OR
               NEW.control_revision<>1 OR
               NEW.cursor<>NEW.start_slot_ordinal OR
               NEW.workflow_command_id IS NOT NULL OR
               NEW.reconciliation_ref IS NOT NULL OR NEW.cancellation_ref IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_insert_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(
            NEW.id,NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.plan_id,
            NEW.campaign_id,NEW.plan_digest,NEW.partition_index,
            NEW.start_slot_ordinal,NEW.end_slot_ordinal_exclusive,
            NEW.partition_digest,NEW.created_at
          ) IS DISTINCT FROM ROW(
            OLD.id,OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.plan_id,
            OLD.campaign_id,OLD.plan_digest,OLD.partition_index,
            OLD.start_slot_ordinal,OLD.end_slot_ordinal_exclusive,
            OLD.partition_digest,OLD.created_at
          ) OR NEW.version<>OLD.version+1 OR NEW.updated_at<OLD.updated_at THEN
            RAISE EXCEPTION 'collection_execution_partition_identity_immutable';
          END IF;
          IF OLD.state='planned' AND NEW.state='start_staged' THEN
            IF NEW.cursor<>OLD.cursor OR NEW.cursor_version<>OLD.cursor_version OR
               NEW.checkpoint_digest<>OLD.checkpoint_digest OR
               NEW.checkpoint_ref<>OLD.checkpoint_ref OR
               NEW.reconciliation_checkpoint_ref<>
                 OLD.reconciliation_checkpoint_ref OR
               NEW.control_revision<>OLD.control_revision OR
               NEW.workflow_command_id IS NULL OR
               NEW.control_policy_revision IS NULL OR
               NEW.reconcile_after IS NULL OR
               NEW.reconciliation_ref IS NOT NULL OR NEW.cancellation_ref IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_start_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state IN (
               'planned','start_staged','running','reconciling'
             ) AND NEW.state='cancelled' THEN
            IF NEW.cursor<>OLD.cursor OR NEW.cursor_version<>OLD.cursor_version OR
               NEW.checkpoint_digest<>OLD.checkpoint_digest OR
               NEW.checkpoint_ref<>OLD.checkpoint_ref OR
               NEW.reconciliation_checkpoint_ref<>
                 OLD.reconciliation_checkpoint_ref OR
               NEW.control_revision<>OLD.control_revision+1 OR
               NEW.workflow_command_id IS DISTINCT FROM OLD.workflow_command_id OR
               NEW.workflow_id IS DISTINCT FROM OLD.workflow_id OR
               NEW.workflow_command_digest IS DISTINCT FROM
                 OLD.workflow_command_digest OR
               NEW.control_policy_revision IS DISTINCT FROM
                 OLD.control_policy_revision OR
               NEW.cancellation_ref IS NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_cancel_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state IN ('start_staged','running') AND
             NEW.state IN ('running','awaiting_terminal') THEN
            IF NEW.cursor<=OLD.cursor OR NEW.cursor_version<>OLD.cursor_version+1 OR
               NEW.control_revision<>OLD.control_revision OR
               NEW.workflow_command_id IS DISTINCT FROM OLD.workflow_command_id OR
               NEW.workflow_id IS DISTINCT FROM OLD.workflow_id OR
               NEW.workflow_command_digest IS DISTINCT FROM OLD.workflow_command_digest OR
               NEW.control_policy_revision IS DISTINCT FROM
                 OLD.control_policy_revision OR
               NEW.workflow_staged_at IS DISTINCT FROM OLD.workflow_staged_at OR
               NEW.reconcile_after IS DISTINCT FROM OLD.reconcile_after OR
               NEW.reconciliation_ref IS NOT NULL OR NEW.cancellation_ref IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_advance_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state IN ('start_staged','running') AND NEW.state='reconciling' THEN
            IF NEW.cursor<>OLD.cursor OR NEW.cursor_version<>OLD.cursor_version OR
               NEW.checkpoint_digest<>OLD.checkpoint_digest OR
               NEW.checkpoint_ref<>OLD.checkpoint_ref OR
               NEW.reconciliation_checkpoint_ref<>
                 OLD.reconciliation_checkpoint_ref OR
               NEW.control_revision<>OLD.control_revision+1 OR
               NEW.workflow_command_id IS DISTINCT FROM OLD.workflow_command_id OR
               NEW.control_policy_revision IS DISTINCT FROM
                 OLD.control_policy_revision OR
               NEW.reconciliation_ref IS NULL OR NEW.cancellation_ref IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_reconcile_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.state='awaiting_terminal' AND NEW.state='completed' THEN
            IF NEW.cursor<>OLD.cursor OR NEW.cursor_version<>OLD.cursor_version OR
               NEW.control_revision<>OLD.control_revision OR
               NEW.checkpoint_digest<>OLD.checkpoint_digest OR
               NEW.checkpoint_ref<>OLD.checkpoint_ref OR
               NEW.reconciliation_checkpoint_ref<>
                 OLD.reconciliation_checkpoint_ref OR
               NEW.workflow_command_id IS DISTINCT FROM OLD.workflow_command_id OR
               NEW.workflow_id IS DISTINCT FROM OLD.workflow_id OR
               NEW.workflow_command_digest IS DISTINCT FROM
                 OLD.workflow_command_digest OR
               NEW.control_policy_revision IS DISTINCT FROM
                 OLD.control_policy_revision OR
               NEW.workflow_staged_at IS DISTINCT FROM OLD.workflow_staged_at OR
               NEW.reconcile_after IS DISTINCT FROM OLD.reconcile_after OR
               NEW.terminal_ref IS NULL OR NEW.terminal_digest IS NULL OR
               NEW.terminal_state IS NULL OR NEW.terminal_at IS NULL THEN
              RAISE EXCEPTION 'collection_execution_partition_terminal_invalid';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'collection_execution_partition_transition_forbidden';
        END
        $$;

        CREATE FUNCTION platform.guard_collection_execution_start_outbox_s11()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'collection_execution_start_outbox_delete_forbidden';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.outbox_state<>'pending' OR NEW.version<>1 OR
               NEW.attempt_count<>0 OR NEW.claim_fence<>0 OR
               NEW.claim_ref IS NOT NULL THEN
              RAISE EXCEPTION 'collection_execution_start_outbox_insert_invalid';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(
            NEW.id,NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.plan_id,
            NEW.partition_id,NEW.campaign_id,NEW.plan_digest,
            NEW.partition_digest,NEW.command_id,NEW.command_digest,
            NEW.workflow_id,NEW.outbox_type,NEW.workflow_type,
            NEW.payload_schema_version,NEW.task_queue,NEW.cursor,
            NEW.command_json,NEW.staged_at,NEW.created_at
          ) IS DISTINCT FROM ROW(
            OLD.id,OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.plan_id,
            OLD.partition_id,OLD.campaign_id,OLD.plan_digest,
            OLD.partition_digest,OLD.command_id,OLD.command_digest,
            OLD.workflow_id,OLD.outbox_type,OLD.workflow_type,
            OLD.payload_schema_version,OLD.task_queue,OLD.cursor,
            OLD.command_json,OLD.staged_at,OLD.created_at
          ) OR NEW.version<>OLD.version+1 OR NEW.updated_at<OLD.updated_at THEN
            RAISE EXCEPTION 'collection_execution_start_outbox_identity_immutable';
          END IF;
          IF OLD.outbox_state IN ('published','cancelled') THEN
            RAISE EXCEPTION 'collection_execution_start_outbox_terminal_immutable';
          END IF;
          IF OLD.outbox_state IN ('pending','failed') AND
             NEW.outbox_state='claimed' AND
             NEW.attempt_count=OLD.attempt_count+1 AND
             NEW.claim_fence=OLD.claim_fence+1 AND NEW.claim_ref IS NOT NULL AND
             NEW.claimed_at IS NOT NULL THEN
            RETURN NEW;
          END IF;
          IF OLD.outbox_state='claimed' AND NEW.outbox_state IN ('published','failed') AND
             NEW.attempt_count=OLD.attempt_count AND
             NEW.claim_fence=OLD.claim_fence AND NEW.claim_ref=OLD.claim_ref THEN
            RETURN NEW;
          END IF;
          IF OLD.outbox_state IN ('pending','failed') AND
             NEW.outbox_state='cancelled' THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'collection_execution_start_outbox_transition_forbidden';
        END
        $$;

        CREATE TRIGGER collection_execution_plan_guard_s11
          BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_plan_v2
          FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_execution_plan_s11();
        CREATE TRIGGER collection_execution_partition_guard_s11
          BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_partition_v2
          FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_execution_partition_s11();
        CREATE TRIGGER collection_execution_start_outbox_guard_s11
          BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_start_outbox_v2
          FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_execution_start_outbox_s11();
        """
    )


def _create_plan_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION platform.create_collection_execution_plan_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_campaign_id uuid,
          p_plan_id uuid,
          p_plan_pub_id text,
          p_execution_partition_size bigint,
          p_workflow_page_size integer,
          p_expected_plan_digest text,
          p_now timestamptz
        ) RETURNS TABLE(plan_id uuid,plan_version integer,created boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform, public
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE campaign_row record;
        DECLARE existing_row platform.collection_execution_plan_v2%ROWTYPE;
        DECLARE calculated_digest text;
        DECLARE calculated_partition_count bigint;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_execution_partition_size<=0 OR
             p_workflow_page_size NOT BETWEEN 1 AND 2048 OR
             p_plan_pub_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,29}$' OR
             p_expected_plan_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'collection_execution_plan_request_invalid';
          END IF;
          SELECT campaign.id,campaign.pub_id AS campaign_pub_id,
                 campaign.tenant_id,campaign.project_id,
                 config.pub_id AS config_revision_pub_id,
                 config.lifecycle_state AS config_lifecycle_state,
                 config.capability_registry_revision AS capability_policy_revision,
                 config.comparison_policy_revision,
                 campaign.binding_policy_revision AS campaign_binding_policy_revision,
                 campaign.config_revision_hash,campaign.specification_hash,
                 campaign.slot_generator_version,campaign.membership_digest_version,
                 campaign.membership_hash,campaign.expected_slot_count,
                 campaign.materialized_slot_count,campaign.materialization_cursor,
                 campaign.materialization_state,campaign.state
            INTO campaign_row
            FROM platform.collection_campaign campaign
            JOIN platform.collection_config_revision_v2 config
              ON config.id=campaign.config_revision_id
             AND config.tenant_id=campaign.tenant_id
             AND config.project_id=campaign.project_id
           WHERE campaign.id=p_campaign_id AND campaign.tenant_id=p_tenant_id
             AND campaign.project_id=p_project_id
           FOR SHARE OF campaign,config;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'collection_execution_campaign_not_found';
          END IF;
          IF campaign_row.config_lifecycle_state<>'active' OR
             campaign_row.state<>'frozen' OR
             campaign_row.materialization_state<>'complete' OR
             campaign_row.expected_slot_count<=0 OR
             campaign_row.materialized_slot_count<>campaign_row.expected_slot_count OR
             campaign_row.materialization_cursor<>campaign_row.expected_slot_count OR
             campaign_row.membership_hash IS NULL OR
             campaign_row.membership_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'collection_execution_campaign_not_exactly_frozen';
          END IF;
          calculated_partition_count :=
            ((campaign_row.expected_slot_count-1)/p_execution_partition_size)+1;
          calculated_digest := platform.collection_execution_plan_digest_s11(
            campaign_row.id,campaign_row.campaign_pub_id,p_tenant_id,p_project_id,
            campaign_row.config_revision_pub_id,campaign_row.config_revision_hash,
            campaign_row.specification_hash,campaign_row.slot_generator_version,
            campaign_row.membership_digest_version,campaign_row.membership_hash,
            campaign_row.expected_slot_count,p_execution_partition_size,
            p_workflow_page_size
          );
          IF calculated_digest<>p_expected_plan_digest OR
             p_plan_pub_id<>('cep2_'||substr(calculated_digest,1,24)) THEN
            RAISE EXCEPTION 'collection_execution_plan_identity_drift';
          END IF;
          SELECT * INTO existing_row
            FROM platform.collection_execution_plan_v2 plan
           WHERE plan.tenant_id=p_tenant_id AND plan.project_id=p_project_id
             AND (plan.campaign_id=p_campaign_id OR plan.id=p_plan_id
                  OR plan.pub_id=p_plan_pub_id)
           ORDER BY CASE WHEN plan.campaign_id=p_campaign_id THEN 0 ELSE 1 END
           LIMIT 1 FOR UPDATE;
          IF FOUND THEN
            IF ROW(
              existing_row.id,existing_row.pub_id,existing_row.campaign_id,
              existing_row.config_revision_pub_id,existing_row.config_revision_hash,
              existing_row.capability_policy_revision,
              existing_row.comparison_policy_revision,
              existing_row.campaign_binding_policy_revision,
              existing_row.specification_hash,existing_row.slot_generator_version,
              existing_row.membership_digest_version,existing_row.membership_hash,
              existing_row.expected_slot_count,existing_row.execution_partition_size,
              existing_row.workflow_page_size,existing_row.expected_partition_count,
              existing_row.plan_digest
            ) IS DISTINCT FROM ROW(
              p_plan_id,p_plan_pub_id,p_campaign_id,
              campaign_row.config_revision_pub_id,campaign_row.config_revision_hash,
              campaign_row.capability_policy_revision,
              campaign_row.comparison_policy_revision,
              campaign_row.campaign_binding_policy_revision,
              campaign_row.specification_hash,campaign_row.slot_generator_version,
              campaign_row.membership_digest_version,campaign_row.membership_hash,
              campaign_row.expected_slot_count,p_execution_partition_size,
              p_workflow_page_size,calculated_partition_count,calculated_digest
            ) THEN
              RAISE EXCEPTION 'collection_execution_plan_exact_replay_drift';
            END IF;
            RETURN QUERY SELECT existing_row.id,existing_row.version,false;
            RETURN;
          END IF;
          INSERT INTO platform.collection_execution_plan_v2(
            id,pub_id,tenant_id,project_id,campaign_id,schema_version,
            config_revision_pub_id,config_revision_hash,
            capability_policy_revision,comparison_policy_revision,
            campaign_binding_policy_revision,specification_hash,
            slot_generator_version,membership_digest_version,membership_hash,
            expected_slot_count,execution_partition_size,workflow_page_size,
            expected_partition_count,plan_digest,created_at,updated_at
          ) VALUES (
            p_plan_id,p_plan_pub_id,p_tenant_id,p_project_id,p_campaign_id,
            'collection-execution-plan-v1',campaign_row.config_revision_pub_id,
            campaign_row.config_revision_hash,campaign_row.capability_policy_revision,
            campaign_row.comparison_policy_revision,
            campaign_row.campaign_binding_policy_revision,
            campaign_row.specification_hash,
            campaign_row.slot_generator_version,campaign_row.membership_digest_version,
            campaign_row.membership_hash,campaign_row.expected_slot_count,
            p_execution_partition_size,p_workflow_page_size,
            calculated_partition_count,calculated_digest,p_now,p_now
          );
          RETURN QUERY SELECT p_plan_id,1,true;
        END
        $$;

        CREATE FUNCTION platform.create_collection_execution_partition_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_partition_pub_id text,
          p_partition_index bigint,
          p_start_slot_ordinal bigint,
          p_end_slot_ordinal_exclusive bigint,
          p_expected_partition_digest text,
          p_checkpoint_ref text,
          p_checkpoint_digest text,
          p_reconciliation_checkpoint_ref text,
          p_expected_prior_cursor bigint,
          p_expected_plan_version integer,
          p_now timestamptz
        ) RETURNS TABLE(partition_id uuid,next_partition_index bigint,
                        plan_version integer,created boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform, public
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE plan_row platform.collection_execution_plan_v2%ROWTYPE;
        DECLARE campaign_row platform.collection_campaign%ROWTYPE;
        DECLARE existing_row platform.collection_execution_partition_v2%ROWTYPE;
        DECLARE calculated_start bigint;
        DECLARE calculated_end bigint;
        DECLARE calculated_digest text;
        DECLARE next_cursor bigint;
        DECLARE next_version integer;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_partition_index<0 OR
             p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             p_checkpoint_digest !~ '^[0-9a-f]{64}$' OR
             p_checkpoint_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_reconciliation_checkpoint_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_partition_pub_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,29}$' THEN
            RAISE EXCEPTION 'collection_execution_partition_request_invalid';
          END IF;
          SELECT * INTO plan_row
            FROM platform.collection_execution_plan_v2 plan
           WHERE plan.id=p_plan_id AND plan.tenant_id=p_tenant_id
             AND plan.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'collection_execution_plan_not_found';
          END IF;
          SELECT * INTO campaign_row
            FROM platform.collection_campaign campaign
           WHERE campaign.id=plan_row.campaign_id
             AND campaign.tenant_id=p_tenant_id
             AND campaign.project_id=p_project_id FOR SHARE;
          IF campaign_row.state<>'frozen' OR
             campaign_row.expected_slot_count<>plan_row.expected_slot_count OR
             campaign_row.materialized_slot_count<>plan_row.expected_slot_count OR
             campaign_row.materialization_cursor<>plan_row.expected_slot_count OR
             campaign_row.materialization_state<>'complete' OR
             campaign_row.specification_hash<>plan_row.specification_hash OR
             campaign_row.membership_hash<>plan_row.membership_hash THEN
            RAISE EXCEPTION 'collection_execution_campaign_lineage_drift';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM platform.collection_config_revision_v2 config
             WHERE config.pub_id=plan_row.config_revision_pub_id
               AND config.tenant_id=p_tenant_id
               AND config.project_id=p_project_id
               AND config.lifecycle_state='active'
               AND config.revision_hash=plan_row.config_revision_hash
          ) THEN
            RAISE EXCEPTION 'collection_execution_plan_config_not_active';
          END IF;
          SELECT * INTO existing_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id
             AND (partition.id=p_partition_id OR
                  (partition.plan_id=p_plan_id AND
                   partition.partition_index=p_partition_index) OR
                  partition.pub_id=p_partition_pub_id)
           ORDER BY CASE WHEN partition.plan_id=p_plan_id AND
                              partition.partition_index=p_partition_index THEN 0 ELSE 1 END
           LIMIT 1 FOR UPDATE;
          IF FOUND THEN
            IF ROW(
              existing_row.id,existing_row.pub_id,existing_row.plan_id,
              existing_row.campaign_id,existing_row.plan_digest,
              existing_row.partition_index,existing_row.start_slot_ordinal,
              existing_row.end_slot_ordinal_exclusive,existing_row.partition_digest,
              existing_row.checkpoint_ref,existing_row.checkpoint_digest,
              existing_row.reconciliation_checkpoint_ref
            ) IS DISTINCT FROM ROW(
              p_partition_id,p_partition_pub_id,p_plan_id,plan_row.campaign_id,
              plan_row.plan_digest,p_partition_index,p_start_slot_ordinal,
              p_end_slot_ordinal_exclusive,p_expected_partition_digest,
              p_checkpoint_ref,p_checkpoint_digest,p_reconciliation_checkpoint_ref
            ) OR plan_row.materialization_cursor<p_partition_index+1 THEN
              RAISE EXCEPTION 'collection_execution_partition_exact_replay_drift';
            END IF;
            RETURN QUERY SELECT existing_row.id,plan_row.materialization_cursor,
                                plan_row.version,false;
            RETURN;
          END IF;
          IF plan_row.state<>'assembling' OR
             plan_row.materialization_cursor<>p_expected_prior_cursor OR
             plan_row.materialization_cursor<>p_partition_index OR
             plan_row.version<>p_expected_plan_version OR
             p_partition_index>=plan_row.expected_partition_count THEN
            RAISE EXCEPTION 'collection_execution_partition_plan_cas_failed';
          END IF;
          calculated_start := p_partition_index*plan_row.execution_partition_size;
          calculated_end := least(
            calculated_start+plan_row.execution_partition_size,
            plan_row.expected_slot_count
          );
          calculated_digest := platform.collection_execution_partition_digest_s11(
            plan_row.campaign_id,campaign_row.pub_id,plan_row.specification_hash,
            plan_row.slot_generator_version,plan_row.membership_digest_version,
            plan_row.membership_hash,p_partition_index,calculated_start,calculated_end
          );
          IF p_start_slot_ordinal<>calculated_start OR
             p_end_slot_ordinal_exclusive<>calculated_end OR
             p_expected_partition_digest<>calculated_digest OR
             p_partition_pub_id<>('cpt2_'||substr(calculated_digest,1,24)) THEN
            RAISE EXCEPTION 'collection_execution_partition_range_or_digest_drift';
          END IF;
          INSERT INTO platform.collection_execution_partition_v2(
            id,pub_id,tenant_id,project_id,plan_id,campaign_id,plan_digest,
            partition_index,start_slot_ordinal,end_slot_ordinal_exclusive,
            partition_digest,cursor,cursor_version,checkpoint_digest,
            checkpoint_ref,reconciliation_checkpoint_ref,control_revision,
            created_at,updated_at
          ) VALUES (
            p_partition_id,p_partition_pub_id,p_tenant_id,p_project_id,p_plan_id,
            plan_row.campaign_id,plan_row.plan_digest,p_partition_index,
            calculated_start,calculated_end,calculated_digest,calculated_start,0,
            p_checkpoint_digest,p_checkpoint_ref,p_reconciliation_checkpoint_ref,1,
            p_now,p_now
          );
          next_cursor := p_partition_index+1;
          UPDATE platform.collection_execution_plan_v2 plan SET
            materialized_partition_count=next_cursor,
            materialization_cursor=next_cursor,
            materialization_state=CASE
              WHEN next_cursor=plan.expected_partition_count THEN 'complete'
              ELSE 'materializing' END,
            version=plan.version+1,updated_at=p_now
           WHERE plan.id=p_plan_id AND plan.version=p_expected_plan_version;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'collection_execution_partition_plan_cas_lost';
          END IF;
          next_version := p_expected_plan_version+1;
          RETURN QUERY SELECT p_partition_id,next_cursor,next_version,true;
        END
        $$;

        CREATE FUNCTION platform.finalize_collection_execution_plan_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_expected_plan_digest text,
          p_expected_partition_count bigint,
          p_expected_last_partition_digest text,
          p_expected_plan_version integer,
          p_now timestamptz
        ) RETURNS TABLE(plan_id uuid,plan_version integer,frozen boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform, public
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE plan_row platform.collection_execution_plan_v2%ROWTYPE;
        DECLARE partition_total bigint;
        DECLARE gap_total bigint;
        DECLARE first_ordinal bigint;
        DECLARE final_ordinal bigint;
        DECLARE final_digest text;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_expected_plan_digest !~ '^[0-9a-f]{64}$' OR
             p_expected_last_partition_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'collection_execution_plan_finalize_request_invalid';
          END IF;
          SELECT * INTO plan_row
            FROM platform.collection_execution_plan_v2 plan
           WHERE plan.id=p_plan_id AND plan.tenant_id=p_tenant_id
             AND plan.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_plan_not_found'; END IF;
          IF plan_row.state='frozen' THEN
            IF plan_row.plan_digest<>p_expected_plan_digest OR
               plan_row.expected_partition_count<>p_expected_partition_count OR
               plan_row.last_partition_digest<>p_expected_last_partition_digest THEN
              RAISE EXCEPTION 'collection_execution_plan_finalize_replay_drift';
            END IF;
            RETURN QUERY SELECT plan_row.id,plan_row.version,false;
            RETURN;
          END IF;
          IF plan_row.state<>'assembling' OR
             plan_row.plan_digest<>p_expected_plan_digest OR
             plan_row.expected_partition_count<>p_expected_partition_count OR
             plan_row.materialization_state<>'complete' OR
             plan_row.materialized_partition_count<>plan_row.expected_partition_count OR
             plan_row.materialization_cursor<>plan_row.expected_partition_count OR
             plan_row.version<>p_expected_plan_version THEN
            RAISE EXCEPTION 'collection_execution_plan_finalize_cas_failed';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM platform.collection_campaign campaign
            JOIN platform.collection_config_revision_v2 config
              ON config.id=campaign.config_revision_id
             AND config.tenant_id=campaign.tenant_id
             AND config.project_id=campaign.project_id
            WHERE campaign.id=plan_row.campaign_id
              AND campaign.tenant_id=p_tenant_id
              AND campaign.project_id=p_project_id
              AND campaign.state='frozen'
              AND campaign.expected_slot_count=plan_row.expected_slot_count
              AND campaign.materialized_slot_count=plan_row.expected_slot_count
              AND campaign.materialization_cursor=plan_row.expected_slot_count
              AND campaign.membership_hash=plan_row.membership_hash
              AND config.lifecycle_state='active'
          ) THEN
            RAISE EXCEPTION 'collection_execution_plan_config_not_active';
          END IF;
          SELECT count(*),count(*) FILTER (
                   WHERE ordered.start_slot_ordinal<>coalesce(ordered.previous_end,0)
                      OR ordered.partition_index<>ordered.row_number-1
                 ),min(ordered.start_slot_ordinal),max(ordered.end_slot_ordinal_exclusive)
            INTO partition_total,gap_total,first_ordinal,final_ordinal
            FROM (
              SELECT partition.partition_index,partition.start_slot_ordinal,
                     partition.end_slot_ordinal_exclusive,
                     lag(partition.end_slot_ordinal_exclusive) OVER (
                       ORDER BY partition.partition_index
                     ) AS previous_end,
                     row_number() OVER (ORDER BY partition.partition_index) AS row_number
                FROM platform.collection_execution_partition_v2 partition
               WHERE partition.plan_id=p_plan_id AND partition.tenant_id=p_tenant_id
                 AND partition.project_id=p_project_id
            ) ordered;
          SELECT partition.partition_digest INTO final_digest
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.plan_id=p_plan_id AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id
           ORDER BY partition.partition_index DESC LIMIT 1;
          IF partition_total<>plan_row.expected_partition_count OR gap_total<>0 OR
             first_ordinal<>0 OR final_ordinal<>plan_row.expected_slot_count OR
             final_digest<>p_expected_last_partition_digest THEN
            RAISE EXCEPTION 'collection_execution_partition_coverage_invalid';
          END IF;
          UPDATE platform.collection_execution_plan_v2 plan SET
            state='frozen',last_partition_digest=final_digest,
            frozen_at=p_now,version=plan.version+1,updated_at=p_now
           WHERE plan.id=p_plan_id AND plan.version=p_expected_plan_version
             AND plan.state='assembling';
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_plan_freeze_cas_lost'; END IF;
          RETURN QUERY SELECT p_plan_id,p_expected_plan_version+1,true;
        END
        $$;
        """
    )


def _create_execution_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION platform.stage_collection_partition_workflow_start_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_plan_digest text,
          p_expected_partition_digest text,
          p_expected_cursor bigint,
          p_expected_control_revision bigint,
          p_expected_partition_version integer,
          p_command_id uuid,
          p_command_json jsonb,
          p_reconcile_after timestamptz,
          p_now timestamptz
        ) RETURNS TABLE(command_id uuid,partition_version integer,staged boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform, public
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE plan_row platform.collection_execution_plan_v2%ROWTYPE;
        DECLARE partition_row platform.collection_execution_partition_v2%ROWTYPE;
        DECLARE scope_row record;
        DECLARE calculated_workflow_id text;
        DECLARE calculated_command_digest text;
        DECLARE command_workflow_id text;
        DECLARE command_digest_value text;
        DECLARE campaign_reference jsonb;
        DECLARE workflow_input jsonb;
        DECLARE digest_basis jsonb;
        DECLARE expected_reference jsonb;
        DECLARE expected_command jsonb;
        DECLARE existing_outbox platform.collection_execution_start_outbox_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_reconcile_after<=p_now OR
             p_expected_plan_digest !~ '^[0-9a-f]{64}$' OR
             p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             jsonb_typeof(p_command_json)<>'object' OR
             octet_length(p_command_json::text)>8192 THEN
            RAISE EXCEPTION 'collection_execution_start_request_invalid';
          END IF;
          SELECT * INTO plan_row
            FROM platform.collection_execution_plan_v2 plan
           WHERE plan.id=p_plan_id AND plan.tenant_id=p_tenant_id
             AND plan.project_id=p_project_id FOR SHARE;
          SELECT * INTO partition_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR UPDATE;
          IF plan_row.id IS NULL OR partition_row.id IS NULL THEN
            RAISE EXCEPTION 'collection_execution_start_target_not_found';
          END IF;
          IF plan_row.state<>'frozen' OR
             plan_row.plan_digest<>p_expected_plan_digest OR
             partition_row.plan_digest<>plan_row.plan_digest OR
             partition_row.partition_digest<>p_expected_partition_digest OR
             partition_row.cursor<>p_expected_cursor OR
             partition_row.control_revision<>p_expected_control_revision OR
             p_expected_cursor<>partition_row.start_slot_ordinal THEN
            RAISE EXCEPTION 'collection_execution_start_lineage_drift';
          END IF;
          SELECT campaign.pub_id AS campaign_pub_id,
                 tenant.pub_id AS tenant_pub_id,project.pub_id AS project_pub_id,
                 config.lifecycle_state AS config_lifecycle_state
            INTO scope_row
            FROM platform.collection_campaign campaign
            JOIN platform.tenant tenant ON tenant.id=campaign.tenant_id
            JOIN platform.project project
              ON project.id=campaign.project_id AND project.tenant_id=campaign.tenant_id
            JOIN platform.collection_config_revision_v2 config
              ON config.id=campaign.config_revision_id
             AND config.tenant_id=campaign.tenant_id
             AND config.project_id=campaign.project_id
           WHERE campaign.id=plan_row.campaign_id AND campaign.state='frozen'
             AND campaign.expected_slot_count=plan_row.expected_slot_count
             AND campaign.materialized_slot_count=plan_row.expected_slot_count
             AND campaign.materialization_cursor=plan_row.expected_slot_count
             AND campaign.membership_hash=plan_row.membership_hash
             AND campaign.tenant_id=p_tenant_id AND campaign.project_id=p_project_id;
          IF scope_row.campaign_pub_id IS NULL OR
             scope_row.config_lifecycle_state<>'active' THEN
            RAISE EXCEPTION 'collection_execution_campaign_lineage_drift';
          END IF;
          command_workflow_id := p_command_json->>'workflow_id';
          command_digest_value := p_command_json->>'command_digest';
          campaign_reference := p_command_json->'campaign_reference';
          workflow_input := p_command_json->'workflow_input';
          IF command_digest_value !~ '^[0-9a-f]{64}$' OR
             jsonb_typeof(campaign_reference)<>'object' OR
             jsonb_typeof(workflow_input)<>'object' OR
             p_command_json->>'schema_version'<>
               'collection-workflow-start-command-v1' OR
             p_command_json->>'outbox_type'<>'geo_collection_v2' OR
             p_command_json->>'workflow_type'<>'GeoCollectionV2Workflow' OR
             p_command_json->>'task_queue'<>'geo-platform-v2-collection-v2' OR
             p_command_json->>'payload_schema_version'<>'collection-workflow-v2' OR
             p_command_json->>'campaign_id'<>plan_row.campaign_id::text OR
             p_command_json->>'campaign_pub_id'<>scope_row.campaign_pub_id OR
             p_command_json->>'partition_pub_id'<>partition_row.pub_id OR
             p_command_json->>'partition_digest'<>partition_row.partition_digest OR
             p_command_json->>'plan_digest'<>plan_row.plan_digest OR
             (p_command_json->>'cursor')::bigint<>p_expected_cursor THEN
            RAISE EXCEPTION 'collection_execution_start_command_envelope_drift';
          END IF;
          expected_reference := jsonb_build_object(
            'schema_version','collection-campaign-workflow-ref-v1',
            'tenant_id',p_tenant_id::text,'project_id',p_project_id::text,
            'campaign_pub_id',scope_row.campaign_pub_id,
            'config_revision_pub_id',plan_row.config_revision_pub_id,
            'config_revision_hash',plan_row.config_revision_hash,
            'specification_hash',plan_row.specification_hash,
            'slot_generator_version',plan_row.slot_generator_version,
            'membership_digest_version',plan_row.membership_digest_version,
            'membership_hash',plan_row.membership_hash,
            'partition_pub_id',partition_row.pub_id,
            'start_slot_ordinal',partition_row.start_slot_ordinal,
            'end_slot_ordinal_exclusive',partition_row.end_slot_ordinal_exclusive,
            'cursor',partition_row.start_slot_ordinal,
            'page_size',plan_row.workflow_page_size
          );
          IF campaign_reference<>expected_reference OR
             workflow_input->>'schema_version'<>'collection-workflow-v2' OR
             workflow_input->>'tenant_pub_id'<>scope_row.tenant_pub_id OR
             workflow_input->>'project_pub_id'<>scope_row.project_pub_id OR
             workflow_input->>'config_revision_pub_id'<>
               plan_row.config_revision_pub_id OR
             workflow_input->>'config_revision_hash'<>plan_row.config_revision_hash OR
             workflow_input->>'campaign_pub_id'<>scope_row.campaign_pub_id OR
             workflow_input->>'specification_hash'<>plan_row.specification_hash OR
             workflow_input->>'partition_pub_id'<>partition_row.pub_id OR
             workflow_input->>'partition_digest'<>partition_row.partition_digest OR
             workflow_input->>'membership_digest_version'<>
               plan_row.membership_digest_version OR
             workflow_input->>'membership_digest'<>plan_row.membership_hash OR
             workflow_input->>'slot_generator_version'<>
               plan_row.slot_generator_version OR
             (workflow_input->>'start_slot_ordinal')::bigint<>
               partition_row.start_slot_ordinal OR
             (workflow_input->>'end_slot_ordinal_exclusive')::bigint<>
               partition_row.end_slot_ordinal_exclusive OR
             (workflow_input->>'cursor')::bigint<>partition_row.start_slot_ordinal OR
             (workflow_input->>'page_size')::integer<>plan_row.workflow_page_size OR
             workflow_input->>'checkpoint_ref'<>partition_row.checkpoint_ref OR
             workflow_input->>'checkpoint_digest'<>partition_row.checkpoint_digest OR
             workflow_input->>'reconciliation_checkpoint_ref'<>
               partition_row.reconciliation_checkpoint_ref OR
             workflow_input->>'capability_policy_revision'<>
               plan_row.capability_policy_revision OR
             workflow_input->>'comparison_policy_revision'<>
               plan_row.comparison_policy_revision OR
             workflow_input->>'control_policy_revision' !~
               '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             workflow_input->>'canonical_enumeration_version' !~
               '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             workflow_input->>'idempotency_key' !~
               '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             workflow_input->>'scheduling_window_start_utc' !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*Z$' OR
             workflow_input->>'scheduling_window_end_utc' !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*Z$' OR
             (workflow_input->>'scheduling_window_end_utc')::timestamptz<=
               (workflow_input->>'scheduling_window_start_utc')::timestamptz OR
             (workflow_input->>'generation')::integer<>1 OR
             (workflow_input->>'continue_as_new_after_pages')::integer NOT BETWEEN 1 AND 100
          THEN
            RAISE EXCEPTION 'collection_execution_start_workflow_input_drift';
          END IF;
          calculated_workflow_id := platform.collection_execution_workflow_id_s11(
            plan_row.campaign_id,partition_row.partition_digest
          );
          digest_basis := jsonb_build_object(
            'campaign_id',plan_row.campaign_id::text,
            'campaign_pub_id',scope_row.campaign_pub_id,
            'cursor',p_expected_cursor,
            'partition_digest',partition_row.partition_digest,
            'partition_pub_id',partition_row.pub_id,
            'plan_digest',plan_row.plan_digest,
            'version','collection-workflow-start-command-v1',
            'outbox_type','geo_collection_v2',
            'workflow_type','GeoCollectionV2Workflow',
            'task_queue','geo-platform-v2-collection-v2',
            'payload_schema_version','collection-workflow-v2',
            'campaign_reference',campaign_reference,
            'workflow_input',workflow_input
          );
          calculated_command_digest :=
            platform.collection_execution_workflow_command_digest_s11(digest_basis);
          expected_command := (digest_basis-'version') || jsonb_build_object(
            'schema_version','collection-workflow-start-command-v1',
            'workflow_id',calculated_workflow_id,
            'command_digest',calculated_command_digest
          );
          IF command_workflow_id<>calculated_workflow_id OR
             command_digest_value<>calculated_command_digest OR
             p_command_json<>expected_command THEN
            RAISE EXCEPTION 'collection_execution_start_command_drift';
          END IF;
          SELECT * INTO existing_outbox
            FROM platform.collection_execution_start_outbox_v2 outbox
           WHERE outbox.tenant_id=p_tenant_id AND outbox.project_id=p_project_id
             AND (outbox.command_id=p_command_id OR
                  outbox.partition_id=p_partition_id OR
                  outbox.workflow_id=command_workflow_id)
           LIMIT 1 FOR UPDATE;
          IF partition_row.workflow_command_id IS NOT NULL OR FOUND THEN
            IF existing_outbox.id IS NULL OR
               ROW(partition_row.workflow_command_id,partition_row.workflow_id,
                   partition_row.workflow_command_digest,
                   existing_outbox.command_id,existing_outbox.command_digest,
                   existing_outbox.command_json,existing_outbox.task_queue)
               IS DISTINCT FROM
               ROW(p_command_id,command_workflow_id,command_digest_value,p_command_id,
                   command_digest_value,p_command_json,
                   'geo-platform-v2-collection-v2') THEN
              RAISE EXCEPTION 'collection_execution_start_exact_replay_drift';
            END IF;
            RETURN QUERY SELECT p_command_id,partition_row.version,false;
            RETURN;
          END IF;
          IF partition_row.state<>'planned' OR
             partition_row.control_revision<>p_expected_control_revision OR
             partition_row.version<>p_expected_partition_version THEN
            RAISE EXCEPTION 'collection_execution_start_cas_failed';
          END IF;
          INSERT INTO platform.collection_execution_start_outbox_v2(
            id,pub_id,tenant_id,project_id,plan_id,partition_id,campaign_id,
            plan_digest,partition_digest,command_id,command_digest,workflow_id,
            outbox_type,workflow_type,payload_schema_version,task_queue,cursor,
            command_json,outbox_state,staged_at,
            created_at,updated_at
          ) VALUES (
            p_command_id,'cwo2_'||substr(command_digest_value,1,24),p_tenant_id,
            p_project_id,p_plan_id,p_partition_id,plan_row.campaign_id,
            plan_row.plan_digest,partition_row.partition_digest,p_command_id,
            command_digest_value,command_workflow_id,'geo_collection_v2',
            'GeoCollectionV2Workflow','collection-workflow-v2',
            'geo-platform-v2-collection-v2',p_expected_cursor,p_command_json,
            'pending',p_now,p_now,p_now
          );
          UPDATE platform.collection_execution_partition_v2 partition SET
            workflow_command_id=p_command_id,workflow_id=command_workflow_id,
            workflow_command_digest=command_digest_value,
            control_policy_revision=workflow_input->>'control_policy_revision',
            workflow_staged_at=p_now,
            reconcile_after=p_reconcile_after,state='start_staged',
            version=partition.version+1,updated_at=p_now
           WHERE partition.id=p_partition_id
             AND partition.version=p_expected_partition_version
             AND partition.state='planned';
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_start_cas_lost'; END IF;
          RETURN QUERY SELECT p_command_id,p_expected_partition_version+1,true;
        END
        $$;

        CREATE FUNCTION platform.claim_collection_execution_start_outbox_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_outbox_id uuid,
          p_expected_command_digest text,
          p_expected_state text,
          p_expected_attempt_count integer,
          p_expected_claim_fence bigint,
          p_claim_ref text,
          p_now timestamptz
        ) RETURNS TABLE(outbox_id uuid,claim_fence bigint,
                        outbox_version integer,claimed boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE outbox_row platform.collection_execution_start_outbox_v2%ROWTYPE;
        DECLARE partition_state text;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_expected_command_digest !~ '^[0-9a-f]{64}$' OR
             p_expected_state NOT IN ('pending','failed') OR
             p_claim_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
            RAISE EXCEPTION 'collection_execution_outbox_claim_request_invalid';
          END IF;
          SELECT * INTO outbox_row
            FROM platform.collection_execution_start_outbox_v2 outbox
           WHERE outbox.id=p_outbox_id AND outbox.tenant_id=p_tenant_id
             AND outbox.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND OR outbox_row.command_digest<>p_expected_command_digest THEN
            RAISE EXCEPTION 'collection_execution_outbox_claim_lineage_drift';
          END IF;
          IF outbox_row.outbox_state='claimed' THEN
            IF outbox_row.claim_ref<>p_claim_ref OR
               outbox_row.claim_fence<>p_expected_claim_fence+1 OR
               outbox_row.attempt_count<>p_expected_attempt_count+1 THEN
              RAISE EXCEPTION 'collection_execution_outbox_claim_replay_drift';
            END IF;
            RETURN QUERY SELECT outbox_row.id,outbox_row.claim_fence,
                                outbox_row.version,false;
            RETURN;
          END IF;
          SELECT partition.state INTO partition_state
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=outbox_row.partition_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR SHARE;
          IF partition_state IS NULL OR partition_state IN ('cancelled','completed') OR
             outbox_row.outbox_state<>p_expected_state OR
             outbox_row.attempt_count<>p_expected_attempt_count OR
             outbox_row.claim_fence<>p_expected_claim_fence THEN
            RAISE EXCEPTION 'collection_execution_outbox_claim_cas_failed';
          END IF;
          UPDATE platform.collection_execution_start_outbox_v2 outbox SET
            outbox_state='claimed',attempt_count=outbox.attempt_count+1,
            claim_ref=p_claim_ref,claim_fence=outbox.claim_fence+1,
            claimed_at=p_now,last_error_code=NULL,
            version=outbox.version+1,updated_at=p_now
           WHERE outbox.id=p_outbox_id AND outbox.outbox_state=p_expected_state
             AND outbox.attempt_count=p_expected_attempt_count
             AND outbox.claim_fence=p_expected_claim_fence;
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_outbox_claim_cas_lost'; END IF;
          RETURN QUERY SELECT p_outbox_id,p_expected_claim_fence+1,
                              outbox_row.version+1,true;
        END
        $$;

        CREATE FUNCTION platform.finalize_collection_execution_start_outbox_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_outbox_id uuid,
          p_expected_command_digest text,
          p_claim_ref text,
          p_claim_fence bigint,
          p_expected_outbox_version integer,
          p_disposition text,
          p_temporal_run_id text,
          p_error_code text,
          p_now timestamptz
        ) RETURNS TABLE(outbox_id uuid,outbox_version integer,finalized boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE outbox_row platform.collection_execution_start_outbox_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_expected_command_digest !~ '^[0-9a-f]{64}$' OR
             p_claim_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_disposition NOT IN ('published','failed') OR
             (p_disposition='published' AND
               (p_temporal_run_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
                p_error_code IS NOT NULL)) OR
             (p_disposition='failed' AND
               (p_error_code !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
                p_temporal_run_id IS NOT NULL)) THEN
            RAISE EXCEPTION 'collection_execution_outbox_finalize_request_invalid';
          END IF;
          SELECT * INTO outbox_row
            FROM platform.collection_execution_start_outbox_v2 outbox
           WHERE outbox.id=p_outbox_id AND outbox.tenant_id=p_tenant_id
             AND outbox.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND OR outbox_row.command_digest<>p_expected_command_digest THEN
            RAISE EXCEPTION 'collection_execution_outbox_finalize_lineage_drift';
          END IF;
          IF outbox_row.outbox_state=p_disposition THEN
            IF outbox_row.claim_ref<>p_claim_ref OR
               outbox_row.claim_fence<>p_claim_fence OR
               outbox_row.temporal_run_id IS DISTINCT FROM p_temporal_run_id OR
               outbox_row.last_error_code IS DISTINCT FROM p_error_code THEN
              RAISE EXCEPTION 'collection_execution_outbox_finalize_replay_drift';
            END IF;
            RETURN QUERY SELECT outbox_row.id,outbox_row.version,false;
            RETURN;
          END IF;
          IF outbox_row.outbox_state<>'claimed' OR
             outbox_row.claim_ref<>p_claim_ref OR
             outbox_row.claim_fence<>p_claim_fence OR
             outbox_row.version<>p_expected_outbox_version THEN
            RAISE EXCEPTION 'collection_execution_outbox_finalize_cas_failed';
          END IF;
          UPDATE platform.collection_execution_start_outbox_v2 outbox SET
            outbox_state=p_disposition,
            published_at=CASE WHEN p_disposition='published' THEN p_now ELSE NULL END,
            temporal_run_id=p_temporal_run_id,last_error_code=p_error_code,
            version=outbox.version+1,updated_at=p_now
           WHERE outbox.id=p_outbox_id AND outbox.outbox_state='claimed'
             AND outbox.claim_ref=p_claim_ref AND outbox.claim_fence=p_claim_fence
             AND outbox.version=p_expected_outbox_version;
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_outbox_finalize_cas_lost'; END IF;
          RETURN QUERY SELECT p_outbox_id,p_expected_outbox_version+1,true;
        END
        $$;

        CREATE FUNCTION platform.read_collection_execution_control_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_partition_digest text
        ) RETURNS TABLE(partition_id uuid,state text,cursor bigint,
                        control_revision bigint,control_policy_revision text,
                        cancellation_ref text,reconciliation_ref text)
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_expected_partition_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'collection_execution_control_request_invalid';
          END IF;
          RETURN QUERY
          SELECT partition.id,partition.state,partition.cursor,
                 partition.control_revision,partition.control_policy_revision,
                 partition.cancellation_ref,partition.reconciliation_ref
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id
             AND partition.partition_digest=p_expected_partition_digest;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'collection_execution_control_lineage_drift';
          END IF;
        END
        $$;

        CREATE FUNCTION platform.advance_collection_execution_partition_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_partition_digest text,
          p_expected_cursor bigint,
          p_new_cursor bigint,
          p_page_digest text,
          p_checkpoint_ref text,
          p_checkpoint_digest text,
          p_reconciliation_checkpoint_ref text,
          p_expected_control_revision bigint,
          p_expected_partition_version integer,
          p_now timestamptz
        ) RETURNS TABLE(partition_id uuid,cursor bigint,checkpoint_ref text,
                        checkpoint_digest text,reconciliation_checkpoint_ref text,
                        control_revision bigint,partition_version integer,
                        advanced boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform, public
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE plan_row platform.collection_execution_plan_v2%ROWTYPE;
        DECLARE partition_row platform.collection_execution_partition_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_page_digest !~ '^[0-9a-f]{64}$' OR
             p_checkpoint_digest !~ '^[0-9a-f]{64}$' OR
             p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             p_checkpoint_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_reconciliation_checkpoint_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
            RAISE EXCEPTION 'collection_execution_page_request_invalid';
          END IF;
          SELECT * INTO plan_row FROM platform.collection_execution_plan_v2 plan
           WHERE plan.id=p_plan_id AND plan.tenant_id=p_tenant_id
             AND plan.project_id=p_project_id FOR SHARE;
          SELECT * INTO partition_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR UPDATE;
          IF plan_row.id IS NULL OR partition_row.id IS NULL OR
             plan_row.state<>'frozen' OR
             partition_row.partition_digest<>p_expected_partition_digest THEN
            RAISE EXCEPTION 'collection_execution_page_lineage_drift';
          END IF;
          IF partition_row.cursor=p_new_cursor AND
             partition_row.last_page_start=p_expected_cursor AND
             partition_row.last_page_end_exclusive=p_new_cursor AND
             partition_row.last_page_digest=p_page_digest AND
             partition_row.checkpoint_ref=p_checkpoint_ref AND
             partition_row.checkpoint_digest=p_checkpoint_digest AND
             partition_row.reconciliation_checkpoint_ref=
               p_reconciliation_checkpoint_ref THEN
            RETURN QUERY SELECT partition_row.id,partition_row.cursor,
                                partition_row.checkpoint_ref,
                                partition_row.checkpoint_digest,
                                partition_row.reconciliation_checkpoint_ref,
                                partition_row.control_revision,
                                partition_row.version,false;
            RETURN;
          END IF;
          IF partition_row.state NOT IN ('start_staged','running') OR
             partition_row.cursor<>p_expected_cursor OR
             partition_row.control_revision<>p_expected_control_revision OR
             partition_row.version<>p_expected_partition_version OR
             p_new_cursor<=p_expected_cursor OR
             p_new_cursor>partition_row.end_slot_ordinal_exclusive OR
             p_new_cursor-p_expected_cursor>plan_row.workflow_page_size THEN
            RAISE EXCEPTION 'collection_execution_page_cas_failed';
          END IF;
          UPDATE platform.collection_execution_partition_v2 partition SET
            cursor=p_new_cursor,cursor_version=partition.cursor_version+1,
            checkpoint_ref=p_checkpoint_ref,checkpoint_digest=p_checkpoint_digest,
            reconciliation_checkpoint_ref=p_reconciliation_checkpoint_ref,
            last_page_start=p_expected_cursor,
            last_page_end_exclusive=p_new_cursor,last_page_digest=p_page_digest,
            state=CASE WHEN p_new_cursor=partition.end_slot_ordinal_exclusive
                       THEN 'awaiting_terminal' ELSE 'running' END,
            version=partition.version+1,updated_at=p_now
           WHERE partition.id=p_partition_id
             AND partition.version=p_expected_partition_version
             AND partition.cursor=p_expected_cursor;
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_page_cas_lost'; END IF;
          RETURN QUERY SELECT p_partition_id,p_new_cursor,p_checkpoint_ref,
                              p_checkpoint_digest,p_reconciliation_checkpoint_ref,
                              p_expected_control_revision,
                              p_expected_partition_version+1,true;
        END
        $$;

        CREATE FUNCTION platform.claim_collection_execution_reconciliation_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_partition_digest text,
          p_expected_cursor bigint,
          p_expected_control_revision bigint,
          p_expected_partition_version integer,
          p_reconciliation_ref text,
          p_reconciliation_reason text,
          p_now timestamptz
        ) RETURNS TABLE(partition_id uuid,control_revision bigint,
                        partition_version integer,claimed boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE partition_row platform.collection_execution_partition_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR
             p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             p_reconciliation_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_reconciliation_reason !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
            RAISE EXCEPTION 'collection_execution_reconciliation_request_invalid';
          END IF;
          SELECT * INTO partition_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND OR partition_row.partition_digest<>p_expected_partition_digest THEN
            RAISE EXCEPTION 'collection_execution_reconciliation_lineage_drift';
          END IF;
          IF partition_row.state='reconciling' THEN
            IF partition_row.reconciliation_ref<>p_reconciliation_ref OR
               partition_row.reconciliation_reason<>p_reconciliation_reason OR
               partition_row.cursor<>p_expected_cursor THEN
              RAISE EXCEPTION 'collection_execution_reconciliation_replay_drift';
            END IF;
            RETURN QUERY SELECT partition_row.id,partition_row.control_revision,
                                partition_row.version,false;
            RETURN;
          END IF;
          IF partition_row.state NOT IN ('start_staged','running') OR
             partition_row.cursor<>p_expected_cursor OR
             partition_row.control_revision<>p_expected_control_revision OR
             partition_row.version<>p_expected_partition_version OR
             partition_row.reconcile_after IS NULL OR p_now<partition_row.reconcile_after THEN
            RAISE EXCEPTION 'collection_execution_reconciliation_cas_failed';
          END IF;
          UPDATE platform.collection_execution_partition_v2 partition SET
            state='reconciling',reconciliation_ref=p_reconciliation_ref,
            reconciliation_reason=p_reconciliation_reason,
            reconciliation_claimed_at=p_now,
            control_revision=partition.control_revision+1,
            version=partition.version+1,
            updated_at=p_now
           WHERE partition.id=p_partition_id
             AND partition.version=p_expected_partition_version
             AND partition.state IN ('start_staged','running');
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_reconciliation_cas_lost'; END IF;
          RETURN QUERY SELECT p_partition_id,p_expected_control_revision+1,
                              p_expected_partition_version+1,true;
        END
        $$;

        CREATE FUNCTION platform.cancel_collection_execution_partition_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_partition_digest text,
          p_expected_cursor bigint,
          p_expected_control_revision bigint,
          p_expected_partition_version integer,
          p_cancellation_ref text,
          p_cancellation_reason text,
          p_now timestamptz
        ) RETURNS TABLE(partition_id uuid,control_revision bigint,
                        partition_version integer,cancelled boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE partition_row platform.collection_execution_partition_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR
             p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             p_cancellation_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_cancellation_reason !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' THEN
            RAISE EXCEPTION 'collection_execution_cancel_request_invalid';
          END IF;
          SELECT * INTO partition_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND OR partition_row.partition_digest<>p_expected_partition_digest THEN
            RAISE EXCEPTION 'collection_execution_cancel_lineage_drift';
          END IF;
          IF partition_row.state='cancelled' THEN
            IF partition_row.cancellation_ref<>p_cancellation_ref OR
               partition_row.cancellation_reason<>p_cancellation_reason OR
               partition_row.cursor<>p_expected_cursor THEN
              RAISE EXCEPTION 'collection_execution_cancel_replay_drift';
            END IF;
            RETURN QUERY SELECT partition_row.id,partition_row.control_revision,
                                partition_row.version,false;
            RETURN;
          END IF;
          IF partition_row.state NOT IN (
               'planned','start_staged','running','reconciling'
             ) OR
             partition_row.cursor<>p_expected_cursor OR
             partition_row.control_revision<>p_expected_control_revision OR
             partition_row.version<>p_expected_partition_version THEN
            RAISE EXCEPTION 'collection_execution_cancel_cas_failed';
          END IF;
          UPDATE platform.collection_execution_partition_v2 partition SET
            state='cancelled',cancellation_ref=p_cancellation_ref,
            cancellation_reason=p_cancellation_reason,cancelled_at=p_now,
            reconciliation_ref=NULL,reconciliation_reason=NULL,
            reconciliation_claimed_at=NULL,
            control_revision=partition.control_revision+1,
            version=partition.version+1,updated_at=p_now
           WHERE partition.id=p_partition_id
             AND partition.version=p_expected_partition_version
             AND partition.state IN ('planned','start_staged','running','reconciling');
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_cancel_cas_lost'; END IF;
          UPDATE platform.collection_execution_start_outbox_v2 outbox SET
            outbox_state='cancelled',version=outbox.version+1,updated_at=p_now
           WHERE outbox.partition_id=p_partition_id
             AND outbox.outbox_state IN ('pending','failed');
          RETURN QUERY SELECT p_partition_id,p_expected_control_revision+1,
                              p_expected_partition_version+1,true;
        END
        $$;

        CREATE FUNCTION platform.finalize_collection_execution_partition_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_plan_id uuid,
          p_partition_id uuid,
          p_expected_partition_digest text,
          p_expected_cursor bigint,
          p_expected_control_revision bigint,
          p_expected_partition_version integer,
          p_terminal_ref text,
          p_terminal_digest text,
          p_terminal_state text,
          p_now timestamptz
        ) RETURNS TABLE(partition_id uuid,partition_version integer,
                        finalized boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE partition_row platform.collection_execution_partition_v2%ROWTYPE;
        BEGIN
          PERFORM platform.assert_collection_execution_context_s11(p_tenant_id);
          IF p_now IS NULL OR p_expected_partition_digest !~ '^[0-9a-f]{64}$' OR
             p_terminal_digest !~ '^[0-9a-f]{64}$' OR
             p_terminal_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR
             p_terminal_state NOT IN ('completed','completed_with_reconciliation') THEN
            RAISE EXCEPTION 'collection_execution_terminal_request_invalid';
          END IF;
          SELECT * INTO partition_row
            FROM platform.collection_execution_partition_v2 partition
           WHERE partition.id=p_partition_id AND partition.plan_id=p_plan_id
             AND partition.tenant_id=p_tenant_id
             AND partition.project_id=p_project_id FOR UPDATE;
          IF NOT FOUND OR partition_row.partition_digest<>p_expected_partition_digest THEN
            RAISE EXCEPTION 'collection_execution_terminal_lineage_drift';
          END IF;
          IF partition_row.state='completed' THEN
            IF partition_row.terminal_ref<>p_terminal_ref OR
               partition_row.terminal_digest<>p_terminal_digest OR
               partition_row.terminal_state<>p_terminal_state OR
               partition_row.cursor<>p_expected_cursor THEN
              RAISE EXCEPTION 'collection_execution_terminal_replay_drift';
            END IF;
            RETURN QUERY SELECT partition_row.id,partition_row.version,false;
            RETURN;
          END IF;
          IF partition_row.state<>'awaiting_terminal' OR
             partition_row.cursor<>partition_row.end_slot_ordinal_exclusive OR
             partition_row.cursor<>p_expected_cursor OR
             partition_row.control_revision<>p_expected_control_revision OR
             partition_row.version<>p_expected_partition_version THEN
            RAISE EXCEPTION 'collection_execution_terminal_cas_failed';
          END IF;
          UPDATE platform.collection_execution_partition_v2 partition SET
            state='completed',terminal_ref=p_terminal_ref,
            terminal_digest=p_terminal_digest,terminal_state=p_terminal_state,
            terminal_at=p_now,version=partition.version+1,updated_at=p_now
           WHERE partition.id=p_partition_id
             AND partition.state='awaiting_terminal'
             AND partition.version=p_expected_partition_version
             AND partition.control_revision=p_expected_control_revision;
          IF NOT FOUND THEN RAISE EXCEPTION 'collection_execution_terminal_cas_lost'; END IF;
          RETURN QUERY SELECT p_partition_id,p_expected_partition_version+1,true;
        END
        $$;
        """
    )


def _grant_minimum_privileges() -> None:
    table_names = ",".join(f"'{table}'" for table in _TABLES)
    entrypoints = ",".join(f"'{function}'" for function in _ENTRYPOINTS)
    op.execute(
        f"""
        DO $$
        DECLARE table_name text;
        DECLARE role_name text;
        DECLARE function_identity text;
        BEGIN
          FOREACH table_name IN ARRAY ARRAY[{table_names}] LOOP
            EXECUTE format('REVOKE ALL ON TABLE platform.%I FROM PUBLIC',table_name);
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE format(
                  'REVOKE ALL ON TABLE platform.%I FROM %I',table_name,role_name
                );
                EXECUTE format(
                  'GRANT SELECT ON TABLE platform.%I TO %I',table_name,role_name
                );
              END IF;
            END LOOP;
          END LOOP;
          FOR function_identity IN
            SELECT format('%I.%I(%s)',namespace.nspname,procedure.proname,
                          pg_get_function_identity_arguments(procedure.oid))
              FROM pg_proc procedure
              JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
             WHERE namespace.nspname='platform' AND
                   (procedure.proname LIKE '%\\_s11' ESCAPE '\\' OR
                    procedure.proname IN ({entrypoints}))
          LOOP
            EXECUTE 'REVOKE ALL ON FUNCTION '||function_identity||' FROM PUBLIC';
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE 'REVOKE ALL ON FUNCTION '||function_identity||
                        ' FROM '||quote_ident(role_name);
              END IF;
            END LOOP;
          END LOOP;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            FOR function_identity IN
              SELECT format('%I.%I(%s)',namespace.nspname,procedure.proname,
                            pg_get_function_identity_arguments(procedure.oid))
                FROM pg_proc procedure
                JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
               WHERE namespace.nspname='platform'
                 AND procedure.proname IN ({entrypoints})
            LOOP
              EXECUTE 'GRANT EXECUTE ON FUNCTION '||function_identity||' TO geo_worker';
            END LOOP;
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _create_tables()
    _enable_rls()
    _create_digest_helpers()
    _create_row_guards()
    _create_plan_functions()
    _create_execution_functions()
    _grant_minimum_privileges()


def downgrade() -> None:
    table_names = ",".join(f"'{table}'" for table in _TABLES)
    op.execute(
        f"""
        DO $$
        DECLARE table_name text;
        DECLARE row_present boolean;
        BEGIN
          FOREACH table_name IN ARRAY ARRAY[{table_names}] LOOP
            EXECUTE format(
              'SELECT EXISTS (SELECT 1 FROM platform.%I)',table_name
            ) INTO row_present;
            IF row_present THEN
              RAISE EXCEPTION 'collection_execution_history_present_downgrade_refused';
            END IF;
          END LOOP;
        END
        $$
        """
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema="platform")
    op.execute(
        """
        DROP FUNCTION IF EXISTS platform.cancel_collection_execution_partition_v2(
          uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.claim_collection_execution_reconciliation_v2(
          uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.finalize_collection_execution_partition_v2(
          uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.advance_collection_execution_partition_v2(
          uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,text,text,bigint,
          integer,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.read_collection_execution_control_v2(
          uuid,uuid,uuid,uuid,text
        );
        DROP FUNCTION IF EXISTS platform.finalize_collection_execution_start_outbox_v2(
          uuid,uuid,uuid,text,text,bigint,integer,text,text,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.claim_collection_execution_start_outbox_v2(
          uuid,uuid,uuid,text,text,integer,bigint,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.stage_collection_partition_workflow_start_v2(
          uuid,uuid,uuid,uuid,text,text,bigint,bigint,integer,uuid,jsonb,timestamptz,
          timestamptz
        );
        DROP FUNCTION IF EXISTS platform.finalize_collection_execution_plan_v2(
          uuid,uuid,uuid,text,bigint,text,integer,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.create_collection_execution_partition_v2(
          uuid,uuid,uuid,uuid,text,bigint,bigint,bigint,text,text,text,text,
          bigint,integer,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.create_collection_execution_plan_v2(
          uuid,uuid,uuid,uuid,text,bigint,integer,text,timestamptz
        );
        DROP FUNCTION IF EXISTS platform.guard_collection_execution_start_outbox_s11();
        DROP FUNCTION IF EXISTS platform.guard_collection_execution_partition_s11();
        DROP FUNCTION IF EXISTS platform.guard_collection_execution_plan_s11();
        DROP FUNCTION IF EXISTS platform.collection_execution_workflow_command_digest_s11(
          jsonb
        );
        DROP FUNCTION IF EXISTS platform.collection_execution_workflow_id_s11(uuid,text);
        DROP FUNCTION IF EXISTS platform.collection_execution_partition_digest_s11(
          uuid,text,text,text,text,text,bigint,bigint,bigint
        );
        DROP FUNCTION IF EXISTS platform.collection_execution_plan_digest_s11(
          uuid,text,uuid,uuid,text,text,text,text,text,text,bigint,bigint,integer
        );
        DROP FUNCTION IF EXISTS platform.assert_collection_execution_context_s11(uuid);
        DROP FUNCTION IF EXISTS platform.collection_canonical_json_s11(jsonb);
        """
    )


__all__ = ["downgrade", "upgrade"]
