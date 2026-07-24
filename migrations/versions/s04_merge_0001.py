"""Merge the independently delivered S01 and S02 migration heads.

Revision ID: s04_merge_0001
Revises: s01_0005, s02_0008
"""

from collections.abc import Sequence

revision: str = "s04_merge_0001"
down_revision: tuple[str, str] = ("s01_0005", "s02_0008")
branch_labels: str | Sequence[str] | None = "s04"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge-only revision; both parent schemas are already authoritative."""


def downgrade() -> None:
    """Return to the two independent heads without changing either schema."""
