"""Scope legacy ID mappings to a source snapshot run.

Revision ID: s04_0005
Revises: s04_0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0005"
down_revision: str = "s04_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_legacy_id_map_source_entity",
        "legacy_id_map",
        schema="integration",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_legacy_id_map_run_source_entity",
        "legacy_id_map",
        ["run_id", "source_system", "entity_type", "source_pk"],
        schema="integration",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_legacy_id_map_run_source_entity",
        "legacy_id_map",
        schema="integration",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_legacy_id_map_source_entity",
        "legacy_id_map",
        ["source_system", "entity_type", "source_pk"],
        schema="integration",
    )
