"""One fail-closed redaction boundary for logs, exceptions, APIs and CLIs."""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Final

REDACTED: Final = "[REDACTED]"
_MAX_LOG_TEXT: Final = 8_192

_DSN = re.compile(
    r"(?i)\b(?:postgres(?:ql)?(?:\+[a-z0-9_]+)?|mysql(?:\+[a-z0-9_]+)?|"
    r"redis(?:s)?|amqps?|mongodb(?:\+srv)?)://[^\s'\"<>\[\]{}]+"
)
_URL_QUERY = re.compile(r"(?i)(https?://[^\s?'\"#<>]+)\?[^\s'\"#<>]*(?:#[^\s'\"<>]*)?")
_AUTHORIZATION = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_COOKIE = re.compile(r"(?i)\b(set-cookie|cookie)\s*[:=]\s*[^\r\n]+")
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?key|secret[_-]?key|"
    r"client[_-]?secret|private[_-]?key|refresh[_-]?token|access[_-]?token|"
    r"auth[_-]?token|dsn)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}|"
    r"gh[oprsu]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,})(?![A-Za-z0-9])"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|cookie|dsn|"
    r"(?:api|access|private|refresh|auth)[_-]?(?:key|token))"
)


def redact_text(value: str) -> str:
    """Remove connection strings, URL queries and common secret forms."""

    text = _DSN.sub("[REDACTED:dsn]", value)
    text = _URL_QUERY.sub(r"\1?<redacted>", text)
    text = _AUTHORIZATION.sub("[REDACTED:authorization]", text)
    text = _COOKIE.sub(lambda match: f"{match.group(1)}: [REDACTED:cookie]", text)
    text = _KEY_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _KNOWN_TOKEN.sub("[REDACTED:token]", text)
    if len(text) > _MAX_LOG_TEXT:
        return f"{text[:_MAX_LOG_TEXT]}...[TRUNCATED]"
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively project arbitrary log/event values into a secret-free form."""

    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, BaseException):
        return safe_exception_summary(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return f"[REDACTED:binary:{len(value)}]"
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_value(child) for child in value]
    if isinstance(value, set | frozenset):
        return [redact_value(child) for child in sorted(value, key=repr)]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


def safe_exception_summary(error: BaseException) -> str:
    """Return a bounded exception type plus redacted message, never a traceback."""

    message = redact_text(str(error)).strip()
    if not message:
        return type(error).__name__
    return f"{type(error).__name__}:{message[:240]}"


def redact_structlog_event(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Structlog processor that also suppresses raw exception tracebacks."""

    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        if isinstance(exc_info, tuple) and exc_info and isinstance(exc_info[0], type):
            event_dict.setdefault("exception_type", exc_info[0].__name__)
        else:
            event_dict.setdefault("exception_type", "captured")
    redacted = redact_value(event_dict)
    if not isinstance(redacted, dict):  # pragma: no cover - mapping input is invariant
        raise TypeError("redacted_structlog_event_not_mapping")
    return redacted


__all__ = [
    "REDACTED",
    "redact_structlog_event",
    "redact_text",
    "redact_value",
    "safe_exception_summary",
]
