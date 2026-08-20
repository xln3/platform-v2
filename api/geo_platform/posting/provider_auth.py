from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from .providers import (
    _client_prfabu_session,
    _datasets_dir,
    _json_object,
    _load_prfabu_session,
    _new_prfabu_client,
    _persist_prfabu_session,
    _prfabu_session_lock,
    _text,
)

_CHALLENGE_TTL_SECONDS = 5 * 60
_MAX_CHALLENGE_BYTES = 4 * 1024
_MAX_CAPTCHA_BYTES = 256 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHALLENGE_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


class PrfabuAuthUnavailable(RuntimeError):
    """The provider login service or its local secure storage is unavailable."""


class PrfabuChallengeInvalid(ValueError):
    """The one-time login challenge is missing, expired, or belongs to another actor."""


@dataclass(frozen=True, slots=True)
class PrfabuCaptchaChallenge:
    challenge_id: str
    image_base64: str
    expires_in_seconds: int = _CHALLENGE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class PrfabuSessionState:
    status: str
    message: str
    balance: Decimal | None = None


def _challenge_directory() -> Path:
    directory = _datasets_dir() / ".provider-login" / "prfabu"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def _challenge_path(challenge_id: str) -> Path:
    if not _CHALLENGE_ID.fullmatch(challenge_id):
        raise PrfabuChallengeInvalid("provider_login_challenge_invalid")
    return _challenge_directory() / f"{challenge_id}.json"


def _remove_expired_challenges(directory: Path, now: float) -> None:
    try:
        candidates = tuple(directory.glob("*.json"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if now - candidate.stat().st_mtime > _CHALLENGE_TTL_SECONDS:
                candidate.unlink()
        except OSError:
            continue


def _write_challenge(challenge_id: str, payload: dict[str, Any]) -> None:
    directory = _challenge_directory()
    _remove_expired_challenges(directory, time.time())
    destination = _challenge_path(challenge_id)
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{challenge_id}.",
            suffix=".tmp",
            dir=directory,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(payload, temporary, ensure_ascii=True, separators=(",", ":"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except OSError as exc:
        raise PrfabuAuthUnavailable("provider_login_storage_unavailable") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _consume_challenge(
    challenge_id: str,
    *,
    tenant_pub_id: str,
    actor_pub_id: str,
) -> str:
    path = _challenge_path(challenge_id)
    try:
        if path.stat().st_size > _MAX_CHALLENGE_BYTES:
            raise PrfabuChallengeInvalid("provider_login_challenge_invalid")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrfabuChallengeInvalid("provider_login_challenge_invalid") from exc
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        payload = json.loads(raw)
    except (UnboundLocalError, UnicodeDecodeError, ValueError) as exc:
        raise PrfabuChallengeInvalid("provider_login_challenge_invalid") from exc
    if not isinstance(payload, dict):
        raise PrfabuChallengeInvalid("provider_login_challenge_invalid")
    expires_at = payload.get("expires_at")
    session_id = payload.get("session_id")
    if (
        payload.get("tenant_pub_id") != tenant_pub_id
        or payload.get("actor_pub_id") != actor_pub_id
        or not isinstance(expires_at, int | float)
        or expires_at < time.time()
        or not isinstance(session_id, str)
        or not 1 <= len(session_id) <= 256
    ):
        raise PrfabuChallengeInvalid("provider_login_challenge_invalid")
    return session_id


def _balance(payload: dict[str, Any]) -> Decimal | None:
    data = payload.get("data")
    value = data.get("money") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return amount if Decimal("0") <= amount <= Decimal("1000000000") else None


def create_prfabu_captcha(
    *,
    tenant_pub_id: str,
    actor_pub_id: str,
) -> PrfabuCaptchaChallenge:
    try:
        with _new_prfabu_client() as client:
            response = client.get(f"/captcha.html?seed=0.{secrets.randbelow(1_000_000_000)}")
            response.raise_for_status()
            image = response.content
            session_id = _client_prfabu_session(client)
    except (httpx.HTTPError, ValueError) as exc:
        raise PrfabuAuthUnavailable("provider_login_captcha_unavailable") from exc
    if (
        not session_id
        or not image.startswith(_PNG_SIGNATURE)
        or not 1 <= len(image) <= _MAX_CAPTCHA_BYTES
    ):
        raise PrfabuAuthUnavailable("provider_login_captcha_invalid")
    challenge_id = secrets.token_urlsafe(24)
    _write_challenge(
        challenge_id,
        {
            "tenant_pub_id": tenant_pub_id,
            "actor_pub_id": actor_pub_id,
            "session_id": session_id,
            "expires_at": time.time() + _CHALLENGE_TTL_SECONDS,
        },
    )
    return PrfabuCaptchaChallenge(
        challenge_id=challenge_id,
        image_base64=base64.b64encode(image).decode("ascii"),
    )


def login_prfabu(
    *,
    challenge_id: str,
    tenant_pub_id: str,
    actor_pub_id: str,
    account: str,
    password: str,
    captcha: str,
) -> PrfabuSessionState:
    try:
        with _prfabu_session_lock():
            session_id = _consume_challenge(
                challenge_id,
                tenant_pub_id=tenant_pub_id,
                actor_pub_id=actor_pub_id,
            )
            with _new_prfabu_client(session_id) as client:
                response = client.post(
                    "/",
                    data={"username": account, "password": password, "captcha": captcha},
                )
                response.raise_for_status()
                payload = _json_object(response)
                message = _text(payload.get("msg")) or _text(payload.get("message"))
                if payload.get("code") != 200:
                    return PrfabuSessionState(
                        status="rejected",
                        message=message or "账号、密码或验证码不正确",
                    )
                verification = client.post("/index/user/wallet.html", data={})
                verification.raise_for_status()
                verified_payload = _json_object(verification)
                verified_message = _text(verified_payload.get("msg")) or _text(
                    verified_payload.get("message")
                )
                if verified_payload.get("code") != 200:
                    return PrfabuSessionState(
                        status="rejected",
                        message=verified_message or "供应商登录态验证失败",
                    )
                if not _persist_prfabu_session(client):
                    raise PrfabuAuthUnavailable("provider_login_session_persist_failed")
                return PrfabuSessionState(
                    status="ready",
                    message="prfabu 登录成功，会话已安全保存",
                    balance=_balance(verified_payload),
                )
    except PrfabuChallengeInvalid:
        raise
    except OSError as exc:
        raise PrfabuAuthUnavailable("provider_login_storage_unavailable") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise PrfabuAuthUnavailable("provider_login_request_failed") from exc


def prfabu_session_state() -> PrfabuSessionState:
    session_id = _load_prfabu_session()
    if not session_id:
        return PrfabuSessionState("missing", "尚未配置 prfabu 登录会话")
    try:
        with _prfabu_session_lock():
            with _new_prfabu_client(session_id) as client:
                response = client.post("/index/user/wallet.html", data={})
                response.raise_for_status()
                payload = _json_object(response)
                message = _text(payload.get("msg")) or _text(payload.get("message"))
                if payload.get("code") != 200:
                    if "登录" in message or "会话" in message:
                        return PrfabuSessionState("expired", message or "prfabu 会话已失效")
                    return PrfabuSessionState("unavailable", message or "供应商状态读取失败")
                if not _persist_prfabu_session(client):
                    return PrfabuSessionState("unavailable", "会话有效，但更新保存失败")
                return PrfabuSessionState(
                    "ready",
                    "prfabu 会话有效",
                    balance=_balance(payload),
                )
    except (httpx.HTTPError, OSError, ValueError):
        return PrfabuSessionState("unavailable", "暂时无法连接 prfabu")
