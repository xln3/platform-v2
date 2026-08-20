from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_APP_ID_RE = re.compile(r"^cli_[A-Za-z0-9_-]{1,156}$")
_CHAT_ID_RE = re.compile(r"^oc_[A-Za-z0-9_-]{1,196}$")
_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]{1,156}$")
_TENANT_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


class NotificationConfigurationError(RuntimeError):
    """A required bot setting is absent or unsafe; messages never contain values."""


def _credential_path(env_name: str, credential_name: str) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured
    directory = os.getenv("CREDENTIALS_DIRECTORY", "").strip()
    return str(Path(directory) / credential_name) if directory else ""


def read_secret_file(path: str, *, label: str, min_length: int = 8) -> str:
    if not path:
        raise NotificationConfigurationError(f"{label}_file_not_configured")
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise NotificationConfigurationError(f"{label}_file_unreadable") from error
    if len(value) < min_length:
        raise NotificationConfigurationError(f"{label}_too_short")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FeishuBotConfig:
    env: str
    app_id: str
    tenant_key: str
    chat_id: str
    public_base_url: str
    api_base_url: str
    app_secret_file: str
    verification_token_file: str
    encrypt_key_file: str
    allowed_open_ids_file: str
    link_signing_key_file: str
    callback_max_age_seconds: int = 300
    sender_poll_seconds: float = 1.0
    sender_batch_size: int = 20
    sender_max_attempts: int = 8
    alert_repeat_window_seconds: int = 14_400
    alert_card_update_seconds: int = 900
    alert_warning_names: str = "GeoCollectionRunStalled"
    alert_quiet_hours: str = ""
    alert_timezone: str = "Asia/Shanghai"
    mention_oncall: bool = False
    oncall_open_id: str = ""

    @classmethod
    def from_env(cls) -> FeishuBotConfig:
        return cls(
            env=os.getenv("GEO_ENV", "development").strip().lower() or "development",
            app_id=os.getenv("GEO_FEISHU_APP_ID", "").strip(),
            tenant_key=os.getenv("GEO_FEISHU_TENANT_KEY", "").strip(),
            chat_id=os.getenv("GEO_FEISHU_CHAT_ID", "").strip(),
            public_base_url=os.getenv("GEO_ASSIST_PUBLIC_BASE", "").strip().rstrip("/"),
            api_base_url=os.getenv("GEO_FEISHU_API_BASE_URL", "https://open.feishu.cn")
            .strip()
            .rstrip("/"),
            app_secret_file=_credential_path("GEO_FEISHU_APP_SECRET_FILE", "feishu-app-secret"),
            verification_token_file=_credential_path(
                "GEO_FEISHU_VERIFICATION_TOKEN_FILE", "feishu-verification-token"
            ),
            encrypt_key_file=_credential_path("GEO_FEISHU_ENCRYPT_KEY_FILE", "feishu-encrypt-key"),
            allowed_open_ids_file=_credential_path(
                "GEO_FEISHU_ALLOWED_OPEN_IDS_FILE", "feishu-allowed-open-ids"
            ),
            link_signing_key_file=_credential_path(
                "GEO_FEISHU_LINK_SIGNING_KEY_FILE", "feishu-link-signing-key"
            ),
            callback_max_age_seconds=_positive_int("GEO_FEISHU_CALLBACK_MAX_AGE_SECONDS", 300),
            sender_poll_seconds=_positive_float("GEO_FEISHU_SENDER_POLL_SECONDS", 1.0),
            sender_batch_size=_positive_int("GEO_FEISHU_SENDER_BATCH_SIZE", 20),
            sender_max_attempts=_positive_int("GEO_FEISHU_SENDER_MAX_ATTEMPTS", 8),
            alert_repeat_window_seconds=_positive_int(
                "GEO_FEISHU_ALERT_REPEAT_WINDOW_SECONDS", 14_400
            ),
            alert_card_update_seconds=_positive_int("GEO_FEISHU_ALERT_CARD_UPDATE_SECONDS", 900),
            alert_warning_names=os.getenv(
                "GEO_FEISHU_ALERT_WARNING_NAMES", "GeoCollectionRunStalled"
            ).strip(),
            alert_quiet_hours=os.getenv("GEO_FEISHU_ALERT_QUIET_HOURS", "").strip(),
            alert_timezone=os.getenv("GEO_FEISHU_ALERT_TIMEZONE", "Asia/Shanghai").strip(),
            mention_oncall=_boolean("GEO_FEISHU_MENTION_ONCALL"),
            oncall_open_id=os.getenv("GEO_FEISHU_ONCALL_OPEN_ID", "").strip(),
        )

    def validate_api_base(self) -> None:
        parsed = urlsplit(self.api_base_url)
        if self.env == "production":
            if self.api_base_url != "https://open.feishu.cn":
                raise NotificationConfigurationError("feishu_api_base_not_allowlisted")
            return
        if parsed.scheme == "https" and parsed.hostname == "open.feishu.cn":
            return
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise NotificationConfigurationError("feishu_test_api_base_not_loopback")

    def validate_sender(self) -> None:
        self.validate_api_base()
        if _APP_ID_RE.fullmatch(self.app_id) is None:
            raise NotificationConfigurationError("feishu_app_id_not_configured")
        if _CHAT_ID_RE.fullmatch(self.chat_id) is None:
            raise NotificationConfigurationError("feishu_chat_id_not_configured")
        read_secret_file(self.app_secret_file, label="feishu_app_secret")

    def validate_assist_links(self) -> None:
        parsed = urlsplit(self.public_base_url)
        unsafe_components = bool(
            parsed.username or parsed.password or parsed.query or parsed.fragment
        )
        if self.env == "production":
            if parsed.scheme != "https" or not parsed.hostname or unsafe_components:
                raise NotificationConfigurationError("assist_public_base_not_https")
        elif parsed.scheme not in {"http", "https"} or not parsed.hostname or unsafe_components:
            raise NotificationConfigurationError("assist_public_base_invalid")
        read_secret_file(
            self.link_signing_key_file,
            label="feishu_link_signing_key",
            min_length=32,
        )

    def validate_callback(self) -> None:
        if _APP_ID_RE.fullmatch(self.app_id) is None:
            raise NotificationConfigurationError("feishu_app_id_not_configured")
        if _TENANT_KEY_RE.fullmatch(self.tenant_key) is None:
            raise NotificationConfigurationError("feishu_tenant_key_not_configured")
        read_secret_file(self.verification_token_file, label="feishu_verification_token")
        read_secret_file(self.encrypt_key_file, label="feishu_encrypt_key")
        if not self.allowed_open_ids():
            raise NotificationConfigurationError("feishu_allowed_open_ids_empty")

    def validate_runtime(self) -> None:
        self.validate_sender()
        self.validate_callback()
        self.validate_assist_links()
        self.validate_policy()

    def warning_names(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.alert_warning_names.split(",") if item.strip()
        )

    def validate_policy(self) -> None:
        try:
            ZoneInfo(self.alert_timezone)
        except ZoneInfoNotFoundError as error:
            raise NotificationConfigurationError("feishu_alert_timezone_invalid") from error
        if self.mention_oncall and (_OPEN_ID_RE.fullmatch(self.oncall_open_id) is None):
            raise NotificationConfigurationError("feishu_oncall_open_id_invalid")
        if self.alert_quiet_hours:
            self._quiet_hour_range()

    def _quiet_hour_range(self) -> tuple[int, int]:
        matched = re.fullmatch(
            r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)",
            self.alert_quiet_hours,
        )
        if matched is None:
            raise NotificationConfigurationError("feishu_alert_quiet_hours_invalid")
        start_hour, start_minute, end_hour, end_minute = map(int, matched.groups())
        return start_hour * 60 + start_minute, end_hour * 60 + end_minute

    def warning_is_quiet(self, now: datetime | None = None) -> bool:
        if not self.alert_quiet_hours:
            return False
        start_minute, end_minute = self._quiet_hour_range()
        current = now or datetime.now(UTC)
        local = current.astimezone(ZoneInfo(self.alert_timezone))
        local_minute = local.hour * 60 + local.minute
        if start_minute == end_minute:
            return True
        if start_minute < end_minute:
            return start_minute <= local_minute < end_minute
        return local_minute >= start_minute or local_minute < end_minute

    def allowed_open_ids(self) -> frozenset[str]:
        path = self.allowed_open_ids_file
        if not path:
            return frozenset()
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise NotificationConfigurationError(
                "feishu_allowed_open_ids_file_unreadable"
            ) from error
        values = {
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
        }
        if any(_OPEN_ID_RE.fullmatch(value) is None for value in values):
            raise NotificationConfigurationError("feishu_allowed_open_id_invalid")
        return frozenset(values)
