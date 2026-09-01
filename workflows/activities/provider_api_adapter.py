"""provider_api 采集模态（ADR-0008 三采集面之一）的 v1 管线内执行体。

定位与边界（2026-08-31，用户拍板先行落地「轻松能加入的 API 版」）：

- ADR-0008 把采集面定为 provider_api / consumer_web / consumer_app 三等公民，
  但 v2 执行层（collection_v2.py）至今是 fail-closed 空壳（Stage 4-6 未建）。
  本模块是 provider_api 在 v1 采集管线的务实执行体：五平台各派生一个
  ``*_api`` adapter slug，复用 v1 的 batch 契约/persist/扇出/证据链。它
  **不是** ADR-0008 v2 路线的替代品；v2 接线完成后本模块应按 ADR 迁移。
- 口径诚实性（防污染 consumer_web 测量分母）：
  ① model/adapter slug 即 surface 判别（doubao vs doubao_api），analytics
  维度原样携带；② 本模态无地域出口测量——run_service._task_matrix 把
  provider_api 任务的 region 折叠为哨兵 ``"api"``，INV-1 geo provenance
  查不到出口声明 → geo_source=unverified → measurement_eligible=False，
  测量读面（answer_agg_blind/brandrank eligible 过滤）自动排除；
  ③ 答案/引用/检索词全部来自官方 API 响应原文，缺失即空，零合成。
- 配置门：五平台各自独立的官方 API Key（env 见下）。**Key 未配置不
  raise**——逐题落 ``wall/adapter_not_configured`` 诚实失败占位（等长结果），
  避免一个未配 Key 的平台把混合 surface 的 run 整体炸败、拖累已完成题的
  下游扇出。这与五个浏览器适配器「配置类错误允许 raise」的约定是有意的
  差异：浏览器五平台在生产必然已配置，provider_api 是按平台可选接入。
- 参考与辅助价值：doubao_api 走火山方舟 Responses API + web_search 工具，
  响应 annotations（url_citation）恢复「答案→信源 URL」关系、
  web_search_call.action.query 恢复平台真实检索词（W1）——正是 2026-08-17
  起豆包网页版 SSE 下线 text_card.summary/sitename 后信源理解缺的输入。
  yiyan_api（千帆 web_search → search_results）、tongyi_api（DashScope
  **原生协议**——兼容模式官方明确不回传搜索来源，原生
  Generation 端点 search_options.enable_source=true 回传
  output.search_info.search_results；enable_citation 开正文角标）、
  yuanbao_api（混元 enable_enhancement 搜索增强 + search_info=true 回传
  search_info 链接列表 + citation 角标；hunyuan-lite 无搜索能力，Model
  必须配支持增强的型号）均回传搜索来源。deepseek 官方 API 无联网搜索
  参数——引用为空是平台能力的诚实反映，不是抽取缺口。
- 每题原始响应 JSON 原文落证据（kind=provider_api_raw），是 sse_raw 的
  API 模态对照物：字段校准/回放重建的权威原料。

请求纪律：只发 model/messages(或 input)/联网开关与必要的成本上限参数
（max_keyword/limit）；temperature/max_tokens 等采样参数一律不发（与调研/
报告 LLM 口径一致）。密钥只进 Authorization 头，绝不落日志/payload/证据。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog
from temporalio import activity

from workflows.activities.collection import (
    PROVIDER_API_ADAPTER_SLUGS,
    CollectionBatchInput,
    CollectionBatchItemResult,
    CollectionBatchResult,
    CollectionEvidenceRef,
)

log = structlog.get_logger()

PROVIDER_API_PLATFORM_SLUGS: tuple[str, ...] = (
    "doubao_api",
    "deepseek_api",
    "yiyan_api",
    "tongyi_api",
    "yuanbao_api",
)

_DEFAULT_TIMEOUT_S = 300.0
_MAX_TIMEOUT_S = 1_800.0
_DEFAULT_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence"

ENV_EVIDENCE_DIR = "GEO_PROVIDER_API_EVIDENCE_DIR"
ENV_SHARED_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 与浏览器适配器共享证据目录的兜底项

# 错误类型词表（per-item error_type；activity 自身绝不因这些 raise）。
ERROR_NOT_CONFIGURED = "adapter_not_configured"
ERROR_UNSUPPORTED_MODE = "unsupported_mode"
ERROR_AUTH_REJECTED = "provider_api_auth_rejected"  # 401/403：Key 无效/无权限（non-retryable 语义）
ERROR_BAD_REQUEST = "provider_api_bad_request"  # 4xx：模型名/参数错误（non-retryable 语义）
ERROR_RATE_LIMITED = "provider_api_rate_limited"  # 429：可重试
ERROR_SERVER = "provider_api_server_error"  # 5xx：可重试
ERROR_TRANSPORT = "provider_api_transport"  # 连接/ DNS 等传输故障：可重试
ERROR_TIMEOUT = "provider_api_timeout"  # 超时：可重试
ERROR_BAD_RESPONSE = "provider_api_bad_response"  # 200 但体非预期 JSON：可重试
ERROR_INCOMPLETE_STATUS = "provider_api_incomplete_status"  # Responses API status != completed
ERROR_ANSWER_INCOMPLETE = "answer_capture_incomplete"  # 与浏览器适配器同词：空答案诚实重试


class ProviderApiNotConfiguredError(Exception):
    """env 配置缺失/畸形（Key/Model 未设）。batch 核心映射为题级 wall 占位。"""


class ProviderApiTransportError(Exception):
    """可重试传输故障（连接/超时/非 JSON 响应体）。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class ProviderHttpResponse:
    status: int
    body: dict[str, Any]


class PostJson(Protocol):
    """HTTP 注入点（单测 fake；生产=httpx）。返回 (status, JSON body)。"""

    async def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float,
    ) -> ProviderHttpResponse: ...


async def _httpx_post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> ProviderHttpResponse:
    # trust_env=False：本机 shell 常驻 mihomo 代理 env，绝不让它劫持国内官方
    # API 的直连（systemd worker 是净 env，此项是双保险）。
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ProviderApiTransportError(ERROR_TIMEOUT, f"request timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise ProviderApiTransportError(
            ERROR_TRANSPORT, f"transport failure: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderApiTransportError(
            ERROR_BAD_RESPONSE,
            f"non-JSON response body (HTTP {response.status_code})",
        ) from exc
    if not isinstance(body, dict):
        raise ProviderApiTransportError(ERROR_BAD_RESPONSE, "JSON response body is not an object")
    return ProviderHttpResponse(status=response.status_code, body=body)


@dataclass(frozen=True)
class ProviderApiProfile:
    """一个 ``*_api`` 平台 slug 的供应方接入档案。

    ``endpoint_style``：``ark_responses``=火山方舟 Responses API（doubao）；
    ``dashscope_native``=百炼原生 Generation 端点（tongyi——兼容模式不回传
    搜索来源，原生协议才回传）；``openai_chat``=OpenAI 兼容
    chat/completions（deepseek/yiyan/yuanbao）。
    ``search_style``：联网与来源回传的请求形状——``ark_web_search``
    （Responses tools）、``qianfan_web_search``（body.web_search 块）、
    ``dashscope_native``（parameters.enable_search+search_options.
    enable_source/enable_citation）、``hunyuan_enhancement``
    （enable_enhancement+search_info+citation）、``none``（deepseek 官方
    API 无联网参数，诚实不发）。
    """

    slug: str
    provider: str
    env_prefix: str
    default_base_url: str
    endpoint_style: str
    search_style: str

    @property
    def env_api_key(self) -> str:
        return f"{self.env_prefix}_API_KEY"

    @property
    def env_base_url(self) -> str:
        return f"{self.env_prefix}_BASE_URL"

    @property
    def env_model(self) -> str:
        return f"{self.env_prefix}_MODEL"

    @property
    def env_timeout_s(self) -> str:
        return f"{self.env_prefix}_TIMEOUT_S"


PROVIDER_API_PROFILES: dict[str, ProviderApiProfile] = {
    profile.slug: profile
    for profile in (
        ProviderApiProfile(
            slug="doubao_api",
            provider="ark",
            env_prefix="GEO_PROVIDER_API_ARK",
            default_base_url="https://ark.cn-beijing.volces.com/api/v3",
            endpoint_style="ark_responses",
            search_style="ark_web_search",
        ),
        ProviderApiProfile(
            slug="deepseek_api",
            provider="deepseek",
            env_prefix="GEO_PROVIDER_API_DEEPSEEK",
            default_base_url="https://api.deepseek.com/v1",
            endpoint_style="openai_chat",
            search_style="none",
        ),
        ProviderApiProfile(
            slug="yiyan_api",
            provider="qianfan",
            env_prefix="GEO_PROVIDER_API_QIANFAN",
            default_base_url="https://qianfan.baidubce.com/v2",
            endpoint_style="openai_chat",
            search_style="qianfan_web_search",
        ),
        ProviderApiProfile(
            slug="tongyi_api",
            provider="dashscope",
            env_prefix="GEO_PROVIDER_API_DASHSCOPE",
            default_base_url="https://dashscope.aliyuncs.com/api/v1",
            endpoint_style="dashscope_native",
            search_style="dashscope_native",
        ),
        ProviderApiProfile(
            slug="yuanbao_api",
            provider="hunyuan",
            env_prefix="GEO_PROVIDER_API_HUNYUAN",
            default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
            endpoint_style="openai_chat",
            search_style="hunyuan_enhancement",
        ),
    )
}


@dataclass(frozen=True)
class ProviderApiConfig:
    """env 配置。api_key 绝不落日志/payload/证据文件。"""

    api_key: str
    base_url: str
    model: str
    timeout_s: float
    evidence_dir: Path

    @classmethod
    def from_env(cls, profile: ProviderApiProfile) -> ProviderApiConfig:
        api_key = os.environ.get(profile.env_api_key, "").strip()
        if not api_key:
            raise ProviderApiNotConfiguredError(
                f"{profile.env_api_key} is not set — {profile.slug} requires an official "
                f"{profile.provider} API key",
            )
        model = os.environ.get(profile.env_model, "").strip()
        if not model:
            raise ProviderApiNotConfiguredError(
                f"{profile.env_model} is not set — {profile.slug} requires an explicit model id "
                f"(model ids drift; no silent default)",
            )
        base_url = (
            os.environ.get(profile.env_base_url, "").strip() or profile.default_base_url
        ).rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            raise ProviderApiNotConfiguredError(
                f"{profile.env_base_url} must be an http(s) URL: {base_url!r}",
            )
        raw_timeout = os.environ.get(profile.env_timeout_s, "").strip()
        timeout_s = _DEFAULT_TIMEOUT_S
        if raw_timeout:
            try:
                timeout_s = float(raw_timeout)
            except ValueError:
                raise ProviderApiNotConfiguredError(
                    f"{profile.env_timeout_s} is not a number: {raw_timeout!r}",
                ) from None
            if not 30.0 <= timeout_s <= _MAX_TIMEOUT_S:
                raise ProviderApiNotConfiguredError(
                    f"{profile.env_timeout_s} must be within [30, {_MAX_TIMEOUT_S}] seconds",
                )
        raw_evidence = (
            os.environ.get(ENV_EVIDENCE_DIR, "").strip()
            or os.environ.get(ENV_SHARED_EVIDENCE_DIR, "").strip()
        )
        evidence_dir = Path(raw_evidence) if raw_evidence else _DEFAULT_EVIDENCE_DIR
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            evidence_dir=evidence_dir,
        )


# ---------------------------------------------------------------------------
# 请求构造（只发必要参数；采样参数一律不发）
# ---------------------------------------------------------------------------


def _build_request(
    profile: ProviderApiProfile, config: ProviderApiConfig, query: str
) -> tuple[str, dict[str, Any]]:
    """返回 (url, payload)。不含 Authorization（调用方统一加头）。"""
    if profile.endpoint_style == "ark_responses":
        # 火山方舟 Responses API + 联网内容插件（web_search 工具）。max_keyword/
        # limit 是官方建议的成本上限参数（单轮关键词数/单轮返回条数）。
        return f"{config.base_url}/responses", {
            "model": config.model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": query}],
                }
            ],
            "tools": [{"type": "web_search", "max_keyword": 3, "limit": 10}],
        }
    if profile.endpoint_style == "dashscope_native":
        # 百炼原生 Generation 端点：enable_source=true 回传 search_info.
        # search_results（兼容模式不回传来源，故不用它）；enable_citation 开
        # 正文 [1] 角标。result_format=message → output.choices[0].message.content。
        return f"{config.base_url}/services/aigc/text-generation/generation", {
            "model": config.model,
            "input": {"messages": [{"role": "user", "content": query}]},
            "parameters": {
                "enable_search": True,
                "result_format": "message",
                "search_options": {"enable_source": True, "enable_citation": True},
            },
        }
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "user", "content": query}],
    }
    if profile.search_style == "qianfan_web_search":
        # 千帆 V2 联网搜索：enable_citation 让正文带引用标记、响应回传
        # search_results[{index,url,title}]（官方文档口径）。
        payload["web_search"] = {
            "enable": True,
            "enable_citation": True,
            "enable_trace": True,
            "search_mode": "auto",
        }
    elif profile.search_style == "hunyuan_enhancement":
        # 混元搜索增强：enable_enhancement 开搜索、search_info=true 回传
        # search_info 链接列表、citation 开正文角标（hunyuan-lite 无此能力）。
        payload["enable_enhancement"] = True
        payload["search_info"] = True
        payload["citation"] = True
    return f"{config.base_url}/chat/completions", payload


# ---------------------------------------------------------------------------
# 响应抽取（防御式：字段缺失即空，绝不合成）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExtractedAnswer:
    answer_text: str
    citations: list[dict[str, Any]]
    search_queries: list[dict[str, Any]]


def _citation(url: Any, title: Any, ordinal: int, cited_text: Any = None) -> dict[str, Any] | None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    return {
        "url": url,
        "title": title.strip()[:300] if isinstance(title, str) and title.strip() else None,
        "cited_text": (
            cited_text.strip()[:2000]
            if isinstance(cited_text, str) and cited_text.strip()
            else None
        ),
        "platform_ordinal": ordinal,
        "ordinal_base": 1,
    }


def _dedupe_queries(queries: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for query in queries:
        cleaned = query.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append({"query": cleaned, "ordinal": len(out) + 1})
    return out


def _extract_ark_responses(data: dict[str, Any]) -> _ExtractedAnswer:
    """火山方舟 Responses API：output 项里 web_search_call（检索词）+ message
    （正文 + annotations url_citation 引用）。"""
    status = data.get("status")
    if isinstance(status, str) and status != "completed":
        raise ProviderApiTransportError(
            ERROR_INCOMPLETE_STATUS, f"response status is {status!r}, not completed"
        )
    answer_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    queries: list[str] = []
    output = data.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "web_search_call":
            action = item.get("action")
            actions = action if isinstance(action, list) else [action]
            for act in actions:
                if not isinstance(act, dict):
                    continue
                query = act.get("query")
                if isinstance(query, str):
                    queries.append(query)
                elif isinstance(act.get("queries"), list):
                    queries.extend(q for q in act["queries"] if isinstance(q, str))
        elif item_type == "message":
            content = item.get("content")
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text"} and isinstance(
                    part.get("text"), str
                ):
                    answer_parts.append(part["text"])
                annotations = part.get("annotations")
                for ann in annotations if isinstance(annotations, list) else []:
                    if not isinstance(ann, dict):
                        continue
                    if ann.get("type") not in {"url_citation", None} and not ann.get("url"):
                        continue
                    built = _citation(
                        ann.get("url"),
                        ann.get("title"),
                        len(citations) + 1,
                        ann.get("summary") or ann.get("content") or ann.get("snippet"),
                    )
                    if built is not None:
                        citations.append(built)
    if not answer_parts and isinstance(data.get("output_text"), str):
        answer_parts.append(data["output_text"])
    return _ExtractedAnswer(
        answer_text="".join(answer_parts).strip(),
        citations=citations,
        search_queries=_dedupe_queries(queries),
    )


def _citations_from_result_list(results: Any) -> list[dict[str, Any]]:
    """各家「搜索结果列表」的公共归一（千帆 search_results / 百炼
    search_info.search_results / 混元 search_info）：条目取 index/url/title
    （+summary 类字段进 cited_text，有则收、无则 None 诚实缺省）。"""
    citations: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    for running, entry in enumerate(results if isinstance(results, list) else [], 1):
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        ordinal = (
            index
            if isinstance(index, int) and not isinstance(index, bool) and index >= 1
            else running
        )
        if ordinal in seen_ordinals:
            ordinal = running + len(results)
        seen_ordinals.add(ordinal)
        built = _citation(
            entry.get("url"),
            entry.get("title"),
            ordinal,
            entry.get("summary") or entry.get("snippet") or entry.get("content"),
        )
        if built is not None:
            citations.append(built)
    return citations


def _extract_dashscope_native(data: dict[str, Any]) -> _ExtractedAnswer:
    """百炼原生 Generation 响应：output.choices[0].message.content 正文 +
    output.search_info.search_results 搜索来源。"""
    output = data.get("output")
    output_dict = output if isinstance(output, dict) else {}
    choices = output_dict.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    answer_text = content.strip() if isinstance(content, str) else ""
    search_info = output_dict.get("search_info")
    citations = _citations_from_result_list(
        search_info.get("search_results") if isinstance(search_info, dict) else None
    )
    return _ExtractedAnswer(answer_text=answer_text, citations=citations, search_queries=[])


def _extract_chat_completion(data: dict[str, Any], profile: ProviderApiProfile) -> _ExtractedAnswer:
    """OpenAI 兼容 chat/completions：choices[0].message.content 为正文；
    千帆回传顶层 search_results[{index,url,title}]；混元命中搜索时回传
    search_info 链接列表。"""
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        # 部分兼容端点把 content 拆成 parts——只取文本片，形状外内容诚实忽略。
        answer_text = "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") in {"text", "output_text"}
            and isinstance(part.get("text"), str)
        ).strip()
    else:
        answer_text = content.strip() if isinstance(content, str) else ""
    citations: list[dict[str, Any]] = []
    if profile.search_style == "qianfan_web_search":
        citations = _citations_from_result_list(data.get("search_results"))
    elif profile.search_style == "hunyuan_enhancement":
        citations = _citations_from_result_list(data.get("search_info"))
    return _ExtractedAnswer(answer_text=answer_text, citations=citations, search_queries=[])


def _extract_answer(profile: ProviderApiProfile, data: dict[str, Any]) -> _ExtractedAnswer:
    if profile.endpoint_style == "ark_responses":
        return _extract_ark_responses(data)
    if profile.endpoint_style == "dashscope_native":
        return _extract_dashscope_native(data)
    return _extract_chat_completion(data, profile)


# ---------------------------------------------------------------------------
# 证据（原始响应 JSON 原文落盘 → CAS；sse_raw 的 API 模态对照物）
# ---------------------------------------------------------------------------


def _write_raw_evidence(
    config: ProviderApiConfig, business_key: str, body: dict[str, Any]
) -> CollectionEvidenceRef:
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = config.evidence_dir / f"{business_key}-provider-api-raw.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return CollectionEvidenceRef(
        kind="provider_api_raw",
        path=str(path),
        relation_type="answer_provider_api_raw",
        mime_type="application/json",
    )


# ---------------------------------------------------------------------------
# batch 核心与 activity 注册实现
# ---------------------------------------------------------------------------


def _failure(
    item_business_key: str,
    status: str,
    error_type: str,
    message: str,
    evidence: list[CollectionEvidenceRef] | None = None,
) -> CollectionBatchItemResult:
    return CollectionBatchItemResult(
        business_key=item_business_key,
        status=status,
        error_type=error_type,
        error_message=message[:500],
        quality_state=error_type,
        evidence=list(evidence or []),
    )


def _map_http_status(status: int) -> tuple[str, str]:
    """非 200 → (item_status, error_type)。"""
    if status in {401, 403}:
        return "wall", ERROR_AUTH_REJECTED
    if status == 429:
        return "incomplete", ERROR_RATE_LIMITED
    if 500 <= status:
        return "incomplete", ERROR_SERVER
    if 400 <= status < 500:
        return "wall", ERROR_BAD_REQUEST
    return "incomplete", ERROR_TRANSPORT


async def run_provider_api_batch(
    batch: CollectionBatchInput,
    *,
    profile: ProviderApiProfile,
    post_json: PostJson = _httpx_post_json,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> CollectionBatchResult:
    """provider_api batch 核心：配置门（题级占位，不 raise）→ 逐题一次 HTTP →
    per-item 结果（等长同序）。与 activity 上下文解耦（post_json/heartbeat 注入）。

    本函数不 raise（配置缺失/传输失败/平台错误全部题级诚实落库）；唯一例外是
    结果契约被破坏前的意外——那属于 bug，交给 Temporal 重试暴露。
    """

    def _heartbeat(payload: dict[str, Any]) -> None:
        if heartbeat is not None:
            heartbeat(payload)

    _heartbeat(
        {"run_pub_id": batch.run_pub_id, "stage": "adapter_started", "adapter": profile.slug}
    )
    try:
        config = ProviderApiConfig.from_env(profile)
    except ProviderApiNotConfiguredError as exc:
        log.warning("provider_api_not_configured", adapter=profile.slug, reason=str(exc))
        return CollectionBatchResult(
            results=[
                _failure(item.business_key, "wall", ERROR_NOT_CONFIGURED, str(exc))
                for item in batch.items
            ]
        )
    results: list[CollectionBatchItemResult] = []
    for item in batch.items:
        _heartbeat(
            {
                "run_pub_id": batch.run_pub_id,
                "stage": "provider_api_request",
                "adapter": profile.slug,
                "business_key": item.business_key,
            }
        )
        if (item.mode or "normal") != "normal":
            # 能力表（PLATFORM_MODE_CAPABILITIES）只给 *_api 开 normal；matrix 之外
            # 的 mode 漏进来一律题级诚实失败，绝不按错误口径采。
            results.append(
                _failure(
                    item.business_key,
                    "wall",
                    ERROR_UNSUPPORTED_MODE,
                    f"{profile.slug} supports mode 'normal' only, got {item.mode!r}",
                )
            )
            continue
        url, payload = _build_request(profile, config, item.query)
        try:
            response = await post_json(
                url,
                headers={"Authorization": f"Bearer {config.api_key}"},
                payload=payload,
                timeout_s=config.timeout_s,
            )
        except ProviderApiTransportError as exc:
            results.append(_failure(item.business_key, "incomplete", exc.error_type, str(exc)))
            continue
        raw_evidence = _write_raw_evidence(config, item.business_key, response.body)
        if response.status != 200:
            status, error_type = _map_http_status(response.status)
            error_detail = json.dumps(
                response.body.get("error", response.body), ensure_ascii=False
            )[:300]
            results.append(
                _failure(
                    item.business_key,
                    status,
                    error_type,
                    f"HTTP {response.status} from {profile.provider}: {error_detail}",
                    evidence=[raw_evidence],
                )
            )
            continue
        try:
            extracted = _extract_answer(profile, response.body)
        except ProviderApiTransportError as exc:
            results.append(
                _failure(
                    item.business_key,
                    "incomplete",
                    exc.error_type,
                    str(exc),
                    evidence=[raw_evidence],
                )
            )
            continue
        if not extracted.answer_text:
            results.append(
                _failure(
                    item.business_key,
                    "incomplete",
                    ERROR_ANSWER_INCOMPLETE,
                    f"{profile.slug} returned an empty answer",
                    evidence=[raw_evidence],
                )
            )
            continue
        results.append(
            CollectionBatchItemResult(
                business_key=item.business_key,
                status="ok",
                answer_text=extracted.answer_text,
                screenshot_ref="",
                quality_state="live_valid",
                citations=extracted.citations,
                evidence=[raw_evidence],
                search_queries=extracted.search_queries,
            )
        )
    return CollectionBatchResult(results=results)


def _make_live_provider_api_batch(slug: str) -> Callable[..., Any]:
    """生成 ``collect_<slug>_batch`` 的 live 注册实现（workers/main.py 在
    GEO_COLLECTION_ADAPTER=multi 下替换 collection.py 的 fail-closed 默认）。"""

    profile = PROVIDER_API_PROFILES[slug]

    @activity.defn(name=f"collect_{slug}_batch")
    async def _impl(batch: CollectionBatchInput) -> CollectionBatchResult:
        return await run_provider_api_batch(
            batch,
            profile=profile,
            heartbeat=activity.heartbeat,
        )

    return _impl


collect_doubao_api_batch = _make_live_provider_api_batch("doubao_api")
collect_deepseek_api_batch = _make_live_provider_api_batch("deepseek_api")
collect_yiyan_api_batch = _make_live_provider_api_batch("yiyan_api")
collect_tongyi_api_batch = _make_live_provider_api_batch("tongyi_api")
collect_yuanbao_api_batch = _make_live_provider_api_batch("yuanbao_api")

# slug 词表双写防漂移：collection.py 的 PROVIDER_API_ADAPTER_SLUGS（matrix
# 折叠/能力表消费）与本模块 profile 注册表必须同集——新增平台两处一起改。
assert set(PROVIDER_API_PROFILES) == set(PROVIDER_API_ADAPTER_SLUGS), (
    "provider_api profile slugs drifted from collection.PROVIDER_API_ADAPTER_SLUGS"
)
