"""Replaceable OpenAI-compatible structured-output model gateway."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from .contracts import GatewayResult, ModelPrompt


class GatewayError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _base_url(value: str) -> str:
    normalized = value.rstrip("/")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


class OpenAICompatibleGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        base_url_fallback: str,
        provider: str,
        model: str,
        model_version: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
        tool_handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
        max_tool_rounds: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not base_url or not model:
            raise ValueError("model_gateway_configuration_incomplete")
        if max_tool_rounds < 0 or max_tool_rounds > 10:
            raise ValueError("model_gateway_tool_rounds_invalid")
        if max_retries < 0 or max_retries > 5:
            raise ValueError("model_gateway_retries_invalid")
        self._api_key = api_key
        self._base_urls = tuple(
            dict.fromkeys(
                _base_url(value) for value in (base_url, base_url_fallback) if value.strip()
            )
        )
        self.provider = provider
        self.model = model
        self.model_version = model_version
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.tool_handlers = dict(tool_handlers or {})
        self.max_tool_rounds = max_tool_rounds
        self.transport = transport

    def infer(self, prompt: ModelPrompt) -> GatewayResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system_message},
                {"role": "user", "content": prompt.user_message},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "knowledge_decisions",
                    "strict": True,
                    "schema": prompt.output_schema,
                },
            },
        }
        if prompt.tools:
            body["tools"] = list(prompt.tools)
        started = time.perf_counter()
        last_error: Exception | None = None
        total_input_tokens = 0
        total_output_tokens = 0
        saw_input_usage = False
        saw_output_usage = False
        tool_summary: list[dict[str, Any]] = []
        allowed_tools = {
            str(function.get("name"))
            for tool in prompt.tools
            if isinstance(tool, dict)
            and isinstance((function := tool.get("function")), dict)
            and function.get("name")
        }

        for tool_round in range(self.max_tool_rounds + 1):
            response: httpx.Response | None = None
            for index, base_url in enumerate(self._base_urls):
                last_base_url = index == len(self._base_urls) - 1
                for attempt in range(self.max_retries + 1):
                    try:
                        with httpx.Client(
                            base_url=base_url,
                            timeout=self.timeout_seconds,
                            headers={"Authorization": f"Bearer {self._api_key}"},
                            trust_env=False,
                            transport=self.transport,
                        ) as client:
                            response = client.post("/chat/completions", json=body)
                    except (httpx.TimeoutException, httpx.HTTPError) as exc:
                        last_error = exc
                        response = None
                        if attempt < self.max_retries:
                            continue
                        break
                    retryable_status = response.status_code in {408, 429} or (
                        response.status_code >= 500
                    )
                    if retryable_status and attempt < self.max_retries:
                        response = None
                        continue
                    break
                if response is not None:
                    retryable_status = response.status_code in {408, 429} or (
                        response.status_code >= 500
                    )
                    if not retryable_status or last_base_url:
                        break
                    response = None
            if response is None:
                if isinstance(last_error, httpx.TimeoutException):
                    raise GatewayError("model_timeout") from last_error
                if isinstance(last_error, httpx.HTTPError):
                    raise GatewayError("model_transport_error") from last_error
                raise GatewayError(f"model_unavailable:{type(last_error).__name__}")
            if response.status_code != 200:
                raise GatewayError(f"model_upstream_{response.status_code}")
            try:
                document = response.json()
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise GatewayError("model_invalid_json") from exc
            usage = document.get("usage") if isinstance(document, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            if usage.get("prompt_tokens") is not None:
                total_input_tokens += int(usage["prompt_tokens"])
                saw_input_usage = True
            if usage.get("completion_tokens") is not None:
                total_output_tokens += int(usage["completion_tokens"])
                saw_output_usage = True
            try:
                message = document["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise GatewayError("model_invalid_shape") from exc
            if not isinstance(message, dict):
                raise GatewayError("model_invalid_shape")
            raw_calls = message.get("tool_calls")
            if raw_calls:
                if tool_round >= self.max_tool_rounds:
                    raise GatewayError("tool_round_limit")
                if not isinstance(raw_calls, list):
                    raise GatewayError("tool_call_invalid")
                body["messages"].append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": raw_calls,
                    }
                )
                for raw_call in raw_calls:
                    if not isinstance(raw_call, dict):
                        raise GatewayError("tool_call_invalid")
                    function = raw_call.get("function")
                    call_id = str(raw_call.get("id") or "")
                    if not isinstance(function, dict) or not call_id:
                        raise GatewayError("tool_call_invalid")
                    name = str(function.get("name") or "")
                    handler = self.tool_handlers.get(name)
                    if name not in allowed_tools or handler is None:
                        raise GatewayError("tool_not_allowed")
                    try:
                        arguments = json.loads(str(function.get("arguments") or "{}"))
                    except ValueError as exc:
                        raise GatewayError("tool_arguments_invalid") from exc
                    if not isinstance(arguments, dict):
                        raise GatewayError("tool_arguments_invalid")
                    tool_started = time.perf_counter()
                    try:
                        tool_result = handler(arguments)
                    except Exception as exc:  # noqa: BLE001 - tool boundary is sanitized
                        raise GatewayError("tool_failure") from exc
                    if not isinstance(tool_result, Mapping):
                        raise GatewayError("tool_result_invalid")
                    rendered = json.dumps(
                        dict(tool_result),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if len(rendered.encode()) > 64 * 1024:
                        raise GatewayError("tool_result_too_large")
                    body["messages"].append(
                        {"role": "tool", "tool_call_id": call_id, "content": rendered}
                    )
                    tool_summary.append(
                        {
                            "tool": name,
                            "status": "ok",
                            "latency_ms": int((time.perf_counter() - tool_started) * 1000),
                        }
                    )
                continue
            content = message.get("content")
            if not isinstance(content, str):
                raise GatewayError("model_invalid_json")
            try:
                payload = json.loads(content)
            except ValueError as exc:
                raise GatewayError("model_invalid_json") from exc
            if not isinstance(payload, dict):
                raise GatewayError("model_invalid_shape")
            return GatewayResult(
                payload=payload,
                provider=self.provider,
                model=self.model,
                model_version=self.model_version,
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=total_input_tokens if saw_input_usage else None,
                output_tokens=total_output_tokens if saw_output_usage else None,
                # Provider price catalogs change independently.  Cost remains
                # unknown unless a deployment adapter supplies a priced result.
                cost_usd=None,
                tool_summary=tuple(tool_summary),
            )
        raise GatewayError(f"model_unavailable:{type(last_error).__name__}")


__all__ = ["GatewayError", "OpenAICompatibleGateway"]
