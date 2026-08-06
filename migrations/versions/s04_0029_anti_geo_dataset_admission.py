"""Add governed Anti-GEO calibration datasets and model admission.

Revision ID: s04_0029
Revises: s04_0028
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0029"
down_revision: str | Sequence[str] | None = "s04_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "evaluation_dataset",
    "evaluation_dataset_case",
    "evaluation_run",
    "evaluation_case_result",
    "model_admission",
)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.audit_log
          ALTER COLUMN resource_pub_id TYPE VARCHAR(255);

        CREATE TABLE intelligence.evaluation_dataset (
          id UUID PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          version TEXT NOT NULL,
          source_artifact_pub_id TEXT NOT NULL
            REFERENCES evidence.evidence_asset(pub_id),
          source_artifact_sha256 TEXT NOT NULL
            CHECK (source_artifact_sha256 ~ '^[0-9a-f]{64}$'),
          label_policy_version TEXT NOT NULL,
          labeler_count INTEGER NOT NULL CHECK (labeler_count >= 2 AND labeler_count <= 100),
          case_count INTEGER NOT NULL CHECK (case_count >= 20 AND case_count <= 10000),
          positive_count INTEGER NOT NULL CHECK (
            positive_count > 0 AND positive_count < case_count
          ),
          dataset_sha256 TEXT NOT NULL CHECK (dataset_sha256 ~ '^[0-9a-f]{64}$'),
          registration_operation_hash TEXT NOT NULL
            CHECK (registration_operation_hash ~ '^[0-9a-f]{64}$'),
          registration_contract_hash TEXT NOT NULL
            CHECK (registration_contract_hash ~ '^[0-9a-f]{64}$'),
          state TEXT NOT NULL DEFAULT 'draft'
            CHECK (state IN ('draft','approved','revoked')),
          submitted_by_pub_id TEXT NOT NULL,
          approved_by_pub_id TEXT,
          approval_rationale TEXT,
          submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          approved_at TIMESTAMPTZ,
          revoked_at TIMESTAMPTZ,
          UNIQUE (tenant_pub_id,version),
          UNIQUE (tenant_pub_id,dataset_sha256),
          UNIQUE (tenant_pub_id,registration_operation_hash),
          CHECK (
            (state='draft' AND approved_by_pub_id IS NULL AND approved_at IS NULL)
            OR
            (
              state IN ('approved','revoked')
              AND approved_by_pub_id IS NOT NULL
              AND approved_at IS NOT NULL
              AND approval_rationale IS NOT NULL
            )
          )
        );

        CREATE TABLE intelligence.evaluation_dataset_case (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tenant_pub_id TEXT NOT NULL,
          dataset_pub_id TEXT NOT NULL
            REFERENCES intelligence.evaluation_dataset(pub_id) ON DELETE CASCADE,
          case_digest TEXT NOT NULL CHECK (case_digest ~ '^[0-9a-f]{64}$'),
          propagation_cluster_digest TEXT NOT NULL
            CHECK (propagation_cluster_digest ~ '^[0-9a-f]{64}$'),
          actual_positive BOOLEAN NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,dataset_pub_id,case_digest),
          UNIQUE (tenant_pub_id,dataset_pub_id,propagation_cluster_digest)
        );

        CREATE TABLE intelligence.evaluation_run (
          id UUID PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          dataset_pub_id TEXT NOT NULL
            REFERENCES intelligence.evaluation_dataset(pub_id),
          scorer_version TEXT NOT NULL,
          decision_threshold NUMERIC(7,6) NOT NULL
            CHECK (decision_threshold > 0 AND decision_threshold < 1),
          calibration_bins INTEGER NOT NULL CHECK (calibration_bins BETWEEN 2 AND 100),
          training_cluster_manifest_sha256 TEXT NOT NULL
            CHECK (training_cluster_manifest_sha256 ~ '^[0-9a-f]{64}$'),
          training_cluster_count INTEGER NOT NULL
            CHECK (training_cluster_count >= 0 AND training_cluster_count <= 50000),
          sample_count INTEGER NOT NULL CHECK (sample_count >= 20),
          precision NUMERIC(12,10),
          recall NUMERIC(12,10),
          false_positive_rate NUMERIC(12,10),
          brier_score NUMERIC(12,10) NOT NULL,
          expected_calibration_error NUMERIC(12,10) NOT NULL,
          explanation_completeness_rate NUMERIC(12,10) NOT NULL,
          evaluation_sha256 TEXT NOT NULL CHECK (evaluation_sha256 ~ '^[0-9a-f]{64}$'),
          admission_policy_version TEXT NOT NULL,
          admission_checks JSONB NOT NULL,
          admission_passed BOOLEAN NOT NULL,
          operation_hash TEXT NOT NULL CHECK (operation_hash ~ '^[0-9a-f]{64}$'),
          contract_hash TEXT NOT NULL CHECK (contract_hash ~ '^[0-9a-f]{64}$'),
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,operation_hash)
        );

        CREATE TABLE intelligence.evaluation_case_result (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tenant_pub_id TEXT NOT NULL,
          evaluation_run_pub_id TEXT NOT NULL
            REFERENCES intelligence.evaluation_run(pub_id) ON DELETE CASCADE,
          case_digest TEXT NOT NULL CHECK (case_digest ~ '^[0-9a-f]{64}$'),
          actual_positive BOOLEAN NOT NULL,
          probability NUMERIC(7,6) NOT NULL CHECK (probability >= 0 AND probability <= 1),
          predicted_positive BOOLEAN NOT NULL,
          explanation_fields TEXT[] NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,evaluation_run_pub_id,case_digest)
        );

        CREATE TABLE intelligence.model_admission (
          id UUID PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          evaluation_run_pub_id TEXT NOT NULL
            REFERENCES intelligence.evaluation_run(pub_id),
          scorer_version TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'admitted'
            CHECK (state IN ('admitted','revoked')),
          operation_hash TEXT NOT NULL CHECK (operation_hash ~ '^[0-9a-f]{64}$'),
          contract_hash TEXT NOT NULL CHECK (contract_hash ~ '^[0-9a-f]{64}$'),
          admitted_by_pub_id TEXT NOT NULL,
          rationale TEXT NOT NULL,
          admitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at TIMESTAMPTZ,
          UNIQUE (tenant_pub_id,operation_hash)
        );

        CREATE UNIQUE INDEX uq_model_admission_active_scorer
          ON intelligence.model_admission (tenant_pub_id,scorer_version)
          WHERE state='admitted';

        CREATE INDEX ix_evaluation_dataset_state
          ON intelligence.evaluation_dataset (tenant_pub_id,state,submitted_at,pub_id);
        CREATE INDEX ix_evaluation_run_dataset
          ON intelligence.evaluation_run (tenant_pub_id,dataset_pub_id,created_at,pub_id);

        CREATE FUNCTION intelligence.retain_approved_evaluation_dataset_source()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog
        AS $$
        BEGIN
          IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL AND EXISTS (
            SELECT 1
            FROM intelligence.evaluation_dataset dataset
            WHERE dataset.source_artifact_pub_id=OLD.pub_id
              AND dataset.state='approved'
          ) THEN
            RAISE EXCEPTION
              'approved evaluation dataset source evidence cannot be deleted';
          END IF;
          RETURN NEW;
        END;
        $$;
        REVOKE ALL ON FUNCTION
          intelligence.retain_approved_evaluation_dataset_source() FROM PUBLIC;
        CREATE TRIGGER retain_approved_evaluation_dataset_source
        BEFORE UPDATE OF deleted_at ON evidence.evidence_asset
        FOR EACH ROW
        EXECUTE FUNCTION intelligence.retain_approved_evaluation_dataset_source();
        """
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE intelligence.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE intelligence.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON intelligence.{table}
            USING (
              tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
            )
            WITH CHECK (
              tenant_pub_id=NULLIF(current_setting('app.tenant_pub_id',true),'')
            )
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS retain_approved_evaluation_dataset_source
          ON evidence.evidence_asset;
        DROP FUNCTION IF EXISTS
          intelligence.retain_approved_evaluation_dataset_source();
        DROP TABLE IF EXISTS intelligence.model_admission;
        DROP TABLE IF EXISTS intelligence.evaluation_case_result;
        DROP TABLE IF EXISTS intelligence.evaluation_run;
        DROP TABLE IF EXISTS intelligence.evaluation_dataset_case;
        DROP TABLE IF EXISTS intelligence.evaluation_dataset;
        ALTER TABLE platform.audit_log
          ALTER COLUMN resource_pub_id TYPE VARCHAR(30);
        """
    )
