"""Tests for pounce.logging — configuration, access log formatting, and format modes."""

import io
import json
import logging
from unittest.mock import patch

import pytest

from pounce.config import ServerConfig
from pounce.logging import (
    _human_bytes,
    _JSONFormatter,
    _PrettyFormatter,
    access_log,
    configure_logging,
)


class TestConfigureLogging:
    """configure_logging() sets up pounce loggers."""

    def test_sets_log_level(self):
        config = ServerConfig(log_level="debug", log_format="text")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.DEBUG

    def test_info_level(self):
        config = ServerConfig(log_level="info", log_format="text")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.INFO

    def test_warning_level(self):
        config = ServerConfig(log_level="warning", log_format="text")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.WARNING

    def test_configures_chirp_logger(self):
        """configure_logging wires up the chirp logger alongside pounce."""
        config = ServerConfig(log_level="debug", log_format="text")
        configure_logging(config)
        chirp_logger = logging.getLogger("chirp")
        assert chirp_logger.level == logging.DEBUG
        assert len(chirp_logger.handlers) >= 1

    def test_json_format_sets_json_formatter(self):
        """log_format='json' installs the JSON formatter on handlers."""
        root = logging.getLogger("pounce")
        root.handlers.clear()
        config = ServerConfig(log_format="json")
        configure_logging(config)
        for handler in root.handlers:
            if isinstance(handler.formatter, _JSONFormatter):
                break
        else:
            pytest.fail("No _JSONFormatter found on pounce logger handlers")

    def test_pretty_format_sets_pretty_formatter(self):
        """log_format='auto' with TTY installs _PrettyFormatter."""
        root = logging.getLogger("pounce")
        root.handlers.clear()
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            config = ServerConfig(log_format="auto")
            configure_logging(config)
        for handler in root.handlers:
            if isinstance(handler.formatter, _PrettyFormatter):
                break
        else:
            pytest.fail("No _PrettyFormatter found on pounce logger handlers")


class TestAutoFormatDetection:
    """auto format resolves to pretty on TTY, JSON when piped."""

    def test_auto_resolves_to_pretty_on_tty(self):
        import pounce.logging as pounce_logging

        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            configure_logging(ServerConfig(log_format="auto"))
        assert pounce_logging._resolved_format == "pretty"

    def test_auto_resolves_to_json_when_piped(self):
        import pounce.logging as pounce_logging

        with patch("sys.stderr") as mock_stderr, patch("sys.stdout") as mock_stdout:
            mock_stderr.isatty.return_value = False
            mock_stdout.isatty.return_value = False
            configure_logging(ServerConfig(log_format="auto"))
        assert pounce_logging._resolved_format == "json"

    def test_auto_resolves_to_pretty_when_stdout_tty_only(self):
        """IDEs may report stderr as non-TTY but stdout as TTY."""
        import pounce.logging as pounce_logging

        with patch("sys.stderr") as mock_stderr, patch("sys.stdout") as mock_stdout:
            mock_stderr.isatty.return_value = False
            mock_stdout.isatty.return_value = True
            configure_logging(ServerConfig(log_format="auto"))
        assert pounce_logging._resolved_format == "pretty"

    def test_explicit_text_stays_text(self):
        import pounce.logging as pounce_logging

        configure_logging(ServerConfig(log_format="text"))
        assert pounce_logging._resolved_format == "text"

    def test_explicit_json_stays_json(self):
        import pounce.logging as pounce_logging

        configure_logging(ServerConfig(log_format="json"))
        assert pounce_logging._resolved_format == "json"


class TestAccessLog:
    """access_log() writes formatted entries in text mode."""

    def test_format(self, caplog):
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
        assert "[abcdef123456]" in msg

    def test_text_access_log_no_suffix_without_request_id(self, caplog):
        """Text access log has no trailing bracket when no request_id."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/", 200, 0, 1.0, "client:80")

        msg = caplog.records[0].getMessage()
        assert "[" not in msg

    def test_text_access_log_includes_worker_id(self, caplog):
        """Text access log appends worker ID when provided."""
        configure_logging(ServerConfig(log_format="text"))
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/", 200, 0, 1.0, "client:80", worker_id=3)

        msg = caplog.records[0].getMessage()
        assert "w3" in msg


class TestJSONAccessLog:
    """JSON-format access log output — flat, no double nesting."""

    def _capture_json_line(self, **kwargs):
        """Call access_log in JSON mode and capture the stderr output."""
        configure_logging(ServerConfig(log_format="json"))
        buf = io.StringIO()
        with patch("pounce.logging.sys") as mock_sys:
            mock_sys.stderr = buf
            access_log(**kwargs)
        output = buf.getvalue().strip()
        return json.loads(output)

    def test_json_access_log_is_flat(self):
        """JSON output has access fields as top-level keys, not nested in 'message'."""
        parsed = self._capture_json_line(
            method="GET",
            path="/api",
            status=200,
            bytes_sent=512,
            duration_ms=3.5,
            client="127.0.0.1:5000",
        )
        assert parsed["method"] == "GET"
        assert parsed["path"] == "/api"
        assert parsed["status"] == 200
        assert parsed["bytes"] == 512
        assert parsed["duration_ms"] == 3.5
        assert parsed["client"] == "127.0.0.1:5000"
        # Must NOT have a "message" key wrapping everything
        assert "message" not in parsed

    def test_json_access_log_uses_short_keys(self):
        """JSON output uses short key names."""
        parsed = self._capture_json_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
            request_id="abcdef1234567890",
        )
        assert "ts" in parsed
        assert "req_id" in parsed
        assert parsed["req_id"] == "abcdef12"  # Truncated to 8 chars

    def test_json_5xx_level_is_warn(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/fail",
            status=500,
            bytes_sent=0,
            duration_ms=10.0,
            client="c:80",
        )
        assert parsed["level"] == "warn"
        assert parsed["status"] == 500

    def test_json_2xx_level_is_info(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/ok",
            status=200,
            bytes_sent=100,
            duration_ms=5.0,
            client="c:80",
        )
        assert parsed["level"] == "info"

    def test_json_includes_request_id(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/api",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
            request_id="abc123def456",
        )
        assert "req_id" in parsed

    def test_json_no_request_id_when_none(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/api",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
        )
        assert "req_id" not in parsed

    def test_json_has_timestamp(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
        )
        assert "ts" in parsed

    def test_json_includes_worker_id(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
            worker_id=5,
        )
        assert parsed["worker"] == 5

    def test_json_no_worker_when_none(self):
        parsed = self._capture_json_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
        )
        assert "worker" not in parsed


class TestPrettyAccessLog:
    """Pretty-format access log output for TTY."""

    def _capture_pretty_line(self, **kwargs):
        """Call access_log in pretty mode and capture stderr output."""
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            configure_logging(ServerConfig(log_format="auto"))
        buf = io.StringIO()
        buf.isatty = lambda: True  # kida ANSI filters check TTY
        with patch("pounce._output.sys") as mock_sys:
            mock_sys.stderr = buf
            access_log(**kwargs)
        return buf.getvalue().strip()

    def test_pretty_contains_method_and_path(self):
        line = self._capture_pretty_line(
            method="GET",
            path="/api/users",
            status=200,
            bytes_sent=1234,
            duration_ms=5.3,
            client="127.0.0.1:5000",
        )
        assert "GET" in line
        assert "/api/users" in line

    def test_pretty_contains_status(self):
        line = self._capture_pretty_line(
            method="GET",
            path="/",
            status=404,
            bytes_sent=0,
            duration_ms=1.0,
            client="c:80",
        )
        assert "404" in line

    def test_pretty_contains_ansi_color(self):
        """Pretty mode includes ANSI escape codes."""
        line = self._capture_pretty_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=100,
            duration_ms=1.0,
            client="c:80",
        )
        assert "\033[" in line  # Contains ANSI escape

    def test_pretty_human_bytes(self):
        line = self._capture_pretty_line(
            method="GET",
            path="/",
            status=200,
            bytes_sent=7730,
            duration_ms=1.0,
            client="c:80",
        )
        assert "7.7kB" in line


class TestHumanBytes:
    """_human_bytes() formats byte counts."""

    def test_bytes(self):
        assert _human_bytes(42) == "42B"

    def test_kilobytes(self):
        assert _human_bytes(7730) == "7.7kB"

    def test_megabytes(self):
        assert _human_bytes(1_500_000) == "1.5MB"

    def test_zero(self):
        assert _human_bytes(0) == "0B"


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
        assert parsed["level"] == "info"
        assert parsed["logger"] == "test"
        assert "ts" in parsed

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

    def test_auto_is_valid(self):
        config = ServerConfig(log_format="auto")
        assert config.log_format == "auto"

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="log_format"):
            ServerConfig(log_format="xml")
