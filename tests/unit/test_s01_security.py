import pytest
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from geo_platform.collection.dlp import redact
from geo_platform.collection.vault import LocalKms, ProfileVault, profile_aad
from geo_platform.identity.policy import Principal, Role

from domain.collection.state import ProfileState, transition


def test_vault_aead_aad_rekey_and_cryptographic_delete() -> None:
    aad = profile_aad("tnt_a", "owner_a", "fixed", "pac_a", 1)
    vault = ProfileVault(LocalKms("master-a"))
    sealed = vault.seal(b'{"cookies":["secret"]}', aad)
    assert vault.open(sealed, aad) == b'{"cookies":["secret"]}'
    with pytest.raises(InvalidTag):
        vault.open(sealed, profile_aad("tnt_b", "owner_a", "fixed", "pac_a", 1))
    rekeyed = vault.rekey(sealed, aad, LocalKms("master-b"))
    assert ProfileVault(LocalKms("master-b")).open(rekeyed, aad).startswith(b"{")
    deleted = ProfileVault.cryptographic_delete(rekeyed)
    with pytest.raises(InvalidTag):
        ProfileVault(LocalKms("master-b")).open(deleted, aad)


def test_dlp_removes_secret_keys_bearer_and_codes() -> None:
    payload = {
        "cookie": "sid=secret",
        "safe": "Authorization: Bearer abc.def and OTP 123456",
        "nested": {"profile_path": "/tmp/profile", "account_mask": "***21"},
    }
    output = redact(payload)
    rendered = str(output).lower()
    assert "secret" not in rendered
    assert "abc.def" not in rendered
    assert "123456" not in rendered
    assert "/tmp/profile" not in rendered
    assert "***21" in rendered


def test_role_downgrade_denies_execution_control() -> None:
    customer = Principal("user", Role.CUSTOMER, "tnt_test")
    with pytest.raises(HTTPException):
        customer.require("collection:control")
    Principal("worker", Role.WORKER, "tnt_test").require("profile:use")


def test_profile_state_machine_rejects_cross_account_style_shortcut() -> None:
    assert transition(ProfileState.CHALLENGE_REQUIRED, ProfileState.ACTIVE) is ProfileState.ACTIVE
    with pytest.raises(ValueError):
        transition(ProfileState.REQUESTED, ProfileState.ACTIVE)
