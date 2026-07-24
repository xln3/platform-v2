from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "authorization",
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"),
    ),
    (
        "cookie",
        re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^\r\n]+"),
    ),
    (
        "otp",
        re.compile(r"(?i)\b(otp|验证码|verification\s*code)\s*[:：=]?\s*\d{4,8}\b"),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    ),
    (
        "security_field",
        re.compile(
            r"(?i)\b(password|refresh_token|access_token|device_key|proxy_password)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
    ),
    (
        "qr_payload",
        re.compile(r"(?i)\b(?:otpauth|weixin|alipays?|qr(?:code)?)[^ \r\n]{8,}"),
    ),
)


@dataclass(frozen=True, slots=True)
class DlpResult:
    redacted: bytes
    findings: tuple[str, ...]
    sha256: str


def redact_bytes(payload: bytes, *, mime_type: str) -> DlpResult:
    if _is_searchable_text(mime_type):
        text = payload.decode("utf-8", errors="replace")
        findings: list[str] = []
        if mime_type in {
            "application/json",
            "application/har+json",
            "application/vnd.geo.ocr+json",
        }:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                parsed = _redact_json(parsed, findings)
                text = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for label, pattern in _PATTERNS:
            text, count = pattern.subn(f"[REDACTED:{label}]", text)
            if count:
                findings.extend([label] * count)
        redacted = text.encode()
    else:
        # Binary screenshots/PDFs must be passed through an OCR/image redactor activity before
        # this storage boundary. A positive marker prevents accidental raw admission.
        if b"otpauth://" in payload or b"Authorization:" in payload or b"Cookie:" in payload:
            raise ValueError("binary object contains a recognizable secret marker")
        findings = []
        redacted = payload
    return DlpResult(
        redacted=redacted,
        findings=tuple(sorted(set(findings))),
        sha256=sha256(redacted).hexdigest(),
    )


def assert_secret_free(value: str) -> None:
    result = redact_bytes(value.encode(), mime_type="text/plain")
    if result.findings:
        raise ValueError(f"secret-bearing content rejected: {','.join(result.findings)}")


def _is_searchable_text(mime_type: str) -> bool:
    return mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/xml",
        "application/har+json",
        "application/vnd.geo.ocr+json",
    }


def _redact_json(value: Any, findings: list[str]) -> Any:
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and "value" in value:
            header_name = value["name"].lower().replace("-", "_")
            if "cookie" in header_name or "authorization" in header_name:
                finding = "cookie" if "cookie" in header_name else "authorization"
                findings.append(finding)
                return {**value, "value": f"[REDACTED:{finding}]"}
        output: dict[str, Any] = {}
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            label = next(
                (
                    candidate
                    for candidate in (
                        "cookie",
                        "authorization",
                        "otp",
                        "password",
                        "refresh_token",
                        "access_token",
                        "device_key",
                        "proxy_password",
                        "qr_payload",
                    )
                    if candidate in normalized
                ),
                None,
            )
            if label is None:
                output[key] = _redact_json(child, findings)
                continue
            finding = (
                "security_field"
                if label
                in {
                    "password",
                    "refresh_token",
                    "access_token",
                    "device_key",
                    "proxy_password",
                }
                else label
            )
            findings.append(finding)
            output[key] = f"[REDACTED:{finding}]"
        return output
    if isinstance(value, list):
        return [_redact_json(child, findings) for child in value]
    return value
