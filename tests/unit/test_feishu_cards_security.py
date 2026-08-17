from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from geo_platform.notifications.cards import build_assist_card, build_card
from geo_platform.notifications.interactions import (
    InteractionProtocolError,
    InteractionService,
    parse_card_action,
)
from geo_platform.notifications.redaction import redact_notification_text
from geo_platform.notifications.security import (
    CallbackSecurityError,
    callback_signature,
    make_assist_capability,
    verify_assist_capability,
    verify_callback_request,
)


def _notice(*, state: str = "active") -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "pub_id": "ntf_test_public",
        "kind": "assist",
        "state": state,
        "desired_state": state,
        "severity": "warning",
        "title": "[GEO] 人工接管请求",
        "summary": {
            "event_type": "验证码接管",
            "platform": "doubao",
            "region": "上海",
            "account_mask": "155****1234",
            "session_public_id": "ast_safe",
            "reason": "query *unsafe*\nnext 13800138000 ticket=fake-ticket-value",
        },
        "assist_ticket_sha256": "a" * 64,
        "claimed_actor_mask": "ou_x…safe" if state == "claimed" else None,
        "claimed_at": now if state == "claimed" else None,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(minutes=30),
    }


def test_assist_capability_is_bound_tamper_evident_and_expires() -> None:
    expiry = int(time.time()) + 60
    cap = make_assist_capability(
        notification_id="ntf_one",
        ticket_sha256="b" * 64,
        expires_at=expiry,
        key="k" * 32,
    )
    assert verify_assist_capability(
        notification_id="ntf_one", capability=cap, key="k" * 32, now=expiry - 1
    ) == ("b" * 64, expiry)
    with pytest.raises(CallbackSecurityError):
        verify_assist_capability(
            notification_id="ntf_other", capability=cap, key="k" * 32, now=expiry - 1
        )
    with pytest.raises(CallbackSecurityError):
        verify_assist_capability(notification_id="ntf_one", capability=cap[:-1] + "A", key="k" * 32)
    with pytest.raises(CallbackSecurityError, match="expired"):
        verify_assist_capability(
            notification_id="ntf_one", capability=cap, key="k" * 32, now=expiry
        )


def test_assist_card_callback_values_are_minimal_and_ticket_free() -> None:
    notice = _notice()
    card = build_assist_card(
        notice,
        public_base_url="https://assist.example",
        link_signing_key="k" * 32,
    )
    serialized = json.dumps(card, ensure_ascii=False)
    assert "a" * 64 not in serialized
    assert "15512341234" not in serialized
    assert "13800138000" not in serialized
    assert "fake-ticket-value" not in serialized
    actions = next(element for element in card["elements"] if element["tag"] == "action")
    callback_values = [item["value"] for item in actions["actions"] if "value" in item]
    assert callback_values == [
        {"v": "1", "notification_id": "ntf_test_public", "action": "claim"},
        {"v": "1", "notification_id": "ntf_test_public", "action": "recheck"},
    ]
    link = next(item["url"] for item in actions["actions"] if "url" in item)
    assert link.startswith("https://assist.example/api/v2/assist/notification/ntf_test_public/")
    assert "剩余" in serialized
    assert r"\*unsafe\*" in card["elements"][1]["text"]["content"]


def test_claimed_card_exposes_release_and_complete_terminal_has_no_buttons() -> None:
    claimed = build_card(
        _notice(state="claimed"),
        public_base_url="https://assist.example",
        link_signing_key="k" * 32,
    )
    values = [
        action["value"]["action"]
        for element in claimed["elements"]
        if element["tag"] == "action"
        for action in element["actions"]
        if "value" in action
    ]
    assert values == ["release", "recheck", "complete"]
    solved_notice = _notice(state="solved")
    solved_notice["resolved_at"] = datetime.now(UTC)
    solved = build_card(
        solved_notice,
        public_base_url="https://assist.example",
        link_signing_key="k" * 32,
    )
    assert all(element["tag"] != "action" for element in solved["elements"])


def test_oncall_mention_is_explicitly_configurable() -> None:
    card = build_assist_card(
        _notice(),
        public_base_url="https://assist.example",
        link_signing_key="k" * 32,
        mention_oncall=True,
        oncall_open_id="ou_oncall_test",
    )
    assert "<at id=ou_oncall_test>" in json.dumps(card, ensure_ascii=False)


def test_notification_text_redacts_common_pii_and_bearers() -> None:
    raw = (
        "账号 13800138000 owner@example.test "
        "ticket=fake-ticket-value Bearer fake-access-value "
        "https://internal.example/path?key=fake " + "x" * 44
    )
    redacted = redact_notification_text(raw)
    assert "13800138000" not in redacted
    assert "owner@example.test" not in redacted
    assert "fake-ticket-value" not in redacted
    assert "fake-access-value" not in redacted
    assert "internal.example" not in redacted
    assert "x" * 44 not in redacted
    assert "138****8000" in redacted


def _encrypted_envelope(payload: dict[str, object], encrypt_key: str) -> bytes:
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = b"0123456789abcdef"
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps(
        {"encrypt": base64.b64encode(iv + ciphertext).decode()}, separators=(",", ":")
    ).encode()


def test_signed_encrypted_callback_is_verified_and_replay_key_is_stable() -> None:
    now = int(time.time())
    encrypt_key = "encrypt-key-test"
    payload = {
        "schema": "2.0",
        "header": {"token": "verify-token", "event_type": "card.action.trigger"},
        "event": {},
    }
    body = _encrypted_envelope(payload, encrypt_key)
    headers = {
        "x-lark-request-timestamp": str(now),
        "x-lark-request-nonce": "nonce-one",
        "x-lark-signature": callback_signature(
            timestamp=str(now), nonce="nonce-one", encrypt_key=encrypt_key, body=body
        ),
    }
    verified = verify_callback_request(
        headers=headers,
        body=body,
        encrypt_key=encrypt_key,
        verification_token="verify-token",
        max_age_seconds=300,
        now=now,
    )
    assert verified.payload == payload
    assert len(verified.replay_key) == 64
    with pytest.raises(CallbackSecurityError, match="signature_invalid"):
        verify_callback_request(
            headers={**headers, "x-lark-signature": "0" * 64},
            body=body,
            encrypt_key=encrypt_key,
            verification_token="verify-token",
            max_age_seconds=300,
            now=now,
        )
    with pytest.raises(CallbackSecurityError, match="timestamp_stale"):
        verify_callback_request(
            headers=headers,
            body=body,
            encrypt_key=encrypt_key,
            verification_token="verify-token",
            max_age_seconds=10,
            now=now + 11,
        )


def test_bad_signature_is_rejected_before_ciphertext_decryption() -> None:
    now = int(time.time())
    with pytest.raises(CallbackSecurityError, match="signature_invalid"):
        verify_callback_request(
            headers={
                "x-lark-request-timestamp": str(now),
                "x-lark-request-nonce": "nonce-one",
                "x-lark-signature": "0" * 64,
            },
            body=b'{"encrypt":"not-valid-base64"}',
            encrypt_key="encrypt-key-test",
            verification_token="verify-token",
            max_age_seconds=300,
            now=now,
        )


def test_url_verification_requires_token_even_without_signature() -> None:
    body = json.dumps(
        {"type": "url_verification", "token": "verify-token", "challenge": "hello"}
    ).encode()
    verified = verify_callback_request(
        headers={},
        body=body,
        encrypt_key="encrypt-key-test",
        verification_token="verify-token",
        max_age_seconds=300,
    )
    assert verified.payload["challenge"] == "hello"
    with pytest.raises(CallbackSecurityError, match="verification_token_invalid"):
        verify_callback_request(
            headers={},
            body=body,
            encrypt_key="encrypt-key-test",
            verification_token="wrong-token",
            max_age_seconds=300,
        )


def _action_payload(*, action: str = "claim", app_id: str = "cli_test") -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_test",
            "event_type": "card.action.trigger",
            "app_id": app_id,
            "tenant_key": "tenant_test",
        },
        "event": {
            "operator": {"open_id": "ou_allowed"},
            "action": {
                "value": {
                    "v": "1",
                    "notification_id": "ntf_test",
                    "action": action,
                }
            },
            "context": {"open_message_id": "om_test", "open_chat_id": "oc_test"},
        },
    }


def test_unknown_action_and_wrong_app_are_rejected_before_database_access() -> None:
    with pytest.raises(InteractionProtocolError, match="action_unknown"):
        parse_card_action(_action_payload(action="run_shell"))  # type: ignore[arg-type]
    service = InteractionService(
        object(),  # type: ignore[arg-type]
        app_id="cli_test",
        tenant_key="tenant_test",
        allowed_open_ids=frozenset({"ou_allowed"}),
        replay_ttl_seconds=300,
    )
    with pytest.raises(InteractionProtocolError, match="app_mismatch"):
        service.handle(
            _action_payload(app_id="cli_other"),  # type: ignore[arg-type]
            replay_key="r" * 64,
        )
