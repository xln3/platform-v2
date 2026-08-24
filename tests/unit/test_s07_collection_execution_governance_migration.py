from __future__ import annotations

import ast
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
MIGRATION_PATH = ROOT / "migrations/versions/s07_0002_collection_execution_governance.py"
NEW_TABLES = (
    "collection_capability_registry_revision",
    "collection_capability_declaration",
    "collection_quota_registry_revision",
    "collection_quota_scope_policy",
    "collection_binding_revision_v2",
    "collection_api_binding_v2",
    "collection_web_binding_v2",
    "collection_app_binding_v2",
    "collection_binding_capability",
    "collection_binding_resource",
    "collection_binding_quota_scope",
    "collection_submission_operation",
    "collection_submission_reconciliation_proof",
    "collection_resource_adoption",
    "collection_resource_capacity_unit",
    "collection_quota_bucket",
    "collection_quota_reservation",
    "collection_quota_reservation_effect",
    "collection_quota_ledger_event",
    "collection_execution_grant_v2",
    "collection_api_execution_grant_v2",
    "collection_web_execution_grant_v2",
    "collection_app_execution_grant_v2",
    "collection_execution_grant_resource",
)
SURFACES = ("provider_api", "consumer_web", "consumer_app")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s07_execution_governance", MIGRATION_PATH)
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


def test_revision_directly_follows_surface_identity() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    revision = scripts.get_revision("s07_0002_execution_governance")
    assert revision is not None
    assert revision.down_revision == "s07_0001_surface_identity"


def test_new_tables_are_project_scoped_and_force_rls(upgrade_sql: str) -> None:
    for table in NEW_TABLES:
        block = _table_block(upgrade_sql, table)
        for column in (
            "id",
            "pub_id",
            "tenant_id",
            "project_id",
            "version",
            "created_at",
            "updated_at",
        ):
            assert re.search(rf"\b{column}\b", block), (table, column)
        assert f"CONSTRAINT pk_{table} PRIMARY KEY (id)" in block
        assert f"CONSTRAINT uq_{table}_pub_id UNIQUE (pub_id)" in block
        assert f"CONSTRAINT uq_{table}_id_scope UNIQUE (id, tenant_id, project_id)" in block
        assert f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'CREATE POLICY tenant_isolation ON platform."{table}"' in upgrade_sql

    assert upgrade_sql.count("current_setting('app.tenant_id', true)") >= len(NEW_TABLES) * 2


def test_legacy_resources_only_gain_nullable_discriminated_v2_columns(
    upgrade_sql: str,
) -> None:
    for table, discriminator, schema_version, columns in (
        (
            "resource_registration",
            "resource_schema_version",
            "collection-resource-v2",
            (
                "project_id",
                "resource_revision",
                "owner_gateway_kind",
                "owner_gateway_revision",
                "opaque_owner_handle",
                "attestation_revision",
                "route_policy_revision",
                "resource_fingerprint",
                "approved_at",
                "revoked_at",
            ),
        ),
        (
            "resource_lease",
            "lease_schema_version",
            "collection-resource-lease-v2",
            (
                "project_id",
                "resource_registration_id",
                "capacity_unit_id",
                "operation_id",
                "binding_revision_id",
                "lease_key",
                "lease_attempt",
                "lease_state",
                "acquired_at",
                "heartbeat_at",
                "revoked_at",
                "owner_gateway_revision",
                "reconciliation_reason",
            ),
        ),
    ):
        assert f"ALTER TABLE platform.{table} ADD COLUMN {discriminator}" in upgrade_sql
        for column in columns:
            assert f"ALTER TABLE platform.{table} ADD COLUMN {column}" in upgrade_sql
        assert f"{discriminator} IS NULL" in upgrade_sql
        assert f"{discriminator} = '{schema_version}'" in upgrade_sql

    assert not re.search(
        r"UPDATE\s+platform\.(?:resource_registration|resource_lease)\s+SET",
        upgrade_sql,
        flags=re.IGNORECASE,
    )
    assert "guard_resource_registration_v2" in upgrade_sql
    assert "guard_resource_lease_v2" in upgrade_sql


def test_capability_registry_is_versioned_and_binding_mapping_is_relational(
    upgrade_sql: str,
) -> None:
    registry = _table_block(upgrade_sql, "collection_capability_registry_revision")
    declaration = _table_block(upgrade_sql, "collection_capability_declaration")
    mapping = _table_block(upgrade_sql, "collection_binding_capability")
    grant = _table_block(upgrade_sql, "collection_execution_grant_v2")

    assert "collection-capability-registry-v1" in registry
    assert "registry_revision" in registry
    assert "revision_hash" in registry
    assert "parent_revision_id" in registry
    assert "collection-capability-v1" in declaration
    for column in (
        "capability_revision",
        "platform",
        "collection_surface",
        "product_variant",
        "interaction_mode",
        "status",
        "production_allowed",
    ):
        assert re.search(rf"\b{column}\b", declaration)
    assert "fk_binding_capability_declaration_exact" in mapping
    assert "fk_binding_capability_binding_identity" in mapping
    assert "capability_declaration_id" in mapping
    assert "interaction_mode" in mapping
    assert "binding_capability_id" in grant
    assert "fk_execution_grant_capability_exact" in grant
    assert "guard_capability_registry_v2" in upgrade_sql
    assert "guard_capability_declaration_v2" in upgrade_sql
    assert "capability registry must begin mutable" in upgrade_sql
    assert "capability registry activation timestamp is immutable" in upgrade_sql
    assert (
        "BEFORE INSERT OR UPDATE OR DELETE\n        ON "
        "platform.collection_capability_registry_revision"
    ) in upgrade_sql


def test_binding_has_three_strict_subtypes_and_frozen_mappings(upgrade_sql: str) -> None:
    binding = _table_block(upgrade_sql, "collection_binding_revision_v2")
    assert "collection-binding-v1" in binding
    assert "fk_binding_v2_cap_registry_exact" in binding
    assert "fk_binding_v2_quota_registry_exact" in binding
    assert "binding_hash" in binding
    assert "credential_references_json" in binding
    for table, surface in (
        ("collection_api_binding_v2", "provider_api"),
        ("collection_web_binding_v2", "consumer_web"),
        ("collection_app_binding_v2", "consumer_app"),
    ):
        block = _table_block(upgrade_sql, table)
        assert f"collection_surface = '{surface}'" in block
        assert "binding_revision_id" in block
        assert f"fk_{table}_binding_surface" in block

    assert "collection_binding_resource" in upgrade_sql
    assert "collection_binding_quota_scope" in upgrade_sql
    assert "guard_binding_child_v2" in upgrade_sql
    assert "binding activation requires subtype and formal mappings" in upgrade_sql
    assert "binding revision must begin mutable" in upgrade_sql
    assert "m.adoption_required AND NOT EXISTS" in upgrade_sql
    assert "a.verification_state='verified'" in upgrade_sql
    assert "bucket_key" not in _table_block(upgrade_sql, "collection_binding_quota_scope")
    assert "window_start" not in _table_block(upgrade_sql, "collection_binding_quota_scope")


def test_operation_is_final_durable_no_resend_anchor(upgrade_sql: str) -> None:
    operation = _table_block(upgrade_sql, "collection_submission_operation")
    for column in (
        "campaign_id",
        "campaign_target_id",
        "sampling_leg_id",
        "primary_slot_id",
        "slot_key",
        "operation_generation",
        "operation_key",
        "send_state",
        "send_state_version",
        "reconciliation_state",
        "reconcile_after",
    ):
        assert re.search(rf"\b{column}\b", operation)
    for state in (
        "NOT_SENT",
        "SENDING",
        "CONFIRMED_SENT",
        "SEND_UNKNOWN",
        "CONFIRMED_NOT_SENT",
    ):
        assert f"'{state}'" in operation
    assert "fk_submission_operation_slot_identity" in operation
    assert "uq_submission_operation_generation" in operation
    assert "CREATE UNIQUE INDEX uq_submission_operation_no_resend" in upgrade_sql
    assert "new submission generation requires prior CONFIRMED_NOT_SENT" in upgrade_sql
    assert "invalid irreversible send-state transition" in upgrade_sql
    assert "submission operations are durable and cannot be deleted" in upgrade_sql


def test_quota_runtime_is_atomic_audited_and_send_truth_bound(upgrade_sql: str) -> None:
    policy = _table_block(upgrade_sql, "collection_quota_scope_policy")
    bucket = _table_block(upgrade_sql, "collection_quota_bucket")
    reservation = _table_block(upgrade_sql, "collection_quota_reservation")
    effect = _table_block(upgrade_sql, "collection_quota_reservation_effect")
    ledger = _table_block(upgrade_sql, "collection_quota_ledger_event")

    for scope in (
        "provider",
        "account",
        "credential",
        "project",
        "contract",
        "platform_surface",
        "mode",
    ):
        assert f"'{scope}'" in policy
    assert "quota-scope-lock-order-v1" in upgrade_sql
    assert "window_start" in bucket and "window_end" in bucket
    assert "reserved_units" in bucket
    assert "settled_consumed_units" in bucket
    assert "settled_unknown_units" in bucket
    assert "reserved_units + settled_consumed_units" in bucket
    assert "uq_quota_reservation_operation" in reservation
    assert "expected_effect_count" in reservation
    assert "effect_set_hash" in reservation
    assert "uq_quota_effect_operation_bucket" in effect
    assert "fk_quota_ledger_effect_exact" in ledger
    for effect_kind in ("reserve", "settle_consumed", "settle_unknown", "release"):
        assert f"'{effect_kind}'" in ledger
    assert "quota settlement contradicts durable send truth" in upgrade_sql
    assert "operation_state IN ('SENDING','CONFIRMED_SENT','SEND_UNKNOWN')" in upgrade_sql
    assert "quota ledger events are append-only" in upgrade_sql
    assert "BEFORE UPDATE OR DELETE ON platform.collection_quota_ledger_event" in upgrade_sql
    assert "quota registry must begin mutable" in upgrade_sql
    assert "quota registry activation timestamp is immutable" in upgrade_sql
    assert "quota reservation must begin preparing" in upgrade_sql
    assert (
        "BEFORE INSERT OR UPDATE OR DELETE\n        ON platform.collection_quota_registry_revision"
    ) in upgrade_sql
    assert (
        "BEFORE INSERT OR UPDATE OR DELETE\n        ON platform.collection_quota_reservation"
    ) in upgrade_sql


def test_legacy_adoption_is_explicit_and_never_auto_live(upgrade_sql: str) -> None:
    adoption = _table_block(upgrade_sql, "collection_resource_adoption")
    capacity = _table_block(upgrade_sql, "collection_resource_capacity_unit")
    for source in (
        "s01_platform_account",
        "s01_browser_profile",
        "s06_platform_account",
        "s06_browser",
        "s06_region",
    ):
        assert f"'{source}'" in adoption
    assert "device_binding" not in adoption
    assert "verification_state" in adoption
    assert "verification_hash" in adoption
    assert "candidate" in capacity
    assert "current_fencing_token" in capacity
    assert "resource fencing token cannot decrease" in upgrade_sql
    assert "legacy adoption cannot cross tenant scope" in upgrade_sql
    assert "resource adoption must begin proposed" in upgrade_sql
    assert "resource capacity must begin candidate at fence zero" in upgrade_sql
    assert not re.search(
        r"INSERT\s+INTO\s+platform\.collection_resource_(?:adoption|capacity_unit)\s+SELECT",
        upgrade_sql,
        flags=re.IGNORECASE,
    )


def test_grants_are_typed_short_lived_and_pin_all_prerequisites(upgrade_sql: str) -> None:
    grant = _table_block(upgrade_sql, "collection_execution_grant_v2")
    for column in (
        "operation_id",
        "binding_revision_id",
        "binding_capability_id",
        "quota_reservation_id",
        "workflow_contract_version",
        "adapter_revision",
        "gateway_protocol_revision",
        "worker_build_id",
        "allowed_actions_json",
        "issued_at",
        "expires_at",
    ):
        assert re.search(rf"\b{column}\b", grant)
    for table, surface in (
        ("collection_api_execution_grant_v2", "provider_api"),
        ("collection_web_execution_grant_v2", "consumer_web"),
        ("collection_app_execution_grant_v2", "consumer_app"),
    ):
        block = _table_block(upgrade_sql, table)
        assert f"collection_surface = '{surface}'" in block
        assert "execution_grant_id" in block
    resource = _table_block(upgrade_sql, "collection_execution_grant_resource")
    assert "fk_execution_grant_resource_lease_exact" in resource
    assert "fence_generation" in resource
    assert "owner_gateway_handle" in resource
    assert "execution grant prerequisites are not active" in upgrade_sql
    assert "l.fencing_token <> c.current_fencing_token" in upgrade_sql
    assert "issued execution grant content is immutable" in upgrade_sql
    assert "execution grant must begin assembling" in upgrade_sql
    assert "c.capacity_state <> 'leased'" in upgrade_sql
    assert "br.required=true" in upgrade_sql


def test_formal_resource_and_lease_history_cannot_be_reused(upgrade_sql: str) -> None:
    assert "formal resource registration cannot be deleted" in upgrade_sql
    assert "formal resource lease cannot be deleted" in upgrade_sql
    assert "BEFORE UPDATE OR DELETE ON platform.resource_registration" in upgrade_sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON platform.resource_lease" in upgrade_sql
    assert "current_capacity_state IS DISTINCT FROM 'leased'" in upgrade_sql
    assert "NEW.heartbeat_at < OLD.heartbeat_at" in upgrade_sql
    assert "acquired_at <= heartbeat_at AND heartbeat_at < expires_at" in upgrade_sql


def test_nullable_v2_extensions_fail_closed_on_partial_shapes(upgrade_sql: str) -> None:
    for required in (
        "project_id IS NOT NULL",
        "resource_revision IS NOT NULL",
        "owner_gateway_kind IS NOT NULL",
        "owner_gateway_revision IS NOT NULL",
        "opaque_owner_handle IS NOT NULL",
        "attestation_revision IS NOT NULL",
        "route_policy_revision IS NOT NULL",
        "resource_fingerprint IS NOT NULL",
        "approved_at IS NOT NULL",
        "resource_registration_id IS NOT NULL",
        "capacity_unit_id IS NOT NULL",
        "operation_id IS NOT NULL",
        "binding_revision_id IS NOT NULL",
        "lease_key IS NOT NULL",
        "lease_attempt IS NOT NULL",
        "lease_state IS NOT NULL",
        "acquired_at IS NOT NULL",
        "heartbeat_at IS NOT NULL",
    ):
        assert required in upgrade_sql


def test_binding_policy_kinds_do_not_collapse_business_resource_roles(
    upgrade_sql: str,
) -> None:
    assert "resource.resource_kind=policy_kind.kind" in upgrade_sql
    assert "resource.resource_role=policy_kind.kind" not in upgrade_sql
    assert "resource.resource_role<>resource.resource_kind" not in upgrade_sql
    assert "resource.resource_role='browser_owner'" not in upgrade_sql


def test_binding_capability_and_grant_resource_pin_exact_frozen_parents(
    upgrade_sql: str,
) -> None:
    grant_resource = _table_block(upgrade_sql, "collection_execution_grant_resource")
    binding_resource = _table_block(upgrade_sql, "collection_binding_resource")

    assert "uq_binding_resource_grant_mapping" in binding_resource
    assert "binding_resource_mapping_revision" in grant_resource
    assert "fk_execution_grant_resource_binding_mapping" in grant_resource
    assert "br.ordinal=m.resource_ordinal" in upgrade_sql
    assert "br.mapping_revision=" in upgrade_sql
    assert "binding.capability_registry_id=registry.id" in upgrade_sql
    assert "declaration.status='supported'" in upgrade_sql
    assert "declaration.production_allowed=true" in upgrade_sql


def test_binding_and_lease_time_are_bounded_before_grant_issuance(
    upgrade_sql: str,
) -> None:
    assert "binding_activated_at > NEW.acquired_at" in upgrade_sql
    assert "binding_expires_at < NEW.expires_at" in upgrade_sql
    assert "active resource lease exceeds its active binding window" in upgrade_sql
    assert "b.activated_at <= NEW.issued_at" in upgrade_sql
    assert "b.expires_at >= NEW.expires_at" in upgrade_sql
    assert "l.acquired_at > NEW.issued_at" in upgrade_sql
    assert "l.expires_at <= NEW.issued_at" in upgrade_sql
    assert "l.expires_at < NEW.expires_at" in upgrade_sql


def test_quota_conservation_and_digest_are_database_enforced(upgrade_sql: str) -> None:
    assert "DEFERRABLE INITIALLY DEFERRED" in upgrade_sql
    assert "preparing quota reservation cannot survive commit" in upgrade_sql
    assert "quota bucket projection violates ledger conservation" in upgrade_sql
    assert "quota ledger is incomplete or forged" in upgrade_sql
    assert "ORDER BY p.lock_order_ordinal,p.scope_policy_key" in upgrade_sql
    assert "ck_quota_scope_canonical_lock_order" in upgrade_sql
    assert "SECURITY DEFINER" in upgrade_sql
    assert "SET search_path = pg_catalog, platform" in upgrade_sql
    assert "public.digest" in upgrade_sql


def test_not_sent_proof_is_restricted_durable_and_lease_bound(upgrade_sql: str) -> None:
    proof = _table_block(upgrade_sql, "collection_submission_reconciliation_proof")

    assert "proof_kind" in proof and "terminated_lease_set_hash" in proof
    assert "submission reconciliation proofs are append-only" in upgrade_sql
    assert "reconciliation proof caller is not trusted worker" in upgrade_sql
    assert "not-sent proof requires every formal lease terminated" in upgrade_sql
    assert "SENDING operation requires accepted not-sent proof" in upgrade_sql
    assert "REVOKE ALL ON FUNCTION" in upgrade_sql
    assert "record_collection_not_sent_proof_v2" in upgrade_sql


def test_opaque_handles_cannot_encode_network_endpoints(upgrade_sql: str) -> None:
    assert "[A-Za-z0-9._-]{0,127}" in upgrade_sql
    assert "[A-Za-z0-9._:-]{0,127}" not in upgrade_sql


def test_migration_does_not_create_or_drop_cluster_global_roles(
    upgrade_sql: str,
    downgrade_sql: str,
) -> None:
    assert not re.search(r"\bCREATE ROLE\b", upgrade_sql)
    assert not re.search(r"\bDROP ROLE\b", downgrade_sql)
    assert "GRANT USAGE ON SCHEMA platform TO geo_api" in upgrade_sql
    assert "GRANT USAGE ON SCHEMA platform TO geo_worker" in upgrade_sql


def test_schema_contains_no_usable_secret_or_raw_endpoint_columns(upgrade_sql: str) -> None:
    create_sql = "\n".join(_table_block(upgrade_sql, table) for table in NEW_TABLES)
    forbidden_columns = (
        "secret_value",
        "raw_secret",
        "api_key",
        "access_token",
        "refresh_token",
        "cookie",
        "password",
        "cdp_url",
        "webdriver_url",
        "adb_url",
        "adb_endpoint",
        "device_serial",
        "hardware_id",
        "imei",
    )
    for column in forbidden_columns:
        assert not re.search(rf"\n\s*{column}\s", create_sql, flags=re.IGNORECASE), column


def test_grants_are_minimum_and_never_include_delete(upgrade_sql: str) -> None:
    assert "geo_api" in upgrade_sql
    assert "geo_worker" in upgrade_sql
    assert "REVOKE ALL ON TABLE platform.%%I FROM PUBLIC" in upgrade_sql
    assert "GRANT INSERT ON TABLE platform.collection_quota_ledger_event" in upgrade_sql
    assert "record_collection_not_sent_proof_v2" in upgrade_sql
    assert "FROM PUBLIC" in upgrade_sql
    assert "TO geo_worker" in upgrade_sql
    assert not re.search(
        r"^\s*GRANT\b[^;]*\bDELETE\b",
        upgrade_sql,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    assert "GRANT SELECT,INSERT ON TABLE platform.%%I TO geo_api" in upgrade_sql


def test_downgrade_drops_every_owned_table_trigger_function_and_extension(
    downgrade_sql: str,
) -> None:
    positions = []
    for table in NEW_TABLES:
        statement = f"DROP TABLE platform.{table};"
        assert statement in downgrade_sql
        positions.append(downgrade_sql.index(statement))
    assert downgrade_sql.index("DROP TABLE platform.collection_execution_grant_v2;") < (
        downgrade_sql.index("DROP TABLE platform.collection_submission_operation;")
    )
    assert downgrade_sql.index("DROP TABLE platform.collection_quota_reservation;") < (
        downgrade_sql.index("DROP TABLE platform.collection_binding_revision_v2;")
    )
    for table, columns in (
        (
            "resource_lease",
            ("project_id", "lease_schema_version", "operation_id", "binding_revision_id"),
        ),
        (
            "resource_registration",
            ("project_id", "resource_schema_version", "resource_revision"),
        ),
    ):
        for column in columns:
            assert f"ALTER TABLE platform.{table} DROP COLUMN {column};" in downgrade_sql
    assert "DROP CONSTRAINT uq_primary_slot_operation_identity_s07" in downgrade_sql
    assert "DROP FUNCTION platform.guard_submission_operation_v2()" in downgrade_sql
    assert "DROP FUNCTION platform.guard_quota_ledger_append_only_v2()" in downgrade_sql


def test_explicit_database_identifiers_fit_postgresql_limit() -> None:
    tree = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
    prefixes = ("pk_", "fk_", "uq_", "ck_", "ix_")
    identifiers = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(prefixes)
    }
    assert identifiers
    assert {name for name in identifiers if len(name) > 63} == set()
