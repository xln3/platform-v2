import base64
import hashlib
import json
import os
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Kms(Protocol):
    def wrap(self, dek: bytes, context: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes, context: bytes) -> bytes: ...


class LocalKms:
    """Development/test KMS adapter; production supplies an HSM-backed implementation."""

    def __init__(self, master_key: str) -> None:
        self._key = hashlib.sha256(master_key.encode()).digest()

    def wrap(self, dek: bytes, context: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, dek, context)

    def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        return AESGCM(self._key).decrypt(wrapped[:12], wrapped[12:], context)


class KmsUnavailableError(RuntimeError):
    """The independently operated deletion authority could not be reached."""


class VaultTransitKms:
    """Envelope-key adapter for an external Vault Transit deletion authority.

    The Vault token is loaded from a restricted file for every request so token
    rotation does not require embedding it in configuration or restarting the
    API. Only Vault ciphertext is persisted in PostgreSQL/backups.
    """

    def __init__(
        self,
        address: str,
        token_file: str,
        key_name: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        parsed = urlparse(address)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("vault_transit_address_must_be_https_origin")
        if not key_name or "/" in key_name or len(key_name) > 128:
            raise ValueError("vault_transit_key_name_invalid")
        self._address = address.rstrip("/")
        self._token_file = Path(token_file)
        self._key_name = key_name
        self._timeout_seconds = timeout_seconds

    def _token(self) -> str:
        try:
            descriptor = os.open(
                self._token_file,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                metadata = os.fstat(descriptor)
                raw_token = os.read(descriptor, 4097)
            finally:
                os.close(descriptor)
        except (OSError, UnicodeError) as exc:
            raise KmsUnavailableError("vault_transit_token_unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise KmsUnavailableError("vault_transit_token_permissions_unsafe")
        try:
            token = raw_token.decode("utf-8").strip()
        except UnicodeError as exc:
            raise KmsUnavailableError("vault_transit_token_invalid") from exc
        if not token or len(token) > 4096 or "\n" in token or "\r" in token:
            raise KmsUnavailableError("vault_transit_token_invalid")
        return token

    @staticmethod
    def _account_parts(context: bytes) -> tuple[str, str]:
        try:
            version, tenant_pub_id, _owner, _platform, account_pub_id, profile_version = (
                context.decode("utf-8").split("|")
            )
        except (UnicodeError, ValueError) as exc:
            raise KmsUnavailableError("vault_transit_context_invalid") from exc
        if (
            version != "v1"
            or not tenant_pub_id
            or not account_pub_id
            or not profile_version.isdigit()
        ):
            raise KmsUnavailableError("vault_transit_context_invalid")
        return tenant_pub_id, account_pub_id

    def account_key_name(self, tenant_pub_id: str, account_pub_id: str) -> str:
        if not tenant_pub_id or not account_pub_id:
            raise KmsUnavailableError("vault_transit_context_invalid")
        digest = hashlib.sha256(f"v1|{tenant_pub_id}|{account_pub_id}".encode()).hexdigest()[:40]
        return f"{self._key_name}-{digest}"

    def _context_key_name(self, context: bytes) -> str:
        return self.account_key_name(*self._account_parts(context))

    def _request(self, operation: str, key_name: str, payload: dict[str, str]) -> dict[str, object]:
        request = Request(
            f"{self._address}/v1/transit/{operation}/{quote(key_name, safe='')}",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Vault-Token": self._token(),
            },
            method="POST",
        )
        try:
            with urlopen(  # noqa: S310 -- constructor enforces HTTPS
                request,
                timeout=self._timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                body = json.loads(response.read(1_048_577))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise KmsUnavailableError("vault_transit_request_failed") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise KmsUnavailableError("vault_transit_response_invalid")
        return cast(dict[str, object], body["data"])

    def wrap(self, dek: bytes, context: bytes) -> bytes:
        data = self._request(
            "encrypt",
            self._context_key_name(context),
            {
                "plaintext": base64.b64encode(dek).decode("ascii"),
                "context": base64.b64encode(context).decode("ascii"),
            },
        )
        ciphertext = data.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext.startswith("vault:v"):
            raise KmsUnavailableError("vault_transit_ciphertext_invalid")
        return ciphertext.encode("ascii")

    def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        try:
            ciphertext = wrapped.decode("ascii")
        except UnicodeDecodeError as exc:
            raise KmsUnavailableError("vault_transit_ciphertext_invalid") from exc
        data = self._request(
            "decrypt",
            self._context_key_name(context),
            {
                "ciphertext": ciphertext,
                "context": base64.b64encode(context).decode("ascii"),
            },
        )
        plaintext = data.get("plaintext")
        if not isinstance(plaintext, str):
            raise KmsUnavailableError("vault_transit_plaintext_invalid")
        try:
            dek = base64.b64decode(plaintext, validate=True)
        except ValueError as exc:
            raise KmsUnavailableError("vault_transit_plaintext_invalid") from exc
        if len(dek) != 32:
            raise KmsUnavailableError("vault_transit_plaintext_invalid")
        return dek

    def destroy_account_key(self, tenant_pub_id: str, account_pub_id: str) -> None:
        """Destroy one account's external key after independently approved revocation.

        The caller must use a separately governed token with delete permission;
        normal API encrypt/decrypt policy must not grant this capability.
        Vault returns 204 on success. A missing key is accepted for idempotent
        workflow retry, while every other failure remains fail-closed.
        """

        key_name = self.account_key_name(tenant_pub_id, account_pub_id)
        request = Request(
            f"{self._address}/v1/transit/keys/{quote(key_name, safe='')}",
            headers={"X-Vault-Token": self._token()},
            method="DELETE",
        )
        try:
            with urlopen(  # noqa: S310 -- constructor enforces HTTPS
                request,
                timeout=self._timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                if response.status not in {200, 204}:
                    raise KmsUnavailableError("vault_transit_delete_failed")
        except HTTPError as exc:
            if exc.code == 404:
                return
            raise KmsUnavailableError("vault_transit_delete_failed") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise KmsUnavailableError("vault_transit_delete_failed") from exc


@dataclass(frozen=True)
class SealedProfile:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    sha256: str


def profile_aad(
    tenant_pub_id: str,
    owner_pub_id: str,
    platform: str,
    account_pub_id: str,
    profile_version: int,
) -> bytes:
    return (
        f"v1|{tenant_pub_id}|{owner_pub_id}|{platform}|{account_pub_id}|{profile_version}"
    ).encode()


class ProfileVault:
    def __init__(self, kms: Kms) -> None:
        self._kms = kms

    def seal(self, plaintext: bytes, aad: bytes) -> SealedProfile:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        return SealedProfile(
            ciphertext=ciphertext,
            nonce=nonce,
            wrapped_dek=self._kms.wrap(dek, aad),
            sha256=hashlib.sha256(ciphertext).hexdigest(),
        )

    def open(self, sealed: SealedProfile, aad: bytes) -> bytes:
        if not sealed.ciphertext or not sealed.nonce or not sealed.wrapped_dek:
            raise InvalidTag
        if not hashlib.sha256(sealed.ciphertext).hexdigest() == sealed.sha256:
            raise InvalidTag
        dek = self._kms.unwrap(sealed.wrapped_dek, aad)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad)

    def rekey(self, sealed: SealedProfile, aad: bytes, new_kms: Kms) -> SealedProfile:
        plaintext = self.open(sealed, aad)
        return ProfileVault(new_kms).seal(plaintext, aad)

    def rotate_dek(self, sealed: SealedProfile, old_aad: bytes, new_aad: bytes) -> SealedProfile:
        """Issue a fresh DEK and bind the replacement ciphertext to its new version."""

        plaintext = self.open(sealed, old_aad)
        return self.seal(plaintext, new_aad)

    @staticmethod
    def cryptographic_delete(sealed: SealedProfile) -> SealedProfile:
        return SealedProfile(
            ciphertext=b"",
            nonce=b"",
            wrapped_dek=b"",
            sha256=hashlib.sha256(b"").hexdigest(),
        )
