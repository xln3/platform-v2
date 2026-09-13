import json
from datetime import datetime

import pytest
from geo_platform.notifications.security import (
    CallbackSecurityError,
    callback_signature,
    verify_callback_request,
)

OBSERVED = "2026-09-14 01:29:20.461402956 +0800 CST m=+295087.910060871"
NOW = int(datetime.fromisoformat("2026-09-14T01:29:20+08:00").timestamp())
BODY = json.dumps({"schema": "2.0", "header": {"token": "test-token"}, "event": {}}).encode()


def verify(stamp: str, *, now: int = NOW, sign_stamp: str | None = None):
    return verify_callback_request(
        headers={
            "x-lark-request-timestamp": stamp,
            "x-lark-request-nonce": "123456789",
            "x-lark-signature": callback_signature(
                timestamp=stamp if sign_stamp is None else sign_stamp,
                nonce="123456789",
                encrypt_key="test-key",
                body=BODY,
            ),
        },
        body=BODY,
        encrypt_key="test-key",
        verification_token="test-token",
        max_age_seconds=300,
        now=now,
    )


@pytest.mark.parametrize(
    "stamp",
    [
        OBSERVED,
        str(NOW),
        "2026-09-13 17:29:20 +0000 UTC",
        "2026-09-14 01:29:20.1 +0800 CST",
        "2026-09-13 12:29:20 -0500 CDT m=+1.1",
    ],
)
def test_supported_timestamps_preserve_freshness(stamp):
    assert verify(stamp).timestamp == NOW
    assert verify(stamp).replay_key == verify(stamp).replay_key


@pytest.mark.parametrize("offset", [-301, 301])
def test_go_timestamp_rejects_stale_or_future(offset):
    with pytest.raises(CallbackSecurityError, match="timestamp_stale"):
        verify(OBSERVED, now=NOW + offset)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-14 01:29:20 CST",
        "2026-09-14 01:29:20 +2500 CST",
        "2026-02-30 01:29:20 +0800 CST",
        OBSERVED + " trailing",
        OBSERVED + ", " + OBSERVED,
        "9" * 129,
        "１２３",
        "NaN",
    ],
)
def test_malformed_timestamp_is_rejected(stamp):
    with pytest.raises(CallbackSecurityError, match="timestamp_invalid"):
        verify(stamp)


def test_signature_must_cover_original_go_timestamp():
    with pytest.raises(CallbackSecurityError, match="signature_invalid"):
        verify(OBSERVED, sign_stamp=str(NOW))
