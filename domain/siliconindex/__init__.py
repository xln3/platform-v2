"""Versioned, read-only SiliconIndex snapshot consumption."""

from .adapter import SiliconIndexAdapter, load_datasets, project_brand_domain
from .publisher import preview_change_bundle, publish_change_bundle
from .snapshot import SiliconIndexSyncError, SiliconIndexSynchronizer, validate_snapshot

__all__ = [
    "SiliconIndexAdapter",
    "SiliconIndexSyncError",
    "SiliconIndexSynchronizer",
    "load_datasets",
    "project_brand_domain",
    "preview_change_bundle",
    "publish_change_bundle",
    "validate_snapshot",
]
