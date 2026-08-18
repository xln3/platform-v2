import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from geo_platform.collection import router as collection_router
from geo_platform.collection import vault as vault_module
from geo_platform.collection.dlp import redact
from geo_platform.collection.vault import (
    KmsUnavailableError,
    LocalKms,
    ProfileVault,
    VaultTransitKms,
    profile_aad,
)
from geo_platform.identity.policy import Principal, Role

from domain.collection.state import ProfileState, transition
from workflows.activities import collection as collection_activities


def test_vault_aead_aad_rekey_and_cryptographic_delete() -> None:
    aad = profile_aad("tnt_a", "owner_a", "fixed", "pac_a", 1)
    vault = ProfileVault(LocalKms("master-a"))
    sealed = vault.seal(b'{"cookies":["secret"]}', aad)
    assert vault.open(sealed, aad) == b'{"cookies":["secret"]}'
    with pytest.raises(InvalidTag):
        vault.open(sealed, profile_aad("tnt_b", "owner_a", "fixed", "pac_a", 1))
    rekeyed = vault.rekey(sealed, aad, LocalKms("master-b"))
    assert ProfileVault(LocalKms("master-b")).open(rekeyed, aad).startswith(b"{")
    next_aad = profile_aad("tnt_a", "owner_a", "fixed", "pac_a", 2)
    rotated = vault.rotate_dek(sealed, aad, next_aad)
    assert vault.open(rotated, next_aad) == b'{"cookies":["secret"]}'
    assert rotated.ciphertext != sealed.ciphertext
    assert rotated.wrapped_dek != sealed.wrapped_dek
    with pytest.raises(InvalidTag):
        vault.open(rotated, aad)
    deleted = ProfileVault.cryptographic_delete(rekeyed)
    with pytest.raises(InvalidTag):
        ProfileVault(LocalKms("master-b")).open(deleted, aad)


def test_production_profile_vault_fails_closed_without_external_kms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection_router.settings, "env", "production")
    monkeypatch.setattr(collection_router.settings, "kms_provider", "unavailable")
    with pytest.raises(HTTPException) as exc_info:
        collection_router._profile_vault()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {"code": "profile_vault_unavailable"}


def test_vault_transit_kms_wraps_deks_with_context_and_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "vault-token"
    token_file.write_text("test-token", encoding="utf-8")
    token_file.chmod(0o600)
    dek = bytes(range(32))
    requests: list[dict[str, Any]] = []

    class Response:
        status = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(self.payload).encode()

    def fake_urlopen(request: Any, **_kwargs: object) -> Response:
        payload = json.loads(request.data)
        requests.append(
            {
                "url": request.full_url,
                "token": request.headers["X-vault-token"],
                "payload": payload,
            }
        )
        if "/encrypt/" in request.full_url:
            assert base64.b64decode(payload["plaintext"]) == dek
            return Response({"data": {"ciphertext": "vault:v1:opaque"}})
        return Response({"data": {"plaintext": base64.b64encode(dek).decode()}})

    monkeypatch.setattr(vault_module, "urlopen", fake_urlopen)
    kms = VaultTransitKms("https://vault.internal", str(token_file), "geo-profile")
    context = profile_aad("tnt_a", "usr_owner", "fixed", "pac_a", 1)
    wrapped = kms.wrap(dek, context)
    assert wrapped == b"vault:v1:opaque"
    assert kms.unwrap(wrapped, context) == dek
    assert [request["token"] for request in requests] == ["test-token", "test-token"]
    assert all(base64.b64decode(request["payload"]["context"]) == context for request in requests)
    assert (
        requests[0]["url"].split("/encrypt/", 1)[1] == requests[1]["url"].split("/decrypt/", 1)[1]
    )
    assert "tnt_" not in requests[0]["url"]


def test_vault_transit_uses_independent_account_keys_and_idempotent_destroy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "vault-token"
    token_file.write_text("deletion-token", encoding="utf-8")
    token_file.chmod(0o600)
    deleted_urls: list[str] = []

    class DeleteResponse:
        status = 204

        def __enter__(self) -> "DeleteResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, **_kwargs: object) -> DeleteResponse:
        assert request.get_method() == "DELETE"
        assert request.headers["X-vault-token"] == "deletion-token"
        deleted_urls.append(request.full_url)
        return DeleteResponse()

    monkeypatch.setattr(vault_module, "urlopen", fake_urlopen)
    kms = VaultTransitKms("https://vault.internal", str(token_file), "geo-profile")
    first = kms.account_key_name("tnt_a", "pac_a")
    assert first == kms.account_key_name("tnt_a", "pac_a")
    assert first != kms.account_key_name("tnt_a", "pac_b")
    assert first != kms.account_key_name("tnt_b", "pac_a")
    assert "tnt_a" not in first and "pac_a" not in first
    kms.destroy_account_key("tnt_a", "pac_a")
    assert deleted_urls == [f"https://vault.internal/v1/transit/keys/{first}"]


def test_vault_transit_destroy_accepts_already_missing_account_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "vault-token"
    token_file.write_text("deletion-token", encoding="utf-8")
    token_file.chmod(0o600)

    def missing_urlopen(request: Any, **_kwargs: object) -> None:
        raise vault_module.HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(vault_module, "urlopen", missing_urlopen)
    kms = VaultTransitKms("https://vault.internal", str(token_file), "geo-profile")
    kms.destroy_account_key("tnt_a", "pac_a")


def test_vault_transit_kms_rejects_unsafe_token_permissions(tmp_path: Path) -> None:
    token_file = tmp_path / "vault-token"
    token_file.write_text("test-token", encoding="utf-8")
    token_file.chmod(0o644)
    kms = VaultTransitKms("https://vault.internal", str(token_file), "geo-profile")
    with pytest.raises(KmsUnavailableError, match="permissions_unsafe"):
        kms.wrap(bytes(32), profile_aad("tnt_a", "usr_owner", "fixed", "pac_a", 1))


@pytest.mark.parametrize(
    "address",
    [
        "http://vault.internal",
        "https://user@vault.internal",
        "https://vault.internal/untrusted-prefix",
        "https://vault.internal?token=unsafe",
    ],
)
def test_vault_transit_kms_rejects_unsafe_endpoints(address: str) -> None:
    with pytest.raises(ValueError, match="https_origin"):
        VaultTransitKms(address, "/run/secrets/token", "geo-profile")


def test_vault_transit_kms_rejects_symlinked_token(tmp_path: Path) -> None:
    real_token = tmp_path / "real-token"
    real_token.write_text("test-token", encoding="utf-8")
    real_token.chmod(0o600)
    token_link = tmp_path / "vault-token"
    token_link.symlink_to(real_token)
    kms = VaultTransitKms("https://vault.internal", str(token_link), "geo-profile")
    with pytest.raises(KmsUnavailableError, match="token_unavailable"):
        kms.wrap(bytes(32), profile_aad("tnt_a", "usr_owner", "fixed", "pac_a", 1))


def test_production_profile_vault_accepts_only_configured_vault_transit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection_router.settings, "env", "production")
    monkeypatch.setattr(collection_router.settings, "kms_provider", "vault_transit")
    monkeypatch.setattr(
        collection_router.settings, "vault_transit_address", "https://vault.internal"
    )
    monkeypatch.setattr(
        collection_router.settings, "vault_transit_token_file", "/run/secrets/vault-token"
    )
    monkeypatch.setattr(collection_router.settings, "vault_transit_key_name", "geo-profile")
    assert isinstance(collection_router._profile_vault(), ProfileVault)


def test_production_revocation_uses_separate_external_deletion_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        env="production",
        kms_provider="vault_transit",
        vault_transit_address="https://vault.internal",
        vault_transit_deletion_token_file="/run/credentials/worker/delete-token",
        vault_transit_key_name="geo-profile",
    )
    calls: list[tuple[str, str, str, str, str]] = []

    class DeletionAuthority:
        def __init__(self, address: str, token_file: str, key_name: str) -> None:
            self.configuration = (address, token_file, key_name)

        def destroy_account_key(self, tenant_pub_id: str, account_pub_id: str) -> None:
            calls.append((*self.configuration, tenant_pub_id, account_pub_id))

    monkeypatch.setattr(collection_activities, "get_settings", lambda: settings)
    monkeypatch.setattr(collection_activities, "VaultTransitKms", DeletionAuthority)
    assert collection_activities._destroy_production_account_key("tnt_a", "pac_a", 2)
    assert calls == [
        (
            "https://vault.internal",
            "/run/credentials/worker/delete-token",
            "geo-profile",
            "tnt_a",
            "pac_a",
        )
    ]


def test_production_revocation_fails_closed_without_deletion_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        env="production",
        kms_provider="vault_transit",
        vault_transit_deletion_token_file="",
    )
    monkeypatch.setattr(collection_activities, "get_settings", lambda: settings)
    with pytest.raises(KmsUnavailableError, match="deletion_authority_unavailable"):
        collection_activities._destroy_production_account_key("tnt_a", "pac_a", 1)
    assert not collection_activities._destroy_production_account_key("tnt_a", "pac_a", 0)


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


def test_internal_evidence_permission_never_leaks_to_customer() -> None:
    for role in (Role.OPERATOR, Role.ANALYST, Role.REVIEWER, Role.ADMIN):
        principal = Principal(role.value, role, "tnt_test")
        assert principal.allows("evidence:read")
        principal.require("evidence:read")

    customer = Principal("customer", Role.CUSTOMER, "tnt_test")
    assert not customer.allows("evidence:read")
    with pytest.raises(HTTPException) as denied:
        customer.require("evidence:read")
    assert denied.value.status_code == 403


def test_report_permissions_separate_authoring_review_publication_and_delivery() -> None:
    analyst = Principal("analyst", Role.ANALYST, "tnt_test")
    analyst.require("report:write")
    for permission in ("report:review", "report:publish", "report:deliver"):
        with pytest.raises(HTTPException) as denied:
            analyst.require(permission)
        assert denied.value.status_code == 403

    reviewer = Principal("reviewer", Role.REVIEWER, "tnt_test")
    for permission in ("report:review", "report:publish", "report:deliver"):
        reviewer.require(permission)
    with pytest.raises(HTTPException) as denied:
        reviewer.require("report:write")
    assert denied.value.status_code == 403

    Principal("operator", Role.OPERATOR, "tnt_test").require("report:deliver")
    with pytest.raises(HTTPException):
        Principal("customer", Role.CUSTOMER, "tnt_test").require("report:deliver")


def test_intelligence_permissions_separate_analysis_and_human_verdicts() -> None:
    analyst = Principal("analyst", Role.ANALYST, "tnt_test")
    analyst.require("intelligence:read")
    analyst.require("intelligence:write")
    with pytest.raises(HTTPException):
        analyst.require("intelligence:review")

    reviewer = Principal("reviewer", Role.REVIEWER, "tnt_test")
    reviewer.require("intelligence:read")
    reviewer.require("intelligence:review")
    with pytest.raises(HTTPException):
        reviewer.require("intelligence:write")

    for role in (Role.CUSTOMER, Role.OPERATOR):
        with pytest.raises(HTTPException):
            Principal(role.value, role, "tnt_test").require("intelligence:read")


def test_actor_public_id_never_falls_back_to_external_subject() -> None:
    projected = Principal("external-subject", Role.CUSTOMER, "tnt_test", "usr_customer")
    assert projected.actor_pub_id == "usr_customer"
    with pytest.raises(HTTPException) as missing:
        _ = Principal("external-subject", Role.CUSTOMER, "tnt_test").actor_pub_id
    assert missing.value.status_code == 401
    assert missing.value.detail == {"code": "identity_projection_incomplete"}


def test_profile_state_machine_rejects_cross_account_style_shortcut() -> None:
    assert transition(ProfileState.CHALLENGE_REQUIRED, ProfileState.ACTIVE) is ProfileState.ACTIVE
    assert transition(ProfileState.ACTIVE, ProfileState.SUPERSEDED) is ProfileState.SUPERSEDED
    with pytest.raises(ValueError):
        transition(ProfileState.REQUESTED, ProfileState.ACTIVE)
    with pytest.raises(ValueError):
        transition(ProfileState.SUPERSEDED, ProfileState.ACTIVE)
