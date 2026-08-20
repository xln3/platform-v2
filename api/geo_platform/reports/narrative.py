"""报告章节 AI 起草。

口径来源（用户既定需求，非旧系统约束照搬）：
  * 提示词纪律 = 已生产验证的报告叙述提示词（proxyllm/geo_ai.generate_report_narrative，
    见 developlog/implementation/fix-20260807-174349.md §4/§8）：面向品牌管理者的客观
    客户语言、只依据给定冻结指标、不臆造数字/样本/趋势/行业事实、禁内部实现术语；
  * 模型清单 = 独立真源 ``GEO_REPORT_LLM_MODELS``（七项既定选型，首项
    ``deep-deepseek-v4-flash`` 为缺省），与 intake 调研清单互不影响——每个功能独立选模型；
  * 输出能力：**不发 max_tokens/max_completion_tokens**，模型自身上限即输出边界；
    提示词不设字数上限，篇幅由内容决定；
  * 传输 = OpenAI 兼容 ``/chat/completions``（七模型逐台实测，生产统一经 inferera）；
    **不发 temperature**（网关缺省即可：实测 kimi-k3 仅在显式发送时才有 =1 约束，
    不发全兼容；claude-* 显式发送直接 400）——请求只带 model/messages，
    不发 max_tokens/max_completion_tokens，模型自身上限即输出边界；
  * 不挂联网工具：报告内容必须落在该报告冻结窗口的指标内，联网会引入窗口外事实、
    破坏冻结语义——这是报告冻结设计本身的要求；
  * key 只走 settings（复用 GEO_RESEARCH_LLM_* 的 key/网关，不新增 LLM env，
    严禁入库/日志）；未配 key → ReportNarrativeDisabled → API 503 llm_disabled；
    主/备 base_url 各试一次仍败或空输出 → ReportNarrativeFailed → API 502 ai_draft_failed；
  * 草稿不落库：返回文本由前端填入编辑器并以 source='ai' 走既有不可变版本 +
    人工确认发布门——内容是否采用由人判断，本模块不做输出过滤。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from ..config import Settings
from ..intake import research

log = structlog.getLogger()

_TIMEOUT_SECONDS = 120.0
_MAX_FACTS = 60
_MAX_FACT_CHARS = 300
# 安全上限（防异常输出撑爆内存），不是生成限制：请求不发 max token 字段，
# 输出边界由模型/供应商自身上限决定。
_MAX_BODY_CHARS = 32768

_SYSTEM_PROMPT = (
    "你是一名面向品牌管理者的 GEO（生成式引擎优化）报告分析师。正在撰写品牌在 AI "
    "问答平台中可见度报告的指定章节。给定该报告冻结后的测试指标（品牌表现、引擎、"
    "竞品、引用来源、内容风险核查案例及其事实核查结论、官网引用能效与整改建议、"
    "优化前后对比差值等 JSON），请写出可直接交付客户的客观中文正文。\n"
    "要求：\n"
    "1) 只依据给定指标撰写，不要臆造未提供的数字、样本、趋势、投放效果或行业事实；\n"
    "2) 不要出现 live、eligible、scope、brandrank、LLM、模型名、字段名、内部规则名、"
    "方法学注记、INV 编号等系统实现术语；用「有效回答」「品牌出现率」等客户语言；\n"
    "3) 如果给定数据只覆盖一个地区、模式或日期，不要把它描述为对比或趋势；\n"
    "4) 紧扣章节标题撰写，可按内容需要自然分段或使用简短列表，篇幅由内容决定。"
)


class ReportNarrativeDisabled(RuntimeError):
    """GEO_RESEARCH_LLM_API_KEY 未配置 → API 503 llm_disabled。"""


class ReportNarrativeFailed(RuntimeError):
    """上游失败/空输出 → API 502 ai_draft_failed。"""


class ReportModelNotAllowed(RuntimeError):
    """请求模型不在 GEO_REPORT_LLM_MODELS 允许清单内 → API 400 model_not_allowed。"""


def available_report_models(settings: Settings) -> list[str]:
    """报告起草可选模型清单：GEO_REPORT_LLM_MODELS 逗号分隔，首项即缺省模型。"""
    return [m.strip() for m in settings.report_llm_models.split(",") if m.strip()][:10]


def resolve_report_model(settings: Settings, requested: str | None) -> str:
    """校验并解析本次起草用模型：空 = 清单首项；不在清单 → ReportModelNotAllowed。"""
    allowed = available_report_models(settings)
    candidate = (requested or "").strip()
    if not candidate:
        return allowed[0]
    if candidate not in allowed:
        raise ReportModelNotAllowed(candidate)
    return candidate


ClientFactory = Callable[[research.LlmConfig, str], httpx.Client]


def _default_client_factory(config: research.LlmConfig, base_url: str) -> httpx.Client:
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=_TIMEOUT_SECONDS,
        trust_env=False,
    )


def _run_prose(
    config: research.LlmConfig,
    base_url: str,
    user_prompt: str,
    client_factory: ClientFactory,
) -> str:
    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    # 纪律：只带 model/messages——不发 temperature（kimi 显式发送仅接受 1、claude 直接
    # 400，不发全兼容），不发 max_tokens/max_completion_tokens（模型自身上限即输出边界）。
    with client_factory(config, base_url) as client:
        response = client.post("/chat/completions", json=body)
    if response.status_code != 200:
        raise ReportNarrativeFailed(f"upstream_{response.status_code}")
    choices = response.json().get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        text = str((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise ReportNarrativeFailed("empty_output")
    return text


def draft_section(
    *,
    report_title: str,
    section_title: str,
    facts: list[str],
    config: research.LlmConfig,
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """为报告章节起草正文；返回 {"body","model"}。facts 为已冻结事实的紧凑文本。"""
    factory = client_factory or _default_client_factory
    if not config.api_key:
        raise ReportNarrativeDisabled("research_llm_api_key_missing")
    bounded_facts = [f[:_MAX_FACT_CHARS] for f in facts[:_MAX_FACTS] if f.strip()]
    if not bounded_facts:
        raise ReportNarrativeFailed("no_frozen_facts")
    user_prompt = (
        f"报告标题：{report_title}\n"
        f"章节标题：{section_title}\n"
        "已冻结指标（撰写唯一依据）：\n"
        + "\n".join(f"- {fact}" for fact in bounded_facts)
        + "\n请为该章节撰写正文。"
    )

    text: str | None = None
    last_error: Exception | None = None
    for base_url in dict.fromkeys([config.base_url, config.base_url_fallback]):
        if not base_url.strip():
            continue
        try:
            text = _run_prose(config, base_url, user_prompt, factory)
            break
        except httpx.HTTPError as exc:  # 网络错误 → 换备通道再试一次
            last_error = exc
            log.warning("report_narrative_retry", base_url=base_url, error=str(exc)[:200])
        except ReportNarrativeFailed as exc:
            if str(exc).startswith("upstream_5"):
                last_error = exc
                log.warning("report_narrative_retry", base_url=base_url, error=str(exc))
                continue
            raise
    if text is None:
        raise ReportNarrativeFailed("upstream_unavailable") from last_error

    return {"body": text[:_MAX_BODY_CHARS], "model": config.model}


def load_frozen_facts(dsn: str, tenant_pub_id: str, report_pub_id: str) -> tuple[str, list[str]]:
    """读报告标题 + 最新版本冻结事实（紧凑 JSON 文本）。报告不存在 → LookupError。"""
    from ..tenancy.psycopg import tenant_connection

    with tenant_connection(dsn, tenant_pub_id) as connection:
        report = connection.execute(
            "SELECT title FROM reporting.report WHERE tenant_pub_id=%s AND pub_id=%s",
            (tenant_pub_id, report_pub_id),
        ).fetchone()
        if report is None:
            raise LookupError("report not found")
        title = report[0] if not isinstance(report, dict) else report["title"]
        version = connection.execute(
            """
            SELECT pub_id FROM reporting.report_version
            WHERE tenant_pub_id=%s AND report_pub_id=%s
            ORDER BY version_number DESC LIMIT 1
            """,
            (tenant_pub_id, report_pub_id),
        ).fetchone()
        if version is None:
            raise LookupError("report version not found")
        version_pub_id = version[0] if not isinstance(version, dict) else version["pub_id"]
        rows = connection.execute(
            """
            SELECT payload FROM reporting.report_frozen_fact
            WHERE tenant_pub_id=%s AND report_version_pub_id=%s
            ORDER BY ordinal LIMIT %s
            """,
            (tenant_pub_id, version_pub_id, _MAX_FACTS + 1),
        ).fetchall()
    facts: list[str] = []
    for row in rows:
        payload = row[0] if not isinstance(row, dict) else row["payload"]
        facts.append(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            )
        )
    return str(title), facts
