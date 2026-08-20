"""通义千问采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / 未知 mode 拒绝
（normal/deep_think 放行且 mode 透传）/ profile 未配置 / 发送墙证据 /
screenshot_ref+answer 过 DLP / 代理口令打码 / 思考链抽取与 trace 词表。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import CollectionTaskInput
from workflows.activities.tongyi_adapter import (
    _ANSWER_EXTRACT_JS,
    _ELEMENT_TEXT_JS,
    CollectedAnswer,
    TongyiAdapterConfig,
    _build_tongyi_trace,
    _composer_value_empty,
    _extract_tongyi_thinking,
    _task_result_from_collected,
    _WallError,
    mask_proxy_url,
    run_tongyi_collection,
)


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-2",
        query="你好，请用一句话介绍你自己",
        model="tongyi",
        region="Beijing",
        mode=mode,
        adapter="tongyi",
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
        self.seen_mode: str | None = None

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer:
        on_stage("fake_stage")
        self.stages.append("fake_stage")
        self.seen_mode = mode
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _factory(session: _FakeSession) -> Callable[..., _FakeSession]:
    def _make(config: TongyiAdapterConfig, evidence_dir: Path, file_stem: str) -> _FakeSession:
        return session

    return _make


@pytest.fixture
def adapter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_TONGYI_HEADLESS", "1")
    return evidence


async def test_success_maps_result_fields(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-2-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="我是通义千问，由阿里巴巴开发的 AI 助手。",
            references=[
                {
                    "url": "https://example.com/article/1",
                    "title": "产品页",
                    "sitename": " example.com ",
                }
            ],
            screenshot_path=shot,
        )
    )
    beats: list[dict[str, Any]] = []
    result = await run_tongyi_collection(
        _item(),
        session_factory=_factory(session),
        heartbeat=lambda payload: beats.append(payload),
    )
    assert result.business_key == "run-9-task-2"
    assert result.answer_text == "我是通义千问，由阿里巴巴开发的 AI 助手。"
    assert result.screenshot_ref == f"file://{shot}"
    assert result.quality_state == "live_valid"
    assert beats and beats[0]["business_key"] == "run-9-task-2"


async def test_login_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-2-a1-login.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_login_required", "tongyi login wall detected", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_login_required"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_send_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-2-a1-send_wall.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_send", "send-not-accepted: composer still populated", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_send"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


async def test_unknown_mode_rejected_as_unsupported(adapter_env: Path) -> None:
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            _item(mode="expert"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []  # mode 门在浏览器启动之前


async def test_deep_think_accepted_and_mode_forwarded(adapter_env: Path) -> None:
    """deep_think（思考研究）20260810 起放行：mode 原样透传 session 层。"""
    shot = adapter_env / "run-9-task-2-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="思考研究回答",
            references=[],
            screenshot_path=shot,
            search_queries=[{"query": "通义千问 评测", "ordinal": 1}],
        )
    )
    result = await run_tongyi_collection(
        _item(mode="deep_think"),
        session_factory=_factory(session),
        heartbeat=lambda p: None,
    )
    assert session.seen_mode == "deep_think"
    assert result.quality_state == "live_valid"
    assert result.search_queries == [{"query": "通义千问 评测", "ordinal": 1}]


async def test_missing_profile_dir_is_adapter_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_TONGYI_PROFILE_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "adapter_not_configured"
    assert exc_info.value.non_retryable is True
    assert session.stages == []

    monkeypatch.delenv("GEO_TONGYI_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_tongyi_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info_unset.value.type == "adapter_not_configured"
    assert exc_info_unset.value.non_retryable is True


async def test_screenshot_ref_and_answer_pass_dlp(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-2-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="真实回答正文",
            references=[],
            screenshot_path=shot,
        )
    )
    result = await run_tongyi_collection(
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


def test_composer_value_empty_recognizes_qianwen_placeholder() -> None:
    """qianwen 空 composer textContent = \\ufeff向千问提问（2026-07-27 live 实测）。"""
    assert _composer_value_empty("\ufeff向千问提问") is True
    assert _composer_value_empty("向千问提问") is True
    assert _composer_value_empty("") is True
    assert _composer_value_empty(None) is True
    assert _composer_value_empty("  ") is True
    assert _composer_value_empty("你好，请用一句话介绍你自己") is False
    assert _composer_value_empty("\ufeff你好") is False


# ---------------------------------------------------------------------------
# 结构化 trace 证据（20260810，kind="sse"/transport="dom"；deep_think 起思考链/
# 检索词自思考流程卡抽取折叠，normal 维持 refs-only 行为）
# ---------------------------------------------------------------------------


def test_build_tongyi_trace_shape() -> None:
    """normal 回归：refs → search_blocks 折叠（DeepSeek 形态）；thinking_chain/
    queries 空（thinking=None 时与旧版行为完全一致）。"""
    refs = [
        {"url": "https://example.com/a", "title": "标题A", "sitename": "站点A"},
        {"url": "https://example.com/b", "title": None, "sitename": None},
    ]
    trace = _build_tongyi_trace(refs)
    assert trace["engine"] == "tongyi"
    assert trace["transport"] == "dom"
    assert trace["deep_think_active"] is False
    assert trace["thinking_chain"] == []
    assert trace["queries"] == []
    block = trace["search_blocks"][0]
    assert block["scene"] is None
    assert block["queries"] == []
    assert block["summary"] == ""
    assert [r["url"] for r in block["results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert block["results"][0]["rank"] == 1
    assert block["results"][0]["site"] == "站点A"
    assert block["results"][1]["title"] == "未命名来源"
    assert block["results"][1]["summary"] == ""
    empty = _build_tongyi_trace([])
    assert empty["search_blocks"] == []


def test_build_tongyi_trace_deep_think_full_shape() -> None:
    """deep_think 全量：reasoning/search 步骤进 thinking_chain，搜索步骤 results
    折叠为独立 search_block，references 折叠照常保留，queries 放平台真实检索词，
    deep_think_active 以实际抽到思考卡为准。"""
    thinking = {
        "card_found": True,
        "steps": [
            {"kind": "reasoning", "title": "检索最新资产搜索评测", "text": "需搜索最新信息"},
            {"kind": "reasoning", "title": "已完成思考", "text": ""},
            {
                "kind": "search",
                "title": "搜索 2 个关键词，参考 3 篇资料",
                "queries": ["资产搜索引擎对比", "测绘引擎排名"],
                "results": [
                    {"title": "结果一", "url": "https://example.com/r1"},
                    {"title": "结果二", "url": None},
                ],
            },
        ],
        "queries": ["资产搜索引擎对比", "测绘引擎排名"],
        "thinking_text": "检索最新资产搜索评测\n需搜索最新信息",
    }
    refs = [{"url": "https://example.com/a", "title": "标题A", "sitename": "站点A"}]
    trace = _build_tongyi_trace(refs, thinking=thinking)
    assert trace["engine"] == "tongyi"
    assert trace["transport"] == "dom"
    assert trace["deep_think_active"] is True
    assert trace["queries"] == ["资产搜索引擎对比", "测绘引擎排名"]
    chain = trace["thinking_chain"]
    assert chain[0] == {
        "kind": "reasoning",
        "text": "检索最新资产搜索评测\n需搜索最新信息",
    }
    assert chain[1] == {"kind": "reasoning", "text": "已完成思考"}  # 标题即内容
    assert chain[2] == {
        "kind": "search",
        "queries": ["资产搜索引擎对比", "测绘引擎排名"],
        "summary": "搜索 2 个关键词，参考 3 篇资料",
    }
    blocks = trace["search_blocks"]
    assert len(blocks) == 2  # 搜索步骤块 + references 折叠块
    step_block = blocks[0]
    assert step_block["queries"] == ["资产搜索引擎对比", "测绘引擎排名"]
    assert step_block["summary"] == "搜索 2 个关键词，参考 3 篇资料"
    assert step_block["results"][0] == {
        "title": "结果一",
        "url": "https://example.com/r1",
        "site": None,
        "rank": 1,
        "summary": "",
    }
    assert step_block["results"][1]["url"] is None  # 无锚点诚实缺省
    assert [r["url"] for r in blocks[1]["results"]] == ["https://example.com/a"]
    # 无 references 时思考卡内容照样出 trace（写盘门由 card_found 决定）
    no_refs = _build_tongyi_trace([], thinking=thinking)
    assert len(no_refs["search_blocks"]) == 1
    assert no_refs["deep_think_active"] is True
    # 卡片缺货：deep_think_active 诚实标 False，不出思考链
    missing = _build_tongyi_trace(refs, thinking={"card_found": False, "steps": [], "queries": []})
    assert missing["deep_think_active"] is False
    assert missing["thinking_chain"] == []


# ---------------------------------------------------------------------------
# 思考流程卡抽取（_extract_tongyi_thinking；fake evaluate 注入探针产出）
# ---------------------------------------------------------------------------


class _ThinkingProbePage:
    """只实现 evaluate 的页面替身：按注入载荷/异常模拟探针结果。"""

    def __init__(self, payload: Any = None, *, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises

    def evaluate(self, _script: str) -> Any:
        if self._raises:
            raise RuntimeError("probe exploded")
        return self._payload


def _probe_payload() -> dict[str, Any]:
    """JS 探针在真实 deep_think 页（probe11）上的产出形状（引号已 strip、
    隐藏副本已去重——JS 侧行为由本地 chromium 对存档 DOM 一次性验证）。"""
    return {
        "card_found": True,
        "steps": [
            {
                "kind": "reasoning",
                "title": "检索最新资产搜索评测",
                "text": "需搜索最新信息，而非仅依赖既有结果。",
            },
            {
                "kind": "search",
                "title": "搜索 2 个关键词，参考 3 篇资料",
                "queries": ["资产搜索引擎对比", "测绘引擎排名"],
                "results": [
                    {"title": "结果一", "url": "https://example.com/r1"},
                    {"title": "", "url": "https://example.com/skip"},  # 空标题丢弃
                    "garbage",
                ],
            },
            {"kind": "reasoning", "title": "已完成思考", "text": ""},
        ],
        "queries": ["资产搜索引擎对比", "测绘引擎排名"],
    }


def test_extract_tongyi_thinking_full_card() -> None:
    thinking = _extract_tongyi_thinking(_ThinkingProbePage(_probe_payload()))
    assert thinking["card_found"] is True
    assert thinking["queries"] == ["资产搜索引擎对比", "测绘引擎排名"]
    steps = thinking["steps"]
    assert [s["kind"] for s in steps] == ["reasoning", "search", "reasoning"]
    assert steps[0]["text"] == "需搜索最新信息，而非仅依赖既有结果。"
    assert steps[1]["results"] == [{"title": "结果一", "url": "https://example.com/r1"}]
    assert steps[2]["text"] == ""
    assert thinking["thinking_text"] == (
        "检索最新资产搜索评测\n需搜索最新信息，而非仅依赖既有结果。"
    )


def test_extract_tongyi_thinking_truncates_long_reasoning() -> None:
    payload = _probe_payload()
    payload["steps"][0]["text"] = "长" * 6_000
    thinking = _extract_tongyi_thinking(_ThinkingProbePage(payload))
    assert len(thinking["steps"][0]["text"]) == 5_000  # 对齐豆包水位


def test_extract_tongyi_thinking_honest_empty() -> None:
    """无卡 / 非 dict / 探针异常 → 统一空形状（零合成，绝不编造思考链）。"""
    empty = {"card_found": False, "steps": [], "queries": [], "thinking_text": ""}
    assert _extract_tongyi_thinking(_ThinkingProbePage(None)) == empty
    assert _extract_tongyi_thinking(_ThinkingProbePage({"card_found": False})) == empty
    assert _extract_tongyi_thinking(_ThinkingProbePage("junk")) == empty
    assert _extract_tongyi_thinking(_ThinkingProbePage(raises=True)) == empty


def test_task_result_maps_trace_evidence(tmp_path: Path) -> None:
    """trace_path → kind="sse" 证据（references 折叠 search_blocks）。"""
    shot = tmp_path / "run-9-task-2.png"
    shot.write_bytes(b"\x89PNG-fake")
    trace = tmp_path / "run-9-task-2-sse-trace.json"
    trace.write_text("{}", encoding="utf-8")
    collected = CollectedAnswer(
        answer_text="正文", references=[], screenshot_path=shot, trace_path=trace
    )
    result = _task_result_from_collected(_item(), collected)
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "sse"
    assert result.evidence[0].relation_type == "answer_sse_trace"
    assert result.evidence[0].mime_type == "application/json"
    assert result.evidence[0].path == str(trace)


def test_task_result_without_trace_has_no_evidence(tmp_path: Path) -> None:
    """无引用（trace_path=None）→ 不出 sse 证据（诚实缺省，不出空证据）。"""
    shot = tmp_path / "run-9-task-2.png"
    shot.write_bytes(b"\x89PNG-fake")
    collected = CollectedAnswer(answer_text="正文", references=[], screenshot_path=shot)
    result = _task_result_from_collected(_item(), collected)
    assert result.evidence == []


def test_extract_js_serializes_tables_as_markdown() -> None:
    # 20260812 锚定（W3 表格碎片证据根治，yiyan 同款）：容器级走查与旧选择器链
    # 兜底两路都必须把 <table> 序列化为 markdown 管道行，防回退 innerText 直出。
    # live 实证：tongyi_bj 当前页 7×42 测绘平台对比表出完整管道表。
    for js in (_ANSWER_EXTRACT_JS, _ELEMENT_TEXT_JS):
        assert "querySelectorAll('table')" in js
        assert "querySelectorAll('th,td')" in js
        assert "'---'" in js
        assert "replaceWith" in js
