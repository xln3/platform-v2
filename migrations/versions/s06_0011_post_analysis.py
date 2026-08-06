"""信源帖子取证分析（Post Analysis）：post_analysis_task 与 post_analysis_item 表。

需求规格：developlog/specs/post-analysis-20260806.md §4。
任务无 project 归属（目标品牌随任务提交），故不套用 s06_0008 的
``_platform_table``（其硬编码 project_id 非空 FK）；RLS 策略语句同款手写。

Revision ID: s06_0011
Revises: s06_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s06_0011"
down_revision: str | Sequence[str] | None = "s06_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(name: str) -> None:
    """platform schema 租户表 RLS（按 app.tenant_id，与 s06_0008 同款）。"""
    op.execute(f'ALTER TABLE platform."{name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{name}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "post_analysis_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("platform.tenant.id"),
            nullable=False,
        ),
        sa.Column("target_brand", sa.String(length=200), nullable=False),
        sa.Column(
            "target_brand_aliases",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        # queued / running / completed / partial / failed
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("url_count", sa.Integer(), nullable=False),
        sa.Column(
            "options",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        # API Idempotency-Key（可空；缺省时 pub_id 按请求体指纹派生，仍幂等）
        sa.Column("idempotency_key", sa.String(length=160)),
        sa.Column("workflow_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", sa.String(length=40), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_post_analysis_task_idem_key"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_post_analysis_task_pub_id",
        "post_analysis_task",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_post_analysis_task_tenant_id",
        "post_analysis_task",
        ["tenant_id"],
        schema="platform",
    )
    _enable_rls("post_analysis_task")

    op.create_table(
        "post_analysis_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("platform.post_analysis_task.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("platform.tenant.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False, server_default=""),
        # pending / fetching / analyzing / annotating / completed /
        # fetch_failed / analysis_failed
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        # pending / completed / failed / skipped（标注失败不毁 analysis，item 仍 completed）
        sa.Column(
            "annotation_status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("final_url", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        # innertext-v1（浏览器优先）/ density-extract-v1（httpx 兜底）
        sa.Column("extractor", sa.String(length=40)),
        sa.Column("text_cas_key", sa.Text()),
        sa.Column("text_sha256", sa.String(length=64)),
        sa.Column("screenshot_cas_key", sa.Text()),
        sa.Column("annotated_cas_key", sa.Text()),
        sa.Column("analysis", JSONB()),
        # quote 逐字校验丢弃计数/明细、事实核验失败留痕
        sa.Column("analysis_validation", JSONB()),
        # [{type,quote,note,rects,matched}]，type ∈ target_brand/disparagement/misinformation
        sa.Column("annotations", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        # 幂等键：同 task 同 URL（归一化 hash）只落一行
        sa.UniqueConstraint("task_id", "url_hash", name="uq_post_analysis_item_task_url"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_post_analysis_item_pub_id",
        "post_analysis_item",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_post_analysis_item_tenant_id",
        "post_analysis_item",
        ["tenant_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_post_analysis_item_task_id",
        "post_analysis_item",
        ["task_id"],
        schema="platform",
    )
    _enable_rls("post_analysis_item")


def downgrade() -> None:
    op.drop_table("post_analysis_item", schema="platform")
    op.drop_table("post_analysis_task", schema="platform")
