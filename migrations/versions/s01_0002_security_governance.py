"""Add S01 resource and credential-access governance.

Revision ID: s01_0002
Revises: s01_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s01_0002"
down_revision: str | Sequence[str] | None = "s01_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TENANT_TABLES = [
    "resource_registration",
    "session_health_check",
    "credential_access_request",
    "credential_access_approval",
]

# Retrospective-poisoning fix (2026-08-10): this revision originally created its
# tables via `Base.metadata.create_all(checkfirst=True)`. `Base.metadata`
# resolves to *today's* full ORM model set, so replaying the chain on an empty
# database pre-created later revisions' tables (e.g. client_profile_version) in
# their final shape, and the explicit op.create_table/op.add_column statements
# in later revisions then failed with DuplicateTable/DuplicateColumn. The
# create_all is replaced with explicit op.create_table statements reproducing
# exactly the 4 tables this revision owned when introduced (anchor commit
# c8b7ec8), with the era column shapes and constraint/index names. Databases
# that already applied this revision never re-run it, so editing the old
# revision is safe.


def upgrade() -> None:
    op.create_table(
        "resource_registration",
        sa.Column("resource_kind", sa.String(length=30), nullable=False),
        sa.Column("display_mask", sa.String(length=160), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_resource_registration_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_registration"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_resource_registration_pub_id",
        "resource_registration",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_resource_registration_tenant_id",
        "resource_registration",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "credential_access_request",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capability_token_hash", sa.String(length=64)),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_credential_access_request_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_credential_access_request_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credential_access_request"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_credential_access_request_pub_id",
        "credential_access_request",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_credential_access_request_tenant_id",
        "credential_access_request",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "session_health_check",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("probe_levels_json", sa.Text(), nullable=False),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("live_canary", sa.Boolean(), nullable=False),
        sa.Column("checked_by", sa.String(length=160), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_session_health_check_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_session_health_check_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_health_check"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_health_check_pub_id",
        "session_health_check",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_session_health_check_tenant_id",
        "session_health_check",
        ["tenant_id"],
        schema="platform",
    )
    op.create_table(
        "credential_access_approval",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("approver_pub_id", sa.String(length=160), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["platform.credential_access_request.id"],
            name="fk_credential_access_approval_request_id_credential_acc_3b00",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_credential_access_approval_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credential_access_approval"),
        sa.UniqueConstraint(
            "request_id",
            "approver_pub_id",
            name="uq_credential_access_approval_request_id",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_credential_access_approval_pub_id",
        "credential_access_approval",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_credential_access_approval_tenant_id",
        "credential_access_approval",
        ["tenant_id"],
        schema="platform",
    )
    for table in NEW_TENANT_TABLES:
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON platform."{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )


def downgrade() -> None:
    for table in reversed(NEW_TENANT_TABLES):
        op.drop_table(table, schema="platform")
