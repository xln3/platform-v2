import hashlib
import json
import os
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.collection import assist_router
from geo_platform.main import app

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)

DENY_CODE = "assist_ticket_invalid"


def bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def create_run(client: TestClient, headers: dict[str, str]) -> str:
    project = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Assist project", "customer_name": "Assist customer"},
    )
    assert project.status_code == 201
    project_pub_id = project.json()["pub_id"]
    headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
    frozen = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers=headers,
        json={
            "query_groups": [{"name": "Core", "items": [{"text": "What is GEO?"}]}],
            "regions": ["CN-BJ"],
            "models": ["fixed"],
            "modes": ["fast"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201
    headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
    accepted = client.post(
        "/api/v2/collection/runs",
        headers=headers,
        json={
            "project_pub_id": project_pub_id,
            "config_version_pub_id": frozen.json()["pub_id"],
            "requires_intervention": False,
        },
    )
    assert accepted.status_code == 202, accepted.text
    return str(accepted.json()["workflow_id"])


class BridgeState:
    """假 bridge：record 收到的 /input 原文，帧/状态内容可调。"""

    def __init__(self) -> None:
        self.port = 0
        self.frame_bytes = b"\xff\xd8\xff\xe0fake-jpeg-frame\xff\xd9"
        self.status: dict[str, object] = {"active": True, "cleared": False}
        self.inputs: list[bytes] = []


def _bridge_handler(state: BridgeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/frame":
                self._send(200, state.frame_bytes, "image/jpeg")
            elif self.path == "/status":
                self._send(200, json.dumps(state.status).encode(), "application/json")
            else:
                self._send(404, b"{}", "application/json")

        def do_POST(self) -> None:
            if self.path == "/input":
                length = int(self.headers.get("Content-Length") or 0)
                state.inputs.append(self.rfile.read(length))
                self._send(200, b'{"ok":true}', "application/json")
            else:
                self._send(404, b"{}", "application/json")

        def log_message(self, *args: object) -> None:
            pass

    return Handler


@pytest.fixture
def bridge() -> Iterator[BridgeState]:
    state = BridgeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _bridge_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.port = server.server_address[1]
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def assist_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(assist_router, "ASSIST_DIR", tmp_path)
    return tmp_path


def write_registry(directory: Path, ticket: str, **overrides: object) -> dict[str, object]:
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    now = int(time.time())
    data: dict[str, object] = {
        "version": 1,
        "run_pub_id": "run_missing",
        "session_id": "sess-" + secrets.token_hex(6),
        "ticket_hash": digest,
        "port": 23456,
        "platform": "doubao",
        "state": "active",
        "business_key": "登录滑块",
        "evidence_ref": None,
        "created_at": now,
        "expires_at": now + 900,
        "push_sent": True,
        "solved_at": None,
    }
    data.update(overrides)
    (directory / f"{digest}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def deny_code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


def test_unknown_expired_closed_mismatch_tickets_all_403_same_code(
    assist_dir: Path,
) -> None:
    with TestClient(app) as client:
        unknown = client.get(f"/api/v2/assist/{secrets.token_urlsafe(16)}")
        assert unknown.status_code == 403

        expired_ticket = secrets.token_urlsafe(16)
        write_registry(assist_dir, expired_ticket, expires_at=int(time.time()) - 10)
        expired = client.get(f"/api/v2/assist/{expired_ticket}")
        assert expired.status_code == 403

        closed_ticket = secrets.token_urlsafe(16)
        write_registry(assist_dir, closed_ticket, state="closed")
        closed = client.get(f"/api/v2/assist/{closed_ticket}")
        assert closed.status_code == 403

        mismatch_ticket = secrets.token_urlsafe(16)
        write_registry(
            assist_dir,
            mismatch_ticket,
            ticket_hash=hashlib.sha256(b"other-ticket").hexdigest(),
        )
        mismatch = client.get(f"/api/v2/assist/{mismatch_ticket}")
        assert mismatch.status_code == 403

        bad_version_ticket = secrets.token_urlsafe(16)
        write_registry(assist_dir, bad_version_ticket, version=2)
        bad_version = client.get(f"/api/v2/assist/{bad_version_ticket}")
        assert bad_version.status_code == 403

    codes = {deny_code(item) for item in (unknown, expired, closed, mismatch, bad_version)}
    assert codes == {DENY_CODE}


def test_valid_ticket_serves_mobile_page(assist_dir: Path) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket)
    with TestClient(app) as client:
        response = client.get(f"/api/v2/assist/{ticket}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert 'name="viewport"' in html
    assert "user-scalable=no" in html
    assert "touch-action: none" in html
    assert "touchstart" in html
    assert "touchend" in html
    assert "naturalWidth" in html  # 显示像素→帧自然分辨率换算
    assert "'/done'" in html
    assert "cleared" in html  # status 轮询自动确认
    assert "已解决，采集已自动恢复" in html
    assert "我已完成，继续采集" in html


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_frame_proxy_passthrough_and_503(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=bridge.port)
    with TestClient(app) as client:
        ok = client.get(f"/api/v2/assist/{ticket}/frame")
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/jpeg"
        assert ok.content == bridge.frame_bytes

        dead_ticket = secrets.token_urlsafe(16)
        write_registry(assist_dir, dead_ticket, port=_unused_port())
        down = client.get(f"/api/v2/assist/{dead_ticket}/frame")
        assert down.status_code == 503
        assert down.json() == {"error": "bridge_unavailable"}


def test_frame_rejects_port_out_of_range(assist_dir: Path) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=80)
    with TestClient(app) as client:
        response = client.get(f"/api/v2/assist/{ticket}/frame")
    assert response.status_code == 403
    assert deny_code(response) == DENY_CODE


def test_frame_rate_limit(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=bridge.port)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get(f"/api/v2/assist/{ticket}/frame").status_code == 200
        limited = client.get(f"/api/v2/assist/{ticket}/frame")
        assert limited.status_code == 429


def test_status_merges_registry_fields(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    registry = write_registry(assist_dir, ticket, port=bridge.port)
    with TestClient(app) as client:
        response = client.get(f"/api/v2/assist/{ticket}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["cleared"] is False  # bridge 透传字段
    assert body["active"] is True
    # 注册表合并字段（bridge /status 不带这些，手机页顶部状态条依赖它们）
    assert body["state"] == "active"
    assert body["expires_at"] == registry["expires_at"]
    assert body["solved_at"] is None
    assert body["platform"] == "doubao"
    assert body["business_key"] == "登录滑块"


def test_input_proxy_passthrough(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=bridge.port)
    payload = {"type": "drag", "start": [10, 20], "end": [30, 40]}
    raw = json.dumps(payload).encode()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/assist/{ticket}/input",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert bridge.inputs == [raw]  # 请求体逐字节原样透传


def test_input_body_limit(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=bridge.port)
    with TestClient(app) as client:
        too_large = client.post(
            f"/api/v2/assist/{ticket}/input",
            content=b"x" * 8193,
            headers={"Content-Type": "application/json"},
        )
        assert too_large.status_code == 413
        at_limit = client.post(
            f"/api/v2/assist/{ticket}/input",
            content=b"x" * 8192,
            headers={"Content-Type": "application/json"},
        )
        assert at_limit.status_code == 200
    assert bridge.inputs == [b"x" * 8192]


def test_input_rate_limit(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, port=bridge.port)
    click = {"type": "click", "at": [1, 2]}
    with TestClient(app) as client:
        for _ in range(20):
            assert client.post(f"/api/v2/assist/{ticket}/input", json=click).status_code == 200
        limited = client.post(f"/api/v2/assist/{ticket}/input", json=click)
        assert limited.status_code == 429


def test_done_marks_registry_and_enqueues_signal_idempotently(
    assist_dir: Path, bridge: BridgeState
) -> None:
    ticket = secrets.token_urlsafe(16)
    session_id = "sess-" + secrets.token_hex(8)
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "assist-done-" + secrets.token_hex(8))
        workflow_id = create_run(client, headers)
        run_pub_id = workflow_id.rsplit("/", 1)[-1]
        write_registry(
            assist_dir,
            ticket,
            tenant_pub_id=tenant,
            run_pub_id=run_pub_id,
            session_id=session_id,
            port=bridge.port,
        )
        first = client.post(f"/api/v2/assist/{ticket}/done")
        assert first.status_code == 200
        assert first.json() == {"ok": True}
        second = client.post(f"/api/v2/assist/{ticket}/done")
        assert second.status_code == 200
        assert second.json() == {"ok": True}

    saved = json.loads(
        (assist_dir / f"{hashlib.sha256(ticket.encode()).hexdigest()}.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["state"] == "solved"
    assert isinstance(saved["solved_at"], int) and saved["solved_at"] > 0

    key_hash = hashlib.sha256(f"captcha-solved:{session_id}".encode()).hexdigest()
    with psycopg.connect(POSTGRES_DSN) as connection:
        rows = connection.execute(
            """
            SELECT tenant_pub_id, workflow_id, signal_name, args
            FROM integration.workflow_signal_command
            WHERE idempotency_key_hash=%s
            """,
            (key_hash,),
        ).fetchall()
    assert len(rows) == 1  # 重复 done 不重复入行
    tenant_pub_id, row_workflow_id, signal_name, args = rows[0]
    assert tenant_pub_id == tenant
    assert row_workflow_id == workflow_id
    assert signal_name == "captcha_solved"
    assert args == [session_id]


def test_done_recovers_signal_when_registry_rewrite_interrupted(
    assist_dir: Path, bridge: BridgeState
) -> None:
    """崩溃窗口自愈：signal 已入队但注册表仍 active 时，重试不得重复入队。"""
    ticket = secrets.token_urlsafe(16)
    session_id = "sess-" + secrets.token_hex(8)
    with TestClient(app) as client:
        tenant, headers = bootstrap(client, "assist-replay-" + secrets.token_hex(8))
        workflow_id = create_run(client, headers)
        run_pub_id = workflow_id.rsplit("/", 1)[-1]
        registry = write_registry(
            assist_dir,
            ticket,
            tenant_pub_id=tenant,
            run_pub_id=run_pub_id,
            session_id=session_id,
            port=bridge.port,
        )
        assert client.post(f"/api/v2/assist/{ticket}/done").status_code == 200
        # 模拟崩溃：注册表回滚成 active（signal 行仍在库里）
        registry_path = assist_dir / f"{hashlib.sha256(ticket.encode()).hexdigest()}.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        retry = client.post(f"/api/v2/assist/{ticket}/done")
        assert retry.status_code == 200

    key_hash = hashlib.sha256(f"captcha-solved:{session_id}".encode()).hexdigest()
    with psycopg.connect(POSTGRES_DSN) as connection:
        count = connection.execute(
            """
            SELECT count(*) FROM integration.workflow_signal_command
            WHERE idempotency_key_hash=%s AND tenant_pub_id=%s
            """,
            (key_hash, tenant),
        ).fetchone()
    assert count is not None and count[0] == 1
    saved = json.loads(registry_path.read_text(encoding="utf-8"))
    assert saved["state"] == "solved"


def test_done_unknown_run_is_404(assist_dir: Path, bridge: BridgeState) -> None:
    ticket = secrets.token_urlsafe(16)
    write_registry(assist_dir, ticket, run_pub_id="run_nonexistent", port=bridge.port)
    with TestClient(app) as client:
        response = client.post(f"/api/v2/assist/{ticket}/done")
    assert response.status_code == 404
    assert deny_code(response) == "run_not_found"
