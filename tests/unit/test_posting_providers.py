from __future__ import annotations

import stat
from datetime import date
from pathlib import Path

import httpx
from geo_platform.posting import providers


def _write_session(path: Path, session_id: str = "old-session") -> None:
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f"#HttpOnly_.prfabu.com\tTRUE\t/\tTRUE\t0\tPHPSESSID\t{session_id}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _submission() -> providers.ProviderSubmission:
    return providers.ProviderSubmission(
        provider="prfabu",
        catalog_type="news",
        provider_media_id="123",
        media_name="测试媒体",
        title="测试标题",
        content_html="<p>测试正文</p>",
        customer_name="测试客户",
        release_time=date(2026, 8, 19),
    )


def _mock_client(monkeypatch, handler):
    original_client = httpx.Client

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(**kwargs)

    monkeypatch.setattr(providers.httpx, "Client", factory)


def test_submit_persists_rotated_session_atomically(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / "prfabu_session.txt"
    _write_session(session_path)
    monkeypatch.setattr(providers, "_datasets_dir", lambda: tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/index/media/article.html"
        assert "PHPSESSID=old-session" in request.headers["cookie"]
        return httpx.Response(
            200,
            headers={"set-cookie": "PHPSESSID=rotated-session; Path=/; HttpOnly; Secure"},
            json={"code": 200, "msg": "下单成功", "data": {"order_no": "order-1"}},
        )

    _mock_client(monkeypatch, handler)

    result = providers.PrfabuProvider().submit(_submission())

    assert result.status == "submitted"
    assert result.external_order_id == "order-1"
    assert providers._load_prfabu_session() == "rotated-session"
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600


def test_submit_does_not_persist_guest_cookie_after_session_expiry(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / "prfabu_session.txt"
    _write_session(session_path)
    monkeypatch.setattr(providers, "_datasets_dir", lambda: tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"set-cookie": "PHPSESSID=guest-session; Path=/; HttpOnly; Secure"},
            json={"code": 201, "msg": "您的登录已失效请重新登录"},
        )

    _mock_client(monkeypatch, handler)

    result = providers.PrfabuProvider().submit(_submission())

    assert result.status == "provider_session_expired"
    assert providers._load_prfabu_session() == "old-session"


def test_refresh_persists_session_and_maps_published_order(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / "prfabu_session.txt"
    _write_session(session_path)
    monkeypatch.setattr(providers, "_datasets_dir", lambda: tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/index/media/order.html"
        return httpx.Response(
            200,
            headers={"set-cookie": "PHPSESSID=refresh-session; Path=/; HttpOnly; Secure"},
            json={
                "code": 200,
                "data": [
                    {
                        "order_no": "order-1",
                        "title": "测试标题",
                        "media_name": "测试媒体",
                        "status_str": "已出稿",
                        "url": "https://publisher.example/article-1",
                    }
                ],
            },
        )

    _mock_client(monkeypatch, handler)

    result = providers.PrfabuProvider().refresh(
        catalog_type="news",
        external_order_id="order-1",
        media_name="测试媒体",
        title="测试标题",
    )

    assert result is not None
    assert result.status == "published"
    assert result.public_url == "https://publisher.example/article-1"
    assert providers._load_prfabu_session() == "refresh-session"
