"""Load the production-proven Doubao share exporter from the legacy package.

The legacy exporter is intentionally reused during the V2 cutover: it contains the
live-tested selector and download fallbacks, while this bridge keeps that dependency
isolated behind two small calls so it can later be vendored without changing the
collection result contract.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


@lru_cache(maxsize=1)
def _share_export_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[3] / "server" / "proxyllm" / "share_export.py"
    if not module_path.is_file():
        raise RuntimeError(f"Doubao share exporter is unavailable: {module_path}")
    spec = importlib.util.spec_from_file_location("geo_legacy_doubao_share_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Doubao share exporter could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture_share_image(page: Any, out_path: Path) -> dict[str, Any]:
    return dict(_share_export_module().capture_share_image(page, out_path, timeout_s=45.0))


def capture_share_link(page: Any) -> dict[str, Any]:
    return dict(_share_export_module().capture_share_link(page, timeout_s=25.0))
