"""元宝采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / 未知 mode 拒绝
（normal/deep_think 均放行）/ profile 未配置 / 截图与正文过 DLP / 代理口令打码。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import CollectionTaskInput
from workflows.activities.yuanbao_adapter import (
    CollectedAnswer,
    YuanbaoAdapterConfig,
    YuanbaoBatchItemOutcome,
    _batch_item_result,
    _build_yuanbao_trace,
    _extract_thinking_text,
    _task_result_from_collected,
    _WallError,
    mask_proxy_url,
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
    assert "腾讯元宝" in result.answer_text
    assert "参考来源：" in result.answer_text
    assert "https://example.com/about/1" in result.answer_text
    assert result.screenshot_ref == f"file://{shot}"
    assert result.screenshot_ref.startswith("file://")
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


def test_proxy_url_masking_never_leaks_credentials() -> None:
    assert mask_proxy_url("http://user:pass@proxy.example.com:8080") == (
        "http://proxy.example.com:8080"
    )
    assert mask_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert mask_proxy_url(None) is None
    assert mask_proxy_url("not-a-url") == "<invalid-proxy-url>"


def test_default_evidence_dir_points_at_adapter_evidence() -> None:
    from workflows.activities.yuanbao_adapter import _DEFAULT_EVIDENCE_DIR

    assert _DEFAULT_EVIDENCE_DIR.name == "yuanbao"
    assert _DEFAULT_EVIDENCE_DIR.parent.name == "adapter-evidence"
    assert _DEFAULT_EVIDENCE_DIR.parent.parent.name == "runtime"


# ---------------------------------------------------------------------------
# 结构化 trace 证据（20260810，kind="sse"/transport="dom"，词表对齐文心/DeepSeek）
# ---------------------------------------------------------------------------


def test_build_yuanbao_trace_shape() -> None:
    """trace 词表对齐文心/DeepSeek（router build_task_trace_view 消费同一词表）：
    思考链单块 reasoning + references 折叠 search_blocks（DeepSeek 形态）。"""
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
    block = trace["search_blocks"][0]
    assert block["scene"] is None
    assert block["queries"] == []  # 检索词平台未暴露，诚实留空
    assert block["summary"] == ""
    assert [r["url"] for r in block["results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert block["results"][0]["rank"] == 1
    assert block["results"][0]["site"] == "站点A"
    assert block["results"][1]["title"] == "未命名来源"
    assert block["results"][1]["summary"] == ""
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
