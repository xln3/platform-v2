#!/usr/bin/env python3
"""Build a redacted Temporal history fixture from an already-local JSON export.

This tool deliberately has no Temporal client or network dependency.  Obtaining the
source export is a separate, authorised operation; this process only validates and
sanitises bytes already present on the local filesystem.  Sanitisation is not replay
proof: every generated manifest remains ``replay_eligible=false`` until a separate
compatibility-worker Replayer gate verifies the fixture.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

WORKFLOW_TYPE = "GeoCollectionWorkflow"
TOOL_NAME = "export_collection_history_fixture"
TOOL_VERSION = "1.1.0"
MANIFEST_SCHEMA_VERSION = 1
REDACTED_TEXT = "[REDACTED]"

_JSON_ENCODING = "json/plain"
_REDACTED_BINARY_DATA = base64.b64encode(REDACTED_TEXT.encode()).decode("ascii")
_STARTED_EVENT_TYPES = {
    "eventtypeworkflowexecutionstarted",
    "workflowexecutionstarted",
}
_MARKER_RECORDED_EVENT_TYPES = {
    "eventtypemarkerrecorded",
    "markerrecorded",
}
_PATCH_MARKER_NAMES = {
    "corepatch",
    "temporalchangeversion",
    "version",
}
_PATCH_DETAIL_KEYS = {
    "changeid",
    "deprecated",
    "patchid",
    "version",
}
_PAYLOAD_CONTAINER_KEYS = {
    "encodedattributes",
    "heartbeatdetails",
    "input",
    "lastheartbeatdetails",
    "payload",
    "payloads",
    "result",
}
_FAILURE_TEXT_KEYS = {
    "details",
    "encodedattributes",
    "identity",
    "message",
    "stacktrace",
}
_SENSITIVE_JSON_KEY_PARTS = {
    "account",
    "answer",
    "apikey",
    "authorization",
    "body",
    "content",
    "cookie",
    "credential",
    "email",
    "html",
    "login",
    "markdown",
    "memo",
    "message",
    "note",
    "password",
    "phone",
    "privatekey",
    "prompt",
    "query",
    "question",
    "raw",
    "response",
    "secret",
    "session",
    "stacktrace",
    "text",
    "token",
    "username",
}
_CONTROL_JSON_KEYS = {
    "activitytype",
    "adapter",
    "businesskey",
    "campaignpubid",
    "channel",
    "configrevisionid",
    "configversionpubid",
    "errorcode",
    "errortype",
    "kind",
    "mode",
    "model",
    "operationid",
    "operationpubid",
    "platform",
    "region",
    "requestedmode",
    "requestedsurface",
    "retrystate",
    "schema",
    "schemaversion",
    "slotpubid",
    "source",
    "state",
    "status",
    "submissionoperationid",
    "surface",
    "targetpubid",
    "type",
    "version",
    "workflowtype",
}
_SOURCE_CATEGORIES = {
    "completed": "completed",
    "failed": "failed",
    "paused-resumed": "paused-resumed",
    "paused_resumed": "paused-resumed",
    "retry": "retry",
    "continue-as-new": "continue-as-new",
    "continue_as_new": "continue-as-new",
}


class HistoryFixtureError(ValueError):
    """The source cannot safely be turned into a collection history fixture."""


def _normalise(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _mapping_value(value: Mapping[str, Any], normalised_key: str) -> Any:
    matches = [item for key, item in value.items() if _normalise(str(key)) == normalised_key]
    if len(matches) > 1:
        raise HistoryFixtureError(f"ambiguous JSON keys for {normalised_key}")
    return matches[0] if matches else None


def _events(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Any] = []
    direct = _mapping_value(document, "events")
    if direct is not None:
        candidates.append(direct)
    history = _mapping_value(document, "history")
    if isinstance(history, Mapping):
        nested = _mapping_value(history, "events")
        if nested is not None:
            candidates.append(nested)
    if len(candidates) != 1:
        raise HistoryFixtureError("history JSON must contain exactly one events array")
    events = candidates[0]
    if not isinstance(events, list) or not events:
        raise HistoryFixtureError("history JSON events must be a non-empty array")
    if not all(isinstance(event, Mapping) for event in events):
        raise HistoryFixtureError("every history event must be a JSON object")
    return events


def _event_type(event: Mapping[str, Any]) -> str:
    value = _mapping_value(event, "eventtype")
    if not isinstance(value, str) or not value.strip():
        raise HistoryFixtureError("every history event must have a string eventType")
    return value


def _workflow_type(events: Sequence[Mapping[str, Any]]) -> str:
    started_events = [
        event for event in events if _normalise(_event_type(event)) in _STARTED_EVENT_TYPES
    ]
    if len(started_events) != 1:
        raise HistoryFixtureError("history must contain exactly one WorkflowExecutionStarted event")
    attributes = _mapping_value(started_events[0], "workflowexecutionstartedeventattributes")
    if not isinstance(attributes, Mapping):
        raise HistoryFixtureError("WorkflowExecutionStarted attributes are missing")
    workflow_type = _mapping_value(attributes, "workflowtype")
    if isinstance(workflow_type, Mapping):
        workflow_type = _mapping_value(workflow_type, "name")
    if workflow_type != WORKFLOW_TYPE:
        actual = workflow_type if isinstance(workflow_type, str) else "missing"
        raise HistoryFixtureError(f"expected workflow type {WORKFLOW_TYPE!r}; found {actual!r}")
    return WORKFLOW_TYPE


def _payload_encoding(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    encoding = _mapping_value(metadata, "encoding")
    if not isinstance(encoding, str):
        return None
    try:
        decoded = base64.b64decode(encoding, validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        decoded = encoding
    return decoded


def _is_control_json_key(key: str) -> bool:
    normalised = _normalise(key)
    if any(part in normalised for part in _SENSITIVE_JSON_KEY_PARTS):
        return False
    if normalised in _CONTROL_JSON_KEYS:
        return True
    return normalised.endswith(("count", "index", "ordinal", "state", "status", "version"))


def _safe_marker_identifier(value: str) -> bool:
    return 0 < len(value) <= 256 and all(
        character.isalnum() or character in "._:/-" for character in value
    )


class _Sanitiser:
    def __init__(self) -> None:
        self.redaction_count = 0
        self.marker_control_count = 0

    def _redacted_scalar(self, value: Any) -> Any:
        if value is None:
            return None
        self.redaction_count += 1
        if isinstance(value, bool):
            return False
        if isinstance(value, int | float):
            return 0
        return REDACTED_TEXT

    def _looks_like_payload(self, value: Mapping[str, Any]) -> bool:
        keys = {_normalise(str(key)) for key in value}
        return "data" in keys and ("metadata" in keys or keys <= {"data"})

    def _decode_json_payload(self, data: Any) -> Any:
        if not isinstance(data, str):
            raise HistoryFixtureError("json/plain payload data must be a base64 string")
        try:
            payload_bytes = base64.b64decode(data, validate=True)
            decoded = payload_bytes.decode("utf-8")
            return json.loads(
                decoded,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoryFixtureError(f"invalid json/plain payload: {exc}") from exc

    def _encode_json_payload(self, value: Any) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise HistoryFixtureError(f"unsupported json/plain payload value: {exc}") from exc
        return base64.b64encode(encoded).decode("ascii")

    def _json_tree(self, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {
                str(child_key): self._json_tree(child, key=str(child_key))
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [self._json_tree(child, key=key) for child in value]
        if isinstance(value, str):
            if key is not None and _is_control_json_key(key):
                return value
            self.redaction_count += 1
            return REDACTED_TEXT
        # JSON booleans/numbers/null are structural and do not carry raw prose.
        return value

    def _json_sensitive_tree(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(child_key): self._json_sensitive_tree(child)
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [self._json_sensitive_tree(child) for child in value]
        return self._redacted_scalar(value)

    def _metadata(self, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return self._redacted_scalar(value)
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if _normalise(str(key)) == "encoding" and isinstance(item, str):
                clean[str(key)] = item
            elif isinstance(item, str):
                self.redaction_count += 1
                clean[str(key)] = _REDACTED_BINARY_DATA
            else:
                clean[str(key)] = self._sensitive_tree(item)
        return clean

    def _payload_object(
        self,
        value: Mapping[str, Any],
        *,
        redact_all: bool,
    ) -> dict[str, Any]:
        metadata = _mapping_value(value, "metadata")
        encoding = _payload_encoding(metadata)
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalised = _normalise(str(key))
            if normalised == "metadata":
                clean[str(key)] = self._metadata(item)
            elif normalised == "data":
                if encoding == _JSON_ENCODING:
                    decoded = self._decode_json_payload(item)
                    redacted = (
                        self._json_sensitive_tree(decoded)
                        if redact_all
                        else self._json_tree(decoded)
                    )
                    clean[str(key)] = self._encode_json_payload(redacted)
                else:
                    self.redaction_count += 1
                    clean[str(key)] = _REDACTED_BINARY_DATA
            else:
                clean[str(key)] = self._sensitive_tree(item)
        return clean

    def _payload_tree(self, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            if self._looks_like_payload(value):
                return self._payload_object(value, redact_all=False)
            return {
                str(child_key): self._payload_tree(child, key=str(child_key))
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [self._payload_tree(child, key=key) for child in value]
        return self._json_tree(value, key=key)

    def _sensitive_tree(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            if self._looks_like_payload(value):
                return self._payload_object(value, redact_all=True)
            return {str(key): self._sensitive_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sensitive_tree(item) for item in value]
        return self._redacted_scalar(value)

    def _copy_tree(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): self._copy_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._copy_tree(item) for item in value]
        return value

    def _marker_control_payload(
        self,
        value: Mapping[str, Any],
        *,
        detail_key: str,
    ) -> dict[str, Any]:
        metadata = _mapping_value(value, "metadata")
        if not isinstance(metadata, Mapping) or _payload_encoding(metadata) != _JSON_ENCODING:
            raise HistoryFixtureError("patch/version marker control must use json/plain")
        if {_normalise(str(key)) for key in metadata} != {"encoding"}:
            raise HistoryFixtureError("patch/version marker metadata contains unsupported fields")
        decoded = self._decode_json_payload(_mapping_value(value, "data"))
        if detail_key in {"changeid", "patchid"}:
            valid = isinstance(decoded, str) and _safe_marker_identifier(decoded)
        elif detail_key == "deprecated":
            valid = isinstance(decoded, bool)
        else:
            valid = isinstance(decoded, int) and not isinstance(decoded, bool)
        if not valid:
            raise HistoryFixtureError(
                f"unsafe or malformed patch/version marker detail: {detail_key}"
            )
        self.marker_control_count += 1
        copied = self._copy_tree(value)
        if not isinstance(copied, dict):  # Defensive: value is a mapping.
            raise HistoryFixtureError("patch/version marker payload is not an object")
        return copied

    def _marker_control_tree(self, value: Any, *, detail_key: str) -> Any:
        if isinstance(value, Mapping):
            if self._looks_like_payload(value):
                return self._marker_control_payload(value, detail_key=detail_key)
            return {
                str(key): self._marker_control_tree(item, detail_key=detail_key)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._marker_control_tree(item, detail_key=detail_key) for item in value]
        raise HistoryFixtureError("patch/version marker detail must contain only payload objects")

    def _marker_attributes(self, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise HistoryFixtureError("MarkerRecorded attributes must be an object")
        marker_name = _mapping_value(value, "markername")
        is_patch_marker = isinstance(marker_name, str) and _normalise(marker_name) in (
            _PATCH_MARKER_NAMES
        )
        clean: dict[str, Any] = {}
        saw_details = False
        for key, item in value.items():
            key_text = str(key)
            normalised = _normalise(key_text)
            if normalised == "details" and is_patch_marker:
                saw_details = True
                if not isinstance(item, Mapping) or not item:
                    raise HistoryFixtureError("patch/version marker details are missing")
                details: dict[str, Any] = {}
                for detail_name, detail_value in item.items():
                    detail_key = _normalise(str(detail_name))
                    if detail_key not in _PATCH_DETAIL_KEYS:
                        raise HistoryFixtureError(
                            f"unsupported patch/version marker detail: {detail_name}"
                        )
                    before = self.marker_control_count
                    details[str(detail_name)] = self._marker_control_tree(
                        detail_value,
                        detail_key=detail_key,
                    )
                    if self.marker_control_count == before:
                        raise HistoryFixtureError(
                            f"patch/version marker detail has no payload: {detail_name}"
                        )
                clean[key_text] = details
            else:
                clean[key_text] = self.sanitise(item)
        if is_patch_marker and not saw_details:
            raise HistoryFixtureError("patch/version marker details are missing")
        return clean

    def _marker_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _normalise(key_text) == "markerrecordedeventattributes":
                clean[key_text] = self._marker_attributes(item)
            else:
                clean[key_text] = self.sanitise(item)
        return clean

    def sanitise(self, value: Any, *, in_failure: bool = False) -> Any:
        if isinstance(value, Mapping):
            event_type = _mapping_value(value, "eventtype")
            if (
                isinstance(event_type, str)
                and _normalise(event_type) in _MARKER_RECORDED_EVENT_TYPES
            ):
                return self._marker_event(value)
            if self._looks_like_payload(value):
                return self._payload_object(value, redact_all=False)
            clean: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                normalised = _normalise(key_text)
                if normalised in {"rawhistory", "rawhistoryv2"}:
                    raise HistoryFixtureError(
                        "opaque rawHistory batches are unsupported; export decoded events"
                    )
                if normalised in {"memo", "searchattributes", "searchattrs"}:
                    clean[key_text] = self._sensitive_tree(item)
                elif normalised in _PAYLOAD_CONTAINER_KEYS or normalised.endswith(
                    ("payload", "payloads")
                ):
                    clean[key_text] = self._payload_tree(item)
                elif in_failure and normalised in _FAILURE_TEXT_KEYS:
                    clean[key_text] = self._sensitive_tree(item)
                elif "failure" in normalised:
                    clean[key_text] = (
                        self.sanitise(item, in_failure=True)
                        if isinstance(item, Mapping | list)
                        else self._redacted_scalar(item)
                    )
                else:
                    clean[key_text] = self.sanitise(item, in_failure=in_failure)
            return clean
        if isinstance(value, list):
            return [self.sanitise(item, in_failure=in_failure) for item in value]
        return value


def _validated_history(document: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    events = _events(document)
    event_types = [_event_type(event) for event in events]
    _workflow_type(events)
    return events, event_types


def sanitize_history(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and redact a decoded Temporal history JSON document."""

    if not isinstance(document, Mapping):
        raise HistoryFixtureError("history JSON root must be an object")
    _events_before, event_types_before = _validated_history(document)
    sanitiser = _Sanitiser()
    clean = sanitiser.sanitise(document)
    if not isinstance(clean, dict):  # Defensive: the validated root is a mapping.
        raise HistoryFixtureError("sanitised history root is not an object")
    _events_after, event_types_after = _validated_history(clean)
    if event_types_after != event_types_before:
        raise HistoryFixtureError("sanitisation changed the Temporal event sequence")
    return clean


def _reject_constant(value: str) -> None:
    raise HistoryFixtureError(f"non-standard JSON numeric constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryFixtureError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _load_document(source: bytes) -> dict[str, Any]:
    try:
        decoded = source.decode("utf-8-sig")
        document = json.loads(
            decoded,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryFixtureError(f"input is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise HistoryFixtureError("history JSON root must be an object")
    return document


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HistoryFixtureError(f"history contains an unsupported JSON value: {exc}") from exc
    return f"{rendered}\n".encode()


def _source_category(value: str) -> str:
    try:
        return _SOURCE_CATEGORIES[value.strip().lower()]
    except KeyError as exc:
        allowed = ", ".join(sorted(set(_SOURCE_CATEGORIES.values())))
        raise HistoryFixtureError(f"source category must be one of: {allowed}") from exc


def _resolved_output(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.parent.is_dir():
        raise HistoryFixtureError(f"{label} parent directory does not exist: {resolved.parent}")
    if resolved.exists() or resolved.is_symlink():
        raise HistoryFixtureError(f"refusing to overwrite existing {label}: {resolved}")
    return resolved


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def export_history_fixture(
    input_path: Path,
    fixture_path: Path,
    manifest_path: Path,
    *,
    source_category: str,
) -> dict[str, Any]:
    """Sanitise one local history export and write immutable fixture/manifest files."""

    source = input_path.expanduser().resolve(strict=True)
    if not source.is_file():
        raise HistoryFixtureError(f"input is not a regular file: {source}")
    fixture = _resolved_output(fixture_path, label="fixture")
    manifest = _resolved_output(manifest_path, label="manifest")
    if len({source, fixture, manifest}) != 3:
        raise HistoryFixtureError("input, fixture, and manifest paths must be distinct")

    source_bytes = source.read_bytes()
    document = _load_document(source_bytes)
    clean = sanitize_history(document)
    events, event_types = _validated_history(clean)
    fixture_bytes = _canonical_json(clean)
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    sequence_bytes = "\n".join(event_types).encode()
    sanitiser = _Sanitiser()
    sanitiser.sanitise(document)
    manifest_document: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "export_tool": TOOL_NAME,
        "export_tool_version": TOOL_VERSION,
        "workflow_type": WORKFLOW_TYPE,
        "source_category": _source_category(source_category),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "fixture_file": fixture.name,
        "fixture_sha256": fixture_sha256,
        "fixture_bytes": len(fixture_bytes),
        "event_count": len(events),
        "event_type_sequence": event_types,
        "event_type_sequence_sha256": hashlib.sha256(sequence_bytes).hexdigest(),
        "redaction_count": sanitiser.redaction_count,
        "preserved_marker_control_payload_count": sanitiser.marker_control_count,
        "replay_eligible": False,
        "replay_eligibility_reason": (
            "sanitised payload values require an independent compatibility-worker "
            "Replayer verification"
        ),
    }
    manifest_bytes = _canonical_json(manifest_document)

    fixture_created = False
    try:
        _write_exclusive(fixture, fixture_bytes)
        fixture_created = True
        _write_exclusive(manifest, manifest_bytes)
    except Exception:
        if fixture_created:
            fixture.unlink(missing_ok=True)
        raise
    return manifest_document


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sanitise an already-local GeoCollectionWorkflow Temporal history JSON export; "
            "this tool never connects to Temporal or another service."
        )
    )
    parser.add_argument("input_path", nargs="?", type=Path, help="local Temporal JSON export")
    parser.add_argument("--input", dest="input_option", type=Path, help="local JSON export")
    parser.add_argument(
        "--fixture", "--output", dest="fixture", required=True, type=Path, help="new fixture path"
    )
    parser.add_argument("--manifest", required=True, type=Path, help="new SHA-256 manifest path")
    parser.add_argument(
        "--source-category",
        required=True,
        help="completed, failed, paused-resumed, retry, or continue-as-new",
    )
    arguments = parser.parse_args(argv)
    if (arguments.input_path is None) == (arguments.input_option is None):
        parser.error("provide exactly one input path, positionally or with --input")
    arguments.input_path = arguments.input_path or arguments.input_option
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        manifest = export_history_fixture(
            arguments.input_path,
            arguments.fixture,
            arguments.manifest,
            source_category=arguments.source_category,
        )
    except (HistoryFixtureError, OSError) as exc:
        print(f"history fixture export refused: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        f"wrote {manifest['event_count']} redacted events; "
        f"fixture sha256={manifest['fixture_sha256']}; replay_eligible=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
