"""Add durable ordered Temporal signal outbox.

Revision ID: s04_0017
Revises: s04_0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0017"
down_revision: str | Sequence[str] | None = "s04_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE integration.workflow_signal_command (
          id bigserial PRIMARY KEY,
          command_id uuid NOT NULL UNIQUE,
          tenant_pub_id text NOT NULL,
          workflow_id text NOT NULL,
          signal_name text NOT NULL,
          args jsonb NOT NULL DEFAULT '[]'::jsonb,
          trace_context jsonb NOT NULL DEFAULT '{}'::jsonb,
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN (
              'pending','dispatching','delivered','workflow_not_found'
            )),
          attempts integer NOT NULL DEFAULT 0,
          last_error_code text,
          claimed_at timestamptz,
          delivered_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_workflow_signal_command_dispatch
        ON integration.workflow_signal_command (state,claimed_at,id)
        """
    )
    op.execute(
        """
        REVOKE ALL ON integration.workflow_signal_command FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT,INSERT ON integration.workflow_signal_command TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_signal_command_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT,UPDATE ON integration.workflow_signal_command TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_signal_command_id_seq TO geo_worker;
          END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration.workflow_signal_command")
