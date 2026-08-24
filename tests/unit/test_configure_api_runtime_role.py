from __future__ import annotations

import inspect

from tools import configure_api_runtime_role as runtime_roles
from tools.configure_api_runtime_role import SCHEMAS


def test_runtime_roles_are_granted_access_to_the_sop_schema() -> None:
    assert "sop" in SCHEMAS


def test_runtime_roles_are_granted_access_to_the_posting_schema() -> None:
    assert "posting" in SCHEMAS


def test_stage2_acl_is_reapplied_after_schema_wide_grants() -> None:
    install_source = inspect.getsource(runtime_roles.install_role)
    verify_source = inspect.getsource(runtime_roles.verify_role)

    assert "apply_stage2_minimum_acl(connection, role=role)" in install_source
    assert "verify_stage2_minimum_acl(connection, role=str(role[0]))" in verify_source


def test_stage2_acl_separates_insert_from_mutable_update_columns() -> None:
    assert "collection_submission_reconciliation_proof" in runtime_roles.STAGE2_TABLES
    assert "tenant_id" not in {
        column
        for columns in runtime_roles.STAGE2_WORKER_UPDATE_COLUMNS.values()
        for column in columns
    }
    assert runtime_roles.STAGE2_WORKER_UPDATE_COLUMNS["collection_quota_bucket"] == (
        "reserved_units",
        "settled_consumed_units",
        "settled_unknown_units",
        "bucket_state",
        "fence_version",
        "version",
        "updated_at",
    )
    assert (
        "collection_submission_reconciliation_proof"
        not in runtime_roles.STAGE2_WORKER_INSERT_TABLES
    )


def test_stage2_internal_functions_are_not_regranted_by_runtime_provisioning() -> None:
    assert runtime_roles.RECONCILIATION_FUNCTION in runtime_roles.STAGE2_FUNCTIONS
    assert "platform.validate_collection_quota_conservation_v2()" in (
        runtime_roles.STAGE2_FUNCTIONS
    )
    source = inspect.getsource(runtime_roles.apply_stage2_minimum_acl)
    assert "REVOKE ALL ON FUNCTION" in source
    assert "function == RECONCILIATION_FUNCTION" in source
