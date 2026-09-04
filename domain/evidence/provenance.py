from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CaptureChannel(StrEnum):
    API = "api"
    WEB = "web"
    # 人工补测登记（manual-ingestion）：运营在浏览器手动跑出的平台回答，
    # 无平台账号/浏览器 profile/会话事件可挂——三元 pub_id 一律 None。
    MANUAL = "manual"


class AccessClass(StrEnum):
    PUBLIC = "public"
    CUSTOMER_PRIVATE = "customer_private"
    PAID_OR_ORGANIZATION = "paid_or_organization"


@dataclass(frozen=True, slots=True)
class RedactedProvenance:
    platform_account_pub_id: str | None
    browser_profile_version_pub_id: str | None
    session_event_pub_id: str | None
    channel: CaptureChannel
    authorization_scope: tuple[str, ...]
    adapter_version: str
    capture_time: datetime
    access_class: AccessClass
    authorized_session_capture: bool = False

    def __post_init__(self) -> None:
        if self.capture_time.tzinfo is None:
            raise ValueError("capture_time must be timezone-aware")
        if self.authorized_session_capture and self.channel is not CaptureChannel.WEB:
            raise ValueError("authorized session capture must use the web channel")
        for value in (
            self.platform_account_pub_id,
            self.browser_profile_version_pub_id,
            self.session_event_pub_id,
        ):
            if value is not None and not _is_opaque_pub_id(value):
                raise ValueError("provenance identifiers must be opaque public IDs")

    def public_projection(self) -> dict[str, Any]:
        data = asdict(self)
        data["channel"] = self.channel.value
        data["access_class"] = self.access_class.value
        data["capture_time"] = self.capture_time.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if self.access_class is not AccessClass.PUBLIC:
            data["platform_account_pub_id"] = None
            data["browser_profile_version_pub_id"] = None
            data["session_event_pub_id"] = None
        return data


def _is_opaque_pub_id(value: str) -> bool:
    prefix, separator, suffix = value.partition("_")
    return bool(separator and prefix.isalpha() and len(suffix) >= 16 and "/" not in suffix)
