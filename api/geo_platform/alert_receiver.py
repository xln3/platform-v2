from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any

import structlog

from workflows.activities.assist_notify import push_captcha_assist

from .logging import configure_logging
from .notifications.config import FeishuBotConfig
from .notifications.redaction import redact_notification_text
from .notifications.service import NotificationService
from .tenancy.database import SessionLocal

MAX_BODY_BYTES = 65_536
ALLOWED_LABELS = ("alertname", "severity", "category", "service", "region")
ALLOWED_ANNOTATIONS = ("summary", "description")
_SAFE_FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# 旧 Server酱 fallback：优先从 systemd credential file 读 SendKey；env 仅临时兼容。
SCT_API_URL_TEMPLATE = "https://sctapi.ftqq.com/{sendkey}.send"
# 同 alertname+fingerprint 的限频窗口：窗口内不重复外发（进程内账本，重启即重置）。
SCT_RESEND_WINDOW_S = 300.0
log = structlog.get_logger()

_sct_last_sent: dict[tuple[str, str], float] = {}
_legacy_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="legacy-alert-forward")
_legacy_slots = threading.BoundedSemaphore(64)


def safe_alert_projection(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise ValueError("invalid_alertmanager_payload")
    projected: list[dict[str, str]] = []
    for value in payload["alerts"][:100]:
        if not isinstance(value, dict):
            continue
        labels = value.get("labels")
        if not isinstance(labels, dict):
            continue
        item = {
            "status": str(value.get("status", "unknown"))[:16],
            **{
                key: redact_notification_text(labels[key])[:120]
                for key in ALLOWED_LABELS
                if key in labels and isinstance(labels[key], str)
            },
        }
        # fingerprint 是 alertmanager 的内容哈希（非敏感），仅用于外发限频键；
        # 载荷缺省时保持缺省，不改动既有投影形状。
        fingerprint = value.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            item["fingerprint"] = (
                fingerprint[:128]
                if _SAFE_FINGERPRINT_RE.fullmatch(fingerprint)
                else hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            )
        annotations = value.get("annotations")
        if isinstance(annotations, dict):
            for key in ALLOWED_ANNOTATIONS:
                annotation = annotations.get(key)
                if isinstance(annotation, str) and annotation:
                    item[key] = " ".join(redact_notification_text(annotation).split())[:500]
        for source, target in (("startsAt", "starts_at"), ("endsAt", "ends_at")):
            timestamp = value.get(source)
            if isinstance(timestamp, str) and timestamp:
                item[target] = timestamp[:80]
        if item.get("alertname"):
            projected.append(item)
    return projected


def eligible_for_feishu(
    alert: dict[str, str],
    *,
    config: FeishuBotConfig | None = None,
    now: datetime | None = None,
) -> bool:
    alertname = alert.get("alertname", "")
    service = alert.get("service", "")
    if alertname.startswith("GeoFeishu") or service == "feishu-bot":
        return False
    if alert.get("severity", "").lower() == "critical":
        return True
    policy = config or FeishuBotConfig.from_env()
    if alertname not in policy.warning_names():
        return False
    if alert.get("status", "firing").lower() == "resolved":
        return True
    return not policy.warning_is_quiet(now)


def persist_business_alerts_feishu(
    alerts: list[dict[str, str]],
    *,
    config: FeishuBotConfig | None = None,
) -> int | None:
    """Persist accepted transitions; this function performs no external I/O."""
    bot_config = config or FeishuBotConfig.from_env()
    if not bot_config.chat_id:
        log.warning("business_alert_feishu_unconfigured")
        return None
    try:
        bot_config.validate_policy()
        accepted = [alert for alert in alerts if eligible_for_feishu(alert, config=bot_config)]
        if not accepted:
            return 0
        with SessionLocal() as session:
            service = NotificationService(session)
            for alert in accepted:
                service.record_alert(
                    alert,
                    target_chat_id=bot_config.chat_id,
                    repeat_window_seconds=bot_config.alert_repeat_window_seconds,
                    card_update_seconds=bot_config.alert_card_update_seconds,
                )
            session.commit()
        return len(accepted)
    except Exception as error:  # noqa: BLE001 - alert intake must always answer quickly
        log.warning(
            "business_alert_feishu_persist_failed",
            marker=type(error).__name__,
            count=len(alerts),
        )
        return None


def _legacy_forward_job(alerts: list[dict[str, str]], sendkey: str) -> None:
    try:
        forward_business_alerts_sct(alerts, sendkey=sendkey)
    finally:
        _legacy_slots.release()


def enqueue_legacy_business_alerts(alerts: list[dict[str, str]], *, sendkey: str) -> bool:
    """Bounded compatibility fallback; webhook request threads never wait on Server酱."""
    if not sendkey.strip():
        return False
    if not _legacy_slots.acquire(blocking=False):
        log.warning("business_alert_legacy_queue_full")
        return False
    try:
        _legacy_executor.submit(_legacy_forward_job, list(alerts), sendkey)
    except RuntimeError:
        _legacy_slots.release()
        return False
    return True


def _legacy_sendkey() -> str:
    """Credential-file first, env only as a temporary backwards-compatible fallback."""
    configured = os.getenv("GEO_ALERT_SCT_SENDKEY_FILE", "").strip()
    credential_dir = os.getenv("CREDENTIALS_DIRECTORY", "").strip()
    path = configured or (str(Path(credential_dir) / "alert-sct-sendkey") if credential_dir else "")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("business_alert_legacy_credential_unreadable")
            return ""
    return os.getenv("GEO_ALERT_SCT_SENDKEY", "").strip()


def _sct_prune(now: float) -> None:
    stale = [key for key, ts in _sct_last_sent.items() if now - ts >= SCT_RESEND_WINDOW_S]
    for key in stale:
        del _sct_last_sent[key]


def forward_business_alerts_sct(
    alerts: list[dict[str, str]], *, sendkey: str, timeout_s: float = 3.0
) -> int:
    """把投影后的告警经 Server酱方糖外发，返回成功外发条数。

    纪律与 assist_notify 一致：绝不抛异常——未配置 sendkey / 限频命中 /
    推送失败只记日志，绝不阻断告警接收应答。推送失败不记账，下一次
    webhook 到达时允许重试。
    """
    sendkey = sendkey.strip()
    if not sendkey:
        return 0
    now = monotonic()
    _sct_prune(now)
    sent = 0
    for alert in alerts:
        key = (alert.get("alertname", ""), alert.get("fingerprint", ""))
        last = _sct_last_sent.get(key)
        if last is not None and now - last < SCT_RESEND_WINDOW_S:
            log.info("business_alert_sct_suppressed", alertname=key[0], fingerprint=key[1])
            continue
        title = f"[GEO告警] {alert.get('severity', 'unknown')} {alert.get('alertname', 'unknown')}"
        body = "\n".join(
            f"{field}: {alert[field]}"
            for field in ("status", "severity", "category", "service", "alertname", "fingerprint")
            if alert.get(field)
        )
        if push_captcha_assist(
            flavor="serverchan",
            url=SCT_API_URL_TEMPLATE.format(sendkey=sendkey),
            title=title,
            body=body,
            timeout_s=timeout_s,
        ):
            _sct_last_sent[key] = now
            sent += 1
        else:
            log.warning("business_alert_sct_failed", alertname=key[0], fingerprint=key[1])
    return sent


class AlertReceiverHandler(BaseHTTPRequestHandler):
    server_version = "GeoAlertReceiver/1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _respond(self, status: HTTPStatus, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._respond(HTTPStatus.NOT_FOUND)
            return
        self._respond(HTTPStatus.OK, b'{"status":"ok"}\n')

    def do_POST(self) -> None:
        if self.path != "/alerts":
            self._respond(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            payload: Any = json.loads(self.rfile.read(length))
            alerts = safe_alert_projection(payload)
        except (json.JSONDecodeError, ValueError):
            self._respond(HTTPStatus.BAD_REQUEST)
            return
        for alert in alerts:
            log.info(
                "business_alert_notification",
                **{
                    key: alert[key]
                    for key in (
                        "status",
                        "severity",
                        "category",
                        "service",
                        "region",
                        "alertname",
                        "fingerprint",
                    )
                    if alert.get(key)
                },
            )
        channel = os.getenv("GEO_ALERT_NOTIFY_CHANNEL", "serverchan").strip().lower()
        if channel == "feishu_app":
            if persist_business_alerts_feishu(alerts) is None:
                self._respond(HTTPStatus.SERVICE_UNAVAILABLE)
                return
        elif channel == "serverchan":
            enqueue_legacy_business_alerts(
                alerts,
                sendkey=_legacy_sendkey(),
            )
        elif channel not in {"", "disabled", "none"}:
            log.warning("business_alert_channel_unknown", channel=channel[:40])
        self._respond(HTTPStatus.NO_CONTENT)


def main() -> None:
    configure_logging(os.getenv("GEO_LOG_LEVEL", "INFO"))
    address = os.getenv("GEO_ALERT_RECEIVER_ADDRESS", "127.0.0.1")
    port = int(os.getenv("GEO_ALERT_RECEIVER_PORT", "18091"))
    server = ThreadingHTTPServer((address, port), AlertReceiverHandler)
    log.info("business_alert_receiver_started", address=address, port=port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("business_alert_receiver_stopped")


if __name__ == "__main__":
    main()
