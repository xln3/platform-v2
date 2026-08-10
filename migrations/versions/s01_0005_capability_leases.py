"""Add cross-service scoped capability leases.

Revision ID: s01_0005
Revises: s01_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s01_0005"
down_revision: str | Sequence[str] | None = "s01_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Retrospective-poisoning fix (2026-08-10): this revision originally created its
# table via `Base.metadata.create_all(checkfirst=True)`. `Base.metadata`
# resolves to *today's* full ORM model set, so replaying the chain on an empty
# database pre-created later revisions' tables (e.g. client_profile_version) in
# their final shape, and the explicit op.create_table/op.add_column statements
# in later revisions then failed with DuplicateTable/DuplicateColumn. The
# create_all is replaced with an explicit op.create_table reproducing exactly
# the capability_lease shape this revision owned when introduced (anchor commit
# c8b7ec8), with era constraint/index names. Databases that already applied
# this revision never re-run it, so editing the old revision is safe.


def upgrade() -> None:
    op.create_table(
        "capability_lease",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("issued_by", sa.String(length=160), nullable=False),
        sa.Column("subject_workflow_id", sa.String(length=500), nullable=False),
        sa.Column("allowed_domains_json", sa.Text(), nullable=False),
        sa.Column("allowed_actions_json", sa.Text(), nullable=False),
        sa.Column("authorization_scope_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform.platform_account.id"],
            name="fk_capability_lease_account_id_platform_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_capability_lease_tenant_id_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capability_lease"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_capability_lease_pub_id",
        "capability_lease",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_capability_lease_tenant_id",
        "capability_lease",
        ["tenant_id"],
        schema="platform",
    )
    op.execute('ALTER TABLE platform."capability_lease" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON platform."capability_lease" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_table("capability_lease", schema="platform")
