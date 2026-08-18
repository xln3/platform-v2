from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from geo_platform.config import get_settings
from geo_platform.posting import provider_login
from geo_platform.posting.provider_credentials import ProviderCredentialStore


@pytest.fixture
def provider_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GEO_ENV", "test")
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_KMS_MASTER_KEY", "provider-login-test-master-key")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler):
    def factory(
        spec: provider_login._ProviderSpec,
        cookies: dict[str, str] | None = None,
    ) -> httpx.Client:
        return httpx.Client(
            base_url=spec.base_url,
            cookies=cookies,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(provider_login, "_new_client", factory)


def test_static_captcha_login_uses_saved_credentials_and_encrypts_rotated_session(
    provider_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProviderCredentialStore()
    store.save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="toumeiw",
        account="supplier-account",
        password="supplier-password",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == "/web/Login/verify.html"
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nprovider-captcha",
                headers={"set-cookie": "PHPSESSID=challenge-session; Path=/; HttpOnly"},
            )
        form = parse_qs(request.content.decode())
        assert request.url.path == "/web/login/login.html"
        assert form["username"] == ["supplier-account"]
        assert form["password"] == ["supplier-password"]
        assert form["captcha"] == ["4821"]
        assert "PHPSESSID=challenge-session" in request.headers["cookie"]
        return httpx.Response(
            200,
            json={"code": 200, "msg": "登录成功"},
            headers={"set-cookie": "PHPSESSID=ready-session; Path=/; HttpOnly"},
        )

    _mock_client(monkeypatch, handler)
    challenge = provider_login.create_provider_captcha(
        provider="toumeiw",
        tenant_pub_id="tnt_alpha",
        actor_pub_id="usr_operator",
    )
    assert challenge.image_mime_type == "image/png"
    challenge_record = (
        provider_storage / ".provider-login" / "toumeiw" / f"{challenge.challenge_id}.json"
    ).read_bytes()
    assert b"challenge-session" not in challenge_record
    assert b"usr_operator" not in challenge_record

    state = provider_login.login_provider(
        provider="toumeiw",
        challenge_id=challenge.challenge_id,
        tenant_pub_id="tnt_alpha",
        actor_pub_id="usr_operator",
        captcha="4821",
    )

    assert state.status == "ready"
    account = store.load(tenant_pub_id="tnt_alpha", provider="toumeiw")
    assert account.cookies["PHPSESSID"] == "ready-session"
    ciphertext = (
        provider_storage / ".provider-credentials" / "tnt_alpha" / "toumeiw.json"
    ).read_bytes()
    assert b"supplier-password" not in ciphertext
    assert b"ready-session" not in ciphertext

    with pytest.raises(provider_login.ProviderLoginChallengeInvalid):
        provider_login.login_provider(
            provider="toumeiw",
            challenge_id=challenge.challenge_id,
            tenant_pub_id="tnt_alpha",
            actor_pub_id="usr_operator",
            captcha="4821",
        )


def test_captcha_challenge_is_bound_to_the_requesting_actor(
    provider_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del provider_storage
    ProviderCredentialStore().save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="pinda",
        account="supplier-account",
        password="supplier-password",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"GIF89aprovider-captcha",
            headers={"set-cookie": "SESSION=challenge-session; Path=/; HttpOnly"},
        )

    _mock_client(monkeypatch, handler)
    challenge = provider_login.create_provider_captcha(
        provider="pinda",
        tenant_pub_id="tnt_alpha",
        actor_pub_id="usr_owner",
    )

    with pytest.raises(provider_login.ProviderLoginChallengeInvalid):
        provider_login.login_provider(
            provider="pinda",
            challenge_id=challenge.challenge_id,
            tenant_pub_id="tnt_alpha",
            actor_pub_id="usr_other",
            captcha="1234",
        )


def test_interactive_supplier_is_never_reported_as_automatically_logged_in(
    provider_storage: Path,
) -> None:
    del provider_storage
    ProviderCredentialStore().save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="meijiehezi",
        account="supplier-account",
        password="supplier-password",
    )

    state = provider_login.provider_session_state(
        provider="meijiehezi",
        tenant_pub_id="tnt_alpha",
    )
    assert state.status == "interactive_required"
    with pytest.raises(provider_login.ProviderLoginInteractiveRequired):
        provider_login.create_provider_captcha(
            provider="meijiehezi",
            tenant_pub_id="tnt_alpha",
            actor_pub_id="usr_owner",
        )
