"""Wukong preemptive-auth CONNECT relay（2026-08-06 起）。

背景：悟空住宅网关反爬规则变更后只接受 **curl 式预认证**（首个 CONNECT 即带
Proxy-Authorization），拒绝 Chromium 的 407 质询-响应模式（生产抓包实证：
无认证头 CONNECT → 407 → 带认证头重连 → 仍被拒）。Chromium 的 CONNECT 头不
可定制，因此在 127.0.0.1 起一个本地中继：对 Chromium 无认证，对上游首个
CONNECT 即注入凭据，后续字节双向透传。

- 只支持 CONNECT（采集流量全 https）；其他方法如实 501，不静默转发。
- 上游凭据只进 env（UPSTREAM_PROXY_URL=http://user:pass@host:port），不写日志。
- 单进程 stdlib，threading 每连接；systemd 兜底重启。
"""

from __future__ import annotations

import base64
import os
import select
import socket
import socketserver
import sys
import threading
import time

from domain.security.redaction import redact_value, safe_exception_summary

CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 300.0
MAX_LINE = 65536


def _log(msg: str, **kv: object) -> None:
    suffix = " ".join(f"{k}={redact_value(v, key=k)}" for k, v in kv.items())
    print(f"[wukong-auth-relay] {msg} {suffix}".rstrip(), flush=True)


def _parse_upstream(url: str) -> tuple[str, int, bytes]:
    """http://user:pass@host:port → (host, port, basic_b64)。非法即 ValueError。"""
    from urllib.parse import urlsplit

    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not parsed.port:
        raise ValueError("UPSTREAM_PROXY_URL must be http(s)://user:pass@host:port")
    user = parsed.username or ""
    password = parsed.password or ""
    if not user:
        raise ValueError("UPSTREAM_PROXY_URL missing username")
    basic = base64.b64encode(f"{user}:{password}".encode()).decode()
    return parsed.hostname, int(parsed.port), basic.encode()


def _recv_line(sock: socket.socket, buf: bytearray) -> bytes:
    while b"\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed while reading line")
        buf += chunk
        if len(buf) > MAX_LINE:
            raise ConnectionError("line too long")
    line, _, rest = buf.partition(b"\r\n")
    buf[:] = rest
    return line


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """双向透传，直到任一端关闭或空闲超时。"""
    a.setblocking(False)
    b.setblocking(False)
    deadline = time.monotonic() + IDLE_TIMEOUT_S
    sockets = (a, b)
    while time.monotonic() < deadline:
        readable, _, exceptional = select.select(sockets, (), sockets, 5.0)
        if exceptional:
            return
        if not readable:
            continue
        for src in readable:
            dst = b if src is a else a
            try:
                data = src.recv(65536)
            except OSError:
                return
            if not data:
                return
            try:
                dst.sendall(data)
            except OSError:
                return
            deadline = time.monotonic() + IDLE_TIMEOUT_S


class _ConnectHandler(socketserver.BaseRequestHandler):
    upstream_host: str = ""
    upstream_port: int = 0
    upstream_basic: bytes = b""

    def handle(self) -> None:
        client = self.request
        client.settimeout(CONNECT_TIMEOUT_S)
        buf = bytearray()
        try:
            request_line = _recv_line(client, buf).decode("latin-1")
            method, target, _version = request_line.split(" ", 2)
            # 读完并丢弃客户端头（我们的 CONNECT 头由自己组装）。
            while True:
                if _recv_line(client, buf) == b"":
                    break
            if method.upper() != "CONNECT":
                _log("method_not_allowed", method=method, target=target)
                client.sendall(b"HTTP/1.1 501 Not Implemented\r\nConnection: close\r\n\r\n")
                return
            host, _, port_s = target.rpartition(":")
            if not host or not port_s.isdigit():
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=CONNECT_TIMEOUT_S
            )
            try:
                upstream.sendall(
                    f"CONNECT {host}:{port_s} HTTP/1.1\r\n"
                    f"Host: {host}:{port_s}\r\n"
                    f"Proxy-Authorization: Basic {self.upstream_basic.decode()}\r\n"
                    f"Proxy-Connection: keep-alive\r\n\r\n".encode()
                )
                ubuf = bytearray()
                status_line = _recv_line(upstream, ubuf).decode("latin-1", errors="replace")
                parts = status_line.split(" ", 2)
                if len(parts) < 2 or not parts[1].isdigit() or int(parts[1]) // 100 != 2:
                    _log("upstream_connect_rejected", target=target, status=status_line[:80])
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                    return
                # 上游 200 后的剩余头全部透传给客户端语义忽略——读完丢弃。
                while True:
                    if _recv_line(upstream, ubuf) == b"":
                        break
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                _log("tunnel_open", target=target)
                _pipe(client, upstream)
            finally:
                try:
                    upstream.close()
                except OSError:
                    pass
        except (ConnectionError, OSError, ValueError) as exc:
            _log("connect_failed", error=safe_exception_summary(exc))
        finally:
            try:
                client.close()
            except OSError:
                pass


class _RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    listen_port = int(os.environ.get("RELAY_PORT", "0"))
    if not 1024 <= listen_port <= 65535:
        _log("config_error", detail="RELAY_PORT must be 1024..65535")
        return 2
    try:
        host, port, basic = _parse_upstream(os.environ.get("UPSTREAM_PROXY_URL", ""))
    except ValueError as exc:
        _log("config_error", detail=safe_exception_summary(exc))
        return 2
    _ConnectHandler.upstream_host = host
    _ConnectHandler.upstream_port = port
    _ConnectHandler.upstream_basic = basic
    server = _RelayServer(("127.0.0.1", listen_port), _ConnectHandler)
    _log("relay_up", listen=f"127.0.0.1:{listen_port}", upstream=f"{host}:{port}")
    threading.current_thread().name = "wukong-auth-relay"
    try:
        server.serve_forever(poll_interval=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    _log("relay_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
