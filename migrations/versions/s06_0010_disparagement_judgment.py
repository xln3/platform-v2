"""W3 拉踩检测：disparagement_judgment 表。

Revision ID: s06_0010
Revises: s06_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0010"
down_revision: str | Sequence[str] | None = "s06_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disparagement_judgment",
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
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("platform.collection_run.id"),
            nullable=False,
        ),
        # answer=采集答案（subject_pub_id=collection_task.pub_id）/
        # source_document=信源正文（subject_pub_id=source_document.pub_id）
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_pub_id", sa.String(length=40), nullable=False),
        sa.Column("window_hash", sa.String(length=64), nullable=False),
        # answer → 采集 model；source_document → host（聚合"平台"维度）
        sa.Column("platform", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        # 表态主体："" = 文本/平台本身；否则为已知品牌名（拉踩方）
        sa.Column("subject_brand", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("target_brand", sa.String(length=200), nullable=False),
        # support / neutral / negative；validation_failure 行三判分字段均 NULL（判分已丢弃）
        sa.Column("attitude", sa.String(length=20)),
        sa.Column("disparagement", sa.Boolean()),
        sa.Column("evidence_quote", sa.Text()),
        sa.Column("confidence", sa.Float()),
        # llm / dictionary_experimental（LLM 不可用时词典弱判定兜底，标 experimental）
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        # disparage-v1（LLM）/ dictionary-v1（词典兜底）
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        # ok / validation_failure
        sa.Column("judgment_status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        # 幂等键：判定结果落库不重复算（重判 = 升 prompt_version 或换 model）
        sa.UniqueConstraint(
            "tenant_id",
            "subject_pub_id",
            "window_hash",
            "target_brand",
            "model",
            "prompt_version",
            name="uq_disparagement_judgment_idem",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_disparagement_judgment_pub_id",
        "disparagement_judgment",
        ["pub_id"],
        unique=True,
        schema="platform",
    )
    op.create_index(
        "ix_platform_disparagement_judgment_tenant_id",
        "disparagement_judgment",
        ["tenant_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_disparagement_judgment_project_id",
        "disparagement_judgment",
        ["project_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_disparagement_judgment_run_id",
        "disparagement_judgment",
        ["run_id"],
        schema="platform",
    )
    op.execute('ALTER TABLE platform."disparagement_judgment" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE platform."disparagement_judgment" FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        CREATE POLICY tenant_isolation ON platform."disparagement_judgment"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.drop_table("disparagement_judgment", schema="platform")
