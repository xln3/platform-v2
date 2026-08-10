"""报价单生成域模型。

模型输出只承载会随品牌变化的内容；服务正文、商务条款和 Word 样式由 renderer 固定，
避免让 LLM 改写合同口径或破坏版式。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_text(value: str) -> str:
    """用于去重的宽松归一化，不改变最终交付文案。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _one_line(value: str) -> str:
    # 交付文本保留中文全角标点；NFKC 只用于 normalize_text 去重。
    value = unicodedata.normalize("NFC", str(value or ""))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return re.sub(r"\s+", " ", value).strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetQuery(StrictModel):
    """从客户目标词工作簿读取的一条原始 Query。"""

    query_id: str = Field(pattern=r"^Q\d{3}$")
    group: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=2, max_length=200)
    sheet: str = Field(min_length=1, max_length=80)
    row: int = Field(ge=1, le=1_048_576)

    @field_validator("group", "text", "sheet", mode="before")
    @classmethod
    def clean_text_fields(cls, value: object) -> str:
        return _one_line(str(value or ""))


class ExistingQueryVariants(StrictModel):
    """附录二：从客户原始目标词中选取的代表 Query 及三类语义变体。"""

    source_id: str = Field(pattern=r"^Q\d{3}$")
    group: str = Field(min_length=1, max_length=80)
    original: str = Field(min_length=2, max_length=200)
    variant_a: str = Field(min_length=2, max_length=200)
    variant_b: str = Field(min_length=2, max_length=200)
    variant_c: str = Field(min_length=2, max_length=200)

    @field_validator("group", "original", "variant_a", "variant_b", "variant_c", mode="before")
    @classmethod
    def clean_text_fields(cls, value: object) -> str:
        return _one_line(str(value or ""))

    @model_validator(mode="after")
    def variants_are_distinct(self) -> ExistingQueryVariants:
        values = [self.original, self.variant_a, self.variant_b, self.variant_c]
        if len({normalize_text(value) for value in values}) != len(values):
            raise ValueError("query_variants_must_be_distinct")
        return self


class OpportunityVariants(StrictModel):
    """附录三：品牌相邻机会词、推荐型改写及三类语义变体。"""

    keyword: str = Field(min_length=2, max_length=120)
    optimized_query: str = Field(min_length=4, max_length=200)
    variant_a: str = Field(min_length=4, max_length=200)
    variant_b: str = Field(min_length=4, max_length=200)
    variant_c: str = Field(min_length=4, max_length=200)
    rewrite_rationale: str = Field(min_length=4, max_length=100)

    @field_validator(
        "keyword",
        "optimized_query",
        "variant_a",
        "variant_b",
        "variant_c",
        "rewrite_rationale",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, value: object) -> str:
        return _one_line(str(value or ""))

    @model_validator(mode="after")
    def variants_are_distinct(self) -> OpportunityVariants:
        values = [
            self.keyword,
            self.optimized_query,
            self.variant_a,
            self.variant_b,
            self.variant_c,
        ]
        if len({normalize_text(value) for value in values}) != len(values):
            raise ValueError("opportunity_variants_must_be_distinct")
        return self


class SourceReference(StrictModel):
    title: str = Field(default="", max_length=300)
    url: str = Field(min_length=8, max_length=2_000)

    @field_validator("title", "url", mode="before")
    @classmethod
    def clean_text_fields(cls, value: object) -> str:
        return _one_line(str(value or ""))


class QuotationPlan(StrictModel):
    """经校验后交给确定性 DOCX renderer 的动态内容。"""

    category_label: str = Field(min_length=2, max_length=50)
    sec_profile: Literal["search", "experience", "trust", "mixed"]
    category_analysis: str = Field(min_length=30, max_length=500)
    intent_diagnosis: str = Field(min_length=30, max_length=500)
    selected_queries: tuple[ExistingQueryVariants, ...]
    opportunities: tuple[OpportunityVariants, ...]
    model: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=80)
    sources: tuple[SourceReference, ...] = ()

    @field_validator(
        "category_label",
        "category_analysis",
        "intent_diagnosis",
        "model",
        "prompt_version",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, value: object) -> str:
        return _one_line(str(value or ""))

    @model_validator(mode="after")
    def collections_are_unique(self) -> QuotationPlan:
        source_ids = [row.source_id for row in self.selected_queries]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("selected_source_ids_must_be_unique")
        opportunity_keys = [normalize_text(row.keyword) for row in self.opportunities]
        if len(opportunity_keys) != len(set(opportunity_keys)):
            raise ValueError("opportunity_keywords_must_be_unique")
        optimized_keys = [normalize_text(row.optimized_query) for row in self.opportunities]
        if len(optimized_keys) != len(set(optimized_keys)):
            raise ValueError("optimized_queries_must_be_unique")
        return self


class QuotationDocument(StrictModel):
    """生成结果元数据；DOCX bytes 单独返回，避免进入 Pydantic JSON。"""

    brand_name: str = Field(min_length=2, max_length=80)
    quote_date: date
    filename: str = Field(min_length=6, max_length=180)
    target_query_count: int = Field(ge=1, le=300)
    selected_query_count: int = Field(ge=1, le=18)
    opportunity_count: int = Field(ge=1, le=16)
    model: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
