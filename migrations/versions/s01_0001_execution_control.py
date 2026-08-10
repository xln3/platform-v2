"""Create S01 execution-control domains and tenant RLS.

Revision ID: s01_0001
Revises: s00_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s01_0001"
down_revision: str | Sequence[str] | None = "s00_0001"
branch_labels: str | Sequence[str] | None = ("s01",)
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "audit_log",
    "membership",
    "customer",
    "project",
    "brand",
    "brand_alias",
    "brand_asset",
    "competitor",
    "monitoring_config",
    "monitoring_config_version",
    "query_group",
    "query_item",
    "client_goal",
    "change_request",
    "platform_account",
    "account_authorization",
    "browser_profile",
    "session_lease",
    "resource_lease",
    "collection_run",
    "collection_task",
    "intervention_request",
    "session_event",
    "revocation_request",
]

# Retrospective-poisoning fix (2026-08-10): this revision originally created its
# tables via `Base.metadata.create_all(checkfirst=True)`. `Base.metadata`
# resolves to *today's* full ORM model set, so replaying the chain on an empty
# database pre-created later revisions' tables (e.g. client_profile_version) in
# their final shape, and the explicit op.create_table/op.add_column statements
# in later revisions then failed with DuplicateTable/DuplicateColumn. The
# create_all is replaced with explicit op.create_table statements reproducing
# exactly the 30 tables this revision owned when introduced (anchor commit
# c8b7ec8), with the era column shapes and constraint/index names; the
# downgrade likewise drops that explicit set instead of drop_all over today's
# metadata. Databases that already applied this revision never re-run it, so
# editing the old revision is safe.


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_service_account", sa.Boolean(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("subject", name="uq_app_user_subject"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_app_user_pub_id",
        "app_user",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_table(
        "permission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permission"),
        sa.UniqueConstraint("name", name="uq_permission_name"),
        schema="platform",
    )
    op.create_table(
        "platform_adapter",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("admission_level", sa.String(length=30), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("last_passed_at", sa.DateTime(timezone=True)),
        sa.Column("next_review_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_platform_adapter"),
        sa.UniqueConstraint("pub_id", name="uq_platform_adapter_pub_id"),
        sa.UniqueConstraint("slug", name="uq_platform_adapter_slug"),
        schema="platform",
    )
    op.create_table(
        "role",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_role"),
        sa.UniqueConstraint("name", name="uq_role_name"),
        schema="platform",
    )
    op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenant"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_tenant_pub_id",
        "tenant",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_pub_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_pub_id", sa.String(length=30), nullable=False),
        sa.Column("receipt", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_audit_log_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.UniqueConstraint("pub_id", name="uq_audit_log_pub_id"),
        schema="platform",
    )
    op.create_table(
        "customer",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("external_ref", sa.String(length=200)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_customer_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_customer_pub_id",
        "customer",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_customer_tenant_id",
        "customer",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_membership_tenant_id_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.app_user.id"],
            name="fk_membership_user_id_app_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_membership"),
        sa.UniqueConstraint("pub_id", name="uq_membership_pub_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_id"),
        schema="platform",
    )
    op.create_table(
        "platform_account",
        sa.Column("adapter_id", sa.Uuid(), nullable=False),
        sa.Column("owner_pub_id", sa.String(length=30), nullable=False),
        sa.Column("account_mask", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("responsible_pub_id", sa.String(length=30), nullable=False),
        sa.Column("custody_mode", sa.String(length=30), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("admission_level", sa.String(length=30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["adapter_id"],
            ["platform.platform_adapter.id"],
            name="fk_platform_account_adapter_id_platform_adapter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_platform_account_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_platform_account"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_platform_account_pub_id",
        "platform_account",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_platform_account_tenant_id",
        "platform_account",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "resource_lease",
        sa.Column("resource_kind", sa.String(length=30), nullable=False),
        sa.Column("resource_pub_id", sa.String(length=30), nullable=False),
        sa.Column("holder", sa.String(length=160), nullable=False),
        sa.Column("capability_json", sa.Text(), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_resource_lease_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_lease"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_resource_lease_pub_id",
        "resource_lease",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_resource_lease_tenant_id",
        "resource_lease",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "role_permission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["platform.permission.id"],
            name="fk_role_permission_permission_id_permission",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["platform.role.id"],
            name="fk_role_permission_role_id_role",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_permission"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission_role_id"),
        schema="platform",
    )
    op.create_table(
        "account_authorization",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("forbidden_actions_json", sa.Text(), nullable=False),
        sa.Column("regions_json", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_account_authorization_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_account_authorization_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_authorization"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_account_authorization_pub_id",
        "account_authorization",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_account_authorization_tenant_id",
        "account_authorization",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "browser_profile",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("custody_mode", sa.String(length=30), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary()),
        sa.Column("nonce", sa.LargeBinary()),
        sa.Column("wrapped_dek", sa.LargeBinary()),
        sa.Column("ciphertext_sha256", sa.String(length=64)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_browser_profile_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_browser_profile_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_browser_profile"),
        sa.UniqueConstraint(
            "account_id",
            "profile_version",
            name="uq_browser_profile_account_id",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_browser_profile_pub_id",
        "browser_profile",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_browser_profile_tenant_id",
        "browser_profile",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "project",
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["platform.customer.id"],
            name="fk_project_customer_id_customer",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_project_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_project_pub_id",
        "project",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_project_tenant_id",
        "project",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "revocation_request",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.String(length=500), nullable=False),
        sa.Column("deletion_verified_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_revocation_request_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_revocation_request_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_revocation_request"),
        sa.UniqueConstraint("workflow_id", name="uq_revocation_request_workflow_id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_revocation_request_pub_id",
        "revocation_request",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_revocation_request_tenant_id",
        "revocation_request",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "session_event",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_session_event_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_session_event_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_event"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_event_pub_id",
        "session_event",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_event_tenant_id",
        "session_event",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "brand",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("website", sa.String(length=500)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_brand_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_brand_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_brand_pub_id",
        "brand",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index("ix_platform_brand_tenant_id", "brand", ["tenant_id"], schema="platform")
    op.create_table(
        "change_request",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("requested_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("reviewed_by", sa.String(length=30)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_change_request_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_change_request_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_change_request"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_change_request_pub_id",
        "change_request",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_change_request_tenant_id",
        "change_request",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "client_goal",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=80), nullable=False),
        sa.Column("target_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_client_goal_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_client_goal_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_goal"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_client_goal_pub_id",
        "client_goal",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_client_goal_tenant_id",
        "client_goal",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "competitor",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("website", sa.String(length=500)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_competitor_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_competitor_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_competitor"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_competitor_pub_id",
        "competitor",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_competitor_tenant_id",
        "competitor",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "monitoring_config",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_monitoring_config_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_monitoring_config_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monitoring_config"),
        sa.UniqueConstraint("project_id", name="uq_monitoring_config_project_id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_monitoring_config_pub_id",
        "monitoring_config",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_monitoring_config_tenant_id",
        "monitoring_config",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "query_group",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_query_group_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_query_group_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_query_group"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_query_group_pub_id",
        "query_group",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_query_group_tenant_id",
        "query_group",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "session_lease",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("holder", sa.String(length=160), nullable=False),
        sa.Column("capability", sa.String(length=30), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_session_lease_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["platform.browser_profile.id"],
            name="fk_session_lease_profile_id_browser_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_session_lease_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_lease"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_lease_pub_id",
        "session_lease",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_lease_tenant_id",
        "session_lease",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "brand_alias",
        sa.Column("brand_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["platform.brand.id"],
            name="fk_brand_alias_brand_id_brand",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_brand_alias_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_alias"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_brand_alias_pub_id",
        "brand_alias",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_brand_alias_tenant_id",
        "brand_alias",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "brand_asset",
        sa.Column("brand_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("uri", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["platform.brand.id"],
            name="fk_brand_asset_brand_id_brand",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_brand_asset_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_asset"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_brand_asset_pub_id",
        "brand_asset",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_brand_asset_tenant_id",
        "brand_asset",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "monitoring_config_version",
        sa.Column("config_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["platform.monitoring_config.id"],
            name="fk_monitoring_config_version_config_id_monitoring_config",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_monitoring_config_version_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monitoring_config_version"),
        sa.UniqueConstraint(
            "config_id",
            "revision",
            name="uq_monitoring_config_version_config_id",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_monitoring_config_version_pub_id",
        "monitoring_config_version",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_monitoring_config_version_tenant_id",
        "monitoring_config_version",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "query_item",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["platform.query_group.id"],
            name="fk_query_item_group_id_query_group",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_query_item_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_query_item"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_query_item_pub_id",
        "query_item",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_query_item_tenant_id",
        "query_item",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "collection_run",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("workflow_id", sa.String(length=500), nullable=False),
        sa.Column("temporal_run_id", sa.String(length=80)),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("total_tasks", sa.Integer(), nullable=False),
        sa.Column("completed_tasks", sa.Integer(), nullable=False),
        sa.Column("failed_tasks", sa.Integer(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_version_id"],
            ["platform.monitoring_config_version.id"],
            name="fk_collection_run_config_version_id_monitoring_config_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["platform.project.id"],
            name="fk_collection_run_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_collection_run_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_run"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_collection_run_tenant_id"),
        sa.UniqueConstraint("workflow_id", name="uq_collection_run_workflow_id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_collection_run_pub_id",
        "collection_run",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_collection_run_tenant_id",
        "collection_run",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "collection_task",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("matrix_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("answer_text", sa.Text()),
        sa.Column("screenshot_ref", sa.String(length=500)),
        sa.Column("quality_state", sa.String(length=40)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["platform.collection_run.id"],
            name="fk_collection_task_run_id_collection_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_collection_task_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_task"),
        sa.UniqueConstraint("run_id", "business_key", name="uq_collection_task_run_id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_collection_task_pub_id",
        "collection_task",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_collection_task_tenant_id",
        "collection_task",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "intervention_request",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("challenge_type", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("allowed_domain", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("pairing_token_hash", sa.String(length=64)),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True)),
        sa.Column("paired_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("platform_result", sa.String(length=40)),
        sa.Column("evidence_hash", sa.String(length=64)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_intervention_request_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["platform.collection_run.id"],
            name="fk_intervention_request_run_id_collection_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_intervention_request_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_intervention_request"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_intervention_request_pub_id",
        "intervention_request",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_intervention_request_tenant_id",
        "intervention_request",
        ["tenant_id"],
        schema="platform",
    )
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON platform."{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )


def downgrade() -> None:
    op.drop_table("intervention_request", schema="platform")
    op.drop_table("collection_task", schema="platform")
    op.drop_table("collection_run", schema="platform")
    op.drop_table("query_item", schema="platform")
    op.drop_table("monitoring_config_version", schema="platform")
    op.drop_table("brand_asset", schema="platform")
    op.drop_table("brand_alias", schema="platform")
    op.drop_table("session_lease", schema="platform")
    op.drop_table("query_group", schema="platform")
    op.drop_table("monitoring_config", schema="platform")
    op.drop_table("competitor", schema="platform")
    op.drop_table("client_goal", schema="platform")
    op.drop_table("change_request", schema="platform")
    op.drop_table("brand", schema="platform")
    op.drop_table("session_event", schema="platform")
    op.drop_table("revocation_request", schema="platform")
    op.drop_table("project", schema="platform")
    op.drop_table("browser_profile", schema="platform")
    op.drop_table("account_authorization", schema="platform")
    op.drop_table("role_permission", schema="platform")
    op.drop_table("resource_lease", schema="platform")
    op.drop_table("platform_account", schema="platform")
    op.drop_table("membership", schema="platform")
    op.drop_table("customer", schema="platform")
    op.drop_table("audit_log", schema="platform")
    op.drop_table("tenant", schema="platform")
    op.drop_table("role", schema="platform")
    op.drop_table("platform_adapter", schema="platform")
    op.drop_table("permission", schema="platform")
    op.drop_table("app_user", schema="platform")
