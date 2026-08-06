from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError


class OidcUnavailableError(RuntimeError):
    pass


class SigningKey(Protocol):
    key: Any


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


@dataclass(frozen=True)
class OidcIdentity:
    issuer: str
    subject: str
    tenant_pub_id: str


def _https_url(value: str, code: str, *, origin_only: bool) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or (origin_only and parsed.path not in {"", "/"})
    ):
        raise ValueError(code)
    return value.rstrip("/")


def normalize_oidc_issuer(value: str) -> str:
    return _https_url(value, "oidc_issuer_invalid", origin_only=True)


class OidcVerifier:
    """Strict asymmetric access-token verifier for the final identity boundary."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...],
        tenant_claim: str,
        max_token_lifetime_seconds: int,
        clock_skew_seconds: int,
        jwks_client: JwksClient | None = None,
    ) -> None:
        self.issuer = normalize_oidc_issuer(issuer)
        self.jwks_url = _https_url(jwks_url, "oidc_jwks_url_invalid", origin_only=False)
        if not audience or len(audience) > 512:
            raise ValueError("oidc_audience_invalid")
        if not tenant_claim or len(tenant_claim) > 256:
            raise ValueError("oidc_tenant_claim_invalid")
        allowed_algorithms = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if not algorithms or any(item not in allowed_algorithms for item in algorithms):
            raise ValueError("oidc_algorithms_invalid")
        if not 60 <= max_token_lifetime_seconds <= 3600:
            raise ValueError("oidc_max_token_lifetime_invalid")
        if not 0 <= clock_skew_seconds <= 120:
            raise ValueError("oidc_clock_skew_invalid")
        self.audience = audience
        self.algorithms = algorithms
        self.tenant_claim = tenant_claim
        self.max_token_lifetime_seconds = max_token_lifetime_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self.jwks_client = jwks_client or PyJWKClient(
            self.jwks_url,
            cache_keys=True,
            max_cached_keys=16,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    def verify(self, token: str) -> OidcIdentity:
        if not token or len(token) > 16_384 or token.count(".") != 2:
            raise OidcUnavailableError("oidc_token_invalid")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms:
                raise OidcUnavailableError("oidc_token_invalid")
            if header.get("typ") != "at+jwt":
                raise OidcUnavailableError("oidc_token_invalid")
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except (InvalidTokenError, PyJWKClientError, KeyError, TypeError, ValueError) as exc:
            raise OidcUnavailableError("oidc_token_invalid") from exc
        subject = claims.get("sub")
        tenant_pub_id = claims.get(self.tenant_claim)
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 512
            or not isinstance(tenant_pub_id, str)
            or not tenant_pub_id
            or len(tenant_pub_id) > 160
            or not isinstance(issued_at, int | float)
            or not isinstance(expires_at, int | float)
            or expires_at <= issued_at
            or expires_at - issued_at > self.max_token_lifetime_seconds
        ):
            raise OidcUnavailableError("oidc_token_invalid")
        return OidcIdentity(
            issuer=self.issuer,
            subject=subject,
            tenant_pub_id=tenant_pub_id,
        )
