# ruff: noqa: B008
"""内部运营报价单生成 API。"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..intake import research
from .generator import QuotationGenerationFailed, QuotationLlmDisabled
from .renderer import DOCX_MIME
from .service import QuotationInputInvalid, generate_quotation
from .xlsx import MAX_UPLOAD_BYTES, TargetWorkbookInvalid

router = APIRouter(prefix="/api/v2/quotations", tags=["quotations"])
log = structlog.get_logger()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post(
    "/generate",
    operation_id="generateQuotation",
    response_class=Response,
    responses={
        200: {"content": {DOCX_MIME: {}}},
        400: {"description": "模型不在允许清单"},
        415: {"description": "目标词文件不是 XLSX"},
        422: {"description": "品牌或工作簿内容无效"},
        502: {"description": "动态内容生成失败"},
        503: {"description": "未配置 LLM"},
    },
)
def generate_quotation_document(
    brand_name: Annotated[str, Form(min_length=2, max_length=80)],
    target_words: Annotated[UploadFile, File(description="品牌方提供的优化目标词 XLSX")],
    quote_date: Annotated[date | None, Form()] = None,
    model: Annotated[str | None, Form(max_length=120)] = None,
    principal: Principal = Depends(get_principal),
) -> Response:
    """输入品牌名称和目标词工作簿，一次生成完整 GEO 报价单 DOCX。"""
    principal.require("project:write")
    if target_words.content_type not in {_XLSX_MIME, "application/octet-stream", None, ""}:
        raise HTTPException(status_code=415, detail={"code": "xlsx_content_type_required"})
    payload = target_words.file.read(MAX_UPLOAD_BYTES + 1)
    try:
        result = generate_quotation(
            brand_name=brand_name,
            workbook_payload=payload,
            settings=get_settings(),
            quote_date=quote_date,
            requested_model=model,
        )
    except research.ResearchModelNotAllowed as exc:
        raise HTTPException(status_code=400, detail={"code": "model_not_allowed"}) from exc
    except TargetWorkbookInvalid as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    except QuotationInputInvalid as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    except QuotationLlmDisabled as exc:
        raise HTTPException(status_code=503, detail={"code": "llm_disabled"}) from exc
    except QuotationGenerationFailed as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "quotation_generation_failed"},
        ) from exc

    log.info(
        "quotation_generated",
        tenant_pub_id=principal.tenant_pub_id,
        actor_pub_id=principal.actor_pub_id,
        target_query_count=result.metadata.target_query_count,
        selected_query_count=result.metadata.selected_query_count,
        opportunity_count=result.metadata.opportunity_count,
        model=result.metadata.model,
        sha256=result.metadata.sha256,
    )
    headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(result.metadata.filename),
        "X-Quotation-Target-Query-Count": str(result.metadata.target_query_count),
        "X-Quotation-Selected-Query-Count": str(result.metadata.selected_query_count),
        "X-Quotation-Opportunity-Count": str(result.metadata.opportunity_count),
        "X-Quotation-SHA256": result.metadata.sha256,
        "Cache-Control": "no-store",
    }
    return Response(content=result.payload, media_type=DOCX_MIME, headers=headers)
