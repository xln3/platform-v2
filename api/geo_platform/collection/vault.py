import hashlib
import os
from dataclasses import dataclass
from typing import Protocol

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

    @staticmethod
    def cryptographic_delete(sealed: SealedProfile) -> SealedProfile:
        return SealedProfile(
            ciphertext=b"",
            nonce=b"",
            wrapped_dek=b"",
            sha256=hashlib.sha256(b"").hexdigest(),
        )
