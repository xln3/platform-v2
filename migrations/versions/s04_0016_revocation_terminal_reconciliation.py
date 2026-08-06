"""Reconcile account revocation workflow terminal state.

Revision ID: s04_0016
Revises: s04_0015
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0016"
down_revision: str | Sequence[str] | None = "s04_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.revocation_request
          ADD COLUMN error_code text
        """
    )
    op.execute("DROP INDEX integration.ix_workflow_start_command_reconcile")
    op.execute(
        """
        CREATE INDEX ix_workflow_start_command_reconcile
        ON integration.workflow_start_command (last_reconciled_at,id)
        WHERE state='started'
          AND workflow_type IN (
            'geo_collection','geo_collection_observation','account_revocation'
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX integration.ix_workflow_start_command_reconcile")
    op.execute(
        """
        CREATE INDEX ix_workflow_start_command_reconcile
        ON integration.workflow_start_command (last_reconciled_at,id)
        WHERE state='started'
          AND workflow_type IN ('geo_collection','geo_collection_observation')
        """
    )
    op.execute(
        """
        ALTER TABLE platform.revocation_request
          DROP COLUMN IF EXISTS error_code
        """
    )
