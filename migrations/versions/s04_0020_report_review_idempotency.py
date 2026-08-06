"""Make report workflow review persistence idempotent.

Revision ID: s04_0020
Revises: s04_0019
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0020"
down_revision: str | Sequence[str] | None = "s04_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.report_review
          ADD COLUMN workflow_operation_id TEXT;

        CREATE UNIQUE INDEX uq_report_review_workflow_operation
          ON reporting.report_review (tenant_pub_id,workflow_operation_id)
          WHERE workflow_operation_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS reporting.uq_report_review_workflow_operation;
        ALTER TABLE reporting.report_review DROP COLUMN workflow_operation_id;
        """
    )
