"""Formal report production progress endpoint: workflow query + honest db fallback."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app
from geo_platform.reports import formal_production_router as production_router
from geo_platform.reports.formal_production import FormalProductionNotFound

_CREATED = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def _production_row(
    status: str = "running",
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "pub_id": "frp_unit",
        "project_pub_id": "prj_unit",
        "services": [1, 2],
        "service_catalog_version": "legacy_report_services_v1",
        "sop_project_pub_id": None,
        "status": status,
        "document_status": "internal_review",
        "window_start": date(2026, 7, 1),
        "window_end": date(2026, 7, 31),
        "before_window": None,
        "after_window": None,
        "candidate_group_strategy": "preregistered_scope_v1",
        "document_governance": {},
        "workflow_id": "formal-report/ten_unit/frp_unit",
        "fact_snapshot_hash": None,
        "outputs": [],
        "error_code": error_code,
        "created_at": _CREATED,
        "updated_at": _UPDATED,
    }


class _FakeHandle:
    def __init__(self, state: object) -> None:
        self._state = state

    async def query(self, name: str) -> object:
        assert name == "state"
        if isinstance(self._state, Exception):
            raise self._state
        return self._state


class _FakeClient:
    def __init__(self, state: object) -> None:
        self._state = state

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        assert workflow_id == "formal-report/ten_unit/frp_unit"
        return _FakeHandle(self._state)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    row: dict[str, Any] | None,
    workflow_state: object,
) -> None:
    class FakeService:
        def get(self, **kwargs: object) -> dict[str, Any]:
            assert kwargs["tenant_pub_id"] == "ten_unit"
            assert kwargs["production_pub_id"] == "frp_unit"
            if row is None:
                raise FormalProductionNotFound("formal_production_not_found")
            return row

    async def fake_connect(*args: object, **kwargs: object) -> _FakeClient:
        del args, kwargs
        # state 为 Exception 时由 handle.query 抛出，覆盖"workflow 已关闭/查询失败"两类。
        return _FakeClient(workflow_state)

    monkeypatch.setattr(production_router, "_service", lambda: FakeService())
    monkeypatch.setattr(production_router, "connect_temporal", fake_connect)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="operator@example.test",
        role=Role.OPERATOR,
        tenant_pub_id="ten_unit",
        user_pub_id="usr_operator",
    )


def _progress() -> dict[str, Any]:
    try:
        response = TestClient(app).get("/api/v2/reports/formal-productions/frp_unit/progress")
    finally:
        app.dependency_overrides.pop(get_principal, None)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def _stage_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {stage["stage"]: stage for stage in payload["stages"]}


# ── 纯函数：阶段数组映射 ──


def test_progress_stages_marks_current_with_honest_timestamps() -> None:
    stages = production_router._progress_stages(
        current_stage="running",
        failed_stage=None,
        created_at=_CREATED,
        updated_at=_UPDATED,
    )
    assert [stage.status for stage in stages] == [
        "done",
        "done",
        "done",
        "current",
        "pending",
        "pending",
        "pending",
    ]
    by_stage = {stage.stage: stage for stage in stages}
    assert by_stage["queued"].entered_at == _CREATED
    assert by_stage["running"].entered_at == _UPDATED
    assert by_stage["preflight"].entered_at is None
    assert by_stage["awaiting_review"].entered_at is None


def test_progress_stages_signed_is_terminal_done() -> None:
    stages = production_router._progress_stages(
        current_stage="signed",
        failed_stage=None,
        created_at=_CREATED,
        updated_at=_UPDATED,
    )
    assert all(stage.status == "done" for stage in stages)
    assert stages[-1].entered_at == _UPDATED


def test_progress_stages_failed_pivot() -> None:
    stages = production_router._progress_stages(
        current_stage=None,
        failed_stage="running",
        created_at=_CREATED,
        updated_at=_UPDATED,
    )
    assert [stage.status for stage in stages] == [
        "done",
        "done",
        "done",
        "failed",
        "pending",
        "pending",
        "pending",
    ]


def test_progress_stages_unknown_failure_keeps_honest_pending() -> None:
    stages = production_router._progress_stages(
        current_stage=None,
        failed_stage=None,
        created_at=_CREATED,
        updated_at=_UPDATED,
    )
    assert [stage.status for stage in stages] == ["pending"] * 7
    assert stages[0].entered_at == _CREATED
    assert all(stage.entered_at is None for stage in stages[1:])


# ── 端点：workflow 查询成功 ──


def test_progress_endpoint_uses_workflow_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, row=_production_row(), workflow_state="preflight")
    payload = _progress()
    assert payload["source"] == "workflow"
    assert payload["failed"] is False
    assert payload["error_code"] is None
    stages = _stage_map(payload)
    assert stages["queued"]["status"] == "done"
    assert stages["binding_snapshot"]["status"] == "done"
    assert stages["preflight"]["status"] == "current"
    assert stages["running"]["status"] == "pending"
    assert stages["signed"]["status"] == "pending"
    assert stages["queued"]["entered_at"] == "2026-08-12T08:00:00Z"
    assert stages["preflight"]["entered_at"] == "2026-08-12T09:30:00Z"
    assert stages["running"]["entered_at"] is None


def test_progress_endpoint_awaiting_review_and_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="awaiting_review"),
        workflow_state="awaiting_review",
    )
    payload = _progress()
    stages = _stage_map(payload)
    assert stages["awaiting_review"]["status"] == "current"
    assert stages["finalizing"]["status"] == "pending"

    _install(monkeypatch, row=_production_row(status="signed"), workflow_state="signed")
    payload = _progress()
    assert payload["failed"] is False
    assert all(stage["status"] == "done" for stage in payload["stages"])


def test_progress_endpoint_workflow_failed_maps_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="failed", error_code="production_failed"),
        workflow_state="failed",
    )
    payload = _progress()
    assert payload["source"] == "workflow"
    assert payload["failed"] is True
    assert payload["error_code"] == "production_failed"
    stages = _stage_map(payload)
    assert stages["running"]["status"] == "failed"
    assert stages["preflight"]["status"] == "done"
    assert stages["awaiting_review"]["status"] == "pending"


def test_progress_endpoint_failed_with_unknown_error_code_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="failed", error_code="unexpected_code"),
        workflow_state="failed",
    )
    payload = _progress()
    assert payload["failed"] is True
    assert payload["error_code"] == "unexpected_code"
    # 映射不出失败阶段：不造 current/failed，全 pending（queued 仍带 created_at）。
    assert all(stage["status"] == "pending" for stage in payload["stages"])


# ── 端点：Temporal 不可达/已关闭 → 库内降级 ──


def test_progress_endpoint_falls_back_to_db_when_temporal_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, row=_production_row(), workflow_state=RuntimeError("temporal down"))
    payload = _progress()
    assert payload["source"] == "db_fallback"
    assert payload["failed"] is False
    stages = _stage_map(payload)
    assert stages["running"]["status"] == "current"
    assert stages["preflight"]["status"] == "done"


def test_progress_endpoint_falls_back_when_workflow_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="awaiting_review"),
        workflow_state=RuntimeError("workflow not found"),
    )
    payload = _progress()
    assert payload["source"] == "db_fallback"
    assert _stage_map(payload)["awaiting_review"]["status"] == "current"


def test_progress_endpoint_db_fallback_failed_uses_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="failed", error_code="changes_requested"),
        workflow_state=RuntimeError("workflow not found"),
    )
    payload = _progress()
    assert payload["source"] == "db_fallback"
    assert payload["failed"] is True
    assert payload["error_code"] == "changes_requested"
    stages = _stage_map(payload)
    assert stages["finalizing"]["status"] == "failed"
    assert stages["awaiting_review"]["status"] == "done"
    assert stages["signed"]["status"] == "pending"


def test_progress_endpoint_db_fallback_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="signed"),
        workflow_state=RuntimeError("workflow not found"),
    )
    payload = _progress()
    assert payload["source"] == "db_fallback"
    assert payload["failed"] is False
    assert all(stage["status"] == "done" for stage in payload["stages"])


def test_progress_endpoint_db_fallback_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        row=_production_row(status="queued"),
        workflow_state=RuntimeError("temporal down"),
    )
    payload = _progress()
    stages = _stage_map(payload)
    assert stages["queued"]["status"] == "current"
    assert stages["binding_snapshot"]["status"] == "pending"


def test_progress_endpoint_unknown_workflow_state_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, row=_production_row(), workflow_state={"unexpected": "shape"})
    payload = _progress()
    assert payload["source"] == "db_fallback"
    assert _stage_map(payload)["running"]["status"] == "current"


def test_progress_endpoint_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, row=None, workflow_state="running")
    try:
        response = TestClient(app).get("/api/v2/reports/formal-productions/frp_unit/progress")
    finally:
        app.dependency_overrides.pop(get_principal, None)
    assert response.status_code == 404
    assert "formal_production_not_found" in str(response.json())
