from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response
from geo_platform.identity.policy import Principal, Role
from geo_platform.identity.project_access import (
    clear_customer_project_acl_cache,
    enforce_customer_project_access,
    enforce_customer_project_request,
)
from geo_platform.metrics_v2 import router as metrics_router


@pytest.fixture(autouse=True)
def _clear_acl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_CUSTOMER_PROJECT_ACL_PATH", raising=False)
    clear_customer_project_acl_cache()
    yield
    clear_customer_project_acl_cache()


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
