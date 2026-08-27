"""Small non-brand domain that exercises the same governed release contracts."""

from __future__ import annotations

import hashlib
import json
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
from ..release import KnowledgeReleaseError, KnowledgeReleaseStore


class SourceTypeFixturePack:
    domain_id = "source/type-fixture"
    policy_version = "source-type-policy-v2"
    prompt_id = "source-type-classifier"
    prompt_version = "source-type-prompt-v2"
    tool_version = "no-tools-v1"
    _BOOTSTRAP_TYPES = {
        "official": "official_source",
        "regulator": "official_source",
        "news": "editorial_source",
        "blog": "editorial_source",
        "social": "social_source",
    }

    def __init__(self, *, knowledge_release_dir: str | None = None) -> None:
        self.knowledge_release_dir = knowledge_release_dir

    def _released_document(
        self, request: RuntimeRequest
    ) -> tuple[dict[str, Any], Mapping[str, Any]] | None:
        if not self.knowledge_release_dir:
            return None
        store = KnowledgeReleaseStore(self.knowledge_release_dir)
        try:
            if request.expected_release_id:
                return store.load_domain(self.domain_id, request.expected_release_id)
            document, manifest, _degraded = store.load_domain_resilient(self.domain_id)
            return document, manifest
        except KnowledgeReleaseError as exc:
            if request.expected_release_id:
                raise ValueError(
                    f"requested_knowledge_release_unavailable:{request.expected_release_id}"
                ) from exc
            return None

    def _entries(self, request: RuntimeRequest) -> dict[str, tuple[str, str]]:
        released = self._released_document(request)
        if released is None:
            return {key: (value, "published") for key, value in self._BOOTSTRAP_TYPES.items()}
        document, _manifest = released
        if (
            document.get("schema_version") != "source-type-v2"
            or document.get("domain") != self.domain_id
            or not isinstance(document.get("entries"), list)
        ):
            raise ValueError("source_type_release_invalid")
        entries: dict[str, tuple[str, str]] = {}
        for raw in document["entries"]:
            if not isinstance(raw, dict) or set(raw) != {
                "key",
                "source_type",
                "knowledge_status",
            }:
                raise ValueError("source_type_release_entry_invalid")
            key = str(raw.get("key") or "").strip().casefold()
            source_type = str(raw.get("source_type") or "").strip()
            status = str(raw.get("knowledge_status") or "")
            if (
                not key
                or not source_type
                or key in entries
                or status not in {"published", "reviewed_local"}
            ):
                raise ValueError("source_type_release_entry_invalid")
            entries[key] = (source_type, status)
        return entries

    def release_ref(self, request: RuntimeRequest) -> ReleaseRef:
        released = self._released_document(request)
        if released is None:
            return ReleaseRef(
                "source-type-bootstrap-1",
                "sha256:" + hashlib.sha256(b"source-type-bootstrap-1").hexdigest(),
                "source-type-v1",
                "bundled_fixture",
            )
        _document, manifest = released
        return ReleaseRef(
            str(manifest["release_id"]),
            str(manifest["content_hash"]),
            str(manifest["schema_version"]),
            "local_knowledge_release",
        )

    def deterministic_resolve(self, request: RuntimeRequest) -> tuple[Decision, ...]:
        entries = self._entries(request)
        output = []
        for index, item in enumerate(request.items):
            input_id = str(item.get("id") or f"item-{index + 1}")
            value = str(item.get("value") or "").strip()
            entry = entries.get(value.casefold())
            source_type = entry[0] if entry else None
            status = KnowledgeStatus(entry[1]) if entry else KnowledgeStatus.UNRESOLVED
            output.append(
                Decision(
                    input_id=input_id,
                    input_value=value,
                    value={"source_type": source_type},
                    knowledge_status=status,
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
        unresolved = [
            {"input_id": row.input_id, "value": row.input_value}
            for row in deterministic
            if row.knowledge_status == KnowledgeStatus.UNRESOLVED
        ]
        return ModelPrompt(
            prompt_id=self.prompt_id,
            prompt_version=self.prompt_version,
            system_message=(
                "Classify source-type terms for this request. Return strict JSON; the result is "
                "a request-scoped hypothesis, never published knowledge."
            ),
            user_message=json.dumps(
                {"task": request.task, "items": unresolved},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["decisions"],
                "properties": {
                    "decisions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["input_id", "source_type", "confidence", "reason"],
                            "properties": {
                                "input_id": {"type": "string"},
                                "source_type": {"type": "string"},
                                "confidence": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                                "reason": {"type": "string"},
                            },
                        },
                    }
                },
            },
        )

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        if set(payload) != {"decisions"} or not isinstance(payload.get("decisions"), list):
            raise ValueError("source_type_model_output_invalid")
        values = {
            str(item.get("id") or f"item-{index + 1}"): str(item.get("value") or "")
            for index, item in enumerate(request.items)
        }
        expected = {
            row.input_id
            for row in deterministic
            if row.knowledge_status == KnowledgeStatus.UNRESOLVED
        }
        seen: set[str] = set()
        output: list[Decision] = []
        for raw in payload["decisions"]:
            if not isinstance(raw, dict) or set(raw) != {
                "input_id",
                "source_type",
                "confidence",
                "reason",
            }:
                raise ValueError("source_type_model_output_invalid")
            input_id = raw.get("input_id")
            source_type = raw.get("source_type")
            reason = raw.get("reason")
            confidence = raw.get("confidence")
            if (
                not isinstance(input_id, str)
                or input_id not in expected
                or input_id in seen
                or not isinstance(source_type, str)
                or not source_type.strip()
                or len(source_type) > 120
                or not isinstance(reason, str)
                or not reason.strip()
                or len(reason) > 500
                or isinstance(confidence, bool)
                or not isinstance(confidence, int | float)
                or not 0 <= float(confidence) <= 1
            ):
                raise ValueError("source_type_model_output_invalid")
            seen.add(input_id)
            output.append(
                Decision(
                    input_id=input_id,
                    input_value=values[input_id],
                    value={"source_type": source_type.strip(), "requires_governance": True},
                    knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                    decision_scope=DecisionScope.REQUEST,
                    confidence=float(confidence),
                    reasons=(reason.strip(),),
                )
            )
        if seen != expected:
            raise ValueError("source_type_model_input_set_invalid")
        return tuple(output)

    def observations(
        self, request: RuntimeRequest, decisions: tuple[Decision, ...]
    ) -> tuple[ObservationDraft, ...]:
        item_by_id = {
            str(item.get("id") or f"item-{index + 1}"): item
            for index, item in enumerate(request.items)
        }
        output: list[ObservationDraft] = []
        for decision in decisions:
            if decision.knowledge_status not in {
                KnowledgeStatus.UNRESOLVED,
                KnowledgeStatus.MODEL_INFERRED,
            }:
                continue
            item = item_by_id[decision.input_id]
            source_ref = str(
                item.get("source_ref") or request.context.get("source_ref") or request.request_id
            )
            source_ref_hash = "sha256:" + hashlib.sha256(source_ref.encode()).hexdigest()
            idempotency_key = (
                str(item.get("idempotency_key") or "").strip()
                or hashlib.sha256(
                    (
                        f"{request.tenant}|{request.namespace}|{request.domain}|"
                        f"{decision.input_id}|{source_ref_hash}"
                    ).encode()
                ).hexdigest()
            )
            output.append(
                ObservationDraft(
                    namespace=request.namespace,
                    domain=request.domain,
                    task=request.task,
                    surface_form=decision.input_value,
                    normalized_key=decision.input_value.casefold(),
                    source_type="runtime_inference",
                    source_ref_hash=source_ref_hash,
                    idempotency_key=idempotency_key,
                    safe_context=None,
                    data_classification=request.data_classification,
                    visibility="private",
                    payload={
                        "knowledge_status": decision.knowledge_status.value,
                        "confidence": decision.confidence,
                        "policy_version": self.policy_version,
                    },
                )
            )
        return tuple(output)

    def project_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        del assertions
        entries: list[dict[str, str]] = []
        for item in objects:
            if item.get("review_status") not in {"reviewed", "published"}:
                continue
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                continue
            entries.append(
                {
                    "key": str(attributes.get("key") or "").strip().casefold(),
                    "source_type": str(attributes.get("source_type") or "").strip(),
                    "knowledge_status": (
                        "published"
                        if item.get("visibility") == "public"
                        and item.get("sync_status") == "reconciled"
                        else "reviewed_local"
                    ),
                }
            )
        entries.sort(key=lambda item: item["key"])
        return {
            "schema_version": "source-type-v2",
            "domain": self.domain_id,
            "entries": entries,
        }

    def validate_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        object_rows = list(objects)
        assertion_rows = list(assertions)
        invalid: list[str] = []
        keys: set[str] = set()
        for row in object_rows:
            attributes = row.get("attributes")
            key = (
                str(attributes.get("key") or "").strip().casefold()
                if isinstance(attributes, dict)
                else ""
            )
            source_type = (
                str(attributes.get("source_type") or "").strip()
                if isinstance(attributes, dict)
                else ""
            )
            if row.get("object_type") != "source_type" or not key or not source_type or key in keys:
                invalid.append(str(row.get("stable_id") or "missing"))
            keys.add(key)
        return {
            "passed": not invalid and not assertion_rows,
            "issues": [f"invalid_source_type:{value}" for value in invalid]
            + (["assertions_not_supported"] if assertion_rows else []),
            "object_count": len(object_rows),
        }

    def validate_release_impact(
        self,
        changes: Iterable[Mapping[str, Any]],
        quality_report: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del quality_report
        return {
            "passed": True,
            "replay_required": False,
            "reason": "fixture_changes_have_no_rank_or_entity_merge_effect",
            "change_count": sum(1 for _ in changes),
        }
