"""brandrank 接 fanout：analytics.answer_brand_extract 表 + project.brandrank_domain 真源列。

两件事：

1. ``analytics.answer_brand_extract``：fanout 品牌抽取落账表（W3）。每（租户，答案，
   domain）至多一行，``UNIQUE(tenant_pub_id,answer_pub_id,domain)`` + ON CONFLICT
   重写 = Temporal activity 重试/重放幂等。诚实纪律（INV-32 零合成）：
   - status 只有 ok/failed 两态；LLM 失败、未配 key（llm_disabled）、项目未设
     domain（domain_unset）一律落 failed + error 稳定错误码（异常类名不落值），
     绝不把失败伪装成空品牌列表；
   - 项目未设 brandrank_domain 时 domain 列落 ''（空串占位，NOT NULL 保住唯一键
     幂等语义——NULL 在 UNIQUE 下互不相等会重复落行）。项目后来补设 domain 后
     按真 domain 另起新行，'' 行留作"未设真源期间"的审计痕迹；
   - brands JSONB 存 LLM raw 抽取列表（归并/剔除在 metrics 层，与文件缓存同口径）。
   RLS 照 s04_0007 S02 域同款 ``app.tenant_pub_id`` 谓词（ENABLE+FORCE）；
   GRANT 照 s06_0013 先例（IF EXISTS DO 块）：geo/geo_worker 显式给
   SELECT/INSERT/UPDATE（worker upsert、API 读），geo_api 由
   tools/configure_api_runtime_role.py 的 ALTER DEFAULT PRIVILEGES 覆盖。
2. ``platform.project.brandrank_domain`` 可空列：项目级规则包 domain 真源。
   词表校验在 API 层（domain/brandrank/rules.py load_domain，非法 400），
   列本身不加 CHECK——规则包目录是可扩展的（rules_data/ 新增包不该要 migration）。

Revision ID: s06_0014
Revises: s06_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s06_0014"
down_revision: str | Sequence[str] | None = "s06_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_brand_extract",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.String(length=40), nullable=False),
        sa.Column("tenant_pub_id", sa.Text(), nullable=False),
        sa.Column("answer_pub_id", sa.Text(), nullable=False),
        # 规则包 domain；项目未设真源时落 ''（NOT NULL 保唯一键幂等，见模块 docstring）
        sa.Column("domain", sa.String(length=40), nullable=False),
        sa.Column("brands", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        # 稳定错误码/类别（llm_disabled/domain_unset/unknown_domain/api_error:…），
        # 异常类名与原始异常值不落库
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_answer_brand_extract_pub_id"),
        sa.UniqueConstraint(
            "tenant_pub_id",
            "answer_pub_id",
            "domain",
            name="uq_answer_brand_extract_tenant_answer_domain",
        ),
        # ck 命名约定会把显式名再包一层（ck_表_名_给定名）；给词缀名「status」，
        # 落库即为作者终形 ck_answer_brand_extract_status（20260812 二次包装修复）。
        sa.CheckConstraint("status IN ('ok','failed')", name="status"),
        schema="analytics",
    )
    op.create_index(
        "ix_analytics_answer_brand_extract_tenant_pub_id",
        "answer_brand_extract",
        ["tenant_pub_id"],
        schema="analytics",
    )
    # RLS：S02 域同款 app.tenant_pub_id 谓词（s04_0007 口径）
    op.execute('ALTER TABLE analytics."answer_brand_extract" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE analytics."answer_brand_extract" FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        CREATE POLICY tenant_isolation ON analytics."answer_brand_extract"
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )
    op.execute(
        """
        COMMENT ON TABLE analytics.answer_brand_extract IS
          'fanout 品牌抽取落账（W3）：每(租户,答案,domain)一行，ON CONFLICT 重写幂等；'
          'failed+error 诚实落账（INV-32 零合成），domain_unset 时 domain 落空串占位。'
        """
    )
    # GRANT 照 s06_0013/s04_0013 先例（IF EXISTS DO 块）：表 SELECT/INSERT/UPDATE
    # （worker upsert、API 读）+ id 序列 USAGE,SELECT（自增主键取值）；
    # geo_api 由 tools/configure_api_runtime_role.py 的 default privileges 覆盖
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT, INSERT, UPDATE ON analytics.answer_brand_extract TO geo;
            GRANT USAGE, SELECT ON SEQUENCE
              analytics.answer_brand_extract_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT, INSERT, UPDATE ON analytics.answer_brand_extract TO geo_worker;
            GRANT USAGE, SELECT ON SEQUENCE
              analytics.answer_brand_extract_id_seq TO geo_worker;
          END IF;
        END
        $$;
        """
    )

    # 项目级规则包 domain 真源（可空；词表校验在 API 层，列不加 CHECK）
    op.add_column(
        "project",
        sa.Column("brandrank_domain", sa.String(length=40), nullable=True),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("project", "brandrank_domain", schema="platform")
    op.drop_table("answer_brand_extract", schema="analytics")
