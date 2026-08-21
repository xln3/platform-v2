"""Merge source-page inspection and formal service-catalog branches.

Revision ID: s06_0033
Revises: s06_0032, s06_0031_formal_catalog

This merge has no schema operations.  Deployments that only authorize the formal
catalog migration must target ``s06_0031_formal_catalog`` explicitly and must not
upgrade to this merge revision until the source-page branch is separately approved.
"""

from collections.abc import Sequence

revision: str = "s06_0033"
down_revision: str | Sequence[str] | None = (
    "s06_0032",
    "s06_0031_formal_catalog",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass


__all__ = ["downgrade", "upgrade"]
