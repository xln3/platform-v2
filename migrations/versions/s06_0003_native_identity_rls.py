"""Force tenant RLS on native identity credential and browser-session tables.

Revision ID: s06_0003
Revises: s06_0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0003"
down_revision: str | Sequence[str] | None = "s06_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _policy_expression() -> str:
    tenant_match = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    native_lookup = (
        "(current_user IN ('geo','geo_api') "
        "AND current_setting('app.auth_scope', true) = 'native_session')"
    )
    return f"({tenant_match} OR {native_lookup})"


def upgrade() -> None:
    expression = _policy_expression()
    for table in ("user_password_credential", "browser_session"):
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON platform."{table}" '
            f"USING {expression} WITH CHECK {expression}"
        )


def downgrade() -> None:
    for table in ("browser_session", "user_password_credential"):
        op.execute(f'DROP POLICY tenant_isolation ON platform."{table}"')
        op.execute(f'ALTER TABLE platform."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE platform."{table}" DISABLE ROW LEVEL SECURITY')
