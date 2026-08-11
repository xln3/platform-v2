"""基线 vs 优化后 run 组对比实体：analytics.run_comparison 表（报价单服务 4 显式实体）。

``analytics.run_comparison``：一次「基线 run 组 vs 优化后 run 组」对比的命名实体。
两臂均为 ``platform.collection_run`` pub_id 数组（JSONB text 数组，归属校验在 API
程序层——必须全部存在且属于本 tenant+project，否则 400 unknown_run_pub_id，故不
加 FK）；计算不预落库，读取时现场走 brandrank 层（api/geo_platform/brandrank/
compare.py，与报告 before_after 扩展组同一份代码），本表只存"谁跟谁比"。

- pub_id 文本主键（``new_pub_id("rcmp")`` 生成；cmp 前缀已被 platform.competitor
  占用，取 rcmp=run comparison 避歧义）；无自增 id 列 → 无序列授权；
- 两臂数组 NOT NULL + CHECK jsonb_typeof='array'（元素形状/非空/归属在 API 层
  校验，列不加元素级约束）；
- RLS 照 s06_0014 S02 域同款 ``app.tenant_pub_id`` 谓词（ENABLE+FORCE）；
- GRANT 照 s06_0014 先例（IF EXISTS DO 块）：geo SELECT/INSERT/UPDATE（迁移
  owner/运维），geo_worker 只 SELECT（worker 无写路径，按需最小化）；geo_api 由
  tools/configure_api_runtime_role.py 的 ALTER DEFAULT PRIVILEGES 覆盖
  （analytics schema 新表默认 SELECT/INSERT/UPDATE/DELETE，含本表所需的
  SELECT+INSERT）。

Revision ID: s06_0016
Revises: s06_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s06_0016"
down_revision: str | Sequence[str] | None = "s06_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_comparison",
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column("project_pub_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # 两臂 collection_run pub_id 数组（归属校验在 API 程序层，见模块 docstring）
        sa.Column("baseline_run_pub_ids", JSONB(), nullable=False),
        sa.Column("optimized_run_pub_ids", JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("pub_id", name="pk_run_comparison"),
        sa.CheckConstraint(
            "jsonb_typeof(baseline_run_pub_ids) = 'array'",
            name="baseline_runs_array",  # 词缀名，落库 ck_run_comparison_baseline_runs_array
        ),
        sa.CheckConstraint(
            "jsonb_typeof(optimized_run_pub_ids) = 'array'",
            name="optimized_runs_array",  # 同上（约定二次包装修复 20260812）
        ),
        schema="analytics",
    )
    op.create_index(
        "ix_analytics_run_comparison_tenant_project_created",
        "run_comparison",
        ["tenant_pub_id", "project_pub_id", sa.text("created_at DESC")],
        schema="analytics",
    )
    # RLS：S02 域同款 app.tenant_pub_id 谓词（s06_0014 口径）
    op.execute('ALTER TABLE analytics."run_comparison" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE analytics."run_comparison" FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        CREATE POLICY tenant_isolation ON analytics."run_comparison"
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )
    op.execute(
        """
        COMMENT ON TABLE analytics.run_comparison IS
          '基线 vs 优化后 run 组对比实体（报价单服务4）：两臂 collection_run pub_id 数组；'
          '计算读取时现场走 brandrank 层（与报告 before_after 扩展组同口径），本表不存结果。'
        """
    )
    # GRANT 照 s06_0014 先例（IF EXISTS DO 块；无序列——pub_id 文本主键）；
    # geo_api 由 tools/configure_api_runtime_role.py 的 default privileges 覆盖
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT, INSERT, UPDATE ON analytics.run_comparison TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT ON analytics.run_comparison TO geo_worker;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("run_comparison", schema="analytics")
