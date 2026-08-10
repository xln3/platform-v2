"""Add scoped worker service credentials.

Revision ID: s01_0003
Revises: s01_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s01_0003"
down_revision: str | Sequence[str] | None = "s01_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Retrospective-poisoning fix (2026-08-10): this revision originally created its
# table via `Base.metadata.create_all(checkfirst=True)`. `Base.metadata`
# resolves to *today's* full ORM model set, so replaying the chain on an empty
# database pre-created later revisions' tables (e.g. client_profile_version) in
# their final shape, and the explicit op.create_table/op.add_column statements
# in later revisions then failed with DuplicateTable/DuplicateColumn. The
# create_all is replaced with an explicit op.create_table reproducing exactly
# the service_credential shape this revision owned when introduced (anchor
# commit c8b7ec8), with era constraint/index names. Databases that already
# applied this revision never re-run it, so editing the old revision is safe.


def upgrade() -> None:
    op.create_table(
        "service_credential",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name="fk_service_credential_tenant_id_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.app_user.id"],
            name="fk_service_credential_user_id_app_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_credential"),
        sa.UniqueConstraint("pub_id", name="uq_service_credential_pub_id"),
        sa.UniqueConstraint("secret_hash", name="uq_service_credential_secret_hash"),
        schema="platform",
    )
    op.execute('ALTER TABLE platform."service_credential" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON platform."service_credential" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_table("service_credential", schema="platform")
