"""Grant the runtime API role the least privileges required by posting.

Revision ID: s06_0026
Revises: s06_0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0026"
down_revision: str | Sequence[str] | None = "s06_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT USAGE ON SCHEMA posting TO geo_api;
            GRANT SELECT, INSERT, UPDATE
              ON posting.batch, posting.target, posting.attribution TO geo_api;
            GRANT SELECT, INSERT ON posting.event TO geo_api;
            GRANT USAGE, SELECT
              ON SEQUENCE posting.batch_id_seq,
                          posting.target_id_seq,
                          posting.event_id_seq,
                          posting.attribution_id_seq
              TO geo_api;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE ALL PRIVILEGES ON SEQUENCE posting.batch_id_seq,
                                               posting.target_id_seq,
                                               posting.event_id_seq,
                                               posting.attribution_id_seq
              FROM geo_api;
            REVOKE ALL PRIVILEGES
              ON posting.batch, posting.target, posting.event, posting.attribution
              FROM geo_api;
            REVOKE USAGE ON SCHEMA posting FROM geo_api;
          END IF;
        END
        $$;
        """
    )
