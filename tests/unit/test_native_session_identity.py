import hashlib
import sqlite3

import pytest
from geo_platform.identity.native_session import (
    _derive_password,
    _verify_legacy_password,
    normalize_email,
    validate_password,
)


def test_native_identity_normalizes_email_and_accepts_strong_password() -> None:
    assert normalize_email("  Analyst@Example.COM ") == "analyst@example.com"
    validate_password("pw123456")


@pytest.mark.parametrize(
    "password",
    [""],
)
def test_native_identity_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValueError, match="invalid_password"):
        validate_password(password)


def test_native_password_hash_is_salted_and_deterministic_per_salt() -> None:
    password = "GeoAutomation2026"
    first_salt = b"a" * 32
    second_salt = b"b" * 32
    first = _derive_password(password, first_salt)
    assert first == _derive_password(password, first_salt)
    assert first != _derive_password(password, second_salt)
    assert len(first) == 64


def test_legacy_password_is_verified_for_one_time_native_upgrade(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    salt = "legacy-salt"
    expected = hashlib.scrypt(
        b"pw123456",
        salt=salt.encode(),
        n=32768,
        r=8,
        p=1,
        dklen=64,
        maxmem=128 * 1024 * 1024,
    ).hex()
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE app_user (email TEXT, password_hash TEXT)")
    connection.execute(
        "INSERT INTO app_user(email,password_hash) VALUES (?,?)",
        ("analyst@example.com", f"scrypt:32768:8:1${salt}${expected}"),
    )
    connection.commit()
    connection.close()

    assert _verify_legacy_password(str(database), "analyst@example.com", "pw123456")
    assert not _verify_legacy_password(str(database), "analyst@example.com", "wrong-pass")
