from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from domain.evidence.dlp import assert_secret_free

_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "platform_account_pub_id",
        "browser_profile_version_pub_id",
        "session_event_pub_id",
        "profile",
        "profile_path",
        "egress",
        "proxy",
        "device",
        "verification_detail",
        "captcha",
        "otp",
    }
)


def _assert_value_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_REPORT_KEYS or any(
                item in normalized for item in _FORBIDDEN_REPORT_KEYS
            ):
                raise ValueError(f"operational provenance is forbidden in customer report: {key}")
            _assert_value_safe(child)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            _assert_value_safe(child)
    elif isinstance(value, str):
        assert_secret_free(value)


def assert_customer_report_safe(sections: Sequence[Mapping[str, object]]) -> None:
    for section in sections:
        for key, value in section.items():
            normalized = key.lower()
            if normalized in _FORBIDDEN_REPORT_KEYS or any(
                item in normalized for item in _FORBIDDEN_REPORT_KEYS
            ):
                raise ValueError(f"operational provenance is forbidden in customer report: {key}")
            _assert_value_safe(value)
