"""Shared Operations lifecycle state semantics consumed by read projections."""

from datetime import timedelta

TERMINAL_RUN_STATES: frozenset[str] = frozenset(
    {"completed", "completed_with_failures", "failed", "cancelled", "skipped"}
)
RUN_DELAY_THRESHOLD = timedelta(minutes=15)
