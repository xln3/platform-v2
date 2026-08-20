from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform import alert_receiver
from geo_platform.notifications.config import FeishuBotConfig


def _config() -> FeishuBotConfig:
    return FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url="http://127.0.0.1:18000",
        app_secret_file="",
        verification_token_file="",
        encrypt_key_file="",
        allowed_open_ids_file="",
        link_signing_key_file="",
    )


def test_projection_whitelists_annotations_region_and_timestamps() -> None:
    projected = alert_receiver.safe_alert_projection(
        {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "GeoCollectionRunStalled",
                        "severity": "warning",
                        "region": "CN-SH",
                        "secret_label": "must-not-leak",
                    },
                    "annotations": {
                        "summary": "collection stalled",
                        "description": "safe detail",
                        "dashboard": "https://secret.invalid/",
                    },
                    "startsAt": "2026-08-14T10:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "fingerprint": "fp-one",
                    "generatorURL": "http://internal-prometheus/graph",
                }
            ]
        }
    )
    assert projected == [
        {
            "status": "firing",
            "alertname": "GeoCollectionRunStalled",
            "severity": "warning",
            "region": "CN-SH",
            "fingerprint": "fp-one",
            "summary": "collection stalled",
            "description": "safe detail",
            "starts_at": "2026-08-14T10:00:00Z",
            "ends_at": "0001-01-01T00:00:00Z",
        }
    ]


def test_feishu_alert_policy_includes_critical_stalled_and_excludes_self() -> None:
    assert alert_receiver.eligible_for_feishu({"alertname": "AnyCritical", "severity": "critical"})
    assert alert_receiver.eligible_for_feishu(
        {"alertname": "GeoCollectionRunStalled", "severity": "warning"}
    )
    assert not alert_receiver.eligible_for_feishu(
        {"alertname": "GeoApiP95LatencyHigh", "severity": "warning"}
    )
    assert not alert_receiver.eligible_for_feishu(
        {"alertname": "GeoFeishuBotDown", "severity": "critical"}
    )
    assert not alert_receiver.eligible_for_feishu(
        {"alertname": "Other", "severity": "critical", "service": "feishu-bot"}
    )


def test_warning_quiet_window_suppresses_firing_but_not_resolved() -> None:
    config = FeishuBotConfig(
        **{
            **_config().__dict__,
            "alert_quiet_hours": "23:00-07:00",
            "alert_timezone": "Asia/Shanghai",
        }
    )
    during_quiet = datetime(2026, 1, 1, 16, 30, tzinfo=UTC)  # Shanghai 00:30
    firing = {
        "status": "firing",
        "alertname": "GeoCollectionRunStalled",
        "severity": "warning",
    }
    assert not alert_receiver.eligible_for_feishu(firing, config=config, now=during_quiet)
    assert alert_receiver.eligible_for_feishu(
        {**firing, "status": "resolved"}, config=config, now=during_quiet
    )


def test_feishu_ingest_only_persists_local_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeSession:
        committed = False

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def commit(self) -> None:
            self.committed = True

    session = FakeSession()

    class FakeNotifications:
        def __init__(self, observed: object) -> None:
            assert observed is session

        def record_alert(self, alert: dict[str, str], **kwargs: Any) -> None:
            calls.append({"alert": alert, **kwargs})

    monkeypatch.setattr(alert_receiver, "SessionLocal", lambda: session)
    monkeypatch.setattr(alert_receiver, "NotificationService", FakeNotifications)
    accepted = alert_receiver.persist_business_alerts_feishu(
        [
            {
                "status": "firing",
                "alertname": "GeoCollectionRunStalled",
                "severity": "warning",
                "fingerprint": "fp-stalled",
            },
            {
                "status": "firing",
                "alertname": "GeoApiP95LatencyHigh",
                "severity": "warning",
                "fingerprint": "fp-noisy",
            },
        ],
        config=_config(),
    )
    assert accepted == 1
    assert session.committed is True
    assert len(calls) == 1
    assert calls[0]["target_chat_id"] == "oc_test"
