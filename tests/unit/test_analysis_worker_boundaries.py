"""Regression guard for physical collection/source/analysis queue isolation."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from types import SimpleNamespace
from typing import Any

import pytest

from workflows.workers import analysis as analysis_worker
from workflows.workers.analysis import ANALYSIS_ACTIVITIES, ANALYSIS_WORKFLOWS
from workflows.workers.main import COLLECTION_ACTIVITIES, COLLECTION_WORKFLOWS
from workflows.workers.source import SOURCE_ACTIVITIES


def _activity_names(items: tuple[Callable[..., Any], ...]) -> set[str]:
    return {item.__temporal_activity_definition.name for item in items}


def _workflow_names(items: tuple[type, ...]) -> set[str]:
    return {item.__temporal_workflow_definition.name for item in items}


def test_authenticated_collection_worker_has_no_analysis_or_public_fetch_activity() -> None:
    collection = _activity_names(COLLECTION_ACTIVITIES)
    source = _activity_names(SOURCE_ACTIVITIES)
    analysis = _activity_names(ANALYSIS_ACTIVITIES)

    assert collection.isdisjoint(source)
    assert collection.isdisjoint(analysis)
    assert source == {
        "capture_own_site_snapshots",
        "enrich_service2_evidence_page",
        "fetch_post_snapshot",
        "fetch_run_sources",
    }
    assert "GeoCollectionWorkflow" in _workflow_names(COLLECTION_WORKFLOWS)
    assert "PostCollectionAnalysisWorkflow" not in _workflow_names(COLLECTION_WORKFLOWS)
    assert "PostCollectionAnalysisWorkflow" in _workflow_names(ANALYSIS_WORKFLOWS)
    assert "PageInspectionWorkflow" in _workflow_names(ANALYSIS_WORKFLOWS)
    assert "inspect_run_source_pages" in analysis
    assert "inspect_run_source_pages" not in source


@pytest.mark.asyncio
async def test_analysis_worker_registers_executor_for_sync_activities(
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

    monkeypatch.setattr(analysis_worker, "Client", FakeClient)
    monkeypatch.setattr(analysis_worker, "Worker", FakeWorker)
    monkeypatch.setattr(analysis_worker, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(analysis_worker, "configure_tracing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        analysis_worker,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            temporal_address="temporal:7233",
            temporal_namespace="default",
            analysis_temporal_task_queue="analysis-test",
        ),
    )

    await analysis_worker.run_worker()

    assert isinstance(registered["activity_executor"], Executor)
    assert registered["max_concurrent_activities"] == 8
    assert analysis_worker.fail_service2_corpus_batch in registered["activities"]
