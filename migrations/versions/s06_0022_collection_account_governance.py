"""采集账号治理实体层：platform schema 五张新表（设计文档 caiji-0813 §3）。

账号与浏览器升为一等实体，机制挂在实体上（检测改写状态、调度消费状态）：

- ``collection_phone_account``：账号管理页的行 = 一个手机号（身份主轴；**无地域
  列**——地域绑定下移到 手机号×平台）。转码（smsforwarder 回码）/接管（方糖推送）
  两链路联通状态挂在号上，OTP 迁库后 ``/api/v2/otp/push`` 回填 last_sms_at。
- ``collection_platform_account``：手机号 × 平台 = 五平台格。地域绑定 / 额度台账
  （quota_* 预算 + used_* 自记 + quota_reset_at 日重置点 + quota_probe_json 平台
  探测快照）/ 运行时状态机（runtime_state ∈ idle/running/quota_exhausted/muted/
  captcha/error + muted_until/quota_resume_at）/ 浏览器实例绑定都在这一层。
  UNIQUE(phone_account_id, platform) = 一号在一平台只一行。
- ``collection_region``：地域字典（「添加地域」=悟空代理购买向导的落点）。
  proxy_env_key 只存 env 键名引用，凭证明文不落库。
- ``collection_browser``：常驻浏览器实例运行时镜像（env/systemd 仍是部署真源，
  启动 sync 由后续 worker 做）。error_streak/breaker_until = 实例级失败收敛熔断。
- ``collection_account_event``：审计（状态迁移/额度地域修改/绑定变更/墙命中/
  链路测试/relay 巡检），管理页行内「最近事件」与「为什么这题没数」的事实源。

机器资源（无租户、无 RLS），照 s06_0012 browser_fence 先例；BIGSERIAL 自增主键
（实体间 FK 用 bigint），pub_id 文本唯一对外（``new_pub_id`` 生成）。
GRANT 照 s06_0014 先例（IF EXISTS DO 块）：geo/geo_worker 表 SELECT/INSERT/UPDATE
+ id 序列 USAGE,SELECT（worker 写状态、API 读管理页）；geo_api 由
tools/configure_api_runtime_role.py 的 ALTER DEFAULT PRIVILEGES 覆盖（platform
schema 在 SCHEMAS 清单内）。无任何 CHECK 约束（状态词表演进不该要 migration，
合法性校验在 account_governor 程序层）。

Revision ID: s06_0022
Revises: s06_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s06_0022"
down_revision: str | Sequence[str] | None = "s06_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collection_phone_account",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="active"),
        # 转码链路（smsforwarder 自动回码）：ok/down/untested + 最近成功收码时间
        sa.Column("sms_link_state", sa.Text(), nullable=False, server_default="untested"),
        sa.Column("last_sms_at", sa.DateTime(timezone=True), nullable=True),
        # 接管通道（方糖推送）：ok/down/untested + 最近测试时间
        sa.Column("push_link_state", sa.Text(), nullable=False, server_default="untested"),
        sa.Column("last_push_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_collection_phone_account_pub_id"),
        sa.UniqueConstraint("phone", name="uq_collection_phone_account_phone"),
        schema="platform",
    )

    op.create_table(
        "collection_region",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("region_gb", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="wukong"),
        # 代理凭证的 env 键名引用（明文不落库）与 relay systemd 单元名
        sa.Column("proxy_env_key", sa.Text(), nullable=True),
        sa.Column("relay_unit", sa.Text(), nullable=True),
        sa.Column("exit_ip_last", sa.Text(), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        # ok / down / arrears（欠费=人工标注）
        sa.Column("state", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_collection_region_pub_id"),
        sa.UniqueConstraint("region_gb", name="uq_collection_region_region_gb"),
        schema="platform",
    )

    op.create_table(
        "collection_browser",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        # = GEO_BROWSER_INSTANCES 键（<platform>_<region>）
        sa.Column("instance_key", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("region_gb", sa.Text(), nullable=True),
        sa.Column("exit_ip", sa.Text(), nullable=True),
        sa.Column("cdp_port", sa.Integer(), nullable=True),
        sa.Column("systemd_unit", sa.Text(), nullable=True),
        sa.Column("profile_path", sa.Text(), nullable=True),
        # idle / busy / captcha（实况来源 browser_fence + captcha-assist 会话）
        sa.Column("activity", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("error_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("breaker_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_collection_browser_pub_id"),
        sa.UniqueConstraint("instance_key", name="uq_collection_browser_instance_key"),
        schema="platform",
    )

    op.create_table(
        "collection_platform_account",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("phone_account_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        # 地域绑定（可空=未分配；修改走二次确认+审计事件）
        sa.Column("region_gb", sa.Text(), nullable=True),
        # 额度：预算（NULL=不限）+ 自记台账 + 日重置点 + 平台探测快照
        sa.Column("quota_day", sa.Integer(), nullable=True),
        sa.Column("quota_week", sa.Integer(), nullable=True),
        sa.Column("quota_year", sa.Integer(), nullable=True),
        sa.Column("used_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_week", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_year", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_probe_json", JSONB(), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=True),
        # 运行时状态机：idle/running/quota_exhausted/muted/captcha/error
        sa.Column("runtime_state", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("current_run_pub_id", sa.Text(), nullable=True),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_resume_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=True),
        # 一实例一平台一号（缓存隔离）
        sa.Column("browser_instance_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_collection_platform_account_pub_id"),
        sa.UniqueConstraint(
            "phone_account_id", "platform", name="uq_collection_platform_account_phone_platform"
        ),
        sa.ForeignKeyConstraint(
            ["phone_account_id"],
            ["platform.collection_phone_account.id"],
            ondelete="CASCADE",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_collection_platform_account_dispatch",
        "collection_platform_account",
        ["platform", "region_gb", "runtime_state"],
        schema="platform",
    )

    op.create_table(
        "collection_account_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("phone_account_id", sa.BigInteger(), nullable=True),
        sa.Column("platform_account_id", sa.BigInteger(), nullable=True),
        sa.Column("browser_id", sa.BigInteger(), nullable=True),
        sa.Column("region_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False, server_default="system"),
        sa.Column("old_value", JSONB(), nullable=True),
        sa.Column("new_value", JSONB(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("run_pub_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_collection_account_event_pub_id"),
        sa.ForeignKeyConstraint(["phone_account_id"], ["platform.collection_phone_account.id"]),
        sa.ForeignKeyConstraint(
            ["platform_account_id"], ["platform.collection_platform_account.id"]
        ),
        sa.ForeignKeyConstraint(["browser_id"], ["platform.collection_browser.id"]),
        sa.ForeignKeyConstraint(["region_id"], ["platform.collection_region.id"]),
        schema="platform",
    )
    op.create_index(
        "ix_collection_account_event_account_created",
        "collection_account_event",
        ["platform_account_id", "created_at"],
        schema="platform",
    )

    # GRANT 照 s06_0014 先例（IF EXISTS DO 块）；geo_api 由
    # tools/configure_api_runtime_role.py 的 default privileges 覆盖
    op.execute(
        """
        DO $$
        DECLARE
          t TEXT;
        BEGIN
          FOREACH t IN ARRAY ARRAY[
            'collection_phone_account', 'collection_platform_account',
            'collection_region', 'collection_browser', 'collection_account_event'
          ] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
              EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON platform.%I TO geo', t);
              EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE platform.%I TO geo', t || '_id_seq');
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
              EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON platform.%I TO geo_worker', t);
              EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE platform.%I TO geo_worker', t || '_id_seq');
            END IF;
          END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("collection_account_event", schema="platform")
    op.drop_table("collection_platform_account", schema="platform")
    op.drop_table("collection_browser", schema="platform")
    op.drop_table("collection_region", schema="platform")
    op.drop_table("collection_phone_account", schema="platform")
