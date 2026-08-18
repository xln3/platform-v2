from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..config import get_settings

_PRFABU_BASE = "https://www.prfabu.com"
_PRFABU_SESSION_FILE = "prfabu_session.txt"
_PRFABU_SESSION_LOCK_FILE = ".prfabu_session.lock"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderSubmission:
    provider: str
    catalog_type: str
    provider_media_id: str
    media_name: str
    title: str
    content_html: str
    customer_name: str
    release_time: date | None


@dataclass(frozen=True, slots=True)
class ProviderResult:
    status: str
    message: str
    external_order_id: str = ""
    public_url: str = ""


class PostingProvider(Protocol):
    def submit(self, submission: ProviderSubmission) -> ProviderResult: ...

    def refresh(
        self,
        *,
        catalog_type: str,
        external_order_id: str,
        media_name: str,
        title: str,
    ) -> ProviderResult | None: ...


def _datasets_dir() -> Path:
    configured = get_settings().datasets_dir
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / ".datasets"


def _load_prfabu_session() -> str:
    path = _datasets_dir() / _PRFABU_SESSION_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.removeprefix("#HttpOnly_").split("\t")
        if len(parts) >= 7 and parts[5] == "PHPSESSID" and parts[6]:
            return parts[6]
    return ""


def _client_prfabu_session(client: httpx.Client) -> str:
    session_id = ""
    for cookie in client.cookies.jar:
        if cookie.name == "PHPSESSID" and cookie.value:
            session_id = cookie.value
    return session_id


def _new_prfabu_client(session_id: str = "") -> httpx.Client:
    cookies = httpx.Cookies()
    if session_id:
        cookies.set("PHPSESSID", session_id, domain="www.prfabu.com", path="/")
    return httpx.Client(
        base_url=_PRFABU_BASE,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        },
        cookies=cookies,
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    )


def _persist_prfabu_session(client: httpx.Client) -> bool:
    """Atomically persist the cookie rotated by an authenticated provider response."""

    session_id = _client_prfabu_session(client)
    if not session_id:
        return False
    path = _datasets_dir() / _PRFABU_SESSION_FILE
    temporary_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(
                "# Netscape HTTP Cookie File\n"
                "#HttpOnly_.prfabu.com\tTRUE\t/\tTRUE\t0\tPHPSESSID\t"
                f"{session_id}\n"
            )
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
        return True
    except OSError as exc:
        logger.warning("prfabu_session_persist_failed", extra={"error_type": type(exc).__name__})
        return False
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


@contextmanager
def _prfabu_session_lock() -> Iterator[None]:
    """Serialize provider calls across API workers because each call may rotate the cookie."""

    path = _datasets_dir() / _PRFABU_SESSION_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any, maximum: int = 500) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def _external_id(payload: dict[str, Any]) -> str:
    candidates: list[Any] = [
        payload.get("order_no"),
        payload.get("order_id"),
        payload.get("id"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("order_no"), data.get("order_id"), data.get("id")])
    for value in candidates:
        if isinstance(value, str | int) and 0 < len(str(value)) <= 160:
            return str(value)
    return ""


def _classify_failure(code: int, message: str) -> ProviderResult:
    if "余额不足" in message:
        return ProviderResult("balance_insufficient", message)
    if "登录" in message or "会话" in message:
        return ProviderResult("provider_session_expired", message)
    if code in {222, 223, 224}:
        return ProviderResult("provider_confirmation_required", message)
    return ProviderResult("rejected", message or f"provider_code_{code}")


def _map_order_status(value: Any, public_url: str) -> str:
    text = str(value or "").strip().lower()
    if public_url or any(token in text for token in ("已发布", "已出稿", "完成", "published")):
        return "published"
    if any(token in text for token in ("审核", "处理中", "review")):
        return "reviewing"
    if any(token in text for token in ("拒绝", "退稿", "失败", "rejected", "failed")):
        return "rejected"
    return "submitted"


class PrfabuProvider:
    def _client(self) -> httpx.Client | None:
        session_id = _load_prfabu_session()
        if not session_id:
            return None
        return _new_prfabu_client(session_id)

    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        if not submission.provider_media_id.isdigit():
            return ProviderResult("unsupported_provider", "当前目录快照缺少 prfabu 媒体 ID")
        try:
            with _prfabu_session_lock():
                client = self._client()
                if client is None:
                    return ProviderResult("provider_session_expired", "prfabu 会话文件缺失")
                media_type = 1 if submission.catalog_type == "news" else 2
                try:
                    response = client.post(
                        f"/index/media/article.html?type={media_type}",
                        data={
                            "title": submission.title,
                            "content": submission.content_html,
                            "medias": submission.provider_media_id,
                            "customer": submission.customer_name,
                            "remark": "",
                            "release_time": (
                                submission.release_time.isoformat()
                                if submission.release_time
                                else ""
                            ),
                        },
                    )
                    response.raise_for_status()
                    payload = _json_object(response)
                    code = payload.get("code")
                    message = _text(payload.get("msg")) or _text(payload.get("message"))
                    if code != 200:
                        result = _classify_failure(
                            code if isinstance(code, int) else -1,
                            message,
                        )
                        if result.status != "provider_session_expired":
                            _persist_prfabu_session(client)
                        return result
                    persisted = _persist_prfabu_session(client)
                    result_message = message or "已提交至 prfabu"
                    if not persisted:
                        result_message += "；会话轮换保存失败，后续提交前请更新会话"
                    return ProviderResult(
                        "submitted",
                        result_message,
                        external_order_id=_external_id(payload),
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    return ProviderResult("failed", f"prfabu 请求失败：{type(exc).__name__}")
                finally:
                    client.close()
        except OSError as exc:
            return ProviderResult("failed", f"prfabu 会话锁失败：{type(exc).__name__}")

    def refresh(
        self,
        *,
        catalog_type: str,
        external_order_id: str,
        media_name: str,
        title: str,
    ) -> ProviderResult | None:
        try:
            with _prfabu_session_lock():
                client = self._client()
                if client is None:
                    return ProviderResult("provider_session_expired", "prfabu 会话文件缺失")
                media_type = 1 if catalog_type == "news" else 2
                try:
                    response = client.post(
                        f"/index/media/order.html?type={media_type}",
                        data={"page": 1, "limit": 100, "title": title},
                    )
                    response.raise_for_status()
                    payload = _json_object(response)
                    if payload.get("code") != 200:
                        message = _text(payload.get("msg")) or _text(payload.get("message"))
                        raw_code = payload.get("code")
                        code = raw_code if isinstance(raw_code, int) else -1
                        result = _classify_failure(code, message)
                        if result.status != "provider_session_expired":
                            _persist_prfabu_session(client)
                        return result
                    _persist_prfabu_session(client)
                    rows = payload.get("data")
                    if not isinstance(rows, list):
                        return None
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row_id = _external_id(row)
                        row_title = _text(row.get("title"), 300)
                        row_media = _text(row.get("media_name"), 500)
                        if external_order_id:
                            matches = row_id == external_order_id
                        else:
                            matches = row_title == title and (
                                not row_media or row_media == media_name
                            )
                        if not matches:
                            continue
                        public_url = _text(row.get("url") or row.get("public_url"), 1_000)
                        status = _map_order_status(
                            row.get("status") or row.get("status_str"),
                            public_url,
                        )
                        return ProviderResult(
                            status,
                            _text(row.get("status_str") or row.get("status"))
                            or "供应商状态已同步",
                            external_order_id=row_id,
                            public_url=public_url,
                        )
                    return None
                except (httpx.HTTPError, ValueError) as exc:
                    return ProviderResult(
                        "failed",
                        f"prfabu 状态读取失败：{type(exc).__name__}",
                    )
                finally:
                    client.close()
        except OSError as exc:
            return ProviderResult("failed", f"prfabu 会话锁失败：{type(exc).__name__}")


class UnsupportedProvider:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        del submission
        return ProviderResult(
            "unsupported_provider",
            f"{self.provider} 目前只有比价数据，自动下单适配器尚未接入",
        )

    def refresh(
        self,
        *,
        catalog_type: str,
        external_order_id: str,
        media_name: str,
        title: str,
    ) -> ProviderResult | None:
        del catalog_type, external_order_id, media_name, title
        return None


def provider_for(name: str) -> PostingProvider:
    return PrfabuProvider() if name == "prfabu" else UnsupportedProvider(name)
