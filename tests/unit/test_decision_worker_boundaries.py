"""Regression guards for the blocking SemanticDecision judge boundary."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from types import SimpleNamespace
from typing import Any

import pytest

from workflows.activities.semantic_decisions_v2 import run_model_judge_activity
from workflows.workers import decision as decision_worker


def _activity_names(items: tuple[Callable[..., Any], ...]) -> set[str]:
    return {item.__temporal_activity_definition.name for item in items}


def test_real_model_judge_is_a_sync_activity_owned_by_decision_worker() -> None:
    assert not run_model_judge_activity.__temporal_activity_definition.is_async
    assert "run_model_judge_v2" in _activity_names(decision_worker.DECISION_ACTIVITIES)


@pytest.mark.asyncio
async def test_decision_worker_registers_executor_for_blocking_model_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: dict[str, Any] = {}

    class FakeClient:
        @staticmethod
        async def connect(*_args: Any, **_kwargs: Any) -> object:
            return object()

    class FakeWorker:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            registered.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(decision_worker, "Client", FakeClient)
    monkeypatch.setattr(decision_worker, "Worker", FakeWorker)
    monkeypatch.setattr(decision_worker, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(decision_worker, "configure_tracing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        decision_worker,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            temporal_address="temporal:7233",
            temporal_namespace="default",
            decision_temporal_task_queue="decision-test",
            semantic_decision_max_concurrent_activities=3,
            semantic_decision_judge_policy_version="semantic-v2-primary-model@2.1.0",
        ),
    )

    await decision_worker.run_worker()

    assert isinstance(registered["activity_executor"], Executor)
    assert registered["max_concurrent_activities"] == 3
    assert run_model_judge_activity in registered["activities"]
