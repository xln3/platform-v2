"""Versioned, read-only SiliconIndex snapshot consumption."""

from .adapter import SiliconIndexAdapter, load_datasets, project_brand_domain
from .snapshot import SiliconIndexSyncError, SiliconIndexSynchronizer, validate_snapshot

__all__ = [
    "SiliconIndexAdapter",
    "SiliconIndexSyncError",
    "SiliconIndexSynchronizer",
    "load_datasets",
    "project_brand_domain",
    "validate_snapshot",
]
