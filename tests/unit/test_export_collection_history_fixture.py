from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path

import pytest

from tools.export_collection_history_fixture import (
    REDACTED_TEXT,
    HistoryFixtureError,
    export_history_fixture,
    sanitize_history,
)

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "export_collection_history_fixture.py"


def _payload(value: object) -> dict[str, object]:
    return {
        "metadata": {
            "encoding": base64.b64encode(b"json/plain").decode(),
            "customer-metadata": base64.b64encode(b"metadata-secret").decode(),
        },
        "data": base64.b64encode(json.dumps(value).encode()).decode(),
    }


def _control_payload(value: object) -> dict[str, object]:
    return {
        "metadata": {"encoding": base64.b64encode(b"json/plain").decode()},
        "data": base64.b64encode(json.dumps(value).encode()).decode(),
    }


def _decoded_payload(payload: dict[str, object]) -> object:
    data = payload["data"]
    assert isinstance(data, str)
    return json.loads(base64.b64decode(data))


def _history(workflow_type: str = "GeoCollectionWorkflow") -> dict[str, object]:
    return {
        "events": [
            {
                "eventId": "1",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                "version": "41",
                "workflowExecutionStartedEventAttributes": {
                    "workflowType": {"name": workflow_type},
                    "input": {
                        "payloads": [
                            _payload(
                                {
                                    "schema_version": 1,
                                    "platform": "doubao",
                                    "surface": "consumer_web",
                                    "mode": "normal",
                                    "adapter": "doubao",
                                    "state": "active",
                                    "persist_results": True,
                                    "history_batch_size": 20,
                                    "prompt": "customer prompt secret",
                                    "tasks": [
                                        {
                                            "query": "customer query secret",
                                            "answer_text": "customer answer secret",
                                            "account_pub_id": "account-secret-id",
                                            "status": "ok",
                                            "ordinal": 3,
                                        }
                                    ],
                                }
                            )
                        ]
                    },
                    "memo": {"fields": {"note": _payload("memo secret")}},
                    "searchAttributes": {"indexedFields": {"Customer": _payload("search secret")}},
                },
            },
            {
                "eventId": "2",
                "eventType": "EVENT_TYPE_MARKER_RECORDED",
                "version": "42",
                "markerRecordedEventAttributes": {
                    "markerName": "core_patch",
                    "details": {
                        "patch_id": {"payloads": [_control_payload("collection-patch-v2")]},
                        "deprecated": {"payloads": [_control_payload(False)]},
                    },
                },
            },
            {
                "eventId": "3",
                "eventType": "EVENT_TYPE_MARKER_RECORDED",
                "version": "43",
                "markerRecordedEventAttributes": {
                    "markerName": "Version",
                    "details": {
                        "change-id": {"payloads": [_control_payload("collection-version-v1")]},
                        "version": {"payloads": [_control_payload(7)]},
                    },
                },
            },
            {
                "eventId": "4",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_FAILED",
                "activityTaskFailedEventAttributes": {
                    "retryState": "RETRY_STATE_IN_PROGRESS",
                    "failure": {
                        "message": "answer and account secret",
                        "stackTrace": "stack includes secret",
                        "applicationFailureInfo": {
                            "type": "CollectionFailure",
                            "nonRetryable": False,
                            "details": {"payloads": [_payload("failure detail secret")]},
                        },
                    },
                },
            },
        ]
    }


def test_sanitizer_removes_sensitive_text_and_preserves_temporal_structure() -> None:
    source = _history()
    clean = sanitize_history(source)
    rendered = json.dumps(clean, ensure_ascii=False)

    for secret in (
        "customer prompt secret",
        "metadata-secret",
        "memo secret",
        "search secret",
        "customer query secret",
        "customer answer secret",
        "account-secret-id",
        "answer and account secret",
        "stack includes secret",
        "failure detail secret",
    ):
        assert secret not in rendered
        assert base64.b64encode(secret.encode()).decode() not in rendered

    events = clean["events"]
    assert [event["eventType"] for event in events] == [
        "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
        "EVENT_TYPE_MARKER_RECORDED",
        "EVENT_TYPE_MARKER_RECORDED",
        "EVENT_TYPE_ACTIVITY_TASK_FAILED",
    ]
    assert [event["version"] for event in events[:3]] == ["41", "42", "43"]
    workflow_input = events[0]["workflowExecutionStartedEventAttributes"]["input"]["payloads"][0]
    decoded_input = _decoded_payload(workflow_input)
    assert decoded_input == {
        "schema_version": 1,
        "platform": "doubao",
        "surface": "consumer_web",
        "mode": "normal",
        "adapter": "doubao",
        "state": "active",
        "persist_results": True,
        "history_batch_size": 20,
        "prompt": REDACTED_TEXT,
        "tasks": [
            {
                "query": REDACTED_TEXT,
                "answer_text": REDACTED_TEXT,
                "account_pub_id": REDACTED_TEXT,
                "status": "ok",
                "ordinal": 3,
            }
        ],
    }
    marker = events[1]["markerRecordedEventAttributes"]
    assert marker["markerName"] == "core_patch"
    assert _decoded_payload(marker["details"]["patch_id"]["payloads"][0]) == ("collection-patch-v2")
    assert _decoded_payload(marker["details"]["deprecated"]["payloads"][0]) is False
    version_marker = events[2]["markerRecordedEventAttributes"]
    assert version_marker["markerName"] == "Version"
    assert _decoded_payload(version_marker["details"]["change-id"]["payloads"][0]) == (
        "collection-version-v1"
    )
    assert _decoded_payload(version_marker["details"]["version"]["payloads"][0]) == 7
    failure = events[3]["activityTaskFailedEventAttributes"]["failure"]
    assert failure["message"] == REDACTED_TEXT
    assert failure["stackTrace"] == REDACTED_TEXT
    assert failure["applicationFailureInfo"]["type"] == "CollectionFailure"
    assert failure["applicationFailureInfo"]["nonRetryable"] is False


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"events": []},
        {"events": [{"eventType": "EVENT_TYPE_ACTIVITY_TASK_COMPLETED"}]},
        _history("SomeOtherWorkflow"),
    ],
)
def test_sanitizer_fails_closed_for_missing_or_non_collection_history(
    document: dict[str, object],
) -> None:
    with pytest.raises(HistoryFixtureError):
        sanitize_history(document)


def test_export_writes_exact_sha256_manifest_and_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "raw.json"
    fixture = tmp_path / "completed.json"
    manifest_path = tmp_path / "completed.manifest.json"
    source_bytes = json.dumps(_history(), ensure_ascii=False).encode()
    source.write_bytes(source_bytes)

    manifest = export_history_fixture(
        source,
        fixture,
        manifest_path,
        source_category="completed",
    )

    fixture_bytes = fixture.read_bytes()
    on_disk_manifest = json.loads(manifest_path.read_text())
    assert on_disk_manifest == manifest
    assert manifest["workflow_type"] == "GeoCollectionWorkflow"
    assert manifest["event_count"] == 4
    assert manifest["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert manifest["fixture_sha256"] == hashlib.sha256(fixture_bytes).hexdigest()
    assert manifest["fixture_bytes"] == len(fixture_bytes)
    assert manifest["redaction_count"] > 0
    assert manifest["preserved_marker_control_payload_count"] == 4
    assert manifest["replay_eligible"] is False
    assert "Replayer" in manifest["replay_eligibility_reason"]
    assert source.read_bytes() == source_bytes

    with pytest.raises(HistoryFixtureError, match="overwrite"):
        export_history_fixture(
            source,
            fixture,
            tmp_path / "second.manifest.json",
            source_category="completed",
        )


def test_sanitizer_rejects_unsafe_patch_marker_control_instead_of_redacting_it() -> None:
    source = _history()
    marker = source["events"][1]["markerRecordedEventAttributes"]
    marker["details"]["patch_id"] = {"payloads": [_control_payload("customer account secret")]}
    with pytest.raises(HistoryFixtureError, match="patch/version marker detail"):
        sanitize_history(source)


def test_sanitizer_rejects_opaque_raw_history_batches() -> None:
    source = _history()
    source["rawHistory"] = [{"data": "opaque-batch-containing-uninspectable-payloads"}]
    with pytest.raises(HistoryFixtureError, match="opaque rawHistory"):
        sanitize_history(source)


def test_tool_imports_only_offline_standard_library_modules() -> None:
    tree = ast.parse(TOOL.read_text())
    imported_roots = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported_roots <= {
        "__future__",
        "argparse",
        "base64",
        "collections",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "sys",
        "typing",
    }
