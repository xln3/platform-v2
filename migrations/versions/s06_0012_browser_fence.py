"""常驻浏览器跨 worker fencing：platform.browser_fence 表。

机器资源 lease（无租户、无 RLS）：platform 单行唯一，fencing_token 单调
递增（含抢占），holder 进程崩溃靠 expires_at 兜底回收。
契约层=workflows/activities/resident_browser.py（GEO_BROWSER_FENCING=db 缺省）。

Revision ID: s06_0012
Revises: s06_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0012"
down_revision: str | Sequence[str] | None = "s06_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_fence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=False),
        sa.Column("holder", sa.String(length=160), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", name="uq_browser_fence_platform"),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("browser_fence", schema="platform")
