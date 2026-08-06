"""Add a durable transactional outbox for Temporal workflow starts.

Revision ID: s04_0013
Revises: s04_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0013"
down_revision: str | Sequence[str] | None = "s04_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE integration.workflow_start_command (
          id bigserial PRIMARY KEY,
          command_id uuid NOT NULL UNIQUE,
          tenant_pub_id text NOT NULL,
          workflow_type text NOT NULL,
          workflow_id text NOT NULL UNIQUE,
          task_queue text NOT NULL,
          payload jsonb NOT NULL,
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending','dispatching','started')),
          attempts integer NOT NULL DEFAULT 0,
          last_error_code text,
          temporal_run_id text,
          claimed_at timestamptz,
          started_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_workflow_start_command_dispatch
        ON integration.workflow_start_command (state, claimed_at, id)
        """
    )
    op.execute(
        """
        REVOKE ALL ON integration.workflow_start_command FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'geo') THEN
            GRANT SELECT, INSERT ON integration.workflow_start_command TO geo;
            GRANT USAGE, SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'geo_worker') THEN
            GRANT SELECT, UPDATE ON integration.workflow_start_command TO geo_worker;
            GRANT USAGE, SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq TO geo_worker;
          END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration.workflow_start_command")
