from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .config import FeishuBotConfig, read_secret_file

_TOKEN_INVALID_CODES = {99991661, 99991663, 99991664}
_RETRYABLE_BUSINESS_CODES = {99991400, 99991401, 99991402, 99991403}
_LOG_ID_HEADERS = ("x-tt-logid", "x-lark-request-id", "x-request-id")
_SAFE_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class FeishuApiError(RuntimeError):
    def __init__(
        self,
        marker: str,
        *,
        code: int | None = None,
        retryable: bool = False,
        request_log_id: str | None = None,
    ) -> None:
        super().__init__(marker)
        self.marker = marker
        self.code = code
        self.retryable = retryable
        self.request_log_id = request_log_id


@dataclass(frozen=True)
class FeishuResult:
    data: dict[str, Any]
    request_log_id: str | None


class FeishuAppClient:
    """Minimal custom-app OpenAPI client with in-memory token single-flight.

    The client never reads proxy environment variables.  Error objects contain
    only stable classes/codes and a provider diagnostic ID, never response
    bodies, request payloads, access tokens, or the App Secret.
    """

    def __init__(
        self,
        config: FeishuBotConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        config.validate_sender()
        self._config = config
        self._app_secret = read_secret_file(config.app_secret_file, label="feishu_app_secret")
        self._client = httpx.Client(
            base_url=config.api_base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
            headers={"User-Agent": "geo-platform-v2-feishu-bot/1"},
        )
        self._max_attempts = max(1, max_attempts)
        self._clock = clock
        self._sleep = sleep
        self._random = random_value
        self._token_lock = threading.Lock()
        self._token = ""
        self._token_refresh_at = 0.0

    def close(self) -> None:
        self._client.close()

    def _log_id(self, response: httpx.Response) -> str | None:
        for name in _LOG_ID_HEADERS:
            value = response.headers.get(name)
            if value:
                candidate = str(value)[:200]
                return candidate if _SAFE_PROVIDER_ID_RE.fullmatch(candidate) else None
        return None

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise FeishuApiError(
                "feishu_response_json_invalid",
                retryable=response.status_code >= 500,
                request_log_id=self._log_id(response),
            ) from error
        if not isinstance(payload, dict):
            raise FeishuApiError(
                "feishu_response_json_invalid",
                request_log_id=self._log_id(response),
            )
        return payload

    def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        retry_after = 0.0
        if response is not None:
            try:
                retry_after = float(response.headers.get("retry-after", "0"))
            except ValueError:
                retry_after = 0.0
        delay = retry_after if 0 < retry_after <= 10 else min(4.0, 0.25 * (2**attempt))
        self._sleep(delay + self._random() * min(0.25, delay))

    def _refresh_token(self) -> str:
        last_error: FeishuApiError | None = None
        for attempt in range(self._max_attempts):
            response: httpx.Response | None = None
            try:
                response = self._client.post(
                    "/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self._config.app_id, "app_secret": self._app_secret},
                )
                log_id = self._log_id(response)
                if response.status_code < 200 or response.status_code >= 300:
                    retryable = response.status_code in {429, 500, 502, 503, 504}
                    raise FeishuApiError(
                        "feishu_token_http_error",
                        code=response.status_code,
                        retryable=retryable,
                        request_log_id=log_id,
                    )
                payload = self._decode(response)
                code = payload.get("code")
                if not isinstance(code, int) or code != 0:
                    raise FeishuApiError(
                        "feishu_token_business_error",
                        code=code if isinstance(code, int) else None,
                        retryable=isinstance(code, int) and code in _RETRYABLE_BUSINESS_CODES,
                        request_log_id=log_id,
                    )
                token = payload.get("tenant_access_token")
                expire = payload.get("expire")
                if not isinstance(token, str) or not token or not isinstance(expire, int):
                    raise FeishuApiError("feishu_token_shape_invalid", request_log_id=log_id)
                # Refresh at least 60 seconds early, and at 80% of very short test TTLs.
                margin = min(300.0, max(1.0, float(expire) * 0.2))
                self._token = token
                self._token_refresh_at = self._clock() + max(1.0, float(expire) - margin)
                return token
            except httpx.HTTPError as error:
                last_error = FeishuApiError("feishu_token_transport_error", retryable=True)
                if attempt + 1 >= self._max_attempts:
                    raise last_error from error
            except FeishuApiError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._max_attempts:
                    raise
            self._backoff(attempt, response)
        assert last_error is not None
        raise last_error

    def tenant_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and self._clock() < self._token_refresh_at:
            return self._token
        # Holding this lock across the request deliberately gives a single-flight
        # refresh; waiters recheck the cache once the winner returns.
        with self._token_lock:
            if not force_refresh and self._token and self._clock() < self._token_refresh_at:
                return self._token
            return self._refresh_token()

    def _invalidate_token(self, token: str) -> None:
        # Only clear the token that actually failed.  If another request has
        # already refreshed it, preserve the winner and let all waiters reuse it.
        with self._token_lock:
            if self._token == token:
                self._token = ""
                self._token_refresh_at = 0.0

    def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any],
        query: Mapping[str, str] | None = None,
    ) -> FeishuResult:
        refreshed = False
        last_error: FeishuApiError | None = None
        for attempt in range(self._max_attempts):
            response: httpx.Response | None = None
            try:
                token = self.tenant_access_token()
                response = self._client.request(
                    method,
                    path,
                    params=query,
                    json=dict(payload),
                    headers={"Authorization": f"Bearer {token}"},
                )
                log_id = self._log_id(response)
                if response.status_code < 200 or response.status_code >= 300:
                    retryable = response.status_code in {429, 500, 502, 503, 504}
                    raise FeishuApiError(
                        "feishu_api_http_error",
                        code=response.status_code,
                        retryable=retryable,
                        request_log_id=log_id,
                    )
                body = self._decode(response)
                code = body.get("code")
                if isinstance(code, int) and code in _TOKEN_INVALID_CODES and not refreshed:
                    self._invalidate_token(token)
                    refreshed = True
                    if attempt + 1 >= self._max_attempts:
                        raise FeishuApiError(
                            "feishu_api_business_error",
                            code=code,
                            retryable=True,
                            request_log_id=log_id,
                        )
                    continue
                if not isinstance(code, int) or code != 0:
                    raise FeishuApiError(
                        "feishu_api_business_error",
                        code=code if isinstance(code, int) else None,
                        retryable=isinstance(code, int) and code in _RETRYABLE_BUSINESS_CODES,
                        request_log_id=log_id,
                    )
                data = body.get("data", {})
                if not isinstance(data, dict):
                    raise FeishuApiError("feishu_api_shape_invalid", request_log_id=log_id)
                return FeishuResult(data=data, request_log_id=log_id)
            except httpx.HTTPError as error:
                last_error = FeishuApiError("feishu_api_transport_error", retryable=True)
                if attempt + 1 >= self._max_attempts:
                    raise last_error from error
            except FeishuApiError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._max_attempts:
                    raise
            self._backoff(attempt, response)
        assert last_error is not None
        raise last_error

    def send_card(
        self,
        *,
        chat_id: str,
        card: dict[str, Any],
        command_uuid: uuid.UUID,
    ) -> FeishuResult:
        result = self._authorized_request(
            "POST",
            "/open-apis/im/v1/messages",
            query={"receive_id_type": "chat_id"},
            payload={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
                "uuid": str(command_uuid),
            },
        )
        message_id = result.data.get("message_id")
        if not isinstance(message_id, str) or _SAFE_PROVIDER_ID_RE.fullmatch(message_id) is None:
            raise FeishuApiError(
                "feishu_send_message_id_missing", request_log_id=result.request_log_id
            )
        return result

    def update_card(self, *, message_id: str, card: dict[str, Any]) -> FeishuResult:
        if _SAFE_PROVIDER_ID_RE.fullmatch(message_id) is None:
            raise FeishuApiError("feishu_update_message_id_invalid")
        return self._authorized_request(
            "PATCH",
            f"/open-apis/im/v1/messages/{message_id}",
            payload={"content": json.dumps(card, ensure_ascii=False, separators=(",", ":"))},
        )

    def send_text(self, *, chat_id: str, text: str, command_uuid: uuid.UUID) -> FeishuResult:
        return self._authorized_request(
            "POST",
            "/open-apis/im/v1/messages",
            query={"receive_id_type": "chat_id"},
            payload={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": str(command_uuid),
            },
        )
