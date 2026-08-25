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
MIGRATION = ROOT / "migrations/versions/s10_0001_collection_submission_transactions.py"
TABLES = (
    "collection_submission_request_manifest_v2",
    "collection_capture_truth_v2",
    "collection_submission_dispatch_v2",
    "collection_submission_transition_evidence_v2",
    "collection_capture_manifest_v2",
    "collection_observation_v2",
    "collection_slot_outcome_v2",
    "collection_analysis_admission_v2",
    "collection_governance_effect_v2",
    "collection_governance_outbox_v2",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s10_submission_transactions", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(operation: str) -> str:
    module = _load()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        getattr(module, operation)()
    return output.getvalue()


@pytest.fixture(scope="module")
def source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _render("downgrade")


def _table(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE TABLE platform\.{re.escape(name)} \((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None, name
    return match.group(1)


def _function(sql: str, name: str) -> str:
    start = sql.index(f"CREATE FUNCTION platform.{name}(")
    next_start = sql.find("CREATE FUNCTION platform.", start + 1)
    return sql[start:] if next_start < 0 else sql[start:next_start]


def test_revision_descends_the_current_operational_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s10_0001_submission_transactions")
    assert revision is not None
    assert revision.down_revision == "s09_0001_ops_keysets"


def test_every_s10_table_is_project_scoped_and_force_rls(upgrade_sql: str) -> None:
    for name in TABLES:
        block = _table(upgrade_sql, name)
        for column in ("id", "pub_id", "tenant_id", "project_id", "version"):
            assert re.search(rf"\b{column}\b", block), (name, column)
        assert f"fk_{name}_project_scope" in block
        assert f'ALTER TABLE platform."{name}" ENABLE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'ALTER TABLE platform."{name}" FORCE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'CREATE POLICY tenant_isolation ON platform."{name}"' in upgrade_sql
    assert "current_setting('app.tenant_id', true)" in upgrade_sql


def test_request_dispatch_and_terminal_evidence_round_trip_exact_truth(
    upgrade_sql: str,
) -> None:
    request = _table(upgrade_sql, "collection_submission_request_manifest_v2")
    dispatch = _table(upgrade_sql, "collection_submission_dispatch_v2")
    transition = _table(upgrade_sql, "collection_submission_transition_evidence_v2")
    assert "collection-request-manifest-v1" in request
    for field in (
        "claim_pub_id",
        "owner_handle",
        "authority_sha256",
        "authority_snapshot_json",
        "grant_resource_set_hash",
        "claimed_at",
        "reconciliation_state",
        "reconciliation_claim_ref",
    ):
        assert field in dispatch
    for field in (
        "terminal_reason",
        "execution_grant_id",
        "evidence_ref",
        "non_submission_proof_ref",
        "provider_reference_ref",
        "terminated_fence_set_hash",
        "reconciliation_proof_id",
        "recorded_at",
    ):
        assert field in transition
    assert "post_claim_not_sent" in transition
    assert "non_submission_proof_ref IS NOT NULL" in transition
    assert "non_submission_proof_ref IS NULL" in transition


def test_claim_uses_domain_fence_identity_and_current_freshness(upgrade_sql: str) -> None:
    fence = _function(upgrade_sql, "collection_dispatch_fence_set_hash_s10")
    authority = _function(upgrade_sql, "assert_collection_authority_snapshot_s10")
    claim = _function(upgrade_sql, "claim_collection_submission_v2")
    assert '"version":"lease-fence-identity-v1"' in fence
    assert '"binding_resource_pub_id":"' in fence
    for field in ("generation", "lease_pub_id", "owner_handle", "resource_role"):
        assert f'"{field}"' in fence
    for mutable_field in ("expires_at", "heartbeat_at", "capacity_unit_id"):
        assert mutable_field not in fence
    assert "ORDER BY grant_resource.resource_role" in fence
    for parameter in (
        "p_claim_pub_id text",
        "p_claimed_at timestamptz",
        "p_expected_grant_hash text",
        "p_expected_fence_set_hash text",
        "p_expected_authority_hash text",
        "p_authority_snapshot_json text",
    ):
        assert parameter in claim
    assert "grant_row_source.expires_at > server_time" in claim
    assert "lease.expires_at <= server_time" in claim
    assert "p_claimed_at" in claim and "existing_dispatch.claimed_at" in claim
    assert "assert_collection_quota_reservation_v2" in claim
    assert "assert_collection_authority_snapshot_s10" in claim
    assert "canonical_authority_json<>p_authority_snapshot_json" in authority
    assert "jsonb_array_length(authority_payload->'lease_fences')" in authority
    assert "NOT BETWEEN 1 AND 32" in authority
    assert "registration.opaque_owner_handle" in authority
    assert "unique_snapshot_resource_count<>authoritative_resource_count" in authority
    assert "count(DISTINCT ROW(" in authority
    for field in (
        "acquired_at",
        "binding_resource_pub_id",
        "expires_at",
        "lease_pub_id",
        "owner_handle",
        "resource_role",
    ):
        assert f"(fence.value->>'{field}')" in authority


def test_reconciliation_is_an_exclusive_durable_cas(upgrade_sql: str) -> None:
    ready = _function(upgrade_sql, "mark_collection_dispatch_reconciliation_ready_v2")
    claim = _function(upgrade_sql, "claim_collection_dispatch_reconciliation_v2")
    terminal = _function(upgrade_sql, "finalize_collection_submission_v2")
    assert "owner_execution_state='owner_lost'" in ready
    assert "reconciliation_state='not_required'" in ready
    assert "p_owner_loss_evidence_ref" in ready
    assert "reconciliation_state='pending'" in claim
    assert "reconciliation_state='in_progress'" in claim
    assert "version=dispatch.version+1" in claim
    assert "dispatch.reconcile_after<=CURRENT_TIMESTAMP" in claim
    assert "reconciliation readiness requires terminated fenced authority" in ready
    assert "FOR UPDATE OF lease,capacity" in ready
    assert "post-claim not-sent requires exact owner or reconciliation proof" in terminal
    assert "('not_required','in_progress')" in terminal
    assert "p_reconciliation_claim_ref IS DISTINCT FROM" in terminal
    assert "accepted_proof_pub_id<>p_non_submission_proof_ref" in terminal
    assert "existing_transition.reason_code" in terminal
    assert "owner terminal replay cannot carry reconciliation claim" in terminal
    assert "terminal replay reconciliation claim drifted" in terminal
    assert "p_dispatch_id IS NULL AND" in terminal


def test_capture_truth_is_versioned_and_uses_exact_fact_times(upgrade_sql: str) -> None:
    truth = _table(upgrade_sql, "collection_capture_truth_v2")
    manifest = _table(upgrade_sql, "collection_capture_manifest_v2")
    begin = _function(upgrade_sql, "begin_collection_capture_v2")
    stage = _function(upgrade_sql, "stage_collection_capture_manifest_v2")
    link = _function(upgrade_sql, "link_collection_capture_v2")
    assert "capture_requested_at" in truth
    assert "active_request_sha256" in truth
    assert "active_command_json" in truth
    assert "capture_state_version" in truth
    assert "'completed','partial','failed','not_observable'" in truth
    assert "'complete'" not in truth
    assert "capture_channel" in manifest and "observed_platform" in manifest
    assert "capture_link_key" in manifest
    assert "capture-link-v1-[0-9a-f]{64}" in manifest
    assert "p_requested_at timestamptz" in begin
    assert "p_capture_command_json text" in begin
    assert "p_expected_authority_sha256 text" in begin
    assert "active_command_json=p_capture_command_json" in begin
    assert "truth_row.active_command_json=p_capture_command_json" in begin
    assert "capture_state_version=truth.capture_state_version+1" in begin
    assert "attempt_count=truth.attempt_count+1" in begin
    assert "truth.capture_state_version=p_expected_capture_state_version" in begin
    assert "jsonb_array_length(command_payload#>'{authority,lease_fences}')" in begin
    assert "NOT BETWEEN 1 AND 32" in begin
    assert "canonical_command_json<>p_capture_command_json" in begin
    assert "unique_command_resource_count<>authoritative_resource_count" in begin
    assert "count(DISTINCT ROW(" in begin
    for field in (
        "acquired_at",
        "binding_resource_pub_id",
        "expires_at",
        "lease_pub_id",
        "owner_handle",
        "resource_role",
    ):
        assert f"(fence.value->>'{field}')" in begin
    assert "('CONFIRMED_SENT','SEND_UNKNOWN')" in begin
    assert "invalid surface or product capture cannot be retried" in begin
    assert "storage_state='linked'" in begin and "is_current=false" in begin
    assert "p_staged_at timestamptz" in stage
    assert "p_staged_at < p_captured_at" in stage
    assert "invalid_surface_or_product" in stage
    assert "p_capture_link_key text" in link
    assert "capture_state_version" in link
    assert "INSERT INTO platform.collection_observation_v2" in link


def test_capture_command_reconstructs_exact_staging_intent(upgrade_sql: str) -> None:
    begin = _function(upgrade_sql, "begin_collection_capture_v2")

    assert "'source_send_state',\n               'staging_intent'" in begin
    assert "jsonb_typeof(command_payload->'staging_intent')<>'object'" in begin
    assert "'object_ref','staging_key'" in begin
    assert "^capture-object-v1-[0-9a-f]{64}$" in begin
    assert "^capture-staging-v1-[0-9a-f]{64}$" in begin
    assert (
        "canonical_staging_intent_basis :=\n"
        '            \'{"attempt_ref":"\' || p_capture_attempt_ref ||\n'
        '            \'","operation":\' || canonical_operation_json ||\n'
        '            \',"version":"collection-capture-staging-intent-v1"}\''
    ) in begin
    assert "public.digest(\n            canonical_staging_intent_basis,'sha256')" in begin
    assert (
        '\'{"object_ref":"capture-object-v1-\' ||\n'
        "              calculated_staging_intent_sha256 ||\n"
        '            \'","staging_key":"capture-staging-v1-\''
    ) in begin
    assert "capture command staging intent drifted" in begin
    assert (
        '\',"source_send_state":"\' || operation_row.send_state ||\n'
        "            '\",\"staging_intent\":' || canonical_staging_intent_json || '}'"
    ) in begin
    assert begin.index("canonical_operation_json :=") < begin.index(
        "canonical_staging_intent_basis :="
    )
    assert begin.index("capture command staging intent drifted") < begin.index(
        "canonical_command_json<>p_capture_command_json"
    )


def test_quarantined_capture_retention_has_one_guarded_orphan_gc_path(
    upgrade_sql: str,
) -> None:
    manifest = _table(upgrade_sql, "collection_capture_manifest_v2")
    guard = _function(upgrade_sql, "guard_collection_capture_manifest_s10")
    classify = _function(upgrade_sql, "classify_collection_capture_orphan_v2")
    eligible = _function(upgrade_sql, "collection_capture_orphan_gc_eligible_v2")

    assert "storage_state='orphaned' AND linked_at IS NULL" in manifest
    assert "quarantined_at IS NULL OR quarantined_at <= orphaned_at" in manifest
    assert "gc_after IS NOT NULL AND gc_after >= retention_until" in manifest

    assert "OLD.storage_state='quarantined'" in guard
    assert "NEW.storage_state='orphaned'" in guard
    assert "NEW.is_current IS NOT DISTINCT FROM OLD.is_current" in guard
    assert "OLD.retention_until<=CURRENT_TIMESTAMP" in guard
    assert "NOT OLD.legal_hold" in guard
    assert "NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at" in guard
    assert "observed capture cannot be quarantined or orphaned" in guard

    assert "NEW.storage_state IN ('linked','quarantined')" in guard
    assert "OLD.is_current=false AND NEW.is_current=false" in guard
    assert "truth.current_capture_manifest_id=OLD.id" in guard

    assert "manifest.storage_state='staging'" in classify
    assert "manifest.is_current=false" in classify
    assert "truth.current_capture_manifest_id=manifest.id" in classify
    assert "manifest.storage_state='quarantined'" in classify
    assert "manifest.retention_until<=CURRENT_TIMESTAMP" in classify
    assert "manifest.version=p_expected_version" in classify
    assert "p_gc_after>=manifest.retention_until" in classify
    assert "NOT manifest.legal_hold" in classify
    assert "NOT EXISTS (" in classify
    assert "collection_observation_v2" in classify
    assert "SET storage_state='orphaned',orphaned_at=CURRENT_TIMESTAMP" in classify
    assert "quarantined_at=" not in classify

    assert "manifest.storage_state='orphaned'" in eligible
    assert "NOT manifest.legal_hold" in eligible
    assert "p_checked_at>=manifest.retention_until" in eligible
    assert "p_checked_at>=manifest.gc_after" in eligible
    assert "collection_observation_v2" in eligible


def test_capture_mismatch_fact_is_stable_across_storage_lifecycle(
    upgrade_sql: str,
) -> None:
    fact = _function(upgrade_sql, "record_collection_slot_outcome_v2")
    link = _function(upgrade_sql, "link_collection_capture_v2")

    assert "capture_identity_mismatch boolean := false" in fact
    assert (
        "ROW(capture_row.observed_platform,\n"
        "                  capture_row.observed_surface,\n"
        "                  capture_row.observed_product_variant)"
    ) in fact
    assert (
        "ROW(operation_row.platform,\n"
        "                  operation_row.collection_surface,\n"
        "                  operation_row.product_variant)"
    ) in fact
    assert "capture_row.storage_state NOT IN ('quarantined','orphaned')" in fact
    assert "capture mismatch storage state is invalid" in fact
    assert "capture mismatch normalization is invalid" in fact
    assert "matched capture identity cannot claim mismatch" in fact
    assert (
        "ELSIF capture_identity_mismatch THEN\n"
        "            expected_outcome_state := 'invalid_surface_or_product'"
    ) in fact
    assert "capture_row.storage_state='quarantined'" not in fact
    assert fact.index("IF FOUND THEN") < fact.index("capture_identity_mismatch :=")

    assert (
        "manifest.storage_state IN\n"
        "                   ('staging','linked','quarantined','orphaned')"
    ) in link
    assert "capture_row.storage_state IN ('quarantined','orphaned')" in link
    assert "non-observable capture cannot be linked or admitted" in link
    assert "create_observation := capture_row.storage_state IN ('staging','linked')" in link


def test_terminal_link_and_fact_have_one_owner_each(upgrade_sql: str) -> None:
    terminal = _function(upgrade_sql, "finalize_collection_submission_v2")
    link = _function(upgrade_sql, "link_collection_capture_v2")
    fact = _function(upgrade_sql, "record_collection_slot_outcome_v2")
    assert "INSERT INTO platform.collection_submission_transition_evidence_v2" in terminal
    assert "INSERT INTO platform.collection_quota_ledger_event" in terminal
    assert "INSERT INTO platform.collection_governance_effect_v2" in terminal
    assert "INSERT INTO platform.collection_governance_outbox_v2" in terminal
    assert "INSERT INTO platform.collection_slot_outcome_v2" not in terminal
    assert "INSERT INTO platform.collection_observation_v2" not in terminal
    assert "p_capture_manifest_id" not in terminal
    assert "p_execution_grant_id uuid" in terminal
    assert "p_reconcile_after" not in terminal
    assert "UPDATE platform.collection_submission_operation AS operation" in terminal
    assert "operation.send_state_version=p_expected_send_state_version" in terminal
    assert "reconciliation_state='resolved'" in terminal
    assert "UPDATE platform.resource_lease lease" in terminal
    assert "UPDATE platform.collection_resource_capacity_unit capacity" in terminal
    assert "version=lease.version+1" in terminal
    assert "version=capacity.version+1" in terminal
    assert "operation_row.collection_surface='provider_api'" in terminal
    assert "IF operation_row.send_state='SENDING' THEN" in terminal
    assert "INSERT INTO platform.collection_observation_v2" in link
    assert "INSERT INTO platform.collection_slot_outcome_v2" not in link
    assert "INSERT INTO platform.collection_governance_outbox_v2" not in link
    assert "INSERT INTO platform.collection_slot_outcome_v2" in fact
    assert "INSERT INTO platform.collection_governance_effect_v2" in fact
    assert "INSERT INTO platform.collection_governance_outbox_v2" in fact


def test_unified_fact_entrypoint_validates_exact_domain_basis(upgrade_sql: str) -> None:
    outcome = _table(upgrade_sql, "collection_slot_outcome_v2")
    fact = _function(upgrade_sql, "record_collection_slot_outcome_v2")
    for field in (
        "operation_state_version",
        "capture_state_version",
        "analysis_state_version",
        "capture_link_key",
        "fact_version",
        "confirmed_sent_capture_pending",
    ):
        assert field in outcome
    assert "analysis_state_version IS NULL" in outcome
    for parameter in (
        "p_expected_operation_state_version integer",
        "p_expected_prior_fact_version integer",
        "p_capture_manifest_id uuid",
        "p_capture_state_version integer",
        "p_analysis_state_version integer",
        "p_capture_link_key text",
        "p_is_final_primary boolean",
        "p_outcome_payload_sha256 text",
        "p_recorded_at timestamptz",
    ):
        assert parameter in fact
    for state in (
        "confirmed_not_sent",
        "unavailable",
        "send_unknown",
        "confirmed_sent_capture_pending",
        "confirmed_sent_capture_complete",
        "confirmed_sent_capture_partial",
        "confirmed_sent_capture_failed",
        "invalid_surface_or_product",
        "not_observable",
    ):
        assert state in fact
    assert "p_analysis_state_version IS NOT NULL" in fact
    assert "current_fact_version<>p_expected_prior_fact_version" in fact
    assert "slot outcome does not derive from durable truth" in fact
    assert (
        "'confirmed_sent_capture_partial') THEN\n            IF capture_row.storage_state" in fact
    )
    assert "existing_outcome.reason_code" in fact
    assert "existing_effect.effect_hash" in fact
    assert "existing_outbox.payload_schema_revision" in fact
    assert "capture_observation_id := capture_row.observation_id" in fact
    assert "CASE WHEN p_capture_manifest_id IS NULL" not in fact


def test_domain_outbox_key_payload_version_and_time_are_exact(upgrade_sql: str) -> None:
    outbox = _table(upgrade_sql, "collection_governance_outbox_v2")
    helper = _function(upgrade_sql, "collection_outbox_key_s10")
    fact = _function(upgrade_sql, "record_collection_slot_outcome_v2")
    terminal = _function(upgrade_sql, "finalize_collection_submission_v2")
    for field in (
        "event_key",
        "aggregate_pub_id",
        "aggregate_version",
        "payload_hash",
        "occurred_at",
    ):
        assert field in outbox
    assert "outbox-v1-" in helper
    canonical = (
        '{"aggregate_ref":"',
        '","aggregate_version":',
        ',"event_type":"',
        '","payload_sha256":"',
        '","version":"collection-outbox-key-v1"}',
    )
    for fragment in canonical:
        assert fragment in helper
    assert "p_terminal_payload_sha256" in terminal
    assert "p_resolved_at" in terminal
    assert "collection.submission.terminal" in terminal
    assert "collection.slot.outcome" in fact
    assert "next_fact_version,p_outcome_payload_sha256" in fact


def test_reverse_commit_time_invariants_cover_terminal_quota_and_fact_outbox(
    upgrade_sql: str,
) -> None:
    invariant = _function(upgrade_sql, "assert_collection_submission_transaction_s10")
    assert "send truth and quota terminal truth diverged" in invariant
    assert "terminal s10 operation is not atomically complete" in invariant
    assert "slot fact and governance outbox diverged" in invariant
    assert "s10 operation preparation must complete in one transaction" in invariant
    assert "operation_row.state_reason='submission_v2_preparation_pending'" in invariant
    assert "assert_collection_quota_reservation_v2" in invariant
    for table in ("collection_submission_operation", "collection_quota_reservation", *TABLES):
        assert f"CREATE CONSTRAINT TRIGGER {table}_s10_atomic_trg" in upgrade_sql
    assert upgrade_sql.count("DEFERRABLE INITIALLY DEFERRED") >= len(TABLES) + 2


def test_prepare_clears_the_v2_pending_marker_only_after_manifest_truth_exist(
    upgrade_sql: str,
) -> None:
    prepare = _function(upgrade_sql, "prepare_collection_submission_request_v2")
    assert "INSERT INTO platform.collection_submission_request_manifest_v2" in prepare
    assert "SELECT id INTO STRICT new_truth_id" in prepare
    assert "SET state_reason='submission_prepared'" in prepare
    assert "operation.state_reason='submission_v2_preparation_pending'" in prepare


def test_acl_is_read_only_and_worker_executes_only_restricted_entries(
    upgrade_sql: str,
) -> None:
    assert "REVOKE ALL ON TABLE platform.%%I FROM PUBLIC" in upgrade_sql
    assert "GRANT SELECT ON TABLE platform.%%I TO geo_api" in upgrade_sql
    assert "GRANT SELECT ON TABLE platform.%%I TO geo_worker" in upgrade_sql
    assert (
        "REVOKE ALL ON TABLE\n"
        "              platform.collection_submission_operation FROM geo_worker" in upgrade_sql
    )
    assert "REVOKE UPDATE (\n              send_state,send_state_version" in upgrade_sql
    assert "ON platform.collection_submission_operation FROM geo_worker" in upgrade_sql
    assert (
        "GRANT SELECT ON TABLE\n"
        "              platform.collection_submission_operation TO geo_worker" in upgrade_sql
    )
    assert "GRANT INSERT ON TABLE platform.collection_submission_operation" not in upgrade_sql
    assert "REVOKE ALL ON FUNCTION" in upgrade_sql
    assert "record_collection_slot_outcome_v2" in upgrade_sql
    assert "GRANT EXECUTE ON FUNCTION" in upgrade_sql
    assert "GRANT INSERT ON TABLE platform.collection_slot_outcome_v2" not in upgrade_sql
    assert "GRANT UPDATE ON TABLE platform.collection_submission_operation" not in upgrade_sql
    acl = upgrade_sql[upgrade_sql.index("s10 function missing during ACL install") - 1000 :]
    assert "FOR function_identity IN\n            SELECT format" not in acl
    assert "to_regprocedure(function_identity) IS NULL" in acl
    assert "unexpected s10 function overload refused" in upgrade_sql
    assert "NOT procedure.oid=ANY" in upgrade_sql


def test_security_definers_fix_search_path_and_validate_worker_tenant(upgrade_sql: str) -> None:
    public_entries = (
        "create_collection_submission_operation_v2",
        "prepare_collection_submission_request_v2",
        "claim_collection_submission_v2",
        "mark_collection_dispatch_reconciliation_ready_v2",
        "claim_collection_dispatch_reconciliation_v2",
        "begin_collection_capture_v2",
        "stage_collection_capture_manifest_v2",
        "finalize_collection_submission_v2",
        "record_collection_slot_outcome_v2",
        "link_collection_capture_v2",
    )
    for name in public_entries:
        block = _function(upgrade_sql, name)
        assert "SECURITY DEFINER" in block
        assert "SET search_path = pg_catalog, platform" in block
        assert "geo_worker" in block
        assert "app.tenant_id" in block


def test_operation_creation_is_a_restricted_frozen_primary_slot_admission(
    upgrade_sql: str,
) -> None:
    admission = _function(upgrade_sql, "create_collection_submission_operation_v2")
    for invariant in (
        "caller_role <> 'geo_worker'",
        "tenant_context::uuid IS DISTINCT FROM p_tenant_id",
        "slot.slot_role='primary'",
        "campaign.state='frozen'",
        "campaign.materialization_state='complete'",
        "campaign.materialized_slot_count=campaign.expected_slot_count",
        "campaign.materialization_cursor=campaign.expected_slot_count",
        "campaign.membership_hash ~ '^[0-9a-f]{64}$'",
        "target.target_key=p_target_key",
        "leg.leg_key=p_leg_key",
        "canonical_operation_material :=",
        "'operation-v1-' || encode(",
        "p_operation_key IS DISTINCT FROM expected_operation_key",
        "submission operation exact replay drifted",
    ):
        assert invariant in admission
    assert "ON CONFLICT DO NOTHING" in admission
    assert "RETURN QUERY SELECT operation_id,true" in admission
    assert "RETURN QUERY SELECT existing_operation.id,false" in admission
    assert "'submission_v2_preparation_pending'" in admission
    replay_check = admission[admission.index("IF existing_operation.id IS NULL OR ROW(") :]
    assert "existing_operation.send_state" not in replay_check
    assert "existing_operation.send_state_version" not in replay_check
    assert "existing_operation.reconciliation_state" not in replay_check


def test_downgrade_refuses_data_drops_cycle_and_restores_exact_stage2_acl(
    downgrade_sql: str,
) -> None:
    assert "s10 submission downgrade refused" in downgrade_sql
    assert "fk_capture_truth_current_manifest_exact" in downgrade_sql
    for name in TABLES:
        assert f"DROP TABLE platform.{name};" in downgrade_sql
    assert "record_collection_slot_outcome_v2" in downgrade_sql
    assert "create_collection_submission_operation_v2" in downgrade_sql
    assert "collection_outbox_key_s10" in downgrade_sql
    assert (
        "GRANT INSERT ON TABLE\n"
        "              platform.collection_submission_operation TO geo_worker" in downgrade_sql
    )
    assert "procedure.proname IN" not in downgrade_sql
    assert "EXECUTE 'DROP FUNCTION IF EXISTS ' || function_identity" in downgrade_sql
    assert "platform.create_collection_submission_operation_v2(" in downgrade_sql
    assert "GRANT UPDATE (" in downgrade_sql
    assert "send_state,send_state_version" in downgrade_sql


def test_stage3_does_not_implement_partitions_workflows_or_analysis_execution(
    source: str,
) -> None:
    assert "execution_partition" not in source
    assert "workflow_input" not in source
    assert "analysis_attempt" not in source
    assert "analysis_truth" not in source
    assert "Temporal" in source  # explicitly documented as out of scope
