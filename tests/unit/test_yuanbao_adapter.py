"""元宝采集适配器 v1 单元测试：浏览器层全部 mock（依赖注入 fake session），
绝不启动真浏览器。覆盖：成功字段映射 / 登录墙 non_retryable / deep_think 拒绝 /
profile 未配置 / 截图与正文过 DLP / 代理口令打码。
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

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
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


async def test_deep_think_rejected_as_unsupported_mode(adapter_env: Path) -> None:
    session = _FakeSession(result=None)
    with pytest.raises(ApplicationError) as exc_info:
        await run_yuanbao_collection(
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
