"""Small HTTP SDK independent of GEO business modules."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

import httpx

from .contracts import RuntimeRequest


class KnowledgeClientError(RuntimeError):
    pass


class KnowledgeHttpClient:
    """Call the stable API while leaving authentication headers to the host system."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("knowledge_client_base_url_invalid")
        self.base_url = normalized
        self.headers = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def resolve(self, request: RuntimeRequest) -> dict[str, Any]:
        body = asdict(request)
        body.pop("tenant", None)
        body["policy"] = request.policy.value
        body["items"] = list(request.items)
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout_seconds,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.post("/api/v2/knowledge/v1/runtime/resolve", json=body)
        except httpx.TimeoutException as exc:
            raise KnowledgeClientError("knowledge_service_timeout") from exc
        except httpx.HTTPError as exc:
            raise KnowledgeClientError("knowledge_service_transport_error") from exc
        if response.status_code != 200:
            code = "knowledge_service_error"
            try:
                document = response.json()
                candidate = document.get("error", {}).get("code")
                if isinstance(candidate, str) and candidate.isascii():
                    code = candidate[:120]
            except (TypeError, ValueError):
                pass
            raise KnowledgeClientError(code)
        if len(response.content) > 4 * 1024 * 1024:
            raise KnowledgeClientError("knowledge_response_too_large")
        try:
            document = response.json()
        except ValueError as exc:
            raise KnowledgeClientError("knowledge_response_invalid_json") from exc
        if not isinstance(document, dict) or not isinstance(document.get("decisions"), list):
            raise KnowledgeClientError("knowledge_response_invalid_shape")
        return document

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.get("/api/v2/knowledge/v1/health")
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KnowledgeClientError("knowledge_health_unavailable") from exc
        if not isinstance(document, dict):
            raise KnowledgeClientError("knowledge_health_invalid_shape")
        return document


__all__ = ["KnowledgeClientError", "KnowledgeHttpClient"]
