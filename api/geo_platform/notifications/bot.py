"""Independent HTTPS-callback adapter and durable Feishu sender process."""

from __future__ import annotations

import collections
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..collection.assist_registry import DEFAULT_ASSIST_DIR, mark_registry_solved
from ..logging import configure_logging
from ..tenancy.database import SessionLocal
from .config import FeishuBotConfig, read_secret_file
from .interactions import InteractionProtocolError, InteractionService
from .security import CallbackSecurityError, verify_callback_request
from .sender import NotificationSender

MAX_CALLBACK_BODY_BYTES = 262_144
log = structlog.get_logger()


@dataclass
class _Runtime:
    config: FeishuBotConfig
    session_factory: Callable[[], Session]
    verification_token: str
    encrypt_key: str
    allowed_open_ids: frozenset[str]
    sender: NotificationSender | None
    sender_thread: threading.Thread | None


class _RateLimiter:
    def __init__(self, *, limit: int = 60, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, collections.deque[float]] = {}

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, collections.deque())
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            if len(self._buckets) > 4096:
                cutoff = now - self.window_seconds
                retained: dict[str, collections.deque[float]] = {}
                for name, values in self._buckets.items():
                    while values and values[0] <= cutoff:
                        values.popleft()
                    if values:
                        retained[name] = values
                self._buckets = retained
            return True


def create_app(
    *,
    config: FeishuBotConfig | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    start_sender: bool = True,
) -> FastAPI:
    bot_config = config or FeishuBotConfig.from_env()
    limiter = _RateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bot_config.validate_runtime()
        verification_token = read_secret_file(
            bot_config.verification_token_file,
            label="feishu_verification_token",
        )
        encrypt_key = read_secret_file(
            bot_config.encrypt_key_file,
            label="feishu_encrypt_key",
        )
        allowed_open_ids = bot_config.allowed_open_ids()
        sender = (
            NotificationSender(config=bot_config, session_factory=session_factory)
            if start_sender
            else None
        )
        sender_thread = None
        if sender is not None:
            sender_thread = threading.Thread(
                target=sender.run_forever,
                daemon=True,
                name="feishu-notification-sender",
            )
            sender_thread.start()
        app.state.runtime = _Runtime(
            config=bot_config,
            session_factory=session_factory,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            allowed_open_ids=allowed_open_ids,
            sender=sender,
            sender_thread=sender_thread,
        )
        log.info("feishu_bot_started", callback_transport="https", sender=start_sender)
        try:
            yield
        finally:
            if sender is not None:
                sender.stop()
            if sender_thread is not None:
                sender_thread.join(timeout=15)
            if sender is not None:
                sender.close()
            log.info("feishu_bot_stopped")

    app = FastAPI(
        title="GEO Feishu Application Bot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readiness")
    def readiness(request: Request) -> JSONResponse:
        runtime: _Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        try:
            with runtime.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - readiness exposes no database detail
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        snapshot = runtime.sender.snapshot() if runtime.sender is not None else None
        if snapshot is not None and not snapshot.running:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.post("/callbacks/feishu/card-action")
    async def card_action(request: Request) -> JSONResponse:
        peer = request.client.host if request.client is not None else "unknown"
        if not limiter.allowed(peer):
            return JSONResponse(status_code=429, content={"error": "rate_limited"})
        length = request.headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                declared = MAX_CALLBACK_BODY_BYTES + 1
            if declared < 0 or declared > MAX_CALLBACK_BODY_BYTES:
                return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        body_parts: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > MAX_CALLBACK_BODY_BYTES:
                return JSONResponse(status_code=413, content={"error": "payload_too_large"})
            body_parts.append(chunk)
        body = b"".join(body_parts)
        if not body:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        runtime: _Runtime = request.app.state.runtime
        try:
            verified = verify_callback_request(
                headers={key.lower(): value for key, value in request.headers.items()},
                body=body,
                encrypt_key=runtime.encrypt_key,
                verification_token=runtime.verification_token,
                max_age_seconds=runtime.config.callback_max_age_seconds,
            )
            if verified.payload.get("type") == "url_verification":
                challenge = verified.payload.get("challenge")
                if not isinstance(challenge, str) or not challenge or len(challenge) > 512:
                    raise CallbackSecurityError("callback_challenge_invalid")
                return JSONResponse(status_code=200, content={"challenge": challenge})
            with runtime.session_factory() as session:
                result = InteractionService(
                    session,
                    app_id=runtime.config.app_id,
                    tenant_key=runtime.config.tenant_key,
                    allowed_open_ids=runtime.allowed_open_ids,
                    replay_ttl_seconds=runtime.config.callback_max_age_seconds,
                ).handle(
                    verified.payload,
                    replay_key=verified.replay_key,
                )
                session.commit()
            if result.finalize_ticket_sha256:
                finalized = mark_registry_solved(
                    DEFAULT_ASSIST_DIR,
                    result.finalize_ticket_sha256,
                )
                if not finalized:
                    raise RuntimeError("assist_registry_finalize_pending")
            return JSONResponse(status_code=200, content=result.response)
        except (CallbackSecurityError, InteractionProtocolError) as error:
            marker = str(error)[:120]
            log.warning("feishu_callback_rejected", marker=marker)
            return JSONResponse(status_code=403, content={"error": "callback_rejected"})
        except Exception as error:  # noqa: BLE001 - callback response must stay sanitized
            log.error("feishu_callback_failed", marker=type(error).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily_unavailable"})

    return app


app = create_app()


def main() -> None:
    configure_logging(os.getenv("GEO_LOG_LEVEL", "INFO"))
    address = os.getenv("GEO_FEISHU_BOT_ADDRESS", "127.0.0.1")
    if address not in {"127.0.0.1", "::1"}:
        raise RuntimeError("feishu_bot_address_must_be_loopback")
    port = int(os.getenv("GEO_FEISHU_BOT_PORT", "18092"))
    uvicorn.run(
        app,
        host=address,
        port=port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
