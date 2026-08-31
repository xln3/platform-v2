"""元宝采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / 未知 mode 拒绝
（normal/deep_think 均放行）/ profile 未配置 / 截图与正文过 DLP / 代理口令打码。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities import yuanbao_adapter as yuanbao_module
from workflows.activities.collection import CollectionEvidenceRef, CollectionTaskInput
from workflows.activities.yuanbao_adapter import (
    _BODY_TEXT_JS,
    _COMBINED_MENU_STATE_JS,
    _COMBINED_MODE_STATE_JS,
    CollectedAnswer,
    YuanbaoAdapterConfig,
    YuanbaoBatchItemOutcome,
    _batch_item_result,
    _build_yuanbao_trace,
    _ensure_combined_mode,
    _extract_thinking_text,
    _task_result_from_collected,
    _WallError,
    _yuanbao_record_from_sse,
    run_yuanbao_collection,
)


def _item(mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-5",
        query="你好，请用一句话介绍你自己",
        model="yuanbao",
        region="Beijing",
        mode=mode,
        adapter="yuanbao",
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

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer:
        on_stage("fake_stage")
        self.stages.append("fake_stage")
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _factory(session: _FakeSession) -> Callable[..., _FakeSession]:
    def _make(config: YuanbaoAdapterConfig, evidence_dir: Path, file_stem: str) -> _FakeSession:
        return session

    return _make


@pytest.fixture
def adapter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(evidence))
    monkeypatch.setenv("GEO_YUANBAO_HEADLESS", "1")
    return evidence


async def test_success_maps_result_fields(adapter_env: Path) -> None:
    shot = adapter_env / "run-9-task-5-a1.png"
    shot.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        result=CollectedAnswer(
            answer_text="我是腾讯元宝，一个 AI 助手。",
            references=[
                {
                    "url": "https://example.com/about/1",
                    "title": "介绍页",
                    "sitename": " example.com ",
                    "summary": None,
                    "index": 0,
                }
            ],
            screenshot_path=shot,
        )
    )
    beats: list[dict[str, Any]] = []
    result = await run_yuanbao_collection(
        _item(),
        session_factory=_factory(session),
        heartbeat=lambda payload: beats.append(payload),
    )
    assert result.business_key == "run-9-task-5"
    assert result.answer_text == "我是腾讯元宝，一个 AI 助手。"
    assert result.screenshot_ref == f"file://{shot}"
    assert result.screenshot_ref.startswith("file://")
    assert result.citations == [
        {
            "url": "https://example.com/about/1",
            "title": "介绍页",
            "cited_text": None,
            "platform_ordinal": 1,
            "ordinal_base": 1,
        }
    ]
    assert result.quality_state == "live_valid"
    assert beats and beats[0]["business_key"] == "run-9-task-5"


async def test_login_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-5-a1-login.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_login_required", "yuanbao login wall detected", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_login_required"
    assert exc_info.value.non_retryable is True
    assert "evidence=" in str(exc_info.value)


def _mojibake(value: str) -> str:
    return value.encode().decode("latin-1")


def test_new_sse_keeps_all_candidates_but_only_cited_cards() -> None:
    raw = "\n\n".join(
        [
            "data: "
            + yuanbao_module.json.dumps(
                {
                    "type": "searchGuid",
                    "docs": [
                        {
                            "index": 1,
                            "url": "https://example.com/a",
                            "title": _mojibake("来源甲"),
                            "quote": _mojibake("摘要甲"),
                        },
                        {
                            "index": 2,
                            "url": "https://example.com/b",
                            "title": _mojibake("来源乙"),
                            "quote": _mojibake("摘要乙"),
                        },
                        {
                            "index": 3,
                            "url": "https://example.com/c",
                            "title": _mojibake("来源丙"),
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            'data: {"type":"text","msg":"正文[citation:2]"}',
            'data: {"type":"text","msg":"补充[citation:2][citation:1][citation:4]"}',
        ]
    )

    record = _yuanbao_record_from_sse(raw)

    assert record is not None
    assert record["answer_text"] == "正文补充"
    assert record["search_guid_observed"] is True
    assert record["candidate_count"] == 3
    assert record["citation_indexes"] == [2, 1, 4]
    assert record["unresolved_citation_indexes"] == [4]
    assert [row["platform_ordinal"] for row in record["references"]] == [2, 1]
    assert [row["title"] for row in record["references"]] == ["来源乙", "来源甲"]
    event = record["retrieval_events"][0]
    assert [row["url"] for row in event["candidates"]] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert [row["final_reference_ordinal"] for row in event["final_references"]] == [
        2,
        1,
    ]


def test_sse_answer_does_not_require_search_guid() -> None:
    raw = (
        'event: message\r\ndata: {"type":"text","msg":"普通"}\r\n\r\n'
        'data: {"type":"text","msg":"回答"}\r\n\r\n'
        "data: [DONE]\r\n\r\n"
    )

    record = _yuanbao_record_from_sse(raw)

    assert record is not None
    assert record["answer_text"] == "普通回答"
    assert record["search_guid_observed"] is False
    assert record["references"] == []
    assert record["retrieval_events"] == []


def test_sse_preserves_identical_protocol_delta_chunks() -> None:
    raw = "\n\n".join(
        [
            'data: {"type":"text","msg":"0123456789"}',
            'data: {"type":"text","msg":"0123456789"}',
        ]
    )

    record = _yuanbao_record_from_sse(raw)

    assert record is not None
    assert record["answer_text"] == "01234567890123456789"


def test_task_result_recovers_citations_from_existing_raw_sse(tmp_path: Path) -> None:
    shot = tmp_path / "answer.png"
    shot.write_bytes(b"\x89PNG-fake")
    raw_path = tmp_path / "answer-sse-raw.txt"
    raw_path.write_text(
        'data: {"type":"searchGuid","docs":['
        '{"index":1,"url":"https://example.com/a","title":"来源A","quote":"摘录A"},'
        '{"index":2,"url":"https://example.com/b","title":"来源B","quote":"摘录B"}'
        ']}\n\ndata: {"type":"text","msg":"答案[citation:2]"}\n\n',
        encoding="utf-8",
    )
    collected = CollectedAnswer(
        answer_text="答案",
        references=[{"url": None, "title": "DOM 只有标题"}],
        screenshot_path=shot,
        raw_evidence=[
            CollectionEvidenceRef(
                kind="sse_raw",
                path=str(raw_path),
                relation_type="answer_sse_raw",
                mime_type="text/event-stream",
            )
        ],
    )

    result = _task_result_from_collected(_item(), collected)

    assert result.citations == [
        {
            "url": "https://example.com/b",
            "title": "来源B",
            "cited_text": "摘录B",
            "platform_ordinal": 2,
            "ordinal_base": 1,
        }
    ]
    assert len(result.retrieval_events[0]["candidates"]) == 2


async def test_captcha_wall_is_non_retryable(adapter_env: Path) -> None:
    evidence = adapter_env / "run-9-task-5-a1-captcha.png"
    evidence.write_bytes(b"\x89PNG-fake")
    session = _FakeSession(
        error=_WallError("wall_captcha", "captcha challenge appeared post-send", evidence)
    )
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "wall_captcha"
    assert exc_info.value.non_retryable is True


async def test_unknown_mode_rejected_as_unsupported_mode(adapter_env: Path) -> None:
    """normal/deep_think 之外的 mode → unsupported_mode non_retryable（mode 门在
    浏览器启动之前）。"""
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_collection(
            _item(mode="expert"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []  # mode 门在浏览器启动之前


async def test_missing_profile_dir_is_adapter_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_YUANBAO_PROFILE_DIR", str(tmp_path / "no-such-dir"))
    monkeypatch.setenv("GEO_ADAPTER_EVIDENCE_DIR", str(tmp_path / "evidence"))
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_collection(
            _item(), session_factory=_factory(session), heartbeat=lambda p: None
        )
    assert exc_info.value.type == "adapter_not_configured"
    assert exc_info.value.non_retryable is True
    assert session.stages == []

    monkeypatch.delenv("GEO_YUANBAO_PROFILE_DIR")
    with pytest.raises(ApplicationError) as exc_info_unset:
        await run_yuanbao_collection(
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
    result = await run_yuanbao_collection(
        _item(), session_factory=_factory(session), heartbeat=lambda p: None
    )
    # 真调用 DLP：两个字段都必须干净（persist 层同语义）
    assert_secret_free(result.screenshot_ref)
    assert_secret_free(result.answer_text)


def test_default_evidence_dir_points_at_adapter_evidence() -> None:
    from workflows.activities.yuanbao_adapter import _DEFAULT_EVIDENCE_DIR

    assert _DEFAULT_EVIDENCE_DIR.name == "yuanbao"
    assert _DEFAULT_EVIDENCE_DIR.parent.name == "adapter-evidence"
    assert _DEFAULT_EVIDENCE_DIR.parent.parent.name == "runtime"


class _CombinedModeLocator:
    def __init__(self, page: _CombinedModePage, kind: str) -> None:
        self.page = page
        self.kind = kind

    @property
    def first(self) -> _CombinedModeLocator:
        return self

    def count(self) -> int:
        return int(self._visible())

    def is_visible(self, timeout: int = 0) -> bool:
        del timeout
        return self._visible()

    def _visible(self) -> bool:
        if not self.page.control_present:
            return False
        if self.kind == "trigger":
            return True
        if self.kind == "model_menu":
            return self.page.menu in {"main", "models"}
        if self.kind == "hy3":
            return self.page.menu == "models" and self.page.has_hy3
        if self.kind in {"normal", "deep_think"}:
            return self.page.menu == "main" and self.page.model == "hy3"
        return False

    def click(self) -> None:
        self.page.clicks.append(self.kind)
        if self.kind == "trigger":
            self.page.menu = "main" if self.page.menu == "closed" else "closed"
        elif self.kind == "model_menu":
            self.page.menu = "models"
        elif self.kind == "hy3":
            self.page.model = "hy3"
            self.page.menu = "main"
        elif self.kind in {"normal", "deep_think"}:
            self.page.mode = self.kind
            self.page.label = "快速回答" if self.kind == "normal" else "深度思考"
            self.page.menu = "closed"


class _CombinedModePage:
    def __init__(
        self,
        mode: str | None,
        label: str,
        *,
        model: str = "hy3",
        control_present: bool = True,
        has_hy3: bool = True,
    ) -> None:
        self.mode = mode
        self.label = label
        self.model = model
        self.control_present = control_present
        self.has_hy3 = has_hy3
        self.menu = "closed"
        self.clicks: list[str] = []

    def evaluate(self, script: str) -> dict[str, object]:
        if script == _COMBINED_MODE_STATE_JS:
            return {
                "found": self.control_present,
                "mode": self.mode,
                "label": self.label,
                "expanded": self.menu != "closed",
            }
        assert script == _COMBINED_MENU_STATE_JS
        if self.menu == "closed":
            return {"found": False}
        return {"found": True, "model": self.model, "label": f"模型 {self.model}"}

    def locator(self, selector: str) -> _CombinedModeLocator:
        if "选择模型" in selector:
            return _CombinedModeLocator(self, "model_menu")
        if "Hy3" in selector:
            return _CombinedModeLocator(self, "hy3")
        if "menuitemradio" in selector:
            kind = "normal" if "快速回答" in selector else "deep_think"
            return _CombinedModeLocator(self, kind)
        return _CombinedModeLocator(self, "trigger")

    def wait_for_timeout(self, timeout: int) -> None:
        del timeout


def test_combined_mode_normal_verifies_hy3_and_closes_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _CombinedModePage("normal", "快速回答")
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "normal") is True
    assert page.clicks == ["trigger", "trigger"]
    assert page.menu == "closed"


def test_combined_mode_switches_to_deep_think(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _CombinedModePage("normal", "快速回答")
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "deep_think") is True
    assert page.clicks == ["trigger", "deep_think"]
    assert page.mode == "deep_think"


def test_combined_mode_recovers_hy4_expert_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _CombinedModePage("expert", "专家模式", model="hy4")
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "normal") is True
    assert page.clicks == ["trigger", "model_menu", "hy3", "normal"]
    assert page.model == "hy3"
    assert page.mode == "normal"
    assert page.menu == "closed"


def test_combined_mode_unknown_label_can_be_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _CombinedModePage(None, "全新实验模式")
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "normal") is True
    assert page.clicks == ["trigger", "normal"]


def test_combined_mode_unknown_model_can_be_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _CombinedModePage(None, "全新实验模式", model="hy5")
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "normal") is True
    assert page.clicks == ["trigger", "model_menu", "hy3", "normal"]
    assert page.model == "hy3"


def test_combined_mode_missing_hy3_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _CombinedModePage("expert", "专家模式", model="hy4", has_hy3=False)
    monkeypatch.setattr(
        yuanbao_module,
        "human_click",
        lambda locator, _page, _rng: locator.click(),
    )
    assert _ensure_combined_mode(page, random.Random(1), "normal") is False
    assert page.clicks == ["trigger", "model_menu"]


def test_combined_mode_absent_allows_legacy_fallback() -> None:
    page = _CombinedModePage(None, "", control_present=False)
    assert _ensure_combined_mode(page, random.Random(1), "normal") is None
    assert page.clicks == []


# ---------------------------------------------------------------------------
# 结构化 trace 证据（20260810，kind="sse"/transport="dom"，词表对齐文心/DeepSeek）
# ---------------------------------------------------------------------------


def test_build_yuanbao_trace_shape() -> None:
    """trace 词表对齐文心/DeepSeek（router build_task_trace_view 消费同一词表）：
    思考链单块 reasoning + 最终引用独立保存，不能把引用反推为 U。"""
    refs = [
        {
            "url": "https://example.com/a",
            "title": "标题A",
            "sitename": "站点A",
            "summary": "摘要A",
        },
        {"url": "https://example.com/b", "title": None, "sitename": None, "summary": None},
    ]
    trace = _build_yuanbao_trace("想了一下", refs, deep_think_active=True)
    assert trace["engine"] == "yuanbao"
    assert trace["transport"] == "dom"
    assert trace["deep_think_active"] is True
    assert trace["thinking_chain"] == [{"kind": "reasoning", "text": "想了一下"}]
    assert trace["search_blocks"] == []
    references = trace["answer_reference_pages"]
    assert [r["url"] for r in references] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert references[0]["rank"] == 1
    assert references[0]["site"] == "站点A"
    assert references[1]["title"] == "未命名来源"
    assert references[1]["summary"] == ""
    empty = _build_yuanbao_trace("", [], deep_think_active=False)
    assert empty["thinking_chain"] == []
    assert empty["search_blocks"] == []
    # 单块思考文本截 5000 字符（对齐豆包水位）
    long = _build_yuanbao_trace("想" * 9_999, [], deep_think_active=True)
    assert len(long["thinking_chain"][0]["text"]) == 5_000


class _ThinkProbePage:
    """_extract_thinking_text 探针页替身：按构造参数返回文本或抛探针异常。"""

    def __init__(self, result: object = "", *, error: bool = False) -> None:
        self._result = result
        self._error = error

    def evaluate(self, script: str) -> object:
        if self._error:
            raise RuntimeError("probe exploded")
        return self._result


def test_extract_thinking_text_three_states() -> None:
    """三态：有块→原文；无块/空→空串；探针异常→空串（零合成，绝不编造）。"""
    assert _extract_thinking_text(_ThinkProbePage("先拆解。\n再作答。")) == "先拆解。\n再作答。"
    assert _extract_thinking_text(_ThinkProbePage("")) == ""
    assert _extract_thinking_text(_ThinkProbePage(None)) == ""
    assert _extract_thinking_text(_ThinkProbePage(error=True)) == ""


def test_task_result_maps_trace_evidence(tmp_path: Path) -> None:
    """trace_path → kind="sse" 证据（transport="dom" 思考链 + 引用折叠）。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    trace = tmp_path / "run-9-task-5-sse-trace.json"
    trace.write_text("{}", encoding="utf-8")
    collected = CollectedAnswer(
        answer_text="正文", references=[], screenshot_path=shot, trace_path=trace
    )
    result = _task_result_from_collected(_item(mode="deep_think"), collected)
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "sse"
    assert result.evidence[0].relation_type == "answer_sse_trace"
    assert result.evidence[0].mime_type == "application/json"
    assert result.evidence[0].path == str(trace)


def test_task_result_without_trace_has_no_evidence(tmp_path: Path) -> None:
    """无思考链且无引用（trace_path=None）→ 不出 sse 证据（诚实缺省）。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    collected = CollectedAnswer(answer_text="正文", references=[], screenshot_path=shot)
    result = _task_result_from_collected(_item(), collected)
    assert result.evidence == []


def test_batch_item_ok_passes_trace_evidence(tmp_path: Path) -> None:
    """batch ok 题：_batch_item_result 透传 trace evidence（复用 per-task 映射）。"""
    shot = tmp_path / "run-9-task-5.png"
    shot.write_bytes(b"\x89PNG-fake")
    trace = tmp_path / "run-9-task-5-sse-trace.json"
    trace.write_text("{}", encoding="utf-8")
    outcome = YuanbaoBatchItemOutcome(
        business_key="run-9-task-5",
        status="ok",
        answer=CollectedAnswer(
            answer_text="正文", references=[], screenshot_path=shot, trace_path=trace
        ),
    )
    result = _batch_item_result(_item(mode="deep_think"), outcome)
    assert result.status == "ok"
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "sse"
    assert result.evidence[0].relation_type == "answer_sse_trace"
    assert result.evidence[0].path == str(trace)


def test_body_text_js_serializes_tables_as_markdown() -> None:
    # 20260812 锚定（W3 表格碎片证据根治，yiyan 同款）：正文抽取必须把 <table>
    # 序列化为 markdown 管道行（存量答案曾混入 tab 压平表格行）；生产路径
    # .all()+reversed+evaluate 已在常驻浏览器 fixture 实证出整行语义。
    assert "querySelectorAll('table')" in _BODY_TEXT_JS
    assert "querySelectorAll('th,td')" in _BODY_TEXT_JS
    assert "'---'" in _BODY_TEXT_JS
    assert "replaceWith" in _BODY_TEXT_JS


def test_trim_response_preserves_noise_words_inside_answer_prose() -> None:
    text = "腾讯元宝可帮助用户解答问题、处理文件及进行联网搜索等。"

    assert yuanbao_module._trim_response(text) == text


def test_trim_response_removes_only_standalone_trailing_ui_line() -> None:
    text = "正文提到深度思考和联网搜索能力。\n联网搜索\n继续追问"

    assert yuanbao_module._trim_response(text) == "正文提到深度思考和联网搜索能力。"
