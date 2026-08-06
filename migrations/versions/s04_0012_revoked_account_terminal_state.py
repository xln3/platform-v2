"""Make account revocation an irreversible database terminal state.

Revision ID: s04_0012
Revises: s04_0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0012"
down_revision: str | Sequence[str] | None = "s04_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.prevent_revoked_account_reactivation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.state = 'revoked' AND NEW.state <> 'revoked' THEN
            RAISE EXCEPTION 'revoked_account_is_terminal'
              USING ERRCODE = '23514',
                    CONSTRAINT = 'ck_platform_account_revoked_terminal';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_platform_account_revoked_terminal
        BEFORE UPDATE OF state ON platform.platform_account
        FOR EACH ROW
        EXECUTE FUNCTION platform.prevent_revoked_account_reactivation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_platform_account_revoked_terminal
        ON platform.platform_account
        """
    )
    op.execute("DROP FUNCTION IF EXISTS platform.prevent_revoked_account_reactivation()")
