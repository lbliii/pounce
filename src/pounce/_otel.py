"""
OpenTelemetry integration for distributed tracing.

Provides automatic span creation for HTTP requests and inbound W3C Trace
Context extraction, enabling pounce to integrate with observability
platforms like Jaeger, Datadog, Tempo, and others.

Features:
- Automatic span creation for each HTTP request
- Inbound W3C Trace Context extraction (traceparent/tracestate parsed
  from request headers so server spans join an upstream trace).  Pounce
  is a server, not an outbound HTTP client, so it does not inject trace
  context into downstream requests.
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
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("pounce.otel")

# Try to import OpenTelemetry (optional dependency)
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    _HAS_OTEL = True
    _PROPAGATOR = TraceContextTextMapPropagator()
except ImportError:
    _HAS_OTEL = False
    _PROPAGATOR = None

_TRACE_HEADER_NAMES = (b"traceparent", b"tracestate")


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

    # Add batch span processor.  Its background export thread is re-spawned in
    # forked child workers automatically by the SDK's ``os.register_at_fork``
    # hook (opentelemetry-sdk >= 1.20), so fork mode inherits a working export
    # path — but the SDK never flushes on process exit (see ``flush_otel``).
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(
        "OpenTelemetry configured: endpoint=%s, service=%s",
        endpoint,
        service_name,
    )


def flush_otel(timeout_millis: int = 5000) -> None:
    """Flush in-flight spans from the active provider's processors.

    The ``BatchSpanProcessor`` only exports on its ``schedule_delay`` timer (or
    when its queue fills), and the SDK does not flush on process exit.  Spans
    created shortly before a worker stops are therefore dropped unless flushed
    explicitly.  Worker shutdown calls this so those spans are exported instead
    of lost (issue #133).  Safe to call when OTel is unavailable or unconfigured.
    """
    if not _HAS_OTEL:
        return
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None:
        return
    try:
        force_flush(timeout_millis)
    except Exception:  # pragma: no cover - defensive; never fail shutdown
        logger.debug("OpenTelemetry force_flush failed on shutdown", exc_info=True)


def extract_trace_context(headers: Sequence[tuple[bytes, bytes]]) -> Any:
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

    # Convert only trace headers to dict format for propagator
    headers_dict: dict[str, str] = {}
    for name, value in headers:
        if name.lower() in _TRACE_HEADER_NAMES:
            headers_dict[name.decode("latin1")] = value.decode("latin1", errors="replace")

    # Extract context using W3C Trace Context propagator
    if _PROPAGATOR is None:
        return None
    return _PROPAGATOR.extract(carrier=headers_dict)


class RequestSpanManager:
    """Manages OpenTelemetry spans for HTTP requests.

    Creates and manages the lifecycle of trace spans for incoming HTTP
    requests, including inbound parent-context extraction (so server spans
    join an upstream trace) and attribute recording.  Does not propagate
    trace context to downstream services.

    """

    __slots__ = ("_enabled", "_tracer")

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
        headers: Sequence[tuple[bytes, bytes]],
        scheme: str = "http",
        server_host: str = "localhost",
        server_port: int = 8000,
        route: str | None = None,
    ) -> Any:
        """Create a span for an HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: Request path.
            headers: Request headers for context extraction.
            scheme: URL scheme (http or https).
            server_host: Server hostname.
            server_port: Server port.
            route: Low-cardinality route template (e.g. ``/users/{id}``) when
                the app exposes one.  Used in the span name to keep cardinality
                bounded; ``None`` falls back to just the method.

        Returns:
            Span context manager (use with `with` statement).

        """
        if not self._enabled or self._tracer is None:
            return _NoOpSpan()

        # Extract parent context from headers
        parent_context = extract_trace_context(headers)

        # Low-cardinality span name: never the raw path (which embeds IDs and
        # would explode cardinality in Tempo/Jaeger/Datadog — issue #135).
        # Use the route template when available, otherwise just the method.
        span_name = f"{method} {route}" if route else method

        # Create span with HTTP semantic conventions
        span = self._tracer.start_span(
            span_name,
            context=parent_context,
            kind=trace.SpanKind.SERVER,
        )

        # Stable OpenTelemetry HTTP semantic conventions (>= 1.20 / 1.40).
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.path", path)
        span.set_attribute("url.scheme", scheme)
        span.set_attribute("server.address", server_host)
        span.set_attribute("server.port", server_port)
        if route:
            span.set_attribute("http.route", route)

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

        # Stable OpenTelemetry HTTP semantic conventions (issue #135).
        span.set_attribute("http.response.status_code", status_code)

        if response_size > 0:
            span.set_attribute("http.response.body.size", response_size)

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
