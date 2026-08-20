"""alert_receiver 的 Server酱方糖外发与限频单测（外发 HTTP 一律 mock，不触网）。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
from geo_platform import alert_receiver


@pytest.fixture(autouse=True)
def _reset_sct_ledger() -> Iterator[None]:
    alert_receiver._sct_last_sent.clear()
    yield
    alert_receiver._sct_last_sent.clear()


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


def test_forward_disabled_without_sendkey(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True
    )
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="") == 0
    assert calls == []


def test_forward_sends_serverchan_push(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True
    )
    sent = alert_receiver.forward_business_alerts_sct([_alert()], sendkey="sctkey")
    assert sent == 1
    assert len(calls) == 1
    assert calls[0]["flavor"] == "serverchan"
    assert calls[0]["url"] == "https://sctapi.ftqq.com/sctkey.send"
    assert "GeoOutboxPoisonMessage" in calls[0]["title"]
    assert "fingerprint: abc123" in calls[0]["body"]


def test_forward_suppresses_repeat_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True
    )
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 1
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 0
    assert len(calls) == 1


def test_forward_resends_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True
    )
    now = 1000.0
    monkeypatch.setattr(alert_receiver, "monotonic", lambda: now)
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 1
    now += alert_receiver.SCT_RESEND_WINDOW_S + 1
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 1
    assert len(calls) == 2


def test_failed_push_is_not_rate_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or False
    )
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 0
    assert alert_receiver.forward_business_alerts_sct([_alert()], sendkey="k") == 0
    assert len(calls) == 2


def test_distinct_fingerprints_are_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alert_receiver, "push_captcha_assist", lambda **kw: True)
    alerts = [_alert(fingerprint="fp1"), _alert(fingerprint="fp2")]
    assert alert_receiver.forward_business_alerts_sct(alerts, sendkey="k") == 2


def test_do_post_forwards_and_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alert_receiver, "push_captcha_assist", lambda **kw: calls.append(kw) or True
    )
    monkeypatch.setenv("GEO_ALERT_SCT_SENDKEY", "sctkey")
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
            with opener.open(req, timeout=5) as resp:
                assert resp.status == 204
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(calls) == 1
    assert calls[0]["url"] == "https://sctapi.ftqq.com/sctkey.send"


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
