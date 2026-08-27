"""Make materialized knowledge versions immutable and uniquely numbered.

Revision ID: s17_0003_knowledge_immutable
Revises: s17_0002_knowledge_trace_details
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s17_0003_knowledge_immutable"
down_revision: str | Sequence[str] | None = "s17_0002_knowledge_trace_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assertion",
        sa.Column("assertion_key", sa.String(200), nullable=True),
        schema="knowledge",
    )
    op.execute("UPDATE knowledge.assertion SET assertion_key=pub_id")
    op.alter_column("assertion", "assertion_key", nullable=False, schema="knowledge")
    op.create_unique_constraint(
        "uq_knowledge_object_identity_version",
        "knowledge_object",
        ["tenant_pub_id", "namespace", "domain", "stable_id", "version"],
        schema="knowledge",
    )
    op.create_unique_constraint(
        "uq_assertion_identity_version",
        "assertion",
        ["tenant_pub_id", "namespace", "domain", "assertion_key", "version"],
        schema="knowledge",
    )
    for table in ("knowledge_object", "assertion"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f'ON knowledge."{table}" '
            "FOR EACH ROW EXECUTE FUNCTION knowledge.reject_mutation()"
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM knowledge.knowledge_object LIMIT 1)
             OR EXISTS (SELECT 1 FROM knowledge.assertion LIMIT 1) THEN
            RAISE EXCEPTION 'knowledge_version_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    for table in ("assertion", "knowledge_object"):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON knowledge.{table}")
    op.drop_constraint(
        "uq_assertion_identity_version",
        "assertion",
        schema="knowledge",
        type_="unique",
    )
    op.drop_constraint(
        "uq_knowledge_object_identity_version",
        "knowledge_object",
        schema="knowledge",
        type_="unique",
    )
    op.drop_column("assertion", "assertion_key", schema="knowledge")
