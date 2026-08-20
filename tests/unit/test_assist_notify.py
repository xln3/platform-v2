"""assist_notify 推送网关单元测试：只打 127.0.0.1 ephemeral 端口的本机回环
假服务器（stdlib http.server），绝不发真 HTTP 出网。

覆盖：六种 flavor 的 URL/payload 拼装正确性 + 未配置/未知 flavor/对端异常 →
False 不抛。
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from workflows.activities.assist_notify import push_captcha_assist

_TITLE = "[GEO] 豆包采集撞验证码，点此接管"
_ASSIST_URL = "https://assist.example/api/v2/assist/TICKETabc123"
_BODY = f"平台: 豆包\n撞码 query: run_1-task-7\n有效期: 70 分钟\n接管链接: {_ASSIST_URL}"


class _CaptureServer:
    """本机回环假推送端点：记录 method/path/headers/body，一律回 200。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.response_body = b'{"code":0,"msg":"success"}'
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silence access log
                pass

            def _record(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                outer.records.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                payload = outer.response_body
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _record
            do_POST = _record

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._srv.shutdown()


@pytest.fixture
def server():
    srv = _CaptureServer()
    yield srv
    srv.stop()


# ── 六种 flavor 拼装正确性 ─────────────────────────────────────────────────────


def test_bark_get_path_and_click_url(server: _CaptureServer) -> None:
    ok = push_captcha_assist(flavor="bark", url=f"{server.base}/keyABC", title=_TITLE, body=_BODY)
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "GET"
    split = urllib.parse.urlsplit(rec["path"])
    segments = split.path.split("/")
    assert segments[1] == "keyABC"  # 含 key 的 base 原样拼接
    assert urllib.parse.unquote(segments[2]) == _TITLE
    assert urllib.parse.unquote(segments[3]) == _BODY
    qs = urllib.parse.parse_qs(split.query)
    assert qs["url"] == [_ASSIST_URL]  # 点了直接打开接管页


def test_serverchan_query_params(server: _CaptureServer) -> None:
    ok = push_captcha_assist(
        flavor="serverchan", url=f"{server.base}/SCT123.send", title=_TITLE, body=_BODY
    )
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "GET"
    split = urllib.parse.urlsplit(rec["path"])
    assert split.path == "/SCT123.send"
    qs = urllib.parse.parse_qs(split.query)
    assert qs["title"] == [_TITLE]
    assert qs["desp"] == [_BODY]


def test_feishu_text_payload(server: _CaptureServer) -> None:
    ok = push_captcha_assist(flavor="feishu", url=server.base, title=_TITLE, body=_BODY)
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "POST"
    assert rec["headers"]["Content-Type"] == "application/json"
    payload = json.loads(rec["body"])
    assert payload == {"msg_type": "text", "content": {"text": f"{_TITLE}\n{_BODY}"}}


def test_feishu_business_error_returns_false(server: _CaptureServer) -> None:
    server.response_body = b'{"code":9499,"msg":"Bad Request"}'
    assert push_captcha_assist(flavor="feishu", url=server.base, title=_TITLE, body=_BODY) is False


def test_wecom_text_payload(server: _CaptureServer) -> None:
    ok = push_captcha_assist(flavor="wecom", url=server.base, title=_TITLE, body=_BODY)
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "POST"
    assert rec["headers"]["Content-Type"] == "application/json"
    payload = json.loads(rec["body"])
    assert payload == {"msgtype": "text", "text": {"content": f"{_TITLE}\n{_BODY}"}}


def test_ntfy_title_header_and_plain_body(server: _CaptureServer) -> None:
    ok = push_captcha_assist(
        flavor="ntfy", url=f"{server.base}/geo-alerts", title=_TITLE, body=_BODY
    )
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "POST"
    # 中文标题走 RFC 2047 encoded-word（ntfy/urllib 头只收 latin-1）
    assert rec["headers"]["Title"].startswith("=?utf-8?")
    assert rec["body"].decode("utf-8") == _BODY  # 纯文本含 assist_url


def test_raw_json_payload(server: _CaptureServer) -> None:
    ok = push_captcha_assist(flavor="raw", url=server.base, title=_TITLE, body=_BODY)
    assert ok is True
    (rec,) = server.records
    assert rec["method"] == "POST"
    payload = json.loads(rec["body"])
    assert payload == {
        "event": "captcha_assist",
        "title": _TITLE,
        "body": _BODY,
        "url": _ASSIST_URL,
    }


def test_default_flavor_is_raw(server: _CaptureServer) -> None:
    ok = push_captcha_assist(flavor="", url=server.base, title=_TITLE, body=_BODY)
    assert ok is True
    (rec,) = server.records
    assert json.loads(rec["body"])["event"] == "captcha_assist"


def test_host_proxy_environment_is_ignored(
    monkeypatch: pytest.MonkeyPatch, server: _CaptureServer
) -> None:
    dead_proxy = "http://127.0.0.1:1"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, dead_proxy)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert push_captcha_assist(flavor="raw", url=server.base, title=_TITLE, body=_BODY) is True


# ── 失败路径：一律 False 不抛 ───────────────────────────────────────────────────


def test_missing_url_returns_false() -> None:
    assert push_captcha_assist(flavor="raw", url="", title=_TITLE, body=_BODY) is False
    assert push_captcha_assist(flavor="bark", url="   ", title=_TITLE, body=_BODY) is False


def test_unknown_flavor_returns_false(server: _CaptureServer) -> None:
    assert (
        push_captcha_assist(flavor="telegram", url=server.base, title=_TITLE, body=_BODY) is False
    )
    assert server.records == []  # 未知 flavor 不发出任何请求


def test_peer_failure_returns_false_not_raise() -> None:
    # 找一个已关闭的端口（bind 后立即 close），连接即 refused
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    for flavor in ("bark", "serverchan", "feishu", "wecom", "ntfy", "raw"):
        assert (
            push_captcha_assist(
                flavor=flavor,
                url=f"http://127.0.0.1:{dead_port}/hook",
                title=_TITLE,
                body=_BODY,
                timeout_s=0.5,
            )
            is False
        )


def test_non_2xx_returns_false(server: _CaptureServer) -> None:
    server.stop()  # 先关正常端点
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _500(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _500)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert (
            push_captcha_assist(
                flavor="raw",
                url=f"http://127.0.0.1:{srv.server_address[1]}",
                title=_TITLE,
                body=_BODY,
                timeout_s=1.0,
            )
            is False
        )
    finally:
        srv.shutdown()
