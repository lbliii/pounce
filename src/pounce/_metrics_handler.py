"""
Built-in /metrics endpoint for Prometheus scraping.

Provides a lightweight ASGI app that serves metrics from a PrometheusCollector
at a configurable path (default: /metrics).

"""

from collections.abc import Callable

from pounce.metrics import PrometheusCollector


def create_metrics_app(
    collector: PrometheusCollector,
    metrics_path: str = "/metrics",
) -> Callable:
    """Create an ASGI app that serves Prometheus metrics.

    This wraps the user's ASGI app and intercepts requests to the metrics
    path, serving Prometheus text format metrics from the collector.

    Args:
        collector: PrometheusCollector instance with metrics
        metrics_path: Path to serve metrics at (default: "/metrics")

    Returns:
        ASGI app callable that handles metrics requests

    Example:
        collector = PrometheusCollector()
        metrics_app = create_metrics_app(collector, "/metrics")

    """

    async def metrics_handler(scope: dict, receive: Callable, send: Callable) -> None:
        """ASGI app that returns metrics in Prometheus text format."""
        if scope["type"] != "http":
            # Not an HTTP request, ignore
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"Not Found",
                }
            )
            return

        path = scope.get("path", "/")

        # Check if this is a metrics request
        if path == metrics_path:
            # Export metrics from collector
            metrics_text = collector.export()
            metrics_bytes = metrics_text.encode("utf-8")

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain; version=0.0.4; charset=utf-8"),
                        (b"content-length", str(len(metrics_bytes)).encode("ascii")),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": metrics_bytes,
                }
            )
        else:
            # Not a metrics request, return 404
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"Not Found",
                }
            )

    return metrics_handler


def wrap_app_with_metrics(
    app: Callable,
    collector: PrometheusCollector,
    metrics_path: str = "/metrics",
) -> Callable:
    """Wrap an ASGI app to intercept metrics requests.

    Requests to `metrics_path` are handled by the metrics handler.
    All other requests are passed to the original app.

    Args:
        app: Original ASGI app
        collector: PrometheusCollector instance
        metrics_path: Path to serve metrics at

    Returns:
        Wrapped ASGI app

    Example:
        app = FastAPI()
        collector = PrometheusCollector()
        wrapped_app = wrap_app_with_metrics(app, collector, "/metrics")

    """

    async def wrapper(scope: dict, receive: Callable, send: Callable) -> None:
        """Intercept metrics requests, pass others to original app."""
        if scope["type"] == "http" and scope.get("path") == metrics_path:
            # Serve metrics
            metrics_text = collector.export()
            metrics_bytes = metrics_text.encode("utf-8")

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain; version=0.0.4; charset=utf-8"),
                        (b"content-length", str(len(metrics_bytes)).encode("ascii")),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": metrics_bytes,
                }
            )
        else:
            # Pass to original app
            await app(scope, receive, send)

    return wrapper
