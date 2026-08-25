from __future__ import annotations

import httpx
import pytest

from domain.security.public_http import (
    PublicHttpRejected,
    fetch_public_http,
    validate_public_http_url,
)
from workflows.activities.service2_evidence_enrichment import (
    _exact_occurrence,
    sanitize_html_for_offline_render,
)


def _resolver(*addresses: str):
    def resolve(_host: str, _port: int):
        return [(2, 1, 6, "", (address, _port)) for address in addresses]

    return resolve


def test_public_url_rejects_private_literal_private_dns_and_mixed_dns() -> None:
    with pytest.raises(PublicHttpRejected, match="non_global"):
        validate_public_http_url("http://127.0.0.1/admin")
    with pytest.raises(PublicHttpRejected, match="non_global"):
        validate_public_http_url("https://example.com", resolver=_resolver("10.0.0.1"))
    with pytest.raises(PublicHttpRejected, match="non_global"):
        validate_public_http_url(
            "https://example.com", resolver=_resolver("93.184.216.34", "169.254.169.254")
        )


def test_fetch_checks_each_redirect_and_connected_peer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/metadata"})
        return httpx.Response(200, content=b"evidence", headers={"content-type": "text/plain"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PublicHttpRejected, match="non_global"):
            fetch_public_http(
                "https://example.com/start",
                client=client,
                resolver=_resolver("93.184.216.34"),
                peer_ip_loader=lambda _response: "93.184.216.34",
            )
        with pytest.raises(PublicHttpRejected, match="non_global"):
            fetch_public_http(
                "https://example.com/final",
                client=client,
                resolver=_resolver("93.184.216.34"),
                peer_ip_loader=lambda _response: "10.0.0.9",
            )


def test_fetch_returns_bounded_public_document() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"verified evidence",
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        document = fetch_public_http(
            "https://example.com/source",
            client=client,
            resolver=_resolver("93.184.216.34"),
            peer_ip_loader=lambda _response: "93.184.216.34",
        )
    assert document.payload == b"verified evidence"
    assert document.mime_type == "text/plain"
    assert document.peer_ip == "93.184.216.34"


def test_offline_visual_html_removes_active_content_and_offsets_disambiguate() -> None:
    source = "重复证据。中段。重复证据。"
    assert (
        _exact_occurrence(
            source,
            "重复证据",
            source.rindex("重复证据"),
            code="quote_not_found",
        )
        == 1
    )
    rendered = sanitize_html_for_offline_render(
        b'<html><body><script>alert(1)</script><p onclick="x()">exact quote</p>'
        b'<img src="http://127.0.0.1/x"></body></html>',
        "exact quote",
    )
    assert "script" not in rendered.lower()
    assert "onclick" not in rendered.lower()
    assert "127.0.0.1" not in rendered
    assert "exact quote" in rendered
