"""报价单生成编排：输入校验 → XLSX → LLM 规划 → 确定性 DOCX。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import PurePath
from zoneinfo import ZoneInfo

import httpx

from domain.evidence.dlp import assert_secret_free

from ..config import Settings
from ..intake import research
from .generator import StructuredRunner, generate_plan
from .models import QuotationDocument, QuotationPlan
from .renderer import render_quotation_docx
from .xlsx import parse_target_queries

_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class QuotationInputInvalid(ValueError):
    """品牌名或目标词中含不允许的输入。"""


@dataclass(frozen=True, slots=True)
class GeneratedQuotation:
    payload: bytes
    metadata: QuotationDocument
    plan: QuotationPlan


def normalize_brand_name(value: str) -> str:
    brand = unicodedata.normalize("NFC", str(value or ""))
    brand = re.sub(r"\s+", " ", brand).strip()
    brand = re.sub(r"[\x00-\x1f\x7f]", "", brand)
    if not 2 <= len(brand) <= 80:
        raise QuotationInputInvalid("brand_name_invalid")
    try:
        assert_secret_free(brand)
    except ValueError as exc:
        raise QuotationInputInvalid("brand_name_contains_secret") from exc
    return brand


def safe_filename(brand_name: str, quote_date: date) -> str:
    brand = _INVALID_FILENAME_RE.sub("_", brand_name).strip(" ._") or "客户"
    # PurePath.name 防御未来规则调整后意外引入路径分隔符。
    filename = f"报价单-{brand}-{quote_date:%Y%m%d}.docx"
    return PurePath(filename).name[:180]


def generate_quotation(
    *,
    brand_name: str,
    workbook_payload: bytes,
    settings: Settings,
    quote_date: date | None = None,
    requested_model: str | None = None,
    client: httpx.Client | None = None,
    runner: StructuredRunner | None = None,
) -> GeneratedQuotation:
    """生成完整报价单；默认报价日期按 Asia/Shanghai 当日。"""
    brand = normalize_brand_name(brand_name)
    effective_date = quote_date or datetime.now(_CHINA_TIMEZONE).date()
    if not 2020 <= effective_date.year <= 2100:
        raise QuotationInputInvalid("quote_date_invalid")
    queries = parse_target_queries(workbook_payload)
    try:
        for query in queries:
            assert_secret_free(query.text)
    except ValueError as exc:
        raise QuotationInputInvalid("target_words_contain_secret") from exc

    model = research.resolve_research_model(settings, requested_model)
    config = replace(research.config_from_settings(settings), model=model)
    plan = generate_plan(
        brand_name=brand,
        queries=queries,
        config=config,
        client=client,
        runner=runner,
    )
    payload = render_quotation_docx(
        brand_name=brand,
        quote_date=effective_date,
        plan=plan,
    )
    digest = hashlib.sha256(payload).hexdigest()
    metadata = QuotationDocument(
        brand_name=brand,
        quote_date=effective_date,
        filename=safe_filename(brand, effective_date),
        target_query_count=len(queries),
        selected_query_count=len(plan.selected_queries),
        opportunity_count=len(plan.opportunities),
        model=plan.model,
        sha256=digest,
    )
    return GeneratedQuotation(payload=payload, metadata=metadata, plan=plan)
