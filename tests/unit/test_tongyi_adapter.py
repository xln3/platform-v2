"""通义千问采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / deep_think 拒绝 /
profile 未配置 / 发送墙证据 / screenshot_ref+answer 过 DLP / 代理口令打码。
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
    CollectedAnswer,
    TongyiAdapterConfig,
    _build_tongyi_trace,
    _composer_value_empty,
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

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        on_stage("fake_stage")
        self.stages.append("fake_stage")
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
    assert "通义千问" in result.answer_text
    assert "参考来源：" in result.answer_text
    assert "https://example.com/article/1" in result.answer_text
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


async def test_deep_think_rejected_as_unsupported_mode(adapter_env: Path) -> None:
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_tongyi_collection(
            _item(mode="deep_think"),
            session_factory=_factory(session),
            heartbeat=lambda p: None,
        )
    assert exc_info.value.type == "unsupported_mode"
    assert exc_info.value.non_retryable is True
    assert session.stages == []  # mode 门在浏览器启动之前


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
# 结构化 trace 证据（20260810，kind="sse"/transport="dom"；思考链/检索词平台
# 未暴露，诚实留空——引用卡片折叠为唯一内容）
# ---------------------------------------------------------------------------


def test_build_tongyi_trace_shape() -> None:
    """refs → search_blocks 折叠（DeepSeek 形态）；thinking_chain/queries 诚实留空。"""
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
