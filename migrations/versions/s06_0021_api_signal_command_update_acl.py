"""Grant the API role UPDATE on the workflow signal outbox.

Revision ID: s06_0021
Revises: s06_0020

The pause/resume/cancel run-control API writes ``workflow_signal_command``
rows with ``ON CONFLICT ... DO UPDATE`` (idempotent signal admission).  The
s06 privilege model granted geo_api INSERT but not UPDATE, so production
pause/resume returned 500 until the GRANT was applied by hand during the
2026-08-13 formal collection round.  Codify that manual fix so fresh
environments converge; the tenant-GUC RLS policy from s06_0020 already
scopes which rows the UPDATE may touch.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0021"
down_revision: str | Sequence[str] | None = "s06_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT UPDATE ON integration.workflow_signal_command TO geo_api;
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE UPDATE ON integration.workflow_signal_command FROM geo_api;
          END IF;
        END
        $$
        """
    )
