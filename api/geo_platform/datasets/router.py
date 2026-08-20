# ruff: noqa: B008

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from ..config import get_settings
from ..identity.policy import Principal, get_principal

router = APIRouter(prefix="/api/v2/datasets", tags=["datasets"])

_DATASET_NAME = "media-prices.json"
_SIDECAR_NAME = "media-prices.sha256"
_WEMEDIA_DATASET_NAME = "media-wemedia.json"
_WEMEDIA_SIDECAR_NAME = "media-wemedia.sha256"
_REFRESH_STATUS_NAME = "media-prices.refresh.json"
_REFRESH_LOCK_NAME = "media-prices.refresh.lock"
_REFRESH_REQUEST_NAME = "media-prices.refresh.request.json"
_REFRESH_RUNNING_REQUEST_NAME = "media-prices.refresh.request.running.json"
_REFRESH_LOCK_TTL_SECONDS = 45 * 60
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LOCK_PID_PATTERN = re.compile(r"(?:^|\s)pid=(\d+)(?:\s|$)")
_REFRESH_STATES = frozenset({"running", "done", "failed"})
_SOURCE_STATUSES = frozenset({"ok", "partial", "stale", "failed", "pending"})

# 数据集与 SHA sidecar 的文件签名 → payload，避免每请求重读约 12MB 的数据集制品。
# sidecar 参与签名，确保数据文件与摘要分两次原子替换时，第二次替换会使缓存失效。
_FileSignature = tuple[int, int, int, int]
_cache: dict[
    str,
    tuple[_FileSignature, _FileSignature | None, bytes, str],
] = {}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefreshSourceView(StrictModel):
    status: str
    rows: int
    note: str


class RefreshStatusView(StrictModel):
    state: str
    started_at: str | None = None
    updated_at: str | None = None
    message: str = ""
    sources: dict[str, RefreshSourceView] = {}


def _datasets_dir() -> Path:
    configured = get_settings().datasets_dir
    if configured:
        return Path(configured)
    # router.py → datasets → geo_platform → api → 仓库根
    return Path(__file__).resolve().parents[3] / ".datasets"


def _sidecar_sha256(base: Path, sidecar_name: str, payload: bytes) -> str | None:
    sidecar = base / sidecar_name
    try:
        token = sidecar.read_text(encoding="utf-8").split()[0].strip().lower()
    except (OSError, IndexError):
        token = ""
    if _SHA256_PATTERN.match(token):
        return token
    return hashlib.sha256(payload).hexdigest()


def _file_signature(path: Path) -> _FileSignature | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)


def _read_dataset(
    dataset_name: str = _DATASET_NAME,
    sidecar_name: str = _SIDECAR_NAME,
) -> tuple[bytes, str] | None:
    base = _datasets_dir()
    path = base / dataset_name
    dataset_signature = _file_signature(path)
    if dataset_signature is None or not path.is_file():
        return None
    sidecar_signature = _file_signature(base / sidecar_name)
    cached = _cache.get(dataset_name)
    if cached is not None and cached[0] == dataset_signature and cached[1] == sidecar_signature:
        return cached[2], cached[3]
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    sha256 = _sidecar_sha256(base, sidecar_name, payload)
    if sha256 is None:
        return None
    _cache[dataset_name] = (dataset_signature, sidecar_signature, payload, sha256)
    return payload, sha256


def _dataset_response(dataset_name: str, sidecar_name: str) -> Response:
    loaded = _read_dataset(dataset_name, sidecar_name)
    if loaded is None:
        raise HTTPException(status_code=404, detail={"code": "dataset_not_found"})
    payload, sha256 = loaded
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "X-Dataset-Sha256": sha256,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _refresh_lock_active(lock: Path) -> bool:
    try:
        lock_text = lock.read_text(encoding="utf-8")
    except OSError:
        return False
    match = _LOCK_PID_PATTERN.search(lock_text)
    if match is not None:
        pid = int(match.group(1))
        if pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    return age <= _REFRESH_LOCK_TTL_SECONDS


def _request_timestamp(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    requested_at = payload.get("requested_at")
    return requested_at if isinstance(requested_at, str) else None


def _queued_refresh_status(base: Path) -> RefreshStatusView | None:
    queued = base / _REFRESH_REQUEST_NAME
    if queued.is_file():
        requested_at = _request_timestamp(queued)
        return RefreshStatusView(
            state="running",
            started_at=requested_at,
            updated_at=requested_at,
            message="refresh_queued",
        )
    running = base / _REFRESH_RUNNING_REQUEST_NAME
    if running.is_file() and not _refresh_lock_active(base / _REFRESH_LOCK_NAME):
        requested_at = _request_timestamp(running)
        return RefreshStatusView(
            state="running",
            started_at=requested_at,
            updated_at=requested_at,
            message="refresh_starting",
        )
    return None


def _read_refresh_status() -> RefreshStatusView:
    base = _datasets_dir()
    queued_status = _queued_refresh_status(base)
    if queued_status is not None:
        return queued_status
    path = base / _REFRESH_STATUS_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RefreshStatusView(state="never")
    if not isinstance(payload, dict):
        return RefreshStatusView(state="never")
    state = payload.get("state")
    sources_raw = payload.get("sources")
    sources: dict[str, RefreshSourceView] = {}
    if isinstance(sources_raw, dict):
        for name, item in sources_raw.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                continue
            status = item.get("status")
            rows = item.get("rows")
            note = item.get("note")
            sources[name] = RefreshSourceView(
                status=status if status in _SOURCE_STATUSES else "failed",
                rows=rows if isinstance(rows, int) and rows >= 0 else 0,
                note=note if isinstance(note, str) else "",
            )
    started = payload.get("started_at")
    updated = payload.get("updated_at")
    message = payload.get("message")
    view = RefreshStatusView(
        state=state if state in _REFRESH_STATES else "never",
        started_at=started if isinstance(started, str) else None,
        updated_at=updated if isinstance(updated, str) else None,
        message=message if isinstance(message, str) else "",
        sources=sources,
    )
    if view.state == "running" and not _refresh_lock_active(base / _REFRESH_LOCK_NAME):
        return RefreshStatusView(
            state="failed",
            started_at=view.started_at,
            updated_at=view.updated_at,
            message="refresh_interrupted_published_dataset_unchanged",
            sources=view.sources,
        )
    return view


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _enqueue_refresh(base: Path, *, tenant_pub_id: str) -> str:
    requested_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    request_path = base / _REFRESH_REQUEST_NAME
    payload = json.dumps(
        {
            "version": 1,
            "requested_at": requested_at,
            "tenant_pub_id": tenant_pub_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(request_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "refresh_already_running"},
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(base)
    except Exception:
        request_path.unlink(missing_ok=True)
        raise
    return requested_at


def _public_dataset_request_compatibility(
    _x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    _x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    _x_actor_role: str | None = Header(default=None, alias="X-Actor-Role"),
    _x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    _authorization: str | None = Header(default=None, alias="Authorization"),
    _native_token: str | None = Cookie(default=None, alias="__Host-geo_session"),
    _development_native_token: str | None = Cookie(default=None, alias="geo_session"),
    _oidc_browser_token: str | None = Cookie(default=None, alias="__Host-geo_oidc"),
) -> None:
    """Keep existing generated-client parameters while permitting anonymous snapshot reads."""


@router.get("/media-prices")
def media_prices_dataset(
    _compatibility: None = Depends(_public_dataset_request_compatibility),
) -> Response:
    return _dataset_response(_DATASET_NAME, _SIDECAR_NAME)


@router.get("/media-wemedia")
def media_wemedia_dataset(
    _compatibility: None = Depends(_public_dataset_request_compatibility),
) -> Response:
    return _dataset_response(_WEMEDIA_DATASET_NAME, _WEMEDIA_SIDECAR_NAME)


@router.post("/media-prices/refresh", status_code=202)
def media_prices_refresh(principal: Principal = Depends(get_principal)) -> RefreshStatusView:
    principal.require("account:operate")
    base = _datasets_dir()
    base.mkdir(parents=True, exist_ok=True)
    if _refresh_lock_active(base / _REFRESH_LOCK_NAME) or any(
        (base / name).exists() for name in (_REFRESH_REQUEST_NAME, _REFRESH_RUNNING_REQUEST_NAME)
    ):
        raise HTTPException(status_code=409, detail={"code": "refresh_already_running"})
    requested_at = _enqueue_refresh(base, tenant_pub_id=principal.tenant_pub_id)
    return RefreshStatusView(
        state="running",
        started_at=requested_at,
        updated_at=requested_at,
        message="refresh_queued",
    )


@router.get("/media-prices/refresh-status")
def media_prices_refresh_status(
    response: Response, principal: Principal = Depends(get_principal)
) -> RefreshStatusView:
    principal.require("account:read")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return _read_refresh_status()
