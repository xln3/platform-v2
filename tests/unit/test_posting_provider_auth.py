from __future__ import annotations

import base64
import stat
import time
from pathlib import Path

import httpx
import pytest
from geo_platform.posting import provider_auth, providers

_PNG = b"\x89PNG\r\n\x1a\nprovider-captcha"


def _datasets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "_datasets_dir", lambda: tmp_path)
    monkeypatch.setattr(provider_auth, "_datasets_dir", lambda: tmp_path)


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    original_client = httpx.Client

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(**kwargs)

    monkeypatch.setattr(providers.httpx, "Client", factory)


def _write_active_session(tmp_path: Path, session_id: str) -> None:
    (tmp_path / "prfabu_session.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        f"#HttpOnly_.prfabu.com\tTRUE\t/\tTRUE\t0\tPHPSESSID\t{session_id}\n",
        encoding="utf-8",
    )


def test_captcha_challenge_is_actor_bound_and_stored_with_restricted_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _datasets(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/captcha.html"
        return httpx.Response(
            200,
            content=_PNG,
            headers={"set-cookie": "PHPSESSID=challenge-session; Path=/; HttpOnly; Secure"},
        )

    _mock_client(monkeypatch, handler)
    challenge = provider_auth.create_prfabu_captcha(
        tenant_pub_id="tnt_test",
        actor_pub_id="usr_test",
    )

    assert base64.b64decode(challenge.image_base64) == _PNG
    challenge_path = tmp_path / ".provider-login" / "prfabu" / f"{challenge.challenge_id}.json"
    assert stat.S_IMODE(challenge_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(challenge_path.stat().st_mode) == 0o600
    with pytest.raises(provider_auth.PrfabuChallengeInvalid):
        provider_auth.login_prfabu(
            challenge_id=challenge.challenge_id,
            tenant_pub_id="tnt_test",
            actor_pub_id="usr_other",
            account="account",
            password="password",
            captcha="1234",
        )
    assert not challenge_path.exists()


def test_web_login_verifies_and_persists_only_the_rotated_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _datasets(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/captcha.html":
            return httpx.Response(
                200,
                content=_PNG,
                headers={"set-cookie": "PHPSESSID=challenge-session; Path=/; HttpOnly; Secure"},
            )
        if request.url.path == "/":
            body = request.content.decode("utf-8")
            assert "username=account" in body
            assert "password=temporary-password" in body
            assert "captcha=1234" in body
            return httpx.Response(
                200,
                json={"code": 200, "msg": "登录成功"},
                headers={"set-cookie": "PHPSESSID=login-session; Path=/; HttpOnly; Secure"},
            )
        assert request.url.path == "/index/user/wallet.html"
        assert "PHPSESSID=login-session" in request.headers["cookie"]
        return httpx.Response(
            200,
            json={"code": 200, "data": {"money": "128.50"}},
            headers={"set-cookie": "PHPSESSID=verified-session; Path=/; HttpOnly; Secure"},
        )

    _mock_client(monkeypatch, handler)
    challenge = provider_auth.create_prfabu_captcha(
        tenant_pub_id="tnt_test",
        actor_pub_id="usr_test",
    )
    state = provider_auth.login_prfabu(
        challenge_id=challenge.challenge_id,
        tenant_pub_id="tnt_test",
        actor_pub_id="usr_test",
        account="account",
        password="temporary-password",
        captcha="1234",
    )

    assert state.status == "ready"
    assert str(state.balance) == "128.50"
    assert providers._load_prfabu_session() == "verified-session"
    session_path = tmp_path / "prfabu_session.txt"
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert "temporary-password" not in session_path.read_text(encoding="utf-8")
    assert not tuple((tmp_path / ".provider-login" / "prfabu").glob("*.json"))


def test_rejected_login_does_not_replace_the_last_known_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _datasets(tmp_path, monkeypatch)
    _write_active_session(tmp_path, "known-session")
    challenge_id = "A" * 32
    provider_auth._write_challenge(
        challenge_id,
        {
            "tenant_pub_id": "tnt_test",
            "actor_pub_id": "usr_test",
            "session_id": "challenge-session",
            "expires_at": time.time() + 60,
        },
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 201, "msg": "验证码错误"},
            headers={"set-cookie": "PHPSESSID=guest-session; Path=/; HttpOnly; Secure"},
        )

    _mock_client(monkeypatch, handler)
    state = provider_auth.login_prfabu(
        challenge_id=challenge_id,
        tenant_pub_id="tnt_test",
        actor_pub_id="usr_test",
        account="account",
        password="wrong",
        captcha="0000",
    )

    assert state.status == "rejected"
    assert state.message == "验证码错误"
    assert providers._load_prfabu_session() == "known-session"


def test_session_probe_reports_expiry_without_persisting_the_guest_cookie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _datasets(tmp_path, monkeypatch)
    _write_active_session(tmp_path, "known-session")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 201, "msg": "您的登录已失效请重新登录"},
            headers={"set-cookie": "PHPSESSID=guest-session; Path=/; HttpOnly; Secure"},
        )

    _mock_client(monkeypatch, handler)
    state = provider_auth.prfabu_session_state()

    assert state.status == "expired"
    assert providers._load_prfabu_session() == "known-session"
