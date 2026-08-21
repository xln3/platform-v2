"""Version formal-report services and allow quotation services 1 through 5.

Revision ID: s06_0031_formal_catalog
Revises: s06_catalog_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0031_formal_catalog"
down_revision: str | Sequence[str] | None = "s06_catalog_0001"
branch_labels: str | Sequence[str] | None = ("formal_catalog",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.formal_report_production
          ADD COLUMN service_catalog_version TEXT NOT NULL
            DEFAULT 'legacy_report_services_v1',
          ADD COLUMN sop_project_pub_id TEXT;

        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_service_catalog_version_ck CHECK (
            service_catalog_version IN (
              'legacy_report_services_v1','quotation_services_v2'
            )
          );

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_services_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_services_ck CHECK (
            CASE
              WHEN service_catalog_version='quotation_services_v2' THEN
                cardinality(services) BETWEEN 1 AND 5
                AND services <@ ARRAY[1,2,3,4,5]::SMALLINT[]
              ELSE
                cardinality(services) BETWEEN 1 AND 4
                AND services <@ ARRAY[1,2,3,4]::SMALLINT[]
            END
          );

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_comparison_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_comparison_ck CHECK (
            CASE
              WHEN service_catalog_version='quotation_services_v2' THEN
                (
                  before_start IS NULL AND before_end IS NULL
                  AND after_start IS NULL AND after_end IS NULL
                  AND NOT (5 = ANY(services))
                ) OR (
                  before_start IS NOT NULL AND before_end IS NOT NULL
                  AND after_start IS NOT NULL AND after_end IS NOT NULL
                  AND before_start <= before_end AND after_start <= after_end
                  AND before_end < after_start AND 5 = ANY(services)
                )
              ELSE
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
            END
          );

        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_sop_project_binding_ck CHECK (
            CASE
              WHEN service_catalog_version='quotation_services_v2'
                   AND services && ARRAY[2,5]::SMALLINT[]
                THEN sop_project_pub_id IS NOT NULL AND btrim(sop_project_pub_id) <> ''
              ELSE sop_project_pub_id IS NULL
            END
          );

        DO $$
        DECLARE check_name TEXT;
        BEGIN
          FOR check_name IN
            SELECT constraint_name
            FROM information_schema.check_constraints
            WHERE constraint_schema='reporting'
              AND constraint_name IN (
                SELECT constraint_name
                FROM information_schema.constraint_column_usage
                WHERE table_schema='reporting'
                  AND table_name='formal_report_output'
                  AND column_name='service_number'
              )
          LOOP
            EXECUTE format(
              'ALTER TABLE reporting.formal_report_output DROP CONSTRAINT %I',
              check_name
            );
          END LOOP;
        END
        $$;
        ALTER TABLE reporting.formal_report_output
          ADD CONSTRAINT formal_output_service_number_ck
          CHECK (service_number BETWEEN 1 AND 5);

        CREATE FUNCTION reporting.enforce_formal_output_service_catalog()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          parent_catalog TEXT;
          parent_services SMALLINT[];
        BEGIN
          SELECT service_catalog_version,services
            INTO parent_catalog,parent_services
          FROM reporting.formal_report_production
          WHERE tenant_pub_id=NEW.tenant_pub_id
            AND pub_id=NEW.production_pub_id
          FOR NO KEY UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'formal output production not found'
              USING ERRCODE='foreign_key_violation';
          END IF;
          IF NOT (NEW.service_number = ANY(parent_services)) THEN
            RAISE EXCEPTION 'formal output service is not selected by production'
              USING ERRCODE='check_violation';
          END IF;
          IF parent_catalog='legacy_report_services_v1'
             AND NEW.service_number NOT BETWEEN 1 AND 4 THEN
            RAISE EXCEPTION 'legacy formal output service must be between 1 and 4'
              USING ERRCODE='check_violation';
          END IF;
          IF parent_catalog='quotation_services_v2'
             AND NEW.service_number NOT BETWEEN 1 AND 5 THEN
            RAISE EXCEPTION 'quotation formal output service must be between 1 and 5'
              USING ERRCODE='check_violation';
          END IF;
          IF parent_catalog NOT IN (
            'legacy_report_services_v1','quotation_services_v2'
          ) THEN
            RAISE EXCEPTION 'formal output has unknown service catalog'
              USING ERRCODE='check_violation';
          END IF;
          RETURN NEW;
        END
        $$;

        CREATE TRIGGER formal_output_service_catalog_trg
          BEFORE INSERT OR UPDATE OF tenant_pub_id,production_pub_id,service_number
          ON reporting.formal_report_output
          FOR EACH ROW EXECUTE FUNCTION reporting.enforce_formal_output_service_catalog();

        CREATE FUNCTION reporting.prevent_formal_catalog_drift_with_outputs()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF (
            OLD.service_catalog_version IS DISTINCT FROM NEW.service_catalog_version
            OR OLD.services IS DISTINCT FROM NEW.services
          ) AND EXISTS (
            SELECT 1 FROM reporting.formal_report_output output
            WHERE output.tenant_pub_id=OLD.tenant_pub_id
              AND output.production_pub_id=OLD.pub_id
          ) THEN
            RAISE EXCEPTION 'formal production catalog is immutable after output creation'
              USING ERRCODE='check_violation';
          END IF;
          RETURN NEW;
        END
        $$;

        CREATE TRIGGER formal_production_catalog_immutable_trg
          BEFORE UPDATE OF service_catalog_version,services
          ON reporting.formal_report_production
          FOR EACH ROW EXECUTE FUNCTION reporting.prevent_formal_catalog_drift_with_outputs();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM reporting.formal_report_production
            WHERE service_catalog_version='quotation_services_v2'
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade while quotation_services_v2 productions exist';
          END IF;
        END
        $$;

        DROP TRIGGER formal_production_catalog_immutable_trg
          ON reporting.formal_report_production;
        DROP FUNCTION reporting.prevent_formal_catalog_drift_with_outputs();
        DROP TRIGGER formal_output_service_catalog_trg
          ON reporting.formal_report_output;
        DROP FUNCTION reporting.enforce_formal_output_service_catalog();

        ALTER TABLE reporting.formal_report_output
          DROP CONSTRAINT formal_output_service_number_ck;
        ALTER TABLE reporting.formal_report_output
          ADD CONSTRAINT formal_report_output_service_number_check
          CHECK (service_number BETWEEN 1 AND 4);

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_sop_project_binding_ck;
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_comparison_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_comparison_ck CHECK (
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
          );

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_services_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_services_ck CHECK (
            cardinality(services) BETWEEN 1 AND 4
            AND services <@ ARRAY[1,2,3,4]::SMALLINT[]
          );
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_service_catalog_version_ck,
          DROP COLUMN sop_project_pub_id,
          DROP COLUMN service_catalog_version;
        """
    )


__all__ = ["downgrade", "upgrade"]
