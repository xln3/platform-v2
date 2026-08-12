"""Load the production-proven Doubao share exporter from the legacy package.

The legacy exporter is intentionally reused during the V2 cutover: it contains the
live-tested selector and download fallbacks, while this bridge keeps that dependency
isolated behind two small calls so it can later be vendored without changing the
collection result contract.
"""

from __future__ import annotations

import os
from functools import lru_cache
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType
from typing import Any

ENV_SHARE_EXPORTER_PATH = "GEO_DOUBAO_SHARE_EXPORTER_PATH"


def _share_export_path() -> Path:
    """Resolve the legacy exporter without assuming a particular worktree depth.

    Production releases are activated from detached worktrees.  Deriving the
    monorepo root solely from ``__file__`` therefore points at the release cache,
    where the legacy ``server`` package is intentionally absent.  Deployments can
    pin the audited exporter explicitly; the historical sibling lookup remains for
    developer checkouts.
    """
    configured = os.environ.get(ENV_SHARE_EXPORTER_PATH, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"{ENV_SHARE_EXPORTER_PATH} must be an absolute path")
        if not path.is_file():
            raise RuntimeError(f"Doubao share exporter is unavailable: {path}")
        return path

    path = Path(__file__).resolve().parents[3] / "server" / "proxyllm" / "share_export.py"
    if not path.is_file():
        raise RuntimeError(
            "Doubao share exporter is unavailable; configure "
            f"{ENV_SHARE_EXPORTER_PATH} with an absolute path (looked for {path})"
        )
    return path


@lru_cache(maxsize=1)
def _share_export_module() -> ModuleType:
    module_path = _share_export_path()
    spec = importlib_util.spec_from_file_location("geo_legacy_doubao_share_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Doubao share exporter could not be loaded")
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture_share_image(page: Any, out_path: Path) -> dict[str, Any]:
    return dict(_share_export_module().capture_share_image(page, out_path, timeout_s=45.0))


def capture_share_link(page: Any) -> dict[str, Any]:
    return dict(_share_export_module().capture_share_link(page, timeout_s=25.0))
