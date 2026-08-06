"""Make collection run terminal states irreversible.

Revision ID: s04_0019
Revises: s04_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0019"
down_revision: str | Sequence[str] | None = "s04_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.guard_collection_run_terminal_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.state IN (
            'completed','completed_with_failures','failed','cancelled','skipped'
          ) AND NEW.state IS DISTINCT FROM OLD.state THEN
            RAISE EXCEPTION USING
              ERRCODE='23514',
              CONSTRAINT='ck_collection_run_terminal_state',
              MESSAGE='collection run terminal state is irreversible';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_collection_run_terminal_state
        BEFORE UPDATE OF state ON platform.collection_run
        FOR EACH ROW
        EXECUTE FUNCTION platform.guard_collection_run_terminal_state();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_collection_run_terminal_state
          ON platform.collection_run;
        DROP FUNCTION IF EXISTS platform.guard_collection_run_terminal_state();
        """
    )
