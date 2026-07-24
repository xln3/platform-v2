"""Add durable workflow-operation idempotency keys.

Revision ID: s02_0006
Revises: s02_0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0006"
down_revision: str | None = "s02_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.report
          ADD COLUMN workflow_operation_id TEXT;
        CREATE UNIQUE INDEX report_workflow_operation_idx
          ON reporting.report(tenant_pub_id,workflow_operation_id)
          WHERE workflow_operation_id IS NOT NULL;

        ALTER TABLE intelligence.detection_score
          ADD COLUMN workflow_operation_id TEXT;
        CREATE UNIQUE INDEX detection_score_workflow_operation_idx
          ON intelligence.detection_score(tenant_pub_id,workflow_operation_id)
          WHERE workflow_operation_id IS NOT NULL;

        ALTER TABLE intelligence.human_verdict
          ADD COLUMN workflow_operation_id TEXT;
        CREATE UNIQUE INDEX human_verdict_workflow_operation_idx
          ON intelligence.human_verdict(tenant_pub_id,workflow_operation_id)
          WHERE workflow_operation_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS intelligence.human_verdict_workflow_operation_idx;
        ALTER TABLE intelligence.human_verdict DROP COLUMN workflow_operation_id;
        DROP INDEX IF EXISTS intelligence.detection_score_workflow_operation_idx;
        ALTER TABLE intelligence.detection_score DROP COLUMN workflow_operation_id;
        DROP INDEX IF EXISTS reporting.report_workflow_operation_idx;
        ALTER TABLE reporting.report DROP COLUMN workflow_operation_id;
        """
    )
