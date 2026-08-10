"""报告事实建议 REST：报价单四指标从分析链路自动供给（只读端点，不调 LLM）。

端点（project:read 门，与 analytics/brandrank 只读端点同口径）::

    GET /api/v2/projects/{project_pub_id}/report-fact-suggestions
        ?window_days=30          时间窗（1..366，缺省 30）
        &before_start=YYYY-MM-DD &before_end=... &after_start=... &after_end=...
                                 优化前后对比（可选；四参齐全且合法才产出
                                 before_after 组，否则为 null，不报错）

返回 fact_rows 草稿（metric/value/unit/numerator/denominator/dimensions/
source/method/domain/window），人工确认后连同既有表单走 POST /api/v2/reports
（冻结/四产物/人工确认门零改动）。响应另含三个独立键的扩展组（报价单服务
2/3/4）：w3_disparagement（拉踩方向比率+典型案例+T1 事实核查）、
w2_site_audit（官网引用率/采纳率+T2 优化建议）、before_after（前后对比差值）；
扩展组不进 fact_rows 主数组（api-client 投影对主数组词表 fail-closed），
表未就绪/无数据一律优雅降级（空组+披露），绝不 500。

诚实边界：
- 项目未设 brandrank_domain → 400 domain_unset（绝不回退缺省规则包）；
- 真源值非法 → 400 unknown_domain（details.available 列可选包）；
- 空窗口/零抽取覆盖/未配置品牌 → 200 insufficient=true 的空草稿（不编造）；
- 响应只含聚合指标与分母披露：不带 answer 原文、不带任何账号/profile/会话维。

错误体与 main.py 全局 HTTPException handler 同形（error.code/message/request_id/
details）；400 需要携带 details 而全局 handler 丢弃 details，故在本层直接返回
JSONResponse（形状保持一致，照 brandrank/router.py 先例）。
"""

from __future__ import annotations

# ruff: noqa: B008
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from domain.brandrank.rules import available_domains

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from . import fact_suggestions

router = APIRouter(prefix="/api/v2/projects", tags=["reports"])


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _error(
    request: Request, status_code: int, code: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    """与 main.py 全局错误体同形的 JSONResponse（details 本层自定义填充）。"""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": code.replace("_", " "),
                "request_id": request_id if isinstance(request_id, str) else "",
                "details": details or {},
            }
        },
    )


# response_model=None 照 brandrank 端点先例：错误路径（400 带 details）直接返回
# JSONResponse，返回注解是 dict | JSONResponse 联合——FastAPI 不能从联合注解推导
# Pydantic 响应模型，必须显式关闭推导。
@router.get("/{project_pub_id}/report-fact-suggestions", response_model=None)
def report_fact_suggestions(
    request: Request,
    project_pub_id: str,
    window_days: int = Query(default=30, ge=1, le=366),
    before_start: str | None = Query(default=None, max_length=32),
    before_end: str | None = Query(default=None, max_length=32),
    after_start: str | None = Query(default=None, max_length=32),
    after_end: str | None = Query(default=None, max_length=32),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any] | JSONResponse:
    """报告事实建议草稿（报价单四指标；只读 brandrank 层，严禁 LLM）。"""
    principal.require("project:read")
    try:
        return fact_suggestions.compute_report_fact_suggestions(
            dsn=_dsn(),
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            window_days=window_days,
            before_start=before_start,
            before_end=before_end,
            after_start=after_start,
            after_end=after_end,
        )
    except fact_suggestions.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    except fact_suggestions.DomainUnset:
        return _error(
            request,
            400,
            "domain_unset",
            {"why": "项目未设置 brandrank_domain（规则包真源），请先在项目设置中选择分析域"},
        )
    except fact_suggestions.UnknownDomain as exc:
        return _error(
            request, 400, "unknown_domain", {"available": available_domains(), "why": str(exc)}
        )
