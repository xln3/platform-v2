"""Domain plugin contract and explicit registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .contracts import Decision, ModelPrompt, ObservationDraft, ReleaseRef, RuntimeRequest


class DomainPack(Protocol):
    domain_id: str
    policy_version: str
    prompt_id: str
    prompt_version: str
    tool_version: str

    def release_ref(self, request: RuntimeRequest) -> ReleaseRef: ...

    def deterministic_resolve(self, request: RuntimeRequest) -> tuple[Decision, ...]: ...

    def build_model_prompt(
        self, request: RuntimeRequest, deterministic: tuple[Decision, ...]
    ) -> ModelPrompt: ...

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]: ...

    def observations(
        self, request: RuntimeRequest, decisions: tuple[Decision, ...]
    ) -> tuple[ObservationDraft, ...]: ...

    def validate_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def validate_release_impact(
        self,
        changes: Iterable[Mapping[str, Any]],
        quality_report: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def project_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]: ...


class DomainRegistry:
    """Small explicit registry; loading a plugin never depends on import side effects."""

    def __init__(self) -> None:
        self._packs: dict[str, DomainPack] = {}

    def register(self, pack: DomainPack) -> None:
        domain_id = str(pack.domain_id).strip()
        if not domain_id:
            raise ValueError("domain_id_required")
        if domain_id in self._packs:
            raise ValueError(f"duplicate_domain_pack:{domain_id}")
        self._packs[domain_id] = pack

    def get(self, domain_id: str) -> DomainPack:
        try:
            return self._packs[domain_id]
        except KeyError as exc:
            raise KeyError(f"unknown_domain_pack:{domain_id}") from exc

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(self._packs))
