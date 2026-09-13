"""Capture diagnostics that describe observed failures without inventing causes."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

from workflows.activities.collection import CollectionEvidenceRef

log = structlog.get_logger()


@contextmanager
def optional_evidence(
    evidence: list[CollectionEvidenceRef],
    *,
    path: Path,
    platform: str,
    stage: str,
) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        record_evidence_failure(evidence, path=path, platform=platform, stage=stage, error=exc)


def incomplete_stream_message(meta: dict[str, Any]) -> str:
    failed = meta.get("terminal_failed", meta.get("failed"))
    reason = "stream-connection-failed" if failed else "stream-open-at-timeout"
    return (
        f"{reason}: completion not confirmed; "
        f"elapsed_ms={meta.get('elapsed_ms')}, failed={bool(meta.get('failed'))}, "
        f"finished={bool(meta.get('finished'))}, "
        f"bytes_received={meta.get('bytes_received', 0)}, "
        f"network_error={meta.get('network_error') or 'unreported'}"
    )


def capture_failure_code(error: Exception) -> str:
    if str(error).startswith("stream-connection-failed:"):
        return "stream_connection_failed"
    if str(error).startswith("stream-open-at-timeout:"):
        return "stream_timeout"
    return "answer_capture_incomplete"


def stream_failure_diagnostics(message: str) -> dict[str, Any]:
    """Project only explicitly observed fields from our canonical failure message."""
    details: dict[str, Any] = {}
    for key in ("elapsed_ms", "bytes_received"):
        found = re.search(rf"\b{key}=(\d+)\b", message)
        if found:
            details[key] = int(found.group(1))
    for key in ("failed", "finished"):
        found = re.search(rf"\b{key}=(True|False)\b", message)
        if found:
            details[key] = found.group(1) == "True"
    return details


def record_evidence_failure(
    evidence: list[CollectionEvidenceRef],
    *,
    path: Path,
    platform: str,
    stage: str,
    error: Exception,
) -> None:
    """Record an optional evidence failure; never discard an accepted answer."""
    detail = {
        "schema_version": "capture-evidence-failure-v1",
        "platform": platform,
        "stage": stage,
        "error_type": type(error).__name__,
        "message": str(error)[:1000],
    }
    log.warning("capture_evidence_degraded", **detail)
    try:
        path.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("capture_evidence_audit_write_failed", platform=platform, stage=stage)
    else:
        evidence.append(
            CollectionEvidenceRef(
                kind="capture_evidence_audit",
                path=str(path),
                relation_type="capture_evidence_audit",
                mime_type="application/json",
            )
        )
