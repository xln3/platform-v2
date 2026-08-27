"""品牌可见度（brandrank）REST：按需计算的只读端点。

端点（project:read 门，与 analytics 只读端点同口径）::

    GET /api/v2/projects/{project_pub_id}/brand-visibility
        ?window_days=30          时间窗（1..366，缺省 30）
        &domain=insurance        显式规则包（可选；未知 → 400 unknown_domain）
        &industry=保险           显式行业 → 规则包映射（可选；未映射 → 400 unmapped_industry）
        &category=保险公司       LLM 抽取 prompt 的类别词（可选，缺省取规则包）
        &target_brand=中意人寿   目标品牌专项（可选，缺省=项目首个 brand 名）
        &competitors=中国平安    竞品专项（可重复参数；可选，缺省=项目 competitor 名单）
        &comparison_scope=ctid   场景资格 ID（可重复；场景型竞品缺省 fail-closed）
        &top_ns=3&top_ns=5       Top-N 口径（可重复，1..50，≤8 个，缺省 3/5/10）

domain 解析：显式 domain > 显式 industry > 项目真源 project.brandrank_domain；
三者皆无 → 400 brandrank_domain_unresolved（fail-loud，绝不静默回退缺省保险包）。

诚实边界：
- LLM 未配置（GEO_BRANDRANK_LLM_API_KEY 缺失）且窗内存在待抽取答案 → 503 llm_disabled
  （body details.llm 带 enabled/model/why，照旧库 503 口径）；
  全部命中缓存或窗内零答案 → 200 照常返回（extraction 账目 + llm.enabled 如实披露）。
- 响应只含聚合结果与分母披露：不带 answer 原文、不带任何账号/profile/会话维
  （本包数据源 analytics.answer/citation_fact 本无这些列）。

错误体与 main.py 全局 HTTPException handler 同形（error.code/message/request_id/details）；
400/503 需要携带 details（why/available/llm 状态）而全局 handler 丢弃 details，
故这两类错误在本层直接返回 JSONResponse（形状保持一致）。
"""

from __future__ import annotations

# ruff: noqa: B008
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from domain.brandrank.rules import available_domains
from domain.knowledge_evolution.contracts import ReasoningPolicy

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from . import service

router = APIRouter(prefix="/api/v2/projects", tags=["brandrank"])

_MAX_COMPETITORS = 20
_MAX_TOP_NS = 8
_MAX_TOP_N = 50
_MAX_COMPARISON_SCOPES = 8
_SCOPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")


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


def _parse_top_ns(values: list[str] | None) -> tuple[int, ...] | None:
    """top_ns 重复参数 → 有序去重 tuple；非整数/越界/超量 → 422。"""
    if not values:
        return None
    parsed: list[int] = []
    for raw in values:
        try:
            value = int(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_top_ns"}) from exc
        if not 1 <= value <= _MAX_TOP_N:
            raise HTTPException(status_code=422, detail={"code": "invalid_top_ns"}) from None
        parsed.append(value)
    if len(parsed) > _MAX_TOP_NS:
        raise HTTPException(status_code=422, detail={"code": "invalid_top_ns"}) from None
    return tuple(sorted(set(parsed)))


def _parse_competitors(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [v.strip() for v in values if v.strip()]
    if len(cleaned) > _MAX_COMPETITORS:
        raise HTTPException(status_code=422, detail={"code": "too_many_competitors"}) from None
    return cleaned


def _parse_comparison_scopes(values: list[str] | None) -> list[str]:
    cleaned = list(
        dict.fromkeys(value.strip().casefold() for value in values or [] if value.strip())
    )
    if len(cleaned) > _MAX_COMPARISON_SCOPES or any(
        _SCOPE_ID_RE.fullmatch(value) is None for value in cleaned
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_comparison_scope"})
    return cleaned


# response_model=None 是**有意**而非兜底：错误路径（400/503 带 details）直接返回
# JSONResponse，返回注解是 dict | JSONResponse 联合——FastAPI 不能从联合注解推导
# Pydantic 响应模型（JSONResponse 不是可序列化字段），必须显式关闭推导。
# Response 实例运行时绕过序列化，正常路径 dict 原样 JSON 化，语义与全局错误体一致。
@router.get("/{project_pub_id}/brand-visibility", response_model=None)
def brand_visibility(
    request: Request,
    project_pub_id: str,
    window_days: int = Query(default=30, ge=1, le=366),
    domain: str | None = Query(default=None, max_length=40),
    industry: str | None = Query(default=None, max_length=40),
    category: str | None = Query(default=None, max_length=50),
    target_brand: str | None = Query(default=None, max_length=200),
    competitors: list[str] | None = Query(default=None),
    comparison_scope: list[str] | None = Query(default=None),
    top_ns: list[str] | None = Query(default=None),
    reasoning_policy: ReasoningPolicy = Query(default=ReasoningPolicy.DETERMINISTIC_ONLY),
    adopt_model_inferred: bool = Query(default=False),
    on_reasoning_failure: Literal["fail", "degrade"] = Query(default="degrade"),
    allow_external_reasoning_model: bool = Query(default=False),
    max_reasoning_latency_ms: int | None = Query(default=None, ge=1, le=600_000),
    max_reasoning_cost_usd: float | None = Query(default=None, ge=0, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any] | JSONResponse:
    """品牌可见度快照（按需计算；旧报告核心口径的 V2 只读端点）。"""
    """品牌可见度快照（按需计算；旧报告核心口径的 V2 只读端点）。"""
    principal.require("project:read")
    try:
        return service.compute_brand_visibility(
            dsn=_dsn(),
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            window_days=window_days,
            domain=domain,
            industry=industry,
            category=category,
            target_brand=target_brand,
            competitors=_parse_competitors(competitors),
            comparison_scopes=_parse_comparison_scopes(comparison_scope),
            top_ns=_parse_top_ns(top_ns),
            reasoning_policy=reasoning_policy,
            adopt_model_inferred=adopt_model_inferred,
            on_reasoning_failure=on_reasoning_failure,
            allow_external_reasoning_model=allow_external_reasoning_model,
            max_reasoning_latency_ms=max_reasoning_latency_ms,
            max_reasoning_cost_usd=max_reasoning_cost_usd,
            reasoning_request_id=str(request.state.request_id),
        )
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    except service.UnmappedIndustry as exc:
        return _error(request, 400, "unmapped_industry", {"why": str(exc)})
    except service.UnknownDomain as exc:
        return _error(
            request, 400, "unknown_domain", {"available": available_domains(), "why": str(exc)}
        )
    except service.DomainUnresolved as exc:
        return _error(
            request,
            400,
            "brandrank_domain_unresolved",
            {"available": available_domains(), "why": str(exc)},
        )
    except service.LlmDisabled as exc:
        return _error(request, 503, "llm_disabled", {"llm": exc.status})
    except service.KnowledgeReasoningUnavailable as exc:
        return _error(
            request,
            503,
            "knowledge_reasoning_unavailable",
            {"why": str(exc), "reasoning_policy": reasoning_policy.value},
        )
