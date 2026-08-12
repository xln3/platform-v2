"""Harden formal-report identity, concurrency, tenant binding, and ACLs.

Revision ID: s06_0019
Revises: s06_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0019"
down_revision: str | Sequence[str] | None = "s06_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every output must close the same tenant across production, report, and the
    # version that actually belongs to that report.
    op.execute(
        """
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT uq_formal_production_tenant_pub_id
          UNIQUE (tenant_pub_id,pub_id);
        ALTER TABLE reporting.report
          ADD CONSTRAINT uq_report_tenant_pub_id
          UNIQUE (tenant_pub_id,pub_id);
        ALTER TABLE reporting.report_version
          ADD CONSTRAINT uq_report_version_tenant_report_pub_id
          UNIQUE (tenant_pub_id,report_pub_id,pub_id);
        ALTER TABLE reporting.formal_report_output
          DROP CONSTRAINT formal_report_output_production_pub_id_fkey,
          DROP CONSTRAINT formal_report_output_report_pub_id_fkey,
          DROP CONSTRAINT formal_report_output_report_version_pub_id_fkey,
          ADD CONSTRAINT formal_report_output_production_fk
            FOREIGN KEY (tenant_pub_id,production_pub_id)
            REFERENCES reporting.formal_report_production(tenant_pub_id,pub_id),
          ADD CONSTRAINT formal_report_output_report_fk
            FOREIGN KEY (tenant_pub_id,report_pub_id)
            REFERENCES reporting.report(tenant_pub_id,pub_id),
          ADD CONSTRAINT formal_report_output_report_version_fk
            FOREIGN KEY (tenant_pub_id,report_pub_id,report_version_pub_id)
            REFERENCES reporting.report_version(tenant_pub_id,report_pub_id,pub_id)
        """
    )
    op.execute(
        """
        ALTER TABLE reporting.formal_report_production
          ADD COLUMN review_request_hash TEXT,
          ADD CONSTRAINT formal_review_request_hash_ck CHECK (
            review_request_hash IS NULL OR review_request_hash ~ '^[0-9a-f]{64}$'
          );
        CREATE UNIQUE INDEX uq_formal_report_production_tenant_active
          ON reporting.formal_report_production (tenant_pub_id)
          WHERE status IN ('queued','running')
        """
    )
    # A dangling or cross-tenant historical signal makes this migration fail
    # closed instead of being silently rebound to another tenant's workflow.
    op.execute(
        """
        ALTER TABLE integration.workflow_start_command
          ADD CONSTRAINT uq_workflow_start_tenant_workflow
          UNIQUE (tenant_pub_id,workflow_id);
        ALTER TABLE integration.workflow_signal_command
          ADD CONSTRAINT workflow_signal_tenant_start_fk
          FOREIGN KEY (tenant_pub_id,workflow_id)
          REFERENCES integration.workflow_start_command(tenant_pub_id,workflow_id)
        """
    )
    op.execute(
        """
        REVOKE ALL ON reporting.formal_report_production FROM PUBLIC;
        REVOKE ALL ON reporting.formal_report_output FROM PUBLIC;
        REVOKE ALL ON integration.workflow_start_command FROM PUBLIC;
        REVOKE ALL ON integration.workflow_signal_command FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            REVOKE ALL ON reporting.formal_report_production FROM geo;
            REVOKE ALL ON reporting.formal_report_output FROM geo;
            REVOKE ALL ON integration.workflow_start_command FROM geo;
            REVOKE ALL ON integration.workflow_signal_command FROM geo;
            REVOKE ALL ON SEQUENCE reporting.formal_report_production_id_seq FROM geo;
            REVOKE ALL ON SEQUENCE reporting.formal_report_output_id_seq FROM geo;
            REVOKE ALL ON SEQUENCE integration.workflow_start_command_id_seq FROM geo;
            REVOKE ALL ON SEQUENCE integration.workflow_signal_command_id_seq FROM geo;
            GRANT SELECT,INSERT,UPDATE ON reporting.formal_report_production TO geo;
            GRANT SELECT,INSERT ON reporting.formal_report_output TO geo;
            GRANT SELECT,INSERT,UPDATE ON integration.workflow_start_command TO geo;
            GRANT SELECT,INSERT,UPDATE ON integration.workflow_signal_command TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_production_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_output_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_signal_command_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE ALL ON reporting.formal_report_production FROM geo_api;
            REVOKE ALL ON reporting.formal_report_output FROM geo_api;
            REVOKE ALL ON integration.workflow_start_command FROM geo_api;
            REVOKE ALL ON integration.workflow_signal_command FROM geo_api;
            REVOKE ALL ON SEQUENCE reporting.formal_report_production_id_seq FROM geo_api;
            REVOKE ALL ON SEQUENCE reporting.formal_report_output_id_seq FROM geo_api;
            REVOKE ALL ON SEQUENCE integration.workflow_start_command_id_seq FROM geo_api;
            REVOKE ALL ON SEQUENCE integration.workflow_signal_command_id_seq FROM geo_api;
            GRANT SELECT,INSERT,UPDATE ON reporting.formal_report_production TO geo_api;
            GRANT SELECT ON reporting.formal_report_output TO geo_api;
            GRANT SELECT,INSERT ON integration.workflow_start_command TO geo_api;
            GRANT SELECT,INSERT ON integration.workflow_signal_command TO geo_api;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_production_id_seq TO geo_api;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_start_command_id_seq TO geo_api;
            GRANT USAGE,SELECT ON SEQUENCE
              integration.workflow_signal_command_id_seq TO geo_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            REVOKE ALL ON reporting.formal_report_production FROM geo_worker;
            REVOKE ALL ON reporting.formal_report_output FROM geo_worker;
            REVOKE ALL ON integration.workflow_start_command FROM geo_worker;
            REVOKE ALL ON integration.workflow_signal_command FROM geo_worker;
            REVOKE ALL ON SEQUENCE reporting.formal_report_production_id_seq FROM geo_worker;
            REVOKE ALL ON SEQUENCE reporting.formal_report_output_id_seq FROM geo_worker;
            REVOKE ALL ON SEQUENCE integration.workflow_start_command_id_seq FROM geo_worker;
            REVOKE ALL ON SEQUENCE integration.workflow_signal_command_id_seq FROM geo_worker;
            GRANT SELECT,UPDATE ON reporting.formal_report_production TO geo_worker;
            GRANT SELECT,INSERT ON reporting.formal_report_output TO geo_worker;
            GRANT SELECT,UPDATE ON integration.workflow_start_command TO geo_worker;
            GRANT SELECT,UPDATE ON integration.workflow_signal_command TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_output_id_seq TO geo_worker;
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.workflow_signal_command
          DROP CONSTRAINT IF EXISTS workflow_signal_tenant_start_fk;
        ALTER TABLE integration.workflow_start_command
          DROP CONSTRAINT IF EXISTS uq_workflow_start_tenant_workflow;
        DROP INDEX IF EXISTS reporting.uq_formal_report_production_tenant_active;
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT IF EXISTS formal_review_request_hash_ck,
          DROP COLUMN IF EXISTS review_request_hash;
        ALTER TABLE reporting.formal_report_output
          DROP CONSTRAINT IF EXISTS formal_report_output_production_fk,
          DROP CONSTRAINT IF EXISTS formal_report_output_report_fk,
          DROP CONSTRAINT IF EXISTS formal_report_output_report_version_fk,
          ADD CONSTRAINT formal_report_output_production_pub_id_fkey
            FOREIGN KEY (production_pub_id)
            REFERENCES reporting.formal_report_production(pub_id),
          ADD CONSTRAINT formal_report_output_report_pub_id_fkey
            FOREIGN KEY (report_pub_id)
            REFERENCES reporting.report(pub_id),
          ADD CONSTRAINT formal_report_output_report_version_pub_id_fkey
            FOREIGN KEY (report_version_pub_id)
            REFERENCES reporting.report_version(pub_id);
        ALTER TABLE reporting.report_version
          DROP CONSTRAINT IF EXISTS uq_report_version_tenant_report_pub_id;
        ALTER TABLE reporting.report
          DROP CONSTRAINT IF EXISTS uq_report_tenant_pub_id;
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT IF EXISTS uq_formal_production_tenant_pub_id
        """
    )
