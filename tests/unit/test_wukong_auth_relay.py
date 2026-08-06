"""wukong_auth_relay 单测：预认证注入、非 CONNECT 501、配置门、端到端假上游。"""

from __future__ import annotations

import base64
import socket
import threading

import pytest

from tools import wukong_auth_relay as relay


class _FakeUpstream:
    """假上游：断言首个 CONNECT 即带 Proxy-Authorization（预认证），回 200 并回显。"""

    def __init__(self, expect_basic: str) -> None:
        self.expect_basic = expect_basic
        self.requests: list[bytes] = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        conn, _ = self._server.accept()
        conn.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        self.requests.append(data)
        if self.expect_basic.encode() in data:
            conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
            echo = conn.recv(4096)
            conn.sendall(b"echo:" + echo)
        else:
            conn.sendall(b"HTTP/1.0 407 Proxy Authentication Required\r\n\r\n")
        conn.close()
        self._server.close()

    def __enter__(self) -> _FakeUpstream:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._thread.join(timeout=5)


def _relay_port(upstream_port: int, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("UPSTREAM_PROXY_URL", f"http://user:pw@127.0.0.1:{upstream_port}")
    server = relay._RelayServer(("127.0.0.1", 0), relay._ConnectHandler)
    relay._ConnectHandler.upstream_host = "127.0.0.1"
    relay._ConnectHandler.upstream_port = upstream_port
    relay._ConnectHandler.upstream_basic = base64.b64encode(b"user:pw")
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    ).start()
    return server


def test_parse_upstream() -> None:
    host, port, basic = relay._parse_upstream("http://u1:p2@183.131.35.19:21208")
    assert (host, port) == ("183.131.35.19", 21208)
    assert basic == base64.b64encode(b"u1:p2")
    with pytest.raises(ValueError):
        relay._parse_upstream("http://183.131.35.19:21208")  # 无用户名
    with pytest.raises(ValueError):
        relay._parse_upstream("not-a-url")


def test_connect_preemptive_auth_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = base64.b64encode(b"user:pw").decode()
    with _FakeUpstream(expected) as upstream:
        server = _relay_port(upstream.port, monkeypatch)
        try:
            port = server.server_address[1]
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            response = client.recv(4096)
            assert b"200 Connection Established" in response
            client.sendall(b"ping")
            assert client.recv(4096) == b"echo:ping"
            client.close()
        finally:
            server.shutdown()
            server.server_close()
        # 上游收到的是**首个请求即带凭据**（预认证，而非 407 后补）
        assert upstream.requests
        assert b"Proxy-Authorization: Basic " + expected.encode() in upstream.requests[0]


def test_non_connect_method_returns_501(monkeypatch: pytest.MonkeyPatch) -> None:
    with _FakeUpstream(""):
        server = _relay_port(1, monkeypatch)
        try:
            client = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
            client.sendall(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
            assert b"501" in client.recv(4096)
            client.close()
        finally:
            server.shutdown()
            server.server_close()


def test_main_config_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_PORT", "80")
    assert relay.main() == 2
    monkeypatch.setenv("RELAY_PORT", "19323")
    monkeypatch.delenv("UPSTREAM_PROXY_URL", raising=False)
    assert relay.main() == 2
