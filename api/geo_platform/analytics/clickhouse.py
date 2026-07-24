from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

import httpx

_FORBIDDEN = frozenset(
    {
        "cookie",
        "authorization",
        "token",
        "otp",
        "profile_path",
        "profile_object_key",
        "device_key",
        "proxy_password",
    }
)
_SAFE_OPAQUE_FIELDS = frozenset({"trace_token"})


class ClickHouseWriter:
    def __init__(self, *, endpoint: str, user: str, password: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.auth = (user, password)

    def initialize(self, ddl: str) -> None:
        self._post(ddl, params={"multiquery": "1"})

    def insert_json_each_row(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int:
        materialized = list(rows)
        for row in materialized:
            self._assert_safe(row)
        if not materialized:
            return 0
        body = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n"
            for row in materialized
        )
        self._post(f"INSERT INTO {table} FORMAT JSONEachRow\n{body}")
        return len(materialized)

    def count_event(self, table: str, event_id: str) -> int:
        safe_event = event_id.replace("'", "''")
        response = self._post(
            f"SELECT count() FROM {table} FINAL WHERE event_id = '{safe_event}' FORMAT TabSeparated"
        )
        return int(response.text.strip())

    def count_trace(self, table: str, trace_token: str) -> int:
        safe_trace = trace_token.replace("'", "''")
        response = self._post(
            f"SELECT count() FROM {table} FINAL "
            f"WHERE trace_token = '{safe_trace}' FORMAT TabSeparated"
        )
        return int(response.text.strip())

    def _post(self, query: str, params: dict[str, str] | None = None) -> httpx.Response:
        response = httpx.post(
            self.endpoint,
            params=params,
            content=query,
            auth=self.auth,
            timeout=20,
            trust_env=False,
        )
        response.raise_for_status()
        return response

    def _assert_safe(self, value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized in _SAFE_OPAQUE_FIELDS:
                    if not isinstance(child, str) or len(child) != 64:
                        raise ValueError(f"invalid controlled opaque field: {path}{key}")
                    continue
                if normalized in _FORBIDDEN or any(part in normalized for part in _FORBIDDEN):
                    raise ValueError(f"secret-bearing ClickHouse field rejected: {path}{key}")
                self._assert_safe(child, f"{path}{key}.")
        elif isinstance(value, list | tuple):
            for child in value:
                self._assert_safe(child, path)
        elif isinstance(value, str):
            lowered = value.lower()
            if (
                "authorization: bearer " in lowered
                or "cookie:" in lowered
                or "otpauth://" in lowered
            ):
                raise ValueError(f"secret-bearing ClickHouse value rejected: {path}")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    return str(value)
