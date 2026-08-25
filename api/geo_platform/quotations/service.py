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
from .catalog import PackageCode
from .generator import StructuredRunner, generate_plan
from .models import (
    QuotationArtifactKind,
    QuotationConfiguration,
    QuotationDocument,
    QuotationPlan,
    TargetQuery,
)
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
    plan: QuotationPlan | None


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


_PACKAGE_FILE_LABEL: dict[PackageCode, str] = {
    "geo_effect_assessment": "GEO效果评测",
    "minimum_validation": "GEO最小验证",
    "custom": "GEO自定义",
}
_ARTIFACT_FILE_LABEL: dict[QuotationArtifactKind, str] = {
    "complete": "",
    "quote_table": "-报价单表格",
    "query_appendix": "-查询附件",
}


def safe_filename(
    brand_name: str,
    quote_date: date,
    package_code: PackageCode,
    artifact_kind: QuotationArtifactKind = "complete",
) -> str:
    brand = _INVALID_FILENAME_RE.sub("_", brand_name).strip(" ._") or "客户"
    # PurePath.name 防御未来规则调整后意外引入路径分隔符。
    filename = (
        f"非最终模板合规产物-报价单-{brand}-{_PACKAGE_FILE_LABEL[package_code]}"
        f"{_ARTIFACT_FILE_LABEL[artifact_kind]}-{quote_date:%Y%m%d}.docx"
    )
    return PurePath(filename).name[:180]


def generate_quotation(
    *,
    brand_name: str,
    configuration: QuotationConfiguration,
    workbook_payload: bytes | None,
    settings: Settings,
    quote_date: date | None = None,
    requested_model: str | None = None,
    client: httpx.Client | None = None,
    runner: StructuredRunner | None = None,
) -> GeneratedQuotation:
    """按 artifact_kind 生成报价单表格、查询附件或完整报价单。"""
    brand = normalize_brand_name(brand_name)
    effective_date = quote_date or datetime.now(_CHINA_TIMEZONE).date()
    if not 2020 <= effective_date.year <= 2100:
        raise QuotationInputInvalid("quote_date_invalid")
    try:
        for value in (
            configuration.website_url,
            configuration.official_site_citation_url,
            configuration.commercial_note,
        ):
            if value:
                assert_secret_free(value)
    except ValueError as exc:
        raise QuotationInputInvalid("quotation_configuration_contains_secret") from exc
    queries: list[TargetQuery] = []
    plan: QuotationPlan | None = None
    model = "deterministic-template"
    has_query_service = any(
        code in configuration.service_codes for code in ("ranking_test", "content_publishing_pilot")
    )
    if configuration.artifact_kind == "query_appendix":
        if workbook_payload is None:
            raise QuotationInputInvalid("query_appendix_workbook_required")
        if not has_query_service:
            raise QuotationInputInvalid("query_appendix_service_required")
    if workbook_payload is not None:
        queries = parse_target_queries(workbook_payload)
        try:
            for query in queries:
                assert_secret_free(query.group)
                assert_secret_free(query.text)
        except ValueError as exc:
            raise QuotationInputInvalid("target_words_contain_secret") from exc

        include_selected_queries = "ranking_test" in configuration.service_codes
        include_opportunities = "content_publishing_pilot" in configuration.service_codes
        should_generate_plan = configuration.artifact_kind != "quote_table" and (
            include_selected_queries or include_opportunities
        )
        if should_generate_plan:
            model = research.resolve_research_model(settings, requested_model)
            config = replace(research.config_from_settings(settings), model=model)
            plan = generate_plan(
                brand_name=brand,
                queries=queries,
                config=config,
                client=client,
                runner=runner,
                include_selected_queries=include_selected_queries,
                include_opportunities=include_opportunities,
            )
    payload = render_quotation_docx(
        brand_name=brand,
        quote_date=effective_date,
        configuration=configuration,
        plan=plan,
    )
    digest = hashlib.sha256(payload).hexdigest()
    metadata = QuotationDocument(
        brand_name=brand,
        quote_date=effective_date,
        filename=safe_filename(
            brand,
            effective_date,
            configuration.package_code,
            configuration.artifact_kind,
        ),
        package_code=configuration.package_code,
        artifact_kind=configuration.artifact_kind,
        service_count=len(configuration.service_quotes),
        pricing_status=configuration.pricing_status,
        total_price_cents=configuration.total_price_cents,
        maximum_total_price_cents=configuration.maximum_total_price_cents,
        target_query_count=len(queries),
        selected_query_count=len(plan.selected_queries) if plan else 0,
        opportunity_count=len(plan.opportunities) if plan else 0,
        query_appendix_included=plan is not None,
        model=plan.model if plan else model,
        sha256=digest,
    )
    return GeneratedQuotation(payload=payload, metadata=metadata, plan=plan)
