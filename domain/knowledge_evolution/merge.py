"""Deterministic base/upstream/local merge without last-write-wins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MergeConflict:
    path: str
    base: Any
    upstream: Any
    local: Any


@dataclass(frozen=True, slots=True)
class MergeResult:
    merged: Any
    conflicts: tuple[MergeConflict, ...]


_MISSING = object()


def _display(value: Any) -> Any:
    return {"$missing": True} if value is _MISSING else value


def _merge(base: Any, upstream: Any, local: Any, path: str) -> tuple[Any, list[MergeConflict]]:
    if local == base:
        return upstream, []
    if upstream == base or upstream == local:
        return local, []
    if all(value is _MISSING or isinstance(value, dict) for value in (base, upstream, local)):
        base_map = {} if base is _MISSING else base
        upstream_map = {} if upstream is _MISSING else upstream
        local_map = {} if local is _MISSING else local
        keys = sorted(set(base_map) | set(upstream_map) | set(local_map))
        output: dict[str, Any] = {}
        conflicts: list[MergeConflict] = []
        for key in keys:
            child_path = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            value, child_conflicts = _merge(
                base_map.get(key, _MISSING),
                upstream_map.get(key, _MISSING),
                local_map.get(key, _MISSING),
                child_path,
            )
            if value is not _MISSING:
                output[key] = value
            conflicts.extend(child_conflicts)
        return output, conflicts
    conflict = MergeConflict(
        path=path or "/",
        base=_display(base),
        upstream=_display(upstream),
        local=_display(local),
    )
    # A conflict never silently chooses a writer.  Keep local in the preview so
    # callers can inspect a complete document, but publication must reject while
    # ``conflicts`` is non-empty.
    return local, [conflict]


def three_way_merge(base: Any, upstream: Any, local: Any) -> MergeResult:
    merged, conflicts = _merge(base, upstream, local, "")
    return MergeResult(merged=merged, conflicts=tuple(conflicts))
