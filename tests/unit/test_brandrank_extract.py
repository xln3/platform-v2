"""domain.brandrank.extract：LLM 抽取（全 mock client）——成功解析/失败诚实/禁用态/批处理/配置。

移植自旧库 server/tests/test_brandrank_extract.py：抽取语义用例逐行保留（SDK 形状假 client
在 V2 由 httpx 适配器实现，接口形状不变）；配置族换 GEO_BRANDRANK_LLM_*（独立 env 族，
无跨族回落）；新增 httpx 传输层（主备 failover/4xx/坏形状）用例。
"""
import json
from types import SimpleNamespace

import httpx
import pytest

from domain.brandrank import extract
from domain.brandrank.rules import load_domain


@pytest.fixture(scope="module")
def rules():
    return load_domain("insurance")


class FakeClient:
    """OpenAI SDK 形状的假 client：按 reply_text 内容决定返回/抛错，记录每次调用参数。"""

    def __init__(self, behavior):
        self.calls = []
        self._behavior = behavior            # fn(reply_text) -> list[str] | Exception
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, temperature, response_format):
        user_prompt = messages[1]["content"]
        reply_text = user_prompt.split("以下是AI回复文本：\n", 1)[1]
        self.calls.append({"model": model, "messages": messages,
                           "temperature": temperature, "response_format": response_format})
        outcome = self._behavior(reply_text)
        if isinstance(outcome, Exception):
            raise outcome
        content = outcome if isinstance(outcome, str) else json.dumps({"brands": outcome})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ── 单条抽取：prompt 用她的模板、temperature=0、json_object ─────────────
def test_extract_success_uses_her_prompt_and_params(rules):
    client = FakeClient(lambda text: ["中意人寿", "中国平安"])
    brands = extract.extract_brands_with_llm(client, "推荐中意人寿和平安", "保险公司",
                                             model="m-test", rules=rules)
    assert brands == ["中意人寿", "中国平安"]
    call = client.calls[0]
    assert call["model"] == "m-test"
    assert call["temperature"] == 0                            # 她的 L899
    assert call["response_format"] == {"type": "json_object"}  # 她的 L900
    assert call["messages"][0] == {"role": "system", "content": rules.system_message}
    assert '关于"保险公司"的AI回复文本' in call["messages"][1]["content"]
    assert "推荐中意人寿和平安" in call["messages"][1]["content"]


def test_extract_moonshot_kimi_k3_uses_required_temperature(rules):
    client = FakeClient(lambda text: ["中意人寿"])
    brands = extract.extract_brands_with_llm(
        client, "推荐中意人寿", "保险公司", model="moonshot-kimi-k3", rules=rules)
    assert brands == ["中意人寿"]
    assert client.calls[0]["temperature"] == 1.0


def test_extract_category_parameterized(rules):
    """category 可配（信任桥模式）：渲染进 prompt。"""
    client = FakeClient(lambda text: [])
    extract.extract_brands_with_llm(client, "正文", "养老保险公司", model="m", rules=rules)
    assert '关于"养老保险公司"的AI回复文本' in client.calls[0]["messages"][1]["content"]


def test_extract_empty_text_no_api_call(rules):
    client = FakeClient(lambda text: ["不应出现"])
    assert extract.extract_brands_with_llm(client, "   ", "保险公司", model="m", rules=rules) == []
    assert client.calls == []                                  # 空输入诚实空列表，不烧 API


def test_extract_bad_json_and_bad_shape_raise(rules):
    with pytest.raises(extract.ExtractError):
        extract.extract_brands_with_llm(FakeClient(lambda t: "不是json"), "正文", "c",
                                        model="m", rules=rules)
    with pytest.raises(extract.ExtractError):
        extract.extract_brands_with_llm(FakeClient(lambda t: '{"no_brands": 1}'), "正文", "c",
                                        model="m", rules=rules)


def test_extract_api_error_raises_never_fabricates(rules):
    client = FakeClient(lambda t: RuntimeError("boom"))
    with pytest.raises(extract.ExtractError, match="api_error"):
        extract.extract_brands_with_llm(client, "正文", "c", model="m", rules=rules)


def test_extract_filters_non_string_items(rules):
    client = FakeClient(lambda t: '{"brands": ["中意人寿", 42, "", null, " 中国平安 "]}')
    brands = extract.extract_brands_with_llm(client, "正文", "c", model="m", rules=rules)
    assert brands == ["中意人寿", "中国平安"]                  # 噪声项丢弃而非臆造转换


# ── 批处理：线程池、按 idx 排序、每条独立成败 ───────────────────────────
def test_batch_mixed_outcomes_preserve_order(rules):
    def behavior(text):
        if "炸" in text:
            return RuntimeError("api down")
        if "坏" in text:
            return "not json"
        return [text.strip()[:2]]
    client = FakeClient(behavior)
    tasks = [(0, "甲甲", "c"), (1, "炸", "c"), (2, "乙乙", "c"), (3, "坏", "c")]
    out = extract.extract_brands_batch(client, tasks, model="m", rules=rules, workers=4)
    assert [r[0] for r in out] == [0, 1, 2, 3]                 # 与输入同序
    assert out[0] == (0, ["甲甲"], None)
    assert out[1][1] is None and "api_error" in out[1][2]      # 失败诚实 error，绝不编造
    assert out[2] == (2, ["乙乙"], None)
    assert out[3][1] is None and "bad_json" in out[3][2]


def test_batch_empty_tasks(rules):
    assert extract.extract_brands_batch(FakeClient(lambda t: []), [], model="m",
                                        rules=rules) == []


# ── 配置：GEO_BRANDRANK_LLM_* 独立 env 族、无跨族回落、无 key→禁用 ────────
@pytest.fixture
def clean_env(monkeypatch):
    for k in ("GEO_BRANDRANK_LLM_API_KEY", "GEO_BRANDRANK_LLM_BASE_URL",
              "GEO_BRANDRANK_LLM_BASE_URL_FALLBACK", "GEO_BRANDRANK_LLM_MODEL",
              "GEO_BRANDRANK_LLM_MAX_WORKERS"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_config_disabled_honest(clean_env, rules):
    assert extract.load_config() is None
    st = extract.llm_status(rules)
    assert st["enabled"] is False and st["model"] is None and "未配置" in st["why"]
    with pytest.raises(extract.ExtractError, match="llm_disabled"):
        extract.default_client()


def test_config_env_and_defaults(clean_env):
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    clean_env.setenv("GEO_BRANDRANK_LLM_MODEL", "m-brand")
    key, base, fallback, model = extract.load_config()
    assert key == "k1" and model == "m-brand"
    assert base == "https://aihubmix.com"                      # V2 主通道缺省
    assert fallback == "https://api.inferera.com"              # 备通道缺省


def test_config_explicit_base_urls(clean_env):
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    clean_env.setenv("GEO_BRANDRANK_LLM_BASE_URL", "https://main.example/v1/")
    clean_env.setenv("GEO_BRANDRANK_LLM_BASE_URL_FALLBACK", "https://bak.example/v1/")
    key, base, fallback, model = extract.load_config()
    assert base == "https://main.example/v1"                   # 尾斜杠剥掉
    assert fallback == "https://bak.example/v1"
    assert model == "deep-deepseek-v4-flash"                   # 缺省模型（旧库缺省）


def test_max_workers_config(clean_env, rules):
    assert extract.max_workers(rules) == 10                    # 规则包默认（她的 MAX_WORKERS）
    clean_env.setenv("GEO_BRANDRANK_LLM_MAX_WORKERS", "3")
    assert extract.max_workers(rules) == 3
    clean_env.setenv("GEO_BRANDRANK_LLM_MAX_WORKERS", "junk")
    assert extract.max_workers(rules) == 10                    # 坏值回落默认


# ── httpx 传输层（生产 default_client 的真实形状；MockTransport 接缝）────────
def _mock_transport(handler):
    """把 httpx.Client 的 transport 换进适配器：monkeypatch httpx.Client 构造。"""

    class _MockClient(httpx.Client):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    return _MockClient


def _ok_payload(brands):
    return {"choices": [{"message": {"content": json.dumps({"brands": brands})}}]}


def test_httpx_client_success_shape(monkeypatch, clean_env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_payload(["中意人寿"]))

    monkeypatch.setattr(httpx, "Client", _mock_transport(handler))
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    clean_env.setenv("GEO_BRANDRANK_LLM_BASE_URL", "https://main.example")
    client = extract.default_client()
    brands = extract.extract_brands_with_llm(client, "推荐中意人寿", "保险公司",
                                             model="m1", rules=load_domain("insurance"))
    assert brands == ["中意人寿"]
    assert seen["url"] == "https://main.example/v1/chat/completions"   # /v1 自动补齐
    assert seen["authorization"] == "Bearer k1"
    # 请求体与旧库 SDK 调用逐字段一致
    assert seen["body"]["model"] == "m1"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][0]["role"] == "system"


def test_httpx_client_5xx_fails_over_to_fallback(monkeypatch, clean_env):
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "main.example" in str(request.url):
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json=_ok_payload(["中国平安"]))

    monkeypatch.setattr(httpx, "Client", _mock_transport(handler))
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    clean_env.setenv("GEO_BRANDRANK_LLM_BASE_URL", "https://main.example")
    clean_env.setenv("GEO_BRANDRANK_LLM_BASE_URL_FALLBACK", "https://bak.example")
    brands = extract.extract_brands_with_llm(
        extract.default_client(), "推荐中国平安", "保险公司",
        model="m1", rules=load_domain("insurance"))
    assert brands == ["中国平安"]
    assert urls == ["https://main.example/v1/chat/completions",
                    "https://bak.example/v1/chat/completions"]         # 主 5xx → 备成功


def test_httpx_client_4xx_no_failover(monkeypatch, clean_env):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(400, text="bad request")

    monkeypatch.setattr(httpx, "Client", _mock_transport(handler))
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    with pytest.raises(extract.ExtractError, match="upstream_400"):
        extract.extract_brands_with_llm(
            extract.default_client(), "正文", "保险公司",
            model="m1", rules=load_domain("insurance"))
    assert len(calls) == 1                                       # 4xx 不重试不 failover


def test_httpx_client_both_down_raises(monkeypatch, clean_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    monkeypatch.setattr(httpx, "Client", _mock_transport(handler))
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    with pytest.raises(extract.ExtractError, match="api_error"):
        extract.extract_brands_with_llm(
            extract.default_client(), "正文", "保险公司",
            model="m1", rules=load_domain("insurance"))


def test_httpx_client_bad_response_shape(monkeypatch, clean_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    monkeypatch.setattr(httpx, "Client", _mock_transport(handler))
    clean_env.setenv("GEO_BRANDRANK_LLM_API_KEY", "k1")
    with pytest.raises(extract.ExtractError, match="bad_response_shape|api_error"):
        extract.extract_brands_with_llm(
            extract.default_client(), "正文", "保险公司",
            model="m1", rules=load_domain("insurance"))
