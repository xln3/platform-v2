from __future__ import annotations

import json
import logging
from io import StringIO
from typing import Any

import pytest
from fastapi import HTTPException, Request
from geo_platform.intake.research import LlmConfig
from geo_platform.logging import _RedactSensitiveLogRecord
from geo_platform.main import http_error, internal_error

from domain.security.redaction import (
    REDACTED,
    redact_structlog_event,
    redact_text,
    redact_value,
    safe_exception_summary,
)
from tools import configure_api_runtime_role

CANARY = "S3CRET_CANARY_20260825"
DSN = f"postgresql+psycopg://geo:{CANARY}@db.internal:5432/geo?sslmode=require"


def _assert_secret_free(value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    assert CANARY not in rendered
    assert DSN not in rendered


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )
    request.state.request_id = "req_redaction_probe"
    return request


def test_text_and_nested_values_remove_dsns_queries_and_secret_keys() -> None:
    text = redact_text(
        f"connect {DSN} url=https://example.test/path?token={CANARY} "
        f"Authorization: Bearer {CANARY} password={CANARY}"
    )
    _assert_secret_free(text)
    assert "[REDACTED:dsn]" in text
    assert "https://example.test/path?<redacted>" in text

    nested = redact_value(
        {
            "database_dsn": DSN,
            "headers": {"Authorization": f"Bearer {CANARY}"},
            "safe": ["visible", RuntimeError(DSN)],
        }
    )
    _assert_secret_free(nested)
    assert nested["database_dsn"] == REDACTED

    llm_config = LlmConfig(
        api_key=CANARY,
        model="fixture-model",
        base_url="https://example.test",
        base_url_fallback="",
        max_rounds=1,
    )
    _assert_secret_free(repr(llm_config))


def test_structlog_and_stdlib_boundaries_remove_exception_material() -> None:
    event = redact_structlog_event(
        None,
        "error",
        {
            "event": "database_failed",
            "error": RuntimeError(DSN),
            "api_key": CANARY,
            "exc_info": (RuntimeError, RuntimeError(DSN), None),
        },
    )
    _assert_secret_free(event)
    assert event["api_key"] == REDACTED
    assert event["exception_type"] == "RuntimeError"
    assert "exc_info" not in event

    record = logging.LogRecord(
        name="probe",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="database failed: %s",
        args=(RuntimeError(DSN),),
        exc_info=(RuntimeError, RuntimeError(DSN), None),
    )
    assert _RedactSensitiveLogRecord().filter(record)
    _assert_secret_free(record.getMessage())
    assert record.exc_info is None


def test_log_record_factory_protects_handlers_created_after_startup() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("late-installed-secret-probe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.ERROR)
    try:
        try:
            raise RuntimeError(DSN)
        except RuntimeError:
            logger.exception("late database failure: %s", DSN)
    finally:
        logger.handlers.clear()
        logger.propagate = True

    rendered = stream.getvalue()
    _assert_secret_free(rendered)
    assert "RuntimeError" in rendered
    assert "Traceback" not in rendered


@pytest.mark.asyncio
async def test_api_error_responses_never_echo_secret_exception_or_code() -> None:
    internal = await internal_error(_request(), RuntimeError(DSN))
    _assert_secret_free(internal.body.decode())
    assert internal.status_code == 500

    rejected = await http_error(
        _request(), HTTPException(status_code=400, detail={"code": f"password={CANARY}"})
    )
    _assert_secret_free(rejected.body.decode())
    assert json.loads(rejected.body)["error"]["code"] == "http_error"


def test_cli_failure_output_uses_the_same_redaction_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> None:
        raise RuntimeError(DSN)

    monkeypatch.setattr(configure_api_runtime_role, "main", fail)
    assert configure_api_runtime_role.cli() == 1
    captured = capsys.readouterr()
    _assert_secret_free(captured.err)
    assert "RuntimeError" in captured.err
    assert safe_exception_summary(RuntimeError(DSN)) in captured.err
