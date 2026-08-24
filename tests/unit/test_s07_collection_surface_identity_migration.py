from __future__ import annotations

import importlib.util
import re
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations/versions/s07_0001_collection_surface_identity.py"
NEW_TABLES = (
    "collection_config_revision_v2",
    "collection_config_target_v2",
    "collection_campaign",
    "collection_campaign_target",
    "collection_sampling_leg",
    "collection_campaign_materialization_batch",
    "collection_primary_slot",
    "collection_surface_backfill_run",
)
SURFACES = ("provider_api", "consumer_web", "consumer_app")
ADDED_COLUMNS = {
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


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s07_surface_identity", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(operation: str) -> str:
    module = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        getattr(module, operation)()
    return output.getvalue()


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _render("downgrade")


def _table_block(sql: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE platform\.{re.escape(table)} \((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing DDL for platform.{table}"
    return match.group(1)


def test_revision_chain_has_one_head_and_identity_extends_frozen_predecessor() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert len(scripts.get_heads()) == 1
    revision = scripts.get_revision("s07_0001_surface_identity")
    assert revision is not None
    assert revision.down_revision == "s06_0038_w_review"


def test_all_new_tables_have_scoped_identity_rls_and_force(upgrade_sql: str) -> None:
    required_columns = (
        "id",
        "pub_id",
        "tenant_id",
        "project_id",
        "version",
        "created_at",
        "updated_at",
    )
    for table in NEW_TABLES:
        block = _table_block(upgrade_sql, table)
        for column in required_columns:
            assert re.search(rf"\b{column}\b", block), (table, column)
        assert f"CONSTRAINT pk_{table} PRIMARY KEY (id)" in block
        assert f"CONSTRAINT uq_{table}_pub_id UNIQUE (pub_id)" in block
        assert f"CONSTRAINT uq_{table}_id_scope UNIQUE (id, tenant_id, project_id)" in block
        assert f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'CREATE POLICY tenant_isolation ON platform."{table}"' in upgrade_sql

    assert "CONSTRAINT uq_project_id_tenant_s07 UNIQUE (id, tenant_id)" in upgrade_sql
    assert upgrade_sql.count("current_setting('app.tenant_id', true)") == 2 * len(NEW_TABLES)


def test_config_revision_and_target_contract_is_strict(upgrade_sql: str) -> None:
    revision = _table_block(upgrade_sql, "collection_config_revision_v2")
    for column in (
        "revision",
        "parent_revision_id",
        "lifecycle_state",
        "schema_version",
        "question_set_revision",
        "canonical_json",
        "revision_hash",
        "capability_registry_revision",
        "comparison_policy_revision",
        "samples_per_cell",
        "province_codes_json",
        "schedule_policy_json",
        "change_reason",
        "change_request_pub_id",
        "approved_by_pub_id",
        "frozen_at",
        "activated_at",
    ):
        assert re.search(rf"\b{column}\b", revision)
    assert "schema_version = 'collection-config-v2'" in revision
    assert "revision_hash ~ '^[0-9a-f]{64}$'" in revision
    assert "samples_per_cell > 0" in revision
    for lifecycle in ("draft", "candidate", "frozen", "active", "superseded", "retired"):
        assert f"'{lifecycle}'" in revision
    assert "uq_collection_config_v2_revision" in revision
    assert "uq_collection_config_v2_hash" in revision
    assert "fk_collection_config_v2_parent_scope" in revision

    target = _table_block(upgrade_sql, "collection_config_target_v2")
    for column in (
        "config_revision_id",
        "target_key",
        "platform",
        "collection_surface",
        "product_variant",
        "interaction_modes_json",
        "capability_revisions_json",
    ):
        assert re.search(rf"\b{column}\b", target)
    assert "uq_collection_config_target_v2_key" in target
    assert "uq_collection_config_target_v2_identity" in target
    assert "fk_collection_config_target_v2_config_scope" in target
    for surface in SURFACES:
        assert f"'{surface}'" in target

    assert "guard_collection_config_v2_immutable" in upgrade_sql
    assert "collection_config_v2_immutable_trg" in upgrade_sql
    assert "an immutable collection config cannot be deleted" in upgrade_sql
    assert "BEFORE UPDATE OR DELETE ON platform.collection_config_revision_v2" in upgrade_sql
    assert "guard_collection_config_target_v2_mutation" in upgrade_sql
    assert "collection_config_target_v2_mutation_trg" in upgrade_sql
    assert "frozen collection config content is immutable" in upgrade_sql


def test_campaign_target_leg_and_slot_freeze_full_identity(upgrade_sql: str) -> None:
    campaign = _table_block(upgrade_sql, "collection_campaign")
    for column in (
        "config_revision_id",
        "config_revision_hash",
        "question_set_revision",
        "time_window_key",
        "run_trigger_source",
        "trigger_idempotency_key",
        "binding_policy_revision",
        "membership_specification_json",
        "specification_schema_version",
        "specification_hash",
        "slot_generator_version",
        "membership_digest_version",
        "expected_primary_slot_count",
        "expected_non_primary_slot_count",
        "expected_slot_count",
        "materialized_slot_count",
        "materialization_state",
        "materialization_cursor",
        "membership_hash",
        "created_by_pub_id",
        "approved_by_pub_id",
        "triggered_by_pub_id",
        "frozen_at",
        "state",
    ):
        assert re.search(rf"\b{column}\b", campaign)
    assert "fk_collection_campaign_config_hash_scope" in campaign
    assert "uq_collection_campaign_trigger_idempotency" in campaign
    assert "uq_collection_campaign_membership_hash" in campaign
    assert "uq_collection_campaign_materialization_lineage" in campaign
    assert "digest(membership_specification_json, 'sha256')" in campaign
    assert "membership_hash VARCHAR(64)" in campaign
    assert "membership_hash VARCHAR(64) NOT NULL" not in campaign
    assert "question_slots_json" not in campaign
    assert "membership_json" not in campaign
    assert "expected_primary_slot_count > 0" in campaign
    assert "expected_non_primary_slot_count >= 0" in campaign
    assert (
        "expected_slot_count = expected_primary_slot_count + expected_non_primary_slot_count"
    ) in " ".join(campaign.split())
    assert "materialization_cursor = materialized_slot_count" in campaign
    for materialization_state in ("pending", "materializing", "complete"):
        assert f"'{materialization_state}'" in campaign
    assert re.search(r"state IN \('assembling',\s*'frozen'\)", campaign)
    assert "state = 'assembling' AND frozen_at IS NULL" in campaign
    assert "state = 'frozen' AND frozen_at IS NOT NULL" in campaign
    assert "CREATE INDEX ix_collection_campaign_project_frozen" in upgrade_sql
    assert "WHERE state = 'frozen'" in upgrade_sql

    campaign_target = _table_block(upgrade_sql, "collection_campaign_target")
    for column in (
        "campaign_id",
        "config_target_id",
        "target_key",
        "platform",
        "collection_surface",
        "product_variant",
        "interaction_modes_json",
        "capability_revisions_json",
        "binding_policy_revision",
    ):
        assert re.search(rf"\b{column}\b", campaign_target)
    assert "uq_collection_campaign_target_key" in campaign_target
    assert "validate_collection_campaign_target_identity" in upgrade_sql
    assert "BEFORE INSERT OR UPDATE ON platform.collection_campaign_target" in upgrade_sql

    leg = _table_block(upgrade_sql, "collection_sampling_leg")
    for column in (
        "campaign_id",
        "campaign_target_id",
        "leg_key",
        "platform",
        "collection_surface",
        "product_variant",
        "province_code",
        "interaction_mode",
    ):
        assert re.search(rf"\b{column}\b", leg)
    assert "fk_collection_sampling_leg_target_identity" in leg
    assert "uq_collection_sampling_leg_key" in leg

    slot = _table_block(upgrade_sql, "collection_primary_slot")
    for column in (
        "campaign_id",
        "campaign_target_id",
        "sampling_leg_id",
        "slot_key",
        "question_slot_id",
        "question_revision",
        "platform",
        "collection_surface",
        "product_variant",
        "province_code",
        "interaction_mode",
        "sample_ordinal",
        "slot_role",
        "role_reason",
        "related_primary_slot_key",
        "slot_ordinal",
        "slot_identity_hash",
        "materialization_batch_id",
    ):
        assert re.search(rf"\b{column}\b", slot)
    assert "sample_ordinal >= 1" in slot
    for role in ("primary", "supplementary", "topup"):
        assert f"'{role}'" in slot
    assert "slot_role <> 'primary' AND role_reason IS NOT NULL" in slot
    assert "btrim(role_reason) <> ''" in slot
    assert "related_primary_slot_key IS NOT NULL" in slot
    assert "fk_collection_primary_slot_leg_identity" in slot
    assert "fk_collection_primary_slot_materialization_batch" in slot
    assert "fk_collection_primary_slot_related_primary" in slot
    assert "uq_collection_primary_slot_logical_identity" in slot
    assert "uq_collection_primary_slot_ordinal" in slot
    assert "uq_collection_primary_slot_identity_hash" in slot
    assert "slot_ordinal >= 0" in slot
    assert "slot_identity_hash ~ '^[0-9a-f]{64}$'" in slot

    assert "guard_collection_campaign_immutable" in upgrade_sql
    assert "guard_collection_membership_immutable" in upgrade_sql
    assert "campaign structure can only change before slot materialization" in upgrade_sql
    assert "collection_primary_slot_materialize_trg" in upgrade_sql


def test_materialization_batches_are_contiguous_atomic_and_retry_safe(
    upgrade_sql: str,
) -> None:
    batch = _table_block(
        upgrade_sql,
        "collection_campaign_materialization_batch",
    )
    for column in (
        "campaign_id",
        "specification_hash",
        "slot_generator_version",
        "start_slot_ordinal",
        "end_slot_ordinal_exclusive",
        "slot_count",
        "chunk_hash",
        "prior_membership_chain_hash",
        "membership_chain_hash",
        "idempotency_key",
        "batch_state",
        "committed_at",
    ):
        assert re.search(rf"\b{column}\b", batch)
    assert "fk_collection_campaign_materialization_batch_lineage" in batch
    assert "uq_collection_campaign_materialization_batch_campaign_scope" in batch
    assert "uq_collection_campaign_materialization_batch_idempotency" in batch
    assert "uq_collection_campaign_materialization_batch_range" in batch
    assert "start_slot_ordinal >= 0" in batch
    assert "end_slot_ordinal_exclusive > start_slot_ordinal" in batch
    assert "slot_count = end_slot_ordinal_exclusive - start_slot_ordinal" in batch
    for state in ("preparing", "completed"):
        assert f"'{state}'" in batch

    for guard in (
        "validate_collection_campaign_batch_insert",
        "guard_collection_slot_materialization",
        "complete_collection_campaign_batch",
        "advance_collection_campaign_materialization",
        "enforce_collection_campaign_batch_completed",
    ):
        assert f"FUNCTION platform.{guard}" in upgrade_sql
    assert "FOR UPDATE" in upgrade_sql
    assert "NEW.start_slot_ordinal IS DISTINCT FROM campaign_cursor" in upgrade_sql
    assert re.search(r'"digest_version":"%{1,2}s"', upgrade_sql)
    assert re.search(r'"expected_slot_count":%{1,2}s', upgrade_sql)
    assert "expected_chain_seed := encode(" in upgrade_sql
    assert "prior_batch.membership_chain_hash=NEW.prior_membership_chain_hash" in upgrade_sql
    assert "count(DISTINCT slot_ordinal)" in upgrade_sql
    assert "digest(NEW.slot_key, 'sha256')" in upgrade_sql
    assert "primary_slot.slot_role='primary'" in upgrade_sql
    assert "materialization_cursor=NEW.end_slot_ordinal_exclusive" in upgrade_sql
    assert "NEW.materialization_state IS DISTINCT FROM (CASE" in upgrade_sql
    assert "GET DIAGNOSTICS affected_rows = ROW_COUNT" in upgrade_sql
    assert "DEFERRABLE INITIALLY DEFERRED" in upgrade_sql
    assert "a preparing materialization batch cannot survive commit" in upgrade_sql

    assert "expected_primary_slot_count" in upgrade_sql
    assert "primary_total <> NEW.expected_primary_slot_count" in upgrade_sql
    assert "slot_min <> 0" in upgrade_sql
    assert "slot_max <> NEW.expected_slot_count - 1" in upgrade_sql
    assert "batch.batch_state<>'completed'" in upgrade_sql
    assert "NEW.membership_hash IS DISTINCT FROM final_chain_hash" in upgrade_sql
    assert "assembling collection campaign specification is immutable" in upgrade_sql
    assert "new collection campaign must begin pending and assembling" in upgrade_sql
    assert "repeat('0'" not in upgrade_sql


def test_backfill_audit_is_count_only_and_resume_safe(upgrade_sql: str) -> None:
    audit = _table_block(upgrade_sql, "collection_surface_backfill_run")
    for column in (
        "execution_mode",
        "state",
        "selector_version",
        "selector_hash",
        "batch_key",
        "batch_size",
        "idempotency_key",
        "checkpoint_run_pub_id",
        "requested_by_pub_id",
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "candidate_count",
        "assigned_count",
        "already_consistent_count",
        "conflict_count",
        "orphan_count",
        "excluded_count",
        "sample_fact_pub_ids_json",
        "started_at",
        "completed_at",
        "error_code",
    ):
        assert re.search(rf"\b{column}\b", audit)
    assert re.search(r"execution_mode IN \('dry_run',\s*'apply'\)", audit)
    assert "batch_size > 0" in audit
    assert "selector_hash ~ '^[0-9a-f]{64}$'" in audit
    assert "collection_surface = 'consumer_web'" in audit
    assert "uq_collection_surface_backfill_idempotency" in audit
    assert not re.search(r"\b(query_text|answer_text|response_text|question_text)\b", audit)
    assert "Public fact IDs only; question and answer content is forbidden." in upgrade_sql


def test_existing_fact_changes_are_nullable_expand_only(upgrade_sql: str) -> None:
    for (schema, table), columns in ADDED_COLUMNS.items():
        for column in columns:
            statement = re.search(
                rf"ALTER TABLE {schema}\.{table} ADD COLUMN {column} ([^;]+);",
                upgrade_sql,
            )
            assert statement is not None, (schema, table, column)
            assert "NOT NULL" not in statement.group(1)

    for table, column in (
        ("collection_run", "collection_surface"),
        ("collection_task", "collection_surface"),
        ("collection_task", "requested_surface"),
        ("collection_task", "observed_surface"),
        ("answer", "collection_surface"),
        ("answer_analysis", "collection_surface"),
        ("evidence_asset", "collection_surface"),
        ("analysis_job", "collection_surface"),
        ("analysis_job", "requested_surface"),
        ("analysis_job", "observed_surface"),
    ):
        assert f"ck_{table}_{column}_s07" in upgrade_sql

    for _, table in ADDED_COLUMNS:
        assert f"ck_{table}_surface_basis_s07" in upgrade_sql
        assert f"ck_{table}_legacy_contract_s07" in upgrade_sql

    assert "validate_collection_task_identity_scope" in upgrade_sql
    assert "collection task campaign target crosses project scope" in upgrade_sql
    assert "fk_collection_run_campaign_config_scope" in upgrade_sql
    assert "fk_collection_task_primary_slot_tenant" in upgrade_sql


def test_upgrade_never_rewrites_historical_facts_or_legacy_channel(upgrade_sql: str) -> None:
    historical_tables = (
        "platform.collection_run",
        "platform.collection_task",
        "analytics.answer",
        "analytics.answer_analysis",
        "evidence.evidence_asset",
        "platform.analysis_job",
    )
    for table in historical_tables:
        assert not re.search(rf"(?im)^\s*UPDATE\s+{re.escape(table)}\b", upgrade_sql)
    assert not re.search(
        r"(?i)ALTER\s+TABLE\s+(?:analytics\.)?(?:answer|answer_analysis).*\bchannel\b",
        upgrade_sql,
    )
    assert not re.search(r"(?i)\bSET\s+channel\s*=", upgrade_sql)


def test_minimum_roles_are_explicit_and_identifiers_fit_postgres(upgrade_sql: str) -> None:
    for role in ("geo", "geo_api", "geo_worker"):
        assert f"rolname='{role}'" in upgrade_sql
    assert re.search(
        r"GRANT SELECT ON\s+"
        r"platform\.collection_campaign_materialization_batch,\s+"
        r"platform\.collection_surface_backfill_run\s+TO geo_api",
        upgrade_sql,
    )
    assert "GRANT SELECT,INSERT,UPDATE ON" in upgrade_sql
    assert "platform.collection_campaign_materialization_batch" in upgrade_sql
    assert not re.search(r"(?i)GRANT\s+[^;]*\bDELETE\b", upgrade_sql)

    identifiers = re.findall(
        r"(?:CONSTRAINT|INDEX|TRIGGER|FUNCTION)\s+(?:platform\.)?([a-z][a-z0-9_]*)",
        upgrade_sql,
        flags=re.IGNORECASE,
    )
    assert identifiers
    assert all(len(identifier) <= 63 for identifier in identifiers)


def test_downgrade_removes_every_expand_column_and_owned_object(downgrade_sql: str) -> None:
    assert downgrade_sql.index("DROP TRIGGER IF EXISTS collection_task_identity_scope_trg") < (
        downgrade_sql.index("DROP COLUMN collection_surface")
    )
    for (schema, table), columns in ADDED_COLUMNS.items():
        for column in columns:
            assert f"ALTER TABLE {schema}.{table} DROP COLUMN {column};" in downgrade_sql

    previous_position = -1
    for table in (
        "collection_surface_backfill_run",
        "collection_primary_slot",
        "collection_campaign_materialization_batch",
        "collection_sampling_leg",
        "collection_campaign_target",
        "collection_campaign",
        "collection_config_target_v2",
        "collection_config_revision_v2",
    ):
        position = downgrade_sql.index(f"DROP TABLE platform.{table};")
        assert position > previous_position
        previous_position = position

    for function in (
        "validate_collection_task_identity_scope",
        "guard_collection_membership_immutable",
        "enforce_collection_campaign_batch_completed",
        "advance_collection_campaign_materialization",
        "complete_collection_campaign_batch",
        "guard_collection_slot_materialization",
        "validate_collection_campaign_batch_insert",
        "guard_collection_campaign_immutable",
        "validate_collection_campaign_target_identity",
        "guard_collection_config_target_v2_mutation",
        "guard_collection_config_v2_immutable",
    ):
        assert f"DROP FUNCTION platform.{function}()" in downgrade_sql
    assert "DROP CONSTRAINT uq_project_id_tenant_s07" in downgrade_sql
