import logging
import sys

import structlog


class _RedactSensitiveAccessQuery(logging.Filter):
    """Remove sensitive query strings from Uvicorn's positional access-log args."""

    _PATHS = ("/api/v2/otp/latest",)

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5 or not isinstance(args[2], str):
            return True
        path_with_query = args[2]
        path, separator, _query = path_with_query.partition("?")
        if separator and path in self._PATHS:
            redacted = list(args)
            redacted[2] = f"{path}?<redacted>"
            record.args = tuple(redacted)
        return True


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _RedactSensitiveAccessQuery) for item in access_logger.filters):
        access_logger.addFilter(_RedactSensitiveAccessQuery())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
