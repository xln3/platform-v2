"""Enforce one monotonic customer delivery per report and recipient.

Revision ID: s04_0024
Revises: s04_0023
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0024"
down_revision: str | Sequence[str] | None = "s04_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_report_delivery_recipient
          ON reporting.report_delivery (tenant_pub_id,report_pub_id,recipient_pub_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS reporting.uq_report_delivery_recipient")
