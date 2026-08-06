"""Add intake invite table (public intake form token channel).

Revision ID: s06_0006
Revises: s06_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0006"
down_revision: str | Sequence[str] | None = "s06_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _policy_expression() -> str:
    # 常规租户隔离 + 窄口子：token 解析前置查找（invite 自身还不知道 tenant），
    # 照搬 s06_0003 native_session 的 auth_scope 先例；解析后立刻注入 tenant 上下文。
    tenant_match = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    invite_lookup = (
        "(current_user IN ('geo','geo_api') "
        "AND current_setting('app.auth_scope', true) = 'intake_invite')"
    )
    return f"({tenant_match} OR {invite_lookup})"


def upgrade() -> None:
    op.create_table(
        "intake_invite",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("platform.tenant.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("platform.project.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("ai_quota", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("ai_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_intake_invite_pub_id",
        "intake_invite",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_intake_invite_tenant_id",
        "intake_invite",
        ["tenant_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_intake_invite_project_id",
        "intake_invite",
        ["project_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_intake_invite_token_hash",
        "intake_invite",
        ["token_hash"],
        unique=True,
        schema="platform",
    )
    expression = _policy_expression()
    op.execute('ALTER TABLE platform."intake_invite" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE platform."intake_invite" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON platform."intake_invite" '
        f"USING {expression} WITH CHECK {expression}"
    )


def downgrade() -> None:
    op.drop_table("intake_invite", schema="platform")
