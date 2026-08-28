"""Versioned, snapshot-only customer metrics API.

The package deliberately keeps reads separate from metric computation.  HTTP
requests may select an immutable snapshot or enqueue a build, but never execute
the measurement engine inline.
"""

from .service import MetricsV2Service

__all__ = ["MetricsV2Service"]
