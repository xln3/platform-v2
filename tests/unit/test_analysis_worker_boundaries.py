"""Regression guard for physical collection/source/analysis queue isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
        "fetch_post_snapshot",
        "fetch_run_sources",
    }
    assert "GeoCollectionWorkflow" in _workflow_names(COLLECTION_WORKFLOWS)
    assert "PostCollectionAnalysisWorkflow" not in _workflow_names(COLLECTION_WORKFLOWS)
    assert "PostCollectionAnalysisWorkflow" in _workflow_names(ANALYSIS_WORKFLOWS)
    assert "PageInspectionWorkflow" in _workflow_names(ANALYSIS_WORKFLOWS)
    assert "inspect_run_source_pages" in analysis
    assert "inspect_run_source_pages" not in source
