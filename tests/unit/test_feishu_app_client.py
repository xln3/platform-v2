from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from geo_platform.notifications.config import (
    FeishuBotConfig,
    NotificationConfigurationError,
)
from geo_platform.notifications.feishu_client import FeishuApiError, FeishuAppClient


def _config(tmp_path: Path, *, api_base: str = "http://127.0.0.1:18000") -> FeishuBotConfig:
    secret = tmp_path / "app-secret"
    secret.write_text("test-app-secret-not-real", encoding="utf-8")
    return FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url=api_base,
        app_secret_file=str(secret),
        verification_token_file="",
        encrypt_key_file="",
        allowed_open_ids_file="",
        link_signing_key_file="",
    )


def test_token_is_cached_and_send_payload_has_uuid(tmp_path: Path) -> None:
    token_calls = 0
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("tenant_access_token/internal"):
            token_calls += 1
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token-one", "expire": 7200},
            )
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"x-tt-logid": "log-safe-1"},
            json={"code": 0, "data": {"message_id": "om_test"}},
        )

    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    command_id = uuid.uuid4()
    try:
        first = client.send_card(
            chat_id="oc_test",
            card={"header": {"title": "test"}},
            command_uuid=command_id,
        )
        client.send_text(chat_id="oc_test", text="status", command_uuid=uuid.uuid4())
    finally:
        client.close()
    assert token_calls == 1
    assert first.data["message_id"] == "om_test"
    assert first.request_log_id == "log-safe-1"
    assert requests[0]["receive_id"] == "oc_test"
    assert requests[0]["msg_type"] == "interactive"
    assert requests[0]["uuid"] == str(command_id)
    assert json.loads(requests[0]["content"])["header"]["title"] == "test"


def test_concurrent_token_refresh_is_single_flight(tmp_path: Path) -> None:
    calls = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path.endswith("tenant_access_token/internal")
        with lock:
            calls += 1
        time.sleep(0.05)
        return httpx.Response(
            200,
            json={"code": 0, "tenant_access_token": "shared-token", "expire": 7200},
        )

    client = FeishuAppClient(_config(tmp_path), transport=httpx.MockTransport(handler))
    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            tokens = list(executor.map(lambda _index: client.tenant_access_token(), range(24)))
    finally:
        client.close()
    assert set(tokens) == {"shared-token"}
    assert calls == 1


def test_invalid_token_refreshes_once_and_retries(tmp_path: Path) -> None:
    token_calls = 0
    api_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("tenant_access_token/internal"):
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": f"token-{token_calls}",
                    "expire": 7200,
                },
            )
        api_tokens.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer token-1":
            return httpx.Response(200, json={"code": 99991661, "msg": "invalid"})
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_2"}})

    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    try:
        result = client.send_text(chat_id="oc_test", text="ok", command_uuid=uuid.uuid4())
    finally:
        client.close()
    assert result.data["message_id"] == "om_2"
    assert token_calls == 2
    assert api_tokens == ["Bearer token-1", "Bearer token-2"]


def test_retryable_token_http_failure_is_bounded(tmp_path: Path) -> None:
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, headers={"x-tt-logid": f"retry-{calls}"})
        return httpx.Response(
            200,
            json={"code": 0, "tenant_access_token": "token-after-retry", "expire": 7200},
        )

    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
        random_value=lambda: 0,
        max_attempts=3,
    )
    try:
        assert client.tenant_access_token() == "token-after-retry"
    finally:
        client.close()
    assert calls == 3
    assert delays == [0.25, 0.5]


def test_api_timeout_and_malformed_json_are_sanitized(tmp_path: Path) -> None:
    mode = "timeout"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        if mode == "timeout":
            raise httpx.ReadTimeout("contains-sensitive-provider-detail", request=request)
        return httpx.Response(200, content=b"malformed", headers={"x-tt-logid": "safe-log"})

    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    try:
        with pytest.raises(FeishuApiError) as timeout_error:
            client.send_text(chat_id="oc_test", text="x", command_uuid=uuid.uuid4())
        assert timeout_error.value.marker == "feishu_api_transport_error"
        assert "sensitive" not in str(timeout_error.value)
        mode = "malformed"
        with pytest.raises(FeishuApiError) as json_error:
            client.send_text(chat_id="oc_test", text="x", command_uuid=uuid.uuid4())
        assert json_error.value.marker == "feishu_response_json_invalid"
        assert json_error.value.request_log_id == "safe-log"
    finally:
        client.close()


@pytest.mark.parametrize(
    ("response", "marker", "retryable"),
    [
        (httpx.Response(403, json={"code": 0}), "feishu_token_http_error", False),
        (
            httpx.Response(200, json={"code": 19001, "msg": "denied"}),
            "feishu_token_business_error",
            False,
        ),
        (
            httpx.Response(
                200,
                json={"tenant_access_token": "missing-code", "expire": 7200},
            ),
            "feishu_token_business_error",
            False,
        ),
        (httpx.Response(200, content=b"not-json"), "feishu_response_json_invalid", False),
    ],
)
def test_token_failures_are_sanitized(
    tmp_path: Path,
    response: httpx.Response,
    marker: str,
    retryable: bool,
) -> None:
    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(lambda _request: response),
        sleep=lambda _seconds: None,
    )
    try:
        with pytest.raises(FeishuApiError) as raised:
            client.tenant_access_token()
    finally:
        client.close()
    assert raised.value.marker == marker
    assert raised.value.retryable is retryable
    assert "test-app-secret-not-real" not in str(raised.value)
    assert "denied" not in str(raised.value)


def test_update_card_uses_patch_content_string(tmp_path: Path) -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        observed.append(request)
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = FeishuAppClient(_config(tmp_path), transport=httpx.MockTransport(handler))
    try:
        client.update_card(message_id="om_123", card={"elements": []})
    finally:
        client.close()
    assert observed[0].method == "PATCH"
    assert observed[0].url.path.endswith("/im/v1/messages/om_123")
    assert json.loads(json.loads(observed[0].content)["content"]) == {"elements": []}


def test_api_response_without_business_code_is_rejected(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        return httpx.Response(200, json={"data": {"message_id": "om_must_not_pass"}})

    client = FeishuAppClient(_config(tmp_path), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FeishuApiError, match="feishu_api_business_error"):
            client.send_text(chat_id="oc_test", text="direct", command_uuid=uuid.uuid4())
    finally:
        client.close()


def test_provider_message_id_cannot_change_request_path(tmp_path: Path) -> None:
    client = FeishuAppClient(
        _config(tmp_path),
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("invalid ID must be rejected before network access")
        ),
    )
    try:
        with pytest.raises(FeishuApiError, match="feishu_update_message_id_invalid"):
            client.update_card(message_id="../auth/token", card={"elements": []})
    finally:
        client.close()


def test_production_openapi_and_assist_base_are_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(NotificationConfigurationError, match="api_base_not_allowlisted"):
        replace(
            config,
            env="production",
            api_base_url="https://open.feishu.cn.evil.example",
        ).validate_sender()
    with pytest.raises(NotificationConfigurationError, match="assist_public_base_not_https"):
        replace(
            config,
            env="production",
            api_base_url="https://open.feishu.cn",
            public_base_url="https://user:password@assist.example/path?token=fake",
        ).validate_assist_links()


def test_proxy_pollution_does_not_intercept_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            if self.path.endswith("tenant_access_token/internal"):
                payload = {"code": 0, "tenant_access_token": "local-token", "expire": 7200}
            else:
                payload = {"code": 0, "data": {"message_id": "om_local"}}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    client = FeishuAppClient(
        _config(tmp_path, api_base=f"http://127.0.0.1:{server.server_address[1]}")
    )
    try:
        result = client.send_text(chat_id="oc_test", text="direct", command_uuid=uuid.uuid4())
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.data["message_id"] == "om_local"
