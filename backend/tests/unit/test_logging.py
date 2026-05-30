"""Tests for structured logging configuration."""

import json
import logging

from app.core.logging import (
    HumanFormatter,
    JsonFormatter,
    RequestIdFilter,
    setup_logging,
)
from app.core.request_id import set_request_id


class TestRequestIdFilter:
    def test_injects_request_id(self):
        set_request_id("test-123")
        try:
            filt = RequestIdFilter()
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            filt.filter(record)
            assert record.request_id == "test-123"  # type: ignore[attr-defined]
        finally:
            set_request_id(None)

    def test_fallback_when_no_request_id(self):
        set_request_id(None)
        filt = RequestIdFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record)
        assert record.request_id == "-"  # type: ignore[attr-defined]


class TestJsonFormatter:
    def test_produces_valid_json(self):
        formatter = JsonFormatter()
        record = logging.LogRecord("test.logger", logging.INFO, "", 0, "hello", (), None)
        record.request_id = "abc-123"  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "hello"
        assert parsed["request_id"] == "abc-123"
        assert "timestamp" in parsed


class TestHumanFormatter:
    def test_readable_output(self):
        formatter = HumanFormatter()
        record = logging.LogRecord("test.logger", logging.WARNING, "", 0, "warn msg", (), None)
        record.request_id = "req-1"  # type: ignore[attr-defined]
        output = formatter.format(record)
        assert "WARNING" in output
        assert "warn msg" in output
        assert "req-1" in output


class TestSetupLogging:
    def test_configures_root_logger(self):
        root = logging.getLogger()
        old_handlers = root.handlers[:]

        setup_logging(json_logs=False, level="DEBUG")

        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, HumanFormatter)

        # Restore
        root.handlers = old_handlers

    def test_json_mode(self):
        root = logging.getLogger()
        old_handlers = root.handlers[:]

        setup_logging(json_logs=True, level="INFO")

        assert isinstance(root.handlers[0].formatter, JsonFormatter)

        # Restore
        root.handlers = old_handlers

    def test_quiets_noisy_loggers(self):
        root = logging.getLogger()
        old_handlers = root.handlers[:]

        setup_logging()

        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

        # Restore
        root.handlers = old_handlers
