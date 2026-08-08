from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Any

import psycopg
import structlog
from psycopg.rows import dict_row

Consumer = Callable[[Mapping[str, Any]], None]
ANALYTICS_EVENT_TYPES = (
    "analytics.answer.analyzed",
    "collection.run.completed",
    "disparagement.recorded",
    "intelligence.feature.recorded",
    "source_audit.recorded",
)

# 毒消息隔离阈值：同一事件投影连续失败达到本次数后不再被选中（等人工介入：
# 修复后 SQL 置 attempts=0 即重新入队）。低于阈值的重试保留既有 at-least-once
# 语义；健康事件按 id 序继续推进，不再被队头毒事件堵死（2026-08-08 修复）。
OUTBOX_MAX_ATTEMPTS = 8

log = structlog.get_logger()


def _error_marker(error: BaseException) -> str:
    """失败标记：异常类名+约束/表名（模式标识符），绝不落异常 message（可能含值）。"""
    diag = getattr(error, "diag", None)
    detail = (
        getattr(diag, "constraint_name", None) or getattr(diag, "table_name", None) or "-"
    )
    return f"{type(error).__name__}:{detail}"[:200]


class OutboxConsumer:
    def __init__(
        self,
        *,
        dsn: str,
        consumer_name: str,
        publish: Consumer,
        event_types: tuple[str, ...] | None = ANALYTICS_EVENT_TYPES,
    ) -> None:
        # SQLAlchemy's explicit driver suffix is not valid in psycopg's URI parser.
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self.consumer_name = consumer_name
        self.publish = publish
        self.event_types = event_types

    def drain(self, *, limit: int = 100) -> int:
        processed = 0
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.transaction():
                events = connection.execute(
                    """
                    SELECT * FROM integration.outbox_event
                    WHERE published_at IS NULL
                      AND attempts < %s
                      AND (%s::text[] IS NULL OR event_type=ANY(%s::text[]))
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    (
                        OUTBOX_MAX_ATTEMPTS,
                        *(list(self.event_types) if self.event_types else None,) * 2,
                        limit,
                    ),
                ).fetchall()
                for event in events:
                    try:
                        # 每事件独立 savepoint：投影失败只回滚本事件的痕迹
                        # （receipt/副作用），失败记账写进外层事务随批次提交——
                        # 毒事件从此不再回滚整批、不再堵队头。
                        with connection.transaction():
                            payload_hash = sha256(
                                json.dumps(
                                    event["payload"], sort_keys=True, separators=(",", ":")
                                ).encode()
                            ).hexdigest()
                            receipt = connection.execute(
                                """
                                INSERT INTO integration.consumer_receipt
                                  (consumer_name,event_id,payload_hash)
                                VALUES (%s,%s,%s)
                                ON CONFLICT DO NOTHING
                                RETURNING event_id
                                """,
                                (self.consumer_name, event["event_id"], payload_hash),
                            ).fetchone()
                            if receipt is not None:
                                self.publish(event)
                                processed += 1
                            connection.execute(
                                """
                                UPDATE integration.outbox_event
                                SET published_at=now(), attempts=attempts+1, last_error=NULL
                                WHERE event_id=%s
                                """,
                                (event["event_id"],),
                            )
                    except Exception as error:
                        connection.execute(
                            """
                            UPDATE integration.outbox_event
                            SET attempts=attempts+1, last_error=%s
                            WHERE event_id=%s
                            """,
                            (_error_marker(error), event["event_id"]),
                        )
                        log.warning(
                            "outbox_event_publish_failed",
                            event_id=event["event_id"],
                            event_type=event["event_type"],
                            error_type=type(error).__name__,
                            pg_constraint=getattr(
                                getattr(error, "diag", None), "constraint_name", None
                            ),
                        )
        return processed
