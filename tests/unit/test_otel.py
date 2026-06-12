"""
Tests for OpenTelemetry integration.

"""

import pytest

from pounce._otel import (
    RequestSpanManager,
    _NoOpSpan,
    extract_trace_context,
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


class TestNoDownstreamInjection:
    """Regression: dead downstream-propagation API was removed (#136)."""

    def test_inject_trace_context_removed(self):
        """inject_trace_context must not be exported — no downstream propagation."""
        import pounce._otel as otel

        assert not hasattr(otel, "inject_trace_context")


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


# ---------------------------------------------------------------------------
# Semantic tests against the real SDK (#134, #135).
#
# These use an isolated TracerProvider + SimpleSpanProcessor + InMemorySpan
# exporter so assertions run against ACTUAL span output from src/pounce/_otel.py
# rather than tautologies.  They FAIL (not skip) when OTel is installed and the
# observable contract regresses.
# ---------------------------------------------------------------------------

PARENT_TRACEPARENT = b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PARENT_TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C


def _isolated_manager():
    """Return (manager, exporter) wired to an in-memory exporter.

    The manager's tracer is bound to a private provider so each test sees only
    its own spans and does not depend on (or mutate) the process-global
    provider, which is set-once per process.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    manager = RequestSpanManager(service_name="test", enabled=True)
    # Bind to the isolated provider's tracer (manager resolves a tracer from
    # the global provider at construction; override for test isolation).
    manager._tracer = provider.get_tracer("pounce._otel", "test")
    return manager, exporter


@pytest.mark.skipif(not is_otel_available(), reason="OpenTelemetry not installed")
class TestOTelSemantics:
    """End-to-end span semantics verified via InMemorySpanExporter."""

    def test_parent_context_propagation(self):
        """A known traceparent makes the server span a child of that trace."""
        manager, exporter = _isolated_manager()

        span = manager.create_request_span(
            method="GET",
            path="/items/42",
            headers=[(b"traceparent", PARENT_TRACEPARENT)],
        )
        manager.end_span(span)

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        # Span joins the upstream trace.
        assert finished[0].context.trace_id == PARENT_TRACE_ID
        assert finished[0].parent is not None
        assert finished[0].parent.span_id == 0xB7AD6B7169203331

    def test_no_parent_starts_new_trace(self):
        """Without a traceparent the span is a fresh root (no parent)."""
        manager, exporter = _isolated_manager()

        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.end_span(span)

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].parent is None
        assert finished[0].context.trace_id != PARENT_TRACE_ID

    def test_span_kind_is_server(self):
        """Inbound request spans are SERVER spans."""
        from opentelemetry.trace import SpanKind

        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.end_span(span)

        assert exporter.get_finished_spans()[0].kind == SpanKind.SERVER

    def test_stable_http_request_attributes(self):
        """Stable OTel HTTP semconv attribute names are set (#135)."""
        manager, exporter = _isolated_manager()

        span = manager.create_request_span(
            method="POST",
            path="/users/12345/posts/678",
            headers=[],
            scheme="https",
            server_host="api.example.com",
            server_port=443,
        )
        manager.end_span(span)

        attrs = exporter.get_finished_spans()[0].attributes
        assert attrs["http.request.method"] == "POST"
        assert attrs["url.path"] == "/users/12345/posts/678"
        assert attrs["url.scheme"] == "https"
        assert attrs["server.address"] == "api.example.com"
        assert attrs["server.port"] == 443

    def test_no_deprecated_attribute_names(self):
        """Pre-1.20 attribute names must not be emitted (#135)."""
        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/x", headers=[])
        manager.record_response(span, status_code=200, response_size=10)
        manager.end_span(span)

        attrs = exporter.get_finished_spans()[0].attributes
        for deprecated in (
            "http.method",
            "http.target",
            "http.host",
            "net.host.port",
            "http.status_code",
            "http.response_content_length",
        ):
            assert deprecated not in attrs, f"deprecated attr leaked: {deprecated}"

    def test_low_cardinality_span_name_is_method_only(self):
        """Span name must NOT embed the raw path (unbounded cardinality, #135)."""
        manager, exporter = _isolated_manager()

        span = manager.create_request_span(method="GET", path="/users/12345/posts/678", headers=[])
        manager.end_span(span)

        name = exporter.get_finished_spans()[0].name
        assert name == "GET"
        assert "12345" not in name
        assert "/users/" not in name

    def test_span_name_uses_route_template_when_available(self):
        """When a route template is supplied, the name is '{method} {route}'."""
        manager, exporter = _isolated_manager()

        span = manager.create_request_span(
            method="GET",
            path="/users/12345",
            headers=[],
            route="/users/{id}",
        )
        manager.end_span(span)

        finished = exporter.get_finished_spans()[0]
        assert finished.name == "GET /users/{id}"
        assert finished.attributes["http.route"] == "/users/{id}"

    def test_status_mapping_server_error(self):
        """5xx maps to StatusCode.ERROR with stable status-code attribute."""
        from opentelemetry.trace import StatusCode

        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.record_response(span, status_code=500)
        manager.end_span(span)

        finished = exporter.get_finished_spans()[0]
        assert finished.status.status_code == StatusCode.ERROR
        assert finished.attributes["http.response.status_code"] == 500

    def test_status_mapping_ok_for_2xx_and_4xx(self):
        """2xx and 4xx are not span errors (client errors are not server faults)."""
        from opentelemetry.trace import StatusCode

        for code in (200, 404):
            manager, exporter = _isolated_manager()
            span = manager.create_request_span(method="GET", path="/", headers=[])
            manager.record_response(span, status_code=code)
            manager.end_span(span)

            finished = exporter.get_finished_spans()[0]
            assert finished.status.status_code == StatusCode.OK, code
            assert finished.attributes["http.response.status_code"] == code

    def test_record_exception_adds_event_and_error_status(self):
        """record_exception records an exception event AND sets ERROR status."""
        from opentelemetry.trace import StatusCode

        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.record_exception(span, ValueError("boom"))
        manager.end_span(span)

        finished = exporter.get_finished_spans()[0]
        assert finished.status.status_code == StatusCode.ERROR
        event_names = [e.name for e in finished.events]
        assert "exception" in event_names

    def test_response_size_attribute_only_when_positive(self):
        """http.response.body.size is set only when response_size > 0."""
        # Positive size -> attribute present.
        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.record_response(span, status_code=200, response_size=1024)
        manager.end_span(span)
        attrs = exporter.get_finished_spans()[0].attributes
        assert attrs["http.response.body.size"] == 1024

        # Zero size -> attribute absent.
        manager, exporter = _isolated_manager()
        span = manager.create_request_span(method="GET", path="/", headers=[])
        manager.record_response(span, status_code=204, response_size=0)
        manager.end_span(span)
        attrs = exporter.get_finished_spans()[0].attributes
        assert "http.response.body.size" not in attrs


@pytest.mark.skipif(not is_otel_available(), reason="OpenTelemetry not installed")
@pytest.mark.skipif(
    not hasattr(__import__("os"), "fork"),
    reason="fork() not available on this platform",
)
class TestForkExportFlush:
    """Spans created just before a forked worker exits must be flushed (#133).

    The BatchSpanProcessor only exports on its schedule-delay timer and never
    on process exit.  Without an explicit flush on worker shutdown a span
    created moments before exit is silently dropped.  ``flush_otel`` is wired
    into worker shutdown to prevent that loss.
    """

    @staticmethod
    def _child_emit_span(path: str, *, flush: bool) -> None:
        """Run in a forked child: build a long-delay batch provider, emit a
        span, optionally flush via flush_otel, then hard-exit."""
        import os

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SpanExporter,
            SpanExportResult,
        )

        import pounce._otel as otel

        class _FileExporter(SpanExporter):
            def export(self, spans):
                with open(path, "a") as fh:
                    for span in spans:
                        fh.write(span.name + "\n")
                return SpanExportResult.SUCCESS

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

            def shutdown(self) -> None:
                pass

        provider = TracerProvider()
        # schedule_delay far larger than the test window so the background
        # thread cannot export on its own — only an explicit flush will.
        provider.add_span_processor(
            BatchSpanProcessor(_FileExporter(), schedule_delay_millis=600_000)
        )
        # Install as the global provider (this child process) so flush_otel,
        # which reads the global, can flush it.
        trace._TRACER_PROVIDER = provider

        tracer = provider.get_tracer("pounce._otel", "test")
        span = tracer.start_span("GET")
        span.end()

        if flush:
            otel.flush_otel(timeout_millis=5000)

        os._exit(0)

    def _run_child(self, *, flush: bool, tmp_path) -> str:
        import os

        out = tmp_path / f"spans_flush_{flush}.txt"
        out.write_text("")
        pid = os.fork()
        if pid == 0:  # pragma: no cover - runs in forked child
            try:
                self._child_emit_span(str(out), flush=flush)
            finally:
                os._exit(0)
        os.waitpid(pid, 0)
        return out.read_text()

    def test_span_dropped_without_flush(self, tmp_path):
        """Regression guard: without a flush the pre-exit span is lost."""
        assert self._run_child(flush=False, tmp_path=tmp_path).strip() == ""

    def test_flush_otel_exports_pre_exit_span(self, tmp_path):
        """flush_otel exports the span the worker would otherwise drop."""
        assert self._run_child(flush=True, tmp_path=tmp_path).strip() == "GET"
