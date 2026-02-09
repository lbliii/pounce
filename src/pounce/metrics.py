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

Thread-safe: all counters use ``threading.Lock`` for free-threading mode.

"""

import math
import threading
import time
from collections import defaultdict

from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionClosed,
    ConnectionOpened,
    LifecycleEvent,
    RequestStarted,
    ResponseCompleted,
)

# Default histogram bucket boundaries (seconds), matching Prometheus defaults
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.075,
    0.1, 0.25, 0.5, 0.75,
    1.0, 2.5, 5.0, 7.5, 10.0,
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
        "_lock",
        "_requests_total",
        "_duration_sum",
        "_duration_count",
        "_duration_buckets",
        "_bucket_boundaries",
        "_connections_active",
        "_requests_in_flight",
        "_bytes_sent_total",
        "_start_time_ns",
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
        self._duration_buckets: dict[float, int] = {b: 0 for b in duration_buckets}
        # Gauges
        self._connections_active: int = 0
        self._requests_in_flight: int = 0
        # Totals
        self._bytes_sent_total: int = 0
        self._start_time_ns: int = time.monotonic_ns()

    def record(self, event: LifecycleEvent) -> None:
        """Process a lifecycle event and update metrics."""
        with self._lock:
            if isinstance(event, ConnectionOpened):
                self._connections_active += 1
            elif isinstance(event, ConnectionClosed):
                self._connections_active = max(0, self._connections_active - 1)
                self._bytes_sent_total += event.total_bytes_sent
            elif isinstance(event, RequestStarted):
                self._requests_in_flight += 1
            elif isinstance(event, ResponseCompleted):
                self._requests_in_flight = max(0, self._requests_in_flight - 1)
                # We don't have method in ResponseCompleted, use status only
                status_str = str(event.status)
                status_class = f"{event.status // 100}xx"
                self._requests_total[("", status_str)] += 1
                # Duration histogram — increment only the first matching
                # bucket; export() computes the cumulative sum.
                duration_s = event.duration_ms / 1000.0
                self._duration_sum += duration_s
                self._duration_count += 1
                for boundary in self._bucket_boundaries:
                    if duration_s <= boundary:
                        self._duration_buckets[boundary] += 1
                        break
            elif isinstance(event, ClientDisconnected):
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
            lines.append(
                "# HELP http_request_duration_seconds Request duration in seconds."
            )
            lines.append("# TYPE http_request_duration_seconds histogram")
            cumulative = 0
            for boundary in self._bucket_boundaries:
                cumulative += self._duration_buckets[boundary]
                le = "+Inf" if math.isinf(boundary) else str(boundary)
                lines.append(
                    f'http_request_duration_seconds_bucket{{le="{le}"}} {cumulative}'
                )
            lines.append(
                f"http_request_duration_seconds_sum {self._duration_sum}"
            )
            lines.append(
                f"http_request_duration_seconds_count {self._duration_count}"
            )

            # http_connections_active (gauge)
            lines.append(
                "# HELP http_connections_active Active TCP connections."
            )
            lines.append("# TYPE http_connections_active gauge")
            lines.append(f"http_connections_active {self._connections_active}")

            # http_requests_in_flight (gauge)
            lines.append(
                "# HELP http_requests_in_flight Requests currently being processed."
            )
            lines.append("# TYPE http_requests_in_flight gauge")
            lines.append(f"http_requests_in_flight {self._requests_in_flight}")

            # http_bytes_sent_total (counter)
            lines.append(
                "# HELP http_bytes_sent_total Total bytes sent in responses."
            )
            lines.append("# TYPE http_bytes_sent_total counter")
            lines.append(f"http_bytes_sent_total {self._bytes_sent_total}")

        lines.append("")  # trailing newline
        return "\n".join(lines)
