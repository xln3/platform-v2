from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class BrowserOidcError(RuntimeError):
    pass


class BrowserOidcUnavailableError(RuntimeError):
    pass


def _https_url(value: str, code: str, *, allow_path: bool) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or (not allow_path and parsed.path not in {"", "/"})
    ):
        raise ValueError(code)
    return value.rstrip("/")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _private_key(path_value: str) -> bytes:
    if not path_value:
        raise ValueError("oidc_browser_cookie_key_missing")
    path = Path(path_value)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError("oidc_browser_cookie_key_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError("oidc_browser_cookie_key_unsafe")
        encoded = os.read(descriptor, 256).decode("ascii").strip()
    finally:
        os.close(descriptor)
    try:
        key = _decode_base64url(encoded)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("oidc_browser_cookie_key_invalid") from exc
    if len(key) != 32:
        raise ValueError("oidc_browser_cookie_key_invalid")
    return key


@dataclass(frozen=True)
class BrowserOidcConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    post_login_uri: str
    cookie_key_file: str
    transaction_ttl_seconds: int = 300
    maximum_access_token_bytes: int = 3800

    def validated(self) -> BrowserOidcConfig:
        issuer = _https_url(self.issuer, "oidc_issuer_invalid", allow_path=False)
        authorization_endpoint = _https_url(
            self.authorization_endpoint,
            "oidc_authorization_endpoint_invalid",
            allow_path=True,
        )
        token_endpoint = _https_url(
            self.token_endpoint, "oidc_token_endpoint_invalid", allow_path=True
        )
        redirect_uri = _https_url(self.redirect_uri, "oidc_redirect_uri_invalid", allow_path=True)
        issuer_origin = urlparse(issuer).netloc
        if (
            urlparse(authorization_endpoint).netloc != issuer_origin
            or urlparse(token_endpoint).netloc != issuer_origin
        ):
            raise ValueError("oidc_endpoint_origin_mismatch")
        if not self.client_id or len(self.client_id) > 256:
            raise ValueError("oidc_client_id_invalid")
        if (
            not self.post_login_uri.startswith("/platform/")
            or self.post_login_uri.startswith("//")
            or "?" in self.post_login_uri
            or "#" in self.post_login_uri
            or "\\" in self.post_login_uri
            or len(self.post_login_uri) > 256
        ):
            raise ValueError("oidc_post_login_uri_invalid")
        if not 60 <= self.transaction_ttl_seconds <= 600:
            raise ValueError("oidc_transaction_ttl_invalid")
        if not 512 <= self.maximum_access_token_bytes <= 3800:
            raise ValueError("oidc_access_token_limit_invalid")
        return BrowserOidcConfig(
            issuer=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            post_login_uri=self.post_login_uri,
            cookie_key_file=self.cookie_key_file,
            transaction_ttl_seconds=self.transaction_ttl_seconds,
            maximum_access_token_bytes=self.maximum_access_token_bytes,
        )


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    transaction_cookie: str


@dataclass(frozen=True)
class TokenExchange:
    access_token: str
    max_age_seconds: int


class BrowserOidcFlow:
    AAD = b"geo-platform-v2:oidc-browser-transaction:v1"

    def __init__(
        self,
        config: BrowserOidcConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config.validated()
        self.transport = transport

    def _seal(self, payload: dict[str, Any]) -> str:
        nonce = os.urandom(12)
        plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ciphertext = AESGCM(_private_key(self.config.cookie_key_file)).encrypt(
            nonce, plaintext, self.AAD
        )
        return _base64url(nonce + ciphertext)

    def _open(self, value: str) -> dict[str, Any]:
        if not value or len(value) > 2048:
            raise BrowserOidcError("oidc_transaction_invalid")
        try:
            encrypted = _decode_base64url(value)
            if len(encrypted) < 29:
                raise ValueError
            plaintext = AESGCM(_private_key(self.config.cookie_key_file)).decrypt(
                encrypted[:12], encrypted[12:], self.AAD
            )
            payload = json.loads(plaintext)
        except (ValueError, UnicodeError, json.JSONDecodeError, InvalidTag) as exc:
            raise BrowserOidcError("oidc_transaction_invalid") from exc
        if not isinstance(payload, dict):
            raise BrowserOidcError("oidc_transaction_invalid")
        return payload

    def authorization_request(self, *, now: int | None = None) -> AuthorizationRequest:
        state = _base64url(os.urandom(32))
        verifier = _base64url(os.urandom(48))
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        transaction_cookie = self._seal(
            {
                "state": state,
                "verifier": verifier,
                "issued_at": int(time.time()) if now is None else now,
            }
        )
        query = urlencode(
            {
                "response_type": "code",
                "response_mode": "form_post",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": "openid",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return AuthorizationRequest(
            url=f"{self.config.authorization_endpoint}?{query}",
            transaction_cookie=transaction_cookie,
        )

    def consume_transaction(self, cookie: str, state: str, *, now: int | None = None) -> str:
        payload = self._open(cookie)
        issued_at = payload.get("issued_at")
        expected_state = payload.get("state")
        verifier = payload.get("verifier")
        current_time = int(time.time()) if now is None else now
        if (
            not isinstance(issued_at, int)
            or issued_at > current_time + 30
            or current_time - issued_at > self.config.transaction_ttl_seconds
            or not isinstance(expected_state, str)
            or not isinstance(verifier, str)
            or len(verifier) < 43
            or len(verifier) > 128
            or len(state) > 128
            or not state
            or not secrets_compare(state, expected_state)
        ):
            raise BrowserOidcError("oidc_transaction_invalid")
        return verifier

    async def exchange(self, code: str, verifier: str) -> TokenExchange:
        if not code or len(code) > 2048 or any(character.isspace() for character in code):
            raise BrowserOidcError("oidc_authorization_code_invalid")
        try:
            async with httpx.AsyncClient(
                timeout=5,
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    self.config.token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "client_id": self.config.client_id,
                        "redirect_uri": self.config.redirect_uri,
                        "code": code,
                        "code_verifier": verifier,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise BrowserOidcUnavailableError("oidc_token_exchange_unavailable") from exc
        if response.status_code != 200:
            raise BrowserOidcError("oidc_token_exchange_rejected")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrowserOidcUnavailableError("oidc_token_exchange_invalid_response") from exc
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        token_type = payload.get("token_type") if isinstance(payload, dict) else None
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token.encode()) > self.config.maximum_access_token_bytes
            or token_type != "Bearer"
            or not isinstance(expires_in, int)
            or not 1 <= expires_in <= 3600
        ):
            raise BrowserOidcUnavailableError("oidc_token_exchange_invalid_response")
        return TokenExchange(
            access_token=access_token,
            max_age_seconds=min(expires_in, 900),
        )


def secrets_compare(left: str, right: str) -> bool:
    return len(left) == len(right) and hmac.compare_digest(left, right)
