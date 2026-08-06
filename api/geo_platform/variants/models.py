"""W5 变体与种子表模型（platform.query_variant / platform.variant_seed）。

幂等键均为 (project_id, normalized)：同一项目同一归一化文本只保留一行，
重跑生成只累计出处不重复建行（INV-32：每条记录必须能回溯真实出处）。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..projects.models import TenantModel
from ..tenancy.database import Base
from ..tenancy.models import now_utc

SOURCE_TYPES = frozenset(
    {
        "search_query",  # collection_task.search_queries_json（豆包 SSE 真实检索词，W1 产物）
        "answer_mining",  # collection_task.answer_text 用户口吻问句（确定性抽取）
        "matrix_gap",  # 意图矩阵空格模板生成（marginal_coverage_cell 落库）
        "llm_expansion",  # LLM 扩写（补充地位；model+prompt_version 落库）
        "recycled_zero_mention",  # 零提及回炉（analytics.answer 闭环）
    }
)

VARIANT_STATUSES = frozenset({"pending", "confirmed", "rejected"})


class VariantSeed(TenantModel, Base):
    """种子库：平台真实检索词 / 回答问句 / 零提及回炉，usage_count 即真实热度。"""

    __tablename__ = "variant_seed"
    __table_args__ = (UniqueConstraint("project_id", "normalized"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40))
    # 出处锚点：collection_task.pub_id 或回炉来源 variant pub_id（可回溯，绝不空编）。
    source_ref: Mapped[str] = mapped_column(String(500), default="")
    usage_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class QueryVariant(TenantModel, Base):
    """候选变体：INV-25 状态机 pending → confirmed/rejected，确认后才允许进 config draft。"""

    __tablename__ = "query_variant"
    __table_args__ = (UniqueConstraint("project_id", "normalized"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str] = mapped_column(String(500), default="")
    intent: Mapped[str] = mapped_column(String(20), default="未分类")
    audience: Mapped[str] = mapped_column(String(80), default="通用")
    region: Mapped[str] = mapped_column(String(80), default="通用")
    product_line: Mapped[str] = mapped_column(String(200), default="通用")
    # 边际覆盖：本变体补的格子坐标（JSON {"intent","audience","region","product_line"}）。
    marginal_coverage_cell: Mapped[str] = mapped_column(Text, default="{}")
    cluster_id: Mapped[str | None] = mapped_column(String(30))
    cluster_size: Mapped[int] = mapped_column(Integer, default=1)
    # 闭环：analytics.answer 有该问法且 mentioned>0 → verified=True。
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # LLM 扩写留痕（非扩写来源为 None）。
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
