"""Tests for pounce.logging — configuration and access log formatting."""

import json
import logging

import pytest

from pounce.config import ServerConfig
from pounce.logging import _JSONFormatter, access_log, configure_logging


class TestConfigureLogging:
    """configure_logging() sets up pounce loggers."""

    def test_sets_log_level(self):
        config = ServerConfig(log_level="debug")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.DEBUG

    def test_info_level(self):
        config = ServerConfig(log_level="info")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.INFO

    def test_warning_level(self):
        config = ServerConfig(log_level="warning")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.WARNING

    def test_configures_chirp_logger(self):
        """configure_logging wires up the chirp logger alongside pounce."""
        config = ServerConfig(log_level="debug")
        configure_logging(config)
        chirp_logger = logging.getLogger("chirp")
        assert chirp_logger.level == logging.DEBUG
        assert len(chirp_logger.handlers) >= 1

    def test_json_format_sets_json_formatter(self):
        """log_format='json' installs the JSON formatter on handlers."""
        # Clear existing handlers so configure_logging adds new ones
        root = logging.getLogger("pounce")
        root.handlers.clear()
        config = ServerConfig(log_format="json")
        configure_logging(config)
        for handler in root.handlers:
            if isinstance(handler.formatter, _JSONFormatter):
                break
        else:
            pytest.fail("No _JSONFormatter found on pounce logger handlers")


class TestAccessLog:
    """access_log() writes formatted entries."""

    def test_format(self, caplog):
        # Ensure text mode
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/api/users", 200, 1234, 5.3, "127.0.0.1:5000")

        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "GET" in msg
        assert "/api/users" in msg
        assert "200" in msg
        assert "1234" in msg
        assert "5.3ms" in msg
        assert "127.0.0.1:5000" in msg

    def test_404_status(self, caplog):
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/missing", 404, 0, 1.2, "10.0.0.1:9999")

        msg = caplog.records[0].getMessage()
        assert "404" in msg
        assert "/missing" in msg

    def test_5xx_logged_at_warning(self, caplog):
        """5xx responses are logged at WARNING level."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.DEBUG, logger="pounce.access"):
            access_log("POST", "/api/create", 500, 42, 100.0, "10.0.0.1:1234")

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING

    def test_503_logged_at_warning(self, caplog):
        """All 5xx statuses use WARNING."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.DEBUG, logger="pounce.access"):
            access_log("GET", "/", 503, 0, 50.0, "client:80")

        assert caplog.records[0].levelno == logging.WARNING

    def test_200_logged_at_info(self, caplog):
        """Successful responses stay at INFO."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.DEBUG, logger="pounce.access"):
            access_log("GET", "/", 200, 100, 5.0, "client:80")

        assert caplog.records[0].levelno == logging.INFO

    def test_404_logged_at_info(self, caplog):
        """4xx responses are INFO, not WARNING."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.DEBUG, logger="pounce.access"):
            access_log("GET", "/missing", 404, 0, 1.0, "client:80")

        assert caplog.records[0].levelno == logging.INFO

    def test_text_access_log_includes_request_id(self, caplog):
        """Text access log appends truncated request ID."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/", 200, 0, 1.0, "client:80", request_id="abcdef123456789")

        msg = caplog.records[0].getMessage()
        assert "[abcdef123456]" in msg  # Truncated to 12 chars

    def test_text_access_log_no_suffix_without_request_id(self, caplog):
        """Text access log has no trailing bracket when no request_id."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/", 200, 0, 1.0, "client:80")

        msg = caplog.records[0].getMessage()
        assert "[" not in msg


class TestJSONAccessLog:
    """JSON-format access log output."""

    def test_json_access_log_is_valid_json(self, caplog):
        configure_logging(ServerConfig(log_format="json"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/api", 200, 512, 3.5, "127.0.0.1:5000")

        assert len(caplog.records) == 1
        # The message itself is a JSON string
        msg = caplog.records[0].getMessage()
        parsed = json.loads(msg)
        assert parsed["method"] == "GET"
        assert parsed["path"] == "/api"
        assert parsed["status"] == 200
        assert parsed["bytes_sent"] == 512
        assert parsed["duration_ms"] == 3.5
        assert parsed["client"] == "127.0.0.1:5000"

    def test_json_access_log_5xx_at_warning(self, caplog):
        configure_logging(ServerConfig(log_format="json"))
        with caplog.at_level(logging.DEBUG, logger="pounce.access"):
            access_log("GET", "/fail", 500, 0, 10.0, "client:80")

        assert caplog.records[0].levelno == logging.WARNING
        parsed = json.loads(caplog.records[0].getMessage())
        assert parsed["level"] == "WARNING"
        assert parsed["status"] == 500

    def test_json_access_log_includes_request_id(self, caplog):
        configure_logging(ServerConfig(log_format="json"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/api", 200, 0, 1.0, "client:80", request_id="abc123def456")

        parsed = json.loads(caplog.records[0].getMessage())
        assert parsed["request_id"] == "abc123def456"

    def test_json_access_log_no_request_id_when_none(self, caplog):
        configure_logging(ServerConfig(log_format="json"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/api", 200, 0, 1.0, "client:80")

        parsed = json.loads(caplog.records[0].getMessage())
        assert "request_id" not in parsed

    def test_json_access_log_has_timestamp(self, caplog):
        configure_logging(ServerConfig(log_format="json"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/", 200, 0, 1.0, "client:80")

        parsed = json.loads(caplog.records[0].getMessage())
        assert "timestamp" in parsed


class TestJSONFormatter:
    """_JSONFormatter produces valid structured JSON."""

    def test_formats_basic_record(self):
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert "timestamp" in parsed

    def test_includes_exception_info(self):
        formatter = _JSONFormatter()
        try:
            msg = "boom"
            raise ValueError(msg)
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="error occurred",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestLogFormatValidation:
    """ServerConfig validates log_format values."""

    def test_text_is_valid(self):
        config = ServerConfig(log_format="text")
        assert config.log_format == "text"

    def test_json_is_valid(self):
        config = ServerConfig(log_format="json")
        assert config.log_format == "json"

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="log_format"):
            ServerConfig(log_format="xml")
