import logging
import sys

import structlog

from domain.security.redaction import redact_structlog_event, redact_text, redact_value


class _RedactSensitiveLogRecord(logging.Filter):
    """Sanitize stdlib and third-party logs before a handler formats them."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc_info = record.exc_info
        exception_type = exc_info[0].__name__ if exc_info and exc_info[0] is not None else None
        record.msg = redact_value(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_value(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = redact_value(record.args)
        if exception_type is not None:
            record.exc_info = None
            record.exc_text = None
            if isinstance(record.msg, str):
                record.msg = f"{record.msg} exception_type={exception_type}"
        return True


class _RedactSensitiveAccessQuery(logging.Filter):
    """Remove every access-log query string, regardless of endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5 or not isinstance(args[2], str):
            return True
        path_with_query = args[2]
        path, separator, _query = path_with_query.partition("?")
        if separator:
            redacted = list(args)
            redacted[2] = f"{path}?<redacted>"
            record.args = tuple(redacted)
        record.msg = redact_text(str(record.msg))
        return True


_BASE_LOG_RECORD_FACTORY = logging.getLogRecordFactory()


def _redacting_log_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    """Apply redaction before any current or future handler can see a record."""

    record = _BASE_LOG_RECORD_FACTORY(*args, **kwargs)
    _RedactSensitiveLogRecord().filter(record)
    return record


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    if logging.getLogRecordFactory() is not _redacting_log_record_factory:
        logging.setLogRecordFactory(_redacting_log_record_factory)
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _RedactSensitiveAccessQuery) for item in access_logger.filters):
        access_logger.addFilter(_RedactSensitiveAccessQuery())
    redaction_filter = _RedactSensitiveLogRecord()
    handlers = {*logging.getLogger().handlers, *access_logger.handlers}
    for handler in handlers:
        if not any(isinstance(item, _RedactSensitiveLogRecord) for item in handler.filters):
            handler.addFilter(redaction_filter)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_structlog_event,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
