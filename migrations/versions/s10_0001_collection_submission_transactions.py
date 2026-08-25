"""Add fail-closed submission transactions, capture truth, and governance outbox.

This revision is additive.  It does not implement execution partitions,
Temporal payloads, schedulers, or adapters.  Existing submission operations
without an s10 request manifest remain legacy rows; once a manifest is
attached, deferred commit-time invariants require the complete s10 protocol.

Revision ID: s10_0001_submission_transactions
Revises: s09_0001_ops_keysets
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "s10_0001_submission_transactions"
down_revision: str | Sequence[str] | None = "s09_0001_ops_keysets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256 = "{column} ~ '^[0-9a-f]{{64}}$'"
_OPAQUE_REF = "{column} ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$'"
_SURFACE = "{column} IN ('provider_api','consumer_web','consumer_app')"
_SEND_STATES = (
    "NOT_SENT",
    "SENDING",
    "CONFIRMED_SENT",
    "SEND_UNKNOWN",
    "CONFIRMED_NOT_SENT",
)
_NEW_TABLES = (
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
_WORKER_FUNCTION_SIGNATURES = (
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
_INTERNAL_FUNCTION_SIGNATURES = (
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
_FUNCTION_SIGNATURES = _WORKER_FUNCTION_SIGNATURES + _INTERNAL_FUNCTION_SIGNATURES


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


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{table}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def _create_parent_candidate_keys() -> None:
    op.create_unique_constraint(
        "uq_submission_operation_slot_scope_s10",
        "collection_submission_operation",
        ["id", "tenant_id", "project_id", "primary_slot_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_execution_grant_dispatch_scope_s10",
        "collection_execution_grant_v2",
        [
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "binding_revision_id",
            "quota_registry_id",
            "quota_reservation_id",
            "grant_revision",
            "grant_hash",
        ],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_execution_grant_operation_scope_s10",
        "collection_execution_grant_v2",
        ["id", "tenant_id", "project_id", "operation_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_submission_reconciliation_proof_scope_s10",
        "collection_submission_reconciliation_proof",
        ["id", "tenant_id", "project_id", "operation_id"],
        schema="platform",
    )


def _create_request_manifest() -> None:
    op.create_table(
        "collection_submission_request_manifest_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("request_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("request_protocol_revision", sa.String(length=128), nullable=False),
        sa.Column("adapter_request_revision", sa.String(length=128), nullable=False),
        sa.Column("request_content_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("prepared_by_pub_id", sa.String(length=128), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_submission_request_manifest_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_submission_request_manifest_operation_scope",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            name="uq_submission_request_manifest_operation",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_submission_request_manifest_dispatch_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-request-manifest-v1'",
            name=op.f("ck_submission_request_manifest_schema"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="request_payload_hash")
            + " AND "
            + _SHA256.format(column="request_manifest_hash")
            + " AND "
            + _SHA256.format(column="provider_idempotency_key_hash"),
            name=op.f("ck_submission_request_manifest_hashes"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="request_protocol_revision")
            + " AND "
            + _OPAQUE_REF.format(column="adapter_request_revision")
            + " AND "
            + _OPAQUE_REF.format(column="request_content_ref")
            + " AND "
            + _OPAQUE_REF.format(column="prepared_by_pub_id"),
            name=op.f("ck_submission_request_manifest_opaque_refs"),
        ),
        schema="platform",
    )


def _create_dispatch() -> None:
    op.create_table(
        "collection_submission_dispatch_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("request_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("grant_revision", sa.Integer(), nullable=False),
        sa.Column("grant_authority_hash", sa.String(length=64), nullable=False),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("quota_registry_id", sa.Uuid(), nullable=False),
        sa.Column("quota_reservation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("claim_pub_id", sa.String(length=128), nullable=False),
        sa.Column("owner_handle", sa.String(length=255), nullable=False),
        sa.Column("authority_sha256", sa.String(length=64), nullable=False),
        sa.Column("authority_snapshot_json", sa.Text(), nullable=False),
        sa.Column("dispatch_key", sa.String(length=128), nullable=False),
        sa.Column("owner_gateway_revision", sa.String(length=128), nullable=False),
        sa.Column("owner_dispatch_ref", sa.String(length=255), nullable=False),
        sa.Column("owner_wal_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("grant_resource_set_hash", sa.String(length=64), nullable=False),
        sa.Column("dispatch_hash", sa.String(length=64), nullable=False),
        sa.Column("prior_send_state_version", sa.Integer(), nullable=False),
        sa.Column("sending_send_state_version", sa.Integer(), nullable=False),
        sa.Column("owner_execution_state", sa.String(length=30), nullable=False),
        sa.Column("reconciliation_state", sa.String(length=30), nullable=False),
        sa.Column("reconciliation_version", sa.Integer(), nullable=False),
        sa.Column("readiness_evidence_ref", sa.String(length=255)),
        sa.Column("readiness_evidence_hash", sa.String(length=64)),
        sa.Column("reconciliation_claim_ref", sa.String(length=255)),
        sa.Column("reconciliation_claim_hash", sa.String(length=64)),
        sa.Column("reconcile_after", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_ready_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_resolved_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_submission_dispatch_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_submission_dispatch_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["request_manifest_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_request_manifest_v2.id",
                "platform.collection_submission_request_manifest_v2.tenant_id",
                "platform.collection_submission_request_manifest_v2.project_id",
                "platform.collection_submission_request_manifest_v2.operation_id",
            ],
            name="fk_submission_dispatch_request_manifest_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "execution_grant_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "binding_revision_id",
                "quota_registry_id",
                "quota_reservation_id",
                "grant_revision",
                "grant_authority_hash",
            ],
            [
                "platform.collection_execution_grant_v2.id",
                "platform.collection_execution_grant_v2.tenant_id",
                "platform.collection_execution_grant_v2.project_id",
                "platform.collection_execution_grant_v2.operation_id",
                "platform.collection_execution_grant_v2.binding_revision_id",
                "platform.collection_execution_grant_v2.quota_registry_id",
                "platform.collection_execution_grant_v2.quota_reservation_id",
                "platform.collection_execution_grant_v2.grant_revision",
                "platform.collection_execution_grant_v2.grant_hash",
            ],
            name="fk_submission_dispatch_execution_grant_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "quota_reservation_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "binding_revision_id",
                "quota_registry_id",
            ],
            [
                "platform.collection_quota_reservation.id",
                "platform.collection_quota_reservation.tenant_id",
                "platform.collection_quota_reservation.project_id",
                "platform.collection_quota_reservation.operation_id",
                "platform.collection_quota_reservation.binding_revision_id",
                "platform.collection_quota_reservation.quota_registry_id",
            ],
            name="fk_submission_dispatch_quota_reservation_exact",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            name="uq_submission_dispatch_operation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "dispatch_key",
            name="uq_submission_dispatch_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_submission_dispatch_evidence_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-submission-dispatch-v1'",
            name=op.f("ck_submission_dispatch_schema"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="dispatch_key")
            + " AND "
            + _OPAQUE_REF.format(column="claim_pub_id")
            + " AND "
            + _OPAQUE_REF.format(column="owner_handle")
            + " AND "
            + _OPAQUE_REF.format(column="owner_gateway_revision")
            + " AND "
            + _OPAQUE_REF.format(column="owner_dispatch_ref"),
            name=op.f("ck_submission_dispatch_opaque_refs"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="owner_wal_evidence_hash")
            + " AND "
            + _SHA256.format(column="authority_sha256")
            + " AND "
            + _SHA256.format(column="grant_authority_hash")
            + " AND "
            + _SHA256.format(column="grant_resource_set_hash")
            + " AND "
            + _SHA256.format(column="dispatch_hash"),
            name=op.f("ck_submission_dispatch_hashes"),
        ),
        sa.CheckConstraint(
            "octet_length(authority_snapshot_json) BETWEEN 2 AND 16384 AND "
            "jsonb_typeof(authority_snapshot_json::jsonb)='object' AND "
            "authority_snapshot_json !~* "
            '\'"(secret|password|cookie|authorization|proxy_url|endpoint)"'
            "[[:space:]]*:'",
            name=op.f("ck_submission_dispatch_authority_dlp"),
        ),
        sa.CheckConstraint(
            "prior_send_state_version > 0 "
            "AND sending_send_state_version = prior_send_state_version + 1 "
            "AND grant_revision > 0 AND reconciliation_version > 0",
            name=op.f("ck_submission_dispatch_versions"),
        ),
        sa.CheckConstraint(
            "owner_execution_state IN ('active','owner_lost','resolved') AND "
            "reconciliation_state IN "
            "('not_required','pending','in_progress','resolved')",
            name=op.f("ck_submission_dispatch_reconciliation_states"),
        ),
        sa.CheckConstraint(
            "(reconciliation_state='not_required' AND "
            "owner_execution_state='active' AND readiness_evidence_ref IS NULL "
            "AND readiness_evidence_hash IS NULL "
            "AND reconciliation_claim_ref IS NULL "
            "AND reconciliation_claim_hash IS NULL AND reconcile_after IS NULL "
            "AND reconciliation_ready_at IS NULL "
            "AND reconciliation_claimed_at IS NULL "
            "AND reconciliation_resolved_at IS NULL) OR "
            "(reconciliation_state='pending' AND owner_execution_state='owner_lost' "
            "AND readiness_evidence_ref IS NOT NULL "
            "AND readiness_evidence_hash IS NOT NULL "
            "AND reconciliation_claim_ref IS NULL "
            "AND reconciliation_claim_hash IS NULL AND reconcile_after IS NOT NULL "
            "AND reconciliation_ready_at IS NOT NULL "
            "AND reconciliation_claimed_at IS NULL "
            "AND reconciliation_resolved_at IS NULL) OR "
            "(reconciliation_state='in_progress' "
            "AND owner_execution_state='owner_lost' "
            "AND readiness_evidence_ref IS NOT NULL "
            "AND readiness_evidence_hash IS NOT NULL "
            "AND reconciliation_claim_ref IS NOT NULL "
            "AND reconciliation_claim_hash IS NOT NULL "
            "AND reconcile_after IS NOT NULL "
            "AND reconciliation_ready_at IS NOT NULL "
            "AND reconciliation_claimed_at IS NOT NULL "
            "AND reconciliation_resolved_at IS NULL) OR "
            "(reconciliation_state='resolved' "
            "AND owner_execution_state='resolved' "
            "AND reconciliation_resolved_at IS NOT NULL)",
            name=op.f("ck_submission_dispatch_reconciliation_shape"),
        ),
        sa.CheckConstraint(
            "(readiness_evidence_ref IS NULL OR "
            + _OPAQUE_REF.format(column="readiness_evidence_ref")
            + ") AND (reconciliation_claim_ref IS NULL OR "
            + _OPAQUE_REF.format(column="reconciliation_claim_ref")
            + ") AND (readiness_evidence_hash IS NULL OR "
            + _SHA256.format(column="readiness_evidence_hash")
            + ") AND (reconciliation_claim_hash IS NULL OR "
            + _SHA256.format(column="reconciliation_claim_hash")
            + ")",
            name=op.f("ck_submission_dispatch_reconciliation_evidence"),
        ),
        schema="platform",
    )


def _create_capture_truth() -> None:
    op.create_table(
        "collection_capture_truth_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("request_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("capture_state", sa.String(length=30), nullable=False),
        sa.Column("capture_state_version", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("current_attempt_ref", sa.String(length=255)),
        sa.Column("active_dispatch_id", sa.Uuid()),
        sa.Column("active_owner_handle", sa.String(length=255)),
        sa.Column("active_fence_set_hash", sa.String(length=64)),
        sa.Column("active_request_sha256", sa.String(length=64)),
        sa.Column("active_command_json", sa.Text()),
        sa.Column("current_capture_manifest_id", sa.Uuid()),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        sa.Column("capture_requested_at", sa.DateTime(timezone=True)),
        sa.Column("capture_started_at", sa.DateTime(timezone=True)),
        sa.Column("capture_resolved_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_capture_truth_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_capture_truth_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["active_dispatch_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_dispatch_v2.id",
                "platform.collection_submission_dispatch_v2.tenant_id",
                "platform.collection_submission_dispatch_v2.project_id",
                "platform.collection_submission_dispatch_v2.operation_id",
            ],
            name="fk_capture_truth_dispatch_exact",
        ),
        sa.ForeignKeyConstraint(
            ["request_manifest_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_request_manifest_v2.id",
                "platform.collection_submission_request_manifest_v2.tenant_id",
                "platform.collection_submission_request_manifest_v2.project_id",
                "platform.collection_submission_request_manifest_v2.operation_id",
            ],
            name="fk_capture_truth_request_manifest_exact",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            name="uq_capture_truth_operation",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_capture_truth_manifest_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-capture-truth-v1'",
            name=op.f("ck_capture_truth_schema"),
        ),
        sa.CheckConstraint(
            "capture_state IN ('not_started','capturing','completed','partial',"
            "'failed','not_observable')",
            name=op.f("ck_capture_truth_state"),
        ),
        sa.CheckConstraint(
            "capture_state_version > 0 AND attempt_count >= 0",
            name=op.f("ck_capture_truth_versions"),
        ),
        sa.CheckConstraint(
            "(capture_state='not_started' AND capture_state_version=1 "
            "AND attempt_count=0 AND current_attempt_ref IS NULL "
            "AND active_dispatch_id IS NULL AND active_owner_handle IS NULL "
            "AND active_fence_set_hash IS NULL AND active_request_sha256 IS NULL "
            "AND active_command_json IS NULL "
            "AND current_capture_manifest_id IS NULL "
            "AND capture_requested_at IS NULL "
            "AND capture_started_at IS NULL AND capture_resolved_at IS NULL) OR "
            "(capture_state='capturing' AND attempt_count > 0 "
            "AND current_attempt_ref IS NOT NULL "
            "AND active_dispatch_id IS NOT NULL AND active_owner_handle IS NOT NULL "
            "AND active_fence_set_hash IS NOT NULL AND active_request_sha256 IS NOT NULL "
            "AND active_command_json IS NOT NULL "
            "AND current_capture_manifest_id IS NULL "
            "AND capture_requested_at IS NOT NULL "
            "AND capture_started_at IS NOT NULL AND capture_resolved_at IS NULL) OR "
            "(capture_state IN ('completed','partial','failed','not_observable') "
            "AND attempt_count > 0 AND current_attempt_ref IS NOT NULL "
            "AND active_dispatch_id IS NOT NULL AND active_owner_handle IS NOT NULL "
            "AND active_fence_set_hash IS NOT NULL AND active_request_sha256 IS NOT NULL "
            "AND active_command_json IS NOT NULL "
            "AND current_capture_manifest_id IS NOT NULL "
            "AND capture_requested_at IS NOT NULL "
            "AND capture_started_at IS NOT NULL AND capture_resolved_at IS NOT NULL)",
            name=op.f("ck_capture_truth_shape"),
        ),
        sa.CheckConstraint(
            "current_attempt_ref IS NULL OR " + _OPAQUE_REF.format(column="current_attempt_ref"),
            name=op.f("ck_capture_truth_attempt_ref"),
        ),
        sa.CheckConstraint(
            "active_owner_handle IS NULL OR " + _OPAQUE_REF.format(column="active_owner_handle"),
            name=op.f("ck_capture_truth_owner_handle"),
        ),
        sa.CheckConstraint(
            "(active_fence_set_hash IS NULL AND active_request_sha256 IS NULL) OR "
            "("
            + _SHA256.format(column="active_fence_set_hash")
            + " AND "
            + _SHA256.format(column="active_request_sha256")
            + ")",
            name=op.f("ck_capture_truth_authority_hashes"),
        ),
        sa.CheckConstraint(
            "active_command_json IS NULL OR ("
            "octet_length(active_command_json) BETWEEN 2 AND 32768 AND "
            "jsonb_typeof(active_command_json::jsonb)='object' AND "
            "active_command_json !~* "
            "'\"(secret|password|cookie|authorization|proxy_url|endpoint)\"[[:space:]]*:')",
            name=op.f("ck_capture_truth_command_dlp"),
        ),
        sa.CheckConstraint("btrim(state_reason) <> ''", name=op.f("ck_capture_truth_reason")),
        schema="platform",
    )


def _create_transition_evidence() -> None:
    op.create_table(
        "collection_submission_transition_evidence_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid()),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("transition_key", sa.String(length=128), nullable=False),
        sa.Column("evidence_kind", sa.String(length=40), nullable=False),
        sa.Column("terminal_reason", sa.String(length=50), nullable=False),
        sa.Column("evidence_state", sa.String(length=30), nullable=False),
        sa.Column("from_send_state", sa.String(length=40), nullable=False),
        sa.Column("to_send_state", sa.String(length=40), nullable=False),
        sa.Column("from_send_state_version", sa.Integer(), nullable=False),
        sa.Column("to_send_state_version", sa.Integer(), nullable=False),
        sa.Column("owner_gateway_revision", sa.String(length=128)),
        sa.Column("owner_dispatch_ref", sa.String(length=255)),
        sa.Column("evidence_ref", sa.String(length=255), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_reference_ref", sa.String(length=255)),
        sa.Column("non_submission_proof_ref", sa.String(length=128)),
        sa.Column("terminated_fence_set_hash", sa.String(length=64)),
        sa.Column("reconciliation_proof_id", sa.Uuid()),
        sa.Column("provider_idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("transition_hash", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_submission_transition_evidence_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_submission_transition_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["execution_grant_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_execution_grant_v2.id",
                "platform.collection_execution_grant_v2.tenant_id",
                "platform.collection_execution_grant_v2.project_id",
                "platform.collection_execution_grant_v2.operation_id",
            ],
            name="fk_submission_transition_execution_grant_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "reconciliation_proof_id",
                "tenant_id",
                "project_id",
                "operation_id",
            ],
            [
                "platform.collection_submission_reconciliation_proof.id",
                "platform.collection_submission_reconciliation_proof.tenant_id",
                "platform.collection_submission_reconciliation_proof.project_id",
                "platform.collection_submission_reconciliation_proof.operation_id",
            ],
            name="fk_submission_transition_reconciliation_proof_exact",
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_dispatch_v2.id",
                "platform.collection_submission_dispatch_v2.tenant_id",
                "platform.collection_submission_dispatch_v2.project_id",
                "platform.collection_submission_dispatch_v2.operation_id",
            ],
            name="fk_submission_transition_dispatch_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "transition_key",
            name="uq_submission_transition_key",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "to_send_state_version",
            name="uq_submission_transition_operation_version",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_submission_transition_effect_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-submission-transition-v1'",
            name=op.f("ck_submission_transition_schema"),
        ),
        sa.CheckConstraint(
            "terminal_reason IN ('dispatch_claimed','submitted','send_unknown',"
            "'preflight_not_sent','post_claim_not_sent','unavailable',"
            "'invalid_surface_or_product')",
            name=op.f("ck_submission_transition_terminal_reason"),
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('dispatch_claimed','provider_accepted',"
            "'send_unknown','preflight_proved_not_sent','owner_proved_not_sent')",
            name=op.f("ck_submission_transition_evidence_kind"),
        ),
        sa.CheckConstraint(
            "evidence_state = 'accepted'",
            name=op.f("ck_submission_transition_evidence_state"),
        ),
        sa.CheckConstraint(
            "from_send_state IN " + str(_SEND_STATES).replace('"', "'") + " AND "
            "to_send_state IN " + str(_SEND_STATES).replace('"', "'"),
            name=op.f("ck_submission_transition_send_states"),
        ),
        sa.CheckConstraint(
            "to_send_state_version = from_send_state_version + 1 AND from_send_state_version > 0",
            name=op.f("ck_submission_transition_versions"),
        ),
        sa.CheckConstraint(
            "(evidence_kind='dispatch_claimed' AND from_send_state='NOT_SENT' "
            "AND to_send_state='SENDING' AND dispatch_id IS NOT NULL "
            "AND owner_gateway_revision IS NOT NULL "
            "AND owner_dispatch_ref IS NOT NULL "
            "AND terminated_fence_set_hash IS NULL "
            "AND non_submission_proof_ref IS NULL "
            "AND reconciliation_proof_id IS NULL "
            "AND terminal_reason='dispatch_claimed') OR "
            "(evidence_kind='provider_accepted' AND from_send_state='SENDING' "
            "AND to_send_state='CONFIRMED_SENT' AND dispatch_id IS NOT NULL "
            "AND owner_gateway_revision IS NOT NULL "
            "AND owner_dispatch_ref IS NOT NULL "
            "AND terminated_fence_set_hash IS NULL "
            "AND non_submission_proof_ref IS NULL "
            "AND reconciliation_proof_id IS NULL AND terminal_reason='submitted') OR "
            "(evidence_kind='send_unknown' AND from_send_state='SENDING' "
            "AND to_send_state='SEND_UNKNOWN' AND dispatch_id IS NOT NULL "
            "AND owner_gateway_revision IS NOT NULL "
            "AND owner_dispatch_ref IS NOT NULL "
            "AND terminated_fence_set_hash IS NULL "
            "AND non_submission_proof_ref IS NULL "
            "AND reconciliation_proof_id IS NULL AND terminal_reason='send_unknown') OR "
            "(evidence_kind='owner_proved_not_sent' AND from_send_state='SENDING' "
            "AND to_send_state='CONFIRMED_NOT_SENT' AND dispatch_id IS NOT NULL "
            "AND owner_gateway_revision IS NOT NULL "
            "AND owner_dispatch_ref IS NOT NULL "
            "AND terminated_fence_set_hash IS NOT NULL "
            "AND non_submission_proof_ref IS NOT NULL "
            "AND reconciliation_proof_id IS NOT NULL "
            "AND terminal_reason='post_claim_not_sent') OR "
            "(evidence_kind='preflight_proved_not_sent' "
            "AND from_send_state='NOT_SENT' AND to_send_state='CONFIRMED_NOT_SENT' "
            "AND dispatch_id IS NULL AND terminated_fence_set_hash IS NOT NULL "
            "AND owner_gateway_revision IS NULL AND owner_dispatch_ref IS NULL "
            "AND non_submission_proof_ref IS NULL "
            "AND reconciliation_proof_id IS NULL "
            "AND terminal_reason IN ('preflight_not_sent','unavailable',"
            "'invalid_surface_or_product'))",
            name=op.f("ck_submission_transition_mapping"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="transition_key")
            + " AND "
            + "(owner_gateway_revision IS NULL OR "
            + _OPAQUE_REF.format(column="owner_gateway_revision")
            + ") AND (owner_dispatch_ref IS NULL OR "
            + _OPAQUE_REF.format(column="owner_dispatch_ref")
            + ")"
            + " AND "
            + _OPAQUE_REF.format(column="evidence_ref")
            + " AND "
            + _OPAQUE_REF.format(column="recorded_by")
            + " AND (non_submission_proof_ref IS NULL OR "
            + _OPAQUE_REF.format(column="non_submission_proof_ref")
            + ")"
            + " AND (provider_reference_ref IS NULL OR "
            + _OPAQUE_REF.format(column="provider_reference_ref")
            + ")",
            name=op.f("ck_submission_transition_opaque_refs"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="evidence_hash")
            + " AND "
            + "(terminated_fence_set_hash IS NULL OR "
            + _SHA256.format(column="terminated_fence_set_hash")
            + ") AND "
            + _SHA256.format(column="provider_idempotency_key_hash")
            + " AND "
            + _SHA256.format(column="transition_hash"),
            name=op.f("ck_submission_transition_hashes"),
        ),
        sa.CheckConstraint(
            "btrim(reason_code) <> ''",
            name=op.f("ck_submission_transition_reason"),
        ),
        schema="platform",
    )


def _create_capture_manifest() -> None:
    op.create_table(
        "collection_capture_manifest_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("capture_truth_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("capture_key", sa.String(length=128), nullable=False),
        sa.Column("capture_attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("capture_attempt_ref", sa.String(length=255), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("capture_link_key", sa.String(length=128)),
        sa.Column("capture_state", sa.String(length=30), nullable=False),
        sa.Column("storage_state", sa.String(length=30), nullable=False),
        sa.Column("capture_channel", sa.String(length=40), nullable=False),
        sa.Column("capture_protocol_revision", sa.String(length=128), nullable=False),
        sa.Column("content_object_ref", sa.String(length=255)),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("content_size_bytes", sa.BigInteger()),
        sa.Column("mime_type", sa.String(length=128)),
        sa.Column("capture_schema_revision", sa.String(length=128)),
        sa.Column("capture_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("capture_evidence_ref", sa.String(length=255), nullable=False),
        sa.Column("capture_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_platform", sa.String(length=128), nullable=False),
        sa.Column("observed_surface", sa.String(length=30), nullable=False),
        sa.Column("observed_product_variant", sa.String(length=128), nullable=False),
        sa.Column("observed_product_version", sa.String(length=128), nullable=False),
        sa.Column("capture_adapter_revision", sa.String(length=128), nullable=False),
        sa.Column("data_classification", sa.String(length=30), nullable=False),
        sa.Column("dlp_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
        sa.Column("quarantined_at", sa.DateTime(timezone=True)),
        sa.Column("orphaned_at", sa.DateTime(timezone=True)),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gc_after", sa.DateTime(timezone=True)),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_scope_constraints("collection_capture_manifest_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_capture_manifest_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_dispatch_v2.id",
                "platform.collection_submission_dispatch_v2.tenant_id",
                "platform.collection_submission_dispatch_v2.project_id",
                "platform.collection_submission_dispatch_v2.operation_id",
            ],
            name="fk_capture_manifest_dispatch_exact",
        ),
        sa.ForeignKeyConstraint(
            ["capture_truth_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_capture_truth_v2.id",
                "platform.collection_capture_truth_v2.tenant_id",
                "platform.collection_capture_truth_v2.project_id",
                "platform.collection_capture_truth_v2.operation_id",
            ],
            name="fk_capture_manifest_truth_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "capture_key",
            name="uq_capture_manifest_key",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "capture_attempt_ordinal",
            name="uq_capture_manifest_attempt_ordinal",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "capture_attempt_ref",
            name="uq_capture_manifest_attempt_ref",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_capture_manifest_effect_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "capture_truth_id",
            name="uq_capture_manifest_truth_current_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "dispatch_id",
            "capture_state",
            "capture_channel",
            "capture_protocol_revision",
            name="uq_capture_manifest_observation_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-capture-manifest-v1'",
            name=op.f("ck_capture_manifest_schema"),
        ),
        sa.CheckConstraint(
            "capture_attempt_ordinal > 0 AND " + _OPAQUE_REF.format(column="capture_attempt_ref"),
            name=op.f("ck_capture_manifest_attempt_identity"),
        ),
        sa.CheckConstraint(
            "capture_state IN ('completed','partial','failed','not_observable')",
            name=op.f("ck_capture_manifest_capture_state"),
        ),
        sa.CheckConstraint(
            "storage_state IN ('staging','linked','quarantined','orphaned')",
            name=op.f("ck_capture_manifest_storage_state"),
        ),
        sa.CheckConstraint(
            "capture_channel IN ('provider_payload','web_dom','web_screenshot',"
            "'web_network','app_ui','app_accessibility','app_screenshot','app_network')",
            name=op.f("ck_capture_manifest_channel"),
        ),
        sa.CheckConstraint(
            "(observed_surface='provider_api' AND capture_channel='provider_payload') OR "
            "(observed_surface='consumer_web' AND capture_channel IN "
            "('web_dom','web_screenshot','web_network')) OR "
            "(observed_surface='consumer_app' AND capture_channel IN "
            "('app_ui','app_accessibility','app_screenshot','app_network'))",
            name=op.f("ck_capture_manifest_channel_surface"),
        ),
        sa.CheckConstraint(
            "((content_object_ref IS NULL AND content_hash IS NULL "
            "AND content_size_bytes IS NULL AND mime_type IS NULL "
            "AND capture_schema_revision IS NULL) OR "
            "(content_object_ref IS NOT NULL AND content_hash IS NOT NULL "
            "AND content_size_bytes IS NOT NULL AND content_size_bytes >= 0 "
            "AND mime_type IS NOT NULL AND capture_schema_revision IS NOT NULL)) "
            "AND (capture_state NOT IN ('completed','partial') "
            "OR content_object_ref IS NOT NULL)",
            name=op.f("ck_capture_manifest_content_shape"),
        ),
        sa.CheckConstraint(
            "(storage_state='staging' AND linked_at IS NULL "
            "AND quarantined_at IS NULL AND orphaned_at IS NULL AND gc_after IS NULL) OR "
            "(storage_state='linked' AND linked_at IS NOT NULL "
            "AND quarantined_at IS NULL AND orphaned_at IS NULL AND gc_after IS NULL) OR "
            "(storage_state='quarantined' AND linked_at IS NULL "
            "AND quarantined_at IS NOT NULL AND orphaned_at IS NULL "
            "AND gc_after IS NULL) OR "
            "(storage_state='orphaned' AND linked_at IS NULL "
            "AND orphaned_at IS NOT NULL "
            "AND (quarantined_at IS NULL OR quarantined_at <= orphaned_at) "
            "AND gc_after IS NOT NULL AND gc_after >= retention_until)",
            name=op.f("ck_capture_manifest_storage_timestamps"),
        ),
        sa.CheckConstraint(
            "captured_at <= staged_at AND retention_until >= staged_at",
            name=op.f("ck_capture_manifest_time_order"),
        ),
        sa.CheckConstraint(
            "(capture_link_key IS NULL OR "
            "capture_link_key ~ '^capture-link-v1-[0-9a-f]{64}$') AND "
            "((storage_state='linked' AND capture_state IN ('completed','partial') "
            "AND capture_link_key IS NOT NULL) OR "
            "(NOT (storage_state='linked' AND capture_state IN "
            "('completed','partial')) AND capture_link_key IS NULL))",
            name=op.f("ck_capture_manifest_link_identity"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="capture_key")
            + " AND (content_object_ref IS NULL OR "
            + _OPAQUE_REF.format(column="content_object_ref")
            + ") AND (capture_schema_revision IS NULL OR "
            + _OPAQUE_REF.format(column="capture_schema_revision")
            + ") AND "
            + _OPAQUE_REF.format(column="observed_product_variant")
            + " AND "
            + _OPAQUE_REF.format(column="observed_product_version")
            + " AND "
            + _OPAQUE_REF.format(column="capture_protocol_revision")
            + " AND "
            + _OPAQUE_REF.format(column="capture_adapter_revision")
            + " AND "
            + _OPAQUE_REF.format(column="dlp_policy_revision")
            + " AND "
            + _OPAQUE_REF.format(column="capture_evidence_ref"),
            name=op.f("ck_capture_manifest_opaque_refs"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="observed_platform"),
            name=op.f("ck_capture_manifest_platform"),
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="observed_surface"),
            name=op.f("ck_capture_manifest_surface"),
        ),
        sa.CheckConstraint(
            "data_classification IN ('public','customer_private','restricted')",
            name=op.f("ck_capture_manifest_data_classification"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="capture_manifest_hash")
            + " AND "
            + _SHA256.format(column="capture_evidence_hash")
            + " AND (content_hash IS NULL OR "
            + _SHA256.format(column="content_hash")
            + ")",
            name=op.f("ck_capture_manifest_hashes"),
        ),
        sa.CheckConstraint("btrim(reason_code) <> ''", name=op.f("ck_capture_manifest_reason")),
        schema="platform",
    )
    op.create_index(
        "uq_capture_manifest_current_operation",
        "collection_capture_manifest_v2",
        ["tenant_id", "project_id", "operation_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_index(
        "ix_capture_manifest_gc",
        "collection_capture_manifest_v2",
        ["tenant_id", "project_id", "storage_state", "gc_after"],
        schema="platform",
        postgresql_where=sa.text("storage_state = 'orphaned' AND legal_hold = false"),
    )
    op.create_foreign_key(
        "fk_capture_truth_current_manifest_exact",
        "collection_capture_truth_v2",
        "collection_capture_manifest_v2",
        [
            "current_capture_manifest_id",
            "tenant_id",
            "project_id",
            "operation_id",
            "id",
        ],
        ["id", "tenant_id", "project_id", "operation_id", "capture_truth_id"],
        source_schema="platform",
        referent_schema="platform",
    )


def _create_observation_and_outcomes() -> None:
    op.create_table(
        "collection_observation_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("primary_slot_id", sa.Uuid(), nullable=False),
        sa.Column("capture_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("request_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("capture_state_version", sa.Integer(), nullable=False),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("grant_authority_hash", sa.String(length=64), nullable=False),
        sa.Column("fence_set_hash", sa.String(length=64), nullable=False),
        sa.Column("observation_key", sa.String(length=128), nullable=False),
        sa.Column("capture_state", sa.String(length=30), nullable=False),
        sa.Column("capture_channel", sa.String(length=40), nullable=False),
        sa.Column("capture_protocol_revision", sa.String(length=128), nullable=False),
        sa.Column("content_object_ref", sa.String(length=255)),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("evidence_set_hash", sa.String(length=64), nullable=False),
        sa.Column("observation_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_platform", sa.String(length=128), nullable=False),
        sa.Column("requested_surface", sa.String(length=30), nullable=False),
        sa.Column("requested_product_variant", sa.String(length=128), nullable=False),
        sa.Column("observed_platform", sa.String(length=128), nullable=False),
        sa.Column("observed_surface", sa.String(length=30), nullable=False),
        sa.Column("observed_product_variant", sa.String(length=128), nullable=False),
        sa.Column("observed_product_version", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_observation_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id", "primary_slot_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
                "platform.collection_submission_operation.primary_slot_id",
            ],
            name="fk_observation_operation_slot_exact",
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_dispatch_v2.id",
                "platform.collection_submission_dispatch_v2.tenant_id",
                "platform.collection_submission_dispatch_v2.project_id",
                "platform.collection_submission_dispatch_v2.operation_id",
            ],
            name="fk_observation_dispatch_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "capture_manifest_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "dispatch_id",
                "capture_state",
                "capture_channel",
                "capture_protocol_revision",
            ],
            [
                "platform.collection_capture_manifest_v2.id",
                "platform.collection_capture_manifest_v2.tenant_id",
                "platform.collection_capture_manifest_v2.project_id",
                "platform.collection_capture_manifest_v2.operation_id",
                "platform.collection_capture_manifest_v2.dispatch_id",
                "platform.collection_capture_manifest_v2.capture_state",
                "platform.collection_capture_manifest_v2.capture_channel",
                "platform.collection_capture_manifest_v2.capture_protocol_revision",
            ],
            name="fk_observation_capture_exact",
        ),
        sa.UniqueConstraint(
            "capture_manifest_id",
            "tenant_id",
            "project_id",
            name="uq_observation_capture_manifest",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "observation_key",
            name="uq_observation_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_observation_effect_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "primary_slot_id",
            name="uq_observation_outcome_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-observation-v1'",
            name=op.f("ck_observation_schema"),
        ),
        sa.CheckConstraint(
            "capture_state IN ('completed','partial','failed','not_observable')",
            name=op.f("ck_observation_capture_state"),
        ),
        sa.CheckConstraint(
            "capture_state_version > 0 AND "
            + _OPAQUE_REF.format(column="observation_key")
            + " AND (content_object_ref IS NULL OR "
            + _OPAQUE_REF.format(column="content_object_ref")
            + ") AND "
            + _OPAQUE_REF.format(column="observed_product_variant")
            + " AND "
            + _OPAQUE_REF.format(column="observed_product_version")
            + " AND "
            + _OPAQUE_REF.format(column="requested_platform")
            + " AND "
            + _OPAQUE_REF.format(column="requested_product_variant"),
            name=op.f("ck_observation_opaque_refs"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="capture_protocol_revision"),
            name=op.f("ck_observation_capture_protocol"),
        ),
        sa.CheckConstraint(
            "capture_channel IN ('provider_payload','web_dom','web_screenshot',"
            "'web_network','app_ui','app_accessibility','app_screenshot','app_network')",
            name=op.f("ck_observation_capture_channel"),
        ),
        sa.CheckConstraint(
            _SHA256.format(column="evidence_set_hash")
            + " AND "
            + _SHA256.format(column="observation_hash")
            + " AND "
            + _SHA256.format(column="request_manifest_hash")
            + " AND "
            + _SHA256.format(column="grant_authority_hash")
            + " AND "
            + _SHA256.format(column="fence_set_hash")
            + " AND (content_hash IS NULL OR "
            + _SHA256.format(column="content_hash")
            + ")",
            name=op.f("ck_observation_hashes"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="observed_platform"),
            name=op.f("ck_observation_platform"),
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="observed_surface")
            + " AND "
            + _SURFACE.format(column="requested_surface"),
            name=op.f("ck_observation_surface"),
        ),
        schema="platform",
    )

    op.create_table(
        "collection_slot_outcome_v2",
        *_identity_columns(),
        sa.Column("primary_slot_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("operation_generation", sa.Integer()),
        sa.Column("capture_manifest_id", sa.Uuid()),
        sa.Column("outcome_ordinal", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Uuid()),
        sa.Column("operation_state_version", sa.Integer()),
        sa.Column("capture_state_version", sa.Integer()),
        sa.Column("analysis_state_version", sa.Integer()),
        sa.Column("capture_link_key", sa.String(length=128)),
        sa.Column("fact_version", sa.Integer()),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("outcome_key", sa.String(length=128), nullable=False),
        sa.Column("outcome_state", sa.String(length=60), nullable=False),
        sa.Column("is_final_primary", sa.Boolean(), nullable=False),
        sa.Column("outcome_hash", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_slot_outcome_v2"),
        sa.ForeignKeyConstraint(
            ["primary_slot_id", "tenant_id", "project_id"],
            [
                "platform.collection_primary_slot.id",
                "platform.collection_primary_slot.tenant_id",
                "platform.collection_primary_slot.project_id",
            ],
            name="fk_slot_outcome_primary_slot_scope",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id", "primary_slot_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
                "platform.collection_submission_operation.primary_slot_id",
            ],
            name="fk_slot_outcome_operation_slot_exact",
        ),
        sa.ForeignKeyConstraint(
            ["capture_manifest_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_capture_manifest_v2.id",
                "platform.collection_capture_manifest_v2.tenant_id",
                "platform.collection_capture_manifest_v2.project_id",
                "platform.collection_capture_manifest_v2.operation_id",
            ],
            name="fk_slot_outcome_capture_exact",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "tenant_id", "project_id", "operation_id", "primary_slot_id"],
            [
                "platform.collection_observation_v2.id",
                "platform.collection_observation_v2.tenant_id",
                "platform.collection_observation_v2.project_id",
                "platform.collection_observation_v2.operation_id",
                "platform.collection_observation_v2.primary_slot_id",
            ],
            name="fk_slot_outcome_observation_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "outcome_key",
            name="uq_slot_outcome_key",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "outcome_ordinal",
            name="uq_slot_outcome_operation_ordinal",
        ),
        sa.UniqueConstraint(
            "capture_manifest_id",
            "tenant_id",
            "project_id",
            name="uq_slot_outcome_capture_manifest",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_slot_outcome_effect_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "primary_slot_id",
            name="uq_slot_outcome_analysis_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-slot-outcome-v1'",
            name=op.f("ck_slot_outcome_schema"),
        ),
        sa.CheckConstraint(
            "outcome_state IN ('not_attempted','unavailable','confirmed_not_sent',"
            "'confirmed_sent_capture_pending',"
            "'confirmed_sent_capture_complete','confirmed_sent_capture_partial',"
            "'confirmed_sent_capture_failed','send_unknown',"
            "'invalid_surface_or_product','analysis_failed','not_observable')",
            name=op.f("ck_slot_outcome_state"),
        ),
        sa.CheckConstraint(
            "outcome_ordinal >= 0 AND "
            "((operation_id IS NULL AND operation_generation IS NULL "
            "AND capture_manifest_id IS NULL AND operation_state_version IS NULL "
            "AND capture_state_version IS NULL AND analysis_state_version IS NULL "
            "AND capture_link_key IS NULL AND fact_version IS NULL "
            "AND outcome_ordinal=0) OR "
            "(operation_id IS NOT NULL AND operation_generation IS NOT NULL "
            "AND operation_generation > 0 AND operation_state_version IS NOT NULL "
            "AND operation_state_version > 0 AND fact_version IS NOT NULL "
            "AND fact_version > 0)) "
            "AND (observation_id IS NULL OR operation_id IS NOT NULL)",
            name=op.f("ck_slot_outcome_operation_shape"),
        ),
        sa.CheckConstraint(
            "((capture_manifest_id IS NULL AND capture_state_version IS NULL "
            "AND capture_link_key IS NULL) OR "
            "(capture_manifest_id IS NOT NULL AND capture_state_version IS NOT NULL "
            "AND capture_state_version > 0)) AND "
            "(capture_link_key IS NULL OR "
            + _OPAQUE_REF.format(column="capture_link_key")
            + ") AND analysis_state_version IS NULL",
            name=op.f("ck_slot_outcome_fact_basis"),
        ),
        sa.CheckConstraint(
            "(outcome_state IN ('confirmed_sent_capture_complete',"
            "'confirmed_sent_capture_partial') AND observation_id IS NOT NULL "
            "AND capture_link_key IS NOT NULL AND analysis_state_version IS NULL) OR "
            "(outcome_state NOT IN ('confirmed_sent_capture_complete',"
            "'confirmed_sent_capture_partial') AND analysis_state_version IS NULL "
            "AND (capture_link_key IS NULL OR observation_id IS NOT NULL))",
            name=op.f("ck_slot_outcome_observation_shape"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="outcome_key")
            + " AND "
            + _SHA256.format(column="outcome_hash")
            + " AND btrim(reason_code) <> ''",
            name=op.f("ck_slot_outcome_integrity"),
        ),
        schema="platform",
    )
    op.create_index(
        "uq_slot_outcome_without_operation",
        "collection_slot_outcome_v2",
        ["tenant_id", "project_id", "primary_slot_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("operation_id IS NULL"),
    )
    op.create_index(
        "uq_slot_outcome_final_primary",
        "collection_slot_outcome_v2",
        ["tenant_id", "project_id", "primary_slot_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("is_final_primary = true"),
    )

    op.create_table(
        "collection_analysis_admission_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("primary_slot_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("admission_key", sa.String(length=128), nullable=False),
        sa.Column("analysis_contract_revision", sa.String(length=128), nullable=False),
        sa.Column("analysis_input_hash", sa.String(length=64), nullable=False),
        sa.Column("admission_state", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_analysis_admission_v2"),
        sa.ForeignKeyConstraint(
            ["observation_id", "tenant_id", "project_id", "operation_id", "primary_slot_id"],
            [
                "platform.collection_observation_v2.id",
                "platform.collection_observation_v2.tenant_id",
                "platform.collection_observation_v2.project_id",
                "platform.collection_observation_v2.operation_id",
                "platform.collection_observation_v2.primary_slot_id",
            ],
            name="fk_analysis_admission_observation_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "admission_key",
            name="uq_analysis_admission_key",
        ),
        sa.UniqueConstraint(
            "observation_id",
            "tenant_id",
            "project_id",
            "analysis_contract_revision",
            name="uq_analysis_admission_observation_contract",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_analysis_admission_effect_scope",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-analysis-admission-v1'",
            name=op.f("ck_analysis_admission_schema"),
        ),
        sa.CheckConstraint(
            "admission_state = 'admitted'",
            name=op.f("ck_analysis_admission_state"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="admission_key")
            + " AND "
            + _OPAQUE_REF.format(column="analysis_contract_revision")
            + " AND "
            + _SHA256.format(column="analysis_input_hash")
            + " AND btrim(reason_code) <> ''",
            name=op.f("ck_analysis_admission_integrity"),
        ),
        schema="platform",
    )


def _create_governance_outbox() -> None:
    op.create_table(
        "collection_governance_effect_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("transition_evidence_id", sa.Uuid()),
        sa.Column("capture_manifest_id", sa.Uuid()),
        sa.Column("observation_id", sa.Uuid()),
        sa.Column("slot_outcome_id", sa.Uuid()),
        sa.Column("analysis_admission_id", sa.Uuid()),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("effect_key", sa.String(length=128), nullable=False),
        sa.Column("effect_kind", sa.String(length=40), nullable=False),
        sa.Column("send_state", sa.String(length=40), nullable=False),
        sa.Column("send_state_version", sa.Integer(), nullable=False),
        sa.Column("effect_hash", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_governance_effect_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_governance_effect_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["transition_evidence_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_submission_transition_evidence_v2.id",
                "platform.collection_submission_transition_evidence_v2.tenant_id",
                "platform.collection_submission_transition_evidence_v2.project_id",
                "platform.collection_submission_transition_evidence_v2.operation_id",
            ],
            name="fk_governance_effect_transition_exact",
        ),
        sa.ForeignKeyConstraint(
            ["capture_manifest_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_capture_manifest_v2.id",
                "platform.collection_capture_manifest_v2.tenant_id",
                "platform.collection_capture_manifest_v2.project_id",
                "platform.collection_capture_manifest_v2.operation_id",
            ],
            name="fk_governance_effect_capture_exact",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_observation_v2.id",
                "platform.collection_observation_v2.tenant_id",
                "platform.collection_observation_v2.project_id",
                "platform.collection_observation_v2.operation_id",
            ],
            name="fk_governance_effect_observation_exact",
        ),
        sa.ForeignKeyConstraint(
            ["slot_outcome_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_slot_outcome_v2.id",
                "platform.collection_slot_outcome_v2.tenant_id",
                "platform.collection_slot_outcome_v2.project_id",
                "platform.collection_slot_outcome_v2.operation_id",
            ],
            name="fk_governance_effect_outcome_exact",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_admission_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_analysis_admission_v2.id",
                "platform.collection_analysis_admission_v2.tenant_id",
                "platform.collection_analysis_admission_v2.project_id",
                "platform.collection_analysis_admission_v2.operation_id",
            ],
            name="fk_governance_effect_analysis_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "effect_key",
            name="uq_governance_effect_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_governance_effect_outbox_scope",
        ),
        sa.UniqueConstraint(
            "slot_outcome_id",
            "tenant_id",
            "project_id",
            name="uq_governance_effect_slot_outcome",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-governance-effect-v1'",
            name=op.f("ck_governance_effect_schema"),
        ),
        sa.CheckConstraint(
            "effect_kind IN ('submission_terminalized','capture_linked',"
            "'capture_classified','slot_outcome_recorded','analysis_admitted',"
            "'resource_outcome',"
            "'binding_outcome','account_outcome')",
            name=op.f("ck_governance_effect_kind"),
        ),
        sa.CheckConstraint(
            "send_state IN " + str(_SEND_STATES).replace('"', "'") + " AND send_state_version > 0",
            name=op.f("ck_governance_effect_send_state"),
        ),
        sa.CheckConstraint(
            "(effect_kind='submission_terminalized' "
            "AND transition_evidence_id IS NOT NULL) OR "
            "(effect_kind IN ('capture_linked','capture_classified') "
            "AND capture_manifest_id IS NOT NULL) OR "
            "(effect_kind='slot_outcome_recorded' "
            "AND slot_outcome_id IS NOT NULL) OR "
            "(effect_kind='analysis_admitted' AND analysis_admission_id IS NOT NULL) OR "
            "(effect_kind IN ('resource_outcome','binding_outcome','account_outcome'))",
            name=op.f("ck_governance_effect_subject"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="effect_key")
            + " AND "
            + _SHA256.format(column="effect_hash")
            + " AND btrim(reason_code) <> ''",
            name=op.f("ck_governance_effect_integrity"),
        ),
        schema="platform",
    )

    op.create_table(
        "collection_governance_outbox_v2",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("governance_effect_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_pub_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("payload_schema_revision", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("publish_state", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("quarantined_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=128)),
        *_scope_constraints("collection_governance_outbox_v2"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_governance_outbox_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["governance_effect_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_governance_effect_v2.id",
                "platform.collection_governance_effect_v2.tenant_id",
                "platform.collection_governance_effect_v2.project_id",
                "platform.collection_governance_effect_v2.operation_id",
            ],
            name="fk_governance_outbox_effect_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "event_key",
            name="uq_governance_outbox_event_key",
        ),
        sa.UniqueConstraint(
            "governance_effect_id",
            "tenant_id",
            "project_id",
            name="uq_governance_outbox_effect",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-governance-outbox-v1'",
            name=op.f("ck_governance_outbox_schema"),
        ),
        sa.CheckConstraint(
            "publish_state IN ('pending','published','quarantined') "
            "AND attempt_count >= 0 AND aggregate_version > 0 "
            "AND available_at >= occurred_at",
            name=op.f("ck_governance_outbox_state"),
        ),
        sa.CheckConstraint(
            "(publish_state='pending' AND published_at IS NULL "
            "AND quarantined_at IS NULL) OR "
            "(publish_state='published' AND published_at IS NOT NULL "
            "AND quarantined_at IS NULL AND last_error_code IS NULL) OR "
            "(publish_state='quarantined' AND published_at IS NULL "
            "AND quarantined_at IS NOT NULL AND last_error_code IS NOT NULL "
            "AND btrim(last_error_code) <> '')",
            name=op.f("ck_governance_outbox_timestamps"),
        ),
        sa.CheckConstraint(
            _OPAQUE_REF.format(column="event_key")
            + " AND "
            + _OPAQUE_REF.format(column="event_type")
            + " AND "
            + _OPAQUE_REF.format(column="aggregate_type")
            + " AND "
            + _OPAQUE_REF.format(column="aggregate_pub_id")
            + " AND "
            + _OPAQUE_REF.format(column="payload_schema_revision")
            + " AND "
            + _SHA256.format(column="payload_hash"),
            name=op.f("ck_governance_outbox_integrity"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_governance_outbox_pending",
        "collection_governance_outbox_v2",
        ["tenant_id", "project_id", "publish_state", "available_at"],
        schema="platform",
        postgresql_where=sa.text("publish_state = 'pending'"),
    )
    op.execute(
        """
        CREATE FUNCTION platform.collection_outbox_key_s10(
          p_event_type text,
          p_aggregate_ref text,
          p_aggregate_version integer,
          p_payload_sha256 text
        ) RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
          IF p_event_type !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_aggregate_ref !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_aggregate_version < 1 OR
             p_payload_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'outbox key material is invalid';
          END IF;
          RETURN 'outbox-v1-' || encode(public.digest(
            '{"aggregate_ref":"' || p_aggregate_ref ||
            '","aggregate_version":' || p_aggregate_version::text ||
            ',"event_type":"' || p_event_type ||
            '","payload_sha256":"' || p_payload_sha256 ||
            '","version":"collection-outbox-key-v1"}',
            'sha256'),'hex');
        END
        $$
        """
    )


def _create_row_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.reject_collection_submission_history_mutation_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION '% is immutable and append-only', TG_TABLE_NAME;
        END
        $$
        """
    )
    for table in (
        "collection_submission_request_manifest_v2",
        "collection_submission_transition_evidence_v2",
        "collection_observation_v2",
        "collection_slot_outcome_v2",
        "collection_analysis_admission_v2",
        "collection_governance_effect_v2",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable_trg
            BEFORE UPDATE OR DELETE ON platform.{table}
            FOR EACH ROW
            EXECUTE FUNCTION platform.reject_collection_submission_history_mutation_s10()
            """
        )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_submission_dispatch_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'submission dispatch is durable and cannot be deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.owner_execution_state<>'active' OR
               NEW.reconciliation_state<>'not_required' OR
               NEW.reconciliation_version<>1 THEN
              RAISE EXCEPTION 'submission dispatch must begin with active owner';
            END IF;
            PERFORM platform.assert_collection_authority_snapshot_s10(
              NEW.tenant_id,NEW.project_id,NEW.operation_id,
              NEW.execution_grant_id,NEW.binding_revision_id,
              NEW.grant_revision,NEW.grant_authority_hash,
              NEW.owner_handle,NEW.grant_resource_set_hash,
              NEW.authority_sha256,NEW.authority_snapshot_json,NEW.claimed_at
            );
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.operation_id,
                 NEW.request_manifest_id,NEW.execution_grant_id,
                 NEW.grant_revision,NEW.grant_authority_hash,
                 NEW.binding_revision_id,NEW.quota_registry_id,
                 NEW.quota_reservation_id,NEW.schema_version,NEW.claim_pub_id,
                 NEW.owner_handle,NEW.authority_sha256,
                 NEW.authority_snapshot_json,NEW.dispatch_key,
                 NEW.owner_gateway_revision,NEW.owner_dispatch_ref,
                 NEW.owner_wal_evidence_hash,NEW.grant_resource_set_hash,
                 NEW.dispatch_hash,NEW.prior_send_state_version,
                 NEW.sending_send_state_version,NEW.claimed_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.operation_id,
                 OLD.request_manifest_id,OLD.execution_grant_id,
                 OLD.grant_revision,OLD.grant_authority_hash,
                 OLD.binding_revision_id,OLD.quota_registry_id,
                 OLD.quota_reservation_id,OLD.schema_version,OLD.claim_pub_id,
                 OLD.owner_handle,OLD.authority_sha256,
                 OLD.authority_snapshot_json,OLD.dispatch_key,
                 OLD.owner_gateway_revision,OLD.owner_dispatch_ref,
                 OLD.owner_wal_evidence_hash,OLD.grant_resource_set_hash,
                 OLD.dispatch_hash,OLD.prior_send_state_version,
                 OLD.sending_send_state_version,OLD.claimed_at) OR
             NEW.reconciliation_version<>OLD.reconciliation_version+1 OR
             NEW.version<>OLD.version+1 THEN
            RAISE EXCEPTION 'submission dispatch identity or CAS version changed';
          END IF;
          IF OLD.reconciliation_state='not_required' AND
             NEW.reconciliation_state='pending' THEN
            IF OLD.owner_execution_state<>'active' OR
               NEW.owner_execution_state<>'owner_lost' THEN
              RAISE EXCEPTION 'reconciliation readiness requires owner-loss evidence';
            END IF;
          ELSIF OLD.reconciliation_state='pending' AND
                NEW.reconciliation_state='in_progress' THEN
            IF NEW.owner_execution_state<>'owner_lost' OR
               ROW(NEW.readiness_evidence_ref,NEW.readiness_evidence_hash,
                   NEW.reconcile_after,NEW.reconciliation_ready_at)
               IS DISTINCT FROM
               ROW(OLD.readiness_evidence_ref,OLD.readiness_evidence_hash,
                   OLD.reconcile_after,OLD.reconciliation_ready_at) THEN
              RAISE EXCEPTION 'reconciliation claim changed readiness proof';
            END IF;
          ELSIF OLD.reconciliation_state IN ('not_required','in_progress') AND
                NEW.reconciliation_state='resolved' THEN
            IF NEW.owner_execution_state<>'resolved' THEN
              RAISE EXCEPTION 'resolved dispatch must close owner authority';
            END IF;
          ELSE
            RAISE EXCEPTION 'invalid dispatch reconciliation transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_submission_dispatch_s10_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_submission_dispatch_v2
        FOR EACH ROW
        EXECUTE FUNCTION platform.guard_collection_submission_dispatch_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_submission_request_manifest_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE operation_row record;
        BEGIN
          SELECT send_state,send_state_version,prepared_at
            INTO operation_row
            FROM platform.collection_submission_operation
           WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id;
          IF NOT FOUND OR operation_row.send_state <> 'NOT_SENT' OR
             operation_row.send_state_version <> 1 OR
             NEW.prepared_at < operation_row.prepared_at THEN
            RAISE EXCEPTION
              'request manifest requires a pristine NOT_SENT operation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER submission_request_manifest_s10_guard_trg
        BEFORE INSERT ON platform.collection_submission_request_manifest_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_submission_request_manifest_s10()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.create_capture_truth_for_request_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE truth_id uuid := gen_random_uuid();
        BEGIN
          INSERT INTO platform.collection_capture_truth_v2 (
            id,pub_id,tenant_id,project_id,operation_id,request_manifest_id,
            schema_version,capture_state,capture_state_version,attempt_count,
            current_attempt_ref,active_dispatch_id,active_owner_handle,
            active_fence_set_hash,active_request_sha256,active_command_json,
            current_capture_manifest_id,state_reason,
            capture_requested_at,capture_started_at,capture_resolved_at
          ) VALUES (
            truth_id,'ctr_' || substr(replace(truth_id::text,'-',''),1,26),
            NEW.tenant_id,NEW.project_id,NEW.operation_id,NEW.id,
            'collection-capture-truth-v1','not_started',1,0,
            NULL,NULL,NULL,NULL,NULL,NULL,NULL,
            'capture_not_started',NULL,NULL,NULL
          );
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER submission_request_manifest_capture_truth_s10_trg
        AFTER INSERT ON platform.collection_submission_request_manifest_v2
        FOR EACH ROW EXECUTE FUNCTION platform.create_capture_truth_for_request_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_capture_truth_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'capture truth is durable and cannot be deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.capture_state <> 'not_started' OR
               NEW.capture_state_version <> 1 OR NEW.attempt_count <> 0 THEN
              RAISE EXCEPTION 'capture truth must begin not_started version 1';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.operation_id,
                 NEW.request_manifest_id,NEW.schema_version)
             IS DISTINCT FROM
             ROW(OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.operation_id,
                 OLD.request_manifest_id,OLD.schema_version) OR
             NEW.capture_state_version <> OLD.capture_state_version + 1 THEN
            RAISE EXCEPTION 'capture truth identity or version is invalid';
          END IF;
          IF NEW.capture_state='capturing' THEN
            IF OLD.capture_state NOT IN
                 ('not_started','partial','failed','not_observable') OR
               NEW.attempt_count <> OLD.attempt_count + 1 OR
               NEW.current_capture_manifest_id IS NOT NULL OR
               NEW.current_attempt_ref IS NULL OR
               NEW.active_dispatch_id IS NULL OR
               NEW.active_owner_handle IS NULL OR
               NEW.active_fence_set_hash IS NULL OR
               NEW.active_request_sha256 IS NULL OR
               NEW.active_command_json IS NULL OR
               NEW.capture_requested_at IS NULL OR
               NEW.capture_started_at IS DISTINCT FROM
                 NEW.capture_requested_at THEN
              RAISE EXCEPTION 'capture retry transition is invalid';
            END IF;
          ELSIF NEW.capture_state IN
              ('completed','partial','failed','not_observable') THEN
            IF OLD.capture_state <> 'capturing' OR
               NEW.attempt_count <> OLD.attempt_count OR
               NEW.current_capture_manifest_id IS NULL OR
               ROW(NEW.current_attempt_ref,NEW.active_dispatch_id,
                   NEW.active_owner_handle,NEW.active_fence_set_hash,
                   NEW.active_request_sha256,NEW.active_command_json,
                   NEW.capture_requested_at,
                   NEW.capture_started_at) IS DISTINCT FROM
               ROW(OLD.current_attempt_ref,OLD.active_dispatch_id,
                   OLD.active_owner_handle,OLD.active_fence_set_hash,
                   OLD.active_request_sha256,OLD.active_command_json,
                   OLD.capture_requested_at,
                   OLD.capture_started_at) THEN
              RAISE EXCEPTION 'capture resolution transition is invalid';
            END IF;
          ELSE
            RAISE EXCEPTION 'invalid capture truth transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_capture_truth_s10_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_capture_truth_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_capture_truth_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_capture_manifest_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE operation_row record;
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'capture manifests are durable and cannot be deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            SELECT send_state,platform,collection_surface,product_variant
              INTO STRICT operation_row
              FROM platform.collection_submission_operation
             WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF operation_row.send_state NOT IN
                 ('CONFIRMED_SENT','SEND_UNKNOWN') OR NOT (
               (operation_row.platform=NEW.observed_platform AND
                operation_row.collection_surface=NEW.observed_surface AND
                operation_row.product_variant=NEW.observed_product_variant AND
                NEW.storage_state='staging') OR
               ((operation_row.platform<>NEW.observed_platform OR
                 operation_row.collection_surface<>NEW.observed_surface OR
                 operation_row.product_variant<>NEW.observed_product_variant) AND
                NEW.capture_state='not_observable' AND
                NEW.storage_state='quarantined')
            ) THEN
              RAISE EXCEPTION
                'capture manifest requires sent truth and safe normalization';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM platform.collection_capture_truth_v2 truth
               WHERE truth.id=NEW.capture_truth_id
                 AND truth.tenant_id=NEW.tenant_id
                 AND truth.project_id=NEW.project_id
                 AND truth.operation_id=NEW.operation_id
                 AND truth.capture_state='capturing'
                 AND truth.active_dispatch_id=NEW.dispatch_id
                 AND truth.attempt_count=NEW.capture_attempt_ordinal
                 AND truth.current_attempt_ref=NEW.capture_attempt_ref
                 AND truth.current_capture_manifest_id IS NULL
            ) OR NEW.is_current=false THEN
              RAISE EXCEPTION 'capture manifest is not the current capture attempt';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.operation_id,
                 NEW.dispatch_id,NEW.capture_truth_id,NEW.schema_version,
                 NEW.capture_key,NEW.capture_attempt_ordinal,
                 NEW.capture_attempt_ref,
                 NEW.capture_state,NEW.capture_channel,
                 NEW.capture_protocol_revision,NEW.content_object_ref,
                 NEW.content_hash,NEW.content_size_bytes,NEW.mime_type,
                 NEW.capture_schema_revision,NEW.capture_manifest_hash,
                 NEW.capture_evidence_ref,NEW.capture_evidence_hash,
                 NEW.observed_platform,NEW.observed_surface,
                 NEW.observed_product_variant,
                 NEW.observed_product_version,NEW.capture_adapter_revision,
                 NEW.data_classification,NEW.dlp_policy_revision,
                 NEW.reason_code,NEW.captured_at,NEW.staged_at,
                 NEW.retention_until,NEW.legal_hold)
             IS DISTINCT FROM
             ROW(OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.operation_id,
                 OLD.dispatch_id,OLD.capture_truth_id,OLD.schema_version,
                 OLD.capture_key,OLD.capture_attempt_ordinal,
                 OLD.capture_attempt_ref,
                 OLD.capture_state,OLD.capture_channel,
                 OLD.capture_protocol_revision,OLD.content_object_ref,
                 OLD.content_hash,OLD.content_size_bytes,OLD.mime_type,
                 OLD.capture_schema_revision,OLD.capture_manifest_hash,
                 OLD.capture_evidence_ref,OLD.capture_evidence_hash,
                 OLD.observed_platform,OLD.observed_surface,
                 OLD.observed_product_variant,
                 OLD.observed_product_version,OLD.capture_adapter_revision,
                 OLD.data_classification,OLD.dlp_policy_revision,
                 OLD.reason_code,OLD.captured_at,OLD.staged_at,
                 OLD.retention_until,OLD.legal_hold) OR
             (OLD.storage_state<>'staging' AND
              NEW.capture_link_key IS DISTINCT FROM OLD.capture_link_key) OR
             (OLD.storage_state='staging' AND NEW.storage_state<>'linked' AND
              NEW.capture_link_key IS NOT NULL) THEN
            RAISE EXCEPTION 'capture identity and content are immutable';
          END IF;
          IF NEW.version <> OLD.version + 1 OR NOT (
             (OLD.storage_state='staging' AND
              NEW.storage_state IN ('linked','quarantined') AND
              NEW.is_current IN (true,false)) OR
             (OLD.storage_state='staging' AND
              NEW.storage_state='orphaned' AND
              OLD.is_current=false AND NEW.is_current=false AND NOT EXISTS (
                SELECT 1 FROM platform.collection_capture_truth_v2 truth
                 WHERE truth.current_capture_manifest_id=OLD.id
                   AND truth.tenant_id=OLD.tenant_id
                   AND truth.project_id=OLD.project_id
                   AND truth.operation_id=OLD.operation_id
              )) OR
             (OLD.storage_state='quarantined' AND
              NEW.storage_state='orphaned' AND
              NEW.is_current IS NOT DISTINCT FROM OLD.is_current AND
              OLD.retention_until<=CURRENT_TIMESTAMP AND
              NOT OLD.legal_hold AND
              NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at AND
              NEW.orphaned_at IS NOT NULL AND
              NEW.gc_after IS NOT NULL AND
              NEW.gc_after>=OLD.retention_until) OR
             (OLD.storage_state IN ('linked','quarantined','orphaned') AND
              NEW.storage_state=OLD.storage_state AND OLD.is_current=true AND
              NEW.is_current=false)
          ) THEN
            RAISE EXCEPTION 'invalid irreversible capture storage transition';
          END IF;
          IF NEW.storage_state='linked' AND NOT EXISTS (
            SELECT 1 FROM platform.collection_observation_v2 observation
             WHERE observation.capture_manifest_id=NEW.id
               AND observation.tenant_id=NEW.tenant_id
               AND observation.project_id=NEW.project_id
          ) THEN
            RAISE EXCEPTION 'linked capture requires immutable observation';
          END IF;
          IF NEW.storage_state IN ('quarantined','orphaned') AND EXISTS (
            SELECT 1 FROM platform.collection_observation_v2 observation
             WHERE observation.capture_manifest_id=NEW.id
               AND observation.tenant_id=NEW.tenant_id
               AND observation.project_id=NEW.project_id
          ) THEN
            RAISE EXCEPTION 'observed capture cannot be quarantined or orphaned';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_capture_manifest_s10_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_capture_manifest_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_capture_manifest_s10()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.resolve_capture_truth_from_manifest_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        BEGIN
          UPDATE platform.collection_capture_truth_v2
             SET capture_state=NEW.capture_state,
                 capture_state_version=capture_state_version+1,
                 current_capture_manifest_id=NEW.id,
                 state_reason=NEW.reason_code,
                 capture_resolved_at=NEW.captured_at,
                 version=version+1,updated_at=CURRENT_TIMESTAMP
           WHERE id=NEW.capture_truth_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id AND operation_id=NEW.operation_id
             AND capture_state='capturing'
             AND attempt_count=NEW.capture_attempt_ordinal
             AND current_attempt_ref=NEW.capture_attempt_ref
             AND current_capture_manifest_id IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture truth compare-and-swap lost';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_capture_manifest_truth_s10_trg
        AFTER INSERT ON platform.collection_capture_manifest_v2
        FOR EACH ROW EXECUTE FUNCTION platform.resolve_capture_truth_from_manifest_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_observation_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE operation_row record;
        DECLARE capture_row record;
        DECLARE dispatch_row record;
        DECLARE request_row record;
        DECLARE truth_state_version integer;
        BEGIN
          SELECT send_state,platform,collection_surface,product_variant
            INTO operation_row
            FROM platform.collection_submission_operation
           WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id;
          SELECT * INTO capture_row
            FROM platform.collection_capture_manifest_v2
           WHERE id=NEW.capture_manifest_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id AND operation_id=NEW.operation_id;
          SELECT execution_grant_id,binding_revision_id,grant_authority_hash,
                 grant_resource_set_hash INTO dispatch_row
            FROM platform.collection_submission_dispatch_v2
           WHERE id=NEW.dispatch_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id AND operation_id=NEW.operation_id;
          SELECT request_manifest_hash INTO request_row
            FROM platform.collection_submission_request_manifest_v2
           WHERE operation_id=NEW.operation_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id;
          SELECT capture_state_version INTO truth_state_version
            FROM platform.collection_capture_truth_v2
           WHERE id=capture_row.capture_truth_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id AND operation_id=NEW.operation_id;
          IF operation_row.send_state NOT IN ('CONFIRMED_SENT','SEND_UNKNOWN') OR
             capture_row.storage_state <> 'staging' OR
             operation_row.platform <> capture_row.observed_platform OR
             operation_row.collection_surface <> capture_row.observed_surface OR
             operation_row.product_variant <> capture_row.observed_product_variant OR
             ROW(NEW.capture_state,NEW.capture_channel,
                 NEW.capture_protocol_revision,NEW.content_object_ref,
                 NEW.content_hash,NEW.observed_surface,
                 NEW.observed_platform,
                 NEW.observed_product_variant,NEW.observed_product_version)
             IS DISTINCT FROM
             ROW(capture_row.capture_state,capture_row.capture_channel,
                 capture_row.capture_protocol_revision,
                 capture_row.content_object_ref,capture_row.content_hash,
                 capture_row.observed_surface,capture_row.observed_platform,
                 capture_row.observed_product_variant,
                 capture_row.observed_product_version) OR
             ROW(NEW.request_manifest_hash,NEW.capture_state_version,
                 NEW.execution_grant_id,NEW.binding_revision_id,
                 NEW.grant_authority_hash,NEW.fence_set_hash,
                 NEW.requested_platform,NEW.requested_surface,
                 NEW.requested_product_variant)
             IS DISTINCT FROM
             ROW(request_row.request_manifest_hash,truth_state_version,
                 dispatch_row.execution_grant_id,
                 dispatch_row.binding_revision_id,
                 dispatch_row.grant_authority_hash,
                 dispatch_row.grant_resource_set_hash,
                 operation_row.platform,operation_row.collection_surface,
                 operation_row.product_variant) THEN
            RAISE EXCEPTION
              'observation must exactly link a compatible staged capture';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_observation_s10_guard_trg
        BEFORE INSERT ON platform.collection_observation_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_observation_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_slot_outcome_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE operation_row record;
        BEGIN
          IF NEW.operation_id IS NULL THEN
            IF NEW.outcome_state NOT IN
                 ('not_attempted','unavailable','invalid_surface_or_product') THEN
              RAISE EXCEPTION 'operation-free slot outcome is invalid';
            END IF;
            RETURN NEW;
          END IF;
          SELECT send_state,send_state_version,operation_generation
            INTO operation_row
            FROM platform.collection_submission_operation
           WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
             AND project_id=NEW.project_id
             AND primary_slot_id=NEW.primary_slot_id;
          IF NOT FOUND OR
             NEW.operation_generation <> operation_row.operation_generation OR
             NEW.operation_state_version <> operation_row.send_state_version OR
             NEW.fact_version <> COALESCE((
               SELECT max(prior.fact_version)
                 FROM platform.collection_slot_outcome_v2 prior
                WHERE prior.operation_id=NEW.operation_id
                  AND prior.tenant_id=NEW.tenant_id
                  AND prior.project_id=NEW.project_id
             ),0)+1 OR
             (NEW.capture_manifest_id IS NOT NULL AND NOT EXISTS (
               SELECT 1
                 FROM platform.collection_capture_manifest_v2 manifest
                 JOIN platform.collection_capture_truth_v2 truth
                   ON truth.id=manifest.capture_truth_id
                  AND truth.tenant_id=manifest.tenant_id
                  AND truth.project_id=manifest.project_id
                WHERE manifest.id=NEW.capture_manifest_id
                  AND manifest.tenant_id=NEW.tenant_id
                  AND manifest.project_id=NEW.project_id
                  AND manifest.operation_id=NEW.operation_id
                  AND truth.capture_state_version=NEW.capture_state_version
             )) OR
             NEW.analysis_state_version IS NOT NULL OR
             (NEW.outcome_state='not_attempted' AND
              operation_row.send_state<>'NOT_SENT') OR
             (NEW.outcome_state IN ('confirmed_not_sent','unavailable') AND
              operation_row.send_state<>'CONFIRMED_NOT_SENT') OR
             (NEW.outcome_state='send_unknown' AND
              operation_row.send_state<>'SEND_UNKNOWN') OR
             (NEW.outcome_state IN
                ('confirmed_sent_capture_pending',
                 'confirmed_sent_capture_complete',
                 'confirmed_sent_capture_partial',
                 'confirmed_sent_capture_failed','analysis_failed',
                 'not_observable') AND
              operation_row.send_state<>'CONFIRMED_SENT') THEN
            RAISE EXCEPTION 'slot outcome contradicts durable send truth';
          END IF;
          IF NEW.outcome_state='invalid_surface_or_product' AND
             operation_row.send_state NOT IN
               ('CONFIRMED_SENT','CONFIRMED_NOT_SENT') THEN
            RAISE EXCEPTION 'invalid-surface outcome contradicts send truth';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_slot_outcome_s10_guard_trg
        BEFORE INSERT ON platform.collection_slot_outcome_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_slot_outcome_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_analysis_admission_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE observation_row record;
        BEGIN
          SELECT observation.capture_state,observation.content_hash,
                 observation.observation_hash,capture.storage_state
            INTO observation_row
            FROM platform.collection_observation_v2 observation
            JOIN platform.collection_capture_manifest_v2 capture
              ON capture.id=observation.capture_manifest_id
             AND capture.tenant_id=observation.tenant_id
             AND capture.project_id=observation.project_id
           WHERE observation.id=NEW.observation_id
             AND observation.tenant_id=NEW.tenant_id
             AND observation.project_id=NEW.project_id
             AND observation.operation_id=NEW.operation_id;
          IF NOT FOUND OR observation_row.capture_state NOT IN ('completed','partial') OR
             observation_row.storage_state <> 'linked' OR
             NEW.analysis_input_hash IS DISTINCT FROM
               COALESCE(observation_row.content_hash,observation_row.observation_hash) OR
             NEW.admission_state <> 'admitted' THEN
            RAISE EXCEPTION
              'analysis admission requires immutable observable capture input';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_analysis_admission_s10_guard_trg
        BEFORE INSERT ON platform.collection_analysis_admission_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_analysis_admission_s10()
        """
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_collection_governance_outbox_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE effect_row record;
        DECLARE operation_pub_id text;
        DECLARE expected_event_key text;
        DECLARE subject_payload_hash text;
        DECLARE expected_aggregate_version integer;
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'governance outbox is durable and cannot be deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.publish_state <> 'pending' OR NEW.attempt_count <> 0 THEN
              RAISE EXCEPTION 'governance outbox must begin pending';
            END IF;
            SELECT * INTO effect_row
              FROM platform.collection_governance_effect_v2 effect
             WHERE effect.id=NEW.governance_effect_id
               AND effect.tenant_id=NEW.tenant_id
               AND effect.project_id=NEW.project_id
               AND effect.operation_id=NEW.operation_id;
            SELECT pub_id INTO operation_pub_id
              FROM platform.collection_submission_operation operation
             WHERE operation.id=NEW.operation_id
               AND operation.tenant_id=NEW.tenant_id
               AND operation.project_id=NEW.project_id;
            IF effect_row.effect_kind IN
                 ('capture_linked','capture_classified','slot_outcome_recorded') THEN
              SELECT outcome_hash,fact_version
                INTO subject_payload_hash,expected_aggregate_version
                FROM platform.collection_slot_outcome_v2 outcome
               WHERE outcome.id=effect_row.slot_outcome_id
                 AND outcome.tenant_id=NEW.tenant_id
                 AND outcome.project_id=NEW.project_id
                 AND outcome.operation_id=NEW.operation_id;
            ELSE
              expected_aggregate_version := effect_row.send_state_version;
            END IF;
            expected_event_key := platform.collection_outbox_key_s10(
              NEW.event_type,NEW.aggregate_pub_id,
              NEW.aggregate_version,NEW.payload_hash
            );
            IF effect_row.id IS NULL OR operation_pub_id IS NULL OR
               NEW.event_key<>expected_event_key OR
               NEW.aggregate_type<>'collection_submission' OR
               NEW.aggregate_pub_id<>operation_pub_id OR
               NEW.aggregate_version IS DISTINCT FROM
                 expected_aggregate_version OR
               (effect_row.effect_kind='submission_terminalized' AND
                NEW.event_type<>'collection.submission.terminal') OR
               (effect_row.effect_kind IN
                  ('capture_linked','capture_classified','slot_outcome_recorded') AND
                NEW.event_type<>'collection.slot.outcome') OR
               (subject_payload_hash IS NOT NULL AND
                NEW.payload_hash<>subject_payload_hash) OR
               NEW.occurred_at<>effect_row.occurred_at OR
               NEW.available_at<NEW.occurred_at THEN
              RAISE EXCEPTION 'governance outbox identity is not deterministic';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id,NEW.tenant_id,NEW.project_id,NEW.operation_id,
                 NEW.governance_effect_id,NEW.schema_version,NEW.event_key,
                 NEW.event_type,NEW.aggregate_type,NEW.aggregate_pub_id,
                 NEW.aggregate_version,NEW.payload_schema_revision,
                 NEW.payload_hash,NEW.occurred_at,NEW.available_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id,OLD.tenant_id,OLD.project_id,OLD.operation_id,
                 OLD.governance_effect_id,OLD.schema_version,OLD.event_key,
                 OLD.event_type,OLD.aggregate_type,OLD.aggregate_pub_id,
                 OLD.aggregate_version,OLD.payload_schema_revision,
                 OLD.payload_hash,OLD.occurred_at,OLD.available_at) OR
             OLD.publish_state <> 'pending' OR
             NEW.publish_state NOT IN ('published','quarantined') OR
             NEW.attempt_count <> OLD.attempt_count + 1 OR
             NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'invalid irreversible governance outbox transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER collection_governance_outbox_s10_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_governance_outbox_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_collection_governance_outbox_s10()
        """
    )


def _create_claim_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.claim_collection_submission_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_claim_pub_id text,
          p_expected_send_state_version integer,
          p_execution_grant_id uuid,
          p_grant_revision integer,
          p_expected_grant_hash text,
          p_expected_fence_set_hash text,
          p_owner_handle text,
          p_authority_snapshot_json text,
          p_expected_authority_hash text,
          p_dispatch_key text,
          p_owner_gateway_revision text,
          p_owner_dispatch_ref text,
          p_owner_wal_evidence_hash text,
          p_claimed_at timestamptz
        ) RETURNS TABLE(
          dispatch_id uuid,
          persisted_claim_pub_id text,
          claim_acquired boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          request_row record;
          grant_row record;
          reservation_row record;
          existing_dispatch record;
          resource_count integer;
          invalid_resource_count integer;
          calculated_fence_set_hash text;
          calculated_dispatch_hash text;
          new_dispatch_id uuid;
          new_dispatch_pub_id text;
          new_transition_id uuid;
          transition_key text;
          transition_hash text;
          claimed_time timestamptz;
          server_time timestamptz := CURRENT_TIMESTAMP;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'submission claim caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'submission claim tenant context mismatch';
          END IF;
          IF p_expected_send_state_version < 1 OR p_grant_revision < 1 OR
             p_claim_pub_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_expected_grant_hash !~ '^[0-9a-f]{64}$' OR
             p_expected_fence_set_hash !~ '^[0-9a-f]{64}$' OR
             p_expected_authority_hash !~ '^[0-9a-f]{64}$' OR
             p_authority_snapshot_json IS NULL OR
             octet_length(p_authority_snapshot_json) NOT BETWEEN 2 AND 16384 OR
             p_authority_snapshot_json ~*
               '"(secret|password|cookie|authorization|proxy_url|endpoint)"[[:space:]]*:' OR
             p_owner_wal_evidence_hash !~ '^[0-9a-f]{64}$' OR
             p_owner_handle !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_dispatch_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_owner_gateway_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_owner_dispatch_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_claimed_at IS NULL OR
             p_claimed_at > server_time + interval '30 seconds' THEN
            RAISE EXCEPTION 'submission claim input is invalid';
          END IF;
          claimed_time := p_claimed_at;

          SELECT * INTO existing_dispatch
            FROM platform.collection_submission_dispatch_v2 dispatch
           WHERE dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.dispatch_key=p_dispatch_key;
          IF FOUND THEN
            IF ROW(existing_dispatch.operation_id,
                   existing_dispatch.execution_grant_id,
                   existing_dispatch.grant_revision,
                   existing_dispatch.grant_authority_hash,
                   existing_dispatch.grant_resource_set_hash,
                   existing_dispatch.claim_pub_id,
                   existing_dispatch.owner_handle,
                   existing_dispatch.authority_snapshot_json,
                   existing_dispatch.authority_sha256,
                   existing_dispatch.owner_gateway_revision,
                   existing_dispatch.owner_dispatch_ref,
                   existing_dispatch.owner_wal_evidence_hash,
                   existing_dispatch.prior_send_state_version,
                   existing_dispatch.claimed_at)
               IS DISTINCT FROM
               ROW(p_operation_id,p_execution_grant_id,p_grant_revision,
                   p_expected_grant_hash,p_expected_fence_set_hash,
                   p_claim_pub_id,
                   p_owner_handle,p_authority_snapshot_json,
                   p_expected_authority_hash,
                   p_owner_gateway_revision,p_owner_dispatch_ref,
                   p_owner_wal_evidence_hash,p_expected_send_state_version,
                   p_claimed_at) THEN
              RAISE EXCEPTION 'submission dispatch idempotency payload drifted';
            END IF;
            RETURN QUERY SELECT existing_dispatch.id,
                                existing_dispatch.claim_pub_id,false;
            RETURN;
          END IF;
          IF claimed_time < server_time - interval '5 minutes' THEN
            RAISE EXCEPTION 'new submission claim timestamp is outside clock skew';
          END IF;

          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
           FOR UPDATE;
          IF NOT FOUND OR operation_row.send_state <> 'NOT_SENT' OR
             operation_row.send_state_version <> p_expected_send_state_version OR
             claimed_time < operation_row.prepared_at THEN
            RAISE EXCEPTION 'submission operation is not claimable';
          END IF;

          SELECT * INTO request_row
            FROM platform.collection_submission_request_manifest_v2 manifest
           WHERE manifest.operation_id=p_operation_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'submission claim requires immutable request manifest';
          END IF;

          SELECT * INTO grant_row
            FROM platform.collection_execution_grant_v2 grant_row_source
           WHERE grant_row_source.id=p_execution_grant_id
             AND grant_row_source.tenant_id=p_tenant_id
             AND grant_row_source.project_id=p_project_id
             AND grant_row_source.operation_id=p_operation_id
             AND grant_row_source.grant_revision=p_grant_revision
             AND grant_row_source.grant_hash=p_expected_grant_hash
             AND grant_row_source.grant_state='issued'
             AND grant_row_source.issued_at <= claimed_time
             AND grant_row_source.expires_at > claimed_time
             AND grant_row_source.issued_at <= server_time
             AND grant_row_source.expires_at > server_time
             AND grant_row_source.revoked_at IS NULL
             AND grant_row_source.allowed_actions_json::jsonb ? 'submit_query'
           FOR UPDATE;
          IF NOT FOUND OR grant_row.gateway_protocol_revision IS DISTINCT FROM
             p_owner_gateway_revision THEN
            RAISE EXCEPTION 'exact execution grant authority is unavailable';
          END IF;

          PERFORM 1
            FROM platform.collection_binding_revision_v2 binding
           WHERE binding.id=grant_row.binding_revision_id
             AND binding.tenant_id=p_tenant_id
             AND binding.project_id=p_project_id
             AND binding.lifecycle_state='active'
             AND binding.activated_at IS NOT NULL
             AND binding.activated_at <= claimed_time
             AND binding.effective_from <= claimed_time
             AND (binding.expires_at IS NULL OR binding.expires_at > claimed_time)
             AND binding.activated_at <= server_time
             AND binding.effective_from <= server_time
             AND (binding.expires_at IS NULL OR binding.expires_at > server_time)
             AND binding.suspended_at IS NULL
             AND binding.revoked_at IS NULL
             AND binding.superseded_at IS NULL
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'execution binding is not active at submit claim';
          END IF;

          SELECT * INTO reservation_row
            FROM platform.collection_quota_reservation reservation
           WHERE reservation.id=grant_row.quota_reservation_id
             AND reservation.tenant_id=p_tenant_id
             AND reservation.project_id=p_project_id
             AND reservation.operation_id=p_operation_id
             AND reservation.binding_revision_id=grant_row.binding_revision_id
             AND reservation.quota_registry_id=grant_row.quota_registry_id
             AND reservation.reservation_state='reserved'
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'submission claim requires reserved exact quota set';
          END IF;
          PERFORM platform.assert_collection_quota_reservation_v2(
            p_tenant_id,p_project_id,reservation_row.id
          );
          PERFORM 1
            FROM platform.collection_quota_reservation_effect effect
            JOIN platform.collection_quota_bucket bucket
              ON bucket.id=effect.quota_bucket_id
             AND bucket.tenant_id=effect.tenant_id
             AND bucket.project_id=effect.project_id
           WHERE effect.reservation_id=reservation_row.id
             AND effect.tenant_id=p_tenant_id
             AND effect.project_id=p_project_id
             AND effect.operation_id=p_operation_id
             AND effect.effect_state='reserved'
           ORDER BY CASE bucket.scope_kind
             WHEN 'provider' THEN 0 WHEN 'account' THEN 1
             WHEN 'credential' THEN 2 WHEN 'project' THEN 3
             WHEN 'contract' THEN 4 WHEN 'platform_surface' THEN 5
             WHEN 'mode' THEN 6 ELSE 2147483647 END,bucket.bucket_key
           FOR UPDATE OF effect,bucket;
          GET DIAGNOSTICS resource_count = ROW_COUNT;
          IF resource_count <> reservation_row.expected_effect_count THEN
            RAISE EXCEPTION 'submission claim quota effect set is incomplete';
          END IF;

          PERFORM 1
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
            JOIN platform.resource_registration registration
              ON registration.id=grant_resource.resource_registration_id
             AND registration.tenant_id=grant_resource.tenant_id
             AND registration.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=p_execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id
           ORDER BY grant_resource.resource_role,grant_resource.resource_ordinal
           FOR UPDATE OF lease,capacity,registration;
          GET DIAGNOSTICS resource_count = ROW_COUNT;
          IF resource_count < 1 THEN
            RAISE EXCEPTION 'submission claim requires typed resource owners';
          END IF;

          SELECT count(*) FILTER (WHERE
                   lease.lease_state <> 'active' OR
                   lease.operation_id <> p_operation_id OR
                   lease.binding_revision_id <> grant_row.binding_revision_id OR
                   lease.expires_at <= claimed_time OR
                   lease.expires_at <= server_time OR
                   lease.fencing_token <> grant_resource.fence_generation OR
                   capacity.capacity_state <> 'leased' OR
                   capacity.current_fencing_token <>
                     grant_resource.fence_generation OR
                   registration.state <> 'active' OR
                   registration.revoked_at IS NOT NULL OR
                   registration.opaque_owner_handle <>
                     grant_resource.owner_gateway_handle OR
                   grant_resource.owner_gateway_handle <> p_owner_handle)
            INTO invalid_resource_count
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
            JOIN platform.resource_registration registration
              ON registration.id=grant_resource.resource_registration_id
             AND registration.tenant_id=grant_resource.tenant_id
             AND registration.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=p_execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id;
          calculated_fence_set_hash :=
            platform.collection_dispatch_fence_set_hash_s10(
              p_tenant_id,p_project_id,p_execution_grant_id
            );
          IF invalid_resource_count <> 0 OR calculated_fence_set_hash IS NULL OR
             calculated_fence_set_hash <> p_expected_fence_set_hash THEN
            RAISE EXCEPTION 'resource lease, fence, owner, or authority set drifted';
          END IF;
          PERFORM platform.assert_collection_authority_snapshot_s10(
            p_tenant_id,p_project_id,p_operation_id,p_execution_grant_id,
            grant_row.binding_revision_id,p_grant_revision,
            p_expected_grant_hash,p_owner_handle,p_expected_fence_set_hash,
            p_expected_authority_hash,p_authority_snapshot_json,claimed_time
          );

          calculated_dispatch_hash := encode(public.digest(
            'collection-submission-dispatch-v1' || E'\\n' ||
            p_operation_id::text || E'\\n' || p_execution_grant_id::text || E'\\n' ||
            p_grant_revision::text || E'\\n' || p_expected_grant_hash || E'\\n' ||
            p_expected_fence_set_hash || E'\\n' || p_dispatch_key || E'\\n' ||
            p_owner_gateway_revision || E'\\n' || p_owner_dispatch_ref || E'\\n' ||
            p_owner_wal_evidence_hash,'sha256'),'hex');
          new_dispatch_id := gen_random_uuid();
          new_dispatch_pub_id :=
            'sdp_' || substr(replace(new_dispatch_id::text,'-',''),1,26);
          INSERT INTO platform.collection_submission_dispatch_v2 (
            id,pub_id,tenant_id,project_id,operation_id,request_manifest_id,
            execution_grant_id,grant_revision,grant_authority_hash,
            binding_revision_id,quota_registry_id,quota_reservation_id,
            schema_version,claim_pub_id,owner_handle,authority_sha256,
            authority_snapshot_json,
            dispatch_key,owner_gateway_revision,
            owner_dispatch_ref,owner_wal_evidence_hash,grant_resource_set_hash,
            dispatch_hash,prior_send_state_version,sending_send_state_version,
            owner_execution_state,reconciliation_state,reconciliation_version,
            readiness_evidence_ref,readiness_evidence_hash,
            reconciliation_claim_ref,reconciliation_claim_hash,reconcile_after,
            reconciliation_ready_at,reconciliation_claimed_at,
            reconciliation_resolved_at,
            claimed_at
          ) VALUES (
            new_dispatch_id,
            new_dispatch_pub_id,
            p_tenant_id,p_project_id,p_operation_id,request_row.id,
            p_execution_grant_id,p_grant_revision,p_expected_grant_hash,
            grant_row.binding_revision_id,grant_row.quota_registry_id,
            grant_row.quota_reservation_id,'collection-submission-dispatch-v1',
            p_claim_pub_id,p_owner_handle,p_expected_authority_hash,
            p_authority_snapshot_json,
            p_dispatch_key,p_owner_gateway_revision,p_owner_dispatch_ref,
            p_owner_wal_evidence_hash,p_expected_fence_set_hash,
            calculated_dispatch_hash,p_expected_send_state_version,
            p_expected_send_state_version+1,'active','not_required',1,
            NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,claimed_time
          );

          UPDATE platform.collection_submission_operation
             SET send_state='SENDING',
                 send_state_version=send_state_version+1,
                 send_started_at=claimed_time,send_resolved_at=NULL,
                 reconciliation_state='not_required',reconcile_after=NULL,
                 state_reason='dispatch_claimed',version=version+1,
                 updated_at=claimed_time
           WHERE id=p_operation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id AND send_state='NOT_SENT'
             AND send_state_version=p_expected_send_state_version;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'submission claim compare-and-swap lost';
          END IF;

          transition_key := 'trn_' || substr(encode(public.digest(
            p_dispatch_key || '|claim','sha256'),'hex'),1,60);
          transition_hash := encode(public.digest(
            'collection-submission-transition-v1' || E'\\n' ||
            p_operation_id::text || E'\\nNOT_SENT\\nSENDING\\n' ||
            p_expected_send_state_version::text || E'\\n' ||
            (p_expected_send_state_version+1)::text || E'\\n' ||
            p_owner_wal_evidence_hash,'sha256'),'hex');
          new_transition_id := gen_random_uuid();
          INSERT INTO platform.collection_submission_transition_evidence_v2 (
            id,pub_id,tenant_id,project_id,operation_id,dispatch_id,
            execution_grant_id,
            schema_version,transition_key,evidence_kind,terminal_reason,
            evidence_state,
            from_send_state,to_send_state,from_send_state_version,
            to_send_state_version,owner_gateway_revision,owner_dispatch_ref,
            evidence_ref,evidence_hash,provider_reference_ref,
            terminated_fence_set_hash,reconciliation_proof_id,
            provider_idempotency_key_hash,
            transition_hash,reason_code,
            recorded_by,recorded_at
          ) VALUES (
            new_transition_id,
            'ste_' || substr(replace(new_transition_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,new_dispatch_id,
            p_execution_grant_id,
            'collection-submission-transition-v1',transition_key,
            'dispatch_claimed','dispatch_claimed','accepted',
            'NOT_SENT','SENDING',
            p_expected_send_state_version,p_expected_send_state_version+1,
            p_owner_gateway_revision,p_owner_dispatch_ref,p_owner_dispatch_ref,
            p_owner_wal_evidence_hash,NULL,NULL,NULL,
            request_row.provider_idempotency_key_hash,transition_hash,
            'dispatch_claimed',caller_role,claimed_time
          );
          RETURN QUERY SELECT new_dispatch_id,p_claim_pub_id,true;
        END
        $$
        """
    )


def _create_operation_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.create_collection_submission_operation_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_pub_id text,
          p_operation_generation integer,
          p_operation_key text,
          p_operation_policy_revision text,
          p_prepared_at timestamptz,
          p_slot_pub_id text,
          p_logical_item_key text,
          p_campaign_pub_id text,
          p_target_key text,
          p_leg_key text,
          p_platform text,
          p_collection_surface text,
          p_product_variant text
        ) RETURNS TABLE(operation_id uuid,created boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        SET row_security = on
        SET timezone = 'UTC'
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          slot_row record;
          existing_operation platform.collection_submission_operation%ROWTYPE;
          inserted_operation_id uuid;
          canonical_operation_material text;
          expected_operation_key text;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'submission operation caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'submission operation tenant context mismatch';
          END IF;
          IF p_operation_pub_id !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$' OR
             p_operation_generation<1 OR
             p_operation_key IS NULL OR
             octet_length(p_operation_key) NOT BETWEEN 1 AND 1800 OR
             p_operation_policy_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_prepared_at IS NULL OR
             p_prepared_at>CURRENT_TIMESTAMP+interval '30 seconds' OR
             p_slot_pub_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$' OR
             p_logical_item_key IS NULL OR
             octet_length(p_logical_item_key) NOT BETWEEN 1 AND 1500 OR
             p_campaign_pub_id !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$' OR
             p_target_key IS NULL OR
             octet_length(p_target_key) NOT BETWEEN 1 AND 500 OR
             p_leg_key IS NULL OR octet_length(p_leg_key) NOT BETWEEN 1 AND 1000 OR
             p_platform IS NULL OR octet_length(p_platform) NOT BETWEEN 1 AND 128 OR
             p_collection_surface NOT IN
               ('provider_api','consumer_web','consumer_app') OR
             p_product_variant IS NULL OR
             octet_length(p_product_variant) NOT BETWEEN 1 AND 128 THEN
            RAISE EXCEPTION 'submission operation input is invalid';
          END IF;

          canonical_operation_material :=
            '{"campaign_pub_id":' || to_jsonb(p_campaign_pub_id)::text ||
            ',"generation":' || p_operation_generation::text ||
            ',"leg_key":' || to_jsonb(p_leg_key)::text ||
            ',"logical_item_key":' || to_jsonb(p_logical_item_key)::text ||
            ',"operation_policy_revision":' ||
              to_jsonb(p_operation_policy_revision)::text ||
            ',"project_id":' || to_jsonb(p_project_id::text)::text ||
            ',"protocol_version":"collection-submission-v1"' ||
            ',"slot_pub_id":' || to_jsonb(p_slot_pub_id)::text ||
            ',"target_key":' || to_jsonb(p_target_key)::text ||
            ',"tenant_id":' || to_jsonb(p_tenant_id::text)::text || '}';
          expected_operation_key := 'operation-v1-' || encode(
            public.digest(canonical_operation_material,'sha256'),'hex'
          );
          IF p_operation_key IS DISTINCT FROM expected_operation_key THEN
            RAISE EXCEPTION 'submission operation key is not deterministic';
          END IF;

          SELECT slot.id AS primary_slot_id,slot.tenant_id,slot.project_id,
                 slot.campaign_id,slot.campaign_target_id,slot.sampling_leg_id,
                 slot.slot_key,slot.platform,slot.collection_surface,
                 slot.product_variant,slot.province_code,slot.interaction_mode
            INTO slot_row
            FROM platform.collection_primary_slot slot
            JOIN platform.collection_campaign campaign
              ON campaign.id=slot.campaign_id
             AND campaign.tenant_id=slot.tenant_id
             AND campaign.project_id=slot.project_id
            JOIN platform.collection_campaign_target target
              ON target.id=slot.campaign_target_id
             AND target.tenant_id=slot.tenant_id
             AND target.project_id=slot.project_id
             AND target.campaign_id=slot.campaign_id
            JOIN platform.collection_sampling_leg leg
              ON leg.id=slot.sampling_leg_id
             AND leg.tenant_id=slot.tenant_id
             AND leg.project_id=slot.project_id
             AND leg.campaign_id=slot.campaign_id
             AND leg.campaign_target_id=slot.campaign_target_id
           WHERE slot.tenant_id=p_tenant_id
             AND slot.project_id=p_project_id
             AND slot.pub_id=p_slot_pub_id
             AND slot.slot_role='primary'
             AND slot.slot_key=p_logical_item_key
             AND campaign.pub_id=p_campaign_pub_id
             AND campaign.state='frozen'
             AND campaign.materialization_state='complete'
             AND campaign.expected_slot_count>0
             AND campaign.materialized_slot_count=campaign.expected_slot_count
             AND campaign.materialization_cursor=campaign.expected_slot_count
             AND campaign.membership_hash ~ '^[0-9a-f]{64}$'
             AND target.target_key=p_target_key
             AND leg.leg_key=p_leg_key
             AND slot.platform=p_platform
             AND slot.collection_surface=p_collection_surface
             AND slot.product_variant=p_product_variant
           FOR SHARE OF slot,campaign,target,leg;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'submission operation requires exact frozen primary slot';
          END IF;

          SELECT operation.* INTO existing_operation
            FROM platform.collection_submission_operation operation
           WHERE operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
             AND (
               operation.pub_id=p_operation_pub_id OR
               operation.operation_key=p_operation_key OR
               (operation.primary_slot_id=slot_row.primary_slot_id AND
                operation.operation_generation=p_operation_generation)
             )
           ORDER BY CASE WHEN operation.operation_key=p_operation_key THEN 0 ELSE 1 END
           LIMIT 1
           FOR UPDATE;
          IF NOT FOUND THEN
            inserted_operation_id := gen_random_uuid();
            INSERT INTO platform.collection_submission_operation (
              id,pub_id,tenant_id,project_id,campaign_id,campaign_target_id,
              sampling_leg_id,primary_slot_id,slot_key,platform,
              collection_surface,product_variant,province_code,interaction_mode,
              operation_generation,operation_key,operation_policy_revision,
              send_state,send_state_version,prepared_at,reconciliation_state,
              reconcile_after,state_reason
            ) VALUES (
              inserted_operation_id,p_operation_pub_id,p_tenant_id,p_project_id,
              slot_row.campaign_id,slot_row.campaign_target_id,
              slot_row.sampling_leg_id,slot_row.primary_slot_id,slot_row.slot_key,
              slot_row.platform,slot_row.collection_surface,slot_row.product_variant,
              slot_row.province_code,slot_row.interaction_mode,
              p_operation_generation,p_operation_key,p_operation_policy_revision,
              'NOT_SENT',1,p_prepared_at,'not_required',NULL,
              'submission_v2_preparation_pending'
            )
            ON CONFLICT DO NOTHING
            RETURNING id INTO operation_id;
            IF operation_id IS NOT NULL THEN
              RETURN QUERY SELECT operation_id,true;
              RETURN;
            END IF;
            SELECT operation.* INTO existing_operation
              FROM platform.collection_submission_operation operation
             WHERE operation.tenant_id=p_tenant_id
               AND operation.project_id=p_project_id
               AND (
                 operation.pub_id=p_operation_pub_id OR
                 operation.operation_key=p_operation_key OR
                 (operation.primary_slot_id=slot_row.primary_slot_id AND
                  operation.operation_generation=p_operation_generation)
               )
             ORDER BY CASE WHEN operation.operation_key=p_operation_key THEN 0 ELSE 1 END
             LIMIT 1
             FOR UPDATE;
          END IF;
          IF existing_operation.id IS NULL OR ROW(
               existing_operation.pub_id,existing_operation.tenant_id,
               existing_operation.project_id,existing_operation.campaign_id,
               existing_operation.campaign_target_id,
               existing_operation.sampling_leg_id,
               existing_operation.primary_slot_id,existing_operation.slot_key,
               existing_operation.platform,existing_operation.collection_surface,
               existing_operation.product_variant,existing_operation.province_code,
               existing_operation.interaction_mode,
               existing_operation.operation_generation,
               existing_operation.operation_key,
               existing_operation.operation_policy_revision,
               existing_operation.prepared_at
             ) IS DISTINCT FROM ROW(
               p_operation_pub_id,p_tenant_id,p_project_id,slot_row.campaign_id,
               slot_row.campaign_target_id,slot_row.sampling_leg_id,
               slot_row.primary_slot_id,slot_row.slot_key,slot_row.platform,
               slot_row.collection_surface,slot_row.product_variant,
               slot_row.province_code,slot_row.interaction_mode,
               p_operation_generation,p_operation_key,
               p_operation_policy_revision,p_prepared_at
             ) THEN
            RAISE EXCEPTION 'submission operation exact replay drifted';
          END IF;
          RETURN QUERY SELECT existing_operation.id,false;
        END
        $$
        """
    )


def _create_prepare_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.prepare_collection_submission_request_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_expected_send_state_version integer,
          p_request_payload_hash text,
          p_request_manifest_hash text,
          p_request_protocol_revision text,
          p_adapter_request_revision text,
          p_request_content_ref text,
          p_provider_idempotency_key_hash text,
          p_prepared_by_pub_id text,
          p_prepared_at timestamptz
        ) RETURNS TABLE(
          request_manifest_id uuid,
          capture_truth_id uuid,
          prepared boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          reservation_row record;
          existing_manifest record;
          new_manifest_id uuid;
          new_truth_id uuid;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'submission prepare caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'submission prepare tenant context mismatch';
          END IF;
          IF p_expected_send_state_version <> 1 OR
             p_request_payload_hash !~ '^[0-9a-f]{64}$' OR
             p_request_manifest_hash !~ '^[0-9a-f]{64}$' OR
             p_provider_idempotency_key_hash !~ '^[0-9a-f]{64}$' OR
             p_request_protocol_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_adapter_request_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_request_content_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_prepared_by_pub_id !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_prepared_at IS NULL OR
             p_prepared_at > CURRENT_TIMESTAMP + interval '30 seconds' THEN
            RAISE EXCEPTION 'submission prepare input is invalid';
          END IF;

          SELECT * INTO existing_manifest
            FROM platform.collection_submission_request_manifest_v2 manifest
           WHERE manifest.operation_id=p_operation_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id;
          IF FOUND THEN
            IF ROW(existing_manifest.request_payload_hash,
                   existing_manifest.request_manifest_hash,
                   existing_manifest.request_protocol_revision,
                   existing_manifest.adapter_request_revision,
                   existing_manifest.request_content_ref,
                   existing_manifest.provider_idempotency_key_hash,
                   existing_manifest.prepared_by_pub_id,
                   existing_manifest.prepared_at)
               IS DISTINCT FROM
               ROW(p_request_payload_hash,p_request_manifest_hash,
                   p_request_protocol_revision,p_adapter_request_revision,
                   p_request_content_ref,p_provider_idempotency_key_hash,
                   p_prepared_by_pub_id,p_prepared_at) THEN
              RAISE EXCEPTION 'request manifest idempotency payload drifted';
            END IF;
            SELECT id INTO STRICT new_truth_id
              FROM platform.collection_capture_truth_v2 truth
             WHERE truth.request_manifest_id=existing_manifest.id
               AND truth.tenant_id=p_tenant_id
               AND truth.project_id=p_project_id;
            RETURN QUERY SELECT existing_manifest.id,new_truth_id,false;
            RETURN;
          END IF;

          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
           FOR UPDATE;
          IF NOT FOUND OR operation_row.send_state<>'NOT_SENT' OR
             operation_row.send_state_version<>p_expected_send_state_version OR
             operation_row.prepared_at>p_prepared_at THEN
            RAISE EXCEPTION 'submission operation is not preparable';
          END IF;
          SELECT * INTO reservation_row
            FROM platform.collection_quota_reservation reservation
           WHERE reservation.operation_id=p_operation_id
             AND reservation.tenant_id=p_tenant_id
             AND reservation.project_id=p_project_id
             AND reservation.reservation_state='reserved'
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'submission prepare requires reserved quota';
          END IF;
          PERFORM platform.assert_collection_quota_reservation_v2(
            p_tenant_id,p_project_id,reservation_row.id
          );

          new_manifest_id := gen_random_uuid();
          INSERT INTO platform.collection_submission_request_manifest_v2 (
            id,pub_id,tenant_id,project_id,operation_id,schema_version,
            request_payload_hash,request_manifest_hash,
            request_protocol_revision,adapter_request_revision,
            request_content_ref,provider_idempotency_key_hash,
            prepared_by_pub_id,prepared_at
          ) VALUES (
            new_manifest_id,
            'srm_' || substr(replace(new_manifest_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,
            'collection-request-manifest-v1',p_request_payload_hash,
            p_request_manifest_hash,p_request_protocol_revision,
            p_adapter_request_revision,p_request_content_ref,
            p_provider_idempotency_key_hash,p_prepared_by_pub_id,p_prepared_at
          );
          SELECT id INTO STRICT new_truth_id
            FROM platform.collection_capture_truth_v2 truth
           WHERE truth.request_manifest_id=new_manifest_id
             AND truth.tenant_id=p_tenant_id
             AND truth.project_id=p_project_id;
          UPDATE platform.collection_submission_operation operation
             SET state_reason='submission_prepared',
                 version=operation.version+1,
                 updated_at=CURRENT_TIMESTAMP
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
             AND operation.state_reason='submission_v2_preparation_pending';
          RETURN QUERY SELECT new_manifest_id,new_truth_id,true;
        END
        $$
        """
    )


def _create_dispatch_freshness_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.collection_dispatch_fence_set_hash_s10(
          p_tenant_id uuid,
          p_project_id uuid,
          p_execution_grant_id uuid
        ) RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
          SELECT encode(public.digest(
            '{"fences":[' || string_agg(
              '{"binding_resource_pub_id":"' ||
                grant_resource.resource_pub_id ||
              '","generation":' || grant_resource.fence_generation::text ||
              ',"lease_pub_id":"' || lease.pub_id ||
              '","owner_handle":"' || grant_resource.owner_gateway_handle ||
              '","resource_role":"' || grant_resource.resource_role || '"}',
              ',' ORDER BY grant_resource.resource_role,
                grant_resource.resource_pub_id,lease.pub_id
            ) || '],"version":"lease-fence-identity-v1"}',
            'sha256'),'hex')
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=p_execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.assert_collection_authority_snapshot_s10(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_execution_grant_id uuid,
          p_binding_revision_id uuid,
          p_expected_grant_revision integer,
          p_expected_grant_hash text,
          p_expected_owner_handle text,
          p_expected_fence_set_hash text,
          p_expected_authority_sha256 text,
          p_authority_snapshot_json text,
          p_authority_use_at timestamptz
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          authority_payload jsonb;
          grant_row record;
          canonical_lease_fences text;
          canonical_authority_json text;
          authoritative_resource_count integer;
          snapshot_resource_count integer;
          unique_snapshot_resource_count integer;
          invalid_snapshot_resource_count integer;
          server_time timestamptz := CURRENT_TIMESTAMP;
        BEGIN
          IF p_expected_grant_revision<1 OR
             p_expected_grant_hash !~ '^[0-9a-f]{64}$' OR
             p_expected_owner_handle !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_expected_fence_set_hash !~ '^[0-9a-f]{64}$' OR
             p_expected_authority_sha256 !~ '^[0-9a-f]{64}$' OR
             p_authority_snapshot_json IS NULL OR
             octet_length(p_authority_snapshot_json) NOT BETWEEN 2 AND 16384 OR
             p_authority_snapshot_json ~*
               '"(secret|password|cookie|authorization|proxy_url|endpoint)"[[:space:]]*:' OR
             p_authority_use_at IS NULL OR
             p_authority_use_at>server_time+interval '30 seconds' THEN
            RAISE EXCEPTION 'authority snapshot input is invalid';
          END IF;
          BEGIN
            authority_payload := p_authority_snapshot_json::jsonb;
          EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'authority snapshot JSON is invalid';
          END;
          IF jsonb_typeof(authority_payload)<>'object' OR
             NOT (authority_payload ?& ARRAY[
               'binding_revision_pub_id','checked_at','fence_set_sha256',
               'grant_pub_id','grant_revision','lease_fences','owner_handle',
               'valid_until'
             ]) OR
             authority_payload - ARRAY[
               'binding_revision_pub_id','checked_at','fence_set_sha256',
               'grant_pub_id','grant_revision','lease_fences','owner_handle',
               'valid_until'
             ] <> '{}'::jsonb OR
             jsonb_typeof(authority_payload->'lease_fences')<>'array' OR
             jsonb_array_length(authority_payload->'lease_fences')
               NOT BETWEEN 1 AND 32 OR
             authority_payload->>'owner_handle' IS DISTINCT FROM
               p_expected_owner_handle OR
             authority_payload->>'fence_set_sha256' IS DISTINCT FROM
               p_expected_fence_set_hash OR
             (authority_payload->>'grant_revision')::integer IS DISTINCT FROM
               p_expected_grant_revision OR
             (authority_payload->>'checked_at')::timestamptz >
               p_authority_use_at OR
             p_authority_use_at >=
               (authority_payload->>'valid_until')::timestamptz OR
             (authority_payload->>'checked_at')::timestamptz >
               server_time+interval '30 seconds' OR
             (authority_payload->>'valid_until')::timestamptz<=server_time THEN
            RAISE EXCEPTION 'authority snapshot envelope is invalid';
          END IF;

          SELECT grant_source.pub_id AS grant_pub_id,
                 grant_source.grant_revision,grant_source.expires_at,
                 binding.pub_id AS binding_revision_pub_id,
                 binding.expires_at AS binding_expires_at
            INTO grant_row
            FROM platform.collection_execution_grant_v2 grant_source
            JOIN platform.collection_binding_revision_v2 binding
              ON binding.id=grant_source.binding_revision_id
             AND binding.tenant_id=grant_source.tenant_id
             AND binding.project_id=grant_source.project_id
           WHERE grant_source.id=p_execution_grant_id
             AND grant_source.tenant_id=p_tenant_id
             AND grant_source.project_id=p_project_id
             AND grant_source.operation_id=p_operation_id
             AND grant_source.binding_revision_id=p_binding_revision_id
             AND grant_source.grant_revision=p_expected_grant_revision
             AND grant_source.grant_hash=p_expected_grant_hash
             AND grant_source.grant_state='issued'
             AND grant_source.issued_at<=p_authority_use_at
             AND grant_source.expires_at>p_authority_use_at
             AND grant_source.issued_at<=server_time
             AND grant_source.expires_at>server_time
             AND grant_source.revoked_at IS NULL
             AND binding.lifecycle_state='active'
             AND binding.activated_at IS NOT NULL
             AND binding.activated_at<=p_authority_use_at
             AND binding.effective_from<=p_authority_use_at
             AND (binding.expires_at IS NULL OR
                  binding.expires_at>p_authority_use_at)
             AND binding.activated_at<=server_time
             AND binding.effective_from<=server_time
             AND (binding.expires_at IS NULL OR binding.expires_at>server_time)
             AND binding.suspended_at IS NULL
             AND binding.revoked_at IS NULL
             AND binding.superseded_at IS NULL
           FOR KEY SHARE OF grant_source,binding;
          IF NOT FOUND OR
             authority_payload->>'grant_pub_id' IS DISTINCT FROM
               grant_row.grant_pub_id OR
             authority_payload->>'binding_revision_pub_id' IS DISTINCT FROM
               grant_row.binding_revision_pub_id OR
             (authority_payload->>'valid_until')::timestamptz>
               grant_row.expires_at OR
             (grant_row.binding_expires_at IS NOT NULL AND
              (authority_payload->>'valid_until')::timestamptz>
                grant_row.binding_expires_at) THEN
            RAISE EXCEPTION 'authority snapshot grant or binding drifted';
          END IF;

          PERFORM 1
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
            JOIN platform.resource_registration registration
              ON registration.id=grant_resource.resource_registration_id
             AND registration.tenant_id=grant_resource.tenant_id
             AND registration.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=p_execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id
           ORDER BY grant_resource.resource_role,grant_resource.resource_ordinal
           FOR KEY SHARE OF grant_resource,lease,capacity,registration;
          GET DIAGNOSTICS authoritative_resource_count=ROW_COUNT;
          IF authoritative_resource_count NOT BETWEEN 1 AND 32 THEN
            RAISE EXCEPTION 'authority snapshot resource set is not bounded';
          END IF;

          SELECT count(*),
                 count(DISTINCT ROW(
                   fence.value->>'lease_pub_id',
                   fence.value->>'binding_resource_pub_id',
                   fence.value->>'resource_role',
                   fence.value->>'owner_handle',
                   fence.value->>'generation'
                 )),
                 count(*) FILTER (WHERE
                   jsonb_typeof(fence.value)<>'object' OR
                   NOT (fence.value ?& ARRAY[
                     'acquired_at','binding_resource_pub_id','expires_at',
                     'generation','lease_pub_id','owner_handle','resource_role'
                   ]) OR
                   fence.value - ARRAY[
                     'acquired_at','binding_resource_pub_id','expires_at',
                     'generation','lease_pub_id','owner_handle','resource_role'
                   ] <> '{}'::jsonb OR
                   NOT EXISTS (
                     SELECT 1
                       FROM platform.collection_execution_grant_resource
                         grant_resource
                       JOIN platform.resource_lease lease
                         ON lease.id=grant_resource.resource_lease_id
                        AND lease.tenant_id=grant_resource.tenant_id
                        AND lease.project_id=grant_resource.project_id
                       JOIN platform.collection_resource_capacity_unit capacity
                         ON capacity.id=grant_resource.capacity_unit_id
                        AND capacity.tenant_id=grant_resource.tenant_id
                        AND capacity.project_id=grant_resource.project_id
                       JOIN platform.resource_registration registration
                         ON registration.id=grant_resource.resource_registration_id
                        AND registration.tenant_id=grant_resource.tenant_id
                        AND registration.project_id=grant_resource.project_id
                      WHERE grant_resource.execution_grant_id=p_execution_grant_id
                        AND grant_resource.tenant_id=p_tenant_id
                        AND grant_resource.project_id=p_project_id
                        AND lease.pub_id=fence.value->>'lease_pub_id'
                        AND grant_resource.resource_pub_id=
                              fence.value->>'binding_resource_pub_id'
                        AND grant_resource.resource_role=
                              fence.value->>'resource_role'
                        AND grant_resource.owner_gateway_handle=
                              fence.value->>'owner_handle'
                        AND grant_resource.owner_gateway_handle=
                              p_expected_owner_handle
                        AND grant_resource.fence_generation=
                              (fence.value->>'generation')::bigint
                        AND lease.acquired_at=
                              (fence.value->>'acquired_at')::timestamptz
                        AND lease.expires_at=
                              (fence.value->>'expires_at')::timestamptz
                        AND lease.lease_state='active'
                        AND lease.operation_id=p_operation_id
                        AND lease.binding_revision_id=p_binding_revision_id
                        AND lease.acquired_at<=
                              (authority_payload->>'checked_at')::timestamptz
                        AND lease.expires_at>=
                              (authority_payload->>'valid_until')::timestamptz
                        AND lease.expires_at>server_time
                        AND capacity.capacity_state='leased'
                        AND capacity.current_fencing_token=
                              grant_resource.fence_generation
                        AND registration.state='active'
                        AND registration.revoked_at IS NULL
                        AND registration.opaque_owner_handle=
                              p_expected_owner_handle
                   ))
            INTO snapshot_resource_count,unique_snapshot_resource_count,
                 invalid_snapshot_resource_count
            FROM jsonb_array_elements(
              authority_payload->'lease_fences'
            ) AS fence(value);
          IF snapshot_resource_count<>authoritative_resource_count OR
             unique_snapshot_resource_count<>authoritative_resource_count OR
             invalid_snapshot_resource_count<>0 OR
             platform.collection_dispatch_fence_set_hash_s10(
               p_tenant_id,p_project_id,p_execution_grant_id
             )<>p_expected_fence_set_hash THEN
            RAISE EXCEPTION 'authority snapshot resource or fence set drifted';
          END IF;

          SELECT string_agg(
                   '{"acquired_at":"' || (fence.value->>'acquired_at') ||
                   '","binding_resource_pub_id":"' ||
                     (fence.value->>'binding_resource_pub_id') ||
                   '","expires_at":"' || (fence.value->>'expires_at') ||
                   '","generation":' ||
                     (fence.value->>'generation')::bigint::text ||
                   ',"lease_pub_id":"' || (fence.value->>'lease_pub_id') ||
                   '","owner_handle":"' || (fence.value->>'owner_handle') ||
                   '","resource_role":"' || (fence.value->>'resource_role') || '"}',
                   ',' ORDER BY fence.ordinality
                 )
            INTO canonical_lease_fences
            FROM jsonb_array_elements(authority_payload->'lease_fences')
              WITH ORDINALITY AS fence(value,ordinality);
          canonical_authority_json :=
            '{"binding_revision_pub_id":"' ||
              (authority_payload->>'binding_revision_pub_id') ||
            '","checked_at":"' || (authority_payload->>'checked_at') ||
            '","fence_set_sha256":"' ||
              (authority_payload->>'fence_set_sha256') ||
            '","grant_pub_id":"' || (authority_payload->>'grant_pub_id') ||
            '","grant_revision":' ||
              (authority_payload->>'grant_revision')::integer::text ||
            ',"lease_fences":[' || canonical_lease_fences ||
            '],"owner_handle":"' || (authority_payload->>'owner_handle') ||
            '","valid_until":"' || (authority_payload->>'valid_until') || '"}';
          IF canonical_authority_json<>p_authority_snapshot_json OR
             encode(public.digest(canonical_authority_json,'sha256'),'hex')<>
               p_expected_authority_sha256 THEN
            RAISE EXCEPTION 'authority snapshot canonical digest drifted';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.assert_collection_dispatch_fresh_s10(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_expected_fence_set_hash text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          dispatch_row record;
          invalid_resource_count integer;
          resource_count integer;
          calculated_fence_set_hash text;
          checked_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
          IF p_expected_fence_set_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'capture fence-set hash is invalid';
          END IF;
          SELECT dispatch.*,operation.send_state,grant_source.grant_state,
                 grant_source.expires_at AS grant_expires_at,
                 grant_source.revoked_at AS grant_revoked_at
            INTO dispatch_row
            FROM platform.collection_submission_dispatch_v2 dispatch
            JOIN platform.collection_submission_operation operation
              ON operation.id=dispatch.operation_id
             AND operation.tenant_id=dispatch.tenant_id
             AND operation.project_id=dispatch.project_id
            JOIN platform.collection_execution_grant_v2 grant_source
              ON grant_source.id=dispatch.execution_grant_id
             AND grant_source.tenant_id=dispatch.tenant_id
             AND grant_source.project_id=dispatch.project_id
             AND grant_source.operation_id=dispatch.operation_id
             AND grant_source.grant_revision=dispatch.grant_revision
             AND grant_source.grant_hash=dispatch.grant_authority_hash
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.operation_id=p_operation_id
             AND dispatch.grant_resource_set_hash=p_expected_fence_set_hash
           FOR KEY SHARE OF dispatch,operation,grant_source;
          IF NOT FOUND OR dispatch_row.send_state NOT IN
               ('SENDING','CONFIRMED_SENT','SEND_UNKNOWN') OR
             dispatch_row.grant_state <> 'issued' OR
             dispatch_row.grant_expires_at <= checked_at OR
             dispatch_row.grant_revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'capture dispatch authority is unavailable';
          END IF;

          PERFORM 1
            FROM platform.collection_binding_revision_v2 binding
           WHERE binding.id=dispatch_row.binding_revision_id
             AND binding.tenant_id=p_tenant_id
             AND binding.project_id=p_project_id
             AND binding.lifecycle_state='active'
             AND binding.activated_at IS NOT NULL
             AND binding.activated_at <= checked_at
             AND binding.effective_from <= checked_at
             AND (binding.expires_at IS NULL OR binding.expires_at > checked_at)
             AND binding.suspended_at IS NULL
             AND binding.revoked_at IS NULL
             AND binding.superseded_at IS NULL
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture binding authority is not active';
          END IF;

          PERFORM 1
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
            JOIN platform.resource_registration registration
              ON registration.id=grant_resource.resource_registration_id
             AND registration.tenant_id=grant_resource.tenant_id
             AND registration.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=dispatch_row.execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id
           ORDER BY grant_resource.resource_role,grant_resource.resource_ordinal
           FOR UPDATE OF lease,capacity,registration;
          GET DIAGNOSTICS resource_count = ROW_COUNT;
          IF resource_count < 1 THEN
            RAISE EXCEPTION 'capture dispatch has no typed resource authority';
          END IF;

          SELECT count(*) FILTER (WHERE
                   lease.lease_state <> 'active' OR
                   lease.operation_id <> p_operation_id OR
                   lease.binding_revision_id <> dispatch_row.binding_revision_id OR
                   lease.expires_at <= checked_at OR
                   lease.fencing_token <> grant_resource.fence_generation OR
                   capacity.capacity_state <> 'leased' OR
                   capacity.current_fencing_token <>
                     grant_resource.fence_generation OR
                   registration.state <> 'active' OR
                   registration.revoked_at IS NOT NULL OR
                   registration.opaque_owner_handle <>
                     grant_resource.owner_gateway_handle)
            INTO invalid_resource_count
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
            JOIN platform.resource_registration registration
              ON registration.id=grant_resource.resource_registration_id
             AND registration.tenant_id=grant_resource.tenant_id
             AND registration.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=dispatch_row.execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id;
          calculated_fence_set_hash :=
            platform.collection_dispatch_fence_set_hash_s10(
              p_tenant_id,p_project_id,dispatch_row.execution_grant_id
            );
          IF invalid_resource_count <> 0 OR calculated_fence_set_hash IS NULL OR
             calculated_fence_set_hash <> p_expected_fence_set_hash THEN
            RAISE EXCEPTION 'capture resource lease, fence, owner, or mapping drifted';
          END IF;
        END
        $$
        """
    )


def _create_capture_attempt_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.begin_collection_capture_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_expected_capture_state_version integer,
          p_expected_fence_set_hash text,
          p_expected_authority_sha256 text,
          p_owner_handle text,
          p_capture_attempt_ref text,
          p_capture_policy_revision text,
          p_capture_request_sha256 text,
          p_capture_command_json text,
          p_requested_at timestamptz
        ) RETURNS TABLE(
          capture_state_version integer,
          capture_attempt_ordinal integer,
          attempt_acquired boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          truth_row record;
          current_manifest record;
          dispatch_row record;
          command_payload jsonb;
          canonical_lease_fences text;
          canonical_authority_json text;
          canonical_operation_json text;
          canonical_surface_json text;
          canonical_staging_intent_basis text;
          canonical_staging_intent_json text;
          canonical_command_json text;
          calculated_authority_sha256 text;
          calculated_staging_intent_sha256 text;
          authoritative_resource_count integer;
          command_resource_count integer;
          unique_command_resource_count integer;
          invalid_command_resource_count integer;
          transition_time timestamptz := CURRENT_TIMESTAMP;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'capture attempt caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'capture attempt tenant context mismatch';
          END IF;
          IF p_expected_capture_state_version < 1 OR
             p_expected_fence_set_hash !~ '^[0-9a-f]{64}$' OR
             p_expected_authority_sha256 !~ '^[0-9a-f]{64}$' OR
             p_capture_request_sha256 !~ '^[0-9a-f]{64}$' OR
             p_owner_handle !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_capture_attempt_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_capture_policy_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_capture_command_json IS NULL OR
             octet_length(p_capture_command_json) NOT BETWEEN 2 AND 32768 OR
             p_capture_command_json ~*
               '"(secret|password|cookie|authorization|proxy_url|endpoint)"[[:space:]]*:' OR
             p_requested_at IS NULL OR
             p_requested_at > transition_time + interval '30 seconds' THEN
            RAISE EXCEPTION 'capture attempt input is invalid';
          END IF;
          BEGIN
            command_payload := p_capture_command_json::jsonb;
          EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'capture command JSON is invalid';
          END;
          IF jsonb_typeof(command_payload)<>'object' OR
             NOT (command_payload ?& ARRAY[
               'attempt_ref','authority','authority_sha256',
               'capture_policy_revision','expected_capture_version','operation',
               'requested_at','requested_surface_product','source_send_state',
               'staging_intent'
             ]) OR
             command_payload - ARRAY[
               'attempt_ref','authority','authority_sha256',
               'capture_policy_revision','expected_capture_version','operation',
               'requested_at','requested_surface_product','source_send_state',
               'staging_intent'
             ] <> '{}'::jsonb OR
             jsonb_typeof(command_payload->'operation')<>'object' OR
             NOT ((command_payload->'operation') ?& ARRAY[
               'generation','operation_key','operation_pub_id','protocol_version',
               'provider_idempotency_key','request_manifest_sha256'
             ]) OR
             (command_payload->'operation') - ARRAY[
               'generation','operation_key','operation_pub_id','protocol_version',
               'provider_idempotency_key','request_manifest_sha256'
             ] <> '{}'::jsonb OR
             jsonb_typeof(command_payload->'requested_surface_product')<>'object' OR
             NOT ((command_payload->'requested_surface_product') ?& ARRAY[
               'collection_surface','platform','product_variant','target_key'
             ]) OR
             (command_payload->'requested_surface_product') - ARRAY[
               'collection_surface','platform','product_variant','target_key'
             ] <> '{}'::jsonb OR
             jsonb_typeof(command_payload->'staging_intent')<>'object' OR
             NOT ((command_payload->'staging_intent') ?& ARRAY[
               'object_ref','staging_key'
             ]) OR
             (command_payload->'staging_intent') - ARRAY[
               'object_ref','staging_key'
             ] <> '{}'::jsonb OR
             command_payload#>>'{staging_intent,object_ref}' !~
               '^capture-object-v1-[0-9a-f]{64}$' OR
             command_payload#>>'{staging_intent,staging_key}' !~
               '^capture-staging-v1-[0-9a-f]{64}$' OR
             jsonb_typeof(command_payload->'authority')<>'object' OR
             NOT ((command_payload->'authority') ?& ARRAY[
               'binding_revision_pub_id','checked_at','fence_set_sha256',
               'grant_pub_id','grant_revision','lease_fences','owner_handle',
               'valid_until'
             ]) OR
             (command_payload->'authority') - ARRAY[
               'binding_revision_pub_id','checked_at','fence_set_sha256',
               'grant_pub_id','grant_revision','lease_fences','owner_handle',
               'valid_until'
             ] <> '{}'::jsonb OR
             command_payload->>'attempt_ref' IS DISTINCT FROM
               p_capture_attempt_ref OR
             command_payload->>'capture_policy_revision' IS DISTINCT FROM
               p_capture_policy_revision OR
             (command_payload->>'expected_capture_version')::integer IS DISTINCT FROM
               p_expected_capture_state_version OR
             command_payload->>'authority_sha256' IS DISTINCT FROM
               p_expected_authority_sha256 OR
             (command_payload->>'requested_at')::timestamptz IS DISTINCT FROM
               p_requested_at OR
             jsonb_typeof(command_payload#>'{authority,lease_fences}')<>'array' OR
             jsonb_array_length(command_payload#>'{authority,lease_fences}')
               NOT BETWEEN 1 AND 32 THEN
            RAISE EXCEPTION 'capture command envelope drifted or is not bounded';
          END IF;

          SELECT operation.pub_id AS operation_pub_id,
                 operation.operation_key,operation.operation_generation,
                 operation.send_state,operation.send_resolved_at,
                 operation.platform,operation.collection_surface,
                 operation.product_variant,target.target_key,
                 manifest.request_manifest_hash,
                 manifest.provider_idempotency_key_hash
            INTO operation_row
            FROM platform.collection_submission_operation operation
            JOIN platform.collection_submission_request_manifest_v2 manifest
              ON manifest.operation_id=operation.id
             AND manifest.tenant_id=operation.tenant_id
             AND manifest.project_id=operation.project_id
            JOIN platform.collection_campaign_target target
              ON target.id=operation.campaign_target_id
             AND target.tenant_id=operation.tenant_id
             AND target.project_id=operation.project_id
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
           FOR KEY SHARE OF operation,manifest,target;
          IF NOT FOUND OR
             operation_row.send_state NOT IN ('CONFIRMED_SENT','SEND_UNKNOWN') OR
             operation_row.send_resolved_at IS NULL OR
             p_requested_at < operation_row.send_resolved_at OR
             command_payload->>'source_send_state' IS DISTINCT FROM
               operation_row.send_state OR
             command_payload#>>'{operation,protocol_version}' IS DISTINCT FROM
               'collection-submission-v1' OR
             command_payload#>>'{operation,operation_pub_id}' IS DISTINCT FROM
               operation_row.operation_pub_id OR
             command_payload#>>'{operation,operation_key}' IS DISTINCT FROM
               operation_row.operation_key OR
             (command_payload#>>'{operation,generation}')::integer
               IS DISTINCT FROM operation_row.operation_generation OR
             command_payload#>>'{operation,request_manifest_sha256}'
               IS DISTINCT FROM operation_row.request_manifest_hash OR
             encode(public.digest(
               command_payload#>>'{operation,provider_idempotency_key}',
               'sha256'),'hex') IS DISTINCT FROM
               operation_row.provider_idempotency_key_hash OR
             command_payload#>>'{requested_surface_product,platform}'
               IS DISTINCT FROM operation_row.platform OR
             command_payload#>>'{requested_surface_product,collection_surface}'
               IS DISTINCT FROM operation_row.collection_surface OR
             command_payload#>>'{requested_surface_product,product_variant}'
               IS DISTINCT FROM operation_row.product_variant OR
             command_payload#>>'{requested_surface_product,target_key}'
               IS DISTINCT FROM operation_row.target_key THEN
            RAISE EXCEPTION 'capture attempt requires sent or unknown durable truth';
          END IF;
          PERFORM platform.assert_collection_dispatch_fresh_s10(
            p_tenant_id,p_project_id,p_operation_id,p_dispatch_id,
            p_expected_fence_set_hash
          );
          SELECT dispatch.owner_handle,dispatch.grant_resource_set_hash,
                 dispatch.authority_sha256,dispatch.execution_grant_id,
                 dispatch.binding_revision_id,dispatch.grant_revision,
                 dispatch.grant_authority_hash,
                 dispatch.authority_snapshot_json,dispatch.claimed_at,
                 grant_source.pub_id AS grant_pub_id,
                 grant_source.grant_revision,
                 grant_source.expires_at AS grant_expires_at,
                 binding.pub_id AS binding_revision_pub_id,
                 binding.expires_at AS binding_expires_at
            INTO STRICT dispatch_row
            FROM platform.collection_submission_dispatch_v2 dispatch
            JOIN platform.collection_execution_grant_v2 grant_source
              ON grant_source.id=dispatch.execution_grant_id
             AND grant_source.tenant_id=dispatch.tenant_id
             AND grant_source.project_id=dispatch.project_id
            JOIN platform.collection_binding_revision_v2 binding
              ON binding.id=dispatch.binding_revision_id
             AND binding.tenant_id=dispatch.tenant_id
             AND binding.project_id=dispatch.project_id
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.operation_id=p_operation_id
           FOR KEY SHARE OF dispatch,grant_source,binding;
          IF ROW(dispatch_row.owner_handle,dispatch_row.grant_resource_set_hash,
                 dispatch_row.authority_sha256)
             IS DISTINCT FROM ROW(p_owner_handle,p_expected_fence_set_hash,
                                  p_expected_authority_sha256) OR
             command_payload#>>'{authority,grant_pub_id}' IS DISTINCT FROM
               dispatch_row.grant_pub_id OR
             (command_payload#>>'{authority,grant_revision}')::integer
               IS DISTINCT FROM dispatch_row.grant_revision OR
             command_payload#>>'{authority,binding_revision_pub_id}'
               IS DISTINCT FROM dispatch_row.binding_revision_pub_id OR
             command_payload#>>'{authority,owner_handle}' IS DISTINCT FROM
               p_owner_handle OR
             command_payload#>>'{authority,fence_set_sha256}' IS DISTINCT FROM
               p_expected_fence_set_hash OR
             command_payload->'authority' IS DISTINCT FROM
               dispatch_row.authority_snapshot_json::jsonb OR
             (command_payload#>>'{authority,checked_at}')::timestamptz >
               p_requested_at OR
             p_requested_at >=
               (command_payload#>>'{authority,valid_until}')::timestamptz OR
             (command_payload#>>'{authority,checked_at}')::timestamptz >
               transition_time + interval '30 seconds' OR
             (command_payload#>>'{authority,valid_until}')::timestamptz <=
               transition_time OR
             (command_payload#>>'{authority,valid_until}')::timestamptz >
               dispatch_row.grant_expires_at OR
             (dispatch_row.binding_expires_at IS NOT NULL AND
               (command_payload#>>'{authority,valid_until}')::timestamptz >
                 dispatch_row.binding_expires_at) THEN
            RAISE EXCEPTION 'capture command owner authority drifted';
          END IF;
          PERFORM platform.assert_collection_authority_snapshot_s10(
            p_tenant_id,p_project_id,p_operation_id,
            dispatch_row.execution_grant_id,dispatch_row.binding_revision_id,
            dispatch_row.grant_revision,dispatch_row.grant_authority_hash,
            p_owner_handle,p_expected_fence_set_hash,
            p_expected_authority_sha256,
            dispatch_row.authority_snapshot_json,p_requested_at
          );

          SELECT count(*) INTO authoritative_resource_count
            FROM platform.collection_execution_grant_resource grant_resource
           WHERE grant_resource.execution_grant_id=
                   dispatch_row.execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id;
          SELECT count(*),
                 count(DISTINCT ROW(
                   fence.value->>'lease_pub_id',
                   fence.value->>'binding_resource_pub_id',
                   fence.value->>'resource_role',
                   fence.value->>'owner_handle',
                   fence.value->>'generation'
                 )),
                 count(*) FILTER (WHERE
                   jsonb_typeof(fence.value)<>'object' OR
                   NOT (fence.value ?& ARRAY[
                     'acquired_at','binding_resource_pub_id','expires_at',
                     'generation','lease_pub_id','owner_handle','resource_role'
                   ]) OR
                   fence.value - ARRAY[
                     'acquired_at','binding_resource_pub_id','expires_at',
                     'generation','lease_pub_id','owner_handle','resource_role'
                   ] <> '{}'::jsonb OR
                   NOT EXISTS (
                     SELECT 1
                       FROM platform.collection_execution_grant_resource
                         grant_resource
                       JOIN platform.resource_lease lease
                         ON lease.id=grant_resource.resource_lease_id
                        AND lease.tenant_id=grant_resource.tenant_id
                        AND lease.project_id=grant_resource.project_id
                      WHERE grant_resource.execution_grant_id=
                              dispatch_row.execution_grant_id
                        AND grant_resource.tenant_id=p_tenant_id
                        AND grant_resource.project_id=p_project_id
                        AND lease.pub_id=fence.value->>'lease_pub_id'
                        AND grant_resource.resource_pub_id=
                              fence.value->>'binding_resource_pub_id'
                        AND grant_resource.resource_role=
                              fence.value->>'resource_role'
                        AND grant_resource.owner_gateway_handle=
                              fence.value->>'owner_handle'
                        AND grant_resource.fence_generation=
                              (fence.value->>'generation')::bigint
                        AND lease.acquired_at=
                              (fence.value->>'acquired_at')::timestamptz
                        AND lease.expires_at=
                              (fence.value->>'expires_at')::timestamptz
                        AND lease.acquired_at <=
                              (command_payload#>>'{authority,checked_at}')::timestamptz
                        AND lease.expires_at >=
                              (command_payload#>>'{authority,valid_until}')::timestamptz
                   ))
            INTO command_resource_count,unique_command_resource_count,
                 invalid_command_resource_count
            FROM jsonb_array_elements(
              command_payload#>'{authority,lease_fences}'
            ) AS fence(value);
          IF command_resource_count<>authoritative_resource_count OR
             unique_command_resource_count<>authoritative_resource_count OR
             invalid_command_resource_count<>0 THEN
            RAISE EXCEPTION 'capture command lease fence snapshot drifted';
          END IF;

          SELECT string_agg(
                   '{"acquired_at":"' || (fence.value->>'acquired_at') ||
                   '","binding_resource_pub_id":"' ||
                     (fence.value->>'binding_resource_pub_id') ||
                   '","expires_at":"' || (fence.value->>'expires_at') ||
                   '","generation":' ||
                     (fence.value->>'generation')::bigint::text ||
                   ',"lease_pub_id":"' || (fence.value->>'lease_pub_id') ||
                   '","owner_handle":"' || (fence.value->>'owner_handle') ||
                   '","resource_role":"' || (fence.value->>'resource_role') || '"}',
                   ',' ORDER BY fence.ordinality
                 )
            INTO canonical_lease_fences
            FROM jsonb_array_elements(
              command_payload#>'{authority,lease_fences}'
            ) WITH ORDINALITY AS fence(value,ordinality);
          canonical_authority_json :=
            '{"binding_revision_pub_id":"' ||
              (command_payload#>>'{authority,binding_revision_pub_id}') ||
            '","checked_at":"' || (command_payload#>>'{authority,checked_at}') ||
            '","fence_set_sha256":"' ||
              (command_payload#>>'{authority,fence_set_sha256}') ||
            '","grant_pub_id":"' || (command_payload#>>'{authority,grant_pub_id}') ||
            '","grant_revision":' ||
              (command_payload#>>'{authority,grant_revision}')::integer::text ||
            ',"lease_fences":[' || canonical_lease_fences ||
            '],"owner_handle":"' || (command_payload#>>'{authority,owner_handle}') ||
            '","valid_until":"' || (command_payload#>>'{authority,valid_until}') || '"}';
          calculated_authority_sha256 := encode(
            public.digest(canonical_authority_json,'sha256'),'hex'
          );
          canonical_operation_json :=
            '{"generation":' || operation_row.operation_generation::text ||
            ',"operation_key":"' || operation_row.operation_key ||
            '","operation_pub_id":"' || operation_row.operation_pub_id ||
            '","protocol_version":"collection-submission-v1"' ||
            ',"provider_idempotency_key":"' ||
              (command_payload#>>'{operation,provider_idempotency_key}') ||
            '","request_manifest_sha256":"' ||
              operation_row.request_manifest_hash || '"}';
          canonical_surface_json :=
            '{"collection_surface":"' || operation_row.collection_surface ||
            '","platform":"' || operation_row.platform ||
            '","product_variant":"' || operation_row.product_variant ||
            '","target_key":"' || operation_row.target_key || '"}';
          canonical_staging_intent_basis :=
            '{"attempt_ref":"' || p_capture_attempt_ref ||
            '","operation":' || canonical_operation_json ||
            ',"version":"collection-capture-staging-intent-v1"}';
          calculated_staging_intent_sha256 := encode(public.digest(
            canonical_staging_intent_basis,'sha256'),'hex'
          );
          canonical_staging_intent_json :=
            '{"object_ref":"capture-object-v1-' ||
              calculated_staging_intent_sha256 ||
            '","staging_key":"capture-staging-v1-' ||
              calculated_staging_intent_sha256 || '"}';
          IF command_payload#>>'{staging_intent,object_ref}' IS DISTINCT FROM
               'capture-object-v1-' || calculated_staging_intent_sha256 OR
             command_payload#>>'{staging_intent,staging_key}' IS DISTINCT FROM
               'capture-staging-v1-' || calculated_staging_intent_sha256 THEN
            RAISE EXCEPTION 'capture command staging intent drifted';
          END IF;
          canonical_command_json :=
            '{"attempt_ref":"' || p_capture_attempt_ref ||
            '","authority":' || canonical_authority_json ||
            ',"authority_sha256":"' || p_expected_authority_sha256 ||
            '","capture_policy_revision":"' || p_capture_policy_revision ||
            '","expected_capture_version":' ||
              p_expected_capture_state_version::text ||
            ',"operation":' || canonical_operation_json ||
            ',"requested_at":"' || (command_payload->>'requested_at') ||
            '","requested_surface_product":' || canonical_surface_json ||
            ',"source_send_state":"' || operation_row.send_state ||
            '","staging_intent":' || canonical_staging_intent_json || '}';
          IF calculated_authority_sha256<>p_expected_authority_sha256 OR
             canonical_command_json<>p_capture_command_json OR
             encode(public.digest(canonical_command_json,'sha256'),'hex')<>
               p_capture_request_sha256 THEN
            RAISE EXCEPTION 'capture command canonical authority digest drifted';
          END IF;

          SELECT * INTO truth_row
            FROM platform.collection_capture_truth_v2 truth
           WHERE truth.operation_id=p_operation_id
             AND truth.tenant_id=p_tenant_id AND truth.project_id=p_project_id
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture truth is unavailable';
          END IF;
          IF truth_row.capture_state='capturing' AND
             truth_row.current_attempt_ref=p_capture_attempt_ref AND
             truth_row.active_dispatch_id=p_dispatch_id AND
             truth_row.active_owner_handle=p_owner_handle AND
             truth_row.active_fence_set_hash=p_expected_fence_set_hash AND
             truth_row.active_request_sha256=p_capture_request_sha256 AND
             truth_row.active_command_json=p_capture_command_json AND
             truth_row.capture_requested_at=p_requested_at AND
             truth_row.capture_state_version=p_expected_capture_state_version+1 THEN
            RETURN QUERY SELECT truth_row.capture_state_version,
                                truth_row.attempt_count,false;
            RETURN;
          END IF;
          IF p_requested_at < transition_time - interval '10 minutes' THEN
            RAISE EXCEPTION 'new capture request timestamp is outside clock skew';
          END IF;
          IF truth_row.capture_state_version <> p_expected_capture_state_version OR
             truth_row.capture_state NOT IN
               ('not_started','partial','failed','not_observable') OR EXISTS (
                 SELECT 1 FROM platform.collection_slot_outcome_v2 outcome
                  WHERE outcome.operation_id=p_operation_id
                    AND outcome.tenant_id=p_tenant_id
                    AND outcome.project_id=p_project_id
                    AND outcome.is_final_primary=true
               ) THEN
            RAISE EXCEPTION 'capture truth is not retryable';
          END IF;
          IF p_requested_at < COALESCE(
               truth_row.capture_resolved_at,truth_row.capture_started_at,
               operation_row.send_resolved_at
             ) THEN
            RAISE EXCEPTION 'capture request predates durable capture truth';
          END IF;

          IF truth_row.current_capture_manifest_id IS NOT NULL THEN
            SELECT * INTO current_manifest
              FROM platform.collection_capture_manifest_v2 manifest
             WHERE manifest.id=truth_row.current_capture_manifest_id
               AND manifest.tenant_id=p_tenant_id
               AND manifest.project_id=p_project_id
             FOR UPDATE;
            IF current_manifest.reason_code='invalid_surface_or_product' THEN
              RAISE EXCEPTION 'invalid surface or product capture cannot be retried';
            ELSIF current_manifest.storage_state='linked' THEN
              UPDATE platform.collection_capture_manifest_v2
                 SET is_current=false,version=version+1,
                     updated_at=transition_time
               WHERE id=current_manifest.id AND tenant_id=p_tenant_id
                 AND project_id=p_project_id AND is_current=true
                 AND storage_state='linked';
            ELSIF current_manifest.storage_state='staging' THEN
              UPDATE platform.collection_capture_manifest_v2
                 SET storage_state='quarantined',is_current=false,
                     quarantined_at=transition_time,linked_at=NULL,
                     orphaned_at=NULL,gc_after=NULL,
                     version=version+1,updated_at=transition_time
               WHERE id=current_manifest.id AND tenant_id=p_tenant_id
                 AND project_id=p_project_id AND is_current=true;
            ELSE
              UPDATE platform.collection_capture_manifest_v2
                 SET is_current=false,version=version+1,
                     updated_at=transition_time
               WHERE id=current_manifest.id AND tenant_id=p_tenant_id
                 AND project_id=p_project_id AND is_current=true;
            END IF;
          END IF;

          UPDATE platform.collection_capture_truth_v2 AS truth
             SET capture_state='capturing',
                 capture_state_version=truth.capture_state_version+1,
                 attempt_count=truth.attempt_count+1,
                 current_attempt_ref=p_capture_attempt_ref,
                 active_dispatch_id=p_dispatch_id,
                 active_owner_handle=p_owner_handle,
                 active_fence_set_hash=p_expected_fence_set_hash,
                 active_request_sha256=p_capture_request_sha256,
                 active_command_json=p_capture_command_json,
                 current_capture_manifest_id=NULL,
                 state_reason='capture_attempt_started',
                 capture_requested_at=p_requested_at,
                 capture_started_at=p_requested_at,capture_resolved_at=NULL,
                 version=truth.version+1,updated_at=p_requested_at
           WHERE truth.id=truth_row.id AND truth.tenant_id=p_tenant_id
             AND truth.project_id=p_project_id
             AND truth.capture_state_version=p_expected_capture_state_version;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture attempt compare-and-swap lost';
          END IF;
          RETURN QUERY SELECT p_expected_capture_state_version+1,
                              truth_row.attempt_count+1,true;
        END
        $$
        """
    )


def _create_capture_stage_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.stage_collection_capture_manifest_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_expected_capture_state_version integer,
          p_expected_fence_set_hash text,
          p_owner_handle text,
          p_capture_attempt_ref text,
          p_capture_request_sha256 text,
          p_capture_key text,
          p_capture_state text,
          p_capture_channel text,
          p_capture_protocol_revision text,
          p_content_object_ref text,
          p_content_hash text,
          p_content_size_bytes bigint,
          p_mime_type text,
          p_capture_schema_revision text,
          p_capture_manifest_hash text,
          p_capture_evidence_ref text,
          p_capture_evidence_hash text,
          p_observed_platform text,
          p_observed_surface text,
          p_observed_product_variant text,
          p_observed_product_version text,
          p_capture_adapter_revision text,
          p_data_classification text,
          p_dlp_policy_revision text,
          p_reason_code text,
          p_captured_at timestamptz,
          p_staged_at timestamptz,
          p_retention_until timestamptz
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          truth_row record;
          operation_row record;
          existing_manifest record;
          new_capture_id uuid;
          normalized_capture_state text;
          normalized_storage_state text;
          normalized_quarantined_at timestamptz;
          normalized_reason_code text;
          server_time timestamptz := CURRENT_TIMESTAMP;
          staged_time timestamptz;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'capture staging caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'capture staging tenant context mismatch';
          END IF;
          IF p_expected_capture_state_version < 2 OR
             p_capture_attempt_ref !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_capture_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_expected_fence_set_hash !~ '^[0-9a-f]{64}$' OR
             p_capture_request_sha256 !~ '^[0-9a-f]{64}$' OR
             p_owner_handle !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_capture_manifest_hash !~ '^[0-9a-f]{64}$' OR
             p_capture_evidence_hash !~ '^[0-9a-f]{64}$' OR
             p_capture_evidence_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_reason_code IS NULL OR btrim(p_reason_code)='' OR
             p_captured_at IS NULL OR p_staged_at IS NULL OR
             p_retention_until IS NULL OR p_staged_at < p_captured_at OR
             p_staged_at > server_time + interval '30 seconds' OR
             p_captured_at > server_time + interval '30 seconds' OR
             p_retention_until < p_staged_at THEN
            RAISE EXCEPTION 'capture staging input is invalid';
          END IF;
          staged_time := p_staged_at;

          PERFORM platform.assert_collection_dispatch_fresh_s10(
            p_tenant_id,p_project_id,p_operation_id,p_dispatch_id,
            p_expected_fence_set_hash
          );
          SELECT platform,collection_surface,product_variant INTO STRICT operation_row
            FROM platform.collection_submission_operation
           WHERE id=p_operation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id;
          IF operation_row.platform=p_observed_platform AND
             operation_row.collection_surface=p_observed_surface AND
             operation_row.product_variant=p_observed_product_variant THEN
            normalized_capture_state := p_capture_state;
            normalized_storage_state := 'staging';
            normalized_quarantined_at := NULL;
            normalized_reason_code := p_reason_code;
          ELSE
            normalized_capture_state := 'not_observable';
            normalized_storage_state := 'quarantined';
            normalized_quarantined_at := staged_time;
            normalized_reason_code := 'invalid_surface_or_product';
          END IF;
          SELECT manifest.* INTO existing_manifest
            FROM platform.collection_capture_manifest_v2 manifest
            JOIN platform.collection_capture_truth_v2 existing_truth
              ON existing_truth.id=manifest.capture_truth_id
             AND existing_truth.tenant_id=manifest.tenant_id
             AND existing_truth.project_id=manifest.project_id
           WHERE manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id
             AND manifest.capture_key=p_capture_key
             AND existing_truth.active_dispatch_id=p_dispatch_id
             AND existing_truth.active_owner_handle=p_owner_handle
             AND existing_truth.active_fence_set_hash=p_expected_fence_set_hash
             AND existing_truth.active_request_sha256=p_capture_request_sha256
             AND existing_truth.capture_state_version=
                 p_expected_capture_state_version+1
             AND existing_truth.current_capture_manifest_id=manifest.id;
          IF FOUND THEN
            IF ROW(existing_manifest.operation_id,existing_manifest.dispatch_id,
                   existing_manifest.capture_attempt_ref,
                   existing_manifest.capture_state,
                   existing_manifest.capture_channel,
                   existing_manifest.capture_protocol_revision,
                   existing_manifest.content_object_ref,
                   existing_manifest.content_hash,
                   existing_manifest.content_size_bytes,
                   existing_manifest.mime_type,
                   existing_manifest.capture_schema_revision,
                   existing_manifest.capture_manifest_hash,
                   existing_manifest.capture_evidence_ref,
                   existing_manifest.capture_evidence_hash,
                   existing_manifest.observed_platform,
                   existing_manifest.observed_surface,
                   existing_manifest.observed_product_variant,
                   existing_manifest.observed_product_version,
                   existing_manifest.capture_adapter_revision,
                   existing_manifest.data_classification,
                   existing_manifest.dlp_policy_revision,
                   existing_manifest.reason_code,
                   existing_manifest.captured_at,
                   existing_manifest.staged_at,
                   existing_manifest.retention_until)
               IS DISTINCT FROM
               ROW(p_operation_id,p_dispatch_id,p_capture_attempt_ref,
                   normalized_capture_state,p_capture_channel,
                   p_capture_protocol_revision,p_content_object_ref,
                   p_content_hash,p_content_size_bytes,p_mime_type,
                   p_capture_schema_revision,p_capture_manifest_hash,
                   p_capture_evidence_ref,p_capture_evidence_hash,
                   p_observed_platform,p_observed_surface,
                   p_observed_product_variant,p_observed_product_version,
                   p_capture_adapter_revision,p_data_classification,
                   p_dlp_policy_revision,normalized_reason_code,
                   p_captured_at,p_staged_at,p_retention_until) THEN
              RAISE EXCEPTION 'capture staging idempotency payload drifted';
            END IF;
            RETURN existing_manifest.id;
          END IF;
          SELECT * INTO truth_row
            FROM platform.collection_capture_truth_v2 truth
           WHERE truth.operation_id=p_operation_id
             AND truth.tenant_id=p_tenant_id AND truth.project_id=p_project_id
           FOR UPDATE;
          IF NOT FOUND OR truth_row.capture_state <> 'capturing' OR
             truth_row.capture_state_version <> p_expected_capture_state_version OR
             truth_row.current_attempt_ref <> p_capture_attempt_ref OR
             truth_row.active_dispatch_id <> p_dispatch_id OR
             truth_row.active_owner_handle <> p_owner_handle OR
             truth_row.active_fence_set_hash <> p_expected_fence_set_hash OR
             truth_row.active_request_sha256 <> p_capture_request_sha256 OR
             truth_row.capture_requested_at IS NULL OR
             p_captured_at < truth_row.capture_requested_at OR
             truth_row.current_capture_manifest_id IS NOT NULL THEN
            RAISE EXCEPTION 'capture staging compare-and-swap lost';
          END IF;

          new_capture_id := gen_random_uuid();
          INSERT INTO platform.collection_capture_manifest_v2 (
            id,pub_id,tenant_id,project_id,operation_id,dispatch_id,
            capture_truth_id,schema_version,capture_key,capture_attempt_ordinal,
            capture_attempt_ref,is_current,capture_state,storage_state,
            capture_channel,capture_protocol_revision,content_object_ref,
            content_hash,content_size_bytes,mime_type,capture_schema_revision,
            capture_manifest_hash,capture_evidence_ref,capture_evidence_hash,
            observed_platform,observed_surface,observed_product_variant,
            observed_product_version,
            capture_adapter_revision,data_classification,dlp_policy_revision,
            reason_code,captured_at,staged_at,linked_at,quarantined_at,
            orphaned_at,retention_until,gc_after,legal_hold
          ) VALUES (
            new_capture_id,
            'cap_' || substr(replace(new_capture_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,p_dispatch_id,truth_row.id,
            'collection-capture-manifest-v1',p_capture_key,
            truth_row.attempt_count,p_capture_attempt_ref,true,
            normalized_capture_state,normalized_storage_state,
            p_capture_channel,p_capture_protocol_revision,
            p_content_object_ref,p_content_hash,p_content_size_bytes,p_mime_type,
            p_capture_schema_revision,p_capture_manifest_hash,
            p_capture_evidence_ref,p_capture_evidence_hash,p_observed_platform,
            p_observed_surface,
            p_observed_product_variant,p_observed_product_version,
            p_capture_adapter_revision,p_data_classification,
            p_dlp_policy_revision,normalized_reason_code,p_captured_at,staged_time,
            NULL,normalized_quarantined_at,NULL,p_retention_until,NULL,false
          );
          RETURN new_capture_id;
        END
        $$
        """
    )


def _create_terminal_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.finalize_collection_submission_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_execution_grant_id uuid,
          p_expected_send_state_version integer,
          p_target_send_state text,
          p_terminal_reason text,
          p_transition_key text,
          p_owner_gateway_revision text,
          p_owner_dispatch_ref text,
          p_evidence_ref text,
          p_evidence_hash text,
          p_non_submission_proof_ref text,
          p_provider_submission_ref text,
          p_terminated_fence_set_hash text,
          p_reason_code text,
          p_resolved_at timestamptz,
          p_terminal_payload_sha256 text,
          p_reconciliation_claim_ref text,
          p_reconciliation_claim_hash text,
          p_expected_reconciliation_version integer
        ) RETURNS TABLE(
          send_state_version integer,
          transition_evidence_id uuid,
          outbox_id uuid,
          finalized boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          request_row record;
          dispatch_row record;
          grant_row record;
          reservation_row record;
          quota_effect_row record;
          existing_transition record;
          existing_effect record;
          existing_outbox record;
          transition_kind text;
          reservation_target text;
          ledger_kind text;
          new_state_version integer;
          new_transition_id uuid;
          new_effect_id uuid;
          new_outbox_id uuid;
          new_ledger_id uuid;
          accepted_proof_id uuid;
          accepted_proof_pub_id text;
          accepted_proof_at timestamptz;
          transition_hash text;
          effect_hash text;
          effect_key text;
          event_key text;
          quota_effect_count integer := 0;
          resource_count integer := 0;
          invalid_resource_count integer := 0;
          server_time timestamptz := CURRENT_TIMESTAMP;
          final_time timestamptz;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'submission finalizer caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'submission finalizer tenant context mismatch';
          END IF;
          IF p_expected_send_state_version < 1 OR
             p_target_send_state NOT IN
               ('CONFIRMED_SENT','SEND_UNKNOWN','CONFIRMED_NOT_SENT') OR
             p_terminal_reason NOT IN
               ('submitted','send_unknown','preflight_not_sent',
                'post_claim_not_sent','unavailable',
                'invalid_surface_or_product') OR
             p_transition_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_execution_grant_id IS NULL OR
             (p_dispatch_id IS NULL AND
              (p_owner_gateway_revision IS NOT NULL OR
               p_owner_dispatch_ref IS NOT NULL OR
               p_reconciliation_claim_ref IS NOT NULL OR
               p_reconciliation_claim_hash IS NOT NULL OR
               p_expected_reconciliation_version IS NOT NULL)) OR
             (p_dispatch_id IS NOT NULL AND
              (p_owner_gateway_revision IS NULL OR
               p_owner_gateway_revision !~
                 '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
               p_owner_dispatch_ref IS NULL OR
               p_owner_dispatch_ref !~
                 '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')) OR
             p_evidence_ref !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_evidence_hash !~ '^[0-9a-f]{64}$' OR
             (p_non_submission_proof_ref IS NOT NULL AND
              p_non_submission_proof_ref !~
                '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') OR
             (p_provider_submission_ref IS NOT NULL AND
              p_provider_submission_ref !~
                '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') OR
             (p_terminated_fence_set_hash IS NOT NULL AND
              p_terminated_fence_set_hash !~ '^[0-9a-f]{64}$') OR
             (p_reconciliation_claim_ref IS NOT NULL AND
              p_reconciliation_claim_ref !~
                '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') OR
             (p_reconciliation_claim_hash IS NOT NULL AND
              p_reconciliation_claim_hash !~ '^[0-9a-f]{64}$') OR
             p_reason_code IS NULL OR btrim(p_reason_code)='' OR
             p_resolved_at IS NULL OR
             p_resolved_at > server_time + interval '30 seconds' OR
             p_terminal_payload_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'submission finalizer input is invalid';
          END IF;
          final_time := p_resolved_at;

          SELECT * INTO existing_transition
            FROM platform.collection_submission_transition_evidence_v2 evidence
           WHERE evidence.tenant_id=p_tenant_id
             AND evidence.project_id=p_project_id
             AND evidence.transition_key=p_transition_key;
          IF FOUND THEN
            IF ROW(existing_transition.operation_id,
                   existing_transition.dispatch_id,
                   existing_transition.execution_grant_id,
                   existing_transition.to_send_state,
                   existing_transition.from_send_state_version,
                   existing_transition.to_send_state_version,
                   existing_transition.terminal_reason,
                   existing_transition.owner_gateway_revision,
                   existing_transition.owner_dispatch_ref,
                   existing_transition.evidence_ref,
                   existing_transition.evidence_hash,
                   existing_transition.non_submission_proof_ref,
                   existing_transition.provider_reference_ref,
                   existing_transition.terminated_fence_set_hash,
                   existing_transition.reason_code,
                   existing_transition.recorded_at)
               IS DISTINCT FROM
               ROW(p_operation_id,p_dispatch_id,p_execution_grant_id,
                   p_target_send_state,
                   p_expected_send_state_version,
                   p_expected_send_state_version+1,p_terminal_reason,
                   p_owner_gateway_revision,p_owner_dispatch_ref,
                   p_evidence_ref,p_evidence_hash,
                   p_non_submission_proof_ref,p_provider_submission_ref,
                   p_terminated_fence_set_hash,p_reason_code,p_resolved_at) THEN
              RAISE EXCEPTION 'terminal transition idempotency payload drifted';
            END IF;
            SELECT * INTO operation_row
              FROM platform.collection_submission_operation operation
             WHERE operation.id=p_operation_id
               AND operation.tenant_id=p_tenant_id
               AND operation.project_id=p_project_id;
            IF NOT FOUND OR
               ROW(operation_row.send_state,operation_row.send_state_version,
                   operation_row.send_resolved_at,
                   operation_row.reconciliation_state,
                   operation_row.reconcile_after)
               IS DISTINCT FROM ROW(p_target_send_state,
                   p_expected_send_state_version+1,p_resolved_at,
                   'resolved',NULL::timestamptz) THEN
              RAISE EXCEPTION 'terminal replay operation truth drifted';
            END IF;
            IF p_dispatch_id IS NULL THEN
              IF p_reconciliation_claim_ref IS NOT NULL OR
                 p_reconciliation_claim_hash IS NOT NULL OR
                 p_expected_reconciliation_version IS NOT NULL THEN
                RAISE EXCEPTION
                  'preflight terminal replay cannot carry reconciliation claim';
              END IF;
            ELSE
              SELECT * INTO dispatch_row
                FROM platform.collection_submission_dispatch_v2 dispatch
               WHERE dispatch.id=p_dispatch_id
                 AND dispatch.operation_id=p_operation_id
                 AND dispatch.tenant_id=p_tenant_id
                 AND dispatch.project_id=p_project_id;
              IF NOT FOUND OR
                 dispatch_row.owner_execution_state<>'resolved' OR
                 dispatch_row.reconciliation_state<>'resolved' THEN
                RAISE EXCEPTION 'terminal replay dispatch truth drifted';
              END IF;
              IF dispatch_row.reconciliation_claim_ref IS NULL THEN
                IF dispatch_row.reconciliation_claim_hash IS NOT NULL OR
                   dispatch_row.reconciliation_version<>2 OR
                   p_reconciliation_claim_ref IS NOT NULL OR
                   p_reconciliation_claim_hash IS NOT NULL OR
                   p_expected_reconciliation_version IS NOT NULL THEN
                  RAISE EXCEPTION
                    'owner terminal replay cannot carry reconciliation claim';
                END IF;
              ELSIF p_reconciliation_claim_ref IS DISTINCT FROM
                       dispatch_row.reconciliation_claim_ref OR
                    p_reconciliation_claim_hash IS DISTINCT FROM
                       dispatch_row.reconciliation_claim_hash OR
                    p_expected_reconciliation_version IS NULL OR
                    dispatch_row.reconciliation_version<>
                      p_expected_reconciliation_version+1 THEN
                RAISE EXCEPTION
                  'terminal replay reconciliation claim drifted';
              END IF;
            END IF;
            SELECT * INTO existing_effect
              FROM platform.collection_governance_effect_v2 effect
             WHERE effect.transition_evidence_id=existing_transition.id
               AND effect.tenant_id=p_tenant_id
               AND effect.project_id=p_project_id;
            SELECT * INTO existing_outbox
              FROM platform.collection_governance_outbox_v2 outbox
             WHERE outbox.governance_effect_id=existing_effect.id
               AND outbox.tenant_id=p_tenant_id
               AND outbox.project_id=p_project_id;
            IF existing_effect.id IS NULL OR
               existing_effect.effect_kind<>'submission_terminalized' OR
               existing_effect.send_state<>p_target_send_state OR
               existing_effect.send_state_version<>
                 p_expected_send_state_version+1 OR
               existing_effect.reason_code<>p_reason_code OR
               existing_outbox.id IS NULL OR
               existing_outbox.event_type<>'collection.submission.terminal' OR
               existing_outbox.aggregate_pub_id<>operation_row.pub_id OR
               existing_outbox.aggregate_version<>
                 p_expected_send_state_version+1 OR
               existing_outbox.payload_hash<>p_terminal_payload_sha256 OR
               existing_outbox.occurred_at<>p_resolved_at OR
               existing_outbox.event_key<>
                 platform.collection_outbox_key_s10(
                   'collection.submission.terminal',operation_row.pub_id,
                   p_expected_send_state_version+1,
                   p_terminal_payload_sha256
                 ) OR existing_effect.slot_outcome_id IS NOT NULL OR
               existing_effect.capture_manifest_id IS NOT NULL OR
               existing_effect.observation_id IS NOT NULL OR
               existing_effect.analysis_admission_id IS NOT NULL THEN
              RAISE EXCEPTION 'terminal replay found incomplete atomic effects';
            END IF;
            PERFORM platform.assert_collection_submission_transaction_s10(
              p_tenant_id,p_project_id,p_operation_id
            );
            RETURN QUERY SELECT existing_transition.to_send_state_version,
                                existing_transition.id,existing_outbox.id,false;
            RETURN;
          END IF;
          IF final_time < server_time - interval '10 minutes' THEN
            RAISE EXCEPTION 'new terminal timestamp is outside clock skew';
          END IF;

          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
           FOR UPDATE;
          IF NOT FOUND OR operation_row.send_state_version <>
             p_expected_send_state_version OR operation_row.send_state NOT IN
             ('NOT_SENT','SENDING') THEN
            RAISE EXCEPTION 'submission operation is not terminalizable';
          END IF;
          IF final_time < operation_row.prepared_at OR
             (operation_row.send_state='SENDING' AND
              (operation_row.send_started_at IS NULL OR
               final_time<operation_row.send_started_at)) THEN
            RAISE EXCEPTION 'terminal resolution predates durable operation truth';
          END IF;
          IF operation_row.send_state='NOT_SENT' AND
             p_target_send_state<>'CONFIRMED_NOT_SENT' OR
             operation_row.send_state='SENDING' AND
             p_target_send_state NOT IN
               ('CONFIRMED_SENT','SEND_UNKNOWN','CONFIRMED_NOT_SENT') THEN
            RAISE EXCEPTION 'terminal send-state transition is invalid';
          END IF;

          SELECT * INTO request_row
            FROM platform.collection_submission_request_manifest_v2 manifest
           WHERE manifest.operation_id=p_operation_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'terminalization requires immutable request manifest';
          END IF;
          SELECT * INTO grant_row
            FROM platform.collection_execution_grant_v2 grant_source
           WHERE grant_source.id=p_execution_grant_id
             AND grant_source.tenant_id=p_tenant_id
             AND grant_source.project_id=p_project_id
             AND grant_source.operation_id=p_operation_id
             AND grant_source.issued_at IS NOT NULL
             AND grant_source.grant_state IN ('issued','expired','revoked')
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'terminalization requires exact formal execution grant';
          END IF;

          IF operation_row.send_state='SENDING' THEN
            SELECT * INTO dispatch_row
              FROM platform.collection_submission_dispatch_v2 dispatch
             WHERE dispatch.id=p_dispatch_id
               AND dispatch.operation_id=p_operation_id
               AND dispatch.tenant_id=p_tenant_id
               AND dispatch.project_id=p_project_id
               AND dispatch.execution_grant_id=p_execution_grant_id
               AND dispatch.sending_send_state_version=
                   p_expected_send_state_version
               AND dispatch.owner_gateway_revision=p_owner_gateway_revision
               AND dispatch.owner_dispatch_ref=p_owner_dispatch_ref
             FOR KEY SHARE;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'terminalization requires exact owner dispatch';
            END IF;
            IF dispatch_row.reconciliation_state='not_required' THEN
              IF p_reconciliation_claim_ref IS NOT NULL OR
                 p_reconciliation_claim_hash IS NOT NULL OR
                 p_expected_reconciliation_version IS NOT NULL THEN
                RAISE EXCEPTION 'owner completion cannot impersonate reconciler';
              END IF;
            ELSIF dispatch_row.reconciliation_state='in_progress' THEN
              IF p_reconciliation_claim_ref IS DISTINCT FROM
                   dispatch_row.reconciliation_claim_ref OR
                 p_reconciliation_claim_hash IS DISTINCT FROM
                   dispatch_row.reconciliation_claim_hash OR
                 p_expected_reconciliation_version IS DISTINCT FROM
                   dispatch_row.reconciliation_version THEN
                RAISE EXCEPTION 'terminal reconciliation claim is not exact';
              END IF;
            ELSE
              RAISE EXCEPTION 'dispatch is not owned or reconciliation-claimed';
            END IF;
          ELSIF p_dispatch_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM platform.collection_submission_dispatch_v2 dispatch
             WHERE dispatch.operation_id=p_operation_id
               AND dispatch.tenant_id=p_tenant_id
               AND dispatch.project_id=p_project_id
          ) THEN
            RAISE EXCEPTION 'preflight not-sent proof cannot carry a dispatch';
          END IF;

          IF p_target_send_state='CONFIRMED_SENT' THEN
            IF p_dispatch_id IS NULL OR
               (operation_row.collection_surface='provider_api' AND
                p_provider_submission_ref IS NULL) OR
               p_non_submission_proof_ref IS NOT NULL OR
               p_terminated_fence_set_hash IS NOT NULL OR
               p_terminal_reason<>'submitted' THEN
              RAISE EXCEPTION 'confirmed-sent evidence shape is invalid';
            END IF;
            transition_kind := 'provider_accepted';
            reservation_target := 'settled_consumed';
            ledger_kind := 'settle_consumed';
          ELSIF p_target_send_state='SEND_UNKNOWN' THEN
            IF p_dispatch_id IS NULL OR
               p_non_submission_proof_ref IS NOT NULL OR
               p_terminated_fence_set_hash IS NOT NULL OR
               p_terminal_reason<>'send_unknown' THEN
              RAISE EXCEPTION 'send-unknown evidence shape is invalid';
            END IF;
            transition_kind := 'send_unknown';
            reservation_target := 'settled_unknown';
            ledger_kind := 'settle_unknown';
          ELSE
            IF p_provider_submission_ref IS NOT NULL THEN
              RAISE EXCEPTION 'confirmed-not-sent evidence shape is invalid';
            END IF;
            transition_kind := CASE operation_row.send_state
              WHEN 'NOT_SENT' THEN 'preflight_proved_not_sent'
              ELSE 'owner_proved_not_sent' END;
            reservation_target := 'released';
            ledger_kind := 'release';
            IF operation_row.send_state='SENDING' THEN
              IF p_terminal_reason<>'post_claim_not_sent' OR
                 dispatch_row.reconciliation_state NOT IN
                   ('not_required','in_progress') OR
                 p_non_submission_proof_ref IS NULL THEN
                RAISE EXCEPTION
                  'post-claim not-sent requires exact owner or reconciliation proof';
              END IF;
              SELECT proof.id,proof.pub_id,proof.accepted_at
                INTO accepted_proof_id,accepted_proof_pub_id,accepted_proof_at
                FROM platform.collection_submission_reconciliation_proof proof
               WHERE proof.operation_id=p_operation_id
                 AND proof.tenant_id=p_tenant_id
                 AND proof.project_id=p_project_id
                 AND proof.proof_kind='owner_proved_not_sent'
                 AND proof.proof_state='accepted'
                 AND proof.owner_gateway_revision=p_owner_gateway_revision
                 AND proof.owner_evidence_ref=p_evidence_ref
                 AND proof.evidence_hash=p_evidence_hash
               FOR KEY SHARE;
              IF p_terminated_fence_set_hash IS NULL OR
                 p_terminated_fence_set_hash <>
                   dispatch_row.grant_resource_set_hash OR
                 platform.collection_dispatch_fence_set_hash_s10(
                   p_tenant_id,p_project_id,dispatch_row.execution_grant_id
                 ) <> p_terminated_fence_set_hash OR
                 accepted_proof_id IS NULL OR
                 accepted_proof_at>final_time OR EXISTS (
                   SELECT 1 FROM platform.resource_lease lease
                    WHERE lease.operation_id=p_operation_id
                      AND lease.tenant_id=p_tenant_id
                      AND lease.project_id=p_project_id
                      AND lease.lease_schema_version='collection-resource-lease-v2'
                      AND (lease.lease_state NOT IN
                            ('released','expired','preempted','quarantined') OR
                           (lease.lease_state='released' AND
                            lease.released_at IS NULL) OR
                           (lease.lease_state='expired' AND
                            lease.expires_at > final_time) OR
                           (lease.lease_state IN ('preempted','quarantined') AND
                            lease.revoked_at IS NULL))
              ) THEN
                RAISE EXCEPTION
                  'post-claim not-sent requires accepted proof and terminated fences';
              END IF;
              IF accepted_proof_pub_id<>p_non_submission_proof_ref THEN
                RAISE EXCEPTION 'post-claim non-submission proof reference drifted';
              END IF;
            END IF;
            IF operation_row.send_state='NOT_SENT' AND
               p_non_submission_proof_ref IS NOT NULL THEN
              RAISE EXCEPTION 'preflight not-sent cannot carry owner proof';
            END IF;
            IF operation_row.send_state='NOT_SENT' AND
               p_terminated_fence_set_hash IS NULL THEN
              RAISE EXCEPTION 'preflight not-sent requires exact fenced grant';
            END IF;
            IF operation_row.send_state='NOT_SENT' AND
               p_terminal_reason NOT IN
                 ('preflight_not_sent','unavailable',
                  'invalid_surface_or_product') THEN
              RAISE EXCEPTION 'preflight not-sent terminal reason is invalid';
            END IF;
          END IF;

          IF p_target_send_state='CONFIRMED_NOT_SENT' THEN
            PERFORM 1
              FROM platform.collection_execution_grant_resource grant_resource
              JOIN platform.resource_lease lease
                ON lease.id=grant_resource.resource_lease_id
               AND lease.tenant_id=grant_resource.tenant_id
               AND lease.project_id=grant_resource.project_id
              JOIN platform.collection_resource_capacity_unit capacity
                ON capacity.id=grant_resource.capacity_unit_id
               AND capacity.tenant_id=grant_resource.tenant_id
               AND capacity.project_id=grant_resource.project_id
             WHERE grant_resource.execution_grant_id=p_execution_grant_id
               AND grant_resource.tenant_id=p_tenant_id
               AND grant_resource.project_id=p_project_id
             ORDER BY grant_resource.resource_role,
                      grant_resource.resource_ordinal
             FOR UPDATE OF lease,capacity;
            GET DIAGNOSTICS resource_count=ROW_COUNT;
            IF resource_count<1 OR
               platform.collection_dispatch_fence_set_hash_s10(
                 p_tenant_id,p_project_id,p_execution_grant_id
               ) IS DISTINCT FROM p_terminated_fence_set_hash THEN
              RAISE EXCEPTION 'not-sent terminal fence set is incomplete';
            END IF;
            IF operation_row.send_state='NOT_SENT' THEN
              UPDATE platform.resource_lease lease
                 SET lease_state='released',released_at=final_time,
                     reconciliation_reason=p_terminal_reason,
                     version=lease.version+1,updated_at=final_time
                FROM platform.collection_execution_grant_resource grant_resource
               WHERE grant_resource.execution_grant_id=p_execution_grant_id
                 AND grant_resource.tenant_id=p_tenant_id
                 AND grant_resource.project_id=p_project_id
                 AND grant_resource.resource_lease_id=lease.id
                 AND lease.tenant_id=p_tenant_id
                 AND lease.project_id=p_project_id
                 AND lease.operation_id=p_operation_id
                 AND lease.lease_state='active';
            END IF;
            UPDATE platform.collection_resource_capacity_unit capacity
               SET capacity_state='available',
                   state_reason='submission_confirmed_not_sent',
                   version=capacity.version+1,updated_at=final_time
              FROM platform.collection_execution_grant_resource grant_resource,
                   platform.resource_lease lease
             WHERE grant_resource.execution_grant_id=p_execution_grant_id
               AND grant_resource.tenant_id=p_tenant_id
               AND grant_resource.project_id=p_project_id
               AND lease.id=grant_resource.resource_lease_id
               AND lease.tenant_id=grant_resource.tenant_id
               AND lease.project_id=grant_resource.project_id
               AND lease.lease_state IN
                 ('released','expired','preempted','quarantined')
               AND capacity.id=grant_resource.capacity_unit_id
               AND capacity.tenant_id=p_tenant_id
               AND capacity.project_id=p_project_id
               AND capacity.current_fencing_token=
                     grant_resource.fence_generation
               AND capacity.capacity_state='leased';
            SELECT count(*) FILTER (WHERE
                     lease.lease_state NOT IN
                       ('released','expired','preempted','quarantined') OR
                     (lease.lease_state='released' AND
                      (lease.released_at IS NULL OR
                       lease.released_at>final_time)) OR
                     (lease.lease_state='expired' AND
                      lease.expires_at>final_time) OR
                     (lease.lease_state IN ('preempted','quarantined') AND
                      lease.revoked_at IS NULL) OR
                     capacity.current_fencing_token<
                       grant_resource.fence_generation OR
                     (capacity.current_fencing_token=
                        grant_resource.fence_generation AND
                      capacity.capacity_state='leased'))
              INTO invalid_resource_count
              FROM platform.collection_execution_grant_resource grant_resource
              JOIN platform.resource_lease lease
                ON lease.id=grant_resource.resource_lease_id
               AND lease.tenant_id=grant_resource.tenant_id
               AND lease.project_id=grant_resource.project_id
              JOIN platform.collection_resource_capacity_unit capacity
                ON capacity.id=grant_resource.capacity_unit_id
               AND capacity.tenant_id=grant_resource.tenant_id
               AND capacity.project_id=grant_resource.project_id
             WHERE grant_resource.execution_grant_id=p_execution_grant_id
               AND grant_resource.tenant_id=p_tenant_id
               AND grant_resource.project_id=p_project_id;
            IF invalid_resource_count<>0 THEN
              RAISE EXCEPTION 'not-sent terminal left live resource authority';
            END IF;
          END IF;

          SELECT * INTO reservation_row
            FROM platform.collection_quota_reservation reservation
           WHERE reservation.operation_id=p_operation_id
             AND reservation.tenant_id=p_tenant_id
             AND reservation.project_id=p_project_id
             AND reservation.reservation_state IN ('reserved','reconciling')
           FOR UPDATE;
          IF NOT FOUND OR
             ROW(reservation_row.id,reservation_row.binding_revision_id,
                 reservation_row.quota_registry_id) IS DISTINCT FROM
             ROW(grant_row.quota_reservation_id,
                 grant_row.binding_revision_id,
                 grant_row.quota_registry_id) THEN
            RAISE EXCEPTION 'terminalization requires exact reserved quota set';
          END IF;
          IF operation_row.send_state='SENDING' THEN
            IF ROW(reservation_row.id,reservation_row.binding_revision_id,
                   reservation_row.quota_registry_id) IS DISTINCT FROM
               ROW(dispatch_row.quota_reservation_id,
                   dispatch_row.binding_revision_id,
                   dispatch_row.quota_registry_id) THEN
              RAISE EXCEPTION 'terminalization requires exact reserved quota set';
            END IF;
          END IF;
          PERFORM platform.assert_collection_quota_reservation_v2(
            p_tenant_id,p_project_id,reservation_row.id
          );

          new_state_version := p_expected_send_state_version+1;
          transition_hash := encode(public.digest(
            'collection-submission-transition-v1' || E'\\n' ||
            p_operation_id::text || E'\\n' || operation_row.send_state || E'\\n' ||
            p_target_send_state || E'\\n' ||
            p_expected_send_state_version::text || E'\\n' ||
            new_state_version::text || E'\\n' || p_evidence_hash,
            'sha256'),'hex');
          new_transition_id := gen_random_uuid();

          UPDATE platform.collection_submission_operation AS operation
             SET send_state=p_target_send_state,
                 send_state_version=new_state_version,
                 send_resolved_at=final_time,
                 reconciliation_state='resolved',
                 reconcile_after=NULL,
                 state_reason=p_reason_code,version=operation.version+1,
                 updated_at=final_time
           WHERE operation.id=p_operation_id AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
             AND operation.send_state=operation_row.send_state
             AND operation.send_state_version=p_expected_send_state_version;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'terminal send-state compare-and-swap lost';
          END IF;
          IF p_dispatch_id IS NOT NULL THEN
            UPDATE platform.collection_submission_dispatch_v2
               SET owner_execution_state='resolved',
                   reconciliation_state='resolved',
                   reconciliation_version=reconciliation_version+1,
                   reconciliation_resolved_at=final_time,
                   version=version+1,updated_at=final_time
             WHERE id=p_dispatch_id AND tenant_id=p_tenant_id
               AND project_id=p_project_id
               AND operation_id=p_operation_id
               AND reconciliation_state=dispatch_row.reconciliation_state
               AND reconciliation_version=dispatch_row.reconciliation_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'dispatch completion compare-and-swap lost';
            END IF;
          END IF;

          INSERT INTO platform.collection_submission_transition_evidence_v2 (
            id,pub_id,tenant_id,project_id,operation_id,dispatch_id,
            execution_grant_id,
            schema_version,transition_key,evidence_kind,terminal_reason,
            evidence_state,
            from_send_state,to_send_state,from_send_state_version,
            to_send_state_version,owner_gateway_revision,owner_dispatch_ref,
            evidence_ref,evidence_hash,provider_reference_ref,
            non_submission_proof_ref,terminated_fence_set_hash,
            reconciliation_proof_id,
            provider_idempotency_key_hash,
            transition_hash,reason_code,recorded_by,recorded_at
          ) VALUES (
            new_transition_id,
            'ste_' || substr(replace(new_transition_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,p_dispatch_id,
            p_execution_grant_id,
            'collection-submission-transition-v1',p_transition_key,
            transition_kind,p_terminal_reason,
            'accepted',operation_row.send_state,
            p_target_send_state,p_expected_send_state_version,new_state_version,
            p_owner_gateway_revision,p_owner_dispatch_ref,p_evidence_ref,
            p_evidence_hash,p_provider_submission_ref,
            p_non_submission_proof_ref,p_terminated_fence_set_hash,
            accepted_proof_id,
            request_row.provider_idempotency_key_hash,
            transition_hash,p_reason_code,caller_role,final_time
          );

          FOR quota_effect_row IN
            SELECT effect.*,bucket.scope_kind,bucket.bucket_key
              FROM platform.collection_quota_reservation_effect effect
              JOIN platform.collection_quota_bucket bucket
                ON bucket.id=effect.quota_bucket_id
               AND bucket.tenant_id=effect.tenant_id
               AND bucket.project_id=effect.project_id
             WHERE effect.reservation_id=reservation_row.id
               AND effect.tenant_id=p_tenant_id
               AND effect.project_id=p_project_id
               AND effect.operation_id=p_operation_id
               AND effect.effect_state='reserved'
             ORDER BY CASE bucket.scope_kind
               WHEN 'provider' THEN 0 WHEN 'account' THEN 1
               WHEN 'credential' THEN 2 WHEN 'project' THEN 3
               WHEN 'contract' THEN 4 WHEN 'platform_surface' THEN 5
               WHEN 'mode' THEN 6 ELSE 2147483647 END,bucket.bucket_key
             FOR UPDATE OF effect,bucket
          LOOP
            quota_effect_count := quota_effect_count+1;
            UPDATE platform.collection_quota_reservation_effect
               SET effect_state=reservation_target,state_reason=p_reason_code,
                   settled_at=CASE WHEN reservation_target IN
                     ('settled_consumed','settled_unknown')
                     THEN final_time ELSE NULL END,
                   released_at=CASE WHEN reservation_target='released'
                     THEN final_time ELSE NULL END,
                   version=version+1,updated_at=final_time
             WHERE id=quota_effect_row.id AND tenant_id=p_tenant_id
               AND project_id=p_project_id AND effect_state='reserved';
            IF NOT FOUND THEN
              RAISE EXCEPTION 'quota effect terminal compare-and-swap lost';
            END IF;
            UPDATE platform.collection_quota_bucket
               SET reserved_units=reserved_units-quota_effect_row.units,
                   settled_consumed_units=settled_consumed_units+CASE
                     WHEN reservation_target='settled_consumed'
                     THEN quota_effect_row.units ELSE 0 END,
                   settled_unknown_units=settled_unknown_units+CASE
                     WHEN reservation_target='settled_unknown'
                     THEN quota_effect_row.units ELSE 0 END,
                   fence_version=fence_version+1,version=version+1,
                   updated_at=final_time
             WHERE id=quota_effect_row.quota_bucket_id
               AND tenant_id=p_tenant_id AND project_id=p_project_id
               AND reserved_units>=quota_effect_row.units;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'quota bucket terminal conservation failed';
            END IF;
            new_ledger_id := gen_random_uuid();
            event_key := encode(public.digest(
              'collection-quota-ledger-v1|effect=' || quota_effect_row.id::text ||
              '|kind=' || ledger_kind,'sha256'),'hex');
            INSERT INTO platform.collection_quota_ledger_event (
              id,pub_id,tenant_id,project_id,reservation_effect_id,
              reservation_id,operation_id,quota_bucket_id,
              quota_scope_policy_id,event_key,idempotency_key,effect_kind,
              from_state,to_state,units,reason_code,actor_pub_id,occurred_at
            ) VALUES (
              new_ledger_id,
              'qle_' || substr(replace(new_ledger_id::text,'-',''),1,26),
              p_tenant_id,p_project_id,quota_effect_row.id,reservation_row.id,
              p_operation_id,quota_effect_row.quota_bucket_id,
              quota_effect_row.quota_scope_policy_id,event_key,event_key,
              ledger_kind,'reserved',reservation_target,quota_effect_row.units,
              p_reason_code,'collection-submission-finalizer-v1',final_time
            );
          END LOOP;
          IF quota_effect_count <> reservation_row.expected_effect_count THEN
            RAISE EXCEPTION 'quota terminal effect set is incomplete';
          END IF;
          UPDATE platform.collection_quota_reservation
             SET reservation_state=reservation_target,finalized_at=final_time,
                 reconcile_after=NULL,state_reason=p_reason_code,
                 version=version+1,updated_at=final_time
           WHERE id=reservation_row.id AND tenant_id=p_tenant_id
             AND project_id=p_project_id
             AND reservation_state IN ('reserved','reconciling');
          IF NOT FOUND THEN
            RAISE EXCEPTION 'quota reservation terminal compare-and-swap lost';
          END IF;

          new_effect_id := gen_random_uuid();
          effect_key := 'gef_' || substr(encode(public.digest(
            p_operation_id::text || '|terminal|' || new_state_version::text,
            'sha256'),'hex'),1,60);
          effect_hash := encode(public.digest(
            'collection-governance-effect-v1' || E'\\n' || effect_key || E'\\n' ||
            transition_hash,'sha256'),'hex');
          INSERT INTO platform.collection_governance_effect_v2 (
            id,pub_id,tenant_id,project_id,operation_id,
            transition_evidence_id,capture_manifest_id,observation_id,
            slot_outcome_id,analysis_admission_id,schema_version,effect_key,
            effect_kind,send_state,send_state_version,effect_hash,
            reason_code,occurred_at
          ) VALUES (
            new_effect_id,
            'gef_' || substr(replace(new_effect_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,new_transition_id,
            NULL,NULL,NULL,NULL,
            'collection-governance-effect-v1',effect_key,
            'submission_terminalized',p_target_send_state,new_state_version,
            effect_hash,p_reason_code,final_time
          );
          new_outbox_id := gen_random_uuid();
          event_key := platform.collection_outbox_key_s10(
            'collection.submission.terminal',operation_row.pub_id,
            new_state_version,p_terminal_payload_sha256
          );
          INSERT INTO platform.collection_governance_outbox_v2 (
            id,pub_id,tenant_id,project_id,operation_id,
            governance_effect_id,schema_version,event_key,event_type,
            aggregate_type,aggregate_pub_id,aggregate_version,
            payload_schema_revision,payload_hash,publish_state,attempt_count,
            occurred_at,available_at,
            published_at,quarantined_at,last_error_code
          ) VALUES (
            new_outbox_id,
            'gox_' || substr(replace(new_outbox_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,new_effect_id,
            'collection-governance-outbox-v1',event_key,
            'collection.submission.terminal','collection_submission',
            operation_row.pub_id,new_state_version,
            'collection-submission-terminal-event-v1',
            p_terminal_payload_sha256,'pending',0,final_time,final_time,
            NULL,NULL,NULL
          );
          RETURN QUERY SELECT new_state_version,new_transition_id,
                              new_outbox_id,true;
        END
        $$
        """
    )


def _create_dispatch_reconciliation_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.mark_collection_dispatch_reconciliation_ready_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_expected_reconciliation_version integer,
          p_owner_loss_evidence_ref text,
          p_owner_loss_evidence_hash text,
          p_reconcile_after timestamptz
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE caller_role text;
        DECLARE tenant_context text;
        DECLARE changed integer;
        DECLARE dispatch_row record;
        DECLARE resource_count integer;
        DECLARE invalid_resource_count integer;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'reconciliation readiness caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'reconciliation readiness tenant context mismatch';
          END IF;
          IF p_expected_reconciliation_version<1 OR
             p_owner_loss_evidence_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_owner_loss_evidence_hash !~ '^[0-9a-f]{64}$' OR
             p_reconcile_after IS NULL OR
             p_reconcile_after<CURRENT_TIMESTAMP THEN
            RAISE EXCEPTION 'reconciliation readiness evidence is invalid';
          END IF;
          SELECT dispatch.* INTO dispatch_row
            FROM platform.collection_submission_dispatch_v2 dispatch
            JOIN platform.collection_submission_operation operation
              ON operation.id=dispatch.operation_id
             AND operation.tenant_id=dispatch.tenant_id
             AND operation.project_id=dispatch.project_id
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.operation_id=p_operation_id
             AND dispatch.owner_execution_state='active'
             AND dispatch.reconciliation_state='not_required'
             AND dispatch.reconciliation_version=
                   p_expected_reconciliation_version
             AND operation.send_state='SENDING'
           FOR UPDATE OF dispatch,operation;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'reconciliation readiness compare-and-swap lost';
          END IF;
          PERFORM 1
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=
                   dispatch_row.execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id
           ORDER BY grant_resource.resource_role,grant_resource.resource_ordinal
           FOR UPDATE OF lease,capacity;
          GET DIAGNOSTICS resource_count=ROW_COUNT;
          SELECT count(*) FILTER (WHERE
                   lease.lease_state NOT IN
                     ('released','expired','preempted','quarantined') OR
                   (lease.lease_state='released' AND lease.released_at IS NULL) OR
                   (lease.lease_state='expired' AND
                    lease.expires_at>CURRENT_TIMESTAMP) OR
                   (lease.lease_state IN ('preempted','quarantined') AND
                    lease.revoked_at IS NULL) OR
                   capacity.current_fencing_token<
                     grant_resource.fence_generation OR
                   (capacity.current_fencing_token=
                      grant_resource.fence_generation AND
                    capacity.capacity_state='leased'))
            INTO invalid_resource_count
            FROM platform.collection_execution_grant_resource grant_resource
            JOIN platform.resource_lease lease
              ON lease.id=grant_resource.resource_lease_id
             AND lease.tenant_id=grant_resource.tenant_id
             AND lease.project_id=grant_resource.project_id
            JOIN platform.collection_resource_capacity_unit capacity
              ON capacity.id=grant_resource.capacity_unit_id
             AND capacity.tenant_id=grant_resource.tenant_id
             AND capacity.project_id=grant_resource.project_id
           WHERE grant_resource.execution_grant_id=
                   dispatch_row.execution_grant_id
             AND grant_resource.tenant_id=p_tenant_id
             AND grant_resource.project_id=p_project_id;
          IF resource_count<1 OR invalid_resource_count<>0 OR
             platform.collection_dispatch_fence_set_hash_s10(
               p_tenant_id,p_project_id,dispatch_row.execution_grant_id
             )<>dispatch_row.grant_resource_set_hash THEN
            RAISE EXCEPTION
              'reconciliation readiness requires terminated fenced authority';
          END IF;
          UPDATE platform.collection_submission_dispatch_v2 dispatch
             SET owner_execution_state='owner_lost',
                 reconciliation_state='pending',
                 reconciliation_version=reconciliation_version+1,
                 readiness_evidence_ref=p_owner_loss_evidence_ref,
                 readiness_evidence_hash=p_owner_loss_evidence_hash,
                 reconcile_after=p_reconcile_after,
                 reconciliation_ready_at=CURRENT_TIMESTAMP,
                 version=version+1,updated_at=CURRENT_TIMESTAMP
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.operation_id=p_operation_id
             AND dispatch.reconciliation_state='not_required'
             AND dispatch.reconciliation_version=
                 p_expected_reconciliation_version
             AND dispatch.owner_execution_state='active';
          GET DIAGNOSTICS changed = ROW_COUNT;
          IF changed<>1 THEN
            RAISE EXCEPTION 'reconciliation readiness compare-and-swap lost';
          END IF;
          RETURN p_expected_reconciliation_version+1;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.claim_collection_dispatch_reconciliation_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_expected_reconciliation_version integer,
          p_reconciliation_claim_ref text,
          p_reconciliation_claim_hash text
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE caller_role text;
        DECLARE tenant_context text;
        DECLARE changed integer;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'reconciliation claim caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'reconciliation claim tenant context mismatch';
          END IF;
          IF p_expected_reconciliation_version<2 OR
             p_reconciliation_claim_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_reconciliation_claim_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'reconciliation claim input is invalid';
          END IF;
          UPDATE platform.collection_submission_dispatch_v2 dispatch
             SET reconciliation_state='in_progress',
                 reconciliation_version=dispatch.reconciliation_version+1,
                 reconciliation_claim_ref=p_reconciliation_claim_ref,
                 reconciliation_claim_hash=p_reconciliation_claim_hash,
                 reconciliation_claimed_at=CURRENT_TIMESTAMP,
                 version=dispatch.version+1,updated_at=CURRENT_TIMESTAMP
            FROM platform.collection_submission_operation operation
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id
             AND dispatch.operation_id=p_operation_id
             AND dispatch.reconciliation_state='pending'
             AND dispatch.reconciliation_version=
                 p_expected_reconciliation_version
             AND dispatch.reconcile_after<=CURRENT_TIMESTAMP
             AND operation.id=dispatch.operation_id
             AND operation.tenant_id=dispatch.tenant_id
             AND operation.project_id=dispatch.project_id
             AND operation.send_state='SENDING';
          GET DIAGNOSTICS changed = ROW_COUNT;
          IF changed<>1 THEN
            RAISE EXCEPTION 'reconciliation claim compare-and-swap lost';
          END IF;
          RETURN p_expected_reconciliation_version+1;
        END
        $$
        """
    )


def _create_slot_outcome_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.record_collection_slot_outcome_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_expected_operation_state_version integer,
          p_expected_prior_fact_version integer,
          p_capture_manifest_id uuid,
          p_capture_state_version integer,
          p_analysis_state_version integer,
          p_capture_link_key text,
          p_outcome_key text,
          p_outcome_state text,
          p_is_final_primary boolean,
          p_outcome_payload_sha256 text,
          p_reason_code text,
          p_recorded_at timestamptz
        ) RETURNS TABLE(
          slot_outcome_id uuid,
          fact_version integer,
          outbox_id uuid,
          recorded boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          capture_row record;
          capture_observation_id uuid := NULL;
          capture_identity_mismatch boolean := false;
          terminal_reason text;
          expected_outcome_state text;
          existing_outcome record;
          existing_effect record;
          existing_outbox record;
          new_outcome_id uuid;
          new_effect_id uuid;
          new_outbox_id uuid;
          next_fact_version integer;
          current_fact_version integer;
          effect_key text;
          effect_hash text;
          event_key text;
          server_time timestamptz := CURRENT_TIMESTAMP;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'slot outcome caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'slot outcome tenant context mismatch';
          END IF;
          IF p_expected_operation_state_version<2 OR
             p_expected_prior_fact_version<0 OR
             p_outcome_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_outcome_state NOT IN
               ('unavailable','confirmed_not_sent',
                'confirmed_sent_capture_pending',
                'confirmed_sent_capture_complete',
                'confirmed_sent_capture_partial',
                'confirmed_sent_capture_failed','send_unknown',
                'invalid_surface_or_product','not_observable') OR
             p_is_final_primary IS NULL OR
             p_is_final_primary IS DISTINCT FROM
               (p_outcome_state='confirmed_sent_capture_complete') OR
             p_analysis_state_version IS NOT NULL OR
             (p_capture_state_version IS NULL) <>
               (p_capture_manifest_id IS NULL) OR
             (p_capture_state_version IS NOT NULL AND
              p_capture_state_version<1) OR
             (p_capture_link_key IS NOT NULL AND
              p_capture_link_key !~ '^capture-link-v1-[0-9a-f]{64}$') OR
             p_outcome_payload_sha256 !~ '^[0-9a-f]{64}$' OR
             p_reason_code IS NULL OR btrim(p_reason_code)='' OR
             p_recorded_at IS NULL OR
             p_recorded_at>server_time+interval '30 seconds' THEN
            RAISE EXCEPTION 'slot outcome input is invalid';
          END IF;

          SELECT * INTO existing_outcome
            FROM platform.collection_slot_outcome_v2 outcome
           WHERE outcome.tenant_id=p_tenant_id
             AND outcome.project_id=p_project_id
             AND outcome.outcome_key=p_outcome_key;
          IF FOUND THEN
            SELECT * INTO operation_row
              FROM platform.collection_submission_operation operation
             WHERE operation.id=p_operation_id
               AND operation.tenant_id=p_tenant_id
               AND operation.project_id=p_project_id;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'slot outcome replay operation truth missing';
            END IF;
            effect_key := 'gef_' || substr(encode(public.digest(
              p_operation_id::text || '|slot-fact|' ||
              existing_outcome.fact_version::text,'sha256'),'hex'),1,60);
            effect_hash := encode(public.digest(
              'collection-governance-effect-v1' || E'\\n' || effect_key ||
              E'\\n' || p_outcome_payload_sha256,'sha256'),'hex');
            event_key := platform.collection_outbox_key_s10(
              'collection.slot.outcome',operation_row.pub_id,
              existing_outcome.fact_version,p_outcome_payload_sha256
            );
            SELECT * INTO existing_effect
              FROM platform.collection_governance_effect_v2 effect
             WHERE effect.slot_outcome_id=existing_outcome.id
               AND effect.tenant_id=p_tenant_id
               AND effect.project_id=p_project_id
               AND effect.effect_kind='slot_outcome_recorded';
            SELECT * INTO existing_outbox
              FROM platform.collection_governance_outbox_v2 outbox
             WHERE outbox.governance_effect_id=existing_effect.id
               AND outbox.tenant_id=p_tenant_id
               AND outbox.project_id=p_project_id;
            IF ROW(existing_outcome.operation_id,
                   existing_outcome.primary_slot_id,
                   existing_outcome.operation_generation,
                   existing_outcome.operation_state_version,
                   existing_outcome.fact_version,existing_outcome.outcome_state,
                   existing_outcome.outcome_hash,existing_outcome.reason_code,
                   existing_outcome.decided_at,existing_outcome.outcome_ordinal,
                   existing_outcome.schema_version,
                   existing_outcome.capture_manifest_id,
                   existing_outcome.capture_state_version,
                   existing_outcome.analysis_state_version,
                   existing_outcome.capture_link_key,
                   existing_outcome.is_final_primary)
               IS DISTINCT FROM
               ROW(p_operation_id,operation_row.primary_slot_id,
                   operation_row.operation_generation,
                   p_expected_operation_state_version,
                   p_expected_prior_fact_version+1,p_outcome_state,
                   p_outcome_payload_sha256,p_reason_code,p_recorded_at,
                   p_expected_prior_fact_version,
                   'collection-slot-outcome-v1',
                   p_capture_manifest_id,p_capture_state_version,
                   p_analysis_state_version,p_capture_link_key,
                   p_is_final_primary) OR
               ROW(existing_effect.operation_id,
                   existing_effect.transition_evidence_id,
                   existing_effect.capture_manifest_id,
                   existing_effect.observation_id,
                   existing_effect.slot_outcome_id,
                   existing_effect.analysis_admission_id,
                   existing_effect.schema_version,existing_effect.effect_key,
                   existing_effect.effect_kind,existing_effect.send_state,
                   existing_effect.send_state_version,existing_effect.effect_hash,
                   existing_effect.reason_code,existing_effect.occurred_at)
               IS DISTINCT FROM
               ROW(p_operation_id,NULL::uuid,p_capture_manifest_id,
                   existing_outcome.observation_id,existing_outcome.id,NULL::uuid,
                   'collection-governance-effect-v1',effect_key,
                   'slot_outcome_recorded',operation_row.send_state,
                   p_expected_operation_state_version,effect_hash,
                   p_reason_code,p_recorded_at) OR
               ROW(existing_outbox.operation_id,
                   existing_outbox.governance_effect_id,
                   existing_outbox.schema_version,existing_outbox.event_key,
                   existing_outbox.event_type,existing_outbox.aggregate_type,
                   existing_outbox.aggregate_pub_id,
                   existing_outbox.aggregate_version,
                   existing_outbox.payload_schema_revision,
                   existing_outbox.payload_hash,existing_outbox.occurred_at)
               IS DISTINCT FROM
               ROW(p_operation_id,existing_effect.id,
                   'collection-governance-outbox-v1',event_key,
                   'collection.slot.outcome','collection_submission',
                   operation_row.pub_id,existing_outcome.fact_version,
                   'collection-slot-outcome-event-v1',
                   p_outcome_payload_sha256,p_recorded_at) THEN
              RAISE EXCEPTION 'slot outcome idempotency payload drifted';
            END IF;
            PERFORM platform.assert_collection_submission_transaction_s10(
              p_tenant_id,p_project_id,p_operation_id
            );
            RETURN QUERY SELECT existing_outcome.id,
                                existing_outcome.fact_version,
                                existing_outbox.id,false;
            RETURN;
          END IF;
          IF p_recorded_at<server_time-interval '10 minutes' THEN
            RAISE EXCEPTION 'new slot fact timestamp is outside clock skew';
          END IF;

          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
           FOR KEY SHARE;
          IF NOT FOUND OR operation_row.send_state_version<>
               p_expected_operation_state_version OR
             operation_row.send_state NOT IN
               ('CONFIRMED_SENT','CONFIRMED_NOT_SENT','SEND_UNKNOWN') OR
             operation_row.send_resolved_at IS NULL OR
             p_recorded_at<operation_row.send_resolved_at THEN
            RAISE EXCEPTION 'slot outcome contradicts operation truth';
          END IF;
          IF p_capture_manifest_id IS NOT NULL THEN
            SELECT manifest.*,
                   truth.capture_state_version AS truth_state_version,
                   observation.id AS observation_id
              INTO capture_row
              FROM platform.collection_capture_manifest_v2 manifest
              JOIN platform.collection_capture_truth_v2 truth
                ON truth.id=manifest.capture_truth_id
               AND truth.tenant_id=manifest.tenant_id
               AND truth.project_id=manifest.project_id
               AND truth.operation_id=manifest.operation_id
               AND truth.current_capture_manifest_id=manifest.id
              LEFT JOIN platform.collection_observation_v2 observation
                ON observation.capture_manifest_id=manifest.id
               AND observation.tenant_id=manifest.tenant_id
               AND observation.project_id=manifest.project_id
             WHERE manifest.id=p_capture_manifest_id
               AND manifest.operation_id=p_operation_id
               AND manifest.tenant_id=p_tenant_id
               AND manifest.project_id=p_project_id
               AND manifest.is_current=true
             FOR KEY SHARE OF manifest,truth;
            IF NOT FOUND OR capture_row.truth_state_version<>
                 p_capture_state_version OR
               capture_row.capture_link_key IS DISTINCT FROM
                 p_capture_link_key OR
               p_recorded_at<COALESCE(capture_row.linked_at,
                                      capture_row.quarantined_at,
                                      capture_row.captured_at) THEN
              RAISE EXCEPTION 'slot outcome capture basis is stale';
            END IF;
            capture_observation_id := capture_row.observation_id;
            capture_identity_mismatch :=
              ROW(capture_row.observed_platform,
                  capture_row.observed_surface,
                  capture_row.observed_product_variant)
              IS DISTINCT FROM
              ROW(operation_row.platform,
                  operation_row.collection_surface,
                  operation_row.product_variant);
            IF capture_identity_mismatch AND
               capture_row.storage_state NOT IN ('quarantined','orphaned') THEN
              RAISE EXCEPTION 'capture mismatch storage state is invalid';
            END IF;
            IF capture_identity_mismatch AND
               (capture_row.reason_code<>'invalid_surface_or_product' OR
                capture_row.capture_state<>'not_observable' OR
                capture_row.capture_link_key IS NOT NULL OR
                capture_observation_id IS NOT NULL) THEN
              RAISE EXCEPTION 'capture mismatch normalization is invalid';
            END IF;
            IF NOT capture_identity_mismatch AND
               capture_row.reason_code='invalid_surface_or_product' THEN
              RAISE EXCEPTION 'matched capture identity cannot claim mismatch';
            END IF;
          ELSIF p_capture_link_key IS NOT NULL THEN
            RAISE EXCEPTION 'capture link requires capture fact basis';
          END IF;

          IF operation_row.send_state='SEND_UNKNOWN' THEN
            expected_outcome_state := 'send_unknown';
          ELSIF operation_row.send_state='CONFIRMED_NOT_SENT' THEN
            IF p_capture_manifest_id IS NOT NULL THEN
              RAISE EXCEPTION 'not-sent outcome cannot carry capture truth';
            END IF;
            SELECT evidence.terminal_reason INTO terminal_reason
              FROM platform.collection_submission_transition_evidence_v2 evidence
             WHERE evidence.operation_id=p_operation_id
               AND evidence.tenant_id=p_tenant_id
               AND evidence.project_id=p_project_id
               AND evidence.to_send_state='CONFIRMED_NOT_SENT'
               AND evidence.to_send_state_version=
                   p_expected_operation_state_version;
            IF terminal_reason IS NULL THEN
              RAISE EXCEPTION 'not-sent fact requires exact terminal evidence';
            END IF;
            expected_outcome_state := CASE terminal_reason
              WHEN 'unavailable' THEN 'unavailable'
              WHEN 'invalid_surface_or_product'
                THEN 'invalid_surface_or_product'
              ELSE 'confirmed_not_sent' END;
          ELSIF p_capture_manifest_id IS NULL THEN
            expected_outcome_state := 'confirmed_sent_capture_pending';
          ELSIF capture_identity_mismatch THEN
            expected_outcome_state := 'invalid_surface_or_product';
          ELSIF capture_row.capture_state='completed' THEN
            expected_outcome_state := 'confirmed_sent_capture_complete';
          ELSIF capture_row.capture_state='partial' THEN
            expected_outcome_state := 'confirmed_sent_capture_partial';
          ELSIF capture_row.capture_state='failed' THEN
            expected_outcome_state := 'confirmed_sent_capture_failed';
          ELSE
            expected_outcome_state := 'not_observable';
          END IF;
          IF p_outcome_state<>expected_outcome_state THEN
            RAISE EXCEPTION 'slot outcome does not derive from durable truth';
          END IF;
          IF p_outcome_state IN
               ('confirmed_sent_capture_complete',
                'confirmed_sent_capture_partial') THEN
            IF capture_row.storage_state<>'linked' OR
               capture_observation_id IS NULL OR
               p_capture_link_key IS NULL THEN
              RAISE EXCEPTION 'slot outcome does not derive from durable truth';
            END IF;
          END IF;
          SELECT COALESCE(max(outcome.fact_version),0)
            INTO current_fact_version
            FROM platform.collection_slot_outcome_v2 outcome
           WHERE outcome.operation_id=p_operation_id
             AND outcome.tenant_id=p_tenant_id
             AND outcome.project_id=p_project_id;
          IF current_fact_version<>p_expected_prior_fact_version THEN
            RAISE EXCEPTION 'slot outcome fact compare-and-swap lost';
          END IF;
          next_fact_version := current_fact_version+1;

          new_outcome_id := gen_random_uuid();
          INSERT INTO platform.collection_slot_outcome_v2 (
            id,pub_id,tenant_id,project_id,primary_slot_id,operation_id,
            operation_generation,capture_manifest_id,outcome_ordinal,
            observation_id,operation_state_version,capture_state_version,
            analysis_state_version,capture_link_key,fact_version,
            schema_version,outcome_key,outcome_state,is_final_primary,
            outcome_hash,reason_code,decided_at
          ) VALUES (
            new_outcome_id,
            'sot_' || substr(replace(new_outcome_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,operation_row.primary_slot_id,
            p_operation_id,operation_row.operation_generation,
            p_capture_manifest_id,next_fact_version-1,
            capture_observation_id,
            p_expected_operation_state_version,p_capture_state_version,
            p_analysis_state_version,p_capture_link_key,next_fact_version,
            'collection-slot-outcome-v1',p_outcome_key,p_outcome_state,
            p_is_final_primary,
            p_outcome_payload_sha256,p_reason_code,p_recorded_at
          );
          new_effect_id := gen_random_uuid();
          effect_key := 'gef_' || substr(encode(public.digest(
            p_operation_id::text || '|slot-fact|' || next_fact_version::text,
            'sha256'),'hex'),1,60);
          effect_hash := encode(public.digest(
            'collection-governance-effect-v1' || E'\\n' || effect_key ||
            E'\\n' || p_outcome_payload_sha256,'sha256'),'hex');
          INSERT INTO platform.collection_governance_effect_v2 (
            id,pub_id,tenant_id,project_id,operation_id,
            transition_evidence_id,capture_manifest_id,observation_id,
            slot_outcome_id,analysis_admission_id,schema_version,effect_key,
            effect_kind,send_state,send_state_version,effect_hash,
            reason_code,occurred_at
          ) VALUES (
            new_effect_id,
            'gef_' || substr(replace(new_effect_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,NULL,
            p_capture_manifest_id,capture_observation_id,
            new_outcome_id,NULL,'collection-governance-effect-v1',effect_key,
            'slot_outcome_recorded',operation_row.send_state,
            p_expected_operation_state_version,effect_hash,
            p_reason_code,p_recorded_at
          );
          new_outbox_id := gen_random_uuid();
          event_key := platform.collection_outbox_key_s10(
            'collection.slot.outcome',operation_row.pub_id,
            next_fact_version,p_outcome_payload_sha256
          );
          INSERT INTO platform.collection_governance_outbox_v2 (
            id,pub_id,tenant_id,project_id,operation_id,
            governance_effect_id,schema_version,event_key,event_type,
            aggregate_type,aggregate_pub_id,aggregate_version,
            payload_schema_revision,payload_hash,publish_state,attempt_count,
            occurred_at,available_at,published_at,quarantined_at,last_error_code
          ) VALUES (
            new_outbox_id,
            'gox_' || substr(replace(new_outbox_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,new_effect_id,
            'collection-governance-outbox-v1',event_key,
            'collection.slot.outcome','collection_submission',
            operation_row.pub_id,next_fact_version,
            'collection-slot-outcome-event-v1',p_outcome_payload_sha256,
            'pending',0,p_recorded_at,p_recorded_at,NULL,NULL,NULL
          );
          RETURN QUERY SELECT new_outcome_id,next_fact_version,
                              new_outbox_id,true;
        END
        $$
        """
    )


def _create_capture_link_function() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.link_collection_capture_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_dispatch_id uuid,
          p_capture_manifest_id uuid,
          p_expected_capture_state_version integer,
          p_capture_link_key text,
          p_analysis_contract_revision text,
          p_linked_at timestamptz
        ) RETURNS TABLE(
          observation_id uuid,
          analysis_admission_id uuid,
          linked boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          caller_role text;
          tenant_context text;
          operation_row record;
          request_row record;
          dispatch_row record;
          capture_row record;
          observation_row record;
          existing_admission record;
          new_observation_id uuid;
          new_admission_id uuid;
          observation_key text;
          observation_hash text;
          admission_key text;
          analysis_input_hash text;
          expected_capture_link_key text;
          link_time timestamptz;
          server_time timestamptz := CURRENT_TIMESTAMP;
          create_observation boolean;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'capture linker caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'capture linker tenant context mismatch';
          END IF;
          IF p_expected_capture_state_version < 3 OR
             (p_capture_link_key IS NOT NULL AND
              p_capture_link_key !~
                '^capture-link-v1-[0-9a-f]{64}$') OR
             (p_analysis_contract_revision IS NOT NULL AND
              p_analysis_contract_revision !~
                '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') OR
             p_linked_at IS NULL OR
             p_linked_at > server_time + interval '30 seconds' THEN
            RAISE EXCEPTION 'capture linker input is invalid';
          END IF;
          link_time := p_linked_at;

          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id
             AND operation.send_state IN ('CONFIRMED_SENT','SEND_UNKNOWN')
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture link requires sent or unknown durable truth';
          END IF;
          SELECT * INTO request_row
            FROM platform.collection_submission_request_manifest_v2 manifest
           WHERE manifest.operation_id=p_operation_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id;
          SELECT * INTO dispatch_row
            FROM platform.collection_submission_dispatch_v2 dispatch
           WHERE dispatch.id=p_dispatch_id
             AND dispatch.operation_id=p_operation_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id;
          IF request_row.id IS NULL OR dispatch_row.id IS NULL THEN
            RAISE EXCEPTION 'capture link provenance is incomplete';
          END IF;
          SELECT manifest.*,
                 truth.capture_state_version AS truth_state_version
            INTO capture_row
            FROM platform.collection_capture_manifest_v2 manifest
            JOIN platform.collection_capture_truth_v2 truth
              ON truth.id=manifest.capture_truth_id
             AND truth.tenant_id=manifest.tenant_id
             AND truth.project_id=manifest.project_id
             AND truth.operation_id=manifest.operation_id
             AND truth.current_capture_manifest_id=manifest.id
             AND truth.capture_state=manifest.capture_state
             AND truth.capture_state_version=p_expected_capture_state_version
           WHERE manifest.id=p_capture_manifest_id
             AND manifest.operation_id=p_operation_id
             AND manifest.dispatch_id=p_dispatch_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id
             AND manifest.is_current=true
             AND manifest.storage_state IN
                   ('staging','linked','quarantined','orphaned')
           FOR UPDATE OF manifest,truth;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capture link compare-and-swap input is stale';
          END IF;
          IF link_time<capture_row.captured_at OR
             link_time<capture_row.staged_at THEN
            RAISE EXCEPTION 'capture link predates observed or staged evidence';
          END IF;
          create_observation := capture_row.storage_state IN ('staging','linked');
          IF create_observation <> (
               capture_row.observed_platform=operation_row.platform AND
               capture_row.observed_surface=operation_row.collection_surface AND
               capture_row.observed_product_variant=operation_row.product_variant
             ) THEN
            RAISE EXCEPTION 'capture normalization contradicts operation identity';
          END IF;
          IF create_observation AND capture_row.capture_state IN
               ('completed','partial') THEN
            expected_capture_link_key := 'capture-link-v1-' ||
              encode(public.digest(
                '{"capture_state_version":' ||
                capture_row.truth_state_version::text ||
                ',"content_sha256":"' || capture_row.content_hash ||
                '","operation_key":"' || operation_row.operation_key ||
                '","staging_key":"' || capture_row.capture_key ||
                '","version":"immutable-capture-link-v1"}',
                'sha256'),'hex');
            IF p_capture_link_key IS DISTINCT FROM expected_capture_link_key THEN
              RAISE EXCEPTION 'capture link identity is not deterministic';
            END IF;
          ELSIF p_capture_link_key IS NOT NULL THEN
            RAISE EXCEPTION 'non-linkable capture cannot carry a capture link key';
          END IF;
          IF capture_row.storage_state='linked' THEN
            SELECT * INTO STRICT observation_row
              FROM platform.collection_observation_v2 observation
             WHERE observation.capture_manifest_id=p_capture_manifest_id
               AND observation.tenant_id=p_tenant_id
               AND observation.project_id=p_project_id;
            SELECT * INTO existing_admission
              FROM platform.collection_analysis_admission_v2 admission
             WHERE admission.observation_id=observation_row.id
               AND admission.tenant_id=p_tenant_id
               AND admission.project_id=p_project_id;
            IF capture_row.linked_at<>p_linked_at OR
               capture_row.capture_link_key IS DISTINCT FROM
                 p_capture_link_key OR
               (p_analysis_contract_revision IS NULL) <>
                 (existing_admission.id IS NULL) OR
               (p_analysis_contract_revision IS NOT NULL AND
                existing_admission.analysis_contract_revision<>
                  p_analysis_contract_revision) THEN
              RAISE EXCEPTION 'capture link idempotency payload drifted';
            END IF;
            RETURN QUERY SELECT observation_row.id,
                                existing_admission.id,false;
            RETURN;
          ELSIF capture_row.storage_state IN ('quarantined','orphaned') THEN
            IF p_capture_link_key IS NOT NULL OR
               p_analysis_contract_revision IS NOT NULL THEN
              RAISE EXCEPTION
                'non-observable capture cannot be linked or admitted';
            END IF;
            RETURN QUERY SELECT NULL::uuid,NULL::uuid,false;
            RETURN;
          END IF;
          IF link_time < server_time - interval '10 minutes' THEN
            RAISE EXCEPTION 'new capture link timestamp is outside clock skew';
          END IF;

          IF create_observation THEN
            new_observation_id := gen_random_uuid();
            observation_key := 'obs_' || substr(encode(public.digest(
              p_operation_id::text || '|' || p_capture_manifest_id::text,
              'sha256'),'hex'),1,60);
            observation_hash := encode(public.digest(
              'collection-observation-v1' || E'\\n' ||
              p_capture_manifest_id::text || E'\\n' ||
              capture_row.capture_manifest_hash || E'\\n' ||
              capture_row.capture_evidence_hash,'sha256'),'hex');
            INSERT INTO platform.collection_observation_v2 (
              id,pub_id,tenant_id,project_id,operation_id,dispatch_id,
              primary_slot_id,capture_manifest_id,schema_version,
              request_manifest_hash,capture_state_version,
              execution_grant_id,binding_revision_id,grant_authority_hash,
              fence_set_hash,
              observation_key,capture_state,capture_channel,
              capture_protocol_revision,content_object_ref,content_hash,
              evidence_set_hash,observation_hash,requested_platform,
              requested_surface,requested_product_variant,observed_platform,
              observed_surface,observed_product_variant,
              observed_product_version,observed_at
            ) VALUES (
              new_observation_id,
              'obs_' || substr(replace(new_observation_id::text,'-',''),1,26),
              p_tenant_id,p_project_id,p_operation_id,p_dispatch_id,
              operation_row.primary_slot_id,p_capture_manifest_id,
              'collection-observation-v1',request_row.request_manifest_hash,
              capture_row.truth_state_version,dispatch_row.execution_grant_id,
              dispatch_row.binding_revision_id,
              dispatch_row.grant_authority_hash,
              dispatch_row.grant_resource_set_hash,observation_key,
              capture_row.capture_state,capture_row.capture_channel,
              capture_row.capture_protocol_revision,
              capture_row.content_object_ref,capture_row.content_hash,
              capture_row.capture_evidence_hash,observation_hash,
              operation_row.platform,operation_row.collection_surface,
              operation_row.product_variant,
              capture_row.observed_platform,capture_row.observed_surface,
              capture_row.observed_product_variant,
              capture_row.observed_product_version,capture_row.captured_at
            );
            UPDATE platform.collection_capture_manifest_v2
               SET storage_state='linked',capture_link_key=p_capture_link_key,
                   linked_at=link_time,
                   version=version+1,updated_at=link_time
             WHERE id=p_capture_manifest_id AND tenant_id=p_tenant_id
               AND project_id=p_project_id AND storage_state='staging';
            IF NOT FOUND THEN
              RAISE EXCEPTION 'capture storage link compare-and-swap lost';
            END IF;
          END IF;

          IF create_observation AND capture_row.capture_state IN
               ('completed','partial') AND
             p_analysis_contract_revision IS NOT NULL THEN
            new_admission_id := gen_random_uuid();
            analysis_input_hash := COALESCE(
              capture_row.content_hash,observation_hash
            );
            admission_key := 'adm_' || substr(encode(public.digest(
              new_observation_id::text || '|' || p_analysis_contract_revision,
              'sha256'),'hex'),1,60);
            INSERT INTO platform.collection_analysis_admission_v2 (
              id,pub_id,tenant_id,project_id,operation_id,primary_slot_id,
              observation_id,schema_version,admission_key,
              analysis_contract_revision,analysis_input_hash,
              admission_state,reason_code,admitted_at
            ) VALUES (
              new_admission_id,
              'ana_' || substr(replace(new_admission_id::text,'-',''),1,26),
              p_tenant_id,p_project_id,p_operation_id,
              operation_row.primary_slot_id,new_observation_id,
              'collection-analysis-admission-v1',admission_key,
              p_analysis_contract_revision,analysis_input_hash,'admitted',
              'immutable_capture_admitted',link_time
            );
          ELSIF p_analysis_contract_revision IS NOT NULL THEN
            RAISE EXCEPTION
              'analysis admission is forbidden without linked observable capture';
          END IF;

          RETURN QUERY SELECT new_observation_id,new_admission_id,true;
        END
        $$
        """
    )


def _create_recovery_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.classify_collection_capture_orphan_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_capture_manifest_id uuid,
          p_expected_version integer,
          p_gc_after timestamptz,
          p_reason_code text
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE caller_role text;
        DECLARE tenant_context text;
        DECLARE changed integer;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'capture classifier caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'capture classifier tenant context mismatch';
          END IF;
          IF p_expected_version<1 OR p_gc_after IS NULL OR
             p_reason_code IS NULL OR btrim(p_reason_code)='' THEN
            RAISE EXCEPTION 'capture classifier input is invalid';
          END IF;
          UPDATE platform.collection_capture_manifest_v2 manifest
             SET storage_state='orphaned',orphaned_at=CURRENT_TIMESTAMP,
                 gc_after=p_gc_after,version=version+1,
                 updated_at=CURRENT_TIMESTAMP
           WHERE manifest.id=p_capture_manifest_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id
             AND manifest.operation_id=p_operation_id
             AND ((manifest.storage_state='staging' AND
                   manifest.is_current=false AND NOT EXISTS (
                     SELECT 1
                       FROM platform.collection_capture_truth_v2 truth
                      WHERE truth.current_capture_manifest_id=manifest.id
                        AND truth.tenant_id=manifest.tenant_id
                        AND truth.project_id=manifest.project_id
                        AND truth.operation_id=manifest.operation_id
                   )) OR
                  (manifest.storage_state='quarantined' AND
                   manifest.quarantined_at IS NOT NULL AND
                   manifest.retention_until<=CURRENT_TIMESTAMP))
             AND manifest.version=p_expected_version
             AND p_gc_after>=manifest.retention_until
             AND NOT manifest.legal_hold
             AND NOT EXISTS (
               SELECT 1 FROM platform.collection_observation_v2 observation
                WHERE observation.capture_manifest_id=manifest.id
                  AND observation.tenant_id=manifest.tenant_id
                  AND observation.project_id=manifest.project_id
             );
          GET DIAGNOSTICS changed = ROW_COUNT;
          IF changed<>1 THEN
            RAISE EXCEPTION 'capture orphan classification compare-and-swap lost';
          END IF;
          RETURN p_expected_version+1;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.collection_capture_orphan_gc_eligible_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_capture_manifest_id uuid,
          p_checked_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE caller_role text;
        DECLARE tenant_context text;
        DECLARE eligible boolean;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'capture GC caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'capture GC tenant context mismatch';
          END IF;
          SELECT manifest.storage_state='orphaned' AND
                 NOT manifest.legal_hold AND
                 p_checked_at>=manifest.retention_until AND
                 p_checked_at>=manifest.gc_after AND NOT EXISTS (
                   SELECT 1 FROM platform.collection_observation_v2 observation
                    WHERE observation.capture_manifest_id=manifest.id
                      AND observation.tenant_id=manifest.tenant_id
                      AND observation.project_id=manifest.project_id
                 )
            INTO eligible
            FROM platform.collection_capture_manifest_v2 manifest
           WHERE manifest.id=p_capture_manifest_id
             AND manifest.tenant_id=p_tenant_id
             AND manifest.project_id=p_project_id;
          RETURN COALESCE(eligible,false);
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.advance_collection_governance_outbox_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_outbox_id uuid,
          p_expected_version integer,
          p_target_state text,
          p_error_code text
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE caller_role text;
        DECLARE tenant_context text;
        DECLARE changed integer;
        BEGIN
          caller_role := current_setting('role',true);
          IF caller_role IS NULL OR caller_role='' OR caller_role='none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role<>'geo_worker' THEN
            RAISE EXCEPTION 'governance outbox caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id',true);
          IF tenant_context IS NULL OR tenant_context='' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'governance outbox tenant context mismatch';
          END IF;
          IF p_expected_version<1 OR p_target_state NOT IN
               ('published','quarantined') OR
             (p_target_state='published' AND p_error_code IS NOT NULL) OR
             (p_target_state='quarantined' AND
              (p_error_code IS NULL OR btrim(p_error_code)='')) THEN
            RAISE EXCEPTION 'governance outbox transition input is invalid';
          END IF;
          UPDATE platform.collection_governance_outbox_v2
             SET publish_state=p_target_state,attempt_count=attempt_count+1,
                 published_at=CASE WHEN p_target_state='published'
                   THEN CURRENT_TIMESTAMP ELSE NULL END,
                 quarantined_at=CASE WHEN p_target_state='quarantined'
                   THEN CURRENT_TIMESTAMP ELSE NULL END,
                 last_error_code=p_error_code,version=version+1,
                 updated_at=CURRENT_TIMESTAMP
           WHERE id=p_outbox_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id AND publish_state='pending'
             AND version=p_expected_version;
          GET DIAGNOSTICS changed = ROW_COUNT;
          IF changed<>1 THEN
            RAISE EXCEPTION 'governance outbox compare-and-swap lost';
          END IF;
          RETURN p_expected_version+1;
        END
        $$
        """
    )


def _create_reverse_invariants() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.assert_collection_submission_transaction_s10(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE operation_row record;
        DECLARE truth_row record;
        DECLARE reservation_row record;
        DECLARE transition_count integer;
        DECLARE dispatch_count integer;
        DECLARE effect_count integer;
        DECLARE outbox_count integer;
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM platform.collection_submission_request_manifest_v2 manifest
             WHERE manifest.operation_id=p_operation_id
               AND manifest.tenant_id=p_tenant_id
               AND manifest.project_id=p_project_id
          ) THEN
            SELECT * INTO operation_row
              FROM platform.collection_submission_operation operation
             WHERE operation.id=p_operation_id
               AND operation.tenant_id=p_tenant_id
               AND operation.project_id=p_project_id;
            IF FOUND AND
               operation_row.state_reason='submission_v2_preparation_pending' THEN
              RAISE EXCEPTION
                's10 operation preparation must complete in one transaction';
            END IF;
            RETURN;
          END IF;
          SELECT * INTO operation_row
            FROM platform.collection_submission_operation operation
           WHERE operation.id=p_operation_id
             AND operation.tenant_id=p_tenant_id
             AND operation.project_id=p_project_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 's10 request manifest lost its operation';
          END IF;
          SELECT * INTO truth_row
            FROM platform.collection_capture_truth_v2 truth
           WHERE truth.operation_id=p_operation_id
             AND truth.tenant_id=p_tenant_id
             AND truth.project_id=p_project_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 's10 operation has no durable capture truth';
          END IF;
          SELECT * INTO reservation_row
            FROM platform.collection_quota_reservation reservation
           WHERE reservation.operation_id=p_operation_id
             AND reservation.tenant_id=p_tenant_id
             AND reservation.project_id=p_project_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 's10 operation has no exact quota reservation';
          END IF;
          PERFORM platform.assert_collection_quota_reservation_v2(
            p_tenant_id,p_project_id,reservation_row.id
          );
          IF (operation_row.send_state IN ('NOT_SENT','SENDING') AND
              reservation_row.reservation_state NOT IN ('reserved','reconciling')) OR
             (operation_row.send_state='CONFIRMED_SENT' AND
              reservation_row.reservation_state<>'settled_consumed') OR
             (operation_row.send_state='SEND_UNKNOWN' AND
              reservation_row.reservation_state<>'settled_unknown') OR
             (operation_row.send_state='CONFIRMED_NOT_SENT' AND
              reservation_row.reservation_state<>'released') THEN
            RAISE EXCEPTION 'send truth and quota terminal truth diverged';
          END IF;

          SELECT count(*) INTO transition_count
            FROM platform.collection_submission_transition_evidence_v2 evidence
           WHERE evidence.operation_id=p_operation_id
             AND evidence.tenant_id=p_tenant_id
             AND evidence.project_id=p_project_id
             AND evidence.to_send_state=operation_row.send_state
             AND evidence.to_send_state_version=operation_row.send_state_version
             AND evidence.evidence_state='accepted';
          SELECT count(*) INTO dispatch_count
            FROM platform.collection_submission_dispatch_v2 dispatch
           WHERE dispatch.operation_id=p_operation_id
             AND dispatch.tenant_id=p_tenant_id
             AND dispatch.project_id=p_project_id;
          IF operation_row.send_state='NOT_SENT' THEN
            IF operation_row.send_state_version<>1 OR transition_count<>0 OR
               dispatch_count<>0 THEN
              RAISE EXCEPTION 'NOT_SENT s10 operation has side-effect evidence';
            END IF;
          ELSIF operation_row.send_state='SENDING' THEN
            IF transition_count<>1 OR dispatch_count<>1 OR NOT EXISTS (
              SELECT 1
                FROM platform.collection_submission_dispatch_v2 dispatch
               WHERE dispatch.operation_id=p_operation_id
                 AND dispatch.tenant_id=p_tenant_id
                 AND dispatch.project_id=p_project_id
                 AND dispatch.sending_send_state_version=
                     operation_row.send_state_version
                 AND dispatch.reconciliation_state IN
                   ('not_required','pending','in_progress')
            ) THEN
              RAISE EXCEPTION 'SENDING s10 operation has no exact live dispatch';
            END IF;
          ELSE
            SELECT count(*) INTO effect_count
              FROM platform.collection_governance_effect_v2 effect
             WHERE effect.operation_id=p_operation_id
               AND effect.tenant_id=p_tenant_id
               AND effect.project_id=p_project_id
               AND effect.effect_kind='submission_terminalized'
               AND effect.send_state=operation_row.send_state
               AND effect.send_state_version=operation_row.send_state_version;
            SELECT count(*) INTO outbox_count
              FROM platform.collection_governance_effect_v2 effect
              JOIN platform.collection_governance_outbox_v2 outbox
                ON outbox.governance_effect_id=effect.id
               AND outbox.tenant_id=effect.tenant_id
               AND outbox.project_id=effect.project_id
             WHERE effect.operation_id=p_operation_id
               AND effect.tenant_id=p_tenant_id
               AND effect.project_id=p_project_id
               AND effect.effect_kind='submission_terminalized'
               AND effect.send_state_version=operation_row.send_state_version;
            IF operation_row.reconciliation_state<>'resolved' OR
               operation_row.reconcile_after IS NOT NULL OR
               transition_count<>1 OR effect_count<>1 OR
               outbox_count<>1 OR (dispatch_count=1 AND NOT EXISTS (
                 SELECT 1
                   FROM platform.collection_submission_dispatch_v2 dispatch
                  WHERE dispatch.operation_id=p_operation_id
                    AND dispatch.tenant_id=p_tenant_id
                    AND dispatch.project_id=p_project_id
                    AND dispatch.reconciliation_state='resolved'
                    AND dispatch.owner_execution_state='resolved'
               )) THEN
              RAISE EXCEPTION 'terminal s10 operation is not atomically complete';
            END IF;
          END IF;

          IF EXISTS (
            SELECT 1
              FROM platform.collection_slot_outcome_v2 outcome
              LEFT JOIN platform.collection_governance_effect_v2 effect
                ON effect.slot_outcome_id=outcome.id
               AND effect.tenant_id=outcome.tenant_id
               AND effect.project_id=outcome.project_id
               AND effect.operation_id=outcome.operation_id
               AND effect.effect_kind='slot_outcome_recorded'
              LEFT JOIN platform.collection_governance_outbox_v2 outbox
                ON outbox.governance_effect_id=effect.id
               AND outbox.tenant_id=effect.tenant_id
               AND outbox.project_id=effect.project_id
             WHERE outcome.operation_id=p_operation_id
               AND outcome.tenant_id=p_tenant_id
               AND outcome.project_id=p_project_id
               AND (effect.id IS NULL OR outbox.id IS NULL OR
                    effect.send_state<>operation_row.send_state OR
                    effect.send_state_version<>
                      outcome.operation_state_version OR
                    effect.capture_manifest_id IS DISTINCT FROM
                      outcome.capture_manifest_id OR
                    effect.observation_id IS DISTINCT FROM outcome.observation_id OR
                    outbox.event_type<>'collection.slot.outcome' OR
                    outbox.aggregate_pub_id<>operation_row.pub_id OR
                    outbox.aggregate_version<>outcome.fact_version OR
                    outbox.payload_hash<>outcome.outcome_hash OR
                    outbox.occurred_at<>outcome.decided_at OR
                    outbox.event_key<>
                      platform.collection_outbox_key_s10(
                        'collection.slot.outcome',operation_row.pub_id,
                        outcome.fact_version,outcome.outcome_hash
                      ))
          ) THEN
            RAISE EXCEPTION 'slot fact and governance outbox diverged';
          END IF;

          IF truth_row.current_capture_manifest_id IS NOT NULL THEN
            IF NOT EXISTS (
              SELECT 1
                FROM platform.collection_capture_manifest_v2 manifest
               WHERE manifest.id=truth_row.current_capture_manifest_id
                 AND manifest.tenant_id=p_tenant_id
                 AND manifest.project_id=p_project_id
                 AND manifest.operation_id=p_operation_id
                 AND manifest.capture_truth_id=truth_row.id
                 AND manifest.is_current=true
                 AND manifest.capture_state=truth_row.capture_state
            ) THEN
              RAISE EXCEPTION 'capture truth current manifest is not exact';
            END IF;
            IF EXISTS (
              SELECT 1
                FROM platform.collection_capture_manifest_v2 manifest
               WHERE manifest.id=truth_row.current_capture_manifest_id
                 AND manifest.storage_state='linked'
                 AND NOT EXISTS (
                   SELECT 1 FROM platform.collection_observation_v2 observation
                    WHERE observation.capture_manifest_id=manifest.id
                      AND observation.tenant_id=manifest.tenant_id
                      AND observation.project_id=manifest.project_id
                 )
            ) OR EXISTS (
              SELECT 1
                FROM platform.collection_capture_manifest_v2 manifest
               WHERE manifest.id=truth_row.current_capture_manifest_id
                 AND manifest.storage_state IN ('quarantined','orphaned')
                 AND EXISTS (
                   SELECT 1 FROM platform.collection_observation_v2 observation
                    WHERE observation.capture_manifest_id=manifest.id
                      AND observation.tenant_id=manifest.tenant_id
                      AND observation.project_id=manifest.project_id
                 )
            ) THEN
              RAISE EXCEPTION 'capture storage and immutable observation diverged';
            END IF;
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.validate_collection_submission_transaction_s10()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE target_operation_id uuid;
        DECLARE target_tenant_id uuid;
        DECLARE target_project_id uuid;
        BEGIN
          target_tenant_id := NEW.tenant_id;
          target_project_id := NEW.project_id;
          IF TG_TABLE_NAME='collection_submission_operation' THEN
            target_operation_id := NEW.id;
          ELSE
            target_operation_id := NEW.operation_id;
          END IF;
          PERFORM platform.assert_collection_submission_transaction_s10(
            target_tenant_id,target_project_id,target_operation_id
          );
          RETURN NULL;
        END
        $$
        """
    )
    for table in (
        "collection_submission_operation",
        "collection_quota_reservation",
        *_NEW_TABLES,
    ):
        events = "INSERT OR UPDATE"
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER {table}_s10_atomic_trg
            AFTER {events} ON platform.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION platform.validate_collection_submission_transaction_s10()
            """
        )


def _grant_minimum_privileges() -> None:
    table_names = ",".join(f"'{table}'" for table in _NEW_TABLES)
    function_signatures = ",".join(f"'{signature}'" for signature in _FUNCTION_SIGNATURES)
    function_names = ",".join(
        f"'{signature.split('(', 1)[0].rsplit('.', 1)[1]}'" for signature in _FUNCTION_SIGNATURES
    )
    worker_signatures = ",".join(f"'{signature}'" for signature in _WORKER_FUNCTION_SIGNATURES)
    op.execute(
        f"""
        DO $$
        DECLARE table_name text;
        DECLARE role_name text;
        DECLARE function_identity text;
        DECLARE unexpected_overload_count integer;
        BEGIN
          FOREACH table_name IN ARRAY ARRAY[{table_names}] LOOP
            EXECUTE format(
              'REVOKE ALL ON TABLE platform.%I FROM PUBLIC',table_name
            );
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE format(
                  'REVOKE ALL ON TABLE platform.%I FROM %I',table_name,role_name
                );
              END IF;
            END LOOP;
          END LOOP;

          SELECT count(*) INTO unexpected_overload_count
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
           WHERE namespace.nspname='platform'
             AND procedure.proname IN ({function_names})
             AND NOT procedure.oid=ANY(
               ARRAY(
                 SELECT to_regprocedure(signature)
                   FROM unnest(ARRAY[{function_signatures}]) signature
               )
             );
          IF unexpected_overload_count<>0 THEN
            RAISE EXCEPTION 'unexpected s10 function overload refused';
          END IF;

          FOREACH function_identity IN ARRAY ARRAY[{function_signatures}] LOOP
            IF to_regprocedure(function_identity) IS NULL THEN
              RAISE EXCEPTION 's10 function missing during ACL install: %',
                function_identity;
            END IF;
            EXECUTE 'REVOKE ALL ON FUNCTION ' || function_identity ||
                    ' FROM PUBLIC';
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE 'REVOKE ALL ON FUNCTION ' || function_identity ||
                        ' FROM ' || quote_ident(role_name);
              END IF;
            END LOOP;
          END LOOP;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT USAGE ON SCHEMA platform TO geo_api;
            FOREACH table_name IN ARRAY ARRAY[{table_names}] LOOP
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.%I TO geo_api',table_name
              );
            END LOOP;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT USAGE ON SCHEMA platform TO geo_worker;
            FOREACH table_name IN ARRAY ARRAY[{table_names}] LOOP
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.%I TO geo_worker',table_name
              );
            END LOOP;
            REVOKE ALL ON TABLE
              platform.collection_submission_operation FROM geo_worker;
            REVOKE UPDATE (
              send_state,send_state_version,send_started_at,send_resolved_at,
              reconciliation_state,reconcile_after,state_reason,version,updated_at
            ) ON platform.collection_submission_operation FROM geo_worker;
            GRANT SELECT ON TABLE
              platform.collection_submission_operation TO geo_worker;
            FOREACH function_identity IN ARRAY ARRAY[{worker_signatures}] LOOP
              EXECUTE 'GRANT EXECUTE ON FUNCTION ' || function_identity ||
                      ' TO geo_worker';
            END LOOP;
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _create_parent_candidate_keys()
    _create_request_manifest()
    _create_dispatch()
    _create_capture_truth()
    _create_transition_evidence()
    _create_capture_manifest()
    _create_observation_and_outcomes()
    _create_governance_outbox()
    for table in _NEW_TABLES:
        _enable_rls(table)
    _create_row_guards()
    _create_dispatch_freshness_function()
    _create_operation_function()
    _create_prepare_function()
    _create_claim_function()
    _create_dispatch_reconciliation_functions()
    _create_capture_attempt_function()
    _create_capture_stage_function()
    _create_terminal_function()
    _create_slot_outcome_function()
    _create_capture_link_function()
    _create_recovery_functions()
    _create_reverse_invariants()
    _grant_minimum_privileges()


def downgrade() -> None:
    table_names = ",".join(f"'{table}'" for table in _NEW_TABLES)
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
              RAISE EXCEPTION
                's10 submission downgrade refused: %.% contains durable rows',
                'platform',table_name;
            END IF;
          END LOOP;
        END
        $$
        """
    )
    for table in (
        "collection_submission_operation",
        "collection_quota_reservation",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_s10_atomic_trg ON platform.{table}")
    op.drop_constraint(
        "fk_capture_truth_current_manifest_exact",
        "collection_capture_truth_v2",
        type_="foreignkey",
        schema="platform",
    )
    for table in (
        "collection_governance_outbox_v2",
        "collection_governance_effect_v2",
        "collection_analysis_admission_v2",
        "collection_slot_outcome_v2",
        "collection_observation_v2",
        "collection_capture_manifest_v2",
        "collection_capture_truth_v2",
        "collection_submission_transition_evidence_v2",
        "collection_submission_dispatch_v2",
        "collection_submission_request_manifest_v2",
    ):
        op.drop_table(table, schema="platform")
    function_signatures = ",".join(f"'{signature}'" for signature in _FUNCTION_SIGNATURES)
    op.execute(
        f"""
        DO $$
        DECLARE function_identity text;
        BEGIN
          FOREACH function_identity IN ARRAY ARRAY[{function_signatures}] LOOP
            EXECUTE 'DROP FUNCTION IF EXISTS ' || function_identity;
          END LOOP;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT INSERT ON TABLE
              platform.collection_submission_operation TO geo_worker;
            GRANT UPDATE (
              send_state,send_state_version,send_started_at,send_resolved_at,
              reconciliation_state,reconcile_after,state_reason,version,updated_at
            ) ON platform.collection_submission_operation TO geo_worker;
          END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "uq_submission_reconciliation_proof_scope_s10",
        "collection_submission_reconciliation_proof",
        type_="unique",
        schema="platform",
    )
    op.drop_constraint(
        "uq_execution_grant_operation_scope_s10",
        "collection_execution_grant_v2",
        type_="unique",
        schema="platform",
    )
    op.drop_constraint(
        "uq_execution_grant_dispatch_scope_s10",
        "collection_execution_grant_v2",
        type_="unique",
        schema="platform",
    )
    op.drop_constraint(
        "uq_submission_operation_slot_scope_s10",
        "collection_submission_operation",
        type_="unique",
        schema="platform",
    )


__all__ = ["downgrade", "upgrade"]
