"""W2 信源正文抓取 + 准确性核对：source_document 与 source_audit 表。

Revision ID: s06_0008
Revises: s06_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0008"
down_revision: str | Sequence[str] | None = "s06_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_table(
    name: str,
    columns: list[sa.Column[object]],
    constraints: list[sa.Constraint],
    extra_indexes: list[tuple[str, list[str], bool]] | None = None,
) -> None:
    """platform schema 租户表（RLS 按 app.tenant_id，与 s06_0004 intake 同款）。"""
    op.create_table(
        name,
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
        *columns,
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        *constraints,
        schema="platform",
    )
    op.create_index(f"ix_platform_{name}_pub_id", name, ["pub_id"], unique=True, schema="platform")
    op.create_index(f"ix_platform_{name}_tenant_id", name, ["tenant_id"], schema="platform")
    op.create_index(f"ix_platform_{name}_project_id", name, ["project_id"], schema="platform")
    for index_name, index_columns, unique in extra_indexes or []:
        op.create_index(index_name, name, index_columns, unique=unique, schema="platform")
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
    _platform_table(
        "source_document",
        [
            sa.Column(
                "run_id",
                sa.Uuid(),
                sa.ForeignKey("platform.collection_run.id"),
                nullable=False,
            ),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("url_hash", sa.String(length=64), nullable=False),
            sa.Column("host", sa.String(length=255), nullable=False),
            sa.Column("final_url", sa.Text()),
            sa.Column("http_status", sa.Integer()),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            # ok / http_error / timeout / blocked / extract_empty / fetch_skipped
            sa.Column("extract_status", sa.String(length=20), nullable=False),
            # density-extract-v1（httpx 路径）/ innertext-v1（浏览器回退）；未抽到正文为 NULL
            sa.Column("extractor", sa.String(length=40)),
            sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("text_cas_key", sa.Text()),
            sa.Column("text_sha256", sa.String(length=64)),
        ],
        constraints=[
            # 幂等键：同 run 同 URL（归一化 hash）只落一行，activity 重试/重跑直接复用
            sa.UniqueConstraint("run_id", "url_hash", name="uq_source_document_run_url"),
        ],
        extra_indexes=[
            ("ix_platform_source_document_run_id", ["run_id"], False),
        ],
    )
    _platform_table(
        "source_audit",
        [
            sa.Column(
                "source_document_id",
                sa.Uuid(),
                sa.ForeignKey("platform.source_document.id"),
                nullable=False,
            ),
            # transcript=转述准确性（豆包引述 vs 正文）/ factual=事实准确性（正文 vs 已确认事实）
            sa.Column("dimension", sa.String(length=20), nullable=False),
            # accurate / inaccurate / unsupported / unverifiable；
            # validation_failure 与 llm_* 状态行 verdict 为 NULL（判分已丢弃/未产生）
            sa.Column("verdict", sa.String(length=20)),
            sa.Column("quote_source", sa.Text()),
            sa.Column("quote_answer", sa.Text()),
            sa.Column("rationale", sa.Text()),
            # ok / validation_failure / llm_error / llm_unavailable /
            # no_confirmed_facts / unverifiable
            sa.Column("audit_status", sa.String(length=24), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("prompt_version", sa.String(length=40), nullable=False),
        ],
        constraints=[
            # 幂等键：判定结果落库不重复算（重判 = 升 prompt_version 或换 model）
            sa.UniqueConstraint(
                "source_document_id",
                "dimension",
                "model",
                "prompt_version",
                name="uq_source_audit_doc_dimension_model_prompt",
            ),
        ],
        extra_indexes=[
            ("ix_platform_source_audit_document_id", ["source_document_id"], False),
        ],
    )


def downgrade() -> None:
    op.drop_table("source_audit", schema="platform")
    op.drop_table("source_document", schema="platform")
