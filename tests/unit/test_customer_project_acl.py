import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role
from geo_platform.identity.project_access import (
    clear_customer_project_acl_cache,
    customer_allowed_project_ids,
    enforce_customer_project_access,
    enforce_customer_project_request,
)
from geo_platform.metrics_v2 import router as metrics_router

TENANT = "tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y"
ZHONGYING_USER = "usr_518N8AMHPB7R9M7D46G63J81MP"
ZHONGYING_PROJECT = "prj_5W3N6H932WCJY1NVEK5JQD727R"
SHENGBANG_PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"


@pytest.fixture(autouse=True)
def reset_acl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEO_CUSTOMER_PROJECT_ACL_PATH", raising=False)
    clear_customer_project_acl_cache()
    yield
    clear_customer_project_acl_cache()


def _write_acl(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": [
                    {
                        "tenant_pub_id": TENANT,
                        "user_pub_id": ZHONGYING_USER,
                        "project_pub_ids": [ZHONGYING_PROJECT],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _request(
    path: str,
    *,
    project_pub_id: str | None = None,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> Request:
    headers = []
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers,
        }
    )
    if body is not None:
        request._body = json.dumps(body).encode()  # noqa: SLF001 - emulate FastAPI body cache
    request.scope["path_params"] = (
        {"project_pub_id": project_pub_id} if project_pub_id is not None else {}
    )
    return request


def test_policy_is_opt_in_for_existing_development_fixtures() -> None:
    assert (
        customer_allowed_project_ids(
            role="customer", tenant_pub_id=TENANT, user_pub_id=ZHONGYING_USER
        )
        is None
    )


def test_bound_customer_receives_only_the_configured_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "customer-project-acl.json"
    _write_acl(path)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", os.fspath(path))
    clear_customer_project_acl_cache()

    assert customer_allowed_project_ids(
        role="customer", tenant_pub_id=TENANT, user_pub_id=ZHONGYING_USER
    ) == frozenset({ZHONGYING_PROJECT})
    assert (
        customer_allowed_project_ids(
            role="customer", tenant_pub_id=TENANT, user_pub_id="usr_UNBOUND12345"
        )
        == frozenset()
    )
    assert (
        customer_allowed_project_ids(
            role="admin", tenant_pub_id=TENANT, user_pub_id="usr_UNBOUND12345"
        )
        is None
    )


def test_cross_project_path_is_hidden_from_customer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "customer-project-acl.json"
    _write_acl(path)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", os.fspath(path))
    clear_customer_project_acl_cache()

    enforce_customer_project_request(
        _request(f"/api/v2/projects/{ZHONGYING_PROJECT}", project_pub_id=ZHONGYING_PROJECT),
        role="customer",
        tenant_pub_id=TENANT,
        user_pub_id=ZHONGYING_USER,
    )
    with pytest.raises(HTTPException) as excinfo:
        enforce_customer_project_request(
            _request(f"/api/v2/projects/{SHENGBANG_PROJECT}", project_pub_id=SHENGBANG_PROJECT),
            role="customer",
            tenant_pub_id=TENANT,
            user_pub_id=ZHONGYING_USER,
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {"code": "project_not_found"}


def test_cross_project_json_body_is_hidden_from_customer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "customer-project-acl.json"
    _write_acl(path)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", os.fspath(path))
    clear_customer_project_acl_cache()

    enforce_customer_project_request(
        _request(
            "/api/v2/exports/metrics",
            method="POST",
            body={"project_pub_id": ZHONGYING_PROJECT},
        ),
        role="customer",
        tenant_pub_id=TENANT,
        user_pub_id=ZHONGYING_USER,
    )
    with pytest.raises(HTTPException) as excinfo:
        enforce_customer_project_request(
            _request(
                "/api/v2/exports/metrics",
                method="POST",
                body={"project_pub_id": SHENGBANG_PROJECT},
            ),
            role="customer",
            tenant_pub_id=TENANT,
            user_pub_id=ZHONGYING_USER,
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {"code": "project_not_found"}


def test_fastapi_dependency_checks_json_body_before_route_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "customer-project-acl.json"
    _write_acl(path)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", os.fspath(path))
    clear_customer_project_acl_cache()
    app = FastAPI()

    def project_acl(request: Request) -> None:
        enforce_customer_project_request(
            request,
            role="customer",
            tenant_pub_id=TENANT,
            user_pub_id=ZHONGYING_USER,
        )

    @app.post("/api/v2/exports/metrics")
    def export_metrics(body: dict[str, object], _acl: None = Depends(project_acl)) -> dict:
        return body

    client = TestClient(app)
    allowed = client.post("/api/v2/exports/metrics", json={"project_pub_id": ZHONGYING_PROJECT})
    denied = client.post("/api/v2/exports/metrics", json={"project_pub_id": SHENGBANG_PROJECT})

    assert allowed.status_code == 200
    assert denied.status_code == 404
    assert denied.json() == {"detail": {"code": "project_not_found"}}


def test_unsafe_acl_file_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "customer-project-acl.json"
    _write_acl(path)
    path.chmod(0o666)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", os.fspath(path))
    clear_customer_project_acl_cache()

    with pytest.raises(HTTPException) as excinfo:
        customer_allowed_project_ids(
            role="customer", tenant_pub_id=TENANT, user_pub_id=ZHONGYING_USER
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {"code": "customer_project_acl_unavailable"}


def _configure_acl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "customer-project-acl.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": [
                    {
                        "tenant_pub_id": "tnt_acme",
                        "user_pub_id": "usr_alice",
                        "project_pub_ids": ["prj_owned"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    monkeypatch.setenv("GEO_CUSTOMER_PROJECT_ACL_PATH", str(path))
    clear_customer_project_acl_cache()


def test_customer_acl_hides_unowned_direct_and_request_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_acl(monkeypatch, tmp_path)

    enforce_customer_project_access(
        "prj_owned",
        role="customer",
        tenant_pub_id="tnt_acme",
        user_pub_id="usr_alice",
    )
    with pytest.raises(HTTPException) as excinfo:
        enforce_customer_project_access(
            "prj_other",
            role="customer",
            tenant_pub_id="tnt_acme",
            user_pub_id="usr_alice",
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {"code": "project_not_found"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/projects/prj_other",
            "headers": [],
            "query_string": b"",
            "path_params": {"project_pub_id": "prj_other"},
        }
    )
    with pytest.raises(HTTPException) as request_exc:
        enforce_customer_project_request(
            request,
            role="customer",
            tenant_pub_id="tnt_acme",
            user_pub_id="usr_alice",
        )
    assert request_exc.value.status_code == 404


def test_acl_disabled_and_non_customer_preserve_existing_tenant_scope() -> None:
    enforce_customer_project_access(
        "prj_any",
        role="customer",
        tenant_pub_id="tnt_acme",
        user_pub_id="usr_alice",
    )
    enforce_customer_project_access(
        "prj_any",
        role="operator",
        tenant_pub_id="tnt_acme",
        user_pub_id="usr_operator",
    )


def test_metrics_indirect_snapshot_set_is_hidden_before_return(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_acl(monkeypatch, tmp_path)
    monkeypatch.setattr(
        metrics_router,
        "_service",
        lambda: SimpleNamespace(
            snapshot_set=lambda **_kwargs: SimpleNamespace(project_pub_id="prj_other")
        ),
    )
    principal = Principal(
        "alice", Role.CUSTOMER, "tnt_acme", user_pub_id="usr_alice"
    )

    with pytest.raises(HTTPException) as excinfo:
        metrics_router.snapshot_set_v2(
            response=Response(),
            set_pub_id="mss_other",
            principal=principal,
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {"code": "project_not_found"}


def test_all_metrics_project_resource_handlers_apply_customer_acl() -> None:
    direct_handlers = (
        metrics_router.current_snapshot_set_v2,
        metrics_router.request_snapshot_set_v2,
        metrics_router.recompute_metrics_v2,
        metrics_router.override_semantic_decision_v2,
    )
    returned_project_handlers = (
        metrics_router.snapshot_job_v2,
        metrics_router.decision_job_v2,
        metrics_router.snapshot_set_v2,
        metrics_router.semantic_event_v2,
        metrics_router.semantic_decision_v2,
        metrics_router.metric_export_v2,
    )
    snapshot_handlers = (
        metrics_router.metric_snapshot_v2,
        metrics_router.metric_query_contributions_v2,
        metrics_router.metric_contributions_v2,
    )

    for handler in direct_handlers + returned_project_handlers:
        assert "_enforce_customer_project" in inspect.getsource(handler), handler.__name__
    for handler in snapshot_handlers:
        assert "_enforce_snapshot_project" in inspect.getsource(handler), handler.__name__
