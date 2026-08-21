"""Allow the API role to admit immutable page-inspection jobs.

Revision ID: s06_0035
Revises: s06_0034
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0035"
down_revision: str | Sequence[str] | None = "s06_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT INSERT ON platform.analysis_job TO geo_api;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE INSERT ON platform.analysis_job FROM geo_api;
          END IF;
        END $$
        """
    )


__all__ = ["downgrade", "upgrade"]
