from __future__ import annotations

from collections.abc import Mapping, Sequence

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


def assert_customer_report_safe(sections: Sequence[Mapping[str, object]]) -> None:
    for section in sections:
        for key, value in section.items():
            normalized = key.lower()
            if normalized in _FORBIDDEN_REPORT_KEYS or any(
                item in normalized for item in _FORBIDDEN_REPORT_KEYS
            ):
                raise ValueError(f"operational provenance is forbidden in customer report: {key}")
            if isinstance(value, str):
                assert_secret_free(value)
