"""workflow_outbox 的 post_analysis 分支单测（fake psycopg 连接，不打真 DB/Temporal）。

覆盖 reconciled_terminal 的 item 中间态清扫纪律：
- 非自然终态（FAILED/CANCELED/…）→ 卡 fetching/analyzing/annotating 的 item
  按阶段落既有失败词表 + error='workflow_interrupted'；
- COMPLETED → 不扫（finalize 是最后写入者）；
- task 行终态回写两种路径都在。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from geo_platform.collection.workflow_outbox import (
    WorkflowStartCommand,
    WorkflowStartOutbox,
)
from temporalio.client import Client


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    """记录全部 SQL；tenant 查询回一行（tuple 口径，生产代码用 tenant[0]）。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        self.queries.append((sql, params or ()))
        if "FROM platform.tenant" in sql:
            return _Result([("00000000-0000-0000-0000-000000000001",)])
        return _Result([])


def _command() -> WorkflowStartCommand:
    return WorkflowStartCommand(
        command_id="00000000-0000-0000-0000-0000000000aa",
        tenant_pub_id="tnt_0123456789abcdef",
        workflow_type="post_analysis",
        workflow_id="post-analysis/tnt_0123456789abcdef/pat_" + "a" * 26,
        task_queue="geo-platform-v2",
        payload={},
        trace_context={},
    )


def _outbox(monkeypatch: pytest.MonkeyPatch) -> tuple[WorkflowStartOutbox, _FakeConnection]:
    connection = _FakeConnection()
    monkeypatch.setattr(
        "geo_platform.collection.workflow_outbox.psycopg.connect",
        lambda dsn, **kwargs: connection,
    )
    outbox = WorkflowStartOutbox(dsn="postgresql://fake", temporal=cast(Client, None))
    return outbox, connection


def _item_sweeps(connection: _FakeConnection) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        (sql, params)
        for sql, params in connection.queries
        if "UPDATE platform.post_analysis_item" in sql
    ]


def _task_updates(connection: _FakeConnection) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        (sql, params)
        for sql, params in connection.queries
        if "UPDATE platform.post_analysis_task" in sql
    ]


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"])
def test_reconciled_terminal_sweeps_transient_items_on_failure(
    monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    outbox, connection = _outbox(monkeypatch)
    outbox.reconciled_terminal(_command(), terminal)
    sweeps = _item_sweeps(connection)
    assert len(sweeps) == 1
    sql, params = sweeps[0]
    assert "workflow_interrupted" in sql
    assert "fetch_failed" in sql and "analysis_failed" in sql
    # 只扫中间态，终态/pending 不动；按 workflow_id 定位 task
    assert "('fetching','analyzing','annotating')" in sql
    assert params == ("post-analysis/tnt_0123456789abcdef/pat_" + "a" * 26,)
    # task 行终态回写也在
    assert len(_task_updates(connection)) == 1


def test_reconciled_terminal_completed_never_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox, connection = _outbox(monkeypatch)
    outbox.reconciled_terminal(_command(), "COMPLETED")
    assert _item_sweeps(connection) == []  # finalize 的写入不被干扰
    assert len(_task_updates(connection)) == 1  # task 终态回写照常（守卫非终态）


def test_reconciled_terminal_other_workflow_types_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox, connection = _outbox(monkeypatch)
    command = _command()
    command = WorkflowStartCommand(
        command_id=command.command_id,
        tenant_pub_id=command.tenant_pub_id,
        workflow_type="answer_analysis",
        workflow_id=command.workflow_id,
        task_queue=command.task_queue,
        payload={},
        trace_context={},
    )
    outbox.reconciled_terminal(command, "FAILED")
    assert _item_sweeps(connection) == []
    assert _task_updates(connection) == []


def test_reconciled_terminal_collection_run_excludes_full_terminal_vocab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归：s04_0019 触发器终态词表五词（completed/completed_with_failures/
    failed/cancelled/skipped）不可逆；对账回写的排除列表必须与之对齐，
    否则对已完成 run 的回写撞 ck_collection_run_terminal_state（23514），
    对账通道永久空转刷错（2026-08-07 生产实证）。"""
    outbox, connection = _outbox(monkeypatch)
    command = WorkflowStartCommand(
        command_id="00000000-0000-0000-0000-0000000000bb",
        tenant_pub_id="tnt_0123456789abcdef",
        workflow_type="geo_collection",
        workflow_id="geo-collection/tnt_x/prj_y/run_z",
        task_queue="geo-platform-v2-production",
        payload={},
        trace_context={},
    )
    outbox.reconciled_terminal(command, "COMPLETED")
    run_updates = [sql for sql, _ in connection.queries if "UPDATE platform.collection_run" in sql]
    assert len(run_updates) == 1
    for terminal_state in (
        "completed",
        "completed_with_failures",
        "cancelled",
        "failed",
        "skipped",
    ):
        assert f"'{terminal_state}'" in run_updates[0]
