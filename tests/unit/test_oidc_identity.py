from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from geo_platform.identity.oidc import OidcUnavailableError, OidcVerifier

ISSUER = "https://identity.example.test"
AUDIENCE = "geo-platform-v2"
TENANT_CLAIM = "https://geo.example/tenant"


@dataclass
class StaticSigningKey:
    key: object


class StaticJwksClient:
    def __init__(self, public_key: object) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> StaticSigningKey:
        return StaticSigningKey(self.public_key)


@pytest.fixture
def oidc_keys() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def verifier(public_key: object) -> OidcVerifier:
    return OidcVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        algorithms=("RS256",),
        tenant_claim=TENANT_CLAIM,
        max_token_lifetime_seconds=900,
        clock_skew_seconds=30,
        jwks_client=StaticJwksClient(public_key),
    )


def token(private_key: object, **overrides: object) -> str:
    issued_at = int(datetime.now(UTC).timestamp())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "sub": "opaque-idp-subject",
        "aud": AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + 300,
        TENANT_CLAIM: "tnt_6FGT8JGH9ASAQ7B1P87R6VHKNE",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"typ": "at+jwt"})


def test_oidc_verifier_validates_signature_issuer_audience_and_tenant(
    oidc_keys: tuple[object, object],
) -> None:
    private_key, public_key = oidc_keys
    identity = verifier(public_key).verify(token(private_key))
    assert identity.issuer == ISSUER
    assert identity.subject == "opaque-idp-subject"
    assert identity.tenant_pub_id == "tnt_6FGT8JGH9ASAQ7B1P87R6VHKNE"


def test_oidc_verifier_returns_the_normalized_verified_issuer(
    oidc_keys: tuple[object, object],
) -> None:
    private_key, public_key = oidc_keys
    configured_with_trailing_slash = OidcVerifier(
        issuer=f"{ISSUER}/",
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        algorithms=("RS256",),
        tenant_claim=TENANT_CLAIM,
        max_token_lifetime_seconds=900,
        clock_skew_seconds=30,
        jwks_client=StaticJwksClient(public_key),
    )

    identity = configured_with_trailing_slash.verify(token(private_key))

    assert identity.issuer == ISSUER


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example"},
        {"aud": "wrong-audience"},
        {"exp": 1},
        {TENANT_CLAIM: ""},
    ],
)
def test_oidc_verifier_rejects_wrong_or_missing_security_claims(
    oidc_keys: tuple[object, object], overrides: dict[str, object]
) -> None:
    private_key, public_key = oidc_keys
    with pytest.raises(OidcUnavailableError, match="oidc_token_invalid"):
        verifier(public_key).verify(token(private_key, **overrides))


def test_oidc_verifier_rejects_excessive_token_lifetime(
    oidc_keys: tuple[object, object],
) -> None:
    private_key, public_key = oidc_keys
    issued_at = int(datetime.now(UTC).timestamp())
    with pytest.raises(OidcUnavailableError, match="oidc_token_invalid"):
        verifier(public_key).verify(token(private_key, iat=issued_at, exp=issued_at + 901))


def test_oidc_verifier_rejects_wrong_type_and_algorithm(
    oidc_keys: tuple[object, object],
) -> None:
    private_key, public_key = oidc_keys
    issued_at = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": ISSUER,
        "sub": "opaque-idp-subject",
        "aud": AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + 300,
        TENANT_CLAIM: "tnt_6FGT8JGH9ASAQ7B1P87R6VHKNE",
    }
    wrong_type = jwt.encode(claims, private_key, algorithm="RS256", headers={"typ": "JWT"})
    wrong_algorithm = jwt.encode(
        claims,
        "symmetric-secret-not-allowed-32-bytes",
        algorithm="HS256",
        headers={"typ": "at+jwt"},
    )
    for invalid_token in (wrong_type, wrong_algorithm):
        with pytest.raises(OidcUnavailableError, match="oidc_token_invalid"):
            verifier(public_key).verify(invalid_token)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "http://identity.example.test"),
        ("issuer", "https://user@identity.example.test"),
        ("issuer", "https://identity.example.test/path"),
        ("jwks_url", "http://identity.example.test/jwks"),
        ("algorithms", ("HS256",)),
    ],
)
def test_oidc_verifier_rejects_unsafe_configuration(
    oidc_keys: tuple[object, object], field: str, value: object
) -> None:
    _private_key, public_key = oidc_keys
    arguments: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": f"{ISSUER}/.well-known/jwks.json",
        "algorithms": ("RS256",),
        "tenant_claim": TENANT_CLAIM,
        "max_token_lifetime_seconds": 900,
        "clock_skew_seconds": 30,
        "jwks_client": StaticJwksClient(public_key),
    }
    arguments[field] = value
    with pytest.raises(ValueError):
        OidcVerifier(**arguments)  # type: ignore[arg-type]
