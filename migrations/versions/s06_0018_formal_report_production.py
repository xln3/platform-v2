"""Persist auditable formal-report production requests and service outputs.

Revision ID: s06_0018
Revises: s06_0017

The API stores only a tenant/project-scoped request.  It never accepts object-store
keys, local paths, or evidence IDs.  The request and the Temporal start command are
inserted in one transaction; workers later attach one ordinary report/version per
selected quotation service.  Keeping the outputs in the existing reporting model
preserves review, delivery, retention, and CAS integrity behaviour.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0018"
down_revision: str | Sequence[str] | None = "s06_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE reporting.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE reporting.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON reporting.{table}
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reporting.formal_report_production (
          id BIGSERIAL PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          services SMALLINT[] NOT NULL,
          window_start DATE NOT NULL,
          window_end DATE NOT NULL,
          before_start DATE,
          before_end DATE,
          after_start DATE,
          after_end DATE,
          document_status TEXT NOT NULL,
          candidate_group_strategy TEXT NOT NULL,
          idempotency_key_hash TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          workflow_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'queued',
          frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          fact_bundle JSONB,
          fact_bundle_hash TEXT,
          fact_snapshot_hash TEXT,
          rendered_bundle JSONB,
          artifact_snapshot_hash TEXT,
          error_code TEXT,
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,idempotency_key_hash),
          CONSTRAINT formal_services_ck CHECK (
            cardinality(services) BETWEEN 1 AND 4
            AND services <@ ARRAY[1,2,3,4]::SMALLINT[]
          ),
          CONSTRAINT formal_window_ck CHECK (window_start <= window_end),
          CONSTRAINT formal_status_ck CHECK (
            status IN ('queued','running','failed','awaiting_review','signed')
          ),
          CONSTRAINT formal_document_status_ck CHECK (
            document_status IN ('pre_formal','formal')
          ),
          CONSTRAINT formal_strategy_ck CHECK (
            candidate_group_strategy='evidence_completeness_v1'
          ),
          CONSTRAINT formal_hashes_ck CHECK (
            idempotency_key_hash ~ '^[0-9a-f]{64}$'
            AND request_hash ~ '^[0-9a-f]{64}$'
            AND (fact_bundle_hash IS NULL OR fact_bundle_hash ~ '^[0-9a-f]{64}$')
            AND (fact_snapshot_hash IS NULL OR fact_snapshot_hash ~ '^[0-9a-f]{64}$')
            AND (artifact_snapshot_hash IS NULL OR artifact_snapshot_hash ~ '^[0-9a-f]{64}$')
          ),
          CONSTRAINT formal_comparison_ck CHECK (
            (
              before_start IS NULL AND before_end IS NULL
              AND after_start IS NULL AND after_end IS NULL
              AND NOT (4 = ANY(services))
            ) OR (
              before_start IS NOT NULL AND before_end IS NOT NULL
              AND after_start IS NOT NULL AND after_end IS NOT NULL
              AND before_start <= before_end AND after_start <= after_end
              AND before_end < after_start AND 4 = ANY(services)
            )
          ),
          CONSTRAINT formal_fact_pair_ck CHECK (
            (fact_bundle IS NULL AND fact_bundle_hash IS NULL AND fact_snapshot_hash IS NULL)
            OR
            (fact_bundle IS NOT NULL AND fact_bundle_hash IS NOT NULL
             AND fact_snapshot_hash IS NOT NULL)
          ),
          CONSTRAINT formal_render_pair_ck CHECK (
            (rendered_bundle IS NULL AND artifact_snapshot_hash IS NULL)
            OR
            (rendered_bundle IS NOT NULL AND artifact_snapshot_hash IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE reporting.formal_report_output (
          id BIGSERIAL PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          production_pub_id TEXT NOT NULL
            REFERENCES reporting.formal_report_production(pub_id),
          service_number SMALLINT NOT NULL CHECK (service_number BETWEEN 1 AND 4),
          report_pub_id TEXT NOT NULL REFERENCES reporting.report(pub_id),
          report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
          fact_snapshot_hash TEXT NOT NULL CHECK (fact_snapshot_hash ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,production_pub_id,service_number),
          UNIQUE (tenant_pub_id,report_pub_id),
          UNIQUE (tenant_pub_id,report_version_pub_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_formal_report_production_project_created
          ON reporting.formal_report_production
          (tenant_pub_id,project_pub_id,created_at DESC,pub_id DESC);
        CREATE INDEX ix_formal_report_production_status_updated
          ON reporting.formal_report_production
          (tenant_pub_id,status,updated_at);
        CREATE INDEX ix_formal_report_output_production
          ON reporting.formal_report_output
          (tenant_pub_id,production_pub_id,service_number);
        """
    )
    _tenant_rls("formal_report_production")
    _tenant_rls("formal_report_output")

    # A formal report carries DOCX/PDF plus a machine-verifiable JSON manifest.
    op.execute(
        """
        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_check;
        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_ck;
        ALTER TABLE reporting.report_artifact
          ADD CONSTRAINT report_artifact_format_ck
          CHECK (format IN ('docx','pdf','xlsx','html','manifest'));
        """
    )
    op.execute(
        """
        REVOKE ALL ON reporting.formal_report_production FROM PUBLIC;
        REVOKE ALL ON reporting.formal_report_output FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT,INSERT,UPDATE ON reporting.formal_report_production TO geo;
            GRANT SELECT,INSERT ON reporting.formal_report_output TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_production_id_seq TO geo;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_output_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT,INSERT,UPDATE ON reporting.formal_report_production TO geo_api;
            GRANT SELECT ON reporting.formal_report_output TO geo_api;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_production_id_seq TO geo_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT,UPDATE ON reporting.formal_report_production TO geo_worker;
            GRANT SELECT,INSERT ON reporting.formal_report_output TO geo_worker;
            GRANT USAGE,SELECT ON SEQUENCE
              reporting.formal_report_output_id_seq TO geo_worker;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_ck;
        ALTER TABLE reporting.report_artifact
          ADD CONSTRAINT report_artifact_format_check
          CHECK (format IN ('docx','pdf','xlsx','html'));
        """
    )
    op.execute("DROP TABLE IF EXISTS reporting.formal_report_output")
    op.execute("DROP TABLE IF EXISTS reporting.formal_report_production")
