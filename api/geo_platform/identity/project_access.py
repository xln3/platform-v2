"""Server-side project allow-list for customer identities.

The existing tenancy membership grants a role within a tenant.  Customer
accounts need a narrower boundary: one customer identity must not discover or
read another customer's project in the same tenant.  Production enables this
policy with ``GEO_CUSTOMER_PROJECT_ACL_PATH``.  Development and existing test
fixtures retain the historical tenant-scoped behaviour when the variable is
unset.
"""

from __future__ import annotations

import json
import os
import stat
from functools import lru_cache
from pathlib import Path
from typing import Any

from anyio import from_thread
from fastapi import HTTPException, Request

_ACL_PATH_ENV = "GEO_CUSTOMER_PROJECT_ACL_PATH"
_MAX_ACL_BYTES = 64 * 1024
_PROJECT_PARAMETER_NAMES = frozenset({"project", "project_id", "project_pub_id"})


class CustomerProjectAclConfigurationError(RuntimeError):
    """The configured customer project policy cannot be trusted."""


def _required_identifier(value: Any, *, prefix: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(f"{prefix}_")
        or len(value) < len(prefix) + 5
        or len(value) > 120
        or not all(character.isalnum() or character in {"_", "-"} for character in value)
    ):
        raise CustomerProjectAclConfigurationError(f"invalid_{field}")
    return value


@lru_cache(maxsize=1)
def _configured_bindings() -> dict[tuple[str, str], frozenset[str]] | None:
    raw_path = os.environ.get(_ACL_PATH_ENV, "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        raise CustomerProjectAclConfigurationError("acl_path_not_absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CustomerProjectAclConfigurationError("acl_file_unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CustomerProjectAclConfigurationError("acl_file_not_regular")
    if metadata.st_uid not in {0, os.geteuid()} or metadata.st_mode & 0o022:
        raise CustomerProjectAclConfigurationError("acl_file_permissions_unsafe")
    if metadata.st_size < 2 or metadata.st_size > _MAX_ACL_BYTES:
        raise CustomerProjectAclConfigurationError("acl_file_size_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CustomerProjectAclConfigurationError("acl_file_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise CustomerProjectAclConfigurationError("acl_version_invalid")
    rows = payload.get("bindings")
    if not isinstance(rows, list):
        raise CustomerProjectAclConfigurationError("acl_bindings_invalid")
    bindings: dict[tuple[str, str], frozenset[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "tenant_pub_id",
            "user_pub_id",
            "project_pub_ids",
        }:
            raise CustomerProjectAclConfigurationError("acl_binding_shape_invalid")
        tenant_pub_id = _required_identifier(
            row["tenant_pub_id"], prefix="tnt", field="tenant_pub_id"
        )
        user_pub_id = _required_identifier(row["user_pub_id"], prefix="usr", field="user_pub_id")
        raw_projects = row["project_pub_ids"]
        if not isinstance(raw_projects, list) or not raw_projects:
            raise CustomerProjectAclConfigurationError("acl_project_ids_invalid")
        project_pub_ids = frozenset(
            _required_identifier(value, prefix="prj", field="project_pub_id")
            for value in raw_projects
        )
        if len(project_pub_ids) != len(raw_projects):
            raise CustomerProjectAclConfigurationError("acl_project_id_duplicate")
        key = (tenant_pub_id, user_pub_id)
        if key in bindings:
            raise CustomerProjectAclConfigurationError("acl_identity_duplicate")
        bindings[key] = project_pub_ids
    return bindings


def clear_customer_project_acl_cache() -> None:
    """Reload the policy on the next request; production normally restarts instead."""

    _configured_bindings.cache_clear()


def customer_allowed_project_ids(
    *, role: str, tenant_pub_id: str, user_pub_id: str | None
) -> frozenset[str] | None:
    """Return ``None`` when the optional policy is disabled, else the exact allow-list."""

    if role != "customer":
        return None
    try:
        bindings = _configured_bindings()
    except CustomerProjectAclConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "customer_project_acl_unavailable"},
        ) from exc
    if bindings is None:
        return None
    if user_pub_id is None:
        return frozenset()
    return bindings.get((tenant_pub_id, user_pub_id), frozenset())


def enforce_customer_project_access(
    project_pub_id: str,
    *,
    role: str,
    tenant_pub_id: str,
    user_pub_id: str | None,
) -> None:
    """Hide a project that is outside a customer's configured allow-list."""

    allowed = customer_allowed_project_ids(
        role=role,
        tenant_pub_id=tenant_pub_id,
        user_pub_id=user_pub_id,
    )
    if allowed is not None and project_pub_id not in allowed:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})


def _project_values(raw_value: Any) -> set[str]:
    if isinstance(raw_value, str):
        return {value.strip() for value in raw_value.split(",") if value.strip()}
    if isinstance(raw_value, list):
        values: set[str] = set()
        for item in raw_value:
            if isinstance(item, str) and item.strip():
                values.add(item.strip())
        return values
    return set()


def _json_request_body(request: Request) -> Any | None:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return None

    # FastAPI caches request bodies before resolving dependencies for routes
    # with Body parameters.  The from-thread path also covers dependencies on
    # routes that read JSON themselves without relying on Starlette internals.
    raw_body = getattr(request, "_body", None)
    if raw_body is None:
        if request.headers.get("content-length") == "0":
            return None
        try:
            raw_body = from_thread.run(request.body)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "customer_project_acl_unavailable"},
            ) from exc
    if not raw_body:
        return None
    try:
        return json.loads(raw_body)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_json_body"}) from exc


def _request_project_ids(request: Request) -> frozenset[str]:
    values: set[str] = set()
    for key, raw_value in request.path_params.items():
        if key.lower() not in _PROJECT_PARAMETER_NAMES:
            continue
        values.update(_project_values(str(raw_value)))
    for key, raw_value in request.query_params.multi_items():
        if key.lower() not in _PROJECT_PARAMETER_NAMES:
            continue
        values.update(_project_values(raw_value))
    body = _json_request_body(request)
    if isinstance(body, dict):
        for key, raw_value in body.items():
            if key.lower() in _PROJECT_PARAMETER_NAMES:
                values.update(_project_values(raw_value))
    return frozenset(values)


def enforce_customer_project_request(
    request: Request,
    *,
    role: str,
    tenant_pub_id: str,
    user_pub_id: str | None,
) -> None:
    """Reject an explicit cross-project request before its route handler executes."""

    allowed = customer_allowed_project_ids(
        role=role,
        tenant_pub_id=tenant_pub_id,
        user_pub_id=user_pub_id,
    )
    if allowed is None:
        return
    requested = _request_project_ids(request)
    if not requested.issubset(allowed):
        # Match an ordinary tenant-scoped miss so callers cannot enumerate projects.
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
