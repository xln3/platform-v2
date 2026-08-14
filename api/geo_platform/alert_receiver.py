from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from typing import Any

import structlog

from workflows.activities.assist_notify import push_captcha_assist

from .logging import configure_logging

MAX_BODY_BYTES = 65_536
ALLOWED_LABELS = ("alertname", "severity", "category", "service")
# Server酱方糖外发：SendKey 由 env GEO_ALERT_SCT_SENDKEY 提供，未配置 = 只写日志不外发。
SCT_API_URL_TEMPLATE = "https://sctapi.ftqq.com/{sendkey}.send"
# 同 alertname+fingerprint 的限频窗口：窗口内不重复外发（进程内账本，重启即重置）。
SCT_RESEND_WINDOW_S = 300.0
log = structlog.get_logger()

_sct_last_sent: dict[tuple[str, str], float] = {}


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
                key: str(labels[key])[:120]
                for key in ALLOWED_LABELS
                if key in labels and isinstance(labels[key], str)
            },
        }
        # fingerprint 是 alertmanager 的内容哈希（非敏感），仅用于外发限频键；
        # 载荷缺省时保持缺省，不改动既有投影形状。
        fingerprint = value.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            item["fingerprint"] = fingerprint[:64]
        if item.get("alertname"):
            projected.append(item)
    return projected


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
            log.info("business_alert_notification", **alert)
        forward_business_alerts_sct(alerts, sendkey=os.getenv("GEO_ALERT_SCT_SENDKEY", ""))
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
