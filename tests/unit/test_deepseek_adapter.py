"""deepseek 采集适配器单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 / deep_think 透传与 toggle 失败 /
未知 mode 拒绝 / profile 未配置 / screenshot_ref 过 DLP / 代理口令打码 /
deep_think 开关 helper（chip aria-pressed + tab 结构差分）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import (
    CollectionEvidenceRef,
    CollectionTaskInput,
    _normalize_evidence_list,
)
from workflows.activities.deepseek_adapter import (
    CollectedAnswer,
    DeepseekAdapterConfig,
    _build_sse_trace,
    _capture_opened_source_previews,
    _chip_engaged,
    _external_http_url,
    _fast_mode_engaged,
    _host_resolves_globally,
    _ModeToggleFailed,
    _rich_record_from_sse,
    _task_result_from_collected,
    _WallError,
    mask_proxy_url,
    run_deepseek_collection,
)


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-5",
        query="你好，请用一句话介绍你自己",
        model="deepseek",
        region="Tianjin",
        mode=mode,
        adapter="deepseek",
    )


class _FakeSession:
    """注入的浏览器层替身：按构造参数返回结果或抛墙。"""

    def __init__(
        self,
        *,
        result: CollectedAnswer | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.stages: list[str] = []
        self.modes: list[str] = []

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer:
        on_stage("fake_stage")
        self.stages.append("fake_stage")
        self.modes.append(mode)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _factory(session: _FakeSession) -> Callable[..., _FakeSession]:
    def _make(config: DeepseekAdapterConfig, evidence_dir: Path, file_stem: str) -> _FakeSession:
        return session

    return _make


@pytest.fixture
def adapter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_DEEPSEEK_HEADLESS", "1")
    return evidence


async def test_success_maps_result_fields(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="你好！我是 DeepSeek，由深度求索公司开发的 AI 助手。",
            references=[
                {
                    "url": "https://example.com/article/1",
                    "title": "介绍页",
                    "sitename": " example.com ",
                    "summary": None,
                }
            ],
            screenshot_path=shot,
        )
    )
    beats: list[dict[str, Any]] = []
    result = await run_deepseek_collection(
        _item(),
        session_factory=_factory(session),
        heartbeat=lambda payload: beats.append(payload),
    )
    assert result.business_key == "run-9-task-5"
    assert "我是 DeepSeek" in result.answer_text
    assert "参考来源：" in result.answer_text
    assert "https://example.com/article/1" in result.answer_text
    assert result.screenshot_ref == f"file://{shot}"
    assert result.screenshot_ref.startswith("file://")
    assert result.quality_state == "live_valid"
    assert beats and beats[0]["business_key"] == "run-9-task-5"


async def test_login_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-5-a1-login.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError(
            "wall_login_required",
            "deepseek login wall detected right after navigation (redirect to /sign_in)",
            evidence,
        )
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_login_required"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_deep_think_mode_passes_through_to_session(adapter_env: Path) -> None:
    """20260810 起 deep_think 解锁：mode 原样透传到浏览器层，由 session 负责
    快速模式 tab + 深度思考/智能搜索 chips 的 UI 确保。"""
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="深度思考后的回答",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_deepseek_collection(
        _item(mode="deep_think"), session_factory=_factory(session), heartbeat=lambda p: None
    )
    assert session.modes == ["deep_think"]
    assert result.quality_state == "live_valid"
    assert "深度思考后的回答" in result.answer_text


async def test_deep_think_toggle_failure_is_non_retryable(adapter_env: Path) -> None:
    """开关无法确认启用 → mode_toggle_failed，绝不静默回退 normal。"""
    evidence = adapter_env / "run-9-task-5-a1-deep_think.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_ModeToggleFailed("深度思考 chip aria-pressed stuck false", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            _item(mode="deep_think"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "mode_toggle_failed"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_unknown_mode_rejected_as_unsupported(adapter_env: Path) -> None:
    """normal/deep_think 之外的 mode 仍诚实拒绝（mode 门在浏览器启动之前）。"""
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            _item(mode="vision"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []  # mode 门在浏览器启动之前


async def test_missing_profile_dir_is_adapter_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_DEEPSEEK_PROFILE_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_deepseek_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "adapter_not_configured"
    assert exc_info.value.non_retryable is True
    assert session.stages == []

    monkeypatch.delenv("GEO_DEEPSEEK_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_deepseek_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"
    assert exc_info_unset.value.non_retryable is True


async def test_screenshot_ref_and_answer_pass_dlp(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="真实回答正文",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_deepseek_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    # 真调用 DLP：两个字段都必须干净（persist 层同语义）
    assert_secret_free(result.screenshot_ref)
    assert_secret_free(result.answer_text)


def test_proxy_url_masking_never_leaks_credentials() -> None:
    assert mask_proxy_url("http://user:pass@proxy.example.com:8080") == (
        "http://proxy.example.com:8080"
    )
    assert mask_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert mask_proxy_url(None) is None
    assert mask_proxy_url("not-a-url") == "<invalid-proxy-url>"


def test_sse_assembly_real_patch_stream() -> None:
    """回归：2026-07-27 live 实测 JSON-patch 流必须组装出完整正文。

    首版只认 {"o":"APPEND"} 形增量，漏掉无 p/o 的裸增量 {"v":"..."}（主流式形态），
    整流只抽到 patch 形式的 "！"（answer_len=1）；SET/BATCH op 的 "FINISHED" 等状态
    字符串也不得混入正文。
    """
    body = (
        "event: ready\n"
        'data: {"request_message_id":1,"response_message_id":2,"model_type":"default"}\n\n'
        "event: update_session\n"
        'data: {"updated_at":1785151127.1688528}\n\n'
        'data: {"v":{"response":{"message_id":2,"parent_id":1,"role":"ASSISTANT",'
        '"thinking_enabled":false,"status":"WIP","search_enabled":true,"fragments":'
        '[{"id":2,"type":"RESPONSE","content":"你好","references":[],"stage_id":1}]}}}\n\n'
        'data: {"p":"response/fragments/-1/content","o":"APPEND","v":"！"}\n\n'
        'data: {"v":"我是"}\n\n'
        'data: {"v":"DeepSeek"}\n\n'
        'data: {"v":"，由深度求索公司打造的AI助手。"}\n\n'
        'data: {"p":"response","o":"BATCH","v":[{"p":"accumulated_token_usage","v":66},'
        '{"p":"quasi_status","v":"FINISHED"}]}\n\n'
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}\n\n'
        "event: title\n"
        'data: {"content":"一句话自我介绍"}\n\n'
        "event: close\n"
        'data: {"click_behavior":"none"}\n\n'
    )
    rich = _rich_record_from_sse(body)
    assert rich is not None
    assert rich["answer_text"] == "你好！我是DeepSeek，由深度求索公司打造的AI助手。"
    assert "FINISHED" not in rich["answer_text"]
    # title 事件的 {"content":...} 不是正文碎片，不得混入
    assert "一句话自我介绍" not in rich["answer_text"]
    assert rich["references"] == []


def test_sse_assembly_think_fragment_excluded() -> None:
    """THINK 碎片（推理链）不进正文；references 卡片从碎片里抽出。"""
    body = (
        'data: {"v":{"response":{"fragments":['
        '{"id":1,"type":"THINK","content":"先想一下","references":[]},'
        '{"id":2,"type":"RESPONSE","content":"正文","references":['
        '{"url":"https://example.com/a","title":"标题A","site_name":"example.com"}]'
        "}]}}"
        "}\n\n"
        'data: {"v":"。"}\n\n'
    )
    rich = _rich_record_from_sse(body)
    assert rich is not None
    assert rich["answer_text"] == "正文。"
    assert rich["references"] == [
        {
            "url": "https://example.com/a",
            "title": "标题A",
            "sitename": "example.com",
            "summary": None,
        }
    ]


# ---------------------------------------------------------------------------
# deep_think 开关 helper（chip aria-pressed / tab 结构差分）纯函数边界
# ---------------------------------------------------------------------------


class _HelperFakeLocator:
    """chip/tab helper 探针替身：只实现 helper 用到的面。"""

    def __init__(self, present: bool, pressed: str | None = None) -> None:
        self._present = present
        self._pressed = pressed

    @property
    def first(self) -> _HelperFakeLocator:
        return self

    def count(self) -> int:
        return 1 if self._present else 0

    def is_visible(self, timeout: int | None = None) -> bool:
        return self._present

    def get_attribute(self, name: str) -> str | None:
        assert name == "aria-pressed"
        return self._pressed


class _HelperFakePage:
    def __init__(self, chips: dict[str, str | None], tab_state: object) -> None:
        self._chips = chips  # name -> aria-pressed 值；缺 key = chip 不在屏
        self._tab_state = tab_state  # evaluate 返回值原样透传

    def locator(self, selector: str) -> _HelperFakeLocator:
        for name in ("深度思考", "智能搜索"):
            if f'has-text("{name}")' in selector:
                return _HelperFakeLocator(name in self._chips, self._chips.get(name))
        raise AssertionError(f"unexpected selector: {selector}")

    def evaluate(self, script: str) -> object:
        return self._tab_state


def test_chip_engaged_states() -> None:
    assert _chip_engaged(_HelperFakePage({"深度思考": "true"}, None), "深度思考") is True
    assert _chip_engaged(_HelperFakePage({"深度思考": "false"}, None), "深度思考") is False
    # chip 不在屏 / 状态读不出 → None（调用方诚实失败，绝不猜）
    assert _chip_engaged(_HelperFakePage({}, None), "深度思考") is None
    assert _chip_engaged(_HelperFakePage({"深度思考": None}, None), "深度思考") is None


def test_fast_mode_engaged_states() -> None:
    assert _fast_mode_engaged(_HelperFakePage({}, {"found": True, "selected": "快速模式"})) is True
    assert _fast_mode_engaged(_HelperFakePage({}, {"found": True, "selected": "专家模式"})) is False
    # tab 条不在屏 / 结构差分失败 / 探针异常 → None（不可观测不阻断、不猜）
    assert _fast_mode_engaged(_HelperFakePage({}, {"found": False})) is None
    assert _fast_mode_engaged(_HelperFakePage({}, {"found": True, "selected": None})) is None
    assert _fast_mode_engaged(_HelperFakePage({}, None)) is None


def test_sse_assembly_deep_think_stream_excludes_think_and_tools() -> None:
    """回归（20260810 live 实测 deep_think+智能搜索流）：THINK/TOOL_SEARCH/
    TOOL_OPEN 碎片与 [reference:N] 锚点不进正文；TOOL_SEARCH 命中、
    TOOL_OPEN 打开与 RESPONSE 引用分类抽取，不得再把检索候选全部写入引用。

    旧版状态无关组装器把思考链当正文（裸增量无路径归属，THINK 碎片流式期间
    的 {"v":...} 全被收进答案）——本流固定实测结构：快照(THINK) → 路径/裸
    增量（思考） → BATCH 加 TOOL_SEARCH → results SET → 独立 APPEND 加
    RESPONSE → 裸增量（正文） → BATCH fragments/-1 引用锚点。"""
    body = (
        'data: {"v":{"response":{"message_id":2,"role":"ASSISTANT","thinking_enabled":true,'
        '"search_enabled":true,"fragments":[{"id":2,"type":"THINK","content":"用户",'
        '"references":[],"stage_id":1}]}}}\n\n'
        'data: {"p":"response/fragments/-1/content","o":"APPEND","v":"想知道"}\n\n'
        'data: {"v":"最近一周的科技新闻。"}\n\n'
        'data: {"p":"response","o":"BATCH","v":[{"p":"fragments","o":"APPEND","v":'
        '[{"id":3,"type":"TOOL_SEARCH","status":"WIP","content":null,'
        '"queries":[{"query":"科技新闻"}],"results":[],"stage_id":1}]}]}\n\n'
        'data: {"p":"response/fragments/-1/results","o":"SET","v":'
        '[{"url":"https://example.com/a","title":"标题A","site_name":"站点A","snippet":"摘要A"},'
        '{"url":"https://example.com/b","title":"标题B","site_name":"站点B"}]}\n\n'
        # TOOL_OPEN 的 result 单卡（显式建档 id=4）
        'data: {"p":"response/fragments","o":"APPEND","v":'
        '[{"id":4,"type":"TOOL_OPEN","result":'
        '{"url":"https://example.com/b","title":"标题B","site_name":"站点B"}}]}\n\n'
        'data: {"p":"response/fragments","o":"APPEND","v":'
        '[{"id":18,"type":"RESPONSE","content":"过去","references":[],"stage_id":3}]}\n\n'
        'data: {"v":"一周"}\n\n'
        'data: {"v":"，科技新闻如下"}\n\n'
        'data: {"p":"response/fragments/-1","o":"BATCH","v":'
        '[{"p":"content","o":"APPEND","v":"[reference:1]"},'
        '{"p":"references","v":[{"id":4,"type":"TOOL_OPEN"}]}]}\n\n'
        'data: {"v":"。"}\n\n'
        'data: {"p":"response/status","o":"SET","v":"FINISHED"}\n\n'
    )
    rich = _rich_record_from_sse(body)
    assert rich is not None
    assert rich["answer_text"] == "过去一周，科技新闻如下。"
    assert [r["url"] for r in rich["search_results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert [r["url"] for r in rich["opened_pages"]] == ["https://example.com/b"]
    assert rich["opened_pages_observed"] is True
    assert [r["url"] for r in rich["references"]] == ["https://example.com/b"]


def test_sse_taxonomy_keeps_48_search_hits_separate_from_6_opened_pages() -> None:
    search_results = [
        {
            "url": f"https://search.example/{index}",
            "title": f"搜索候选 {index}",
            "site_name": "search.example",
            "snippet": f"摘要 {index}",
        }
        for index in range(1, 49)
    ]
    opened = [
        {
            "id": 100 + index,
            "type": "TOOL_OPEN",
            "result": search_results[index * 7],
        }
        for index in range(6)
    ]
    event = {
        "v": {
            "response": {
                "fragments": [
                    {
                        "id": 1,
                        "type": "TOOL_SEARCH",
                        "queries": [{"query": "盛邦安全"}],
                        "results": search_results,
                    },
                    *opened,
                    {
                        "id": 200,
                        "type": "RESPONSE",
                        "content": "最终答案",
                        "references": [
                            {"id": item["id"], "type": "TOOL_OPEN"} for item in opened
                        ],
                    },
                ]
            }
        }
    }

    rich = _rich_record_from_sse(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    )

    assert rich is not None
    assert len(rich["search_results"]) == 48
    assert len(rich["opened_pages"]) == 6
    assert len(rich["references"]) == 6
    assert {row["url"] for row in rich["opened_pages"]}.issubset(
        {row["url"] for row in rich["search_results"]}
    )
    trace = _build_sse_trace(rich)
    assert len(trace["search_blocks"][0]["results"]) == 48
    assert len(trace["opened_pages"]) == 6
    assert trace["opened_pages_observed"] is True


# ---------------------------------------------------------------------------
# SSE 结构化 trace（思考链/检索词落证据，20260810）与结构化信源映射
# ---------------------------------------------------------------------------

_DEEP_THINK_STREAM = (
    'data: {"v":{"response":{"message_id":2,"role":"ASSISTANT","thinking_enabled":true,'
    '"search_enabled":true,"fragments":[{"id":2,"type":"THINK","content":"用户",'
    '"references":[],"stage_id":1}]}}}\n\n'
    'data: {"p":"response/fragments/-1/content","o":"APPEND","v":"想知道"}\n\n'
    'data: {"v":"最近一周的科技新闻。"}\n\n'
    'data: {"p":"response","o":"BATCH","v":[{"p":"fragments","o":"APPEND","v":'
    '[{"id":3,"type":"TOOL_SEARCH","status":"WIP","content":null,'
    '"queries":[{"query":"科技新闻"}],"results":[],"stage_id":1}]}]}\n\n'
    'data: {"p":"response/fragments/-1/results","o":"SET","v":'
    '[{"url":"https://example.com/a","title":"标题A","site_name":"站点A","snippet":"摘要A"},'
    '{"url":"https://example.com/b","title":"标题B","site_name":"站点B"}]}\n\n'
    'data: {"p":"response/fragments","o":"APPEND","v":'
    '[{"id":18,"type":"RESPONSE","content":"过去","references":[],"stage_id":3}]}\n\n'
    'data: {"v":"一周"}\n\n'
    'data: {"v":"，科技新闻如下"}\n\n'
    'data: {"p":"response/fragments/-1","o":"BATCH","v":'
    '[{"p":"content","o":"APPEND","v":"[reference:1]"},'
    '{"p":"references","v":[{"id":3,"type":"TOOL_SEARCH"}]}]}\n\n'
    'data: {"v":"。"}\n\n'
    'data: {"p":"response/status","o":"SET","v":"FINISHED"}\n\n'
)


def test_sse_assembly_deep_think_captures_thinking_and_queries() -> None:
    """deep_think 流：THINK 碎片内容/检索词/thinking_enabled 进 record（不进正文）。"""
    rich = _rich_record_from_sse(_DEEP_THINK_STREAM)
    assert rich is not None
    assert rich["answer_text"] == "过去一周，科技新闻如下。"
    assert rich["thinking_text"] == "用户想知道最近一周的科技新闻。"
    assert rich["search_queries"] == [{"query": "科技新闻", "ordinal": 1}]
    assert rich["deep_think_active"] is True


def test_sse_assembly_normal_stream_has_empty_thinking() -> None:
    """normal 流无 THINK 碎片：thinking_text 空、deep_think_active=False。"""
    body = (
        'data: {"v":{"response":{"fragments":['
        '{"id":2,"type":"RESPONSE","content":"正文","references":[]}'
        "]}}"
        "}\n\n"
        'data: {"v":"。"}\n\n'
    )
    rich = _rich_record_from_sse(body)
    assert rich is not None
    assert rich["thinking_text"] == ""
    assert rich["search_queries"] == []
    assert rich["deep_think_active"] is False


def test_build_sse_trace_shape() -> None:
    """trace record 词表对齐豆包（trace 回放端点消费 thinking_chain/search_blocks）。"""
    rich = _rich_record_from_sse(_DEEP_THINK_STREAM)
    assert rich is not None
    trace = _build_sse_trace(rich)
    assert trace["engine"] == "deepseek"
    assert trace["deep_think_active"] is True
    assert trace["thinking_chain"][0] == {
        "kind": "reasoning",
        "text": "用户想知道最近一周的科技新闻。",
    }
    assert trace["thinking_chain"][1]["kind"] == "search"
    assert trace["thinking_chain"][1]["queries"] == ["科技新闻"]
    assert [r["url"] for r in trace["search_blocks"][0]["results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert trace["source_taxonomy_version"] == 2
    assert trace["opened_pages_observed"] is True
    assert trace["opened_pages"] == []
    # RESPONSE 只返回 TOOL_SEARCH id，不能把两个检索候选倒推为答案引用。
    assert trace["answer_reference_pages"] == []


def test_tool_open_preview_reopens_real_page_without_synthetic_overlay(tmp_path: Path) -> None:
    events: list[tuple[str, object]] = []

    class FakePage:
        def goto(self, url: str, **kwargs: Any) -> None:
            events.append(("goto", url))
            assert kwargs == {"wait_until": "domcontentloaded", "timeout": 15_000}

        def wait_for_timeout(self, timeout: int) -> None:
            assert timeout == 1_500

        def screenshot(self, *, path: str, full_page: bool) -> None:
            events.append(("screenshot_full_page", full_page))
            Path(path).write_bytes(b"\x89PNG-real-page")

        def title(self) -> str:
            return "真实网页标题"

        def close(self) -> None:
            events.append(("close", True))

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

    evidence, audit = _capture_opened_source_previews(
        FakeContext(),
        [
            {"url": "https://example.com/a", "title": "流中标题"},
            {"url": "http://127.0.0.1/private", "title": "不安全地址"},
        ],
        evidence_dir=tmp_path,
        file_stem="answer-1",
        timeout_error=TimeoutError,
    )

    assert len(evidence) == 1
    assert evidence[0].relation_type == "ai_opened_source_preview"
    assert evidence[0].kind == "source_screenshot"
    assert evidence[0].source_url == "https://example.com/a"
    assert evidence[0].title == "真实网页标题"
    assert evidence[0].cited_text is None
    assert ("screenshot_full_page", False) in events
    assert audit["tool_open_count"] == 2
    assert audit["captured"] == 1
    assert audit["failures"] == [{"ordinal": 2, "error": "unsafe_or_invalid_url"}]


def test_opened_preview_url_guard_rejects_private_and_dns_rebinding_targets() -> None:
    def public_resolver(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    def mixed_resolver(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("10.0.0.8", 0)),
        ]

    assert _host_resolves_globally("example.com", resolver=public_resolver) is True
    assert _host_resolves_globally("example.com", resolver=mixed_resolver) is False
    assert _host_resolves_globally("localhost", resolver=public_resolver) is False
    assert _host_resolves_globally("anything.local", resolver=public_resolver) is False
    assert _host_resolves_globally("metadata.google.internal", resolver=public_resolver) is False
    assert (
        _external_http_url(
            "https://example.com/page", host_guard=lambda host: host == "example.com"
        )
        == "https://example.com/page"
    )
    for unsafe in (
        "http://127.0.0.1/a",
        "http://[::1]/a",
        "http://10.0.0.1/a",
        "http://localhost/a",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        assert _external_http_url(unsafe, host_guard=lambda _host: False) is None


def test_task_result_keeps_opened_preview_out_of_answer_citations(tmp_path: Path) -> None:
    shot = tmp_path / "answer.png"
    shot.write_bytes(b"\x89PNG-answer")
    preview = tmp_path / "opened.png"
    preview.write_bytes(b"\x89PNG-opened")
    collected = CollectedAnswer(
        answer_text="正文",
        references=[],
        screenshot_path=shot,
        opened_source_previews=[
            CollectionEvidenceRef(
                kind="source_screenshot",
                path=str(preview),
                relation_type="ai_opened_source_preview",
                mime_type="image/png",
                source_url="https://example.com/opened",
            )
        ],
    )

    result = _task_result_from_collected(_item(), collected)

    assert result.citations == []
    assert [item.relation_type for item in result.evidence] == ["ai_opened_source_preview"]
    assert _normalize_evidence_list(result.evidence)[0].relation_type == (
        "ai_opened_source_preview"
    )


def test_task_result_prefers_official_share_image_as_screenshot_ref(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.png"
    runtime.write_bytes(b"runtime")
    official = tmp_path / "official-share.png"
    official.write_bytes(b"official")
    collected = CollectedAnswer(
        answer_text="正文",
        references=[],
        screenshot_path=runtime,
        evidence=[
            CollectionEvidenceRef(
                kind="share_image",
                path=str(official),
                relation_type="official_share_image",
                mime_type="image/png",
                source_url="https://chat.deepseek.com/share/example",
            )
        ],
    )

    result = _task_result_from_collected(_item(), collected)

    assert result.screenshot_ref == f"file://{official}"


def test_task_result_maps_citations_trace_and_queries(tmp_path: Path) -> None:
    """ok 映射：references → 结构化 citations（cited_text=None 诚实缺省）；
    trace_path → kind="sse" 证据；search_queries 透传。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    trace = tmp_path / "run-9-task-5-sse-trace.json"
    trace.write_text("{}", encoding="utf-8")
    collected = CollectedAnswer(
        answer_text="正文",
        references=[
            {"url": "https://example.com/a", "title": "标题A", "sitename": "站点A"},
            {"url": "not-a-url", "title": "假链接"},
        ],
        screenshot_path=shot,
        trace_path=trace,
        search_queries=[{"query": "科技新闻", "ordinal": 1}],
    )
    result = _task_result_from_collected(_item(mode="deep_think"), collected)
    assert result.citations == [
        {"url": "https://example.com/a", "title": "标题A", "cited_text": None}
    ]
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "sse"
    assert result.evidence[0].relation_type == "answer_sse_trace"
    assert result.evidence[0].path == str(trace)
    assert result.search_queries == [{"query": "科技新闻", "ordinal": 1}]


def test_task_result_without_trace_has_no_evidence(tmp_path: Path) -> None:
    """SSE 解析失败（DOM 兜底）→ trace_path=None → 不出 sse 证据（诚实缺省）。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    collected = CollectedAnswer(
        answer_text="正文",
        references=[],
        screenshot_path=shot,
    )
    result = _task_result_from_collected(_item(), collected)
    assert result.evidence == []
    assert result.citations == []
    assert result.search_queries == []
