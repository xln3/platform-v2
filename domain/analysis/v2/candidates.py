"""Closed candidate boundaries used before and after semantic judging."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from domain.analysis.v2._canonical import FrozenDomainModel, Sha256Hex, canonical_hash
from domain.analysis.v2.decision_task_schema import CandidateMode, CandidatePolicy

_PATH_SEGMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<many>\[\*?\])?$")


class CandidateBoundaryError(ValueError):
    def __init__(self, code: str, *, path: str | None = None, value: object = None) -> None:
        self.code = code
        self.path = path
        self.value = value
        super().__init__(code)


class Candidate(FrozenDomainModel):
    candidate_id: str = Field(min_length=1, max_length=500)
    candidate_type: str = Field(min_length=1, max_length=100)
    labels: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def labels_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class CandidateSet(FrozenDomainModel):
    """A frozen, hash-bound candidate set visible to one decision task."""

    candidates: tuple[Candidate, ...]
    source_ref: str = Field(min_length=1, max_length=500)
    source_hash: Sha256Hex
    candidate_set_hash: str = ""

    @property
    def candidate_ids(self) -> frozenset[str]:
        return frozenset(candidate.candidate_id for candidate in self.candidates)

    def calculated_hash(self) -> str:
        material = {
            "candidates": sorted(
                (candidate.model_dump(mode="python") for candidate in self.candidates),
                key=lambda item: (str(item["candidate_type"]), str(item["candidate_id"])),
            ),
            "source_hash": self.source_hash,
            "source_ref": self.source_ref,
        }
        return canonical_hash(material)

    @model_validator(mode="after")
    def ids_and_hash_are_valid(self) -> Self:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_ids_must_be_unique")
        calculated = self.calculated_hash()
        if self.candidate_set_hash and self.candidate_set_hash != calculated:
            raise ValueError("candidate_set_hash_mismatch")
        object.__setattr__(self, "candidate_set_hash", calculated)
        return self


def validate_candidate_membership(
    output: Mapping[str, Any],
    *,
    policy: CandidatePolicy,
    candidate_set: CandidateSet | None,
) -> None:
    """Reject any normalized candidate identifier outside the frozen set.

    Open surface discovery permits new literal ``surface`` strings, never a new
    normalized entity ID.  Unmanaged/ambiguous states must therefore use null
    or a declared unresolved label instead of inventing an ID.
    """

    if policy.mode is CandidateMode.NONE:
        return
    if candidate_set is None:
        raise CandidateBoundaryError("candidate_set_required")
    allowed = candidate_set.candidate_ids
    for path in policy.candidate_paths:
        values = values_at_path(output, path)
        for value in values:
            if value is None and policy.allow_null:
                continue
            if isinstance(value, str) and value in policy.unresolved_labels:
                continue
            if not isinstance(value, str) or value not in allowed:
                raise CandidateBoundaryError("candidate_out_of_set", path=path, value=value)


def validate_fast_path(policy: CandidatePolicy, fast_path_name: str | None) -> None:
    if fast_path_name is None or fast_path_name not in policy.deterministic_fast_paths:
        raise CandidateBoundaryError("deterministic_fast_path_not_allowed")


def values_at_path(value: Mapping[str, Any], path: str) -> tuple[object, ...]:
    """Resolve a small, deterministic JSON path subset (``$.a[*].b``)."""

    raw = path[2:] if path.startswith("$.") else path
    if not raw:
        raise CandidateBoundaryError("candidate_path_invalid", path=path)
    current: tuple[object, ...] = (value,)
    for raw_segment in raw.split("."):
        match = _PATH_SEGMENT_RE.fullmatch(raw_segment)
        if match is None:
            raise CandidateBoundaryError("candidate_path_invalid", path=path)
        name = match.group("name")
        many = match.group("many") is not None
        next_values: list[object] = []
        for item in current:
            if not isinstance(item, Mapping) or name not in item:
                continue
            child = item[name]
            if many:
                if not isinstance(child, Sequence) or isinstance(child, str | bytes | bytearray):
                    raise CandidateBoundaryError("candidate_path_expected_array", path=path)
                next_values.extend(child)
            else:
                next_values.append(child)
        current = tuple(next_values)
    return current
