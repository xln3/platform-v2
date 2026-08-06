import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from geo_platform.collection.leases import LeaseBusyError, acquire_session_lease
from geo_platform.collection.models import (
    AccountAuthorization,
    BrowserProfile,
    PlatformAccount,
    SessionLease,
)
from geo_platform.identity import policy as identity_policy
from geo_platform.identity import router as identity_router
from geo_platform.identity.browser_oidc import AuthorizationRequest, TokenExchange
from geo_platform.identity.oidc import OidcIdentity, OidcUnavailableError
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import (
    AuditLog,
    OidcIdentityBinding,
    Permission,
    RoleDefinition,
    RolePermission,
    Tenant,
    User,
)
from sqlalchemy import select, update


def bootstrap(client: TestClient, subject: str) -> str:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    return str(response.json()["tenant_pub_id"])


def headers(tenant: str, subject: str, role: str = "admin") -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
        "Idempotency-Key": "idem-" + secrets.token_hex(16),
    }


def test_membership_revocation_and_worker_credential_boundary() -> None:
    client = TestClient(app)
    admin_subject = "govern-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, admin_subject)
    admin_headers = headers(tenant, admin_subject)
    customer_subject = "customer-" + secrets.token_hex(5)
    member = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": customer_subject,
            "display_name": "Customer",
            "role": "customer",
        },
    )
    assert member.status_code == 201
    customer_headers = headers(tenant, customer_subject, "customer")
    assert client.get("/api/v2/projects", headers=customer_headers).status_code == 200
    assert client.get("/api/v2/platform-accounts", headers=customer_headers).status_code == 403
    assert (
        client.post(
            f"/api/v2/identity/members/{member.json()['pub_id']}/revoke",
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert client.get("/api/v2/projects", headers=customer_headers).status_code == 401

    worker = client.post(
        "/api/v2/identity/service-accounts",
        headers=admin_headers,
        json={"name": "Browser Worker", "expires_in_hours": 1},
    )
    assert worker.status_code == 201
    worker_headers = headers(tenant, worker.json()["subject"], "worker")
    assert client.get("/api/v2/projects", headers=worker_headers).status_code == 401
    worker_headers["X-Service-Token"] = worker.json()["token"]
    # Authenticated worker is real but intentionally lacks customer/project read.
    assert client.get("/api/v2/projects", headers=worker_headers).status_code == 403


def test_oidc_bearer_maps_hashed_subject_to_database_membership_and_ignores_actor_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    admin_subject = "oidc-admin-" + secrets.token_hex(5)
    tenant_pub_id = bootstrap(client, admin_subject)
    issuer = "https://identity.example.test"
    oidc_subject = "opaque-idp-" + secrets.token_hex(8)
    with SessionLocal() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
        user = session.scalar(select(User).where(User.subject == admin_subject))
        assert tenant is not None and user is not None
        session.add(
            OidcIdentityBinding(
                tenant_id=tenant.id,
                user_id=user.id,
                issuer_sha256=hashlib.sha256(issuer.encode()).hexdigest(),
                subject_sha256=hashlib.sha256(oidc_subject.encode()).hexdigest(),
            )
        )
        session.commit()

    class Verifier:
        def verify(self, token: str) -> OidcIdentity:
            if token != "valid.test.token":
                raise OidcUnavailableError("oidc_token_invalid")
            return OidcIdentity(
                issuer=issuer,
                subject=oidc_subject,
                tenant_pub_id=tenant_pub_id,
            )

    settings = identity_policy.get_settings()
    monkeypatch.setattr(settings, "identity_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", issuer)
    monkeypatch.setattr(identity_policy, "_oidc_verifier", lambda: Verifier())
    response = client.get(
        "/api/v2/projects",
        headers={
            "Authorization": "Bearer valid.test.token",
            "X-Tenant-Id": "tnt_browser_spoof",
            "X-Actor-Id": "usr_browser_spoof",
            "X-Actor-Role": "admin",
        },
    )
    assert response.status_code == 200
    assert (
        client.get(
            "/api/v2/projects",
            headers={"Authorization": "Bearer invalid.test.token"},
        ).status_code
        == 401
    )


def test_oidc_browser_pkce_callback_uses_http_only_cookie_and_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app, base_url="https://testserver")
    admin_subject = "oidc-browser-admin-" + secrets.token_hex(5)
    tenant_pub_id = bootstrap(client, admin_subject)
    issuer = "https://identity.example.test"
    oidc_subject = "opaque-browser-" + secrets.token_hex(8)
    with SessionLocal() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
        user = session.scalar(select(User).where(User.subject == admin_subject))
        assert tenant is not None and user is not None
        session.add(
            OidcIdentityBinding(
                tenant_id=tenant.id,
                user_id=user.id,
                issuer_sha256=hashlib.sha256(issuer.encode()).hexdigest(),
                subject_sha256=hashlib.sha256(oidc_subject.encode()).hexdigest(),
            )
        )
        session.commit()

    class Verifier:
        def verify(self, token: str) -> OidcIdentity:
            assert token == "header.payload.signature"
            return OidcIdentity(
                issuer=issuer,
                subject=oidc_subject,
                tenant_pub_id=tenant_pub_id,
            )

    class Flow:
        config = SimpleNamespace(post_login_uri="/platform/customer/")

        def authorization_request(self) -> AuthorizationRequest:
            return AuthorizationRequest(
                url="https://identity.example.test/authorize?state=opaque-state",
                transaction_cookie="encrypted-transaction",
            )

        def consume_transaction(self, cookie: str, state: str) -> str:
            assert cookie == "encrypted-transaction"
            assert state == "opaque-state"
            return "v" * 64

        async def exchange(self, code: str, verifier: str) -> TokenExchange:
            assert code == "opaque-code"
            assert verifier == "v" * 64
            return TokenExchange("header.payload.signature", 300)

    settings = identity_policy.get_settings()
    monkeypatch.setattr(settings, "identity_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", issuer)
    monkeypatch.setattr(identity_policy, "_oidc_verifier", lambda: Verifier())
    monkeypatch.setattr(identity_router, "_oidc_verifier", lambda: Verifier())
    monkeypatch.setattr(identity_router, "_browser_oidc_flow", lambda: Flow())

    login = client.get("/api/v2/identity/login", follow_redirects=False)
    assert login.status_code == 302
    assert login.headers["location"].startswith("https://identity.example.test/")
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "secure" in login.headers["set-cookie"].lower()

    callback = client.post(
        "/api/v2/identity/callback",
        data={"code": "opaque-code", "state": "opaque-state"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/platform/customer/"
    assert (
        client.get(
            "/api/v2/identity/session",
            headers={"Authorization": "Bearer header.payload.signature"},
        ).status_code
        == 401
    )
    assert client.get("/api/v2/identity/session").status_code == 200

    logout = client.post("/api/v2/identity/logout")
    assert logout.status_code == 204
    assert client.get("/api/v2/identity/session").status_code == 401


def test_oidc_binding_lifecycle_hashes_subject_rejects_reassignment_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    admin_subject = "binding-admin-" + secrets.token_hex(5)
    tenant_pub_id = bootstrap(client, admin_subject)
    admin_headers = headers(tenant_pub_id, admin_subject)
    first = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": "first-user-" + secrets.token_hex(5),
            "display_name": "First",
            "role": "customer",
        },
    )
    second = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={
            "subject": "second-user-" + secrets.token_hex(5),
            "display_name": "Second",
            "role": "reviewer",
        },
    )
    assert first.status_code == second.status_code == 201
    raw_oidc_subject = "idp-subject-" + secrets.token_hex(12)
    settings = identity_policy.get_settings()
    monkeypatch.setattr(settings, "oidc_issuer", "https://identity.example.test/")

    created = client.put(
        f"/api/v2/identity/members/{first.json()['user_pub_id']}/oidc-binding",
        headers=admin_headers,
        json={"subject": raw_oidc_subject},
    )
    assert created.status_code == 200
    assert created.json()["active"] is True
    assert raw_oidc_subject not in created.text

    conflict = client.put(
        f"/api/v2/identity/members/{second.json()['user_pub_id']}/oidc-binding",
        headers=admin_headers,
        json={"subject": raw_oidc_subject},
    )
    assert conflict.status_code == 409
    listed = client.get("/api/v2/identity/oidc-bindings", headers=admin_headers)
    assert listed.status_code == 200
    assert [item["user_pub_id"] for item in listed.json()] == [first.json()["user_pub_id"]]
    assert raw_oidc_subject not in listed.text

    revoked = client.delete(
        f"/api/v2/identity/members/{first.json()['user_pub_id']}/oidc-binding",
        headers=admin_headers,
    )
    repeated = client.delete(
        f"/api/v2/identity/members/{first.json()['user_pub_id']}/oidc-binding",
        headers=admin_headers,
    )
    assert revoked.status_code == repeated.status_code == 200
    assert revoked.json()["active"] is repeated.json()["active"] is False
    with SessionLocal() as session:
        binding = session.scalar(
            select(OidcIdentityBinding).where(
                OidcIdentityBinding.subject_sha256
                == hashlib.sha256(raw_oidc_subject.encode()).hexdigest()
            )
        )
        receipts = session.scalars(
            select(AuditLog.receipt).where(
                AuditLog.action.in_(
                    [
                        "identity.oidc_binding.created",
                        "identity.oidc_binding.revoked",
                    ]
                )
            )
        ).all()
    assert binding is not None and binding.revoked_at is not None
    assert all(raw_oidc_subject not in receipt for receipt in receipts)
    assert len(receipts) >= 2


def test_database_rbac_catalog_matches_runtime_policy() -> None:
    with SessionLocal() as session:
        roles = set(session.scalars(select(RoleDefinition.name)).all())
        permissions = set(session.scalars(select(Permission.name)).all())
        grants = session.scalar(select(RolePermission).limit(1))
    assert {"customer", "operator", "analyst", "reviewer", "admin", "worker"} <= roles
    assert {
        "project:read",
        "collection:control",
        "account:operate",
        "break_glass:approve",
        "collection:execute",
        "*",
    } <= permissions
    assert grants is not None


def test_fixed_adapter_catalog_never_claims_live_verification() -> None:
    client = TestClient(app)
    admin = "adapter-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, admin)
    request_headers = headers(tenant, admin)
    created = client.post(
        "/api/v2/platform-accounts",
        headers=request_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "fixture-catalog-***",
            "owner_pub_id": "owner_catalog",
            "purpose": "measure",
            "responsible_pub_id": admin,
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    )
    assert created.status_code == 201
    response = client.get("/api/v2/platform-adapters", headers=request_headers)
    assert response.status_code == 200
    fixed = next(item for item in response.json() if item["slug"] == "fixed")
    assert fixed["admission_level"] == "adapter_ready"
    assert fixed["last_passed_at"] is None
    assert set(fixed["capabilities"]) == {"read", "query"}


def test_two_workers_compete_for_one_fenced_account_lease() -> None:
    with SessionLocal() as session:
        account = session.scalars(
            select(PlatformAccount).order_by(PlatformAccount.created_at.desc())
        ).first()
        assert account is not None
        profile = session.scalar(
            select(BrowserProfile)
            .where(BrowserProfile.account_id == account.id)
            .order_by(BrowserProfile.profile_version.desc())
        )
        if profile is None:
            profile = BrowserProfile(
                pub_id=new_pub_id("prf"),
                tenant_id=account.tenant_id,
                account_id=account.id,
                profile_version=1,
                custody_mode="customer_device",
                state="ACTIVE",
                constraints_json="[]",
            )
            session.add(profile)
            session.commit()
        account_id, profile_id = account.id, profile.id
        session.execute(
            update(SessionLease)
            .where(
                SessionLease.account_id == account_id,
                SessionLease.released_at.is_(None),
            )
            .values(released_at=datetime.now(UTC))
        )
        session.commit()

    def acquire(holder: str) -> tuple[str, int] | None:
        with SessionLocal() as session:
            account = session.get(PlatformAccount, account_id)
            profile = session.get(BrowserProfile, profile_id)
            assert account is not None and profile is not None
            try:
                lease = acquire_session_lease(
                    session,
                    account,
                    profile,
                    holder,
                    "query",
                    timedelta(minutes=5),
                )
                session.commit()
                return holder, lease.fencing_token
            except LeaseBusyError:
                session.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ["worker-a", "worker-b"]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1


def test_break_glass_requires_two_distinct_non_requester_approvals() -> None:
    client = TestClient(app)
    requester = "break-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, requester)
    requester_headers = headers(tenant, requester)
    account = client.post(
        "/api/v2/platform-accounts",
        headers=requester_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "fixture-***77",
            "owner_pub_id": "own_break",
            "purpose": "measure",
            "responsible_pub_id": "usr_break",
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    ).json()
    reviewer = "reviewer-" + secrets.token_hex(5)
    second_admin = "admin2-" + secrets.token_hex(5)
    for subject, role in [(reviewer, "reviewer"), (second_admin, "admin")]:
        assert (
            client.post(
                "/api/v2/identity/members",
                headers=requester_headers,
                json={"subject": subject, "display_name": subject, "role": role},
            ).status_code
            == 201
        )
    request = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/break-glass",
        headers=requester_headers,
        json={
            "reason": "Investigate a confirmed profile integrity incident",
            "ttl_seconds": 600,
        },
    )
    assert request.status_code == 201
    request_id = request.json()["pub_id"]
    listed = client.get("/api/v2/break-glass", headers=requester_headers)
    assert listed.status_code == 200
    listed_request = next(item for item in listed.json() if item["pub_id"] == request_id)
    assert listed_request["approvals"] == 0
    assert listed_request.get("capability_token") is None
    assert (
        client.post(
            f"/api/v2/break-glass/{request_id}/approve", headers=requester_headers
        ).status_code
        == 403
    )
    first = client.post(
        f"/api/v2/break-glass/{request_id}/approve",
        headers=headers(tenant, reviewer, "reviewer"),
    )
    assert first.status_code == 200
    assert first.json()["approvals"] == 1
    assert first.json()["capability_token"] is None
    second = client.post(
        f"/api/v2/break-glass/{request_id}/approve",
        headers=headers(tenant, second_admin, "admin"),
    )
    assert second.status_code == 200
    assert second.json()["approvals"] == 2
    assert second.json()["state"] == "approved"
    assert len(second.json()["capability_token"]) >= 32


def test_worker_fenced_profile_cas_and_real_l0_probe() -> None:
    client = TestClient(app)
    admin = "cas-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, admin)
    admin_headers = headers(tenant, admin)
    account = client.post(
        "/api/v2/platform-accounts",
        headers=admin_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "fixture-cas-***",
            "owner_pub_id": "own_cas",
            "purpose": "measure",
            "responsible_pub_id": "usr_cas",
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    ).json()
    now = datetime.now(UTC)
    assert (
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
            headers=admin_headers,
            json={
                "scopes": ["read", "query"],
                "regions": ["CN-BJ"],
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
            },
        ).status_code
        == 201
    )
    profile = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
        headers=admin_headers,
        json={
            "profile_payload": '{"version":1}',
            "custody_mode": "server",
            "constraints": ["READ_ONLY"],
        },
    ).json()
    worker = client.post(
        "/api/v2/identity/service-accounts",
        headers=admin_headers,
        json={"name": "CAS Worker", "expires_in_hours": 1},
    ).json()
    worker_headers = headers(tenant, worker["subject"], "worker")
    worker_headers["X-Service-Token"] = worker["token"]
    lease = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/leases",
        headers=worker_headers,
        json={
            "profile_pub_id": profile["pub_id"],
            "holder": worker["subject"],
            "capability": "query",
            "ttl_seconds": 300,
        },
    )
    assert lease.status_code == 201, lease.text
    sealed = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/seal",
        headers=worker_headers,
        json={
            "lease_pub_id": lease.json()["pub_id"],
            "fencing_token": lease.json()["fencing_token"],
            "expected_profile_version": 1,
            "profile_payload": '{"version":2,"sliding":true}',
        },
    )
    assert sealed.status_code == 201
    assert sealed.json()["profile_version"] == 2
    assert (
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/profiles/seal",
            headers=worker_headers,
            json={
                "lease_pub_id": lease.json()["pub_id"],
                "fencing_token": lease.json()["fencing_token"],
                "expected_profile_version": 1,
                "profile_payload": '{"stale":true}',
            },
        ).status_code
        == 409
    )
    health = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/health-checks",
        headers=admin_headers,
    )
    assert health.status_code == 202
    assert health.json()["levels"]["L0"] == "passed"
    with SessionLocal() as session:
        account_row = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account["pub_id"])
        )
        assert account_row is not None
        authorization = session.scalar(
            select(AccountAuthorization)
            .where(AccountAuthorization.account_id == account_row.id)
            .order_by(AccountAuthorization.created_at.desc())
        )
        assert authorization is not None
        authorization.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired_health = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/health-checks",
        headers=admin_headers,
    )
    assert expired_health.status_code == 403
    expired_enrollment = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
        headers=admin_headers,
        json={
            "profile_payload": '{"fixture":"must-not-enroll"}',
            "custody_mode": "server",
            "constraints": ["READ_ONLY"],
        },
    )
    assert expired_enrollment.status_code == 403


def test_wrong_account_profile_ciphertext_is_quarantined() -> None:
    client = TestClient(app)
    admin = "isolation-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, admin)
    admin_headers = headers(tenant, admin)
    profile_ids: list[str] = []
    account_ids: list[str] = []
    for suffix in ("a", "b"):
        account = client.post(
            "/api/v2/platform-accounts",
            headers=admin_headers,
            json={
                "platform_slug": "fixed",
                "platform_name": "Fixed",
                "account_mask": f"fixture-isolation-{suffix}-***",
                "owner_pub_id": f"owner_{suffix}",
                "purpose": "measure",
                "responsible_pub_id": admin,
                "custody_mode": "server",
                "region": "CN-BJ",
            },
        ).json()
        authorization = client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
            headers=admin_headers,
            json={
                "scopes": ["query"],
                "forbidden_actions": [],
                "regions": ["CN-BJ"],
                "valid_from": datetime.now(UTC).isoformat(),
                "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert authorization.status_code == 201
        profile = client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
            headers=admin_headers,
            json={
                "profile_payload": f'{{"account":"{suffix}"}}',
                "custody_mode": "server",
                "constraints": ["READ_ONLY"],
            },
        ).json()
        account_ids.append(account["pub_id"])
        profile_ids.append(profile["pub_id"])
    with SessionLocal() as session:
        first = session.scalar(
            select(BrowserProfile).where(BrowserProfile.pub_id == profile_ids[0])
        )
        second = session.scalar(
            select(BrowserProfile).where(BrowserProfile.pub_id == profile_ids[1])
        )
        assert first is not None and second is not None
        first.ciphertext = second.ciphertext
        first.nonce = second.nonce
        first.wrapped_dek = second.wrapped_dek
        first.ciphertext_sha256 = second.ciphertext_sha256
        session.commit()
    health = client.post(
        f"/api/v2/platform-accounts/{account_ids[0]}/health-checks",
        headers=admin_headers,
    )
    assert health.status_code == 202
    assert health.json()["levels"]["L0"] == "failed_quarantined"
    accounts = client.get("/api/v2/platform-accounts", headers=admin_headers).json()
    isolated = next(item for item in accounts if item["pub_id"] == account_ids[0])
    assert isolated["state"] == "quarantined"
    assert isolated["profile_state"] == "QUARANTINED"


def test_profile_dek_rekey_is_fenced_idempotent_versioned_and_secret_free() -> None:
    client = TestClient(app)
    admin = "rekey-admin-" + secrets.token_hex(5)
    tenant = bootstrap(client, admin)
    admin_headers = headers(tenant, admin)
    account = client.post(
        "/api/v2/platform-accounts",
        headers=admin_headers,
        json={
            "platform_slug": "fixed",
            "platform_name": "Fixed",
            "account_mask": "rekey-***",
            "owner_pub_id": "own_rekey",
            "purpose": "measure",
            "responsible_pub_id": "usr_rekey",
            "custody_mode": "server",
            "region": "CN-BJ",
        },
    ).json()
    now = datetime.now(UTC)
    assert (
        client.post(
            f"/api/v2/platform-accounts/{account['pub_id']}/authorizations",
            headers=admin_headers,
            json={
                "scopes": ["read", "query"],
                "regions": ["CN-BJ"],
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
            },
        ).status_code
        == 201
    )
    profile = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/enroll",
        headers=admin_headers,
        json={
            "profile_payload": '{"fixture":"rekey-sensitive-value"}',
            "custody_mode": "server",
            "constraints": ["READ_ONLY"],
        },
    ).json()
    worker = client.post(
        "/api/v2/identity/service-accounts",
        headers=admin_headers,
        json={"name": "Rekey Worker", "expires_in_hours": 1},
    ).json()
    worker_headers = headers(tenant, worker["subject"], "worker")
    worker_headers["X-Service-Token"] = worker["token"]
    lease = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/leases",
        headers=worker_headers,
        json={
            "profile_pub_id": profile["pub_id"],
            "holder": worker["subject"],
            "capability": "query",
            "ttl_seconds": 300,
        },
    ).json()
    with SessionLocal() as session:
        before = session.scalar(
            select(BrowserProfile).where(BrowserProfile.pub_id == profile["pub_id"])
        )
        assert before is not None
        before_ciphertext = before.ciphertext
        before_wrapped_dek = before.wrapped_dek

    idempotency_key = "rekey-" + secrets.token_hex(16)
    request_headers = {**worker_headers, "Idempotency-Key": idempotency_key}
    request_body = {
        "lease_pub_id": lease["pub_id"],
        "fencing_token": lease["fencing_token"],
        "expected_profile_version": 1,
        "reason": "scheduled_rotation",
    }
    rotated = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/rekey",
        headers=request_headers,
        json=request_body,
    )
    assert rotated.status_code == 201, rotated.text
    assert rotated.json()["profile_version"] == 2
    assert rotated.json()["ciphertext_sha256"] != profile["ciphertext_sha256"]
    replay = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/rekey",
        headers=request_headers,
        json=request_body,
    )
    assert replay.status_code == 201
    assert replay.json() == rotated.json()
    conflict = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/rekey",
        headers=request_headers,
        json={**request_body, "reason": "incident_recovery"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    stale_lease = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/profiles/seal",
        headers=worker_headers,
        json={
            "lease_pub_id": lease["pub_id"],
            "fencing_token": lease["fencing_token"],
            "expected_profile_version": 2,
            "profile_payload": '{"must":"fail"}',
        },
    )
    assert stale_lease.status_code == 409
    assert stale_lease.json()["error"]["code"] == "profile_lease_mismatch"
    replacement_lease = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/leases",
        headers=worker_headers,
        json={
            "profile_pub_id": rotated.json()["pub_id"],
            "holder": worker["subject"],
            "capability": "query",
            "ttl_seconds": 300,
        },
    )
    assert replacement_lease.status_code == 201, replacement_lease.text
    assert replacement_lease.json()["fencing_token"] == lease["fencing_token"] + 1
    with SessionLocal() as session:
        profiles = session.scalars(
            select(BrowserProfile)
            .where(BrowserProfile.account_id == before.account_id)
            .order_by(BrowserProfile.profile_version)
        ).all()
        assert [item.state for item in profiles] == ["SUPERSEDED", "ACTIVE"]
        assert profiles[1].ciphertext != before_ciphertext
        assert profiles[1].wrapped_dek != before_wrapped_dek
        event = session.scalar(
            select(AuditLog).where(
                AuditLog.tenant_id == profiles[1].tenant_id,
                AuditLog.resource_pub_id == profiles[1].pub_id,
                AuditLog.action.like("profile.rekeyed:%"),
            )
        )
        assert event is not None
        assert "rekey-sensitive-value" not in event.receipt
    health = client.post(
        f"/api/v2/platform-accounts/{account['pub_id']}/health-checks",
        headers=admin_headers,
    )
    assert health.status_code == 202
    assert health.json()["levels"]["L0"] == "passed"
