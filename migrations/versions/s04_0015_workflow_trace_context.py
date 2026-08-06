"""Persist W3C trace context across transactional workflow start outbox.

Revision ID: s04_0015
Revises: s04_0014
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0015"
down_revision: str | Sequence[str] | None = "s04_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command
          ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command
          DROP COLUMN IF EXISTS trace_context
        """
    )
