"""
OpenTelemetry integration for distributed tracing.

Provides automatic span creation and context propagation for HTTP requests,
enabling pounce to integrate with observability platforms like Jaeger, Datadog,
Tempo, and others.

Features:
- Automatic span creation for each HTTP request
- Context propagation via traceparent/tracestate headers (W3C Trace Context)
- OTLP exporter configuration
- Request/response attributes (method, path, status, duration)
- Optional integration (graceful degradation if OTel not installed)
- Zero overhead when disabled

Usage:
    config = ServerConfig(
        otel_endpoint="http://localhost:4318",  # OTLP HTTP endpoint
        otel_service_name="my-service",
    )

Security:
- Only enabled when otel_endpoint is configured
- Never samples sensitive data (passwords, tokens)
- Respects standard OTel environment variables

"""

import logging
from typing import Any

logger = logging.getLogger("pounce.otel")

# Try to import OpenTelemetry (optional dependency)
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.propagate import extract, inject
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False


def is_otel_available() -> bool:
    """Check if OpenTelemetry is installed."""
    return _HAS_OTEL


def configure_otel(
    *,
    endpoint: str,
    service_name: str = "pounce",
    insecure: bool = False,
) -> None:
    """Configure OpenTelemetry with OTLP exporter.

    Args:
        endpoint: OTLP endpoint URL (e.g., "http://localhost:4318").
        service_name: Service name for resource attributes.
        insecure: Allow insecure connections (HTTP instead of HTTPS).

    Raises:
        RuntimeError: If OpenTelemetry is not installed.

    """
    if not _HAS_OTEL:
        raise RuntimeError(
            "OpenTelemetry integration requires opentelemetry-api and related packages. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-http"
        )

    # Create resource with service name
    resource = Resource(attributes={SERVICE_NAME: service_name})

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Create OTLP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=endpoint if endpoint.endswith("/v1/traces") else f"{endpoint}/v1/traces",
    )

    # Add batch span processor
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(
        "OpenTelemetry configured: endpoint=%s, service=%s",
        endpoint,
        service_name,
    )


def extract_trace_context(headers: list[tuple[bytes, bytes]]) -> Any:
    """Extract trace context from HTTP headers.

    Parses W3C Trace Context headers (traceparent, tracestate) from the
    incoming request and returns a context object for span creation.

    Args:
        headers: List of (name, value) header tuples.

    Returns:
        OpenTelemetry context object with extracted trace info.

    """
    if not _HAS_OTEL:
        return None

    # Convert headers to dict format for propagator
    headers_dict = {
        name.decode("latin1").lower(): value.decode("latin1", errors="replace")
        for name, value in headers
    }

    # Extract context using W3C Trace Context propagator
    propagator = TraceContextTextMapPropagator()
    return propagator.extract(carrier=headers_dict)


def inject_trace_context(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Inject trace context into outgoing HTTP headers.

    Adds traceparent and tracestate headers to propagate trace context
    to downstream services.

    Args:
        headers: Existing headers list.

    Returns:
        Updated headers list with trace context.

    """
    if not _HAS_OTEL:
        return headers

    # Create carrier dict for injection
    carrier: dict[str, str] = {}
    propagator = TraceContextTextMapPropagator()
    propagator.inject(carrier=carrier)

    # Add injected headers
    updated_headers = list(headers)
    for name, value in carrier.items():
        updated_headers.append((name.encode("latin1"), value.encode("latin1")))

    return updated_headers


class RequestSpanManager:
    """Manages OpenTelemetry spans for HTTP requests.

    Creates and manages the lifecycle of trace spans for incoming HTTP
    requests, including context propagation and attribute recording.

    """

    __slots__ = ("_tracer", "_enabled")

    def __init__(self, *, service_name: str = "pounce", enabled: bool = True) -> None:
        """Initialize the span manager.

        Args:
            service_name: Service name for the tracer.
            enabled: Whether tracing is enabled.

        """
        self._enabled = enabled and _HAS_OTEL

        if self._enabled:
            self._tracer = trace.get_tracer(__name__, service_name)
        else:
            self._tracer = None

    def create_request_span(
        self,
        *,
        method: str,
        path: str,
        headers: list[tuple[bytes, bytes]],
        scheme: str = "http",
        server_host: str = "localhost",
        server_port: int = 8000,
    ) -> Any:
        """Create a span for an HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: Request path.
            headers: Request headers for context extraction.
            scheme: URL scheme (http or https).
            server_host: Server hostname.
            server_port: Server port.

        Returns:
            Span context manager (use with `with` statement).

        """
        if not self._enabled:
            return _NoOpSpan()

        # Extract parent context from headers
        parent_context = extract_trace_context(headers)

        # Create span with HTTP semantic conventions
        span = self._tracer.start_span(
            f"{method} {path}",
            context=parent_context,
            kind=trace.SpanKind.SERVER,
        )

        # Set HTTP attributes (following OpenTelemetry semantic conventions)
        span.set_attribute("http.method", method)
        span.set_attribute("http.target", path)
        span.set_attribute("http.scheme", scheme)
        span.set_attribute("http.host", server_host)
        span.set_attribute("net.host.port", server_port)

        return span

    def record_response(
        self,
        span: Any,
        *,
        status_code: int,
        response_size: int = 0,
    ) -> None:
        """Record response attributes on a span.

        Args:
            span: The span to update.
            status_code: HTTP status code.
            response_size: Response body size in bytes.

        """
        if not self._enabled or span is None:
            return

        # Set status code
        span.set_attribute("http.status_code", status_code)

        if response_size > 0:
            span.set_attribute("http.response_content_length", response_size)

        # Set span status based on HTTP status code
        if status_code >= 500:
            span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
        elif status_code >= 400:
            # Client errors are not span errors
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.OK))

    def record_exception(self, span: Any, exception: Exception) -> None:
        """Record an exception on a span.

        Args:
            span: The span to update.
            exception: The exception that occurred.

        """
        if not self._enabled or span is None:
            return

        span.record_exception(exception)
        span.set_status(Status(StatusCode.ERROR, str(exception)))

    def end_span(self, span: Any) -> None:
        """End a span.

        Args:
            span: The span to end.

        """
        if not self._enabled or span is None:
            return

        span.end()


class _NoOpSpan:
    """No-op span for when OpenTelemetry is disabled."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def record_exception(self, *args, **kwargs):
        pass

    def end(self):
        pass


def get_current_span() -> Any:
    """Get the current active span.

    Returns:
        Current span or None if no span is active.

    """
    if not _HAS_OTEL:
        return None

    return trace.get_current_span()


def add_span_attribute(key: str, value: Any) -> None:
    """Add an attribute to the current span.

    Args:
        key: Attribute name.
        value: Attribute value.

    """
    if not _HAS_OTEL:
        return

    span = trace.get_current_span()
    if span is not None:
        span.set_attribute(key, value)
