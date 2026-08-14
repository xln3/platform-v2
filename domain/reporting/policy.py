from __future__ import annotations

import re
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

# Service-1 quotation evidence explicitly requires a redacted account/browser/
# egress ledger.  These exact schema fields are safe customer audit data; the
# broad substring rules still reject raw proxy, profile, device and egress keys.
_AUDITED_PROVENANCE_KEYS = frozenset(
    {
        "account_id_masked",
        "browser_instance",
        "egress_region_gb",
        "egress_audit",
        "ip_sha256",
        "probe_at",
        "probe_state",
        "provenance_recorded_at",
    }
)

_OPAQUE_HASH_KEYS = frozenset(
    {
        "content_hash",
        "dimensions_hash",
        "fact_snapshot_hash",
        "quote_hash",
        "sha256",
        "trace_token",
    }
)

# These mappings are keyed by customer-visible source names/hosts, not by report
# schema fields.  A public host such as ``riskprofiler.io`` must therefore be DLP
# checked as data, but the substring ``profile`` must not be mistaken for the
# forbidden operational ``profile`` field.
_DYNAMIC_MAP_KEY_FIELDS = frozenset({"sitename_counts"})
_URL_FIELDS = frozenset({"url", "final_url", "source_url"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _assert_value_safe(value: Any, *, field_name: str | None = None) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if field_name in _DYNAMIC_MAP_KEY_FIELDS:
                assert_secret_free(str(key))
            elif normalized not in _AUDITED_PROVENANCE_KEYS and (
                normalized in _FORBIDDEN_REPORT_KEYS
                or any(item in normalized for item in _FORBIDDEN_REPORT_KEYS)
            ):
                raise ValueError(f"operational provenance is forbidden in customer report: {key}")
            _assert_value_safe(child, field_name=normalized)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            _assert_value_safe(child, field_name=field_name)
    elif isinstance(value, str):
        # Cryptographic identifiers are not prose and can randomly contain an
        # 11-digit substring that resembles a phone number.  Bypass text DLP
        # only for an exact, structurally valid SHA-256 value under a known hash
        # field; arbitrary strings under the same key are still inspected.
        if field_name in _OPAQUE_HASH_KEYS | {"ip_sha256"} and _SHA256_RE.fullmatch(value):
            return
        if field_name in _URL_FIELDS and re.fullmatch(r"https?://[^\s]+", value):
            return
        assert_secret_free(value)


def assert_customer_report_safe(sections: Sequence[Mapping[str, object]]) -> None:
    for section in sections:
        for key, value in section.items():
            normalized = key.lower()
            if normalized not in _AUDITED_PROVENANCE_KEYS and (
                normalized in _FORBIDDEN_REPORT_KEYS
                or any(item in normalized for item in _FORBIDDEN_REPORT_KEYS)
            ):
                raise ValueError(f"operational provenance is forbidden in customer report: {key}")
            _assert_value_safe(value, field_name=normalized)
