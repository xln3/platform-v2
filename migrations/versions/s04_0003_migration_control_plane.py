"""Add resumable legacy migration control-plane tables.

Revision ID: s04_0003
Revises: s04_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s04_0003"
down_revision: str = "s04_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "migration_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pub_id", sa.String(30), nullable=False, unique=True),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(120)),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint(
            "source_system",
            "source_snapshot_sha256",
            name="uq_migration_run_source_snapshot",
        ),
        schema="integration",
    )
    op.create_table(
        "legacy_id_map",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("source_pk", sa.String(255), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("target_pub_id", sa.String(80), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["integration.migration_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "source_system",
            "entity_type",
            "source_pk",
            name="uq_legacy_id_map_source_entity",
        ),
        schema="integration",
    )
    op.create_index(
        "ix_legacy_id_map_target",
        "legacy_id_map",
        ["entity_type", "target_pub_id"],
        schema="integration",
    )
    op.create_table(
        "migration_watermark",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("last_source_pk", sa.String(255)),
        sa.Column("rows_seen", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_written", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_skipped", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_failed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["integration.migration_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "entity_type", name="uq_migration_watermark_entity"),
        schema="integration",
    )


def downgrade() -> None:
    op.drop_table("migration_watermark", schema="integration")
    op.drop_index("ix_legacy_id_map_target", table_name="legacy_id_map", schema="integration")
    op.drop_table("legacy_id_map", schema="integration")
    op.drop_table("migration_run", schema="integration")
