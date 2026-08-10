"""P3 计算层：拉踩事实核查（T1）+ 官网诊断建议（T2）+ 己方内容通道标记。

1. ``platform.disparagement_factcheck``（T1）：W3 拉踩案例的联网事实核查结论。
   每 judgment 至多一行（judgment_pub_id UNIQUE + FK →
   platform.disparagement_judgment(pub_id)，该列有唯一索引满足 FK 引用条件；
   类型 text 对 varchar(30) 属同类型族，PG 允许跨类型 FK）。
   verdict 词表：supported / refuted / unverifiable（契约词表，一字不改）。
   诚实纪律（INV-32）：LLM 不可用/失败时【不落行】——unverifiable 只表达
   "公开渠道查不到"，绝不拿它伪装"没核查"（重跑靠 UNIQUE 幂等补查）。

2. ``platform.site_audit_suggestion``（T2）：官网诊断建议批次。一批共享
   batch_pub_id（确定性派生：tenant|run|model|prompt_version），行 pub_id =
   sha256(batch_pub_id|ordinal) 确定性派生 + ON CONFLICT DO NOTHING，重跑安全；
   sink 先查批次存在性（already_generated 跳过）。evidence_document_pub_id 不加
   FK——归属校验在程序层（必须属于本项目且为 own_site 文档，否则置 NULL）。

3. ``platform.disparagement_judgment`` 三处 additive 变更（己方内容拉踩通道）：
   - 新增 content_origin varchar(20) NOT NULL DEFAULT 'collection' +
     CHECK IN ('collection','own_content')：存量行与既有 W3 写入路径自动落
     'collection'（server_default），行为语义零变化；own_content = 己方稿件通道；
   - run_id / project_id DROP NOT NULL：己方稿件判定不依附采集 run，SOP 世界
     （sop.project）与 platform.project 无外键关联，两列对 own_content 行置 NULL。
     RLS 策略只看 tenant_id，不受影响；既有聚合按 project_id join，NULL 行自然
     不进分布。

T1/T2 按契约无 tenant 列 → 不启用 RLS（与 integration.* 同款：REVOKE ALL +
显式 GRANT）；租户隔离由读路径经 project 归属保证（platform.project 有 RLS）。
GRANT 照 s06_0013/s06_0014 先例（IF EXISTS DO 块）：geo/geo_worker 读写 +
序列 USAGE（worker 落库），geo_api 只读（API 读路径）。

Revision ID: s06_0015
Revises: s06_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0015"
down_revision: str | Sequence[str] | None = "s06_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── T1：拉踩事实核查结论 ─────────────────────────────────────────────
    op.create_table(
        "disparagement_factcheck",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("judgment_pub_id", sa.Text(), nullable=False),
        sa.Column("project_pub_id", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_disparagement_factcheck_pub_id"),
        sa.UniqueConstraint("judgment_pub_id", name="uq_disparagement_factcheck_judgment_pub_id"),
        sa.CheckConstraint(
            "verdict IN ('supported','refuted','unverifiable')",
            name="ck_disparagement_factcheck_verdict",
        ),
        sa.ForeignKeyConstraint(
            ["judgment_pub_id"],
            ["platform.disparagement_judgment.pub_id"],
            name="fk_disparagement_factcheck_judgment",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_disparagement_factcheck_project_created",
        "disparagement_factcheck",
        ["project_pub_id", "created_at"],
        schema="platform",
    )

    # ── T2：官网诊断建议 ─────────────────────────────────────────────────
    op.create_table(
        "site_audit_suggestion",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pub_id", sa.Text(), nullable=False),
        sa.Column("project_pub_id", sa.Text(), nullable=False),
        sa.Column("batch_pub_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("evidence_document_pub_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pub_id", name="uq_site_audit_suggestion_pub_id"),
        sa.CheckConstraint(
            "category IN ('content_coverage','citability','fact_consistency',"
            "'crawlability','other')",
            name="ck_site_audit_suggestion_category",
        ),
        sa.CheckConstraint(
            "severity IN ('high','medium','low')",
            name="ck_site_audit_suggestion_severity",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_site_audit_suggestion_project_created",
        "site_audit_suggestion",
        ["project_pub_id", sa.text("created_at DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_platform_site_audit_suggestion_batch",
        "site_audit_suggestion",
        ["batch_pub_id"],
        schema="platform",
    )

    # ── 己方内容通道：disparagement_judgment additive 变更 ───────────────
    op.add_column(
        "disparagement_judgment",
        sa.Column(
            "content_origin",
            sa.String(length=20),
            nullable=False,
            server_default="collection",
        ),
        schema="platform",
    )
    op.create_check_constraint(
        "ck_disparagement_judgment_content_origin",
        "disparagement_judgment",
        "content_origin IN ('collection','own_content')",
        schema="platform",
    )
    op.alter_column("disparagement_judgment", "run_id", nullable=True, schema="platform")
    op.alter_column("disparagement_judgment", "project_id", nullable=True, schema="platform")

    # ── 授权（T1/T2 无 tenant 列 → 无 RLS；读路径经 project 归属隔离）────
    op.execute(
        """
        REVOKE ALL ON platform.disparagement_factcheck FROM PUBLIC;
        REVOKE ALL ON platform.site_audit_suggestion FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT, INSERT ON platform.disparagement_factcheck TO geo;
            GRANT SELECT, INSERT ON platform.site_audit_suggestion TO geo;
            GRANT USAGE, SELECT ON SEQUENCE
              platform.disparagement_factcheck_id_seq TO geo;
            GRANT USAGE, SELECT ON SEQUENCE
              platform.site_audit_suggestion_id_seq TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT, INSERT ON platform.disparagement_factcheck TO geo_worker;
            GRANT SELECT, INSERT ON platform.site_audit_suggestion TO geo_worker;
            GRANT USAGE, SELECT ON SEQUENCE
              platform.disparagement_factcheck_id_seq TO geo_worker;
            GRANT USAGE, SELECT ON SEQUENCE
              platform.site_audit_suggestion_id_seq TO geo_worker;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT SELECT ON platform.disparagement_factcheck TO geo_api;
            GRANT SELECT ON platform.site_audit_suggestion TO geo_api;
          END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        COMMENT ON TABLE platform.disparagement_factcheck IS
          'W3 拉踩案例联网事实核查（T1）：每 judgment 至多一行，judgment_pub_id 唯一幂等；'
          'verdict 词表 supported/refuted/unverifiable；LLM 不可用/失败不落行（INV-32）。'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE platform.site_audit_suggestion IS
          '官网诊断建议（T2）：一批共享确定性 batch_pub_id（run+model+prompt_version），'
          '批次存在即跳过（already_generated）；evidence 归属程序层校验，不外键。'
        """
    )


def downgrade() -> None:
    op.alter_column("disparagement_judgment", "project_id", nullable=False, schema="platform")
    op.alter_column("disparagement_judgment", "run_id", nullable=False, schema="platform")
    op.drop_constraint(
        "ck_disparagement_judgment_content_origin",
        "disparagement_judgment",
        schema="platform",
    )
    op.drop_column("disparagement_judgment", "content_origin", schema="platform")
    op.drop_table("site_audit_suggestion", schema="platform")
    op.drop_table("disparagement_factcheck", schema="platform")
