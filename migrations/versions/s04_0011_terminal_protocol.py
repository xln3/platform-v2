"""Add customer-terminal device bindings and signed tasks.

Revision ID: s04_0011
Revises: s04_0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0011"
down_revision: str | Sequence[str] | None = "s04_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.device_binding (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id),
          account_id UUID NOT NULL REFERENCES platform.platform_account(id),
          public_key BYTEA NOT NULL CHECK (octet_length(public_key) = 32),
          public_key_sha256 VARCHAR(64) NOT NULL CHECK (public_key_sha256 ~ '^[0-9a-f]{64}$'),
          label VARCHAR(80) NOT NULL,
          state VARCHAR(30) NOT NULL DEFAULT 'active',
          last_used_at TIMESTAMPTZ,
          revoked_at TIMESTAMPTZ,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (account_id, public_key_sha256)
        );
        CREATE TABLE platform.terminal_task (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id),
          intervention_id UUID NOT NULL UNIQUE REFERENCES platform.intervention_request(id),
          device_binding_id UUID NOT NULL REFERENCES platform.device_binding(id),
          nonce_sha256 VARCHAR(64) NOT NULL UNIQUE CHECK (nonce_sha256 ~ '^[0-9a-f]{64}$'),
          payload_json TEXT NOT NULL,
          server_signature BYTEA NOT NULL CHECK (octet_length(server_signature) = 64),
          expires_at TIMESTAMPTZ NOT NULL,
          state VARCHAR(30) NOT NULL DEFAULT 'issued',
          consumed_at TIMESTAMPTZ,
          result VARCHAR(30),
          evidence_hash VARCHAR(64),
          version INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for table in ("device_binding", "terminal_task"):
        op.execute(f"ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY tenant_isolation ON platform.{table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"""
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.terminal_task")
    op.execute("DROP TABLE IF EXISTS platform.device_binding")
