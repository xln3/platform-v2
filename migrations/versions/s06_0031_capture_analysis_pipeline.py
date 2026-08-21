"""Separate durable answer capture from versioned analysis jobs.

Revision ID: s06_0031
Revises: s06_catalog_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s06_0031"
down_revision: str | Sequence[str] | None = "s06_catalog_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_answer_capture_completed_outbox
          ON integration.outbox_event (tenant_pub_id,aggregate_pub_id)
          WHERE event_type='answer.capture.completed'
        """
    )
    op.create_table(
        "analysis_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("platform.tenant.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("platform.project.id"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_run.id"),
            nullable=False,
        ),
        sa.Column(
            "answer_task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_task.id"),
        ),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_pub_id", sa.String(length=30), nullable=False),
        sa.Column("analyzer_kind", sa.String(length=60), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "subject_type IN ('answer','run')",
            name="subject_type",
        ),
        sa.CheckConstraint(
            "state IN ("
            "'not_requested','queued','running','completed','partial','failed','skipped'"
            ")",
            name="state",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="input_hash",
        ),
        sa.CheckConstraint(
            "(subject_type='answer' AND answer_task_id IS NOT NULL) "
            "OR (subject_type='run' AND answer_task_id IS NULL)",
            name="subject_link",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_analysis_job_pub_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_type",
            "subject_pub_id",
            "analyzer_kind",
            "policy_version",
            name="uq_analysis_job_subject_analyzer_policy",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_analysis_job_subject",
        "analysis_job",
        ["tenant_id", "subject_type", "subject_pub_id", "created_at"],
        schema="platform",
    )
    op.create_index(
        "ix_analysis_job_run_state",
        "analysis_job",
        ["run_id", "state", "analyzer_kind"],
        schema="platform",
    )
    op.create_index(
        "ix_analysis_job_workflow",
        "analysis_job",
        ["workflow_id", "analyzer_kind"],
        schema="platform",
    )
    op.execute("ALTER TABLE platform.analysis_job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.analysis_job FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.analysis_job
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT,INSERT,UPDATE ON platform.analysis_job TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT ON platform.analysis_job TO geo_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT,INSERT,UPDATE ON platform.analysis_job TO geo_worker;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_job_workflow",
        table_name="analysis_job",
        schema="platform",
    )
    op.drop_index(
        "ix_analysis_job_run_state",
        table_name="analysis_job",
        schema="platform",
    )
    op.drop_index(
        "ix_analysis_job_subject",
        table_name="analysis_job",
        schema="platform",
    )
    op.drop_table("analysis_job", schema="platform")
    op.execute(
        """
        DELETE FROM integration.outbox_event WHERE event_type='answer.capture.completed';
        DROP INDEX IF EXISTS integration.uq_answer_capture_completed_outbox
        """
    )
