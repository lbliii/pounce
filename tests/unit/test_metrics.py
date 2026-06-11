"""Tests for pounce.metrics — Prometheus-compatible lifecycle collector."""

import time

from pounce.lifecycle import (
    ConnectionCompleted,
    ConnectionOpened,
    RequestStarted,
    ResponseCompleted,
)
from pounce.metrics import PrometheusCollector


class TestPrometheusCollector:
    """PrometheusCollector tracks metrics from lifecycle events."""

    def _now(self) -> int:
        return time.monotonic_ns()

    def test_connection_gauge(self):
        """Active connections gauge tracks opens and closes."""
        collector = PrometheusCollector()

        collector.record(
            ConnectionOpened(
                connection_id=1,
                worker_id=0,
                client_addr="127.0.0.1",
                client_port=5000,
                server_addr="0.0.0.0",
                server_port=8000,
                protocol="h1",
                timestamp_ns=self._now(),
            )
        )
        collector.record(
            ConnectionOpened(
                connection_id=2,
                worker_id=0,
                client_addr="127.0.0.1",
                client_port=5001,
                server_addr="0.0.0.0",
                server_port=8000,
                protocol="h1",
                timestamp_ns=self._now(),
            )
        )

        snap = collector.snapshot()
        assert snap["connections_active"] == 2

        collector.record(
            ConnectionCompleted(
                connection_id=1,
                worker_id=0,
                requests_served=1,
                total_bytes_sent=100,
                duration_ms=50.0,
                reason="complete",
                timestamp_ns=self._now(),
            )
        )

        snap = collector.snapshot()
        assert snap["connections_active"] == 1
        assert snap["bytes_sent_total"] == 100

    def test_request_counter(self):
        """Requests total increments per response."""
        collector = PrometheusCollector()

        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="GET",
                path="/",
                http_version="1.1",
                timestamp_ns=self._now(),
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=500,
                duration_ms=10.0,
                timestamp_ns=self._now(),
                method="GET",
            )
        )
        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="POST",
                path="/api",
                http_version="1.1",
                timestamp_ns=self._now(),
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=404,
                bytes_sent=100,
                duration_ms=5.0,
                timestamp_ns=self._now(),
                method="POST",
            )
        )

        snap = collector.snapshot()
        assert snap["duration_count"] == 2
        assert snap["requests_in_flight"] == 0

        # The exported counter must carry the real request method, not the
        # "unknown" sentinel (regression test for method-label dropping).
        text = collector.export()
        assert 'http_requests_total{method="GET",status="200"} 1' in text
        assert 'http_requests_total{method="POST",status="404"} 1' in text
        assert 'method="unknown"' not in text

    def test_export_method_label_uses_request_method(self):
        """Exported http_requests_total carries the real method label."""
        collector = PrometheusCollector()

        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=10,
                duration_ms=1.0,
                timestamp_ns=self._now(),
                method="GET",
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=2,
                worker_id=0,
                status=404,
                bytes_sent=10,
                duration_ms=1.0,
                timestamp_ns=self._now(),
                method="POST",
            )
        )

        text = collector.export()
        assert 'http_requests_total{method="GET",status="200"} 1' in text
        assert 'http_requests_total{method="POST",status="404"} 1' in text
        # The empty string is never emitted; default is the stable sentinel.
        assert 'method=""' not in text

    def test_export_method_label_unknown_sentinel(self):
        """Default/empty method falls back to the stable 'unknown' sentinel."""
        collector = PrometheusCollector()

        # ResponseCompleted constructed without a method (e.g. early-out
        # error path) defaults to the "unknown" sentinel.
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=500,
                bytes_sent=0,
                duration_ms=1.0,
                timestamp_ns=self._now(),
            )
        )

        text = collector.export()
        assert 'http_requests_total{method="unknown",status="500"} 1' in text
        assert 'method=""' not in text

    def test_in_flight_gauge(self):
        """In-flight gauge tracks request start/complete."""
        collector = PrometheusCollector()

        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="GET",
                path="/slow",
                http_version="1.1",
                timestamp_ns=self._now(),
            )
        )
        snap = collector.snapshot()
        assert snap["requests_in_flight"] == 1

        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=0,
                duration_ms=100.0,
                timestamp_ns=self._now(),
            )
        )
        snap = collector.snapshot()
        assert snap["requests_in_flight"] == 0

    def test_export_format(self):
        """Export produces valid Prometheus text format."""
        collector = PrometheusCollector()

        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="GET",
                path="/",
                http_version="1.1",
                timestamp_ns=self._now(),
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=500,
                duration_ms=10.0,
                timestamp_ns=self._now(),
            )
        )

        text = collector.export()
        assert "# TYPE http_requests_total counter" in text
        assert "# TYPE http_request_duration_seconds histogram" in text
        assert "# TYPE http_connections_active gauge" in text
        assert "# TYPE http_requests_in_flight gauge" in text
        assert "http_requests_total{" in text
        assert 'http_request_duration_seconds_bucket{le="+Inf"}' in text
        assert "http_request_duration_seconds_sum" in text
        assert "http_request_duration_seconds_count 1" in text

    def test_duration_histogram_buckets(self):
        """Duration histogram populates correct buckets."""
        collector = PrometheusCollector(duration_buckets=(0.01, 0.1, 1.0, float("inf")))

        # 5ms request → should fall in 0.01 bucket
        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="GET",
                path="/",
                http_version="1.1",
                timestamp_ns=self._now(),
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=0,
                duration_ms=5.0,
                timestamp_ns=self._now(),
            )
        )

        text = collector.export()
        assert 'http_request_duration_seconds_bucket{le="0.01"} 1' in text
        assert 'http_request_duration_seconds_bucket{le="0.1"} 1' in text
        assert 'http_request_duration_seconds_bucket{le="+Inf"} 1' in text
