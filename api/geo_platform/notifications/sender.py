"""Durable Feishu delivery worker; no producer performs external I/O."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from .cards import build_card
from .config import FeishuBotConfig, read_secret_file
from .feishu_client import FeishuApiError, FeishuAppClient
from .service import ClaimedDelivery, NotificationService, notice_card_mapping, utc_now

log = structlog.get_logger()


@dataclass(frozen=True)
class SenderSnapshot:
    running: bool
    last_poll_at: datetime | None
    last_delivery_at: datetime | None
    last_error_marker: str | None


class NotificationSender:
    def __init__(
        self,
        *,
        config: FeishuBotConfig,
        session_factory: Callable[[], Session],
        client: FeishuAppClient | None = None,
    ) -> None:
        config.validate_sender()
        config.validate_assist_links()
        config.validate_policy()
        self.config = config
        self.session_factory = session_factory
        self.client = client or FeishuAppClient(config)
        self.link_signing_key = read_secret_file(
            config.link_signing_key_file,
            label="feishu_link_signing_key",
            min_length=32,
        )
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._running = False
        self._last_poll_at: datetime | None = None
        self._last_delivery_at: datetime | None = None
        self._last_error_marker: str | None = None

    def snapshot(self) -> SenderSnapshot:
        with self._state_lock:
            return SenderSnapshot(
                running=self._running,
                last_poll_at=self._last_poll_at,
                last_delivery_at=self._last_delivery_at,
                last_error_marker=self._last_error_marker,
            )

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.client.close()

    def _set_error(self, marker: str | None) -> None:
        with self._state_lock:
            self._last_error_marker = marker

    def _claim(self) -> list[ClaimedDelivery]:
        with self.session_factory() as session:
            service = NotificationService(session)
            service.expire_due_assists(limit=self.config.sender_batch_size)
            claimed = service.claim_deliveries(
                limit=self.config.sender_batch_size,
                max_attempts=self.config.sender_max_attempts,
            )
            session.commit()
        with self._state_lock:
            self._last_poll_at = utc_now()
        return claimed

    def _deliver(self, claimed: ClaimedDelivery) -> None:
        try:
            with self.session_factory() as session:
                command, notice = NotificationService(session).delivery_context(claimed.command_id)
                card = build_card(
                    notice_card_mapping(notice),
                    public_base_url=self.config.public_base_url,
                    link_signing_key=self.link_signing_key,
                    mention_oncall=self.config.mention_oncall,
                    oncall_open_id=self.config.oncall_open_id,
                )
                chat_id = notice.target_chat_id
                message_id = notice.message_id
                command_uuid = command.command_uuid

            if claimed.operation == "send":
                result = self.client.send_card(
                    chat_id=chat_id,
                    card=card,
                    command_uuid=command_uuid,
                )
                delivered_message = result.data.get("message_id")
                if not isinstance(delivered_message, str) or not delivered_message:
                    raise FeishuApiError(
                        "feishu_send_message_id_missing",
                        request_log_id=result.request_log_id,
                    )
            elif claimed.operation == "update" and message_id:
                result = self.client.update_card(message_id=message_id, card=card)
                delivered_message = None
            else:
                raise FeishuApiError("feishu_update_message_id_missing", retryable=True)

            with self.session_factory() as session:
                NotificationService(session).delivery_succeeded(
                    command_id=claimed.command_id,
                    message_id=delivered_message,
                    request_log_id=result.request_log_id,
                )
                session.commit()
            with self._state_lock:
                self._last_delivery_at = utc_now()
                self._last_error_marker = None
        except FeishuApiError as error:
            with self.session_factory() as session:
                NotificationService(session).delivery_failed(
                    command_id=claimed.command_id,
                    marker=(
                        f"{error.marker}:code_{error.code}"
                        if error.code is not None
                        else error.marker
                    ),
                    request_log_id=error.request_log_id,
                    max_attempts=self.config.sender_max_attempts,
                    retryable=error.retryable,
                )
                session.commit()
            self._set_error(error.marker)
            log.warning(
                "feishu_delivery_failed",
                command_id=claimed.command_id,
                marker=error.marker,
                code=error.code,
                request_log_id=error.request_log_id,
                retryable=error.retryable,
            )
        except Exception as error:  # noqa: BLE001 - outbox must survive malformed local state
            marker = f"local_{type(error).__name__}"
            try:
                with self.session_factory() as session:
                    NotificationService(session).delivery_failed(
                        command_id=claimed.command_id,
                        marker=marker,
                        request_log_id=None,
                        max_attempts=self.config.sender_max_attempts,
                        retryable=False,
                    )
                    session.commit()
            except Exception:  # noqa: BLE001 - preserve original safe marker only
                log.warning(
                    "feishu_delivery_failure_record_failed",
                    command_id=claimed.command_id,
                    marker=marker,
                )
            self._set_error(marker)
            log.warning(
                "feishu_delivery_local_failure",
                command_id=claimed.command_id,
                marker=marker,
            )

    def run_once(self) -> int:
        claimed = self._claim()
        for command in claimed:
            if self._stop.is_set():
                break
            self._deliver(command)
        return len(claimed)

    def run_forever(self) -> None:
        with self._state_lock:
            self._running = True
        try:
            while not self._stop.is_set():
                try:
                    count = self.run_once()
                except Exception as error:  # noqa: BLE001 - DB outage must not kill service
                    marker = f"poll_{type(error).__name__}"
                    self._set_error(marker)
                    log.warning("feishu_sender_poll_failed", marker=marker)
                    count = 0
                if count == 0:
                    self._stop.wait(self.config.sender_poll_seconds)
        finally:
            with self._state_lock:
                self._running = False
