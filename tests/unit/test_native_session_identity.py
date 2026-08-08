import hashlib
import sqlite3
import uuid

import pytest
from geo_platform.config import DEFAULT_NATIVE_AUTH_PEPPER, Settings
from geo_platform.identity import native_session as native_session_module
from geo_platform.identity.native_session import (
    _derive_password,
    _verify_legacy_password,
    _verify_password_hash,
    create_native_session,
    normalize_email,
    validate_password,
)

ROTATED_PEPPER = "rotated-production-pepper-" + "7" * 30  # ≥32 字符且 ≠ 旧缺省


def _settings(**overrides) -> Settings:
    base = {"env": "development", "native_auth_pepper": DEFAULT_NATIVE_AUTH_PEPPER}
    base.update(overrides)
    return Settings(**base)


def _patch_settings(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr(native_session_module, "get_settings", lambda: settings)


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


# ---------- P1 安全包：pepper 双读轮换 ----------


def test_verify_password_hash_dual_reads_legacy_pepper(monkeypatch) -> None:
    """当前 pepper 已轮换：旧缺省 pepper 设置的哈希仍能命中，并标记 legacy。"""
    _patch_settings(monkeypatch, _settings(native_auth_pepper=ROTATED_PEPPER))
    salt = b"s" * 32
    legacy_hash = _derive_password("pw123456", salt, pepper=DEFAULT_NATIVE_AUTH_PEPPER)
    assert _verify_password_hash("pw123456", salt, legacy_hash, n=2**15, r=8, p=1) == (True, True)
    current_hash = _derive_password("pw123456", salt)
    assert _verify_password_hash("pw123456", salt, current_hash, n=2**15, r=8, p=1) == (True, False)
    assert _verify_password_hash("wrong-pass", salt, legacy_hash, n=2**15, r=8, p=1) == (
        False,
        False,
    )


def test_verify_password_hash_no_legacy_flag_when_pepper_not_rotated(monkeypatch) -> None:
    """当前 pepper 仍是旧缺省：第一次派生即命中，不触发无谓的惰性升级。"""
    _patch_settings(monkeypatch, _settings())
    salt = b"s" * 32
    legacy_hash = _derive_password("pw123456", salt, pepper=DEFAULT_NATIVE_AUTH_PEPPER)
    assert _verify_password_hash("pw123456", salt, legacy_hash, n=2**15, r=8, p=1) == (True, False)


def test_production_default_pepper_fails_loud(monkeypatch) -> None:
    """生产下 pepper 等于缺省字面量 → RuntimeError（不只查长度）。"""
    _patch_settings(monkeypatch, _settings(env="production"))
    with pytest.raises(RuntimeError, match="native_auth_pepper_not_configured"):
        _derive_password("pw123456", b"a" * 32)


def test_production_short_pepper_fails_loud(monkeypatch) -> None:
    """生产下 pepper 长度不足 → RuntimeError（既有守卫保持）。"""
    _patch_settings(monkeypatch, _settings(env="production", native_auth_pepper="short"))
    with pytest.raises(RuntimeError, match="native_auth_pepper_not_configured"):
        _derive_password("pw123456", b"a" * 32)


def test_explicit_pepper_bypasses_production_guard_for_legacy_dual_read(monkeypatch) -> None:
    """显式传 pepper 的派生绕开生产守卫——否则生产轮换后旧哈希永远无法被读出。"""
    _patch_settings(monkeypatch, _settings(env="production", native_auth_pepper=ROTATED_PEPPER))
    assert len(_derive_password("pw123456", b"a" * 32, pepper=DEFAULT_NATIVE_AUTH_PEPPER)) == 64


def test_development_default_pepper_still_works(monkeypatch) -> None:
    """非生产缺省 pepper 行为不变。"""
    _patch_settings(monkeypatch, _settings())
    assert len(_derive_password("pw123456", b"a" * 32)) == 64


class _Result:
    def __init__(self, row=None, scalar=0):
        self._row = row
        self._scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    """按 SQL 文本路由的极简 fake，只够喂 create_native_session。"""

    def __init__(self, credential_row, membership_row):
        self._credential_row = credential_row
        self._membership_row = membership_row
        self.updates = []
        self.commits = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        if "count(*) FROM platform.login_attempt" in sql:
            return _Result(scalar=0)
        if "UPDATE platform.user_password_credential" in sql:
            self.updates.append(params)
            return _Result()
        if "FROM platform.user_password_credential" in sql:
            return _Result(row=self._credential_row)
        if "FROM platform.membership" in sql:
            return _Result(row=self._membership_row)
        return _Result()

    def commit(self):
        self.commits += 1


def _credential_row(password: str, *, pepper: str | None) -> dict:
    salt = b"legacy-salt".ljust(32, b"\0")
    return {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "salt": salt,
        "password_hash": _derive_password(password, salt, pepper=pepper),
        "scrypt_n": 2**15,
        "scrypt_r": 8,
        "scrypt_p": 1,
        "tenant_pub_id": "ten_test",
        "user_pub_id": "usr_test",
        "subject": "analyst@example.com",
    }


def test_login_with_legacy_pepper_lazy_upgrades_credential(monkeypatch) -> None:
    """旧缺省 pepper 设置的密码能登录，且登录后哈希被惰性升级到当前 pepper。"""
    _patch_settings(monkeypatch, _settings(native_auth_pepper=ROTATED_PEPPER))
    session = _FakeSession(
        _credential_row("pw123456", pepper=DEFAULT_NATIVE_AUTH_PEPPER),
        {"role": "admin"},
    )

    token, identity, _expires = create_native_session(
        session,
        email="analyst@example.com",
        password="pw123456",
        network_label="unit-test",
    )

    assert token
    assert identity.subject == "analyst@example.com"
    assert identity.role == "admin"
    assert session.commits == 1
    assert len(session.updates) == 1
    upgraded = session.updates[0]
    # 落库哈希按当前（轮换后）pepper 可验、按旧缺省 pepper 不再命中
    assert _derive_password("pw123456", upgraded["salt"]) == upgraded["password_hash"]
    assert (
        _derive_password("pw123456", upgraded["salt"], pepper=DEFAULT_NATIVE_AUTH_PEPPER)
        != upgraded["password_hash"]
    )
    # 升级后再次校验不再走 legacy 分支
    assert _verify_password_hash(
        "pw123456", upgraded["salt"], upgraded["password_hash"], n=2**15, r=8, p=1
    ) == (True, False)


def test_login_with_current_pepper_does_not_rewrite_credential(monkeypatch) -> None:
    """当前 pepper 命中的登录不触发任何重写。"""
    _patch_settings(monkeypatch, _settings(native_auth_pepper=ROTATED_PEPPER))
    session = _FakeSession(_credential_row("pw123456", pepper=None), {"role": "admin"})

    token, identity, _expires = create_native_session(
        session,
        email="analyst@example.com",
        password="pw123456",
        network_label="unit-test",
    )

    assert token
    assert identity.role == "admin"
    assert session.updates == []


def test_login_with_wrong_password_after_rotation_rejected(monkeypatch) -> None:
    """轮换后错误密码在两种 pepper 下都不命中 → invalid_credentials。"""
    _patch_settings(monkeypatch, _settings(native_auth_pepper=ROTATED_PEPPER))
    session = _FakeSession(
        _credential_row("pw123456", pepper=DEFAULT_NATIVE_AUTH_PEPPER),
        {"role": "admin"},
    )
    with pytest.raises(PermissionError, match="invalid_credentials"):
        create_native_session(
            session,
            email="analyst@example.com",
            password="wrong-pass",
            network_label="unit-test",
        )
    assert session.updates == []
