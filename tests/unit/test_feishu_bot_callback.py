from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from geo_platform.notifications import bot
from geo_platform.notifications.config import FeishuBotConfig
from geo_platform.notifications.interactions import InteractionResult
from geo_platform.notifications.security import callback_signature


def _config(tmp_path: Path) -> FeishuBotConfig:
    values = {
        "app-secret": "fake-app-secret",
        "verification-token": "fake-verification-token",
        "encrypt-key": "fake-encrypt-key",
        "allowed-open-ids": "ou_allowed\n",
        "link-key": "k" * 32,
    }
    paths: dict[str, str] = {}
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        paths[name] = str(path)
    return FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url="http://127.0.0.1:18000",
        app_secret_file=paths["app-secret"],
        verification_token_file=paths["verification-token"],
        encrypt_key_file=paths["encrypt-key"],
        allowed_open_ids_file=paths["allowed-open-ids"],
        link_signing_key_file=paths["link-key"],
    )


class _FakeSession:
    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        return None


def test_url_verification_challenge(tmp_path: Path) -> None:
    app = bot.create_app(
        config=_config(tmp_path),
        session_factory=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
        start_sender=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/callbacks/feishu/card-action",
            json={
                "type": "url_verification",
                "token": "fake-verification-token",
                "challenge": "challenge-value",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-value"}


def test_signed_action_returns_local_result_without_network(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    handled: list[dict[str, Any]] = []

    class FakeInteractions:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def handle(self, payload: dict[str, Any], **_kwargs: object) -> InteractionResult:
            handled.append(payload)
            return InteractionResult(response={"toast": {"type": "success", "content": "认领成功"}})

    monkeypatch.setattr(bot, "InteractionService", FakeInteractions)
    app = bot.create_app(
        config=_config(tmp_path),
        session_factory=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
        start_sender=False,
    )
    timestamp = str(int(time.time()))
    payload = {
        "schema": "2.0",
        "header": {
            "token": "fake-verification-token",
            "event_type": "card.action.trigger",
        },
        "event": {},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = callback_signature(
        timestamp=timestamp,
        nonce="nonce-test",
        encrypt_key="fake-encrypt-key",
        body=body,
    )
    with TestClient(app) as client:
        response = client.post(
            "/callbacks/feishu/card-action",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": "nonce-test",
                "X-Lark-Signature": signature,
            },
        )
    assert response.status_code == 200
    assert response.json()["toast"]["content"] == "认领成功"
    assert handled == [payload]


def test_completion_returns_retryable_error_until_registry_is_finalized(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    class FakeInteractions:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def handle(self, *_args: object, **_kwargs: object) -> InteractionResult:
            return InteractionResult(
                response={"toast": {"type": "success", "content": "已确认完成"}},
                finalize_ticket_sha256="a" * 64,
            )

    monkeypatch.setattr(bot, "InteractionService", FakeInteractions)
    monkeypatch.setattr(bot, "mark_registry_solved", lambda *_args: False)
    app = bot.create_app(
        config=_config(tmp_path),
        session_factory=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
        start_sender=False,
    )
    timestamp = str(int(time.time()))
    payload = {
        "schema": "2.0",
        "header": {
            "token": "fake-verification-token",
            "event_type": "card.action.trigger",
        },
        "event": {},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = callback_signature(
        timestamp=timestamp,
        nonce="nonce-finalize",
        encrypt_key="fake-encrypt-key",
        body=body,
    )
    with TestClient(app) as client:
        response = client.post(
            "/callbacks/feishu/card-action",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": "nonce-finalize",
                "X-Lark-Signature": signature,
            },
        )
    assert response.status_code == 503
    assert response.json() == {"error": "temporarily_unavailable"}


def test_callback_rejects_bad_signature_and_oversized_body(tmp_path: Path) -> None:
    app = bot.create_app(
        config=_config(tmp_path),
        session_factory=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
        start_sender=False,
    )
    payload = {
        "schema": "2.0",
        "header": {
            "token": "fake-verification-token",
            "event_type": "card.action.trigger",
        },
        "event": {},
    }
    with TestClient(app) as client:
        rejected = client.post(
            "/callbacks/feishu/card-action",
            json=payload,
            headers={
                "X-Lark-Request-Timestamp": str(int(time.time())),
                "X-Lark-Request-Nonce": "nonce-test",
                "X-Lark-Signature": "0" * 64,
            },
        )
        oversized = client.post(
            "/callbacks/feishu/card-action",
            content=b"x" * (bot.MAX_CALLBACK_BODY_BYTES + 1),
        )
    assert rejected.status_code == 403
    assert oversized.status_code == 413
