"""Provision a non-superuser, RLS-bound production API database role.

The generated password is written only to the restricted production environment
file. It is never printed or included in evidence.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
API_ROLE = "geo_api"
WORKER_ROLE = "geo_worker"
SCHEMAS = (
    "platform",
    "analytics",
    "evidence",
    "reporting",
    "intelligence",
    "integration",
    "sop",
    "posting",
)
STAGE2_TABLES = (
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
STAGE2_API_INSERT_TABLES = (
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
    "collection_resource_adoption",
)
STAGE2_API_UPDATE_COLUMNS = {
    "collection_capability_registry_revision": (
        "lifecycle_state",
        "change_reason",
        "approved_by_pub_id",
        "frozen_at",
        "activated_at",
        "retired_at",
        "version",
        "updated_at",
    ),
    "collection_capability_declaration": (
        "status",
        "production_allowed",
        "region_policy_revision",
        "required_resource_kinds_json",
        "observable_capture_fields_json",
        "product_version_constraints_json",
        "unsupported_reason",
        "alternative_suggestion",
        "version",
        "updated_at",
    ),
    "collection_quota_registry_revision": (
        "lifecycle_state",
        "change_reason",
        "approved_by_pub_id",
        "frozen_at",
        "activated_at",
        "retired_at",
        "version",
        "updated_at",
    ),
    "collection_quota_scope_policy": (
        "share_policy",
        "window_unit",
        "window_size",
        "window_timezone",
        "window_boundary_revision",
        "provider_window_code",
        "limit_units",
        "limit_source",
        "settlement_policy_revision",
        "lock_order_ordinal",
        "version",
        "updated_at",
    ),
    "collection_binding_revision_v2": (
        "lifecycle_state",
        "lifecycle_reason",
        "activated_at",
        "suspended_at",
        "revoked_at",
        "superseded_at",
        "version",
        "updated_at",
    ),
    "collection_resource_adoption": (
        "verification_state",
        "verified_by_pub_id",
        "verified_at",
        "adopted_at",
        "revoked_at",
        "state_reason",
        "version",
        "updated_at",
    ),
}
STAGE2_WORKER_INSERT_TABLES = (
    "collection_submission_operation",
    "collection_resource_capacity_unit",
    "collection_quota_bucket",
    "collection_quota_reservation",
    "collection_quota_reservation_effect",
    "collection_execution_grant_v2",
    "collection_api_execution_grant_v2",
    "collection_web_execution_grant_v2",
    "collection_app_execution_grant_v2",
    "collection_execution_grant_resource",
)
STAGE2_WORKER_UPDATE_COLUMNS = {
    "collection_submission_operation": (
        "send_state",
        "send_state_version",
        "send_started_at",
        "send_resolved_at",
        "reconciliation_state",
        "reconcile_after",
        "state_reason",
        "version",
        "updated_at",
    ),
    "collection_resource_capacity_unit": (
        "capacity_state",
        "current_fencing_token",
        "last_heartbeat_at",
        "quarantined_at",
        "revoked_at",
        "state_reason",
        "version",
        "updated_at",
    ),
    "collection_quota_bucket": (
        "reserved_units",
        "settled_consumed_units",
        "settled_unknown_units",
        "bucket_state",
        "fence_version",
        "version",
        "updated_at",
    ),
    "collection_quota_reservation": (
        "reservation_state",
        "reserved_at",
        "finalized_at",
        "reconcile_after",
        "state_reason",
        "version",
        "updated_at",
    ),
    "collection_quota_reservation_effect": (
        "effect_state",
        "state_reason",
        "settled_at",
        "released_at",
        "version",
        "updated_at",
    ),
    "collection_execution_grant_v2": (
        "grant_state",
        "issued_at",
        "revoked_at",
        "revocation_reason",
        "version",
        "updated_at",
    ),
}
RECONCILIATION_FUNCTION = (
    "platform.record_collection_not_sent_proof_v2(uuid,uuid,uuid,text,text,text,text)"
)
STAGE2_FUNCTIONS = (
    "platform.guard_resource_registration_v2()",
    "platform.guard_capability_registry_v2()",
    "platform.guard_capability_declaration_v2()",
    "platform.guard_quota_registry_v2()",
    "platform.guard_quota_scope_policy_v2()",
    "platform.guard_binding_revision_v2()",
    "platform.guard_binding_child_v2()",
    "platform.guard_submission_operation_v2()",
    "platform.guard_submission_reconciliation_proof_v2()",
    RECONCILIATION_FUNCTION,
    "platform.guard_resource_adoption_v2()",
    "platform.guard_resource_capacity_v2()",
    "platform.guard_resource_lease_v2()",
    "platform.guard_quota_bucket_v2()",
    "platform.guard_quota_reservation_v2()",
    "platform.guard_quota_effect_v2()",
    "platform.guard_quota_ledger_append_only_v2()",
    "platform.assert_collection_quota_bucket_v2(uuid,uuid,uuid)",
    "platform.assert_collection_quota_reservation_v2(uuid,uuid,uuid)",
    "platform.validate_collection_quota_conservation_v2()",
    "platform.guard_execution_grant_v2()",
    "platform.guard_execution_grant_child_v2()",
)
STAGE3_TABLES = (
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
STAGE3_WORKER_FUNCTIONS = (
    "platform.create_collection_submission_operation_v2("
    "uuid,uuid,text,integer,text,text,timestamptz,text,text,text,text,text,text,text,text)",
    "platform.prepare_collection_submission_request_v2("
    "uuid,uuid,uuid,integer,text,text,text,text,text,text,text,timestamptz)",
    "platform.claim_collection_submission_v2("
    "uuid,uuid,uuid,text,integer,uuid,integer,text,text,text,text,text,text,text,text,text,"
    "timestamptz)",
    "platform.mark_collection_dispatch_reconciliation_ready_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,timestamptz)",
    "platform.claim_collection_dispatch_reconciliation_v2(uuid,uuid,uuid,uuid,integer,text,text)",
    "platform.begin_collection_capture_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,timestamptz)",
    "platform.stage_collection_capture_manifest_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,text,text,text,bigint,"
    "text,text,text,text,text,text,text,text,text,text,text,text,text,timestamptz,timestamptz,"
    "timestamptz)",
    "platform.finalize_collection_submission_v2("
    "uuid,uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,text,text,text,text,"
    "timestamptz,text,text,text,integer)",
    "platform.record_collection_slot_outcome_v2("
    "uuid,uuid,uuid,integer,integer,uuid,integer,integer,text,text,text,boolean,text,text,"
    "timestamptz)",
    "platform.link_collection_capture_v2(uuid,uuid,uuid,uuid,uuid,integer,text,text,timestamptz)",
    "platform.classify_collection_capture_orphan_v2(uuid,uuid,uuid,uuid,integer,timestamptz,text)",
    "platform.collection_capture_orphan_gc_eligible_v2(uuid,uuid,uuid,timestamptz)",
    "platform.advance_collection_governance_outbox_v2(uuid,uuid,uuid,integer,text,text)",
)
STAGE3_INTERNAL_FUNCTIONS = (
    "platform.collection_outbox_key_s10(text,text,integer,text)",
    "platform.reject_collection_submission_history_mutation_s10()",
    "platform.guard_collection_submission_dispatch_s10()",
    "platform.guard_submission_request_manifest_s10()",
    "platform.create_capture_truth_for_request_s10()",
    "platform.guard_collection_capture_truth_s10()",
    "platform.guard_collection_capture_manifest_s10()",
    "platform.resolve_capture_truth_from_manifest_s10()",
    "platform.guard_collection_observation_s10()",
    "platform.guard_collection_slot_outcome_s10()",
    "platform.guard_collection_analysis_admission_s10()",
    "platform.guard_collection_governance_outbox_s10()",
    "platform.collection_dispatch_fence_set_hash_s10(uuid,uuid,uuid)",
    "platform.assert_collection_authority_snapshot_s10("
    "uuid,uuid,uuid,uuid,uuid,integer,text,text,text,text,text,timestamptz)",
    "platform.assert_collection_dispatch_fresh_s10(uuid,uuid,uuid,uuid,text)",
    "platform.assert_collection_submission_transaction_s10(uuid,uuid,uuid)",
    "platform.validate_collection_submission_transaction_s10()",
)
STAGE3_FUNCTIONS = STAGE3_WORKER_FUNCTIONS + STAGE3_INTERNAL_FUNCTIONS
_STAGE3_TENANT_POLICY_EXPRESSION = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)


def _relation_exists(connection: psycopg.Connection[tuple[object, ...]], table: str) -> bool:
    row = connection.execute("SELECT to_regclass(%s)", (f"platform.{table}",)).fetchone()
    return row is not None and row[0] is not None


def _stage2_resource_extensions_exist(
    connection: psycopg.Connection[tuple[object, ...]],
) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='platform' AND table_name='resource_registration'
          AND column_name='resource_schema_version'
        """
    ).fetchone()
    return row is not None


def _stage3_catalog_installed(
    connection: psycopg.Connection[tuple[object, ...]],
) -> bool:
    """Return false only for a wholly pre-s10 catalog; reject partial installs."""

    function_names = sorted(
        {function.split("(", 1)[0].rsplit(".", 1)[1] for function in STAGE3_FUNCTIONS}
    )
    unexpected = connection.execute(
        """
        SELECT count(*)
          FROM pg_proc procedure
          JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
         WHERE namespace.nspname='platform'
           AND procedure.proname=ANY(%s)
           AND NOT procedure.oid=ANY(
             ARRAY(
               SELECT to_regprocedure(signature)
                 FROM unnest(%s::text[]) signature
                WHERE to_regprocedure(signature) IS NOT NULL
             )
           )
        """,
        (function_names, list(STAGE3_FUNCTIONS)),
    ).fetchone()
    if unexpected != (0,):
        raise RuntimeError("unexpected Stage 3 function overload")

    table_presence = {table: _relation_exists(connection, table) for table in STAGE3_TABLES}
    function_presence = {}
    for function in STAGE3_FUNCTIONS:
        row = connection.execute("SELECT to_regprocedure(%s)", (function,)).fetchone()
        function_presence[function] = row is not None and row[0] is not None
    present = sum(table_presence.values()) + sum(function_presence.values())
    expected = len(table_presence) + len(function_presence)
    if present == 0:
        return False
    if present != expected:
        missing_tables = tuple(table for table, exists in table_presence.items() if not exists)
        missing_functions = tuple(
            function for function, exists in function_presence.items() if not exists
        )
        raise RuntimeError(
            "partial Stage 3 catalog:"
            f"missing_tables={missing_tables!r}:missing_functions={missing_functions!r}"
        )
    return True


def _verify_stage3_rls(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    for table in STAGE3_TABLES:
        row = connection.execute(
            """
            SELECT relation.relrowsecurity,
                   relation.relforcerowsecurity,
                   count(policy.oid),
                   count(policy.oid) FILTER (
                     WHERE policy.polname='tenant_isolation'
                       AND policy.polcmd='*'
                       AND policy.polpermissive
                       AND policy.polroles=ARRAY[0::oid]
                       AND pg_get_expr(policy.polqual,policy.polrelid)=%s
                       AND pg_get_expr(policy.polwithcheck,policy.polrelid)=%s
                   )
              FROM pg_class relation
              JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
              LEFT JOIN pg_policy policy ON policy.polrelid=relation.oid
             WHERE namespace.nspname='platform' AND relation.relname=%s
             GROUP BY relation.relrowsecurity,relation.relforcerowsecurity
            """,
            (_STAGE3_TENANT_POLICY_EXPRESSION, _STAGE3_TENANT_POLICY_EXPRESSION, table),
        ).fetchone()
        if row != (True, True, 1, 1):
            raise RuntimeError(f"stage3 RLS policy mismatch:{table}")


def apply_stage2_minimum_acl(
    connection: psycopg.Connection[tuple[object, ...]], *, role: str
) -> None:
    """Undo schema-wide grants for Stage 2 and restore the exact runtime matrix."""

    if role not in (API_ROLE, WORKER_ROLE):
        raise ValueError(f"unsupported Stage 2 runtime role:{role}")
    existing = tuple(table for table in STAGE2_TABLES if _relation_exists(connection, table))
    for table in existing:
        relation = sql.SQL("platform.{}").format(sql.Identifier(table))
        connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC").format(relation))
        connection.execute(
            sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                relation,
                sql.Identifier(role),
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                relation,
                sql.Identifier(role),
            )
        )

    insert_tables = STAGE2_API_INSERT_TABLES if role == API_ROLE else STAGE2_WORKER_INSERT_TABLES
    update_columns = STAGE2_API_UPDATE_COLUMNS if role == API_ROLE else STAGE2_WORKER_UPDATE_COLUMNS
    for table in insert_tables:
        if table not in existing:
            continue
        connection.execute(
            sql.SQL("GRANT INSERT ON TABLE platform.{} TO {}").format(
                sql.Identifier(table),
                sql.Identifier(role),
            )
        )
    for table, columns in update_columns.items():
        if table not in existing:
            continue
        connection.execute(
            sql.SQL("GRANT UPDATE ({}) ON TABLE platform.{} TO {}").format(
                sql.SQL(",").join(sql.Identifier(column) for column in columns),
                sql.Identifier(table),
                sql.Identifier(role),
            )
        )
    if role == WORKER_ROLE and "collection_quota_ledger_event" in existing:
        connection.execute(
            sql.SQL("GRANT INSERT ON TABLE platform.collection_quota_ledger_event TO {}").format(
                sql.Identifier(role)
            )
        )

    for function in STAGE2_FUNCTIONS:
        function_row = connection.execute("SELECT to_regprocedure(%s)", (function,)).fetchone()
        if function_row is None or function_row[0] is None:
            continue
        signature = sql.SQL(function)
        connection.execute(sql.SQL("REVOKE ALL ON FUNCTION {} FROM PUBLIC").format(signature))
        connection.execute(
            sql.SQL("REVOKE ALL ON FUNCTION {} FROM {}").format(
                signature,
                sql.Identifier(role),
            )
        )
        if role == WORKER_ROLE and function == RECONCILIATION_FUNCTION:
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    signature,
                    sql.Identifier(role),
                )
            )

    if not _stage2_resource_extensions_exist(connection):
        return
    for table in ("resource_registration", "resource_lease"):
        connection.execute(
            sql.SQL("REVOKE ALL ON TABLE platform.{} FROM {}").format(
                sql.Identifier(table),
                sql.Identifier(role),
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON TABLE platform.{} TO {}").format(
                sql.Identifier(table),
                sql.Identifier(role),
            )
        )
    if role == API_ROLE:
        connection.execute(
            sql.SQL("GRANT INSERT ON TABLE platform.resource_registration TO {}").format(
                sql.Identifier(role)
            )
        )
        connection.execute(
            sql.SQL(
                "GRANT UPDATE (display_mask,capabilities_json,region,concurrency_limit,"
                "state,last_heartbeat_at,project_id,resource_schema_version,"
                "resource_revision,owner_gateway_kind,owner_gateway_revision,"
                "opaque_owner_handle,attestation_revision,route_policy_revision,"
                "resource_fingerprint,approved_at,revoked_at,version,updated_at) "
                "ON platform.resource_registration TO {}"
            ).format(sql.Identifier(role))
        )
    else:
        connection.execute(
            sql.SQL(
                "GRANT UPDATE (state,last_heartbeat_at,revoked_at,version,updated_at) "
                "ON platform.resource_registration TO {}"
            ).format(sql.Identifier(role))
        )
        connection.execute(
            sql.SQL("GRANT INSERT ON TABLE platform.resource_lease TO {}").format(
                sql.Identifier(role)
            )
        )
        connection.execute(
            sql.SQL(
                "GRANT UPDATE (lease_state,heartbeat_at,expires_at,released_at,"
                "revoked_at,reconciliation_reason,version,updated_at) "
                "ON platform.resource_lease TO {}"
            ).format(sql.Identifier(role))
        )


def verify_stage2_minimum_acl(
    connection: psycopg.Connection[tuple[object, ...]], *, role: str
) -> None:
    for table in STAGE2_TABLES:
        if not _relation_exists(connection, table):
            continue
        delete_allowed = connection.execute(
            "SELECT has_table_privilege(%s,%s,'DELETE')",
            (role, f"platform.{table}"),
        ).fetchone()
        if delete_allowed is None or delete_allowed[0] is not False:
            raise RuntimeError(f"stage2 role has DELETE privilege:{role}:{table}")
        identity_update = connection.execute(
            "SELECT has_column_privilege(%s,%s,'tenant_id','UPDATE')",
            (role, f"platform.{table}"),
        ).fetchone()
        if identity_update is None or identity_update[0] is not False:
            raise RuntimeError(f"stage2 role can update identity:{role}:{table}")
    for table, column, expected in (
        ("collection_quota_bucket", "reserved_units", role == WORKER_ROLE),
        ("collection_quota_bucket", "bucket_key", False),
        ("collection_submission_reconciliation_proof", "proof_state", False),
    ):
        if not _relation_exists(connection, table):
            continue
        allowed = connection.execute(
            "SELECT has_column_privilege(%s,%s,%s,'UPDATE')",
            (role, f"platform.{table}", column),
        ).fetchone()
        if allowed is None or allowed[0] is not expected:
            raise RuntimeError(f"stage2 column ACL mismatch:{role}:{table}:{column}")
    proof_function = connection.execute(
        "SELECT to_regprocedure(%s)", (RECONCILIATION_FUNCTION,)
    ).fetchone()
    if proof_function is not None and proof_function[0] is not None:
        allowed = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            (role, RECONCILIATION_FUNCTION),
        ).fetchone()
        expected = role == WORKER_ROLE
        if allowed is None or allowed[0] is not expected:
            raise RuntimeError(f"stage2 reconciliation EXECUTE mismatch:{role}")


def apply_stage3_minimum_acl(
    connection: psycopg.Connection[tuple[object, ...]], *, role: str
) -> None:
    """Restore the s10 read-only tables and narrow worker entrypoint matrix."""

    if role not in (API_ROLE, WORKER_ROLE):
        raise ValueError(f"unsupported Stage 3 runtime role:{role}")
    if not _stage3_catalog_installed(connection):
        return

    for table in STAGE3_TABLES:
        if not _relation_exists(connection, table):
            continue
        relation = sql.SQL("platform.{}").format(sql.Identifier(table))
        connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC").format(relation))
        connection.execute(
            sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                relation,
                sql.Identifier(role),
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                relation,
                sql.Identifier(role),
            )
        )

    # Stage 2 grants worker direct INSERT and mutable-column UPDATE on the s07
    # operation table.  Stage 3 replaces both capabilities with exact
    # SECURITY DEFINER entrypoints, so the table itself is read-only here.
    if role == WORKER_ROLE and _relation_exists(connection, "collection_submission_operation"):
        operation = sql.SQL("platform.collection_submission_operation")
        connection.execute(
            sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                operation,
                sql.Identifier(role),
            )
        )
        connection.execute(
            sql.SQL("REVOKE UPDATE ({}) ON TABLE {} FROM {}").format(
                sql.SQL(",").join(
                    sql.Identifier(column)
                    for column in STAGE2_WORKER_UPDATE_COLUMNS["collection_submission_operation"]
                ),
                operation,
                sql.Identifier(role),
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                operation,
                sql.Identifier(role),
            )
        )

    worker_functions = frozenset(STAGE3_WORKER_FUNCTIONS)
    for function in STAGE3_FUNCTIONS:
        function_row = connection.execute("SELECT to_regprocedure(%s)", (function,)).fetchone()
        if function_row is None or function_row[0] is None:
            continue
        signature = sql.SQL(function)
        connection.execute(sql.SQL("REVOKE ALL ON FUNCTION {} FROM PUBLIC").format(signature))
        connection.execute(
            sql.SQL("REVOKE ALL ON FUNCTION {} FROM {}").format(
                signature,
                sql.Identifier(role),
            )
        )
        if role == WORKER_ROLE and function in worker_functions:
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    signature,
                    sql.Identifier(role),
                )
            )


def verify_stage3_minimum_acl(
    connection: psycopg.Connection[tuple[object, ...]], *, role: str
) -> None:
    """Fail unless s10 is read-only and only worker entrypoints are executable."""

    if role not in (API_ROLE, WORKER_ROLE):
        raise ValueError(f"unsupported Stage 3 runtime role:{role}")
    if not _stage3_catalog_installed(connection):
        return
    _verify_stage3_rls(connection)

    for table in STAGE3_TABLES:
        if not _relation_exists(connection, table):
            continue
        qualified_table = f"platform.{table}"
        privileges = connection.execute(
            """
            SELECT has_table_privilege(%s,%s,'SELECT'),
                   has_table_privilege(%s,%s,'INSERT'),
                   has_table_privilege(%s,%s,'UPDATE'),
                   has_table_privilege(%s,%s,'DELETE'),
                   has_any_column_privilege(%s,%s,'INSERT'),
                   has_any_column_privilege(%s,%s,'UPDATE')
            """,
            (
                role,
                qualified_table,
                role,
                qualified_table,
                role,
                qualified_table,
                role,
                qualified_table,
                role,
                qualified_table,
                role,
                qualified_table,
            ),
        ).fetchone()
        if privileges != (True, False, False, False, False, False):
            raise RuntimeError(f"stage3 table ACL mismatch:{role}:{table}")

    if _relation_exists(connection, "collection_submission_operation"):
        operation_privileges = connection.execute(
            """
            SELECT has_table_privilege(%s,%s,'SELECT'),
                   has_table_privilege(%s,%s,'INSERT'),
                   has_table_privilege(%s,%s,'UPDATE'),
                   has_table_privilege(%s,%s,'DELETE'),
                   has_any_column_privilege(%s,%s,'UPDATE')
            """,
            (
                role,
                "platform.collection_submission_operation",
                role,
                "platform.collection_submission_operation",
                role,
                "platform.collection_submission_operation",
                role,
                "platform.collection_submission_operation",
                role,
                "platform.collection_submission_operation",
            ),
        ).fetchone()
        if operation_privileges != (True, False, False, False, False):
            raise RuntimeError(f"stage3 operation ACL mismatch:{role}")

    worker_functions = frozenset(STAGE3_WORKER_FUNCTIONS)
    for function in STAGE3_FUNCTIONS:
        function_row = connection.execute("SELECT to_regprocedure(%s)", (function,)).fetchone()
        if function_row is None or function_row[0] is None:
            continue
        public_execute = connection.execute(
            """
            SELECT EXISTS (
              SELECT 1
                FROM pg_proc procedure
                CROSS JOIN LATERAL aclexplode(
                  COALESCE(procedure.proacl,acldefault('f',procedure.proowner))
                ) privilege
               WHERE procedure.oid=to_regprocedure(%s)
                 AND privilege.grantee=0
                 AND privilege.privilege_type='EXECUTE'
            )
            """,
            (function,),
        ).fetchone()
        if public_execute is None or public_execute[0] is not False:
            raise RuntimeError(f"stage3 function is executable by PUBLIC:{function}")
        role_execute = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            (role, function),
        ).fetchone()
        expected = role == WORKER_ROLE and function in worker_functions
        if role_execute is None or role_execute[0] is not expected:
            raise RuntimeError(f"stage3 function EXECUTE mismatch:{role}:{function}")


def read_environment(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return lines, values


def runtime_dsn(owner_dsn: str, password: str, role: str) -> str:
    parsed = urlsplit(owner_dsn.replace("postgresql+psycopg://", "postgresql://"))
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (
            "postgresql+psycopg",
            f"{role}:{quote(password, safe='')}@{host}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def install_role(owner_dsn: str, password: str, *, role: str, bypass_rls: bool) -> None:
    parsed = urlsplit(owner_dsn.replace("postgresql+psycopg://", "postgresql://"))
    owner = unquote(parsed.username or "")
    with psycopg.connect(owner_dsn.replace("postgresql+psycopg://", "postgresql://")) as connection:
        database = connection.info.dbname
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        rls_clause = sql.SQL("BYPASSRLS" if bypass_rls else "NOBYPASSRLS")
        if exists is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT {} PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    rls_clause,
                    sql.Literal(password),
                )
            )
        else:
            connection.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT {} PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    rls_clause,
                    sql.Literal(password),
                )
            )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
        for schema in SCHEMAS:
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(schema), sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON TABLES FROM {}"
                ).format(
                    sql.Identifier(owner),
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                    "REVOKE ALL ON SEQUENCES FROM {}"
                ).format(
                    sql.Identifier(owner),
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                    "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                ).format(
                    sql.Identifier(owner),
                    sql.Identifier(schema),
                )
            )
        apply_stage2_minimum_acl(connection, role=role)
        apply_stage3_minimum_acl(connection, role=role)


def verify_role(dsn: str, *, bypass_rls: bool) -> None:
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://")) as connection:
        role = connection.execute(
            """
            SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls
            FROM pg_roles WHERE rolname=current_user
            """
        ).fetchone()
        if role is None or role[1:] != (False, False, False, bypass_rls):
            raise RuntimeError("runtime database role is privileged")
        verify_stage2_minimum_acl(connection, role=str(role[0]))
        verify_stage3_minimum_acl(connection, role=str(role[0]))
        connection.execute("SELECT count(*) FROM sop.project").fetchone()
        tenant = connection.execute(
            "SELECT id,pub_id FROM platform.tenant ORDER BY id LIMIT 1"
        ).fetchone()
        if tenant is not None:
            connection.execute(
                """
                SELECT set_config('app.tenant_id', %s, true),
                       set_config('app.tenant_pub_id', %s, true)
                """,
                (str(tenant[0]), tenant[1]),
            )
            connection.execute("SELECT count(*) FROM platform.membership").fetchone()
            connection.execute("SELECT count(*) FROM analytics.metric_daily").fetchone()


def write_environment(path: Path, lines: list[str], replacements: dict[str, str]) -> None:
    updated: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in replacements:
            output.append(f"{key}={replacements[key]}")
            updated.add(key)
            continue
        output.append(line)
    for key, value in replacements.items():
        if key not in updated:
            output.append(f"{key}={value}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
    os.replace(temporary, path)


def main() -> None:
    lines, values = read_environment(ENV_PATH)
    owner_dsn = values.get("GEO_POSTGRES_DSN")
    if not owner_dsn:
        raise RuntimeError("GEO_POSTGRES_DSN is missing")
    api_password = secrets.token_urlsafe(36)
    worker_password = secrets.token_urlsafe(36)
    install_role(owner_dsn, api_password, role=API_ROLE, bypass_rls=False)
    install_role(owner_dsn, worker_password, role=WORKER_ROLE, bypass_rls=False)
    api_dsn = runtime_dsn(owner_dsn, api_password, API_ROLE)
    worker_dsn = runtime_dsn(owner_dsn, worker_password, WORKER_ROLE)
    verify_role(api_dsn, bypass_rls=False)
    verify_role(worker_dsn, bypass_rls=False)
    write_environment(
        ENV_PATH,
        lines,
        {
            "GEO_RUNTIME_POSTGRES_DSN": api_dsn,
            "GEO_WORKER_POSTGRES_DSN": worker_dsn,
            "S02_POSTGRES_DSN": worker_dsn.replace("postgresql+psycopg://", "postgresql://"),
            "GEO_IDENTITY_MODE": "native_session",
        },
    )
    print(
        json.dumps(
            {
                "result": "configured",
                "api_role": {
                    "name": API_ROLE,
                    "superuser": False,
                    "bypass_rls": False,
                },
                "worker_role": {
                    "name": WORKER_ROLE,
                    "superuser": False,
                    "bypass_rls": False,
                },
                "secret_emitted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
