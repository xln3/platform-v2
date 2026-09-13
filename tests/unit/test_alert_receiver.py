"""Alert intake defaults to durable Feishu; retired Server酱 must never send."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest
from geo_platform import alert_receiver


def _alert(**overrides: str) -> dict[str, str]:
    base = {
        "status": "firing",
        "alertname": "GeoOutboxPoisonMessage",
        "severity": "critical",
        "category": "pipeline",
        "service": "outbox-worker",
        "fingerprint": "abc123",
    }
    base.update(overrides)
    return base


def test_projection_carries_fingerprint_only_when_present() -> None:
    with_fp = alert_receiver.safe_alert_projection(
        {"alerts": [{"status": "firing", "labels": {"alertname": "A"}, "fingerprint": "ff00"}]}
    )
    assert with_fp[0]["fingerprint"] == "ff00"
    without_fp = alert_receiver.safe_alert_projection(
        {"alerts": [{"status": "firing", "labels": {"alertname": "A"}}]}
    )
    assert "fingerprint" not in without_fp[0]


def test_serverchan_configuration_rejected_without_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True, raising=False
    )
    monkeypatch.setenv("GEO_ALERT_SCT_SENDKEY", "sctkey")
    monkeypatch.setenv("GEO_ALERT_NOTIFY_CHANNEL", "serverchan")
    server = alert_receiver.ThreadingHTTPServer(
        ("127.0.0.1", 0), alert_receiver.AlertReceiverHandler
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        port = server.server_address[1]
        payload = json.dumps(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "GeoOutboxPoisonMessage", "severity": "critical"},
                        "fingerprint": "abc123",
                    }
                ]
            }
        ).encode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(2):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/alerts", data=payload, method="POST"
            )
            with pytest.raises(urllib.error.HTTPError) as raised:
                opener.open(req, timeout=5)
            assert raised.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert calls == []


def test_feishu_intake_returns_503_when_durable_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEO_ALERT_NOTIFY_CHANNEL", "feishu_app")
    monkeypatch.setattr(alert_receiver, "persist_business_alerts_feishu", lambda _alerts: None)
    server = alert_receiver.ThreadingHTTPServer(
        ("127.0.0.1", 0), alert_receiver.AlertReceiverHandler
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        payload = json.dumps(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "GeoOutboxPoisonMessage", "severity": "critical"},
                        "fingerprint": "abc123",
                    }
                ]
            }
        ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/alerts",
            data=payload,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with pytest.raises(urllib.error.HTTPError) as raised:
            opener.open(request, timeout=5)
        assert raised.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
