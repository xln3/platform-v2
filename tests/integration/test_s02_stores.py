import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.evidence.object_store import ContentAddressedObjectStore

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


@pytest.fixture
def object_store() -> ContentAddressedObjectStore:
    store = ContentAddressedObjectStore(
        endpoint="http://127.0.0.1:19000",
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    return store


def test_minio_content_addressing_dlp_hash_and_tamper_detection(
    object_store: ContentAddressedObjectStore,
) -> None:
    first = object_store.put_redacted(
        b'{"Cookie":"sid=secret","body":"same"}', mime_type="application/json"
    )
    second = object_store.put_redacted(
        b'{"Cookie":"sid=secret","body":"same"}', mime_type="application/json"
    )
    assert first.key == second.key
    assert b"secret" not in object_store.get_verified(first.key, first.sha256)
    with pytest.raises(ValueError, match="integrity"):
        object_store.get_verified(first.key, "0" * 64)
    assert "X-Amz-Expires=60" in object_store.presign_get(first.key, expires_seconds=60)


def test_clickhouse_rejects_account_secrets_and_accepts_opaque_dimension() -> None:
    writer = ClickHouseWriter(
        endpoint="http://127.0.0.1:18123", user="geo", password="geo_dev_only"
    )
    event_id = f"evt_{uuid4().hex}"
    row = {
        "tenant_pub_id": "tnt_test",
        "project_pub_id": "prj_test",
        "answer_pub_id": "ans_test",
        "run_pub_id": "run_test",
        "query_pub_id": "qry_test",
        "event_time": datetime.now(UTC),
        "model": "test",
        "region": "test",
        "mode": "normal",
        "channel": "web",
        "account_dimension_opaque": "acctdim_9f6c",
        "mentioned": 1,
        "rank": 1,
        "sentiment": "positive",
        "recommended": None,
        "citation_count": 1,
        "scorer_version": "v1",
        "metric_version": "v1",
        "input_hash": "a" * 64,
        "event_id": event_id,
    }
    assert writer.insert_json_each_row("geo_analytics.answer_fact", [row]) == 1
    assert writer.count_event("geo_analytics.answer_fact", event_id) == 1
    with pytest.raises(ValueError, match="secret-bearing"):
        writer.insert_json_each_row("geo_analytics.answer_fact", [row | {"cookie": "sid=x"}])


def test_postgres_outbox_duplicate_delivery_is_idempotent() -> None:
    event_id = f"evt_{uuid4().hex}"
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO integration.outbox_event
              (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,occurred_at)
            VALUES (%s,'tnt_test','analytics.answer.analyzed','ans_test','trace_test',
                    '{"safe":true}',now())
            """,
            (event_id,),
        )
    deliveries: list[str] = []
    consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"test-{uuid4().hex}",
        publish=lambda event: deliveries.append(str(event["event_id"])),
    )
    assert consumer.drain() >= 1
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "UPDATE integration.outbox_event SET published_at=NULL WHERE event_id=%s", (event_id,)
        )
    # Other tests or a migration may legitimately leave unrelated outbox rows.
    # The receipt contract is per consumer/event, not "the global outbox is empty".
    consumer.drain()
    assert deliveries.count(event_id) == 1
