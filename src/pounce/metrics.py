"""
Prometheus-compatible metrics collector.

Implements the ``LifecycleCollector`` protocol to track standard HTTP
server metrics from lifecycle events.  No external dependencies — uses
internal counters that can be exported in Prometheus text format.

Metrics:
    - ``http_requests_total`` — counter by method and status
    - ``http_request_duration_seconds`` — histogram of request durations
    - ``http_connections_active`` — gauge of open connections
    - ``http_requests_in_flight`` — gauge of in-progress requests
    - ``http_streams_active`` — gauge of open streaming responses
    - ``http_stream_duration_seconds`` — histogram of completed stream lifetimes

Label stability contract (``http_requests_total``):
    - ``method`` — the uppercase HTTP request method as parsed (e.g.
      ``"GET"``, ``"POST"``).  On error/early-out paths where the method
      was never parsed, the stable sentinel ``"unknown"`` is used.  The
      empty string is never emitted.
    - ``status`` — the numeric HTTP status code rendered as a string.

Thread-safe: all counters use ``threading.Lock`` for free-threading mode.

"""

import math
import threading
import time
from collections import defaultdict

from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LifecycleEvent,
    RequestStarted,
    ResponseCompleted,
    StreamClosed,
    StreamOpened,
)

# Default histogram bucket boundaries (seconds), matching Prometheus defaults
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    float("inf"),
)


class PrometheusCollector:
    """Lifecycle collector that maintains Prometheus-compatible metrics.

    Thread-safe — multiple workers can call ``record()`` concurrently.

    Example::

        collector = PrometheusCollector()
        server = Server(config, app, lifecycle_collector=collector)

        # Later: export metrics
        text = collector.export()

    """

    __slots__ = (
        "_bucket_boundaries",
        "_bytes_sent_total",
        "_connections_active",
        "_duration_buckets",
        "_duration_count",
        "_duration_sum",
        "_lock",
        "_requests_in_flight",
        "_requests_total",
        "_start_time_ns",
        "_stream_duration_buckets",
        "_stream_duration_count",
        "_stream_duration_sum",
        "_streams_active",
    )

    def __init__(
        self,
        *,
        duration_buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> None:
        self._lock = threading.Lock()
        # Counter: {(method, status_str): count}
        self._requests_total: dict[tuple[str, str], int] = defaultdict(int)
        # Histogram: duration in seconds
        self._duration_sum: float = 0.0
        self._duration_count: int = 0
        self._bucket_boundaries = duration_buckets
        self._duration_buckets: dict[float, int] = dict.fromkeys(duration_buckets, 0)
        # Gauges
        self._connections_active: int = 0
        self._requests_in_flight: int = 0
        self._streams_active: int = 0
        self._stream_duration_sum: float = 0.0
        self._stream_duration_count: int = 0
        self._stream_duration_buckets: dict[float, int] = dict.fromkeys(duration_buckets, 0)
        # Totals
        self._bytes_sent_total: int = 0
        self._start_time_ns: int = time.monotonic_ns()

    def record(self, event: LifecycleEvent) -> None:
        """Process a lifecycle event and update metrics."""
        with self._lock:
            match event:
                case ConnectionOpened():
                    self._connections_active += 1
                case ConnectionCompleted():
                    self._connections_active = max(0, self._connections_active - 1)
                    self._bytes_sent_total += event.total_bytes_sent
                case RequestStarted():
                    self._requests_in_flight += 1
                case ResponseCompleted():
                    self._requests_in_flight = max(0, self._requests_in_flight - 1)
                    # Key on the real method carried by ResponseCompleted.
                    # ``method`` defaults to the "unknown" sentinel on early-out
                    # paths where the request method was never parsed.
                    status_str = str(event.status)
                    method = event.method or "unknown"
                    self._requests_total[(method, status_str)] += 1
                    # Duration histogram — increment only the first matching
                    # bucket; export() computes the cumulative sum.
                    duration_s = event.duration_ms / 1000.0
                    self._duration_sum += duration_s
                    self._duration_count += 1
                    for boundary in self._bucket_boundaries:
                        if duration_s <= boundary:
                            self._duration_buckets[boundary] += 1
                            break
                case StreamOpened():
                    self._streams_active += 1
                case StreamClosed():
                    self._streams_active = max(0, self._streams_active - 1)
                    duration_s = event.duration_ms / 1000.0
                    self._stream_duration_sum += duration_s
                    self._stream_duration_count += 1
                    for boundary in self._bucket_boundaries:
                        if duration_s <= boundary:
                            self._stream_duration_buckets[boundary] += 1
                            break
                case ClientDisconnected():
                    self._requests_in_flight = max(0, self._requests_in_flight - 1)

    def snapshot(self) -> dict[str, object]:
        """Return a snapshot of current metrics as a dict.

        Useful for JSON export or programmatic access.

        """
        with self._lock:
            return {
                "requests_total": dict(self._requests_total),
                "duration_sum_seconds": self._duration_sum,
                "duration_count": self._duration_count,
                "connections_active": self._connections_active,
                "requests_in_flight": self._requests_in_flight,
                "streams_active": self._streams_active,
                "stream_duration_sum_seconds": self._stream_duration_sum,
                "stream_duration_count": self._stream_duration_count,
                "bytes_sent_total": self._bytes_sent_total,
            }

    def export(self) -> str:
        """Export metrics in Prometheus text exposition format.

        Returns:
            String in Prometheus text format, ready to serve at ``/metrics``.

        """
        lines: list[str] = []

        with self._lock:
            # http_requests_total
            lines.append("# HELP http_requests_total Total HTTP requests.")
            lines.append("# TYPE http_requests_total counter")
            for (method, status), count in sorted(self._requests_total.items()):
                label_method = method or "unknown"
                lines.append(
                    f'http_requests_total{{method="{label_method}",status="{status}"}} {count}'
                )

            # http_request_duration_seconds (histogram)
            lines.append("# HELP http_request_duration_seconds Request duration in seconds.")
            lines.append("# TYPE http_request_duration_seconds histogram")
            cumulative = 0
            for boundary in self._bucket_boundaries:
                cumulative += self._duration_buckets[boundary]
                le = "+Inf" if math.isinf(boundary) else str(boundary)
                lines.append(f'http_request_duration_seconds_bucket{{le="{le}"}} {cumulative}')
            lines.append(f"http_request_duration_seconds_sum {self._duration_sum}")
            lines.append(f"http_request_duration_seconds_count {self._duration_count}")

            # http_connections_active (gauge)
            lines.append("# HELP http_connections_active Active TCP connections.")
            lines.append("# TYPE http_connections_active gauge")
            lines.append(f"http_connections_active {self._connections_active}")

            # http_requests_in_flight (gauge)
            lines.append("# HELP http_requests_in_flight Requests currently being processed.")
            lines.append("# TYPE http_requests_in_flight gauge")
            lines.append(f"http_requests_in_flight {self._requests_in_flight}")

            # http_streams_active (gauge)
            lines.append("# HELP http_streams_active Open streaming HTTP responses.")
            lines.append("# TYPE http_streams_active gauge")
            lines.append(f"http_streams_active {self._streams_active}")

            # http_stream_duration_seconds (histogram)
            lines.append(
                "# HELP http_stream_duration_seconds Completed streaming response lifetime."
            )
            lines.append("# TYPE http_stream_duration_seconds histogram")
            cumulative = 0
            for boundary in self._bucket_boundaries:
                cumulative += self._stream_duration_buckets[boundary]
                le = "+Inf" if math.isinf(boundary) else str(boundary)
                lines.append(f'http_stream_duration_seconds_bucket{{le="{le}"}} {cumulative}')
            lines.append(f"http_stream_duration_seconds_sum {self._stream_duration_sum}")
            lines.append(f"http_stream_duration_seconds_count {self._stream_duration_count}")

            # http_bytes_sent_total (counter)
            lines.append("# HELP http_bytes_sent_total Total bytes sent in responses.")
            lines.append("# TYPE http_bytes_sent_total counter")
            lines.append(f"http_bytes_sent_total {self._bytes_sent_total}")

        lines.append("")  # trailing newline
        return "\n".join(lines)
