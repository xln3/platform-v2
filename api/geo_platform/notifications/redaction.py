"""Small, deterministic redaction boundary for notification-facing text."""

from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:access[_-]?token|api[_-]?key|app[_-]?secret|encrypt[_-]?key|"
    r"verification[_-]?token|sendkey|password|passwd|ticket|authorization)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_URL_RE = re.compile(r"(?i)https?://[^\s<>]+")
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_NATIONAL_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_OPAQUE_SECRET_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])")


def redact_notification_text(value: object) -> str:
    """Remove common bearer/PII shapes before DB, card, or log projection.

    This is defense in depth, not a general DLP engine. Producers must still
    keep notification annotations free of credentials and unnecessary PII.
    """

    text = str(value or "")
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT_RE.sub("[credential redacted]", text)
    text = _URL_RE.sub("[link redacted]", text)
    text = _PHONE_RE.sub(r"\1****\2", text)
    text = _NATIONAL_ID_RE.sub("[id redacted]", text)
    text = _EMAIL_RE.sub("[email redacted]", text)
    return _OPAQUE_SECRET_RE.sub("[opaque value redacted]", text)


__all__ = ["redact_notification_text"]
