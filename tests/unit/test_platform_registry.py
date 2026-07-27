"""platform_registry dispatcher 单测：路由 / fail-closed 分类，不启动真浏览器。"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.collection import CollectionTaskInput, CollectionTaskResult
from workflows.activities.platform_registry import dispatch_collection


def _item(adapter: str) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key="run-9-task-1",
        query="测试查询",
        model="doubao",
        region="Shanghai",
        mode="normal",
        adapter=adapter,
    )


async def test_routes_to_doubao_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """doubao slug lazy import 对应模块并调用 run_doubao_collection（mock 浏览器层）。"""
    calls: list[dict[str, Any]] = []
    fake = types.ModuleType("workflows.activities.doubao_adapter")

    async def run_doubao_collection(
        item: CollectionTaskInput, *, heartbeat: Any = None, **kwargs: Any
    ) -> CollectionTaskResult:
        if heartbeat is not None:
            heartbeat({"business_key": item.business_key, "stage": "fake"})
        return CollectionTaskResult(
            business_key=item.business_key,
            answer_text="fake-real-answer",
            screenshot_ref="file:///tmp/fake.png",
            quality_state="live_valid",
        )

    fake.run_doubao_collection = run_doubao_collection  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "workflows.activities.doubao_adapter", fake)

    beats: list[dict[str, Any]] = []
    result = await dispatch_collection(_item("doubao"), heartbeat=lambda p: beats.append(p))
    assert result.business_key == "run-9-task-1"
    assert result.answer_text == "fake-real-answer"
    assert result.quality_state == "live_valid"
    assert beats == [{"business_key": "run-9-task-1", "stage": "fake"}]
    assert calls == []  # 确认无额外全局状态


@pytest.mark.parametrize("slug", ["fixed", "", "unknown", "DOUBAOX"])
async def test_unknown_slug_is_unsupported_adapter(slug: str) -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await dispatch_collection(_item(slug), heartbeat=lambda p: None)
    assert exc_info.value.type == "unsupported_adapter"
    assert exc_info.value.non_retryable is True


async def test_missing_module_is_unsupported_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法 slug 但模块不存在（sys.modules 置 None 强制 ImportError）→ 同样 fail-closed。"""
    monkeypatch.setitem(sys.modules, "workflows.activities.deepseek_adapter", None)
    with pytest.raises(ApplicationError) as exc_info:
        await dispatch_collection(_item("deepseek"), heartbeat=lambda p: None)
    assert exc_info.value.type == "unsupported_adapter"
    assert exc_info.value.non_retryable is True


async def test_module_without_runner_is_unsupported_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("workflows.activities.tongyi_adapter")
    monkeypatch.setitem(sys.modules, "workflows.activities.tongyi_adapter", fake)
    with pytest.raises(ApplicationError) as exc_info:
        await dispatch_collection(_item("tongyi"), heartbeat=lambda p: None)
    assert exc_info.value.type == "unsupported_adapter"
    assert exc_info.value.non_retryable is True
