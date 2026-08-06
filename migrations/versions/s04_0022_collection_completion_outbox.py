"""Persist and backfill collection completion outbox events.

Revision ID: s04_0022
Revises: s04_0021
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0022"
down_revision: str | Sequence[str] | None = "s04_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_collection_run_completed_outbox
          ON integration.outbox_event (tenant_pub_id,aggregate_pub_id)
          WHERE event_type='collection.run.completed';

        INSERT INTO integration.outbox_event
          (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,occurred_at)
        SELECT
          'evt_collection_' || substr(md5(run.pub_id),1,24),
          tenant.pub_id,
          'collection.run.completed',
          run.pub_id,
          md5(run.workflow_id),
          jsonb_build_object(
            'run_pub_id',run.pub_id,
            'workflow_id',run.workflow_id,
            'state',run.state,
            'total_tasks',run.total_tasks,
            'completed_tasks',run.completed_tasks,
            'failed_tasks',run.failed_tasks
          ),
          run.updated_at
        FROM platform.collection_run run
        JOIN platform.tenant tenant ON tenant.id=run.tenant_id
        WHERE run.state IN ('completed','completed_with_failures')
        ON CONFLICT (tenant_pub_id,aggregate_pub_id)
          WHERE event_type='collection.run.completed'
        DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM integration.outbox_event
          WHERE event_type='collection.run.completed';
        DROP INDEX IF EXISTS integration.uq_collection_run_completed_outbox;
        """
    )
