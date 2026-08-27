"""Minimal non-brand domain used to prove the core has no brand assumptions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from ..contracts import (
    Decision,
    DecisionScope,
    KnowledgeStatus,
    ModelPrompt,
    ObservationDraft,
    ReleaseRef,
    RuntimeRequest,
)


class SourceTypeFixturePack:
    domain_id = "source/type-fixture"
    policy_version = "source-type-policy-v1"
    prompt_id = "source-type-classifier"
    prompt_version = "source-type-prompt-v1"
    tool_version = "no-tools-v1"
    _TYPES = {
        "official": "official_source",
        "regulator": "official_source",
        "news": "editorial_source",
        "blog": "editorial_source",
        "social": "social_source",
    }

    def release_ref(self, request: RuntimeRequest) -> ReleaseRef:
        del request
        return ReleaseRef("fixture-1", "sha256:fixture", "source-type-v1", "fixture")

    def deterministic_resolve(self, request: RuntimeRequest) -> tuple[Decision, ...]:
        output = []
        for index, item in enumerate(request.items):
            input_id = str(item.get("id") or f"item-{index + 1}")
            value = str(item.get("value") or "").strip()
            source_type = self._TYPES.get(value.casefold())
            output.append(
                Decision(
                    input_id=input_id,
                    input_value=value,
                    value={"source_type": source_type},
                    knowledge_status=(
                        KnowledgeStatus.PUBLISHED if source_type else KnowledgeStatus.UNRESOLVED
                    ),
                    decision_scope=(
                        DecisionScope.GLOBAL_RELEASE
                        if source_type
                        else DecisionScope.DOMAIN_CANDIDATE
                    ),
                    confidence=1.0 if source_type else 0.0,
                    adopted=source_type is not None,
                )
            )
        return tuple(output)

    def build_model_prompt(
        self, request: RuntimeRequest, deterministic: tuple[Decision, ...]
    ) -> ModelPrompt:
        del request, deterministic
        return ModelPrompt(
            prompt_id=self.prompt_id,
            prompt_version=self.prompt_version,
            system_message="Classify source types.",
            user_message="Return decisions for unresolved inputs.",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["decisions"],
                "properties": {"decisions": {"type": "array", "items": {"type": "object"}}},
            },
        )

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        del payload, request, deterministic
        raise ValueError("fixture_model_path_not_implemented")

    def observations(
        self, request: RuntimeRequest, decisions: tuple[Decision, ...]
    ) -> tuple[ObservationDraft, ...]:
        return tuple(
            ObservationDraft(
                namespace=request.namespace,
                domain=request.domain,
                task=request.task,
                surface_form=decision.input_value,
                normalized_key=decision.input_value.casefold(),
                source_type="fixture",
                source_ref_hash="sha256:" + hashlib.sha256(request.request_id.encode()).hexdigest(),
                idempotency_key=hashlib.sha256(
                    f"{request.request_id}|{decision.input_id}".encode()
                ).hexdigest(),
                safe_context=None,
                data_classification=request.data_classification,
                visibility="private",
            )
            for decision in decisions
            if decision.knowledge_status == KnowledgeStatus.UNRESOLVED
        )

    def project_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        del assertions
        return {"entries": [dict(item) for item in objects]}

    def validate_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        object_rows = list(objects)
        assertion_rows = list(assertions)
        invalid = [
            str(row.get("stable_id") or "missing")
            for row in object_rows
            if row.get("object_type") != "source_type"
            or not isinstance(row.get("attributes"), dict)
        ]
        return {
            "passed": not invalid and not assertion_rows,
            "issues": [f"invalid_source_type:{value}" for value in invalid]
            + (["assertions_not_supported"] if assertion_rows else []),
        }
