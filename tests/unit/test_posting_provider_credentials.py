from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from geo_platform.collection.vault import LocalKms, ProfileVault
from geo_platform.posting.provider_credentials import (
    ProviderCredentialNotConfigured,
    ProviderCredentialStore,
    ProviderCredentialUnavailable,
)


def _store(tmp_path: Path) -> ProviderCredentialStore:
    return ProviderCredentialStore(
        datasets_dir=tmp_path,
        vault=ProfileVault(LocalKms("provider-credential-test-master-key")),
    )


def test_provider_credentials_and_session_are_encrypted_and_tenant_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    summary = store.save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="prfabu",
        account="13800138000",
        password="supplier-password-value",
    )

    assert summary.account_mask == "138****8000"
    assert summary.session_status == "needs_login"
    record_path = tmp_path / ".provider-credentials" / "tnt_alpha" / "prfabu.json"
    raw = record_path.read_bytes()
    assert b"13800138000" not in raw
    assert b"supplier-password-value" not in raw
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(record_path.parent.stat().st_mode) == 0o700

    account = store.load(tenant_pub_id="tnt_alpha", provider="prfabu")
    assert account.account == "13800138000"
    assert account.password == "supplier-password-value"
    assert account.cookies == {}

    updated = store.update_session(
        tenant_pub_id="tnt_alpha",
        provider="prfabu",
        cookies={"PHPSESSID": "rotated-provider-session"},
        status="ready",
        message="会话有效",
    )
    assert updated.session_status == "ready"
    assert b"rotated-provider-session" not in record_path.read_bytes()
    assert store.load(tenant_pub_id="tnt_alpha", provider="prfabu").cookies == {
        "PHPSESSID": "rotated-provider-session"
    }

    with pytest.raises(ProviderCredentialNotConfigured):
        store.load(tenant_pub_id="tnt_beta", provider="prfabu")


def test_provider_credential_tampering_and_unsafe_permissions_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="toumeiw",
        account="supplier-account",
        password="supplier-password",
    )
    path = tmp_path / ".provider-credentials" / "tnt_alpha" / "toumeiw.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["ciphertext_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ProviderCredentialUnavailable, match="decrypt_failed"):
        store.load(tenant_pub_id="tnt_alpha", provider="toumeiw")

    path.chmod(0o644)
    with pytest.raises(ProviderCredentialUnavailable, match="record_invalid"):
        store.summary(tenant_pub_id="tnt_alpha", provider="toumeiw")


def test_provider_credential_delete_removes_ciphertext(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_credentials(
        tenant_pub_id="tnt_alpha",
        provider="pinda",
        account="supplier-account",
        password="supplier-password",
    )

    store.delete(tenant_pub_id="tnt_alpha", provider="pinda")

    assert store.summary(tenant_pub_id="tnt_alpha", provider="pinda").configured is False
    with pytest.raises(ProviderCredentialNotConfigured):
        store.load(tenant_pub_id="tnt_alpha", provider="pinda")
