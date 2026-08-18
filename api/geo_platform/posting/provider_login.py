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
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from .catalog import PROVIDERS, ProviderName
from .provider_credentials import (
    ProviderCredentialNotConfigured,
    ProviderCredentialStore,
    ProviderCredentialUnavailable,
)

_CHALLENGE_TTL_SECONDS = 5 * 60
_MAX_CHALLENGE_BYTES = 32 * 1024
_MAX_CAPTCHA_BYTES = 256 * 1024
_CHALLENGE_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_IMAGE_SIGNATURES = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/gif", b"GIF8"),
)


class ProviderLoginUnavailable(RuntimeError):
    """The provider login service or secure local challenge storage is unavailable."""


class ProviderLoginChallengeInvalid(ValueError):
    """The one-time login challenge is invalid, expired, or belongs to another actor."""


class ProviderLoginInteractiveRequired(RuntimeError):
    """The provider requires a browser-native challenge that cannot be proxied as an image."""


ProviderSessionStatus = Literal[
    "not_configured",
    "needs_login",
    "ready",
    "expired",
    "rejected",
    "verification_required",
    "interactive_required",
    "unavailable",
]
_SESSION_STATUSES = frozenset(
    {
        "not_configured",
        "needs_login",
        "ready",
        "expired",
        "rejected",
        "verification_required",
        "interactive_required",
        "unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderCaptchaChallenge:
    provider: ProviderName
    challenge_id: str
    image_base64: str
    image_mime_type: Literal["image/png", "image/jpeg", "image/gif"]
    expires_in_seconds: int = _CHALLENGE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class ProviderLoginState:
    provider: ProviderName
    status: ProviderSessionStatus
    message: str
    balance: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _ProviderSpec:
    provider: ProviderName
    base_url: str
    captcha_path: str | None
    login_path: str
    captcha_field: str
    success_code: int
    login_kind: Literal["standard", "pinda"] = "standard"


_SPECS: dict[ProviderName, _ProviderSpec] = {
    "prfabu": _ProviderSpec(
        "prfabu", "https://www.prfabu.com", "/captcha.html", "/", "captcha", 200
    ),
    "toumeiw": _ProviderSpec(
        "toumeiw",
        "https://toumeiw.cn",
        "/web/Login/verify.html",
        "/web/login/login.html",
        "captcha",
        200,
    ),
    "mtpfw": _ProviderSpec("mtpfw", "https://mtpfw.cn", "/captcha.html", "/web/", "captcha", 200),
    "meititejia": _ProviderSpec(
        "meititejia",
        "https://www.meititejia.com",
        "/web/Login/verify.html",
        "/web/login/login.html",
        "captcha",
        200,
    ),
    # 媒介盒子使用腾讯交互式验证码 ticket/randstr，不能伪装成静态图片验证码。
    "meijiehezi": _ProviderSpec(
        "meijiehezi",
        "https://vip.meijiehezi.com",
        None,
        "/index/login/login_by_username_p.html",
        "captcha",
        200,
    ),
    "pinda": _ProviderSpec(
        "pinda",
        "https://fagao.pindarpr.com",
        "/home_portal/validCode",
        "/home_portal/checklogin",
        "code",
        2,
        "pinda",
    ),
}


def _provider(value: str) -> ProviderName:
    if value not in PROVIDERS:
        raise ProviderLoginUnavailable("provider_login_provider_invalid")
    return cast(ProviderName, value)


def _datasets_dir() -> Path:
    configured = get_settings().datasets_dir
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / ".datasets"


def _challenge_directory(provider: ProviderName) -> Path:
    directory = _datasets_dir() / ".provider-login" / provider
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise ProviderLoginUnavailable("provider_login_storage_unavailable") from exc
    return directory


def _challenge_path(provider: ProviderName, challenge_id: str) -> Path:
    if _CHALLENGE_ID.fullmatch(challenge_id) is None:
        raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid")
    return _challenge_directory(provider) / f"{challenge_id}.json"


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


def _write_challenge(
    provider: ProviderName,
    challenge_id: str,
    *,
    tenant_pub_id: str,
    actor_pub_id: str,
    payload: dict[str, Any],
) -> None:
    directory = _challenge_directory(provider)
    _remove_expired_challenges(directory, time.time())
    destination = _challenge_path(provider, challenge_id)
    temporary_path: str | None = None
    try:
        record = ProviderCredentialStore().seal_login_challenge(
            tenant_pub_id=tenant_pub_id,
            provider=provider,
            actor_pub_id=actor_pub_id,
            challenge_id=challenge_id,
            payload=payload,
        )
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{challenge_id}.", suffix=".tmp", dir=directory
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(record, temporary, ensure_ascii=True, separators=(",", ":"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except ProviderCredentialUnavailable as exc:
        raise ProviderLoginUnavailable(str(exc)) from exc
    except OSError as exc:
        raise ProviderLoginUnavailable("provider_login_storage_unavailable") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _consume_challenge(
    provider: ProviderName,
    challenge_id: str,
    *,
    tenant_pub_id: str,
    actor_pub_id: str,
) -> dict[str, str]:
    path = _challenge_path(provider, challenge_id)
    claimed = path.with_name(f".{challenge_id}.{secrets.token_urlsafe(8)}.claimed")
    try:
        # Atomic rename is the one-time-use boundary across API workers. Only one
        # concurrent request can claim a challenge; every other request fails.
        os.replace(path, claimed)
        metadata = claimed.stat()
        if metadata.st_size <= 0 or metadata.st_size > _MAX_CHALLENGE_BYTES:
            raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid")
        raw = claimed.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid") from exc
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    try:
        record = json.loads(raw)
        if not isinstance(record, dict):
            raise ValueError("challenge record is not an object")
        payload = ProviderCredentialStore().open_login_challenge(
            tenant_pub_id=tenant_pub_id,
            provider=provider,
            actor_pub_id=actor_pub_id,
            challenge_id=challenge_id,
            record=record,
        )
    except (
        UnboundLocalError,
        UnicodeDecodeError,
        ValueError,
        ProviderCredentialUnavailable,
    ) as exc:
        raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid") from exc
    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    expires_at = payload.get("expires_at") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != provider
        or not isinstance(expires_at, int | float)
        or expires_at < time.time()
        or not isinstance(cookies, dict)
    ):
        raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid")
    safe_cookies: dict[str, str] = {}
    for name, value in cookies.items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) is None
            or not isinstance(value, str)
            or not 1 <= len(value) <= 4096
        ):
            raise ProviderLoginChallengeInvalid("provider_login_challenge_invalid")
        safe_cookies[name] = value
    return safe_cookies


def _new_client(spec: _ProviderSpec, cookies: dict[str, str] | None = None) -> httpx.Client:
    jar = httpx.Cookies()
    hostname = urlparse(spec.base_url).hostname
    assert hostname is not None
    for name, value in (cookies or {}).items():
        jar.set(name, value, domain=hostname, path="/")
    return httpx.Client(
        base_url=spec.base_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        },
        cookies=jar,
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    )


def _cookies(client: httpx.Client) -> dict[str, str]:
    result: dict[str, str] = {}
    for cookie in client.cookies.jar:
        if cookie.name and cookie.value:
            result[cookie.name] = cookie.value
    return result


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _message(payload: dict[str, Any]) -> str:
    for key in ("msg", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value[:500]
    return ""


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


def _image_type(image: bytes) -> Literal["image/png", "image/jpeg", "image/gif"] | None:
    for mime_type, signature in _IMAGE_SIGNATURES:
        if image.startswith(signature):
            return cast(Literal["image/png", "image/jpeg", "image/gif"], mime_type)
    return None


def create_provider_captcha(
    *,
    provider: str,
    tenant_pub_id: str,
    actor_pub_id: str,
) -> ProviderCaptchaChallenge:
    safe_provider = _provider(provider)
    spec = _SPECS[safe_provider]
    if spec.captcha_path is None:
        raise ProviderLoginInteractiveRequired("provider_login_interactive_verification_required")
    try:
        ProviderCredentialStore().load(tenant_pub_id=tenant_pub_id, provider=safe_provider)
        with _new_client(spec) as client:
            separator = "&" if "?" in spec.captcha_path else "?"
            response = client.get(
                f"{spec.captcha_path}{separator}seed=0.{secrets.randbelow(1_000_000_000)}"
            )
            response.raise_for_status()
            image = response.content
            cookies = _cookies(client)
    except ProviderCredentialNotConfigured as exc:
        raise ProviderLoginUnavailable("provider_credential_not_configured") from exc
    except ProviderCredentialUnavailable as exc:
        raise ProviderLoginUnavailable(str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderLoginUnavailable("provider_login_captcha_unavailable") from exc
    mime_type = _image_type(image)
    if mime_type is None or not cookies or not 1 <= len(image) <= _MAX_CAPTCHA_BYTES:
        raise ProviderLoginUnavailable("provider_login_captcha_invalid")
    challenge_id = secrets.token_urlsafe(24)
    _write_challenge(
        safe_provider,
        challenge_id,
        tenant_pub_id=tenant_pub_id,
        actor_pub_id=actor_pub_id,
        payload={
            "provider": safe_provider,
            "cookies": cookies,
            "expires_at": time.time() + _CHALLENGE_TTL_SECONDS,
        },
    )
    return ProviderCaptchaChallenge(
        provider=safe_provider,
        challenge_id=challenge_id,
        image_base64=base64.b64encode(image).decode("ascii"),
        image_mime_type=mime_type,
    )


def _login_request(
    client: httpx.Client,
    spec: _ProviderSpec,
    *,
    account: str,
    password: str,
    captcha: str,
) -> httpx.Response:
    if spec.login_kind == "pinda":
        return client.post(
            spec.login_path,
            headers={"x-image-code": captcha},
            json={
                "account": account,
                "password": password,
                "code": captcha,
                "rememberMe": True,
            },
        )
    return client.post(
        spec.login_path,
        data={"type": 1, "username": account, "password": password, spec.captcha_field: captcha},
    )


def login_provider(
    *,
    provider: str,
    challenge_id: str,
    tenant_pub_id: str,
    actor_pub_id: str,
    captcha: str,
) -> ProviderLoginState:
    safe_provider = _provider(provider)
    spec = _SPECS[safe_provider]
    if spec.captcha_path is None:
        raise ProviderLoginInteractiveRequired("provider_login_interactive_verification_required")
    store = ProviderCredentialStore()
    try:
        account = store.load(tenant_pub_id=tenant_pub_id, provider=safe_provider)
        cookies = _consume_challenge(
            safe_provider,
            challenge_id,
            tenant_pub_id=tenant_pub_id,
            actor_pub_id=actor_pub_id,
        )
        with _new_client(spec, cookies) as client:
            response = _login_request(
                client,
                spec,
                account=account.account,
                password=account.password,
                captcha=captcha,
            )
            response.raise_for_status()
            payload = _json_object(response)
            code = payload.get("code")
            message = _message(payload)
            if code in {222, 223, 224}:
                store.update_session(
                    tenant_pub_id=tenant_pub_id,
                    provider=safe_provider,
                    cookies={},
                    status="verification_required",
                    message=message or "供应商要求短信或设备二次验证",
                )
                return ProviderLoginState(
                    safe_provider,
                    "verification_required",
                    message or "供应商要求短信或设备二次验证",
                )
            if code != spec.success_code:
                store.update_session(
                    tenant_pub_id=tenant_pub_id,
                    provider=safe_provider,
                    cookies={},
                    status="rejected",
                    message=message or "账号、密码或验证码不正确",
                )
                return ProviderLoginState(
                    safe_provider,
                    "rejected",
                    message or "账号、密码或验证码不正确",
                )
            balance: Decimal | None = None
            if safe_provider == "prfabu":
                verification = client.post("/index/user/wallet.html", data={})
                verification.raise_for_status()
                verified_payload = _json_object(verification)
                if verified_payload.get("code") != 200:
                    verified_message = _message(verified_payload) or "供应商登录态验证失败"
                    store.update_session(
                        tenant_pub_id=tenant_pub_id,
                        provider=safe_provider,
                        cookies={},
                        status="rejected",
                        message=verified_message,
                    )
                    return ProviderLoginState(
                        safe_provider,
                        "rejected",
                        verified_message,
                    )
                balance = _balance(verified_payload)
            session_cookies = _cookies(client)
            if not session_cookies:
                raise ProviderLoginUnavailable("provider_login_session_missing")
            store.update_session(
                tenant_pub_id=tenant_pub_id,
                provider=safe_provider,
                cookies=session_cookies,
                status="ready",
                message="登录成功，会话由系统自动维护",
            )
            return ProviderLoginState(
                safe_provider,
                "ready",
                "登录成功，会话由系统自动维护",
                balance,
            )
    except ProviderLoginChallengeInvalid:
        raise
    except ProviderCredentialNotConfigured as exc:
        raise ProviderLoginUnavailable("provider_credential_not_configured") from exc
    except ProviderCredentialUnavailable as exc:
        raise ProviderLoginUnavailable(str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderLoginUnavailable("provider_login_request_failed") from exc


def provider_session_state(*, provider: str, tenant_pub_id: str) -> ProviderLoginState:
    safe_provider = _provider(provider)
    store = ProviderCredentialStore()
    try:
        account = store.load(tenant_pub_id=tenant_pub_id, provider=safe_provider)
    except ProviderCredentialNotConfigured:
        return ProviderLoginState(safe_provider, "not_configured", "尚未配置账号凭据")
    except ProviderCredentialUnavailable:
        return ProviderLoginState(safe_provider, "unavailable", "加密凭据暂不可用")
    if not account.cookies:
        status: ProviderSessionStatus = (
            "interactive_required" if _SPECS[safe_provider].captcha_path is None else "needs_login"
        )
        return ProviderLoginState(safe_provider, status, account.session_message)
    if safe_provider != "prfabu":
        if account.session_status not in _SESSION_STATUSES:
            return ProviderLoginState(safe_provider, "unavailable", "保存的登录状态无效")
        return ProviderLoginState(
            safe_provider,
            cast(ProviderSessionStatus, account.session_status),
            account.session_message,
        )
    try:
        with _new_client(_SPECS[safe_provider], account.cookies) as client:
            response = client.post("/index/user/wallet.html", data={})
            response.raise_for_status()
            payload = _json_object(response)
            message = _message(payload)
            if payload.get("code") != 200:
                live_status: ProviderSessionStatus = (
                    "expired" if "登录" in message or "会话" in message else "unavailable"
                )
                state_message = message or "供应商会话已失效"
                if live_status == "expired":
                    store.update_session(
                        tenant_pub_id=tenant_pub_id,
                        provider=safe_provider,
                        cookies={},
                        status=live_status,
                        message=state_message,
                    )
                return ProviderLoginState(safe_provider, live_status, state_message)
            store.update_session(
                tenant_pub_id=tenant_pub_id,
                provider=safe_provider,
                cookies=_cookies(client),
                status="ready",
                message="会话有效，由系统自动维护",
            )
            return ProviderLoginState(
                safe_provider,
                "ready",
                "会话有效，由系统自动维护",
                _balance(payload),
            )
    except (ProviderCredentialUnavailable, httpx.HTTPError, ValueError):
        return ProviderLoginState(safe_provider, "unavailable", "暂时无法验证供应商会话")
