from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from geo_platform.identity.browser_oidc import (
    BrowserOidcConfig,
    BrowserOidcError,
    BrowserOidcFlow,
    BrowserOidcUnavailableError,
)


@pytest.fixture
def cookie_key_file(tmp_path: Path) -> Path:
    path = tmp_path / "oidc-cookie-key"
    path.write_text(
        base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode(),
        encoding="ascii",
    )
    path.chmod(0o600)
    return path


def config(cookie_key_file: Path, **overrides: object) -> BrowserOidcConfig:
    values: dict[str, object] = {
        "issuer": "https://identity.example.test",
        "authorization_endpoint": "https://identity.example.test/oauth2/authorize",
        "token_endpoint": "https://identity.example.test/oauth2/token",
        "client_id": "geo-platform-v2-browser",
        "redirect_uri": "https://geo.example.test/api/v2/identity/callback",
        "post_login_uri": "/platform/customer/",
        "cookie_key_file": str(cookie_key_file),
    }
    values.update(overrides)
    return BrowserOidcConfig(**values)  # type: ignore[arg-type]


def test_authorization_request_uses_state_and_s256_pkce(
    cookie_key_file: Path,
) -> None:
    flow = BrowserOidcFlow(config(cookie_key_file))

    request = flow.authorization_request(now=1000)
    query = parse_qs(urlparse(request.url).query)
    verifier = flow.consume_transaction(
        request.transaction_cookie,
        query["state"][0],
        now=1001,
    )

    assert query["response_type"] == ["code"]
    assert query["response_mode"] == ["form_post"]
    assert query["scope"] == ["openid"]
    assert query["code_challenge_method"] == ["S256"]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [expected_challenge]
    assert "client_secret" not in query


def test_transaction_is_confidential_tamper_evident_state_bound_and_expiring(
    cookie_key_file: Path,
) -> None:
    flow = BrowserOidcFlow(config(cookie_key_file))
    request = flow.authorization_request(now=1000)
    state = parse_qs(urlparse(request.url).query)["state"][0]

    assert state not in request.transaction_cookie
    for cookie, supplied_state, now in (
        (request.transaction_cookie + "A", state, 1001),
        (request.transaction_cookie, state + "A", 1001),
        (request.transaction_cookie, state, 1301),
    ):
        with pytest.raises(BrowserOidcError, match="oidc_transaction_invalid"):
            flow.consume_transaction(cookie, supplied_state, now=now)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authorization_endpoint", "http://identity.example.test/authorize"),
        ("token_endpoint", "https://user@identity.example.test/token"),
        ("token_endpoint", "https://tokens.attacker.example/token"),
        ("redirect_uri", "https://geo.example.test/callback?code=leak"),
        ("post_login_uri", "//attacker.example"),
        ("post_login_uri", "/api/v2/identity/session"),
    ],
)
def test_browser_flow_rejects_unsafe_configuration(
    cookie_key_file: Path, field: str, value: str
) -> None:
    with pytest.raises(ValueError):
        BrowserOidcFlow(config(cookie_key_file, **{field: value}))


def test_browser_flow_rejects_readable_or_symlinked_cookie_key(
    cookie_key_file: Path,
) -> None:
    readable = cookie_key_file.with_name("readable-key")
    readable.write_bytes(cookie_key_file.read_bytes())
    readable.chmod(0o644)
    symlink = cookie_key_file.with_name("symlink-key")
    symlink.symlink_to(cookie_key_file)
    for path in (readable, symlink):
        flow = BrowserOidcFlow(config(path))
        with pytest.raises(ValueError):
            flow.authorization_request()


@pytest.mark.asyncio
async def test_token_exchange_sends_pkce_and_accepts_only_bounded_bearer(
    cookie_key_file: Path,
) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(
            {
                key: values[0]
                for key, values in parse_qs(
                    request.content.decode(), keep_blank_values=True
                ).items()
            }
        )
        return httpx.Response(
            200,
            json={
                "access_token": "header.payload.signature",
                "token_type": "Bearer",
                "expires_in": 300,
            },
        )

    flow = BrowserOidcFlow(
        config(cookie_key_file),
        transport=httpx.MockTransport(handler),
    )
    exchange = await flow.exchange("opaque-code", "v" * 64)

    assert exchange.access_token == "header.payload.signature"
    assert exchange.max_age_seconds == 300
    assert observed["grant_type"] == "authorization_code"
    assert observed["code_verifier"] == "v" * 64
    assert "client_secret" not in observed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "a.b.c", "token_type": "bearer", "expires_in": 300},
        {"access_token": "a" * 3801, "token_type": "Bearer", "expires_in": 300},
        {"access_token": "a.b.c", "token_type": "Bearer", "expires_in": 0},
        {"refresh_token": "must-not-be-used", "token_type": "Bearer", "expires_in": 300},
    ],
)
async def test_token_exchange_fails_closed_on_unsafe_response(
    cookie_key_file: Path, payload: dict[str, object]
) -> None:
    flow = BrowserOidcFlow(
        config(cookie_key_file),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(BrowserOidcUnavailableError):
        await flow.exchange("opaque-code", "v" * 64)
