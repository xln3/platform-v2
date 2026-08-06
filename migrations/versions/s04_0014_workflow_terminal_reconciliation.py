"""Backfill workflow execution index and terminal reconciliation state.

Revision ID: s04_0014
Revises: s04_0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0014"
down_revision: str | Sequence[str] | None = "s04_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command
          ADD COLUMN terminal_status text,
          ADD COLUMN last_reconciled_at timestamptz
        """
    )
    op.execute(
        """
        INSERT INTO integration.workflow_start_command (
          command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
          state,attempts,temporal_run_id,started_at
        )
        SELECT
          gen_random_uuid(), tenant.pub_id, 'geo_collection_observation',
          run.workflow_id, 'historical-observation',
          jsonb_build_object('run_pub_id',run.pub_id),
          'started', 0, run.temporal_run_id, run.created_at
        FROM platform.collection_run run
        JOIN platform.tenant tenant ON tenant.id=run.tenant_id
        LEFT JOIN integration.workflow_start_command command
          ON command.workflow_id=run.workflow_id
        WHERE command.workflow_id IS NULL
          AND run.state IN ('starting','start_failed','running')
        """
    )
    op.execute(
        """
        CREATE INDEX ix_workflow_start_command_reconcile
        ON integration.workflow_start_command
          (last_reconciled_at,id)
        WHERE state='started'
          AND workflow_type IN ('geo_collection','geo_collection_observation')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS integration.ix_workflow_start_command_reconcile")
    op.execute(
        """
        DELETE FROM integration.workflow_start_command
        WHERE workflow_type='geo_collection_observation'
        """
    )
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command
          DROP COLUMN IF EXISTS last_reconciled_at,
          DROP COLUMN IF EXISTS terminal_status
        """
    )
