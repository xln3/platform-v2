"""P1 安全包：生产环境身份头认证闸门单测。

修复前：请求带任意值 X-Service-Token 即跳过 cookie 认证进入 trusted-headers
分支，且该分支对非 service-account 用户不验任何凭据——伪造
X-Tenant-Id/X-Actor-Id/X-Actor-Role/X-Service-Token 四元组可冒充任何人。
修复后：生产环境（env in {production, prod}）头认证路径仅放行
service-account（维持 sha256+未吊销+未过期 token 校验），其余主体一律 401；
非生产（dev/test）行为完全不变。
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from geo_platform.config import Settings
from geo_platform.identity import policy as policy_module
from geo_platform.identity.policy import Role, get_principal


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"env": "development", "identity_mode": "trusted_headers"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@dataclass
class _FakeTenant:
    id: uuid.UUID
    pub_id: str


@dataclass
class _FakeMembership:
    tenant_id: uuid.UUID
    role: str


@dataclass
class _FakeUser:
    subject: str
    pub_id: str
    is_service_account: bool
    id: uuid.UUID = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid.uuid4()


class _Result:
    def __init__(self, row: object = None) -> None:
        self._row = row

    def first(self) -> object:
        return self._row


class _FakeSession:
    """按调用序喂 scalar（Tenant → credential verifier），execute 恒回 membership 行。"""

    def __init__(
        self,
        *,
        tenant: _FakeTenant | None,
        row: tuple[_FakeMembership, _FakeUser] | None,
        credential: object = None,
    ) -> None:
        self._scalars = [tenant, credential]
        self._row = row

    def scalar(self, _statement: object) -> object:
        return self._scalars.pop(0)

    def execute(self, _statement: object, _params: object = None) -> _Result:
        return _Result(self._row)


def _call_principal(session: _FakeSession, **headers: object) -> object:
    defaults: dict[str, object] = {
        "x_tenant_id": None,
        "x_actor_id": None,
        "x_actor_role": None,
        "x_service_token": None,
        "authorization": None,
        "native_token": None,
        "development_native_token": None,
        "oidc_browser_token": None,
        "session": session,
    }
    defaults.update(headers)
    return get_principal(**defaults)  # type: ignore[arg-type]


def test_production_forged_identity_headers_rejected(monkeypatch) -> None:
    """生产 native_session + 伪造四元组（非 service-account 的 admin 用户）→ 401。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="production", identity_mode="native_session"),
    )
    tenant = _FakeTenant(uuid.uuid4(), "ten_acme")
    session = _FakeSession(
        tenant=tenant,
        row=(
            _FakeMembership(tenant.id, "admin"),
            _FakeUser("admin@example.com", "usr_admin", is_service_account=False),
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        _call_principal(
            session,
            x_tenant_id="ten_acme",
            x_actor_id="admin@example.com",
            x_actor_role="admin",
            x_service_token="forged",
        )
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["code"] == "identity_headers_not_allowed"


def test_production_service_account_with_valid_token_passes(monkeypatch) -> None:
    """生产下 service-account + 有效 token（未吊销未过期）→ 维持放行。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="production", identity_mode="native_session"),
    )
    tenant = _FakeTenant(uuid.uuid4(), "ten_acme")
    session = _FakeSession(
        tenant=tenant,
        row=(
            _FakeMembership(tenant.id, "worker"),
            _FakeUser("svc-worker", "usr_worker", is_service_account=True),
        ),
        credential=True,
    )
    principal = _call_principal(
        session,
        x_tenant_id="ten_acme",
        x_actor_id="svc-worker",
        x_actor_role="worker",
        x_service_token="valid-service-token",
    )
    assert principal.role is Role.WORKER
    assert principal.tenant_pub_id == "ten_acme"
    assert principal.user_pub_id == "usr_worker"


def test_production_service_account_with_invalid_token_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="production", identity_mode="native_session"),
    )
    tenant = _FakeTenant(uuid.uuid4(), "ten_acme")
    session = _FakeSession(
        tenant=tenant,
        row=(
            _FakeMembership(tenant.id, "worker"),
            _FakeUser("svc-worker", "usr_worker", is_service_account=True),
        ),
        credential=False,
    )
    with pytest.raises(HTTPException) as excinfo:
        _call_principal(
            session,
            x_tenant_id="ten_acme",
            x_actor_id="svc-worker",
            x_actor_role="worker",
            x_service_token="invalid-service-token",
        )
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["code"] == "service_token_invalid"


def test_production_without_cookie_rejected(monkeypatch) -> None:
    """生产 native_session + 无 cookie 无头 → 401（cookie 分支不受影响）。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="production", identity_mode="native_session"),
    )
    with pytest.raises(HTTPException) as excinfo:
        _call_principal(_FakeSession(tenant=None, row=None))
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["code"] == "session_invalid"


def test_production_trusted_headers_mode_still_fails_closed(monkeypatch) -> None:
    """生产 + trusted_headers 的 503 闸门保持原样。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="production", identity_mode="trusted_headers"),
    )
    with pytest.raises(HTTPException) as excinfo:
        _call_principal(
            _FakeSession(tenant=None, row=None),
            x_tenant_id="ten_acme",
            x_actor_id="admin@example.com",
            x_actor_role="admin",
            x_service_token="forged",
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == "identity_provider_unavailable"


def test_development_trusted_headers_behavior_unchanged(monkeypatch) -> None:
    """非生产（dev）：旧行为不变——四元组直接认证通过，无 token 校验。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="development", identity_mode="trusted_headers"),
    )
    tenant = _FakeTenant(uuid.uuid4(), "ten_acme")
    session = _FakeSession(
        tenant=tenant,
        row=(
            _FakeMembership(tenant.id, "admin"),
            _FakeUser("admin@example.com", "usr_admin", is_service_account=False),
        ),
    )
    principal = _call_principal(
        session,
        x_tenant_id="ten_acme",
        x_actor_id="admin@example.com",
        x_actor_role="admin",
    )
    assert principal.role is Role.ADMIN
    assert principal.tenant_pub_id == "ten_acme"


def test_development_native_session_forged_token_header_unchanged(monkeypatch) -> None:
    """非生产 native_session：带任意 X-Service-Token 仍走头路径（e2e 依赖此行为）。"""
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: _settings(env="development", identity_mode="native_session"),
    )
    tenant = _FakeTenant(uuid.uuid4(), "ten_acme")
    session = _FakeSession(
        tenant=tenant,
        row=(
            _FakeMembership(tenant.id, "operator"),
            _FakeUser("ops@example.com", "usr_ops", is_service_account=False),
        ),
    )
    principal = _call_principal(
        session,
        x_tenant_id="ten_acme",
        x_actor_id="ops@example.com",
        x_actor_role="operator",
        x_service_token="any-value",
    )
    assert principal.role is Role.OPERATOR
