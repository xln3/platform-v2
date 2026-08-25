from __future__ import annotations

from geo_platform.tenancy.runtime_acl import (
    API_ROLE,
    FUNCTION_GRANTS,
    MANAGED_SCHEMAS,
    RUNTIME_ROLES,
    SEQUENCE_GRANTS,
    TABLE_GRANTS,
    WORKER_ROLE,
    migration_reconcile_sql,
)


def test_runtime_acl_is_a_closed_manifest_for_every_object_kind() -> None:
    assert RUNTIME_ROLES == (API_ROLE, WORKER_ROLE)
    assert set(TABLE_GRANTS) == set(RUNTIME_ROLES)
    assert set(SEQUENCE_GRANTS) == set(RUNTIME_ROLES)
    assert set(FUNCTION_GRANTS) == set(RUNTIME_ROLES)
    for role in RUNTIME_ROLES:
        assert TABLE_GRANTS[role]
        assert all(name.split(".", 1)[0] in MANAGED_SCHEMAS for name in TABLE_GRANTS[role])
        assert all(name.split(".", 1)[0] in MANAGED_SCHEMAS for name in SEQUENCE_GRANTS[role])


def test_sensitive_credentials_and_unlisted_future_objects_are_denied() -> None:
    for role in RUNTIME_ROLES:
        assert "platform.service_credential" not in TABLE_GRANTS[role]
        assert "platform.acl_future_table_probe" not in TABLE_GRANTS[role]
        assert "platform.acl_future_table_probe_id_seq" not in SEQUENCE_GRANTS[role]
        assert "platform.acl_future_function_probe()" not in FUNCTION_GRANTS[role]


def test_delete_is_limited_to_real_api_delete_paths() -> None:
    api_delete_tables = {
        table for table, grant in TABLE_GRANTS[API_ROLE].items() if "DELETE" in grant.privileges
    }
    assert api_delete_tables == {
        "notification.callback_replay",
        "platform.brand",
        "platform.brand_alias",
        "platform.brand_asset",
        "platform.change_request",
        "platform.client_goal",
        "platform.competitor",
        "platform.intake_promo",
        "platform.intake_trigger_question",
        "platform.query_group",
        "platform.query_item",
    }
    assert not any("DELETE" in grant.privileges for grant in TABLE_GRANTS[WORKER_ROLE].values())


def test_stage3_history_is_read_only_and_worker_uses_exact_entrypoints() -> None:
    for role in RUNTIME_ROLES:
        for table in (
            "platform.collection_submission_request_manifest_v2",
            "platform.collection_capture_truth_v2",
            "platform.collection_submission_dispatch_v2",
            "platform.collection_submission_transition_evidence_v2",
            "platform.collection_capture_manifest_v2",
            "platform.collection_observation_v2",
            "platform.collection_slot_outcome_v2",
            "platform.collection_analysis_admission_v2",
            "platform.collection_governance_effect_v2",
            "platform.collection_governance_outbox_v2",
        ):
            assert TABLE_GRANTS[role][table].privileges == frozenset({"SELECT"})
    assert not FUNCTION_GRANTS[API_ROLE]
    assert "platform.record_collection_not_sent_proof_v2(" in "\n".join(
        FUNCTION_GRANTS[WORKER_ROLE]
    )


def test_legacy_formal_report_outbox_and_alert_boundaries_remain_exact() -> None:
    assert TABLE_GRANTS[API_ROLE]["reporting.formal_report_production"].privileges == (
        frozenset({"SELECT", "INSERT", "UPDATE"})
    )
    assert TABLE_GRANTS[API_ROLE]["reporting.formal_report_output"].privileges == frozenset(
        {"SELECT"}
    )
    assert TABLE_GRANTS[WORKER_ROLE]["reporting.formal_report_production"].privileges == (
        frozenset({"SELECT", "UPDATE"})
    )
    assert TABLE_GRANTS[WORKER_ROLE]["reporting.formal_report_output"].privileges == (
        frozenset({"SELECT", "INSERT"})
    )
    assert TABLE_GRANTS[API_ROLE]["integration.workflow_start_command"].privileges == (
        frozenset({"SELECT", "INSERT"})
    )
    assert TABLE_GRANTS[API_ROLE]["integration.workflow_signal_command"].privileges == (
        frozenset({"SELECT", "INSERT"})
    )
    assert TABLE_GRANTS[WORKER_ROLE]["integration.workflow_start_command"].privileges == (
        frozenset({"SELECT", "INSERT", "UPDATE"})
    )
    assert TABLE_GRANTS[WORKER_ROLE]["integration.workflow_signal_command"].privileges == (
        frozenset({"SELECT", "UPDATE"})
    )
    assert "integration.business_alert_snapshot()" not in FUNCTION_GRANTS[API_ROLE]
    assert "integration.business_alert_snapshot()" in FUNCTION_GRANTS[WORKER_ROLE]


def test_execution_history_and_learning_ledgers_cannot_bypass_governed_writes() -> None:
    for role in RUNTIME_ROLES:
        for table in (
            "platform.collection_execution_plan_v2",
            "platform.collection_execution_partition_v2",
            "platform.collection_execution_start_outbox_v2",
        ):
            assert TABLE_GRANTS[role][table].privileges == frozenset({"SELECT"})
    for table in (
        "platform.collection_query_execution_attempt",
        "platform.collection_failure_knowledge",
    ):
        assert TABLE_GRANTS[API_ROLE][table].privileges == frozenset({"SELECT"})
        assert TABLE_GRANTS[WORKER_ROLE][table].privileges == frozenset({"SELECT", "INSERT"})
    assert TABLE_GRANTS[API_ROLE]["platform.service2_model_call"].privileges == frozenset(
        {"SELECT"}
    )
    assert TABLE_GRANTS[WORKER_ROLE]["platform.service2_model_call"].privileges == frozenset(
        {"SELECT", "INSERT", "UPDATE"}
    )
    for role in RUNTIME_ROLES:
        for table in (
            "platform.service2_analysis_attempt",
            "platform.service2_batch_event",
            "platform.service2_finding_review",
            "platform.service2_fact_manifest",
        ):
            assert TABLE_GRANTS[role][table].privileges == frozenset({"SELECT", "INSERT"})
    assert "platform.create_collection_execution_plan_v2(" in "\n".join(
        FUNCTION_GRANTS[WORKER_ROLE]
    )
    assert not FUNCTION_GRANTS[API_ROLE]


def test_sequence_grants_follow_the_only_roles_that_insert_formal_and_signal_rows() -> None:
    assert "reporting.formal_report_production_id_seq" in SEQUENCE_GRANTS[API_ROLE]
    assert "reporting.formal_report_output_id_seq" not in SEQUENCE_GRANTS[API_ROLE]
    assert "reporting.formal_report_output_id_seq" in SEQUENCE_GRANTS[WORKER_ROLE]
    assert "reporting.formal_report_production_id_seq" not in SEQUENCE_GRANTS[WORKER_ROLE]
    assert "integration.workflow_signal_command_id_seq" in SEQUENCE_GRANTS[API_ROLE]
    assert "integration.workflow_signal_command_id_seq" not in SEQUENCE_GRANTS[WORKER_ROLE]
    assert "integration.workflow_start_command_id_seq" in SEQUENCE_GRANTS[WORKER_ROLE]


def test_reconciliation_revokes_catalog_and_defaults_before_exact_grants() -> None:
    for role in RUNTIME_ROLES:
        rendered = migration_reconcile_sql(role)
        first_grant = rendered.index("GRANT USAGE ON SCHEMA")
        assert rendered.index("REVOKE ALL PRIVILEGES ON ALL TABLES") < first_grant
        assert rendered.index("REVOKE ALL PRIVILEGES ON ALL SEQUENCES") < first_grant
        assert rendered.index("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS") < first_grant
        assert "ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM PUBLIC" in rendered
        assert "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES" not in rendered
        assert 'FROM "platform"."service_credential"' not in rendered
