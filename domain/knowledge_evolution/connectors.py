"""Domain-neutral source/sink connector contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .merge import MergeResult


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    adapter: str
    operation: str
    status: str
    base_release_id: str | None = None
    upstream_release_id: str | None = None
    local_release_id: str | None = None
    cursor: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None


class KnowledgeConnector(Protocol):
    adapter_id: str
    adapter_version: str

    def import_release(self, source: str) -> ConnectorResult: ...

    def export_changes(self, changes: tuple[Mapping[str, Any], ...]) -> ConnectorResult: ...

    def reconcile(self, base: Any, upstream: Any, local: Any) -> MergeResult: ...


__all__ = ["ConnectorResult", "KnowledgeConnector"]
