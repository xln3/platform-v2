"""LLM 品牌抽取：逐条从答案全文抽品牌列表（旧库 geosys/brandrank/extract.py 的 V2 移植）。

口径与旧库逐行一致（旧库本身对齐同事版 analyze_brand.py L875-929）：
- prompt/system message 全部来自 rules.DomainRules（她的 L877-890 f-string 模板，category 参数化）；
- temperature=0（规则包 llm.temperature）、response_format=json_object（她的 L899-900）；
- ThreadPoolExecutor 批处理（L909-929，max_workers 可配，默认取规则包 llm.max_workers_default=10）。

诚实化修正（照旧库，INV-32 零合成）：她的 extract_brands_with_llm 把**一切异常**吞成 ``[]``——
失败与"确实没提到品牌"不可区分。本模块失败抛 ExtractError，由调用方把该条标 failed 记 error，
**绝不编造品牌、绝不把失败伪装成空列表**。

V2 适配（仅传输层与配置族，抽取语义零变化）：
- 不引 openai SDK（V2 无此依赖）：``default_client()`` 用 httpx 实现 OpenAI SDK 形状的
  ``.chat.completions.create(...)`` 适配器，请求体与旧库逐字段一致
  （model/messages/temperature/response_format）；测试注入同形状假 client 即可。
- 主备 failover（V2 LLM 惯例，照 intake/research.py）：网络错/5xx 换
  ``GEO_BRANDRANK_LLM_BASE_URL_FALLBACK`` 重试一次；4xx 不重试。
- 配置独立 env 族（**不与 GEO_RESEARCH_LLM_* 混用**，key 绝不入代码/git）::

    GEO_BRANDRANK_LLM_API_KEY           未配 → 禁用态（API 503 llm_disabled，诚实降级）
    GEO_BRANDRANK_LLM_BASE_URL          缺省 https://aihubmix.com
    GEO_BRANDRANK_LLM_BASE_URL_FALLBACK 缺省 https://api.inferera.com
    GEO_BRANDRANK_LLM_MODEL             缺省 deep-deepseek-v4-flash（旧库缺省模型）
    GEO_BRANDRANK_LLM_MAX_WORKERS       批处理线程数，缺省取规则包 llm.max_workers_default
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .rules import DomainRules

_DEFAULT_BASE_URL = "https://aihubmix.com"
_DEFAULT_BASE_URL_FALLBACK = "https://api.inferera.com"
_DEFAULT_MODEL = "deep-deepseek-v4-flash"
_TIMEOUT_S = 60.0          # 品牌抽取输入是整篇回答（比 otp 短信长得多），超时给足
_MAX_TEXT_CHARS = 12000    # 防御性截断（她的脚本无截断；超长回答只浪费 token，不影响口径）


class ExtractError(Exception):
    """单条抽取失败（API/网络/坏 JSON/形状不符）。调用方据此标 failed，绝不落假品牌。"""


def load_config() -> tuple[str, str, str, str] | None:
    """(key, base_url, base_url_fallback, model) 或 None（未配 key→禁用）。

    独立 env 族：未配置即禁用，**不做**跨族回落（V2 任务书口径；旧库的
    AIHUBMIX/OTP 回落在 V2 由 GEO_RESEARCH_LLM_* 承担，不混用）。"""
    key = (os.environ.get("GEO_BRANDRANK_LLM_API_KEY") or "").strip()
    if not key:
        return None
    base = (os.environ.get("GEO_BRANDRANK_LLM_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    fallback = (os.environ.get("GEO_BRANDRANK_LLM_BASE_URL_FALLBACK")
                or _DEFAULT_BASE_URL_FALLBACK).rstrip("/")
    model = (os.environ.get("GEO_BRANDRANK_LLM_MODEL") or _DEFAULT_MODEL).strip()
    return key, base, fallback, model


def max_workers(rules: DomainRules) -> int:
    """批处理线程数：env 优先，缺省取规则包 llm.max_workers_default（=10，她的 MAX_WORKERS）。"""
    raw = (os.environ.get("GEO_BRANDRANK_LLM_MAX_WORKERS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass                      # 坏 env 值不致命，回落规则包默认
    return max(1, int(rules.llm_defaults.get("max_workers_default") or 10))


def llm_status(rules: DomainRules | None = None) -> dict[str, Any]:
    """禁用态诚实回报（API 503 用）：enabled/model/base_url/why。绝不泄漏 key。"""
    cfg = load_config()
    if not cfg:
        return {"enabled": False, "model": None, "base_url": None,
                "why": "GEO_BRANDRANK_LLM_API_KEY 未配置"}
    _, base, fallback, model = cfg
    return {"enabled": True, "model": model, "base_url": base,
            "base_url_fallback": fallback, "why": None,
            "max_workers": max_workers(rules) if rules else None}


def _normalize_base_url(base_url: str) -> str:
    """历史 env 值无 /v1（OpenAI SDK 不自动补）——归一补齐（照 intake/research.py）。"""
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


class _HttpxChatCompletions:
    """OpenAI SDK ``chat.completions`` 形状的 httpx 实现（生产传输层）。

    请求体与旧库 openai SDK 调用逐字段一致；网络错/5xx 换备 base_url 重试一次，
    4xx 或其他异常 → ExtractError(api_error)（照旧库：一律算该条失败）。"""

    def __init__(self, *, api_key: str, base_url: str, base_url_fallback: str) -> None:
        self._api_key = api_key
        self._base_urls = list(dict.fromkeys(
            _normalize_base_url(u) for u in (base_url, base_url_fallback) if u.strip()))

    def create(self, *, model: str, messages: list[dict[str, str]], temperature: float,
               response_format: dict[str, str]) -> Any:
        import httpx  # 延迟 import：测试全 mock 路径零传输依赖
        body = {"model": model, "messages": messages,
                "temperature": temperature, "response_format": response_format}
        last_error: Exception | None = None
        for i, base in enumerate(self._base_urls):
            is_last = i == len(self._base_urls) - 1
            try:
                with httpx.Client(base_url=base, timeout=_TIMEOUT_S,
                                  headers={"Authorization": f"Bearer {self._api_key}"}) as client:
                    response = client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:       # 网络/超时 → 换备通道
                last_error = e
                if is_last:
                    break
                continue
            if response.status_code == 200:
                try:
                    payload = response.json()
                    content = payload["choices"][0]["message"]["content"]
                except Exception as e:  # noqa: BLE001 — 形状不符同坏 JSON 处理
                    raise ExtractError(f"bad_response_shape: {type(e).__name__}: {e}") from e
                return _Completion(content)
            if response.status_code >= 500 and not is_last:
                continue                       # 5xx → 换备通道
            raise ExtractError(f"api_error: upstream_{response.status_code}")
        raise ExtractError(f"api_error: {type(last_error).__name__}: {last_error}")


class _Completion:
    """``response.choices[0].message.content`` 的最小替身。"""

    def __init__(self, content: Any) -> None:
        from types import SimpleNamespace
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _HttpxClient:
    def __init__(self, *, api_key: str, base_url: str, base_url_fallback: str) -> None:
        from types import SimpleNamespace
        completions = _HttpxChatCompletions(
            api_key=api_key, base_url=base_url, base_url_fallback=base_url_fallback)
        self.chat = SimpleNamespace(completions=completions)


def default_client() -> Any:
    """生产 client 工厂（httpx 适配器，OpenAI SDK 形状）。

    未配置 → ExtractError（调用方应先走 load_config()/llm_status 判禁用，此兜底防御）。"""
    cfg = load_config()
    if not cfg:
        raise ExtractError("llm_disabled: 未配置 GEO_BRANDRANK_LLM_API_KEY")
    key, base, fallback, _model = cfg
    return _HttpxClient(api_key=key, base_url=base, base_url_fallback=fallback)


def _parse_brands(content: str) -> list[str]:
    """解析模型返回的 ``{"brands": [...]}``；坏 JSON/形状不符 → ExtractError（不猜、不补）。"""
    try:
        obj = json.loads(content or "")
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"bad_json: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("brands"), list):
        raise ExtractError("bad_shape: 缺 brands 列表")
    # 只收非空字符串项（数字等噪声项丢弃而非臆造转换；她的 .get('brands',[]) 不做校验）
    return [b.strip() for b in obj["brands"] if isinstance(b, str) and b.strip()]


def extract_brands_with_llm(client: Any, reply_text: str, category: str,
                            *, model: str, rules: DomainRules) -> list[str]:
    """单条抽取（对齐 analyze_brand.py L875-906）。失败抛 ExtractError，绝不返回"伪空列表"。"""
    text = (reply_text or "").strip()
    if not text:
        return []                   # 无输入文本=确实无可抽（非 LLM 失败），诚实空列表且不烧 API
    prompt = rules.render_prompt(text[:_MAX_TEXT_CHARS], category)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": rules.system_message},
                {"role": "user", "content": prompt},
            ],
            # moonshot-kimi-k3 的兼容网关只接受 temperature=1；其他模型继续严格
            # 遵循领域规则包的确定性抽取温度（保险/法律当前均为 0）。
            temperature=(1.0 if str(model).strip().lower() == "moonshot-kimi-k3"
                         else rules.llm_defaults.get("temperature", 0)),
            response_format={"type": rules.llm_defaults.get("response_format", "json_object")},
        )
        result = response.choices[0].message.content
    except ExtractError:
        raise
    except Exception as e:  # noqa: BLE001 — API/网络/超时/限流一律算该条失败
        raise ExtractError(f"api_error: {type(e).__name__}: {e}") from e
    return _parse_brands(result)


def extract_brands_batch(client: Any, tasks: list[tuple[int, str, str]],
                         *, model: str, rules: DomainRules,
                         workers: int | None = None,
                         ) -> list[tuple[int, list[str] | None, str | None]]:
    """批量抽取（对齐 analyze_brand.py L909-929：ThreadPoolExecutor + as_completed + 按 idx 排序）。

    tasks: [(idx, reply_text, category)]。返回与输入同序的 [(idx, brands|None, error|None)]——
    每条独立成败：brands=None 且 error=str 即该条失败（调用方标 failed，绝不编造）。"""
    if not tasks:
        return []

    def process_single(
            task: tuple[int, str, str]) -> tuple[int, list[str] | None, str | None]:
        idx, reply_text, category = task
        try:
            return idx, extract_brands_with_llm(client, reply_text, category,
                                                model=model, rules=rules), None
        except ExtractError as e:
            return idx, None, str(e)
        except Exception as e:  # noqa: BLE001 — 防御：线程内任何漏网异常都算该条失败
            return idx, None, f"unexpected: {type(e).__name__}: {e}"

    results: list[tuple[int, list[str] | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=workers or max_workers(rules)) as executor:
        futures = [executor.submit(process_single, t) for t in tasks]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda x: x[0])
    return results
