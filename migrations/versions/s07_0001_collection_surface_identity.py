"""Add the three-surface config, campaign, and frozen-slot identity plane.

This is an expand-only revision.  Existing collection and fact rows receive
nullable provenance columns; historical assignment is performed by a separate,
audited backfill and is deliberately absent from this migration.

Revision ID: s07_0001_surface_identity
Revises: s06_0038_w_review
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s07_0001_surface_identity"
down_revision: str | Sequence[str] | None = "s06_0038_w_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SURFACE_CHECK = "{column} IN ('provider_api','consumer_web','consumer_app')"
_TENANT_TABLES = (
    "collection_config_revision_v2",
    "collection_config_target_v2",
    "collection_campaign",
    "collection_campaign_target",
    "collection_sampling_leg",
    "collection_campaign_materialization_batch",
    "collection_primary_slot",
    "collection_surface_backfill_run",
)
_FACT_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("platform", "collection_run"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "config_revision_v2_id",
        "campaign_id",
    ),
    ("platform", "collection_task"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "requested_surface",
        "observed_surface",
        "observed_product_variant",
        "campaign_target_id",
        "sampling_leg_id",
        "primary_slot_id",
    ),
    ("analytics", "answer"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
    ),
    ("analytics", "answer_analysis"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
    ),
    ("evidence", "evidence_asset"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
    ),
    ("platform", "analysis_job"): (
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "requested_surface",
        "observed_surface",
        "observed_product_variant",
    ),
}


def _identity_columns() -> list[sa.Column[object]]:
    """Return fresh common columns for one tenant/project business table."""

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
    """Return common identity and tenant/project ownership constraints."""

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


def _enable_tenant_rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{table}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def _create_config_tables() -> None:
    op.create_table(
        "collection_config_revision_v2",
        *_identity_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid()),
        sa.Column(
            "lifecycle_state",
            sa.String(length=30),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("question_set_revision", sa.String(length=128), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "capability_registry_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "comparison_policy_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("samples_per_cell", sa.Integer(), nullable=False),
        sa.Column("province_codes_json", sa.Text(), nullable=False),
        sa.Column("schedule_policy_json", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.String(length=128), nullable=False),
        sa.Column("change_request_pub_id", sa.String(length=128)),
        sa.Column("approved_by_pub_id", sa.String(length=128)),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_config_revision_v2"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
            ],
            name="fk_collection_config_v2_parent_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision",
            name="uq_collection_config_v2_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_collection_config_v2_hash",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_collection_config_v2_id_hash_scope",
        ),
        sa.CheckConstraint("revision > 0", name="ck_collection_config_v2_revision"),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft','candidate','frozen','active','superseded','retired')",
            name="ck_collection_config_v2_lifecycle",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-config-v2'",
            name="ck_collection_config_v2_schema",
        ),
        sa.CheckConstraint(
            "revision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_collection_config_v2_hash",
        ),
        sa.CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name="ck_collection_config_v2_parent",
        ),
        sa.CheckConstraint(
            "samples_per_cell > 0",
            name="ck_collection_config_v2_samples",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_json::jsonb) = 'object'",
            name="ck_collection_config_v2_canonical_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(province_codes_json::jsonb) = 'array'",
            name="ck_collection_config_v2_provinces_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(schedule_policy_json::jsonb) = 'object'",
            name="ck_collection_config_v2_schedule_json",
        ),
        sa.CheckConstraint(
            "btrim(question_set_revision) <> '' "
            "AND btrim(capability_registry_revision) <> '' "
            "AND btrim(comparison_policy_revision) <> '' "
            "AND btrim(change_reason) <> ''",
            name="ck_collection_config_v2_required_text",
        ),
        sa.CheckConstraint(
            "(lifecycle_state IN ('draft','candidate') "
            "AND frozen_at IS NULL AND activated_at IS NULL) OR "
            "(lifecycle_state = 'frozen' "
            "AND frozen_at IS NOT NULL AND activated_at IS NULL) OR "
            "(lifecycle_state = 'active' "
            "AND frozen_at IS NOT NULL AND activated_at IS NOT NULL) OR "
            "(lifecycle_state IN ('superseded','retired') "
            "AND frozen_at IS NOT NULL)",
            name="ck_collection_config_v2_lifecycle_times",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_config_v2_project_state",
        "collection_config_revision_v2",
        ["tenant_id", "project_id", "lifecycle_state", "revision"],
        schema="platform",
    )

    op.create_table(
        "collection_config_target_v2",
        *_identity_columns(),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column("target_key", sa.String(length=500), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("interaction_modes_json", sa.Text(), nullable=False),
        sa.Column("capability_revisions_json", sa.Text(), nullable=False),
        *_scope_constraints("collection_config_target_v2"),
        sa.ForeignKeyConstraint(
            ["config_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
            ],
            name="fk_collection_config_target_v2_config_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "config_revision_id",
            name="uq_collection_config_target_v2_config_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "config_revision_id",
            "target_key",
            name="uq_collection_config_target_v2_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "config_revision_id",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_collection_config_target_v2_identity",
        ),
        sa.CheckConstraint(
            _SURFACE_CHECK.format(column="collection_surface"),
            name="ck_collection_config_target_v2_surface",
        ),
        sa.CheckConstraint(
            "btrim(target_key) <> '' AND btrim(platform) <> '' AND btrim(product_variant) <> ''",
            name="ck_collection_config_target_v2_required_text",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(interaction_modes_json::jsonb) = 'array' "
            "AND jsonb_array_length(interaction_modes_json::jsonb) > 0",
            name="ck_collection_config_target_v2_modes_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(capability_revisions_json::jsonb) IN ('array','object')",
            name="ck_collection_config_target_v2_caps_json",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_config_target_v2_config",
        "collection_config_target_v2",
        ["tenant_id", "project_id", "config_revision_id"],
        schema="platform",
    )


def _create_campaign_tables() -> None:
    op.create_table(
        "collection_campaign",
        *_identity_columns(),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column("config_revision_hash", sa.String(length=64), nullable=False),
        sa.Column("question_set_revision", sa.String(length=128), nullable=False),
        sa.Column("time_window_key", sa.String(length=255), nullable=False),
        sa.Column("run_trigger_source", sa.String(length=30), nullable=False),
        sa.Column("trigger_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("binding_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("membership_specification_json", sa.Text(), nullable=False),
        sa.Column("specification_schema_version", sa.String(length=80), nullable=False),
        sa.Column("specification_hash", sa.String(length=64), nullable=False),
        sa.Column("slot_generator_version", sa.String(length=80), nullable=False),
        sa.Column("membership_digest_version", sa.String(length=80), nullable=False),
        sa.Column("expected_primary_slot_count", sa.BigInteger(), nullable=False),
        sa.Column("expected_non_primary_slot_count", sa.BigInteger(), nullable=False),
        sa.Column("expected_slot_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "materialized_slot_count",
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
        sa.Column(
            "materialization_cursor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("membership_hash", sa.String(length=64)),
        sa.Column("created_by_pub_id", sa.String(length=128), nullable=False),
        sa.Column("approved_by_pub_id", sa.String(length=128)),
        sa.Column("triggered_by_pub_id", sa.String(length=128), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "state",
            sa.String(length=30),
            nullable=False,
            server_default="assembling",
        ),
        *_scope_constraints("collection_campaign"),
        sa.ForeignKeyConstraint(
            [
                "config_revision_id",
                "tenant_id",
                "project_id",
                "config_revision_hash",
            ],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
                "platform.collection_config_revision_v2.revision_hash",
            ],
            name="fk_collection_campaign_config_hash_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "config_revision_id",
            name="uq_collection_campaign_config_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "trigger_idempotency_key",
            name="uq_collection_campaign_trigger_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "membership_hash",
            name="uq_collection_campaign_membership_hash",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "specification_hash",
            "slot_generator_version",
            name="uq_collection_campaign_materialization_lineage",
        ),
        sa.CheckConstraint(
            "config_revision_hash ~ '^[0-9a-f]{64}$' "
            "AND specification_hash ~ '^[0-9a-f]{64}$' "
            "AND specification_hash = encode("
            "digest(membership_specification_json, 'sha256'), 'hex') "
            "AND (membership_hash IS NULL "
            "OR membership_hash ~ '^[0-9a-f]{64}$')",
            name="ck_collection_campaign_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(membership_specification_json::jsonb) = 'object'",
            name="ck_collection_campaign_membership_specification_json",
        ),
        sa.CheckConstraint(
            "specification_schema_version = 'collection-campaign-membership-v1' "
            "AND slot_generator_version = 'collection-slot-generator-v1' "
            "AND membership_digest_version = 'collection-membership-chain-v1'",
            name="ck_collection_campaign_materialization_versions",
        ),
        sa.CheckConstraint(
            "btrim(question_set_revision) <> '' AND btrim(time_window_key) <> '' "
            "AND btrim(run_trigger_source) <> '' "
            "AND btrim(trigger_idempotency_key) <> '' "
            "AND btrim(binding_policy_revision) <> '' "
            "AND btrim(specification_schema_version) <> '' "
            "AND btrim(slot_generator_version) <> '' "
            "AND btrim(membership_digest_version) <> '' "
            "AND btrim(created_by_pub_id) <> '' "
            "AND btrim(triggered_by_pub_id) <> ''",
            name="ck_collection_campaign_required_text",
        ),
        sa.CheckConstraint(
            "expected_primary_slot_count > 0 "
            "AND expected_non_primary_slot_count >= 0 "
            "AND expected_slot_count = expected_primary_slot_count "
            "+ expected_non_primary_slot_count "
            "AND materialized_slot_count >= 0 "
            "AND materialized_slot_count <= expected_slot_count "
            "AND materialization_cursor = materialized_slot_count",
            name="ck_collection_campaign_materialization_counts",
        ),
        sa.CheckConstraint(
            "materialization_state IN ('pending','materializing','complete')",
            name="ck_collection_campaign_materialization_state",
        ),
        sa.CheckConstraint(
            "(materialization_state = 'pending' "
            "AND materialization_cursor = 0) OR "
            "(materialization_state = 'materializing' "
            "AND materialization_cursor > 0 "
            "AND materialization_cursor < expected_slot_count) OR "
            "(materialization_state = 'complete' "
            "AND materialization_cursor = expected_slot_count)",
            name="ck_collection_campaign_materialization_progress",
        ),
        sa.CheckConstraint(
            "state IN ('assembling','frozen')",
            name="ck_collection_campaign_state",
        ),
        sa.CheckConstraint(
            "(state = 'assembling' AND frozen_at IS NULL "
            "AND membership_hash IS NULL) OR "
            "(state = 'frozen' AND frozen_at IS NOT NULL "
            "AND membership_hash IS NOT NULL "
            "AND materialization_state = 'complete' "
            "AND materialization_cursor = expected_slot_count)",
            name="ck_collection_campaign_freeze",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_campaign_project_frozen",
        "collection_campaign",
        ["tenant_id", "project_id", "frozen_at", "pub_id"],
        schema="platform",
        postgresql_where=sa.text("state = 'frozen'"),
    )

    op.create_table(
        "collection_campaign_target",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("config_target_id", sa.Uuid(), nullable=False),
        sa.Column("target_key", sa.String(length=500), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("interaction_modes_json", sa.Text(), nullable=False),
        sa.Column("capability_revisions_json", sa.Text(), nullable=False),
        sa.Column("binding_policy_revision", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_campaign_target"),
        sa.ForeignKeyConstraint(
            ["campaign_id", "tenant_id", "project_id"],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
            ],
            name="fk_collection_campaign_target_campaign_scope",
        ),
        sa.ForeignKeyConstraint(
            ["config_target_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_target_v2.id",
                "platform.collection_config_target_v2.tenant_id",
                "platform.collection_config_target_v2.project_id",
            ],
            name="fk_collection_campaign_target_config_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            name="uq_collection_campaign_target_campaign_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_campaign_target_tenant",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_collection_campaign_target_identity_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "target_key",
            name="uq_collection_campaign_target_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "config_target_id",
            name="uq_collection_campaign_target_config",
        ),
        sa.CheckConstraint(
            _SURFACE_CHECK.format(column="collection_surface"),
            name="ck_collection_campaign_target_surface",
        ),
        sa.CheckConstraint(
            "btrim(target_key) <> '' AND btrim(platform) <> '' "
            "AND btrim(product_variant) <> '' "
            "AND btrim(binding_policy_revision) <> ''",
            name="ck_collection_campaign_target_required_text",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(interaction_modes_json::jsonb) = 'array' "
            "AND jsonb_array_length(interaction_modes_json::jsonb) > 0",
            name="ck_collection_campaign_target_modes_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(capability_revisions_json::jsonb) IN ('array','object')",
            name="ck_collection_campaign_target_caps_json",
        ),
        schema="platform",
    )

    op.create_table(
        "collection_sampling_leg",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_target_id", sa.Uuid(), nullable=False),
        sa.Column("leg_key", sa.String(length=1000), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("province_code", sa.String(length=6), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_sampling_leg"),
        sa.ForeignKeyConstraint(
            [
                "campaign_target_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "platform",
                "collection_surface",
                "product_variant",
            ],
            [
                "platform.collection_campaign_target.id",
                "platform.collection_campaign_target.tenant_id",
                "platform.collection_campaign_target.project_id",
                "platform.collection_campaign_target.campaign_id",
                "platform.collection_campaign_target.platform",
                "platform.collection_campaign_target.collection_surface",
                "platform.collection_campaign_target.product_variant",
            ],
            name="fk_collection_sampling_leg_target_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "campaign_target_id",
            "platform",
            "collection_surface",
            "product_variant",
            "province_code",
            "interaction_mode",
            name="uq_collection_sampling_leg_identity_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_sampling_leg_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "leg_key",
            name="uq_collection_sampling_leg_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_target_id",
            "province_code",
            "interaction_mode",
            name="uq_collection_sampling_leg_cell",
        ),
        sa.CheckConstraint(
            _SURFACE_CHECK.format(column="collection_surface"),
            name="ck_collection_sampling_leg_surface",
        ),
        sa.CheckConstraint(
            "province_code ~ '^[0-9]{6}$'",
            name="ck_collection_sampling_leg_province",
        ),
        sa.CheckConstraint(
            "btrim(leg_key) <> '' AND btrim(platform) <> '' "
            "AND btrim(product_variant) <> '' AND btrim(interaction_mode) <> ''",
            name="ck_collection_sampling_leg_required_text",
        ),
        schema="platform",
    )

    op.create_table(
        "collection_campaign_materialization_batch",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("specification_hash", sa.String(length=64), nullable=False),
        sa.Column("slot_generator_version", sa.String(length=80), nullable=False),
        sa.Column("start_slot_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("end_slot_ordinal_exclusive", sa.BigInteger(), nullable=False),
        sa.Column("slot_count", sa.BigInteger(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("prior_membership_chain_hash", sa.String(length=64), nullable=False),
        sa.Column("membership_chain_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "batch_state",
            sa.String(length=30),
            nullable=False,
            server_default="preparing",
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_campaign_materialization_batch"),
        sa.ForeignKeyConstraint(
            [
                "campaign_id",
                "tenant_id",
                "project_id",
                "specification_hash",
                "slot_generator_version",
            ],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
                "platform.collection_campaign.specification_hash",
                "platform.collection_campaign.slot_generator_version",
            ],
            name="fk_collection_campaign_materialization_batch_lineage",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            name="uq_collection_campaign_materialization_batch_campaign_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "idempotency_key",
            name="uq_collection_campaign_materialization_batch_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "start_slot_ordinal",
            "end_slot_ordinal_exclusive",
            name="uq_collection_campaign_materialization_batch_range",
        ),
        sa.CheckConstraint(
            "start_slot_ordinal >= 0 "
            "AND end_slot_ordinal_exclusive > start_slot_ordinal "
            "AND slot_count = end_slot_ordinal_exclusive - start_slot_ordinal",
            name="ck_collection_campaign_batch_range",
        ),
        sa.CheckConstraint(
            "specification_hash ~ '^[0-9a-f]{64}$' "
            "AND chunk_hash ~ '^[0-9a-f]{64}$' "
            "AND prior_membership_chain_hash ~ '^[0-9a-f]{64}$' "
            "AND membership_chain_hash ~ '^[0-9a-f]{64}$'",
            name="ck_collection_campaign_batch_hashes",
        ),
        sa.CheckConstraint(
            "btrim(slot_generator_version) <> '' AND btrim(idempotency_key) <> ''",
            name="ck_collection_campaign_batch_required_text",
        ),
        sa.CheckConstraint(
            "batch_state IN ('preparing','completed')",
            name="ck_collection_campaign_batch_state",
        ),
        sa.CheckConstraint(
            "(batch_state = 'preparing' AND committed_at IS NULL) OR "
            "(batch_state = 'completed' AND committed_at IS NOT NULL)",
            name="ck_collection_campaign_batch_commit",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_campaign_batch_cursor",
        "collection_campaign_materialization_batch",
        ["tenant_id", "project_id", "campaign_id", "start_slot_ordinal"],
        schema="platform",
    )

    op.create_table(
        "collection_primary_slot",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_target_id", sa.Uuid(), nullable=False),
        sa.Column("sampling_leg_id", sa.Uuid(), nullable=False),
        sa.Column("slot_key", sa.String(length=1500), nullable=False),
        sa.Column("question_slot_id", sa.String(length=128), nullable=False),
        sa.Column("question_revision", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("province_code", sa.String(length=6), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        sa.Column("sample_ordinal", sa.Integer(), nullable=False),
        sa.Column("slot_role", sa.String(length=30), nullable=False),
        sa.Column("role_reason", sa.String(length=128)),
        sa.Column("related_primary_slot_key", sa.String(length=1500)),
        sa.Column("slot_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("slot_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("materialization_batch_id", sa.Uuid(), nullable=False),
        *_scope_constraints("collection_primary_slot"),
        sa.ForeignKeyConstraint(
            [
                "sampling_leg_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "campaign_target_id",
                "platform",
                "collection_surface",
                "product_variant",
                "province_code",
                "interaction_mode",
            ],
            [
                "platform.collection_sampling_leg.id",
                "platform.collection_sampling_leg.tenant_id",
                "platform.collection_sampling_leg.project_id",
                "platform.collection_sampling_leg.campaign_id",
                "platform.collection_sampling_leg.campaign_target_id",
                "platform.collection_sampling_leg.platform",
                "platform.collection_sampling_leg.collection_surface",
                "platform.collection_sampling_leg.product_variant",
                "platform.collection_sampling_leg.province_code",
                "platform.collection_sampling_leg.interaction_mode",
            ],
            name="fk_collection_primary_slot_leg_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "materialization_batch_id",
                "tenant_id",
                "project_id",
                "campaign_id",
            ],
            [
                "platform.collection_campaign_materialization_batch.id",
                "platform.collection_campaign_materialization_batch.tenant_id",
                "platform.collection_campaign_materialization_batch.project_id",
                "platform.collection_campaign_materialization_batch.campaign_id",
            ],
            name="fk_collection_primary_slot_materialization_batch",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "project_id",
                "campaign_id",
                "related_primary_slot_key",
            ],
            [
                "platform.collection_primary_slot.tenant_id",
                "platform.collection_primary_slot.project_id",
                "platform.collection_primary_slot.campaign_id",
                "platform.collection_primary_slot.slot_key",
            ],
            name="fk_collection_primary_slot_related_primary",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_primary_slot_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_key",
            name="uq_collection_primary_slot_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_ordinal",
            name="uq_collection_primary_slot_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_identity_hash",
            name="uq_collection_primary_slot_identity_hash",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "sampling_leg_id",
            "question_slot_id",
            "sample_ordinal",
            "slot_role",
            name="uq_collection_primary_slot_logical_identity",
        ),
        sa.CheckConstraint("sample_ordinal >= 1", name="ck_collection_primary_slot_sample"),
        sa.CheckConstraint(
            "slot_ordinal >= 0",
            name="ck_collection_primary_slot_ordinal",
        ),
        sa.CheckConstraint(
            "slot_identity_hash ~ '^[0-9a-f]{64}$'",
            name="ck_collection_primary_slot_identity_hash",
        ),
        sa.CheckConstraint(
            "slot_role IN ('primary','supplementary','topup')",
            name="ck_collection_primary_slot_role",
        ),
        sa.CheckConstraint(
            "(slot_role = 'primary' AND role_reason IS NULL "
            "AND related_primary_slot_key IS NULL) OR "
            "(slot_role <> 'primary' AND role_reason IS NOT NULL "
            "AND btrim(role_reason) <> '' "
            "AND related_primary_slot_key IS NOT NULL)",
            name="ck_collection_primary_slot_role_reason",
        ),
        sa.CheckConstraint(
            _SURFACE_CHECK.format(column="collection_surface"),
            name="ck_collection_primary_slot_surface",
        ),
        sa.CheckConstraint(
            "province_code ~ '^[0-9]{6}$'",
            name="ck_collection_primary_slot_province",
        ),
        sa.CheckConstraint(
            "btrim(slot_key) <> '' AND btrim(question_slot_id) <> '' "
            "AND btrim(question_revision) <> '' AND btrim(platform) <> '' "
            "AND btrim(product_variant) <> '' AND btrim(interaction_mode) <> ''",
            name="ck_collection_primary_slot_required_text",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_primary_slot_campaign_role",
        "collection_primary_slot",
        ["tenant_id", "project_id", "campaign_id", "slot_role"],
        schema="platform",
    )


def _create_backfill_audit_table() -> None:
    op.create_table(
        "collection_surface_backfill_run",
        *_identity_columns(),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("selector_version", sa.String(length=128), nullable=False),
        sa.Column("selector_hash", sa.String(length=64), nullable=False),
        sa.Column("batch_key", sa.String(length=128), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_run_pub_id", sa.String(length=30)),
        sa.Column("requested_by_pub_id", sa.String(length=255), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("surface_assignment_basis", sa.String(length=128), nullable=False),
        sa.Column("legacy_contract_version", sa.String(length=80), nullable=False),
        sa.Column("candidate_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("assigned_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "already_consistent_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("conflict_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("orphan_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("excluded_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "sample_fact_pub_ids_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Public fact IDs only; question and answer content is forbidden.",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=128)),
        *_scope_constraints("collection_surface_backfill_run"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key",
            name="uq_collection_surface_backfill_idempotency",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('dry_run','apply')",
            name="ck_collection_surface_backfill_mode",
        ),
        sa.CheckConstraint(
            "state IN ('running','completed','failed')",
            name="ck_collection_surface_backfill_state",
        ),
        sa.CheckConstraint(
            "collection_surface = 'consumer_web'",
            name="ck_collection_surface_backfill_surface",
        ),
        sa.CheckConstraint(
            "selector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_collection_surface_backfill_selector_hash",
        ),
        sa.CheckConstraint("batch_size > 0", name="ck_collection_surface_backfill_batch"),
        sa.CheckConstraint(
            "candidate_count >= 0 AND assigned_count >= 0 "
            "AND already_consistent_count >= 0 AND conflict_count >= 0 "
            "AND orphan_count >= 0 AND excluded_count >= 0",
            name="ck_collection_surface_backfill_counts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sample_fact_pub_ids_json) = 'array'",
            name="ck_collection_surface_backfill_samples_json",
        ),
        sa.CheckConstraint(
            "btrim(selector_version) <> '' AND btrim(batch_key) <> '' "
            "AND btrim(idempotency_key) <> '' AND btrim(requested_by_pub_id) <> '' "
            "AND btrim(surface_assignment_basis) <> '' "
            "AND btrim(legacy_contract_version) <> ''",
            name="ck_collection_surface_backfill_required_text",
        ),
        sa.CheckConstraint(
            "(state = 'running' AND completed_at IS NULL) OR "
            "(state IN ('completed','failed') AND completed_at IS NOT NULL)",
            name="ck_collection_surface_backfill_completion",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_surface_backfill_project_started",
        "collection_surface_backfill_run",
        ["tenant_id", "project_id", "started_at", "pub_id"],
        schema="platform",
    )


def _create_immutability_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_config_v2_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.lifecycle_state IN ('frozen','active','superseded','retired') THEN
              RAISE EXCEPTION 'an immutable collection config cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.lifecycle_state IN ('frozen','active','superseded','retired')
             AND ROW(
               NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.revision,
               NEW.parent_revision_id, NEW.schema_version,
               NEW.question_set_revision, NEW.canonical_json, NEW.revision_hash,
               NEW.capability_registry_revision, NEW.comparison_policy_revision,
               NEW.samples_per_cell, NEW.province_codes_json,
               NEW.schedule_policy_json, NEW.change_reason,
               NEW.change_request_pub_id, NEW.approved_by_pub_id, NEW.frozen_at
             ) IS DISTINCT FROM ROW(
               OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.revision,
               OLD.parent_revision_id, OLD.schema_version,
               OLD.question_set_revision, OLD.canonical_json, OLD.revision_hash,
               OLD.capability_registry_revision, OLD.comparison_policy_revision,
               OLD.samples_per_cell, OLD.province_codes_json,
               OLD.schedule_policy_json, OLD.change_reason,
               OLD.change_request_pub_id, OLD.approved_by_pub_id, OLD.frozen_at
             ) THEN
            RAISE EXCEPTION 'frozen collection config content is immutable';
          END IF;

          IF (OLD.lifecycle_state = 'draft'
                AND NEW.lifecycle_state NOT IN ('draft','candidate'))
             OR (OLD.lifecycle_state = 'candidate'
                AND NEW.lifecycle_state NOT IN ('draft','candidate','frozen'))
             OR (OLD.lifecycle_state = 'frozen'
                AND NEW.lifecycle_state NOT IN ('frozen','active','retired'))
             OR (OLD.lifecycle_state = 'active'
                AND NEW.lifecycle_state NOT IN ('active','superseded','retired'))
             OR (OLD.lifecycle_state = 'superseded'
                AND NEW.lifecycle_state NOT IN ('superseded','retired'))
             OR (OLD.lifecycle_state = 'retired'
                AND NEW.lifecycle_state <> 'retired') THEN
            RAISE EXCEPTION 'invalid collection config lifecycle transition: % -> %',
              OLD.lifecycle_state, NEW.lifecycle_state;
          END IF;

          IF OLD.lifecycle_state IN ('active','superseded','retired')
             AND NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'collection config activation timestamp is immutable';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_config_v2_immutable_trg
        BEFORE UPDATE OR DELETE ON platform.collection_config_revision_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_config_v2_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_config_target_v2_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            SELECT lifecycle_state INTO parent_state
              FROM platform.collection_config_revision_v2
             WHERE id=OLD.config_revision_id
               AND tenant_id=OLD.tenant_id
               AND project_id=OLD.project_id;
            IF parent_state IN ('frozen','active','superseded','retired') THEN
              RAISE EXCEPTION 'targets of a frozen collection config are immutable';
            END IF;
          END IF;

          IF TG_OP <> 'DELETE' THEN
            SELECT lifecycle_state INTO parent_state
              FROM platform.collection_config_revision_v2
             WHERE id=NEW.config_revision_id
               AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF parent_state IN ('frozen','active','superseded','retired') THEN
              RAISE EXCEPTION 'targets cannot be attached to a frozen collection config';
            END IF;
            RETURN NEW;
          END IF;
          RETURN OLD;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_config_target_v2_mutation_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_config_target_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_config_target_v2_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.validate_collection_campaign_target_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM platform.collection_campaign AS campaign
              JOIN platform.collection_config_target_v2 AS target
                ON target.id=NEW.config_target_id
               AND target.tenant_id=NEW.tenant_id
               AND target.project_id=NEW.project_id
               AND target.config_revision_id=campaign.config_revision_id
             WHERE campaign.id=NEW.campaign_id
               AND campaign.tenant_id=NEW.tenant_id
               AND campaign.project_id=NEW.project_id
               AND ROW(
                 target.target_key, target.platform, target.collection_surface,
                 target.product_variant, target.interaction_modes_json,
                 target.capability_revisions_json
               ) IS NOT DISTINCT FROM ROW(
                 NEW.target_key, NEW.platform, NEW.collection_surface,
                 NEW.product_variant, NEW.interaction_modes_json,
                 NEW.capability_revisions_json
               )
          ) THEN
            RAISE EXCEPTION
              'campaign target must exactly match its scoped config target';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_campaign_target_identity_trg
        BEFORE INSERT OR UPDATE ON platform.collection_campaign_target
        FOR EACH ROW EXECUTE FUNCTION platform.validate_collection_campaign_target_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_campaign_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          slot_total bigint;
          slot_min bigint;
          slot_max bigint;
          primary_total bigint;
          non_primary_total bigint;
          checkpoint_total bigint;
          checkpoint_min bigint;
          checkpoint_max bigint;
          final_chain_hash text;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.state <> 'assembling'
               OR NEW.materialization_state <> 'pending'
               OR NEW.materialization_cursor <> 0
               OR NEW.materialized_slot_count <> 0
               OR NEW.membership_hash IS NOT NULL
               OR NEW.frozen_at IS NOT NULL THEN
              RAISE EXCEPTION
                'new collection campaign must begin pending and assembling';
            END IF;
            RETURN NEW;
          END IF;

          IF TG_OP = 'DELETE' THEN
            IF OLD.state <> 'assembling' OR OLD.materialization_cursor <> 0 THEN
              RAISE EXCEPTION
                'a frozen or materialized collection campaign cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.state = 'frozen' THEN
            RAISE EXCEPTION 'frozen collection campaign identity is immutable';
          END IF;

          IF ROW(
               NEW.id, NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.created_at,
               NEW.config_revision_id,
               NEW.config_revision_hash, NEW.question_set_revision,
               NEW.time_window_key, NEW.run_trigger_source,
               NEW.trigger_idempotency_key, NEW.binding_policy_revision,
               NEW.membership_specification_json,
               NEW.specification_schema_version, NEW.specification_hash,
               NEW.slot_generator_version, NEW.membership_digest_version,
               NEW.expected_primary_slot_count,
               NEW.expected_non_primary_slot_count, NEW.expected_slot_count,
               NEW.created_by_pub_id, NEW.triggered_by_pub_id
             ) IS DISTINCT FROM ROW(
               OLD.id, OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.created_at,
               OLD.config_revision_id,
               OLD.config_revision_hash, OLD.question_set_revision,
               OLD.time_window_key, OLD.run_trigger_source,
               OLD.trigger_idempotency_key, OLD.binding_policy_revision,
               OLD.membership_specification_json,
               OLD.specification_schema_version, OLD.specification_hash,
               OLD.slot_generator_version, OLD.membership_digest_version,
               OLD.expected_primary_slot_count,
               OLD.expected_non_primary_slot_count, OLD.expected_slot_count,
               OLD.created_by_pub_id, OLD.triggered_by_pub_id
          ) THEN
            RAISE EXCEPTION
              'assembling collection campaign specification is immutable';
          END IF;

          IF NEW.approved_by_pub_id IS DISTINCT FROM OLD.approved_by_pub_id
             AND NOT (OLD.state = 'assembling' AND NEW.state = 'frozen') THEN
            RAISE EXCEPTION
              'campaign approval identity may only be fixed at finalization';
          END IF;

          IF NEW.state NOT IN ('assembling','frozen') THEN
            RAISE EXCEPTION 'invalid collection campaign freeze transition: % -> %',
              OLD.state, NEW.state;
          END IF;

          IF ROW(
               NEW.materialized_slot_count, NEW.materialization_cursor,
               NEW.materialization_state
             ) IS DISTINCT FROM ROW(
               OLD.materialized_slot_count, OLD.materialization_cursor,
               OLD.materialization_state
             ) THEN
            IF NEW.state <> 'assembling'
               OR NEW.materialization_cursor <= OLD.materialization_cursor
               OR NEW.materialized_slot_count <> NEW.materialization_cursor
               OR NEW.materialization_state IS DISTINCT FROM (CASE
                    WHEN NEW.materialization_cursor = NEW.expected_slot_count
                    THEN 'complete'
                    ELSE 'materializing'
                  END)
               OR NOT EXISTS (
                    SELECT 1
                      FROM platform.collection_campaign_materialization_batch AS batch
                     WHERE batch.campaign_id=OLD.id
                       AND batch.tenant_id=OLD.tenant_id
                       AND batch.project_id=OLD.project_id
                       AND batch.specification_hash=OLD.specification_hash
                       AND batch.slot_generator_version=OLD.slot_generator_version
                       AND batch.start_slot_ordinal=OLD.materialization_cursor
                       AND batch.end_slot_ordinal_exclusive=NEW.materialization_cursor
                       AND batch.slot_count=(
                         NEW.materialization_cursor - OLD.materialization_cursor
                       )
                       AND batch.batch_state='completed'
                  ) THEN
              RAISE EXCEPTION
                'campaign materialization progress requires one completed contiguous batch';
            END IF;
          END IF;

          IF OLD.state = 'assembling' AND NEW.state = 'frozen' THEN
            IF NEW.materialization_state <> 'complete'
               OR NEW.materialization_cursor <> NEW.expected_slot_count
               OR NEW.materialized_slot_count <> NEW.expected_slot_count
               OR NEW.membership_hash IS NULL
               OR NEW.membership_hash !~ '^[0-9a-f]{64}$'
               OR NEW.frozen_at IS NULL THEN
              RAISE EXCEPTION
                'collection campaign cannot freeze before materialization completes';
            END IF;

            SELECT count(*), min(slot_ordinal), max(slot_ordinal),
                   count(*) FILTER (WHERE slot_role='primary'),
                   count(*) FILTER (WHERE slot_role<>'primary')
              INTO slot_total, slot_min, slot_max,
                   primary_total, non_primary_total
              FROM platform.collection_primary_slot
             WHERE campaign_id=OLD.id
               AND tenant_id=OLD.tenant_id
               AND project_id=OLD.project_id;
            IF slot_total <> NEW.expected_slot_count
               OR slot_min <> 0
               OR slot_max <> NEW.expected_slot_count - 1
               OR primary_total <> NEW.expected_primary_slot_count
               OR non_primary_total <> NEW.expected_non_primary_slot_count THEN
              RAISE EXCEPTION
                'collection campaign slot ordinals or role counts are incomplete';
            END IF;

            IF EXISTS (
              SELECT 1
                FROM platform.collection_campaign_materialization_batch AS batch
               WHERE batch.campaign_id=OLD.id
                 AND batch.tenant_id=OLD.tenant_id
                 AND batch.project_id=OLD.project_id
                 AND batch.batch_state<>'completed'
            ) THEN
              RAISE EXCEPTION
                'collection campaign has an incomplete materialization batch';
            END IF;

            SELECT coalesce(sum(slot_count), 0),
                   min(start_slot_ordinal), max(end_slot_ordinal_exclusive)
              INTO checkpoint_total, checkpoint_min, checkpoint_max
              FROM platform.collection_campaign_materialization_batch
             WHERE campaign_id=OLD.id
               AND tenant_id=OLD.tenant_id
               AND project_id=OLD.project_id
               AND batch_state='completed';
            IF checkpoint_total <> NEW.expected_slot_count
               OR checkpoint_min <> 0
               OR checkpoint_max <> NEW.expected_slot_count
               OR EXISTS (
                    SELECT 1
                      FROM (
                        SELECT start_slot_ordinal,
                               lag(end_slot_ordinal_exclusive) OVER (
                                 ORDER BY start_slot_ordinal
                               ) AS prior_end
                          FROM platform.collection_campaign_materialization_batch
                         WHERE campaign_id=OLD.id
                           AND tenant_id=OLD.tenant_id
                           AND project_id=OLD.project_id
                           AND batch_state='completed'
                      ) AS ordered_batch
                     WHERE (prior_end IS NULL AND start_slot_ordinal <> 0)
                        OR (prior_end IS NOT NULL
                            AND start_slot_ordinal <> prior_end)
                  ) THEN
              RAISE EXCEPTION
                'collection campaign checkpoints do not cover one contiguous range';
            END IF;

            SELECT membership_chain_hash INTO final_chain_hash
              FROM platform.collection_campaign_materialization_batch
             WHERE campaign_id=OLD.id
               AND tenant_id=OLD.tenant_id
               AND project_id=OLD.project_id
               AND end_slot_ordinal_exclusive=NEW.expected_slot_count
               AND batch_state='completed';
            IF final_chain_hash IS NULL
               OR NEW.membership_hash IS DISTINCT FROM final_chain_hash THEN
              RAISE EXCEPTION
                'campaign membership hash must equal the final ordered slot chain';
            END IF;
          ELSIF NEW.state <> 'assembling'
             OR NEW.membership_hash IS NOT NULL
             OR NEW.frozen_at IS NOT NULL THEN
            RAISE EXCEPTION 'invalid collection campaign freeze transition: % -> %',
              OLD.state, NEW.state;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_campaign_immutable_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_campaign
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_campaign_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_membership_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          parent_state text;
          parent_materialization_state text;
          parent_cursor bigint;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION
              'campaign membership structure is append-only';
          END IF;
          SELECT state, materialization_state, materialization_cursor
            INTO parent_state, parent_materialization_state, parent_cursor
            FROM platform.collection_campaign
           WHERE id=NEW.campaign_id
             AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id;
          IF parent_state IS DISTINCT FROM 'assembling'
             OR parent_materialization_state IS DISTINCT FROM 'pending'
             OR parent_cursor IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION
              'campaign structure can only change before slot materialization';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    for table in (
        "collection_campaign_target",
        "collection_sampling_leg",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable_trg
            BEFORE INSERT OR UPDATE OR DELETE ON platform.{table}
            FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_membership_immutable()
            """
        )

    op.execute(
        """
        CREATE FUNCTION platform.validate_collection_campaign_batch_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          campaign_state text;
          campaign_materialization_state text;
          campaign_cursor bigint;
          campaign_expected bigint;
          campaign_specification_hash text;
          campaign_generator_version text;
          campaign_digest_version text;
          expected_chain_seed text;
        BEGIN
          SELECT state, materialization_state, materialization_cursor,
                 expected_slot_count, specification_hash, slot_generator_version,
                 membership_digest_version
            INTO campaign_state, campaign_materialization_state, campaign_cursor,
                 campaign_expected, campaign_specification_hash,
                 campaign_generator_version, campaign_digest_version
            FROM platform.collection_campaign
           WHERE id=NEW.campaign_id
             AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id
           FOR UPDATE;

          IF NOT FOUND
             OR campaign_state IS DISTINCT FROM 'assembling'
             OR campaign_materialization_state NOT IN ('pending','materializing')
             OR NEW.batch_state IS DISTINCT FROM 'preparing'
             OR NEW.committed_at IS NOT NULL
             OR NEW.specification_hash IS DISTINCT FROM campaign_specification_hash
             OR NEW.slot_generator_version IS DISTINCT FROM campaign_generator_version
             OR NEW.start_slot_ordinal IS DISTINCT FROM campaign_cursor
             OR NEW.end_slot_ordinal_exclusive > campaign_expected THEN
            RAISE EXCEPTION
              'materialization batch must start at the locked campaign cursor';
          END IF;

          IF NEW.start_slot_ordinal = 0 THEN
            expected_chain_seed := encode(
              digest(
                format(
                  '{"digest_version":"%s","expected_slot_count":%s,'
                  '"slot_generator_version":"%s","specification_hash":"%s"}',
                  campaign_digest_version,
                  campaign_expected,
                  campaign_generator_version,
                  campaign_specification_hash
                ),
                'sha256'
              ),
              'hex'
            );
            IF NEW.prior_membership_chain_hash <> expected_chain_seed THEN
              RAISE EXCEPTION
                'first materialization batch must use the membership chain seed';
            END IF;
          ELSIF NOT EXISTS (
            SELECT 1
              FROM platform.collection_campaign_materialization_batch AS prior_batch
             WHERE prior_batch.campaign_id=NEW.campaign_id
               AND prior_batch.tenant_id=NEW.tenant_id
               AND prior_batch.project_id=NEW.project_id
               AND prior_batch.end_slot_ordinal_exclusive=NEW.start_slot_ordinal
               AND prior_batch.membership_chain_hash=NEW.prior_membership_chain_hash
               AND prior_batch.batch_state='completed'
          ) THEN
            RAISE EXCEPTION
              'materialization batch membership chain is not contiguous';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_campaign_batch_prepare_trg
        BEFORE INSERT ON platform.collection_campaign_materialization_batch
        FOR EACH ROW EXECUTE FUNCTION platform.validate_collection_campaign_batch_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_slot_materialization()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          parent_state text;
          parent_expected bigint;
          batch_start bigint;
          batch_end bigint;
          batch_state text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'materialized campaign slots are append-only';
          END IF;

          SELECT campaign.state, campaign.expected_slot_count,
                 batch.start_slot_ordinal, batch.end_slot_ordinal_exclusive,
                 batch.batch_state
            INTO parent_state, parent_expected, batch_start, batch_end, batch_state
            FROM platform.collection_campaign_materialization_batch AS batch
            JOIN platform.collection_campaign AS campaign
              ON campaign.id=batch.campaign_id
             AND campaign.tenant_id=batch.tenant_id
             AND campaign.project_id=batch.project_id
           WHERE batch.id=NEW.materialization_batch_id
             AND batch.campaign_id=NEW.campaign_id
             AND batch.tenant_id=NEW.tenant_id
             AND batch.project_id=NEW.project_id
           FOR SHARE OF batch, campaign;

          IF NOT FOUND
             OR parent_state IS DISTINCT FROM 'assembling'
             OR batch_state IS DISTINCT FROM 'preparing'
             OR NEW.slot_ordinal < batch_start
             OR NEW.slot_ordinal >= batch_end
             OR NEW.slot_ordinal >= parent_expected
             OR NEW.slot_identity_hash IS DISTINCT FROM encode(
                  digest(NEW.slot_key, 'sha256'), 'hex'
                ) THEN
            RAISE EXCEPTION
              'slot must belong to the current preparing materialization batch';
          END IF;
          IF NEW.slot_role <> 'primary' AND NOT EXISTS (
            SELECT 1
              FROM platform.collection_primary_slot AS primary_slot
             WHERE primary_slot.tenant_id=NEW.tenant_id
               AND primary_slot.project_id=NEW.project_id
               AND primary_slot.campaign_id=NEW.campaign_id
               AND primary_slot.slot_key=NEW.related_primary_slot_key
               AND primary_slot.slot_role='primary'
          ) THEN
            RAISE EXCEPTION
              'non-primary materialization slot must reference a primary slot';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_primary_slot_materialize_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_primary_slot
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_slot_materialization()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.complete_collection_campaign_batch()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          actual_count bigint;
          actual_min bigint;
          actual_max bigint;
          actual_distinct bigint;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'campaign materialization batches are immutable';
          END IF;
          IF OLD.batch_state <> 'preparing'
             OR NEW.batch_state <> 'completed'
             OR NEW.committed_at IS NULL
             OR ROW(
                  NEW.id, NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.created_at,
                  NEW.campaign_id,
                  NEW.specification_hash, NEW.slot_generator_version,
                  NEW.start_slot_ordinal, NEW.end_slot_ordinal_exclusive,
                  NEW.slot_count, NEW.chunk_hash,
                  NEW.prior_membership_chain_hash, NEW.membership_chain_hash,
                  NEW.idempotency_key
                ) IS DISTINCT FROM ROW(
                  OLD.id, OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.created_at,
                  OLD.campaign_id,
                  OLD.specification_hash, OLD.slot_generator_version,
                  OLD.start_slot_ordinal, OLD.end_slot_ordinal_exclusive,
                  OLD.slot_count, OLD.chunk_hash,
                  OLD.prior_membership_chain_hash, OLD.membership_chain_hash,
                  OLD.idempotency_key
                ) THEN
            RAISE EXCEPTION
              'materialization batch only permits preparing to completed';
          END IF;

          SELECT count(*), min(slot_ordinal), max(slot_ordinal),
                 count(DISTINCT slot_ordinal)
            INTO actual_count, actual_min, actual_max, actual_distinct
            FROM platform.collection_primary_slot
           WHERE materialization_batch_id=OLD.id
             AND campaign_id=OLD.campaign_id
             AND tenant_id=OLD.tenant_id
             AND project_id=OLD.project_id;
          IF actual_count <> NEW.slot_count
             OR actual_distinct <> NEW.slot_count
             OR actual_min <> NEW.start_slot_ordinal
             OR actual_max <> NEW.end_slot_ordinal_exclusive - 1 THEN
            RAISE EXCEPTION
              'materialization batch slot range is incomplete';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_campaign_batch_complete_trg
        BEFORE UPDATE OR DELETE
        ON platform.collection_campaign_materialization_batch
        FOR EACH ROW EXECUTE FUNCTION platform.complete_collection_campaign_batch()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.advance_collection_campaign_materialization()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE affected_rows integer;
        BEGIN
          UPDATE platform.collection_campaign
             SET materialization_cursor=NEW.end_slot_ordinal_exclusive,
                 materialized_slot_count=NEW.end_slot_ordinal_exclusive,
                 materialization_state=CASE
                   WHEN NEW.end_slot_ordinal_exclusive=expected_slot_count
                   THEN 'complete'
                   ELSE 'materializing'
                 END,
                 version=version + 1,
                 updated_at=now()
           WHERE id=NEW.campaign_id
             AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id
             AND state='assembling'
             AND specification_hash=NEW.specification_hash
             AND slot_generator_version=NEW.slot_generator_version
             AND materialization_cursor=NEW.start_slot_ordinal
             AND materialized_slot_count=NEW.start_slot_ordinal
             AND membership_hash IS NULL;
          GET DIAGNOSTICS affected_rows = ROW_COUNT;
          IF affected_rows <> 1 THEN
            RAISE EXCEPTION
              'materialization batch could not atomically advance its campaign';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_campaign_batch_advance_trg
        AFTER UPDATE OF batch_state
        ON platform.collection_campaign_materialization_batch
        FOR EACH ROW
        WHEN (OLD.batch_state = 'preparing' AND NEW.batch_state = 'completed')
        EXECUTE FUNCTION platform.advance_collection_campaign_materialization()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.enforce_collection_campaign_batch_completed()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE persisted_state text;
        BEGIN
          SELECT batch_state INTO persisted_state
            FROM platform.collection_campaign_materialization_batch
           WHERE id=NEW.id
             AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id;
          IF persisted_state IS DISTINCT FROM 'completed' THEN
            RAISE EXCEPTION
              'a preparing materialization batch cannot survive commit';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER collection_campaign_batch_commit_trg
        AFTER INSERT ON platform.collection_campaign_materialization_batch
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION platform.enforce_collection_campaign_batch_completed()
        """
    )


def _add_fact_surface_columns() -> None:
    for schema, table in _FACT_COLUMNS:
        for column in _FACT_COLUMNS[(schema, table)]:
            if column in {
                "config_revision_v2_id",
                "campaign_id",
                "campaign_target_id",
                "sampling_leg_id",
                "primary_slot_id",
            }:
                sql_type: sa.TypeEngine[object] = sa.Uuid()
            elif column == "observed_product_variant":
                sql_type = sa.String(length=128)
            elif column in {
                "collection_surface",
                "requested_surface",
                "observed_surface",
            }:
                sql_type = sa.String(length=30)
            elif column == "legacy_contract_version":
                sql_type = sa.String(length=80)
            else:
                sql_type = sa.String(length=128)
            op.add_column(table, sa.Column(column, sql_type), schema=schema)

    surface_columns = {
        ("platform", "collection_run"): ("collection_surface",),
        ("platform", "collection_task"): (
            "collection_surface",
            "requested_surface",
            "observed_surface",
        ),
        ("analytics", "answer"): ("collection_surface",),
        ("analytics", "answer_analysis"): ("collection_surface",),
        ("evidence", "evidence_asset"): ("collection_surface",),
        ("platform", "analysis_job"): (
            "collection_surface",
            "requested_surface",
            "observed_surface",
        ),
    }
    for (schema, table), columns in surface_columns.items():
        for column in columns:
            op.create_check_constraint(
                f"ck_{table}_{column}_s07",
                table,
                f"{column} IS NULL OR " + _SURFACE_CHECK.format(column=column),
                schema=schema,
            )

    for schema, table in _FACT_COLUMNS:
        op.create_check_constraint(
            f"ck_{table}_surface_basis_s07",
            table,
            "surface_assignment_basis IS NULL OR btrim(surface_assignment_basis) <> ''",
            schema=schema,
        )
        op.create_check_constraint(
            f"ck_{table}_legacy_contract_s07",
            table,
            "legacy_contract_version IS NULL OR btrim(legacy_contract_version) <> ''",
            schema=schema,
        )

    op.create_check_constraint(
        "ck_collection_task_observed_product_s07",
        "collection_task",
        "observed_product_variant IS NULL OR btrim(observed_product_variant) <> ''",
        schema="platform",
    )
    op.create_check_constraint(
        "ck_analysis_job_observed_product_s07",
        "analysis_job",
        "observed_product_variant IS NULL OR btrim(observed_product_variant) <> ''",
        schema="platform",
    )

    op.create_foreign_key(
        "fk_collection_run_config_v2_scope",
        "collection_run",
        "collection_config_revision_v2",
        ["config_revision_v2_id", "tenant_id", "project_id"],
        ["id", "tenant_id", "project_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_collection_run_campaign_config_scope",
        "collection_run",
        "collection_campaign",
        ["campaign_id", "tenant_id", "project_id", "config_revision_v2_id"],
        ["id", "tenant_id", "project_id", "config_revision_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_check_constraint(
        "ck_collection_run_campaign_config_s07",
        "collection_run",
        "campaign_id IS NULL OR config_revision_v2_id IS NOT NULL",
        schema="platform",
    )

    op.create_foreign_key(
        "fk_collection_task_campaign_target_tenant",
        "collection_task",
        "collection_campaign_target",
        ["campaign_target_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_collection_task_sampling_leg_tenant",
        "collection_task",
        "collection_sampling_leg",
        ["sampling_leg_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_collection_task_primary_slot_tenant",
        "collection_task",
        "collection_primary_slot",
        ["primary_slot_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.execute(
        """
        CREATE FUNCTION platform.validate_collection_task_identity_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE run_project_id uuid;
        BEGIN
          IF NEW.campaign_target_id IS NULL
             AND NEW.sampling_leg_id IS NULL
             AND NEW.primary_slot_id IS NULL THEN
            RETURN NEW;
          END IF;

          SELECT project_id INTO run_project_id
            FROM platform.collection_run
           WHERE id=NEW.run_id AND tenant_id=NEW.tenant_id;
          IF run_project_id IS NULL THEN
            RAISE EXCEPTION 'collection task run scope is unavailable';
          END IF;

          IF NEW.campaign_target_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM platform.collection_campaign_target AS target
             WHERE target.id=NEW.campaign_target_id
               AND target.tenant_id=NEW.tenant_id
               AND target.project_id=run_project_id
          ) THEN
            RAISE EXCEPTION 'collection task campaign target crosses project scope';
          END IF;

          IF NEW.sampling_leg_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM platform.collection_sampling_leg AS leg
             WHERE leg.id=NEW.sampling_leg_id
               AND leg.tenant_id=NEW.tenant_id
               AND leg.project_id=run_project_id
               AND (
                 NEW.campaign_target_id IS NULL
                 OR leg.campaign_target_id=NEW.campaign_target_id
               )
          ) THEN
            RAISE EXCEPTION 'collection task sampling leg crosses identity scope';
          END IF;

          IF NEW.primary_slot_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM platform.collection_primary_slot AS slot
             WHERE slot.id=NEW.primary_slot_id
               AND slot.tenant_id=NEW.tenant_id
               AND slot.project_id=run_project_id
               AND (
                 NEW.campaign_target_id IS NULL
                 OR slot.campaign_target_id=NEW.campaign_target_id
               )
               AND (
                 NEW.sampling_leg_id IS NULL
                 OR slot.sampling_leg_id=NEW.sampling_leg_id
               )
          ) THEN
            RAISE EXCEPTION 'collection task primary slot crosses identity scope';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_task_identity_scope_trg
        BEFORE INSERT OR UPDATE OF
          run_id, tenant_id, campaign_target_id, sampling_leg_id, primary_slot_id
        ON platform.collection_task
        FOR EACH ROW EXECUTE FUNCTION platform.validate_collection_task_identity_scope()
        """
    )

    op.create_index(
        "ix_collection_run_surface_campaign_s07",
        "collection_run",
        ["tenant_id", "project_id", "collection_surface", "campaign_id"],
        schema="platform",
    )
    op.create_index(
        "ix_collection_task_surface_slot_s07",
        "collection_task",
        ["tenant_id", "collection_surface", "primary_slot_id"],
        schema="platform",
    )
    op.create_index(
        "ix_answer_surface_s07",
        "answer",
        ["tenant_pub_id", "project_pub_id", "collection_surface", "capture_time"],
        schema="analytics",
    )
    op.create_index(
        "ix_answer_analysis_surface_s07",
        "answer_analysis",
        ["tenant_pub_id", "collection_surface", "capture_time"],
        schema="analytics",
    )
    op.create_index(
        "ix_evidence_asset_surface_s07",
        "evidence_asset",
        ["tenant_pub_id", "project_pub_id", "collection_surface", "capture_time"],
        schema="evidence",
    )
    op.create_index(
        "ix_analysis_job_surface_s07",
        "analysis_job",
        ["tenant_id", "collection_surface", "state"],
        schema="platform",
    )


def _grant_minimum_privileges() -> None:
    op.execute(
        """
        DO $$
        DECLARE table_name text;
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            FOREACH table_name IN ARRAY ARRAY[
              'collection_config_revision_v2','collection_config_target_v2',
              'collection_campaign','collection_campaign_target',
              'collection_sampling_leg',
              'collection_campaign_materialization_batch',
              'collection_primary_slot',
              'collection_surface_backfill_run'
            ] LOOP
              EXECUTE format(
                'GRANT SELECT,INSERT,UPDATE ON platform.%I TO geo', table_name
              );
            END LOOP;
          END IF;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT,INSERT,UPDATE ON
              platform.collection_config_revision_v2,
              platform.collection_config_target_v2,
              platform.collection_campaign,
              platform.collection_campaign_target,
              platform.collection_sampling_leg,
              platform.collection_primary_slot
            TO geo_api;
            GRANT SELECT ON
              platform.collection_campaign_materialization_batch,
              platform.collection_surface_backfill_run
            TO geo_api;
          END IF;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT ON
              platform.collection_config_revision_v2,
              platform.collection_config_target_v2
            TO geo_worker;
            GRANT SELECT,INSERT,UPDATE ON
              platform.collection_campaign,
              platform.collection_campaign_target,
              platform.collection_sampling_leg,
              platform.collection_campaign_materialization_batch,
              platform.collection_primary_slot,
              platform.collection_surface_backfill_run
            TO geo_worker;
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    # A project-scoped FK makes tenant/project ownership enforceable without a
    # trigger or a soft public-id join.  ``project.id`` remains the primary key.
    op.create_unique_constraint(
        "uq_project_id_tenant_s07",
        "project",
        ["id", "tenant_id"],
        schema="platform",
    )
    _create_config_tables()
    _create_campaign_tables()
    _create_backfill_audit_table()
    _create_immutability_guards()
    for table in _TENANT_TABLES:
        _enable_tenant_rls(table)
    _add_fact_surface_columns()
    _grant_minimum_privileges()


def _drop_fact_surface_columns() -> None:
    for schema, table, index_name in (
        ("platform", "analysis_job", "ix_analysis_job_surface_s07"),
        ("evidence", "evidence_asset", "ix_evidence_asset_surface_s07"),
        ("analytics", "answer_analysis", "ix_answer_analysis_surface_s07"),
        ("analytics", "answer", "ix_answer_surface_s07"),
        ("platform", "collection_task", "ix_collection_task_surface_slot_s07"),
        ("platform", "collection_run", "ix_collection_run_surface_campaign_s07"),
    ):
        op.drop_index(index_name, table_name=table, schema=schema)

    for constraint in (
        "fk_collection_task_primary_slot_tenant",
        "fk_collection_task_sampling_leg_tenant",
        "fk_collection_task_campaign_target_tenant",
    ):
        op.drop_constraint(constraint, "collection_task", schema="platform", type_="foreignkey")
    op.drop_constraint(
        "ck_collection_run_campaign_config_s07",
        "collection_run",
        schema="platform",
        type_="check",
    )
    op.drop_constraint(
        "fk_collection_run_campaign_config_scope",
        "collection_run",
        schema="platform",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_collection_run_config_v2_scope",
        "collection_run",
        schema="platform",
        type_="foreignkey",
    )
    for schema, table, column in (
        ("platform", "analysis_job", "observed_surface"),
        ("platform", "analysis_job", "requested_surface"),
        ("platform", "analysis_job", "collection_surface"),
        ("evidence", "evidence_asset", "collection_surface"),
        ("analytics", "answer_analysis", "collection_surface"),
        ("analytics", "answer", "collection_surface"),
        ("platform", "collection_task", "observed_surface"),
        ("platform", "collection_task", "requested_surface"),
        ("platform", "collection_task", "collection_surface"),
        ("platform", "collection_run", "collection_surface"),
    ):
        op.drop_constraint(
            f"ck_{table}_{column}_s07",
            table,
            schema=schema,
            type_="check",
        )
    for schema, table in reversed(tuple(_FACT_COLUMNS)):
        op.drop_constraint(
            f"ck_{table}_legacy_contract_s07",
            table,
            schema=schema,
            type_="check",
        )
        op.drop_constraint(
            f"ck_{table}_surface_basis_s07",
            table,
            schema=schema,
            type_="check",
        )
    op.drop_constraint(
        "ck_analysis_job_observed_product_s07",
        "analysis_job",
        schema="platform",
        type_="check",
    )
    op.drop_constraint(
        "ck_collection_task_observed_product_s07",
        "collection_task",
        schema="platform",
        type_="check",
    )
    for (schema, table), columns in reversed(tuple(_FACT_COLUMNS.items())):
        for column in reversed(columns):
            op.drop_column(table, column, schema=schema)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS collection_task_identity_scope_trg ON platform.collection_task"
    )
    op.execute("DROP FUNCTION platform.validate_collection_task_identity_scope()")
    _drop_fact_surface_columns()

    for table in (
        "collection_campaign_target",
        "collection_sampling_leg",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_trg ON platform.{table}")
    op.execute(
        "DROP TRIGGER IF EXISTS collection_primary_slot_materialize_trg "
        "ON platform.collection_primary_slot"
    )
    for trigger in (
        "collection_campaign_batch_commit_trg",
        "collection_campaign_batch_advance_trg",
        "collection_campaign_batch_complete_trg",
        "collection_campaign_batch_prepare_trg",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger} "
            "ON platform.collection_campaign_materialization_batch"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS collection_campaign_immutable_trg ON platform.collection_campaign"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS collection_campaign_target_identity_trg "
        "ON platform.collection_campaign_target"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS collection_config_target_v2_mutation_trg "
        "ON platform.collection_config_target_v2"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS collection_config_v2_immutable_trg "
        "ON platform.collection_config_revision_v2"
    )
    op.execute("DROP FUNCTION platform.guard_collection_membership_immutable()")
    op.execute("DROP FUNCTION platform.enforce_collection_campaign_batch_completed()")
    op.execute("DROP FUNCTION platform.advance_collection_campaign_materialization()")
    op.execute("DROP FUNCTION platform.complete_collection_campaign_batch()")
    op.execute("DROP FUNCTION platform.guard_collection_slot_materialization()")
    op.execute("DROP FUNCTION platform.validate_collection_campaign_batch_insert()")
    op.execute("DROP FUNCTION platform.guard_collection_campaign_immutable()")
    op.execute("DROP FUNCTION platform.validate_collection_campaign_target_identity()")
    op.execute("DROP FUNCTION platform.guard_collection_config_target_v2_mutation()")
    op.execute("DROP FUNCTION platform.guard_collection_config_v2_immutable()")

    op.drop_table("collection_surface_backfill_run", schema="platform")
    op.drop_table("collection_primary_slot", schema="platform")
    op.drop_table("collection_campaign_materialization_batch", schema="platform")
    op.drop_table("collection_sampling_leg", schema="platform")
    op.drop_table("collection_campaign_target", schema="platform")
    op.drop_table("collection_campaign", schema="platform")
    op.drop_table("collection_config_target_v2", schema="platform")
    op.drop_table("collection_config_revision_v2", schema="platform")
    op.drop_constraint(
        "uq_project_id_tenant_s07",
        "project",
        schema="platform",
        type_="unique",
    )


__all__ = ["downgrade", "upgrade"]
