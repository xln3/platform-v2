"""Add durable signal-command idempotency receipts.

Revision ID: s04_0018
Revises: s04_0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0018"
down_revision: str | Sequence[str] | None = "s04_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_signal_command
          ADD COLUMN idempotency_key_hash text,
          ADD COLUMN contract_hash text
        """
    )
    op.execute(
        """
        UPDATE integration.workflow_signal_command
        SET idempotency_key_hash=encode(
              digest('legacy:' || command_id::text,'sha256'),'hex'
            ),
            contract_hash=encode(
              digest(
                workflow_id || ':' || signal_name || ':' || args::text,
                'sha256'
              ),
              'hex'
            )
        """
    )
    op.execute(
        """
        ALTER TABLE integration.workflow_signal_command
          ALTER COLUMN idempotency_key_hash SET NOT NULL,
          ALTER COLUMN contract_hash SET NOT NULL,
          ADD CONSTRAINT uq_workflow_signal_idempotency
            UNIQUE (tenant_pub_id,idempotency_key_hash)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_signal_command
          DROP CONSTRAINT IF EXISTS uq_workflow_signal_idempotency,
          DROP COLUMN IF EXISTS contract_hash,
          DROP COLUMN IF EXISTS idempotency_key_hash
        """
    )
