"""Built-in fixed-rate HTTP/1.1 load driver for sustained benchmark evidence.

The external ``wrk``/``hey`` path remains useful for saturation throughput.
This driver serves a different purpose: schedule requests at a fixed rate,
include scheduler delay in latency (avoiding coordinated omission), and retain
enough samples to report p50, p99, and p999 without an external binary.
"""

from __future__ import annotations

import http.client
import json
import queue
import statistics
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil
from urllib.parse import urlsplit


@dataclass(slots=True)
class _Counters:
    completed: int = 0
    bytes_received: int = 0
    status_errors: int = 0
    transport_errors: int = 0


@dataclass(frozen=True, slots=True)
class FixedRateRequest:
    """One request variant rotated by the fixed-rate load driver."""

    method: str = "GET"
    body: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


def _percentile_ms(latencies_ns: list[int], percentile: float) -> float:
    """Return a nearest-rank latency percentile in milliseconds."""
    if not latencies_ns:
        return 0.0
    ordered = sorted(latencies_ns)
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1] / 1_000_000


def run_fixed_rate(
    url: str,
    *,
    duration: float,
    connections: int,
    rate: int,
    method: str = "GET",
    body_size: str | None = None,
    requests: Sequence[FixedRateRequest] | None = None,
) -> dict:
    """Drive *url* at ``rate`` scheduled requests/second.

    Each worker owns one persistent ``HTTPConnection``. The bounded schedule
    queue records overload as dropped requests instead of silently extending
    the run and turning a fixed-rate test into a saturation test. Latency is
    measured from the intended schedule time through the complete response,
    so queueing delay remains visible in tail percentiles.
    """
    if duration <= 0:
        raise ValueError("duration must be > 0")
    if connections < 1:
        raise ValueError("connections must be >= 1")
    if rate < 1:
        raise ValueError("rate must be >= 1")
    if requests is not None and not requests:
        raise ValueError("requests must contain at least one request variant")

    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname is None:
        raise ValueError("fixed-rate driver supports http:// URLs only")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    body = b"x" * int(body_size) if body_size and method == "POST" else None
    headers = {"content-type": "application/octet-stream"} if body is not None else {}
    request_plan = (
        tuple(requests) if requests is not None else (FixedRateRequest(method, body, headers),)
    )

    schedule: queue.Queue[tuple[int, int] | None] = queue.Queue(maxsize=max(2, connections * 2))
    latencies_ns: list[int] = []
    counters = _Counters()
    result_lock = threading.Lock()

    def worker() -> None:
        connection: http.client.HTTPConnection | None = None
        try:
            while True:
                scheduled = schedule.get()
                try:
                    if scheduled is None:
                        return
                    scheduled_ns, request_index = scheduled
                    request = request_plan[request_index % len(request_plan)]
                    if connection is None:
                        connection = http.client.HTTPConnection(
                            parsed.hostname,
                            parsed.port or 80,
                            timeout=max(5.0, duration),
                        )
                    try:
                        connection.request(
                            request.method,
                            target,
                            body=request.body,
                            headers=dict(request.headers),
                        )
                        response = connection.getresponse()
                        payload = response.read()
                        latency_ns = time.perf_counter_ns() - scheduled_ns
                        with result_lock:
                            counters.completed += 1
                            counters.bytes_received += len(payload)
                            counters.status_errors += int(response.status >= 400)
                            latencies_ns.append(latency_ns)
                    except (OSError, http.client.HTTPException, TimeoutError):  # fmt: skip
                        connection.close()
                        connection = None
                        with result_lock:
                            counters.transport_errors += 1
                finally:
                    schedule.task_done()
        finally:
            if connection is not None:
                connection.close()

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(connections)]
    for thread in workers:
        thread.start()

    started = time.perf_counter()
    started_ns = time.perf_counter_ns()
    interval_ns = 1_000_000_000 / rate
    scheduled = 0
    dropped = 0
    target_count = max(1, int(duration * rate))
    for request_index in range(target_count):
        due_ns = started_ns + int(request_index * interval_ns)
        remaining_ns = due_ns - time.perf_counter_ns()
        if remaining_ns > 0:
            time.sleep(remaining_ns / 1_000_000_000)
        try:
            schedule.put_nowait((due_ns, request_index))
            scheduled += 1
        except queue.Full:
            dropped += 1

    schedule.join()
    elapsed = max(time.perf_counter() - started, duration)
    for _ in workers:
        schedule.put(None)
    schedule.join()
    for thread in workers:
        thread.join(timeout=1.0)

    errors = dropped + counters.status_errors + counters.transport_errors
    avg_latency_ms = statistics.fmean(latencies_ns) / 1_000_000 if latencies_ns else 0.0
    raw = {
        "target_rps": rate,
        "scheduled": scheduled,
        "completed": counters.completed,
        "dropped": dropped,
        "status_errors": counters.status_errors,
        "transport_errors": counters.transport_errors,
        "elapsed_seconds": round(elapsed, 6),
    }
    return {
        "req_per_sec": counters.completed / elapsed,
        "avg_latency_ms": avg_latency_ms,
        "p50_latency_ms": _percentile_ms(latencies_ns, 0.50),
        "p99_latency_ms": _percentile_ms(latencies_ns, 0.99),
        "p999_latency_ms": _percentile_ms(latencies_ns, 0.999),
        "transfer_per_sec": f"{counters.bytes_received / elapsed:.0f}B",
        "total_requests": counters.completed,
        "errors": errors,
        "load_tool_stdout": json.dumps(raw, sort_keys=True),
        "load_tool_stderr": "",
    }
