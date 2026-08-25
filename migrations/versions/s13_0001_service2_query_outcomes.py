"""Record terminal per-query outcomes for Service 2 batch coverage.

A collection run is only the immutable selection envelope.  Successful tasks
inside ``completed_with_failures`` runs remain admissible; failed tasks are
preserved as explicit coverage gaps instead of discarding the whole run or
pretending that the missing answers produced zero U occurrences.

Revision ID: s13_0001_service2_query_outcomes
Revises: s11_0001_execution_partitions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s13_0001_service2_query_outcomes"
down_revision: str | Sequence[str] | None = "s11_0001_execution_partitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service2_corpus_batch_query",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_pub_id", sa.String(length=30), nullable=False),
        sa.Column("answer_task_id", sa.Uuid(), nullable=False),
        sa.Column("answer_task_pub_id", sa.String(length=30), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("task_state", sa.String(length=30), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("answer_present", sa.Boolean(), nullable=False),
        sa.Column("u_occurrence_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "tenant_id", "project_id"],
            [
                "platform.service2_corpus_batch.id",
                "platform.service2_corpus_batch.tenant_id",
                "platform.service2_corpus_batch.project_id",
            ],
            name="fk_service2_batch_query_batch_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "project_id"],
            [
                "platform.collection_run.id",
                "platform.collection_run.tenant_id",
                "platform.collection_run.project_id",
            ],
            name="fk_service2_batch_query_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["answer_task_id", "tenant_id", "run_id"],
            [
                "platform.collection_task.id",
                "platform.collection_task.tenant_id",
                "platform.collection_task.run_id",
            ],
            name="fk_service2_batch_query_task_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service2_corpus_batch_query"),
        sa.UniqueConstraint("pub_id", name="uq_service2_batch_query_pub_id"),
        sa.UniqueConstraint("batch_id", "answer_task_id", name="uq_service2_batch_query_task"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_service2_batch_query_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_service2_batch_query_ordinal")),
        sa.CheckConstraint(
            "task_state IN ('done','failed')", name=op.f("ck_service2_batch_query_terminal_state")
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded','failed')", name=op.f("ck_service2_batch_query_outcome")
        ),
        sa.CheckConstraint("u_occurrence_count >= 0", name=op.f("ck_service2_batch_query_u_count")),
        sa.CheckConstraint(
            "(outcome='succeeded' AND task_state='done' AND answer_present) OR "
            "(outcome='failed' AND task_state='failed')",
            name=op.f("ck_service2_batch_query_truth"),
        ),
        sa.CheckConstraint(
            "outcome <> 'failed' OR failure_code IS NOT NULL",
            name=op.f("ck_service2_batch_query_failure_reason"),
        ),
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_query_outcome",
        "service2_corpus_batch_query",
        ["batch_id", "outcome", "ordinal"],
        schema="platform",
    )
    op.create_index(
        "ix_service2_batch_query_run",
        "service2_corpus_batch_query",
        ["batch_id", "run_id", "ordinal"],
        schema="platform",
    )
    op.execute("ALTER TABLE platform.service2_corpus_batch_query ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.service2_corpus_batch_query FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform.service2_corpus_batch_query
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("REVOKE ALL ON platform.service2_corpus_batch_query FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER trg_service2_batch_query_frozen_guard
          BEFORE INSERT OR UPDATE OR DELETE ON platform.service2_corpus_batch_query
          FOR EACH ROW EXECUTE FUNCTION platform.service2_guard_frozen_batch()
        """
    )
    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker','geo_api'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format(
                'GRANT SELECT,INSERT ON platform.service2_corpus_batch_query TO %I',
                role_name
              );
            END IF;
          END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM platform.service2_corpus_batch_query LIMIT 1) THEN
            RAISE EXCEPTION 'service2_query_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.drop_table("service2_corpus_batch_query", schema="platform")


__all__ = ["downgrade", "upgrade"]
