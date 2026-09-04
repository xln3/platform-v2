"""采集账号占用租约（采集账号占用模型，2026-09-01 起）。

- ``collection_platform_account.reservation_expires_at``（timestamptz NULL）：
  run 认领账号（idle→running）时写入 now+``GEO_ACCOUNT_RESERVATION_TTL_S``
  （缺省 6h，须覆盖 captcha 挂起上限 3×60min+余量）；owned 复用命中时续约；
  run 终态释放/回收时清空。NULL = 存量行从未持有租约，惰性回收视同已过期。
- ``platform.collection_run_terminal_state(p_run_pub_id)``：SECURITY DEFINER
  只读函数，按 pub_id 返回 ``platform.collection_run.state``（无行 → NULL）。
  collection_run 是 FORCE RLS 租户表，geo_worker/geo_api 无 BYPASSRLS 直查恒
  空集；函数 owner = 迁移角色（geo，BYPASSRLS），为「占用账号的持有 run 是否
  已终态」这一治理判定开一个窄只读口（只暴露 state 文本，不暴露任何租户
  数据列）。search_path 钉死防劫持；EXECUTE 只授 geo/geo_worker/geo_api。

Revision ID: s19_0001_account_lease
Revises: s18_0003_metrics_v2_failure
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s19_0001_account_lease"
down_revision: str | Sequence[str] | None = "s18_0003_metrics_v2_failure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_platform_account",
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform.collection_run_terminal_state(p_run_pub_id text)
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = platform, pg_catalog
        AS $$
          SELECT state FROM platform.collection_run WHERE pub_id = p_run_pub_id;
        $$;
        """
    )
    # GRANT 照 s06_0022 先例（IF EXISTS DO 块）：worker 治理消费（惰性回收/清扫）
    # 与 api 管理端都需要执行这个窄只读口。
    op.execute(
        """
        DO $$
        DECLARE
          r TEXT;
        BEGIN
          FOREACH r IN ARRAY ARRAY['geo', 'geo_worker', 'geo_api'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format(
                'GRANT EXECUTE ON FUNCTION platform.collection_run_terminal_state(text) TO %I',
                r
              );
            END IF;
          END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform.collection_run_terminal_state(text)")
    op.drop_column("collection_platform_account", "reservation_expires_at", schema="platform")


__all__ = ["downgrade", "upgrade"]
