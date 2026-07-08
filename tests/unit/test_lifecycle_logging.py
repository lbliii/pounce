"""
Tests for structured lifecycle event logging.

"""

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from pounce.config import ServerConfig
from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LoggingCollector,
    RequestStarted,
    ResponseCompleted,
    StreamClosed,
    monotonic_ns,
)


class TestLoggingCollector:
    """Tests for LoggingCollector."""

    def test_create_collector(self):
        """Test creating a LoggingCollector."""
        collector = LoggingCollector(
            slow_request_threshold_ms=2000,
            log_format="json",
        )

        assert collector._slow_threshold_ms == 2000
        assert collector._json_format is True
        assert collector._health_check_path is None

    def test_create_collector_with_health_check_path(self):
        """Test creating collector with health check filtering."""
        collector = LoggingCollector(
            slow_request_threshold_ms=5000,
            log_format="json",
            health_check_path="/health",
        )

        assert collector._health_check_path == "/health"

    def test_connection_opened_logged_at_debug(self):
        """Test that ConnectionOpened is logged at DEBUG level."""
        collector = LoggingCollector(log_format="json")

        event = ConnectionOpened(
            connection_id=1,
            worker_id=1,
            client_addr="127.0.0.1",
            client_port=12345,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            # Should log at DEBUG level
            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.DEBUG

            # Check JSON structure
            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            assert log_data["event"] == "ConnectionOpened"
            assert log_data["connection_id"] == 1
            assert log_data["worker_id"] == 1
            assert log_data["client_addr"] == "127.0.0.1"
            assert log_data["protocol"] == "h1"
            assert "timestamp" in log_data

    def test_request_started_logged_at_debug(self):
        """Test that RequestStarted is logged at DEBUG level."""
        collector = LoggingCollector(log_format="json")

        event = RequestStarted(
            connection_id=1,
            worker_id=1,
            method="GET",
            path="/api/users",
            http_version="1.1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.DEBUG

    def test_fast_response_logged_at_debug(self):
        """Test that fast responses are logged at DEBUG level."""
        collector = LoggingCollector(
            slow_request_threshold_ms=5000,
            log_format="json",
        )

        event = ResponseCompleted(
            connection_id=1,
            worker_id=1,
            status=200,
            bytes_sent=1024,
            duration_ms=100.5,  # Fast (< 5000ms)
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.DEBUG

            # Should NOT be marked as slow
            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            assert "slow" not in log_data or not log_data.get("slow")

    def test_slow_response_logged_at_info(self):
        """Test that slow responses are logged at INFO level."""
        collector = LoggingCollector(
            slow_request_threshold_ms=2000,
            log_format="json",
        )

        event = ResponseCompleted(
            connection_id=1,
            worker_id=1,
            status=200,
            bytes_sent=1024,
            duration_ms=3500.0,  # Slow (> 2000ms)
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            # Slow requests logged at INFO
            assert mock_log.call_args[0][0] == logging.INFO

            # Should be marked as slow
            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            assert log_data["slow"] is True
            assert log_data["duration_ms"] == 3500.0

    def test_streaming_response_not_marked_slow(self):
        """Long-lived SSE/chunked streams must not trigger slow:true."""
        collector = LoggingCollector(
            slow_request_threshold_ms=5000,
            log_format="json",
        )

        event = ResponseCompleted(
            connection_id=1,
            worker_id=1,
            status=200,
            bytes_sent=6_504_291,
            duration_ms=899_981.2,
            timestamp_ns=monotonic_ns(),
            method="GET",
            streaming=True,
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.DEBUG

            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            assert log_data.get("streaming") is True
            assert "slow" not in log_data or not log_data.get("slow")

    def test_client_disconnected_logged_at_warning(self):
        """Test that ClientDisconnected is logged at WARNING level."""
        collector = LoggingCollector(log_format="json")

        event = ClientDisconnected(
            connection_id=1,
            worker_id=1,
            during_streaming=True,
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.WARNING

    def test_drained_stream_close_logged_at_info(self):
        collector = LoggingCollector(log_format="json")
        event = StreamClosed(
            connection_id=1,
            worker_id=1,
            duration_ms=250.0,
            reason="drain",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.call_args[0][0] == logging.INFO
            log_data = json.loads(mock_log.call_args[0][2])
            assert log_data["event"] == "StreamClosed"
            assert log_data["reason"] == "drain"

    def test_connection_closed_logged_at_debug(self):
        """Test that ConnectionCompleted is logged at DEBUG level."""
        collector = LoggingCollector(log_format="json")

        event = ConnectionCompleted(
            connection_id=1,
            worker_id=1,
            requests_served=5,
            total_bytes_sent=10240,
            duration_ms=1500.5,
            reason="complete",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            assert mock_log.call_args[0][0] == logging.DEBUG

            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            assert log_data["event"] == "ConnectionCompleted"
            assert log_data["requests_served"] == 5
            assert log_data["reason"] == "complete"

    def test_health_check_filtered(self):
        """Test that health check requests are filtered from logs."""
        collector = LoggingCollector(
            log_format="json",
            health_check_path="/health",
        )

        event = RequestStarted(
            connection_id=1,
            worker_id=1,
            method="GET",
            path="/health",  # Should be filtered
            http_version="1.1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            # Should NOT log health checks
            assert not mock_log.called

    def test_non_health_check_not_filtered(self):
        """Test that non-health-check requests are logged."""
        collector = LoggingCollector(
            log_format="json",
            health_check_path="/health",
        )

        event = RequestStarted(
            connection_id=1,
            worker_id=1,
            method="GET",
            path="/api/users",  # Not a health check
            http_version="1.1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            # Should log normal requests
            assert mock_log.called

    def test_text_format_logging(self):
        """Test logging in text format instead of JSON."""
        collector = LoggingCollector(
            log_format="text",
            slow_request_threshold_ms=5000,
        )

        event = ConnectionOpened(
            connection_id=1,
            worker_id=1,
            client_addr="127.0.0.1",
            client_port=12345,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            assert mock_log.called
            # In text format, check the message argument
            # call_args[0] is (level, format_string, message, event_dict)
            assert len(mock_log.call_args[0]) >= 3
            log_message = mock_log.call_args[0][2]
            assert "Connection opened" in log_message

    def test_correlation_ids_in_logs(self):
        """Test that connection_id and worker_id are included for correlation."""
        collector = LoggingCollector(log_format="json")

        event = ResponseCompleted(
            connection_id=42,
            worker_id=3,
            status=200,
            bytes_sent=1024,
            duration_ms=150.0,
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            # Correlation IDs for distributed tracing
            assert log_data["connection_id"] == 42
            assert log_data["worker_id"] == 3


class TestServerConfigLifecycleLogging:
    """Tests for lifecycle logging configuration."""

    def test_lifecycle_logging_disabled_by_default(self):
        """Test that lifecycle logging is disabled by default."""
        config = ServerConfig()
        assert config.lifecycle_logging is False

    def test_lifecycle_logging_can_be_enabled(self):
        """Test that lifecycle logging can be enabled."""
        config = ServerConfig(lifecycle_logging=True)
        assert config.lifecycle_logging is True

    def test_slow_request_threshold_default(self):
        """Test default slow request threshold."""
        config = ServerConfig()
        assert config.log_slow_requests_threshold == 5.0

    def test_slow_request_threshold_configurable(self):
        """Test that slow request threshold is configurable."""
        config = ServerConfig(log_slow_requests_threshold=2.5)
        assert config.log_slow_requests_threshold == 2.5

    def test_slow_request_threshold_validation(self):
        """Test that slow request threshold must be positive."""
        with pytest.raises(ValueError, match="log_slow_requests_threshold must be > 0"):
            ServerConfig(log_slow_requests_threshold=0.0)

        with pytest.raises(ValueError, match="log_slow_requests_threshold must be > 0"):
            ServerConfig(log_slow_requests_threshold=-1.0)


class TestLoggingCollectorThreadSafety:
    """Tests for thread safety of LoggingCollector."""

    def test_concurrent_logging(self):
        """Test that LoggingCollector is thread-safe."""
        import threading

        collector = LoggingCollector(log_format="json")
        events_logged = []

        def log_event(event_id):
            event = ConnectionOpened(
                connection_id=event_id,
                worker_id=1,
                client_addr="127.0.0.1",
                client_port=12345,
                server_addr="0.0.0.0",
                server_port=8000,
                protocol="h1",
                timestamp_ns=monotonic_ns(),
            )
            with patch.object(collector._logger, "log"):
                collector.record(event)
                events_logged.append(event_id)

        # Create multiple threads logging concurrently
        threads = [threading.Thread(target=log_event, args=(i,)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All events should be logged
        assert len(events_logged) == 10


class TestTimestampConversion:
    """Tests for timestamp conversion in logging."""

    def test_timestamp_converted_to_iso_format(self):
        """Test that nanosecond timestamps are converted to ISO format."""
        collector = LoggingCollector(log_format="json")

        event = ConnectionOpened(
            connection_id=1,
            worker_id=1,
            client_addr="127.0.0.1",
            client_port=12345,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

            # Should have ISO format timestamp, not nanoseconds
            assert "timestamp" in log_data
            assert "T" in log_data["timestamp"]  # ISO format
            assert "timestamp_ns" not in log_data  # Original removed

    def test_timestamp_is_wall_clock(self):
        """Freshly recorded events must use wall-clock time, not monotonic epoch."""
        collector = LoggingCollector(log_format="json")
        before = datetime.now(UTC)

        event = ResponseCompleted(
            connection_id=1,
            worker_id=1,
            status=200,
            bytes_sent=100,
            duration_ms=1.0,
            timestamp_ns=monotonic_ns(),
        )

        with patch.object(collector._logger, "log") as mock_log:
            collector.record(event)

            log_message = mock_log.call_args[0][1]
            if "%s" in log_message:
                log_data = json.loads(mock_log.call_args[0][2])
            else:
                log_data = json.loads(log_message)

        after = datetime.now(UTC) + timedelta(seconds=2)
        recorded = datetime.fromisoformat(log_data["timestamp"])
        assert before - timedelta(seconds=1) <= recorded <= after
        assert recorded.year >= 2020
