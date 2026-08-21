import logging

from geo_platform.logging import _RedactSensitiveAccessQuery


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_sensitive_otp_access_query_is_redacted() -> None:
    record = _access_record("/api/v2/otp/latest?phone=13121622231&within=180")

    assert _RedactSensitiveAccessQuery().filter(record) is True
    assert record.args[2] == "/api/v2/otp/latest?<redacted>"
    assert "13121622231" not in record.getMessage()


def test_unrelated_access_query_is_preserved() -> None:
    record = _access_record("/api/v2/search?q=brand")

    assert _RedactSensitiveAccessQuery().filter(record) is True
    assert record.args[2] == "/api/v2/search?q=brand"
