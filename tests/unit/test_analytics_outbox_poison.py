"""analytics outbox 毒消息兜底单测（fake psycopg 连接，模拟 savepoint 语义，不打真 DB）。

钉住 2026-08-08 修复的队头阻塞缺陷：
- 单事件投影失败只回滚该事件（savepoint），失败记账（attempts+1/last_error）
  随外层事务持久化；健康事件按序继续投影；
- 失败事件下一轮可被重试（receipt 随 savepoint 回滚，不毒化幂等键）；
- attempts 达到 OUTBOX_MAX_ATTEMPTS 的隔离事件不再被选中（等人工）。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from geo_platform.analytics.outbox import OUTBOX_MAX_ATTEMPTS, OutboxConsumer


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeTransaction:
    """两级事务语义：外层直接改状态；内层（savepoint）先快照、异常即恢复并传播。"""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeTransaction:
        if self._connection.in_transaction:
            self._snapshot = (
                copy.deepcopy(self._connection.rows),
                set(self._connection.receipts),
            )
        else:
            self._snapshot = None
        self._connection.in_transaction = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None and self._snapshot is not None:
            # 原地替换（保持 list 对象身份），否则外层 failure 记账与测试断言
            # 看到的是被快照换掉之前的旧行。
            self._connection.rows[:] = self._snapshot[0]
            self._connection.receipts.clear()
            self._connection.receipts.update(self._snapshot[1])
        self._connection.in_transaction = False
        return False  # 异常总是传播（与 psycopg transaction 一致）


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.receipts: set[tuple[str, str]] = set()
        self.in_transaction = False
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        params = params or ()
        self.statements.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM integration.outbox_event"):
            max_attempts, event_types, _event_types_2, limit = params
            selected = [
                row
                for row in sorted(self.rows, key=lambda item: item["id"])
                if row["published_at"] is None
                and row["attempts"] < max_attempts
                and (event_types is None or row["event_type"] in event_types)
            ]
            return _Result(selected[:limit])
        if "INSERT INTO integration.consumer_receipt" in sql:
            consumer_name, event_id, _payload_hash = params
            if (consumer_name, event_id) in self.receipts:
                return _Result([])
            self.receipts.add((consumer_name, event_id))
            return _Result([{"event_id": event_id}])
        if "SET published_at=now()" in sql:
            row = self._row(params[0])
            row["published_at"] = "2026-08-08T00:00:00+00:00"
            row["attempts"] += 1
            row["last_error"] = None
            return _Result([])
        if "SET attempts=attempts+1, last_error=%s" in sql:
            row = self._row(params[1])
            row["attempts"] += 1
            row["last_error"] = params[0]
            return _Result([])
        raise AssertionError(f"unexpected SQL: {normalized}")

    def _row(self, event_id: str) -> dict[str, Any]:
        return next(row for row in self.rows if row["event_id"] == event_id)


def _event(
    event_id: str, *, attempts: int = 0, event_type: str = "analytics.answer.analyzed"
) -> dict[str, Any]:
    return {
        "id": len(event_id),
        "event_id": event_id,
        "event_type": event_type,
        "payload": {"safe": True},
        "published_at": None,
        "attempts": attempts,
        "last_error": None,
    }


def _consumer(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    publish: Any,
) -> tuple[OutboxConsumer, _FakeConnection]:
    connection = _FakeConnection(rows)
    monkeypatch.setattr(
        "geo_platform.analytics.outbox.psycopg.connect",
        lambda dsn, **kwargs: connection,
    )
    return (
        OutboxConsumer(
            dsn="postgresql://fake",
            consumer_name="test-consumer",
            publish=publish,
        ),
        connection,
    )


def test_poison_event_is_recorded_and_healthy_events_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 毒事件 id 最小（队头）——旧实现会让它永久堵住后两条健康事件。
    rows = [_event("a_poison"), _event("bb_healthy"), _event("ccc_healthy2")]
    published: list[str] = []

    def publish(event: Any) -> None:
        if event["event_id"] == "a_poison":
            raise RuntimeError("synthetic projection failure")
        published.append(event["event_id"])

    consumer, connection = _consumer(monkeypatch, rows, publish)
    assert consumer.drain() == 2
    assert published == ["bb_healthy", "ccc_healthy2"]
    poison = rows[0]
    assert poison["published_at"] is None
    assert poison["attempts"] == 1
    assert poison["last_error"] is not None
    assert poison["last_error"].startswith("RuntimeError:")
    # 记账 UPDATE 发生在 savepoint 之外（随外层事务提交）。
    failure_updates = [
        params
        for sql, params in connection.statements
        if "SET attempts=attempts+1, last_error=%s" in sql
    ]
    assert failure_updates == [(poison["last_error"], "a_poison")]


def test_failed_event_is_retried_next_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_event("a_flaky")]
    calls: list[str] = []

    def publish(event: Any) -> None:
        calls.append(event["event_id"])
        if len(calls) == 1:
            raise ValueError("transient")

    consumer, _connection = _consumer(monkeypatch, rows, publish)
    assert consumer.drain() == 0  # 首轮失败：receipt 已随 savepoint 回滚
    assert consumer.drain() == 1  # 次轮重试成功
    assert calls == ["a_flaky", "a_flaky"]
    assert rows[0]["published_at"] is not None
    assert rows[0]["last_error"] is None


def test_quarantined_events_are_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_event("a_poison", attempts=OUTBOX_MAX_ATTEMPTS)]
    consumer, connection = _consumer(monkeypatch, rows, lambda event: None)
    assert consumer.drain() == 0
    selects = [
        (sql, params)
        for sql, params in connection.statements
        if sql.lstrip().startswith("SELECT * FROM integration.outbox_event")
    ]
    assert len(selects) == 1
    sql, params = selects[0]
    assert "attempts < %s" in sql
    assert params[0] == OUTBOX_MAX_ATTEMPTS
    assert rows[0]["published_at"] is None  # 隔离事件原样等人工


def test_duplicate_receipt_skips_publish_but_marks_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_event("a_seen")]
    published: list[str] = []
    consumer, connection = _consumer(monkeypatch, rows, lambda event: published.append("called"))
    connection.receipts.add(("test-consumer", "a_seen"))
    assert consumer.drain() == 0
    assert published == []
    assert rows[0]["published_at"] is not None
