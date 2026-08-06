"""Add privacy-preserving OIDC issuer/subject bindings.

Revision ID: s04_0010
Revises: s04_0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0010"
down_revision: str | Sequence[str] | None = "s04_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.oidc_identity_binding (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id),
          user_id UUID NOT NULL REFERENCES platform.app_user(id),
          issuer_sha256 VARCHAR(64) NOT NULL CHECK (issuer_sha256 ~ '^[0-9a-f]{64}$'),
          subject_sha256 VARCHAR(64) NOT NULL CHECK (subject_sha256 ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at TIMESTAMPTZ,
          UNIQUE (issuer_sha256, subject_sha256),
          UNIQUE (tenant_id, user_id)
        )
        """
    )
    op.execute("ALTER TABLE platform.oidc_identity_binding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.oidc_identity_binding FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.oidc_identity_binding
        USING (
          tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
        WITH CHECK (
          tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.oidc_identity_binding")
