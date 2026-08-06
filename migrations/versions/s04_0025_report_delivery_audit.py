"""Make report delivery and confirmation audit events idempotent.

Revision ID: s04_0025
Revises: s04_0024
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0025"
down_revision: str | Sequence[str] | None = "s04_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_report_event_delivery_transition
          ON reporting.report_event
            (tenant_pub_id,report_pub_id,event_type,((data->>'delivery_pub_id')))
          WHERE event_type IN ('delivered','delivery_confirmed');
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS reporting.uq_report_event_delivery_transition")
