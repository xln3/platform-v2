from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

Consumer = Callable[[Mapping[str, Any]], None]


class OutboxConsumer:
    def __init__(self, *, dsn: str, consumer_name: str, publish: Consumer) -> None:
        # SQLAlchemy's explicit driver suffix is not valid in psycopg's URI parser.
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self.consumer_name = consumer_name
        self.publish = publish

    def drain(self, *, limit: int = 100) -> int:
        processed = 0
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.transaction():
                events = connection.execute(
                    """
                    SELECT * FROM integration.outbox_event
                    WHERE published_at IS NULL
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
                for event in events:
                    payload_hash = sha256(
                        json.dumps(event["payload"], sort_keys=True, separators=(",", ":")).encode()
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
        return processed
