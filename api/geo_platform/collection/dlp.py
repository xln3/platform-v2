import re
from typing import Any

SECRET_KEYS = re.compile(
    r"(cookie|authorization|token|otp|password|proxy_password|profile_path|qr_code|biometric)",
    re.IGNORECASE,
)
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/-]+=*")
OTP = re.compile(r"(?<!\d)\d{4,8}(?!\d)")


def redact_text(value: str) -> str:
    value = BEARER.sub("[REDACTED_AUTH]", value)
    return OTP.sub("[REDACTED_CODE]", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
