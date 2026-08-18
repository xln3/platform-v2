from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag

from ..collection.vault import (
    KmsUnavailableError,
    LocalKms,
    ProfileVault,
    SealedProfile,
    VaultTransitKms,
    profile_aad,
)
from ..config import get_settings
from .catalog import PROVIDERS, ProviderName

_TENANT_ID = re.compile(r"^tnt_[A-Za-z0-9_-]{1,116}$")
_CHALLENGE_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_MAX_RECORD_BYTES = 128 * 1024


class ProviderCredentialUnavailable(RuntimeError):
    """Encrypted provider credential storage is unavailable or corrupt."""


class ProviderCredentialNotConfigured(LookupError):
    """No saved credentials exist for this tenant and provider."""


@dataclass(frozen=True, slots=True)
class StoredProviderAccount:
    provider: ProviderName
    account: str
    password: str
    cookies: dict[str, str]
    session_status: str
    session_message: str
    session_verified_at: str | None


@dataclass(frozen=True, slots=True)
class ProviderAccountSummary:
    provider: ProviderName
    configured: bool
    account_mask: str
    session_status: str
    session_message: str
    updated_at: str | None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _provider_vault() -> ProfileVault:
    settings = get_settings()
    if settings.env.lower() in {"production", "prod"}:
        if settings.kms_provider != "vault_transit":
            raise ProviderCredentialUnavailable("provider_credential_vault_unavailable")
        try:
            kms = VaultTransitKms(
                settings.vault_transit_address,
                settings.vault_transit_token_file,
                settings.vault_transit_key_name,
            )
        except ValueError as exc:
            raise ProviderCredentialUnavailable("provider_credential_vault_unavailable") from exc
        return ProfileVault(kms)
    return ProfileVault(LocalKms(settings.kms_master_key))


def _mask_account(account: str) -> str:
    if account.isdigit() and len(account) == 11:
        return f"{account[:3]}****{account[-4:]}"
    if len(account) <= 2:
        return "*" * len(account)
    if len(account) <= 5:
        return f"{account[0]}***{account[-1]}"
    return f"{account[:2]}***{account[-2:]}"


class ProviderCredentialStore:
    """Tenant-scoped provider credentials and sessions encrypted by envelope KMS."""

    def __init__(
        self, *, datasets_dir: Path | None = None, vault: ProfileVault | None = None
    ) -> None:
        settings = get_settings()
        configured = Path(settings.datasets_dir) if settings.datasets_dir else None
        self._root = datasets_dir or configured or Path(__file__).resolve().parents[3] / ".datasets"
        self._vault = vault

    @staticmethod
    def _provider(provider: str) -> ProviderName:
        if provider not in PROVIDERS:
            raise ProviderCredentialUnavailable("provider_credential_provider_invalid")
        return cast(ProviderName, provider)

    def _directory(self, tenant_pub_id: str) -> Path:
        if _TENANT_ID.fullmatch(tenant_pub_id) is None:
            raise ProviderCredentialUnavailable("provider_credential_tenant_invalid")
        root = self._root / ".provider-credentials"
        tenant = root / tenant_pub_id
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            tenant.mkdir(exist_ok=True, mode=0o700)
            os.chmod(root, 0o700)
            os.chmod(tenant, 0o700)
        except OSError as exc:
            raise ProviderCredentialUnavailable("provider_credential_storage_unavailable") from exc
        return tenant

    def _path(self, tenant_pub_id: str, provider: ProviderName) -> Path:
        return self._directory(tenant_pub_id) / f"{provider}.json"

    @contextmanager
    def _lock(self, tenant_pub_id: str, provider: ProviderName) -> Iterator[None]:
        path = self._directory(tenant_pub_id) / f".{provider}.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise ProviderCredentialUnavailable("provider_credential_storage_unavailable") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _account_pub_id(tenant_pub_id: str, provider: ProviderName) -> str:
        digest = hashlib.sha256(f"v1|{tenant_pub_id}|{provider}".encode()).hexdigest()[:32]
        return f"pac_posting_{digest}"

    @classmethod
    def _aad(cls, tenant_pub_id: str, provider: ProviderName, version: int) -> bytes:
        return profile_aad(
            tenant_pub_id,
            "posting-provider",
            provider,
            cls._account_pub_id(tenant_pub_id, provider),
            version,
        )

    @classmethod
    def _login_challenge_aad(
        cls,
        tenant_pub_id: str,
        provider: ProviderName,
        actor_pub_id: str,
        challenge_id: str,
    ) -> bytes:
        if (
            _TENANT_ID.fullmatch(tenant_pub_id) is None
            or not 1 <= len(actor_pub_id) <= 120
            or any(ord(character) < 32 for character in actor_pub_id)
            or _CHALLENGE_ID.fullmatch(challenge_id) is None
        ):
            raise ProviderCredentialUnavailable("provider_login_challenge_invalid")
        owner_digest = hashlib.sha256(f"v1|{actor_pub_id}|{challenge_id}".encode()).hexdigest()[:32]
        return profile_aad(
            tenant_pub_id,
            f"posting-provider-login-{owner_digest}",
            provider,
            cls._account_pub_id(tenant_pub_id, provider),
            1,
        )

    def _active_vault(self) -> ProfileVault:
        return self._vault or _provider_vault()

    @staticmethod
    def _sealed(record: Mapping[str, Any]) -> SealedProfile:
        try:
            ciphertext = base64.b64decode(record["ciphertext"], validate=True)
            nonce = base64.b64decode(record["nonce"], validate=True)
            wrapped_dek = base64.b64decode(record["wrapped_dek"], validate=True)
            digest = record["ciphertext_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_credential_record_invalid") from exc
        if not isinstance(digest, str) or len(nonce) != 12:
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        return SealedProfile(ciphertext, nonce, wrapped_dek, digest)

    def _read_record(self, path: Path) -> dict[str, Any]:
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o077
                    or metadata.st_size <= 0
                    or metadata.st_size > _MAX_RECORD_BYTES
                ):
                    raise ProviderCredentialUnavailable("provider_credential_record_invalid")
                chunks: list[bytes] = []
                remaining = metadata.st_size
                while remaining > 0:
                    chunk = os.read(descriptor, min(remaining, 65_536))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if len(payload) != metadata.st_size:
                    raise ProviderCredentialUnavailable("provider_credential_record_invalid")
            finally:
                os.close(descriptor)
            record = json.loads(payload)
        except FileNotFoundError as exc:
            raise ProviderCredentialNotConfigured("provider_credential_not_configured") from exc
        except ProviderCredentialUnavailable:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_credential_record_invalid") from exc
        if not isinstance(record, dict) or record.get("schema_version") != 1:
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        return record

    def _open_record(
        self,
        tenant_pub_id: str,
        provider: ProviderName,
        record: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        version = record.get("profile_version")
        if (
            not isinstance(version, int)
            or not 1 <= version <= 1_000_000
            or record.get("tenant_pub_id") != tenant_pub_id
            or record.get("provider") != provider
        ):
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        try:
            plaintext = self._active_vault().open(
                self._sealed(record),
                self._aad(tenant_pub_id, provider, version),
            )
            payload = json.loads(plaintext)
        except (InvalidTag, KmsUnavailableError, OSError, UnicodeError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_credential_decrypt_failed") from exc
        if not isinstance(payload, dict):
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        return payload, version

    def _write_payload(
        self,
        tenant_pub_id: str,
        provider: ProviderName,
        payload: Mapping[str, Any],
        *,
        version: int,
    ) -> None:
        try:
            plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            sealed = self._active_vault().seal(
                plaintext,
                self._aad(tenant_pub_id, provider, version),
            )
        except (KmsUnavailableError, OSError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_credential_encrypt_failed") from exc
        record = {
            "schema_version": 1,
            "tenant_pub_id": tenant_pub_id,
            "provider": provider,
            "account_mask": _mask_account(str(payload["account"])),
            "session_status": str(payload.get("session_status") or "needs_login"),
            "session_message": str(payload.get("session_message") or "凭据已保存，等待登录"),
            "profile_version": version,
            "updated_at": _utc_now(),
            "ciphertext": base64.b64encode(sealed.ciphertext).decode("ascii"),
            "nonce": base64.b64encode(sealed.nonce).decode("ascii"),
            "wrapped_dek": base64.b64encode(sealed.wrapped_dek).decode("ascii"),
            "ciphertext_sha256": sealed.sha256,
        }
        destination = self._path(tenant_pub_id, provider)
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{provider}.", suffix=".tmp", dir=destination.parent
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(record, temporary, ensure_ascii=True, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as exc:
            raise ProviderCredentialUnavailable("provider_credential_storage_unavailable") from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def seal_login_challenge(
        self,
        *,
        tenant_pub_id: str,
        provider: str,
        actor_pub_id: str,
        challenge_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Encrypt one short-lived CAPTCHA session before it reaches local storage."""

        safe_provider = self._provider(provider)
        aad = self._login_challenge_aad(
            tenant_pub_id,
            safe_provider,
            actor_pub_id,
            challenge_id,
        )
        try:
            plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            sealed = self._active_vault().seal(plaintext, aad)
        except (KmsUnavailableError, OSError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_login_challenge_encrypt_failed") from exc
        return {
            "schema_version": 1,
            "ciphertext": base64.b64encode(sealed.ciphertext).decode("ascii"),
            "nonce": base64.b64encode(sealed.nonce).decode("ascii"),
            "wrapped_dek": base64.b64encode(sealed.wrapped_dek).decode("ascii"),
            "ciphertext_sha256": sealed.sha256,
        }

    def open_login_challenge(
        self,
        *,
        tenant_pub_id: str,
        provider: str,
        actor_pub_id: str,
        challenge_id: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Decrypt a claimed CAPTCHA session bound to tenant, actor and challenge ID."""

        safe_provider = self._provider(provider)
        if record.get("schema_version") != 1:
            raise ProviderCredentialUnavailable("provider_login_challenge_invalid")
        aad = self._login_challenge_aad(
            tenant_pub_id,
            safe_provider,
            actor_pub_id,
            challenge_id,
        )
        try:
            plaintext = self._active_vault().open(self._sealed(record), aad)
            payload = json.loads(plaintext)
        except (InvalidTag, KmsUnavailableError, OSError, UnicodeError, ValueError) as exc:
            raise ProviderCredentialUnavailable("provider_login_challenge_decrypt_failed") from exc
        if not isinstance(payload, dict):
            raise ProviderCredentialUnavailable("provider_login_challenge_invalid")
        return payload

    @staticmethod
    def _validated_payload(
        payload: Mapping[str, Any], provider: ProviderName
    ) -> StoredProviderAccount:
        account = payload.get("account")
        password = payload.get("password")
        cookies = payload.get("cookies", {})
        status = payload.get("session_status", "needs_login")
        message = payload.get("session_message", "凭据已保存，等待登录")
        verified_at = payload.get("session_verified_at")
        if (
            payload.get("schema_version") != 1
            or payload.get("provider") != provider
            or not isinstance(account, str)
            or not 1 <= len(account) <= 120
            or not isinstance(password, str)
            or not 1 <= len(password) <= 256
            or not isinstance(cookies, dict)
            or not isinstance(status, str)
            or not isinstance(message, str)
            or (verified_at is not None and not isinstance(verified_at, str))
        ):
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        safe_cookies: dict[str, str] = {}
        for name, value in cookies.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
                or not isinstance(value, str)
                or not 1 <= len(value) <= 4096
            ):
                raise ProviderCredentialUnavailable("provider_credential_record_invalid")
            safe_cookies[name] = value
        return StoredProviderAccount(
            provider=provider,
            account=account,
            password=password,
            cookies=safe_cookies,
            session_status=status[:80],
            session_message=message[:500],
            session_verified_at=verified_at,
        )

    def save_credentials(
        self,
        *,
        tenant_pub_id: str,
        provider: str,
        account: str,
        password: str,
    ) -> ProviderAccountSummary:
        safe_provider = self._provider(provider)
        account = account.strip()
        if not 1 <= len(account) <= 120 or not 1 <= len(password) <= 256:
            raise ProviderCredentialUnavailable("provider_credential_input_invalid")
        with self._lock(tenant_pub_id, safe_provider):
            version = 1
            path = self._path(tenant_pub_id, safe_provider)
            try:
                record = self._read_record(path)
                existing_version = record.get("profile_version")
                if isinstance(existing_version, int):
                    version = existing_version + 1
            except ProviderCredentialNotConfigured:
                pass
            self._write_payload(
                tenant_pub_id,
                safe_provider,
                {
                    "schema_version": 1,
                    "provider": safe_provider,
                    "account": account,
                    "password": password,
                    "cookies": {},
                    "session_status": "needs_login",
                    "session_message": "凭据已加密保存，等待完成登录验证",
                    "session_verified_at": None,
                },
                version=version,
            )
        return self.summary(tenant_pub_id=tenant_pub_id, provider=safe_provider)

    def load(self, *, tenant_pub_id: str, provider: str) -> StoredProviderAccount:
        safe_provider = self._provider(provider)
        with self._lock(tenant_pub_id, safe_provider):
            payload, _version = self._open_record(
                tenant_pub_id,
                safe_provider,
                self._read_record(self._path(tenant_pub_id, safe_provider)),
            )
        return self._validated_payload(payload, safe_provider)

    def update_session(
        self,
        *,
        tenant_pub_id: str,
        provider: str,
        cookies: Mapping[str, str],
        status: str,
        message: str,
    ) -> ProviderAccountSummary:
        safe_provider = self._provider(provider)
        with self._lock(tenant_pub_id, safe_provider):
            record = self._read_record(self._path(tenant_pub_id, safe_provider))
            payload, version = self._open_record(tenant_pub_id, safe_provider, record)
            payload["cookies"] = dict(cookies)
            payload["session_status"] = status[:80]
            payload["session_message"] = message[:500]
            payload["session_verified_at"] = _utc_now() if status == "ready" else None
            self._write_payload(
                tenant_pub_id,
                safe_provider,
                payload,
                version=version + 1,
            )
        return self.summary(tenant_pub_id=tenant_pub_id, provider=safe_provider)

    def summary(self, *, tenant_pub_id: str, provider: str) -> ProviderAccountSummary:
        safe_provider = self._provider(provider)
        try:
            record = self._read_record(self._path(tenant_pub_id, safe_provider))
        except ProviderCredentialNotConfigured:
            return ProviderAccountSummary(
                safe_provider,
                False,
                "",
                "not_configured",
                "尚未配置账号凭据",
                None,
            )
        account_mask = record.get("account_mask")
        status = record.get("session_status")
        message = record.get("session_message")
        updated_at = record.get("updated_at")
        if not all(isinstance(item, str) for item in (account_mask, status, message, updated_at)):
            raise ProviderCredentialUnavailable("provider_credential_record_invalid")
        return ProviderAccountSummary(
            safe_provider,
            True,
            str(account_mask)[:120],
            str(status)[:80],
            str(message)[:500],
            str(updated_at),
        )

    def list_summaries(self, *, tenant_pub_id: str) -> list[ProviderAccountSummary]:
        return [self.summary(tenant_pub_id=tenant_pub_id, provider=item) for item in PROVIDERS]

    def delete(self, *, tenant_pub_id: str, provider: str) -> None:
        safe_provider = self._provider(provider)
        with self._lock(tenant_pub_id, safe_provider):
            try:
                self._path(tenant_pub_id, safe_provider).unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ProviderCredentialUnavailable(
                    "provider_credential_storage_unavailable"
                ) from exc
