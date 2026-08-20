from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from fastapi.testclient import TestClient
from geo_platform.collection import assist_router
from geo_platform.main import app
from geo_platform.notifications.security import make_assist_capability
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import text


def _write_otp_registry(directory: Path, ticket: str) -> dict[str, object]:
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    now = int(time.time())
    record: dict[str, object] = {
        "version": 1,
        "session_kind": "otp_cli",
        "run_pub_id": "otp-assist-yiyan-test",
        "session_id": "otp-session-" + secrets.token_hex(8),
        "ticket_hash": digest,
        "port": 19226,
        "platform": "yiyan",
        "instance_key": "yiyan_sh",
        "state": "active",
        "business_key": "OTP test",
        "evidence_ref": None,
        "created_at": now,
        "expires_at": now + 600,
        "push_sent": True,
        "solved_at": None,
    }
    (directory / f"{digest}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def test_otp_mobile_done_does_not_require_collection_run(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(assist_router, "ASSIST_DIR", tmp_path)
    ticket = secrets.token_urlsafe(24)
    record = _write_otp_registry(tmp_path, ticket)
    with TestClient(app) as client:
        response = client.post(f"/api/v2/assist/{ticket}/done")
    assert response.status_code == 200
    saved = json.loads(
        (tmp_path / f"{hashlib.sha256(ticket.encode()).hexdigest()}.json").read_text()
    )
    assert saved["state"] == "solved"
    with SessionLocal() as session:
        signals = session.execute(
            text(
                "SELECT count(*) FROM integration.workflow_signal_command "
                "WHERE args=CAST(:args AS jsonb)"
            ),
            {"args": json.dumps([record["session_id"]])},
        ).scalar_one()
    assert signals == 0


def test_notification_capability_route_never_needs_raw_ticket_in_card_url(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(assist_router, "ASSIST_DIR", tmp_path)
    link_key = tmp_path / "link-key"
    link_key.write_text("k" * 32, encoding="utf-8")
    monkeypatch.setenv("GEO_FEISHU_LINK_SIGNING_KEY_FILE", str(link_key))
    ticket = secrets.token_urlsafe(24)
    record = _write_otp_registry(tmp_path, ticket)
    notification_id = "ntf_test_capability"
    capability = make_assist_capability(
        notification_id=notification_id,
        ticket_sha256=str(record["ticket_hash"]),
        expires_at=int(record["expires_at"]),
        key="k" * 32,
    )
    path = f"/api/v2/assist/notification/{notification_id}/{capability}"
    assert ticket not in path
    with TestClient(app) as client:
        page = client.get(path)
        wrong_notice = client.get(f"/api/v2/assist/notification/ntf_other/{capability}")
        done = client.post(path + "/done")
    assert page.status_code == 200
    assert wrong_notice.status_code == 403
    assert done.status_code == 200
