"""AI 联网调研（旧 server/geosys/intake/ai_research.py 的 httpx 移植版）。

口径：多传输按模型路由（``_transport_for_model``，经 inferera 逐台实证）——
gpt 系/gemini-3.6-flash 走 OpenAI Responses API（POST {base}/responses，**非流式**）+
宿主 ``web_search`` 工具（搜索+开网页一体，信源走 url_citation 标注）；claude 系走
Anthropic 原生 ``/v1/messages`` + server 工具 ``web_search_20250305``；qwen 系走
``/chat/completions`` + ``enable_search:true``；gemini *-search 后缀系走
``/chat/completions``（grounding 内建）。模型把品牌公开信息结构化为严格 JSON，
供客户信息表（profile/promo/trigger）草稿预填。多轮补缺 loop：每轮后盘点仍空字段定向追问，
直到填满、模型确认公开渠道查不到（unavailable）、或达 max_rounds（缺省 3，硬上限 5）。

纪律：
  * key 只走 settings（GEO_RESEARCH_LLM_API_KEY，**严禁**入库/代码/日志）；未配 →
    ResearchDisabled，由 API 层映射为 503 llm_disabled（诚实降级）；
  * 网络/5xx 失败自动换 base_url_fallback **重试一次**（每轮独立，4xx 不重试）；
  * 词表 fail-closed：industry/goals/audience_type/platforms/features/strength/
    review_category/ad_review_doc_types/business_license_code 以 models 词表为唯一真源，
    不合法值**丢弃并计数披露**（返回 dropped），绝不塞进表单；
  * 补缺合并只填仍空字段，**绝不覆盖**已确认值；
  * 零合成：SYSTEM_PROMPT 明示「不要编造」，模型无法确认的字段保持 null/[] 原样返回。

实现：httpx 直调（不引 openai SDK）；``_build_client`` 是测试 mock 接缝（MockTransport）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from ..config import Settings
from . import models

log = structlog.get_logger()

_DEFAULT_MODEL = "gpt-5.6-luna"
_DEFAULT_BASE_URL = "https://api.inferera.com"
_DEFAULT_FALLBACK_BASE_URL = ""
_DEFAULT_MAX_ROUNDS = 3
_HARD_MAX_ROUNDS = 5
_TIMEOUT_SECONDS = 300.0

# Responses API 宿主 web_search 工具：high=调研级搜索深度；CN 定位让结果偏中文源。
_WEB_SEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "web_search",
        "search_context_size": "high",
        "user_location": {"type": "approximate", "country": "CN", "timezone": "Asia/Shanghai"},
    }
]


class ResearchDisabled(RuntimeError):
    """GEO_RESEARCH_LLM_API_KEY 未配置 → API 503 llm_disabled。"""


class ResearchModelNotAllowed(RuntimeError):
    """请求模型不在 GEO_RESEARCH_LLM_MODELS 允许清单内 → API 400 model_not_allowed。"""


def available_models(settings: Settings) -> list[str]:
    """前端可选调研模型清单：GEO_RESEARCH_LLM_MODELS 逗号分隔，缺省模型恒在首位。

    入清单前提=实测真联网（传输路由见 _transport_for_model 的实证注释）。
    """
    configured = settings.research_llm_model.strip() or _DEFAULT_MODEL
    models = [m.strip() for m in settings.research_llm_models.split(",") if m.strip()]
    if configured not in models:
        models.insert(0, configured)
    return models[:16]


def grouped_models(settings: Settings) -> list[dict[str, Any]]:
    """按 provider 分组的级联选项（同 provider 的模型归一组，保持清单顺序）。
    provider = 型号 ID 的字母前缀（gpt-5.6-luna→gpt、qwen3.7-max→qwen）。"""
    groups: dict[str, list[str]] = {}
    for model in available_models(settings):
        match = re.match(r"[a-z]+", model.strip().lower())
        provider = match.group() if match else model.split("-", 1)[0]
        groups.setdefault(provider, []).append(model)
    return [{"provider": provider, "models": models} for provider, models in groups.items()]


def resolve_research_model(settings: Settings, requested: str | None) -> str:
    """校验并解析本次调研用模型：空 = 缺省；不在清单 → ResearchModelNotAllowed。"""
    allowed = available_models(settings)
    candidate = (requested or "").strip()
    if not candidate:
        return allowed[0]
    if candidate not in allowed:
        raise ResearchModelNotAllowed(candidate)
    return candidate


class ResearchFailed(RuntimeError):
    """上游调用失败（主/备 base_url 均败、4xx、无文本、JSON 抽取失败）→ API 502 research_failed。"""


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    model: str
    base_url: str
    base_url_fallback: str
    max_rounds: int


def config_from_settings(settings: Settings) -> LlmConfig:
    """从 Settings 组装调研配置；max_rounds 钳制 [1, 5]（每轮都是完整联网调研，成本须可封顶）。"""
    return LlmConfig(
        api_key=settings.research_llm_api_key.strip(),
        model=settings.research_llm_model.strip() or _DEFAULT_MODEL,
        base_url=settings.research_llm_base_url.strip() or _DEFAULT_BASE_URL,
        base_url_fallback=(
            settings.research_llm_base_url_fallback.strip() or _DEFAULT_FALLBACK_BASE_URL
        ),
        max_rounds=max(1, min(settings.research_llm_max_rounds, _HARD_MAX_ROUNDS)),
    )


def _normalize_base_url(base_url: str) -> str:
    """历史 env 值无 /v1（OpenAI SDK 不自动补）——归一补齐。"""
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _build_client(config: LlmConfig, base_url: str) -> httpx.Client:
    """按 base_url 建 httpx 客户端（测试 mock 接缝：monkeypatch 此函数注入 MockTransport）。"""
    return httpx.Client(
        base_url=_normalize_base_url(base_url),
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=_TIMEOUT_SECONDS,
        trust_env=False,
    )


def _q(xs: tuple[str, ...]) -> str:
    return ",".join(f'"{x}"' for x in xs)


# SYSTEM_PROMPT 字段映射契约照旧版原样保留；词表从 models 单一真源注入（防两侧漂移）。
SYSTEM_PROMPT = (
    "你是专业的品牌调研分析师，服务于 GEO（生成式引擎优化）客户信息收集流程。\n"
    "你的任务：根据用户提供的品牌/公司名称，使用联网搜索工具 (web_search，"
    "可搜索关键词并打开网页)，\n"
    "尽可能完整地调研该品牌的公开信息，并以**严格 JSON** 输出，便于前端自动填充表单。\n"
    "\n"
    "调研重点（依次执行搜索）：\n"
    "1. 官方网站：抓取首页、关于我们、产品/服务页 -> 提取公司介绍、主营产品/服务、联系方式\n"
    "2. 社交媒体：微信公众号、抖音/视频号、小红书、B站、微博 -> 找到官方账号名\n"
    "3. 行业与定位：所属行业、目标客户、核心卖点、差异化优势\n"
    "4. 代表案例与实力：服务过的知名客户、团队规模、融资/上市情况、资质专利\n"
    "5. 资质公示信息：统一社会信用代码（18位，如国家企业信用信息公示系统等公开渠道）、\n"
    "   行业许可证/广告审查批准文号（如药监局、市场监管部门公示）\n"
    "6. GEO 优化建议：根据品牌特征，推荐合理的推广目标、目标客户类型、用户问法、目标地域、AI 平台\n"
    "\n"
    "字段映射规则（严格遵守）：\n"
    "- 只填写你能从公开渠道确认的信息，无法确认的字段填 null 或空数组 []，**不要编造**\n"
    "- 确认公开渠道查不到的字段，把字段名列入 unavailable 数组（多轮调研将不再追问这些字段）\n"
    "- industry 必须从这些选项中选择：" + _q(models.INDUSTRIES) + "\n"
    "- goals（核心推广目的）从下列候选中挑选 1~3 个：" + _q(models.GOALS) + "\n"
    "- audience_type 从候选中挑选 1~2 个：" + _q(models.AUDIENCE_TYPES) + "\n"
    "- platforms 从候选中挑选：" + _q(models.PLATFORMS) + "\n"
    "- features 选项限定：" + _q(models.PRODUCT_FEATURES) + "\n"
    "- strength 选项限定：" + _q(models.COMPANY_STRENGTHS) + "\n"
    "- review_category（行业广告审查分类，建议值，最终由客户确认）只能选：\n"
    "  "
    + _q(models.REVIEW_CATEGORIES)
    + "，含义："
    + "; ".join(f'"{k}"={v}' for k, v in models.REVIEW_CATEGORY_LABELS.items())
    + "\n"
    "- ad_review_doc_types 选项限定：" + _q(models.AD_REVIEW_DOC_TYPES) + "\n"
    "- business_license_code：18 位统一社会信用代码（0-9/A-Z），查不到或格式不确定填 null\n"
    '- products 数组：1~3 个主营产品/服务（type 固定为 "product"）\n'
    '- company_brief 对象：公司主体的整体实力描述（type 固定为 "company"）\n'
    "- selling_points：核心卖点（200 字以内，每条卖点需有公开出处，无出处的表述不要写）\n"
    "- evidence_links：可公开引用的佐证材料数组（官网、检测报告、权威媒体报道、行业奖项等链接）\n"
    "- trigger_questions：给出 3~6 条用户向 AI 提问时希望被推荐到的问法，每行一条\n"
    '- regions：目标推广地域数组，如 ["全国"] 或 ["华东","上海"]\n'
    "\n"
    "输出格式（只能输出 JSON，不要任何前后缀文字、不要 markdown 代码块）：\n"
    "{\n"
    '  "company_name": "string",\n'
    '  "industry": "string 或 null",\n'
    '  "description": "string，1-2 句品牌简介",\n'
    '  "website": "string 或 null",\n'
    '  "wechat": "string 或 null",\n'
    '  "douyin": "string 或 null",\n'
    '  "social_media": "string 或 null，含小红书/B站/微博等",\n'
    '  "business_license_code": "string 或 null",\n'
    '  "review_category": "string 或 null",\n'
    '  "ad_review_no": "string 或 null",\n'
    '  "ad_review_authority": "string 或 null",\n'
    '  "ad_review_expiry": "string 或 null",\n'
    '  "ad_review_doc_types": [],\n'
    '  "selling_points": "string 或 null",\n'
    '  "evidence_links": [],\n'
    '  "products": [\n'
    '    {"name":"...","category":"...","features":[],'
    '"desc":"...","price":"..."}\n'
    "  ],\n"
    '  "company_brief": {\n'
    '    "name":"...","strength":[],"advantage":"...",'
    '"cases":"...","data":"..."\n'
    "  },\n"
    '  "goals": [],\n'
    '  "audience_type": [],\n'
    '  "audience_desc": "string 或 null，决策人画像补充",\n'
    '  "trigger_questions": "多行字符串",\n'
    '  "regions": [],\n'
    '  "platforms": [],\n'
    '  "unavailable": ["确认公开渠道查不到的字段名"],\n'
    '  "confidence": {"high":[],"medium":[],"low":[]},\n'
    '  "sources": [{"title":"","url":""}],\n'
    '  "summary": "string，给用户看的调研小结，说明本次联网调研到了哪些信息、'
    '哪些字段建议人工核对"\n'
    "}\n"
    "\n"
    "confidence 字段用于标注每个填充字段的可信度：\n"
    "- high：来自官方网站或权威渠道\n"
    "- medium：来自新闻报道、行业报告\n"
    "- low：基于推测，建议用户核对\n"
    '字段名使用上述 JSON 的 key（如 ["company_name","industry"]）。'
)


def _build_user_prompt(brand: str, hints: dict[str, Any] | None = None) -> str:
    parts = [f"请联网调研品牌：【{brand}】"]
    hints = hints or {}
    if hints.get("website"):
        parts.append(f"已知官网：{hints['website']}（可作为搜索/打开网页的起点）")
    if hints.get("industry"):
        parts.append(f"用户自述所属行业：{hints['industry']}")
    if hints.get("region"):
        parts.append(f"主要经营地区：{hints['region']}")
    parts.append(
        "请按系统提示中的 JSON schema 输出。务必先用 web_search "
        "确认信息，再填写字段；找不到的字段填 null 或空数组。"
    )
    return "\n".join(parts)


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出中抽取 JSON 对象（兼容裸 JSON / ```json 代码块）。

    gemini 系经 /chat/completions 的输出偶发尾逗号等瑕疵（20260808 生产实测），
    首试严格解析，失败再做一次尾逗号清理重试，仍败才判 ResearchFailed。
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ResearchFailed("AI 输出中未找到合法 JSON")
    candidate = s[start : end + 1]
    try:
        strict_parsed: dict[str, Any] = json.loads(candidate)
        return strict_parsed
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        parsed: dict[str, Any] = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError as e:
        raise ResearchFailed("AI 输出 JSON 解析失败") from e


def _transport_for_model(model: str) -> str:
    """按模型选传输与联网激活方式（逐台实证，当前统一经 inferera）：

    - ``claude-*`` → Anthropic 原生 ``/v1/messages`` + server 工具
      ``web_search_20250305``（OpenAI 兼容端点不透传搜索执行；/responses 会忽略 tools）；
    - ``qwen*`` → ``/chat/completions`` + ``enable_search:true``（服务端检索）；
    - ``gemini-*-search`` 后缀系 → ``/chat/completions``（grounding 内建，不带 tools）；
    - 其余（gpt-*、gemini-3.6-flash 等）→ Responses API + 宿主 ``web_search`` 工具。
    """
    name = model.strip().lower()
    if name.startswith("claude-"):
        return "anthropic"
    if name.startswith("qwen"):
        return "chat_enable_search"
    if name.startswith("gemini-") and name.endswith("-search"):
        return "chat_builtin"
    return "responses"


_ANTHROPIC_MAX_TOKENS = 32768  # /v1/messages 强制字段（API 要求），取模型输出上限级，非人为截断


def _run_once(
    client: httpx.Client,
    model: str,
    user_msg: str,
    *,
    instructions: str = SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, int]]:
    """单轮非流式调用 → (data, sources, usage)；HTTP 错误原样上抛由调用方定重试。
    传输按 _transport_for_model 路由；sources 在非 Responses 路径取模型按提示词
    回填的 JSON sources 字段。各路径均不发 temperature/人为 max token（/v1/messages
    的 max_tokens 为 API 强制字段，取模型输出上限级）。"""
    transport = _transport_for_model(model)

    if transport == "anthropic":
        ant_body: dict[str, Any] = {
            "model": model,
            "max_tokens": _ANTHROPIC_MAX_TOKENS,
            "system": instructions,
            "messages": [{"role": "user", "content": user_msg}],
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        }
        resp = client.post("/messages", json=ant_body)
        resp.raise_for_status()
        ant_payload: dict[str, Any] = resp.json()
        ant_parts = [
            str(block.get("text") or "")
            for block in ant_payload.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        ant_text = "\n".join(p for p in ant_parts if p).strip()
        if not ant_text:
            raise ResearchFailed("AI 未返回任何文本内容")
        ant_data = _extract_json(ant_text)
        ant_sources: list[dict[str, str]] = []
        if isinstance(ant_data.get("sources"), list):
            ant_sources = [s for s in ant_data["sources"] if isinstance(s, dict)]
        ant_usage_raw = ant_payload.get("usage") or {}
        ant_usage = {
            "input_tokens": int(ant_usage_raw.get("input_tokens") or 0),
            "output_tokens": int(ant_usage_raw.get("output_tokens") or 0),
        }
        return ant_data, ant_sources, ant_usage

    if transport in ("chat_enable_search", "chat_builtin"):
        chat_body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_msg},
            ],
        }
        if transport == "chat_enable_search":
            chat_body["enable_search"] = True
        # 参数最小化：不发 temperature/max_tokens
        resp = client.post("/chat/completions", json=chat_body)
        resp.raise_for_status()
        chat_payload: dict[str, Any] = resp.json()
        choices = chat_payload.get("choices") or []
        chat_text = ""
        if choices and isinstance(choices[0], dict):
            chat_text = str((choices[0].get("message") or {}).get("content") or "").strip()
        if not chat_text:
            raise ResearchFailed("AI 未返回任何文本内容")
        data = _extract_json(chat_text)
        chat_sources: list[dict[str, str]] = []
        if isinstance(data.get("sources"), list):
            chat_sources = [s for s in data["sources"] if isinstance(s, dict)]
        chat_usage_raw = chat_payload.get("usage") or {}
        chat_usage = {
            "input_tokens": int(
                chat_usage_raw.get("prompt_tokens") or chat_usage_raw.get("input_tokens") or 0
            ),
            "output_tokens": int(
                chat_usage_raw.get("completion_tokens") or chat_usage_raw.get("output_tokens") or 0
            ),
        }
        return data, chat_sources, chat_usage

    body: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_msg,
    }
    if tools is None:
        body["tools"] = _WEB_SEARCH_TOOLS
    elif tools:
        body["tools"] = tools
    resp = client.post("/responses", json=body)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()

    text_parts: list[str] = []
    sources: list[dict[str, str]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text_parts.append(str(content.get("text") or ""))
            for annotation in content.get("annotations") or []:
                if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                    sources.append(
                        {
                            "title": str(annotation.get("title") or ""),
                            "url": str(annotation.get("url") or ""),
                        }
                    )
    raw_text = "\n".join(text_parts).strip()
    if not raw_text:
        raise ResearchFailed("AI 未返回任何文本内容")
    data = _extract_json(raw_text)

    if not sources and isinstance(data.get("sources"), list):
        sources = [s for s in data["sources"] if isinstance(s, dict)]

    usage_raw = payload.get("usage") or {}
    usage = {
        "input_tokens": int(usage_raw.get("input_tokens") or 0),
        "output_tokens": int(usage_raw.get("output_tokens") or 0),
    }
    return data, sources, usage


_LICENSE_CODE_RE = re.compile(r"[0-9A-Z]{18}")


def _filter_vocab(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """词表 fail-closed 过滤：不合法值丢弃并计数披露（绝不把词表外值塞进表单）。

    返回 (过滤后 data 副本, dropped 计数 dict)。null/[] 语义保留：无法确认的字段不补、不猜。"""
    dropped = {
        "industry": 0,
        "goals": 0,
        "audience_type": 0,
        "platforms": 0,
        "features": 0,
        "strength": 0,
        "review_category": 0,
        "ad_review_doc_types": 0,
        "business_license_code": 0,
    }
    out = dict(data)
    ind = out.get("industry")
    if ind is not None and ind not in models.INDUSTRIES:
        out["industry"] = None
        dropped["industry"] += 1
    rc = out.get("review_category")
    if rc is not None and rc not in models.REVIEW_CATEGORIES:
        out["review_category"] = None
        dropped["review_category"] += 1
    blc = out.get("business_license_code")
    if blc is not None:
        blc = str(blc).strip()
        if not _LICENSE_CODE_RE.fullmatch(blc):
            blc = None
            dropped["business_license_code"] += 1
        out["business_license_code"] = blc
    for key, vocab in (
        ("goals", models.GOALS),
        ("audience_type", models.AUDIENCE_TYPES),
        ("platforms", models.PLATFORMS),
        ("ad_review_doc_types", models.AD_REVIEW_DOC_TYPES),
    ):
        vals = out.get(key)
        if isinstance(vals, list):
            kept = [v for v in vals if v in vocab]
            dropped[key] += len(vals) - len(kept)
            out[key] = kept
        else:
            out[key] = []  # 非数组（含 null）→ 空数组，绝不造值
    ev = out.get("evidence_links")  # 自由文本数组：只规整，无词表
    out["evidence_links"] = (
        [str(x).strip() for x in ev if str(x).strip()] if isinstance(ev, list) else []
    )
    products = []
    for p in out.get("products") or []:
        if not isinstance(p, dict):
            continue
        row = {
            k: (p.get(k) or "").strip() if isinstance(p.get(k), str) else ""
            for k in ("name", "category", "desc", "price")
        }
        feats = [f for f in (p.get("features") or []) if f in models.PRODUCT_FEATURES]
        dropped["features"] += len(p.get("features") or []) - len(feats)
        row["features"] = feats
        products.append(row)
    out["products"] = products
    cb = out.get("company_brief")
    if isinstance(cb, dict):
        brief = {
            k: (cb.get(k) or "").strip() if isinstance(cb.get(k), str) else ""
            for k in ("name", "advantage", "cases", "data")
        }
        strs = [s for s in (cb.get("strength") or []) if s in models.COMPANY_STRENGTHS]
        dropped["strength"] += len(cb.get("strength") or []) - len(strs)
        brief["strength"] = strs
        out["company_brief"] = brief
    else:
        out["company_brief"] = None
    return out, dropped


# ── 多轮补缺 loop（loop 直到填满 / 模型确认公开查不到 / 达轮次上限）────────────────
# AI 可填字段全集（联系人/填表人/真实性确认等合规亲笔项**永不**在内）。
FILLABLE_FIELDS = (
    "industry",
    "description",
    "website",
    "wechat",
    "douyin",
    "social_media",
    "business_license_code",
    "review_category",
    "ad_review_no",
    "ad_review_authority",
    "ad_review_expiry",
    "ad_review_doc_types",
    "selling_points",
    "evidence_links",
    "products",
    "company_brief",
    "goals",
    "audience_type",
    "audience_desc",
    "trigger_questions",
    "regions",
    "platforms",
)
_LIST_FILLABLE = {
    "goals",
    "audience_type",
    "platforms",
    "regions",
    "ad_review_doc_types",
    "evidence_links",
    "products",
}


def _is_empty(v: Any) -> bool:
    """字段空值判定：None / 空白字符串 / 空数组 / 全空值对象（company_brief）均视为空。"""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, dict):
        return all(_is_empty(x) for x in v.values())
    return False


def _missing_fields(acc: dict[str, Any], unavailable: set[str]) -> list[str]:
    return [f for f in FILLABLE_FIELDS if _is_empty(acc.get(f)) and f not in unavailable]


def _merge_missing(acc: dict[str, Any], new: dict[str, Any]) -> None:
    """补缺合并：只把 new 里非空的值填进 acc 仍空的字段——绝不覆盖已确认值。"""
    for f in FILLABLE_FIELDS + ("company_name",):
        if not _is_empty(acc.get(f)):
            continue
        v = new.get(f)
        if not _is_empty(v):
            acc[f] = v


def _build_followup_prompt(
    brand: str,
    hints: dict[str, Any] | None,
    round_no: int,
    acc: dict[str, Any],
    missing: list[str],
    unavailable: set[str],
) -> str:
    """第 N 轮补缺 prompt：已确认值仅作定位参考（防重复调研防覆盖），针对空缺定向搜索。"""
    filled = {
        f: acc.get(f) for f in FILLABLE_FIELDS + ("company_name",) if not _is_empty(acc.get(f))
    }
    parts = [f"请继续联网调研品牌：【{brand}】（第 {round_no} 轮，补缺调研）"]
    hints = hints or {}
    if hints.get("website"):
        parts.append(f"已知官网：{hints['website']}")
    parts.append(
        "此前轮次已确认以下字段（仅供你定位品牌，**不要重复调研；输出时这些字段填 null 即可**）：\n"
        + json.dumps(filled, ensure_ascii=False)
    )
    parts.append("以下字段仍为空，请**针对性地**继续用 web_search 查找：" + "、".join(missing))
    if unavailable:
        parts.append("以下字段已确认公开渠道查不到，不要再查：" + "、".join(sorted(unavailable)))
    parts.append(
        "输出 JSON schema 与系统提示一致，但**只需输出待补字段**（外加 "
        "confidence/sources/summary/unavailable）；本轮确认查不到的字段列入 unavailable。"
    )
    return "\n".join(parts)


def _retry_fallback(
    config: LlmConfig,
    model: str,
    user_msg: str,
    first_err: Exception,
    *,
    instructions: str = SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, int]]:
    """主 base_url 网络/5xx 失败 → base_url_fallback 重试一次（仅一次）。"""
    log.warning("research_primary_failed", error_type=type(first_err).__name__)
    fallback = config.base_url_fallback.strip()
    if not fallback or _normalize_base_url(fallback) == _normalize_base_url(config.base_url):
        raise ResearchFailed("LLM 上游不可用") from first_err
    try:
        with _build_client(config, fallback) as client:
            return _run_once(client, model, user_msg, instructions=instructions, tools=tools)
    except (httpx.HTTPError, ResearchFailed) as e2:
        raise ResearchFailed("LLM 上游不可用（主/备 base_url 均失败）") from e2


def _run_with_fallback(
    client: httpx.Client | None,
    config: LlmConfig,
    model: str,
    user_msg: str,
    *,
    instructions: str = SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, int]]:
    """单轮调用：client 注入（测试 mock）时直接用；否则按 config 建客户端，
    网络/5xx 失败换 fallback base_url 重试一次（4xx 不重试）。"""
    if client is not None:
        try:
            return _run_once(client, model, user_msg, instructions=instructions, tools=tools)
        except httpx.HTTPError as e:
            raise ResearchFailed("LLM 上游调用失败") from e
    try:
        with _build_client(config, config.base_url) as primary:
            return _run_once(primary, model, user_msg, instructions=instructions, tools=tools)
    except httpx.HTTPStatusError as e:
        if e.response.status_code < 500:  # 4xx：请求侧问题，重试无义
            raise ResearchFailed(f"LLM 上游拒绝请求（HTTP {e.response.status_code}）") from e
        return _retry_fallback(config, model, user_msg, e, instructions=instructions, tools=tools)
    except httpx.TransportError as e:
        # 网络失败（建连/读取中断）：换 fallback 重试一次
        return _retry_fallback(config, model, user_msg, e, instructions=instructions, tools=tools)


def research_brand_fields(
    brand: str,
    hints: dict[str, Any] | None = None,
    *,
    config: LlmConfig,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """联网调研品牌，**多轮补缺 loop**：每轮后盘点仍空字段，针对空缺继续调研，
    直到填满、模型确认公开渠道查不到（unavailable）、或达 max_rounds（缺省 3，硬上限 5）。

    返回结构：
      {"data": {...表单字段（已过词表过滤，null/[] 保留）...},
       "confidence": {"high":[],"medium":[],"low":[]},
       "sources": [{"title","url"}], "summary": str,
       "model": str, "usage": {"input_tokens","output_tokens"},   # 各轮累加
       "dropped": {字段: 被丢弃的词表外值个数},                     # fail-closed 披露（各轮累加）
       "rounds": int,                                             # 实际调研轮数
       "unavailable": [字段名],                                    # 模型确认公开渠道查不到
       "unfilled": [字段名]}                                       # 最终仍空（查不到 ∪ 轮次用尽）

    client 可注入（测试 mock）；为 None 时按 config 建客户端，每轮独立走主/备 base_url fallback。
    失败抛 ValueError（品牌名空）/ ResearchDisabled（无 key）/ ResearchFailed（上游/解析失败）。
    """
    if not brand or not brand.strip():
        raise ValueError("品牌名称不能为空")
    brand = brand.strip()
    if client is None and not config.api_key:
        raise ResearchDisabled("未配置 GEO_RESEARCH_LLM_API_KEY")
    model = config.model
    max_rounds = config.max_rounds

    log.info("research_started", brand=brand, model=model, max_rounds=max_rounds)
    acc: dict[str, Any] = {}
    unavailable: set[str] = set()
    confidence: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    dropped: dict[str, int] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    summary = ""
    rounds = 0
    user_msg = _build_user_prompt(brand, hints)

    while True:
        rounds += 1
        data, srcs, use = _run_with_fallback(client, config, model, user_msg)
        usage["input_tokens"] += use.get("input_tokens", 0)
        usage["output_tokens"] += use.get("output_tokens", 0)
        for s in srcs:
            url = s.get("url") if isinstance(s, dict) else None
            if url:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            sources.append(s)
        filtered, d = _filter_vocab(data)
        for k, v in d.items():
            dropped[k] = dropped.get(k, 0) + v
        s2 = filtered.pop("summary", None)
        if s2:
            summary = str(s2)
        conf = filtered.pop("confidence", None)
        if isinstance(conf, dict):
            for ck in ("high", "medium", "low"):
                for f in conf.get(ck) or []:
                    if f not in confidence[ck]:
                        confidence[ck].append(str(f))
        filtered.pop("sources", None)  # sources 以工具结果/顶层为准
        unav = filtered.pop("unavailable", None)
        if isinstance(unav, list):
            unavailable |= {f for f in unav if f in FILLABLE_FIELDS}
        _merge_missing(acc, filtered)
        missing = _missing_fields(acc, unavailable)
        log.info(
            "research_round_done",
            round=rounds,
            brand=brand,
            missing=missing,
            unavailable=sorted(unavailable),
        )
        if not missing or rounds >= max_rounds:
            break
        user_msg = _build_followup_prompt(brand, hints, rounds + 1, acc, missing, unavailable)

    # 输出契约归一：未命中的字段显式给 null/[]（null/[] 语义保留——无法确认绝不补、不猜）
    for f in FILLABLE_FIELDS:
        if f not in acc:
            acc[f] = [] if f in _LIST_FILLABLE else None
    unfilled = [f for f in FILLABLE_FIELDS if _is_empty(acc.get(f))]
    log.info(
        "research_done",
        brand=brand,
        rounds=rounds,
        sources=len(sources),
        unfilled=unfilled,
        dropped=dropped,
    )
    return {
        "data": acc,
        "confidence": confidence,
        "sources": sources,
        "summary": summary,
        "model": model,
        "usage": usage,
        "dropped": dropped,
        "rounds": rounds,
        "unavailable": sorted(unavailable),
        "unfilled": unfilled,
    }


# ══ AI 扩写问法（旧 server/geosys/intake/ai_research.py suggest_monitor_questions 移植）════
# 与联网调研不同：**不带 web_search**（纯生成，快且便宜）；candidate_only——只回候选，
# 落库与否由调用方决定（客户填表通道里由客户勾选后走 trigger-questions 收录）。
_SUGGEST_SYSTEM_PROMPT = (
    "你是GEO监测策略师。根据品牌和核心词生成真实用户会向AI助手提出的中文问题。"
    "问题要自然、口语化，覆盖推荐、对比、选购、场景、口碑和地域意图；避免绝对化承诺，"
    "不要机械重复品牌名，不得与已有问法重复。严格输出JSON对象："
    '{"questions":[{"question":"...","core_word":"...","heat":80}]}。'
)


def suggest_monitor_questions(
    brand: str,
    core_words: list[str],
    existing: list[str] | None = None,
    n: int = 12,
    *,
    config: LlmConfig,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """根据核心词生成自然、去重的 GEO 监测问法候选；不落库。

    输入钳制：core_words ≤20、existing ≤200、n ∈ [1, 50]。
    失败抛 ValueError（brand/核心词空）/ ResearchDisabled（无 key）/ ResearchFailed（上游失败）。
    """
    if client is None and not config.api_key:
        raise ResearchDisabled("未配置 GEO_RESEARCH_LLM_API_KEY")
    brand = (brand or "").strip()
    words = [str(x).strip() for x in core_words if str(x).strip()][:20]
    if not brand or not words:
        raise ValueError("brand_and_core_words_required")
    n = max(1, min(int(n or 12), 50))
    avoid = [str(x).strip() for x in (existing or []) if str(x).strip()][:200]
    payload = json.dumps(
        {"brand": brand, "core_words": words, "count": n, "existing_questions": avoid},
        ensure_ascii=False,
    )
    data, _, _ = _run_with_fallback(
        client,
        config,
        config.model,
        payload,
        instructions=_SUGGEST_SYSTEM_PROMPT,
        tools=[],  # 纯生成：不带 web_search
    )
    rows = data.get("questions") or []
    seen = set(avoid)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        q = str(row.get("question") or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        try:
            heat = max(0, min(int(row.get("heat", 50)), 100))
        except (TypeError, ValueError):
            heat = 50
        cw = str(row.get("core_word") or "").strip()
        out.append({"question": q, "core_word": cw if cw in words else words[0], "heat": heat})
        if len(out) >= n:
            break
    return out
