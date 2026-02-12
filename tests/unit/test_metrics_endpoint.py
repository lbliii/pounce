"""
Tests for Prometheus metrics endpoint.

"""

import pytest

from pounce._metrics_handler import create_metrics_app, wrap_app_with_metrics
from pounce.config import ServerConfig
from pounce.metrics import PrometheusCollector


class TestMetricsConfiguration:
    """Tests for metrics configuration."""

    def test_metrics_disabled_by_default(self):
        """Test that metrics are disabled by default."""
        config = ServerConfig()
        assert config.metrics_enabled is False

    def test_metrics_can_be_enabled(self):
        """Test that metrics can be enabled."""
        config = ServerConfig(metrics_enabled=True)
        assert config.metrics_enabled is True

    def test_default_metrics_path(self):
        """Test default metrics path."""
        config = ServerConfig()
        assert config.metrics_path == "/metrics"

    def test_custom_metrics_path(self):
        """Test custom metrics path."""
        config = ServerConfig(metrics_path="/prometheus")
        assert config.metrics_path == "/prometheus"

    def test_metrics_path_validation(self):
        """Test that metrics_path must start with /."""
        with pytest.raises(ValueError, match="metrics_path must start with /"):
            ServerConfig(metrics_path="metrics")


class TestMetricsHandler:
    """Tests for metrics handler."""

    @pytest.mark.asyncio
    async def test_metrics_app_returns_prometheus_format(self):
        """Test that metrics app returns valid Prometheus format."""
        collector = PrometheusCollector()
        app = create_metrics_app(collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        # Check response
        assert len(messages) == 2
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 200
        assert (b"content-type", b"text/plain; version=0.0.4; charset=utf-8") in messages[0]["headers"]

        assert messages[1]["type"] == "http.response.body"
        body = messages[1]["body"].decode("utf-8")
        assert "# HELP http_requests_total" in body
        assert "# TYPE http_requests_total counter" in body

    @pytest.mark.asyncio
    async def test_metrics_app_404_for_other_paths(self):
        """Test that metrics app returns 404 for non-metrics paths."""
        collector = PrometheusCollector()
        app = create_metrics_app(collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/other",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        # Check 404 response
        assert len(messages) == 2
        assert messages[0]["status"] == 404
        assert messages[1]["body"] == b"Not Found"


class TestWrappedApp:
    """Tests for app wrapping with metrics."""

    @pytest.mark.asyncio
    async def test_wrapped_app_intercepts_metrics_path(self):
        """Test that wrapped app intercepts metrics requests."""

        async def original_app(scope, receive, send):
            """Original app that shouldn't be called for /metrics."""
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({
                "type": "http.response.body",
                "body": b"Original app",
            })

        collector = PrometheusCollector()
        wrapped = wrap_app_with_metrics(original_app, collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await wrapped(scope, receive, send)

        # Should return metrics, not original app
        assert messages[0]["status"] == 200
        body = messages[1]["body"].decode("utf-8")
        assert "# HELP http_requests_total" in body
        assert "Original app" not in body

    @pytest.mark.asyncio
    async def test_wrapped_app_passes_other_requests(self):
        """Test that wrapped app passes non-metrics requests to original."""

        async def original_app(scope, receive, send):
            """Original app."""
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({
                "type": "http.response.body",
                "body": b"Original app",
            })

        collector = PrometheusCollector()
        wrapped = wrap_app_with_metrics(original_app, collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/users",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await wrapped(scope, receive, send)

        # Should call original app
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"Original app"


class TestMetricsContent:
    """Tests for metrics content."""

    @pytest.mark.asyncio
    async def test_metrics_updated_by_collector(self):
        """Test that metrics reflect collector state."""
        from pounce.lifecycle import ConnectionOpened, ResponseCompleted, monotonic_ns

        collector = PrometheusCollector()

        # Simulate some events
        collector.record(
            ConnectionOpened(
                connection_id=1,
                worker_id=1,
                client_addr="127.0.0.1",
                client_port=12345,
                server_addr="0.0.0.0",
                server_port=8000,
                protocol="h1",
                timestamp_ns=monotonic_ns(),
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=1,
                status=200,
                bytes_sent=1024,
                duration_ms=150.5,
                timestamp_ns=monotonic_ns(),
            )
        )

        app = create_metrics_app(collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        body = messages[1]["body"].decode("utf-8")

        # Check that metrics include our events
        assert "http_connections_active 1" in body
        assert 'http_requests_total{method="unknown",status="200"} 1' in body
        assert "http_request_duration_seconds_count 1" in body

    @pytest.mark.asyncio
    async def test_metrics_content_type_header(self):
        """Test that metrics have correct content-type."""
        collector = PrometheusCollector()
        app = create_metrics_app(collector, "/metrics")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        headers = dict(messages[0]["headers"])
        assert headers[b"content-type"] == b"text/plain; version=0.0.4; charset=utf-8"
        assert b"content-length" in headers


class TestCustomMetricsPath:
    """Tests for custom metrics paths."""

    @pytest.mark.asyncio
    async def test_custom_metrics_path(self):
        """Test that custom metrics path works."""
        collector = PrometheusCollector()
        app = create_metrics_app(collector, "/prometheus")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/prometheus",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        # Should return metrics at custom path
        assert messages[0]["status"] == 200
        body = messages[1]["body"].decode("utf-8")
        assert "# HELP http_requests_total" in body

    @pytest.mark.asyncio
    async def test_custom_path_doesnt_respond_to_default(self):
        """Test that custom path doesn't respond to /metrics."""
        collector = PrometheusCollector()
        app = create_metrics_app(collector, "/prometheus")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

        # Should return 404 for /metrics
        assert messages[0]["status"] == 404
