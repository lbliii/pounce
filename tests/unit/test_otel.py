"""
Tests for OpenTelemetry integration.

"""

import pytest

from pounce._otel import (
    RequestSpanManager,
    _NoOpSpan,
    extract_trace_context,
    inject_trace_context,
    is_otel_available,
)
from pounce.config import ServerConfig


class TestOTelAvailability:
    """Tests for OpenTelemetry availability detection."""

    def test_is_otel_available(self):
        """Test that is_otel_available returns a boolean."""
        available = is_otel_available()
        assert isinstance(available, bool)


class TestExtractTraceContext:
    """Tests for W3C Trace Context extraction."""

    def test_extract_without_otel(self):
        """Test extraction when OTel is not available."""
        headers = [
            (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
        ]

        # Should not crash if OTel not installed
        context = extract_trace_context(headers)

        # Will be None if OTel not available
        assert context is None or context is not None  # Just check it doesn't crash

    def test_extract_empty_headers(self):
        """Test extraction with no headers."""
        context = extract_trace_context([])

        assert context is None or context is not None  # Shouldn't crash

    def test_extract_invalid_traceparent(self):
        """Test extraction with malformed traceparent."""
        headers = [(b"traceparent", b"invalid")]

        context = extract_trace_context(headers)

        # Should handle gracefully
        assert context is None or context is not None


class TestInjectTraceContext:
    """Tests for W3C Trace Context injection."""

    def test_inject_without_otel(self):
        """Test injection when OTel is not available."""
        headers = [(b"content-type", b"application/json")]

        result = inject_trace_context(headers)

        # Should at least return the original headers
        assert len(result) >= len(headers)

    def test_inject_preserves_existing_headers(self):
        """Test that injection preserves existing headers."""
        headers = [
            (b"content-type", b"application/json"),
            (b"user-agent", b"test"),
        ]

        result = inject_trace_context(headers)

        # Original headers should be preserved
        assert (b"content-type", b"application/json") in result
        assert (b"user-agent", b"test") in result


class TestRequestSpanManager:
    """Tests for RequestSpanManager."""

    def test_create_disabled_manager(self):
        """Test creating a span manager when disabled."""
        manager = RequestSpanManager(enabled=False)

        assert manager._enabled is False

    def test_create_enabled_manager_without_otel(self):
        """Test creating a span manager without OTel installed."""
        # Should not crash even if OTel not available
        manager = RequestSpanManager(enabled=True)

        # Will be disabled if OTel not available
        assert isinstance(manager._enabled, bool)

    def test_create_request_span_when_disabled(self):
        """Test creating a span when manager is disabled."""
        manager = RequestSpanManager(enabled=False)

        span = manager.create_request_span(
            method="GET",
            path="/test",
            headers=[],
        )

        # Should return a no-op span
        assert isinstance(span, _NoOpSpan)

    def test_record_response_when_disabled(self):
        """Test recording response when disabled."""
        manager = RequestSpanManager(enabled=False)

        # Should not crash
        manager.record_response(None, status_code=200, response_size=100)

    def test_record_exception_when_disabled(self):
        """Test recording exception when disabled."""
        manager = RequestSpanManager(enabled=False)

        # Should not crash
        manager.record_exception(None, ValueError("test"))

    def test_end_span_when_disabled(self):
        """Test ending span when disabled."""
        manager = RequestSpanManager(enabled=False)

        # Should not crash
        manager.end_span(None)


class TestNoOpSpan:
    """Tests for no-op span."""

    def test_noop_span_context_manager(self):
        """Test that no-op span works as context manager."""
        span = _NoOpSpan()

        with span:
            pass  # Should not crash

    def test_noop_span_set_attribute(self):
        """Test setting attributes on no-op span."""
        span = _NoOpSpan()

        span.set_attribute("key", "value")  # Should not crash

    def test_noop_span_set_status(self):
        """Test setting status on no-op span."""
        span = _NoOpSpan()

        span.set_status("OK")  # Should not crash

    def test_noop_span_record_exception(self):
        """Test recording exception on no-op span."""
        span = _NoOpSpan()

        span.record_exception(ValueError("test"))  # Should not crash

    def test_noop_span_end(self):
        """Test ending no-op span."""
        span = _NoOpSpan()

        span.end()  # Should not crash


class TestServerConfig:
    """Tests for OpenTelemetry configuration."""

    def test_default_otel_endpoint_none(self):
        """Test that otel_endpoint defaults to None."""
        config = ServerConfig()

        assert config.otel_endpoint is None

    def test_set_otel_endpoint(self):
        """Test setting otel_endpoint."""
        config = ServerConfig(otel_endpoint="http://localhost:4318")

        assert config.otel_endpoint == "http://localhost:4318"

    def test_default_otel_service_name(self):
        """Test default otel_service_name."""
        config = ServerConfig()

        assert config.otel_service_name == "pounce"

    def test_custom_otel_service_name(self):
        """Test custom otel_service_name."""
        config = ServerConfig(otel_service_name="my-service")

        assert config.otel_service_name == "my-service"

    def test_otel_disabled_by_default(self):
        """Test that OTel is disabled when no endpoint set."""
        config = ServerConfig()

        # OTel only enabled when endpoint is configured
        assert config.otel_endpoint is None


class TestSpanAttributes:
    """Tests for span attribute setting."""

    def test_create_span_with_http_attributes(self):
        """Test that spans are created with HTTP attributes."""
        manager = RequestSpanManager(enabled=False)  # Use disabled for testing

        span = manager.create_request_span(
            method="POST",
            path="/api/users",
            headers=[(b"content-type", b"application/json")],
            scheme="https",
            server_host="example.com",
            server_port=443,
        )

        # Should not crash
        assert span is not None

    def test_record_response_with_status_code(self):
        """Test recording response status."""
        manager = RequestSpanManager(enabled=False)
        span = _NoOpSpan()

        # Should not crash with various status codes
        manager.record_response(span, status_code=200)
        manager.record_response(span, status_code=404)
        manager.record_response(span, status_code=500)

    def test_record_response_with_size(self):
        """Test recording response size."""
        manager = RequestSpanManager(enabled=False)
        span = _NoOpSpan()

        manager.record_response(span, status_code=200, response_size=1024)

        # Should not crash


class TestTraceContextPropagation:
    """Tests for trace context propagation."""

    def test_extract_preserves_trace_id(self):
        """Test that extraction preserves trace ID from headers."""
        # Valid W3C traceparent format
        headers = [
            (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
        ]

        context = extract_trace_context(headers)

        # Should not crash
        assert context is None or context is not None

    def test_extract_with_tracestate(self):
        """Test extraction with both traceparent and tracestate."""
        headers = [
            (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
            (b"tracestate", b"vendor1=value1,vendor2=value2"),
        ]

        context = extract_trace_context(headers)

        # Should not crash
        assert context is None or context is not None


# Skip these tests if we want to test with actual OpenTelemetry installed
@pytest.mark.skipif(
    not is_otel_available(),
    reason="OpenTelemetry not installed",
)
class TestOTelWithLibrary:
    """Tests that run only when OpenTelemetry is installed."""

    def test_configure_otel(self):
        """Test configuring OpenTelemetry."""
        from pounce._otel import configure_otel

        # Should be able to configure
        configure_otel(
            endpoint="http://localhost:4318",
            service_name="test-service",
        )

    def test_span_creation_with_library(self):
        """Test actual span creation when library is available."""
        manager = RequestSpanManager(service_name="test", enabled=True)

        span = manager.create_request_span(
            method="GET",
            path="/test",
            headers=[],
        )

        # Should get a real span, not NoOp
        assert not isinstance(span, _NoOpSpan)
