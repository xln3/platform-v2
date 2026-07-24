import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from geo_platform.collection.leases import LeaseBusyError, acquire_session_lease
from geo_platform.collection.models import BrowserProfile, PlatformAccount, SessionLease
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import Permission, RoleDefinition, RolePermission
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
