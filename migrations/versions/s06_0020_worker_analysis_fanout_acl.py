"""Restore worker fanout and isolate workflow outboxes by tenant.

Revision ID: s06_0020
Revises: s06_0019

``publish_downstream_event`` runs under ``geo_worker`` and atomically inserts
one ``workflow_start_command`` per completed answer.  The s06_0019 ACL
hardening retained dispatcher UPDATE but unintentionally removed that producer
INSERT path and the backing identity-sequence permission.  The same revision
also left both command tables outside RLS; this successor gives the API a
tenant-GUC policy while retaining an explicit cross-tenant dispatcher policy
for the worker role.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0020"
down_revision: str | Sequence[str] | None = "s06_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command ENABLE ROW LEVEL SECURITY;
        ALTER TABLE integration.workflow_start_command FORCE ROW LEVEL SECURITY;
        ALTER TABLE integration.workflow_signal_command ENABLE ROW LEVEL SECURITY;
        ALTER TABLE integration.workflow_signal_command FORCE ROW LEVEL SECURITY;

        CREATE POLICY workflow_outbox_geo_compat
          ON integration.workflow_start_command
          USING (current_user='geo') WITH CHECK (current_user='geo');
        CREATE POLICY workflow_outbox_api_tenant
          ON integration.workflow_start_command
          USING (
            current_user='geo_api'
            AND tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
          )
          WITH CHECK (
            current_user='geo_api'
            AND tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
          );
        CREATE POLICY workflow_outbox_worker_dispatch
          ON integration.workflow_start_command
          USING (current_user='geo_worker') WITH CHECK (current_user='geo_worker');

        CREATE POLICY workflow_outbox_geo_compat
          ON integration.workflow_signal_command
          USING (current_user='geo') WITH CHECK (current_user='geo');
        CREATE POLICY workflow_outbox_api_tenant
          ON integration.workflow_signal_command
          USING (
            current_user='geo_api'
            AND tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
          )
          WITH CHECK (
            current_user='geo_api'
            AND tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
          );
        CREATE POLICY workflow_outbox_worker_dispatch
          ON integration.workflow_signal_command
          USING (current_user='geo_worker') WITH CHECK (current_user='geo_worker');

        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT INSERT ON integration.workflow_start_command TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq TO geo_worker;
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
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            REVOKE INSERT ON integration.workflow_start_command FROM geo_worker;
            REVOKE USAGE,SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq FROM geo_worker;
          END IF;
        END
        $$;

        DROP POLICY IF EXISTS workflow_outbox_worker_dispatch
          ON integration.workflow_signal_command;
        DROP POLICY IF EXISTS workflow_outbox_api_tenant
          ON integration.workflow_signal_command;
        DROP POLICY IF EXISTS workflow_outbox_geo_compat
          ON integration.workflow_signal_command;
        ALTER TABLE integration.workflow_signal_command NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE integration.workflow_signal_command DISABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS workflow_outbox_worker_dispatch
          ON integration.workflow_start_command;
        DROP POLICY IF EXISTS workflow_outbox_api_tenant
          ON integration.workflow_start_command;
        DROP POLICY IF EXISTS workflow_outbox_geo_compat
          ON integration.workflow_start_command;
        ALTER TABLE integration.workflow_start_command NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE integration.workflow_start_command DISABLE ROW LEVEL SECURITY
        """
    )
