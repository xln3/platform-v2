from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import structlog

from .logging import configure_logging

MAX_BODY_BYTES = 65_536
ALLOWED_LABELS = ("alertname", "severity", "category", "service")
log = structlog.get_logger()


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
        if item.get("alertname"):
            projected.append(item)
    return projected


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
