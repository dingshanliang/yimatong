"""Structured logging configuration.

Provides JSON (production) and human-readable (development) formatters,
automatic request_id injection via ContextVar, and a setup_logging()
function called once at application startup.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.request_id import get_request_id

# Third-party loggers to quiet down
_NOISY_LOGGERS = [
    "httpx",
    "openai",
    "sqlalchemy.engine",
    "urllib3",
    "aioredis",
    "httpcore",
    "asyncio",
]


class RequestIdFilter(logging.Filter):
    """Inject request_id from ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Colored, readable output for development."""

    COLORS = {
        "DEBUG": "\033[36m",  # cyan
        "INFO": "\033[32m",  # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        request_id = getattr(record, "request_id", "-")
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = f"{ts} {color}{record.levelname:<8}{self.RESET} [{request_id}] {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[1]:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(*, json_logs: bool = False, level: str = "INFO") -> None:
    """Configure root logger with structured formatting.

    Called once during application startup (lifespan).
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())

    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    root.addHandler(handler)

    # Quiet down noisy third-party loggers
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
